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
the F13 spoiler-guard: test rows only, gap-naming "why", no fix
talk. The full guard rules + tests.
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
_MAX_PROGRAM_WORDS = 8           # cap per program-extension proposal


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
        # 2.10: program-driven targets (cpu-like) — expose the existing
        # program so the model can derive per-cycle register state.
        rom = _program_rom_of(circuit)
        if rom is not None:
            words, addr_bits = rom
            target["has_program_rom"] = True
            target["program_words"] = [f"{w:x}" for w in words]
            target["rom_capacity_left"] = max(0, (1 << addr_bits) - len(words))
        targets.append(target)
    return targets


def _program_rom_of(circuit) -> tuple[list[int], int] | None:
    """(existing_words, addr_bits) of the single program ROM, else None."""
    roms = []
    for comp in circuit.components:
        if comp.element_name != "ROM":
            continue
        if str(comp.attributes.get("isProgramMemory", "")).lower() != "true":
            continue
        data = str(comp.attributes.get("Data", "") or "")
        try:
            words = [int(w, 16) for w in data.replace("\n", "").split(",")
                     if w.strip()]
        except ValueError:
            continue
        try:
            addr_bits = int(comp.attributes.get("AddrBits", 10))
        except (TypeError, ValueError):
            addr_bits = 10
        roms.append((words, addr_bits))
    return roms[0] if len(roms) == 1 else None


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
    proposals: list[dict], targets: list[dict],
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
        if p.get("program_words"):          # 2.10: atomic program extension
            ok_entry, reason = _validate_program_group(p, t)
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


def _validate_program_group(p: dict, t: dict) -> tuple[dict | None, str]:
    """2.10: validate an atomic program-extension proposal (words + rows).
    Returns (valid_entry, "") or (None, reason). No dedupe: the rows run in
    a fresh SECOND testcase whose replay context is different by design."""
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
    return {**p, "rows": list(p["rows"]),
            "program_words": [f"{w:x}" for w in words]}, ""


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
    used_model = model or DEFAULT_MODEL
    resp = call(prompt, model=used_model, max_tokens=1500)
    if not resp.get("ok"):
        return {"ok": False, "proposals": [], "rejected": [],
                "model": used_model,
                "error": resp.get("error") or "Model call failed.",
                "notes": []}

    proposals = parse_proposals(resp.get("text") or "")
    if not proposals:
        return {"ok": True, "proposals": [], "rejected": [],
                "model": used_model, "error": None,
                "notes": ["The model proposed nothing usable this time — "
                          "run it again or add rows by hand."]}
    valid, rejected = validate_and_dedupe(proposals, targets)
    notes: list[str] = []

    # HARDENING, strongest gate first:
    # 1) deterministic reference check — a row the lab reference disagrees
    #    with is WRONG and is dropped before the student ever sees it;
    # 2) model self-check — for un-referenced combinational targets, the
    #    model must independently re-derive its own rows' outputs; any
    #    mismatch drops the row.
    valid, rejected, notes = _reference_gate(valid, rejected, notes, targets)
    valid, rejected, notes = _selfcheck_gate(
        valid, rejected, notes, targets, call, used_model,
    )

    if any(g.get("program_words") for g in valid):
        notes.append("program-extending proposal(s) run in a SECOND testcase "
                     "on Accept — the official testcase is never edited and "
                     "is re-run unchanged as a regression guard.")
    if rejected:
        notes.append(f"{sum(len(r['rows']) for r in rejected)} proposed "
                     f"row(s) were dropped before display.")
    return {"ok": True, "proposals": valid, "rejected": rejected,
            "model": used_model, "error": None, "notes": notes}


def _reference_gate(valid, rejected, notes, targets):
    """2.9: judge every surviving row against the lab reference circuit,
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
    if checked:
        notes.append("rows were checked against the lab reference circuit.")
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
