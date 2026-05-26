"""F6 checker"""

import glob
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.bit_widths import check_bit_widths
from dlc.analyzer.wire_completeness import IssueSeverity

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"


def test_width_mismatch_buggy_sample_surfaces_issue():
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "width_mismatch.dig"))
    issues = check_bit_widths(c)
    rel = issues.by_kind("width_mismatch") + issues.by_kind("width_conflict")
    assert len(rel) >= 1
    assert any(i.severity == IssueSeverity.ERROR for i in rel)


def test_clean_tier1_minimal_produces_no_width_issues():
    for f in glob.glob("data/sample_circuits/tier1_minimal/*.dig"):
        c = parse_dig_file(f)
        issues = check_bit_widths(c)
        assert len(issues.issues) == 0, f"{f}: unexpected width issues"


def test_clean_tier3_realistic_produces_no_width_issues():
    for f in glob.glob("data/sample_circuits/tier3_realistic/*.dig"):
        c = parse_dig_file(f)
        issues = check_bit_widths(c)
        assert len(issues.issues) == 0, f"{f}: unexpected width issues"


def test_run_all_l1_aggregator_includes_width_issues():
    from dlc.analyzer import check_all_l1
    c = parse_dig_file(str(SAMPLES / "tier1_buggy" / "width_mismatch.dig"))
    issues = check_all_l1(c)
    rel = issues.by_kind("width_mismatch") + issues.by_kind("width_conflict")
    assert len(rel) >= 1

def test_barrel_shifter_sh_oversize_source_is_not_flagged():
    from dlc.parser.models import Circuit, Component, Position, Wire
    in_c = Component(
        element_name="In", position=Position(0, 0),
        attributes={"Bits": 4, "Label": "A"}, label="A",
    )
    bs = Component(
        element_name="BarrelShifter", position=Position(100, 0),
        attributes={"Bits": 2}, label=None,
    )
    wires = [
        Wire(p1=Position(0, 0), p2=Position(100, 0)),       
        Wire(p1=Position(0, 0), p2=Position(0, 40)),        
        Wire(p1=Position(0, 40), p2=Position(100, 40)),      
    ]
    c = Circuit(components=[in_c, bs], wires=wires)
    issues = check_bit_widths(c)
    assert not any(
        i.kind == "width_mismatch" and "sh" in i.title
        for i in issues.issues
    ), f"unexpected sh width_mismatch: {[i.title for i in issues.issues]}"