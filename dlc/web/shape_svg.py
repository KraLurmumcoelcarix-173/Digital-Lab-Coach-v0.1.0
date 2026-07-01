"""Parametric component glyphs for the signal-flow graph.

Each node in the Cytoscape graph can carry a small inline-SVG drawn to look
like the real Digital part instead of a plain round-rectangle. The SVG is
returned as a base64 data-URI that the front end drops into Cytoscape's
`background-image`, with the node width/height sized to match.

Design goals (from the Layer-1 UI plan):
  * Real silhouettes for the multi-input parts: gates (D / curved bodies with
    output + inverter bubbles), multiplexer trapezoid, decoder / priority
    boxes, splitter comb, seven-seg.
  * WIDTH stays fixed, HEIGHT grows with the port count.
  * A 3-tier fallback so a pathological port count never breaks anything:
      - "glyph"  : the real silhouette (within a sensible port budget)
      - "box"    : a plain labelled rectangle, still sized to the ports
      - "failed" : a small red "render failed" placeholder
    In every tier the node still exists with all its edges, so the
    signal-flow graph is completely unaffected.

This module is display-only: it never touches the netlist, evaluator, or the
Layer-1 checkers.
"""

from __future__ import annotations

import base64

from dlc.parser.models import Component
from dlc.parser.pin_geometry import get_pin_specs, inverted_input_names


# Fill palette — mirrors FAMILY_COLORS in app.js so glyphs match the legend.
_FAMILY_FILL = {
    "io-in": "#cfe5ff", "io-out": "#ffdcb3", "gate": "#b9e4c1",
    "arith": "#f4b9b9", "mux": "#d8c4ef", "splitter": "#f1ea9a",
    "storage": "#d3d3d3", "tunnel": "#f7d7e8", "subcircuit": "#ffc1c1",
    "const": "#dfdfdf", "clock": "#dfdfdf", "other": "#e9ecef",
}
_STROKE = "#334155"

# Port-count budgets for the 3-tier fallback (my call; tell me to re-tune).
_GATE_GLYPH_MAX = 16     # real gate silhouette up to 16 inputs
_GATE_BOX_MAX = 64       # plain box 17..64, then "render failed"
_MUX_GLYPH_SEL = 4       # trapezoid up to 4 selector bits (16 data inputs)
_SEL_BOX_MAX = 8         # tall box 5..8 sel bits (up to 256 ports), then failed
_SPLIT_GLYPH_MAX = 32    # comb up to 32 groups/side
_SPLIT_BOX_MAX = 64      # then box up to 64, then failed

_NARY_GATES = {"And", "Or", "XOr", "NAnd", "NOr", "XNOr"}
_BUBBLE_OUT = {"NAnd", "NOr", "XNOr"}


def _data_uri(svg: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml;base64," + b64


def _svg(w: int, h: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">{body}</svg>'
    )


def _even_ys(n: int, top: int, bottom: int) -> list[float]:
    """n port y-positions spread evenly (and symmetrically) in [top, bottom]."""
    if n <= 1:
        return [(top + bottom) / 2]
    step = (bottom - top) / (n - 1)
    return [top + i * step for i in range(n)]


def _stub(x1, y1, x2, y2) -> str:
    return f'<line x1="{x1}" y1="{y1:.1f}" x2="{x2}" y2="{y2:.1f}" stroke="{_STROKE}" stroke-width="1.4"/>'


def _bubble(cx, cy, r=3.2) -> str:
    return (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="#fff" '
            f'stroke="{_STROKE}" stroke-width="1.2"/>')


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def _gate_svg(comp: Component, fill: str) -> dict | None:
    n = max(2, int(comp.attributes.get("Inputs", 2) or 2))
    if n > _GATE_BOX_MAX:
        return _failed_box(f"{comp.element_name}×{n}", fill)
    if n > _GATE_GLYPH_MAX:
        return _plain_box(comp.element_name, n, fill, side="in")

    inv = set(inverted_input_names(comp))       # {"in0", ...}
    pad = 12
    span = max(1, n - 1) * 13
    h = span + pad * 2
    top, bot = pad, pad + span
    mid = (top + bot) / 2
    body_l, body_r = 16, 50           # left edge / nose base
    out_x = 66
    w = 72
    base = comp.element_name
    ys = _even_ys(n, top, bot)

    parts = [f'<rect width="{w}" height="{h}" fill="none"/>']

    # body silhouette
    if base in ("And", "NAnd"):
        # flat left, bulged right (D)
        parts.append(
            f'<path d="M{body_l},{top} L{body_r-6},{top} '
            f'C{body_r+12},{top} {body_r+12},{bot} {body_r-6},{bot} '
            f'L{body_l},{bot} Z" fill="{fill}" stroke="{_STROKE}" stroke-width="1.6"/>'
        )
        nose = body_r + 6
    else:
        # OR / XOR: concave back, sweeping to a point on the right
        parts.append(
            f'<path d="M{body_l},{top} '
            f'Q{body_l+22},{mid} {out_x-4},{mid} '
            f'Q{body_l+22},{mid} {body_l},{bot} '
            f'Q{body_l+9},{mid} {body_l},{top} Z" '
            f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.6"/>'
        )
        nose = out_x - 4
        if base in ("XOr", "XNOr"):
            parts.append(
                f'<path d="M{body_l-5},{top} Q{body_l+4},{mid} {body_l-5},{bot}" '
                f'fill="none" stroke="{_STROKE}" stroke-width="1.4"/>'
            )

    # output bubble + stub
    ox = nose
    if base in _BUBBLE_OUT:
        parts.append(_bubble(nose + 3, mid))
        ox = nose + 6
    parts.append(_stub(ox, mid, out_x + 6, mid))

    # input stubs (+ inverter bubbles)
    for i, y in enumerate(ys):
        if f"in{i}" in inv:
            parts.append(_bubble(body_l - 4, y))
            parts.append(_stub(0, y, body_l - 8, y))
        else:
            parts.append(_stub(0, y, body_l, y))

    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "glyph"}


def _not_svg(comp: Component, fill: str) -> dict:
    w, h = 60, 34
    mid = h / 2
    parts = [
        f'<path d="M14,6 L44,{mid} L14,{h-6} Z" fill="{fill}" '
        f'stroke="{_STROKE}" stroke-width="1.6"/>',
        _bubble(48, mid),
        _stub(0, mid, 14, mid),
        _stub(51, mid, w, mid),
    ]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "glyph"}


# --------------------------------------------------------------------------
# Multiplexer / Decoder / PriorityEncoder (selector-bit sized)
# --------------------------------------------------------------------------

def _mux_svg(comp: Component, fill: str) -> dict | None:
    sel_bits = max(1, int(comp.attributes.get("Selector Bits", 1) or 1))
    n = 2 ** sel_bits
    if sel_bits > _SEL_BOX_MAX:
        return _failed_box(f"MUX 2^{sel_bits}", fill)
    if sel_bits > _MUX_GLYPH_SEL:
        return _plain_box("MUX", n, fill, side="in", sublabel=f"2^{sel_bits}")

    pad = 12
    span = max(1, n - 1) * 13
    h = span + pad * 2
    top, bot = pad, pad + span
    inset = min(span * 0.18, 14)
    lx, rx = 16, 52
    w = 64
    ys = _even_ys(n, top + 3, bot - 3)
    parts = [
        f'<path d="M{lx},{top} L{rx},{top+inset:.1f} L{rx},{bot-inset:.1f} '
        f'L{lx},{bot} Z" fill="{fill}" stroke="{_STROKE}" stroke-width="1.6"/>',
    ]
    for y in ys:
        parts.append(_stub(0, y, lx, y))
    parts.append(_stub(rx, (top + bot) / 2, w, (top + bot) / 2))   # out
    parts.append(_stub((lx + rx) / 2, bot - inset / 2, (lx + rx) / 2, h))  # sel
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "glyph"}


def _decoder_svg(comp: Component, fill: str) -> dict | None:
    sel_bits = max(1, int(comp.attributes.get("Selector Bits", 1) or 1))
    n_out = 2 ** sel_bits
    if sel_bits > _SEL_BOX_MAX:
        return _failed_box(f"DEC 2^{sel_bits}", fill)
    if sel_bits > _MUX_GLYPH_SEL:
        return _plain_box("DEC", n_out, fill, side="out", sublabel=f"2^{sel_bits}")
    return _port_box(comp, fill, n_left=1, n_right=n_out, label="DEC")


def _priority_svg(comp: Component, fill: str) -> dict | None:
    sel_bits = max(1, int(comp.attributes.get("Selector Bits", 1) or 1))
    n_in = 2 ** sel_bits
    if sel_bits > _SEL_BOX_MAX:
        return _failed_box(f"PRI 2^{sel_bits}", fill)
    if sel_bits > _MUX_GLYPH_SEL:
        return _plain_box("PRI", n_in, fill, side="in", sublabel=f"2^{sel_bits}")
    return _port_box(comp, fill, n_left=n_in, n_right=1, label="PRI")


# --------------------------------------------------------------------------
# Splitter comb
# --------------------------------------------------------------------------

def _splitter_svg(comp: Component, fill: str) -> dict | None:
    from dlc.facts.splitter import parse_splitting
    in_s = str(comp.attributes.get("Input Splitting", "1"))
    out_s = str(comp.attributes.get("Output Splitting", "1"))
    try:
        ins = parse_splitting(in_s)
        outs = parse_splitting(out_s)
    except ValueError:
        ins, outs = [], []
    n = max(len(ins), len(outs), 1)
    if n > _SPLIT_BOX_MAX:
        return _failed_box(f"SPLIT×{n}", fill)
    if n > _SPLIT_GLYPH_MAX:
        return _plain_box("SPLIT", n, fill, side="both")

    pad = 10
    span = max(1, n - 1) * 15
    h = span + pad * 2
    top = pad
    bar_x = 34
    w = 68
    parts = [f'<line x1="{bar_x}" y1="{top-4}" x2="{bar_x}" y2="{top+span+4}" '
             f'stroke="{_STROKE}" stroke-width="4"/>']
    lys = _even_ys(len(ins) or 1, top, top + span)
    rys = _even_ys(len(outs) or 1, top, top + span)
    for i, y in enumerate(lys):
        parts.append(_stub(4, y, bar_x, y))
        lbl = _range_label(ins[i]) if i < len(ins) else ""
        parts.append(f'<text x="6" y="{y-2:.1f}" font-size="7" fill="{_STROKE}">{lbl}</text>')
    for i, y in enumerate(rys):
        parts.append(_stub(bar_x, y, w - 4, y))
        lbl = _range_label(outs[i]) if i < len(outs) else ""
        parts.append(f'<text x="{bar_x+4}" y="{y-2:.1f}" font-size="7" fill="{_STROKE}">{lbl}</text>')
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "glyph"}


def _range_label(grp) -> str:
    return str(grp.bit_lo) if grp.width == 1 else f"{grp.bit_lo}-{grp.bit_hi}"


# --------------------------------------------------------------------------
# Seven-seg
# --------------------------------------------------------------------------

def _seven_seg_svg(comp: Component, fill: str) -> dict:
    w, h = 44, 60
    # simple static 7-seg outline (all segments faint; lighting is a later task)
    seg = '#c9c9c9'
    parts = [f'<rect x="2" y="2" width="{w-4}" height="{h-4}" rx="3" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.4"/>']
    x0, x1 = 12, 32
    ys = [12, 30, 48]
    # horizontal segments a,g,d
    for y in ys:
        parts.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="{seg}" stroke-width="3"/>')
    # verticals b,c (right) and f,e (left)
    parts.append(f'<line x1="{x1}" y1="12" x2="{x1}" y2="30" stroke="{seg}" stroke-width="3"/>')
    parts.append(f'<line x1="{x1}" y1="30" x2="{x1}" y2="48" stroke="{seg}" stroke-width="3"/>')
    parts.append(f'<line x1="{x0}" y1="12" x2="{x0}" y2="30" stroke="{seg}" stroke-width="3"/>')
    parts.append(f'<line x1="{x0}" y1="30" x2="{x0}" y2="48" stroke="{seg}" stroke-width="3"/>')
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "glyph"}


# --------------------------------------------------------------------------
# Generic fallbacks
# --------------------------------------------------------------------------

def _port_box(comp: Component, fill: str, n_left: int, n_right: int,
              label: str) -> dict:
    rows = max(n_left, n_right, 1)
    pad = 12
    span = max(1, rows - 1) * 13
    h = span + pad * 2
    top = pad
    lx, rx, w = 14, 54, 68
    parts = [f'<rect x="{lx}" y="{top-6}" width="{rx-lx}" height="{span+12}" rx="2" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.6"/>',
             f'<text x="{(lx+rx)/2}" y="{(top+span/2)+3:.1f}" font-size="8" '
             f'text-anchor="middle" fill="{_STROKE}">{label}</text>']
    for y in _even_ys(n_left, top, top + span):
        parts.append(_stub(0, y, lx, y))
    for y in _even_ys(n_right, top, top + span):
        parts.append(_stub(rx, y, w, y))
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "glyph"}


def _plain_box(label: str, count: int, fill: str, side: str,
               sublabel: str | None = None) -> dict:
    """Tier-2 fallback: a labelled rectangle sized to the (large) port count."""
    rows = min(count, 40)
    pad = 10
    span = max(1, rows - 1) * 8
    h = span + pad * 2
    w = 74
    parts = [f'<rect x="8" y="4" width="{w-16}" height="{h-8}" rx="3" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.4"/>',
             f'<text x="{w/2}" y="{h/2:.1f}" font-size="9" text-anchor="middle" '
             f'fill="{_STROKE}">{label}</text>',
             f'<text x="{w/2}" y="{h/2+11:.1f}" font-size="7.5" text-anchor="middle" '
             f'fill="#64748b">{sublabel or (str(count)+" ports")}</text>']
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "box"}


def _failed_box(label: str, fill: str) -> dict:
    w, h = 78, 40
    parts = [f'<rect x="3" y="3" width="{w-6}" height="{h-6}" rx="3" '
             f'fill="#fee2e2" stroke="#dc2626" stroke-width="1.6" stroke-dasharray="4 2"/>',
             f'<text x="{w/2}" y="16" font-size="8" text-anchor="middle" fill="#b91c1c">render failed</text>',
             f'<text x="{w/2}" y="28" font-size="7.5" text-anchor="middle" fill="#b91c1c">{_esc(label)}</text>']
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h,
            "tier": "failed"}

# Simple / fixed-shape parts (I/O ports, constants, clock, register, arith)
# --------------------------------------------------------------------------

def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _dot(cx, cy) -> str:
    return f'<circle cx="{cx}" cy="{cy:.1f}" r="2.6" fill="{_STROKE}"/>'


def _in_svg(comp, fill) -> dict:
    # right-pointing tag: signal leaves toward the circuit
    w, h = 60, 32
    mid = h / 2
    parts = [f'<path d="M4,5 L42,5 L54,{mid} L42,{h-5} L4,{h-5} Z" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.5"/>',
             _dot(54, mid), _stub(54, mid, w, mid)]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _out_svg(comp, fill) -> dict:
    # left-notched tag: signal arrives from the circuit
    w, h = 60, 32
    mid = h / 2
    parts = [f'<path d="M18,5 L{w-5},5 L{w-5},{h-5} L18,{h-5} L6,{mid} Z" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.5"/>',
             _dot(6, mid), _stub(0, mid, 6, mid)]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _const_svg(comp, fill) -> dict:
    # small box with a driving dot on the right (the value shows in the label)
    w, h = 54, 30
    mid = h / 2
    parts = [f'<rect x="6" y="5" width="34" height="{h-10}" rx="2" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.5"/>',
             _dot(44, mid), _stub(44, mid, w, mid)]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _ground_svg(comp, fill) -> dict:
    w, h = 40, 40
    cx = w / 2
    parts = [_stub(cx, 4, cx, 20), _dot(cx, 4)]
    for i, half in enumerate((11, 7, 3)):
        y = 22 + i * 5
        parts.append(f'<line x1="{cx-half}" y1="{y}" x2="{cx+half}" y2="{y}" '
                     f'stroke="{_STROKE}" stroke-width="1.8"/>')
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _vdd_svg(comp, fill) -> dict:
    w, h = 40, 40
    cx = w / 2
    parts = [f'<line x1="{cx-11}" y1="8" x2="{cx+11}" y2="8" stroke="{_STROKE}" stroke-width="1.8"/>',
             _stub(cx, 8, cx, h - 6), _dot(cx, h - 6)]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _clock_svg(comp, fill) -> dict:
    w, h = 58, 34
    mid = h / 2
    parts = [f'<rect x="6" y="5" width="34" height="{h-10}" rx="2" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.5"/>',
             f'<path d="M11,{mid+6} V{mid-5} H19 V{mid+6} H27 V{mid-5} H34" '
             f'fill="none" stroke="{_STROKE}" stroke-width="1.4"/>',
             _dot(44, mid), _stub(40, mid, w, mid)]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _box_with_pins(comp, fill, *, label="", symbol="", clock_pin=None) -> dict:
    """Rectangle sized to the component's real in/out pins, with an optional
    centre symbol and a clock-edge triangle on `clock_pin`."""
    pins = get_pin_specs(comp)
    ins = [p for p in pins if p.direction == "in"]
    outs = [p for p in pins if p.direction == "out"]
    rows = max(len(ins), len(outs), 1)
    pad = 12
    span = max(1, rows - 1) * 14
    h = span + pad * 2
    top = pad
    lx, rx, w = 14, 52, 66
    cx = (lx + rx) / 2
    mid = top + span / 2
    parts = [f'<rect x="{lx}" y="{top-8}" width="{rx-lx}" height="{span+16}" rx="3" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.6"/>']
    if symbol:
        parts.append(f'<text x="{cx}" y="{mid+5:.1f}" font-size="15" font-weight="bold" '
                     f'text-anchor="middle" fill="{_STROKE}">{_esc(symbol)}</text>')
    elif label:
        parts.append(f'<text x="{cx}" y="{mid+3:.1f}" font-size="8" '
                     f'text-anchor="middle" fill="{_STROKE}">{_esc(label)}</text>')
    in_ys = _even_ys(len(ins), top, top + span)
    for i, y in enumerate(in_ys):
        parts.append(_stub(0, y, lx, y))
        if clock_pin is not None and i < len(ins) and ins[i].name == clock_pin:
            parts.append(f'<path d="M{lx},{y-4:.1f} L{lx+6},{y:.1f} L{lx},{y+4:.1f} Z" '
                         f'fill="none" stroke="{_STROKE}" stroke-width="1.2"/>')
    for y in _even_ys(len(outs), top, top + span):
        parts.append(_stub(rx, y, w, y))
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


def _bitextender_svg(comp, fill) -> dict:
    # narrow (in) -> wide (out) trapezoid, showing the width growth
    w, h = 60, 40
    parts = [f'<path d="M16,16 L46,6 L46,{h-6} L16,{h-16} Z" '
             f'fill="{fill}" stroke="{_STROKE}" stroke-width="1.6"/>',
             _stub(0, h / 2, 16, h / 2), _stub(46, h / 2, w, h / 2)]
    return {"svg": _data_uri(_svg(w, h, "".join(parts))), "w": w, "h": h, "tier": "glyph"}


# --------------------------------------------------------------------------
# Public dispatch
# --------------------------------------------------------------------------

def shape_for(comp: Component, family: str) -> dict | None:
    """Return {svg, w, h, tier} for a component, or None to keep the default
    round-rectangle. Never raises: any failure falls back to None."""
    fill = _FAMILY_FILL.get(family, "#e9ecef")
    name = comp.element_name
    try:
        if name in _NARY_GATES:
            return _gate_svg(comp, fill)
        if name == "Not":
            return _not_svg(comp, fill)
        if name == "Multiplexer":
            return _mux_svg(comp, fill)
        if name == "Decoder":
            return _decoder_svg(comp, fill)
        if name == "PriorityEncoder":
            return _priority_svg(comp, fill)
        if name == "Splitter":
            return _splitter_svg(comp, fill)
        if name == "Seven-Seg":
            return _seven_seg_svg(comp, fill)
        if name == "In":
            return _in_svg(comp, fill)
        if name == "Out":
            return _out_svg(comp, fill)
        if name == "Const":
            return _const_svg(comp, fill)
        if name == "Ground":
            return _ground_svg(comp, fill)
        if name == "VDD":
            return _vdd_svg(comp, fill)
        if name == "Clock":
            return _clock_svg(comp, fill)
        if name == "Register":
            return _box_with_pins(comp, fill, label="reg", clock_pin="C")
        if name == "Add":
            return _box_with_pins(comp, fill, symbol="+")
        if name == "Comparator":
            return _box_with_pins(comp, fill, symbol="⋛")   # ⋛
        if name == "BarrelShifter":
            return _box_with_pins(comp, fill, symbol="≪")   # ≪
        if name == "ROM":
            return _box_with_pins(comp, fill, label="ROM")
        if name == "BitExtender":
            return _bitextender_svg(comp, fill)
    except Exception:
        return None
    return None
