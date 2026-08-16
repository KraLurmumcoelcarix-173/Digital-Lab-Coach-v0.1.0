"""Turn an l3_benchmark CSV into the summary, six charts, and the
decision-memo skeleton (Data Dictionary A.3).

    uv run python -m dlc.l3bench.plots [path/to/l3_benchmark_*.csv]

With no path it uses the newest l3_benchmark_*.csv in OUT_DIR.
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from dlc.l3bench import config as C


def _latest_csv() -> Path:
    files = sorted(C.OUT_DIR.glob("l3_benchmark_*.csv"))
    if not files:
        raise SystemExit(f"No l3_benchmark_*.csv in {C.OUT_DIR}")
    return files[-1]


def _load(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return ([r for r in rows if r["kind"] == "llm"],
            [r for r in rows if r["kind"] == "control"])


def _summarize(llm_rows):
    by_model = defaultdict(list)
    for r in llm_rows:
        by_model[r["model"]].append(r)
    out = []
    for model, rows in by_model.items():
        n = len(rows)
        complete = sum(r["complete"] == "True" for r in rows)
        partial = sum(r["outcome"] == "partial" for r in rows)
        zfe_bad = sum(r["zfe_ok"] == "False" for r in rows)
        walls = [float(r["wall_s"]) for r in rows]
        costs = [float(r["cost_usd"]) for r in rows]
        refuted = sum(int(r["refuted_ideas"] or 0) for r in rows)
        out.append({
            "model": model, "runs": n,
            "complete_rate": round(complete / n, 3) if n else 0,
            "partial_rate": round(partial / n, 3) if n else 0,
            "false_error_runs": zfe_bad,
            "mean_wall_s": round(statistics.mean(walls), 1) if walls else 0,
            "median_wall_s": round(statistics.median(walls), 1) if walls else 0,
            "mean_cost_usd": round(statistics.mean(costs), 4) if costs else 0,
            "usd_per_complete_solve": (round(sum(costs) / complete, 3)
                                       if complete else ""),
            "refuted_ideas_total": refuted,
        })
    return sorted(out, key=lambda r: -r["complete_rate"])


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_csv()
    llm_rows, control_rows = _load(path)
    summary = _summarize(llm_rows)
    stem = path.stem

    spath = C.OUT_DIR / f"{stem}_summary.csv"
    with open(spath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    print(f"summary -> {spath}")
    for r in summary:
        print("  ", r)
    bad_controls = [r for r in control_rows
                    if r["outcome"] != "control_pass"]
    print(f"controls: {len(control_rows) - len(bad_controls)}/"
          f"{len(control_rows)} pass"
          + (f"  FAILURES: {[r['circuit'] for r in bad_controls]}"
             if bad_controls else ""))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — charts skipped "
              "(summary CSV is complete).")
        return 0

    models = [r["model"] for r in summary]

    # 1. Pareto: mean $/run vs complete-solve rate
    fig, ax = plt.subplots(figsize=(7, 5))
    for r in summary:
        ax.scatter(r["mean_cost_usd"], r["complete_rate"], s=90)
        ax.annotate(r["model"], (r["mean_cost_usd"], r["complete_rate"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=8)
    ax.set_xlabel("mean $ per run")
    ax.set_ylabel("complete-solve rate")
    ax.set_title("L3 Mode A: cost vs solve (Pareto)")
    ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(C.OUT_DIR / f"{stem}_pareto.png", dpi=150)

    # 2. Grouped outcome bars
    fig, ax = plt.subplots(figsize=(8, 5))
    idx = range(len(summary))
    comp = [r["complete_rate"] for r in summary]
    part = [r["partial_rate"] for r in summary]
    rest = [max(0.0, 1 - c - p) for c, p in zip(comp, part)]
    ax.bar(idx, comp, label="complete")
    ax.bar(idx, part, bottom=comp, label="partial")
    ax.bar(idx, rest, bottom=[c + p for c, p in zip(comp, part)],
           label="unverified/none")
    ax.set_xticks(list(idx), models, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("share of runs")
    ax.set_title("Outcomes per model")
    ax.legend()
    fig.tight_layout()
    fig.savefig(C.OUT_DIR / f"{stem}_outcomes.png", dpi=150)

    # 3. Heatmap: model x bug code (complete-solve fraction)
    codes = sorted({b for r in llm_rows for b in r["bugs"].split(",")})
    grid = []
    for m in models:
        row = []
        for code in codes:
            rs = [r for r in llm_rows if r["model"] == m
                  and code in r["bugs"].split(",")]
            row.append(sum(r["complete"] == "True" for r in rs) / len(rs)
                       if rs else float("nan"))
        grid.append(row)
    fig, ax = plt.subplots(figsize=(1.2 + len(codes), 1 + .6 * len(models)))
    im = ax.imshow(grid, vmin=0, vmax=1, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(codes)), codes, fontsize=8)
    ax.set_yticks(range(len(models)), models, fontsize=8)
    for i in range(len(models)):
        for j in range(len(codes)):
            v = grid[i][j]
            if v == v:
                ax.text(j, i, f"{v:.0%}", ha="center", va="center",
                        fontsize=7)
    ax.set_title("Complete-solve rate by bug code")
    fig.colorbar(im, shrink=.7)
    fig.tight_layout()
    fig.savefig(C.OUT_DIR / f"{stem}_heatmap.png", dpi=150)

    # 4. Latency box plot
    fig, ax = plt.subplots(figsize=(8, 5))
    data = [[float(r["wall_s"]) for r in llm_rows if r["model"] == m]
            for m in models]
    ax.boxplot(data, tick_labels=models)
    ax.set_ylabel("wall seconds per run")
    ax.set_title("Latency per model")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right", fontsize=8)
    fig.tight_layout()
    fig.savefig(C.OUT_DIR / f"{stem}_latency.png", dpi=150)

    # 5. Verifier value-add: refuted ideas per model
    fig, ax = plt.subplots(figsize=(8, 4.5))
    refuted = [r["refuted_ideas_total"] for r in summary]
    ax.bar(range(len(summary)), refuted)
    ax.set_xticks(range(len(summary)), models, rotation=20, ha="right",
                  fontsize=8)
    ax.set_ylabel("model ideas refuted by the machine verifier")
    ax.set_title("What verification caught before any student saw it")
    fig.tight_layout()
    fig.savefig(C.OUT_DIR / f"{stem}_verifier.png", dpi=150)

    # 6. Round variance strip: circuit x model, colored by round outcomes
    fig, ax = plt.subplots(figsize=(9, 6))
    circuits = sorted({r["circuit"] for r in llm_rows})
    colors = {"complete": "#16a34a", "partial": "#f59e0b"}
    for yi, cid in enumerate(circuits):
        for xi, m in enumerate(models):
            rs = [r for r in llm_rows
                  if r["circuit"] == cid and r["model"] == m]
            for k, r in enumerate(sorted(rs, key=lambda x: x["round"])):
                ax.scatter(xi + (k - 1) * .18, yi, s=42,
                           color=colors.get(r["outcome"], "#dc2626"))
    ax.set_xticks(range(len(models)), models, rotation=20, ha="right",
                  fontsize=8)
    ax.set_yticks(range(len(circuits)), circuits, fontsize=8)
    ax.set_title("Per-round outcomes (green=complete, amber=partial, "
                 "red=other)")
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(C.OUT_DIR / f"{stem}_variance.png", dpi=150)

    memo = C.OUT_DIR / f"{stem}_decision_memo.md"
    lines = ["# L3 Mode A model decision (Data Dictionary A.3)", "",
             "| model | runs | complete | partial | false errors | "
             "med wall s | $/run | $/solve |",
             "|---|---|---|---|---|---|---|---|"]
    for r in summary:
        lines.append(
            f"| {r['model']} | {r['runs']} | {r['complete_rate']:.0%} | "
            f"{r['partial_rate']:.0%} | {r['false_error_runs']} | "
            f"{r['median_wall_s']} | ${r['mean_cost_usd']:.3f} | "
            f"{r['usd_per_complete_solve'] or '—'} |")
    lines += ["", "Chosen default model: (fill after reading)",
              "Chosen premium model: (fill after reading)", ""]
    memo.write_text("\n".join(lines), encoding="utf-8")
    print(f"charts + memo -> {C.OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
