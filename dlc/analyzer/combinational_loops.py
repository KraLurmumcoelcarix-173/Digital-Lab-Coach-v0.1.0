"""
F7: Combinational loop checker.
=
"""

from dlc.parser.models import Circuit
from dlc.facts.extractor import (
    CircuitFacts, extract_facts, _component_display_name,
)
from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection,
)


def _check_combinational_loops(
    circuit: Circuit, facts: CircuitFacts
) -> list[Issue]:
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "combinational_cycle":
            continue
        cycle_indices = bug.detail.get("cycle_order", []) or []
        if not cycle_indices:
            continue
        names = [
            _component_display_name(circuit.components[i], i)
            for i in cycle_indices
        ]
        path_str = " -> ".join(names) + " -> ..."
        anchor = circuit.components[cycle_indices[0]].position
        out.append(Issue(
            kind="combinational_loop",
            severity=IssueSeverity.ERROR,
            title=f"Combinational loop across {len(cycle_indices)} components",
            message=(
                f"There is a purely combinational loop in the circuit: "
                f"{path_str}. With no clocked element (Register, Clock) "
                f"breaking the cycle, the output is undefined — it will "
                f"either oscillate or settle to whatever value Digital "
                f"happens to compute first."
            ),
            component_indices=cycle_indices,
            location=(anchor.x, anchor.y),
            suggested_fix=(
                "Either break the cycle with a Register (turning it into "
                "a clocked feedback path), or fix the wiring if the loop "
                "is accidental."
            ),
        ))
    return out


def check_combinational_loops(
    circuit: Circuit,
    facts: CircuitFacts | None = None,
) -> IssueCollection:
    """Run combinational-loop checks against `circuit`."""
    if facts is None:
        facts = extract_facts(circuit)
    issues = IssueCollection()
    issues.extend(_check_combinational_loops(circuit, facts))
    return issues