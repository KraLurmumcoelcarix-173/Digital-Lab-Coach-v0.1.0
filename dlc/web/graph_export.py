"""
Convert a parsed Circuit + NetList + signal_graph into the JSON shape
Cytoscape.js consumes for rendering.
"""

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList
from dlc.facts.width import pin_width
from dlc.facts.net_width import infer_net_widths
from dlc.parser.pin_geometry import inverted_input_names
from dlc.web.shape_svg import shape_for


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
    "NFET": "switch",
    "PFET": "switch",
    "PullUp": "switch",
    "PullDown": "switch",
    "Testcase": "annotation",
    "Rectangle": "annotation",
    "Text": "annotation",
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
    "switch": "TRANSISTOR",
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


def _build_child_by_index(circuit: Circuit) -> dict:
    out: dict[int, "Circuit"] = {}
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                out[idx] = sub_ref.child_circuit
                break
    return out


def _resolve_edge_bits(
    circuit: Circuit,
    driver_comp,
    driver_pin: str | None,
    target_comp,
    sink_pin: str | None,
    child_by_index: dict,
    u_idx: int,
    v_idx: int,
) -> int | None:
    if driver_pin:
        w = pin_width(driver_comp, driver_pin)
        if w is not None:
            return w
    if driver_comp.element_name.endswith(".dig") and driver_pin:
        child = child_by_index.get(u_idx)
        if child is not None:
            for c in child.outputs():
                if c.label == driver_pin:
                    return int(c.attributes.get("Bits", 1))
    if sink_pin:
        w = pin_width(target_comp, sink_pin)
        if w is not None:
            return w
    if target_comp.element_name.endswith(".dig") and sink_pin:
        child = child_by_index.get(v_idx)
        if child is not None:
            for c in child.inputs():
                if c.label == sink_pin:
                    return int(c.attributes.get("Bits", 1))
    return None

def _synthetic_not_node(node_id: str, gate_comp) -> dict:
    return {
        "data": {
            "id": node_id,
            "label": "NOT",
            "element_name": "Not",
            "comp_label": "",
            "family": "gate",
            "family_display": _family_display("gate"),
            "attributes": {"bubble": True},
            "x_dig": gate_comp.position.x - 40,
            "y_dig": gate_comp.position.y,
            "synthetic": True,
        },
    }

def to_cytoscape(circuit: Circuit, netlist: NetList, graph) -> dict:
    """
    Build {"nodes": [...], "edges": [...]} in Cytoscape.js Elements
    format. Tunnel and annotation (Testcase/Rectangle) elements are
    omitted 
    """
    child_by_index = _build_child_by_index(circuit)
    per_net, _conflicts = infer_net_widths(circuit, netlist)
    nodes = []
    node_ports: dict[str, dict] = {}
    for idx, comp in enumerate(circuit.components):
        family = _family(comp.element_name)
        if family in ("annotation", "tunnel"):
            continue
        data = {
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
        }
        glyph = shape_for(comp, family)
        if glyph is not None:
            data["shape_svg"] = glyph["svg"]
            data["shape_w"] = glyph["w"]
            data["shape_h"] = glyph["h"]
            data["shape_tier"] = glyph["tier"]
            node_ports[str(idx)] = glyph.get("ports", {})
        nodes.append({"data": data})

    inverted_by_comp = {}
    for idx, comp in enumerate(circuit.components):
        inv = inverted_input_names(comp)
        if inv:
            inverted_by_comp[idx] = set(inv)

    edges = []
    edge_id = 0
    not_node_ids: dict = {}
    for u, v, data in graph.edges(data=True):
        driver_comp = circuit.components[u]
        target_comp = circuit.components[v]
        driver_pin = data.get("driver_pin")
        sink_pin = data.get("sink_pin")
        net_id = data.get("net_id")
        info = per_net.get(net_id) if net_id is not None else None
        bits = info.width if info is not None else None
        if bits is None:
            bits = _resolve_edge_bits(
                circuit, driver_comp, driver_pin, target_comp, sink_pin,
                child_by_index, u, v,
            )

        if v in inverted_by_comp and sink_pin in inverted_by_comp[v]:
            key = (v, sink_pin)
            not_id = not_node_ids.get(key)
            if not_id is None:
                not_id = f"not-{v}-{sink_pin}"
                not_node_ids[key] = not_id
                nodes.append(_synthetic_not_node(not_id, target_comp))
            se = node_ports.get(str(u), {}).get(driver_pin)
            d1 = {
                "id": f"e{edge_id}", "source": str(u), "target": not_id,
                "net_id": net_id, "driver_pin": driver_pin, "sink_pin": "A",
                "bits": bits,
            }
            if se:
                d1["se"] = se
            edges.append({"data": d1})
            edge_id += 1
            te = node_ports.get(str(v), {}).get(sink_pin)
            d2 = {
                "id": f"e{edge_id}", "source": not_id, "target": str(v),
                "net_id": net_id, "driver_pin": "Y", "sink_pin": sink_pin,
                "bits": bits,
            }
            if te:
                d2["te"] = te
            edges.append({"data": d2})
            edge_id += 1
        else:
            edata = {
                "id": f"e{edge_id}",
                "source": str(u),
                "target": str(v),
                "net_id": net_id,
                "driver_pin": driver_pin,
                "sink_pin": sink_pin,
                "bits": bits,
            }
            se = node_ports.get(str(u), {}).get(driver_pin)
            te = node_ports.get(str(v), {}).get(sink_pin)
            if se:
                edata["se"] = se
            if te:
                edata["te"] = te
            edges.append({"data": edata})
            edge_id += 1

    return {"nodes": nodes, "edges": edges}

def circuit_summary(circuit: Circuit, netlist: NetList) -> dict:
    inventory: dict[str, int] = {}
    for comp in circuit.components:
        if comp.element_name in ("Testcase", "Rectangle", "Text"):
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

    n_driven = sum(1 for n in netlist.nets if n.possible_drivers())
    n_undriven_with_pins = sum(
        1 for n in netlist.nets if n.pins and not n.possible_drivers()
    )
    n_multi = sum(1 for n in netlist.nets if len(n.drivers()) > 1)

    n_testcases = sum(
        1 for c in circuit.components if c.element_name == "Testcase"
    )

    official_status = None
    try:
        import os as _os
        from dlc.testing.inject import file_test_status
        name = _os.path.basename(circuit.source_path or "")
        if name:
            official_status = file_test_status(circuit, name)
    except Exception:
        pass

    return {
        "source_path": circuit.source_path,
        "format_version": circuit.format_version,
        "inventory": inventory,
        "inputs": inputs,
        "outputs": outputs,
        "subcircuits": subcircuits,
        "has_testcases": n_testcases > 0,
        "testcase_count": n_testcases,
        "official_test_available": official_status is not None,
        "official_test_status": official_status,
        "net_stats": {
            "total": len(netlist.nets),
            "driven": n_driven,
            "undriven_with_pins": n_undriven_with_pins,
            "multi_driver": n_multi,
        },
    }