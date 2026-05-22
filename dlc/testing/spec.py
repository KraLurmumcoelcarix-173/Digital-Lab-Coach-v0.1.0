"""
F4 : TestSpec extractor.

Given a parsed Circuit, walk its Testcase elements, parse the
`<dataString>` text into a structured `TestSpec` per testcase, and
match each header column to a top-level circuit port.
"""

from dataclasses import dataclass, field

from dlc.parser.models import Circuit
import re

@dataclass(frozen=True)
class Token:

    raw: str
    kind: str            # "int" | "clock" | "highZ" | "dontcare" | "loop_expr" | "unknown"
    value: int | None    


@dataclass
class TestRow:
    __test__ = False

    raw: str
    values: list[Token] = field(default_factory=list)
    line_index: int = 0
    is_malformed: bool = False


@dataclass(frozen=True)
class VariableBinding:
    name: str
    role: str                       # "input" | "output" | "clock" | "unbound"
    component_index: int | None
    bit_width: int | None


@dataclass
class TestSpec:
    """One Testcase from a circuit, parsed into rows."""
    __test__ = False

    name: str                       
    component_index: int          
    headers: list[str]
    rows: list[TestRow]
    raw_data_string: str
    has_unexpanded_loops: bool

    def row_count(self) -> int:
        return len(self.rows)

    def well_formed_row_count(self) -> int:
        return sum(1 for r in self.rows if not r.is_malformed)

# Tokenization

def _tokenize(raw: str) -> Token:
    """Parse a single whitespace-stripped cell into a Token."""
    s = raw.strip()
    if not s:
        return Token(raw=s, kind="unknown", value=None)

    if s == "C":
        return Token(raw=s, kind="clock", value=None)
    if s in ("z", "Z"):
        return Token(raw=s, kind="highZ", value=None)
    if s in ("x", "X"):
        return Token(raw=s, kind="dontcare", value=None)

    # Parenthesized: 
    if s.startswith("(") and s.endswith(")") and len(s) >= 3:
        inner = s[1:-1].strip()
        try:
            return Token(raw=s, kind="int", value=int(inner))
        except ValueError:
            return Token(raw=s, kind="loop_expr", value=None)

    # Hex
    if len(s) > 2 and s[0] == "0" and s[1] in ("x", "X"):
        try:
            return Token(raw=s, kind="int", value=int(s, 16))
        except ValueError:
            return Token(raw=s, kind="unknown", value=None)

    # Binary
    if len(s) > 2 and s[0] == "0" and s[1] in ("b", "B"):
        try:
            return Token(raw=s, kind="int", value=int(s[2:], 2))
        except ValueError:
            return Token(raw=s, kind="unknown", value=None)

    # Plain decimal 
    try:
        return Token(raw=s, kind="int", value=int(s))
    except ValueError:
        return Token(raw=s, kind="unknown", value=None)


# Line-level parsing

def _strip_inline_comment(line: str) -> str:
    """Drop everything from the first `#` to the end of the line."""
    idx = line.find("#")
    if idx < 0:
        return line
    return line[:idx]

_LOOP_OPEN_RE = re.compile(r'^loop\(\s*(\w+)\s*,\s*(\d+)\s*\)$')


def _expand_loop_line(line: str, var: str, n: int) -> str:
    pattern = re.compile(rf'\(\s*{re.escape(var)}\s*([+\-])?\s*(\d+)?\s*\)')
    def repl(m):
        op, num = m.group(1), m.group(2)
        if op is None and num is None:
            val = n
        elif op == '+':
            val = n + int(num)
        elif op == '-':
            val = n - int(num)
        else:
            return m.group(0)
        return str(val)
    return pattern.sub(repl, line)

def _is_loop_marker(line: str) -> bool:
    s = line.strip()
    if s.startswith("loop(") and s.endswith(")"):
        return True
    if s == "end loop":
        return True
    return False


def parse_data_string(text: str) -> tuple[list[str], list[TestRow], bool]:
    
    headers: list[str] = []
    rows: list[TestRow] = []
    has_unexpanded = False
    next_row_index = 0

    in_loop = False
    loop_var: str | None = None
    loop_count = 0
    loop_body: list[str] = []

    def emit(stripped: str) -> None:
        nonlocal next_row_index, has_unexpanded
        tokens_raw = stripped.split()
        if len(tokens_raw) != len(headers):
            rows.append(TestRow(
                raw=stripped, values=[], line_index=next_row_index,
                is_malformed=True,
            ))
        else:
            row_tokens = [_tokenize(t) for t in tokens_raw]
            rows.append(TestRow(
                raw=stripped, values=row_tokens, line_index=next_row_index,
                is_malformed=False,
            ))
            if any(t.kind == "loop_expr" for t in row_tokens):
                has_unexpanded = True
        next_row_index += 1

    for raw_line in text.splitlines():
        stripped = _strip_inline_comment(raw_line).strip()
        if not stripped:
            continue

        loop_match = _LOOP_OPEN_RE.match(stripped)
        if loop_match:
            in_loop = True
            loop_var = loop_match.group(1)
            loop_count = int(loop_match.group(2))
            loop_body = []
            continue
        if stripped == "end loop":
            if loop_var is not None and loop_count > 0:
                for n in range(loop_count):
                    for body_line in loop_body:
                        emit(_expand_loop_line(body_line, loop_var, n))
            in_loop = False
            loop_var = None
            loop_count = 0
            loop_body = []
            continue
        if in_loop:
            loop_body.append(stripped)
            continue

        if not headers:
            headers = stripped.split()
            continue
        emit(stripped)

    if in_loop:
        has_unexpanded = True

    return headers, rows, has_unexpanded


# Public API

def extract_test_specs(circuit: Circuit) -> list[TestSpec]:
    """Build a TestSpec for every Testcase element in `circuit`.
    """
    specs: list[TestSpec] = []
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Testcase":
            continue
        raw = comp.attributes.get("Testdata", "")
        if not isinstance(raw, str):
            raw = ""
        headers, rows, has_loops = parse_data_string(raw)
        name = comp.label or f"Testcase_{idx}"
        specs.append(TestSpec(
            name=name,
            component_index=idx,
            headers=headers,
            rows=rows,
            raw_data_string=raw,
            has_unexpanded_loops=has_loops,
        ))
    return specs


def match_variables_to_io(
    headers: list[str], circuit: Circuit
) -> dict[str, VariableBinding]:
    """Resolve each header column name against the circuit's top-level
    In, Out, and Clock components by Label."""
    by_in: dict[str, tuple[int, object]] = {}
    by_out: dict[str, tuple[int, object]] = {}
    by_clock: dict[str, tuple[int, object]] = {}
    for i, comp in enumerate(circuit.components):
        if comp.label is None:
            continue
        if comp.is_input():
            by_in[comp.label] = (i, comp)
        elif comp.is_output():
            by_out[comp.label] = (i, comp)
        elif comp.element_name == "Clock":
            by_clock[comp.label] = (i, comp)

    out: dict[str, VariableBinding] = {}
    for var in headers:
        if var in by_in:
            i, comp = by_in[var]
            out[var] = VariableBinding(var, "input", i, comp.bit_width())
        elif var in by_out:
            i, comp = by_out[var]
            out[var] = VariableBinding(var, "output", i, comp.bit_width())
        elif var in by_clock:
            i, comp = by_clock[var]
            out[var] = VariableBinding(var, "clock", i, 1)
        else:
            out[var] = VariableBinding(var, "unbound", None, None)
    return out
