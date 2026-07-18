"""Per-lab manifest (2.9): the deterministic intent-reference for Mode B.

A manifest is one JSON file describing ONE lab:

    {
      "lab": "lab5",
      "applies_to": ["cpu.dig", "alu.dig", ...],
      "categories": {
        "control-unit.dig": [
          {"name": "addi", "when": {"opcode": "0b0010011", "funct3": "0b000"}},
          ...
        ]
      },
      "official_tests": {"cpu.dig": "<sha1 of the normalized dataString>"},
      "reference_dir": null
    }

What each block buys, all deterministic:
  categories       CATEGORY-graded coverage (ratified 07-11): a circuit is
                   GREEN when every named category is touched by some row,
                   however small the raw input-space percentage is.
  official_tests   fingerprint of the instructor-issued testcase. If a
                   student's testcase still hashes to it, a disagreement on
                   that file is classified "official" — the test is right,
                   the circuit is wrong, full stop.
  reference_dir    folder holding the instructor's solution .dig files
                   (NEVER shipped in the repo; typically set per machine via
                   the DLC_REFERENCE_DIR env var, which overrides this
                   field). With it, a proposed row can be judged against
                   the reference before a student ever sees it.

No manifest, or a tree no manifest applies to => every consumer behaves
exactly as before. Manifests live in data/manifests/ (override the folder
with DLC_MANIFEST_DIR).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.sim import inputs_for_row, simulate
from dlc.testing.spec import _tokenize, match_variables_to_io

_DEFAULT_DIR = Path(__file__).parent.parent.parent / "data" / "manifests"


def manifest_dir() -> Path:
    env = os.environ.get("DLC_MANIFEST_DIR")
    return Path(env) if env else _DEFAULT_DIR


def load_manifests() -> list[dict]:
    d = manifest_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(m, dict) and m.get("applies_to"):
                out.append(m)
        except Exception:
            continue                      # a broken manifest never breaks scans
    return out


def find_manifest(filenames: set[str]) -> dict | None:
    """First manifest whose applies_to intersects the tree's file names."""
    for m in load_manifests():
        if filenames & set(m.get("applies_to", [])):
            return m
    return None


def reference_dir(manifest: dict | None) -> Path | None:
    env = os.environ.get("DLC_REFERENCE_DIR")
    if env:
        return Path(env)
    ref = (manifest or {}).get("reference_dir")
    return Path(ref) if ref else None


# ---------------------------------------------------------------------------
# Official-test fingerprints
# ---------------------------------------------------------------------------

def normalized_test_hash(raw_data_string: str) -> str:
    """sha1 over the dataString with comments stripped and whitespace
    collapsed, so cosmetic edits don't break the fingerprint."""
    lines = []
    for line in (raw_data_string or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(" ".join(line.split()))
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()


def official_status(manifest: dict | None, file: str,
                    raw_data_string: str) -> str | None:
    """'official' when the file's testcase still matches the instructor
    fingerprint, 'modified' when a fingerprint exists but differs, None
    when the manifest says nothing about this file."""
    if not manifest:
        return None
    want = (manifest.get("official_tests") or {}).get(file)
    if not want:
        return None
    return "official" if normalized_test_hash(raw_data_string) == want else "modified"


# ---------------------------------------------------------------------------
# Category-graded coverage
# ---------------------------------------------------------------------------

def _cell_value(cell: str) -> int | None:
    tok = _tokenize(cell)
    return tok.value if tok.kind == "int" else None


def _norm_when(when: dict) -> dict[str, int]:
    out = {}
    for col, v in (when or {}).items():
        val = _cell_value(str(v)) if not isinstance(v, int) else v
        if val is not None:
            out[col] = val
    return out


def category_coverage(manifest: dict | None, file: str, spec) -> dict | None:
    """{total, touched, missing} for the manifest categories bound to this
    file, judged over the spec's rows; None when no categories apply or the
    category columns aren't all present in the headers."""
    if not manifest:
        return None
    cats = (manifest.get("categories") or {}).get(file)
    if not cats:
        return None
    headers = list(spec.headers)
    col_idx = {h: i for i, h in enumerate(headers)}
    parsed = []
    for cat in cats:
        when = _norm_when(cat.get("when", {}))
        if not when or any(c not in col_idx for c in when):
            return None                    # predicate can't bind — stay silent
        parsed.append((cat.get("name", "?"), when))

    touched: set[str] = set()
    for row in spec.rows:
        if row.is_malformed:
            continue
        cells = row.raw.split("#", 1)[0].split()
        for name, when in parsed:
            if name in touched:
                continue
            ok = True
            for col, want in when.items():
                i = col_idx[col]
                got = _cell_value(cells[i]) if i < len(cells) else None
                if got != want:
                    ok = False
                    break
            if ok:
                touched.add(name)
    names = [n for n, _ in parsed]
    return {
        "total": len(names),
        "touched": [n for n in names if n in touched],
        "missing": [n for n in names if n not in touched],
    }


# ---------------------------------------------------------------------------
# Program-word decoding : deterministic instruction-category
# judgment for program-ROM extensions. The manifest's optional block
#
#   "program_decode": {
#     "categories_from": "control-unit.dig",
#     "fields": {"opcode": [0, 7], "funct3": [12, 3], "funct7": [25, 7],
#                "rd": [7, 5], "rs1": [15, 5], "rs2": [20, 5]}
#   }
#
# maps bit ranges [low_bit, width] of a program word onto the SAME column
# names the categories_from file's category predicates use — so "which lab
# instruction is this word, and which category does it close?" is decided
# by decode, never by the model's own claim.
# ---------------------------------------------------------------------------

def decode_program_word(manifest: dict | None, word: int) -> dict | None:
    """{'category': name|None, 'fields': {...}} for one program word, or
    None when the manifest defines no program_decode block."""
    pd = (manifest or {}).get("program_decode")
    if not isinstance(pd, dict) or not pd.get("fields"):
        return None
    fields: dict[str, int] = {}
    for name, spec in pd["fields"].items():
        try:
            lo, width = int(spec[0]), int(spec[1])
        except (TypeError, ValueError, IndexError):
            continue
        fields[name] = (word >> lo) & ((1 << width) - 1)
    cats = (manifest.get("categories") or {}).get(
        pd.get("categories_from") or "", [])
    category = None
    for cat in cats:
        when = _norm_when(cat.get("when", {}))
        if when and all(fields.get(c) == v for c, v in when.items()):
            category = cat.get("name")
            break
    return {"category": category, "fields": fields}


def program_categories(manifest: dict | None, words: list[int]) -> dict | None:
    """Deterministic category coverage of a PROGRAM: which lab-defined
    instruction categories the given words execute, and which are missing.
    None when the manifest can't decode words."""
    pd = (manifest or {}).get("program_decode")
    if not isinstance(pd, dict):
        return None
    cats = (manifest.get("categories") or {}).get(
        pd.get("categories_from") or "", [])
    if not cats:
        return None
    names = [c.get("name", "?") for c in cats]
    present: set[str] = set()
    for w in words:
        d = decode_program_word(manifest, w)
        if d and d["category"]:
            present.add(d["category"])
    return {
        "present": [n for n in names if n in present],
        "missing": [n for n in names if n not in present],
    }


# ---------------------------------------------------------------------------
# Reference verdicts (the deterministic wrong-row killer)
# ---------------------------------------------------------------------------

def reference_row_verdicts(
    ref_file: Path, headers: list[str], rows: list[str],
) -> list[dict]:
    """Run each candidate row's INPUTS through the reference circuit and
    compare the reference's outputs against the row's asserted outputs.

    Verdict per row: 'agrees' | 'disagrees' (plus which columns) |
    'unresolved' (reference evaluator couldn't settle an asserted output —
    e.g. clocked designs, which need a replay context; those rows are left
    for the normal inject verification instead).
    """
    circuit = parse_dig_file(str(ref_file))
    netlist = build_netlist(circuit)
    graph = build_signal_graph(circuit, netlist)
    bindings = match_variables_to_io(headers, circuit)
    out_cols = [h for h, b in bindings.items() if b and b.role == "output"]
    clocked = any(b and b.role == "clock" for b in bindings.values())

    verdicts: list[dict] = []
    for raw in rows:
        if clocked:
            verdicts.append({"row": raw, "verdict": "unresolved",
                             "detail": "clocked reference needs full replay"})
            continue
        cells = raw.split("#", 1)[0].split()
        by_col = dict(zip(headers, cells))

        class _Row:                        # inputs_for_row's duck shape
            pass
        shim = _Row()
        shim.values = [_tokenize(c) for c in cells]
        inp = inputs_for_row(circuit, headers, shim)
        res = simulate(circuit, netlist, graph, inp)

        bad: list[str] = []
        unresolved = False
        for col in out_cols:
            want = _cell_value(by_col.get(col, ""))
            if want is None:
                continue                   # don't-care cell: nothing asserted
            b = bindings[col]
            got = res.output_values.get(col)
            if got is None:
                unresolved = True
                continue
            width = b.bit_width or 0
            mask = (1 << width) - 1 if width else None
            same = ((got & mask) == (want & mask)) if mask else (got == want)
            if not same:
                bad.append(f"{col}: reference computes "
                           f"{got & mask if mask else got}, row says {want}")
        if bad:
            verdicts.append({"row": raw, "verdict": "disagrees",
                             "detail": "; ".join(bad)})
        elif unresolved:
            verdicts.append({"row": raw, "verdict": "unresolved",
                             "detail": "reference output not resolved"})
        else:
            verdicts.append({"row": raw, "verdict": "agrees", "detail": ""})
    return verdicts
