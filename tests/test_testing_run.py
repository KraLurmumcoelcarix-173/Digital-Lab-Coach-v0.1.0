"""
Tests for dlc.testing.run — TestSpec×TestRunResults join.
"""

from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs
from dlc.testing.results import parse_cli_output, TestRunResults
from dlc.testing.run import (
    TestRun, PerRowResult, join_test_runs,
)

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample_circuits"


def test_join_with_matching_cli_result():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    assert len(specs) == 1
    fallback_name = specs[0].name
    results = parse_cli_output(f"{fallback_name}: passed\n")
    runs = join_test_runs(specs, results, c)
    assert len(runs) == 1
    run = runs[0]
    assert run.has_result
    assert run.passed
    assert run.result.status == "passed"


def test_join_with_no_cli_result():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    runs = join_test_runs(specs, None, c)
    assert len(runs) == 1
    assert not runs[0].has_result
    assert not runs[0].passed
    assert runs[0].bindings


def test_join_with_mismatched_name():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    results = parse_cli_output("totally_different_testcase: passed\n")
    runs = join_test_runs(specs, results, c)
    assert len(runs) == 1
    assert not runs[0].has_result


def test_join_carries_variable_bindings_from_spec():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "full_adder.dig"))
    from dlc.testing.spec import TestSpec
    spec = TestSpec(
        name="manual", component_index=-1,
        headers=["A", "B", "Cin", "Sum", "Cout"],
        rows=[], raw_data_string="", has_unexpanded_loops=False,
    )
    runs = join_test_runs([spec], None, c)
    bindings = runs[0].bindings
    assert bindings["A"].role == "input"
    assert bindings["Sum"].role == "output"
    assert bindings["Cout"].role == "output"


def test_failing_rows_returns_rows_marked_failed_by_per_row_runner():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    spec = specs[0]
    runs = join_test_runs([spec], None, c)
    run = runs[0]
    run.per_row_results = [
        PerRowResult(spec_name=spec.name, row_index=0, status="passed"),
        PerRowResult(spec_name=spec.name, row_index=1, status="failed"),
        PerRowResult(spec_name=spec.name, row_index=2, status="passed"),
        PerRowResult(spec_name=spec.name, row_index=3, status="failed"),
    ]
    assert run.has_per_row_data
    failing = run.failing_rows()
    assert len(failing) == 2
    assert {r.line_index for r in failing} == {1, 3}


def test_failing_rows_empty_when_no_per_row_data():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    runs = join_test_runs(specs, None, c)
    assert runs[0].failing_rows() == []
    assert not runs[0].has_per_row_data

def test_join_multiple_specs_to_results():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    from dlc.testing.spec import TestSpec
    sa = TestSpec(name="a", component_index=-1, headers=["A"], rows=[],
                  raw_data_string="", has_unexpanded_loops=False)
    sb = TestSpec(name="b", component_index=-1, headers=["B"], rows=[],
                  raw_data_string="", has_unexpanded_loops=False)
    results = parse_cli_output("a: passed\nb: failed (40%)\n")
    runs = join_test_runs([sa, sb], results, c)
    assert len(runs) == 2
    assert runs[0].passed is True
    assert runs[1].passed is False
    assert runs[1].result.fail_pct == 40