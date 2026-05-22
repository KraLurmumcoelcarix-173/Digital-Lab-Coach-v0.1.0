"""
Tests for dlc.testing.spec 
"""

from pathlib import Path

from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import (
    Token, TestRow, TestSpec, VariableBinding,
    _tokenize, parse_data_string, extract_test_specs, match_variables_to_io,
)


SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample_circuits"

def test_tokenize_plain_decimal():
    t = _tokenize("25")
    assert t.kind == "int" and t.value == 25 and t.raw == "25"


def test_tokenize_negative_decimal_without_parens():
    t = _tokenize("-5")
    assert t.kind == "int" and t.value == -5


def test_tokenize_hex_lowercase_prefix():
    t = _tokenize("0xCB126889")
    assert t.kind == "int" and t.value == 0xCB126889


def test_tokenize_hex_uppercase_prefix():
    t = _tokenize("0X7F0")
    assert t.kind == "int" and t.value == 0x7F0


def test_tokenize_binary():
    t = _tokenize("0b0110011")
    assert t.kind == "int" and t.value == 0b0110011 == 51


def test_tokenize_parens_negative():
    t = _tokenize("(-20)")
    assert t.kind == "int" and t.value == -20


def test_tokenize_parens_positive():
    t = _tokenize("(5)")
    assert t.kind == "int" and t.value == 5


def test_tokenize_clock_pulse_uppercase_only():
    assert _tokenize("C").kind == "clock"
    assert _tokenize("c").kind == "unknown"


def test_tokenize_highz_and_dontcare():
    assert _tokenize("z").kind == "highZ"
    assert _tokenize("Z").kind == "highZ"
    assert _tokenize("x").kind == "dontcare"
    assert _tokenize("X").kind == "dontcare"


def test_tokenize_loop_expressions():
    assert _tokenize("(N+1)").kind == "loop_expr"
    assert _tokenize("(N-60)").kind == "loop_expr"
    assert _tokenize("(N)").kind == "loop_expr"


def test_tokenize_unknown_is_recorded_not_raised():
    t = _tokenize("not-a-number")
    assert t.kind == "unknown" and t.value is None

def test_parse_data_string_single_and_pattern():
    text = "A B Y\n0 0 0\n0 1 0\n1 0 0\n1 1 1"
    headers, rows, has_loops = parse_data_string(text)
    assert headers == ["A", "B", "Y"]
    assert len(rows) == 4
    assert not has_loops
    assert rows[3].values[2].value == 1


def test_parse_data_string_strips_inline_comments():
    text = (
        "clk ReadData1 ReadData2\n"
        "0 0 0        # addi x4, x0, -20\n"
        "C (-20) 0    # addi x5, x4, 0x7F0\n"
    )
    headers, rows, has_loops = parse_data_string(text)
    assert headers == ["clk", "ReadData1", "ReadData2"]
    assert len(rows) == 2
    assert rows[1].values[0].kind == "clock"
    assert rows[1].values[1].value == -20
    assert not has_loops


def test_parse_data_string_skips_comment_only_lines_and_blanks():
    text = (
        "A B ALUOp Result FlagZ\n"
        "\n"
        "# ADD tests (ALUOp = 0b0010)\n"
        "0x289322C4 0x01846399 0b0010 0x2A17865D 0\n"
        "\n"
        "# SUB tests (ALUOp = 0b0110)\n"
        "0x39332544 0x0435FCAD 0b0110 0x34FD2897 0\n"
    )
    headers, rows, has_loops = parse_data_string(text)
    assert headers == ["A", "B", "ALUOp", "Result", "FlagZ"]
    assert len(rows) == 2
    assert rows[0].values[2].value == 0b0010
    assert rows[0].values[3].value == 0x2A17865D
    assert not has_loops


def test_parse_data_string_expands_loop_blocks():
    text = (
        "A B C D\n"
        "loop(N, 30)\n"
        "(N+1) (N+1) 1 C\n"
        "end loop\n"
    )
    headers, rows, has_unexpanded = parse_data_string(text)
    assert headers == ["A", "B", "C", "D"]
    assert len(rows) == 30
    assert not has_unexpanded
    assert rows[0].values[0].kind == "int"
    assert rows[0].values[0].value == 1
    assert rows[0].values[3].kind == "clock"
    assert rows[29].values[0].value == 30


def test_loop_expansion_handles_negative_results():
    text = "A B\nloop(N, 3)\n(N+1) (N-60)\nend loop\n"
    _, rows, _ = parse_data_string(text)
    assert [r.values[1].value for r in rows] == [-60, -59, -58]
    assert [r.values[0].value for r in rows] == [1, 2, 3]


def test_register_file_full_dataString_expands_to_93_rows():
    text = (
        "WriteReg WriteData RegWrite Clock ReadReg1 ReadReg2 ReadData1 ReadData2\n"
        "\n"
        "# $1 = 25\n"
        "1 25 1 C 1 0 25 0\n"
        "\n"
        "# $2 = 30\n"
        "2 30 1 C 0 2 0 30\n"
        "\n"
        "loop(N, 30)\n"
        "(N+1) (N+1) 1 C (N+1) (N) (N+1) (N)\n"
        "end loop\n"
        "\n"
        "loop(N, 30)\n"
        "(N+1) (N-60) 1 C (N+1) (N+1) (N-60) (N-60)\n"
        "end loop\n"
        "\n"
        "0 25 1 C 0 0 0 0\n"
        "\n"
        "loop(N, 30)\n"
        "(N+1) (N+80) 0 C (N+1) (N+1) (N-60) (N-60)\n"
        "end loop\n"
    )
    _, rows, has_unexpanded = parse_data_string(text)
    assert len(rows) == 93
    assert not has_unexpanded
    assert rows[2].values[0].value == 1
    assert rows[2].values[5].value == 0
    assert rows[32].values[1].value == -60


def test_unrecognized_loop_variable_keeps_loop_expr_and_flags_unexpanded():
    text = "A\nloop(N, 3)\n(M)\nend loop\n"
    _, rows, has_unexpanded = parse_data_string(text)
    assert len(rows) == 3
    assert all(r.values[0].kind == "loop_expr" for r in rows)
    assert has_unexpanded


def test_unclosed_loop_block_flags_unexpanded():
    text = "A\nloop(N, 3)\n(N+1)\n"
    _, rows, has_unexpanded = parse_data_string(text)
    assert rows == []
    assert has_unexpanded


def test_multiple_loops_in_one_datastring():
    text = (
        "A B\n"
        "loop(N, 2)\n(N+1) (N)\nend loop\n"
        "loop(K, 3)\n(K) (K+10)\nend loop\n"
    )
    _, rows, has_unexpanded = parse_data_string(text)
    assert len(rows) == 5     
    assert not has_unexpanded
    assert rows[0].values[0].value == 1   
    assert rows[2].values[0].value == 0   
    assert rows[4].values[1].value == 12  

def test_parse_data_string_malformed_row_recorded_but_kept():

    text = "A B Y\n0 0 0\n0 1\n1 0 0\n"
    headers, rows, has_loops = parse_data_string(text)
    assert headers == ["A", "B", "Y"]
    assert len(rows) == 3
    assert rows[1].is_malformed and rows[1].values == []
    assert not rows[0].is_malformed and not rows[2].is_malformed


def test_parse_data_string_empty_input():
    assert parse_data_string("") == ([], [], False)
    assert parse_data_string("   \n  \n") == ([], [], False)
    assert parse_data_string("# just a comment\n# another\n") == ([], [], False)


def test_extract_test_specs_from_single_and():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.headers == ["A", "B", "Y"]
    assert spec.row_count() == 4
    assert spec.well_formed_row_count() == 4
    assert not spec.has_unexpanded_loops


def test_extract_test_specs_falls_back_to_positional_name(tmp_path):
    """A Testcase with no Label gets 'Testcase_{index}'."""
    dig = tmp_path / "nolabel.dig"
    dig.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<circuit><version>2</version><attributes/>'
        '<visualElements>'
        '<visualElement>'
        '<elementName>Testcase</elementName>'
        '<elementAttributes><entry>'
        '<string>Testdata</string>'
        '<testData><dataString>X Y\n0 0</dataString></testData>'
        '</entry></elementAttributes>'
        '<pos x="0" y="0"/>'
        '</visualElement>'
        '</visualElements><wires/></circuit>'
    )
    c = parse_dig_file(str(dig))
    specs = extract_test_specs(c)
    assert len(specs) == 1
    assert specs[0].name == "Testcase_0"


def test_extract_test_specs_returns_empty_when_no_testcase(tmp_path):
    dig = tmp_path / "no_tc.dig"
    dig.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<circuit><version>2</version><attributes/>'
        '<visualElements>'
        '<visualElement><elementName>In</elementName>'
        '<elementAttributes><entry>'
        '<string>Label</string><string>A</string>'
        '</entry></elementAttributes>'
        '<pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    c = parse_dig_file(str(dig))
    assert extract_test_specs(c) == []


def test_extract_test_specs_empty_dataString_yields_empty_spec(tmp_path):
    dig = tmp_path / "empty.dig"
    dig.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<circuit><version>2</version><attributes/>'
        '<visualElements>'
        '<visualElement>'
        '<elementName>Testcase</elementName>'
        '<elementAttributes><entry>'
        '<string>Testdata</string>'
        '<testData><dataString></dataString></testData>'
        '</entry></elementAttributes>'
        '<pos x="0" y="0"/>'
        '</visualElement>'
        '</visualElements><wires/></circuit>'
    )
    c = parse_dig_file(str(dig))
    specs = extract_test_specs(c)
    assert len(specs) == 1
    assert specs[0].headers == []
    assert specs[0].rows == []



def test_match_variables_to_io_full_adder():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "full_adder.dig"))
    bindings = match_variables_to_io(["A", "B", "Cin", "Sum", "Cout"], c)
    assert bindings["A"].role == "input" and bindings["A"].bit_width == 1
    assert bindings["B"].role == "input"
    assert bindings["Cin"].role == "input"
    assert bindings["Sum"].role == "output"
    assert bindings["Cout"].role == "output"


def test_match_variables_marks_unbound_columns():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "full_adder.dig"))
    bindings = match_variables_to_io(["A", "B", "MysteryCol"], c)
    assert bindings["MysteryCol"].role == "unbound"
    assert bindings["MysteryCol"].component_index is None


def test_match_variables_resolves_clock_column():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "register_test.dig"))
    clocks = [(i, comp) for i, comp in enumerate(c.components) if comp.element_name == "Clock"]
    assert len(clocks) == 1
    clock_label = clocks[0][1].label
    if clock_label is None:
        return
    bindings = match_variables_to_io([clock_label], c)
    assert bindings[clock_label].role == "clock"
    assert bindings[clock_label].bit_width == 1

def test_testcase_dataString_actually_extracted_for_spec():
    c = parse_dig_file(str(SAMPLES_DIR / "tier1_minimal" / "single_and.dig"))
    specs = extract_test_specs(c)
    assert specs[0].raw_data_string.startswith("A B Y")

