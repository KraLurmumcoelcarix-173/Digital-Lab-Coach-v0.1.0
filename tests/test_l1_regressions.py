"""L1 false-positive regressions from real student lab files (r27).

Two field bugs, both re-created here on synthetic fixtures:

  * A rotation-2 (upside-down) Decoder was flagged "undriven sel" because
    the pin table put sel one grid row below its real spot — Digital's
    Decoder sel sits at the LAST output's height, (20, (n-1)*20), not at
    (20, n*20) like the Multiplexer. Invisible on upright decoders (a
    wire stub usually crosses both candidates); a flipped decoder whose
    sel is fed by a tunnel sitting exactly ON the pin exposed it.
  * A Demultiplexer-based register-file write path produced 32 "undriven
    input" flags (one per enable And gate): Demultiplexer had no pin
    geometry at all, so its outputs drove nothing.
"""

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.models import Component, Position
from dlc.parser.netlist import build_netlist
from dlc.parser.pin_geometry import get_pin_specs
from dlc.analyzer import check_all_l1
from dlc.sim import simulator as sim
from dlc.sim.simulator import simulate

_DIR = "data/sample_circuits/l1_regressions"


def _c(element, **attrs):
    return Component(element_name=element, position=Position(0, 0),
                     attributes=attrs, label=attrs.get("Label"))


def _pin(specs, name):
    return next(p for p in specs if p.name == name)


# ---- the two field circuits, re-created synthetically ----------------------

def test_flipped_decoder_sel_is_not_flagged():
    c = parse_dig_file(f"{_DIR}/flipped_decoder_selfeed.dig")
    assert check_all_l1(c).issues == []


def test_demux_write_enable_fanout_is_not_flagged():
    c = parse_dig_file(f"{_DIR}/demux_write_enable.dig")
    assert check_all_l1(c).issues == []


# ---- pin-table geometry ----------------------------------------------------

def test_decoder_sel_sits_at_last_output_height():
    d = _c("Decoder", **{"Selector Bits": 5})
    sel = _pin(get_pin_specs(d), "sel")
    assert (sel.offset_x, sel.offset_y) == (20, 620)     # (n-1)*20, NOT 640


def test_decoder_flip_sel_pos_moves_sel_to_top():
    d = _c("Decoder", **{"Selector Bits": 5, "flipSelPos": True})
    sel = _pin(get_pin_specs(d), "sel")
    assert (sel.offset_x, sel.offset_y) == (20, -20)


def test_demux_pin_layout_matches_measured_lab_geometry():
    # Selector Bits=5 measured live: in on the left middle, sel at the
    # bottom (mux rule), outputs on the right at x=40
    d = _c("Demultiplexer", **{"Selector Bits": 5})
    specs = get_pin_specs(d)
    assert (_pin(specs, "in").offset_x, _pin(specs, "in").offset_y) == (0, 320)
    assert (_pin(specs, "sel").offset_x, _pin(specs, "sel").offset_y) == (20, 640)
    assert (_pin(specs, "out_0").offset_x, _pin(specs, "out_0").offset_y) == (40, 0)
    assert (_pin(specs, "out_31").offset_x, _pin(specs, "out_31").offset_y) == (40, 620)
    assert sum(1 for p in specs if p.direction == "out") == 32


def test_demux_two_way_uses_wide_spacing_like_the_two_way_mux():
    d = _c("Demultiplexer", **{"Selector Bits": 1})
    specs = get_pin_specs(d)
    assert (_pin(specs, "in").offset_y, _pin(specs, "sel").offset_y) == (20, 40)
    assert (_pin(specs, "out_0").offset_y, _pin(specs, "out_1").offset_y) == (0, 40)


def test_mux_and_demux_flip_sel_pos_moves_sel_to_top():
    for name in ("Multiplexer", "Demultiplexer"):
        d = _c(name, **{"Selector Bits": 2, "flipSelPos": True})
        sel = _pin(get_pin_specs(d), "sel")
        assert (sel.offset_x, sel.offset_y) == (20, -20), name


# ---- demux value semantics -------------------------------------------------

def test_demux_routes_input_and_zeroes_the_rest():
    d = _c("Demultiplexer", **{"Selector Bits": 2})
    out = sim._eval_demux(d, {"in": 1, "sel": 3})
    assert out == {"out_0": 0, "out_1": 0, "out_2": 0, "out_3": 1}
    out = sim._eval_demux(d, {"in": 0, "sel": 3})
    assert out == {"out_0": 0, "out_1": 0, "out_2": 0, "out_3": 0}
    assert sim._eval_demux(d, {"sel": 1}) is None        # honest: in unknown


def test_demux_fixture_simulates_end_to_end():
    c = parse_dig_file(f"{_DIR}/demux_write_enable.dig")
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    res = simulate(c, nl, g, {"RegWrite": 1, "WriteReg": 0})
    assert res.output_values.get("En0") == 1
    assert res.output_values.get("En31") == 0
    res = simulate(c, nl, g, {"RegWrite": 1, "WriteReg": 31})
    assert res.output_values.get("En0") == 0
    assert res.output_values.get("En31") == 1
    res = simulate(c, nl, g, {"RegWrite": 0, "WriteReg": 31})
    assert res.output_values.get("En31") == 0


# ---- r28: Digital's built-in RegisterFile ----------------------------------
# Field bug: student CPUs built on the Memory-category RegisterFile element
# (dual async read ports + one clocked write port) — completely unsupported,
# so every read net flagged undriven. The fixture's expected rows were
# validated against Digital.jar itself before being locked here.

def test_registerfile_fixture_is_l1_clean():
    c = parse_dig_file(f"{_DIR}/regfile_write_read.dig")
    assert check_all_l1(c).issues == []


def test_registerfile_pin_layout():
    from dlc.parser.pin_geometry import absolute_pin_positions
    rf = _c("RegisterFile", **{"AddrBits": 5, "Bits": 32})
    by_name = {s.name: (p.x, p.y) for p, s in absolute_pin_positions(rf)}
    assert by_name == {
        "Din": (0, 0), "we": (0, 20), "Rw": (0, 40), "C": (0, 60),
        "Ra": (0, 80), "Rb": (0, 100), "Da": (80, 0), "Db": (80, 20),
    }


def test_registerfile_sequential_semantics_match_digital():
    # write r0=5 -> write r1=7 -> we=0 blocks -> overwrite r1=3;
    # reads are post-edge, unwritten words read 0 (jar-verified rows)
    from dlc.sim.simulator import simulate_sequential
    from dlc.testing.spec import extract_test_specs
    c = parse_dig_file(f"{_DIR}/regfile_write_read.dig")
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    sp = extract_test_specs(c)[0]
    expected = {0: (5, 0), 1: (5, 7), 2: (0, 7), 3: (3, 5)}
    for r in sp.rows:
        if r.is_malformed:
            continue
        res = simulate_sequential(c, nl, g, sp, r.line_index)
        got = (res.output_values.get("DA"), res.output_values.get("DB"))
        assert got == expected[r.line_index], f"row {r.line_index}: {got}"


def test_registerfile_breaks_combinational_loops():
    # Da -> logic -> Din is a CLOCKED path, not a combinational loop
    from dlc.facts.extractor import _CLOCKED_ELEMENTS
    assert "RegisterFile" in _CLOCKED_ELEMENTS


# ---- r28: version-skewed subcircuit children -------------------------------
# Field bug: pairing a student CPU with a DIFFERENT version of its child
# .dig files (ports changed -> Digital drew a different box) sprayed
# unrelated-looking undriven/width/multi-driver errors. The forensic
# signature — dangling wire ends exactly on the instance's pin rows or
# columns — now folds into ONE actionable shape-mismatch error.

def test_version_skewed_child_reports_one_mismatch_not_a_flood():
    c = parse_dig_file(f"{_DIR}/skew/skew_parent.dig")
    issues = check_all_l1(c).issues
    kinds = [i.kind for i in issues]
    assert kinds == ["subcircuit_shape_mismatch"]
    assert "different version" in issues[0].message


def test_matching_child_stays_clean_no_false_mismatch():
    # the skew child ITSELF (a well-formed pair of files) must not flag
    c = parse_dig_file(f"{_DIR}/skew/skew_child.dig")
    assert check_all_l1(c).issues == []


# ---- r28: ROM run-length Data syntax ---------------------------------------

def test_rom_run_length_data_expands():
    from dlc.sim.simulator import _rom_words
    rom = _c("ROM", **{"Data": "a,3*b,c", "Bits": 8, "AddrBits": 4})
    assert _rom_words(rom) == [0xA, 0xB, 0xB, 0xB, 0xC]
