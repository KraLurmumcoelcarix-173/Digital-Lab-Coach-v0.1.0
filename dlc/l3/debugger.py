"""L3 Mode A coordinator + verifier — the failed-test analysis pipeline.

One explicit trigger runs the whole ladder (docs/l3_debug_contract.md):

  deterministic   per-row verdict (Digital.jar when configured, the
                  Python evaluator otherwise) → evidence.assemble_evidence
                  decides clear / lazy / analysis and packs one frozen
                  payload per failing-row cluster.
  sub-agents      ONE single-shot model call per cluster (cap 4), no
                  tools, no iteration; one format re-prompt allowed when
                  the reply isn't the strict JSON object; one refutation
                  retry allowed when the verifier refutes the fix.
  verify          apply_patch → per-row rerun of the patched temp —
                  CONFIRMED iff every cluster row now passes AND no
                  previously-passing row regressed AND the L1 guard held.
                  Rows a Mode B accept ADDED are judged by strict
                  improvement instead (see verify_ops) — their expected
                  cells are model guesses, not ground truth.
                  With no jar the SAME Python evaluator that judged the
                  original failure re-judges the fix (verified.runner
                  says which ran). Nothing unconfirmed is ever shown AS A
                  VERIFIED FIX: unconfirmed hypotheses become dropped
                  ideas, except that a run with ZERO confirmed cards
                  ships its best-ranked survivor clearly labeled
                  unverified (best_unverified), with a free re-run.
  cards           dedupe by normalized ops, rank by (confirmed, rows
                  covered, confidence), top-K = 3. Student-visible text
                  passes the F13 leak guard; animation scripts are
                  validated act-by-act with retest forced last.

The `call=` hook injects a fake model in tests — the pipeline never
touches the network there, exactly like Mode B's proposer.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dlc.l3 import evidence as ev
from dlc.l3.patch import KNOWN_OPS, apply_patch, rerun_with_patch
from dlc.llm.client import call_llm
from dlc.llm.guard import sanitize_output
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.sim.simulator import simulate_sequential
from dlc.testing.runner import find_digital_jar, per_row_run_auto
from dlc.testing.spec import extract_test_specs, match_variables_to_io

CONTRACT = "l3.debug.v1.1"
K_CARDS = 3
_MAX_OPS = 6
# Stop condition: once this many ideas have been REFUTED by the
# verifier in one run, stop spending model calls (skip further refute
# retries, the escalation wave, and any untouched clusters) and return
# the best surviving idea instead. 4 = two clusters' worth of
# initial-idea + one-retry each — the typical cpu-tree shape — so a run
# that can't land a verified fix ends after ~4 model calls instead of 8+.
# The benchmark's "best solution" hard trigger reads the
# `stopped_early` flag this produces.
_MAX_REFUTED_IDEAS = 4
_MAX_TOKENS = 3000
# premium-tier models (Opus) reason longer before the JSON: at 3000 the
# reply truncates mid-object and validates as invalid_response, wasting
# the cluster's only shot — give them headroom.
_MAX_TOKENS_PREMIUM = 6000


def _max_tokens_for(model: str) -> int:
    try:
        from dlc.llm.client import MODEL_CATALOG
        if (MODEL_CATALOG.get(model) or {}).get("tier") == "premium":
            return _MAX_TOKENS_PREMIUM
    except Exception:
        pass
    return _MAX_TOKENS
_MAX_ANIM_ACTS = 12

_PROMPT_DIR = Path(__file__).parent.parent.parent / "prompts"
_PROMPT_NAME = "l3_modeA_hypothesis_v1.txt"

# Hypothesis generation needs real multi-step reasoning over evidence —
# same tier as Mode B's proposer. Override per install with l3_debug_model
# in ~/.dlc/config.json or the DLC_L3_DEBUG_MODEL env var (this is the
# zero-code switch the OpenAI-vs-Claude benchmark flips).
_DEBUG_MODEL_FALLBACK = "claude-sonnet-4-6"

_ANIM_ACTS = frozenset({
    "diagnose_line", "focus", "drill", "drill_back", "mark_fix", "retest",
})

# Light per-op field check so obvious junk earns the format re-prompt
# instead of dying inside the applier. apply_patch stays the authority.
_OP_REQUIRED = {
    "change_attribute": ("component_index", "name", "value"),
    "replace_element": ("component_index", "new_element"),
    "swap_pins": ("component_index", "pin_a", "pin_b"),
    "rewire_pin": ("component_index", "pin", "to"),
    "add_wire": ("p1", "p2"),
    "delete_wire": ("p1", "p2"),
    "add_component": ("element_name", "position"),
    "delete_component": ("component_index",),
}


def _debug_model() -> str:
    env = os.environ.get("DLC_L3_DEBUG_MODEL", "").strip()
    if env:
        return env
    try:
        from dlc.llm.client import _load_config
        cfg = _load_config().get("l3_debug_model")
        if isinstance(cfg, str) and cfg.strip():
            return cfg.strip()
    except Exception:
        pass
    return _DEBUG_MODEL_FALLBACK


def _load_prompt() -> str:
    return (_PROMPT_DIR / _PROMPT_NAME).read_text(encoding="utf-8")


def _clean(s) -> str:
    return sanitize_output(str(s or "")).strip()


# ---------------------------------------------------------------------------
# Untrusted-output handling
# ---------------------------------------------------------------------------

def parse_agent_json(text: str) -> dict | None:
    """First {...} blob of the reply as a dict; tolerates prose/fences
    around ONE JSON object, exactly like Mode B's parser."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_hypothesis(obj: dict | None) -> tuple[dict | None, str | None]:
    """(clean hypothesis, None) or (None, reason). The reason is written
    for the ONE format re-prompt, so it names the exact defect."""
    if not isinstance(obj, dict):
        return None, "the reply was not a JSON object"
    if obj.get("contract") != CONTRACT:
        return None, f'"contract" must be "{CONTRACT}"'
    hint = obj.get("hint")
    if not isinstance(hint, dict) or not str(hint.get("suspect_region", "")).strip():
        return None, '"hint.suspect_region" is required'
    signals = hint.get("suspect_signals")
    if not isinstance(signals, list):
        signals = []
    fix = obj.get("fix")
    if not isinstance(fix, dict):
        return None, '"fix" object is required'
    ops = fix.get("ops")
    if not isinstance(ops, list) or not (1 <= len(ops) <= _MAX_OPS):
        return None, f'"fix.ops" must be a list of 1..{_MAX_OPS} ops'
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in KNOWN_OPS:
            return None, (f"fix.ops[{i}] uses an unknown op "
                          f"{(op or {}).get('op')!r}")
        missing = [f for f in _OP_REQUIRED[op["op"]] if f not in op]
        if missing:
            return None, (f"fix.ops[{i}] ({op['op']}) is missing "
                          f"{', '.join(missing)}")
    try:
        confidence = float(obj.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = min(1.0, max(0.0, confidence))
    script = fix.get("animation_script")
    return {
        "hint": {
            "suspect_region": _clean(hint.get("suspect_region")),
            "suspect_signals": [_clean(s) for s in signals if _clean(s)][:8],
            "why": _clean(hint.get("why")),
        },
        "ops": ops,
        "explanation": _clean(fix.get("explanation_for_student")),
        "animation": script if isinstance(script, list) else [],
        "confidence": confidence,
    }, None


def validate_animation(script: list, n_components: int) -> list[dict]:
    """Executor-side validation: unknown acts dropped, out-of-range
    targets dropped, exactly one retest forced last. Playback never
    mutates a circuit, so validation only guards the pointer."""
    def _idx_ok(v):
        return isinstance(v, int) and 0 <= v < n_components

    def _path_ok(p):
        return isinstance(p, list) and all(isinstance(i, int) for i in p)

    out: list[dict] = []
    for raw in script[: _MAX_ANIM_ACTS * 2]:
        if not isinstance(raw, dict):
            continue
        act = raw.get("act")
        if act not in _ANIM_ACTS or act == "retest":
            continue
        if act == "diagnose_line":
            text = _clean(raw.get("text"))
            if text:
                out.append({"act": act, "text": text})
        elif act == "focus":
            path = raw.get("path") if _path_ok(raw.get("path")) else []
            idx = raw.get("component_index")
            # with a non-empty path the index points inside a subcircuit,
            # so only its type can be checked here
            if _idx_ok(idx) if not path else isinstance(idx, int):
                out.append({"act": act, "component_index": idx,
                            "path": path})
        elif act == "drill":
            if _path_ok(raw.get("path")) and raw.get("path"):
                out.append({"act": act, "path": raw["path"]})
        elif act == "drill_back":
            out.append({"act": act})
        elif act == "mark_fix":
            t = raw.get("target")
            if isinstance(t, dict) and (
                (not t.get("path") and _idx_ok(t.get("component_index")))
                or (t.get("path") and _path_ok(t.get("path")))
                or isinstance(t.get("net_id"), int)
            ):
                out.append({"act": act, "target": t,
                            "label": _clean(raw.get("label"))})
        if len(out) >= _MAX_ANIM_ACTS:
            break
    out.append({"act": "retest"})
    return out


# ---------------------------------------------------------------------------
# Verify (the self-check oracle — only CONFIRMED fixes rank as cards;
# a zero-card run ships its best survivor clearly labeled unverified)
# ---------------------------------------------------------------------------

def _offline_failing(temp_path: str, spec_name: str) -> tuple[list[int], dict]:
    """Failing row indices (+ mismatch cells) of `spec_name` on the patched
    temp, judged by the Python evaluator — the no-jar verify path."""
    circuit = parse_dig_file(temp_path)
    netlist = build_netlist(circuit)
    graph = build_signal_graph(circuit, netlist)
    spec = next(s for s in extract_test_specs(circuit) if s.name == spec_name)
    bindings = match_variables_to_io(spec.headers, circuit)
    failing: list[int] = []
    details: dict[int, list[dict]] = {}
    for row in spec.rows:
        if row.is_malformed:
            continue
        sim = simulate_sequential(circuit, netlist, graph, spec,
                                  row.line_index)
        _outs, mism = ev._outputs_report(spec, bindings, row, sim)
        if mism:
            failing.append(row.line_index)
            details[row.line_index] = mism
    return failing, details


def verify_ops(dig_path: str, spec_name: str, ops: list[dict],
               cluster_rows: list[int], original_failing: list[int], *,
               jar_path: str | None = None,
               coach_targets: dict[int, set] | None = None) -> dict:
    """CONFIRMED iff the patch applies (L1 guard included), every cluster
    row now passes, and no previously-passing row fails. Runs Digital when
    a jar is configured; otherwise the same evaluator that produced the
    original verdict re-judges the fix.

    ``coach_targets`` maps a coach-ADDED row (Mode B hand-off) to the set
    of output columns it originally failed on. Such a row is judged by
    STRICT IMPROVEMENT instead of perfection: the fix must repair at least
    one of those columns and break no new one. Any residual columns land
    in ``coach_residuals`` (not ``still_failing``) — the coach's own
    expectation for those cells is a model guess, not ground truth, so an
    imperfect side-cell guess must not refute a correct fix."""
    jar = jar_path or find_digital_jar()
    verdict = {
        "confirmed": False, "apply_ok": False,
        "runner": "digital" if jar else "evaluator",
        "still_failing": [], "regressions": [],
        "details": {}, "coach_residuals": {}, "warning": None,
    }
    if jar:
        outcome = rerun_with_patch(dig_path, ops, spec_name=spec_name,
                                   jar_path=jar)
        if not outcome.ok:
            verdict["warning"] = outcome.warning
            return verdict
        verdict["apply_ok"] = True
        spec_payload = next(
            (s for s in outcome.specs if s["name"] == spec_name), None)
        if spec_payload is None:
            verdict["warning"] = f"testcase {spec_name!r} missing after patch"
            return verdict
        failing_after = []
        for r in spec_payload["rows"]:
            if r["status"] in ("failed", "error"):
                failing_after.append(r["index"])
                verdict["details"][r["index"]] = (
                    r.get("mismatches") or
                    [{"error": r.get("error_message") or r["status"]}])
    else:
        temp_path, report = apply_patch(dig_path, ops)
        if temp_path is None:
            verdict["warning"] = report.warning
            return verdict
        verdict["apply_ok"] = True
        try:
            failing_after, details = _offline_failing(temp_path, spec_name)
            verdict["details"] = details
        except Exception as exc:
            verdict["warning"] = (f"evaluator could not re-run the patched "
                                  f"circuit: {type(exc).__name__}: {exc}")
            return verdict
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    after = set(failing_after)
    coach = coach_targets or {}
    still: list[int] = []
    for idx in sorted(after & set(cluster_rows)):
        orig_cols = coach.get(idx)
        cells = verdict["details"].get(idx) or []
        res_cols = {m.get("column") for m in cells if m.get("column")}
        # len(res_cols) == len(cells) rejects rows whose details carry an
        # unnamed {"error": ...} entry — an errored row is never "improved".
        if (orig_cols and len(res_cols) == len(cells)
                and res_cols < set(orig_cols)):
            verdict["coach_residuals"][idx] = sorted(res_cols)
        else:
            still.append(idx)
    verdict["still_failing"] = still
    verdict["regressions"] = sorted(after - set(original_failing))
    verdict["confirmed"] = (not verdict["still_failing"]
                            and not verdict["regressions"])
    return verdict


# ---------------------------------------------------------------------------
# Lazy branch (deterministic detective suggestions — 0 model calls)
# ---------------------------------------------------------------------------

# `terms` are L2 library vocabulary, marked for the blue hover-cards.
_SUGGESTION_TABLE = {
    "scattered_failures": {
        "question": ("Pick ONE failing row: why is it wrong in four or "
                     "more output columns AT ONCE?"),
        "hint": ("Rows failing across many outputs at the same time "
                 "almost never come from one localized bug — the block's "
                 "plan itself disagrees with the spec. Rebuild the truth "
                 "table output by output, check a few rows by hand, and "
                 "test each subcircuit alone before re-running the "
                 "analysis."),
        "terms": ["truth table", "combinational logic", "subcircuit"],
    },
    "too_many_failures": {
        "question": ("Which OUTPUT column fails most often — and does it "
                     "fail the same way every time?"),
        "hint": ("With this many rows failing, verify the datapath plan "
                 "itself before chasing single rows: build the truth table "
                 "for ONE output, check it against a few rows by hand, and "
                 "test each subcircuit alone with its own testcase."),
        "terms": ["truth table", "combinational logic", "subcircuit"],
    },
    "subcircuit_failing": {
        "question": ("Which subcircuit fails its OWN embedded tests — and "
                     "can the parent possibly work before it does?"),
        "hint": ("Debug bottom-up: upload the failing subcircuit as its own "
                 "file, run its tests, fix it there (Mode A analyzes it "
                 "directly), then re-upload the whole tree."),
        "terms": ["subcircuit", "testcase", "modular design"],
    },
    "subcircuit_failing_official": {
        "question": ("Which subcircuit fails its OFFICIAL tests — and can "
                     "the parent possibly work before it does?"),
        "hint": ("Debug bottom-up: open the failing subcircuit as its own "
                 "file — its official testcase loads automatically — fix "
                 "it there (Mode A analyzes it directly), then re-run the "
                 "whole tree."),
        "terms": ["subcircuit", "testcase", "modular design"],
    },
    "build_refused": {
        "question": ("Which red Layer 1 error is the build blocker — a "
                     "dangling pin, an unconnected tunnel, or two outputs "
                     "shorted together?"),
        "hint": ("Digital could not even BUILD this circuit, so no row "
                 "verdict is real and analysis would only guess. The red "
                 "Layer 1 errors mirror the build blocker — fix them "
                 "first, then re-run the tests and the analysis."),
        "terms": ["wire", "tunnel", "pin"],
    },
    "low_pass_rate": {
        "question": ("Pick ONE failing output: can you follow its value "
                     "backwards, gate by gate, for a single row?"),
        "hint": ("Too large a share of the rows fails for this to be one "
                 "localized bug. Rebuild the truth table for one output, "
                 "check a few rows by hand, and test each subcircuit alone "
                 "with its own testcase before re-running the analysis."),
        "terms": ["truth table", "combinational logic", "subcircuit"],
    },
    "missing_clocked_logic": {
        "question": ("Your testcase steps a clock (C rows) — which element "
                     "in your circuit is supposed to REMEMBER a value "
                     "between two rows?"),
        "hint": ("A clocked testcase expects state: without a register (or "
                 "flip-flop) nothing survives the clock edge, so rows that "
                 "assert last cycle's result can never pass. Revisit the "
                 "pipeline-stage diagram from lecture."),
        "terms": ["register", "clock edge", "pipeline stage", "flip-flop"],
    },
    "unbound_columns": {
        "question": ("Which testcase columns cannot find a port with the "
                     "same label in your circuit?"),
        "hint": ("Digital matches testcase columns to In/Out components by "
                 "LABEL, exactly. Rename your ports to the lab's specified "
                 "interface (case matters) and re-run."),
        "terms": ["input", "output", "label", "interface"],
    },
}


def lazy_suggestions(gross_flags: list[dict]) -> list[dict]:
    out = []
    for flag in gross_flags:
        entry = _SUGGESTION_TABLE.get(flag.get("kind"))
        if entry:
            out.append({"kind": flag["kind"], "detail": flag.get("detail"),
                        **entry})
    return out


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

def _comp_tag(circuit, idx) -> str:
    """The tool-wide component naming standard: [index] ElementName plus
    the label when one exists — '[16] Const', \"[3] Register 'RegA1'\"."""
    try:
        comp = circuit.components[int(idx)]
    except (TypeError, ValueError, IndexError):
        return f"[{idx}]"
    label = getattr(comp, "label", None)
    return f"[{idx}] {comp.element_name}" + (f" '{label}'" if label else "")


def describe_ops(circuit, ops: list[dict]) -> list[str]:
    """Student-facing one-liners for fix ops. Wiring arrows point the way
    the SIGNAL flows: a rewired pin RECEIVES from its new source (←)."""
    out: list[str] = []
    for op in ops:
        kind = op.get("op")
        tag = _comp_tag(circuit, op.get("component_index"))
        if kind == "change_attribute":
            out.append(f"set {tag} attribute {op.get('name')} = "
                       f"{json.dumps(op.get('value'))}")
        elif kind == "replace_element":
            out.append(f"replace {tag} with {op.get('new_element')}")
        elif kind == "swap_pins":
            out.append(f"swap wires on {tag} pins "
                       f"{op.get('pin_a')} ↔ {op.get('pin_b')}")
        elif kind == "rewire_pin":
            to = op.get("to") or {}
            out.append(f"rewire {tag}.{op.get('pin')} ← "
                       f"{_comp_tag(circuit, to.get('component_index'))}"
                       f".{to.get('pin')}")
        elif kind == "add_wire":
            out.append(f"add wire ({','.join(map(str, op.get('p1') or []))})"
                       f" — ({','.join(map(str, op.get('p2') or []))})")
        elif kind == "delete_wire":
            out.append(f"delete wire "
                       f"({','.join(map(str, op.get('p1') or []))}) — "
                       f"({','.join(map(str, op.get('p2') or []))})")
        elif kind == "add_component":
            out.append(f"add {op.get('element_name')} at "
                       f"({','.join(map(str, op.get('position') or []))})")
        elif kind == "delete_component":
            out.append(f"delete {tag}")
        else:
            out.append(json.dumps(op))
    return out


def _failing_children(circuit) -> list[tuple[str, int, str]]:
    """(reference, failing-row count, test source) for every resolved
    subcircuit that fails under the evaluator. Parent-level analysis is
    noise until the children hold — the coach says so instead of drowning
    in a giant multi-cause evidence payload.

    Test source per child mirrors the Gradescope flow: a child whose
    testcase is missing or modified gets the OFFICIAL rows injected
    (tool-owned sibling temp, removed before returning) and reports
    source "official"; otherwise its own embedded rows run ("own").
    This is what routes a cpu tree whose real bug lives in
    control-unit.dig to that file instead of burning model calls on
    parent wiring that can never fix it."""
    from dlc.testing.inject import prepare_injected_run, cleanup_injected

    out: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for sub in circuit.subcircuits:
        ref = getattr(sub, "reference", None)
        path = getattr(sub, "resolved_path", None)
        if not ref or not path or ref in seen:
            continue
        seen.add(ref)
        temp = None
        try:
            temp, _notes = prepare_injected_run(str(path), ref)
            run_path, src = (temp, "official") if temp else (str(path), "own")
            child = parse_dig_file(run_path)
            n = 0
            for spec in extract_test_specs(child):
                if not spec.rows:
                    continue
                failing, _ = _offline_failing(run_path, spec.name)
                n += len(failing)
            if n:
                out.append((ref, n, src))
        except Exception:
            continue
        finally:
            if temp:
                cleanup_injected(temp)
    return out


def _slim_payload(payload: dict) -> dict:
    """Drop representative net values outside the suspect wiring — the
    oversized-tree safety valve, so a messy upload degrades to slimmer
    evidence instead of a blown context window."""
    keep = {str(p.get("net_id"))
            for w in payload.get("suspect_wiring", [])
            for p in w.get("pins", [])}
    out = dict(payload)
    cl = dict(out.get("cluster") or {})
    reps = []
    for r in cl.get("representative_evidence") or []:
        r2 = dict(r)
        r2["net_values"] = {k: v
                            for k, v in (r.get("net_values") or {}).items()
                            if k in keep}
        reps.append(r2)
    cl["representative_evidence"] = reps
    out["cluster"] = cl
    return out


def _diagnosis_line(cluster) -> str:
    rows = ", ".join(str(r.row_index) for r in cluster.rows)
    cols = ", ".join(cluster.signature.get("columns") or ["?"])
    line = f"Row(s) {rows} fail on {cols}"
    sels = cluster.signature.get("selects") or []
    if sels:
        line += " when " + ", ".join(f"{c}={v}" for c, v in sels)
    cat = cluster.signature.get("category")
    if cat:
        line += f" (instruction category: {cat})"
    return line + "."


def _touches_stored_data(ops_lists: list[list[dict]],
                         circuit=None) -> bool:
    """A refuted Data rewrite justifies the r27 address-path steer ONLY
    when the target ROM actually stores words today — the steer's
    premise is "the stored words satisfy every passing row". A rewrite
    of an EMPTY ROM refuted for having the WRONG words must keep the
    Data path open (r37, s008's unprogrammed decode ROM)."""
    for ops in ops_lists:
        for op in (ops or []):
            if not (isinstance(op, dict) and op.get("name") == "Data"):
                continue
            if circuit is None:
                return True
            idx = op.get("component_index")
            if not (isinstance(idx, int)
                    and 0 <= idx < len(circuit.components)):
                return True
            raw = circuit.components[idx].attributes.get("Data", "")
            if str(raw or "").strip():
                return True
    return False


# Machine fact appended once a ROM/LookUpTable Data rewrite has been
# REFUTED: the stored words provably satisfy every currently-passing row
# that reads the same table, so a second Data guess is the one move that
# cannot be right — steer the retry to the ADDRESS/SELECT path instead
# (the live failure mode on the control unit: the model kept rewriting
# words when the bug was a detect gate feeding the priority encoder).
_DATA_REFUTED_STEER = (
    "\nMACHINE FACT: a stored-Data rewrite was already applied and "
    "REFUTED by the re-run above — the stored words satisfy every row "
    "that passes today, so the table content is NOT the bug. Do NOT "
    "propose another Data change. The failing rows read the WRONG WORD: "
    "suspect the ADDRESS/SELECT path instead — the detect gates, "
    "comparators and encoder/selector inputs that choose which word is "
    "read. Run the gate-kind sanity check on each of them using the "
    "values table."
)


def _refutation_block(ops: list[dict], verdict: dict,
                      circuit=None) -> str:
    payload = {
        "refuted_ops": ops,
        "rerun": {
            "applied": verdict["apply_ok"],
            "still_failing": [
                {"index": i, "mismatches": verdict["details"].get(i, [])}
                for i in verdict["still_failing"]
            ],
            "regressions": [
                {"index": i, "mismatches": verdict["details"].get(i, [])}
                for i in verdict["regressions"]
            ],
            "warning": verdict["warning"],
        },
    }
    return ("\n\n[REFUTED ATTEMPT]\n"
            + json.dumps(payload, indent=2, default=str)
            + (_DATA_REFUTED_STEER
               if _touches_stored_data([ops], circuit) else ""))


def _rank_key(h: dict):
    return (-int(h["verdict"]["confirmed"]), -len(h["cluster_rows"]),
            -h["confidence"], h["cluster_index"])


def dedupe_hypotheses(hyps: list[dict]) -> list[dict]:
    """Merge hypotheses whose normalized op lists match. The best-ranked
    survivor absorbs a duplicate's cluster rows only when BOTH were
    confirmed — a confirmed card must never claim rows its own verify
    didn't clear."""
    by_ops: dict[str, dict] = {}
    for h in sorted(hyps, key=_rank_key):
        key = json.dumps(h["ops"], sort_keys=True, default=str)
        prev = by_ops.get(key)
        if prev is None:
            by_ops[key] = h
        elif prev["verdict"]["confirmed"] and h["verdict"]["confirmed"]:
            prev["cluster_rows"] = sorted(
                set(prev["cluster_rows"]) | set(h["cluster_rows"]))
    return sorted(by_ops.values(), key=_rank_key)


def _lazy_exempt_name(name: str | None) -> bool:
    """Instructor ruling (marked TEMPORARY — revisit on request):
    control-unit files always skip the lazy gate and go straight to
    analysis. Matched on the normalized filename (case/punctuation
    insensitive, tool-owned `.dlc_injected__` prefix stripped), so
    `control-unit.dig`, `controlunit.dig` and their injected temps all
    qualify. Refusal guards (build refused / unbound columns) and the
    failing-children gate still apply — an unrunnable file has nothing
    to analyze."""
    if not name:
        return False
    base = Path(str(name)).name
    if base.startswith(".dlc_injected__"):
        base = base[len(".dlc_injected__"):]
    norm = "".join(ch for ch in base.lower() if ch.isalnum())
    return norm in ("controlunitdig", "controlunit")


def debug_circuit(dig_path: str, *, spec_name: str | None = None,
                  spec_index: int = 0, model: str | None = None,
                  call=None, api_key: str | None = None,
                  jar_path: str | None = None,
                  manifest: dict | None = None, use_manifest: bool = True,
                  failing_indices: list[int] | None = None,
                  jar_mismatches: dict[int, list[dict]] | None = None,
                  coach_rows: list[int] | None = None,
                  lazy_exempt: bool | None = None,
                  k_cards: int = K_CARDS) -> dict:
    """Run Mode A end to end for one circuit + testcase. Returns the
    contract §6 response dict; never raises for content-level problems.

    ``failing_indices`` (+ ``jar_mismatches``) overrides the per-row
    verdict entirely — the eval-harness / test seam; without it the jar
    decides when configured, the evaluator sweep otherwise.

    ``coach_rows`` names the spec rows that a Mode B accept ADDED (the
    web layer reads them off the registered temp) — the verifier judges
    those by strict improvement, not perfection (see verify_ops).

    ``lazy_exempt`` skips the gross-check lazy gate (see
    ``_lazy_exempt_name``); None derives it from the file name — the
    web layer passes the REAL filename explicitly because coach temps
    carry generated names."""
    model = model or _debug_model()
    if lazy_exempt is None:
        lazy_exempt = _lazy_exempt_name(dig_path)
    if call is None:
        call = call_llm

    try:
        circuit = parse_dig_file(str(dig_path))
        netlist = build_netlist(circuit)
        graph = build_signal_graph(circuit, netlist)
    except Exception as exc:
        return {"ok": False, "mode": "error",
                "warning": f"Could not parse circuit: {exc}"}
    specs = extract_test_specs(circuit)
    if not specs:
        return {"ok": False, "mode": "error",
                "warning": "This file has no testcase."}
    spec = None
    if spec_name is not None:
        spec = next((s for s in specs if s.name == spec_name), None)
        if spec is None:
            return {"ok": False, "mode": "error",
                    "warning": f"No testcase named {spec_name!r}."}
    else:
        if not (0 <= spec_index < len(specs)):
            return {"ok": False, "mode": "error",
                    "warning": f"spec_index {spec_index} out of range."}
        spec = specs[spec_index]

    if manifest is None and use_manifest:
        names = {Path(dig_path).name}
        for sub in circuit.subcircuits:
            if getattr(sub, "reference", None):
                names.add(sub.reference)
        from dlc.l3.manifest import tree_element_names
        manifest = ev.find_manifest(
            names, element_names=tree_element_names(circuit))

    # Children first: a subcircuit failing its OWN tests gates the whole
    # analysis into the (free) suggestion branch — fix it, re-upload.
    broken = _failing_children(circuit)
    if broken:
        flags = [{
            "kind": ("subcircuit_failing_official" if src == "official"
                     else "subcircuit_failing"),
            "detail": (f"subcircuit {ref!r} fails {n} of its "
                       f"{'official' if src == 'official' else 'own'} "
                       f"test row(s) — fix that file first, then re-run "
                       f"the tree."),
        } for ref, n, src in broken]
        return {
            "ok": True, "contract": CONTRACT, "model": model,
            "spec_name": spec.name, "failing_count": 0,
            "row_verdict_runner": "evaluator",
            "notes": [], "usage": {"input_tokens": 0, "output_tokens": 0},
            "llm_calls": 0, "mode": "lazy",
            "gross_flags": flags, "suggestions": lazy_suggestions(flags),
        }

    # Per-row verdict: caller override first, then Digital when
    # configured, then the evaluator sweep.
    jar = jar_path or find_digital_jar()
    runner = "caller" if failing_indices is not None else "evaluator"
    notes: list[str] = []
    if jar and failing_indices is None:
        try:
            results = per_row_run_auto(spec, str(dig_path), jar_path=jar)
            if results and all(r.status == "error" for r in results):
                # Every row erroring means Digital REFUSED the run, not
                # that rows failed. Analysis on an unrunnable circuit
                # only guesses — and its verifier would refute every
                # idea through the same refusal, the r37 runaway. Stop
                # here, free, with the blocker named. Two families:
                # testcase columns that bind to no port (renamed labels
                # — s008's op/f3/f7 control unit) get the rename
                # guidance; anything else (unconnected tunnel, dangling
                # pin) mirrors the red Layer 1 errors.
                msg = results[0].error_message or "build error"
                bindings = match_variables_to_io(spec.headers, circuit)
                unbound = [h for h in spec.headers
                           if bindings[h].role == "unbound"]
                if unbound:
                    flags = [{
                        "kind": "unbound_columns",
                        "detail": (
                            "testcase column(s) "
                            + ", ".join(repr(h) for h in unbound)
                            + " match no input, output, or clock label "
                            "in this circuit — Digital refuses the run "
                            f"({msg}). Rename the ports to the lab's "
                            "interface, then re-run."),
                    }]
                else:
                    flags = [{
                        "kind": "build_refused",
                        "detail": (f"Digital refuses to build this "
                                   f"circuit ({msg}). Fix the red "
                                   f"Layer 1 errors first — they "
                                   f"mirror this blocker — then "
                                   f"re-run the analysis."),
                    }]
                return {
                    "ok": True, "contract": CONTRACT, "model": model,
                    "spec_name": spec.name, "failing_count": 0,
                    "row_verdict_runner": "digital",
                    "notes": notes, "usage": {"input_tokens": 0,
                                              "output_tokens": 0},
                    "llm_calls": 0, "mode": "lazy",
                    "gross_flags": flags,
                    "suggestions": lazy_suggestions(flags),
                }
            else:
                failing_indices = [r.row_index for r in results
                                   if r.status in ("failed", "error")]
                jar_mismatches = {r.row_index: r.mismatches for r in results
                                  if r.status == "failed" and r.mismatches}
                runner = "digital"
        except Exception as exc:
            notes.append(f"Digital runner failed ({type(exc).__name__}); "
                         "falling back to the built-in evaluator.")

    evres = ev.assemble_evidence(
        circuit, netlist, graph, spec, manifest=manifest,
        failing_indices=failing_indices, jar_mismatches=jar_mismatches,
        lazy_exempt=lazy_exempt,
    )
    notes.extend(evres.notes)

    base = {
        "ok": True,
        "contract": CONTRACT,
        "model": model,
        "spec_name": spec.name,
        "failing_count": evres.failing_count,
        "row_verdict_runner": runner,
        "notes": notes,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "llm_calls": 0,
    }

    if evres.mode == "clear":
        return {**base, "mode": "clear",
                "message": "Every row of this testcase passes — nothing "
                           "to debug."}
    if evres.mode == "lazy":
        return {**base, "mode": "lazy", "gross_flags": evres.gross_flags,
                "suggestions": lazy_suggestions(evres.gross_flags)}

    # --- analysis: one single-shot sub-agent per cluster ------------------
    prompt_template = _load_prompt()
    usage = base["usage"]
    calls = 0
    t_begin = time.monotonic()
    llm_seconds: list[float] = []
    verify_seconds: list[float] = []
    refuted_total = 0
    stopped_early = False

    def ask(prompt_text: str) -> dict:
        nonlocal calls
        t0 = time.monotonic()
        r = call(prompt_text, api_key=api_key, model=model,
                 max_tokens=_max_tokens_for(model))
        llm_seconds.append(round(time.monotonic() - t0, 2))
        calls += 1
        u = r.get("usage") or {}
        usage["input_tokens"] += u.get("input_tokens") or 0
        usage["output_tokens"] += u.get("output_tokens") or 0
        return r

    original_failing = sorted(
        {r.row_index for c in evres.clusters for r in c.rows})
    # A coach-added row's expected cells are model guesses: hold fixes to
    # "repaired what the row originally flagged, broke nothing" instead of
    # full-row perfection, or one imperfect side-cell guess would refute
    # every correct fix (the bug6 case-3 failure).
    coach_targets: dict[int, set] | None = None
    if coach_rows:
        want = set(coach_rows)
        coach_targets = {
            r.row_index: {m.get("column") for m in r.mismatches
                          if m.get("column")}
            for c in evres.clusters for r in c.rows
            if r.row_index in want and r.mismatches} or None
    hypotheses: list[dict] = []
    dropped: list[dict] = []

    def verify(ops: list[dict], rows: list[int]) -> dict:
        """verify_ops with central timing + refuted-idea accounting."""
        nonlocal refuted_total
        t0 = time.monotonic()
        v = verify_ops(str(dig_path), spec.name, ops, rows,
                       original_failing, jar_path=jar_path,
                       coach_targets=coach_targets)
        verify_seconds.append(round(time.monotonic() - t0, 2))
        if not v["confirmed"]:
            refuted_total += 1
        return v

    for ci, (cluster, payload) in enumerate(
            zip(evres.clusters, evres.payloads)):
        if refuted_total >= _MAX_REFUTED_IDEAS:
            stopped_early = True
            notes.append(
                f"analysis stopped after {refuted_total} refuted ideas — "
                f"remaining cluster(s) skipped; the best surviving idea "
                f"is returned below.")
            break
        cluster_rows = [r.row_index for r in cluster.rows]
        payload_json = json.dumps(payload, indent=2, default=str)
        if len(payload_json) > 250_000:
            payload = _slim_payload(payload)
            evres.payloads[ci] = payload      # escalation reuses the slim one
            payload_json = json.dumps(payload, indent=2, default=str)
            notes.append("evidence payload was slimmed (net values limited "
                         "to suspect nets) to fit the model context.")
        prompt = prompt_template.replace("<<PAYLOAD_JSON>>", payload_json)

        reply = ask(prompt)
        if not reply.get("ok"):
            dropped.append({"cluster_rows": cluster_rows,
                            "reason": "llm_error",
                            "detail": reply.get("error")})
            continue
        clean, err = validate_hypothesis(parse_agent_json(reply.get("text")))
        if err is not None:
            retry = ask(prompt + "\n\n# FORMAT RETRY\nYour previous reply "
                        f"was rejected: {err}. Output ONLY the JSON object "
                        "specified above — no prose, no code fences.")
            clean = None
            if retry.get("ok"):
                clean, err = validate_hypothesis(
                    parse_agent_json(retry.get("text")))
        if clean is None:
            dropped.append({"cluster_rows": cluster_rows,
                            "reason": "invalid_response", "detail": err})
            continue

        verdict = verify(clean["ops"], cluster_rows)
        if not verdict["confirmed"] and refuted_total < _MAX_REFUTED_IDEAS:
            retry = ask(prompt + _refutation_block(clean["ops"], verdict,
                                                   circuit))
            if retry.get("ok"):
                clean2, err2 = validate_hypothesis(
                    parse_agent_json(retry.get("text")))
                if clean2 is not None:
                    verdict2 = verify(clean2["ops"], cluster_rows)
                    if verdict2["confirmed"] or not verdict["apply_ok"]:
                        clean, verdict = clean2, verdict2

        hypotheses.append({"cluster_index": ci, "cluster_rows": cluster_rows,
                           "confidence": clean["confidence"],
                           "hint": clean["hint"], "ops": clean["ops"],
                           "explanation": clean["explanation"],
                           "animation": clean["animation"],
                           "verdict": verdict})

    # Escalation — one extra shot per cluster, ONLY when the whole run
    # would otherwise deliver nothing: all refuted ops are disclosed and
    # the prompt forces the gate-kind sanity check over the wiring values
    # table ("the culprit is a DIFFERENT component"). Clusters whose
    # replies never validated get no escalation — there is nothing to
    # refute, and two format failures already spent their budget.
    if hypotheses and not any(h["verdict"]["confirmed"] for h in hypotheses):
        if refuted_total >= _MAX_REFUTED_IDEAS:
            if not stopped_early:
                stopped_early = True
                notes.append(
                    f"analysis stopped after {refuted_total} refuted "
                    f"ideas — escalation skipped; the best surviving "
                    f"idea is returned below.")
        else:
            tried: dict[int, list] = {}
            for h in hypotheses:
                tried.setdefault(h["cluster_index"], []).append(h["ops"])
            for ci, (cluster, payload) in enumerate(
                    zip(evres.clusters, evres.payloads)):
                if ci not in tried:
                    continue
                if refuted_total >= _MAX_REFUTED_IDEAS:
                    stopped_early = True
                    notes.append(
                        f"analysis stopped after {refuted_total} refuted "
                        f"ideas — remaining escalation(s) skipped; the "
                        f"best surviving idea is returned below.")
                    break
                cluster_rows = [r.row_index for r in cluster.rows]
                prompt = prompt_template.replace(
                    "<<PAYLOAD_JSON>>",
                    json.dumps(payload, indent=2, default=str)) + (
                    "\n\n[ESCALATION]\n"
                    + json.dumps({"refuted_ops": tried[ci]}, indent=2,
                                 default=str)
                    + (_DATA_REFUTED_STEER
                       if _touches_stored_data(tried[ci], circuit)
                       else ""))
                reply = ask(prompt)
                if not reply.get("ok"):
                    continue
                clean, _err = validate_hypothesis(
                    parse_agent_json(reply.get("text")))
                if clean is None:
                    continue
                verdict = verify(clean["ops"], cluster_rows)
                hypotheses.append({"cluster_index": ci,
                                   "cluster_rows": cluster_rows,
                                   "confidence": clean["confidence"],
                                   "hint": clean["hint"],
                                   "ops": clean["ops"],
                                   "explanation": clean["explanation"],
                                   "animation": clean["animation"],
                                   "verdict": verdict})

    ranked = dedupe_hypotheses(hypotheses)
    cards: list[dict] = []
    carded: set[int] = set()
    for i, h in enumerate(ranked):
        if not h["verdict"]["confirmed"] or len(cards) >= k_cards:
            continue
        carded.add(i)
        cards.append({
            "rank": len(cards) + 1,
            "confidence": h["confidence"],
            "cluster_rows": h["cluster_rows"],
            "hint": h["hint"],
            "verified": {
                "confirmed": True,
                "runner": h["verdict"]["runner"],
                "regressions": h["verdict"]["regressions"],
                "coach_residuals": h["verdict"].get("coach_residuals") or {},
            },
            "fix": {
                "ops": h["ops"],
                "ops_pretty": describe_ops(circuit, h["ops"]),
                "explanation_for_student": h["explanation"],
                "animation_script": validate_animation(
                    h["animation"], len(circuit.components)),
            },
        })
    for i, h in enumerate(ranked):
        if i in carded:
            continue
        if h["verdict"]["confirmed"]:
            reason = "beyond_top_k"
        elif h["verdict"]["apply_ok"]:
            reason = "refuted"
        else:
            reason = "patch_failed"
        # dropped ideas name the idea and the reason kind only — the
        # "applied cleanly, but row(s) … still fail" recap read as noise
        # to students (user ruling, r27); machine warnings still ride
        det = h["verdict"]["warning"]
        dropped.append({
            "cluster_rows": h["cluster_rows"],
            "reason": reason,
            "why": h["hint"].get("why") or h["hint"].get("suspect_region"),
            "detail": det,
            "ops_pretty": describe_ops(circuit, h["ops"]),
        })

    # Never leave the student empty-handed: when NOTHING passed the
    # verifier, the best-ranked surviving idea ships anyway — clearly
    # labeled unverified, with exactly which rows it failed to fix. The
    # run stays free, and the Analyze button stays live for a re-run
    # (which only counts when it delivers verified cards).
    best_unverified = None
    if not cards and ranked:
        b = ranked[0]
        best_unverified = {
            "confidence": b["confidence"],
            "cluster_rows": b["cluster_rows"],
            "hint": b["hint"],
            "fix": {"ops": b["ops"],
                    "ops_pretty": describe_ops(circuit, b["ops"]),
                    "explanation_for_student": b["explanation"]},
            "verdict": {"apply_ok": b["verdict"]["apply_ok"],
                        "runner": b["verdict"]["runner"],
                        "still_failing": b["verdict"]["still_failing"],
                        "regressions": b["verdict"]["regressions"],
                        "coach_residuals":
                            b["verdict"].get("coach_residuals") or {},
                        "warning": b["verdict"]["warning"]},
        }

    return {**base, "mode": "analysis",
            "diagnosis_lines": [_diagnosis_line(c) for c in evres.clusters],
            "clusters": evres.to_dict()["clusters"],
            "cards": cards,
            "best_unverified": best_unverified,
            "dropped_ideas": dropped,
            "stopped_early": stopped_early,
            "refuted_ideas": refuted_total,
            "timings": {"llm_s": llm_seconds, "verify_s": verify_seconds,
                        "total_s": round(time.monotonic() - t_begin, 2)},
            "verify_runner": "digital" if jar else "evaluator",
            "usage": usage, "llm_calls": calls}
