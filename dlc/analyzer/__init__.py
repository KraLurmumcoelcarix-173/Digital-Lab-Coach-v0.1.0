from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection, check_wire_completeness,
        _link_cascades_to_missing,
)
from dlc.analyzer.bit_widths import check_bit_widths
from dlc.analyzer.combinational_loops import check_combinational_loops
from dlc.analyzer.interface_conformance import check_interface_conformance
from dlc.analyzer.sequential import check_sequential

__all__ = [
    "Issue", "IssueSeverity", "IssueCollection",
    "check_wire_completeness", "check_bit_widths",
    "check_combinational_loops", "check_interface_conformance",
    "check_sequential",
    "check_all_l1", "check_all_l1_deep",
]


def check_all_l1(circuit):
    from dlc.parser.netlist import build_netlist
    from dlc.parser.graph import build_signal_graph
    from dlc.facts.extractor import extract_facts

    netlist = build_netlist(circuit)
    graph = build_signal_graph(circuit, netlist)
    facts = extract_facts(circuit, netlist=netlist, graph=graph)

    out = IssueCollection()
    out.extend(check_wire_completeness(
        circuit, netlist=netlist, graph=graph, facts=facts,
    ))
    out.extend(check_bit_widths(circuit, netlist=netlist, facts=facts))
    out.extend(check_combinational_loops(circuit, facts=facts))
    out.extend(check_interface_conformance(
        circuit, netlist=netlist, facts=facts,
    ))
    out.extend(check_sequential(circuit, netlist=netlist))
    # fold undriven errors caused by a missing child file into one
    # follow-up note under their missing_subcircuit root cause. Runs here
    # (not in a single checker) so it sees every checker's issues.
    return _link_cascades_to_missing(circuit, netlist, out)

def check_all_l1_deep(circuit, _chain=None, _top_instance_idx=None):
    """Run every L1 checker on `circuit` AND every resolved subcircuit.

    Nested issues are tagged so the web UI can both list and highlight
    them: `scope` carries the file breadcrumb, the title is prefixed
    with it, `child_component_indices` keeps the indices valid inside
    the child circuit, and `component_indices` is remapped to the
    top-level subcircuit-instance component the issue lives under.
    """
    if _chain is None:
        _chain = []
    out = check_all_l1(circuit)
    if _chain:
        breadcrumb = " > ".join(_chain)
        for i in out.issues:
            i.title = f"[{breadcrumb}] {i.title}"
            i.scope = breadcrumb
            i.child_component_indices = list(i.component_indices)
            i.component_indices = (
                [_top_instance_idx] if _top_instance_idx is not None else []
            )
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue
        if _top_instance_idx is None:
            # First hop down: remember which TOP-level component this
            # subtree hangs off, for graph highlighting.
            inst_idx = next(
                (k for k, comp in enumerate(circuit.components)
                 if comp is sub_ref.parent_component),
                None,
            )
        else:
            inst_idx = _top_instance_idx
        child_issues = check_all_l1_deep(
            sub_ref.child_circuit, _chain + [sub_ref.reference], inst_idx,
        )
        out.extend(child_issues)
    return out

def check_all_l1_muted(
    circuit,
    all_tests_passed: bool,
    threshold: int = 3,
):
    issues = check_all_l1(circuit)
    if all_tests_passed and len(issues.issues) <= threshold:
        return IssueCollection()
    return issues


def check_all_l1_deep_muted(
    circuit,
    all_tests_passed: bool,
    threshold: int = 3,
):
    issues = check_all_l1_deep(circuit)
    if all_tests_passed and len(issues.issues) <= threshold:
        return IssueCollection()
    return issues