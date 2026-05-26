"""Tests for dlc.facts.widths — per-pin bit-width helpers."""

from dlc.parser.models import Component, Position
from dlc.facts.width import pin_width


def _mk(element_name, **attrs):
    return Component(
        element_name=element_name,
        position=Position(0, 0),
        attributes=attrs,
        label=attrs.get("Label"),
    )


def test_in_default_one_bit():
    assert pin_width(_mk("In"), "out") == 1


def test_in_with_bits_attribute():
    assert pin_width(_mk("In", Bits=4), "out") == 4


def test_out_with_bits():
    assert pin_width(_mk("Out", Bits=8), "in") == 8


def test_const_with_bits():
    assert pin_width(_mk("Const", Bits=4, Value=0), "out") == 4


def test_clock_always_one_bit():
    assert pin_width(_mk("Clock"), "clk") == 1


def test_ground_default_one_bit():
    assert pin_width(_mk("Ground"), "out") == 1


def test_ground_with_bits():
    assert pin_width(_mk("Ground", Bits=4), "out") == 4


def test_and_gate_bitwise():
    assert pin_width(_mk("And", Bits=4, Inputs=2), "in0") == 4
    assert pin_width(_mk("And", Bits=4, Inputs=2), "Y") == 4


def test_not_gate():
    assert pin_width(_mk("Not", Bits=4), "A") == 4
    assert pin_width(_mk("Not", Bits=4), "Y") == 4



def test_add_data_pins_use_bits_carry_pins_one_bit():
    c = _mk("Add", Bits=4)
    assert pin_width(c, "a") == 4
    assert pin_width(c, "b") == 4
    assert pin_width(c, "s") == 4
    assert pin_width(c, "c_i") == 1
    assert pin_width(c, "c_o") == 1


def test_comparator_data_vs_output_pins():
    c = _mk("Comparator", Bits=4)
    assert pin_width(c, "A") == 4
    assert pin_width(c, "B") == 4
    assert pin_width(c, "gr") == 1
    assert pin_width(c, "eq") == 1
    assert pin_width(c, "le") == 1


def test_barrel_shifter_sh_width():
    assert pin_width(_mk("BarrelShifter", Bits=8), "in") == 8
    assert pin_width(_mk("BarrelShifter", Bits=8), "sh") == 4
    assert pin_width(_mk("BarrelShifter", Bits=32), "sh") == 6
    assert pin_width(_mk("BarrelShifter", Bits=1), "sh") == 1
    assert pin_width(_mk("BarrelShifter", Bits=2), "sh") == 1
    assert pin_width(_mk("BarrelShifter", Bits=4), "sh") == 3


def test_mux_data_pins_use_bits_sel_uses_selector_bits():
    c = _mk("Multiplexer", Bits=4, **{"Selector Bits": 2})
    assert pin_width(c, "in0") == 4
    assert pin_width(c, "in3") == 4
    assert pin_width(c, "out") == 4
    assert pin_width(c, "sel") == 2


def test_mux_default_2to1():
    c = _mk("Multiplexer", Bits=1)
    assert pin_width(c, "in0") == 1
    assert pin_width(c, "in1") == 1
    assert pin_width(c, "sel") == 1
    assert pin_width(c, "out") == 1


def test_decoder_sel_uses_selector_bits_outputs_one_bit():
    c = _mk("Decoder", **{"Selector Bits": 5})
    assert pin_width(c, "sel") == 5
    assert pin_width(c, "out_0") == 1
    assert pin_width(c, "out_31") == 1


def test_priority_encoder_inputs_one_bit_num_uses_selector_bits():
    c = _mk("PriorityEncoder", **{"Selector Bits": 3})
    assert pin_width(c, "in_0") == 1
    assert pin_width(c, "in_7") == 1
    assert pin_width(c, "num") == 3


def test_register_d_q_use_bits_c_en_one_bit():
    c = _mk("Register", Bits=8)
    assert pin_width(c, "D") == 8
    assert pin_width(c, "Q") == 8
    assert pin_width(c, "C") == 1
    assert pin_width(c, "en") == 1


def test_rom_pins():
    c = _mk("ROM", AddrBits=10, Bits=32)
    assert pin_width(c, "A") == 10
    assert pin_width(c, "sel") == 1
    assert pin_width(c, "D") == 32


def test_bitextender_pins():
    c = _mk("BitExtender", inputBits=4, outputBits=32)
    assert pin_width(c, "in") == 4
    assert pin_width(c, "out") == 32

def test_splitter_simple_4_to_1111():
    c = _mk("Splitter", **{"Input Splitting": "4", "Output Splitting": "1,1,1,1"})
    assert pin_width(c, "in0") == 4
    assert pin_width(c, "out0") == 1
    assert pin_width(c, "out1") == 1
    assert pin_width(c, "out2") == 1
    assert pin_width(c, "out3") == 1


def test_splitter_instruction_field():
    c = _mk("Splitter", **{
        "Input Splitting": "0-31",
        "Output Splitting": "25-31, 24-20, 19-15, 14-12, 11-7, 6-0",
    })
    assert pin_width(c, "in0") == 32
    assert pin_width(c, "out0") == 7
    assert pin_width(c, "out1") == 5
    assert pin_width(c, "out2") == 5
    assert pin_width(c, "out3") == 3
    assert pin_width(c, "out4") == 5
    assert pin_width(c, "out5") == 7


def test_splitter_out_of_range_returns_none():
    c = _mk("Splitter", **{"Input Splitting": "4", "Output Splitting": "1,1,1,1"})
    assert pin_width(c, "in1") is None
    assert pin_width(c, "out4") is None



def test_tunnel_returns_none():
    assert pin_width(_mk("Tunnel", NetName="A"), "net") is None


def test_subcircuit_instance_returns_none():
    assert pin_width(_mk("bool_unit.dig"), "A") is None


def test_annotation_elements_return_none():
    assert pin_width(_mk("Testcase"), "any") is None
    assert pin_width(_mk("Rectangle"), "any") is None


def test_unknown_element_returns_none():
    assert pin_width(_mk("MysteryElement"), "in") is None


def test_unknown_pin_on_known_element_returns_none():
    assert pin_width(_mk("Add", Bits=4), "fakepin") is None