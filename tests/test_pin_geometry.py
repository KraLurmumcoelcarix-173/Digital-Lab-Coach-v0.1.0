"""
Tests for pin_geometry.absolute_pin_positions.
"""

from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.models import Component, Position
from dlc.parser.pin_geometry import absolute_pin_positions, _rotate

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample_circuits" / "tier1_minimal"


def _mk(element_name: str, x=0, y=0, **attrs) -> Component:
    return Component(
        element_name=element_name,
        position=Position(x=x, y=y),
        attributes=attrs,
        label=attrs.get("Label"),
    )


# Static-table elements

def test_in_out_const_clock_tunnel_have_anchor_pin():
    for name, dir_ in [("In", "out"), ("Out", "in"), ("Const", "out"),
                       ("Clock", "out"), ("Tunnel", "bidir")]:
        positions = absolute_pin_positions(_mk(name, 100, 200))
        assert len(positions) == 1, name
        pos, spec = positions[0]
        assert (pos.x, pos.y) == (100, 200), name
        assert spec.direction == dir_, name


def test_ground_and_vdd_have_single_output_at_anchor():
    for name in ("Ground", "VDD"):
        positions = absolute_pin_positions(_mk(name, 50, 60))
        assert len(positions) == 1
        pos, spec = positions[0]
        assert (pos.x, pos.y) == (50, 60)
        assert spec.direction == "out"


def test_add_has_a_b_ci_s_co_pins():
    positions = absolute_pin_positions(_mk("Add", 100, 100, Bits=32))
    by_name = {spec.name: (pos.x, pos.y, spec.direction)
               for pos, spec in positions}
    assert by_name["a"] == (100, 100, "in")
    assert by_name["b"] == (100, 120, "in")
    assert by_name["c_i"] == (100, 140, "in")
    assert by_name["s"] == (160, 100, "out")
    assert by_name["c_o"] == (160, 120, "out")


def test_bitextender_has_in_and_out():
    positions = absolute_pin_positions(_mk("BitExtender", 0, 0, inputBits=1, outputBits=32))
    names = {spec.name for _, spec in positions}
    assert names == {"in", "out"}


def test_barrel_shifter_has_in_sh_out():
    positions = absolute_pin_positions(_mk("BarrelShifter", 0, 0, Bits=32))
    names = {spec.name for _, spec in positions}
    assert names == {"in", "sh", "out"}


def test_rom_has_a_sel_d():
    positions = absolute_pin_positions(_mk("ROM", 0, 0, AddrBits=3, Bits=8))
    names = {spec.name for _, spec in positions}
    assert names == {"A", "sel", "D"}


# Dynamic-table elements 


def test_nary_gate_wideshape_even_n_uses_middle_gap():
    """
    For wideShape=True with even N>=4, inputs split top/bottom with a
    40-unit gap in the middle. Verifies four_inputand.dig's layout.
    """
    pos = absolute_pin_positions(_mk("And", 0, 0, Inputs=4, wideShape=True))
    in_ys = sorted(p.y for p, spec in pos if spec.direction == "in")
    assert in_ys == [0, 20, 60, 80]


def test_multiplexer_2input_uses_spacing_40():
    """sel_bits=1 → 2 inputs spaced 40, sel at (20, 40), out at (40, 20)."""
    pos = absolute_pin_positions(_mk("Multiplexer", 0, 0))
    by_name = {spec.name: (p.x, p.y) for p, spec in pos}
    assert by_name["in0"] == (0, 0)
    assert by_name["in1"] == (0, 40)
    assert by_name["sel"] == (20, 40)
    assert by_name["out"] == (40, 20)


def test_multiplexer_4input_uses_spacing_20():
    """sel_bits=2 → 4 inputs spaced 20, sel at (20, 80), out at (40, 40)."""
    pos = absolute_pin_positions(_mk("Multiplexer", 0, 0, **{"Selector Bits": 2}))
    by_name = {spec.name: (p.x, p.y) for p, spec in pos}
    assert by_name["in0"] == (0, 0)
    assert by_name["in3"] == (0, 60)
    assert by_name["sel"] == (20, 80)
    assert by_name["out"] == (40, 40)


def test_decoder_layout():
    """Selector Bits=N → 2^N outputs spaced 20 on right edge; sel sits
    at (20, n*20), bottom-middle."""
    pos = absolute_pin_positions(_mk("Decoder", 0, 0, **{"Selector Bits": 5}))
    by_name = {spec.name: (p.x, p.y) for p, spec in pos}
    assert by_name["sel"] == (20, 640)
    assert by_name["out_0"] == (60, 0)
    assert by_name["out_31"] == (60, 620)


def test_priority_encoder_layout():
    pos = absolute_pin_positions(_mk("PriorityEncoder", 0, 0, **{"Selector Bits": 3}))
    in_count = sum(1 for _, spec in pos if spec.direction == "in")
    assert in_count == 8  # 2^3
    by_name = {spec.name: (p.x, p.y) for p, spec in pos}
    assert by_name["num"] == (80, 0)


def test_splitter_uses_splitterSpreading():
    """splitterSpreading=2 doubles the pin spacing from 20 to 40."""
    pos = absolute_pin_positions(_mk(
        "Splitter", 0, 0,
        **{"Input Splitting": "0-31",
           "Output Splitting": "25-31, 24-20, 19-15, 14-12, 11-7, 6-0",
           "splitterSpreading": 2}
    ))
    out_ys = sorted(p.y for p, spec in pos if spec.direction == "out")
    assert out_ys == [0, 40, 80, 120, 160, 200]


def test_register_pins_d_c_en_q():
    pos = absolute_pin_positions(_mk("Register", 0, 0, Bits=4))
    names = {spec.name for _, spec in pos}
    assert names == {"D", "C", "en", "Q"}


def test_comparator_outputs_at_x60_not_x80():
    pos = absolute_pin_positions(_mk("Comparator", 0, 0, Bits=4))
    by_name = {spec.name: (p.x, p.y) for p, spec in pos}
    assert by_name["gr"][0] == 60
    assert by_name["eq"][0] == 60
    assert by_name["le"][0] == 60


# Rotation

def test_rotation_function_180_degrees():
    assert _rotate(20, 40, 2) == (-20, -40)
    assert _rotate(0, 0, 2) == (0, 0)


def test_rotation_function_90_ccw():
    assert _rotate(20, 40, 1) == (40, -20)
    assert _rotate(40, 20, 1) == (20, -40)


def test_rotation_function_270():
    assert _rotate(20, 40, 3) == (-40, 20)


def test_rotation_applies_to_component():
    """A rotated Multiplexer's pins must be in their rotated positions."""
    rotated = _mk("Multiplexer", 100, 100, rotation=1)
    pos = absolute_pin_positions(rotated)
    by_name = {spec.name: (p.x, p.y) for p, spec in pos}
    assert by_name["in0"] == (100, 100)
    assert by_name["in1"] == (140, 100)
    assert by_name["sel"] == (140, 80)
    assert by_name["out"] == (120, 60)
