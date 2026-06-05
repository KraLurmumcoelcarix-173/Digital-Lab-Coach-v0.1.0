
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
from pathlib import Path

from dlc.evaluator import config as C
from dlc.evaluator.benchmark import _facts_and_issues
from dlc.llm.grade import grade_summary


CANDIDATE_GRADERS = list(C.BENCH_MODELS)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def _load_refs() -> list[dict]:
    p = os.environ.get("DLC_GRADER_REFS")
    if not p:
        raise SystemExit("Set $DLC_GRADER_REFS to your references JSON file.")
    refs = json.loads(Path(p).read_text(encoding="utf-8"))
    if not refs:
        raise SystemExit("References file is empty.")
    return refs


def run_grader_selection(progress=print) -> Path:
    refs = _load_refs()
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = C.OUTPUT_DIR / f"grader_selection_{stamp}.csv"

    facts_cache: dict[str, dict] = {}
    per_grader: dict[str, list[tuple[float, float]]] = {g: [] for g in CANDIDATE_GRADERS}

    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ref_id", "manual_score", "grader_model", "grader_score",
                    "grade_error"])
        for ref in refs:
            cp = ref["circuit_path"]
            if cp not in facts_cache:
                facts_cache[cp] = _facts_and_issues(cp)[0]
            facts = facts_cache[cp]
            manual = float(ref["manual_score"])
            for g in CANDIDATE_GRADERS:
                res = grade_summary(facts=facts, summary_text=ref["summary_text"],
                                    student_goal=ref.get("student_goal"),
                                    test_summary=None, grader_model=g)
                score = res.get("total")
                w.writerow([ref.get("ref_id", ""), manual, g,
                            score if score is not None else "",
                            res.get("error") or ""])
                fh.flush()
                if res.get("ok") and score is not None:
                    per_grader[g].append((manual, float(score)))
                progress(f"  {ref.get('ref_id','?')} | {g} -> {score}")

    print(f"\nRows -> {out}\n")
    print("Correlation with your manual grades (higher = better grader):")
    ranking = []
    for g, pairs in per_grader.items():
        r = _pearson([m for m, _ in pairs], [s for _, s in pairs]) if pairs else None
        ranking.append((g, r, len(pairs)))
    for g, r, n in sorted(ranking, key=lambda t: (t[1] is not None, t[1] or -2), reverse=True):
        print(f"  {g:<28} pearson={r if r is None else round(r, 3)}  (n={n})")
    return out


if __name__ == "__main__":
    run_grader_selection()
