"""
F5 + missing subcircuit checker
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


def test_check_wire_completeness_does_not_crash_on_bug_sample():
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "dangling_input.dig"))
    issues = check_wire_completeness(c)
    assert isinstance(issues, IssueCollection)

def test_dangling_input_check_surfaces_one_issue_on_bug_sample():
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "dangling_input.dig"))
    issues = check_wire_completeness(c)
    dangling = issues.by_kind("dangling_input")
    assert len(dangling) == 1
    assert dangling[0].severity == IssueSeverity.ERROR
    assert "in1" in dangling[0].message
    assert dangling[0].location is not None


def test_multi_driver_check_surfaces_one_issue_on_bug_sample():
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "multi_driver.dig"))
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
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "unused_top_output.dig"))
    issues = check_wire_completeness(c)
    unused = issues.by_kind("unused_top_output")
    assert len(unused) == 1
    assert unused[0].severity == IssueSeverity.ERROR
    assert "Y_unused" in unused[0].message
    dangling = issues.by_kind("dangling_input")
    assert not any("Y_unused" in d.message for d in dangling)


def test_isolated_component_surfaces_one_issue_not_dangling():
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "isolated_component.dig"))
    issues = check_wire_completeness(c)
    iso = issues.by_kind("isolated_component")
    assert len(iso) == 1
    assert iso[0].severity == IssueSeverity.WARNING
    assert "And" in iso[0].title
    assert len(issues.by_kind("dangling_input")) == 0


def test_empty_tunnel_surfaces_only_lonely_tunnel_not_wired_one():
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "empty_tunnel.dig"))
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
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "dangling_input.dig"))
    dangling = check_wire_completeness(c).by_kind("dangling_input")
    assert dangling and dangling[0].net_id is not None

# Missing subcircuit checks

def test_missing_top_subcircuit_real_fixture():
    c = parse_dig_file(
        str(SAMPLES / "tier2_bug" / "missing_top_subcircuit.dig")
    )
    issues = check_wire_completeness(c)
    missing = issues.by_kind("missing_subcircuit")
    assert len(missing) == 1
    assert missing[0].severity == IssueSeverity.ERROR
    assert "ghost.dig" in missing[0].message
    assert "Nested" not in missing[0].title

def test_missing_nested_subcircuit_real_fixture():
    c = parse_dig_file(
        str(SAMPLES / "tier2_bug" / "missing_nested_subcircuit.dig")
    )
    issues = check_wire_completeness(c)
    missing = issues.by_kind("missing_subcircuit")
    assert len(missing) == 1
    assert missing[0].severity == IssueSeverity.ERROR
    assert "ghost2.dig" in missing[0].message
    assert "Nested" in missing[0].title

#cascade linking — undriven errors caused by a missing subcircuit fold
# into ONE follow-up note under the missing_subcircuit error. Linking runs in
# check_all_l1 (it must see every checker's issues), so these tests call that.

from dlc.analyzer import check_all_l1

def _elem(name, x, y, label=None, wide=False):
    entries = ""
    if label:
        entries += (
            "<entry><string>Label</string>"
            f"<string>{label}</string></entry>"
        )
    if wide:
        entries += "<entry><string>wideShape</string><boolean>true</boolean></entry>"
    return (
        f"<visualElement><elementName>{name}</elementName>"
        f"<elementAttributes>{entries}</elementAttributes>"
        f'<pos x="{x}" y="{y}"/></visualElement>'
    )


def _wire(x1, y1, x2, y2):
    return f'<wire><p1 x="{x1}" y="{y1}"/><p2 x="{x2}" y="{y2}"/></wire>'


def _write_cascade_parent(tmp_path):
    """In A -> And.in0; (missing ghost.dig).out -> And.in1; And -> Out Y.
    Plus an INDEPENDENT mistake: the second And's in1 has no wire at all."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><circuit><version>2</version>'
        "<attributes/><visualElements>"
        + _elem("In", 400, 300, label="A")
        + _elem("ghost.dig", 300, 340)
        + _elem("And", 480, 300, wide=True)
        + _elem("Out", 620, 320, label="Y")
        + _elem("In", 400, 500, label="B")
        + _elem("And", 480, 500, wide=True)
        + _elem("Out", 620, 520, label="Z")
        + "</visualElements><wires>"
        + _wire(400, 300, 480, 300)    # A -> And.in0
        + _wire(360, 340, 480, 340)    # ghost out -> And.in1 (undriven!)
        + _wire(560, 320, 620, 320)    # And.Y -> Out Y
        + _wire(400, 500, 480, 500)    # B -> And2.in0 (And2.in1 left unwired)
        + _wire(560, 520, 620, 520)    # And2.Y -> Out Z
        + "</wires><measurementOrdering/></circuit>"
    )
    p = tmp_path / "parent_cascade.dig"
    p.write_text(xml)
    return p


def test_cascade_dangling_input_folds_into_missing_subcircuit_note(tmp_path):
    c = parse_dig_file(str(_write_cascade_parent(tmp_path)))
    issues = check_all_l1(c)

    cascades = issues.by_kind("missing_subcircuit_cascade")
    assert len(cascades) == 1
    grp = cascades[0]
    assert grp.severity == IssueSeverity.WARNING
    assert "ghost.dig" in grp.title
    assert "And" in grp.message
    # highlights the missing instance (idx 1) AND the victim And (idx 2)
    assert 1 in grp.component_indices and 2 in grp.component_indices

    # the And.in2 dangling error was absorbed by the group...
    dangling = issues.by_kind("dangling_input")
    assert not any(2 in d.component_indices for d in dangling)
    # ...but the INDEPENDENT Xor.in2 mistake stays a separate ERROR.
    assert len(dangling) == 1
    assert dangling[0].severity == IssueSeverity.ERROR
    assert 5 in dangling[0].component_indices


def test_cascade_note_sits_directly_after_its_root_cause(tmp_path):
    c = parse_dig_file(str(_write_cascade_parent(tmp_path)))
    kinds = [i.kind for i in check_all_l1(c).issues]
    i = kinds.index("missing_subcircuit")
    assert kinds[i + 1] == "missing_subcircuit_cascade"


def test_cascade_absorbs_unused_top_output_on_real_fixture():
    """missing_top_subcircuit.dig: ghost.dig would drive Out Y, so the
    'Y is never driven' error is the cascade, not a separate mistake."""
    c = parse_dig_file(
        str(SAMPLES / "tier2_bug" / "missing_top_subcircuit.dig")
    )
    issues = check_all_l1(c)
    assert len(issues.by_kind("missing_subcircuit")) == 1   # root unchanged
    assert issues.by_kind("unused_top_output") == []        # absorbed
    cascades = issues.by_kind("missing_subcircuit_cascade")
    assert len(cascades) == 1
    assert "ghost.dig" in cascades[0].title
    assert "Y" in cascades[0].message


def test_no_cascade_linking_without_missing_subcircuit():
    c = parse_dig_file(str(SAMPLES / "tier1_bug" / "dangling_input.dig"))
    issues = check_all_l1(c)
    assert issues.by_kind("missing_subcircuit_cascade") == []
    assert len(issues.by_kind("dangling_input")) == 1       # untouched

def test_cascade_via_tunnel_sitting_on_missing_instance_pin(tmp_path):
    """cpu.dig pattern: a tunnel placed directly ON the missing child's
    output pin (wire-degree 0) teleports the undriven net far away. The
    tunnel-anchor-proximity rule must still attribute the cascade."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><circuit><version>2</version>'
        "<attributes/><visualElements>"
        + _elem("ghost.dig", 300, 300)
        + _elem("Tunnel", 460, 300, label=None).replace(
            "<elementAttributes>",
            "<elementAttributes><entry><string>NetName</string>"
            "<string>S</string></entry>",
        )
        + _elem("Tunnel", 800, 280, label=None).replace(
            "<elementAttributes>",
            "<elementAttributes><entry><string>NetName</string>"
            "<string>S</string></entry>",
        )
        + _elem("In", 700, 320, label="A")
        + _elem("And", 880, 280, wide=True)
        + _elem("Out", 1040, 300, label="Y")
        + "</visualElements><wires>"
        + _wire(800, 280, 880, 280)     # tunnel S -> And.in0 (undriven!)
        + _wire(700, 320, 880, 320)     # A -> And.in1
        + _wire(960, 300, 1040, 300)    # And.Y -> Out Y
        + "</wires><measurementOrdering/></circuit>"
    )
    p = tmp_path / "tunnel_cascade.dig"
    p.write_text(xml)
    issues = check_all_l1(parse_dig_file(str(p)))

    cascades = issues.by_kind("missing_subcircuit_cascade")
    assert len(cascades) == 1
    assert "ghost.dig" in cascades[0].title
    assert issues.by_kind("dangling_input") == []   # fully absorbed