"""L3 circuit-patch vocabulary + applier (fix representation).

A Mode-A "fix" must be MACHINE-APPLICABLE, or the self-verify oracle has
nothing to run. This module freezes a small op vocabulary (JSON dicts —
exactly what the debug agent emits) and applies it to a TEMP copy of the
circuit; the student's original file is never modified.

The vocabulary, mapped to the L3 bug classes:

  {"op": "change_attribute", "component_index": i,
   "name": "Value", "value": 0}
      Set/replace one elementAttributes entry. Covers wrong constants
      (op-encodings), wrong Bits / Selector Bits / AddrBits, wrong
      splitter ranges, wrong shifter direction/mode, wrong Tunnel
      NetName, ROM Data, rotation.

  {"op": "replace_element", "component_index": i, "new_element": "Or"}
      Swap a component's kind, keeping position + attributes (And→Or,
      XOr→XNOr, ...).

  {"op": "swap_pins", "component_index": i, "pin_a": "in0", "pin_b": "in1"}
      Exchange the wires feeding two pins of one component — the classic
      wrong-input-position bug.

  {"op": "rewire_pin", "component_index": i, "pin": "in3",
   "to": {"component_index": j, "pin": "Result"}}
      Detach `pin`'s wire and run a new wire from that pin to `to`'s net
      — the semantic miswire. (The new segment may be diagonal; nets are
      endpoint unions, and the temp file exists to be RERUN, not shown.)

  {"op": "add_wire", "p1": [x, y], "p2": [x, y]}
  {"op": "delete_wire", "p1": [x, y], "p2": [x, y]}
      Raw escape hatches when the pin-level ops don't fit (e.g. pins
      meeting at shared junctions, which swap/rewire refuse to touch).

Safety:
  * swap_pins / rewire_pin refuse pins sitting on a junction
    (>1 wire endpoint at that coordinate) — silently splitting a shared
    net would corrupt semantics; the agent must use add/delete_wire
    explicitly there.
  * rewire_pin CONNECTS at an existing junction of the target net when
    one exists, never at a claimed pin coordinate — raising a claimed
    endpoint's degree above 1 breaks the netlist's pin heuristics
    (worst on subcircuit implicit pins, which require degree-1 endpoints).
  * After applying, the temp circuit is REPARSED and deep-L1-checked;
    a patch that introduces NEW structural errors (multi-driver, width
    conflict, ...) is rejected — a "fix" must keep the circuit clean.
  * Attribute writes preserve the existing XML value tag (Digital types
    its attributes: Value is <long>, Bits is <int>, ...); new entries
    use a known-type table.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from dlc.analyzer import check_all_l1_deep
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.testing.runner import find_digital_jar, per_row_run_auto
from dlc.testing.spec import extract_test_specs

_TEMP_PREFIX = "dlc_row_l3fix_"   # rides the dlc_row_*.dig gitignore glob

KNOWN_OPS = frozenset({
    "change_attribute", "replace_element", "swap_pins", "rewire_pin",
    "add_wire", "delete_wire",
})

# XML value tag for NEW attribute entries (existing entries keep their tag).
# Digital types its keys — writing the wrong tag breaks its deserializer.
_NEW_ENTRY_TAG = {
    "Value": "long",
    "Bits": "int", "Inputs": "int", "Selector Bits": "int",
    "AddrBits": "int", "splitterSpreading": "int",
    "inputBits": "int", "outputBits": "int",
    "Label": "string", "NetName": "string",
    "Input Splitting": "string", "Output Splitting": "string",
    "Data": "data", "intFormat": "intFormat",
    "direction": "direction", "barrelShifterMode": "barrelShifterMode",
    "wideShape": "boolean", "isProgramCounter": "boolean",
    "isProgramMemory": "boolean", "bigEndian": "boolean",
    "rotation": "rotation",
}


@dataclass
class PatchReport:
    ok: bool
    warning: str | None = None
    applied: list[str] = field(default_factory=list)   # one summary per op
    l1_errors_before: int | None = None
    l1_errors_after: int | None = None
    new_l1_error_kinds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class PatchOutcome:
    """apply + rerun result — the self-verify oracle's answer for one fix."""
    ok: bool
    warning: str | None = None
    report: PatchReport | None = None
    temp_path: str | None = None          # populated only when keep_temp=True
    specs: list[dict] = field(default_factory=list)
    # [{name, headers, rows:[{index, raw, status, mismatches, error_message}],
    #   all_passed}]
    all_passed: bool | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _visual_elements(root) -> list:
    block = root.find("visualElements")
    return [] if block is None else block.findall("visualElement")


def _ve_for_index(root, component_index: int):
    ves = _visual_elements(root)
    if component_index < 0 or component_index >= len(ves):
        raise ValueError(
            f"component_index {component_index} out of range "
            f"(0..{len(ves) - 1})."
        )
    return ves[component_index]


def _format_value(tag: str, value) -> tuple[str, str | None, dict]:
    """(tag, text, xml_attribs) for a new/replacement value element."""
    if tag == "boolean":
        return tag, ("true" if value else "false"), {}
    if tag == "rotation":
        return tag, None, {"rotation": str(int(value))}
    return tag, str(value), {}


def _apply_change_attribute(root, op) -> str:
    ve = _ve_for_index(root, op["component_index"])
    name = op["name"]
    value = op["value"]
    attrs = ve.find("elementAttributes")
    if attrs is None:
        attrs = etree.Element("elementAttributes")
        ve.insert(list(ve).index(ve.find("elementName")) + 1, attrs)

    for entry in attrs.findall("entry"):
        children = list(entry)
        if len(children) >= 2 and children[0].text == name:
            old_tag = children[1].tag
            tag, text, xattr = _format_value(old_tag, value)
            new_val = etree.Element(tag)
            new_val.text = text
            for k, v in xattr.items():
                new_val.set(k, v)
            entry.replace(children[1], new_val)
            return f"change_attribute[{op['component_index']}].{name} -> {value!r}"

    # No existing entry: create one with the known (or inferred) tag.
    if name in _NEW_ENTRY_TAG:
        tag = _NEW_ENTRY_TAG[name]
    elif isinstance(value, bool):
        tag = "boolean"
    elif isinstance(value, int):
        tag = "int"
    else:
        tag = "string"
    entry = etree.SubElement(attrs, "entry")
    key_el = etree.SubElement(entry, "string")
    key_el.text = name
    tag, text, xattr = _format_value(tag, value)
    val_el = etree.SubElement(entry, tag)
    val_el.text = text
    for k, v in xattr.items():
        val_el.set(k, v)
    return f"change_attribute[{op['component_index']}].{name} -> {value!r} (new entry)"


def _apply_replace_element(root, op) -> str:
    ve = _ve_for_index(root, op["component_index"])
    name_el = ve.find("elementName")
    old = name_el.text
    name_el.text = str(op["new_element"])
    return f"replace_element[{op['component_index']}]: {old} -> {op['new_element']}"


def _wires_block(root):
    wires = root.find("wires")
    if wires is None:
        raise ValueError("Circuit has no <wires> block.")
    return wires


def _wire_endpoints(wire) -> tuple[tuple[int, int], tuple[int, int]]:
    p1, p2 = wire.find("p1"), wire.find("p2")
    return ((int(p1.get("x")), int(p1.get("y"))),
            (int(p2.get("x")), int(p2.get("y"))))


def _apply_add_wire(root, op) -> str:
    wires = _wires_block(root)
    (x1, y1), (x2, y2) = tuple(op["p1"]), tuple(op["p2"])
    wire = etree.SubElement(wires, "wire")
    etree.SubElement(wire, "p1", x=str(int(x1)), y=str(int(y1)))
    etree.SubElement(wire, "p2", x=str(int(x2)), y=str(int(y2)))
    return f"add_wire ({x1},{y1}) -> ({x2},{y2})"


def _apply_delete_wire(root, op) -> str:
    wires = _wires_block(root)
    want = {tuple(op["p1"]), tuple(op["p2"])}
    removed = 0
    for wire in list(wires.findall("wire")):
        a, b = _wire_endpoints(wire)
        if {a, b} == want:
            wires.remove(wire)
            removed += 1
    if removed == 0:
        raise ValueError(
            f"delete_wire: no wire between {op['p1']} and {op['p2']}."
        )
    return f"delete_wire ({op['p1']}) -> ({op['p2']}) x{removed}"


class _PinIndex:
    """Claimed pin coordinates + wire-endpoint degrees for one circuit."""

    def __init__(self, dig_path: str):
        self.circuit = parse_dig_file(dig_path)
        self.netlist = build_netlist(self.circuit)
        self.degree: dict[tuple[int, int], int] = {}
        for w in self.circuit.wires:
            for ep in (w.p1.as_tuple(), w.p2.as_tuple()):
                self.degree[ep] = self.degree.get(ep, 0) + 1

    def pin_coord(self, component_index: int, pin_name: str) -> tuple[int, int]:
        for net in self.netlist.nets:
            for p in net.pins:
                if p.component_index == component_index and p.pin_name == pin_name:
                    return (p.x, p.y)
        comp = self.circuit.components[component_index]
        raise ValueError(
            f"Pin {pin_name!r} not found on component "
            f"[{component_index}] {comp.element_name}."
        )

    def require_simple(self, coord: tuple[int, int], what: str) -> None:
        if self.degree.get(coord, 0) > 1:
            raise ValueError(
                f"{what}: pin coordinate {coord} is a shared junction "
                f"({self.degree[coord]} wire endpoints); use "
                f"delete_wire/add_wire explicitly instead."
            )

    def attach_coord(self, component_index: int, pin_name: str) -> tuple[int, int]:
        """Best coordinate for CONNECTING INTO the net of (component, pin).

        Never the pin's own claim point when avoidable: raising a claimed
        endpoint's wire-degree above 1 breaks the netlist's pin heuristics
        (worst on subcircuit implicit pins, which require degree-1
        endpoints). An existing junction (degree >= 2) on the same net is
        immune — junction coords are never pin claims — so prefer the
        closest-sorted one; fall back to the pin coordinate only when the
        net has no junction yet.
        """
        target_net = None
        pin_coord = None
        for net in self.netlist.nets:
            for p in net.pins:
                if p.component_index == component_index and p.pin_name == pin_name:
                    target_net, pin_coord = net, (p.x, p.y)
                    break
            if target_net is not None:
                break
        if target_net is None:
            comp = self.circuit.components[component_index]
            raise ValueError(
                f"Pin {pin_name!r} not found on component "
                f"[{component_index}] {comp.element_name}."
            )
        junctions = sorted(
            c for c in target_net.coords if self.degree.get(c, 0) >= 2
        )
        return junctions[0] if junctions else pin_coord


def _apply_swap_pins(root, op, pins: _PinIndex) -> str:
    idx = op["component_index"]
    ca = pins.pin_coord(idx, op["pin_a"])
    cb = pins.pin_coord(idx, op["pin_b"])
    pins.require_simple(ca, "swap_pins")
    pins.require_simple(cb, "swap_pins")
    if pins.degree.get(ca, 0) == 0 and pins.degree.get(cb, 0) == 0:
        raise ValueError("swap_pins: neither pin has a wire to swap.")
    swapped = 0
    for wire in _wires_block(root).findall("wire"):
        for pt in (wire.find("p1"), wire.find("p2")):
            coord = (int(pt.get("x")), int(pt.get("y")))
            if coord == ca:
                pt.set("x", str(cb[0])); pt.set("y", str(cb[1]))
                swapped += 1
            elif coord == cb:
                pt.set("x", str(ca[0])); pt.set("y", str(ca[1]))
                swapped += 1
    return (f"swap_pins[{idx}] {op['pin_a']}@{ca} <-> {op['pin_b']}@{cb} "
            f"({swapped} endpoint(s))")


def _apply_rewire_pin(root, op, pins: _PinIndex) -> str:
    idx = op["component_index"]
    src = pins.pin_coord(idx, op["pin"])
    to = op["to"]
    dst = pins.attach_coord(to["component_index"], to["pin"])
    pins.require_simple(src, "rewire_pin")
    wires = _wires_block(root)
    removed = 0
    for wire in list(wires.findall("wire")):
        a, b = _wire_endpoints(wire)
        if src in (a, b):
            wires.remove(wire)
            removed += 1
    wire = etree.SubElement(wires, "wire")
    etree.SubElement(wire, "p1", x=str(src[0]), y=str(src[1]))
    etree.SubElement(wire, "p2", x=str(dst[0]), y=str(dst[1]))
    return (f"rewire_pin[{idx}].{op['pin']}@{src} -> "
            f"[{to['component_index']}].{to['pin']}@{dst} "
            f"(detached {removed} segment(s))")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_patch(dig_path: str, ops: list[dict]) -> tuple[str | None, PatchReport]:
    """Apply `ops` to a temp copy of `dig_path` (same directory).

    Returns (temp_path, report). On any failure — bad op, junction refusal,
    reparse failure, or NEW L1 errors introduced — the temp file is removed,
    temp_path is None, and report.ok is False. The original is never touched.
    """
    report = PatchReport(ok=False)
    if not ops:
        report.warning = "No patch ops given."
        return None, report
    for op in ops:
        if op.get("op") not in KNOWN_OPS:
            report.warning = f"Unknown patch op: {op.get('op')!r}."
            return None, report

    src_path = Path(dig_path)
    try:
        original_errors = len(check_all_l1_deep(parse_dig_file(str(src_path))).errors())
        original_kinds = {
            i.kind for i in check_all_l1_deep(parse_dig_file(str(src_path))).errors()
        }
    except Exception as exc:
        report.warning = f"Could not parse original circuit: {exc}"
        return None, report
    report.l1_errors_before = original_errors

    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(src_path), parser)
    root = tree.getroot()
    pins = _PinIndex(str(src_path))

    try:
        for op in ops:
            kind = op["op"]
            if kind == "change_attribute":
                report.applied.append(_apply_change_attribute(root, op))
            elif kind == "replace_element":
                report.applied.append(_apply_replace_element(root, op))
            elif kind == "swap_pins":
                report.applied.append(_apply_swap_pins(root, op, pins))
            elif kind == "rewire_pin":
                report.applied.append(_apply_rewire_pin(root, op, pins))
            elif kind == "add_wire":
                report.applied.append(_apply_add_wire(root, op))
            elif kind == "delete_wire":
                report.applied.append(_apply_delete_wire(root, op))
    except (ValueError, KeyError, TypeError) as exc:
        report.warning = f"Patch failed: {exc}"
        return None, report

    fd, temp_path = tempfile.mkstemp(
        suffix=".dig", prefix=_TEMP_PREFIX, dir=str(src_path.parent),
    )
    os.close(fd)
    tree.write(temp_path, xml_declaration=True, encoding="utf-8")

    # Validation: the patched circuit must reparse AND stay structurally
    # at-least-as-clean as the original.
    try:
        patched = parse_dig_file(temp_path)
        issues = check_all_l1_deep(patched)
        n_after = len(issues.errors())
        report.l1_errors_after = n_after
        report.new_l1_error_kinds = sorted(
            {i.kind for i in issues.errors()} - original_kinds
        )
        if n_after > original_errors:
            report.warning = (
                f"Patch introduces {n_after - original_errors} new Layer-1 "
                f"error(s) ({', '.join(report.new_l1_error_kinds) or 'same kinds'}); "
                f"rejected."
            )
            os.unlink(temp_path)
            return None, report
    except Exception as exc:
        report.warning = f"Patched circuit failed to reparse: {exc}"
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return None, report

    report.ok = True
    return temp_path, report


def rerun_with_patch(
    dig_path: str,
    ops: list[dict],
    *,
    spec_name: str | None = None,
    jar_path: str | None = None,
    timeout: float = 60.0,
    keep_temp: bool = False,
) -> PatchOutcome:
    """Apply a fix and run Digital per-row on the patched temp circuit.

    Runs every testcase in the file (or just `spec_name`); Mode A's verify
    step confirms a hypothesis only if its cluster's rows now pass AND no
    previously-passing row regressed — both readable from the result.
    """
    jar = jar_path or find_digital_jar()
    if jar is None:
        return PatchOutcome(ok=False, warning=(
            "Digital.jar not configured. Open the jar picker from the "
            "toolbar to select it."
        ))

    temp_path, report = apply_patch(dig_path, ops)
    if temp_path is None:
        return PatchOutcome(ok=False, warning=report.warning, report=report)

    try:
        circuit = parse_dig_file(temp_path)
        specs = [s for s in extract_test_specs(circuit) if s.rows]
        if spec_name is not None:
            specs = [s for s in specs if s.name == spec_name]
            if not specs:
                return PatchOutcome(
                    ok=False, report=report,
                    warning=f"No testcase named {spec_name!r} in this circuit.",
                )

        spec_payloads: list[dict] = []
        overall_ok = True
        for spec in specs:
            rows_by_idx = {r.line_index: r for r in spec.rows}
            results = per_row_run_auto(spec, temp_path, jar_path=jar,
                                       timeout=timeout)
            rows = []
            spec_ok = True
            for rr in results:
                src_row = rows_by_idx.get(rr.row_index)
                rows.append({
                    "index": rr.row_index,
                    "raw": src_row.raw if src_row else "",
                    "status": rr.status,
                    "error_message": rr.error_message,
                    "mismatches": rr.mismatches,
                })
                if rr.status in ("failed", "error"):
                    spec_ok = False
            spec_payloads.append({
                "name": spec.name, "headers": list(spec.headers),
                "rows": rows, "all_passed": spec_ok,
            })
            overall_ok &= spec_ok

        return PatchOutcome(
            ok=True,
            report=report,
            temp_path=temp_path if keep_temp else None,
            specs=spec_payloads,
            all_passed=overall_ok,
        )
    finally:
        if not keep_temp:
            try:
                os.unlink(temp_path)
            except OSError:
                pass