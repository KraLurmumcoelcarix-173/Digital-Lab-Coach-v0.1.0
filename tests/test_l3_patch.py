"""L3 circuit-patch applier (dlc/l3/patch.py).

The two flagship cases are SELF-VERIFYING fixes of seeded benchmark bugs:
apply the known-correct patch, rerun Digital per-row, and the previously
failing suite goes green — the exact Mode-A verify loop.
"""

import glob
import os
from pathlib import Path

import pytest

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.testing.runner import find_digital_jar
from dlc.l3.patch import apply_patch, rerun_with_patch

_BUG3 = "data/sample_circuits/30_bug_benchmark/bug3_wrong_cin/Wrong_cin.dig"
_BUG1 = "data/sample_circuits/30_bug_benchmark/bug1_meaningless_mux_in3/tier3_calculator.dig"
_AND = "data/sample_circuits/tier1_minimal/single_and.dig"

_needs_jar = pytest.mark.skipif(
    find_digital_jar() is None, reason="Digital.jar not configured",
)


def _mini_circuit(wires: str, extra_elements: str = "") -> str:
    """A tiny In-A/In-B/Comparator/Out circuit for pin-level op tests.
    Comparator pins: A@(200,0) B@(200,20); gr@(260,0)."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<circuit>
  <version>2</version>
  <attributes/>
  <visualElements>
    <visualElement>
      <elementName>In</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>A</string></entry>
      </elementAttributes>
      <pos x="0" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>In</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>B</string></entry>
      </elementAttributes>
      <pos x="0" y="20"/>
    </visualElement>
    <visualElement>
      <elementName>Comparator</elementName>
      <elementAttributes/>
      <pos x="200" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>Out</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>G</string></entry>
      </elementAttributes>
      <pos x="300" y="0"/>
    </visualElement>{extra_elements}
  </visualElements>
  <wires>
{wires}
  </wires>
  <measurementOrdering/>
</circuit>
"""


_SIMPLE_WIRES = """    <wire><p1 x="0" y="0"/><p2 x="200" y="0"/></wire>
    <wire><p1 x="0" y="20"/><p2 x="200" y="20"/></wire>
    <wire><p1 x="260" y="0"/><p2 x="300" y="0"/></wire>"""


def _edges(path):
    c = parse_dig_file(path)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    out = set()
    for u, v, d in g.edges(data=True):
        cu, cv = c.components[u], c.components[v]
        out.add((cu.label or cu.element_name, d["driver_pin"],
                 cv.label or cv.element_name, d["sink_pin"]))
    return out


# ---------------------------------------------------------------------------
# Individual ops (no jar needed)
# ---------------------------------------------------------------------------

def test_swap_pins_exchanges_the_feeding_wires(tmp_path):
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    before = _edges(str(src))
    assert ("A", "out", "Comparator", "A") in before
    assert ("B", "out", "Comparator", "B") in before

    temp, report = apply_patch(str(src), [
        {"op": "swap_pins", "component_index": 2, "pin_a": "A", "pin_b": "B"},
    ])
    assert report.ok, report.warning
    try:
        after = _edges(temp)
        assert ("A", "out", "Comparator", "B") in after
        assert ("B", "out", "Comparator", "A") in after
    finally:
        os.unlink(temp)


def test_rewire_pin_moves_a_sink_to_another_driver(tmp_path):
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "rewire_pin", "component_index": 2, "pin": "B",
         "to": {"component_index": 0, "pin": "out"}},
    ])
    assert report.ok, report.warning
    try:
        after = _edges(temp)
        assert ("A", "out", "Comparator", "B") in after       # rewired to A
        assert ("B", "out", "Comparator", "B") not in after
    finally:
        os.unlink(temp)


def test_swap_pins_refuses_a_shared_junction(tmp_path):
    # A second segment continues out of Comparator.A's coordinate, so the
    # pin sits on a junction — pin-level ops must refuse, not corrupt.
    wires = _SIMPLE_WIRES + """
    <wire><p1 x="200" y="0"/><p2 x="200" y="-40"/></wire>"""
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(wires), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "swap_pins", "component_index": 2, "pin_a": "A", "pin_b": "B"},
    ])
    assert temp is None and not report.ok
    assert "junction" in (report.warning or "")


def test_replace_element_keeps_attributes(tmp_path):
    temp, report = apply_patch(_AND, [
        {"op": "replace_element", "component_index": 2, "new_element": "Or"},
    ]) if parse_dig_file(_AND).components[2].element_name == "And" else (None, None)
    if temp is None:
        # locate the And's index robustly instead of assuming position
        c = parse_dig_file(_AND)
        idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name == "And")
        temp, report = apply_patch(_AND, [
            {"op": "replace_element", "component_index": idx, "new_element": "Or"},
        ])
    assert report.ok, report.warning
    try:
        patched = parse_dig_file(temp)
        assert any(comp.element_name == "Or" for comp in patched.components)
        assert not any(comp.element_name == "And" for comp in patched.components)
    finally:
        os.unlink(temp)


def test_change_attribute_preserves_existing_value_tag(tmp_path):
    extra = """
    <visualElement>
      <elementName>Const</elementName>
      <elementAttributes>
        <entry><string>Value</string><long>0</long></entry>
      </elementAttributes>
      <pos x="0" y="200"/>
    </visualElement>"""
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES, extra), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "change_attribute", "component_index": 4,
         "name": "Value", "value": 5},
    ])
    assert report.ok, report.warning
    try:
        text = Path(temp).read_text(encoding="utf-8")
        assert "<long>5</long>" in text            # Digital types Value as long
        patched = parse_dig_file(temp)
        assert patched.components[4].attributes["Value"] == 5
    finally:
        os.unlink(temp)


def test_change_attribute_creates_missing_entry_with_typed_tag(tmp_path):
    extra = """
    <visualElement>
      <elementName>Const</elementName>
      <elementAttributes/>
      <pos x="0" y="200"/>
    </visualElement>"""
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES, extra), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "change_attribute", "component_index": 4,
         "name": "Value", "value": 0},
    ])
    assert report.ok, report.warning
    try:
        text = Path(temp).read_text(encoding="utf-8")
        assert "<long>0</long>" in text
        assert parse_dig_file(temp).components[4].attributes["Value"] == 0
    finally:
        os.unlink(temp)


def test_add_and_delete_wire_roundtrip(tmp_path):
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "delete_wire", "p1": [0, 20], "p2": [200, 20]},
        {"op": "add_wire", "p1": [0, 20], "p2": [200, 20]},
    ])
    assert report.ok, report.warning
    try:
        assert _edges(temp) == _edges(str(src))
    finally:
        os.unlink(temp)


# ---------------------------------------------------------------------------
# Validation & guards
# ---------------------------------------------------------------------------

def test_unknown_op_and_bad_index_are_rejected(tmp_path):
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [{"op": "teleport"}])
    assert temp is None and "Unknown patch op" in report.warning
    temp, report = apply_patch(str(src), [
        {"op": "replace_element", "component_index": 99, "new_element": "Or"},
    ])
    assert temp is None and "out of range" in report.warning


def test_delete_missing_wire_is_rejected(tmp_path):
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "delete_wire", "p1": [1, 1], "p2": [2, 2]},
    ])
    assert temp is None and "no wire" in report.warning


def test_patch_introducing_l1_errors_is_rejected(tmp_path):
    # Shorting In A's net into In B's output creates a multi-driver net.
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "add_wire", "p1": [0, 20], "p2": [0, 0]},
    ])
    assert temp is None and not report.ok
    assert "new Layer-1" in report.warning
    assert "multi_driver" in report.new_l1_error_kinds
    leftovers = glob.glob(str(tmp_path / "dlc_row_l3fix_*.dig"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# Self-verifying fixes of seeded bugs (jar-gated — the Mode-A verify loop)
# ---------------------------------------------------------------------------

@_needs_jar
def test_bug3_fix_carry_const_makes_all_rows_pass():
    # Seeded bug: Const[16] (omitted Value -> 1) drives Add.c_i.
    # The fix: give it an explicit Value of 0.
    out = rerun_with_patch(_BUG3, [
        {"op": "change_attribute", "component_index": 16,
         "name": "Value", "value": 0},
    ])
    assert out.ok, out.warning
    assert out.all_passed is True, out.specs
    assert out.temp_path is None
    leftovers = glob.glob(
        "data/sample_circuits/30_bug_benchmark/bug3_wrong_cin/dlc_row_l3fix_*.dig"
    )
    assert leftovers == []


@_needs_jar
def test_bug3_unpatched_baseline_still_fails():
    # The self-verify contrast: a no-op patch must NOT make the suite pass.
    out = rerun_with_patch(_BUG3, [
        {"op": "change_attribute", "component_index": 16,
         "name": "Value", "value": 1},          # explicit 1 == the bug
    ])
    assert out.ok
    assert out.all_passed is False


@_needs_jar
def test_bug1_rewire_mux_in3_to_bool_unit_makes_all_rows_pass():
    # Seeded bug: mux[14].in3 tied to Ground[23]; correct wiring routes the
    # boolean unit's Result (already on in2) to in3 as well (Op=3 -> OR).
    out = rerun_with_patch(_BUG1, [
        {"op": "rewire_pin", "component_index": 14, "pin": "in3",
         "to": {"component_index": 9, "pin": "Result"}},
    ])
    assert out.ok, out.warning
    assert out.all_passed is True, out.specs

# ---------------------------------------------------------------------------
# add_component / delete_component
# ---------------------------------------------------------------------------

def test_add_component_wired_via_add_wire(tmp_path):
    # Insert a NOT between In B and Comparator.B: add the component, cut the
    # old wire, wire B -> Not.A and Not.Y -> Comparator.B at its pin offsets
    # (Not: A@anchor, Y@anchor+40).
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "add_component", "element_name": "Not",
         "position": [100, 60], "attributes": {"Bits": 1}},
        {"op": "delete_wire", "p1": [0, 20], "p2": [200, 20]},
        {"op": "add_wire", "p1": [0, 20], "p2": [100, 60]},
        {"op": "add_wire", "p1": [140, 60], "p2": [200, 20]},
    ])
    assert report.ok, report.warning
    try:
        patched = parse_dig_file(temp)
        assert patched.components[-1].element_name == "Not"   # appended at END
        assert patched.components[-1].attributes["Bits"] == 1
        after = _edges(temp)
        assert ("B", "out", "Not", "A") in after
        assert ("Not", "Y", "Comparator", "B") in after
    finally:
        os.unlink(temp)


def test_delete_component_removes_element_and_deadend_wire(tmp_path):
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "delete_component", "component_index": 3},     # the Out G
    ])
    assert report.ok, report.warning
    try:
        patched = parse_dig_file(temp)
        assert len(patched.components) == 3
        assert not any(c.element_name == "Out" for c in patched.components)
        assert len(patched.wires) == 2                        # gr->G wire gone
    finally:
        os.unlink(temp)


def test_delete_component_keeps_junction_wires(tmp_path):
    # Out G's pin coordinate is a junction (a second segment continues from
    # it): deleting G must not remove the shared routing.
    wires = _SIMPLE_WIRES + """
    <wire><p1 x="300" y="0"/><p2 x="300" y="100"/></wire>"""
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(wires), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "delete_component", "component_index": 3},
    ])
    assert report.ok, report.warning
    try:
        patched = parse_dig_file(temp)
        assert len(patched.wires) == 4                        # nothing removed
        assert not any(c.element_name == "Out" for c in patched.components)
    finally:
        os.unlink(temp)


def test_delete_component_breaking_the_circuit_is_rejected(tmp_path):
    # Deleting In A orphans Comparator.A -> NEW dangling_input -> guard trips.
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "delete_component", "component_index": 0},
    ])
    assert temp is None and not report.ok
    assert "new Layer-1" in report.warning
    assert "dangling_input" in report.new_l1_error_kinds


def test_deletes_apply_last_so_indices_stay_original(tmp_path):
    # Two isolated Consts [4] and [5]; delete [4] while changing [5]'s Value.
    # If deletion ran first, index 5 would shift and the change would miss.
    extra = """
    <visualElement>
      <elementName>Const</elementName>
      <elementAttributes>
        <entry><string>Value</string><long>1</long></entry>
      </elementAttributes>
      <pos x="0" y="200"/>
    </visualElement>
    <visualElement>
      <elementName>Const</elementName>
      <elementAttributes>
        <entry><string>Value</string><long>2</long></entry>
      </elementAttributes>
      <pos x="0" y="240"/>
    </visualElement>"""
    src = tmp_path / "mini.dig"
    src.write_text(_mini_circuit(_SIMPLE_WIRES, extra), encoding="utf-8")
    temp, report = apply_patch(str(src), [
        {"op": "delete_component", "component_index": 4},
        {"op": "change_attribute", "component_index": 5,
         "name": "Value", "value": 7},
    ])
    assert report.ok, report.warning
    try:
        patched = parse_dig_file(temp)
        consts = [c for c in patched.components if c.element_name == "Const"]
        assert len(consts) == 1
        assert consts[0].attributes["Value"] == 7
    finally:
        os.unlink(temp)