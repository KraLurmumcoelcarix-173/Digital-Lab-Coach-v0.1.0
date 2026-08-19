"""
Per-net bit-width inference.
"""

from dataclasses import dataclass

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList, Pin
from dlc.facts.width import pin_width


@dataclass(frozen=True)
class WidthConflict:
    net_id: int
    driver_a_name: str
    driver_a_width: int
    driver_b_name: str
    driver_b_width: int


@dataclass
class NetWidthInfo:

    width: int | None
    source: str


def _pin_width_with_subcircuit(circuit: Circuit, pin: Pin) -> int | None:

    comp = circuit.components[pin.component_index]
    if comp.element_name.endswith(".dig"):
        for sub_ref in circuit.subcircuits:
            if sub_ref.parent_component is comp and sub_ref.child_circuit is not None:
                child = sub_ref.child_circuit
                for child_in in child.inputs():
                    if child_in.label == pin.pin_name:
                        return child_in.bit_width()
                for child_out in child.outputs():
                    if child_out.label == pin.pin_name:
                        return child_out.bit_width()
                return None
        return None
    return pin_width(comp, pin.pin_name)


def infer_net_widths(
    circuit: Circuit, netlist: NetList
) -> tuple[dict[int, NetWidthInfo], list[WidthConflict]]:

    per_net: dict[int, NetWidthInfo] = {}
    conflicts: list[WidthConflict] = []

    for net in netlist.nets:
        # Collect known driver widths
        driver_widths: list[tuple[Pin, int]] = []
        for pin in net.drivers():
            w = _pin_width_with_subcircuit(circuit, pin)
            if w is not None:
                driver_widths.append((pin, w))

        if driver_widths:
            widths = [w for _, w in driver_widths]
            net_width = max(widths)
            per_net[net.net_id] = NetWidthInfo(width=net_width, source="driver")

            for i in range(len(driver_widths)):
                for j in range(i + 1, len(driver_widths)):
                    pin_a, w_a = driver_widths[i]
                    pin_b, w_b = driver_widths[j]
                    if w_a != w_b:
                        conflicts.append(WidthConflict(
                            net_id=net.net_id,
                            driver_a_name=f"{pin_a.element_name}.{pin_a.pin_name}",
                            driver_a_width=w_a,
                            driver_b_name=f"{pin_b.element_name}.{pin_b.pin_name}",
                            driver_b_width=w_b,
                        ))
            continue

        sink_widths: list[int] = []
        for pin in net.sinks():
            w = _pin_width_with_subcircuit(circuit, pin)
            if w is not None:
                sink_widths.append(w)

        if sink_widths:
            per_net[net.net_id] = NetWidthInfo(
                width=max(sink_widths), source="sink"
            )
        else:
            per_net[net.net_id] = NetWidthInfo(width=None, source="unknown")

    return per_net, conflicts


def width_of_net(circuit: Circuit, netlist: NetList, net_id: int) -> int | None:
    per_net, _ = infer_net_widths(circuit, netlist)
    info = per_net.get(net_id)
    return info.width if info else None