"""Mode B row proposer: ONE hidden model call, grounded on the
coverage report, proposing non-redundant new test rows.

Trust boundary: the model's output is treated as UNTRUSTED text. Every
proposed row is (a) parsed from a strict JSON envelope, (b) validated
against the target testcase's real header with the oracle's validator,
and (c) deduplicated token-by-token against the rows the testcase already
contains and against the other proposals. Only survivors reach the UI,
and none of them touches a file until the student accepts them — at which
point /api/l3/inject machine-verifies the lot on a temp copy.

The prompt (prompts/l3_coverage_proposer_v1.txt) carries the v1 DRAFT of
the F13 spoiler-guard: test rows only, gap-naming "why". The full guard rules + tests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dlc.l3.coverage import TreeCoverageReport, scan_tree_coverage
from dlc.l3.oracle import InjectedRow, validate_rows
from dlc.llm.client import DEFAULT_MODEL, call_llm
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import _tokenize, extract_test_specs, match_variables_to_io

_PROMPT_DIR = Path(__file__).parent.parent.parent / "prompts"
_PROMPT_NAME = "l3_coverage_proposer_v1.txt"

_MAX_EXISTING_ROWS_SHOWN = 40    # keep the prompt lean on loop-heavy specs
_MAX_TOTAL_ROWS = 6              # hard cap across all accepted proposals
_MAX_PROGRAM_WORDS = 8           # 2.10: cap per program-extension proposal

# The proposer needs real reasoning about machine state and lab ISAs —
#  Override per install with l3_propose_model in
# ~/.dlc/config.json or the DLC_L3_PROPOSE_MODEL env var.
_PROPOSE_MODEL_FALLBACK = "claude-sonnet-4-6"


def _propose_model() -> str:
    import os
    env = os.environ.get("DLC_L3_PROPOSE_MODEL", "").strip()
    if env:
        return env
    try:
        from dlc.llm.client import _load_config
        cfg = _load_config().get("l3_propose_model")
        if isinstance(cfg, str) and cfg.strip():
            return cfg.strip()
    except Exception:
        pass
    return _PROPOSE_MODEL_FALLBACK


def _load_prompt() -> str:
    return (_PROMPT_DIR / _PROMPT_NAME).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Grounding: which files can receive rows, and what do they look like
# ---------------------------------------------------------------------------

def build_targets(report: TreeCoverageReport) -> list[dict]:
    """One entry per injectable circuit in the tree (has a testcase and a
    parseable file): headers, IO widths, and existing rows — everything the
    model needs to write a legal, non-redundant row."""
    targets: list[dict] = []
    for cov in report.circuits:
        if not cov.has_testcases or not cov.path:
            continue
        try:
            circuit = parse_dig_file(cov.path)
            specs = extract_test_specs(circuit)
        except Exception:
            continue
        if not specs:
            continue
        spec = specs[0]              # Mode B injects into the first testcase
        existing = [r.raw.strip() for r in spec.rows if not r.is_malformed]
        shown = existing[:_MAX_EXISTING_ROWS_SHOWN]
        bindings = match_variables_to_io(spec.headers, circuit)
        clock_col = next((col for col, b in bindings.items()
                          if b is not None and b.role == "clock"), None)
        target = {
            "file": cov.file,
            "spec_name": spec.name,
            "headers": list(spec.headers),
            "inputs": [{"label": c.label, "bits": c.bit_width()}
                       for c in circuit.inputs() if c.label],
            "outputs": [{"label": c.label, "bits": c.bit_width()}
                        for c in circuit.outputs() if c.label],
            "existing_rows": shown,
            "existing_rows_omitted": max(0, len(existing) - len(shown)),
            "has_clock": clock_col is not None,
            "clock_col": clock_col,
            "has_program_rom": False,
        }
        # program-driven targets (cpu-like) — expose the existing
        # program so the model can derive per-cycle register state.
        rom = _program_rom_of(circuit)
        if rom is not None:
            words, addr_bits = rom
            target["has_program_rom"] = True
            target["program_words"] = [f"{w:x}" for w in words]
            target["rom_capacity_left"] = max(0, (1 << addr_bits) - len(words))
            # Deterministic category truth: which lab instructions the
            # program already executes, and which are missing — decoded
            # from the words, never guessed by the model.
            from dlc.l3 import manifest as mf
            m = mf.find_manifest({c.file for c in report.circuits})
            pc = mf.program_categories(m, words) if m else None
            if pc is not None:
                target["program_categories_present"] = pc["present"]
                target["program_categories_missing"] = pc["missing"]
        targets.append(target)
    return targets


def _program_rom_of(circuit) -> tuple[list[int], int] | None:
    """(existing_words, addr_bits) of the single program ROM, else None."""
    from dlc.l3.manifest import program_rom_words
    return program_rom_words(circuit)


def build_prompt(report: TreeCoverageReport, targets: list[dict]) -> str:
    template = _load_prompt()
    slim = report.to_dict()
    for c in slim["circuits"]:
        c.pop("flags", None)         # clean scans have none; drop the field
        c.pop("path", None)          # never leak local filesystem paths
    return (template
            .replace("<<REPORT_JSON>>", json.dumps(slim, indent=1))
            .replace("<<TARGETS_JSON>>", json.dumps(targets, indent=1)))


# ---------------------------------------------------------------------------
# Untrusted-output handling
# ---------------------------------------------------------------------------

def parse_proposals(text: str) -> list[dict]:
    """Extract the proposals list from the model's reply. Tolerates prose
    or code fences around ONE JSON object; returns [] when nothing sane."""
    if not text:
        return []
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    raw = obj.get("proposals")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        rows = p.get("rows")
        if not isinstance(rows, list):
            continue
        rows = [r.strip() for r in rows if isinstance(r, str) and r.strip()]
        if not rows:
            continue
        entry = {
            "file": str(p.get("file", "")),
            "spec_name": str(p.get("spec_name", "")),
            "rows": rows,
            "why": str(p.get("why", "")).strip(),
        }
        pw = p.get("program_words")
        if isinstance(pw, list):        # 2.10: optional program extension
            pw = [str(w).strip() for w in pw if str(w).strip()]
            if pw:
                entry["program_words"] = pw
        out.append(entry)
    return out


def _row_key(raw: str, headers: list[str]) -> tuple:
    """Comparison key for redundancy: token VALUES, not spelling — `10`,
    `0xA` and `0b1010` are the same row cell."""
    cells = raw.split("#", 1)[0].split()
    key = []
    for cell in cells[:len(headers)]:
        tok = _tokenize(cell)
        key.append(("v", tok.value) if tok.kind == "int" else ("r", tok.raw.upper()))
    return tuple(key)


def validate_and_dedupe(
    proposals: list[dict], targets: list[dict], manifest: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split the model's proposals into (valid, rejected). Rejected entries
    carry a `reason`; valid rows are legal for their spec, non-duplicate,
    and capped at _MAX_TOTAL_ROWS across all groups."""
    by_file = {t["file"]: t for t in targets}
    seen: dict[str, set] = {t["file"]: set() for t in targets}
    for t in targets:
        for raw in t["existing_rows"]:
            seen[t["file"]].add(_row_key(raw, t["headers"]))

    valid: list[dict] = []
    rejected: list[dict] = []
    total = 0
    for p in proposals:
        t = by_file.get(p["file"])
        if t is None or p["spec_name"] != t["spec_name"]:
            rejected.append({**p, "reason": "unknown target file or testcase"})
            continue
        if p.get("program_words"):         
            ok_entry, reason = _validate_program_group(p, t, manifest)
            if ok_entry is not None:
                valid.append(ok_entry)
            else:
                rejected.append({**p, "reason": reason})
            continue
        good_rows: list[str] = []
        bad: list[tuple[str, str]] = []
        for raw in p["rows"]:
            if total + len(good_rows) >= _MAX_TOTAL_ROWS:
                bad.append((raw, f"over the {_MAX_TOTAL_ROWS}-row cap"))
                continue
            try:
                # header-shape + token legality, same validator inject uses
                _validate_against_headers(raw, t["headers"], p["spec_name"])
            except ValueError as exc:
                bad.append((raw, str(exc)))
                continue
            k = _row_key(raw, t["headers"])
            if k in seen[p["file"]]:
                bad.append((raw, "duplicate of an existing or proposed row"))
                continue
            seen[p["file"]].add(k)
            good_rows.append(raw)
        if good_rows:
            total += len(good_rows)
            valid.append({**p, "rows": good_rows})
        for raw, reason in bad:
            rejected.append({"file": p["file"], "spec_name": p["spec_name"],
                             "rows": [raw], "why": p.get("why", ""),
                             "reason": reason})
    return valid, rejected


def _validate_program_group(
    p: dict, t: dict, manifest: dict | None = None,
) -> tuple[dict | None, str]:
    """validate an atomic program-extension proposal (words + rows).
    Returns (valid_entry, "") or (None, reason). Row dedupe is skipped (a
    fresh SECOND testcase has a different replay context by design), but
    WORDS are deduped against the existing program, and — when the manifest
    can decode — each word must be an instruction the lab defines; its
    category is recorded in `word_info` so the UI states deterministically
    what the extension tests, instead of trusting the model's claim."""
    from dlc.l3.oracle import parse_program_words
    if not (t.get("has_clock") and t.get("has_program_rom")):
        return None, ("program_words are only valid for clocked targets "
                      "with a program ROM")
    try:
        words = parse_program_words(p["program_words"])
    except ValueError as exc:
        return None, str(exc)
    if not 1 <= len(words) <= _MAX_PROGRAM_WORDS:
        return None, f"program extension must be 1..{_MAX_PROGRAM_WORDS} words"
    if len(words) > t.get("rom_capacity_left", 0):
        return None, "program extension does not fit in the ROM"
    if len(p["rows"]) != len(words):
        return None, (f"needs exactly one row per program word "
                      f"({len(words)} word(s), {len(p['rows'])} row(s))")

    existing = set()
    try:
        existing = {int(w, 16) for w in t.get("program_words", [])}
    except ValueError:
        pass
    seen_new: set[int] = set()
    from dlc.l3 import manifest as mf
    can_decode = bool((manifest or {}).get("program_decode"))
    word_info: list[dict] = []
    for w in words:
        if w in existing:
            d = mf.decode_program_word(manifest, w) if can_decode else None
            cat = f" (category '{d['category']}')" if d and d["category"] else ""
            return None, (f"word {w:x} duplicates an instruction already in "
                          f"the program{cat} — nothing new would execute")
        if w in seen_new:
            return None, f"word {w:x} appears twice in the extension"
        seen_new.add(w)
        if can_decode:
            d = mf.decode_program_word(manifest, w)
            if not d or not d["category"]:
                return None, (f"word {w:x} is not an instruction this lab "
                              f"defines — the lab ISA cannot execute it")
            missing = t.get("program_categories_missing") or []
            word_info.append({
                "word": f"{w:x}",
                "category": d["category"],
                "closes_gap": d["category"] in missing,
            })

    clk = t.get("clock_col")
    for raw in p["rows"]:
        try:
            _validate_against_headers(raw, t["headers"], p["spec_name"])
        except ValueError as exc:
            return None, str(exc)
        cells = dict(zip(t["headers"], raw.split("#", 1)[0].split()))
        if clk and cells.get(clk, "").upper() != "C":
            return None, (f"every program-extension row must clock the "
                          f"circuit: expected C in column {clk!r}: {raw!r}")
    entry = {**p, "rows": list(p["rows"]),
             "program_words": [f"{w:x}" for w in words]}
    if word_info:
        entry["word_info"] = word_info
    return entry, ""


def _validate_against_headers(raw: str, headers: list[str], spec_name: str) -> None:
    """Reuse the oracle's row validator without re-parsing the file: build a
    minimal spec-shaped object carrying just the headers."""
    class _HeaderOnly:
        pass
    shim = _HeaderOnly()
    shim.headers = list(headers)
    shim.name = spec_name
    validate_rows(shim, [InjectedRow(raw=raw)])


# ---------------------------------------------------------------------------
# The one hidden call
# ---------------------------------------------------------------------------

def propose_rows(
    dig_path: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    call=None,
) -> dict:
    """Scan → prompt → ONE model call → parse/validate/dedupe.

    Returns {ok, proposals, rejected, model, error, notes}. `call` is
    injectable (and resolved at RUN time, so tests may also monkeypatch
    module-level call_llm) — tests never touch the network. A scan that
    still has disagreements refuses to propose (the UI redirects to
    Mode A first).
    """
    if call is None:
        call = call_llm
    report = scan_tree_coverage(dig_path)
    if report.total_flags > 0:
        return {"ok": False, "proposals": [], "rejected": [],
                "model": None,
                "error": ("Tests and circuit still disagree somewhere — "
                          "resolve that before asking for new rows."),
                "notes": []}
    targets = build_targets(report)
    if not targets:
        return {"ok": False, "proposals": [], "rejected": [],
                "model": None,
                "error": "No testcase anywhere in this tree to extend.",
                "notes": []}

    prompt = build_prompt(report, targets)
    used_model = model or _propose_model()
    # stronger models front-load visible analysis before the JSON;
    # 1500 tokens truncated mid-thought and parsed as "nothing usable".
    resp = call(prompt, model=used_model, max_tokens=4000)
    if not resp.get("ok"):
        return {"ok": False, "proposals": [], "rejected": [],
                "model": used_model,
                "error": resp.get("error") or "Model call failed.",
                "notes": []}

    proposals = parse_proposals(resp.get("text") or "")
    if not proposals and (resp.get("text") or "").strip():
        # the model sometimes writes analysis and never reaches the
        # JSON. One bounded retry with a terse reminder fixes most of it.
        resp2 = call(prompt + "\n\nREMINDER: output ONLY the JSON object "
                     "now — no analysis text.",
                     model=used_model, max_tokens=4000)
        if resp2.get("ok"):
            proposals = parse_proposals(resp2.get("text") or "")
    if not proposals:
        return {"ok": True, "proposals": [], "rejected": [],
                "model": used_model, "error": None,
                "notes": ["The coach found nothing trustworthy to propose "
                          "this time — try Propose again."]}
    from dlc.l3 import manifest as mf
    m = mf.find_manifest({t["file"] for t in targets})
    valid, rejected = validate_and_dedupe(proposals, targets, manifest=m)
    notes: list[str] = []

    # HARDENING, strongest gates first:
    # 1) deterministic REPLAY pre-gate — for clocked targets (where the
    #    reference and self-check gates cannot run) the student's own
    #    circuit is replayed through the official rows and the proposed
    #    rows; a row whose expected values ignore the machine state at
    #    that point is dropped. On a clean-scanned circuit (tests and
    #    circuit agree everywhere) such a row is almost surely a wrong
    #    expectation, not a discovered bug.
    # 2) deterministic reference check — a row the lab reference disagrees
    #    with is WRONG and is dropped before the student ever sees it;
    # 3) model self-check — for un-referenced combinational targets, the
    #    model must independently re-derive its own rows' outputs; any
    #    mismatch drops the row.
    paths = {c.file: c.path for c in report.circuits if c.path}
    valid, rejected, notes = _category_gate(valid, rejected, notes, targets, m)
    valid, rejected, notes = _replay_gate(valid, rejected, notes, targets, paths)
    valid, rejected, notes = _reference_gate(valid, rejected, notes, targets)
    valid, rejected, notes = _selfcheck_gate(
        valid, rejected, notes, targets, call, used_model,
    )
    for r in rejected:                     
        r["kind"] = _classify_reason(r.get("reason", ""))

    return {"ok": True, "proposals": valid, "rejected": rejected,
            "model": used_model, "error": None, "notes": notes}


def _classify_reason(reason: str) -> str:
    r = reason.lower()
    if "duplicate" in r or "already in the program" in r or "appears twice" in r:
        return "duplicate"
    if ("not an instruction" in r or "instruction set" in r
            or "does not define" in r or "doesn't define" in r):
        return "undefined_op"
    if ("wrong expected value" in r or "lab reference" in r
            or "self-check" in r or "machine state" in r
            or "follows a dropped row" in r):
        return "wrong_expectation"
    return "format"


def _category_gate(valid, rejected, notes, targets, manifest):
    if not manifest:
        return valid, rejected, notes
    from dlc.l3.manifest import _cell_value
    cats_by_file = manifest.get("categories") or {}
    kept: list[dict] = []
    for g in valid:
        cats = cats_by_file.get(g["file"])
        t = next((x for x in targets if x["file"] == g["file"]), None)
        if not cats or t is None or g.get("program_words"):
            kept.append(g)               # program words judged by decode gate
            continue
        headers = t["headers"]
        pred_cols = set()
        parsed = []
        for cat in cats:
            when = {}
            for col, v in (cat.get("when") or {}).items():
                val = _cell_value(str(v)) if not isinstance(v, int) else v
                if val is not None:
                    when[col] = val
            if when:
                parsed.append(when)
                pred_cols |= set(when)
        if not parsed or any(c not in headers for c in pred_cols):
            kept.append(g)
            continue
        idx = {h: i for i, h in enumerate(headers)}
        good: list[str] = []
        for raw in g["rows"]:
            cells = raw.split("#", 1)[0].split()
            vals = {}
            for col in pred_cols:
                i = idx[col]
                vals[col] = _cell_value(cells[i]) if i < len(cells) else None
            if any(v is None for v in vals.values()):
                good.append(raw)        
                continue
            if any(all(vals.get(c) == w for c, w in when.items())
                   for when in parsed):
                good.append(raw)
                continue
            rejected.append({
                "file": g["file"], "spec_name": g["spec_name"],
                "rows": [raw], "why": g.get("why", ""),
                "reason": ("tests an operation this lab does not define "
                           f"({', '.join(f'{c}={vals[c]}' for c in sorted(vals))}"
                           " matches no defined category) — no test needed"),
            })
        if good:
            kept.append({**g, "rows": good})
    return kept, rejected, notes


def _replay_gate(valid, rejected, notes, targets, paths):
    """deterministic pre-check for CLOCKED targets — replay the
    student's circuit through the official rows, then evaluate each proposed
    row in sequence. Rows whose asserted outputs disagree with the machine
    state at that point are dropped with the computed values in the reason
    (it is the student's own circuit — nothing secret is revealed).
    Program extensions are judged the same way with the ROM pre-extended;
    they drop as a unit. A replay that errors never blocks."""
    from dlc.l3.coverage import replay_appended_rows
    by_file = {t["file"]: t for t in targets}
    kept: list[dict] = []
    checked = False
    for g in valid:
        t = by_file.get(g["file"])
        path = paths.get(g["file"])
        if t is None or path is None or not t.get("has_clock"):
            kept.append(g)
            continue
        try:
            verdicts = replay_appended_rows(
                path, g["spec_name"], g["rows"], g.get("program_words"),
            )
        except Exception:
            kept.append(g)               # a broken replay never blocks
            continue
        checked = True
        if g.get("program_words"):       # atomic: any disagreement kills all
            bad = [v for v in verdicts if v["verdict"] == "disagrees"]
            if bad:
                rejected.append({
                    "file": g["file"], "spec_name": g["spec_name"],
                    "rows": [v["row"] for v in bad],
                    "why": g.get("why", ""),
                    "reason": ("expected values don't match the machine "
                               "state at that point in the program — "
                               + "; ".join(v["detail"] for v in bad)),
                })
            else:
                kept.append(g)
            continue
        # Prefix-keep: a clocked row's expectations were derived under the
        # state left by the rows BEFORE it — once one row drops, every
        # later row's context is gone, so they drop with it.
        good: list[str] = []
        bad_hit = False
        for v in verdicts:
            if bad_hit:
                rejected.append({
                    "file": g["file"], "spec_name": g["spec_name"],
                    "rows": [v["row"]], "why": g.get("why", ""),
                    "reason": ("follows a dropped row — its expected values "
                               "assume that row's state change happened"),
                })
                continue
            if v["verdict"] == "disagrees":
                bad_hit = True
                rejected.append({
                    "file": g["file"], "spec_name": g["spec_name"],
                    "rows": [v["row"]], "why": g.get("why", ""),
                    "reason": ("wrong expected value for the state after "
                               "the existing rows — " + v["detail"]),
                })
                continue
            good.append(v["row"])
        if good:
            kept.append({**g, "rows": good})
    return kept, rejected, notes


def _reference_gate(valid, rejected, notes, targets):
    """judge every surviving row against the lab reference circuit,
    when one is configured. Deterministic; 'unresolved' rows pass through
    to the normal inject verification."""
    from dlc.l3 import manifest as mf
    m = mf.find_manifest({t["file"] for t in targets})
    ref_dir = mf.reference_dir(m)
    if not ref_dir or not ref_dir.is_dir():
        return valid, rejected, notes
    by_file = {t["file"]: t for t in targets}
    kept: list[dict] = []
    checked = False
    for g in valid:
        ref_file = ref_dir / g["file"]
        t = by_file.get(g["file"])
        if not ref_file.is_file() or t is None:
            kept.append(g)
            continue
        try:
            verdicts = mf.reference_row_verdicts(
                ref_file, t["headers"], g["rows"],
            )
        except Exception:
            kept.append(g)               # a broken reference never blocks
            continue
        checked = True
        good = [v["row"] for v in verdicts if v["verdict"] != "disagrees"]
        for v in verdicts:
            if v["verdict"] == "disagrees":
                rejected.append({"file": g["file"], "spec_name": g["spec_name"],
                                 "rows": [v["row"]], "why": g.get("why", ""),
                                 "reason": f"disagrees with the lab reference "
                                           f"({v['detail']})"})
        if good:
            kept.append({**g, "rows": good})
    return kept, rejected, notes


_SELFCHECK_PROMPT = "l3_row_selfcheck_v1.txt"


def _selfcheck_gate(valid, rejected, notes, targets, call, used_model):
    """Second model pass: re-derive outputs for the surviving rows with the
    output cells hidden; keep only rows whose asserted outputs the model
    reproduces (null = unsure = drop). Skipped for clocked targets (a lone
    row has no replay context) — those rely on the reference/inject."""
    by_file = {t["file"]: t for t in targets}
    candidates = []                       # (group_idx, row_idx_in_group)
    payload_rows = []
    for gi, g in enumerate(valid):
        t = by_file.get(g["file"])
        if t is None or t.get("has_clock"):
            continue
        out_cols = [o["label"] for o in t["outputs"]]
        if not out_cols:
            continue
        for ri, raw in enumerate(g["rows"]):
            cells = raw.split("#", 1)[0].split()
            masked = [
                "?" if h in out_cols else (cells[i] if i < len(cells) else "?")
                for i, h in enumerate(t["headers"])
            ]
            payload_rows.append({
                "index": len(payload_rows),
                "file": g["file"],
                "inputs": " ".join(masked),
            })
            candidates.append((gi, ri, t))
    if not candidates:
        return valid, rejected, notes

    t0 = by_file[valid[candidates[0][0]]["file"]]
    template = (_PROMPT_DIR / _SELFCHECK_PROMPT).read_text(encoding="utf-8")
    prompt = (template
              .replace("<<HEADERS_JSON>>", json.dumps(
                  {c[2]["file"]: c[2]["headers"] for c in candidates}))
              .replace("<<OUTPUT_COLS_JSON>>", json.dumps(
                  {c[2]["file"]: [o["label"] for o in c[2]["outputs"]]
                   for c in candidates}))
              .replace("<<ROWS_JSON>>", json.dumps(payload_rows, indent=1)))
    resp = call(prompt, model=used_model, max_tokens=1200)
    if not resp.get("ok"):
        notes.append("self-check call failed — rows pass through to the "
                     "inject verification unchecked.")
        return valid, rejected, notes

    m = re.search(r"\{.*\}", resp.get("text") or "", re.S)
    derived: dict[int, dict] = {}
    if m:
        try:
            for r in json.loads(m.group(0)).get("rows", []):
                if isinstance(r, dict) and isinstance(r.get("outputs"), dict):
                    derived[int(r.get("index", -1))] = r["outputs"]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    drop: set[tuple[int, int]] = set()
    for pi, (gi, ri, t) in enumerate(candidates):
        outs = derived.get(pi)
        raw = valid[gi]["rows"][ri]
        cells = raw.split("#", 1)[0].split()
        by_col = dict(zip(t["headers"], cells))
        ok = outs is not None
        if outs is not None:
            for col in (o["label"] for o in t["outputs"]):
                want = _row_cell_value(by_col.get(col))
                got = _row_cell_value(outs.get(col))
                if want is None:
                    continue              # don't-care in the proposal
                if got is None or got != want:
                    ok = False
                    break
        if not ok:
            drop.add((gi, ri))
    if not drop:
        notes.append("self-check confirmed every proposed row.")
        return valid, rejected, notes

    kept: list[dict] = []
    for gi, g in enumerate(valid):
        rows = [r for ri, r in enumerate(g["rows"]) if (gi, ri) not in drop]
        for ri, r in enumerate(g["rows"]):
            if (gi, ri) in drop:
                rejected.append({"file": g["file"], "spec_name": g["spec_name"],
                                 "rows": [r], "why": g.get("why", ""),
                                 "reason": "failed the coach's self-check "
                                           "(could not re-derive the same "
                                           "expected outputs)"})
        if rows:
            kept.append({**g, "rows": rows})
    notes.append(f"self-check dropped {len(drop)} row(s).")
    return kept, rejected, notes


def _row_cell_value(cell) -> int | None:
    if cell is None:
        return None
    tok = _tokenize(str(cell).strip())
    return tok.value if tok.kind == "int" else None
