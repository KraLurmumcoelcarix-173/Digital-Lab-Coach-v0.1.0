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
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["mode"] == "analysis"
    assert res["llm_calls"] == 1
    assert len(res["cards"]) == 1
    card = res["cards"][0]
    assert card["rank"] == 1
    assert card["cluster_rows"] == [0, 1, 2, 3]
    assert card["verified"] == {"confirmed": True, "runner": "evaluator",
                                "regressions": []}
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
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["llm_calls"] == 2
    assert "FORMAT RETRY" in call.log[1]
    assert len(res["cards"]) == 1 and res["cards"][0]["verified"]["confirmed"]


def test_double_garbage_becomes_dropped_idea():
    call = _fake(["nope", "still nope"])
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["llm_calls"] == 2
    assert res["cards"] == []
    assert [d["reason"] for d in res["dropped_ideas"]] == ["invalid_response"]


def test_refuted_fix_earns_one_retry_with_evidence():
    call = _fake([_reply(BAD_OPS), _reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["llm_calls"] == 2
    # the template mentions "[REFUTED ATTEMPT]" itself, so the real
    # refutation block is recognized by its payload keys
    assert '"refuted_ops"' in call.log[1]
    assert '"still_failing"' in call.log[1]
    assert len(res["cards"]) == 1
    assert res["cards"][0]["fix"]["ops"] == GOOD_OPS


def test_unknown_op_is_a_format_error_then_dropped():
    bad = _reply(GOOD_OPS)
    bad["fix"]["ops"] = [{"op": "explode_everything"}]
    call = _fake([bad, "garbage"])
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["llm_calls"] == 2
    assert "unknown op" in call.log[1]
    assert res["cards"] == []
    assert res["dropped_ideas"][0]["reason"] == "invalid_response"


@_needs_jar
def test_bug3_confirms_through_real_digital(monkeypatch):
    monkeypatch.setattr(debugger, "find_digital_jar",
                        lambda: find_digital_jar())
    call = _fake([_reply(GOOD_OPS)])
    res = debug_circuit(_BUG3, call=call, use_manifest=False)
    assert res["mode"] == "analysis"
    assert res["row_verdict_runner"] == "digital"
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
