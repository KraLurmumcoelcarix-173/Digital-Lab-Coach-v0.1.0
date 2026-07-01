"""Parametric component glyphs (dlc.web.shape_svg).

Covers: real silhouettes for the multi-input parts, the width-fixed /
height-grows rule, the 3-tier fallback (glyph -> box -> render-failed), inverter
bubbles, and that undecorated parts fall back to the default (None).
"""

import base64

from dlc.parser.models import Component, Position
from dlc.web.shape_svg import shape_for


def _c(element, **attrs):
    return Component(element_name=element, position=Position(0, 0),
                     attributes=attrs, label=None)


def _svg_text(res):
    return base64.b64decode(res["svg"].split(",", 1)[1]).decode()


def test_gate_is_a_glyph_with_valid_svg_data_uri():
    res = shape_for(_c("And", Inputs=2), "gate")
    assert res["tier"] == "glyph"
    assert res["svg"].startswith("data:image/svg+xml;base64,")
    svg = _svg_text(res)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<path" in svg  # the body silhouette


def test_gate_width_fixed_height_grows_with_inputs():
    small = shape_for(_c("And", Inputs=2), "gate")
    big = shape_for(_c("And", Inputs=10, wideShape=True), "gate")
    assert small["w"] == big["w"]          # width stays fixed
    assert big["h"] > small["h"]           # height grows with port count


def test_nand_and_inverter_bubble_add_circles():
    plain = _svg_text(shape_for(_c("And", Inputs=2), "gate"))
    nand = _svg_text(shape_for(_c("NAnd", Inputs=2), "gate"))
    bubble_in = _svg_text(shape_for(_c("And", Inputs=2, inverterConfig=["In_1"]), "gate"))
    assert plain.count("<circle") == 0
    assert nand.count("<circle") >= 1       # output bubble
    assert bubble_in.count("<circle") >= 1  # negated-input bubble


def test_gate_three_tier_fallback():
    assert shape_for(_c("And", Inputs=8), "gate")["tier"] == "glyph"
    assert shape_for(_c("And", Inputs=40), "gate")["tier"] == "box"
    assert shape_for(_c("And", Inputs=200), "gate")["tier"] == "failed"


def test_failed_tier_still_returns_a_usable_node_glyph():
    # "render failed" must not break the node — it still yields a sized SVG box
    res = shape_for(_c("Multiplexer", **{"Selector Bits": 12}), "mux")
    assert res["tier"] == "failed"
    assert res["w"] > 0 and res["h"] > 0
    assert "render failed" in _svg_text(res)


def test_mux_and_decoder_scale_by_selector_bits():
    m1 = shape_for(_c("Multiplexer", **{"Selector Bits": 1}), "mux")
    m3 = shape_for(_c("Multiplexer", **{"Selector Bits": 3}), "mux")
    assert m1["w"] == m3["w"] and m3["h"] > m1["h"]
    assert shape_for(_c("Multiplexer", **{"Selector Bits": 6}), "mux")["tier"] == "box"
    assert shape_for(_c("Decoder", **{"Selector Bits": 2}), "mux")["tier"] == "glyph"


def test_splitter_comb_labels_bit_ranges():
    res = shape_for(_c("Splitter", **{"Input Splitting": "4,4", "Output Splitting": "8"}), "splitter")
    svg = _svg_text(res)
    assert res["tier"] == "glyph"
    assert "0-3" in svg and "4-7" in svg   # per-group bit-range labels


def test_subcircuit_falls_back_to_default_box():
    # subcircuit references keep the plain round-rectangle (user's call)
    assert shape_for(_c("alu.dig"), "subcircuit") is None


def test_simple_fixed_parts_all_render_a_glyph():
    for el, fam in [("In", "io-in"), ("Out", "io-out"), ("Const", "const"),
                    ("Ground", "const"), ("VDD", "const"), ("Clock", "clock"),
                    ("Register", "storage"), ("Add", "arith"),
                    ("Comparator", "arith"), ("ROM", "storage"),
                    ("BarrelShifter", "arith"), ("BitExtender", "arith")]:
        res = shape_for(_c(el), fam)
        assert res is not None and res["tier"] == "glyph", el
        svg = _svg_text(res)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), el


def test_register_marks_the_clock_pin_with_an_edge_triangle():
    # the C input gets a small triangle; D/en do not -> exactly one extra path
    reg = _svg_text(shape_for(_c("Register"), "storage"))
    assert reg.count("<path") == 1  # the clock-edge triangle


def test_not_and_seven_seg_render():
    assert shape_for(_c("Not"), "gate")["tier"] == "glyph"
    ss = shape_for(_c("Seven-Seg"), "other")
    assert ss["tier"] == "glyph" and ss["h"] > ss["w"]  # tall display
