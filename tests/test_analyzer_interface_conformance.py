"""F8 checkers"""

import glob
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.interface_conformance import check_interface_conformance
from dlc.analyzer.wire_completeness import IssueSeverity

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"


def test_dangling_subcircuit_input_fixture():
    c = parse_dig_file(
        str(SAMPLES / "tier2_bug" / "dangling_subcircuit_input.dig")
    )
    issues = check_interface_conformance(c)
    found = issues.by_kind("dangling_subcircuit_input")
    assert len(found) >= 1
    assert all(i.severity == IssueSeverity.ERROR for i in found)


def test_clean_tier2_structured_produces_no_interface_issues():
    for f in glob.glob("data/sample_circuits/tier2_structured/*.dig"):
        c = parse_dig_file(f)
        issues = check_interface_conformance(c)
        assert len(issues.issues) == 0, f"{f}: unexpected interface issues"


def test_clean_tier3_realistic_produces_no_interface_issues():
    for f in glob.glob("data/sample_circuits/tier3_realistic/*.dig"):
        c = parse_dig_file(f)
        issues = check_interface_conformance(c)
        assert len(issues.issues) == 0, f"{f}: unexpected interface issues"


def test_check_all_l1_deep_surfaces_child_bug_with_breadcrumb():
    from dlc.analyzer import check_all_l1_deep
    from dlc.parser.models import (
        Circuit, Component, SubcircuitReference, Position,
    )
    child_and = Component(
        element_name="And", position=Position(40, 0),
        attributes={"Inputs": 2, "wideShape": True},
    )
    child_in = Component(
        element_name="In", position=Position(0, 0),
        attributes={"Label": "X"}, label="X",
    )
    child_out = Component(
        element_name="Out", position=Position(120, 20),
        attributes={"Label": "Y"}, label="Y",
    )
    child = Circuit(
        components=[child_in, child_and, child_out], wires=[],
    )
    parent_inst = Component(
        element_name="child.dig", position=Position(0, 0), attributes={},
    )
    sub_ref = SubcircuitReference(
        reference="child.dig", parent_component=parent_inst,
        resolved_path="/fake/child.dig", child_circuit=child,
    )
    parent = Circuit(
        components=[parent_inst], wires=[], subcircuits=[sub_ref],
    )
    issues = check_all_l1_deep(parent)
    child_origin = [i for i in issues.issues if "[child.dig]" in i.title]
    assert len(child_origin) >= 1

def test_check_all_l1_deep_scopes_and_remaps_nested_issues():
    from pathlib import Path
    from dlc.analyzer import check_all_l1_deep
    from dlc.parser.dig_parser import parse_dig_file

    top_path = (
        Path(__file__).parent.parent / "data" / "sample_circuits"
        / "tier2_bug" / "L1_deep_check" / "L1_deep_top.dig"
    )
    top = parse_dig_file(str(top_path))
    issues = check_all_l1_deep(top).issues
    nested = [i for i in issues if i.scope]
    assert nested, "deep check should surface the nested bugs"

    inst_idx = next(
        k for k, comp in enumerate(top.components)
        if comp.element_name.endswith(".dig")
    )
    for i in nested:
        assert i.scope.startswith("L1_deep_middle.dig")
        assert i.component_indices == [inst_idx]
        assert i.title.startswith(f"[{i.scope}]")
