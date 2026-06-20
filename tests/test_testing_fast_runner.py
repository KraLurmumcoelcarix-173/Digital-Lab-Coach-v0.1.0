"""
F4 fast runner: verbose-table parsing + 1:1 row mapping.

Fixture texts are real `CLI test -verbose` outputs captured from
Digital v0.31 (java -cp Digital.jar CLI test -circ ... -verbose).
"""

import os
from pathlib import Path

import pytest

from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.results import parse_cli_output_verbose
from dlc.testing.runner import (
    _map_section_to_rows, _spec_is_fast_safe, find_digital_jar,
    per_file_run_fast, per_row_run, per_row_run_auto,
)
from dlc.testing.spec import TestSpec, parse_data_string

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"


# Captured output: stateful circuit, one C-clock row failing in place.
VERBOSE_FAILED = """\
this_is_a_test: failed (20%)
A B C D load Clock Fa Fb Fc Fd Fe Ff Fg
0 0 0 0 0 0 1 1 1 1 1 1 0
0 1 0 1 1 0 1 0 1 1 0 1 1
1 1 1 1 0 0 1 0 1 1 0 1 E: 0 / F: 1
1 0 1 0 1 0 1 1 1 0 1 1 1
0 0 0 0 0 0 1 1 1 0 1 1 1

Tests have failed.
"""

# Captured output: two testcases, first failed (with table), second passed.
VERBOSE_TWO_TC = VERBOSE_FAILED.replace(
    "\nTests have failed.\n", "\nsecond_test: passed\nTests have failed.\n"
)

VERBOSE_PASSED = "this_is_a_test: passed\n"

VERBOSE_TC_ERROR = """\
this_is_a_test: Test signal Qx not found in the circuit!
Tests have failed.
"""

# Spaced testcase name + multiple failing cells in one row.
VERBOSE_SPACED_NAME = """\
Register File Test: failed (1%)
WriteReg WriteData RegWrite Clock ReadReg1 ReadReg2 ReadData1 ReadData2
1 0x19 1 0 1 0 0x19 0
2 1E 1 0 0 2 E: 1 / F: 0 E: 1F / F: 1E
1 1 1 0 1 0 1 0

Tests have failed.
"""

NOISE = (
    "Jun 12, 2026 2:30:34 PM java.util.prefs.FileSystemPreferences$1 run\n"
    "INFO: Created user preferences directory.\n"
)

TESTDATA_5_ROWS = """\
A B C D load Clock Fa Fb Fc Fd Fe Ff Fg
0 0 0 0 0 C 1 1 1 1 1 1 0
0 1 0 1 1 C 1 0 1 1 0 1 1
1 1 1 1 0 C 1 0 1 1 0 1 1
1 0 1 0 1 C 1 1 1 0 1 1 1
0 0 0 0 0 C 1 1 1 0 1 1 1
"""


def _spec(name: str, datastring: str) -> TestSpec:
    headers, rows, has_loops = parse_data_string(datastring)
    return TestSpec(
        name=name, component_index=0, headers=headers, rows=rows,
        raw_data_string=datastring, has_unexpanded_loops=has_loops,
    )


def test_verbose_parse_failed_section_marks_rows():
    sections = parse_cli_output_verbose(VERBOSE_FAILED)
    sec = sections["this_is_a_test"]
    assert sec.status == "failed"
    assert sec.fail_pct == 20
    assert sec.table_ok
    assert sec.headers == "A B C D load Clock Fa Fb Fc Fd Fe Ff Fg".split()
    assert sec.row_failed == [False, False, True, False, False]
    assert sec.row_mismatches[2] == [
        {"column": "Fg", "expected": "0", "found": "1"}
    ]


def test_verbose_parse_passed_section():
    sections = parse_cli_output_verbose(VERBOSE_PASSED)
    assert sections["this_is_a_test"].status == "passed"


def test_verbose_parse_two_testcases():
    sections = parse_cli_output_verbose(VERBOSE_TWO_TC)
    assert sections["this_is_a_test"].status == "failed"
    assert sections["this_is_a_test"].row_failed == [False, False, True, False, False]
    assert sections["second_test"].status == "passed"


def test_verbose_parse_spaced_name_and_multi_mismatch_row():
    sections = parse_cli_output_verbose(VERBOSE_SPACED_NAME)
    sec = sections["Register File Test"]
    assert sec.status == "failed"
    assert sec.row_failed == [False, True, False]
    assert [m["column"] for m in sec.row_mismatches[1]] == [
        "ReadData1", "ReadData2",
    ]


def test_verbose_parse_testcase_error_needs_known_names():
    # Without known names the error line is ignored (could be log noise)...
    assert parse_cli_output_verbose(VERBOSE_TC_ERROR) == {}
    # ...with known names it becomes an error section with the real message.
    sections = parse_cli_output_verbose(
        VERBOSE_TC_ERROR, known_names={"this_is_a_test"},
    )
    sec = sections["this_is_a_test"]
    assert sec.status == "error"
    assert "Qx" in sec.error_message


def test_verbose_parse_ignores_java_log_noise():
    sections = parse_cli_output_verbose(
        NOISE + VERBOSE_PASSED, known_names={"this_is_a_test"},
    )
    assert set(sections) == {"this_is_a_test"}


def test_map_section_failed_table_to_rows():
    spec = _spec("this_is_a_test", TESTDATA_5_ROWS)
    sec = parse_cli_output_verbose(VERBOSE_FAILED)["this_is_a_test"]
    rows = _map_section_to_rows(spec, sec)
    assert [r.status for r in rows] == [
        "passed", "passed", "failed", "passed", "passed",
    ]
    assert rows[2].mismatches == [
        {"column": "Fg", "expected": "0", "found": "1"}
    ]
    assert rows[0].mismatches is None


def test_map_section_passed_marks_all_rows_passed():
    spec = _spec("this_is_a_test", TESTDATA_5_ROWS)
    sec = parse_cli_output_verbose(VERBOSE_PASSED)["this_is_a_test"]
    rows = _map_section_to_rows(spec, sec)
    assert len(rows) == 5
    assert all(r.status == "passed" for r in rows)


def test_map_section_row_count_mismatch_requests_fallback():
    spec = _spec("this_is_a_test", TESTDATA_5_ROWS + "0 0 0 0 0 C 1 1 1 1 1 1 0\n")
    sec = parse_cli_output_verbose(VERBOSE_FAILED)["this_is_a_test"]
    assert _map_section_to_rows(spec, sec) is None


def test_spec_with_unexpanded_loops_is_not_fast_safe():
    spec = _spec("t", "A B\nloop(N, 3)\n(N+1) (N+1)\n")
    assert spec.has_unexpanded_loops
    assert not _spec_is_fast_safe(spec)


def test_loop_expanded_spec_is_fast_safe_and_maps_in_order():
    spec = _spec("t", "A B\nloop(N, 3)\n(N) (N)\nend loop\n")
    assert _spec_is_fast_safe(spec)
    out = "t: failed (33%)\nA B\n0 0\n1 E: 1 / F: 0\n2 2\n\nTests have failed.\n"
    sec = parse_cli_output_verbose(out)["t"]
    rows = _map_section_to_rows(spec, sec)
    assert [r.status for r in rows] == ["passed", "failed", "passed"]


_JAR = os.environ.get("DIGITAL_JAR") or find_digital_jar()

@pytest.mark.slow
@pytest.mark.skipif(_JAR is None, reason="Digital.jar not available")
def test_fast_equals_cumulative_on_30_bug_circuit():
    """Issue #11 acceptance: fast results == cumulative results."""
    from dlc.testing.spec import extract_test_specs
    path = str(
        SAMPLES / "30_bug_benchmark" / "bug1_meaningless_mux_in3"
        / "tier3_calculator.dig"
    )
    circuit = parse_dig_file(path)
    specs = [s for s in extract_test_specs(circuit) if s.rows]
    fast_by_name, fallback = per_file_run_fast(specs, path, jar_path=_JAR)
    assert not fallback
    for spec in specs:
        slow = per_row_run(spec, path, jar_path=_JAR)
        fast = fast_by_name[spec.name]
        assert [(r.row_index, r.status) for r in fast] == \
               [(r.row_index, r.status) for r in slow]

@pytest.mark.slow
@pytest.mark.skipif(_JAR is None, reason="Digital.jar not available")
def test_per_row_run_auto_uses_fast_path_on_clean_spec():
    from dlc.testing.spec import extract_test_specs
    path = str(SAMPLES / "tier3_realistic" / "tier3_latched_display.dig")
    circuit = parse_dig_file(path)
    spec = extract_test_specs(circuit)[0]
    rows = per_row_run_auto(spec, path, jar_path=_JAR)
    assert len(rows) == len(spec.rows)
    assert all(r.status == "passed" for r in rows)
