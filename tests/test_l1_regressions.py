"""L1 false-positive regressions from real student lab files.

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


# ---- real cpu-tree false positives, re-created synthetically ----------
# Field evidence: three complete student lab-5 trees that Digital builds
# and runs (warnings at most) drew ~39 L1 errors from five root causes.
# Each cause is pinned here on synthetic fixtures.

def _xml_circuit(elements: str, wires: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<circuit>\n'
        "  <version>2</version>\n  <attributes/>\n"
        f"  <visualElements>\n{elements}  </visualElements>\n"
        f"  <wires>\n{wires}  </wires>\n</circuit>\n"
    )


def _ve(name, x, y, entries="") -> str:
    attrs = f"<elementAttributes>{entries}</elementAttributes>" if entries \
        else "<elementAttributes/>"
    return (f"    <visualElement><elementName>{name}</elementName>"
            f"{attrs}<pos x=\"{x}\" y=\"{y}\"/></visualElement>\n")


def _entry(k, v, tag="string") -> str:
    return f"<entry><string>{k}</string><{tag}>{v}</{tag}></entry>"


def _w(x1, y1, x2, y2) -> str:
    return (f"    <wire><p1 x=\"{x1}\" y=\"{y1}\"/>"
            f"<p2 x=\"{x2}\" y=\"{y2}\"/></wire>\n")


def test_priority_encoder_f_output_exists_and_is_1bit():
    # Students wire PriorityEncoder.f (the "any input set" flag, directly
    # below num) as a ROM chip select; without it the ROM sel net looked
    # undriven.
    from dlc.facts.width import pin_width
    pe = _c("PriorityEncoder", **{"Selector Bits": 3})
    f = _pin(get_pin_specs(pe), "f")
    assert (f.offset_x, f.offset_y, f.direction) == (80, 20, "out")
    num = _pin(get_pin_specs(pe), "num")
    assert (num.offset_x, num.offset_y) == (80, 0)
    assert pin_width(pe, "f") == 1


def test_rom_output_sits_on_60_wide_box():
    # Digital's ROM box is 60 wide (SVG-verified): D at (60, 20). The old
    # (80, 20) survived only through loose endpoint snapping.
    rom = _c("ROM", AddrBits=4, Bits=8)
    d = _pin(get_pin_specs(rom), "D")
    assert (d.offset_x, d.offset_y) == (60, 20)


def test_unwired_output_does_not_grab_a_routing_corner(tmp_path):
    # s002/s008 comparator ladders: only eq is wired, and its wire turns a
    # corner 20 px from the unused gr pin. gr must NOT claim the corner —
    # that fabricated "gr and eq wired together" multi-driver errors.
    elements = (
        _ve("In", 0, 0, _entry("Label", "A") + _entry("Bits", 7, "int"))
        + _ve("In", 0, 20, _entry("Label", "B") + _entry("Bits", 7, "int"))
        + _ve("Comparator", 40, 0, _entry("Bits", 7, "int"))
        + _ve("Out", 180, 0, _entry("Label", "EQ"))
    )
    wires = (
        _w(0, 0, 40, 0) + _w(0, 20, 40, 20)
        + _w(100, 20, 120, 20)      # eq leaves its pin...
        + _w(120, 0, 120, 20)       # ...turns a corner at (120, 0)
        + _w(120, 0, 180, 0)        # ...20 px from unwired gr (100, 0)
    )
    p = tmp_path / "ladder.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    issues = check_all_l1(c)
    assert not [i for i in issues.issues if i.kind == "multi_driver"], [
        (i.kind, i.title) for i in issues.issues
    ]


def test_output_tied_to_ground_is_warning_not_error(tmp_path):
    # s008's register file shorts x0's Q to Ground on purpose ("always
    # 0"); Digital only errors at run time if the values ever disagree,
    # and its official test passes. One constant + one signal => warning.
    elements = (
        _ve("In", 0, 0, _entry("Label", "D") + _entry("Bits", 8, "int"))
        + _ve("Clock", 0, 20, _entry("Label", "C"))
        + _ve("Register", 60, 0, _entry("Bits", 8, "int"))
        + _ve("Ground", 160, 20, _entry("Bits", 8, "int"))
        + _ve("Out", 200, 60, _entry("Label", "Q") + _entry("Bits", 8, "int"))
    )
    wires = (
        _w(0, 0, 60, 0) + _w(0, 20, 60, 20)
        + _w(120, 20, 160, 20)                    # Q -- Ground pin
        + _w(140, 20, 140, 60) + _w(140, 60, 200, 60)
    )
    p = tmp_path / "x0tie.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    md = [i for i in check_all_l1(c).issues if i.kind == "multi_driver"]
    assert len(md) == 1
    assert md[0].severity.value == "warning"


def test_two_register_outputs_tied_is_still_an_error(tmp_path):
    # The demotion is ONLY for constant ties: two real outputs shorted
    # together remain a hard error.
    elements = (
        _ve("In", 0, 0, _entry("Label", "D") + _entry("Bits", 8, "int"))
        + _ve("Clock", 0, 20, _entry("Label", "C"))
        + _ve("Register", 60, 0, _entry("Bits", 8, "int"))
        + _ve("Register", 60, 100, _entry("Bits", 8, "int"))
        + _ve("Out", 200, 60, _entry("Label", "Q") + _entry("Bits", 8, "int"))
    )
    wires = (
        _w(0, 0, 60, 0) + _w(0, 20, 60, 20)
        + _w(0, 0, 0, 100) + _w(0, 100, 60, 100)
        + _w(0, 20, 0, 120) + _w(0, 120, 60, 120)
        + _w(120, 20, 140, 20)
        + _w(120, 120, 140, 120)
        + _w(140, 20, 140, 120)
        + _w(140, 60, 200, 60)
    )
    p = tmp_path / "shorted.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    md = [i for i in check_all_l1(c).issues if i.kind == "multi_driver"]
    assert md and all(i.severity.value == "error" for i in md)


def test_no_width_child_defaults_to_digital_3_grid(tmp_path):
    # Student subcircuits carry no Width attribute; Digital renders them 3
    # grid units (60 px) wide. Our old default of 10 put every output pin
    # 140 px too far right, and the implicit-pin fallback then misnamed
    # pins (ImmSrc landing where ALUOp belongs, "Clock not wired", ...).
    child = _xml_circuit(
        _ve("In", 0, 0, _entry("Label", "A"))
        + _ve("Out", 200, 0, _entry("Label", "Y"))
        + _ve("Not", 80, 0),
        _w(0, 0, 80, 0) + _w(140, 0, 200, 0),
    )
    (tmp_path / "inv.dig").write_text(child, encoding="utf-8")
    parent = _xml_circuit(
        _ve("In", 0, 0, _entry("Label", "X"))
        + _ve("inv.dig", 100, 0)
        + _ve("Out", 220, 0, _entry("Label", "Z")),
        # output wire starts at pos.x + 3*20 = 160: Digital's real edge
        _w(0, 0, 100, 0) + _w(160, 0, 220, 0),
    )
    p = tmp_path / "top.dig"
    p.write_text(parent, encoding="utf-8")
    c = parse_dig_file(str(p))
    from dlc.analyzer import check_all_l1_deep
    issues = check_all_l1_deep(c)
    assert not issues.errors(), [
        (i.kind, i.title) for i in issues.errors()
    ]
