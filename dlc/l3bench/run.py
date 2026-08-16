"""L3 Mode A benchmark runner.

    uv run python -m dlc.l3bench.run --dry-run   # plan + cost estimate
    uv run python -m dlc.l3bench.run             # run (resumable)

One CSV row per run, flushed immediately (a crash keeps what finished;
rerunning skips completed (circuit, model, round) keys). Full debug
results go to RAW_DIR for audit. Hard money caps in config.py.

Every LLM row is machine-judged: the confirmed cards' ops are applied
to a temp copy and the FULL testcase re-runs on the real Digital jar —
"complete" means every row went green on that independent re-check.
Control rows must make ZERO model calls and land in their expected
deterministic mode; any model call there is recorded as control-fail
without spending money.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import time
from pathlib import Path

from dlc.l3 import debugger
from dlc.l3.patch import rerun_with_patch
from dlc.l3bench import config as C
from dlc.l3bench.prepare import prepare
from dlc.llm import client as lc
from dlc.testing.runner import find_digital_jar

CSV_FIELDS = [
    "timestamp", "circuit", "bugs", "kind", "model", "round",
    "outcome", "complete", "zfe_ok",
    "llm_calls", "refuted_ideas", "stopped_early", "escalated",
    "verify_runner", "wall_s", "llm_s_total",
    "in_tokens", "out_tokens", "cost_usd", "run_total_usd",
    "ops_rank1", "notes",
]


def _csv_path() -> Path:
    date = _dt.date.today().strftime("%Y%m%d")
    return C.OUT_DIR / f"l3_benchmark_{date}.csv"


def _existing_keys(path: Path) -> set[tuple]:
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["circuit"], r["model"], r["round"])
                for r in csv.DictReader(f)}


def _cost(model: str, usage: dict) -> float:
    pin, pout = C.PRICES[model]
    return (usage.get("input_tokens", 0) * pin
            + usage.get("output_tokens", 0) * pout) / 1e6


def _estimate() -> list[tuple[str, int, float]]:
    out = []
    for model in C.MODELS_FULL + C.MODELS_PROBE:
        probe = model in C.MODELS_PROBE
        rows = [c for c in C.CIRCUITS if c["kind"] == "llm"
                and (not probe or c.get("probe"))]
        runs = len(rows) * C.ROUNDS
        dollars = 0.0
        for c in rows:
            cls = C.circuit_class(c["id"])
            pin, pout = C.PRICES[model]
            per_run = (cls["in_call"] * cls["calls"] * pin
                       + C.EST_OUT_CALL[model] * cls["calls"] * pout) / 1e6
            dollars += per_run * C.ROUNDS
        out.append((model, runs, dollars))
    return out


def _classify(res: dict, dig_path: str) -> tuple[str, bool, bool, str]:
    """-> (outcome, complete, zfe_ok, notes). Applies confirmed ops and
    re-runs the whole testcase on the jar, independently of the run."""
    cards = res.get("cards") or []
    confirmed = [c for c in cards
                 if (c.get("verified") or {}).get("confirmed")]
    if not confirmed:
        if res.get("best_unverified"):
            return "best_unverified_only", False, True, ""
        return "none", False, True, ""
    # rank-1 first; if it does not finish the job, the union of all
    # confirmed cards' ops gets one shot.
    candidates = [[confirmed[0]["fix"]["ops"]]]
    if len(confirmed) > 1:
        candidates.append([c["fix"]["ops"] for c in confirmed])
    for ops in candidates:
        flat, seen = [], set()
        for lst in ops:
            for op in lst:
                key = json.dumps(op, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    flat.append(op)
        try:
            out = rerun_with_patch(dig_path, flat)
            if out.ok and out.all_passed:
                return "complete", True, True, ""
        except Exception as exc:
            return ("verified_but_recheck_failed", False, False,
                    f"recheck error: {type(exc).__name__}: {exc}")
    return "partial", False, True, ""


def _refuse_call(prompt, **_kw):
    raise RuntimeError("control row tried to call the LLM")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"out dir : {C.OUT_DIR}")
    print(f"keys    : anthropic={lc.has_api_key('anthropic')} "
          f"openai={lc.has_api_key('openai')}")
    jar = find_digital_jar()
    print(f"jar     : {jar or 'MISSING'}")
    ok_paths = True
    for c in C.CIRCUITS:
        exists = Path(c["path"]).exists()
        mark = "ok" if exists else "MISSING"
        if not exists and not str(c["path"]).startswith(str(C.PREPARED)):
            ok_paths = False
        print(f"  [{mark:>7}] {c['id']:24} {c['path']}")

    print("\nestimate (rough, live run bills real usage):")
    total_est = 0.0
    for model, runs, dollars in _estimate():
        total_est += dollars
        print(f"  {model:28} {runs:3} runs  ~${dollars:5.2f}")
    print(f"  {'TOTAL':28} {'':3}       ~${total_est:5.2f}  "
          f"(caps: total ${C.TOTAL_CAP_USD}, gpt ${C.GPT_CAP_USD})")

    if args.dry_run:
        print("\n[dry-run] running prepare in --check mode:")
        prepare(check_only=True)
        return 0

    if not jar:
        print("FATAL: Digital.jar not configured — the benchmark verdicts "
              "must come from the real jar.")
        return 1
    if not ok_paths:
        print("FATAL: archive/base circuits missing (see MISSING above).")
        return 1
    if not prepare(check_only=False):
        return 1

    C.OUT_DIR.mkdir(parents=True, exist_ok=True)
    C.RAW_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _csv_path()
    done = _existing_keys(csv_path)
    new_file = not csv_path.exists()
    fh = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
    if new_file:
        writer.writeheader()

    total_usd = 0.0
    gpt_usd = 0.0
    if not new_file:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                total_usd += float(r["cost_usd"] or 0)
                if r["model"].startswith("gpt"):
                    gpt_usd += float(r["cost_usd"] or 0)

    def emit(row: dict) -> None:
        writer.writerow(row)
        fh.flush()

    # ---- control rows: once each, zero calls, zero dollars ----
    for c in [c for c in C.CIRCUITS if c["kind"] == "control"]:
        key = (c["id"], "control", "1")
        if key in done:
            continue
        t0 = time.monotonic()
        try:
            res = debugger.debug_circuit(str(c["path"]), call=_refuse_call)
            mode = res.get("mode")
            called = False
        except RuntimeError as exc:
            if "control row" not in str(exc):
                raise
            mode, called, res = "llm_attempted", True, {}
        expected = c["expect"]
        got_ok = {
            "lazy": mode == "lazy",
            "child_gate": mode in ("analysis", "lazy", "suggestions")
                          and not called,
            "refusal": mode in ("lazy", "suggestions", "error", "analysis")
                       and not called,
            "quiet": not called,
            "clear": mode == "clear",
        }[expected] and not called
        emit({"timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
              "circuit": c["id"], "bugs": c["bugs"], "kind": "control",
              "model": "control", "round": 1,
              "outcome": "control_pass" if got_ok else "control_FAIL",
              "complete": "", "zfe_ok": not called,
              "llm_calls": 0, "refuted_ideas": "", "stopped_early": "",
              "escalated": "", "verify_runner": "",
              "wall_s": round(time.monotonic() - t0, 1),
              "llm_s_total": 0, "in_tokens": 0, "out_tokens": 0,
              "cost_usd": 0.0, "run_total_usd": round(total_usd, 2),
              "ops_rank1": "", "notes": f"mode={mode} expected={expected}"})
        print(f"[control] {c['id']:24} mode={mode:10} "
              f"{'ok' if got_ok else 'FAIL'}")

    # ---- llm rows ----
    for model in C.MODELS_FULL + C.MODELS_PROBE:
        probe = model in C.MODELS_PROBE
        rows = [c for c in C.CIRCUITS if c["kind"] == "llm"
                and (not probe or c.get("probe"))]
        for rnd in range(1, C.ROUNDS + 1):
            for c in rows:
                key = (c["id"], model, str(rnd))
                if key in done:
                    continue
                if total_usd >= C.TOTAL_CAP_USD:
                    print(f"HARD STOP: total ${total_usd:.2f} >= "
                          f"${C.TOTAL_CAP_USD}")
                    fh.close()
                    return 2
                if model.startswith("gpt") and gpt_usd >= C.GPT_CAP_USD:
                    print(f"gpt cap hit (${gpt_usd:.2f}) — skipping "
                          f"remaining gpt runs")
                    break

                calls = []
                def call(prompt, **kw):
                    t = time.monotonic()
                    r = lc.call_llm(prompt, **kw)
                    calls.append({"secs": round(time.monotonic() - t, 1),
                                  "usage": r.get("usage") or {},
                                  "stop": r.get("stop_reason")})
                    return r

                t0 = time.monotonic()
                try:
                    res = debugger.debug_circuit(str(c["path"]),
                                                 model=model, call=call)
                except Exception as exc:
                    res = {"mode": "crash",
                           "error": f"{type(exc).__name__}: {exc}"}
                wall = round(time.monotonic() - t0, 1)
                usage = {"input_tokens": sum(x["usage"].get("input_tokens", 0)
                                             for x in calls),
                         "output_tokens": sum(x["usage"].get("output_tokens", 0)
                                              for x in calls)}
                cost = _cost(model, usage)
                total_usd += cost
                if model.startswith("gpt"):
                    gpt_usd += cost

                if res.get("mode") == "crash":
                    outcome, complete, zfe, notes = ("crash", False, True,
                                                     res.get("error", ""))
                else:
                    outcome, complete, zfe, notes = _classify(
                        res, str(c["path"]))
                cards = res.get("cards") or []
                rank1_ops = (json.dumps(cards[0]["fix"]["ops"])
                             if cards else "")
                raw_name = f"{c['id']}__{model}__r{rnd}.json"
                try:
                    (C.RAW_DIR / raw_name).write_text(
                        json.dumps(res, indent=1, default=str),
                        encoding="utf-8")
                except Exception:
                    pass

                emit({"timestamp": _dt.datetime.now().isoformat(
                          timespec="seconds"),
                      "circuit": c["id"], "bugs": c["bugs"], "kind": "llm",
                      "model": model, "round": rnd,
                      "outcome": outcome, "complete": complete,
                      "zfe_ok": zfe,
                      "llm_calls": res.get("llm_calls", len(calls)),
                      "refuted_ideas": res.get("refuted_ideas", ""),
                      "stopped_early": res.get("stopped_early", ""),
                      "escalated": bool(res.get("escalation_used")),
                      "verify_runner": res.get("verify_runner", ""),
                      "wall_s": wall,
                      "llm_s_total": round(sum(x["secs"] for x in calls), 1),
                      "in_tokens": usage["input_tokens"],
                      "out_tokens": usage["output_tokens"],
                      "cost_usd": round(cost, 4),
                      "run_total_usd": round(total_usd, 2),
                      "ops_rank1": rank1_ops, "notes": notes})
                print(f"[{model:>26}] r{rnd} {c['id']:24} {outcome:22} "
                      f"calls={len(calls)} ${cost:.3f} "
                      f"(total ${total_usd:.2f})")
    fh.close()
    print(f"\ndone. total ${total_usd:.2f} (gpt ${gpt_usd:.2f})")
    print(f"csv: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
