"""
F8: Subcircuit-interface conformance checker.
"""

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList, build_netlist
from dlc.facts.extractor import (
    CircuitFacts, extract_facts, _component_display_name,
)
from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection,
)


def _check_dangling_subcircuit_inputs(
    circuit: Circuit, netlist: NetList
) -> list[Issue]:
    out: list[Issue] = []
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue 
        child = sub_ref.child_circuit
        inst_idx = -1
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                inst_idx = idx
                break
        if inst_idx < 0:
            continue
        inst_comp = circuit.components[inst_idx]
        inst_name = _component_display_name(inst_comp, inst_idx)
        instance_pins = {}
        for net in netlist.nets:
            for pin in net.pins:
                if pin.component_index == inst_idx:
                    instance_pins[pin.pin_name] = (pin, net)
        child_input_components = [
            c for c in child.components
            if c.is_input() or c.element_name == "Clock"
        ]
        for child_in in child_input_components:
            if not child_in.label:
                continue
            entry = instance_pins.get(child_in.label)
            if entry is None:
                out.append(Issue(
                    kind="dangling_subcircuit_input",
                    severity=IssueSeverity.ERROR,
                    title=(
                        f"Subcircuit input not wired: "
                        f"{inst_name}.{child_in.label}"
                    ),
                    message=(
                        f"The subcircuit '{sub_ref.reference}' declares "
                        f"an input '{child_in.label}' "
                        f"({child_in.bit_width()}-bit), but no wire on the "
                        f"parent reaches that pin on this instance."
                    ),
                    component_indices=[inst_idx],
                    location=(inst_comp.position.x, inst_comp.position.y),
                    suggested_fix=(
                        f"Connect a signal in the parent circuit to "
                        f"{inst_name}'s '{child_in.label}' pin."
                    ),
                ))
                continue
            pin, net = entry
            if not net.possible_drivers():
                out.append(Issue(
                    kind="dangling_subcircuit_input",
                    severity=IssueSeverity.ERROR,
                    title=(
                        f"Subcircuit input has no driver: "
                        f"{inst_name}.{child_in.label}"
                    ),
                    message=(
                        f"The subcircuit '{sub_ref.reference}' input "
                        f"'{child_in.label}' on this instance is on a net "
                        f"with no driver — the subcircuit will receive "
                        f"an undefined value."
                    ),
                    component_indices=[inst_idx],
                    location=(pin.x, pin.y),
                    suggested_fix=(
                        f"Connect a driving signal to "
                        f"{inst_name}.{child_in.label}."
                    ),
                ))
    return out


def check_interface_conformance(
    circuit: Circuit,
    netlist: NetList | None = None,
    facts: CircuitFacts | None = None,
) -> IssueCollection:
    if netlist is None:
        netlist = build_netlist(circuit)
    if facts is None:
        facts = extract_facts(circuit, netlist=netlist)
    issues = IssueCollection()
    issues.extend(_check_dangling_subcircuit_inputs(circuit, netlist))
    return issues