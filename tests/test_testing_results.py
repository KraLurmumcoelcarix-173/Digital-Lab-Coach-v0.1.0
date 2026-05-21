"""
Tests for dlc.testing.results
"""

from dlc.testing.results import (
    parse_cli_output,
    TestcaseResult,
    TestRunResults,
)


def test_parse_single_pass_line():
    res = parse_cli_output("cpu: passed\n")
    assert len(res.testcases) == 1
    tc = res.testcases[0]
    assert tc.name == "cpu"
    assert tc.status == "passed"
    assert tc.fail_pct is None


def test_parse_single_fail_line_with_percent():
    res = parse_cli_output("cpu: failed (60%)\n")
    assert len(res.testcases) == 1
    tc = res.testcases[0]
    assert tc.name == "cpu"
    assert tc.status == "failed"
    assert tc.fail_pct == 60


def test_parse_single_fail_line_without_percent():
    """Some Digital versions or summary lines emit 'failed' with no %."""
    res = parse_cli_output("ALU: failed\n")
    assert len(res.testcases) == 1
    assert res.testcases[0].status == "failed"
    assert res.testcases[0].fail_pct is None


def test_parse_empty_output():
    res = parse_cli_output("")
    assert res.testcases == []
    assert res.raw_output == ""


def test_parse_blank_lines_only():
    res = parse_cli_output("\n   \n\n")
    assert res.testcases == []


def test_parse_multiple_testcases():
    text = "add-sub: passed\nALU: failed (33%)\nslt-unit: passed\n"
    res = parse_cli_output(text)
    assert len(res.testcases) == 3
    by_name = res.by_name()
    assert by_name["add-sub"].status == "passed"
    assert by_name["ALU"].status == "failed"
    assert by_name["ALU"].fail_pct == 33
    assert by_name["slt-unit"].status == "passed"


def test_duplicate_name_last_occurrence_wins():
    """Autograder re-runs the same Testcase; the later result should
    overwrite the earlier one."""
    text = "cpu: failed (50%)\ncpu: passed\n"
    res = parse_cli_output(text)
    assert len(res.testcases) == 1
    assert res.testcases[0].status == "passed"

