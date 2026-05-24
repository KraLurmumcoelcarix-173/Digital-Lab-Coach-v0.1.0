"""
Layer 1 basic checker
"""

import json
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection, check_wire_completeness,
)

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"

# Wire completeness checkers

def test_issue_to_dict_serializes_severity_as_string():
    issue = Issue(
        kind="dangling_input",
        severity=IssueSeverity.ERROR,
        title="Undriven input pin",
        message="And[5].in1 has no wire.",
        component_indices=[5],
        location=(120, 40),
        suggested_fix="Connect a wire to And[5].in1.",
    )
    d = issue.to_dict()
    assert d["kind"] == "dangling_input"
    assert d["severity"] == "error"
    assert d["component_indices"] == [5]


def test_issue_collection_filters_by_severity_and_kind():
    c = IssueCollection()
    c.add(Issue(kind="a", severity=IssueSeverity.ERROR,   title="t", message="m"))
    c.add(Issue(kind="b", severity=IssueSeverity.WARNING, title="t", message="m"))
    c.add(Issue(kind="a", severity=IssueSeverity.INFO,    title="t", message="m"))
    assert len(c.errors()) == 1
    assert len(c.warnings()) == 1
    assert len(c.infos()) == 1
    assert len(c.by_kind("a")) == 2


def test_issue_collection_summary_format():
    c = IssueCollection()
    c.add(Issue(kind="x", severity=IssueSeverity.ERROR, title="t", message="m"))
    s = c.summary()
    assert "1 issues" in s and "1 errors" in s


def test_issue_collection_to_json_serializes_cleanly():
    c = IssueCollection()
    c.add(Issue(
        kind="multi_driver", severity=IssueSeverity.ERROR,
        title="t", message="m", component_indices=[1, 2],
    ))
    parsed = json.loads(c.to_json())
    assert parsed["issues"][0]["kind"] == "multi_driver"
    assert parsed["issues"][0]["severity"] == "error"


def test_check_wire_completeness_returns_empty_collection_on_clean_circuit():
    c = parse_dig_file(str(SAMPLES / "tier1_minimal" / "single_and.dig"))
    issues = check_wire_completeness(c)
    assert isinstance(issues, IssueCollection)
    # Stage 1: no checks wired yet -> empty.
    assert issues.issues == []


def test_check_wire_completeness_does_not_crash_on_buggy_sample():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "dangling_input.dig"))
    issues = check_wire_completeness(c)
    assert isinstance(issues, IssueCollection)

def test_dangling_input_check_surfaces_one_issue_on_buggy_sample():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "dangling_input.dig"))
    issues = check_wire_completeness(c)
    dangling = issues.by_kind("dangling_input")
    assert len(dangling) == 1
    assert dangling[0].severity == IssueSeverity.ERROR
    assert "in1" in dangling[0].message
    assert dangling[0].location is not None


def test_multi_driver_check_surfaces_one_issue_on_buggy_sample():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "multi_driver.dig"))
    issues = check_wire_completeness(c)
    multi = issues.by_kind("multi_driver")
    assert len(multi) == 1
    assert multi[0].severity == IssueSeverity.ERROR


def test_missing_subcircuit_check_uses_inmemory_circuit():
    from dlc.parser.models import (
        Circuit, Component, SubcircuitReference, Position,
    )
    comp = Component(
        element_name="bogus.dig", position=Position(0, 0),
        attributes={}, label=None,
    )
    sub_ref = SubcircuitReference(
        reference="bogus.dig", parent_component=comp,
        resolved_path=None, child_circuit=None,
        resolution_error="Referenced file not found: bogus.dig",
    )
    c = Circuit(components=[comp], wires=[], subcircuits=[sub_ref])
    issues = check_wire_completeness(c)
    missing = issues.by_kind("missing_subcircuit")
    assert len(missing) == 1
    assert missing[0].severity == IssueSeverity.ERROR
    assert "bogus.dig" in missing[0].message


def test_clean_tier1_minimal_produces_no_stage2_issues():
    import glob
    for f in glob.glob("data/sample_circuits/tier1_minimal/*.dig"):
        c = parse_dig_file(f)
        issues = check_wire_completeness(c)
        for kind in ("dangling_input", "multi_driver", "missing_subcircuit"):
            assert not issues.by_kind(kind), f"{f}: unexpected {kind}"

def test_unused_top_output_surfaces_one_issue_not_dangling():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "unused_top_output.dig"))
    issues = check_wire_completeness(c)
    unused = issues.by_kind("unused_top_output")
    assert len(unused) == 1
    assert unused[0].severity == IssueSeverity.ERROR
    assert "Y_unused" in unused[0].message
    dangling = issues.by_kind("dangling_input")
    assert not any("Y_unused" in d.message for d in dangling)


def test_isolated_component_surfaces_one_issue_not_dangling():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "isolated_component.dig"))
    issues = check_wire_completeness(c)
    iso = issues.by_kind("isolated_component")
    assert len(iso) == 1
    assert iso[0].severity == IssueSeverity.WARNING
    assert "And" in iso[0].title
    assert len(issues.by_kind("dangling_input")) == 0


def test_empty_tunnel_surfaces_only_lonely_tunnel_not_wired_one():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "empty_tunnel.dig"))
    issues = check_wire_completeness(c)
    empty = issues.by_kind("empty_tunnel")
    assert len(empty) == 1
    assert empty[0].severity == IssueSeverity.WARNING
    assert empty[0].location == (460, 320)


def test_clean_tier1_minimal_produces_no_stage3_issues():
    import glob
    for f in glob.glob("data/sample_circuits/tier1_minimal/*.dig"):
        c = parse_dig_file(f)
        issues = check_wire_completeness(c)
        for kind in ("unused_top_output", "isolated_component", "empty_tunnel"):
            assert not issues.by_kind(kind), f"{f}: unexpected {kind}"

def test_dangling_input_issue_carries_net_id_for_llm_consumption():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "dangling_input.dig"))
    dangling = check_wire_completeness(c).by_kind("dangling_input")
    assert dangling and dangling[0].net_id is not None