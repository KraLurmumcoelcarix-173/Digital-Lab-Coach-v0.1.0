"""
Layer 1 checker
"""

import json
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection, check_wire_completeness,
)

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"


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