
from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

from dlc.evaluator import config as C


def _latest_csv() -> Path:
    files = sorted(C.OUTPUT_DIR.glob("l2_benchmark_*.csv"))
    if not files:
        raise SystemExit(f"No l2_benchmark_*.csv in {C.OUTPUT_DIR}")
    return files[-1]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _gen_cost(model, in_tok, out_tok):
    price = C.MODEL_PRICES.get(model)
    if not price or in_tok is None or out_tok is None:
        return None
    pin, pout = price
    return in_tok / 1e6 * pin + out_tok / 1e6 * pout


def load(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            g = _num(r.get("grade_total"))
            if g is None:           
                continue
            rows.append({
                "model": r["model"],
                "condition": r["goal_condition"],
                "circuit": r["circuit"],
                "grade": g,
                "cost": _gen_cost(r["model"], _num(r.get("gen_in_tokens")),
                                  _num(r.get("gen_out_tokens"))),
            })
    return rows


def per_model(rows: list[dict]) -> dict:
    out = {}
    for m in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == m]
        grades = [r["grade"] for r in rs]
        costs = [r["cost"] for r in rs if r["cost"] is not None]
        out[m] = {
            "n": len(rs),
            "mean_grade": round(statistics.mean(grades), 2) if grades else None,
            "stdev_grade": round(statistics.pstdev(grades), 2) if len(grades) > 1 else 0.0,
            "mean_cost": round(statistics.mean(costs), 5) if costs else None,
        }
    return out


def pareto_front(per: dict) -> set:
    """Higher grade is better, lower cost is better."""
    items = [(m, d) for m, d in per.items()
             if d["mean_grade"] is not None and d["mean_cost"] is not None]
    front = set()
    for m, d in items:
        dominated = any(
            o["mean_grade"] >= d["mean_grade"] and o["mean_cost"] <= d["mean_cost"]
            and (o["mean_grade"] > d["mean_grade"] or o["mean_cost"] < d["mean_cost"])
            for om, o in items if om != m
        )
        if not dominated:
            front.add(m)
    return front


def main(path=None):
    path = Path(path) if path else _latest_csv()
    rows = load(path)
    if not rows:
        raise SystemExit("No graded rows in the CSV.")
    per = per_model(rows)
    front = pareto_front(per)
    ordered = sorted(per.items(), key=lambda kv: (kv[1]["mean_grade"] or -1), reverse=True)

    summ = path.with_name(path.stem + "_summary.csv")
    with open(summ, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "n", "mean_grade", "stdev_grade",
                    "mean_gen_cost_usd", "on_pareto_front"])
        for m, d in ordered:
            w.writerow([m, d["n"], d["mean_grade"], d["stdev_grade"],
                        d["mean_cost"], m in front])

    print(f"Source: {path.name}  ({len(rows)} graded cells)\n")
    print(f"{'model':<28}{'n':>4}{'grade':>8}{'+-sd':>7}{'$gen':>11}  pareto")
    for m, d in ordered:
        cost = d["mean_cost"]
        cost_s = f"{cost:>11.5f}" if cost is not None else f"{'n/a':>11}"
        star = "  <-- frontier" if m in front else ""
        print(f"{m:<28}{d['n']:>4}{d['mean_grade']:>8}{d['stdev_grade']:>7}{cost_s}{star}")
    print(f"\nSummary CSV -> {summ}")
    for cond in sorted({r["condition"] for r in rows}):
        gs = [r["grade"] for r in rows if r["condition"] == cond]
        print(f"  mean grade [{cond:<7}] = {round(statistics.mean(gs), 2)}  (n={len(gs)})")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(install matplotlib for PNG charts:  uv add matplotlib)")
        return
    _charts(per, front, path, plt)


def _charts(per, front, path, plt):
    fig, ax = plt.subplots(figsize=(7, 5))
    for m, d in per.items():
        if d["mean_cost"] is None or d["mean_grade"] is None:
            continue
        ax.scatter(d["mean_cost"], d["mean_grade"], s=80,
                   color="#2563eb" if m in front else "#9aa1ab")
        ax.annotate(m, (d["mean_cost"], d["mean_grade"]), fontsize=8,
                    xytext=(4, 4), textcoords="offset points")
    ax.set_xlabel("mean generation cost per summary (USD)")
    ax.set_ylabel("mean credibility grade / 100")
    ax.set_title("L2 quality vs cost (blue = Pareto frontier)")
    fig.tight_layout()
    p1 = path.with_name(path.stem + "_pareto.png")
    fig.savefig(p1, dpi=130)

    fig2, ax2 = plt.subplots(figsize=(7, 5))
    ms = sorted(per, key=lambda m: per[m]["mean_grade"] or -1, reverse=True)
    ax2.bar(range(len(ms)), [per[m]["mean_grade"] for m in ms], color="#10b981")
    ax2.set_xticks(range(len(ms)))
    ax2.set_xticklabels(ms, rotation=30, ha="right", fontsize=8)
    ax2.set_ylabel("mean grade / 100")
    ax2.set_title("Mean grade by model")
    fig2.tight_layout()
    p2 = path.with_name(path.stem + "_grades.png")
    fig2.savefig(p2, dpi=130)
    print(f"Charts -> {p1.name}, {p2.name}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
