"""
Tests for signal-flow graph + reachability helpers.
"""

from pathlib import Path
import glob

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import (
    build_signal_graph,
    input_component_indices,
    output_component_indices,
    reachable_outputs_from_inputs,
)

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier1_minimal"
TIER2_DIR  = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier2_structured"


def _idx_by_label(circuit, label):
    return next(i for i, c in enumerate(circuit.components) if c.label == label)


def _idx_by_element(circuit, element_name):
    return next(i for i, c in enumerate(circuit.components)
                if c.element_name == element_name)


def test_graph_builds_for_all_samples():
    """Graph builder must not crash, and every component is a node."""
    for f in glob.glob("data/sample_circuits/**/*.dig", recursive=True):
        c = parse_dig_file(f)
        nl = build_netlist(c)
        g = build_signal_graph(c, nl)
        assert g.number_of_nodes() == len(c.components), f


def test_graph_single_and_edges():
    """single_and: In(A)->AND, In(B)->AND, AND->Out(Y) = 3 edges."""
    c = parse_dig_file(str(SAMPLES_DIR / "single_and.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)

    in_a = _idx_by_label(c, "A")
    in_b = _idx_by_label(c, "B")
    and_idx = _idx_by_element(c, "And")
    out_y = _idx_by_label(c, "Y")

    assert g.has_edge(in_a, and_idx)
    assert g.has_edge(in_b, and_idx)
    assert g.has_edge(and_idx, out_y)
    assert g.number_of_edges() == 3


def test_graph_node_attributes():
    c = parse_dig_file(str(SAMPLES_DIR / "single_and.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    and_idx = _idx_by_element(c, "And")
    assert g.nodes[and_idx]["element_name"] == "And"
    assert g.nodes[and_idx]["component"] is c.components[and_idx]


def test_graph_edge_attributes_carry_pin_and_net():
    c = parse_dig_file(str(SAMPLES_DIR / "single_and.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)

    and_idx = _idx_by_element(c, "And")
    out_y = _idx_by_label(c, "Y")
    edges = list(g.get_edge_data(and_idx, out_y).values())
    assert len(edges) == 1
    e = edges[0]
    assert e["driver_pin"] == "Y"
    assert e["sink_pin"] == "in"
    assert isinstance(e["net_id"], int)


def test_graph_half_adder_topology():
    """
    Half adder: A, B each fan out to XOR and AND; XOR→Sum; AND→Carry.
    Six edges total.
    """
    c = parse_dig_file(str(SAMPLES_DIR / "half_adder.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)

    in_a = _idx_by_label(c, "A")
    in_b = _idx_by_label(c, "B")
    xor_idx = _idx_by_element(c, "XOr")
    and_idx = _idx_by_element(c, "And")
    sum_idx = _idx_by_label(c, "Sum")
    carry_idx = _idx_by_label(c, "Carry")

    assert g.has_edge(in_a, xor_idx)
    assert g.has_edge(in_a, and_idx)
    assert g.has_edge(in_b, xor_idx)
    assert g.has_edge(in_b, and_idx)
    assert g.has_edge(xor_idx, sum_idx)
    assert g.has_edge(and_idx, carry_idx)
    assert g.number_of_edges() == 6


def test_graph_tunnel_handoff():
    """Tunnel's NetName merging must let signals cross with no wire."""
    c = parse_dig_file(str(SAMPLES_DIR / "tunnel_test.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)

    in_a = _idx_by_label(c, "A")
    not_idx = _idx_by_element(c, "Not")
    out_y = _idx_by_label(c, "Y")

    assert g.has_edge(in_a, not_idx)
    assert g.has_edge(not_idx, out_y)


# Reachability

def test_input_output_helpers():
    c = parse_dig_file(str(SAMPLES_DIR / "full_adder.dig"))
    in_idxs = input_component_indices(c)
    out_idxs = output_component_indices(c)
    assert {c.components[i].label for i in in_idxs} == {"A", "B", "Cin"}
    assert {c.components[i].label for i in out_idxs} == {"Sum", "Cout"}


def test_full_adder_full_reachability():
    """Every input must reach every output in a full adder."""
    c = parse_dig_file(str(SAMPLES_DIR / "full_adder.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    out_idxs = set(output_component_indices(c))
    reach = reachable_outputs_from_inputs(c, g)
    for in_idx, outs in reach.items():
        assert outs == out_idxs


def test_tier1_minimal_full_reachability():
    """
    Every tier-1 sample must have all inputs reaching all
    outputs.
    """
    for f in glob.glob("data/sample_circuits/tier1_minimal/*.dig"):
        c = parse_dig_file(f)
        nl = build_netlist(c)
        g = build_signal_graph(c, nl)
        out_idxs = set(output_component_indices(c))
        if not out_idxs:
            continue
        reach = reachable_outputs_from_inputs(c, g)
        for in_idx, outs in reach.items():
            in_lbl = c.components[in_idx].label
            assert outs == out_idxs, (
                f"{f}: input {in_lbl} reaches {outs}, expected {out_idxs}")


def test_tier2_subcircuits_full_reachability():
    """
    Every input reaches every output in tier-2 parent circuits.
    """
    for fname in ("uses_subcircuit.dig", "two_subcircuits.dig", "uses_nested.dig"):
        c = parse_dig_file(str(TIER2_DIR / fname))
        nl = build_netlist(c)
        g = build_signal_graph(c, nl)
        out_idxs = set(output_component_indices(c))
        reach = reachable_outputs_from_inputs(c, g)
        for in_idx, outs in reach.items():
            assert outs == out_idxs, (
                f"{fname}: input {c.components[in_idx].label} "
                f"reaches {outs}, expected {out_idxs}")
            
TIER3_DIR = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier3_realistic"

def test_tier3_calculator_full_reachability():
    """
    tier3_calculator: 4 Ins (A, B, Ci, Op) x 4 Outs (Result, Carry,
    Zero, Bit0) = 16 input-output pairs, all reachable by design.
    """
    c = parse_dig_file(str(TIER3_DIR / "tier3_calculator.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    reach = reachable_outputs_from_inputs(c, g)

    expected_outs = {"Result", "Carry", "Zero", "Bit0"}
    for in_idx, outs in reach.items():
        in_lbl = c.components[in_idx].label
        out_lbls = {c.components[i].label for i in outs}
        assert out_lbls == expected_outs, (
            f"Input {in_lbl} reaches {out_lbls}, expected {expected_outs}"
        )

    total_pairs = sum(len(v) for v in reach.values())
    assert total_pairs == 16