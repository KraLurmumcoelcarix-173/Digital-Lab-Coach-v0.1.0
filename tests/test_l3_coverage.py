"""Mode B deterministic engines (dlc/l3/coverage.py): the wrong-test
scan with its ratified honesty guard, and the coverage report.

Everything here is evaluator-only — no Digital.jar, no network.
"""

import json
from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.l3.coverage import (
    CircuitCoverage, MuxBranchCoverage, TreeCoverageReport,
    _build_notes, scan_circuit_coverage, scan_tree_coverage,
)

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"
_CALC = SAMPLES / "30_bug_benchmark" / "bug1_meaningless_mux_in3" / "tier3_calculator.dig"
_WRONG_CIN = SAMPLES / "30_bug_benchmark" / "bug3_wrong_cin" / "Wrong_cin.dig"


# ---------------------------------------------------------------------------
# Inline .dig builders (same convention as the wire-completeness tests)
# ---------------------------------------------------------------------------

def _elem(name, x, y, label=None, wide=False, extra_entries=""):
    entries = extra_entries
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


def _testcase(x, y, data):
    return (
        "<visualElement><elementName>Testcase</elementName>"
        "<elementAttributes><entry><string>Testdata</string>"
        f"<testData><dataString>{data}</dataString></testData>"
        f'</entry></elementAttributes><pos x="{x}" y="{y}"/></visualElement>'
    )


def _wire(x1, y1, x2, y2):
    return f'<wire><p1 x="{x1}" y="{y1}"/><p2 x="{x2}" y="{y2}"/></wire>'


def _circuit(elements, wires):
    return (
        '<?xml version="1.0" encoding="utf-8"?><circuit><version>2</version>'
        f"<attributes/><visualElements>{elements}</visualElements>"
        f"<wires>{wires}</wires><measurementOrdering/></circuit>"
    )


def _write_and_circuit(tmp_path, testdata, name="and_test.dig"):
    """In A -> And.in0, In B -> And.in1, And.Y -> Out Y, plus a testcase."""
    xml = _circuit(
        _elem("In", 400, 300, label="A")
        + _elem("In", 400, 340, label="B")
        + _elem("And", 480, 300, wide=True)
        + _elem("Out", 620, 320, label="Y")
        + _testcase(400, 200, testdata),
        _wire(400, 300, 480, 300)
        + _wire(400, 340, 480, 340)
        + _wire(560, 320, 620, 320),
    )
    p = tmp_path / name
    p.write_text(xml)
    return p


# ---------------------------------------------------------------------------
# 2.1 wrong-test scan
# ---------------------------------------------------------------------------

def test_clean_circuit_produces_no_flags_and_full_coverage():
    r = scan_tree_coverage(str(SAMPLES / "tier1_minimal" / "single_and.dig"))
    assert r.total_flags == 0
    c = r.circuits[0]
    assert c.has_testcases
    assert c.specs[0].checked_cells == 4
    assert c.specs[0].unresolved_cells == 0
    assert c.input_space_pct == 100.0
    assert any("4 of 4" in n for n in c.notes)


def test_wrong_test_row_is_flagged_with_both_values(tmp_path):
    # Row 3 asserts 1&1 = 0 — the classic testcase typo.
    p = _write_and_circuit(tmp_path, "A B Y\n0 0 0\n0 1 0\n1 0 0\n1 1 0")
    r = scan_tree_coverage(str(p))
    assert r.total_flags == 1
    f = r.circuits[0].flags[0]
    assert f.column == "Y"
    assert f.row_index == 3
    assert f.asserted == 0 and f.computed == 1
    assert "Y" in f.describe() and "row 3" in f.describe()
    scan = r.circuits[0].specs[0]
    assert scan.checked_cells == 4 and scan.mismatched_cells == 1


def test_honesty_guard_never_accuses_unresolved_outputs(tmp_path):
    # Out Z has no driver at all, so the evaluator can never resolve it;
    # every asserted Z cell must be counted unresolved and NEVER flagged.
    xml = _circuit(
        _elem("In", 400, 300, label="A")
        + _elem("Out", 560, 300, label="Y")
        + _elem("Out", 560, 400, label="Z")
        + _testcase(400, 200, "A Y Z\n0 0 1\n1 1 1"),
        _wire(400, 300, 560, 300),   # A -> Y direct; Z left undriven
    )
    p = tmp_path / "guard.dig"
    p.write_text(xml)
    r = scan_tree_coverage(str(p))
    assert r.total_flags == 0                      # nothing accused
    scan = r.circuits[0].specs[0]
    assert scan.unresolved_cells == 2              # the two Z cells
    assert scan.checked_cells == 2                 # the two Y cells


def test_seeded_bug_fixture_flags_exactly_its_failing_rows():
    r = scan_tree_coverage(str(_CALC))
    assert r.total_flags > 0
    top = r.circuits[0]
    assert {f.row_index for f in top.flags} == {6, 11}   # bug1's failing rows
    assert all(f.file == "tier3_calculator.dig" for f in top.flags)


def test_sequential_circuit_is_replayed_with_clock_edges():
    r = scan_tree_coverage(str(_WRONG_CIN))
    c = r.circuits[0]
    assert c.clock_edge_rows == 4
    assert len(c.flags) == 4                        # bug3: every row's Sum off
    assert all(f.column == "Sum" for f in c.flags)


# ---------------------------------------------------------------------------
# Tree walk
# ---------------------------------------------------------------------------

def test_tree_walk_includes_children_and_marks_untested_ones():
    r = scan_tree_coverage(str(_CALC))
    assert [c.file for c in r.circuits] == ["tier3_calculator.dig", "bool_unit.dig"]
    child = r.circuits[1]
    assert not child.has_testcases
    assert child.row_count == 0
    assert any("no embedded testcase" in n for n in child.notes)


def test_unparseable_root_reports_a_note_not_a_crash(tmp_path):
    p = tmp_path / "broken.dig"
    p.write_text("this is not xml")
    r = scan_tree_coverage(str(p))
    assert r.circuits == []
    assert r.total_flags == 0
    assert any("could not parse" in n for n in r.notes)


# ---------------------------------------------------------------------------
# 2.2 coverage metrics
# ---------------------------------------------------------------------------

def test_input_coverage_missing_values_constants_and_untested_inputs(tmp_path):
    # A only ever 0; B never appears in any testcase column at all.
    p = _write_and_circuit(tmp_path, "A Y\n0 0\n0 0")
    c = scan_tree_coverage(str(p)).circuits[0]
    by_label = {ic.label: ic for ic in c.inputs}
    a, b = by_label["A"], by_label["B"]
    assert a.in_testcases and a.missing_values == [1] and a.constant_value == 0
    assert not b.in_testcases
    assert any("'B' never appears" in n for n in c.notes)
    assert any("'A' (1-bit) is never tested with value 1" in n for n in c.notes)


def test_output_only_ever_asserted_constant_is_noted(tmp_path):
    p = _write_and_circuit(tmp_path, "A B Y\n0 0 0\n0 1 0\n1 0 0")
    c = scan_tree_coverage(str(p)).circuits[0]
    y = next(oc for oc in c.outputs if oc.label == "Y")
    assert y.asserted_cells == 3 and y.constant_value == 0
    assert any("only ever expected to be 0" in n for n in c.notes)


def test_mux_branch_coverage_is_observed_from_resolved_selects():
    circuit = parse_dig_file(str(_CALC))
    c = scan_circuit_coverage(circuit, display="calc")
    assert c.mux_branches, "calculator has muxes"
    for mb in c.mux_branches:
        assert mb.arms_total == 1 << mb.selector_bits
        assert set(mb.arms_taken) | set(mb.arms_missing) == set(range(mb.arms_total))
        assert set(mb.arms_taken).isdisjoint(mb.arms_missing)
    # the calculator's 12 rows exercise every op, so its op muxes are covered
    assert any(mb.arms_missing == [] and mb.arms_taken for mb in c.mux_branches)


def test_missing_mux_arm_produces_a_note():
    cov = CircuitCoverage(file="x.dig", path=None, has_testcases=True)
    cov.mux_branches.append(MuxBranchCoverage(
        component_index=14, selector_bits=2, arms_total=4,
        arms_taken=[0, 1], arms_missing=[2, 3],
    ))
    _build_notes(cov)
    assert any("Multiplexer[14]" in n and "2, 3" in n for n in cov.notes)


def test_input_space_pct_and_distinct_vectors(tmp_path):
    # 3 distinct vectors over a 2-input (4-vector) space, one duplicated row.
    p = _write_and_circuit(tmp_path, "A B Y\n0 0 0\n0 1 0\n1 1 1\n1 1 1")
    c = scan_tree_coverage(str(p)).circuits[0]
    assert c.row_count == 4
    assert c.distinct_vectors == 3
    assert c.input_space == 4
    assert c.input_space_pct == 75.0


def test_report_round_trips_through_json():
    r = scan_tree_coverage(str(SAMPLES / "tier1_minimal" / "single_and.dig"))
    blob = json.dumps(r.to_dict())
    back = json.loads(blob)
    assert back["root"] == "single_and.dig"
    assert back["total_flags"] == 0
    assert back["circuits"][0]["specs"][0]["checked_cells"] == 4
    assert isinstance(TreeCoverageReport(root="x"), TreeCoverageReport)

# ---------------------------------------------------------------------------
# 07-17: replay helper + manifest-aware note filter
# ---------------------------------------------------------------------------

def test_replay_appended_rows_agrees_and_disagrees():
    from dlc.l3.coverage import replay_appended_rows
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs
    p = "data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig"
    spec = extract_test_specs(parse_dig_file(p))[0]
    # two-stage pipe, post-edge asserts: appended row N sees the sum fed
    # two rows earlier. 5+5 lands two rows later; the final row wrongly
    # claims 10 again when the pipe has already flushed to 0+0.
    v = replay_appended_rows(p, spec.name, ["5 5 C 0", "0 0 C 10", "0 0 C 10"])
    assert [x["verdict"] for x in v] == ["agrees", "agrees", "disagrees"]
    assert "Sum" in v[2]["detail"]


def test_manifest_note_filter_rewrites_undefined_value_notes():
    from dlc.l3.coverage import _manifest_note_filter
    cats = [{"name": "AND", "when": {"ALUOp": "0b0000"}},
            {"name": "ADD", "when": {"ALUOp": "0b0010"}}]
    notes = [
        "input 'ALUOp' (4-bit) is never tested with values 9, 10, 11.",
        "input 'Other' (4-bit) is never tested with values 9, 10.",
        "output 'FlagZ' is only ever expected to be 1 — no row checks it "
        "at another value.",
    ]
    out = _manifest_note_filter(notes, cats, categories_complete=True)
    assert "not part of this lab's instruction set" in out[0]
    assert out[1] == notes[1]                     # non-category column: kept
    assert "consistent with this lab's instruction set" in out[2]
    # a value INSIDE the defined set keeps the original honest note
    notes2 = ["input 'ALUOp' (4-bit) is never tested with values 0, 9."]
    out2 = _manifest_note_filter(notes2, cats, categories_complete=False)
    assert out2 == notes2


# ---------------------------------------------------------------------------
# mux arm drivers — deterministic wiring truth per select arm
# ---------------------------------------------------------------------------

def test_mux_arm_drivers_name_the_selected_components():
    report = scan_tree_coverage(str(_CALC))
    c = report.circuits[0]
    assert c.mux_branches
    named = [mb for mb in c.mux_branches if mb.arm_drivers]
    assert named, "at least one mux should have identifiable arm drivers"
    for mb in named:
        for arm, desc in mb.arm_drivers.items():
            assert 0 <= arm < mb.arms_total
            assert isinstance(desc, str) and desc     # "ElementName 'Label'"
    # the report survives JSON round-tripping (int keys become strings)
    json.dumps(report.to_dict())


def test_sevenseg_manifest_grades_display_digits(monkeypatch):
    """The shipped sevenseg manifest proves category coverage is not
    RISC-V-specific: digits are categories, holds are a category."""
    disp = SAMPLES / "tier3_realistic" / "tier3_latched_display.dig"
    report = scan_tree_coverage(str(disp))
    c = report.circuits[0]
    assert c.categories_total == 17            # 16 digits + hold
    assert set(c.categories_touched) == {"digit_5", "digit_A", "hold"}
    assert len(c.categories_missing) == 14
    assert any("category gap" in n for n in c.notes)
