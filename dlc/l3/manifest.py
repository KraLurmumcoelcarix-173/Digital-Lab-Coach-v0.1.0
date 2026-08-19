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
                m["_file"] = p.name
                out.append(m)
        except Exception:
            continue
    return out


def find_manifest(filenames: set[str],
                  element_names: set[str] | None = None) -> dict | None:
    manifests = load_manifests()
    for m in manifests:
        if filenames & set(m.get("applies_to", [])):
            return m
    if element_names:
        for m in manifests:
            hooks = set(m.get("applies_to_elements") or [])
            if not (hooks & element_names):
                continue
            alias = dict(m)
            cats = m.get("categories") or {}
            canon = next(iter(cats.values()), None)
            if canon is not None:
                alias["categories"] = {
                    **cats, **{f: canon for f in filenames if f not in cats}}
            alias["_element_matched"] = sorted(hooks & element_names)
            return alias
    return None


def tree_element_names(circuit) -> set[str]:
    out: set[str] = set()
    stack = [circuit]
    seen: set[int] = set()
    while stack:
        c = stack.pop()
        if id(c) in seen:
            continue
        seen.add(id(c))
        out |= {comp.element_name for comp in c.components}
        for sub in getattr(c, "subcircuits", []):
            if getattr(sub, "child_circuit", None) is not None:
                stack.append(sub.child_circuit)
    return out


def reference_dir(manifest: dict | None) -> Path | None:
    env = os.environ.get("DLC_REFERENCE_DIR")
    if env:
        return Path(env)
    ref = (manifest or {}).get("reference_dir")
    return Path(ref) if ref else None


def normalized_test_hash(raw_data_string: str) -> str:
    lines = []
    for line in (raw_data_string or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(" ".join(line.split()))
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()


def official_status(manifest: dict | None, file: str,
                    raw_data_string: str) -> str | None:
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
            return None
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

def program_rom_words(circuit) -> tuple[list[int], int] | None:
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

_OPCODE_RTYPE = 0b0110011
_OPCODE_ITYPE_ALU = 0b0010011


def lazy_word_reason(manifest: dict | None, word: int) -> str | None:
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
        return None
    if op == _OPCODE_ITYPE_ALU:
        if rs1 == 0 and ((word >> 20) & 0xFFF) == 0:
            return ("it reads x0 with immediate 0 — every lab instruction "
                    "computes 0 on (0, 0), so the row cannot tell one "
                    "operation from another")
        if rd == 0 and rs1 == 0:
            return ("it discards its result into x0 and reads only x0 — "
                    "nothing register-dependent is observable")
        return None
    return None


def encode_category_word(
    manifest: dict | None, category: str, *,
    rd: int = 0, rs1: int = 0, rs2: int = 0, imm: int = 0,
) -> int | None:
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
        word |= (imm & 0xFFF) << 20
    else:
        word |= place("rs2", rs2) or 0
    for name, val in (("rd", rd), ("rs1", rs1)):
        bits = place(name, val)
        if bits is None:
            return None
        word |= bits
    d = decode_program_word(manifest, word)
    if not d or d["category"] != category:
        return None
    return word


_M32 = 0xFFFFFFFF


def _signed32(v: int) -> int:
    return v - (1 << 32) if v & (1 << 31) else v


def constant_registers(manifest: dict | None, words: list[int]) -> dict[int, int]:
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
            imm = imm - 0x1000 if imm & 0x800 else imm
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
            continue
        if out is None:
            known.pop(rd, None)
        else:
            known[rd] = out
    return known


def category_word_examples(
    manifest: dict | None, missing: list[str], existing_words: list[int] = (),
) -> list[dict]:
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
        pool = list(range(5, 32))
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
                used_reads = {}
                if not reads:
                    rd = setup_a
            elif reads:
                used_reads = {f"x{setup_a}": _signed32(known[setup_a])}
            word = encode_category_word(manifest, name, rd=rd, rs1=rs1, imm=imm)
            asm = f"{name} x{rd}, x{rs1}, {imm}"
        else:
            word = encode_category_word(
                manifest, name, rd=rd, rs1=setup_a, rs2=setup_b)
            asm = f"{name} x{rd}, x{setup_a}, x{setup_b}"
        if word is None or lazy_word_reason(manifest, word) is not None:
            continue
        entry = {"category": name, "word": f"{word:x}", "asm": asm}
        if used_reads:
            entry["reads"] = used_reads
        out.append(entry)
    return out


def _fmt_signed(v: int) -> str:
    s = _signed32(v)
    return str(s) if s >= 0 else f"({s})"


def synthesize_program_extension(
    manifest: dict | None,
    existing_words: list[int],
    missing: list[str],
    headers: list[str],
    clock_col: str | None,
) -> dict | None:
    pd = (manifest or {}).get("program_decode") or {}
    observe = pd.get("observe") or {}
    rs1_col = observe.get("rs1_port")
    if not missing or not clock_col or not rs1_col or rs1_col not in headers:
        return None
    rs2_col = observe.get("rs2_port")

    examples = category_word_examples(manifest, missing, existing_words)
    if not examples:
        return None
    setups: list[int] = []
    first = decode_program_word(manifest, int(examples[0]["word"], 16))
    known0 = constant_registers(manifest, list(existing_words))
    needs_setup = bool(examples) and not any(
        e.get("reads") for e in examples)
    if needs_setup and first:
        srcs = sorted({first["fields"].get("rs1"), first["fields"].get("rs2")}
                      - {None, 0})
        imms = [7, -3]
        for i, r in enumerate(srcs[:2]):
            if known0.get(r):
                continue
            w = encode_category_word(manifest, "addi", rd=r, rs1=0,
                                     imm=imms[i % 2])
            if w is None or w in existing_words:
                return None
            setups.append(w)
    budget = 12 - len(setups)
    max_gap = max(1, budget // 2)
    trimmed = examples[:max_gap]

    words = setups + [int(e["word"], 16) for e in trimmed]
    rows: list[str] = []
    state_words = list(existing_words)
    for w in words:
        st = constant_registers(manifest, state_words)
        f = (decode_program_word(manifest, w) or {}).get("fields", {})
        cells = []
        for h in headers:
            if h == clock_col:
                cells.append("C")
            elif h == rs1_col:
                v = st.get(f.get("rs1"))
                cells.append(_fmt_signed(v) if v is not None else "X")
            elif rs2_col and h == rs2_col:
                v = st.get(f.get("rs2"))
                cells.append(_fmt_signed(v) if v is not None else "X")
            else:
                cells.append("X")
        rows.append(" ".join(cells))
        state_words.append(w)
    closed = ", ".join(e["category"] for e in trimmed)
    dropped = len(examples) - len(trimmed)
    why = (f"Machine-built extension closing {closed} — every expected "
           f"value derived deterministically from the program by constant "
           f"propagation."
           + (f" ({dropped} more missing categor"
              f"{'y' if dropped == 1 else 'ies'} left for the next run —"
              f" word budget.)" if dropped > 0 else ""))
    return {"program_words": [f"{w:x}" for w in words],
            "rows": rows, "why": why}

def reference_row_verdicts(
    ref_file: Path, headers: list[str], rows: list[str],
) -> list[dict]:
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

        class _Row:
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
                continue 
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
