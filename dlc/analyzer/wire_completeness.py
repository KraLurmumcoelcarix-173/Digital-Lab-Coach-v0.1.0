"""
Layer1 - F5: Wire-completeness checker 

Output: IssueCollection, JSON-serializable list of Issue records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum

from dlc.parser.models import Circuit
from dlc.parser.netlist import (
    NetList, build_netlist, _wire_endpoint_degree, IMPLICIT_PIN_RADIUS,
)
from dlc.parser.graph import build_signal_graph
from dlc.facts.extractor import CircuitFacts, extract_facts

from dlc.facts.extractor import CircuitFacts, extract_facts, _component_display_name
from dlc.parser.pin_geometry import absolute_pin_positions

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
    net_id: int | None = None
    # Deep-check provenance. scope is the subcircuit breadcrumb
    # ("alu.dig > add-sub.dig"), None for top-level issues. For a
    # nested issue, component_indices is remapped to the TOP-level
    # subcircuit-instance component (so UI highlighting works on the
    # top graph) and the original child-circuit indices move here.
    scope: str | None = None
    child_component_indices: list[int] = field(default_factory=list)

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


def _check_dangling_inputs(
    circuit: Circuit, facts: CircuitFacts, netlist: NetList
) -> list[Issue]:
    isolated = _component_isolated_indices(circuit, netlist)
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "dangling_input":
            continue
        raw = bug.detail.get("pins", []) or []
        pins = []
        for p in raw:
            idx = p["component_index"]
            comp = circuit.components[idx]
            if comp.is_output():
                continue
            if idx in isolated:
                continue
            if comp.element_name.endswith(".dig"):
                continue
            pins.append(p)
        if not pins:
            continue
        descs = [_pin_descr(circuit, p) for p in pins]
        loc = (pins[0]["x"], pins[0]["y"])
        plural = "s" if len(pins) > 1 else ""
        out.append(Issue(
            kind="dangling_input",
            severity=IssueSeverity.ERROR,
            title=f"Undriven input pin{plural}: {', '.join(descs)}",
            message=(
                f"Input pin{plural} {', '.join(descs)} have no wire "
                f"connecting them. The circuit will produce an undefined "
                f"value at this point."
            ),
            component_indices=[p["component_index"] for p in pins],
            location=loc,
            net_id=bug.net_id,
            suggested_fix=(
                f"Connect a driving output (a gate output, a Const, or "
                f"an In pin) to {descs[0]}."
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
            title=f"Multiple drivers wired together: {', '.join(descs)}",
            message=(
                f"{len(drivers)} outputs are wired together at the same "
                f"point: {', '.join(descs)}. Two outputs on the same wire "
                f"short-circuit; Digital will flag this at run time."
            ),
            component_indices=bug.component_indices,
            location=loc,
            net_id=bug.net_id,
            suggested_fix=(
                "Disconnect one of the drivers, or feed them through a "
                "Multiplexer if you actually need to select between them."
            ),
        ))
    return out


def _check_missing_subcircuit(
    circuit: Circuit, facts: CircuitFacts
) -> list[Issue]:
    out: list[Issue] = []
    for bug in facts.bugs:
        if bug.kind != "missing_subcircuit":
            continue
        ref = bug.detail.get("reference", "<unknown>")
        err = bug.detail.get("resolution_error", "")
        chain = bug.detail.get("parent_chain", []) or []
        loc = None
        if bug.component_indices:
            anchor = circuit.components[bug.component_indices[0]].position
            loc = (anchor.x, anchor.y)
        if chain:
            chain_path = " -> ".join(chain) + " -> " + ref
            title = f"Nested subcircuit file not found: {ref}"
            message = (
                f"Your top-level circuit references '{chain[0]}', which "
                f"resolves fine, but its dependency chain leads to '{ref}' "
                f"and that file is missing. Full path: {chain_path}."
            )
            fix = (
                f"Find '{ref}' and place it next to '{chain[-1]}'. The "
                f"intermediate file '{chain[0]}' is present; only '{ref}' "
                f"is missing."
            )
        else:
            title = f"Subcircuit file not found: {ref}"
            message = (
                f"This circuit references '{ref}' but the file could not "
                f"be resolved. {err}"
            )
            fix = (
                f"Verify '{ref}' exists in the same folder as the parent "
                f".dig, and that the filename matches exactly "
                f"(case-sensitive on macOS/Linux)."
            )
        out.append(Issue(
            kind="missing_subcircuit",
            severity=IssueSeverity.ERROR,
            title=title,
            message=message,
            component_indices=bug.component_indices,
            location=loc,
            suggested_fix=fix,
        ))
    return out

_NON_LOGIC_FOR_ISOLATION = {
    "In", "Out", "Tunnel", "Const", "Ground", "VDD", "Clock",
    "Testcase", "Rectangle",
}

def _check_duplicate_io_labels(circuit, issues) -> None:

    by_in: dict = {}
    by_out: dict = {}
    for idx, comp in enumerate(circuit.components):
        label = comp.label
        if not label:
            continue
        if comp.is_input():
            by_in.setdefault(label, []).append(idx)
        elif comp.is_output():
            by_out.setdefault(label, []).append(idx)

    for label, idxs in by_in.items():
        if len(idxs) > 1:
            anchor = circuit.components[idxs[0]].position
            issues.add(Issue(
                kind="duplicate_input_label",
                severity=IssueSeverity.ERROR,
                title=f"Duplicate input label: '{label}'",
                message=(
                    f"{len(idxs)} In elements share the label '{label}' "
                    f"(component indices {idxs}). The testbench column "
                    f"'{label}' can only feed one of them, and any wire "
                    f"that references In({label}) by name is ambiguous."
                ),
                component_indices=idxs,
                location=(anchor.x, anchor.y),
                suggested_fix=(
                    f"Rename all but one of these inputs so each has a "
                    f"unique Label (e.g., '{label}_1', '{label}_2')."
                ),
            ))

    for label, idxs in by_out.items():
        if len(idxs) > 1:
            anchor = circuit.components[idxs[0]].position
            issues.add(Issue(
                kind="duplicate_output_label",
                severity=IssueSeverity.ERROR,
                title=f"Duplicate output label: '{label}'",
                message=(
                    f"{len(idxs)} Out elements share the label '{label}' "
                    f"(component indices {idxs}). The testbench expects "
                    f"one column named '{label}' so it can't tell which "
                    f"Out to sample."
                ),
                component_indices=idxs,
                location=(anchor.x, anchor.y),
                suggested_fix=(
                    f"Rename all but one so each output has a unique "
                    f"Label (e.g., '{label}_1', '{label}_2')."
                ),
            ))

def _component_isolated_indices(
    circuit: Circuit, netlist: NetList
) -> set[int]:
    isolated: set[int] = set()
    for idx, comp in enumerate(circuit.components):
        if not absolute_pin_positions(comp):
            continue
        if comp.element_name in _NON_LOGIC_FOR_ISOLATION:
            continue
        on_nets = [n for n in netlist.nets
                   if any(p.component_index == idx for p in n.pins)]
        if not on_nets:
            isolated.add(idx)
            continue
        has_partner = any(
            p.component_index != idx and p.element_name != "Tunnel"
            for net in on_nets for p in net.pins
        )
        if not has_partner:
            isolated.add(idx)
    return isolated


def _check_unused_top_outputs(
    circuit: Circuit, facts: CircuitFacts
) -> list[Issue]:
    out: list[Issue] = []
    seen: set[int] = set()
    for bug in facts.bugs:
        if bug.kind != "dangling_input":
            continue
        for p in bug.detail.get("pins", []) or []:
            idx = p["component_index"]
            if idx in seen:
                continue
            comp = circuit.components[idx]
            if not comp.is_output():
                continue
            seen.add(idx)
            label = comp.label or f"Out[{idx}]"
            out.append(Issue(
                kind="unused_top_output",
                severity=IssueSeverity.ERROR,
                title=f"Output '{label}' is never driven",
                message=(
                    f"Top-level output '{label}' has no wire feeding it. "
                    f"The circuit defines this as an output but never "
                    f"computes a value for it."
                ),
                component_indices=[idx],
                location=(comp.position.x, comp.position.y),
                suggested_fix=(
                    f"Connect a driving signal (a gate output, a Const, "
                    f"or an In) to '{label}', or remove the Out pin if "
                    f"it isn't needed."
                ),
            ))
    return out


def _check_isolated_components(
    circuit: Circuit, netlist: NetList
) -> list[Issue]:
    out: list[Issue] = []
    for idx in sorted(_component_isolated_indices(circuit, netlist)):
        comp = circuit.components[idx]
        name = _component_display_name(comp, idx)
        out.append(Issue(
            kind="isolated_component",
            severity=IssueSeverity.WARNING,
            title=f"Orphan component {name}",
            message=(
                f"Component {name} at ({comp.position.x}, {comp.position.y}) "
                f"has no wires connecting any of its pins to the rest of "
                f"the circuit. It contributes nothing."
            ),
            component_indices=[idx],
            location=(comp.position.x, comp.position.y),
            suggested_fix=(
                f"Either wire {name} into your circuit, or delete it if "
                f"it's leftover from an earlier design."
            ),
        ))
    return out


def _check_empty_tunnels(
    circuit: Circuit, netlist: NetList
) -> list[Issue]:
    out: list[Issue] = []
    for net in netlist.nets:
        if not net.tunnel_names:
            continue
        if any(p.element_name != "Tunnel" for p in net.pins):
            continue
        tunnel_indices = sorted({
            p.component_index for p in net.pins if p.element_name == "Tunnel"
        })
        net_name = sorted(net.tunnel_names)[0]
        anchor = circuit.components[tunnel_indices[0]].position
        if len(tunnel_indices) > 1:
            out.append(Issue(
                kind="empty_tunnel",
                severity=IssueSeverity.WARNING,
                title=f"Tunnel net '{net_name}' carries no signal",
                message=(
                    f"Tunnels named '{net_name}' are connected to each "
                    f"other but nothing drives or reads them. The named "
                    f"net is electrically isolated."
                ),
                component_indices=tunnel_indices,
                location=(anchor.x, anchor.y),
                suggested_fix=(
                    f"Either wire a driving signal into one of the "
                    f"'{net_name}' tunnels, or delete them."
                ),
            ))
        else:
            out.append(Issue(
                kind="empty_tunnel",
                severity=IssueSeverity.WARNING,
                title=f"Isolated Tunnel '{net_name}'",
                message=(
                    f"Tunnel '{net_name}' has no signal — no other Tunnel "
                    f"shares this NetName and no wire connects to it. "
                    f"This Tunnel does nothing in the circuit."
                ),
                component_indices=tunnel_indices,
                location=(anchor.x, anchor.y),
                suggested_fix=(
                    f"Either connect a wire to this Tunnel, place a "
                    f"matching Tunnel named '{net_name}' elsewhere in "
                    f"the circuit, or delete it."
                ),
            ))
    return out

# --- cascade linking ---------------------------------------------------
# One missing child file makes every net its outputs would drive read as
# "undriven", so a student sees a pile of unrelated-looking dangling_input /
# unused_top_output / dangling_subcircuit_input ERRORS whose real cause is a
# single missing .dig. Fold each missing instance's cascade into ONE follow-up
# note placed right after its missing_subcircuit error. The root cause stays
# an ERROR; the cascade group is a WARNING so it never moves the L1 gate by
# itself. Invoked from check_all_l1 (dlc/analyzer/__init__.py) so it sees
# every checker's issues, not just this module's.

_CASCADE_LINKED_KINDS = (
    "dangling_input", "unused_top_output", "dangling_subcircuit_input",
)



def _unresolved_subcircuit_instances(circuit: Circuit) -> dict[int, str]:
    """component index -> reference name for every DIRECT (this-level)
    subcircuit instance whose file could not be resolved."""
    unresolved: dict[int, str] = {}
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is not None:
            continue
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                unresolved[idx] = sub_ref.reference
                break
    return unresolved


def _net_for_issue(issue: Issue, netlist: NetList):
    if issue.net_id is not None:
        if 0 <= issue.net_id < len(netlist.nets):
            return netlist.nets[issue.net_id]
        return None
    # unused_top_output / dangling_subcircuit_input carry no net_id; the
    # undriven flavors set `location` to the pin coordinate. Only accept a
    # location-recovered net the issue's component actually sits on.
    members = set(issue.component_indices)
    if issue.location is not None:
        nid = netlist.by_coord.get(tuple(issue.location))
        if nid is not None:
            net = netlist.nets[nid]
            if any(p.component_index in members for p in net.pins):
                return net
    if issue.component_indices:
        target = issue.component_indices[0]
        for net in netlist.nets:
            if net.drivers():
                continue
            if any(p.component_index == target for p in net.pins):
                return net
    return None


def _cascade_attribution(
    net, circuit: Circuit, unresolved: dict[int, str], endpoint_degree: dict
) -> int | None:
    """Which missing instance (component index) explains this undriven net,
    or None if it looks like an independent mistake."""
    if net.drivers():
        return None      # driven nets are never a missing-child cascade
    # Direct evidence: the netlist claimed one of the net's wire ends as an
    # implicit pin of a missing instance (direction stays "unknown", so the
    # net has no driver even though the student wired it correctly).
    for p in net.pins:
        if p.component_index in unresolved:
            return p.component_index

    # Indirect evidence — coordinates of this net the missing part would
    # explain. Tunnel pins don't count as claims: a tunnel never drives, and
    # a bidir tunnel pin can even snap onto the wire end the missing child
    # was supposed to drive (seen on lab5 cpu.dig).
    hard_pin_coords = {
        (p.x, p.y) for p in net.pins if p.element_name != "Tunnel"
    }
    candidates: list[tuple[int, int]] = [
        c for c in net.coords
        if endpoint_degree.get(c, 0) == 1 and c not in hard_pin_coords
    ]
    # Tunnels placed directly ON a missing instance's pin have wire-degree 0,
    # so the loop above can't see them; treat a net tunnel anchor near a
    # missing instance as evidence too (radius = the netlist's own
    # implicit-pin envelope).
    near_tunnel_anchors: list[tuple[int, int]] = []
    for comp in circuit.components:
        if comp.element_name != "Tunnel":
            continue
        anchor = (comp.position.x, comp.position.y)
        if anchor not in net.coords:
            continue
        for idx in unresolved:
            inst = circuit.components[idx]
            d = abs(inst.position.x - anchor[0]) + abs(inst.position.y - anchor[1])
            if d <= IMPLICIT_PIN_RADIUS:
                near_tunnel_anchors.append(anchor)
                break
    candidates.extend(near_tunnel_anchors)
    if not candidates:
        return None
    best_idx = None
    best_dist = None
    for idx in sorted(unresolved):
        comp = circuit.components[idx]
        for (ex, ey) in candidates:
            d = abs(comp.position.x - ex) + abs(comp.position.y - ey)
            if best_dist is None or d < best_dist:
                best_idx, best_dist = idx, d
    return best_idx


def _cascade_group_issue(
    circuit: Circuit, inst_idx: int, ref: str, members: list[Issue]
) -> Issue:
    victims: list[int] = []
    for iss in members:
        for c_idx in iss.component_indices:
            if c_idx != inst_idx and c_idx not in victims:
                victims.append(c_idx)
    names = [
        _component_display_name(circuit.components[i], i) for i in victims
    ]
    shown = names[:6]
    if len(names) > 6:
        shown.append(f"+{len(names) - 6} more")
    n = len(members)
    comp = circuit.components[inst_idx]
    return Issue(
        kind="missing_subcircuit_cascade",
        severity=IssueSeverity.WARNING,
        title=(
            f"{n} undriven signal{'s' if n != 1 else ''} — all caused by "
            f"the missing '{ref}'"
        ),
        message=(
            f"{', '.join(shown)} {'sits' if len(names) == 1 else 'sit'} on "
            f"wires that '{ref}' would drive, so "
            f"{'it reads' if len(names) == 1 else 'they read'} as undriven. "
            f"This is very likely ONE root cause — the missing file — not "
            f"{n} separate wiring mistake{'s' if n != 1 else ''}."
        ),
        component_indices=[inst_idx] + victims,
        location=(comp.position.x, comp.position.y),
        suggested_fix=(
            f"Don't rewire these. Put '{ref}' next to this .dig file and "
            f"re-upload — these signals should come back on their own."
        ),
    )


def _link_cascades_to_missing(
    circuit: Circuit, netlist: NetList, issues: IssueCollection
) -> IssueCollection:
    unresolved = _unresolved_subcircuit_instances(circuit)
    if not unresolved:
        return issues

    endpoint_degree = _wire_endpoint_degree(circuit)
    kept: list[Issue] = []
    grouped: dict[int, list[Issue]] = {}
    for iss in issues.issues:
        if iss.kind in _CASCADE_LINKED_KINDS and iss.scope is None:
            net = _net_for_issue(iss, netlist)
            inst = (
                _cascade_attribution(net, circuit, unresolved, endpoint_degree)
                if net is not None else None
            )
            if inst is not None:
                grouped.setdefault(inst, []).append(iss)
                continue
        kept.append(iss)

    if not grouped:
        return issues

    # Re-emit with each cascade group directly after its root-cause error.
    out = IssueCollection()
    emitted: set[int] = set()
    for iss in kept:
        out.add(iss)
        if iss.kind != "missing_subcircuit" or not iss.component_indices:
            continue
        anchor = iss.component_indices[0]
        if anchor in grouped and anchor not in emitted:
            out.add(_cascade_group_issue(
                circuit, anchor, unresolved[anchor], grouped[anchor],
            ))
            emitted.add(anchor)
    for inst in sorted(grouped):
        if inst not in emitted:
            out.add(_cascade_group_issue(
                circuit, inst, unresolved[inst], grouped[inst],
            ))
    return out



# Public API

def check_wire_completeness(
    circuit: Circuit,
    netlist: NetList | None = None,
    graph=None,
    facts: CircuitFacts | None = None,
) -> IssueCollection:
    """Run all wire-completeness checks against `circuit`."""
    if netlist is None:
        netlist = build_netlist(circuit)
    if graph is None:
        graph = build_signal_graph(circuit, netlist)
    if facts is None:
        facts = extract_facts(circuit, netlist=netlist, graph=graph)

    issues = IssueCollection()
    issues.extend(_check_dangling_inputs(circuit, facts, netlist))
    issues.extend(_check_multi_drivers(circuit, facts))
    issues.extend(_check_missing_subcircuit(circuit, facts))
    issues.extend(_check_unused_top_outputs(circuit, facts))
    issues.extend(_check_isolated_components(circuit, netlist))
    issues.extend(_check_empty_tunnels(circuit, netlist))
    _check_duplicate_io_labels(circuit, issues)
    return issues