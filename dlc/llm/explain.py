"""
Conceptual circuit-summary generator:

Gates BEFORE calling the LLM:
  G1. Many L1 errors -> "fix structural issues first".
  G2. A few L1 issues remain -> precheck text listing top items.
  G3. Tests failed -> guide to Layer 3 debug.

If none fire, builds a prompt from compact CircuitFacts + the
student goal + the COMP 311 syllabus, calls LLM, sanitizes the
response.
"""

import json
from pathlib import Path

from dlc.llm.client import call_llm
from dlc.llm.guard import sanitize_output


_PROMPT_DIR = Path(__file__).parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


SYLLABUS_311 = """\
Lecture 4: Transistors, Intro to CMOS Gates
Lecture 5: Logic Gates
Lecture 6-7: Multiplexers, Gate Minimization 
Lecture 8: Karnaugh Maps (K-Maps)
Lecture 9: Circuit Design: A Priority Encoder, Basic Adder Circuits
Lecture 10: Arithmetic Circuits (Adder/Subtractors, Shifters), R-Type and I-Type Instructions
Lecture 11: Introducing the ALU
Lecture 12: Building the ALU, The Register File
Lecture 13: Intro to Timing Analysis
Lecture 14: Computer Performance Metrics, More Timing Analysis
Lecture 15: Intro to Pipelining
Lecture 16: Loads, Stores, Memory
Lecture 17: More RISC-V Programming, Pipelining a CPU
Lecture 18: The 5 Stage RISC-V CPU
Lecture 19: RISC-V Programming with Procedures
Lecture 20: More Stack Review and RISC-V Calling Convention
Lecture 21: Pipeline Hazards
Lecture 22: Source Bypassing/Forwarding
Lecture 23: Intro to Memory
Lecture 24: Caching
Lecture 25: Caching, Stack Review, Hardware Security Conceptual expanding
"""


def _compact_facts(facts: dict) -> dict:
    return {
        "inventory": facts.get("inventory", {}),
        "inputs": [
            {"label": p.get("label"), "bits": p.get("bits")}
            for p in facts.get("inputs", [])
        ],
        "outputs": [
            {"label": p.get("label"), "bits": p.get("bits")}
            for p in facts.get("outputs", [])
        ],
        "subcircuits": [
            {"reference": s.get("reference"),
             "resolved": s.get("resolved")}
            for s in facts.get("subcircuits", [])
        ],
        "has_clock": "Clock" in (facts.get("inventory", {}) or {}),
        "has_register": "Register" in (facts.get("inventory", {}) or {}),
        "has_rom": "ROM" in (facts.get("inventory", {}) or {}),
    }


_GATE_FIX_FIRST = 3
_GATE_SOFT_LIMIT = 3


def _classify_issues(issues: list[dict]) -> tuple[int, int]:
    n_err = sum(1 for i in issues if i.get("severity") == "error")
    n_warn = sum(1 for i in issues if i.get("severity") == "warning")
    return n_err, n_warn


def _gate_text_for_issues(issues: list[dict]) -> str | None:
    n_err, n_warn = _classify_issues(issues)
    if n_err >= _GATE_FIX_FIRST:
        return (
            f"Your circuit has {n_err} Layer 1 errors and {n_warn} "
            f"warnings. Fix the structural issues on the Dashboard tab "
            f"before asking Layer 2 for a conceptual summary - major "
            f"structural bugs make any functional explanation unreliable."
        )
    if n_err > 0 or n_warn > _GATE_SOFT_LIMIT:
        top = "; ".join(f"{i.get('kind', '?')}" for i in issues[:4])
        return (
            f"Quick precheck before conceptual summary: {n_err} "
            f"error(s), {n_warn} warning(s) remain in Layer 1. "
            f"Top items: {top}. Fix these on the Dashboard tab and "
            f"come back."
        )
    return None


def _gate_text_for_tests(test_summary: str | None) -> str | None:
    if not test_summary:
        return None
    s = test_summary.lower()
    if "failed" in s or "did not pass" in s:
        return (
            f"Your circuit currently has failing tests ({test_summary}). "
            f"For a Layer 2 conceptual summary to be useful the test "
            f"bench should pass first. Head to the L3 Coach tab to "
            f"debug failing rows."
        )
    return None


def explain_circuit(
    facts: dict,
    issues: list[dict],
    test_summary: str | None,
    student_goal: str | None,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    gate = _gate_text_for_issues(issues)
    if gate is None:
        gate = _gate_text_for_tests(test_summary)
    if gate is not None:
        return {
            "ok": True, "text": None, "gate_message": gate,
            "error": None, "usage": None, "model": None,
        }

    compact = _compact_facts(facts)
    template = _load_prompt("layer2_circuit_summary_v1.txt")
    prompt = template.format(
        circuit_facts_json=json.dumps(compact, indent=2),
        test_results_summary=test_summary or "(tests not yet run)",
        student_goal_or_none=(student_goal.strip() if student_goal else "(none)"),
        lectures_list=SYLLABUS_311,
    )

    from dlc.llm.client import DEFAULT_MODEL
    result = call_llm(
        prompt,
        api_key=api_key,
        model=model or DEFAULT_MODEL,
        system=(
            "You are a circuit reasoning assistant for UNC COMP 311. "
            "Use plain text only. No markdown, no bullets, no headers."
        ),
    )
    return {
        "ok": result["ok"],
        "text": sanitize_output(result["text"]) if result["text"] else None,
        "gate_message": None,
        "error": result["error"],
        "usage": result["usage"],
        "model": result["model"],
    }