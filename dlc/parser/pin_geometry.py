"""
Pin geometry for covered Digital element types (details specified at docs/digital_notes.md).

Digital component positions in the .dig file are anchor points. Each
component's actual pin positions are OFFSETS from that anchor. We use a registry 
mapping element_name to its pin offsets.

Digital uses x increasing rightward, y increasing downward. Offsets here are added to a
component's <pos x=.. y=..> to get the absolute pin location.

For elements whose pin geometry depends on attributes, we provide a function instead
of a static list.

"""

from dataclasses import dataclass

from dlc.parser.models import Component, Position


@dataclass(frozen=True)
class PinSpec:

    name: str
    offset_x: int
    offset_y: int
    direction: str  
    inverted: bool = False   # gate input carrying an inverter bubble (inverterConfig)

_NOT_PINS = [
    PinSpec("A", offset_x=0,  offset_y=0, direction="in"),
    PinSpec("Y", offset_x=40, offset_y=0, direction="out"),
]

_INPUT_PINS = [PinSpec("out", 0, 0, "out")]
_OUTPUT_PINS = [PinSpec("in", 0, 0, "in")]
_TUNNEL_PINS = [PinSpec("net", 0, 0, "bidir")]
_CONST_PINS = [PinSpec("out", 0, 0, "out")]
_CLOCK_PINS = [PinSpec("clk", 0, 0, "out")]
_GROUND_PINS = [PinSpec("out", 0, 0, "out")]
_VDD_PINS = [PinSpec("out", 0, 0, "out")]

_ADD_PINS = [
    PinSpec("a",   0,  0,  "in"),
    PinSpec("b",   0,  20, "in"),
    PinSpec("c_i", 0,  40, "in"),
    PinSpec("s",   60, 0,  "out"),
    PinSpec("c_o", 60, 20, "out"),
]

_BITEXTENDER_PINS = [
    PinSpec("in",  0,  0, "in"),
    PinSpec("out", 80, 0, "out"),
]

_BARREL_SHIFTER_PINS = [
    PinSpec("in",  0,  0,  "in"),
    PinSpec("sh",  0,  40, "in"),
    PinSpec("out", 60, 20, "out"),
]


_ROM_PINS = [
    PinSpec("A",   0,  0,  "in"),
    PinSpec("sel", 0,  40, "in"),
    PinSpec("D",   80, 20, "out"),
]

_SEVEN_SEG_PINS = [
    PinSpec("a",  offset_x=0,  offset_y=-40, direction="in"),
    PinSpec("b",  offset_x=20, offset_y=-40, direction="in"),
    PinSpec("c",  offset_x=40, offset_y=-40, direction="in"),
    PinSpec("d",  offset_x=60, offset_y=-40, direction="in"),
    PinSpec("e",  offset_x=0,  offset_y=180, direction="in"),
    PinSpec("f",  offset_x=20, offset_y=180, direction="in"),
    PinSpec("g",  offset_x=40, offset_y=180, direction="in"),
    PinSpec("dp", offset_x=60, offset_y=240, direction="in"),
]

STATIC_PIN_TABLE: dict[str, list[PinSpec]] = {
    "Not":    _NOT_PINS,
    "In":     _INPUT_PINS,
    "Out":    _OUTPUT_PINS,
    "Tunnel": _TUNNEL_PINS,
    "Const":  _CONST_PINS,
    "Clock":  _CLOCK_PINS,
    "Ground": _GROUND_PINS,
    "VDD":    _VDD_PINS,
    "Add":    _ADD_PINS,
    "BitExtender":   _BITEXTENDER_PINS,
    "BarrelShifter": _BARREL_SHIFTER_PINS,
    "ROM":    _ROM_PINS,
    "Seven-Seg": _SEVEN_SEG_PINS,
}

_NARY_GATE_ELEMENTS = frozenset({"And", "Or", "XOr", "NAnd", "NOr", "XNOr"})

def inverted_input_names(comp: Component) -> list[str]:
    """Gate input pin names (in0, in1, …) that carry an inverter bubble.

    Digital stores them 1-indexed in `inverterConfig` as In_1, In_2, …; we
    return the 0-indexed pin names the rest of the pipeline uses. Empty for
    non-gates or gates without the attribute.
    """
    if comp.element_name not in _NARY_GATE_ELEMENTS:
        return []
    names: list[str] = []
    for raw in (comp.attributes.get("inverterConfig") or []):
        if isinstance(raw, str) and raw.startswith("In_"):
            try:
                idx = int(raw[3:]) - 1
            except ValueError:
                continue
            if idx >= 0:
                names.append(f"in{idx}")
    return names

def _nary_gate_pins(comp: Component) -> list[PinSpec]:
    """
    Boolean gate (And/Or/XOr/NAnd/NOr/XNOr) with N inputs.

    Digital attribute 'Inputs' (<int>) controls input count; absent = 2.

    'wideShape' (boolean) selects a taller body. Default treated as False.

    Geometry:

      wideShape=False (compact body): inputs uniform spacing 20.
      wideShape=True, odd N: inputs uniform spacing 20.
      wideShape=True, even N: top half + 40-unit middle gap + bottom half,
        each half internally spaced 20.
        Examples: N=2 -> +0, +40
                  N=4 -> +0, +20, +60, +80
                  N=6 -> +0, +20, +40, +80, +100, +120

    Output sits at x=80, y centered between the topmost and bottommost
    input. NAnd/NOr/XNOr add a bubble that  pushes the visible output 20
    further right, absorbed by the endpoint-snap tolerance in build_netlist.
    """
    n = int(comp.attributes.get("Inputs", 2))
    wide = bool(comp.attributes.get("wideShape", False))
    inverted_idxs = {int(nm[2:]) for nm in inverted_input_names(comp)}
    pins: list[PinSpec] = []

    def _input_x(i: int) -> int:
        return -20 if i in inverted_idxs else 0

    if wide and n >= 2 and n % 2 == 0:
        half = n // 2
        for i in range(half):
            pins.append(
                PinSpec(f"in{i}", offset_x=_input_x(i), offset_y=i * 20,
                        direction="in", inverted=(i in inverted_idxs))
            )
        bottom_start = (half - 1) * 20 + 40
        for i in range(half):
            idx = half + i
            pins.append(
                PinSpec(
                    f"in{idx}",
                    offset_x=_input_x(idx),
                    offset_y=bottom_start + i * 20,
                    direction="in",
                    inverted=(idx in inverted_idxs),
                )
            )
        center_y = ((half - 1) * 20 + bottom_start) // 2

    else:
        for i in range(n):
            pins.append(
                PinSpec(f"in{i}", offset_x=_input_x(i), offset_y=i * 20,
                        direction="in", inverted=(i in inverted_idxs))
            )
        center_y = ((n - 1) * 20) // 2
    pins.append(PinSpec("Y", offset_x=80, offset_y=center_y, direction="out"))

    return pins


def _multiplexer_pins(comp: Component) -> list[PinSpec]:
    """
    Multiplexer. 'Selector Bits' = N → 2^N data inputs (absent = 1 → 2-to-1).
    2-input Mux uses spacing 40 with sel at (20, 40), 4+ input uses spacing 20.

      n_inputs == 2 (sel_bits=1):
        in_i at (0, i * 40)   
        sel  at (20, 40)
        out  at (40, 20)

      n_inputs >= 4 (sel_bits >= 2):
        in_i at (0, i * 20)   
        sel  at (20, n_inputs * 20)
        out  at (40, n_inputs * 10)
    """
    sel_bits = int(comp.attributes.get("Selector Bits", 1))
    n_inputs = 2 ** sel_bits
    if n_inputs == 2:
        spacing, sel_y, out_y = 40, 40, 20
    else:
        spacing = 20
        sel_y = n_inputs * 20
        out_y = n_inputs * 10

    pins: list[PinSpec] = []
    for i in range(n_inputs):
        pins.append(PinSpec(f"in{i}", offset_x=0, offset_y=i * spacing, direction="in"))
    pins.append(PinSpec("sel", offset_x=20, offset_y=sel_y, direction="in"))
    pins.append(PinSpec("out", offset_x=40, offset_y=out_y, direction="out"))
    return pins


def _splitter_pins(comp: Component) -> list[PinSpec]:
    """
    Splitter. 
    Splitter / merger. Bit-group sizes determine pin count. Inputs on the
    left edge, outputs on the right edge. Pin spacing = 20 * splitterSpreading.

    splitterSpreading defaults to 1 (20-unit spacing). When set (e.g. =2),
    pins are spaced 40 units apart
    """
    in_split = str(comp.attributes.get("Input Splitting", "1"))
    out_split = str(comp.attributes.get("Output Splitting", "1"))
    spread = int(comp.attributes.get("splitterSpreading", 1))
    in_groups = [s.strip() for s in in_split.split(",") if s.strip()]
    out_groups = [s.strip() for s in out_split.split(",") if s.strip()]
    spacing = 20 * spread

    pins: list[PinSpec] = []
    for i, _ in enumerate(in_groups):
        pins.append(PinSpec(f"in{i}", offset_x=0, offset_y=i * spacing, direction="in"))
    for i, _ in enumerate(out_groups):
        pins.append(PinSpec(f"out{i}", offset_x=20, offset_y=i * spacing, direction="out"))
    return pins


def _register_pins(comp: Component) -> list[PinSpec]:
    """
    Register: D input, C clock, en write-enable, Q output.

      D  at offset (0, 0)   (top-left)
      C  at offset (0, 20)  (left, below D)
      en at offset (0, 40)  (left, below C) typically tied to Const(1).
      Q  at offset (60, 20) (right, between D and en heights)
    """
    return [
        PinSpec("D",  offset_x=0,  offset_y=0,  direction="in"),
        PinSpec("C",  offset_x=0,  offset_y=20, direction="in"),
        PinSpec("en", offset_x=0, offset_y=40, direction="in"),
        PinSpec("Q",  offset_x=60, offset_y=20, direction="out"),
    ]

def _decoder_pins(comp: Component) -> list[PinSpec]:
    """
    Decoder: 2^Selector Bits outputs stacked on the right edge,
    one sel input at the bottom-middle (analogous to Multiplexer n>=4
    layout). Example (Selector Bits=5, 32 enable outputs):
      out_i at (60, i * 20)
      sel   at (20, n_outputs * 20)
    """
    sel_bits = int(comp.attributes.get("Selector Bits", 1))
    n_outputs = 2 ** sel_bits
    pins: list[PinSpec] = [
        PinSpec("sel", offset_x=20, offset_y=n_outputs * 20, direction="in"),
    ]
    for i in range(n_outputs):
        pins.append(PinSpec(f"out_{i}", offset_x=60, offset_y=i * 20, direction="out"))
    return pins


def _priority_encoder_pins(comp: Component) -> list[PinSpec]:
    """
    PriorityEncoder: 2^Selector Bits priority inputs on the left, one
    encoded selector output on the right. Approximate offsets verified
    Example (Selector Bits=3, 8 inputs):
      in_i at (0, i*20)
      out  at (80, 0)
    """
    sel_bits = int(comp.attributes.get("Selector Bits", 1))
    n_inputs = 2 ** sel_bits
    pins: list[PinSpec] = []
    for i in range(n_inputs):
        pins.append(PinSpec(f"in_{i}", offset_x=0, offset_y=i * 20, direction="in"))
    pins.append(PinSpec("num", offset_x=80, offset_y=0, direction="out"))
    return pins

def _comparator_pins(comp: Component) -> list[PinSpec]:
    """
    Comparator: A and B inputs left, greater/equal/less outputs right.

      Visual width = 60, so outputs sit at x=60.
      A  at (0, 0)  in
      B  at (0, 20) in
      gr at (60, 0)  out
      eq at (60, 20) out
      le at (60, 40) out
    """
    return [
        PinSpec("A",  offset_x=0,  offset_y=0,  direction="in"),
        PinSpec("B",  offset_x=0,  offset_y=20, direction="in"),
        PinSpec("gr", offset_x=60, offset_y=0,  direction="out"),
        PinSpec("eq", offset_x=60, offset_y=20, direction="out"),
        PinSpec("le", offset_x=60, offset_y=40, direction="out"),
    ]

DYNAMIC_PIN_TABLE: dict[str, callable] = {
    "And":  _nary_gate_pins,
    "Or":   _nary_gate_pins,
    "XOr":  _nary_gate_pins,
    "NAnd": _nary_gate_pins,
    "NOr":  _nary_gate_pins,
    "XNOr": _nary_gate_pins,
    "Multiplexer": _multiplexer_pins,
    "Splitter":    _splitter_pins,
    "Register":    _register_pins,
    "Comparator":  _comparator_pins,
    "Decoder":         _decoder_pins,
    "PriorityEncoder": _priority_encoder_pins,
}



# Public API

def get_pin_specs(component: Component) -> list[PinSpec]:
    """
    public look up, use static table for static pins, dynamic table for dynamic pins look up
    """
    name = component.element_name

    if name in STATIC_PIN_TABLE:
        return STATIC_PIN_TABLE[name]
    if name in DYNAMIC_PIN_TABLE:
        return DYNAMIC_PIN_TABLE[name](component)

    return []

def _rotate(dx: int, dy: int, rotation: int) -> tuple[int, int]:
    """
    Rotate a (dx, dy) offset by Digital's rotation index (0..3). Digital
    stores rotation as 0/1/2/3 in <rotation rotation="N"/>. Rotation 1
    means 90 degrees counter-clockwise as viewed in screen coordinates
    (y growing down).
    """
    r = rotation % 4
    if r == 0:
        return (dx, dy)
    if r == 1:
        return (dy, -dx)
    if r == 2:
        return (-dx, -dy)
    return (-dy, dx)  

def absolute_pin_positions(component: Component) -> list[tuple[Position, PinSpec]]:
    """
    Return each pin's absolute canvas position plus its spec.Applies the
    rotation attribute (if any) to the offsets before adding the anchor.
    Used for matching wire endpoints to pins.
    """
    pins = get_pin_specs(component)
    rotation = component.attributes.get("rotation", 0)
    if not isinstance(rotation, int):
        rotation = 0
    result = []
    for pin in pins:
        dx, dy = _rotate(pin.offset_x, pin.offset_y, rotation)
        absolute = Position(
            x=component.position.x + dx,
            y=component.position.y + dy,
        )
        result.append((absolute, pin))
    return result