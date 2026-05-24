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

from dlc.facts.extractor import CircuitFacts, extract_facts, _component_display_name

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


def _pin_descr(circuit: Circuit, pin_dict: dict) -> str:
    """Render a pin from a BugFact.detail entry as 'DisplayName.pin_name'."""
    idx = pin_dict["component_index"]
    comp = circuit.components[idx]
    return f"{_component_display_name(comp, idx)}.{pin_dict['pin_name']}"


def _check_dangling_inputs(circuit: Circuit, facts: CircuitFacts) -> list[Issue]:
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "dangling_input":
            continue
        pins = bug.detail.get("pins", []) or []
        if not pins:
            continue
        descs = [_pin_descr(circuit, p) for p in pins]
        loc = (pins[0]["x"], pins[0]["y"])
        plural = "s" if len(pins) > 1 else ""
        out.append(Issue(
            kind="dangling_input",
            severity=IssueSeverity.ERROR,
            title=f"Undriven input pin{plural} on net {bug.net_id}",
            message=(
                f"Input pin{plural} {', '.join(descs)} on net {bug.net_id} "
                f"have no wire connecting them. The circuit will produce "
                f"an undefined value at this point."
            ),
            component_indices=bug.component_indices,
            location=loc,
            suggested_fix=(
                f"Connect a driving output (a gate output, a Const, or an "
                f"In pin) to {descs[0]}."
            ),
        ))
    return out


def _check_multi_drivers(circuit: Circuit, facts: CircuitFacts) -> list[Issue]:
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "multi_driver":
            continue
        drivers = bug.detail.get("drivers", []) or []
        if not drivers:
            continue
        descs = [_pin_descr(circuit, d) for d in drivers]
        loc = (drivers[0]["x"], drivers[0]["y"])
        out.append(Issue(
            kind="multi_driver",
            severity=IssueSeverity.ERROR,
            title=f"Multiple drivers on net {bug.net_id}",
            message=(
                f"Net {bug.net_id} is driven by {len(drivers)} outputs at once: "
                f"{', '.join(descs)}. Two outputs on the same wire short-circuit "
                f"the signal; Digital will flag this at run time."
            ),
            component_indices=bug.component_indices,
            location=loc,
            suggested_fix=(
                "Disconnect one of the drivers, or feed them through a "
                "Multiplexer if you actually need to select between them."
            ),
        ))
    return out


def _check_missing_subcircuit(circuit: Circuit, facts: CircuitFacts) -> list[Issue]:
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "missing_subcircuit":
            continue
        ref = bug.detail.get("reference", "<unknown>")
        err = bug.detail.get("resolution_error", "")
        loc = None
        if bug.component_indices:
            anchor = circuit.components[bug.component_indices[0]].position
            loc = (anchor.x, anchor.y)
        out.append(Issue(
            kind="missing_subcircuit",
            severity=IssueSeverity.ERROR,
            title=f"Subcircuit file not found: {ref}",
            message=(
                f"This circuit references '{ref}' but the file could not be "
                f"resolved. {err}"
            ),
            component_indices=bug.component_indices,
            location=loc,
            suggested_fix=(
                f"Verify '{ref}' exists in the same folder as the parent .dig, "
                f"and that the filename matches exactly (case-sensitive on macOS/Linux)."
            ),
        ))
    return out

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