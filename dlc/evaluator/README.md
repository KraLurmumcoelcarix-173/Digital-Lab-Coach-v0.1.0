# Layer 2 evaluation harness

Two scripts. **Neither runs on import** — you configure, then invoke explicitly.
All outputs go to `config.OUTPUT_DIR` (default `~/dlc_benchmark_out`, overridable
with `$DLC_BENCH_OUT`) — **outside the repo, IRB-safe**. 

## 1. Pick the grader — `grader_selection.py`

Hand-grade 20 reference summaries, then let every candidate grade the same 20
and see which correlates best with you.

```bash

DLC_GRADER_REFS=/abs/refs.json uv run python -m dlc.evaluator.grader_selection
```
Prints a Pearson-correlation ranking; lock the winner into `config.BENCH_GRADER`.

## 2. Main benchmark — `benchmark.py`

`BENCH_MODELS × BENCH_CIRCUITS × GOAL_CONDITIONS × RUNS_PER_CELL` →
generate + grade → one CSV row per cell (flushed incrementally).

```bash
uv run python -m dlc.evaluator.benchmark --dry-run   
uv run python -m dlc.evaluator.benchmark             
```

## Before a real run
1. Edit `config.py`: set the **6 circuits + goals**, confirm the 6 models, the grader.
2. Configure API keys (`~/.dlc/config.json` or env vars) for every provider used.
3. `--dry-run` first to confirm the cell count (6×6×2×3 = **216** generations + 216 grades).

## Cost (rough)
216 gen + 216 grade calls. Cheap models are cents; Opus generations/grades dominate.
Your stated ceiling (a few hundred $) holds comfortably. Tip: validate end-to-end on
**1 circuit** (set `BENCH_CIRCUITS` to one entry) before the full sweep.

## Notes
- The benchmark does **not** run Digital.jar — L2 quality doesn't need pass/fail, and
  test rows already reach the model through the facts. `TEST_SUMMARY = None`.
- Auto-retry-on-low-grade is a **UI-only** behavior; the harness grades each summary
  exactly once, so the numbers are clean.
