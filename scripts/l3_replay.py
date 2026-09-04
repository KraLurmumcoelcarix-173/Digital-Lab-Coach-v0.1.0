"""
Layer 3 record/replay — keep previously solved Mode A cases solved.

    uv run python scripts/l3_replay.py CASES.json            # replay, no model calls
    uv run python scripts/l3_replay.py CASES.json --record   # run live, store replies

A cases file:

    {"cases": [
      {"name": "bug3 carry-in",
       "circuit": "bug3_wrong_cin/Wrong_cin.dig",      # relative to this file
       "spec_index": 0,
       "replies": [ {...model JSON...}, ... ],          # filled by --record
       "expect": {"mode": "analysis", "confirmed": true, "min_cards": 1,
                  "max_llm_calls": 2, "max_wall_s": 60}}
    ]}

Replay feeds the recorded replies to the coordinator in order and checks
the outcome against `expect`; the jar verifies fixes exactly as in the
app. Record runs the real model, writes the replies back, and — when a
case has no `expect` yet — stores the observed outcome as its baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dlc.l3.debugger import debug_circuit          # noqa: E402
from dlc.llm.client import call_llm                # noqa: E402
from dlc.testing.runner import find_digital_jar    # noqa: E402


def recorded_call(replies: list):
    """A model stand-in that answers with the recorded replies, in order."""
    queue = [r if isinstance(r, str) else json.dumps(r) for r in replies]

    def call(prompt, **_kw):
        call.asked += 1
        if not queue:
            return {"ok": True, "text": "{}", "error": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "model": "replay (no reply recorded for this call)"}
        return {"ok": True, "text": queue.pop(0), "error": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "model": "replay"}
    call.asked = 0
    return call


def recording_call(store: list):
    """The real model, with every reply text captured into `store`."""
    def call(prompt, **kw):
        res = call_llm(prompt, **kw)
        if res.get("ok") and res.get("text"):
            store.append(res["text"])
        return res
    return call


def observed(res: dict, wall: float) -> dict:
    cards = res.get("cards") or []
    return {
        "mode": res.get("mode"),
        "confirmed": any((c.get("verified") or {}).get("confirmed") for c in cards),
        "cards": len(cards),
        "llm_calls": int(res.get("llm_calls") or 0),
        "wall_s": round(wall, 1),
        "warning": res.get("warning"),
    }


def check(expect: dict, got: dict) -> list[str]:
    problems = []
    if "mode" in expect and got["mode"] != expect["mode"]:
        problems.append(f"mode {got['mode']!r} != {expect['mode']!r}")
    if "confirmed" in expect and got["confirmed"] != expect["confirmed"]:
        problems.append(f"confirmed {got['confirmed']} != {expect['confirmed']}")
    if got["cards"] < int(expect.get("min_cards", 0)):
        problems.append(f"cards {got['cards']} < {expect['min_cards']}")
    if "max_llm_calls" in expect and got["llm_calls"] > expect["max_llm_calls"]:
        problems.append(f"llm_calls {got['llm_calls']} > {expect['max_llm_calls']}")
    if "max_wall_s" in expect and got["wall_s"] > expect["max_wall_s"]:
        problems.append(f"wall {got['wall_s']}s > {expect['max_wall_s']}s")
    return problems


def run_cases(cases_path: Path, *, record: bool = False,
              jar: str | None = None, model: str | None = None,
              out=print) -> tuple[int, int]:
    doc = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = doc.get("cases") or []
    jar = jar or find_digital_jar()
    if not jar:
        out("no Digital.jar configured — set DIGITAL_JAR or pass --jar")
        return 0, len(cases)
    passed = failed = 0
    for case in cases:
        name = case.get("name") or case.get("circuit")
        circuit = (cases_path.parent / case["circuit"]).resolve()
        if not circuit.is_file():
            out(f"FAIL  {name}: circuit not found: {circuit}")
            failed += 1
            continue
        store: list = []
        call = recording_call(store) if record else recorded_call(case.get("replies") or [])
        t = time.perf_counter()
        try:
            res = debug_circuit(str(circuit), spec_index=int(case.get("spec_index", 0)),
                                jar_path=jar, call=call, model=model)
        except Exception as exc:                      # noqa: BLE001
            out(f"FAIL  {name}: coordinator raised {type(exc).__name__}: {exc}")
            failed += 1
            continue
        got = observed(res, time.perf_counter() - t)
        if record:
            case["replies"] = [json.loads(s) if _is_json(s) else s for s in store]
            if not case.get("expect"):
                case["expect"] = {"mode": got["mode"], "confirmed": got["confirmed"],
                                  "min_cards": got["cards"],
                                  "max_llm_calls": got["llm_calls"] + 1,
                                  "max_wall_s": max(60, int(got["wall_s"] * 2))}
        problems = check(case.get("expect") or {}, got)
        line = (f"{name}: mode={got['mode']} confirmed={got['confirmed']} "
                f"cards={got['cards']} llm_calls={got['llm_calls']} wall={got['wall_s']}s")
        if problems:
            out(f"FAIL  {line} — " + "; ".join(problems))
            failed += 1
        else:
            out(f"PASS  {line}")
            passed += 1
    if record:
        cases_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        out(f"recorded {len(cases)} case(s) into {cases_path}")
    out(f"{passed} passed, {failed} failed")
    return passed, failed


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases", help="cases JSON file")
    ap.add_argument("--record", action="store_true",
                    help="run the real model and store its replies")
    ap.add_argument("--jar", help="Digital.jar path (default: configured jar)")
    ap.add_argument("--model", help="model name for --record (default: configured)")
    args = ap.parse_args(argv)
    _passed, failed = run_cases(Path(args.cases), record=args.record,
                                jar=args.jar, model=args.model)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
