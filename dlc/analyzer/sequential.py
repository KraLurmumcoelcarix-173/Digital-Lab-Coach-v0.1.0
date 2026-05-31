"""
F9: Sequential / timing checker.
"""

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList, build_netlist
from dlc.facts.extractor import _component_display_name
from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection,
)


_CLOCKED_ELEMENTS = {"Register", "RAM", "D-FlipFlop", "JK-FF", "T-FF", "Counter"}


def _pin_on_instance(netlist: NetList, comp_idx: int, pin_name: str):
    for net in netlist.nets:
        for pin in net.pins:
            if pin.component_index == comp_idx and pin.pin_name == pin_name:
                return pin, net
    return None, None


def _check_register_clock(circuit: Circuit, netlist: NetList) -> list[Issue]:
    out: list[Issue] = []
    for idx, comp in enumerate(circuit.components):
        if comp.element_name not in _CLOCKED_ELEMENTS:
            continue
        c_pin, c_net = _pin_on_instance(netlist, idx, "C")
        if c_pin is None or c_net is None:
            continue
        if c_net.drivers():
            continue
        name = _component_display_name(comp, idx)
        out.append(Issue(
            kind="register_no_clock",
            severity=IssueSeverity.ERROR,
            title=f"{name} has no clock driving it",
            message=(
                f"{name}'s clock pin (C) is on a net with no driver. "
                f"Without a clock signal the register will never update "
                f"its stored value."
            ),
            component_indices=[idx],
            location=(c_pin.x, c_pin.y),
            net_id=c_net.net_id,
            suggested_fix=(
                f"Wire a Clock element (or another clocked signal) to "
                f"{name}'s C pin."
            ),
        ))
    return out


def _check_register_en(circuit: Circuit, netlist: NetList) -> list[Issue]:
    out: list[Issue] = []
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Register":
            continue
        en_pin, en_net = _pin_on_instance(netlist, idx, "en")
        if en_pin is None or en_net is None:
            continue
        if en_net.drivers():
            continue
        name = _component_display_name(comp, idx)
        out.append(Issue(
            kind="floating_register_en",
            severity=IssueSeverity.ERROR,
            title=f"{name}.en is floating",
            message=(
                f"{name}'s enable pin (en) has no driver. Floating enable "
                f"produces undefined write behavior — the register may "
                f"never write, or may oscillate at runtime."
            ),
            component_indices=[idx],
            location=(en_pin.x, en_pin.y),
            net_id=en_net.net_id,
            suggested_fix=(
                f"If {name} should always write, tie en to a Const(1). "
                f"Otherwise wire en to your write-enable control signal."
            ),
        ))
    return out


def _check_orphan_clock(circuit: Circuit, netlist: NetList) -> list[Issue]:
    out: list[Issue] = []
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Clock":
            continue
        clock_net = None
        for net in netlist.nets:
            for p in net.pins:
                if p.component_index == idx:
                    clock_net = net
                    break
            if clock_net is not None:
                break
        if clock_net is None:
            continue
        non_tunnel_sinks = [
            p for p in clock_net.pins
            if p.direction == "in" and p.element_name != "Tunnel"
        ]
        if non_tunnel_sinks:
            continue
        name = comp.label or f"Clock[{idx}]"
        out.append(Issue(
            kind="orphan_clock",
            severity=IssueSeverity.WARNING,
            title=f"Clock '{name}' has no downstream sink",
            message=(
                f"The Clock element '{name}' drives a net with no "
                f"consumer (no Register C pin, no other input). The "
                f"clock signal is generated but used nowhere."
            ),
            component_indices=[idx],
            location=(comp.position.x, comp.position.y),
            net_id=clock_net.net_id,
            suggested_fix=(
                f"Connect '{name}' to the C pin of at least one Register "
                f"or RAM, or delete this Clock element if it's leftover."
            ),
        ))
    return out


def _check_empty_rom(circuit: Circuit) -> list[Issue]:
    out: list[Issue] = []
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "ROM":
            continue
        data = comp.attributes.get("Data", "")
        if not isinstance(data, str):
            data = "" if data is None else str(data)
        if data.strip():
            continue
        name = _component_display_name(comp, idx)
        out.append(Issue(
            kind="empty_rom",
            severity=IssueSeverity.WARNING,
            title=f"{name} has no data programmed",
            message=(
                f"{name}'s Data attribute is empty. At runtime every "
                f"address read will return 0 regardless of the input, "
                f"which is rarely intended. Digital does not flag this "
                f"on its own (it only validates malformed Data strings)."
            ),
            component_indices=[idx],
            location=(comp.position.x, comp.position.y),
            suggested_fix=(
                f"In Digital, double-click {name} and either type the "
                f"contents into the Data field or use 'Load Data' to "
                f"import a .hex file."
            ),
        ))
    return out


def check_sequential(
    circuit: Circuit,
    netlist: NetList | None = None,
) -> IssueCollection:
    if netlist is None:
        netlist = build_netlist(circuit)
    issues = IssueCollection()
    issues.extend(_check_register_clock(circuit, netlist))
    issues.extend(_check_register_en(circuit, netlist))
    issues.extend(_check_orphan_clock(circuit, netlist))
    issues.extend(_check_empty_rom(circuit))
    return issues