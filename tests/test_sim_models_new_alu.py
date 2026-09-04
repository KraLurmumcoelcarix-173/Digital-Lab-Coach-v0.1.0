from dlc.parser.dig_parser import parse_dig_file
from dlc.sim import models as M


def _ev(name, **inp):
    return M.BY_NAME[name].evaluate(inp, None)[0]


def test_lab5_alu_shifts_b_by_a_with_the_64_bit_style_amount():
    assert _ev("lab5_alu", A=1, B=8, ALUOp=4)["Result"] == 16
    assert _ev("rv32i_alu", A=1, B=8, ALUOp=4)["Result"] == 256
    assert _ev("lab5_alu", A=33, B=8, ALUOp=4)["Result"] == 0
    assert _ev("lab5_alu", A=1, B=0x80000001, ALUOp=8)["Result"] == 0xC0000000
    assert _ev("lab5_alu", A=40, B=0x80000001, ALUOp=8)["Result"] == 0xFFFFFFFF
    assert _ev("lab5_alu", A=4, B=0x80000001, ALUOp=5)["Result"] == 0x08000000
    assert _ev("lab5_alu", A=7, B=7, ALUOp=6) == {"Result": 0, "FlagZ": 1}
    assert _ev("lab5_alu", A=0xFFFFFFFF, B=5, ALUOp=9)["Result"] == 4


def _alu_child(tmp_path, rows):
    def elem(name, label, bits, x, y):
        return (f'<visualElement><elementName>{name}</elementName>'
                '<elementAttributes>'
                f'<entry><string>Label</string><string>{label}</string></entry>'
                f'<entry><string>Bits</string><int>{bits}</int></entry>'
                f'</elementAttributes><pos x="{x}" y="{y}"/></visualElement>')
    body = (elem("In", "A", 32, 0, 0) + elem("In", "B", 32, 0, 60)
            + elem("In", "ALUOp", 4, 0, 120)
            + elem("Out", "Result", 32, 400, 0) + elem("Out", "FlagZ", 1, 400, 60))
    body += ('<visualElement><elementName>Testcase</elementName>'
             '<elementAttributes><entry><string>Testdata</string>'
             f'<testData><dataString>A B ALUOp Result FlagZ\n{rows}\n</dataString></testData>'
             '</entry></elementAttributes><pos x="0" y="500"/></visualElement>')
    p = tmp_path / "alu.dig"
    p.write_text('<?xml version="1.0" encoding="utf-8"?><circuit><version>2</version>'
                 f'<attributes/><visualElements>{body}</visualElements><wires/></circuit>')
    return parse_dig_file(str(p))


def test_the_childs_own_test_picks_the_right_alu(tmp_path):
    old = _alu_child(tmp_path, "1 8 4 16 0\n7 7 6 0 1")
    assert [m.name for m in M.candidates(old)] == ["rv32i_alu", "lab5_alu"]
    assert M.validate(M.BY_NAME["lab5_alu"], old)[0] is True
    assert M.validate(M.BY_NAME["rv32i_alu"], old)[0] is False
    new = _alu_child(tmp_path, "1 8 4 256 0\n7 7 6 0 1")
    assert M.validate(M.BY_NAME["rv32i_alu"], new)[0] is True
    assert M.validate(M.BY_NAME["lab5_alu"], new)[0] is False
