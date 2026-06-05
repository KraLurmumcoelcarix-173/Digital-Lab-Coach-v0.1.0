

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import sys
import time
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.facts.extractor import extract_facts
from dlc.analyzer import check_all_l1
from dlc.web.graph_export import circuit_summary
from dlc.llm.explain import explain_circuit
from dlc.llm.grade import grade_summary
from dlc.llm.grade import RUBRIC
from dlc.evaluator import config as C

_SUBSCORE_KEYS = [k for (k, *_rest) in RUBRIC]

CSV_FIELDS = [
    "timestamp", "model", "circuit", "goal_condition", "run_idx",
    "gen_ok", "gen_error", "gate_message", "gen_ms",
    "gen_in_tokens", "gen_out_tokens",
    "grade_total", "grade_raw_total", "grade_band", "grade_capped",
    "hallucination", "grade_ms", "grader_model", "grade_error",
    *[f"sub_{k}" for k in _SUBSCORE_KEYS],
    "summary_text",
]


def _facts_and_issues(path: str):
    circ = parse_dig_file(path)
    nl = build_netlist(circ)
    g = build_signal_graph(circ, nl)
    try:
        facts = extract_facts(circ, netlist=nl, graph=g).to_dict()
    except Exception:
        facts = circuit_summary(circ, nl)
    issues = check_all_l1(circ).to_dict()["issues"]
    return facts, issues


def run_one(model: str, circuit_path: str, goal: str | None,
            grader_model: str) -> dict:
    """Generate + grade a single cell. Never raises; errors go in the row."""
    row = {f: "" for f in CSV_FIELDS}
    row["timestamp"] = _dt.datetime.now().isoformat(timespec="seconds")
    row["model"] = model
    row["circuit"] = Path(circuit_path).name
    row["goal_condition"] = "goal" if goal else "no_goal"

    try:
        facts, issues = _facts_and_issues(circuit_path)
    except Exception as exc:
        row["gen_ok"] = False
        row["gen_error"] = f"facts failed: {type(exc).__name__}: {exc}"
        return row

    t0 = time.time()
    summ = explain_circuit(facts=facts, issues=issues,
                           test_summary=C.TEST_SUMMARY, student_goal=goal,
                           model=model)
    row["gen_ms"] = round((time.time() - t0) * 1000)
    row["gen_ok"] = bool(summ.get("ok"))
    row["gen_error"] = summ.get("error") or ""
    row["gate_message"] = (summ.get("gate_message") or "")[:200]
    usage = summ.get("usage") or {}
    row["gen_in_tokens"] = usage.get("input_tokens", "")
    row["gen_out_tokens"] = usage.get("output_tokens", "")
    text = summ.get("text")
    row["summary_text"] = (text or "").replace("\r", " ")

    if not (summ.get("ok") and text):
        return row

    t1 = time.time()
    grade = grade_summary(facts=facts, summary_text=text, student_goal=goal,
                          test_summary=C.TEST_SUMMARY, grader_model=grader_model)
    row["grade_ms"] = round((time.time() - t1) * 1000)
    row["grader_model"] = grade.get("grader_model", grader_model)
    if not grade.get("ok"):
        row["grade_error"] = grade.get("error") or "grade failed"
        return row
    row["grade_total"] = grade.get("total")
    row["grade_raw_total"] = grade.get("raw_total")
    row["grade_band"] = grade.get("band")
    row["grade_capped"] = grade.get("capped")
    row["hallucination"] = grade.get("hallucination")
    for s in grade.get("sub_scores", []):
        row[f"sub_{s['key']}"] = s.get("score")
    return row


def run_benchmark(progress=print) -> Path:
    if not C.BENCH_CIRCUITS:
        raise SystemExit("config.BENCH_CIRCUITS is empty - add your 6 circuits first.")
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = C.OUTPUT_DIR / f"l2_benchmark_{stamp}.csv"
    total = C.total_cells()
    done = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for model in C.BENCH_MODELS:
            for path, goal_text in C.BENCH_CIRCUITS:
                for cond in C.GOAL_CONDITIONS:
                    goal = goal_text if cond == "goal" else None
                    for run_idx in range(C.RUNS_PER_CELL):
                        row = run_one(model, path, goal, C.BENCH_GRADER)
                        row["run_idx"] = run_idx
                        w.writerow(row)
                        fh.flush()
                        done += 1
                        progress(f"[{done}/{total}] {model} | {Path(path).name} "
                                 f"| {cond} | run {run_idx} -> "
                                 f"grade={row['grade_total']}")
    progress(f"\nDone. {done} cells -> {out}")
    return out


def _dry_run():
    print("Benchmark plan (no calls made):")
    print(f"  models      : {len(C.BENCH_MODELS)} -> {C.BENCH_MODELS}")
    print(f"  circuits    : {len(C.BENCH_CIRCUITS)}")
    print(f"  conditions  : {C.GOAL_CONDITIONS}")
    print(f"  runs/cell   : {C.RUNS_PER_CELL}")
    print(f"  TOTAL cells : {C.total_cells()} generations + the same many grades")
    print(f"  grader      : {C.BENCH_GRADER}")
    print(f"  output dir  : {C.OUTPUT_DIR}")
    if not C.BENCH_CIRCUITS:
        print("  !! BENCH_CIRCUITS is empty - edit config.py before a real run.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plan, make no calls")
    args = ap.parse_args()
    if args.dry_run:
        _dry_run()
        sys.exit(0)
    run_benchmark()
