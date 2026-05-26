from dlc.analyzer.wire_completeness import (
    Issue, IssueSeverity, IssueCollection, check_wire_completeness,
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
    return out

def check_all_l1_deep(circuit, _chain=None):
    if _chain is None:
        _chain = []
    out = check_all_l1(circuit)
    if _chain:
        breadcrumb = " > ".join(_chain)
        for i in out.issues:
            i.title = f"[{breadcrumb}] {i.title}"
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue
        child_issues = check_all_l1_deep(
            sub_ref.child_circuit, _chain + [sub_ref.reference]
        )
        out.extend(child_issues)
    return out