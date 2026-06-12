"""Layer 2 summary grader."""

import json
import re
from pathlib import Path

from dlc.llm.client import call_llm
from dlc.llm.explain import _compact_facts

_PROMPT_DIR = Path(__file__).parent.parent.parent / "prompts"
_GRADER_PROMPT = "layer2_grader_v1.txt"

DEFAULT_GRADER_MODEL = "claude-sonnet-4-6"
HALLUCINATION_CAP = 60

RUBRIC = [
    ("function_accuracy",        "Function description",     20, "LLM",
     "Did paragraph 1 correctly name what the circuit does as a whole?"),
    ("signal_flow_accuracy",     "Signal-flow accuracy",     20, "LLM",
     "Does the paragraph 3 example trace match how the circuit actually computes?"),
    ("signal_flow_completeness", "Signal-flow completeness", 15, "Hybrid",
     "Are the key components named in the trace, and all clock cycles shown when sequential?"),
    ("goal_comparison",          "Goal comparison",          15, "LLM",
     "If a goal was given, did paragraph 4 compare it against the right facts? "
     "(Full marks for a correct N/A when no goal was given.)"),
    ("key_component_mention",    "Key-component mention",    10, "Deterministic",
     "Share of top-level I/O labels and non-Tunnel/Const components mentioned at least once."),
    ("topology_accuracy",        "Topology accuracy",        10, "LLM",
     "Paragraph 5: was the architectural pattern named correctly (or N/A correctly stated)? "
     "Full marks when the needed path (e.g. through the mux) was already traced in paragraph 3."),
    ("lecture_relevance",        "Lecture-tag relevance",    10, "LLM",
     "Paragraph 6: are the 1-3 cited lectures each defensibly relevant?"),
]
_MAXES = {key: mx for (key, _l, mx, _s, _d) in RUBRIC}

_NON_KEY_ELEMENTS = {
    "Tunnel", "Const", "Wire", "Ground", "VDD", "Text", "Testcase", "Splitter",
}

_ELEMENT_TERMS = {
    "Add": ["adder", "add"],
    "Multiplexer": ["multiplexer", "mux"],
    "Register": ["register"],
    "ROM": ["rom", "read-only memory"],
    "RAM": ["ram"],
    "Comparator": ["comparator", "compare"],
    "BarrelShifter": ["barrel shifter", "shifter", "shift"],
    "PriorityEncoder": ["priority encoder", "encoder"],
    "Decoder": ["decoder"],
    "Counter": ["counter"],
    "And": ["and gate", "and"],
    "Or": ["or gate", "or"],
    "XOr": ["xor", "exclusive or"],
    "Not": ["not gate", "inverter"],
    "NAnd": ["nand"],
    "NOr": ["nor"],
    "XNOr": ["xnor"],
    "Clock": ["clock"],
}


def _key_component_targets(facts: dict) -> list[tuple[str, str]]:
    """Checkable mention targets: ("label"|"element", name)."""
    targets: list[tuple[str, str]] = []
    for pin in (facts.get("inputs", []) or []):
        if pin.get("label"):
            targets.append(("label", str(pin["label"])))
    for pin in (facts.get("outputs", []) or []):
        if pin.get("label"):
            targets.append(("label", str(pin["label"])))
    for elem in (facts.get("inventory", {}) or {}):
        if elem not in _NON_KEY_ELEMENTS:
            targets.append(("element", elem))
    return targets


def _mentions(text_low: str, kind: str, name: str) -> bool:
    terms = [name.lower()] if kind == "label" else _ELEMENT_TERMS.get(name, [name.lower()])
    return any(re.search(r"\b" + re.escape(t) + r"\b", text_low) for t in terms)


def _key_component_mention_score(facts: dict, summary_text: str) -> tuple[int, str]:
    targets = _key_component_targets(facts)
    mx = _MAXES["key_component_mention"]
    if not targets:
        return mx, "No key components to mention."
    low = summary_text.lower()
    hits = sum(1 for (kind, name) in targets if _mentions(low, kind, name))
    rate = hits / len(targets)
    return round(rate * mx), f"{hits}/{len(targets)} key items mentioned ({round(rate * 100)}%)."


def _parse_grader_json(text: str) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_flag(f) -> dict | None:
    """Flags are structured grader feedback about THE SUMMARY TEXT:
    {"paragraph": 1-6|None, "quote": str, "issue": str}. Models that
    ignore the shape and emit plain strings are normalized too."""
    if isinstance(f, dict):
        issue = str(f.get("issue", "")).strip()
        if not issue:
            return None
        para = f.get("paragraph")
        try:
            para = int(para)
        except (TypeError, ValueError):
            para = None
        if para is not None and not (1 <= para <= 6):
            para = None
        return {
            "paragraph": para,
            "quote": str(f.get("quote", "") or "").strip()[:160],
            "issue": issue,
        }
    s = str(f).strip()
    if not s:
        return None
    return {"paragraph": None, "quote": "", "issue": s}


def _band(total: int) -> str:
    if total <= 60:
        return "red"
    if total <= 90:
        return "yellow"
    return "green"


def _load_prompt() -> str:
    return (_PROMPT_DIR / _GRADER_PROMPT).read_text(encoding="utf-8")


def grade_summary(
    facts: dict,
    summary_text: str,
    student_goal: str | None,
    test_summary: str | None,
    *,
    api_key: str | None = None,
    grader_model: str | None = None,
) -> dict:
    grader_model = grader_model or DEFAULT_GRADER_MODEL
    if not summary_text or not summary_text.strip():
        return {"ok": False, "error": "No summary text to grade.", "total": None,
                "sub_scores": [], "grader_model": grader_model, "usage": None}

    # Deterministic sub-score (also handed to the grader to anchor completeness).
    det_score, det_rationale = _key_component_mention_score(facts, summary_text)
    key_list = ", ".join(sorted({n for (_k, n) in _key_component_targets(facts)})) or "(none)"

    prompt = (
        _load_prompt()
        .replace("{{CIRCUIT_FACTS}}", json.dumps(_compact_facts(facts), indent=2))
        .replace("{{STUDENT_GOAL}}", student_goal.strip() if student_goal else "(none)")
        .replace("{{TEST_RESULTS}}", test_summary or "(tests not yet run)")
        .replace("{{KEY_COMPONENTS}}", key_list)
        .replace("{{SUMMARY}}", summary_text.strip())
    )
    result = call_llm(
        prompt, api_key=api_key, model=grader_model,
        system=("You are a strict grader. Output ONLY a single JSON object, "
                "with no prose and no markdown code fences."),
    )
    if not result["ok"]:
        return {"ok": False, "error": result["error"], "total": None,
                "sub_scores": [], "grader_model": grader_model, "usage": result["usage"]}

    parsed = _parse_grader_json(result["text"])
    if parsed is None:
        return {"ok": False, "error": "Grader did not return parseable JSON.",
                "total": None, "sub_scores": [], "grader_model": grader_model,
                "usage": result["usage"], "raw": result["text"]}

    rationales = parsed.get("rationales", {}) or {}
    sub_scores = []
    total = 0
    for key, label, mx, source, desc in RUBRIC:
        if key == "key_component_mention":
            score, why = det_score, det_rationale
        else:
            try:
                score = int(round(float(parsed.get(key, 0))))
            except (TypeError, ValueError):
                score = 0
            score = max(0, min(mx, score))                
            why = str(rationales.get(key, "")).strip()
        total += score
        sub_scores.append({
            "key": key, "label": label, "score": score, "max": mx,
            "source": source, "description": desc, "rationale": why,
        })

    hallucination = bool(parsed.get("hallucination", False))
    hallucinated = parsed.get("hallucinated_items", []) or []
    flags = []
    for f in (parsed.get("flags", []) or []):
        nf = _normalize_flag(f)
        if nf is not None:
            flags.append(nf)
    raw_total, capped = total, False
    if hallucination and total > HALLUCINATION_CAP:
        total, capped = HALLUCINATION_CAP, True

    return {
        "ok": True, "error": None,
        "total": total, "raw_total": raw_total, "band": _band(total),
        "capped": capped, "hallucination": hallucination,
        "hallucinated_items": hallucinated,
        "flags": flags,
        "sub_scores": sub_scores,
        "grader_model": grader_model, "usage": result["usage"],
    }
