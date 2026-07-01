"""Combinational value evaluator (dlc.sim).

Two layers of coverage:
  * pure per-component rules (multi-bit gates, inverter bubbles, mux, decoder,
    splitter merge/split, adder carry, comparator) driven by hand-built
    Components, and
  * end-to-end `simulate()` over real fixtures, asserting both correctness of
    the computed outputs and the honest unresolved-net boundary for clocked
    circuits.
"""

from dlc.parser.models import Component, Position
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.testing.spec import extract_test_specs
from dlc.sim.simulator import simulate, simulate_sequential, inputs_for_row
from dlc.sim import simulator as sim


def _c(element, **attrs):
    return Component(element_name=element, position=Position(0, 0),
                     attributes=attrs, label=attrs.get("Label"))


# ---- pure gate rules -------------------------------------------------------

def test_and_or_xor_multibit():
    g = _c("And", Bits=4, Inputs=2)
    assert sim._eval_gate(g, {"in0": 0b1100, "in1": 0b1010}) == {"Y": 0b1000}
    g = _c("Or", Bits=4, Inputs=2)
    assert sim._eval_gate(g, {"in0": 0b1100, "in1": 0b1010}) == {"Y": 0b1110}
    g = _c("XOr", Bits=4, Inputs=2)
    assert sim._eval_gate(g, {"in0": 0b1100, "in1": 0b1010}) == {"Y": 0b0110}


def test_nand_nor_xnor_negate_and_mask():
    g = _c("NAnd", Bits=4, Inputs=2)
    assert sim._eval_gate(g, {"in0": 0b1111, "in1": 0b1111}) == {"Y": 0b0000}
    g = _c("NOr", Bits=4, Inputs=2)
    assert sim._eval_gate(g, {"in0": 0, "in1": 0}) == {"Y": 0b1111}
    g = _c("XNOr", Bits=1, Inputs=2)
    assert sim._eval_gate(g, {"in0": 1, "in1": 1}) == {"Y": 1}


def test_gate_inverter_bubble_negates_named_input():
    # In_1 (1-indexed) -> in0 gets a bubble: A' AND B
    g = _c("And", Bits=1, Inputs=2, inverterConfig=["In_1"])
    assert sim._eval_gate(g, {"in0": 0, "in1": 1}) == {"Y": 1}
    assert sim._eval_gate(g, {"in0": 1, "in1": 1}) == {"Y": 0}


def test_gate_unresolved_when_a_wired_input_missing():
    g = _c("And", Bits=1, Inputs=3)
    assert sim._eval_gate(g, {"in0": 1, "in1": 1}) is None  # in2 missing


def test_mux_selects_the_addressed_input():
    m = _c("Multiplexer", Bits=4)
    m.attributes["Selector Bits"] = 2
    vals = {"in0": 10, "in1": 11, "in2": 12, "in3": 13, "sel": 2}
    assert sim._eval_mux(m, vals) == {"out": 12}


def test_decoder_is_one_hot():
    d = _c("Decoder")
    d.attributes["Selector Bits"] = 2
    out = sim._eval_decoder(d, {"sel": 1})
    assert out == {"out_0": 0, "out_1": 1, "out_2": 0, "out_3": 0}


def test_splitter_merges_two_nibbles_then_splits_byte():
    merge = _c("Splitter")
    merge.attributes["Input Splitting"] = "4,4"
    merge.attributes["Output Splitting"] = "8"
    assert sim._eval_splitter(merge, {"in0": 0xA, "in1": 0x5}) == {"out0": 0x5A}

    split = _c("Splitter")
    split.attributes["Input Splitting"] = "8"
    split.attributes["Output Splitting"] = "4,4"
    assert sim._eval_splitter(split, {"in0": 0x5A}) == {"out0": 0xA, "out1": 0x5}


def test_adder_carry_out():
    a = _c("Add", Bits=4)
    assert sim._eval_add(a, {"a": 0xF, "b": 0x1, "c_i": 0}) == {"s": 0x0, "c_o": 1}
    assert sim._eval_add(a, {"a": 0x2, "b": 0x3, "c_i": 1}) == {"s": 0x6, "c_o": 0}


def test_comparator():
    cmp = _c("Comparator", Bits=4)
    assert sim._eval_comparator(cmp, {"A": 5, "B": 3}) == {"gr": 1, "eq": 0, "le": 0}
    assert sim._eval_comparator(cmp, {"A": 3, "B": 3}) == {"gr": 0, "eq": 1, "le": 0}


# ---- end-to-end over fixtures ---------------------------------------------

_T1 = "data/sample_circuits/tier1_minimal"
_T3 = "data/sample_circuits/tier3_realistic"


def _load(path):
    c = parse_dig_file(path)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    return c, nl, g


def test_single_and_every_row_matches_truth_table():
    c, nl, g = _load(f"{_T1}/single_and.dig")
    spec = extract_test_specs(c)[0]
    for row in spec.rows:
        if row.is_malformed:
            continue
        inp = inputs_for_row(c, spec.headers, row)
        res = simulate(c, nl, g, inp)
        assert res.output_values.get("Y") == (inp["A"] & inp["B"]), row.raw
        # fully combinational -> every net carries a value
        assert not res.unresolved_nets, row.raw


def test_tier3_calculator_full_coverage_and_correct_arithmetic():
    c, nl, g = _load(f"{_T3}/tier3_calculator.dig")
    spec = extract_test_specs(c)[0]
    rows = [r for r in spec.rows if not r.is_malformed]
    for row in rows:
        inp = inputs_for_row(c, spec.headers, row)
        res = simulate(c, nl, g, inp)
        # this ALU-style circuit is pure combinational: no blank wires
        assert not res.unresolved_nets, row.raw
        assert len(res.net_values) == len(nl.nets), row.raw
    # spot-check add (Op 0) and or (Op 3) rows against the testcase's expected
    bindings = {h: i for i, h in enumerate(spec.headers)}
    for row in rows:
        inp = inputs_for_row(c, spec.headers, row)
        res = simulate(c, nl, g, inp)
        expected_result = row.values[bindings["Result"]].value
        assert res.output_values.get("Result") == expected_result, row.raw


def test_clocked_circuit_leaves_register_wires_unresolved_in_single_pass():
    c, nl, g = _load(f"{_T3}/pipelined_adder_correct.dig")
    spec = extract_test_specs(c)[0]
    row = next(r for r in spec.rows if not r.is_malformed)
    inp = inputs_for_row(c, spec.headers, row)
    res = simulate(c, nl, g, inp)  # no state -> combinational only
    assert res.net_values, "input-side combinational nets should still resolve"
    assert res.unresolved_nets, "register-fed nets must stay blank"
    assert any("clocked" in n.lower() for n in res.notes)

def test_const_defaults_to_one_when_value_omitted():
    # matches Digital + dlc.facts.extractor: omitted Value == 1 (write-enables)
    assert sim._eval_const(_c("Const"), {}) == {"out": 1}
    assert sim._eval_const(_c("Const", Value=0), {}) == {"out": 0}


def _seq_matches_expected(path):
    c, nl, g = _load(path)
    spec = extract_test_specs(c)[0]
    col = {h: i for i, h in enumerate(spec.headers)}
    mismatches = []
    for row in spec.rows:
        if row.is_malformed:
            continue
        res = simulate_sequential(c, nl, g, spec, row.line_index)
        for h in spec.headers:
            tok = row.values[col[h]]
            if tok.kind == "int" and h in res.output_values:
                if res.output_values[h] != tok.value:
                    mismatches.append((row.raw, h, tok.value, res.output_values[h]))
    return mismatches


def test_pipeline_sequential_replay_matches_reference_outputs():
    # the CORRECT reference: replaying rows in order reproduces the pipeline,
    # including the 2-cycle latency that lands 3+4=7 on the third row.
    assert _seq_matches_expected(f"{_T3}/pipelined_adder_correct.dig") == []


def test_latched_seven_seg_display_matches_reference_outputs():
    assert _seq_matches_expected(f"{_T3}/tier3_latched_display.dig") == []


def test_buggy_circuit_is_faithfully_reproduced_for_red_mismatch():
    # A known-buggy circuit (wrong carry-in): our evaluator must compute the
    # *wrong* output the real circuit produces, so the failed-row UI can show
    # expected-vs-found. Here found should differ from the testcase's expected.
    mism = _seq_matches_expected(
        "data/sample_circuits/30_bug_benchmark/bug3_wrong_cin/Wrong_cin.dig"
    )
    assert mism, "evaluator should reproduce the bug (found != expected)"

def test_rom_reads_the_word_at_the_address():
    r = _c("ROM", Data="0A, 14, FF", Bits=8, AddrBits=4)   # hex tokens
    assert sim._rom_words(r) == [0x0A, 0x14, 0xFF]
    assert sim._eval_rom(r, {"A": 0}) == {"D": 0x0A}
    assert sim._eval_rom(r, {"A": 2}) == {"D": 0xFF}
    assert sim._eval_rom(r, {"A": 9}) == {"D": 0}         # out of range -> 0
    assert sim._eval_rom(r, {"A": 2, "sel": 0}) == {"D": 0}  # chip-select low
    assert sim._eval_rom(_c("ROM", Data="1FF", Bits=8), {"A": 0}) == {"D": 0xFF}  # masked


def test_rom_lookup_fixture_resolves_every_row():
    c, nl, g = _load(f"{_T1}/rom_lookup.dig")
    spec = extract_test_specs(c)[0]
    col = {h: i for i, h in enumerate(spec.headers)}
    for row in spec.rows:
        if row.is_malformed:
            continue
        res = simulate_sequential(c, nl, g, spec, row.line_index)
        assert res.output_values.get("D") == row.values[col["D"]].value, row.raw
        assert not res.unresolved_nets, row.raw

