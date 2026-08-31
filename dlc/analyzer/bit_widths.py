"""
F6: Bit-width consistency checker.

Two checks:
  - width_conflict 
  - width_mismatch 
"""

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList, build_netlist
from dlc.facts.extractor import (
    CircuitFacts, extract_facts, _component_display_name,
)
from dlc.facts.net_width import _pin_width_with_subcircuit
from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection,
)


def _check_driver_width_conflicts(
    circuit: Circuit, facts: CircuitFacts
) -> list[Issue]:
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "width_conflict":
            continue
        a = bug.detail.get("driver_a", {})
        b = bug.detail.get("driver_b", {})
        out.append(Issue(
            kind="width_conflict",
            severity=IssueSeverity.ERROR,
            title=(
                f"Width mismatch between drivers: "
                f"{a.get('name')} vs {b.get('name')}"
            ),
            message=(
                f"Two drivers on the same net have different bit widths: "
                f"{a.get('name')} is {a.get('width')}-bit, "
                f"{b.get('name')} is {b.get('width')}-bit. "
                f"Only one driver should feed a net."
            ),
            component_indices=bug.component_indices,
            net_id=bug.net_id,
            suggested_fix=(
                "Disconnect one driver, or insert a BitExtender / "
                "Splitter to match widths before they meet."
            ),
        ))
    return out

def _is_safe_truncation(sink_comp, sink_pin_name: str,
                       driver_w: int, sink_w: int) -> bool:
    if driver_w <= sink_w:
        return False
    if sink_comp.element_name == "BarrelShifter" and sink_pin_name == "sh":
        return True
    return False

def _check_driver_sink_width_mismatch(
    circuit: Circuit, netlist: NetList
) -> list[Issue]:
    out: list[Issue] = []
    for net in netlist.nets:
        drivers = net.drivers()
        sinks = net.sinks()
        if not drivers or not sinks:
            continue
        driver_pin = drivers[0]
        driver_w = _pin_width_with_subcircuit(circuit, driver_pin)
        if driver_w is None:
            continue
        for s in sinks:
            sink_w = _pin_width_with_subcircuit(circuit, s)
            if sink_w is None or sink_w == driver_w:
                continue
            sink_comp = circuit.components[s.component_index]
            if _is_safe_truncation(sink_comp, s.pin_name, driver_w, sink_w):
                continue
            d_name = _component_display_name(
                circuit.components[driver_pin.component_index],
                driver_pin.component_index,
            )
            s_name = _component_display_name(sink_comp, s.component_index)
            out.append(Issue(
                kind="width_mismatch",
                severity=IssueSeverity.ERROR,
                title=(
                    f"Bit-width mismatch: "
                    f"{d_name}.{driver_pin.pin_name} -> "
                    f"{s_name}.{s.pin_name}"
                ),
                message=(
                    f"{d_name}.{driver_pin.pin_name} produces a "
                    f"{driver_w}-bit signal, but {s_name}.{s.pin_name} "
                    f"expects {sink_w} bits. Digital will refuse to "
                    f"simulate this net."
                ),
                component_indices=[
                    driver_pin.component_index, s.component_index,
                ],
                location=(s.x, s.y),
                net_id=net.net_id,
                suggested_fix=(
                    "Either change one component's Bits attribute to "
                    "match the other, or insert a Splitter / "
                    "BitExtender between them to bridge the widths."
                ),
            ))
    return out


def _check_switch_net_widths(
    circuit: Circuit, netlist: NetList
) -> list[Issue]:
    out: list[Issue] = []
    for net in netlist.nets:
        pins = [p for p in net.pins if p.element_name != "Tunnel"]
        if not any(p.direction in ("bidir", "weak") for p in pins):
            continue
        widths = []
        for p in pins:
            w = _pin_width_with_subcircuit(circuit, p)
            if w is not None:
                widths.append((w, p))
        if len(widths) < 2:
            continue
        widths.sort(key=lambda t: (t[1].direction != "out",
                                   t[1].component_index))
        base_w, base_p = widths[0]
        for w, p in widths[1:]:
            if w == base_w:
                continue
            if (p.direction not in ("bidir", "weak")
                    and base_p.direction not in ("bidir", "weak")):
                continue
            b_name = _component_display_name(
                circuit.components[base_p.component_index],
                base_p.component_index)
            p_name = _component_display_name(
                circuit.components[p.component_index], p.component_index)
            out.append(Issue(
                kind="width_mismatch",
                severity=IssueSeverity.ERROR,
                title=(
                    f"Bit-width mismatch on a transistor net: "
                    f"{b_name}.{base_p.pin_name} vs {p_name}.{p.pin_name}"
                ),
                message=(
                    f"{b_name}.{base_p.pin_name} is {base_w}-bit but "
                    f"{p_name}.{p.pin_name} on the same wire is {w}-bit. "
                    f"Every rail, transistor and resistor sharing a wire "
                    f"must use the same Bits value; Digital will refuse "
                    f"to simulate this net."
                ),
                component_indices=[base_p.component_index,
                                   p.component_index],
                location=(p.x, p.y),
                net_id=net.net_id,
                suggested_fix=(
                    "Open each component's attributes and set the same "
                    "Bits value on both (transistor labs normally use "
                    "1 bit everywhere)."
                ),
            ))
            break
    return out


def check_bit_widths(
    circuit: Circuit,
    netlist: NetList | None = None,
    facts: CircuitFacts | None = None,
) -> IssueCollection:
    """Run all bit-width checks against `circuit`."""
    if netlist is None:
        netlist = build_netlist(circuit)
    if facts is None:
        facts = extract_facts(circuit, netlist=netlist)
    issues = IssueCollection()
    issues.extend(_check_driver_width_conflicts(circuit, facts))
    issues.extend(_check_driver_sink_width_mismatch(circuit, netlist))
    issues.extend(_check_switch_net_widths(circuit, netlist))
    return issues