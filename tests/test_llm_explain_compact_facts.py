from pathlib import Path

from dlc.llm.explain import _compact_facts
from dlc.parser.dig_parser import parse_dig_file
from dlc.facts.extractor import extract_facts

_FIXTURES = Path("data/sample_circuits")


def test_compact_surfaces_io_bit_width_from_extractor_shape():
    facts = {"inputs": [{"label": "A", "bit_width": 4}],
             "outputs": [{"label": "Y", "bit_width": 8}]}
    compact = _compact_facts(facts)
    assert compact["inputs"][0] == {"label": "A", "bits": 4}
    assert compact["outputs"][0] == {"label": "Y", "bits": 8}


def test_compact_marks_resolved_and_carries_child_interface():
    facts = {"subcircuits": [{
        "reference": "alu.dig", "resolved_path": "/labs/alu.dig",
        "resolution_error": None,
        "child_inputs": [{"label": "A", "bit_width": 32}],
        "child_outputs": [{"label": "Result", "bit_width": 32}]}]}
    sub = _compact_facts(facts)["subcircuits"][0]
    assert sub["resolved"] is True
    assert sub["child_inputs"] == [{"label": "A", "bits": 32}]
    assert sub["child_outputs"] == [{"label": "Result", "bits": 32}]


def test_compact_marks_unresolved_subcircuit_false_with_error():
    facts = {"subcircuits": [{
        "reference": "missing.dig", "resolved_path": None,
        "resolution_error": "file not found"}]}
    sub = _compact_facts(facts)["subcircuits"][0]
    assert sub["resolved"] is False
    assert sub["resolution_error"] == "file not found"


def test_compact_still_reads_circuit_summary_fallback_shape():
    facts = {"inputs": [{"label": "A", "bits": 4}],
             "outputs": [{"label": "Y", "bits": 8}],
             "subcircuits": [{"reference": "x.dig", "resolved": True, "error": None}]}
    compact = _compact_facts(facts)
    assert compact["inputs"][0]["bits"] == 4
    assert compact["outputs"][0]["bits"] == 8
    assert compact["subcircuits"][0]["resolved"] is True


def test_real_fixture_has_no_null_io_widths_and_resolved_subcircuit():
    path = _FIXTURES / "tier2_structured" / "uses_subcircuit.dig"
    facts = extract_facts(parse_dig_file(str(path))).to_dict()
    compact = _compact_facts(facts)
    assert all(p["bits"] is not None for p in compact["inputs"]), compact["inputs"]
    assert all(p["bits"] is not None for p in compact["outputs"]), compact["outputs"]
    assert compact["subcircuits"], "fixture should reference a subcircuit"
    sub = compact["subcircuits"][0]
    assert sub["resolved"] is True
    assert sub.get("child_inputs") or sub.get("child_outputs")