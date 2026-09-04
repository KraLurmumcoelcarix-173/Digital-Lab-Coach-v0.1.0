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


def test_bug1_active_cone_pins_the_mux_and_its_ground():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG1, col_equals=("Op", 3))
    rep = localize(c, nl, g, sim, outputs)
    idxs = rep.suspect_indices()
    assert 14 in idxs and 23 in idxs
    top3 = idxs[:3]
    assert 14 in top3 and 23 in top3, (
        f"dynamic slicing should rank the mux and its ground input on top; "
        f"got {[(s.component_index, s.display_name, s.score) for s in rep.suspects[:5]]}"
    )


def test_bug1_inactive_branch_is_downranked_not_active():
    c, nl, g, sim, outputs, _row = _row_evidence(_BUG1, col_equals=("Op", 3))
    rep = localize(c, nl, g, sim, outputs, max_suspects=50)
    by_idx = {s.component_index: s for s in rep.suspects}
    if 9 in by_idx:
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
    outputs = [{**o, "ok": True} for o in outputs]
    rep = localize(c, nl, g, sim, outputs)
    assert rep.suspects == []
    assert rep.failing_outputs == []
    assert rep.notes

from dlc.l3.evidence import _expected_ints, net_names_map, stuck_components
from dlc.l3.localizer import witness_steer
from dlc.sim.simulator import simulate_rows

_BUG9 = ("data/sample_circuits/30_bug_benchmark/bug9_swapped_select_gate/"
         "mini_alu_swapped_gate.dig")
_BUG9_DEAD_GATE = 12


def _bug9_failing_row(op):
    c = parse_dig_file(_BUG9)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    spec = extract_test_specs(c)[0]
    bindings = match_variables_to_io(spec.headers, c)
    sims = simulate_rows(c, nl, g, spec)
    col = {h: i for i, h in enumerate(spec.headers)}
    for row in spec.rows:
        if row.is_malformed or row.values[col["Op"]].value != op:
            continue
        sim = sims[row.line_index]
        exp = _expected_ints(spec, bindings, row)
        found = sim.output_values["Result"]
        if (found & 0xFF) == (exp["Result"][0] & 0xFF):
            continue        # a row that passes by coincidence
        outputs = [{"label": "Result", "expected": row.values[col["Result"]].raw,
                    "found": found, "ok": False}]
        return c, nl, g, sim, sims, outputs, exp
    raise AssertionError("no failing row for that op")


def test_bug9_dead_select_gate_becomes_the_top_suspect():
    c, nl, g, sim, sims, outputs, exp = _bug9_failing_row(2)
    stuck = stuck_components(c, nl, sims)
    assert set(stuck) == {_BUG9_DEAD_GATE}, stuck
    rep = localize(c, nl, g, sim, outputs, expected_values=exp, stuck=stuck,
                   net_names=net_names_map(c, nl))
    top = rep.suspects[0]
    assert top.component_index == _BUG9_DEAD_GATE and top.element_name == "And"
    assert any(r.startswith("SELECT-PATH suspect") for r in top.reasons)
    assert any("never changes over the whole testcase" in r for r in top.reasons)
    assert any("expected value found on net sumO" in n for n in rep.notes)
    by = {s.component_index: s for s in rep.suspects}
    assert 11 not in by or not any("SELECT-PATH" in r for r in by[11].reasons)
    plain = localize(c, nl, g, sim, outputs)
    assert _BUG9_DEAD_GATE not in [s.component_index for s in plain.suspects[:3]]


def test_witness_steer_declines_ambiguous_values():
    c, nl, g, sim, _sims, _outputs, exp = _bug9_failing_row(2)
    out_idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name == "Out")
    assert witness_steer(c, nl, g, sim, out_idx, 1, 8) == ({}, [])
    assert witness_steer(c, nl, g, sim, out_idx, 0xFF, 8) == ({}, [])
    assert witness_steer(c, nl, g, sim, out_idx, exp["Result"][0], 1) == ({}, [])
    boosted, notes = witness_steer(c, nl, g, sim, out_idx, exp["Result"][0], 8)
    assert _BUG9_DEAD_GATE in boosted and notes
    weight, reason = boosted[_BUG9_DEAD_GATE]
    assert 0 < weight <= 2.5 and "sel bit 1" in reason
