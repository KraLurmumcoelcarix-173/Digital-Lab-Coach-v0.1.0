"""
Subcircuit-instance pin placement must match Digital's GenericShape.
"""

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import _subcircuit_pin_specs, build_netlist
from dlc.analyzer import check_all_l1


def _in(label, x, y):
    return (
        '<visualElement><elementName>In</elementName>'
        '<elementAttributes><entry><string>Label</string>'
        f'<string>{label}</string></entry></elementAttributes>'
        f'<pos x="{x}" y="{y}"/></visualElement>'
    )


def _clock(label, x, y):
    return (
        '<visualElement><elementName>Clock</elementName>'
        '<elementAttributes><entry><string>Label</string>'
        f'<string>{label}</string></entry></elementAttributes>'
        f'<pos x="{x}" y="{y}"/></visualElement>'
    )


def _out(label, x, y):
    return (
        '<visualElement><elementName>Out</elementName>'
        '<elementAttributes><entry><string>Label</string>'
        f'<string>{label}</string></entry></elementAttributes>'
        f'<pos x="{x}" y="{y}"/></visualElement>'
    )


def _circuit(elements, width=None):
    attrs = (
        '<attributes><entry><string>Width</string>'
        f'<int>{width}</int></entry></attributes>'
    ) if width is not None else '<attributes/>'
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<circuit><version>2</version>{attrs}'
        f'<visualElements>{"".join(elements)}</visualElements>'
        '<wires/></circuit>'
    )


def _child_specs(tmp_path, n_in, n_out, width=None, clock_last=False):
    elements = []
    for i in range(n_in - (1 if clock_last else 0)):
        elements.append(_in(f"I{i}", 0, i * 60))
    if clock_last:
        elements.append(_clock("CK", 0, (n_in - 1) * 60))
    for i in range(n_out):
        elements.append(_out(f"O{i}", 400, i * 60))
    path = tmp_path / "child.dig"
    path.write_text(_circuit(elements, width=width))
    child = parse_dig_file(str(path))
    specs = _subcircuit_pin_specs(child)
    ins = [(s.name, s.offset_x, s.offset_y) for s in specs if s.direction == "in"]
    outs = [(s.name, s.offset_x, s.offset_y) for s in specs if s.direction == "out"]
    return ins, outs


def test_single_output_two_inputs_skips_centre(tmp_path):
    ins, outs = _child_specs(tmp_path, 2, 1)
    assert [(x, y) for _, x, y in ins] == [(0, 0), (0, 40)]
    assert [(x, y) for _, x, y in outs] == [(60, 20)]


def test_single_output_three_inputs_centres_output(tmp_path):
    ins, outs = _child_specs(tmp_path, 3, 1)
    assert [(x, y) for _, x, y in ins] == [(0, 0), (0, 20), (0, 40)]
    assert [(x, y) for _, x, y in outs] == [(60, 20)]


def test_single_output_four_inputs_skips_centre(tmp_path):
    ins, outs = _child_specs(tmp_path, 4, 1)
    assert [(x, y) for _, x, y in ins] == [(0, 0), (0, 20), (0, 60), (0, 80)]
    assert [(x, y) for _, x, y in outs] == [(60, 40)]


def test_single_output_five_inputs_centres_output(tmp_path):
    ins, outs = _child_specs(tmp_path, 5, 1)
    assert [(x, y) for _, x, y in ins] == [
        (0, 0), (0, 20), (0, 40), (0, 60), (0, 80)]
    assert [(x, y) for _, x, y in outs] == [(60, 40)]


def test_single_output_single_input_plain(tmp_path):
    ins, outs = _child_specs(tmp_path, 1, 1)
    assert [(x, y) for _, x, y in ins] == [(0, 0)]
    assert [(x, y) for _, x, y in outs] == [(60, 0)]


def test_multi_output_stacks_plainly(tmp_path):
    ins, outs = _child_specs(tmp_path, 3, 2)
    assert [(x, y) for _, x, y in ins] == [(0, 0), (0, 20), (0, 40)]
    assert [(x, y) for _, x, y in outs] == [(60, 0), (60, 20)]


def test_multi_output_even_inputs_no_skip(tmp_path):
    ins, outs = _child_specs(tmp_path, 4, 3)
    assert [(x, y) for _, x, y in ins] == [(0, 0), (0, 20), (0, 40), (0, 60)]
    assert [(x, y) for _, x, y in outs] == [(60, 0), (60, 20), (60, 40)]


def test_width_attribute_moves_output_column(tmp_path):
    ins, outs = _child_specs(tmp_path, 5, 1, width=8)
    assert [(x, y) for _, x, y in outs] == [(160, 40)]


def test_clock_counts_as_input_slot(tmp_path):
    ins, outs = _child_specs(tmp_path, 5, 1, clock_last=True)
    assert [n for n, _, _ in ins] == ["I0", "I1", "I2", "I3", "CK"]
    assert [(x, y) for _, x, y in ins] == [
        (0, 0), (0, 20), (0, 40), (0, 60), (0, 80)]
    assert [(x, y) for _, x, y in outs] == [(60, 40)]


def _wire(x1, y1, x2, y2):
    return f'<wire><p1 x="{x1}" y="{y1}"/><p2 x="{x2}" y="{y2}"/></wire>'


def test_five_in_one_out_instance_binds_at_real_positions(tmp_path):
    # Parent wires the instance at Digital's REAL pin spots: inputs at
    # y+0..y+80 and the single output at (x+60, y+40). Before the
    # GenericShape fix DLC looked for that output at (x+60, y+0), missed
    # it, and reported the downstream input as undriven.
    elements = [_in(f"I{i}", 0, i * 60) for i in range(5)]
    elements.append(_out("Y", 400, 0))
    (tmp_path / "five.dig").write_text(_circuit(elements))

    parent_elems = [_in(f"P{i}", -100, i * 20) for i in range(5)]
    parent_elems.append(
        '<visualElement><elementName>five.dig</elementName>'
        '<elementAttributes/><pos x="0" y="0"/></visualElement>'
    )
    parent_elems.append(_out("R", 160, 40))
    wires = [_wire(-80, i * 20, 0, i * 20) for i in range(5)]
    wires.append(_wire(60, 40, 160, 40))
    parent = tmp_path / "parent.dig"
    parent.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<circuit><version>2</version><attributes/>'
        f'<visualElements>{"".join(parent_elems)}</visualElements>'
        f'<wires>{"".join(wires)}</wires></circuit>'
    )
    circ = parse_dig_file(str(parent))
    issues = check_all_l1(circ)
    assert not [b for b in issues.issues if b.kind == "dangling_input"], \
        [b.title for b in issues.issues]
    netlist = build_netlist(circ)
    inst_idx = next(
        i for i, c in enumerate(circ.components)
        if c.element_name == "five.dig")
    bound = {
        (p.component_index, p.pin_name)
        for net in netlist.nets for p in net.pins
    }
    for pin in ("I0", "I1", "I2", "I3", "I4", "Y"):
        assert (inst_idx, pin) in bound


def test_two_in_one_out_instance_binds_at_real_positions(tmp_path):
    elements = [_in("A", 0, 0), _in("B", 0, 80), _out("Y", 400, 0)]
    (tmp_path / "two.dig").write_text(_circuit(elements))

    parent_elems = [
        _in("PA", -100, 0), _in("PB", -100, 40),
        '<visualElement><elementName>two.dig</elementName>'
        '<elementAttributes/><pos x="0" y="0"/></visualElement>',
        _out("R", 160, 20),
    ]
    wires = [
        _wire(-80, 0, 0, 0), _wire(-80, 40, 0, 40),
        _wire(60, 20, 160, 20),
    ]
    parent = tmp_path / "parent.dig"
    parent.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<circuit><version>2</version><attributes/>'
        f'<visualElements>{"".join(parent_elems)}</visualElements>'
        f'<wires>{"".join(wires)}</wires></circuit>'
    )
    circ = parse_dig_file(str(parent))
    issues = check_all_l1(circ)
    assert not [b for b in issues.issues if b.kind == "dangling_input"], \
        [b.title for b in issues.issues]
