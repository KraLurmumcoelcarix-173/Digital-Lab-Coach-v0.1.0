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
    best, best_n = None, 0
    for m in manifests:
        n = len(filenames & set(m.get("applies_to", [])))
        if n > best_n:
            best, best_n = m, n
    if best is not None:
        return best
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
_OPCODE_LOAD = 0b0000011
_OPCODE_STORE = 0b0100011
_OPCODE_BRANCH = 0b1100011
_OPCODE_JAL = 0b1101111
_OPCODE_JALR = 0b1100111
_OPCODE_LUI = 0b0110111
_OPCODE_AUIPC = 0b0010111

_CLASS_OF = {
    _OPCODE_RTYPE: "R", _OPCODE_ITYPE_ALU: "I", _OPCODE_LOAD: "LOAD",
    _OPCODE_STORE: "STORE", _OPCODE_BRANCH: "BR", _OPCODE_JAL: "JAL",
    _OPCODE_JALR: "JALR", _OPCODE_LUI: "LUI", _OPCODE_AUIPC: "AUIPC",
}
_WRITES_RD = {"R", "I", "LOAD", "JAL", "JALR", "LUI", "AUIPC"}
_READS_RS1 = {"R", "I", "LOAD", "STORE", "BR", "JALR"}
_READS_RS2 = {"R", "STORE", "BR"}


def _sext(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def word_immediate(word: int, cls: str) -> int:
    if cls in ("I", "LOAD", "JALR"):
        return _sext(word >> 20, 12)
    if cls == "STORE":
        return _sext(((word >> 25) << 5) | ((word >> 7) & 0x1F), 12)
    if cls == "BR":
        raw = (((word >> 31) & 1) << 12) | (((word >> 7) & 1) << 11) \
            | (((word >> 25) & 0x3F) << 5) | (((word >> 8) & 0xF) << 1)
        return _sext(raw, 13)
    if cls in ("LUI", "AUIPC"):
        return _sext(word & 0xFFFFF000, 32)
    if cls == "JAL":
        raw = (((word >> 31) & 1) << 20) | (((word >> 12) & 0xFF) << 12) \
            | (((word >> 20) & 1) << 11) | (((word >> 21) & 0x3FF) << 1)
        return _sext(raw, 21)
    return 0


def _place_immediate(cls: str, imm: int) -> int:
    if cls in ("I", "LOAD", "JALR"):
        return (imm & 0xFFF) << 20
    if cls == "STORE":
        return (((imm >> 5) & 0x7F) << 25) | ((imm & 0x1F) << 7)
    if cls == "BR":
        return ((((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25)
                | (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7))
    if cls in ("LUI", "AUIPC"):
        return imm & 0xFFFFF000
    if cls == "JAL":
        return ((((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21)
                | (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12))
    return 0


def lazy_word_reason(manifest: dict | None, word: int) -> str | None:
    d = decode_program_word(manifest, word)
    if not d or not d["category"]:
        return None
    f = d["fields"]
    op, rd, rs1 = f.get("opcode"), f.get("rd"), f.get("rs1")
    cls = _CLASS_OF.get(op)
    if cls == "R":
        if rs1 == 0 and f.get("rs2") == 0:
            return ("it reads x0 for BOTH operands — every lab instruction "
                    "computes 0 on (0, 0), so the row cannot tell one "
                    "operation from another")
        return None
    if cls == "I":
        if rs1 == 0 and ((word >> 20) & 0xFFF) == 0:
            return ("it reads x0 with immediate 0 — every lab instruction "
                    "computes 0 on (0, 0), so the row cannot tell one "
                    "operation from another")
        if rd == 0 and rs1 == 0:
            return ("it discards its result into x0 and reads only x0 — "
                    "nothing register-dependent is observable")
        return None
    if cls == "BR":
        if rs1 == f.get("rs2"):
            return ("it compares a register with itself — the outcome is "
                    "fixed whatever the register holds")
        return None
    if cls in ("LOAD", "LUI", "AUIPC", "JAL", "JALR") and rd == 0:
        if cls == "JAL" and word_immediate(word, cls) == 0:
            return "it jumps to itself — a halt loop, nothing executes after it"
        if cls in ("LOAD", "LUI", "AUIPC"):
            return ("it discards its result into x0 — nothing the "
                    "instruction produced is observable")
    return None


def program_class(manifest: dict | None, word: int) -> str | None:
    d = decode_program_word(manifest, word)
    if not d:
        return None
    return _CLASS_OF.get(d["fields"].get("opcode"))


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
    cls = _CLASS_OF.get(when.get("opcode"))
    if cls == "I" and "funct7" in when:
        word |= (imm & 0x1F) << 20
    elif cls in ("I", "LOAD", "STORE", "BR", "JAL", "JALR", "LUI", "AUIPC"):
        word |= _place_immediate(cls, imm)
        if cls in ("STORE", "BR"):
            word |= place("rs2", rs2) or 0
    else:
        word |= place("rs2", rs2) or 0
    for name, val in (("rd", rd), ("rs1", rs1)):
        if cls in ("STORE", "BR") and name == "rd":
            continue
        if cls in ("LUI", "AUIPC", "JAL") and name == "rs1":
            continue
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


def _alu_result(cat: str, a: int, b: int) -> int | None:
    sh = b & 31
    table = {
        "add": (a + b) & _M32, "addi": (a + b) & _M32,
        "sub": (a - b) & _M32,
        "and": a & b, "andi": a & b,
        "or": a | b, "ori": a | b,
        "xor": a ^ b, "xori": a ^ b,
        "sll": (a << sh) & _M32, "slli": (a << sh) & _M32,
        "srl": a >> sh, "srli": a >> sh,
        "sra": (_signed32(a) >> sh) & _M32, "srai": (_signed32(a) >> sh) & _M32,
        "slt": 1 if _signed32(a) < _signed32(b) else 0,
        "slti": 1 if _signed32(a) < _signed32(b) else 0,
        "sltu": 1 if a < b else 0, "sltiu": 1 if a < b else 0,
    }
    return table.get(cat)


def _branch_taken(cat: str, a: int, b: int) -> bool | None:
    return {
        "beq": a == b, "bne": a != b,
        "blt": _signed32(a) < _signed32(b), "bge": _signed32(a) >= _signed32(b),
        "bltu": a < b, "bgeu": a >= b,
    }.get(cat)


def _mem_load(cat: str, mem: dict, addr: int) -> int | None:
    word_idx, ofs = (addr >> 2) & 31, addr & 3
    word = mem.get(word_idx, 0)
    if word is None:
        return None
    if cat == "lw":
        return word
    sh = ofs * 8
    if cat in ("lb", "lbu"):
        raw = (word >> sh) & 0xFF
        return raw if cat == "lbu" else _sext(raw, 8) & _M32
    if cat in ("lh", "lhu"):
        raw = (word >> sh) & 0xFFFF
        return raw if cat == "lhu" else _sext(raw, 16) & _M32
    return None


def _mem_store(cat: str, mem: dict, addr: int, val: int | None) -> None:
    word_idx, ofs = (addr >> 2) & 31, addr & 3
    if val is None:
        mem[word_idx] = None
        return
    size = {"sb": 8, "sh": 16, "sw": 32}.get(cat)
    if size is None:
        mem[word_idx] = None
        return
    mask, sh = (1 << size) - 1, ofs * 8
    old = mem.get(word_idx, 0)
    if old is None:
        old = 0 if size == 32 and ofs == 0 else None
    if old is None:
        mem[word_idx] = None
        return
    mem[word_idx] = ((old & ~((mask << sh) & _M32)) | ((val & mask) << sh)) & _M32


def execute_program(manifest: dict | None, words: list[int], *,
                    max_steps: int = 4096, regs: dict | None = None,
                    mem: dict | None = None, pc: int = 0) -> dict:
    pd = (manifest or {}).get("program_decode") or {}
    if "rd" not in (pd.get("fields") or {}):
        return {"regs": {}, "mem": {}, "pc": pc, "trace": [],
                "halted": False, "stopped": "no rd field"}
    regs = dict(regs) if regs is not None else {r: 0 for r in range(32)}
    mem = dict(mem) if mem is not None else {}
    trace: list[dict] = []
    halted = False
    stopped: str | None = None
    for _ in range(max_steps):
        idx = pc >> 2
        if pc & 3 or idx < 0 or idx >= len(words):
            stopped = "pc left the program"
            break
        w = words[idx]
        d = decode_program_word(manifest, w)
        f = d["fields"] if d else {}
        cat = d["category"] if d else None
        cls = _CLASS_OF.get(f.get("opcode")) if cat else None
        rs1, rs2, rd = f.get("rs1"), f.get("rs2"), f.get("rd") or 0
        a = regs.get(rs1) if rs1 is not None else None
        b = regs.get(rs2) if rs2 is not None else None
        trace.append({"pc": pc, "word": w, "category": cat,
                      "rs1": rs1, "rs2": rs2, "a": a, "b": b})
        next_pc = (pc + 4) & _M32
        out: int | None = None
        writes = False
        if cls is None:
            writes = True
        elif cls in ("R", "I"):
            operand = b if cls == "R" else word_immediate(w, cls) & _M32
            if cls == "I" and "funct7" in _norm_when(
                    next((c.get("when", {}) for c in
                          (manifest.get("categories") or {}).get(
                              pd.get("categories_from") or "", [])
                          if c.get("name") == cat), {})):
                operand = (w >> 20) & 0x1F
            if a is not None and operand is not None:
                out = _alu_result(cat, a, operand)
            writes = True
        elif cls == "LOAD":
            if a is not None:
                out = _mem_load(cat, mem, (a + word_immediate(w, cls)) & _M32)
            writes = True
        elif cls == "STORE":
            if a is None:
                mem = {k: None for k in range(32)}
            else:
                _mem_store(cat, mem, (a + word_immediate(w, cls)) & _M32, b)
        elif cls == "BR":
            taken = _branch_taken(cat, a, b) if (a is not None and b is not None) else None
            if taken is None:
                stopped = "branch on an unknown value"
                break
            if taken:
                next_pc = (pc + word_immediate(w, cls)) & _M32
        elif cls == "JAL":
            out = (pc + 4) & _M32
            next_pc = (pc + word_immediate(w, cls)) & _M32
            writes = True
        elif cls == "JALR":
            if a is None:
                stopped = "jump on an unknown value"
                break
            out = (pc + 4) & _M32
            next_pc = (a + word_immediate(w, cls)) & ~1 & _M32
            writes = True
        elif cls == "LUI":
            out = word_immediate(w, cls) & _M32
            writes = True
        elif cls == "AUIPC":
            out = (pc + word_immediate(w, cls)) & _M32
            writes = True
        if writes and rd != 0:
            if out is None:
                regs.pop(rd, None)
            else:
                regs[rd] = out
        if next_pc == pc:
            halted = True
            break
        pc = next_pc
    else:
        stopped = "step cap"
    return {"regs": regs, "mem": mem, "pc": pc, "trace": trace,
            "halted": halted, "stopped": stopped}


def program_halt_index(manifest: dict | None, words: list[int]) -> int | None:
    run = execute_program(manifest, words)
    return (run["pc"] >> 2) if run["halted"] else None


def constant_registers(manifest: dict | None, words: list[int], *,
                       appended: int = 0) -> dict[int, int]:
    pd = (manifest or {}).get("program_decode") or {}
    if "rd" not in (pd.get("fields") or {}):
        return {}
    base = words[:len(words) - appended] if appended else list(words)
    extra = words[len(words) - appended:] if appended else []
    run = execute_program(manifest, spliced_program(manifest, base, extra))
    known = {r: v for r, v in run["regs"].items() if v is not None}
    known[0] = 0
    return known


def spliced_program(manifest: dict | None, base: list[int],
                    extra: list[int]) -> list[int]:
    if not extra:
        return list(base)
    h = program_halt_index(manifest, base)
    if h is None:
        return list(base) + list(extra)
    return list(base[:h]) + list(extra) + list(base[h:])


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

    pd = (manifest or {}).get("program_decode") or {}
    cats = (manifest.get("categories") or {}).get(
        pd.get("categories_from") or "", [])

    def when_of(name):
        return next((_norm_when(c.get("when", {})) for c in cats
                     if c.get("name") == name), {})

    order = {"STORE": 0, "LOAD": 1}
    missing = sorted(missing, key=lambda n: (
        order.get(_CLASS_OF.get(when_of(n).get("opcode")), 2)))

    out: list[dict] = []
    for i, name in enumerate(missing):
        rd = free[i % len(free)] if free else setup_a
        when = when_of(name)
        cls = _CLASS_OF.get(when.get("opcode"))
        used_reads = reads
        extra_words: list[int] = []
        if cls == "I":
            shift = "funct7" in when
            rs1, imm = (0, 7) if name == "addi" else (setup_a, 3 if shift else 7)
            if name == "addi":
                used_reads = {}
                if not reads:
                    rd = setup_a
            elif reads:
                used_reads = {f"x{setup_a}": _signed32(known[setup_a])}
            word = encode_category_word(manifest, name, rd=rd, rs1=rs1, imm=imm)
            asm = f"{name} x{rd}, x{rs1}, {imm}"
        elif cls == "R":
            word = encode_category_word(
                manifest, name, rd=rd, rs1=setup_a, rs2=setup_b)
            asm = f"{name} x{rd}, x{setup_a}, x{setup_b}"
        elif cls == "STORE":
            used_reads = ({f"x{setup_a}": _signed32(known[setup_a])}
                          if reads else {})
            word = encode_category_word(
                manifest, name, rs1=0, rs2=setup_a, imm=0)
            asm = f"{name} x{setup_a}, 0(x0)"
        elif cls == "LOAD":
            used_reads = {}
            word = encode_category_word(manifest, name, rd=rd, rs1=0, imm=0)
            asm = f"{name} x{rd}, 0(x0)"
        elif cls == "BR":
            word = encode_category_word(
                manifest, name, rs1=setup_a, rs2=setup_b, imm=4)
            asm = f"{name} x{setup_a}, x{setup_b}, +4"
        elif cls == "JAL":
            used_reads = {}
            word = encode_category_word(manifest, name, rd=rd, imm=4)
            asm = f"{name} x{rd}, +4"
        elif cls == "JALR":
            used_reads = {}
            base_rd = free[(i + 1) % len(free)] if len(free) > 1 else setup_b
            auipc_cat = next((c.get("name") for c in cats
                              if _norm_when(c.get("when", {})).get("opcode")
                              == _OPCODE_AUIPC), None)
            base_word = (encode_category_word(manifest, auipc_cat, rd=base_rd, imm=0)
                         if auipc_cat else None)
            word = encode_category_word(manifest, name, rd=rd, rs1=base_rd, imm=8)
            if base_word is None:
                word = None
            else:
                extra_words = [base_word]
            asm = f"auipc x{base_rd}, 0 ; {name} x{rd}, 8(x{base_rd})"
        elif cls == "LUI":
            used_reads = {}
            word = encode_category_word(manifest, name, rd=rd, imm=0x12345000)
            asm = f"{name} x{rd}, 0x12345"
        elif cls == "AUIPC":
            used_reads = {}
            word = encode_category_word(manifest, name, rd=rd, imm=0)
            asm = f"{name} x{rd}, 0"
        else:
            word = encode_category_word(
                manifest, name, rd=rd, rs1=setup_a, rs2=setup_b)
            asm = f"{name} x{rd}, x{setup_a}, x{setup_b}"
        if word is None or lazy_word_reason(manifest, word) is not None:
            continue
        entry = {"category": name, "word": f"{word:x}", "asm": asm}
        if extra_words:
            entry["words"] = [f"{w:x}" for w in extra_words] + [f"{word:x}"]
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

    pc_col = observe.get("pc_port")
    if pc_col not in headers:
        pc_col = None

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

    words = list(setups)
    for e in trimmed:
        words.extend(int(w, 16) for w in e.get("words") or [e["word"]])
    insert_at = program_halt_index(manifest, list(existing_words))
    start_pc = 4 * (insert_at if insert_at is not None else len(existing_words))
    run = execute_program(manifest,
                          spliced_program(manifest, list(existing_words), words))
    by_pc = {t["pc"]: t for t in run["trace"]}
    rows: list[str] = []
    for i in range(len(words)):
        t = by_pc.get(start_pc + 4 * i)
        if t is None:
            return None
        cells = []
        for h in headers:
            if h == clock_col:
                cells.append("C")
            elif h == pc_col:
                cells.append(f"0x{t['pc']:X}")
            elif h == rs1_col:
                cells.append(_fmt_signed(t["a"]) if t["a"] is not None else "X")
            elif rs2_col and h == rs2_col:
                cells.append(_fmt_signed(t["b"]) if t["b"] is not None else "X")
            else:
                cells.append("X")
        rows.append(" ".join(cells))
    closed = ", ".join(e["category"] for e in trimmed)
    dropped = len(examples) - len(trimmed)
    why = (f"Machine-built extension closing {closed} — every expected "
           f"value derived deterministically from the program by constant "
           f"propagation."
           + (" Inserted before the program's halt loop." if insert_at is not None else "")
           + (f" ({dropped} more missing categor"
              f"{'y' if dropped == 1 else 'ies'} left for the next run —"
              f" word budget.)" if dropped > 0 else ""))
    out = {"program_words": [f"{w:x}" for w in words],
           "rows": rows, "why": why}
    if insert_at is not None:
        out["insert_at"] = insert_at
        out["pc_shift"] = 4 * len(words)
    return out

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
