"""L3 Mode A evidence core (dlc/l3/evidence.py).

Ground truth comes from the seeded 30-bug benchmark, all offline (the
Python evaluator decides pass/fail — no Digital.jar):
  * bug3_wrong_cin       — every row fails on Sum (carry-in stuck high);
                           one cluster, Add + Const among the suspects.
  * bug1_meaningless_mux_in3 — only the two Op=3 rows fail; the cluster
                           signature carries Op, the mux and its Ground
                           rank on top.
  * bug4_missing_pipeline — clocked testcase, zero clocked elements:
                           the gross-check sends it to the lazy branch.
"""

import pytest

from dlc.l3.evidence import (
    Cluster,
    RowEvidence,
    assemble_evidence,
    assemble_evidence_for_file,
    build_payload,
    cluster_rows,
    gross_check,
    row_category,
    select_columns,
    _program_rom_out_net,
)
from dlc.l3.localizer import Suspect, SuspectReport
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.sim.simulator import simulate_sequential
from dlc.testing.spec import TestSpec, extract_test_specs

_BENCH = "data/sample_circuits/30_bug_benchmark"
_BUG1 = f"{_BENCH}/bug1_meaningless_mux_in3/tier3_calculator.dig"
_BUG3 = f"{_BENCH}/bug3_wrong_cin/Wrong_cin.dig"
_BUG4 = f"{_BENCH}/bug4_missing_pipeline/Missing_pipeline.dig"
_BUG5 = (f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
         f"wrong_bool_LED1.dig")
_CLEAN = "data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig"
_ROM = "data/sample_circuits/tier1_minimal/rom_lookup.dig"


def _parsed(path):
    c = parse_dig_file(path)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    return c, nl, g


# ---------------------------------------------------------------------------
# Mode decision on the benchmark
# ---------------------------------------------------------------------------

def test_bug3_is_analysis_with_one_cluster_and_the_const_suspect():
    # Digital's per-row verdict flags 2 of bug3's 4 rows (50% pass = right
    # at the 1-5 row bar); the evaluator-only sweep flags all 4, which the
    # tiered gate correctly calls a low-pass-rate circuit. Analysis tests
    # therefore feed the jar-style verdict through the caller seam.
    res = assemble_evidence_for_file(
        _BUG3, use_manifest=False, failing_indices=[0, 1],
    )
    assert res.mode == "analysis"
    assert res.failing_count == 2
    assert len(res.clusters) == 1
    cluster = res.clusters[0]
    assert [r.row_index for r in cluster.rows] == [0, 1]
    assert cluster.signature["columns"] == ["Sum"]
    idxs = cluster.merged.suspect_indices()
    assert 5 in idxs, "the Add itself must survive the merge"
    assert 16 in idxs, "the Const driving c_i (the seeded bug) must survive"


def test_small_circuit_is_exempt_from_the_rate_bars():
    # bug3 has 17 components (<= 30): even 0% passing stays analyzable —
    # a small Layer-3-ready circuit is exactly the close-to-answer case
    res = assemble_evidence_for_file(_BUG3, use_manifest=False)
    assert res.mode == "analysis"
    assert res.failing_count == 4
    assert [r.row_index for r in res.clusters[0].rows] == [0, 1, 2, 3]
    idxs = res.clusters[0].merged.suspect_indices()
    assert 5 in idxs and 16 in idxs


def test_bug1_cluster_signature_carries_op_and_pins_the_mux():
    res = assemble_evidence_for_file(_BUG1, use_manifest=False)
    assert res.mode == "analysis"
    assert res.failing_count == 2
    assert len(res.clusters) == 1
    cluster = res.clusters[0]
    assert [r.row_index for r in cluster.rows] == [6, 11]
    assert sorted(cluster.signature["columns"]) == ["Bit0", "Result", "Zero"]
    assert ["Op", "3"] in cluster.signature["selects"]
    top3 = cluster.merged.suspect_indices()[:3]
    assert 14 in top3 and 23 in top3, (
        "dynamic slicing must keep the mux and its Ground on top of the "
        f"merged report; got {top3}"
    )


def test_clean_circuit_is_clear():
    res = assemble_evidence_for_file(_CLEAN, use_manifest=False)
    assert res.mode == "clear"
    assert res.failing_count == 0
    assert res.clusters == [] and res.payloads == []
    assert res.gross_flags == []


def test_bug4_missing_pipeline_goes_lazy():
    res = assemble_evidence_for_file(_BUG4, use_manifest=False)
    assert res.mode == "lazy"
    assert res.failing_count == 2
    kinds = [f["kind"] for f in res.gross_flags]
    assert "missing_clocked_logic" in kinds
    assert res.clusters == [] and res.payloads == []


def test_big_circuit_below_its_bar_goes_lazy_before_any_evidence():
    # the LED lab has 160+ components and these caller-seam mismatches
    # scatter over FOUR columns (unfocused, so the bars apply): 1 of 5
    # rows passing (20%) is under the 30% bar
    res = assemble_evidence_for_file(
        _BUG5, use_manifest=False, failing_indices=[0, 1, 2, 3],
        jar_mismatches={
            0: [{"column": "Fa", "expected": "1", "found": "0"}],
            1: [{"column": "Fb", "expected": "1", "found": "0"}],
            2: [{"column": "Fe", "expected": "1", "found": "0"}],
            3: [{"column": "Fg", "expected": "1", "found": "0"}],
        },
    )
    assert res.mode == "lazy"
    kinds = [f["kind"] for f in res.gross_flags]
    assert "low_pass_rate" in kinds
    assert res.clusters == [] and res.payloads == []


def test_big_circuit_on_or_above_its_bar_is_analyzable():
    # 3 of 5 passing (60%) clears the 30% bar even without column focus
    res = assemble_evidence_for_file(
        _BUG5, use_manifest=False, failing_indices=[1, 2],
    )
    assert res.mode == "analysis"
    assert res.failing_count == 2


def _rows(n):
    from dlc.testing.spec import TestRow
    return [TestRow(raw="0 0", values=[], line_index=i) for i in range(n)]


def _spec_of(n):
    return TestSpec(name="t", component_index=0, headers=["A", "Sum"],
                    rows=_rows(n), raw_data_string="",
                    has_unexpanded_loops=False)


def test_tiered_pass_rate_bars():
    # exercised with the bars forced on (bug3 itself is a small circuit,
    # exempt by default — asserted at the end). v0.1.0 bars: 20/60/30 —
    # the >10-row bar was lowered from 80% to 20% (r34, instructor's
    # call) so a 27%-passing real cpu still gets Mode A analysis.
    c, _nl, _g = _parsed(_BUG3)
    on = {"rate_gate_min_components": 0}
    # big suite: >10 failing alone is fine while >=80% still passes
    assert gross_check(c, _spec_of(200), failing_count=15, **on) == []
    # the old 90% bar rejected this near-passing suite; 85% is analyzable
    assert gross_check(c, _spec_of(100), failing_count=15, **on) == []
    # big suite: 45% passing (11 of 20 failing) is analyzable at the 20% bar
    assert gross_check(c, _spec_of(20), failing_count=11, **on) == []
    # a 27%-passing cpu-style suite is analyzable too (the r34 motivator)
    assert gross_check(c, _spec_of(23), failing_count=17, **on) == []
    # 19 of 23 failing = 17% passing, but <= 20 absolute failures is
    # still analyzable (r35: max_failing raised from 10 to 20)
    assert gross_check(c, _spec_of(23), failing_count=19, **on) == []
    # big suite: MORE than 20 failing AND under 20% -> structural
    kinds = [f["kind"] for f in gross_check(c, _spec_of(30), 25, **on)]
    assert kinds == ["too_many_failures"]
    # big suite: many rows failing but <=10 absolute -> still analyzable
    assert gross_check(c, _spec_of(12), failing_count=2, **on) == []
    # 6-10 rows: 60% bar (3 of 8 failing = 62.5% passing = ok now)
    assert gross_check(c, _spec_of(8), failing_count=3, **on) == []
    assert [f["kind"] for f in gross_check(c, _spec_of(8), 4, **on)] == [
        "low_pass_rate"]
    # 1-5 rows: 30% bar (2 of 4 failing = 50% = ok; 3 of 4 = 25% = lazy)
    assert gross_check(c, _spec_of(4), failing_count=2, **on) == []
    assert [f["kind"] for f in gross_check(c, _spec_of(4), 3, **on)] == [
        "low_pass_rate"]
    # default threshold: bug3's 17 components sit under 30 -> bars off
    assert gross_check(c, _spec_of(4), failing_count=4) == []


# ---------------------------------------------------------------------------
# Gross-check pieces
# ---------------------------------------------------------------------------

def test_gross_check_flags_unbound_columns():
    c, _nl, _g = _parsed(_BUG3)
    fake = TestSpec(name="t", component_index=0, headers=["Ghost", "Sum"],
                    rows=[], raw_data_string="", has_unexpanded_loops=False)
    flags = gross_check(c, fake, failing_count=1)
    unbound = [f for f in flags if f["kind"] == "unbound_columns"]
    assert unbound and "'Ghost'" in unbound[0]["detail"]
    assert all(f["kind"] != "missing_clocked_logic" for f in flags)


def test_gross_check_quiet_on_registered_clocked_circuit():
    c, _nl, _g = _parsed(_BUG3)
    spec = extract_test_specs(c)[0]
    # 2 of 4 failing sits well above the 30% bar; registers exist, so no
    # missing_clocked_logic either — nothing gross about this circuit
    assert gross_check(c, spec, failing_count=2) == []


def test_select_columns_probe_and_hints():
    c, nl, _g = _parsed(_BUG1)
    spec = extract_test_specs(c)[0]
    assert select_columns(c, nl, spec) == ["Op"]
    c3, nl3, _g3 = _parsed(_BUG3)
    spec3 = extract_test_specs(c3)[0]
    assert select_columns(c3, nl3, spec3) == []


# ---------------------------------------------------------------------------
# Clustering (synthetic rows, pure logic)
# ---------------------------------------------------------------------------

def _fake_row(idx, columns, selects=(), category=None, suspects=()):
    report = SuspectReport(
        failing_outputs=list(columns),
        suspects=[
            Suspect(component_index=i, element_name="And",
                    display_name=f"And #{i}", score=1.0)
            for i in suspects
        ],
    )
    return RowEvidence(
        row_index=idx,
        raw=f"row {idx}",
        mismatches=[{"column": col, "expected": "1", "found": "0"}
                    for col in columns],
        selects=[list(s) for s in selects],
        category=category,
        suspect_report=report,
    )


def test_cluster_rows_splits_on_disjoint_suspects():
    rows = [
        _fake_row(0, ["Sum"], suspects=(1, 2)),
        _fake_row(1, ["Sum"], suspects=(1, 2, 3)),
        _fake_row(2, ["Sum"], suspects=(8, 9)),
    ]
    clusters, _notes = cluster_rows(rows)
    assert [[r.row_index for r in c.rows] for c in clusters] == [[0, 1], [2]]


def test_cluster_rows_keeps_empty_suspect_rows_together():
    rows = [_fake_row(0, ["Sum"]), _fake_row(1, ["Sum"])]
    clusters, _notes = cluster_rows(rows)
    assert len(clusters) == 1 and len(clusters[0].rows) == 2


def test_cluster_rows_separates_by_selects_and_category():
    rows = [
        _fake_row(0, ["R"], selects=[("Op", "1")], suspects=(1,)),
        _fake_row(1, ["R"], selects=[("Op", "2")], suspects=(1,)),
        _fake_row(2, ["R"], selects=[("Op", "2")], category="addi",
                  suspects=(1,)),
    ]
    clusters, _notes = cluster_rows(rows)
    assert len(clusters) == 3


def test_cluster_cap_folds_overflow_instead_of_dropping():
    rows = [_fake_row(i, [f"O{i}"], suspects=(i,)) for i in range(6)]
    clusters, notes = cluster_rows(rows, cap=4)
    assert len(clusters) == 4
    assert sum(len(c.rows) for c in clusters) == 6, "no row may be dropped"
    assert sum(c.folded_rows for c in clusters) == 2
    assert notes and "folded" in notes[0]


# ---------------------------------------------------------------------------
# Payload shape (frozen l3.debug.v1.1 §3)
# ---------------------------------------------------------------------------

def test_payload_matches_frozen_contract_shape():
    res = assemble_evidence_for_file(
        _BUG3, use_manifest=False, failing_indices=[0, 1],
    )
    payload = res.payloads[0]
    assert set(payload) == {"contract", "circuit", "testcase", "cluster",
                            "suspects", "suspect_wiring"}
    assert payload["contract"] == "l3.debug.v1.1"
    assert payload["testcase"] == {"name": "Testcase_12",
                                   "headers": ["A", "B", "Clk", "Sum"]}
    assert {"inventory", "selectors", "inputs", "outputs"} <= set(
        payload["circuit"])
    cluster = payload["cluster"]
    assert set(cluster) == {"rows", "representative_evidence"}
    assert [set(r) for r in cluster["rows"]] == [
        {"index", "raw", "mismatches"}] * 2
    reps = cluster["representative_evidence"]
    assert len(reps) == 2, "full per-net evidence for at most 2 rows"
    for rep in reps:
        assert set(rep) == {"row_index", "net_values", "unresolved_nets",
                            "outputs"}
        some_net = next(iter(rep["net_values"].values()))
        assert set(some_net) == {"value", "bits", "hex"}
        for out in rep["outputs"]:
            assert set(out) == {"label", "expected", "found", "ok"}
    assert payload["suspects"]["failing_outputs"] == ["Sum"]
    assert payload["suspects"]["suspects"], "merged suspects must be present"


def test_suspect_wiring_names_the_true_driver():
    # bug3 has FOUR identical Consts; only #16 feeds the adder's carry-in.
    # The wiring block must say so, or the sub-agent is left guessing.
    res = assemble_evidence_for_file(
        _BUG3, use_manifest=False, failing_indices=[0, 1],
    )
    wiring = res.payloads[0]["suspect_wiring"]
    assert any(w["component_index"] == 16 for w in wiring)
    w16 = next(w for w in wiring if w["component_index"] == 16)
    ends = [(e["component_index"], e["pin"])
            for p in w16["pins"] for e in p["connects_to"]]
    assert (5, "c_i") in ends, "Const #16 must be shown driving Add.c_i"


def test_jar_verdict_is_authoritative_when_evaluator_disagrees():
    cells = [{"column": "Sum", "expected": "9", "found": "1"}]
    res = assemble_evidence_for_file(
        _CLEAN, use_manifest=False,
        failing_indices=[0], jar_mismatches={0: cells},
    )
    assert res.mode == "analysis"
    assert res.failing_count == 1
    assert res.clusters[0].rows[0].mismatches == cells
    assert any("cannot reproduce" in n for n in res.notes)


# ---------------------------------------------------------------------------
# Program-category plumbing
# ---------------------------------------------------------------------------

def test_row_category_is_none_without_a_program_rom():
    c, nl, g = _parsed(_BUG3)
    assert _program_rom_out_net(c, nl) is None
    spec = extract_test_specs(c)[0]
    sim = simulate_sequential(c, nl, g, spec, 0)
    manifest = {"program_decode": {"fields": {"opcode": [0, 7]}}}
    assert row_category(c, nl, sim, manifest) is None


def test_row_category_decodes_through_a_program_rom():
    c = parse_dig_file(_ROM)
    rom_idx = next(i for i, comp in enumerate(c.components)
                   if comp.element_name == "ROM")
    c.components[rom_idx].attributes["isProgramMemory"] = "true"
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    nid = _program_rom_out_net(c, nl)
    assert nid is not None
    spec = extract_test_specs(c)[0]
    sim = simulate_sequential(c, nl, g, spec, 0)
    word = sim.net_values.get(nid)
    assert word is not None, "probe row must resolve the ROM output"
    manifest = {
        "program_decode": {"fields": {"opcode": [0, 4]},
                           "categories_from": "ops"},
        "categories": {"ops": [{"name": "demo",
                                "when": {"opcode": word & 0xF}}]},
    }
    got = row_category(c, nl, sim, manifest)
    assert got is not None
    assert got["category"] == "demo"
    assert got["word"] == f"{word:x}"
    assert got["fields"]["opcode"] == word & 0xF


def test_suspect_wiring_carries_pin_values_for_representative_rows():
    # the LED-lab lesson: same-scored suspects separate by BEHAVIOR, so
    # every wiring pin carries its actual value on the representative rows
    res = assemble_evidence_for_file(
        _BUG3, use_manifest=False, failing_indices=[0, 1],
    )
    w16 = next(w for w in res.payloads[0]["suspect_wiring"]
               if w["component_index"] == 16)
    out_pin = next(p for p in w16["pins"] if p["direction"] == "out")
    assert out_pin["values"] == {"0": 1, "1": 1}, \
        "the carry-in Const must show value 1 on both representative rows"


def test_case3_fixture_is_clean_but_hides_the_mux_bug():
    # bug6: bug1's circuit with the Op=3 rows REMOVED — passes everything,
    # so Mode B's select-coverage gate is the only trace of the hidden
    # bug (the raw arm note is folded into the gate card, not repeated)
    path = (f"{_BENCH}/bug6_hidden_mux_case3/uncovered_op_calculator.dig")
    res = assemble_evidence_for_file(path, use_manifest=False)
    assert res.mode == "clear"
    from dlc.l3.coverage import scan_tree_coverage
    rep = scan_tree_coverage(path)
    root = rep.circuits[0]
    assert root.flags == []
    assert rep.select_gate and rep.select_gate[0]["missing"] == [3]
    assert not any("never selected" in n for n in (root.notes or []))


def test_focus_is_a_requisite_not_an_amnesty():
    # v0.1.0: LED5 with 5 of 5 rows failing in ONE column (Ff) meets the
    # focus requisite (no scattered rows) but no longer skips the rate
    # bars — 0% passing is under the 30% bar, so the run is lazy. "You
    # have <=3 columns wrong, so you can debug? no way" — the focus is a
    # precondition, the pass rate still gets the last word.
    led5 = (f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
            f"wrong_bool_LED5.dig")
    cells = {i: [{"column": "Ff", "expected": "1", "found": "0"}]
             for i in range(5)}
    res = assemble_evidence_for_file(
        led5, use_manifest=False,
        failing_indices=[0, 1, 2, 3, 4], jar_mismatches=cells,
    )
    assert res.mode == "lazy"
    kinds = [f["kind"] for f in res.gross_flags]
    assert "low_pass_rate" in kinds
    assert "scattered_failures" not in kinds
    # without column info the requisite stays silent; the bars still judge
    res2 = assemble_evidence_for_file(
        led5, use_manifest=False, failing_indices=[0, 1, 2, 3, 4],
    )
    assert res2.mode == "lazy"


def test_scattered_rows_are_lazy_regardless_of_pass_rate():
    # Charles's ruling: failing rows wrong in 4+ output columns AT ONCE
    # are a fundamentals symptom even when the pass rate looks fine.
    # 3 of 5 rows pass (60%, above the 30% bar) — but TWO of the five
    # rows scatter over four columns: 2/5 = 40% >= 25% of ALL rows -> lazy.
    led5 = (f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
            f"wrong_bool_LED5.dig")
    four = [{"column": c, "expected": "1", "found": "0"}
            for c in ("Fa", "Fb", "Fd", "Ff")]
    res = assemble_evidence_for_file(
        led5, use_manifest=False, failing_indices=[1, 2],
        jar_mismatches={1: four, 2: four},
    )
    assert res.mode == "lazy"
    assert [f["kind"] for f in res.gross_flags] == ["scattered_failures"]


def test_rare_scattered_row_in_a_long_suite_passes():
    # one scattered row in a 20-row suite (1/20 = 5% < 25% of ALL rows)
    # usually shares the focused rows' root cause — it passes; the same
    # scattered row in a 4-row suite is 25% of the testcase -> lazy
    c, _nl, _g = _parsed(_BUG3)
    on = {"rate_gate_min_components": 0}
    rows_cols = [{"Fa", "Fb", "Fd", "Ff"}] + [{"Ff"}] * 4
    assert gross_check(c, _spec_of(20), failing_count=5,
                       row_mismatch_columns=rows_cols, **on) == []
    flags = gross_check(c, _spec_of(4), failing_count=4,
                        row_mismatch_columns=rows_cols[:4], **on)
    assert "scattered_failures" in [f["kind"] for f in flags]


def test_dead_trunk_full_output_surface_is_analyzable():
    # r37 (s008 control unit): every failing row wrong on EVERY output
    # column = one mechanism upstream of all outputs (dead stage, empty
    # decode ROM) — analyzable despite 0% passing. A shared SUBSET of
    # columns keeps the ratified bars (tests above).
    led5 = (f"{_BENCH}/bug5_wrong_boolean_gate_decoder_logic/"
            f"wrong_bool_LED5.dig")
    all_cols = [{"column": c, "expected": "1", "found": "0"}
                for c in ("Fa", "Fb", "Fc", "Fd", "Fe", "Ff", "Fg")]
    res = assemble_evidence_for_file(
        led5, use_manifest=False, failing_indices=[0, 1, 2, 3, 4],
        jar_mismatches={i: list(all_cols) for i in range(5)},
    )
    assert res.mode == "analysis"
    assert res.failing_count == 5
