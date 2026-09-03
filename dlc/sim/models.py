"""
Formula models for known lab subcircuits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import hashlib

M32 = 0xFFFFFFFF


def _mask(bits: int) -> int:
    return (1 << bits) - 1 if bits > 0 else 0


def _s32(v: int) -> int:
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def _sext(v: int, bits: int) -> int:
    v &= _mask(bits)
    if v & (1 << (bits - 1)):
        v -= 1 << bits
    return v & M32


@dataclass(frozen=True)
class FormulaModel:
    name: str
    role: str
    inputs: tuple[tuple[str, int], ...]
    outputs: tuple[tuple[str, int], ...]
    fn: Callable
    stateful: bool = False
    # Control units may expose only a subset of the table's signals.
    output_subset_ok: bool = False
    # Inputs the OUTPUTS depend on. A register file must answer its read
    # ports before its write data (which loops back through the ALU) has
    # settled, exactly as the gate-level child does; the next state is only
    # committed once every input is known.
    output_inputs: tuple[str, ...] | None = None

    def input_labels(self) -> set[str]:
        return {n for n, _ in self.inputs}

    def evaluate(self, in_vals: dict, state):
        """Return (outputs, next_state) or None while inputs are incomplete."""
        all_needed = [n for n, _ in self.inputs if n != "Clock"]
        for_outputs = self.output_inputs or all_needed
        if any(n not in in_vals for n in for_outputs):
            return None
        complete = all(n in in_vals for n in all_needed)
        filled = {n: in_vals.get(n, 0) for n, _ in self.inputs}
        outs, nxt = self.fn(filled, state)
        if self.stateful and not complete:
            nxt = state if isinstance(state, dict) else {}
        widths = dict(self.outputs)
        outs = {k: (v & _mask(widths[k])) for k, v in outs.items() if k in widths}
        return outs, nxt


# formulas

def _alu(i, _s):
    a, b, op = i["A"] & M32, i["B"] & M32, i["ALUOp"] & 0xF
    sh = b & 31
    if op == 0:
        r = a & b
    elif op == 1:
        r = a | b
    elif op == 3:
        r = a ^ b
    elif op == 4:
        r = (a << sh) & M32
    elif op == 5:
        r = a >> sh
    elif op == 6:
        r = (a - b) & M32
    elif op == 7:
        r = 1 if _s32(a) < _s32(b) else 0
    elif op == 8:
        r = (_s32(a) >> sh) & M32
    elif op == 9:
        r = 1 if a < b else 0
    else:
        r = (a + b) & M32
    return {"Result": r, "FlagZ": 1 if r == 0 else 0}, None


_OPC = {"R": 0b0110011, "I": 0b0010011, "LOAD": 0b0000011,
        "STORE": 0b0100011, "BR": 0b1100011, "JAL": 0b1101111,
        "JALR": 0b1100111, "LUI": 0b0110111, "AUIPC": 0b0010111}
_ALU_CODE = {"AND": 0, "OR": 1, "ADD": 2, "XOR": 3, "SLL": 4, "SRL": 5,
             "SUB": 6, "SLT": 7, "SRA": 8, "SLTU": 9}
_IMM_CODE = {"I": 0, "S": 1, "B": 2, "U": 3, "J": 4}


def _ctrl_word(rw=0, srcB=0, imm="I", alu="AND", srcA=0, mw=0, res=0,
               br=0, jp=0, jr=0) -> int:
    im = _IMM_CODE[imm]
    return (_ALU_CODE[alu] | ((im & 3) << 4) | (srcB << 6) | (rw << 7)
            | ((im >> 2) << 8) | ((srcA & 3) << 9) | (mw << 11)
            | ((res & 3) << 12) | (br << 14) | (jp << 15) | (jr << 16))


# (opcode class, funct3 or None, funct7 or None) -> control word
_RV32I_TABLE = [
    (("R", 0, 0), _ctrl_word(rw=1, alu="ADD")),
    (("R", 0, 32), _ctrl_word(rw=1, alu="SUB")),
    (("R", 7, 0), _ctrl_word(rw=1, alu="AND")),
    (("R", 6, 0), _ctrl_word(rw=1, alu="OR")),
    (("R", 2, 0), _ctrl_word(rw=1, alu="SLT")),
    (("I", 0, None), _ctrl_word(rw=1, srcB=1, alu="ADD")),
    (("I", 2, None), _ctrl_word(rw=1, srcB=1, alu="SLT")),
    (("I", 7, None), _ctrl_word(rw=1, srcB=1, alu="AND")),
    (("R", 4, 0), _ctrl_word(rw=1, alu="XOR")),
    (("R", 1, 0), _ctrl_word(rw=1, alu="SLL")),
    (("R", 5, 0), _ctrl_word(rw=1, alu="SRL")),
    (("R", 5, 32), _ctrl_word(rw=1, alu="SRA")),
    (("R", 3, 0), _ctrl_word(rw=1, alu="SLTU")),
    (("I", 4, None), _ctrl_word(rw=1, srcB=1, alu="XOR")),
    (("I", 6, None), _ctrl_word(rw=1, srcB=1, alu="OR")),
    (("I", 1, 0), _ctrl_word(rw=1, srcB=1, alu="SLL")),
    (("I", 5, 0), _ctrl_word(rw=1, srcB=1, alu="SRL")),
    (("I", 5, 32), _ctrl_word(rw=1, srcB=1, alu="SRA")),
    (("I", 3, None), _ctrl_word(rw=1, srcB=1, alu="SLTU")),
    (("LUI", None, None), _ctrl_word(rw=1, srcB=1, imm="U", alu="ADD", srcA=2)),
    (("AUIPC", None, None), _ctrl_word(rw=1, srcB=1, imm="U", alu="ADD", srcA=1)),
    (("JAL", None, None), _ctrl_word(rw=1, imm="J", res=2, jp=1)),
    (("JALR", None, None), _ctrl_word(rw=1, srcB=1, imm="I", alu="ADD", res=2, jr=1)),
    (("BR", 0, None), _ctrl_word(imm="B", br=1)),
    (("BR", 1, None), _ctrl_word(imm="B", br=1)),
    (("BR", 4, None), _ctrl_word(imm="B", br=1)),
    (("BR", 5, None), _ctrl_word(imm="B", br=1)),
    (("BR", 6, None), _ctrl_word(imm="B", br=1)),
    (("BR", 7, None), _ctrl_word(imm="B", br=1)),
    (("LOAD", 0, None), _ctrl_word(rw=1, srcB=1, alu="ADD", res=1)),
    (("LOAD", 1, None), _ctrl_word(rw=1, srcB=1, alu="ADD", res=1)),
    (("LOAD", 2, None), _ctrl_word(rw=1, srcB=1, alu="ADD", res=1)),
    (("LOAD", 4, None), _ctrl_word(rw=1, srcB=1, alu="ADD", res=1)),
    (("LOAD", 5, None), _ctrl_word(rw=1, srcB=1, alu="ADD", res=1)),
    (("STORE", 0, None), _ctrl_word(srcB=1, imm="S", alu="ADD", mw=1)),
    (("STORE", 1, None), _ctrl_word(srcB=1, imm="S", alu="ADD", mw=1)),
    (("STORE", 2, None), _ctrl_word(srcB=1, imm="S", alu="ADD", mw=1)),
]

_CTRL_BITS = {
    "ALUOp0": 0, "ALUOp1": 1, "ALUOp2": 2, "ALUOp3": 3, "ImmSrc0": 4,
    "ImmSrc1": 5, "ALUSrc": 6, "RegWrite": 7, "ImmSrc2": 8, "ALUSrcA0": 9,
    "ALUSrcA1": 10, "MemWrite": 11, "ResultSrc0": 12, "ResultSrc1": 13,
    "Branch": 14, "Jump": 15, "Jalr": 16,
}
_CTRL_OUTPUTS = tuple((n, 1) for n in _CTRL_BITS)
_LAB5_OUTPUTS = tuple((n, 1) for n in (
    "RegWrite", "ALUSrc", "ALUOp3", "ImmSrc1", "ImmSrc0",
    "ALUOp2", "ALUOp1", "ALUOp0"))


def _decode(opcode: int, f3: int, f7: int, table, default: int) -> int:
    for (cls, want3, want7), word in table:
        if _OPC[cls] != opcode:
            continue
        if want3 is not None and want3 != f3:
            continue
        if want7 is not None and want7 != f7:
            continue
        return word
    return default


def _ctrl_outputs(word: int) -> dict:
    return {name: (word >> bit) & 1 for name, bit in _CTRL_BITS.items()}


def _control_rv32i(i, _s):
    w = _decode(i["opcode"] & 0x7F, i["funct3"] & 7, i["funct7"] & 0x7F,
                _RV32I_TABLE, 0)
    return _ctrl_outputs(w), None
_LAB5_TABLE = _RV32I_TABLE[:8]


def _control_lab5(i, _s):
    w = _decode(i["opcode"] & 0x7F, i["funct3"] & 7, i["funct7"] & 0x7F,
                _LAB5_TABLE, _LAB5_TABLE[0][1])
    return _ctrl_outputs(w), None


def _register_file(i, state):
    regs = state if isinstance(state, dict) else {}
    r1, r2 = i["ReadReg1"] & 31, i["ReadReg2"] & 31
    outs = {"ReadData1": regs.get(r1, 0), "ReadData2": regs.get(r2, 0)}
    nxt = dict(regs)
    if i.get("RegWrite", 0) & 1:
        wr = i["WriteReg"] & 31
        if wr != 0:
            nxt[wr] = i["WriteData"] & M32
    return outs, nxt


def _add_sub(i, _s):
    a, b, sub = i["A"] & M32, i["B"] & M32, i["Sub"] & 1
    sa, sb = _s32(a), _s32(b)
    full = sa - sb if sub else sa + sb
    out = full & M32
    overflow = 1 if full != _s32(out) else 0
    return {"Out": out, "Overflow": overflow, "Sign": (out >> 31) & 1}, None


def _boolean_unit(i, _s):
    a, b, sel = i["A"] & M32, i["B"] & M32, i["Bool"] & 3
    out = (a & b, a | b, a ^ b, (~(a | b)) & M32)[sel]
    return {"Out": out}, None


# Shifts B by A[5:0] (the lab's "64-bit style" amount); Bool 1 is an
# unused arm tied to zero in the lab circuit.
def _bidirectional_shifter(i, _s):
    amt, b, sel = i["A"] & 63, i["B"] & M32, i["Bool"] & 3
    if sel == 0:
        out = (b << amt) & M32 if amt < 32 else 0
    elif sel == 2:
        out = b >> amt if amt < 32 else 0
    elif sel == 3:
        out = (_s32(b) >> min(amt, 31)) & M32
    else:
        out = 0
    return {"Out": out}, None


def _slt_unit(i, _s):
    return {"Result": (i["Sign"] ^ i["Overflow"]) & 1}, None


def _immgen(i, _s):
    ins, src = i["Instr"] & M32, i["ImmSrc"] & 7
    if src == 1:
        raw = ((ins >> 25) << 5) | ((ins >> 7) & 0x1F)
        imm = _sext(raw, 12)
    elif src == 2:
        raw = (((ins >> 31) & 1) << 12) | (((ins >> 7) & 1) << 11) \
            | (((ins >> 25) & 0x3F) << 5) | (((ins >> 8) & 0xF) << 1)
        imm = _sext(raw, 13)
    elif src == 3:
        imm = ins & 0xFFFFF000
    elif src == 4:
        raw = (((ins >> 31) & 1) << 20) | (((ins >> 12) & 0xFF) << 12) \
            | (((ins >> 20) & 1) << 11) | (((ins >> 21) & 0x3FF) << 1)
        imm = _sext(raw, 21)
    else:
        imm = _sext(ins >> 20, 12)
    return {"Imm": imm}, None


def _branch_unit(i, _s):
    a, b, f3 = i["A"] & M32, i["B"] & M32, i["funct3"] & 7
    cond = {
        0: a == b, 1: a != b, 2: False, 3: False,
        4: _s32(a) < _s32(b), 5: _s32(a) >= _s32(b),
        6: a < b, 7: a >= b,
    }[f3]
    taken = ((i["Branch"] & 1) and cond) or (i["Jump"] & 1)
    return {"Taken": 1 if taken else 0}, None

def _data_memory(i, state):
    mem = state if isinstance(state, dict) else {}
    addr = i["Addr"] & M32
    word_idx, ofs = (addr >> 2) & 31, addr & 3
    f3 = i["funct3"] & 7
    size_bits = (8, 16, 32, 32)[f3 & 3]
    mask = _mask(size_bits)
    sh = ofs * 8
    word = mem.get(word_idx, 0)
    if size_bits == 32:
        read = word
    else:
        raw = (word >> sh) & mask
        read = raw if (f3 & 4) else _sext(raw, size_bits)
    nxt = dict(mem)
    if i.get("MemWrite", 0) & 1:
        wd = i["WriteData"] & M32
        merged = (word & ~((mask << sh) & M32)) | ((wd & mask) << sh)
        nxt[word_idx] = merged & M32
    return {"ReadData": read & M32}, nxt


_IO32 = (("A", 32), ("B", 32))

MODELS: tuple[FormulaModel, ...] = (
    FormulaModel(
        "rv32i_alu",
        "ALU: applies the operation selected by ALUOp to A and B and "
        "raises FlagZ when the result is zero.",
        _IO32 + (("ALUOp", 4),), (("Result", 32), ("FlagZ", 1)), _alu),
    FormulaModel(
        "lab5_control",
        "Control unit: decodes opcode/funct3/funct7 into the datapath's "
        "control signals for the eight Lab 5 instructions.",
        (("opcode", 7), ("funct3", 3), ("funct7", 7)), _LAB5_OUTPUTS,
        _control_lab5),
    FormulaModel(
        "rv32i_control",
        "Control unit: decodes opcode/funct3/funct7 into the datapath's "
        "control signals for the 37 RV32I instructions.",
        (("opcode", 7), ("funct3", 3), ("funct7", 7)), _CTRL_OUTPUTS,
        _control_rv32i, output_subset_ok=True),
    FormulaModel(
        "rv32i_register_file",
        "Register file: 32 registers read on two ports; writes WriteData "
        "into WriteReg on the clock edge when RegWrite is 1 (x0 stays 0).",
        (("ReadReg1", 5), ("ReadReg2", 5), ("WriteReg", 5),
         ("WriteData", 32), ("RegWrite", 1), ("Clock", 1)),
        (("ReadData1", 32), ("ReadData2", 32)), _register_file,
        stateful=True, output_inputs=("ReadReg1", "ReadReg2")),
    FormulaModel(
        "add_sub",
        "Adder/subtractor: A plus or minus B with signed-overflow and "
        "sign flags.",
        _IO32 + (("Sub", 1),), (("Out", 32), ("Overflow", 1), ("Sign", 1)),
        _add_sub),
    FormulaModel(
        "boolean_unit",
        "Boolean unit: bitwise AND, OR, XOR or NOR of A and B, chosen "
        "by Bool.",
        _IO32 + (("Bool", 2),), (("Out", 32),), _boolean_unit),
    FormulaModel(
        "bidirectional_shifter",
        "Shifter: shifts B left, right logical or right arithmetic by A, "
        "chosen by Bool.",
        _IO32 + (("Bool", 2),), (("Out", 32),), _bidirectional_shifter),
    FormulaModel(
        "slt_unit",
        "Set-less-than unit: derives A<B from the subtractor's sign and "
        "overflow flags.",
        (("Sign", 1), ("Overflow", 1)), (("Result", 1),), _slt_unit),
    FormulaModel(
        "rv32i_immgen",
        "Immediate generator: rebuilds the I, S, B, U or J immediate "
        "from the instruction word, chosen by ImmSrc.",
        (("Instr", 32), ("ImmSrc", 3)), (("Imm", 32),), _immgen),
    FormulaModel(
        "rv32i_branch_unit",
        "Branch unit: evaluates the funct3 condition on A and B; Taken "
        "is the enabled branch outcome or an unconditional jump.",
        _IO32 + (("funct3", 3), ("Branch", 1), ("Jump", 1)),
        (("Taken", 1),), _branch_unit),
    FormulaModel(
        "rv32i_data_memory",
        "Data memory: 32 words of byte-addressed storage with byte, "
        "half and word loads/stores; writes land on the clock edge when "
        "MemWrite is 1.",
        (("Addr", 32), ("WriteData", 32), ("MemWrite", 1), ("funct3", 3),
         ("Clock", 1)), (("ReadData", 32),), _data_memory, stateful=True,
        output_inputs=("Addr", "funct3")),
)

BY_NAME = {m.name: m for m in MODELS}


# matching

def child_signature(child) -> tuple[frozenset, frozenset]:
    ins = frozenset(
        (c.label, c.bit_width() if c.element_name != "Clock" else 1)
        for c in child.components
        if (c.is_input() or c.element_name == "Clock") and c.label)
    outs = frozenset((c.label, c.bit_width()) for c in child.outputs() if c.label)
    return ins, outs


def candidates(child) -> list[FormulaModel]:
    """Models whose interface fits `child`, exact output match first."""
    ins, outs = child_signature(child)
    exact, subset = [], []
    for m in MODELS:
        if frozenset(m.inputs) != ins:
            continue
        m_outs = frozenset(m.outputs)
        if m_outs == outs:
            exact.append(m)
        elif m.output_subset_ok and outs and outs <= m_outs:
            subset.append(m)
    return exact + subset


# validation

_VALIDATION_CACHE: dict[tuple, tuple[bool, str]] = {}


def _child_key(child) -> str:
    path = getattr(child, "source_path", None)
    if path:
        try:
            with open(path, "rb") as f:
                return hashlib.sha1(f.read()).hexdigest()
        except OSError:
            pass
    return f"id:{id(child)}"


def validate(model: FormulaModel, child) -> tuple[bool, str]:
    """
    Replay the child's own testcase through `model`.
    """
    key = (_child_key(child), model.name)
    hit = _VALIDATION_CACHE.get(key)
    if hit is not None:
        return hit

    from dlc.testing.spec import extract_test_specs, match_variables_to_io
    from dlc.sim.simulator import inputs_for_row, _row_has_clock_edge

    specs = [s for s in extract_test_specs(child) if s.rows]
    if not specs:
        result = (False, "no testcase")
        _VALIDATION_CACHE[key] = result
        return result

    widths = dict(model.outputs)
    checked = 0
    for spec in specs:
        bindings = match_variables_to_io(spec.headers, child)
        state = None
        for row in spec.rows:
            if row.is_malformed:
                continue
            inp = inputs_for_row(child, spec.headers, row)
            ev = model.evaluate(inp, state)
            if ev is None:
                result = (False, f"row {row.line_index}: inputs incomplete")
                _VALIDATION_CACHE[key] = result
                return result
            outs, nxt = ev
            if _row_has_clock_edge(child, spec.headers, row) and model.stateful:
                state = nxt
                outs, _ = model.evaluate(inp, state)
            for col, header in enumerate(spec.headers):
                b = bindings.get(header)
                if not (b and b.role == "output" and col < len(row.values)):
                    continue
                tok = row.values[col]
                if tok.kind != "int" or tok.value is None:
                    continue
                if header not in outs:
                    result = (False, f"output {header!r} not modeled")
                    _VALIDATION_CACHE[key] = result
                    return result
                w = widths.get(header) or b.bit_width or 1
                if (outs[header] & _mask(w)) != (tok.value & _mask(w)):
                    result = (False,
                              f"row {row.line_index}: {header} expected "
                              f"0x{tok.value & _mask(w):X}, model gave "
                              f"0x{outs[header] & _mask(w):X}")
                    _VALIDATION_CACHE[key] = result
                    return result
                checked += 1
    result = (True, f"validated on {checked} test cells")
    _VALIDATION_CACHE[key] = result
    return result


# resolver

def _walk_children(circuit, seen: dict) -> None:
    for ref in circuit.subcircuits:
        child = ref.child_circuit
        if child is None or id(child) in seen:
            continue
        seen[id(child)] = (ref.reference, child)
        _walk_children(child, seen)


def resolver_for(circuit, manifest: dict | None = None,
                 notes: list[str] | None = None) -> Callable:
    configured = (manifest or {}).get("subcircuits") or {}
    seen: dict = {}
    _walk_children(circuit, seen)
    decided: dict[int, FormulaModel] = {}

    for cid, (reference, child) in seen.items():
        cfg = configured.get(reference) or {}
        wanted = cfg.get("model")
        if wanted == "simulate":
            if notes is not None:
                notes.append(f"{reference}: simulated as drawn (manifest).")
            continue
        if wanted:
            model = BY_NAME.get(wanted)
            if model is None or model not in candidates(child):
                if notes is not None:
                    notes.append(
                        f"{reference}: manifest model {wanted!r} does not "
                        f"fit this file's interface; simulated as drawn.")
                continue
            ok, detail = validate(model, child)
            if ok or detail == "no testcase":
                decided[cid] = model
                if notes is not None:
                    notes.append(f"{reference}: formula model {model.name} "
                                 f"({detail}; manifest).")
            elif notes is not None:
                notes.append(f"{reference}: manifest model {model.name} "
                             f"disagrees with the file's own test "
                             f"({detail}); simulated as drawn.")
            continue
        for model in candidates(child):
            ok, detail = validate(model, child)
            if ok:
                decided[cid] = model
                if notes is not None:
                    notes.append(
                        f"{reference}: formula model {model.name} ({detail}).")
                break

    def resolve(child):
        return decided.get(id(child))

    resolve.decided = {seen[c][0]: m.name for c, m in decided.items()}
    return resolve


def role_for(child, manifest: dict | None = None,
             reference: str | None = None) -> str | None:
    """Instructor-facing one-line role of a subcircuit, if known."""
    cfg = ((manifest or {}).get("subcircuits") or {}).get(reference or "") or {}
    if cfg.get("role"):
        return cfg["role"]
    named = cfg.get("model")
    if named and named in BY_NAME:
        return BY_NAME[named].role
    for model in candidates(child):
        return model.role
    return None
