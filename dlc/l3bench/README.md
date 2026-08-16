# L3 Mode A benchmark harness

**NEVER COMMIT THIS DIRECTORY.** It rides the working tree untracked
during collection and moves to the owner's local projects folder when
the charts are delivered (same lifecycle as `dlc/evaluator`, which moves
at the same time).

Three commands, in order:

```bash
uv run python -m dlc.l3bench.run --dry-run   # plan, paths, cost estimate
uv run python -m dlc.l3bench.run             # the run (resumable)
uv run python -m dlc.l3bench.plots           # summary + 6 charts + memo
```

- Design lives in `config.py` (models, circuits, rounds, money caps).
- All outputs land in `DLC_BENCH_OUT` (outside the repo, IRB-safe);
  student circuits are read from the local archive and never copied
  into the repo.
- `run.py` bills from real usage, prints a live total, and hard-stops
  at the caps. Crash-safe: rerunning skips finished rows.
- Verdicts come from the real Digital jar: confirmed ops are re-applied
  and the full testcase re-run independently before a row may say
  "complete".

See the followbook delivered with r44 for the step-by-step.
