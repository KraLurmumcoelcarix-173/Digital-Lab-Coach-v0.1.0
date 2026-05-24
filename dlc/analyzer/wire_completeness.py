"""
Layer1 - F5: Wire-completeness checker 

Output: IssueCollection, JSON-serializable list of Issue records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList, build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.facts.extractor import CircuitFacts, extract_facts


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Issue:
    kind: str
    severity: IssueSeverity
    title: str
    message: str
    component_indices: list[int] = field(default_factory=list)
    location: tuple[int, int] | None = None
    suggested_fix: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class IssueCollection:
    issues: list[Issue] = field(default_factory=list)

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    def extend(self, issues: "list[Issue] | IssueCollection") -> None:
        if isinstance(issues, IssueCollection):
            self.issues.extend(issues.issues)
        else:
            self.issues.extend(issues)

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]

    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]

    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == IssueSeverity.INFO]

    def by_kind(self, kind: str) -> list[Issue]:
        return [i for i in self.issues if i.kind == kind]

    def summary(self) -> str:
        n_err = len(self.errors())
        n_warn = len(self.warnings())
        n_info = len(self.infos())
        return (
            f"IssueCollection: {len(self.issues)} issues "
            f"({n_err} errors, {n_warn} warnings, {n_info} infos)"
        )

    def to_dict(self) -> dict:
        return {"issues": [i.to_dict() for i in self.issues]}

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# Public API

def check_wire_completeness(
    circuit: Circuit,
    netlist: NetList | None = None,
    graph=None,
    facts: CircuitFacts | None = None,
) -> IssueCollection:
    """Run all wire-completeness checks against `circuit`.
    Auto-builds netlist/graph/facts when not provided."""
    if netlist is None:
        netlist = build_netlist(circuit)
    if graph is None:
        graph = build_signal_graph(circuit, netlist)
    if facts is None:
        facts = extract_facts(circuit, netlist=netlist, graph=graph)

    issues = IssueCollection()
    return issues