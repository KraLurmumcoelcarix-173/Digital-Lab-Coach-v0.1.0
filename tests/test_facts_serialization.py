"""
Tests for dlc.facts.extractor's JSON serializer.
"""

import glob
import json
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.facts.extractor import extract_facts


SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample_circuits"

# to_dict

def _facts(rel_path: str):
    c = parse_dig_file(str(SAMPLES_DIR / rel_path))
    return extract_facts(c)

def test_to_dict_returns_dict_with_all_top_level_fields():
    f = _facts("tier1_minimal/single_and.dig")
    d = f.to_dict()
    assert isinstance(d, dict)
    expected = {
        "source_path", "format_version", "header", "inventory",
        "inputs", "outputs", "subcircuits", "components", "nets", "bugs",
    }
    assert set(d.keys()) == expected


def test_to_dict_header_matches_dataclass_field():
    f = _facts("tier1_minimal/full_adder.dig")
    assert f.to_dict()["header"] == f.header


def test_to_dict_inventory_matches_dataclass_field():
    f = _facts("tier1_minimal/full_adder.dig")
    assert f.to_dict()["inventory"] == f.inventory


def test_to_dict_io_facts_become_list_of_dicts():
    f = _facts("tier1_minimal/single_and.dig")
    d = f.to_dict()
    assert isinstance(d["inputs"], list)
    assert {io["label"] for io in d["inputs"]} == {"A", "B"}
    for io in d["inputs"]:
        assert {"index", "label", "bit_width", "position", "direction"} <= set(io.keys())


def test_to_dict_components_carry_topology():
    f = _facts("tier1_minimal/single_and.dig")
    d = f.to_dict()
    and_comp = next(c for c in d["components"] if c["element_name"] == "And")
    assert "predecessors" in and_comp and "successors" in and_comp
    assert isinstance(and_comp["predecessors"], list)


def test_to_dict_nets_carry_pin_dicts_with_pin_width():
    f = _facts("tier1_minimal/single_and.dig")
    d = f.to_dict()
    for net in d["nets"]:
        for pin in net["pins"]:
            assert {"component_index", "element_name", "pin_name",
                    "direction", "x", "y", "pin_width"} <= set(pin.keys())


def test_to_dict_subcircuit_hierarchy_round_trips():
    f = _facts("tier2_structured/uses_subcircuit.dig")
    d = f.to_dict()
    assert len(d["subcircuits"]) == 1
    sub = d["subcircuits"][0]
    assert sub["reference"] == "subcircuit_inner.dig"
    in_labels = {ci["label"] for ci in sub["child_inputs"]}
    assert in_labels == {"A", "B"}


def test_to_dict_bug_list_preserves_kind_and_detail():
    f = _facts("tier1_buggy/dangling_input.dig")
    d = f.to_dict()
    kinds = [b["kind"] for b in d["bugs"]]
    assert "dangling_input" in kinds
    dangling = next(b for b in d["bugs"] if b["kind"] == "dangling_input")
    assert "pins" in dangling["detail"]


# to_json

def test_to_json_returns_str_that_loads_back():
    f = _facts("tier1_minimal/single_and.dig")
    s = f.to_json()
    assert isinstance(s, str)
    parsed = json.loads(s)
    assert parsed["header"]["component_count"] == 5


def test_to_json_indented_form_has_newlines():
    f = _facts("tier1_minimal/single_and.dig")
    s = f.to_json(indent=2)
    assert "\n" in s
    assert "  " in s


def test_to_json_preserves_bug_count_in_buggy_circuit():
    f = _facts("tier1_buggy/multi_driver.dig")
    parsed = json.loads(f.to_json())
    assert parsed["header"]["bug_count"] >= 1
    assert any(b["kind"] == "multi_driver" for b in parsed["bugs"])


def test_to_json_round_trips_tier3_calculator():
    f = _facts("tier3_realistic/tier3_calculator.dig")
    parsed = json.loads(f.to_json())
    assert parsed["header"]["component_count"] == f.header["component_count"]
    assert parsed["header"]["net_count"] == f.header["net_count"]
    assert parsed["header"]["bug_count"] == 0


def test_to_json_round_trips_canonical_layer3_only_bug_case():
    f = _facts("30_buggy_benchmark/bug1_meaningless_mux_in3/Meaningless_mux_input/tier3_calculator.dig")
    parsed = json.loads(f.to_json())
    assert parsed["bugs"] == []
    assert parsed["header"]["subcircuit_count"] == 1


# Type-fidelity

def test_position_tuples_become_arrays_in_json():
    f = _facts("tier1_minimal/single_and.dig")
    parsed = json.loads(f.to_json())
    for io in parsed["inputs"]:
        assert isinstance(io["position"], list)
        assert len(io["position"]) == 2


def test_attribute_types_survive_round_trip():
    f = _facts("tier1_minimal/single_and.dig")
    parsed = json.loads(f.to_json())
    and_comp = next(c for c in parsed["components"] if c["element_name"] == "And")
    assert and_comp["attributes"].get("wideShape") is True


def test_comparator_bits_attribute_survives_as_int():
    f = _facts("tier1_minimal/comparator_4bit.dig")
    parsed = json.loads(f.to_json())
    cmp = next(c for c in parsed["components"] if c["element_name"] == "Comparator")
    assert cmp["attributes"]["Bits"] == 4
    assert isinstance(cmp["attributes"]["Bits"], int)


def test_none_fields_serialize_as_json_null():
    """A circuit's missing labels (Out without Label) round-trip as null."""
    f = _facts("tier1_buggy/combinational_loop.dig")
    parsed = json.loads(f.to_json())
    null_labels = [c for c in parsed["components"] if c["label"] is None]
    assert len(null_labels) >= 1


# Sweep

def test_every_sample_circuit_serializes_without_error():
    """No .dig in the repo should make to_json() raise."""
    for f_path in glob.glob("data/sample_circuits/**/*.dig", recursive=True):
        c = parse_dig_file(f_path)
        facts = extract_facts(c)
        try:
            s = facts.to_json()
            parsed = json.loads(s)
        except (TypeError, ValueError) as e:
            assert False, f"{f_path}: serialization failed: {e}"
        assert "header" in parsed
        assert "bugs" in parsed


def test_dict_and_json_carry_same_information():
    """to_dict() and to_json()->json.loads should give equivalent dicts
    (after tuple->list normalization)."""
    f = _facts("tier1_minimal/full_adder.dig")
    via_dict = f.to_dict()
    via_json = json.loads(f.to_json())
    # Normalize tuples to lists in via_dict so comparison is meaningful
    normalized = json.loads(json.dumps(via_dict))
    assert normalized == via_json