"""L3 fault-localizer pre-pass (deterministic, no LLM).

Given one evaluated test row — the evaluator's per-net values plus the
expected-vs-found verdict per top-level output — narrow the circuit down
to a ranked SUSPECT list that grounds the Mode-A sub-agents:

  1. STATIC cone-of-influence: everything upstream of a failing output.
     Components outside every failing cone are never suspects.
  2. ACTIVE cone (dynamic slicing): the same backward walk, but at a
     Multiplexer whose select value resolved for this row, only the
     SELECTED data input (plus the select itself) is followed. Wrong-mux
     bugs collapse from "everything upstream" to a handful of parts.
  3. Exoneration: parts that also feed a PASSING output are less suspect
     than parts feeding only failing ones.
  4. Muted parts: a component whose output net stayed unresolved is
     suspicious by itself (the blueprint's "unresolved/muted" signal).
  5. Hierarchy: a subcircuit instance in the cone is marked expandable
     (the drill-in shows its inner flow); optionally its OWN embedded
     testcase is run — a child that passes its own tests points the
     finger at the parent's wiring, a failing child at the child.

Cones deliberately cross register boundaries (a wrong value latched two
rows ago still traces to its combinational source); Mode A's per-cluster
aggregation (`merge_reports`) sharpens multi-row cases.

This module never proposes a fix — it only says WHERE to look and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList
from dlc.sim.simulator import SimResult

# Components that cannot be "the bug" for Mode A's purposes: test stimuli
# come from the testcase (In), the clock is a given, tunnels are pure
# naming, and annotations carry no signal. Const stays IN — a wrong
# constant (bug3's carry-in!) is a classic seeded bug.
_NEVER_SUSPECT = frozenset({"In", "Clock", "Tunnel", "Testcase", "Rectangle"})

# Semantic hot spots: the L3 bug classes (wrong input-position, wrong
# op-encoding, semantic miswire) concentrate on selectors and splitters.
_HOT_KINDS = frozenset({"Multiplexer", "Decoder", "Splitter"})


@dataclass
class Suspect:
    component_index: int
    element_name: str
    display_name: str
    score: float
    reasons: list[str] = field(default_factory=list)
    in_failing_cones: list[str] = field(default_factory=list)  # output labels
    in_active_cones: list[str] = field(default_factory=list)
    feeds_passing_output: bool = False
    drives_unresolved: bool = False
    is_subcircuit: bool = False
    child_reference: str | None = None
    child_self_test: str | None = None   # "passed" | "failed" | "no_tests" | None


@dataclass
class SuspectReport:
    failing_outputs: list[str] = field(default_factory=list)
    passing_outputs: list[str] = field(default_factory=list)
    suspects: list[Suspect] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    def suspect_indices(self) -> list[int]:
        return [s.component_index for s in self.suspects]


# ---------------------------------------------------------------------------
# Cone walks
# ---------------------------------------------------------------------------

def _output_component_index(circuit: Circuit, label: str) -> int | None:
    for idx, comp in enumerate(circuit.components):
        if comp.is_output() and (comp.label or f"out_{idx}") == label:
            return idx
    return None


def _static_cone(graph: nx.MultiDiGraph, out_idx: int) -> set[int]:
    if out_idx not in graph:
        return {out_idx}
    return set(nx.ancestors(graph, out_idx)) | {out_idx}


def _mux_sel_value(circuit, netlist, sim: SimResult, idx: int) -> int | None:
    """The resolved select value of Multiplexer `idx` for this row, if any."""
    for net in netlist.nets:
        for p in net.pins:
            if (p.component_index == idx and p.pin_name == "sel"
                    and p.direction == "in"):
                return sim.net_values.get(net.net_id)
    return None


def _active_cone(
    circuit: Circuit,
    netlist: NetList,
    graph: nx.MultiDiGraph,
    sim: SimResult,
    out_idx: int,
) -> set[int]:
    """Backward walk that follows only the ACTIVE data path through
    multiplexers whose select value resolved for this row. The select
    edge itself is always followed (a wrong select is a bug class);
    an unresolved select falls back to following every input."""
    if out_idx not in graph:
        return {out_idx}
    seen: set[int] = {out_idx}
    frontier = [out_idx]
    while frontier:
        node = frontier.pop()
        comp = circuit.components[node]
        sel_val = None
        if comp.element_name == "Multiplexer":
            sel_val = _mux_sel_value(circuit, netlist, sim, node)
        for pred, _node, edata in graph.in_edges(node, keys=False, data=True):
            if sel_val is not None:
                pin = edata.get("sink_pin") or ""
                if pin != "sel" and pin != f"in{sel_val}":
                    continue                      # inactive data input this row
            if pred not in seen:
                seen.add(pred)
                frontier.append(pred)
    return seen


# ---------------------------------------------------------------------------
# Auxiliary signals
# ---------------------------------------------------------------------------

def _unresolved_drivers(netlist: NetList, sim: SimResult) -> set[int]:
    """Components driving a signal-carrying net the evaluator left blank."""
    out: set[int] = set()
    for nid in sim.unresolved_nets:
        for p in netlist.nets[nid].pins:
            if p.direction == "out":
                out.add(p.component_index)
    return out


def _child_of_instance(circuit: Circuit, idx: int):
    comp = circuit.components[idx]
    for sub in circuit.subcircuits:
        if sub.parent_component is comp:
            return sub
    return None


def _run_child_self_test(sub_ref, jar_path, timeout) -> str:
    """Run a subcircuit's OWN embedded testcases standalone. A passing child
    shifts suspicion to the parent's wiring; a failing child owns it."""
    if sub_ref is None or sub_ref.resolved_path is None:
        return "no_tests"
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs
    from dlc.testing.runner import per_file_run_fast

    try:
        child = parse_dig_file(sub_ref.resolved_path)
        specs = [s for s in extract_test_specs(child) if s.rows]
        if not specs:
            return "no_tests"
        results, fallback = per_file_run_fast(
            specs, sub_ref.resolved_path, jar_path=jar_path, timeout=timeout,
        )
        for rows in results.values():
            if any(r.status in ("failed", "error") for r in rows):
                return "failed"
        if fallback:
            return "no_tests"      # couldn't trust the mapping; stay neutral
        return "passed"
    except Exception:
        return "no_tests"


def _display_name(circuit: Circuit, idx: int) -> str:
    from dlc.facts.extractor import _component_display_name
    return _component_display_name(circuit.components[idx], idx)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Scoring weights — deliberately simple; Phase-4's localization-accuracy
# metric on the 30-bug set is where these get tuned.
_W_ACTIVE = 3.0        # on the active (mux-pruned) path of a failing output
_W_SHARED = 1.0        # per ADDITIONAL failing output sharing that active path
_W_STATIC = 1.0        # upstream of a failing output (static cone only)
_W_EXONERATED = -1.0   # also feeds at least one passing output
_W_MUTED = 1.5         # drives an unresolved net this row
_W_HOT = 0.5           # selector/splitter — semantic-miswire hot spot
_W_CHILD_FAILED = 2.0  # subcircuit whose own tests fail
_W_CHILD_PASSED = -1.0 # subcircuit whose own tests pass (parent wiring likelier)


def localize(
    circuit: Circuit,
    netlist: NetList,
    graph: nx.MultiDiGraph,
    sim: SimResult,
    outputs_report: list[dict],
    *,
    max_suspects: int = 12,
    run_child_self_tests: bool = False,
    jar_path: str | None = None,
    child_test_timeout: float = 60.0,
) -> SuspectReport:
    """Rank suspects for ONE evaluated row.

    `outputs_report` is exactly /api/simulate's ``outputs`` list:
    ``[{"label": ..., "expected": ..., "found": ..., "ok": bool|None}]``.
    Outputs with ok=False are failing; ok=None (evaluator couldn't resolve
    them) are treated as failing too, since they cannot be right.
    """
    report = SuspectReport()
    failing = [o["label"] for o in outputs_report if o.get("ok") is not True]
    passing = [o["label"] for o in outputs_report if o.get("ok") is True]
    report.failing_outputs = failing
    report.passing_outputs = passing
    if not failing:
        report.notes.append("No failing outputs on this row; nothing to localize.")
        return report

    static_cones: dict[str, set[int]] = {}
    active_cones: dict[str, set[int]] = {}
    for label in failing:
        out_idx = _output_component_index(circuit, label)
        if out_idx is None:
            report.notes.append(f"Output column {label!r} has no Out component.")
            continue
        static_cones[label] = _static_cone(graph, out_idx)
        active_cones[label] = _active_cone(circuit, netlist, graph, sim, out_idx)

    passing_union: set[int] = set()
    for label in passing:
        out_idx = _output_component_index(circuit, label)
        if out_idx is not None:
            passing_union |= _static_cone(graph, out_idx)

    muted = _unresolved_drivers(netlist, sim)

    candidates: set[int] = set()
    for cone in static_cones.values():
        candidates |= cone

    suspects: list[Suspect] = []
    for idx in sorted(candidates):
        comp = circuit.components[idx]
        if comp.element_name in _NEVER_SUSPECT:
            continue
        in_static = [lb for lb, cone in static_cones.items() if idx in cone]
        in_active = [lb for lb, cone in active_cones.items() if idx in cone]
        if not in_static:
            continue

        is_sub = comp.element_name.endswith(".dig")
        score = 0.0
        reasons: list[str] = []
        if in_active:
            # Common-cause bonus: when several outputs fail, their shared
            # upstream origin outranks each output's private sinks.
            score += _W_ACTIVE + _W_SHARED * (len(in_active) - 1)
            reasons.append(
                "on the ACTIVE signal path of failing output(s) "
                + ", ".join(in_active)
            )
            if len(in_active) > 1:
                reasons.append(
                    f"common cause candidate: active in {len(in_active)} "
                    f"failing outputs' paths"
                )
        else:
            score += _W_STATIC
            reasons.append(
                "upstream of failing output(s) " + ", ".join(in_static)
                + " (not on the row's active mux path)"
            )
        exonerated = idx in passing_union
        if exonerated:
            score += _W_EXONERATED
            reasons.append("also feeds a passing output (partly exonerated)")
        drives_unres = idx in muted
        if drives_unres:
            score += _W_MUTED
            reasons.append("its output net stayed unresolved this row (muted)")
        if comp.element_name in _HOT_KINDS:
            score += _W_HOT
            reasons.append("selector/splitter — semantic-miswire hot spot")

        child_ref = None
        child_verdict = None
        if is_sub:
            sub = _child_of_instance(circuit, idx)
            child_ref = sub.reference if sub else comp.element_name
            reasons.append("subcircuit instance — expandable via drill-in")
            if run_child_self_tests:
                child_verdict = _run_child_self_test(
                    sub, jar_path, child_test_timeout,
                )
                if child_verdict == "failed":
                    score += _W_CHILD_FAILED
                    reasons.append("its OWN embedded tests fail")
                elif child_verdict == "passed":
                    score += _W_CHILD_PASSED
                    reasons.append(
                        "its OWN embedded tests pass (parent wiring likelier)"
                    )

        suspects.append(Suspect(
            component_index=idx,
            element_name=comp.element_name,
            display_name=_display_name(circuit, idx),
            score=round(score, 2),
            reasons=reasons,
            in_failing_cones=sorted(in_static),
            in_active_cones=sorted(in_active),
            feeds_passing_output=exonerated,
            drives_unresolved=drives_unres,
            is_subcircuit=is_sub,
            child_reference=child_ref,
            child_self_test=child_verdict,
        ))

    # Highest score first; ties broken deterministically by component index.
    suspects.sort(key=lambda s: (-s.score, s.component_index))
    if len(suspects) > max_suspects:
        report.notes.append(
            f"{len(suspects) - max_suspects} low-ranked suspect(s) beyond "
            f"max_suspects={max_suspects} were dropped."
        )
        suspects = suspects[:max_suspects]
    report.suspects = suspects
    return report


def merge_reports(reports: list[SuspectReport], *, max_suspects: int = 12) -> SuspectReport:
    """Aggregate per-row reports for a CLUSTER of failing rows: a part
    suspected on every row of the cluster outranks a one-row wonder."""
    merged = SuspectReport()
    if not reports:
        return merged
    for r in reports:
        for lb in r.failing_outputs:
            if lb not in merged.failing_outputs:
                merged.failing_outputs.append(lb)
        for lb in r.passing_outputs:
            if lb not in merged.passing_outputs:
                merged.passing_outputs.append(lb)

    by_idx: dict[int, Suspect] = {}
    hits: dict[int, int] = {}
    for r in reports:
        for s in r.suspects:
            hits[s.component_index] = hits.get(s.component_index, 0) + 1
            prev = by_idx.get(s.component_index)
            if prev is None:
                by_idx[s.component_index] = Suspect(**{
                    **s.__dict__, "reasons": list(s.reasons),
                    "in_failing_cones": list(s.in_failing_cones),
                    "in_active_cones": list(s.in_active_cones),
                })
            else:
                prev.score += s.score
                for reason in s.reasons:
                    if reason not in prev.reasons:
                        prev.reasons.append(reason)
                for lb in s.in_failing_cones:
                    if lb not in prev.in_failing_cones:
                        prev.in_failing_cones.append(lb)
                for lb in s.in_active_cones:
                    if lb not in prev.in_active_cones:
                        prev.in_active_cones.append(lb)

    n_rows = len(reports)
    out: list[Suspect] = []
    for idx, s in by_idx.items():
        s.score = round(s.score / n_rows + (hits[idx] / n_rows), 2)
        if hits[idx] == n_rows and n_rows > 1:
            s.reasons.append(f"suspected on all {n_rows} rows of the cluster")
        out.append(s)
    out.sort(key=lambda s: (-s.score, s.component_index))
    merged.suspects = out[:max_suspects]
    return merged