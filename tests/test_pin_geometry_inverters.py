"""Inverter-bubble (`inverterConfig`) support: negated gate inputs are exposed
as an explicit `inverted` flag on PinSpec and via inverted_input_names() — not
just the implicit -20 x-shift — so the Layer-1 UI and the future simulator can
render the bubble and apply the negation."""

from dlc.parser.models import Component, Position
from dlc.parser.pin_geometry import absolute_pin_positions, inverted_input_names


def _gate(element="And", **attrs):
    return Component(element_name=element, position=Position(200, 100),
                     attributes=attrs, label=None)


def test_inverted_input_names_maps_one_indexed_In_to_zero_indexed_in():
    assert inverted_input_names(_gate(inverterConfig=["In_1", "In_3"])) == ["in0", "in2"]


def test_inverted_input_names_empty_without_config():
    assert inverted_input_names(_gate()) == []
    assert inverted_input_names(_gate(inverterConfig=[])) == []


def test_inverted_input_names_only_for_gates():
    mux = Component(element_name="Multiplexer", position=Position(0, 0),
                    attributes={"inverterConfig": ["In_1"]}, label=None)
    assert inverted_input_names(mux) == []


def test_pinspec_marks_inverted_input_wide_even():
    g = _gate(wideShape=True, inverterConfig=["In_1"])   # top input negated
    by_name = {s.name: s for _p, s in absolute_pin_positions(g)}
    assert by_name["in0"].inverted is True
    assert by_name["in1"].inverted is False
    assert by_name["Y"].inverted is False
    pos = {s.name: (p.x, p.y) for p, s in absolute_pin_positions(g)}
    assert pos["in0"][0] == 180   # bubble still shifts x by -20 (200 - 20)


def test_pinspec_marks_inverted_input_non_wide():
    g = _gate(element="NAnd", Inputs=3, inverterConfig=["In_2"])   # middle negated
    by_name = {s.name: s for _p, s in absolute_pin_positions(g)}
    assert by_name["in1"].inverted is True
    assert by_name["in0"].inverted is False
    assert by_name["in2"].inverted is False