"""
Grader flags are normalized to structured objects:
{"paragraph": 1-6|None, "quote": str, "issue": str}.
Plain-string flags (models ignoring the shape) must still survive.
"""

from dlc.llm.grade import _normalize_flag


def test_structured_flag_passes_through():
    f = _normalize_flag({
        "paragraph": 3,
        "quote": "the ROM outputs 0x99",
        "issue": "value not derivable from the ROM contents",
    })
    assert f == {
        "paragraph": 3,
        "quote": "the ROM outputs 0x99",
        "issue": "value not derivable from the ROM contents",
    }


def test_plain_string_flag_is_wrapped():
    f = _normalize_flag("asserts a carry chain the facts do not show")
    assert f == {
        "paragraph": None,
        "quote": "",
        "issue": "asserts a carry chain the facts do not show",
    }


def test_out_of_range_or_garbage_paragraph_becomes_none():
    assert _normalize_flag({"paragraph": 9, "issue": "x"})["paragraph"] is None
    assert _normalize_flag({"paragraph": "two", "issue": "x"})["paragraph"] is None
    assert _normalize_flag({"paragraph": None, "issue": "x"})["paragraph"] is None


def test_empty_issue_is_dropped():
    assert _normalize_flag({"paragraph": 2, "quote": "q", "issue": "  "}) is None
    assert _normalize_flag("   ") is None


def test_overlong_quote_is_truncated():
    f = _normalize_flag({"issue": "x", "quote": "q" * 500})
    assert len(f["quote"]) == 160


def test_grade_summary_normalizes_mixed_flags(monkeypatch):
    """End-to-end through grade_summary with a canned grader reply:
    structured + string flags both come out structured."""
    import dlc.llm.grade as grade_mod

    canned = {
        "function_accuracy": 18, "signal_flow_accuracy": 17,
        "signal_flow_completeness": 13, "goal_comparison": 15,
        "topology_accuracy": 10, "lecture_relevance": 8,
        "hallucination": False, "hallucinated_items": [],
        "flags": [
            {"paragraph": 3, "quote": "x = 9", "issue": "value not derived"},
            "stray string flag",
        ],
        "rationales": {},
    }
    import json as _json
    monkeypatch.setattr(
        grade_mod, "call_llm",
        lambda *a, **k: {"ok": True, "text": _json.dumps(canned),
                         "error": None, "usage": {}},
    )
    out = grade_mod.grade_summary(
        facts={"inputs": [], "outputs": [], "inventory": {}},
        summary_text="some summary", student_goal=None, test_summary=None,
    )
    assert out["ok"]
    assert out["flags"] == [
        {"paragraph": 3, "quote": "x = 9", "issue": "value not derived"},
        {"paragraph": None, "quote": "", "issue": "stray string flag"},
    ]
    topo = [s for s in out["sub_scores"] if s["key"] == "topology_accuracy"][0]
    assert topo["score"] == 10
