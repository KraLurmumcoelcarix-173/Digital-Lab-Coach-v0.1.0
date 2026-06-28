"""extract_facts exposes per-gate inverted input pins (the inverter bubble) so
the Layer-1 UI and the future simulator can use them. Not surfaced into Layer 2
(it's rarely used and stays out of the compact prompt)."""

from dlc.parser.dig_parser import parse_dig_file
from dlc.facts.extractor import extract_facts

_DIG = "data/sample_circuits/tier1_minimal/negated_input_and.dig"


def test_component_fact_lists_inverted_inputs():
    facts = extract_facts(parse_dig_file(_DIG))
    and_fact = next(cf for cf in facts.components if cf.element_name == "And")
    in_fact = next(cf for cf in facts.components if cf.element_name == "In")
    assert and_fact.inverted_inputs == ["in0"]
    assert in_fact.inverted_inputs == []


def test_to_dict_round_trips_inverted_inputs():
    d = extract_facts(parse_dig_file(_DIG)).to_dict()
    and_fact = next(cf for cf in d["components"] if cf["element_name"] == "And")
    assert and_fact["inverted_inputs"] == ["in0"]