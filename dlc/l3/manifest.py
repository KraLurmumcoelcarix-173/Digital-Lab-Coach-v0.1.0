"""Per-lab manifest: the deterministic intent-reference for Mode B.

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
    when nothing is known about this file. The user-configured official
    store (Settings ⚙) is consulted FIRST and works without any manifest —
    it is the instructor-controlled truth; manifest fingerprints are the
    shipped fallback."""
    from dlc.l3 import official_store
    st = official_store.status_for(file, raw_data_string)
    if st is not None:
        return st
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
# Program-word decoding: deterministic instruction-category
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

def program_rom_words(circuit) -> tuple[list[int], int] | None:
    """(words, addr_bits) of the circuit's single program-memory ROM, else
    None (absent or ambiguous)."""
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
# RV32I word quality + encoding: the program_decode
# block is RV32I-shaped by design (see its note), so the two ALU opcode
# conventions below are manifest knowledge, not circuit guesses. Everything
# here is deterministic and round-trip-verified against decode_program_word —
# these helpers refuse to answer rather than answer wrong.
# ---------------------------------------------------------------------------

_OPCODE_RTYPE = 0b0110011      # R-type ALU: reads rs1+rs2, writes rd
_OPCODE_ITYPE_ALU = 0b0010011  # I-type ALU: reads rs1+imm[31:20], writes rd


def lazy_word_reason(manifest: dict | None, word: int) -> str | None:
    """Why this program word is a LAZY test, or None when it is fine (or
    when the manifest lacks the fields to judge — never guess).

    Lazy = the word can never distinguish a correct circuit from a broken
    one: its operands are all zero (EVERY lab ALU instruction computes the
    same 0, so the row cannot tell one operation from another), or it both
    discards its result into x0 AND reads only x0 (nothing register-
    dependent becomes observable). Deliberately narrow on the other side:
    addi xN, x0, <imm≠0> is the idiomatic register loader, and
    addi x0, xN, 0 is the lab's READ-BACK idiom — writing x0 while
    exposing xN on a register-file read port — so neither is ever
    flagged."""
    d = decode_program_word(manifest, word)
    if not d or not d["category"]:
        return None
    f = d["fields"]
    op, rd, rs1 = f.get("opcode"), f.get("rd"), f.get("rs1")
    if op == _OPCODE_RTYPE:
        if rs1 == 0 and f.get("rs2") == 0:
            return ("it reads x0 for BOTH operands — every lab instruction "
                    "computes 0 on (0, 0), so the row cannot tell one "
                    "operation from another")
        return None                    # any nonzero source is observable
    if op == _OPCODE_ITYPE_ALU:
        if rs1 == 0 and ((word >> 20) & 0xFFF) == 0:
            return ("it reads x0 with immediate 0 — every lab instruction "
                    "computes 0 on (0, 0), so the row cannot tell one "
                    "operation from another")
        if rd == 0 and rs1 == 0:
            return ("it discards its result into x0 and reads only x0 — "
                    "nothing register-dependent is observable")
        return None                    # rd=x0 with rs1≠0 is the read-back
    return None                        # rd/rs1 only architectural for ALU ops


def encode_category_word(
    manifest: dict | None, category: str, *,
    rd: int = 0, rs1: int = 0, rs2: int = 0, imm: int = 0,
) -> int | None:
    """Deterministically encode ONE program word of a named category using
    the manifest's own field map + that category's `when` predicate. The
    result is verified by decoding it back: anything that does not land on
    the same category returns None instead of a wrong word."""
    pd = (manifest or {}).get("program_decode")
    if not isinstance(pd, dict) or not pd.get("fields"):
        return None
    cats = (manifest.get("categories") or {}).get(
        pd.get("categories_from") or "", [])
    when = None
    for cat in cats:
        if cat.get("name") == category:
            when = _norm_when(cat.get("when", {}))
            break
    if not when:
        return None
    fields = pd["fields"]

    def place(name: str, val: int) -> int | None:
        spec = fields.get(name)
        try:
            lo, width = int(spec[0]), int(spec[1])
        except (TypeError, ValueError, IndexError):
            return None
        return (val & ((1 << width) - 1)) << lo

    word = 0
    for col, v in when.items():
        bits = place(col, v)
        if bits is None:
            return None
        word |= bits
    if when.get("opcode") == _OPCODE_ITYPE_ALU:
        word |= (imm & 0xFFF) << 20            # I-type immediate [31:20]
    else:
        word |= place("rs2", rs2) or 0
    for name, val in (("rd", rd), ("rs1", rs1)):
        bits = place(name, val)
        if bits is None:
            return None
        word |= bits
    d = decode_program_word(manifest, word)
    if not d or d["category"] != category:
        return None                            # round-trip failed: no lies
    return word


_M32 = 0xFFFFFFFF


def _signed32(v: int) -> int:
    return v - (1 << 32) if v & (1 << 31) else v


def constant_registers(manifest: dict | None, words: list[int]) -> dict[int, int]:
    """Register file AFTER the program, for every register whose value is
    deterministically computable from the ISA alone: constant propagation
    over the lab's decoded ALU categories (registers start at 0; RV32I
    add/sub/and/or/slt + addi/andi/slti semantics, 32-bit two's-complement,
    returned unsigned). A write this walker cannot compute — an undecoded
    word, an untracked category, an unknown source — DROPS its rd from the
    result: false knowledge is the only failure mode that matters here.
    Empty dict when the manifest cannot decode rd at all."""
    pd = (manifest or {}).get("program_decode") or {}
    if "rd" not in (pd.get("fields") or {}):
        return {}
    known: dict[int, int] = {r: 0 for r in range(32)}
    for w in words:
        d = decode_program_word(manifest, w)
        if not d:
            return {}
        f = d["fields"]
        rd = f.get("rd") or 0
        cat, op = d["category"], f.get("opcode")
        out = None
        if cat and op == _OPCODE_ITYPE_ALU:
            a = known.get(f.get("rs1"))
            imm = (w >> 20) & 0xFFF
            imm = imm - 0x1000 if imm & 0x800 else imm       # sign-extend
            if a is not None:
                if cat == "addi":
                    out = (a + imm) & _M32
                elif cat == "andi":
                    out = a & (imm & _M32)
                elif cat == "slti":
                    out = 1 if _signed32(a) < imm else 0
        elif cat and op == _OPCODE_RTYPE:
            a, b = known.get(f.get("rs1")), known.get(f.get("rs2"))
            if a is not None and b is not None:
                if cat == "add":
                    out = (a + b) & _M32
                elif cat == "sub":
                    out = (a - b) & _M32
                elif cat == "and":
                    out = a & b
                elif cat == "or":
                    out = a | b
                elif cat == "slt":
                    out = 1 if _signed32(a) < _signed32(b) else 0
        if rd == 0:
            continue                       # x0 is never written
        if out is None:
            known.pop(rd, None)            # can't compute => don't pretend
        else:
            known[rd] = out
    return known


def category_word_examples(
    manifest: dict | None, missing: list[str], existing_words: list[int] = (),
) -> list[dict]:
    """Machine-verified, NON-lazy example encodings for the missing program
    categories — [{category, word, asm, reads?}], in `missing` order.
    Source registers prefer registers constant_registers PROVES hold
    distinct non-zero values when the extension starts — those examples
    test live data with no extra setup, and `reads` states the proven
    values (signed) so expected outputs can be derived from ground truth.
    Destination registers avoid every register the program writes. When
    the program proves no usable sources, two fresh registers are used and
    the addi example (when missing) doubles as their loader. Purely
    illustrative — the proposer may pick other registers/values — but each
    word is guaranteed to decode to its category and to pass the lazy
    gate."""
    if not missing:
        return []
    written: set[int] = set()
    for w in existing_words:
        d = decode_program_word(manifest, w)
        if d and d["category"] and d["fields"].get("opcode") in (
                _OPCODE_RTYPE, _OPCODE_ITYPE_ALU):
            written.add(d["fields"].get("rd") or 0)
    pool = [r for r in range(5, 32) if r not in written]
    if len(pool) < 2 + len(missing):
        pool = list(range(5, 32))              # crowded program: just rotate
    known = constant_registers(manifest, list(existing_words))
    cands = [(r, v) for r, v in known.items()
             if r != 0 and v != 0 and r in written]
    reads: dict[str, int] = {}
    if len(cands) >= 2:
        setup_a = cands[0][0]
        setup_b = next((r for r, v in cands[1:] if v != cands[0][1]),
                       cands[1][0])
        reads = {f"x{setup_a}": _signed32(known[setup_a]),
                 f"x{setup_b}": _signed32(known[setup_b])}
        free = pool
    else:
        setup_a, setup_b = pool[0], pool[1]
        free = pool[2:]

    out: list[dict] = []
    for i, name in enumerate(missing):
        rd = free[i % len(free)] if free else setup_a
        pd = (manifest or {}).get("program_decode") or {}
        cats = (manifest.get("categories") or {}).get(
            pd.get("categories_from") or "", [])
        when = next((_norm_when(c.get("when", {})) for c in cats
                     if c.get("name") == name), {})
        used_reads = reads
        if when.get("opcode") == _OPCODE_ITYPE_ALU:
            rs1, imm = (0, 7) if name == "addi" else (setup_a, 7)
            if name == "addi":
                used_reads = {}                # reads only x0
                if not reads:
                    rd = setup_a   # doubles as the loader the others read
            elif reads:
                used_reads = {f"x{setup_a}": _signed32(known[setup_a])}
            word = encode_category_word(manifest, name, rd=rd, rs1=rs1, imm=imm)
            asm = f"{name} x{rd}, x{rs1}, {imm}"
        else:
            word = encode_category_word(
                manifest, name, rd=rd, rs1=setup_a, rs2=setup_b)
            asm = f"{name} x{rd}, x{setup_a}, x{setup_b}"
        if word is None or lazy_word_reason(manifest, word) is not None:
            continue                           # cannot verify: stay silent
        entry = {"category": name, "word": f"{word:x}", "asm": asm}
        if used_reads:
            # values PROVEN by constant propagation over the official
            # program — ground truth for deriving expected outputs
            entry["reads"] = used_reads
        out.append(entry)
    return out


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
