"""
Asserts that every clean tier sample produces zero issues across all
Layer-1 checkers.
"""

import glob

import pytest

from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer import check_all_l1


@pytest.mark.parametrize("dig_path", sorted(
    glob.glob("data/sample_circuits/tier1_minimal/*.dig")
    + glob.glob("data/sample_circuits/tier2_structured/*.dig")
    + glob.glob("data/sample_circuits/tier2_structured/subs/*.dig")
    + glob.glob("data/sample_circuits/tier3_realistic/*.dig")
))
def test_clean_sample_produces_no_l1_issues(dig_path):
    c = parse_dig_file(dig_path)
    issues = check_all_l1(c)
    assert issues.issues == [], (
        f"{dig_path}: unexpected issues: "
        f"{[(i.severity.value, i.kind, i.title) for i in issues.issues]}"
    )