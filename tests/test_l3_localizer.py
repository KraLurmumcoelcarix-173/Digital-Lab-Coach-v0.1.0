"""L3 fault localizer (dlc/l3/localizer.py).

Ground truth comes from the seeded 30-bug benchmark:
  * bug3_wrong_cin      — Add.c_i tied to a Const whose omitted Value
                          defaults to 1 (carry-in stuck high).
  * bug1_meaningless_mux_in3 — the result mux's in3 tied to Ground
                          instead of the boolean unit's output.
"""

import pytest

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.sim.simulator import simulate_sequential
from dlc.testing.spec import extract_test_specs, match_variables_to_io
from dlc.l3.localizer import localize, merge_reports

_BUG3 = "data/sample_circuits/30_bug_benchmark/bug3_wrong_cin/Wrong_cin.dig"
_BUG1 = "data/sample_circuits/30_bug_benchmark/bug1_meaningless_mux_in3/tier3_calculator.dig"


def _masked_eq(found, expected, width):
    if width:
        m = (1 << width) - 1
        return (found & m) == (expected & m)
    return found == expected


def _row_evidence(path, *, want_fail=True, col_equals=None):
    """(circuit, netlist, graph, sim, outputs_report, row) for the first row
    matching the filters, with outputs_report in /api/simulate's shape."""
    c = parse_dig_file(path)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    spec = extract_test_specs(c)[0]
    bindings = match_variables_to_io(spec.headers, c)
    col = {h: i for i, h in enumerate(spec.headers)}
    out_cols = [h for h, b in bindings.items() if b.role == "output"]
    widths = {h: bindings[h].bit_width for h in out_cols}

    for row in spec.rows:
        if row.is_malformed:
            continue
        if col_equals:
            name, value = col_equals
            tok = row.values[col[name]]
            if tok.kind != "int" or tok.value != value:
                continue
        sim = simulate_sequential(c, nl, g, spec, row.line_index)
        report, any_fail = [], False
        for h in out_cols:
            tok = row.values[col[h]]
            if tok.kind != "int" or tok.value is None:
                continue
            found = sim.output_values.get(h)
            ok = None if found is None else _masked_eq(found, tok.value, widths[h])
            any_fail |= ok is not True
            report.append({"label": h, "expected": tok.raw,
                           "found": found, "ok": ok})
        if want_fail and not any_fail:
            continue
        return c, nl, g, sim, report, row
    raise AssertionError(f"no matching row in {path}")


# ---------------------------------------------------------------------------
# bug3: wrong carry-in (pipelined adder)
# ---------------------------------------------------------------------------

def test_bug3_suspects_include_adder_and_carry_const():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG3)
    rep = localize(c, nl, g, sim, outputs)
    assert rep.failing_outputs == ["Sum"]
    idxs = rep.suspect_indices()
    assert 5 in idxs, "the Add itself must be a suspect"
    assert 16 in idxs, "the Const driving c_i (the seeded bug) must be a suspect"


def test_bug3_never_suspects_stimuli_or_annotations():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG3)
    rep = localize(c, nl, g, sim, outputs)
    kinds = {c.components[i].element_name for i in rep.suspect_indices()}
    assert kinds.isdisjoint({"In", "Clock", "Tunnel", "Testcase", "Rectangle"})


def test_bug3_suspects_are_ranked_and_reasoned():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG3)
    rep = localize(c, nl, g, sim, outputs)
    scores = [s.score for s in rep.suspects]
    assert scores == sorted(scores, reverse=True)
    assert all(s.reasons for s in rep.suspects)


# ---------------------------------------------------------------------------
# bug1: meaningless mux input (dynamic slicing showcase)
# ---------------------------------------------------------------------------

def test_bug1_active_cone_pins_the_mux_and_its_ground():
    # Op=3 selects the mux's in3, which the seeded bug ties to Ground.
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG1, col_equals=("Op", 3))
    rep = localize(c, nl, g, sim, outputs)
    idxs = rep.suspect_indices()
    assert 14 in idxs and 23 in idxs           # Multiplexer + Ground
    top3 = idxs[:3]
    assert 14 in top3 and 23 in top3, (
        f"dynamic slicing should rank the mux and its ground input on top; "
        f"got {[(s.component_index, s.display_name, s.score) for s in rep.suspects[:5]]}"
    )


def test_bug1_inactive_branch_is_downranked_not_active():
    # bool_unit feeds in2 — NOT selected when Op=3 — so it may appear as a
    # static (upstream) suspect but must not be on any ACTIVE cone.
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG1, col_equals=("Op", 3))
    rep = localize(c, nl, g, sim, outputs, max_suspects=50)
    by_idx = {s.component_index: s for s in rep.suspects}
    if 9 in by_idx:                             # the bool_unit instance
        assert by_idx[9].in_active_cones == []
        assert by_idx[9].score < by_idx[14].score


def test_bug1_subcircuit_is_marked_expandable():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG1, col_equals=("Op", 3))
    rep = localize(c, nl, g, sim, outputs, max_suspects=50)
    subs = [s for s in rep.suspects if s.is_subcircuit]
    assert subs and subs[0].child_reference == "bool_unit.dig"


def test_merge_reports_rewards_cluster_wide_suspects():
    c = parse_dig_file(_BUG1)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    spec = extract_test_specs(c)[0]
    bindings = match_variables_to_io(spec.headers, c)
    col = {h: i for i, h in enumerate(spec.headers)}
    out_cols = [h for h, b in bindings.items() if b.role == "output"]
    widths = {h: bindings[h].bit_width for h in out_cols}

    reports = []
    for row in spec.rows:
        if row.is_malformed or row.values[col["Op"]].value != 3:
            continue
        sim = simulate_sequential(c, nl, g, spec, row.line_index)
        outs = []
        any_fail = False
        for h in out_cols:
            tok = row.values[col[h]]
            if tok.kind != "int":
                continue
            found = sim.output_values.get(h)
            ok = None if found is None else _masked_eq(found, tok.value, widths[h])
            any_fail |= ok is not True
            outs.append({"label": h, "expected": tok.raw, "found": found, "ok": ok})
        if any_fail:
            reports.append(localize(c, nl, g, sim, outs))
    assert len(reports) >= 2, "bug1 should have at least two failing Op=3 rows"
    merged = merge_reports(reports)
    assert 14 in merged.suspect_indices()[:3]
    all_rows_reason = [
        s for s in merged.suspects
        if any("all" in r and "cluster" in r for r in s.reasons)
    ]
    assert all_rows_reason, "cluster-wide suspects should be called out"


def test_clean_row_produces_no_suspects():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG1, want_fail=False,
                                                 col_equals=("Op", 0))
    # force the all-pass shape regardless of which row matched
    outputs = [{**o, "ok": True} for o in outputs]
    rep = localize(c, nl, g, sim, outputs)
    assert rep.suspects == []
    assert rep.failing_outputs == []
    assert rep.notes