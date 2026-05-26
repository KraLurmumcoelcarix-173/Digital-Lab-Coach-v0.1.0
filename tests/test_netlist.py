"""
Tests for netlist construction and subcircuit-instance pin direction resolution.
"""

from pathlib import Path
import glob

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist, NetList

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier1_minimal"
BUGGY_DIR  = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier1_buggy"
TIER2_DIR  = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier2_structured"



def test_netlist_builds_for_all_samples():
    for f in glob.glob("data/sample_circuits/**/*.dig", recursive=True):
        c = parse_dig_file(f)
        nl = build_netlist(c)
        assert isinstance(nl, NetList)


def test_netlist_summary_runs():
    c = parse_dig_file(str(SAMPLES_DIR / "full_adder.dig"))
    nl = build_netlist(c)
    s = nl.summary()
    assert "NetList:" in s


def test_netlist_single_and_has_connected_structure():
    c = parse_dig_file(str(SAMPLES_DIR / "single_and.dig"))
    nl = build_netlist(c)
    assert any(net.drivers() and net.sinks() for net in nl.nets)


def test_tier1_minimal_all_clean():
    """
    Every tier-1 minimal sample must have:
      - 0 undriven-with-pins nets
      - 0 multi-driver nets
    """
    for f in glob.glob("data/sample_circuits/tier1_minimal/*.dig"):
        c = parse_dig_file(f)
        nl = build_netlist(c)
        n_undriven = sum(1 for n in nl.nets if n.pins and not n.drivers())
        n_multi = sum(1 for n in nl.nets if len(n.drivers()) > 1)
        assert n_undriven == 0, f"{f}: {n_undriven} undriven-with-pins nets"
        assert n_multi == 0, f"{f}: {n_multi} multi-driver nets"


def test_netlist_single_and_exact_three_nets():
    """single_and: 3 logical nets (A→AND.in0, B→AND.in1, AND.Y→Out)."""
    c = parse_dig_file(str(SAMPLES_DIR / "single_and.dig"))
    nl = build_netlist(c)
    assert len(nl.nets) == 3
    assert all(n.drivers() for n in nl.nets)


def test_netlist_half_adder_exact_four_nets():
    c = parse_dig_file(str(SAMPLES_DIR / "half_adder.dig"))
    nl = build_netlist(c)
    assert len(nl.nets) == 4
    assert all(n.drivers() for n in nl.nets)


def test_netlist_full_adder_exact_eight_nets():
    """3 inputs + 1 XOR intermediate + 2 AND-OR intermediates + 2 outputs = 8."""
    c = parse_dig_file(str(SAMPLES_DIR / "full_adder.dig"))
    nl = build_netlist(c)
    assert len(nl.nets) == 8
    assert all(n.drivers() for n in nl.nets)


def test_wideshape_even_n_inputs_all_attached():
    """N=2/4/6 wideShape AND gates must attach every input pin."""
    cases = {"single_and.dig": 2, "four_inputand.dig": 4, "six_inputand.dig": 6}
    for fname, expected in cases.items():
        c = parse_dig_file(str(SAMPLES_DIR / fname))
        nl = build_netlist(c)
        and_idx = next(i for i, comp in enumerate(c.components)
                       if comp.element_name == "And")
        in_pins = [p for net in nl.nets for p in net.pins
                   if p.component_index == and_idx and p.direction == "in"]
        assert len(in_pins) == expected, (
            f"{fname}: expected {expected} AND inputs, got {len(in_pins)}")


def test_register_pins_all_attached_and_wired():
    """All 4 Register pins (D, C, en, Q) must live in real nets with siblings."""
    c = parse_dig_file(str(SAMPLES_DIR / "register_test.dig"))
    nl = build_netlist(c)
    reg_idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name == "Register")
    reg_pins = [p for net in nl.nets for p in net.pins
                if p.component_index == reg_idx]
    assert sorted(p.pin_name for p in reg_pins) == ["C", "D", "Q", "en"]
    for p in reg_pins:
        net = nl.net_at(p.x, p.y)
        assert net is not None and len(net.pins) >= 2, \
            f"Register pin {p.pin_name} is alone in a singleton net"


def test_tunnel_unifies_nets_across_gap():
    """Two same-named tunnels must collapse into one net."""
    c = parse_dig_file(str(SAMPLES_DIR / "tunnel_test.dig"))
    nl = build_netlist(c)
    net1_nets = [n for n in nl.nets if "net1" in n.tunnel_names]
    assert len(net1_nets) == 1


# Tier-1 buggy: each bug surfaces a distinct signature

def test_buggy_dangling_input_flags_one_undriven_singleton():
    """
    dangling_input.dig: AND.in1 has no wire. Expect exactly one net
    with a single sink pin and no driver — that's the dangling input.
    """
    c = parse_dig_file(str(BUGGY_DIR / "dangling_input.dig"))
    nl = build_netlist(c)
    undriven_with_only_sinks = [
        n for n in nl.nets
        if n.pins and not n.drivers() and all(p.direction == "in" for p in n.pins)
    ]
    assert len(undriven_with_only_sinks) == 1
    pin = undriven_with_only_sinks[0].pins[0]
    assert pin.element_name == "And"
    assert pin.pin_name == "in1"


def test_buggy_multi_driver_flags_one_net_with_two_drivers():
    """multi_driver.dig: In(A) and In(B) merged before AND.in0."""
    c = parse_dig_file(str(BUGGY_DIR / "multi_driver.dig"))
    nl = build_netlist(c)
    multi = [n for n in nl.nets if len(n.drivers()) > 1]
    assert len(multi) == 1
    drivers = multi[0].drivers()
    assert len(drivers) == 2
    assert all(d.element_name == "In" for d in drivers)


def test_buggy_combinational_loop_keeps_signal_path():
    """
    combinational_loop.dig: two NOTs forming a ring + driver. The
    netlist itself doesn't reject the loop (that's a graph-layer
    detection job); F2 just guarantees the build succeeds.
    """
    c = parse_dig_file(str(BUGGY_DIR / "combinational_loop.dig"))
    nl = build_netlist(c)
    assert len(nl.nets) > 0


def test_buggy_width_mismatch_netlist_is_structurally_clean():
    """
    width_mismatch.dig: 4-bit bus feeds 1-bit AND input. Parser doesn't
    track widths, it should produce a clean netlist.
    """
    c = parse_dig_file(str(BUGGY_DIR / "width_mismatch.dig"))
    nl = build_netlist(c)
    n_undriven = sum(1 for n in nl.nets if n.pins and not n.drivers())
    n_multi = sum(1 for n in nl.nets if len(n.drivers()) > 1)
    assert n_undriven == 0
    assert n_multi == 0


# Tier-2: subcircuit instance pin direction resolution 

def test_subcircuit_pins_get_child_io_labels():
    """
    uses_subcircuit instance pins should be named after the child's
    In/Out element Labels (A, B, Y) and have correct directions.
    """
    c = parse_dig_file(str(TIER2_DIR / "uses_subcircuit.dig"))
    nl = build_netlist(c)
    sub_idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name.endswith(".dig"))
    sub_pins = [p for net in nl.nets for p in net.pins
                if p.component_index == sub_idx]
    assert sorted(p.pin_name for p in sub_pins) == ["A", "B", "Y"]
    by_name = {p.pin_name: p for p in sub_pins}
    assert by_name["A"].direction == "in"
    assert by_name["B"].direction == "in"
    assert by_name["Y"].direction == "out"


def test_two_subcircuits_no_phantom_pins():
    """
    two_subcircuits.dig has wire L-bends near both subcircuit instances.
    The degree-1 filter must keep those bends out of the implicit-pin
    set. Result: 6 driven nets, no multi-drivers.
    """
    c = parse_dig_file(str(TIER2_DIR / "two_subcircuits.dig"))
    nl = build_netlist(c)
    assert len(nl.nets) == 6
    assert sum(1 for n in nl.nets if n.drivers()) == 6
    assert sum(1 for n in nl.nets if len(n.drivers()) > 1) == 0


def test_subcircuit_one_implicit_pin_per_net():
    """A subcircuit can have at most one implicit pin per net."""
    c = parse_dig_file(str(TIER2_DIR / "uses_subcircuit.dig"))
    nl = build_netlist(c)
    sub_idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name.endswith(".dig"))
    sub_pins = [p for net in nl.nets for p in net.pins
                if p.component_index == sub_idx]
    nets_with_sub = {nl.net_at(p.x, p.y).net_id for p in sub_pins}
    assert len(nets_with_sub) == len(sub_pins)


# Subcircuit implicit-pin cap 

TIER3_DIR = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier3_realistic"


def test_subcircuit_implicit_pin_count_capped_to_child_ports():
    """
    With IMPLICIT_PIN_RADIUS=500, a wide search can pull in unrelated
    wire endpoints far from the instance anchor (observed in
    tier3_calculator: bool_unit grabbed 6 pins for a 4-port child).
    The cap drops the farthest extras so the count matches the child's
    port count exactly.
    """
    c = parse_dig_file(str(TIER3_DIR / "tier3_calculator.dig"))
    nl = build_netlist(c)
    bu_idx = next(i for i, comp in enumerate(c.components)
                  if comp.element_name == "bool_unit.dig")
    child = c.subcircuits[next(
        i for i, s in enumerate(c.subcircuits)
        if s.reference == "bool_unit.dig"
    )].child_circuit
    expected_ports = len(child.inputs()) + len(child.outputs())
    bu_pins = [p for net in nl.nets for p in net.pins
               if p.component_index == bu_idx]
    assert len(bu_pins) == expected_ports
    pin_names = sorted(p.pin_name for p in bu_pins)
    assert pin_names == ["A", "B", "LogSel", "Result"]


def test_tier3_calculator_full_io_reachability():
    from dlc.parser.graph import (
        build_signal_graph,
        input_component_indices,
        output_component_indices,
        reachable_outputs_from_inputs,
    )
    c = parse_dig_file(str(TIER3_DIR / "tier3_calculator.dig"))
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    in_idxs = input_component_indices(c)
    out_idxs = set(output_component_indices(c))
    reach = reachable_outputs_from_inputs(c, g)
    for in_idx in in_idxs:
        assert reach[in_idx] == out_idxs


def test_tier3_bool_unit_fully_clean():
    c = parse_dig_file(str(TIER3_DIR / "bool_unit.dig"))
    nl = build_netlist(c)
    assert sum(1 for n in nl.nets if n.pins and not n.drivers()) == 0
    assert sum(1 for n in nl.nets if len(n.drivers()) > 1) == 0


# Shared-endpoint behavior (multi-pin same coord)

def test_two_pins_at_same_coord_share_net():
    """
    If two predicted pins land at the exact same coord (Decoder.sel
    and Multiplexer.in0 in register-file.dig do this), both must end
    up in the same net rather than one stealing the endpoint.

    Synthetic: an In at (0, 0) and a Register at (0, 0) both predict a
    pin at (0, 0). A short wire from (0, 0) to (60, 20) puts a real
    endpoint at (0, 0) so the snap algorithm sees it.
    """
    from dlc.parser.models import Circuit, Component, Position, Wire
    c = Circuit(
        format_version=2,
        components=[
            Component("In", Position(0, 0), {"Label": "X"}, label="X"),
            Component("Register", Position(0, 0), {"Bits": 1}),
            Component("Out", Position(60, 20), {"Label": "Y"}, label="Y"),
        ],
        wires=[
            Wire(Position(0, 20), Position(0, 20)),  
            Wire(Position(0, 0), Position(60, 20)),
        ],
        source_path="synthetic",
    )
    nl = build_netlist(c)
    net_at_origin = nl.net_at(0, 0)
    assert net_at_origin is not None
    pin_names = {(p.element_name, p.pin_name) for p in net_at_origin.pins}
    assert ("In", "out") in pin_names
    assert ("Register", "D") in pin_names

# Output pin co-located with a Tunnel (no wire between them).

def test_output_pin_at_tunnel_coord_joins_tunnel_net():
    from dlc.parser.models import Circuit, Component, Position, Wire
    c = Circuit(
        format_version=2,
        components=[
            Component("Clock", Position(0, 0), {"Label": "Clk"}, label="Clk"),
            Component("Tunnel", Position(0, 0), {"NetName": "clk"}),
            Component("Tunnel", Position(100, 20), {"NetName": "clk"}),
            Component("Register", Position(100, 0), {"Bits": 1}),
        ],
        wires=[],
        source_path="synthetic",
    )
    nl = build_netlist(c)
    clk_nets = [n for n in nl.nets if "clk" in n.tunnel_names]
    assert len(clk_nets) == 1, "the two clk tunnels must merge into one net"
    clk = clk_nets[0]
    drivers = clk.drivers()
    sinks = clk.sinks()
    assert any(d.element_name == "Clock" for d in drivers), (
        f"Clock should be a driver on the clk-tunnel net; "
        f"drivers got: {[(d.element_name, d.pin_name) for d in drivers]}"
    )
    assert any(s.element_name == "Register" and s.pin_name == "C" for s in sinks), (
        f"Register.C should be a sink on the clk-tunnel net; "
        f"sinks got: {[(s.element_name, s.pin_name) for s in sinks]}"
    )
    assert len(drivers) >= 1, (
        "the merged clk-tunnel net must have at least one driver "
        "(Clock) after the fix"
    )


    # OUT pin tolerance fallback to nearby tunnel coords.

def test_out_pin_attaches_to_offgrid_tunnel_within_tolerance():
    from dlc.parser.models import Circuit, Component, Position, Wire
    c = Circuit(
        format_version=2,
        components=[
            Component("Decoder", Position(-300, -760), {"Selector Bits": 5}),
            Component("Register", Position(140, -220), {"Bits": 32, "Label": "Reg 20"}, label="Reg 20"),
            Component("Tunnel", Position(-260, -360), {"NetName": "Enable 20"}),
            Component("Tunnel", Position(120, -180), {"NetName": "Enable 20"}),
        ],
        wires=[Wire(Position(120, -180), Position(140, -180))],
        source_path="synthetic",
    )
    nl = build_netlist(c)
    en_nets = [n for n in nl.nets if "Enable 20" in n.tunnel_names]
    assert len(en_nets) == 1
    drivers = en_nets[0].drivers()
    assert any(d.element_name == "Decoder" and d.pin_name == "out_20"
               for d in drivers), (
        f"Decoder out_20 should drive the Enable 20 net via tunnel-coord "
        f"fallback; drivers got: "
        f"{[(d.element_name, d.pin_name) for d in drivers]}"
    )


def test_out_pin_does_not_poach_already_claimed_tunnel():
    from dlc.parser.models import Circuit, Component, Position, Wire
    c = Circuit(
        format_version=2,
        components=[
            Component("Decoder", Position(-300, -760), {"Selector Bits": 5}),
            Component("Tunnel", Position(-240, -740), {"NetName": "Enable 1"}),
        ],
        wires=[Wire(Position(-260, -740), Position(-240, -740))],
        source_path="synthetic",
    )
    nl = build_netlist(c)
    en1 = [n for n in nl.nets if "Enable 1" in n.tunnel_names]
    assert len(en1) == 1
    drivers = en1[0].drivers()
    driver_names = sorted((d.element_name, d.pin_name) for d in drivers)
    assert driver_names == [("Decoder", "out_1")], (
        f"only out_1 (exact wire-endpoint match) should drive Enable 1; "
        f"got: {driver_names}"
    )


def test_out_element_at_splitter_out_coord_joins_net():
    from dlc.parser.models import Circuit, Component, Position, Wire
    c = Circuit(
        format_version=2,
        components=[
            Component("In", Position(-100, 0), {"Bits": 4, "Label": "X"}, label="X"),
            Component("Splitter", Position(0, 0),
                      {"Input Splitting": "4", "Output Splitting": "1,1,1,1"}),
            Component("Out", Position(20, 60), {"Label": "ALUOp1"}, label="ALUOp1"),
        ],
        wires=[Wire(Position(-100, 0), Position(0, 0))],
        source_path="synthetic",
    )
    nl = build_netlist(c)
    out_idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name == "Out")
    out_net = next(
        (n for n in nl.nets for p in n.pins if p.component_index == out_idx),
        None,
    )
    assert out_net is not None
    drivers = [(d.element_name, d.pin_name) for d in out_net.drivers()]
    assert ("Splitter", "out3") in drivers, (
        f"Out at (20, 60) should be driven by Splitter's out3 pin; "
        f"drivers got: {drivers}"
    )