"""
Components' pins' bit-width helpers.
"""

from dlc.parser.models import Component
from dlc.facts.splitter import parse_splitting


_BITS_DEFAULT = 1


def _bits(comp: Component) -> int:
    return int(comp.attributes.get("Bits", _BITS_DEFAULT))


def _selector_bits(comp: Component) -> int:
    return int(comp.attributes.get("Selector Bits", 1))


def _splitter_group_width(comp: Component, pin_name: str) -> int | None:
    if pin_name.startswith("in"):
        idx_str = pin_name[2:]
        spec = comp.attributes.get("Input Splitting", "1")
    elif pin_name.startswith("out"):
        idx_str = pin_name[3:]
        spec = comp.attributes.get("Output Splitting", "1")
    else:
        return None
    try:
        idx = int(idx_str)
    except ValueError:
        return None
    groups = parse_splitting(str(spec))
    if 0 <= idx < len(groups):
        return groups[idx].width
    return None


def pin_width(component: Component, pin_name: str) -> int | None:
    """
    Return the bit width of `pin_name` on `component`, or None when the
    width depends on external context (Tunnels, subcircuit instances,
    unknown elements, or an unrecognized pin name).
    """
    e = component.element_name


    if e == "Clock":
        return 1

    if e in ("In", "Out", "Const", "Ground", "VDD"):
        return _bits(component)

    if e in ("And", "Or", "XOr", "NAnd", "NOr", "XNOr", "Not"):
        return _bits(component)

    if e == "Add":
        if pin_name in ("a", "b", "s"):
            return _bits(component)
        if pin_name in ("c_i", "c_o"):
            return 1
        return None

    if e == "Comparator":
        if pin_name in ("A", "B"):
            return _bits(component)
        if pin_name in ("gr", "eq", "le"):
            return 1
        return None

    if e == "BarrelShifter":
        if pin_name in ("in", "out"):
            return _bits(component)
        if pin_name == "sh":
            n = _bits(component)
            if n <= 2:
                return 1
            return n.bit_length()
        return None

    if e == "Multiplexer":
        if pin_name == "sel":
            return _selector_bits(component)
        if pin_name == "out" or pin_name.startswith("in"):
            return _bits(component)
        return None

    if e == "Decoder":
        if pin_name == "sel":
            return _selector_bits(component)
        if pin_name.startswith("out"):
            return 1
        return None

    if e == "PriorityEncoder":
        if pin_name == "num":
            return _selector_bits(component)
        if pin_name.startswith("in"):
            return 1
        return None

    if e == "Register":
        if pin_name in ("D", "Q"):
            return _bits(component)
        if pin_name in ("C", "en"):
            return 1
        return None

    if e == "ROM":
        if pin_name == "A":
            return int(component.attributes.get("AddrBits", _BITS_DEFAULT))
        if pin_name == "sel":
            return 1
        if pin_name == "D":
            return _bits(component)
        return None

    if e == "BitExtender":
        if pin_name == "in":
            return int(component.attributes.get("inputBits", _BITS_DEFAULT))
        if pin_name == "out":
            return int(component.attributes.get("outputBits", _BITS_DEFAULT))
        return None
    
    if e == "Splitter":
        return _splitter_group_width(component, pin_name)
    
    if e == "Seven-Seg":
        if pin_name in ("a", "b", "c", "d", "e", "f", "g", "dp"):
            return 1
        return None
    
    if e == "Tunnel":
        return None
    
    if e in ("Testcase", "Rectangle"):
        return None
    
    if e.endswith(".dig"):
        return None

    return None