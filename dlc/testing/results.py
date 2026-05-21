"""
F4: Digital CLI test output parser.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TestcaseResult:
    __test__ = False

    name: str
    status: str         
    fail_pct: int | None
    raw_line: str


@dataclass
class TestRunResults:
    __test__ = False

    testcases: list[TestcaseResult]
    raw_output: str

    def passed(self) -> bool:
        if not self.testcases:
            return False
        return all(t.status == "passed" for t in self.testcases)

    def by_name(self) -> dict[str, TestcaseResult]:
        return {t.name: t for t in self.testcases}

    def failed(self) -> list[TestcaseResult]:
        return [t for t in self.testcases if t.status == "failed"]

    def passed_testcases(self) -> list[TestcaseResult]:
        return [t for t in self.testcases if t.status == "passed"]

_PASS_RE = re.compile(r'(\S+):\s+passed\s*$')
_FAIL_RE = re.compile(r'(\S+):\s+failed(?:\s*\((\d+)%\))?\s*$')


def parse_cli_output(text: str) -> TestRunResults:
    by_name: dict[str, TestcaseResult] = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = _FAIL_RE.search(line)
        if m:
            name = m.group(1)
            pct_str = m.group(2)
            pct = int(pct_str) if pct_str else None
            by_name[name] = TestcaseResult(
                name=name, status="failed", fail_pct=pct, raw_line=line,
            )
            continue
        m = _PASS_RE.search(line)
        if m:
            name = m.group(1)
            by_name[name] = TestcaseResult(
                name=name, status="passed", fail_pct=None, raw_line=line,
            )
    return TestRunResults(
        testcases=list(by_name.values()),
        raw_output=text,
    )