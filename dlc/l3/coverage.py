"""Mode B deterministic engines: wrong-test scan + coverage report.

TREE-WIDE by ratified scope: the selected file AND every resolved subcircuit
file are each scanned as their own top-level circuit against their own
embedded testcases. Everything here is pure evaluator work — Digital.jar is
never invoked, no LLM is involved.

wrong-test scan
    Replays every testcase row through the dlc/sim value evaluator and flags
    output cells whose ASSERTED value disagrees with what the circuit-as-built
    COMPUTES. A flag means "the test and the circuit disagree here" — either
    the row's expectation is a typo or the circuit is buggy at that point; the
    UI wording presents it as exactly that question.
    HONESTY GUARD (ratified decision c): a cell is accused ONLY when the
    evaluator fully resolved that output for that row; unresolved cells are
    counted in `unresolved_cells` and never flagged.

coverage report
    Deterministic metrics per circuit, merged across its specs:
      - per-input value coverage (distinct values; missing values for ports
        up to 4 bits; inputs no testcase column ever drives),
      - distinct input vectors vs the full input space (exact when the space
        is small enough to state),
      - per-output assertion coverage (outputs never checked; outputs only
        ever expected to be one constant),
      - Multiplexer branch-arm coverage, observed from the evaluator's
        resolved select values row by row,
      - clock-edge row count.
    The per-circuit `notes` list is the human-readable "good report"; the
    same structure grounds the row-proposal prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict

from dlc.parser.models import Circuit
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.sim import simulate, inputs_for_row
from dlc.sim.simulator import _row_has_clock_edge
from dlc.testing.spec import extract_test_specs, match_variables_to_io


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------

@dataclass
class WrongTestFlag:
    """One output cell where the asserted value and the circuit disagree."""
    file: str
    spec_name: str
    spec_index: int
    row_index: int              # TestRow.line_index — same ids the UI rows use
    column: str                 # output header accused
    asserted_raw: str           # the token as written in the testcase
    asserted: int
    computed: int
    asserted_fmt: str
    computed_fmt: str

    def describe(self) -> str:
        return (
            f"{self.file} · '{self.spec_name}' row {self.row_index}: "
            f"the row expects {self.column}={self.asserted_fmt} but the "
            f"circuit as built computes {self.column}={self.computed_fmt}"
        )


@dataclass
class SpecScan:
    """2.1 accounting for one testcase."""
    name: str
    spec_index: int
    row_count: int
    malformed_rows: int
    checked_cells: int          # honesty guard passed, value compared
    unresolved_cells: int       # guard failed — counted, never accused
    mismatched_cells: int
    error: str | None = None    # evaluator blew up -> spec skipped, never accused


@dataclass
class InputCoverage:
    label: str
    bits: int
    in_testcases: bool          # some spec has a column driving this input
    distinct_values: int
    missing_values: list[int] = field(default_factory=list)  # ports <= 4 bits
    constant_value: int | None = None   # same value in every row (rows > 1)


@dataclass
class OutputCoverage:
    label: str
    bits: int
    asserted_cells: int         # rows that check this output with a value
    distinct_values: int
    constant_value: int | None = None   # only ever expected to be this


@dataclass
class MuxBranchCoverage:
    component_index: int
    selector_bits: int
    arms_total: int
    arms_taken: list[int] = field(default_factory=list)
    arms_missing: list[int] = field(default_factory=list)


@dataclass
class CircuitCoverage:
    """Everything Mode B knows about one circuit in the tree."""
    file: str                   # display name (basename)
    path: str | None
    has_testcases: bool = False
    specs: list[SpecScan] = field(default_factory=list)
    flags: list[WrongTestFlag] = field(default_factory=list)
    row_count: int = 0
    distinct_vectors: int = 0
    input_bits_total: int = 0
    input_space: int | None = None       # 2**bits when small enough to state
    input_space_pct: float | None = None
    inputs: list[InputCoverage] = field(default_factory=list)
    outputs: list[OutputCoverage] = field(default_factory=list)
    mux_branches: list[MuxBranchCoverage] = field(default_factory=list)
    clock_edge_rows: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class TreeCoverageReport:
    root: str
    circuits: list[CircuitCoverage] = field(default_factory=list)
    total_flags: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SPACE_STATE_LIMIT = 20     # state the exact input space only up to 2^20
_ENUM_BITS_LIMIT = 4        # enumerate missing values only for <=4-bit ports


def _mask(bits: int | None) -> int:
    return (1 << bits) - 1 if bits else 0


def _bitpattern_eq(found: int, expected: int, width: int | None) -> bool:
    """Digital's masked compare: a signed expected like -60 matches the
    evaluator's unsigned two's-complement value at the port's width."""
    if width:
        m = _mask(width)
        return (found & m) == (expected & m)
    return found == expected


def _fmt_value(v: int | None, width: int | None, signed_hint: bool) -> str:
    """Render like the Layer-1 UI: signed decimal when the testcase wrote a
    negative value, hex for buses, plain decimal for 1-bit."""
    if v is None:
        return ""
    if not width or width <= 1:
        return str(v)
    u = v & _mask(width)
    if signed_hint and (u >> (width - 1)) & 1:
        return str(u - (1 << width))
    return f"0x{u:X}"


def _mux_sel_nets(circuit: Circuit, netlist) -> list[tuple[int, int, int]]:
    """(component_index, selector_bits, sel_net_id) for every Multiplexer
    whose 'sel' pin landed on a net."""
    pin_net: dict[tuple[int, str], int] = {}
    for net in netlist.nets:
        for p in net.pins:
            pin_net[(p.component_index, p.pin_name)] = net.net_id
    out: list[tuple[int, int, int]] = []
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Multiplexer":
            continue
        try:
            sel_bits = int(comp.attributes.get("Selector Bits", 1))
        except (TypeError, ValueError):
            sel_bits = 1
        nid = pin_net.get((idx, "sel"))
        if nid is not None:
            out.append((idx, sel_bits, nid))
    return out


# ---------------------------------------------------------------------------
# Per-circuit scan (2.1 + 2.2 in one replay pass)
# ---------------------------------------------------------------------------

def scan_circuit_coverage(
    circuit: Circuit,
    *,
    display: str = "circuit",
    path: str | None = None,
) -> CircuitCoverage:
    """Scan ONE circuit (as its own top level) against its own testcases."""
    cov = CircuitCoverage(file=display, path=path)
    netlist = build_netlist(circuit)
    graph = build_signal_graph(circuit, netlist)
    specs = extract_test_specs(circuit)
    cov.has_testcases = bool(specs)

    ins = circuit.inputs()
    outs = circuit.outputs()
    in_bits = {c.label: c.bit_width() for c in ins if c.label}
    out_bits = {c.label: c.bit_width() for c in outs if c.label}
    cov.input_bits_total = sum(in_bits.values())

    muxes = _mux_sel_nets(circuit, netlist)
    arms_taken: dict[int, set[int]] = {idx: set() for idx, _b, _n in muxes}

    in_values: dict[str, set[int]] = {lbl: set() for lbl in in_bits}
    in_driven: dict[str, bool] = {lbl: False for lbl in in_bits}
    out_asserted: dict[str, list[int]] = {lbl: [] for lbl in out_bits}
    vectors: set[tuple] = set()

    if not specs:
        cov.notes.append(
            "This circuit has no embedded testcase — nothing here is "
            "tested directly."
        )

    for spec_index, spec in enumerate(specs):
        scan = SpecScan(
            name=spec.name, spec_index=spec_index,
            row_count=0, malformed_rows=0,
            checked_cells=0, unresolved_cells=0, mismatched_cells=0,
        )
        cov.specs.append(scan)
        if spec.has_unexpanded_loops:
            cov.notes.append(
                f"testcase '{spec.name}': some loop constructs could not be "
                f"expanded; only the expanded rows were scanned."
            )
        bindings = match_variables_to_io(spec.headers, circuit)
        for header, b in bindings.items():
            if b.role == "input" and header in in_driven:
                in_driven[header] = True

        reg_state: dict[tuple[int, ...], int] = {}
        try:
            for row in spec.rows:
                if row.is_malformed:
                    scan.malformed_rows += 1
                    continue
                scan.row_count += 1
                cov.row_count += 1

                inp = inputs_for_row(circuit, spec.headers, row)
                clocked = _row_has_clock_edge(circuit, spec.headers, row)
                res = simulate(circuit, netlist, graph, inp,
                               state_store=dict(reg_state))
                if clocked:
                    cov.clock_edge_rows += 1
                    new_state = dict(reg_state)
                    new_state.update(res.reg_next)
                    reg_state = new_state
                    # re-settle so post-edge values are what the row asserts
                    res = simulate(circuit, netlist, graph, inp,
                                   state_store=dict(reg_state))

                # ---- 2.2 collectors --------------------------------------
                vectors.add(tuple(sorted(inp.items())))
                for lbl, val in inp.items():
                    if lbl in in_values:
                        in_values[lbl].add(val)
                for idx, sel_bits, nid in muxes:
                    v = res.net_values.get(nid)
                    if v is not None:
                        arms_taken[idx].add(v & _mask(sel_bits))

                # ---- 2.1 asserted-vs-computed ----------------------------
                for col, header in enumerate(spec.headers):
                    b = bindings.get(header)
                    if b is None or b.role != "output":
                        continue
                    if col >= len(row.values):
                        continue
                    tok = row.values[col]
                    if tok.kind != "int" or tok.value is None:
                        continue        # don't-care / high-Z: nothing asserted
                    if header in out_asserted:
                        out_asserted[header].append(
                            tok.value & _mask(b.bit_width)
                            if b.bit_width else tok.value
                        )
                    found = res.output_values.get(header)
                    if found is None:
                        scan.unresolved_cells += 1   # HONESTY GUARD: no accusation
                        continue
                    scan.checked_cells += 1
                    if not _bitpattern_eq(found, tok.value, b.bit_width):
                        scan.mismatched_cells += 1
                        signed = tok.value < 0
                        cov.flags.append(WrongTestFlag(
                            file=display,
                            spec_name=spec.name,
                            spec_index=spec_index,
                            row_index=row.line_index,
                            column=header,
                            asserted_raw=tok.raw,
                            asserted=tok.value,
                            computed=found,
                            asserted_fmt=_fmt_value(tok.value, b.bit_width, signed),
                            computed_fmt=_fmt_value(found, b.bit_width, signed),
                        ))
        except Exception as exc:                      # never crash the scan
            scan.error = f"{type(exc).__name__}: {exc}"
            cov.notes.append(
                f"testcase '{spec.name}': evaluator error "
                f"({scan.error}) — this spec was skipped, nothing accused."
            )

    # ---- 2.2 rollups ------------------------------------------------------
    cov.distinct_vectors = len(vectors)
    if 0 < cov.input_bits_total <= _SPACE_STATE_LIMIT:
        cov.input_space = 1 << cov.input_bits_total
        if cov.row_count:
            cov.input_space_pct = round(
                100.0 * cov.distinct_vectors / cov.input_space, 2
            )

    for lbl, bits in in_bits.items():
        seen = in_values[lbl]
        ic = InputCoverage(
            label=lbl, bits=bits,
            in_testcases=in_driven[lbl],
            distinct_values=len(seen),
        )
        if bits <= _ENUM_BITS_LIMIT and in_driven[lbl]:
            ic.missing_values = sorted(set(range(1 << bits)) - seen)
        if cov.row_count > 1 and len(seen) == 1:
            ic.constant_value = next(iter(seen))
        cov.inputs.append(ic)

    for lbl, bits in out_bits.items():
        vals = out_asserted[lbl]
        oc = OutputCoverage(
            label=lbl, bits=bits,
            asserted_cells=len(vals),
            distinct_values=len(set(vals)),
        )
        if len(vals) > 1 and len(set(vals)) == 1:
            oc.constant_value = vals[0]
        cov.outputs.append(oc)

    for idx, sel_bits, _nid in muxes:
        total = 1 << sel_bits
        taken = sorted(arms_taken[idx])
        cov.mux_branches.append(MuxBranchCoverage(
            component_index=idx,
            selector_bits=sel_bits,
            arms_total=total,
            arms_taken=taken,
            arms_missing=sorted(set(range(total)) - set(taken)),
        ))

    _build_notes(cov)
    return cov


def _build_notes(cov: CircuitCoverage) -> None:
    """Human-readable gap sentences — the per-circuit 'good report'."""
    if not cov.has_testcases:
        return
    for ic in cov.inputs:
        if not ic.in_testcases:
            cov.notes.append(
                f"input '{ic.label}' never appears in any testcase column."
            )
        elif ic.missing_values:
            vals = ", ".join(str(v) for v in ic.missing_values)
            cov.notes.append(
                f"input '{ic.label}' ({ic.bits}-bit) is never tested with "
                f"value{'s' if len(ic.missing_values) != 1 else ''} {vals}."
            )
        elif ic.constant_value is not None:
            cov.notes.append(
                f"input '{ic.label}' is {ic.constant_value} in every row."
            )
    for oc in cov.outputs:
        if oc.asserted_cells == 0:
            cov.notes.append(
                f"output '{oc.label}' is never checked by any row."
            )
        elif oc.constant_value is not None:
            cov.notes.append(
                f"output '{oc.label}' is only ever expected to be "
                f"{oc.constant_value} — no row checks it at another value."
            )
    for mb in cov.mux_branches:
        if mb.arms_missing and mb.arms_taken:
            arms = ", ".join(str(a) for a in mb.arms_missing)
            cov.notes.append(
                f"Multiplexer[{mb.component_index}]: input arm"
                f"{'s' if len(mb.arms_missing) != 1 else ''} {arms} never "
                f"selected by any test row."
            )
    if cov.input_space is not None and cov.row_count:
        cov.notes.append(
            f"tests cover {cov.distinct_vectors} of {cov.input_space} "
            f"possible input vectors ({cov.input_space_pct}%)."
        )
    elif cov.input_bits_total > _SPACE_STATE_LIMIT and cov.row_count:
        cov.notes.append(
            f"input space is 2^{cov.input_bits_total} vectors — exhaustive "
            f"coverage is impossible; {cov.distinct_vectors} distinct "
            f"vectors tested."
        )


# ---------------------------------------------------------------------------
# Tree walk (root + every resolved subcircuit file, deduped, BFS order)
# ---------------------------------------------------------------------------

def _collect_tree(circuit: Circuit, root_path: str) -> list[tuple[str, str, Circuit]]:
    """[(display, path, circuit)] for the root and every resolved child,
    first-seen (breadth-first) order, deduplicated by resolved path."""
    seen: set[str] = set()
    out: list[tuple[str, str, Circuit]] = []
    queue: list[tuple[str, str, Circuit]] = [
        (os.path.basename(root_path), os.path.abspath(root_path), circuit)
    ]
    while queue:
        display, path, circ = queue.pop(0)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        out.append((display, path, circ))
        for sub in circ.subcircuits:
            if sub.child_circuit is None or not sub.resolved_path:
                continue
            queue.append((
                os.path.basename(sub.resolved_path),
                os.path.abspath(sub.resolved_path),
                sub.child_circuit,
            ))
    return out


def scan_tree_coverage(dig_path: str) -> TreeCoverageReport:
    """Mode B's deterministic pass over the whole tree rooted at `dig_path`."""
    report = TreeCoverageReport(root=os.path.basename(dig_path))
    try:
        circuit = parse_dig_file(str(dig_path))
    except Exception as exc:
        report.notes.append(f"could not parse {report.root}: {exc}")
        return report

    unresolved: set[str] = set()
    for display, path, circ in _collect_tree(circuit, str(dig_path)):
        report.circuits.append(
            scan_circuit_coverage(circ, display=display, path=path)
        )
        for sub in circ.subcircuits:
            if sub.child_circuit is None:
                unresolved.add(sub.reference)
    for ref in sorted(unresolved):
        report.notes.append(
            f"subcircuit '{ref}' could not be resolved — not scanned."
        )

    report.total_flags = sum(len(c.flags) for c in report.circuits)
    return report