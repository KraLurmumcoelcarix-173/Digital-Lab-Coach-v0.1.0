"""
Structural fact extractor.

Inputs:
  - Circuit:  from dig_parser
  - NetList:  from build_netlist 
  - graph:    networkx.MultiDiGraph from build_signal_graph

Output:
  CircuitFacts containing:
    - source_path, format_version
    - header counts (components, wires, nets, subcircuits, I/O, bugs)
    - inventory by element type
    - I/O facts (label, bit width, position)
    - subcircuit hierarchy with child interface
    - per-net summary tagged with anomalies + inferred width
    - per-component topology (predecessors, successors)
    - bug facts (dangling_input, multi_driver, combinational_cycle,
      width_conflict, missing_subcircuit, dangling subcircuit reference input,
      missing subcircuit reference, isolated component, empty tunnel, unused_top_output)
    - to_dict() / to_json() for JSON-safe export to the LLM layer.
"""

from dataclasses import dataclass, field, asdict
from collections import Counter
import json

import networkx as nx

from dlc.parser.models import Circuit, Component
from dlc.parser.netlist import NetList, Net, Pin, build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.facts.net_width import infer_net_widths, NetWidthInfo, WidthConflict
from dlc.facts.width import pin_width


_CLOCKED_ELEMENTS = frozenset({
    "Register", "Clock", "RAM", "D-FlipFlop", "JK-FF", "T-FF", "Counter",
})


# Data records

@dataclass
class IOFact:
    """A top-level circuit port (one In or Out component)."""
    index: int
    label: str | None
    bit_width: int
    position: tuple[int, int]
    direction: str  # "in" for In components, "out" for Out components


@dataclass
class ComponentFact:
    """One component with its graph neighbors."""
    index: int
    element_name: str
    label: str | None
    display_name: str 
    position: tuple[int, int]
    attributes: dict
    bit_width: int | None
    predecessors: list[int]
    successors: list[int]


@dataclass
class SubcircuitFact:
    """One subcircuit instance and its resolved child's interface."""
    instance_index: int
    reference: str
    resolved_path: str | None
    resolution_error: str | None
    child_source_path: str | None
    child_inputs: list[dict]   # [{"label": str|None, "bit_width": int}, ...]
    child_outputs: list[dict]


@dataclass
class NetFact:
    """One net summary."""
    net_id: int
    pin_count: int
    driver_count: int
    sink_count: int
    coord_count: int
    tunnel_names: list[str]
    bit_width: int | None
    width_source: str   # "driver" | "sink" | "unknown"
    anomalies: list[str]   
    pins: list[dict]    # each: component_index, element_name, pin_name,
                        # direction, x, y, pin_width


@dataclass
class BugFact:
    """One structurally-detectable issue."""
    kind: str   # "dangling_input" | "multi_driver" | "combinational_cycle"
                # | "width_conflict" | "missing_subcircuit"
    description: str
    net_id: int | None = None
    component_indices: list[int] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class CircuitFacts:
    source_path: str | None
    format_version: int | None
    header: dict
    inventory: dict
    inputs: list[IOFact]
    outputs: list[IOFact]
    subcircuits: list[SubcircuitFact]
    components: list[ComponentFact]
    nets: list[NetFact]
    bugs: list[BugFact]


# Helpers

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        """Return the bundle as a JSON string.
        """
        return json.dumps(self.to_dict(), indent=indent)

def _component_display_name(comp: Component, index: int) -> str:
    if comp.label:
        return comp.label
    e = comp.element_name
    if e == "Const":
        val = comp.attributes.get("Value", 1)
        return f"Const({val})"
    if e == "Tunnel":
        return f"Tunnel({comp.attributes.get('NetName', '?')})"
    return f"{e}[{index}]"

def _component_bit_width(comp: Component) -> int | None:
    """The raw `Bits` attribute, or None when absent.

    Component.bit_width() defaults to 1 when Bits is missing, which is
    misleading for elements where Bits doesn't apply (Splitter, Tunnel,
    Testcase, subcircuit instances). Returning None lets the LLM skip
    those instead of seeing a wrong "1-bit".
    """
    if "Bits" in comp.attributes:
        try:
            return int(comp.attributes["Bits"])
        except (TypeError, ValueError):
            return None
    return None


def _io_facts(circuit: Circuit) -> tuple[list[IOFact], list[IOFact]]:
    ins: list[IOFact] = []
    outs: list[IOFact] = []
    for i, comp in enumerate(circuit.components):
        if comp.is_input():
            ins.append(IOFact(
                index=i,
                label=comp.label,
                bit_width=comp.bit_width(),
                position=(comp.position.x, comp.position.y),
                direction="in",
            ))
        elif comp.is_output():
            outs.append(IOFact(
                index=i,
                label=comp.label,
                bit_width=comp.bit_width(),
                position=(comp.position.x, comp.position.y),
                direction="out",
            ))
    return ins, outs


def _inventory(circuit: Circuit) -> dict:
    counter = Counter(c.element_name for c in circuit.components)
    return dict(sorted(counter.items()))


def _subcircuit_instance_index(circuit: Circuit, sub_ref) -> int:
    for idx, comp in enumerate(circuit.components):
        if comp is sub_ref.parent_component:
            return idx
    return -1


def _subcircuit_facts(circuit: Circuit) -> list[SubcircuitFact]:
    out: list[SubcircuitFact] = []
    for sub_ref in circuit.subcircuits:
        child_inputs: list[dict] = []
        child_outputs: list[dict] = []
        child_source = None
        if sub_ref.child_circuit is not None:
            child = sub_ref.child_circuit
            child_source = child.source_path
            for ch in child.inputs():
                child_inputs.append({
                    "label": ch.label,
                    "bit_width": ch.bit_width(),
                })
            for ch in child.outputs():
                child_outputs.append({
                    "label": ch.label,
                    "bit_width": ch.bit_width(),
                })
        out.append(SubcircuitFact(
            instance_index=_subcircuit_instance_index(circuit, sub_ref),
            reference=sub_ref.reference,
            resolved_path=sub_ref.resolved_path,
            resolution_error=sub_ref.resolution_error,
            child_source_path=child_source,
            child_inputs=child_inputs,
            child_outputs=child_outputs,
        ))
    return out


def _component_facts(circuit: Circuit, graph: nx.MultiDiGraph) -> list[ComponentFact]:
    out: list[ComponentFact] = []
    for idx, comp in enumerate(circuit.components):
        if idx in graph:
            preds = sorted(set(graph.predecessors(idx)))
            succs = sorted(set(graph.successors(idx)))
        else:
            preds, succs = [], []
        out.append(ComponentFact(
            index=idx,
            element_name=comp.element_name,
            label=comp.label,
            display_name=_component_display_name(comp, idx),
            position=(comp.position.x, comp.position.y),
            attributes=dict(comp.attributes),
            bit_width=_component_bit_width(comp),
            predecessors=preds,
            successors=succs,
        ))
    return out


def _pin_dict(circuit: Circuit, pin: Pin) -> dict:
    """Serialize a Pin, including its declared pin-width when known."""
    comp = circuit.components[pin.component_index]
    if comp.element_name.endswith(".dig"):
        pw = None
        for sub_ref in circuit.subcircuits:
            if sub_ref.parent_component is comp and sub_ref.child_circuit is not None:
                child = sub_ref.child_circuit
                for inp in child.inputs():
                    if inp.label == pin.pin_name:
                        pw = inp.bit_width()
                        break
                if pw is None:
                    for outp in child.outputs():
                        if outp.label == pin.pin_name:
                            pw = outp.bit_width()
                            break
                break
    else:
        pw = pin_width(comp, pin.pin_name)
    return {
        "component_index": pin.component_index,
        "element_name": pin.element_name,
        "pin_name": pin.pin_name,
        "direction": pin.direction,
        "x": pin.x,
        "y": pin.y,
        "pin_width": pw,
    }


def _net_anomalies(net: Net, has_width_conflict: bool) -> list[str]:
    tags: list[str] = []
    if net.pins and not net.drivers():
        tags.append("undriven")
        if all(p.direction == "in" for p in net.pins):
            tags.append("dangling_input")
    if len(net.drivers()) > 1:
        tags.append("multi_driver")
    if has_width_conflict:
        tags.append("width_conflict")
    return sorted(tags)


def _net_facts(
    circuit: Circuit,
    netlist: NetList,
    per_net_width: dict[int, NetWidthInfo],
    conflicts: list[WidthConflict],
) -> list[NetFact]:
    conflict_nets = {c.net_id for c in conflicts}
    out: list[NetFact] = []
    for net in netlist.nets:
        info = per_net_width.get(net.net_id)
        width = info.width if info else None
        source = info.source if info else "unknown"
        out.append(NetFact(
            net_id=net.net_id,
            pin_count=len(net.pins),
            driver_count=len(net.drivers()),
            sink_count=len(net.sinks()),
            coord_count=len(net.coords),
            tunnel_names=sorted(net.tunnel_names),
            bit_width=width,
            width_source=source,
            anomalies=_net_anomalies(net, net.net_id in conflict_nets),
            pins=[_pin_dict(circuit, p) for p in net.pins],
        ))
    return out

def _child_has_clocked(child: Circuit) -> bool:
    for c in child.components:
        if c.element_name in _CLOCKED_ELEMENTS:
            return True
    for sub_ref in child.subcircuits:
        if sub_ref.child_circuit is not None and _child_has_clocked(sub_ref.child_circuit):
            return True
    return False


def _is_or_contains_clocked(circuit: Circuit, comp_idx: int) -> bool:
    """True if component is a clocked element OR a subcircuit instance whose
    resolved child contains one recursively."""
    comp = circuit.components[comp_idx]
    if comp.element_name in _CLOCKED_ELEMENTS:
        return True
    if comp.element_name.endswith(".dig"):
        for sub_ref in circuit.subcircuits:
            if sub_ref.parent_component is comp and sub_ref.child_circuit is not None:
                return _child_has_clocked(sub_ref.child_circuit)
    return False

def _purely_combinational_cycles(
    circuit: Circuit, graph: nx.MultiDiGraph
) -> list[list[int]]:
    cycles: list[list[int]] = []
    for cyc in nx.simple_cycles(graph):
        if any(_is_or_contains_clocked(circuit, i) for i in cyc):
            continue
        cycles.append(list(cyc))
    return cycles


def _pin_descriptor(circuit: Circuit, pin: Pin) -> str:
    comp = circuit.components[pin.component_index]
    return f"{_component_display_name(comp, pin.component_index)}.{pin.pin_name}"


def _collect_bugs(
    circuit: Circuit,
    netlist: NetList,
    graph: nx.MultiDiGraph,
    conflicts: list[WidthConflict],
) -> list[BugFact]:
    bugs: list[BugFact] = []

    for net in netlist.nets:
        if net.pins and not net.drivers():
            sink_pins = [p for p in net.pins if p.direction == "in"]
            if sink_pins:
                descs = [_pin_descriptor(circuit, p) for p in sink_pins]
                bugs.append(BugFact(
                    kind="dangling_input",
                    description=(
                        f"Net {net.net_id} has no driver; "
                        f"undriven input pin(s): {', '.join(descs)}"
                    ),
                    net_id=net.net_id,
                    component_indices=sorted({p.component_index for p in sink_pins}),
                    detail={
                        "pins": [
                            {
                                "component_index": p.component_index,
                                "element_name": p.element_name,
                                "pin_name": p.pin_name,
                                "x": p.x,
                                "y": p.y,
                            }
                            for p in sink_pins
                        ],
                    },
                ))

        if len(net.drivers()) > 1:
            drivers = net.drivers()
            descs = [_pin_descriptor(circuit, p) for p in drivers]
            bugs.append(BugFact(
                kind="multi_driver",
                description=(
                    f"Net {net.net_id} has {len(drivers)} drivers: "
                    f"{', '.join(descs)}"
                ),
                net_id=net.net_id,
                component_indices=sorted({p.component_index for p in drivers}),
                detail={
                    "drivers": [
                        {
                            "component_index": p.component_index,
                            "element_name": p.element_name,
                            "pin_name": p.pin_name,
                            "x": p.x,
                            "y": p.y,
                        }
                        for p in drivers
                    ],
                },
            ))

    for c in conflicts:
        bugs.append(BugFact(
            kind="width_conflict",
            description=(
                f"Net {c.net_id} has drivers of different widths: "
                f"{c.driver_a_name}={c.driver_a_width} vs "
                f"{c.driver_b_name}={c.driver_b_width}"
            ),
            net_id=c.net_id,
            detail={
                "driver_a": {"name": c.driver_a_name, "width": c.driver_a_width},
                "driver_b": {"name": c.driver_b_name, "width": c.driver_b_width},
            },
        ))

    for cyc in _purely_combinational_cycles(circuit, graph):
        names = [
            f"[{i}] {circuit.components[i].label or circuit.components[i].element_name}"
            for i in cyc
        ]
        bugs.append(BugFact(
            kind="combinational_cycle",
            description=f"Combinational cycle: {' -> '.join(names)} -> ...",
            component_indices=sorted(cyc),
            detail={"cycle_order": list(cyc)},
        ))

    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None and sub_ref.resolution_error:
            inst_idx = _subcircuit_instance_index(circuit, sub_ref)
            bugs.append(BugFact(
                kind="missing_subcircuit",
                description=(
                    f"Subcircuit '{sub_ref.reference}' could not be resolved: "
                    f"{sub_ref.resolution_error}"
                ),
                component_indices=[inst_idx] if inst_idx >= 0 else [],
                detail={
                    "reference": sub_ref.reference,
                    "resolution_error": sub_ref.resolution_error,
                },
            ))

    return bugs


# Public API

def extract_facts(
    circuit: Circuit,
    netlist: NetList | None = None,
    graph: nx.MultiDiGraph | None = None,
) -> CircuitFacts:
    """Assemble the full CircuitFacts bundle for a parsed circuit.
    """
    if netlist is None:
        netlist = build_netlist(circuit)
    if graph is None:
        graph = build_signal_graph(circuit, netlist)

    per_net_width, conflicts = infer_net_widths(circuit, netlist)

    ins, outs = _io_facts(circuit)
    inventory = _inventory(circuit)
    sub_facts = _subcircuit_facts(circuit)
    comp_facts = _component_facts(circuit, graph)
    net_facts = _net_facts(circuit, netlist, per_net_width, conflicts)
    bugs = _collect_bugs(circuit, netlist, graph, conflicts)

    header = {
        "component_count": len(circuit.components),
        "wire_count": len(circuit.wires),
        "net_count": len(netlist.nets),
        "subcircuit_count": len(circuit.subcircuits),
        "input_count": len(ins),
        "output_count": len(outs),
        "bug_count": len(bugs),
    }

    return CircuitFacts(
        source_path=circuit.source_path,
        format_version=circuit.format_version,
        header=header,
        inventory=inventory,
        inputs=ins,
        outputs=outs,
        subcircuits=sub_facts,
        components=comp_facts,
        nets=net_facts,
        bugs=bugs,
    )
