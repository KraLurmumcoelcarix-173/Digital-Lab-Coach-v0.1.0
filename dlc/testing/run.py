"""
F4: TestSpec × TestRunResults join.
"""

from dataclasses import dataclass, field

from dlc.parser.models import Circuit
from dlc.testing.spec import (
    TestSpec, TestRow, VariableBinding, match_variables_to_io,
)
from dlc.testing.results import TestcaseResult, TestRunResults


@dataclass(frozen=True)
class PerRowResult:
    __test__ = False

    spec_name: str
    row_index: int
    status: str
    error_message: str | None = None
    raw_output: str | None = None
    mismatches: list[dict] | None = None


@dataclass
class TestRun:
    """One Testcase's full picture for Layer 3 consumption."""
    __test__ = False

    spec: TestSpec
    bindings: dict[str, VariableBinding]
    result: TestcaseResult | None = None
    per_row_results: list[PerRowResult] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def has_result(self) -> bool:
        return self.result is not None

    @property
    def passed(self) -> bool:
        return self.has_result and self.result.status == "passed"

    @property
    def has_per_row_data(self) -> bool:
        return bool(self.per_row_results)

    def failing_row_indices(self) -> list[int]:
        return [r.row_index for r in self.per_row_results if r.status == "failed"]

    def failing_rows(self) -> list[TestRow]:
        """Rows that the per-row runner reported as failing."""
        idxs = set(self.failing_row_indices())
        return [row for row in self.spec.rows if row.line_index in idxs]


def join_test_runs(
    specs: list[TestSpec],
    results: TestRunResults | None,
    circuit: Circuit,
) -> list[TestRun]:

    by_name = results.by_name() if results is not None else {}
    runs: list[TestRun] = []
    for spec in specs:
        runs.append(TestRun(
            spec=spec,
            bindings=match_variables_to_io(spec.headers, circuit),
            result=by_name.get(spec.name),
            per_row_results=[],
        ))
    return runs