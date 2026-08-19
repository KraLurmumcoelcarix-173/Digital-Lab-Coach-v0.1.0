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


def test_flipped_decoder_sel_is_not_flagged():
    c = parse_dig_file(f"{_DIR}/flipped_decoder_selfeed.dig")
    assert check_all_l1(c).issues == []


def test_demux_write_enable_fanout_is_not_flagged():
    c = parse_dig_file(f"{_DIR}/demux_write_enable.dig")
    assert check_all_l1(c).issues == []


def test_decoder_sel_sits_at_last_output_height():
    d = _c("Decoder", **{"Selector Bits": 5})
    sel = _pin(get_pin_specs(d), "sel")
    assert (sel.offset_x, sel.offset_y) == (20, 620)


def test_decoder_flip_sel_pos_moves_sel_to_top():
    d = _c("Decoder", **{"Selector Bits": 5, "flipSelPos": True})
    sel = _pin(get_pin_specs(d), "sel")
    assert (sel.offset_x, sel.offset_y) == (20, -20)


def test_demux_pin_layout_matches_measured_lab_geometry():
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


def test_demux_routes_input_and_zeroes_the_rest():
    d = _c("Demultiplexer", **{"Selector Bits": 2})
    out = sim._eval_demux(d, {"in": 1, "sel": 3})
    assert out == {"out_0": 0, "out_1": 0, "out_2": 0, "out_3": 1}
    out = sim._eval_demux(d, {"in": 0, "sel": 3})
    assert out == {"out_0": 0, "out_1": 0, "out_2": 0, "out_3": 0}
    assert sim._eval_demux(d, {"sel": 1}) is None

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
    from dlc.facts.width import pin_width
    pe = _c("PriorityEncoder", **{"Selector Bits": 3})
    f = _pin(get_pin_specs(pe), "f")
    assert (f.offset_x, f.offset_y, f.direction) == (80, 20, "out")
    num = _pin(get_pin_specs(pe), "num")
    assert (num.offset_x, num.offset_y) == (80, 0)
    assert pin_width(pe, "f") == 1


def test_rom_output_sits_on_60_wide_box():
    rom = _c("ROM", AddrBits=4, Bits=8)
    d = _pin(get_pin_specs(rom), "D")
    assert (d.offset_x, d.offset_y) == (60, 20)


def test_unwired_output_does_not_grab_a_routing_corner(tmp_path):
    elements = (
        _ve("In", 0, 0, _entry("Label", "A") + _entry("Bits", 7, "int"))
        + _ve("In", 0, 20, _entry("Label", "B") + _entry("Bits", 7, "int"))
        + _ve("Comparator", 40, 0, _entry("Bits", 7, "int"))
        + _ve("Out", 180, 0, _entry("Label", "EQ"))
    )
    wires = (
        _w(0, 0, 40, 0) + _w(0, 20, 40, 20)
        + _w(100, 20, 120, 20)
        + _w(120, 0, 120, 20)
        + _w(120, 0, 180, 0)
    )
    p = tmp_path / "ladder.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    issues = check_all_l1(c)
    assert not [i for i in issues.issues if i.kind == "multi_driver"], [
        (i.kind, i.title) for i in issues.issues
    ]


def test_output_tied_to_ground_is_warning_not_error(tmp_path):
    elements = (
        _ve("In", 0, 0, _entry("Label", "D") + _entry("Bits", 8, "int"))
        + _ve("Clock", 0, 20, _entry("Label", "C"))
        + _ve("Register", 60, 0, _entry("Bits", 8, "int"))
        + _ve("Ground", 160, 20, _entry("Bits", 8, "int"))
        + _ve("Out", 200, 60, _entry("Label", "Q") + _entry("Bits", 8, "int"))
    )
    wires = (
        _w(0, 0, 60, 0) + _w(0, 20, 60, 20)
        + _w(120, 20, 160, 20)
        + _w(140, 20, 140, 60) + _w(140, 60, 200, 60)
    )
    p = tmp_path / "x0tie.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    md = [i for i in check_all_l1(c).issues if i.kind == "multi_driver"]
    assert len(md) == 1
    assert md[0].severity.value == "warning"


def test_two_register_outputs_tied_is_still_an_error(tmp_path):
    elements = (
        _ve("In", 0, 0, _entry("Label", "D") + _entry("Bits", 8, "int"))
        + _ve("Clock", 0, 20, _entry("Label", "C"))
        + _ve("Register", 60, 0, _entry("Bits", 8, "int"))
        + _ve("Register", 60, 100, _entry("Bits", 8, "int"))
        + _ve("Out", 200, 60, _entry("Label", "Q") + _entry("Bits", 8, "int"))
    )
    wires = (
        _w(0, 0, 60, 0) + _w(0, 20, 60, 20)
        + _w(20, 0, 20, 100) + _w(20, 100, 60, 100)
        + _w(40, 20, 40, 120) + _w(40, 120, 60, 120)
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


def test_register_file_pins_and_widths():
    from dlc.facts.width import pin_width
    rf = _c("RegisterFile", **{"AddrBits": 5, "Bits": 32})
    specs = get_pin_specs(rf)
    got = {p.name: (p.offset_x, p.offset_y, p.direction) for p in specs}
    assert got == {
        "Din": (0, 0, "in"), "we": (0, 20, "in"), "Rw": (0, 40, "in"),
        "C": (0, 60, "in"), "Ra": (0, 80, "in"), "Rb": (0, 100, "in"),
        "Da": (80, 0, "out"), "Db": (80, 20, "out"),
    }
    assert pin_width(rf, "Din") == 32 and pin_width(rf, "Da") == 32
    assert pin_width(rf, "Ra") == 5 and pin_width(rf, "Rw") == 5
    assert pin_width(rf, "we") == 1 and pin_width(rf, "C") == 1


def test_register_file_write_then_read(tmp_path):
    elements = (
        _ve("In", 100, 0, _entry("Label", "Din") + _entry("Bits", 8, "int"))
        + _ve("Const", 160, 20, _entry("Value", 1, "long") + _entry("Bits", 1, "int"))
        + _ve("In", 100, 40, _entry("Label", "Rw") + _entry("Bits", 2, "int"))
        + _ve("Clock", 100, 60, _entry("Label", "C"))
        + _ve("In", 100, 80, _entry("Label", "Ra") + _entry("Bits", 2, "int"))
        + _ve("In", 100, 100, _entry("Label", "Rb") + _entry("Bits", 2, "int"))
        + _ve("RegisterFile", 200, 0,
              _entry("Bits", 8, "int") + _entry("AddrBits", 2, "int"))
        + _ve("Out", 340, 0, _entry("Label", "Da") + _entry("Bits", 8, "int"))
        + _ve("Out", 340, 20, _entry("Label", "Db") + _entry("Bits", 8, "int"))
        + (
            "    <visualElement><elementName>Testcase</elementName>"
            "<elementAttributes><entry><string>Testdata</string>"
            "<testData><dataString>C Din Rw Ra Rb Da Db\n"
            "0 0 0 0 0 0 0\n"
            "C 7 1 1 3 7 0\n"
            "C 9 2 2 1 9 7\n"
            "C 5 0 2 0 9 5\n"
            "</dataString></testData></entry></elementAttributes>"
            "<pos x=\"100\" y=\"200\"/></visualElement>\n"
        )
    )
    wires = (
        _w(100, 0, 200, 0) + _w(160, 20, 200, 20) + _w(100, 40, 200, 40)
        + _w(100, 60, 200, 60) + _w(100, 80, 200, 80)
        + _w(100, 100, 200, 100)
        + _w(280, 0, 340, 0) + _w(280, 20, 340, 20)
    )
    p = tmp_path / "regfile.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    assert not check_all_l1(c).issues

    from dlc.testing.spec import extract_test_specs
    from dlc.sim.simulator import simulate_sequential
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    spec = extract_test_specs(c)[0]
    out_idx = {comp.label: i for i, comp in enumerate(c.components)
               if comp.element_name == "Out"}
    expected = [(0, 0), (7, 0), (9, 7), (9, 5)]
    for k, row in enumerate([r for r in spec.rows if not r.is_malformed]):
        res = simulate_sequential(c, nl, g, spec, row.line_index)
        got = {}
        for lbl, idx in out_idx.items():
            comp = c.components[idx]
            net = nl.net_at(comp.position.x, comp.position.y)
            got[lbl] = res.net_values.get(net.net_id) if net else None
        assert (got["Da"], got["Db"]) == expected[k], f"row {k}: {got}"


def test_rom_run_length_data_expands():
    from dlc.sim.simulator import _rom_words
    rom = _c("ROM", **{"AddrBits": 4, "Bits": 8, "Data": "3*1f,2a"})
    assert _rom_words(rom) == [0x1F, 0x1F, 0x1F, 0x2A]

def test_mirrored_splitter_pins_flip_up():
    s = _c("Splitter", **{"Input Splitting": "32",
                          "Output Splitting": "31, 1", "mirror": True})
    got = {p.name: (p.offset_x, p.offset_y) for p in get_pin_specs(s)}
    assert got == {"in0": (0, 0), "out0": (20, 0), "out1": (20, -20)}


def test_seven_seg_pins_sit_on_anchor_and_140():
    seg = _c("Seven-Seg")
    got = {p.name: (p.offset_x, p.offset_y) for p in get_pin_specs(seg)}
    assert got == {
        "a": (0, 0), "b": (20, 0), "c": (40, 0), "d": (60, 0),
        "e": (0, 140), "f": (20, 140), "g": (40, 140), "dp": (60, 140),
    }


def test_equal_valued_consts_tied_by_tunnels_is_warning(tmp_path):
    def circuit(v2):
        elements = (
            _ve("Const", 0, 0, _entry("Value", 51, "long") + _entry("Bits", 7, "int"))
            + _ve("Const", 0, 100, _entry("Value", v2, "long") + _entry("Bits", 7, "int"))
            + _ve("Out", 200, 0, _entry("Label", "Y") + _entry("Bits", 7, "int"))
        )
        wires = (_w(0, 0, 100, 0) + _w(0, 100, 100, 100)
                 + _w(100, 0, 100, 100) + _w(100, 0, 200, 0))
        return _xml_circuit(elements, wires)

    p = tmp_path / "consts.dig"
    p.write_text(circuit(51), encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert len(md) == 1 and md[0].severity.value == "warning"

    p.write_text(circuit(50), encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert md and all(i.severity.value == "error" for i in md)


def test_testcase_unlisted_in_tap_is_warning(tmp_path):
    tap = (
        _ve("In", 0, 0, _entry("Label", "rs1") + _entry("Bits", 5, "int"))
        + _ve("In", 0, 100, _entry("Label", "src") + _entry("Bits", 5, "int"))
        + _ve("Out", 200, 0, _entry("Label", "Y") + _entry("Bits", 5, "int"))
    )
    wires = (_w(0, 0, 100, 0) + _w(0, 100, 100, 100)
             + _w(100, 0, 100, 100) + _w(100, 0, 200, 0))
    with_test = _xml_circuit(
        tap + (
            "    <visualElement><elementName>Testcase</elementName>"
            "<elementAttributes><entry><string>Testdata</string>"
            "<testData><dataString>src Y\n1 1\n</dataString></testData>"
            "</entry></elementAttributes><pos x=\"0\" y=\"300\"/>"
            "</visualElement>\n"
        ),
        wires,
    )
    p = tmp_path / "tap.dig"
    p.write_text(with_test, encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert len(md) == 1 and md[0].severity.value == "warning"

    p.write_text(_xml_circuit(tap, wires), encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert md and all(i.severity.value == "error" for i in md)


def test_duplicated_identical_gates_tied_is_warning(tmp_path):
    def circuit(second_gate, invert_second=False):
        inv = (
            "<entry><string>inverterConfig</string><inverterConfig>"
            "<string>In_1</string></inverterConfig></entry>"
            if invert_second else ""
        )
        wide = _entry("wideShape", "true", "boolean")
        elements = (
            _ve("In", 0, 0, _entry("Label", "A"))
            + _ve("In", 0, 80, _entry("Label", "B"))
            + _ve("And", 200, 0, wide)
            + _ve(second_gate, 200, 120, wide + inv)
            + _ve("Tunnel", 300, 20, _entry("NetName", "T"))
            + _ve("Tunnel", 300, 140, _entry("NetName", "T"))
            + _ve("Tunnel", 380, 60, _entry("NetName", "T"))
            + _ve("Out", 400, 60, _entry("Label", "F"))
        )
        wires = (
            _w(0, 0, 60, 0) + _w(60, 0, 200, 0)
            + _w(60, 0, 60, 120) + _w(60, 120, 200, 120)
            + _w(0, 80, 100, 80)
            + _w(100, 40, 100, 80) + _w(100, 40, 200, 40)
            + _w(100, 80, 100, 160) + _w(100, 160, 200, 160)
            + _w(280, 20, 300, 20) + _w(280, 140, 300, 140)
            + _w(380, 60, 400, 60)
        )
        return _xml_circuit(elements, wires)

    p = tmp_path / "dup.dig"
    p.write_text(circuit("And"), encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert len(md) == 1 and md[0].severity.value == "warning"

    p.write_text(circuit("Or"), encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert md and all(i.severity.value == "error" for i in md)

    p.write_text(circuit("And", invert_second=True), encoding="utf-8")
    md = [i for i in check_all_l1(parse_dig_file(str(p))).issues
          if i.kind == "multi_driver"]
    assert md and all(i.severity.value == "error" for i in md)


def test_mute_contract_unblocks_test_runs(tmp_path):
    from dlc.web.server import _l1_error_block
    elements = (
        _ve("In", 0, 0, _entry("Label", "A"))
        + _ve("And", 40, 0, _entry("wideShape", "true", "boolean"))
        + _ve("Out", 160, 20, _entry("Label", "Y"))
    )
    wires = _w(0, 0, 40, 0) + _w(120, 20, 160, 20)
    p = tmp_path / "one_err.dig"
    p.write_text(_xml_circuit(elements, wires), encoding="utf-8")
    c = parse_dig_file(str(p))
    errs = check_all_l1(c).errors()
    assert errs, "fixture must carry at least one L1 error"

    assert _l1_error_block(c) is not None
    assert _l1_error_block(c, {"last_all_passed": False}) is not None
    assert _l1_error_block(c, {"last_all_passed": True}) is None
