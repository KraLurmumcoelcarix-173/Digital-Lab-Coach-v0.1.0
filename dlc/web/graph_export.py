"""
Convert a parsed Circuit + NetList + signal_graph into the JSON shape
Cytoscape.js consumes for rendering.

"""

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList
from dlc.facts.width import pin_width


_FAMILY_BY_ELEMENT: dict[str, str] = {
    "In": "io-in",
    "Out": "io-out",
    "Clock": "clock",
    "Const": "const",
    "Ground": "const",
    "VDD": "const",
    "Tunnel": "tunnel",
    "And": "gate",
    "Or": "gate",
    "XOr": "gate",
    "NAnd": "gate",
    "NOr": "gate",
    "XNOr": "gate",
    "Not": "gate",
    "Add": "arith",
    "BarrelShifter": "arith",
    "BitExtender": "arith",
    "Comparator": "arith",
    "Multiplexer": "mux",
    "Decoder": "mux",
    "PriorityEncoder": "mux",
    "Splitter": "splitter",
    "Register": "storage",
    "ROM": "storage",
    "Testcase": "annotation",
    "Rectangle": "annotation",
}

_FAMILY_DISPLAY: dict[str, str] = {
    "io-in": "I/O",
    "io-out": "I/O",
    "gate": "GATE",
    "arith": "ARITHMETIC",
    "mux": "SELECTOR",
    "splitter": "SPLITTER",
    "storage": "STORAGE",
    "tunnel": "TUNNEL",
    "subcircuit": "SUBCIRCUIT",
    "const": "CONSTANT",
    "clock": "CLOCK",
    "other": "OTHER",
}

_HIDDEN_ATTRS = {"lastDataFile"}

def _family(element_name: str) -> str:
    if element_name.endswith(".dig"):
        return "subcircuit"
    return _FAMILY_BY_ELEMENT.get(element_name, "other")


def _family_display(family: str) -> str:
    return _FAMILY_DISPLAY.get(family, family.upper())


def _node_display_label(idx: int, comp) -> str:
    if comp.label:
        return f"{comp.label}\n[{idx}]"
    if comp.element_name == "Const":
        value = comp.attributes.get("Value", 0)
        return f"Const({value})\n[{idx}]"
    return f"{comp.element_name}\n[{idx}]"


def to_cytoscape(circuit: Circuit, netlist: NetList, graph) -> dict:
    """
    Build {"nodes": [...], "edges": [...]} in Cytoscape.js Elements
    format. Tunnel and annotation (Testcase/Rectangle) elements are
    omitted 
    """
    nodes = []
    for idx, comp in enumerate(circuit.components):
        family = _family(comp.element_name)
        if family in ("annotation", "tunnel"):
            continue
        nodes.append({
            "data": {
                "id": str(idx),
                "label": _node_display_label(idx, comp),
                "element_name": comp.element_name,
                "comp_label": comp.label or "",
                "family": family,
                "family_display": _family_display(family),
                "attributes": {
                    k: v for k, v in comp.attributes.items()
                    if isinstance(v, (str, int, float, bool))
                    and k not in _HIDDEN_ATTRS
                },
                "x_dig": comp.position.x,
                "y_dig": comp.position.y,
            },
        })

    edges = []
    edge_id = 0
    for u, v, data in graph.edges(data=True):
        driver_comp = circuit.components[u]
        driver_pin = data.get("driver_pin")
        bits = pin_width(driver_comp, driver_pin) if driver_pin else None
        edges.append({
            "data": {
                "id": f"e{edge_id}",
                "source": str(u),
                "target": str(v),
                "net_id": data.get("net_id"),
                "driver_pin": driver_pin,
                "sink_pin": data.get("sink_pin"),
                "bits": bits,
            },
        })
        edge_id += 1

    return {"nodes": nodes, "edges": edges}

def circuit_summary(circuit: Circuit, netlist: NetList) -> dict:
    """
    Right-sidebar headline numbers. Static L2 (no LLM).
    """
    inventory: dict[str, int] = {}
    for comp in circuit.components:
        if comp.element_name in ("Testcase", "Rectangle"):
            continue
        inventory[comp.element_name] = inventory.get(comp.element_name, 0) + 1

    inputs = [
        {"label": c.label or "(unlabeled)", "bits": c.attributes.get("Bits", 1)}
        for c in circuit.inputs()
    ]
    outputs = [
        {"label": c.label or "(unlabeled)", "bits": c.attributes.get("Bits", 1)}
        for c in circuit.outputs()
    ]
    subcircuits = [
        {
            "reference": sub.reference,
            "resolved": sub.resolved_path is not None,
            "error": sub.resolution_error,
        }
        for sub in circuit.subcircuits
    ]

    n_driven = sum(1 for n in netlist.nets if n.drivers())
    n_undriven_with_pins = sum(
        1 for n in netlist.nets if n.pins and not n.drivers()
    )
    n_multi = sum(1 for n in netlist.nets if len(n.drivers()) > 1)

    n_testcases = sum(
        1 for c in circuit.components if c.element_name == "Testcase"
    )

    return {
        "source_path": circuit.source_path,
        "format_version": circuit.format_version,
        "inventory": inventory,
        "inputs": inputs,
        "outputs": outputs,
        "subcircuits": subcircuits,
        "has_testcases": n_testcases > 0,
        "testcase_count": n_testcases,
        "net_stats": {
            "total": len(netlist.nets),
            "driven": n_driven,
            "undriven_with_pins": n_undriven_with_pins,
            "multi_driver": n_multi,
        },
    }