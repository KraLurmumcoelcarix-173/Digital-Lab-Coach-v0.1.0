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
    # refuted on a ROM that HOLDS words, the retry must carry the
    # machine fact that the stored words are consistent and point at
    # the address/select path instead. r37 refinement: a Data rewrite
    # on an EMPTY ROM must NOT be steered off — the missing words ARE
    # the bug there (s008's unprogrammed decode ROM), so the steer's
    # premise ("the stored words satisfy every passing row") is false.
    from types import SimpleNamespace
    from dlc.l3.debugger import _touches_stored_data, _refutation_block

    data_op = [{"op": "change_attribute", "component_index": 0,
                "name": "Data", "value": "82,86"}]
    stored = SimpleNamespace(components=[
        SimpleNamespace(attributes={"Data": "1,2,3"})])
    empty = SimpleNamespace(components=[SimpleNamespace(attributes={})])
    assert _touches_stored_data([data_op], stored) is True
    assert _touches_stored_data([data_op], empty) is False
    assert _touches_stored_data([data_op], None) is True  # unknown: steer
    assert _touches_stored_data([GOOD_OPS], stored) is False

    verdict = {"apply_ok": True, "still_failing": [0], "regressions": [],
               "details": {}, "warning": None}
    assert "MACHINE FACT" in _refutation_block(data_op, verdict, stored)
    assert "MACHINE FACT" not in _refutation_block(data_op, verdict, empty)
    # end to end: a refuted NON-data fix never carries the steer
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


# ---------------------------------------------------------------------------
# r37: runaway firewalls — build refusal, official child tests, stop budget
# ---------------------------------------------------------------------------

def test_all_rows_error_returns_build_refused_lazy(monkeypatch):
    # jar-probed (r37, s008 tree): a dangling tunnel makes Digital refuse
    # the whole build, so EVERY per-row result is an error. Analysis on
    # an unbuildable circuit would only guess (and its verifier would
    # refute every idea through the same refusal) — the run must stop
    # free, pointing at the Layer 1 errors.
    from types import SimpleNamespace
    monkeypatch.setattr(debugger, "find_digital_jar", lambda: "fake.jar")
    monkeypatch.setattr(
        debugger, "per_row_run_auto",
        lambda spec, path, jar_path=None: [
            SimpleNamespace(status="error", row_index=i,
                            error_message="A tunnel rd2 is not connected!",
                            mismatches=[])
            for i in range(4)
        ])
    res = debug_circuit(_BUG3, call=_never, use_manifest=False)
    assert res["mode"] == "lazy"
    assert res["llm_calls"] == 0
    assert [f["kind"] for f in res["gross_flags"]] == ["build_refused"]
    assert "rd2" in res["gross_flags"][0]["detail"]
    assert [s["kind"] for s in res["suggestions"]] == ["build_refused"]
    assert "Layer 1" in res["suggestions"][0]["hint"]


def test_child_failing_official_tests_gates_the_parent(tmp_path):
    # r37 (s008): the real bug lives in control-unit.dig, which carries NO
    # testcase of its own — the official rows must be injected for the
    # children gate to see it, or Mode A burns model calls on parent
    # wiring that can never fix a child. Child = a synthetic control unit
    # whose outputs are all grounded (fails the official add row).
    child_parts = []
    for i, (label, bits) in enumerate(
            [("opcode", 7), ("funct3", 3), ("funct7", 7)]):
        child_parts.append(
            f'<visualElement><elementName>In</elementName><elementAttributes>'
            f'<entry><string>Label</string><string>{label}</string></entry>'
            f'<entry><string>Bits</string><int>{bits}</int></entry>'
            f'</elementAttributes><pos x="0" y="{i * 60}"/></visualElement>')
    outs = ["RegWrite", "ALUSrc", "ImmSrc1", "ImmSrc0",
            "ALUOp3", "ALUOp2", "ALUOp1", "ALUOp0"]
    wires = []
    for i, label in enumerate(outs):
        y = 300 + i * 40
        child_parts.append(
            f'<visualElement><elementName>Ground</elementName>'
            f'<elementAttributes/><pos x="160" y="{y}"/></visualElement>')
        child_parts.append(
            f'<visualElement><elementName>Out</elementName><elementAttributes>'
            f'<entry><string>Label</string><string>{label}</string></entry>'
            f'</elementAttributes><pos x="200" y="{y}"/></visualElement>')
        wires.append(f'<wire><p1 x="160" y="{y}"/><p2 x="200" y="{y}"/></wire>')
    child = ('<?xml version="1.0" encoding="utf-8"?><circuit><version>2'
             '</version><attributes/><visualElements>'
             + "".join(child_parts)
             + '</visualElements><wires>' + "".join(wires)
             + '</wires></circuit>')
    (tmp_path / "control-unit.dig").write_text(child, encoding="utf-8")

    parent = ('<?xml version="1.0" encoding="utf-8"?><circuit><version>2'
              '</version><attributes/><visualElements>'
              '<visualElement><elementName>In</elementName>'
              '<elementAttributes><entry><string>Label</string>'
              '<string>A</string></entry></elementAttributes>'
              '<pos x="0" y="0"/></visualElement>'
              '<visualElement><elementName>Out</elementName>'
              '<elementAttributes><entry><string>Label</string>'
              '<string>X</string></entry></elementAttributes>'
              '<pos x="200" y="0"/></visualElement>'
              '<visualElement><elementName>control-unit.dig</elementName>'
              '<elementAttributes/><pos x="0" y="200"/></visualElement>'
              '<visualElement><elementName>Testcase</elementName>'
              '<elementAttributes><entry><string>Testdata</string>'
              '<testData><dataString>A X\n0 0</dataString></testData>'
              '</entry></elementAttributes><pos x="0" y="400"/>'
              '</visualElement>'
              '</visualElements><wires><wire><p1 x="0" y="0"/>'
              '<p2 x="200" y="0"/></wire></wires></circuit>')
    p = tmp_path / "top.dig"
    p.write_text(parent, encoding="utf-8")

    res = debug_circuit(str(p), call=_never, use_manifest=False)
    assert res["mode"] == "lazy"
    assert res["llm_calls"] == 0
    assert [f["kind"] for f in res["gross_flags"]] == [
        "subcircuit_failing_official"]
    assert "control-unit.dig" in res["gross_flags"][0]["detail"]
    assert "official" in res["gross_flags"][0]["detail"]
    # the tool-owned injected temp must not survive the gate
    assert not list(tmp_path.glob(".dlc_injected__*"))


def test_stop_condition_returns_best_solution_early(monkeypatch):
    # r37 stop condition: once _MAX_REFUTED_IDEAS ideas are refuted with
    # no confirmed card, the run stops spending (no refute retry, no
    # escalation) and ships the best unverified idea — the benchmark's
    # "best solution" hard trigger reads stopped_early.
    monkeypatch.setattr(debugger, "_MAX_REFUTED_IDEAS", 1)
    call = _fake([_reply(BAD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["mode"] == "analysis"
    assert res["llm_calls"] == 1           # no retry, no escalation
    assert res["stopped_early"] is True
    assert res["refuted_ideas"] == 1
    assert res["cards"] == []
    assert res["best_unverified"] is not None
    assert res["best_unverified"]["fix"]["ops"] == BAD_OPS
    assert any("stopped after 1 refuted" in n for n in res["notes"])
    assert len(res["timings"]["llm_s"]) == 1
    assert len(res["timings"]["verify_s"]) == 1
    assert res["timings"]["total_s"] >= 0


def test_unstopped_run_reports_flag_false():
    call = _fake([_reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False,
                        failing_indices=[0, 1])
    assert res["stopped_early"] is False
    assert res["refuted_ideas"] == 0
    assert res["cards"] and res["cards"][0]["verified"]["confirmed"]


def test_all_rows_error_with_unbound_columns_gets_rename_guidance(
        monkeypatch, tmp_path):
    # r37 (s008 raw control unit): Digital refuses with "Test signal
    # funct3 not found" when the testcase columns don't match the port
    # labels. That refusal is an INTERFACE problem, not wiring — the
    # lazy result must say "rename your ports", not "fix Layer 1".
    from types import SimpleNamespace
    p = tmp_path / "renamed.dig"
    p.write_text(
        '<?xml version="1.0" encoding="utf-8"?><circuit><version>2'
        '</version><attributes/><visualElements>'
        '<visualElement><elementName>In</elementName><elementAttributes>'
        '<entry><string>Label</string><string>A</string></entry>'
        '</elementAttributes><pos x="0" y="0"/></visualElement>'
        '<visualElement><elementName>Out</elementName><elementAttributes>'
        '<entry><string>Label</string><string>X</string></entry>'
        '</elementAttributes><pos x="200" y="0"/></visualElement>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string><testData>'
        '<dataString>A Q\n0 0</dataString></testData></entry>'
        '</elementAttributes><pos x="0" y="200"/></visualElement>'
        '</visualElements><wires><wire><p1 x="0" y="0"/>'
        '<p2 x="200" y="0"/></wire></wires></circuit>',
        encoding="utf-8")
    monkeypatch.setattr(debugger, "find_digital_jar", lambda: "fake.jar")
    monkeypatch.setattr(
        debugger, "per_row_run_auto",
        lambda spec, path, jar_path=None: [
            SimpleNamespace(status="error", row_index=0,
                            error_message="Test signal Q not found in "
                                          "the circuit!",
                            mismatches=[])
        ])
    res = debug_circuit(str(p), call=_never, use_manifest=False)
    assert res["mode"] == "lazy"
    assert res["llm_calls"] == 0
    assert [f["kind"] for f in res["gross_flags"]] == ["unbound_columns"]
    assert "'Q'" in res["gross_flags"][0]["detail"]
    assert [s["kind"] for s in res["suggestions"]] == ["unbound_columns"]
    assert "Rename" in res["suggestions"][0]["hint"]


def test_control_unit_files_skip_the_lazy_gate(tmp_path):
    # r37.1 instructor ruling (marked temporary): control-unit files
    # always analyze — the decode-table lab must reach Mode A no matter
    # how gross the failure shape looks. Same content under any other
    # name keeps every ratified lazy bar.
    from dlc.l3.debugger import _lazy_exempt_name
    assert _lazy_exempt_name("control-unit.dig") is True
    assert _lazy_exempt_name("ControlUnit.dig") is True
    assert _lazy_exempt_name("/x/y/.dlc_injected__control-unit.dig") is True
    assert _lazy_exempt_name("cpu.dig") is False
    assert _lazy_exempt_name("register-file.dig") is False
    assert _lazy_exempt_name(None) is False

    # 160+ component LED lab in a would-be-lazy shape: 4 of 5 rows
    # failing on scattered single columns (low pass rate, no frozen
    # trunk). Under its own name: lazy. Renamed control-unit.dig: the
    # SAME verdict shape goes to analysis.
    led5 = Path(f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
                f"wrong_bool_LED5.dig")
    cells = {0: [{"column": "Fa", "expected": "1", "found": "0"}],
             1: [{"column": "Fb", "expected": "1", "found": "0"}],
             2: [{"column": "Fe", "expected": "1", "found": "0"}],
             3: [{"column": "Fg", "expected": "1", "found": "0"}]}

    other = tmp_path / "led.dig"
    other.write_text(led5.read_text(encoding="utf-8"), encoding="utf-8")
    res = debug_circuit(str(other), call=_never, use_manifest=False,
                        failing_indices=[0, 1, 2, 3], jar_mismatches=cells)
    assert res["mode"] == "lazy"

    cu = tmp_path / "control-unit.dig"
    cu.write_text(led5.read_text(encoding="utf-8"), encoding="utf-8")
    call = _fake(["nope", "still nope"])
    res2 = debug_circuit(str(cu), call=call, use_manifest=False,
                         failing_indices=[0, 1, 2, 3], jar_mismatches=cells)
    assert res2["mode"] == "analysis"
    assert any("lazy-gate checks skipped" in n for n in res2["notes"])


def test_rom_injected_note_rides_every_cluster_prompt():
    # r38: when the analyzed copy runs with grader-injected rom content,
    # every cluster prompt carries the [ROM NOTE] so the model never
    # proposes Data changes against official words. Plain runs don't.
    # the template MENTIONS "[ROM NOTE]" in its reading guide, so the
    # live block is recognized by its unique closing sentence
    marker = "still has that ROM unprogrammed"
    call = _fake([_reply(GOOD_OPS)])
    debug_circuit(_BUG3, call=call, use_manifest=False,
                  failing_indices=[0, 1], rom_injected=True)
    assert marker in call.log[0]

    call2 = _fake([_reply(GOOD_OPS)])
    debug_circuit(_BUG3, call=call2, use_manifest=False,
                  failing_indices=[0, 1])
    assert marker not in call2.log[0]


def test_prompt_checks_stored_data_first_not_last():
    # r38 instructor ruling: the old "ROM data is a last resort" bias is
    # gone; unverified stored data is now checked FIRST, and only
    # grader-injected content is off limits (via the [ROM NOTE]).
    from dlc.l3.debugger import _load_prompt
    text = _load_prompt()
    assert "LAST RESORT" not in text
    assert "CHECK IT FIRST" in text
    assert "[ROM NOTE]" in text
