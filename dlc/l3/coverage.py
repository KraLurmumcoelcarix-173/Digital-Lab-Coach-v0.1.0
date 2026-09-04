from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dlc.parser.models import Circuit
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.sim import simulate, inputs_for_row
from dlc.sim.simulator import _row_has_clock_edge
from dlc.testing.spec import extract_test_specs, match_variables_to_io, _tokenize

@dataclass
class WrongTestFlag:
    file: str
    spec_name: str
    spec_index: int
    row_index: isinstance
    column: str
    asserted_raw: str
    asserted: int
    computed: int
    asserted_fmt: str
    computed_fmt: str
    classification: str | None = None

    def describe(self) -> str:
        return (
            f"{self.file} · '{self.spec_name}' row {self.row_index}: "
            f"the row expects {self.column}={self.asserted_fmt} but the "
            f"circuit as built computes {self.column}={self.computed_fmt}"
        )


@dataclass
class SpecScan:
    name: str
    spec_index: int
    row_count: int
    malformed_rows: int
    checked_cells: int
    unresolved_cells: int
    mismatched_cells: int
    error: str | None = None


@dataclass
class InputCoverage:
    label: str
    bits: int
    in_testcases: bool
    distinct_values: int
    missing_values: list[int] = field(default_factory=list)
    constant_value: int | None = None


@dataclass
class OutputCoverage:
    label: str
    bits: int
    asserted_cells: int
    distinct_values: int
    constant_value: int | None = None


@dataclass
class MuxBranchCoverage:
    component_index: int
    selector_bits: int
    arms_total: int
    arms_taken: list[int] = field(default_factory=list)
    arms_missing: list[int] = field(default_factory=list)
    arm_drivers: dict[int, str] = field(default_factory=dict)
    select_from_input: str | None = None


@dataclass
class CircuitCoverage:
    """Everything Mode B knows about one circuit in the tree."""
    file: str
    path: str | None
    has_testcases: bool = False
    specs: list[SpecScan] = field(default_factory=list)
    flags: list[WrongTestFlag] = field(default_factory=list)
    row_count: int = 0
    distinct_vectors: int = 0
    input_bits_total: int = 0
    input_space: int | None = None
    input_space_pct: float | None = None
    inputs: list[InputCoverage] = field(default_factory=list)
    outputs: list[OutputCoverage] = field(default_factory=list)
    mux_branches: list[MuxBranchCoverage] = field(default_factory=list)
    clock_edge_rows: int = 0
    notes: list[str] = field(default_factory=list)
    hidden_notes: list[str] = field(default_factory=list)
    categories_total: int = 0
    categories_touched: list[str] = field(default_factory=list)
    categories_missing: list[str] = field(default_factory=list)
    official_test: str | None = None


@dataclass
class TreeCoverageReport:
    root: str
    circuits: list[CircuitCoverage] = field(default_factory=list)
    total_flags: int = 0
    notes: list[str] = field(default_factory=list)
    element_names: list[str] = field(default_factory=list)
    select_gate: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

_SPACE_STATE_LIMIT = 20
_ENUM_BITS_LIMIT = 4


def _mask(bits: int | None) -> int:
    return (1 << bits) - 1 if bits else 0


def _bitpattern_eq(found: int, expected: int, width: int | None) -> bool:
    if width:
        m = _mask(width)
        return (found & m) == (expected & m)
    return found == expected


def _fmt_value(v: int | None, width: int | None, signed_hint: bool) -> str:
    if v is None:
        return ""
    if not width or width <= 1:
        return str(v)
    u = v & _mask(width)
    if signed_hint and (u >> (width - 1)) & 1:
        return str(u - (1 << width))
    return f"0x{u:X}"

_ARM_ATTR_HINTS = ("direction", "signed", "barrelSigned", "mode")


_PASSTHROUGH_ELEMS = {"Splitter"}


def _mux_arm_drivers(circuit: Circuit, netlist) -> dict[int, dict[int, str]]:
    pin_net: dict[tuple[int, str], int] = {}
    in_pins: dict[int, list[str]] = {}
    driver_of_net: dict[int, int] = {}
    for net in netlist.nets:
        for p in net.pins:
            pin_net[(p.component_index, p.pin_name)] = net.net_id
            if p.direction == "out":
                driver_of_net.setdefault(net.net_id, p.component_index)
            elif p.direction == "in":
                in_pins.setdefault(p.component_index, []).append(p.pin_name)

    def _name(ci: int) -> str:
        c = circuit.components[ci]
        label = c.attributes.get("Label")
        return c.element_name + (f" '{label}'" if label else "")

    def _source_of(ci: int, pin: str) -> str | None:
        nid = pin_net.get((ci, pin))
        src = driver_of_net.get(nid) if nid is not None else None
        if src is None or src == ci:
            return None
        via = []
        hops = 2
        while (circuit.components[src].element_name in _PASSTHROUGH_ELEMS
               and hops > 0):
            via.append(circuit.components[src].element_name)
            nxt = None
            for p2 in in_pins.get(src, []):
                n2 = pin_net.get((src, p2))
                d2 = driver_of_net.get(n2) if n2 is not None else None
                if d2 is not None and d2 != src:
                    nxt = d2
                    break
            if nxt is None:
                break
            src = nxt
            hops -= 1
        desc = _name(src)
        if via:
            desc += " via " + " via ".join(via)
        return desc

    out: dict[int, dict[int, str]] = {}
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Multiplexer":
            continue
        try:
            sel_bits = int(comp.attributes.get("Selector Bits", 1))
        except (TypeError, ValueError):
            sel_bits = 1
        arms: dict[int, str] = {}
        for k in range(1 << sel_bits):
            nid = pin_net.get((idx, f"in{k}"))
            ci = driver_of_net.get(nid) if nid is not None else None
            if ci is None or ci == idx:
                continue
            c = circuit.components[ci]
            desc = _name(ci)
            extra = [f"{a}={c.attributes[a]}" for a in _ARM_ATTR_HINTS
                     if a in c.attributes]
            if extra:
                desc += " (" + ", ".join(extra) + ")"
            srcs = [(p, _source_of(ci, p)) for p in in_pins.get(ci, [])[:4]]
            srcs = [(p, s) for p, s in srcs if s]
            if srcs:
                desc += " [" + ", ".join(f"{p}←{s}" for p, s in srcs) + "]"
            arms[k] = desc
        if arms:
            out[idx] = arms
    return out


def _mux_sel_nets(circuit: Circuit, netlist) -> list[tuple[int, int, int]]:
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


def _sel_input_labels(circuit: Circuit, netlist) -> dict[int, str]:
    pin_net: dict[tuple[int, str], int] = {}
    driver_of_net: dict[int, int] = {}
    in_pins: dict[int, list[str]] = {}
    for net in netlist.nets:
        for p in net.pins:
            pin_net[(p.component_index, p.pin_name)] = net.net_id
            if p.direction == "out":
                driver_of_net.setdefault(net.net_id, p.component_index)
            elif p.direction == "in":
                in_pins.setdefault(p.component_index, []).append(p.pin_name)

    out: dict[int, str] = {}
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Multiplexer":
            continue
        nid = pin_net.get((idx, "sel"))
        src = driver_of_net.get(nid) if nid is not None else None
        hops = 2
        while (src is not None
               and circuit.components[src].element_name in _PASSTHROUGH_ELEMS
               and hops > 0):
            nxt = None
            for p2 in in_pins.get(src, []):
                n2 = pin_net.get((src, p2))
                d2 = driver_of_net.get(n2) if n2 is not None else None
                if d2 is not None and d2 != src:
                    nxt = d2
                    break
            src = nxt
            hops -= 1
        if src is None:
            continue
        c = circuit.components[src]
        if c.element_name == "In" and c.attributes.get("Label"):
            out[idx] = c.attributes["Label"]
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
    arm_drivers = _mux_arm_drivers(circuit, netlist)
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
                    res = simulate(circuit, netlist, graph, inp,
                                   state_store=dict(reg_state))
                vectors.add(tuple(sorted(inp.items())))
                for lbl, val in inp.items():
                    if lbl in in_values:
                        in_values[lbl].add(val)
                for idx, sel_bits, nid in muxes:
                    v = res.net_values.get(nid)
                    if v is not None:
                        arms_taken[idx].add(v & _mask(sel_bits))
                for col, header in enumerate(spec.headers):
                    b = bindings.get(header)
                    if b is None or b.role != "output":
                        continue
                    if col >= len(row.values):
                        continue
                    tok = row.values[col]
                    if tok.kind != "int" or tok.value is None:
                        continue
                    if header in out_asserted:
                        out_asserted[header].append(
                            tok.value & _mask(b.bit_width)
                            if b.bit_width else tok.value
                        )
                    found = res.output_values.get(header)
                    if found is None:
                        scan.unresolved_cells += 1
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
        except Exception as exc:
            scan.error = f"{type(exc).__name__}: {exc}"
            cov.notes.append(
                f"testcase '{spec.name}': evaluator error "
                f"({scan.error}) — this spec was skipped, nothing accused."
            )
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

    sel_inputs = _sel_input_labels(circuit, netlist)
    for idx, sel_bits, _nid in muxes:
        total = 1 << sel_bits
        taken = sorted(arms_taken[idx])
        cov.mux_branches.append(MuxBranchCoverage(
            component_index=idx,
            selector_bits=sel_bits,
            arms_total=total,
            arms_taken=taken,
            arms_missing=sorted(set(range(total)) - set(taken)),
            arm_drivers=arm_drivers.get(idx, {}),
            select_from_input=sel_inputs.get(idx),
        ))

    _build_notes(cov)
    return cov


def _build_notes(cov: CircuitCoverage) -> None:
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
            def _arm_name(a: int) -> str:
                d = mb.arm_drivers.get(a)
                return f"{a} (← {d})" if d else str(a)
            arms = ", ".join(_arm_name(a) for a in mb.arms_missing)
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

def _collect_tree(circuit: Circuit, root_path: str) -> list[tuple[str, str, Circuit]]:
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

def replay_appended_rows(
    path: str,
    spec_name: str,
    appended: list[str],
    rom_words: list[str] | None = None,
    insert_at: int | None = None,
    insert_before_row: int | None = None,
) -> list[dict]:
    tmp = None
    try:
        if rom_words:
            from dlc.l3.oracle import extend_program_rom_text, parse_program_words
            src = Path(path).read_text(encoding="utf-8")
            src = extend_program_rom_text(src, parse_program_words(rom_words),
                                          insert_at=insert_at)
            fd, tmp = tempfile.mkstemp(suffix=".dig", prefix="dlc_row_l3_",
                                       dir=str(Path(path).parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(src)
            circuit = parse_dig_file(tmp)
        else:
            circuit = parse_dig_file(str(path))
        netlist = build_netlist(circuit)
        graph = build_signal_graph(circuit, netlist)
        spec = next(s for s in extract_test_specs(circuit)
                    if s.name == spec_name)
        bindings = match_variables_to_io(spec.headers, circuit)

        reg_state: dict = {}

        def run_row(rowobj):
            nonlocal reg_state
            inp = inputs_for_row(circuit, spec.headers, rowobj)
            clocked = _row_has_clock_edge(circuit, spec.headers, rowobj)
            res = simulate(circuit, netlist, graph, inp,
                           state_store=dict(reg_state))
            if clocked:
                ns = dict(reg_state)
                ns.update(res.reg_next)
                reg_state = ns
                res = simulate(circuit, netlist, graph, inp,
                               state_store=dict(reg_state))
            return res

        data_rows = [r for r in spec.rows if not r.is_malformed]
        if insert_at is not None and insert_before_row is not None:
            data_rows = data_rows[:insert_before_row]
        for row in data_rows:
            if any(t.kind == "loop_expr" for t in row.values):
                continue
            run_row(row)

        verdicts: list[dict] = []
        for raw in appended:
            cells = raw.split("#", 1)[0].split()

            class _Row:
                pass
            shim = _Row()
            shim.values = [_tokenize(c) for c in cells]
            res = run_row(shim)
            by_col = dict(zip(spec.headers, cells))
            bad: list[str] = []
            unresolved = False
            for col, b in bindings.items():
                if b is None or b.role != "output" or col not in by_col:
                    continue
                tok = _tokenize(by_col[col])
                if tok.kind != "int" or tok.value is None:
                    continue
                found = res.output_values.get(col)
                if found is None:
                    unresolved = True
                    continue
                if not _bitpattern_eq(found, tok.value, b.bit_width):
                    signed = tok.value < 0
                    bad.append(
                        f"{col}: your circuit computes "
                        f"{_fmt_value(found, b.bit_width, signed)} at that "
                        f"point, the row says "
                        f"{_fmt_value(tok.value, b.bit_width, signed)}")
            verdicts.append({
                "row": raw,
                "verdict": ("disagrees" if bad
                            else ("unresolved" if unresolved else "agrees")),
                "detail": "; ".join(bad),
            })
        return verdicts
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def scan_tree_coverage(dig_path: str) -> TreeCoverageReport:
    report = TreeCoverageReport(root=os.path.basename(dig_path))
    try:
        circuit = parse_dig_file(str(dig_path))
    except Exception as exc:
        report.notes.append(f"could not parse {report.root}: {exc}")
        return report

    unresolved: set[str] = set()
    kinds: set[str] = set()
    for display, path, circ in _collect_tree(circuit, str(dig_path)):
        report.circuits.append(
            scan_circuit_coverage(circ, display=display, path=path)
        )
        kinds |= {comp.element_name for comp in circ.components}
        for sub in circ.subcircuits:
            if sub.child_circuit is None:
                unresolved.add(sub.reference)
    report.element_names = sorted(kinds)
    for ref in sorted(unresolved):
        report.notes.append(
            f"subcircuit '{ref}' could not be resolved — not scanned."
        )

    report.total_flags = sum(len(c.flags) for c in report.circuits)
    _apply_manifest(report)
    _apply_select_gate(report)
    return report


def _apply_select_gate(report: TreeCoverageReport) -> None:
    for cov in report.circuits:
        if not cov.has_testcases or cov.categories_total:
            continue
        for mb in cov.mux_branches:
            if (mb.select_from_input and mb.arms_taken and mb.arms_missing
                    and len(mb.arms_missing) * 32 > mb.arms_total):
                report.select_gate.append({
                    "file": cov.file,
                    "component_index": mb.component_index,
                    "input": mb.select_from_input,
                    "selector_bits": mb.selector_bits,
                    "missing": list(mb.arms_missing),
                    "taken": list(mb.arms_taken),
                    "arm_drivers": {str(a): mb.arm_drivers[a]
                                    for a in mb.arms_missing
                                    if a in mb.arm_drivers},
                })
                cov.notes = [n for n in cov.notes
                             if not n.startswith(
                                 f"Multiplexer[{mb.component_index}]:")]


def _apply_manifest(report: TreeCoverageReport) -> None:
    from dlc.l3 import manifest as mf
    m = mf.find_manifest({c.file for c in report.circuits},
                         element_names=set(report.element_names))
    if m:
        src = f" ({m['_file']})" if m.get("_file") else ""
        how = ""
        if m.get("_element_matched"):
            how = (" — matched by circuit element "
                   f"{', '.join(m['_element_matched'])}, so it applies "
                   f"whatever the file is named")
        report.notes.append(
            f"lab manifest '{m.get('lab', '?')}'{src} applied{how}.")
    for cov in report.circuits:
        if not cov.has_testcases or not cov.path:
            continue
        try:
            circuit = parse_dig_file(cov.path)
            spec = extract_test_specs(circuit)[0]
        except Exception:
            continue
        cov.official_test = mf.official_status(
            m, cov.file, spec.raw_data_string,
        )
        if not m:
            if cov.official_test == "official" and cov.flags:
                for f in cov.flags:
                    f.classification = "official"
                cov.notes.insert(0, (
                    "this testcase matches the configured official test — "
                    "the rows are right; the disagreements below mean the "
                    "CIRCUIT is wrong."
                ))
            continue
        rom = mf.program_rom_words(circuit)
        if rom is not None:
            pc = mf.program_categories(m, rom[0])
            if pc and pc["missing"]:
                cov.notes.insert(0, (
                    f"the instruction ROM's program executes "
                    f"{len(pc['present'])}/{len(pc['present']) + len(pc['missing'])} "
                    f"lab instruction categories ({', '.join(pc['present'])}) "
                    f"— these NEVER execute: {', '.join(pc['missing'])} "
                    f"(decoded from the program words). Instructions outside "
                    f"these categories are not in this lab's decode set — no "
                    f"program can exercise them on this circuit."
                ))
            elif pc:
                cov.notes.insert(0, (
                    f"the instruction ROM's program executes all "
                    f"{len(pc['present'])} lab instruction categories — "
                    f"program-level category coverage is GREEN."
                ))
        if cov.official_test == "official" and cov.flags:
            for f in cov.flags:
                f.classification = "official"
            cov.notes.insert(0, (
                "this testcase matches the official lab fingerprint — the "
                "rows are right; the disagreements below mean the CIRCUIT "
                "is wrong."
            ))
        cc = mf.category_coverage(m, cov.file, spec)
        if cc is None:
            continue
        cov.categories_total = cc["total"]
        cov.categories_touched = cc["touched"]
        cov.categories_missing = cc["missing"]
        if cc["missing"]:
            cov.notes.insert(0, (
                f"category gap: {', '.join(cc['missing'])} never exercised "
                f"by any row ({len(cc['touched'])}/{cc['total']} categories "
                f"touched)."
            ))
        else:
            cov.notes.insert(0, (
                f"all {cc['total']} instruction categories touched — "
                f"category coverage is GREEN (raw vector % is informational "
                f"only)."
            ))
        cov.notes, cov.hidden_notes = _manifest_note_filter(
            cov.notes, (m.get("categories") or {}).get(cov.file) or [],
            categories_complete=not cc["missing"],
        )


_NEVER_TESTED_RE = re.compile(
    r"input '([^']+)'.*never tested with values? ([\d, ]+)\.?$")
_CONST_OUTPUT_RE = re.compile(r"output '([^']+)' is only ever expected")


def _manifest_note_filter(
    notes: list[str], cats: list[dict], *, categories_complete: bool,
) -> tuple[list[str], list[str]]:
    defined: dict[str, set[int]] = {}
    for cat in cats:
        for col, v in (cat.get("when") or {}).items():
            from dlc.l3.manifest import _cell_value
            val = _cell_value(str(v)) if not isinstance(v, int) else v
            if val is not None:
                defined.setdefault(col, set()).add(val)
    if not defined:
        return notes, []
    out: list[str] = []
    hidden: list[str] = []
    for note in notes:
        m2 = _NEVER_TESTED_RE.search(note)
        if m2 and m2.group(1) in defined:
            try:
                listed = [int(x) for x in m2.group(2).replace(" ", "").split(",") if x]
            except ValueError:
                listed = []
            if listed and all(v not in defined[m2.group(1)] for v in listed):
                hidden.append(
                    f"input '{m2.group(1)}': every lab-defined value is "
                    f"tested; the untested values "
                    f"({', '.join(str(v) for v in listed)}) are not part of "
                    f"this lab's instruction set — no test needed.")
                continue
        if categories_complete and _CONST_OUTPUT_RE.search(note):
            out.append(note + " (consistent with this lab's instruction "
                              "set — every defined category is tested)")
            continue
        out.append(note)
    return out, hidden