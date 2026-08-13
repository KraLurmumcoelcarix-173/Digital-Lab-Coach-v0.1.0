"""L3 Mode A coordinator + verifier (dlc/l3/debugger.py).

Every test injects a fake model via `call=` — the pipeline never touches
the network. The default fixture pins the NO-JAR path (the Python
evaluator is both judge and verifier) so results are identical with or
without a configured Digital.jar; one jar-gated test exercises the real
Digital verify path end to end.

Ground truth: bug3_wrong_cin — the Const feeding the adder's c_i omits
Value (defaults to 1). The correct fix is change_attribute[16].Value=0;
setting Value=1 instead applies cleanly but changes nothing (refutation
fodder).
"""

import json
import shutil
from pathlib import Path

import pytest

from dlc.l3 import debugger
from dlc.l3.debugger import (
    debug_circuit,
    dedupe_hypotheses,
    validate_animation,
    validate_hypothesis,
    verify_ops,
)
from dlc.testing.runner import find_digital_jar

_BENCH = "data/sample_circuits/30_bug_benchmark"
_BUG3 = f"{_BENCH}/bug3_wrong_cin/Wrong_cin.dig"
_BUG4 = f"{_BENCH}/bug4_missing_pipeline/Missing_pipeline.dig"
_CLEAN = "data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig"

_needs_jar = pytest.mark.skipif(
    find_digital_jar() is None, reason="Digital.jar not configured",
)

GOOD_OPS = [{"op": "change_attribute", "component_index": 16,
             "name": "Value", "value": 0}]
BAD_OPS = [{"op": "change_attribute", "component_index": 16,
            "name": "Value", "value": 1}]


def _reply(ops, why="Sum is one too high on every failing row."):
    return {
        "contract": "l3.debug.v1.1",
        "confidence": 0.9,
        "hint": {"suspect_region": "the adder's carry-in constant",
                 "suspect_signals": ["c_i"], "why": why},
        "fix": {"ops": ops,
                "explanation_for_student": ("the constant driving c_i "
                                            "defaults to 1; set it to 0."),
                "animation_script": [
                    {"act": "diagnose_line", "text": "Sum runs one high."},
                    {"act": "mark_fix",
                     "target": {"component_index": 16, "path": []},
                     "label": "carry-in 1 -> 0"},
                    {"act": "retest"},
                ]},
    }


def _fake(replies):
    """call= stub: pops canned replies (dicts are JSON-encoded), records
    every prompt on .log, and refuses unexpected extra calls."""
    replies = list(replies)
    def call(prompt, **_kw):
        call.log.append(prompt)
        assert replies, "unexpected extra LLM call"
        r = replies.pop(0)
        return {"ok": True,
                "text": json.dumps(r) if isinstance(r, dict) else r,
                "error": None,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "model": "fake"}
    call.log = []
    return call


def _never(prompt, **_kw):
    raise AssertionError("the model must not be called on this path")


@pytest.fixture(autouse=True)
def _no_jar(monkeypatch):
    """Pin the evaluator path; the jar-gated test below re-enables it."""
    monkeypatch.setattr(debugger, "find_digital_jar", lambda: None)


# ---------------------------------------------------------------------------
# End to end on the benchmark (offline)
# ---------------------------------------------------------------------------

def test_bug3_end_to_end_confirmed_card():
    call = _fake([_reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["mode"] == "analysis"
    assert res["llm_calls"] == 1
    assert len(res["cards"]) == 1
    card = res["cards"][0]
    assert card["rank"] == 1
    assert card["cluster_rows"] == [0, 1]
    assert card["verified"] == {"confirmed": True, "runner": "evaluator",
                                "regressions": [], "coach_residuals": {}}
    assert card["fix"]["ops"] == GOOD_OPS
    assert card["fix"]["animation_script"][-1] == {"act": "retest"}
    assert card["hint"]["suspect_region"]
    assert res["dropped_ideas"] == []
    assert res["diagnosis_lines"] and "Sum" in res["diagnosis_lines"][0]
    assert res["usage"]["output_tokens"] == 20


def test_clear_circuit_makes_no_model_calls():
    res = debug_circuit(_CLEAN, call=_never, use_manifest=False)
    assert res["mode"] == "clear"
    assert res["llm_calls"] == 0


def test_lazy_circuit_makes_no_model_calls_and_suggests():
    res = debug_circuit(_BUG4, call=_never, use_manifest=False)
    assert res["mode"] == "lazy"
    assert res["llm_calls"] == 0
    assert [s["kind"] for s in res["suggestions"]] == ["missing_clocked_logic"]
    sug = res["suggestions"][0]
    assert sug["question"] and sug["hint"]
    assert "register" in sug["terms"]


def test_invalid_json_earns_one_format_reprompt():
    call = _fake(["sorry, I cannot produce JSON today", _reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 2
    assert "FORMAT RETRY" in call.log[1]
    assert len(res["cards"]) == 1 and res["cards"][0]["verified"]["confirmed"]


def test_double_garbage_becomes_dropped_idea():
    call = _fake(["nope", "still nope"])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 2
    assert res["cards"] == []
    assert [d["reason"] for d in res["dropped_ideas"]] == ["invalid_response"]


def test_refuted_fix_earns_one_retry_with_evidence():
    call = _fake([_reply(BAD_OPS), _reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 2
    # the template mentions "[REFUTED ATTEMPT]" itself, so the real
    # refutation block is recognized by its payload keys
    assert '"refuted_ops"' in call.log[1]
    assert '"still_failing"' in call.log[1]
    assert len(res["cards"]) == 1
    assert res["cards"][0]["fix"]["ops"] == GOOD_OPS


def test_refuted_rom_data_rewrite_steers_the_retry_off_the_table():
    # r27, from a live control-unit failure: once a Data rewrite is
    # refuted, the retry must carry the machine fact that the stored
    # words are consistent and point at the address/select path instead
    rom_ops = [{"op": "change_attribute", "component_index": 16,
                "name": "Data", "value": "82,86"}]
    call = _fake([_reply(rom_ops), _reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 2
    assert "MACHINE FACT" in call.log[1]
    assert "ADDRESS/SELECT path" in call.log[1]
    # a refuted NON-data fix must not carry the steer (other test's log)
    call2 = _fake([_reply(BAD_OPS), _reply(GOOD_OPS)])
    debug_circuit(_BUG3, call=call2, use_manifest=False,
                  failing_indices=[0, 1])
    assert "MACHINE FACT" not in call2.log[1]


def test_unknown_op_is_a_format_error_then_dropped():
    bad = _reply(GOOD_OPS)
    bad["fix"]["ops"] = [{"op": "explode_everything"}]
    call = _fake([bad, "garbage"])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 2
    assert "unknown op" in call.log[1]
    assert res["cards"] == []
    assert res["dropped_ideas"][0]["reason"] == "invalid_response"


@_needs_jar
def test_bug3_fix_verifies_through_real_digital(monkeypatch):
    # Digital's own bug3 verdict varies by jar version. 
    # Pin the verdict through the caller seam so the REAL
    # Digital VERIFY path is what this test exercises, deterministically.
    monkeypatch.setattr(debugger, "find_digital_jar",
                        lambda: find_digital_jar())
    call = _fake([_reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["mode"] == "analysis"
    assert len(res["cards"]) == 1
    assert res["cards"][0]["verified"]["confirmed"] is True
    assert res["cards"][0]["verified"]["runner"] == "digital"


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

def test_verify_ops_confirms_the_correct_fix_offline():
    v = verify_ops(_BUG3, "Testcase_12", GOOD_OPS,
                   cluster_rows=[0, 1, 2, 3],
                   original_failing=[0, 1, 2, 3])
    assert v["confirmed"] is True and v["apply_ok"] is True
    assert v["runner"] == "evaluator"
    assert v["still_failing"] == [] and v["regressions"] == []


def test_verify_ops_refutes_a_no_op_fix():
    v = verify_ops(_BUG3, "Testcase_12", BAD_OPS,
                   cluster_rows=[0, 1, 2, 3],
                   original_failing=[0, 1, 2, 3])
    assert v["confirmed"] is False and v["apply_ok"] is True
    assert v["still_failing"] == [0, 1, 2, 3]
    assert v["details"][0], "refutation evidence must carry the cells"


def test_verify_ops_reports_apply_failure():
    v = verify_ops(_BUG3, "Testcase_12",
                   [{"op": "delete_wire", "p1": [1, 1], "p2": [2, 2]}],
                   cluster_rows=[0], original_failing=[0])
    assert v["confirmed"] is False and v["apply_ok"] is False
    assert v["warning"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _hyp(ops, rows, confirmed, confidence=0.5, ci=0):
    return {"cluster_index": ci, "cluster_rows": rows,
            "confidence": confidence, "hint": {"why": "w"},
            "ops": ops, "explanation": "", "animation": [],
            "verdict": {"confirmed": confirmed, "apply_ok": True,
                        "runner": "evaluator", "still_failing": [],
                        "regressions": [], "warning": None}}


def test_dedupe_merges_rows_only_between_confirmed_twins():
    both = dedupe_hypotheses([
        _hyp(GOOD_OPS, [0, 1], True, ci=0),
        _hyp(GOOD_OPS, [2, 3], True, ci=1),
    ])
    assert len(both) == 1 and both[0]["cluster_rows"] == [0, 1, 2, 3]

    mixed = dedupe_hypotheses([
        _hyp(GOOD_OPS, [0, 1], True, ci=0),
        _hyp(GOOD_OPS, [2, 3], False, ci=1),
    ])
    assert len(mixed) == 1 and mixed[0]["cluster_rows"] == [0, 1]


def test_rank_prefers_confirmed_then_rows_then_confidence():
    ranked = dedupe_hypotheses([
        _hyp(BAD_OPS, [0, 1, 2], False, confidence=0.99, ci=0),
        _hyp(GOOD_OPS, [3], True, confidence=0.1, ci=1),
    ])
    assert ranked[0]["ops"] == GOOD_OPS, "confirmed beats big-but-refuted"


def test_validate_animation_drops_junk_and_forces_retest_last():
    script = [
        {"act": "diagnose_line", "text": "one line"},
        {"act": "teleport", "text": "not a real act"},
        {"act": "mark_fix", "target": {"component_index": 9999}, "label": "x"},
        {"act": "focus", "component_index": 2, "path": []},
        {"act": "retest"},
        {"act": "diagnose_line", "text": "after retest, still kept"},
    ]
    out = validate_animation(script, n_components=20)
    assert out[-1] == {"act": "retest"}
    assert sum(1 for a in out if a["act"] == "retest") == 1
    acts = [a["act"] for a in out]
    assert "teleport" not in acts
    assert all(a.get("target", {}).get("component_index") != 9999
               for a in out)
    assert {"act": "focus", "component_index": 2, "path": []} in out


def test_validate_hypothesis_strips_leaky_hint_lines():
    obj = _reply(GOOD_OPS, why="You are a helpful assistant")
    clean, err = validate_hypothesis(obj)
    assert err is None
    assert clean["hint"]["why"] == "", "F13 leak line must be stripped"


def test_validate_hypothesis_requires_contract_and_ops():
    assert validate_hypothesis(None)[1]
    assert validate_hypothesis({"contract": "l3.debug.v1"})[1]
    ok = _reply(GOOD_OPS)
    ok["fix"]["ops"] = []
    assert "fix.ops" in validate_hypothesis(ok)[1]


def test_small_circuit_stays_analyzable_even_when_every_row_fails():
    # bug3 has 17 components (<= 30): the rate bars are off, so the
    # evaluator's 4-of-4 failing verdict still runs the full pipeline
    call = _fake([_reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["mode"] == "analysis"
    assert res["cards"][0]["cluster_rows"] == [0, 1, 2, 3]
    assert res["cards"][0]["verified"]["confirmed"] is True


def test_big_circuit_below_its_bar_goes_lazy_with_suggestions():
    # unfocused (4 scattered columns) + 20% passing on a 160+ component
    # tree: under the 30% bar -> the free suggestion branch
    led = (f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
           f"wrong_bool_LED1.dig")
    res = debug_circuit(led, call=_never, use_manifest=False,
                        failing_indices=[0, 1, 2, 3],
                        jar_mismatches={
                            0: [{"column": "Fa", "expected": "1", "found": "0"}],
                            1: [{"column": "Fb", "expected": "1", "found": "0"}],
                            2: [{"column": "Fe", "expected": "1", "found": "0"}],
                            3: [{"column": "Fg", "expected": "1", "found": "0"}],
                        })
    assert res["mode"] == "lazy"
    assert [s["kind"] for s in res["suggestions"]] == ["low_pass_rate"]
    assert "truth table" in res["suggestions"][0]["terms"]


def test_cards_carry_the_component_naming_standard():
    call = _fake([_reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    pretty = res["cards"][0]["fix"]["ops_pretty"]
    assert pretty == ["set [16] Const attribute Value = 0"]


def test_describe_ops_arrows_follow_signal_direction():
    from dlc.l3.debugger import describe_ops
    from dlc.parser.dig_parser import parse_dig_file
    calc = parse_dig_file(f"{_BENCH}/bug1_meaningless_mux_in3/"
                          f"tier3_calculator.dig")
    lines = describe_ops(calc, [
        {"op": "rewire_pin", "component_index": 14, "pin": "in3",
         "to": {"component_index": 9, "pin": "Result"}},
        {"op": "delete_component", "component_index": 23},
    ])
    assert lines[0] == "rewire [14] Multiplexer.in3 ← [9] bool_unit.dig.Result"
    assert lines[1] == "delete [23] Ground"


def test_zero_card_run_earns_one_escalation_per_cluster():
    # first answer refuted, its retry refuted again -> the run would be
    # empty -> ONE escalation attempt with the refuted ops disclosed
    call = _fake([_reply(BAD_OPS), _reply(BAD_OPS), _reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 3
    assert "[ESCALATION]" in call.log[2] and '"refuted_ops"' in call.log[2]
    assert len(res["cards"]) == 1
    assert res["cards"][0]["fix"]["ops"] == GOOD_OPS
    # pipeline internals stay off the student's board
    assert not any("escalation" in n for n in res["notes"])


def test_led5_gate_swap_fix_confirms_offline():
    # the seeded LED5 bug: an Or turned into an And at index 164 — the
    # single replace_element found by exhaustive verification
    led = (f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
           f"wrong_bool_LED5.dig")
    reply = _reply([{"op": "replace_element", "component_index": 164,
                     "new_element": "Or"}],
                   why="Ff is 0 whenever either minterm group fires.")
    call = _fake([reply])
    res = debug_circuit(led, call=call, use_manifest=False,
                        failing_indices=[1, 2])
    assert res["mode"] == "analysis"
    card = res["cards"][0]
    assert card["verified"]["confirmed"] is True
    assert card["fix"]["ops_pretty"] == ["replace [164] And with Or"]


def test_best_unverified_survivor_when_everything_is_refuted():
    # first answer, refutation retry, escalation — all three refuted →
    # zero cards, but the top-ranked idea ships as best_unverified with
    # exactly which rows it failed to fix; the board can offer a re-run
    call = _fake([_reply(BAD_OPS), _reply(BAD_OPS), _reply(BAD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["llm_calls"] == 3
    assert res["cards"] == []
    b = res["best_unverified"]
    assert b is not None
    assert b["fix"]["ops"] == BAD_OPS
    assert b["fix"]["ops_pretty"] == ["set [16] Const attribute Value = 1"]
    assert b["verdict"]["still_failing"] == [0, 1]
    assert b["hint"]["suspect_region"]
    # the amber card itself explains the state — no extra note on the board
    assert not any("unverified" in n.lower() for n in res["notes"])
    assert all((d.get("ops_pretty") or []) for d in res["dropped_ideas"]), \
        "dropped ideas must show what they tried"


def test_no_best_unverified_without_any_valid_hypothesis():
    call = _fake(["nope", "still nope"])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["cards"] == []
    assert res["best_unverified"] is None


def test_failing_subcircuit_gates_the_parent_into_suggestions():
    # bug7: the calculator's bool_unit child carries its OWN testcase with
    # 2 failing rows — parent analysis is noise until the child is fixed,
    # so the run goes to the (free) suggestion branch, zero model calls
    parent = f"{_BENCH}/bug7_broken_child/tier3_calculator.dig"
    res = debug_circuit(parent, call=_never, use_manifest=False)
    assert res["mode"] == "lazy"
    assert res["llm_calls"] == 0
    assert [f["kind"] for f in res["gross_flags"]] == ["subcircuit_failing"]
    assert "bool_unit.dig" in res["gross_flags"][0]["detail"]
    assert [s["kind"] for s in res["suggestions"]] == ["subcircuit_failing"]
    assert "bottom-up" in res["suggestions"][0]["hint"]


# ---------------------------------------------------------------------------
# Coach-added rows: strict improvement, not perfection (the Mode B hand-off)
# ---------------------------------------------------------------------------

_BUG6 = f"{_BENCH}/bug6_hidden_mux_case3/uncovered_op_calculator.dig"
# the hidden-mux repair: in3 stops feeding from the stray Ground [23] and
# takes the boolean unit's Result, like in2 does
MUX_FIX = [{"op": "rewire_pin", "component_index": 14, "pin": "in3",
            "to": {"component_index": 9, "pin": "Result"}}]


def _coach_temp(tmp_path):
    """bug6 + ONE coach-style row appended to its testcase:
    '5 10 0 3 15 1 0 1' — Result/Zero/Bit0 assert the TRUE Op=3 values
    (the mux fix repairs them; pre-fix all four columns mismatch), but
    Carry is deliberately mis-guessed (1; truly 0 on both sides), so the
    row can never FULLY pass. Exactly the bug6 case-3 hand-off shape that
    used to refute the correct fix."""
    src = Path(_BUG6)
    shutil.copy(src.parent / "bool_unit.dig", tmp_path / "bool_unit.dig")
    text = src.read_text(encoding="utf-8")
    anchor = "0 0 0 2 0 0 1 0</dataString>"
    assert text.count(anchor) == 1
    out = tmp_path / "uncovered_op_calculator.dig"
    out.write_text(
        text.replace(anchor,
                     "0 0 0 2 0 0 1 0\n5 10 0 3 15 1 0 1</dataString>"),
        encoding="utf-8")
    return str(out)


def test_verify_ops_refutes_partial_fix_without_coach_targets(tmp_path):
    # baseline: no coach knowledge -> the imperfect Carry guess refutes
    # the CORRECT mux fix (this was the 90% case-3 failure)
    path = _coach_temp(tmp_path)
    v = verify_ops(path, "Testcase_25", MUX_FIX, [10], [10])
    assert v["confirmed"] is False
    assert v["still_failing"] == [10]


def test_coach_row_strict_improvement_confirms_with_residual(tmp_path):
    path = _coach_temp(tmp_path)
    v = verify_ops(path, "Testcase_25", MUX_FIX, [10], [10],
                   coach_targets={10: {"Result", "Carry", "Zero", "Bit0"}})
    assert v["confirmed"] is True
    assert v["still_failing"] == [] and v["regressions"] == []
    assert v["coach_residuals"] == {10: ["Carry"]}


def test_coach_row_must_improve_and_break_nothing(tmp_path):
    path = _coach_temp(tmp_path)
    # a do-nothing patch repairs no flagged column -> still refuted
    noop = [{"op": "change_attribute", "component_index": 23,
             "name": "Bits", "value": 4}]
    v = verify_ops(path, "Testcase_25", noop, [10], [10],
                   coach_targets={10: {"Result", "Carry", "Zero", "Bit0"}})
    assert v["confirmed"] is False and v["still_failing"] == [10]
    # a residual column OUTSIDE the originally-flagged set -> refuted too
    v2 = verify_ops(path, "Testcase_25", MUX_FIX, [10], [10],
                    coach_targets={10: {"Result", "Zero", "Bit0"}})
    assert v2["confirmed"] is False and v2["still_failing"] == [10]


def test_debug_circuit_judges_coach_rows_by_improvement(tmp_path):
    # end to end: the evaluator sweep finds only the coach row failing;
    # with coach_rows the correct fix earns a CONFIRMED card that names
    # the residual cell instead of being refuted by it
    path = _coach_temp(tmp_path)
    call = _fake([_reply(MUX_FIX)])
    res = debug_circuit(path, call=call, use_manifest=False,
                        coach_rows=[10])
    assert res["mode"] == "analysis"
    assert res["llm_calls"] == 1
    card = res["cards"][0]
    assert card["verified"]["confirmed"] is True
    assert card["verified"]["coach_residuals"] == {10: ["Carry"]}
    assert card["fix"]["ops"] == MUX_FIX
