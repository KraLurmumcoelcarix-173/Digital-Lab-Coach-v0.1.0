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
    # the LED lab has 160+ components, so the bars apply: 3 of its 5
    # rows failing (40% pass) is under the 1-5 row bar
    res = assemble_evidence_for_file(
        _BUG5, use_manifest=False, failing_indices=[0, 1, 2],
    )
    assert res.mode == "lazy"
    kinds = [f["kind"] for f in res.gross_flags]
    assert "low_pass_rate" in kinds
    assert res.clusters == [] and res.payloads == []


def test_big_circuit_on_or_above_its_bar_is_analyzable():
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
    # exempt by default — asserted at the end)
    c, _nl, _g = _parsed(_BUG3)
    on = {"rate_gate_min_components": 0}
    # big suite: >10 failing alone is fine while >=90% still passes
    assert gross_check(c, _spec_of(200), failing_count=15, **on) == []
    # big suite: >10 failing AND under 90% -> structural
    kinds = [f["kind"] for f in gross_check(c, _spec_of(20), 11, **on)]
    assert kinds == ["too_many_failures"]
    # big suite: many rows failing but <=10 absolute -> still analyzable
    assert gross_check(c, _spec_of(12), failing_count=2, **on) == []
    # 6-10 rows: 80% bar
    assert gross_check(c, _spec_of(8), failing_count=1, **on) == []
    assert [f["kind"] for f in gross_check(c, _spec_of(8), 2, **on)] == [
        "low_pass_rate"]
    # 1-5 rows: 50% bar
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
    # 2 of 4 failing sits exactly on the 50% bar; registers exist, so no
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
