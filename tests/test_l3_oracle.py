"""L3 oracle, row-injection half (dlc/l3/oracle.py).

Covers: byte-preserving dataString injection, row validation, multi-testcase
targeting, XML escaping, and (jar-gated) the inject + per-row rerun loop
Mode B's accept-flow uses.
"""

import glob
import os
from pathlib import Path

import pytest

from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs
from dlc.testing.runner import find_digital_jar
from dlc.l3.oracle import (
    InjectedRow,
    inject_rows_text,
    rerun_with_rows,
    validate_rows,
    write_temp_with_rows,
)

_CALC = "data/sample_circuits/tier3_realistic/tier3_calculator.dig"


def _calc_spec_name() -> str:
    return extract_test_specs(parse_dig_file(_CALC))[0].name


# ---------------------------------------------------------------------------
# Injection is byte-preserving outside the target block
# ---------------------------------------------------------------------------

def test_injected_temp_differs_only_inside_datastring(tmp_path):
    spec_name = _calc_spec_name()
    rows = [InjectedRow("5 3 0 0 8 0 0 0"), InjectedRow("5 3 0 0 8 0 0 0")]
    temp_path, spec = write_temp_with_rows(_CALC, spec_name, rows)
    try:
        original = Path(_CALC).read_text(encoding="utf-8")
        injected = Path(temp_path).read_text(encoding="utf-8")
        # identical up to the closing tag of the (single) dataString block...
        cut = original.index("</dataString>")
        assert injected.startswith(original[:cut])
        # ...and identical from that closing tag to EOF.
        assert injected.endswith(original[cut:])
        # the injected middle carries exactly our two extra lines
        assert injected.count("5 3 0 0 8 0 0 0") == original.count("5 3 0 0 8 0 0 0") + 2
    finally:
        os.unlink(temp_path)


def test_injected_temp_parses_with_appended_rows():
    spec_name = _calc_spec_name()
    orig_spec = extract_test_specs(parse_dig_file(_CALC))[0]
    rows = [InjectedRow("1 1 0 0 2 0 0 0", origin="coach"),
            InjectedRow("2 2 0 0 4 0 0 0", origin="student")]
    temp_path, _ = write_temp_with_rows(_CALC, spec_name, rows)
    try:
        temp_spec = extract_test_specs(parse_dig_file(temp_path))[0]
        assert temp_spec.headers == orig_spec.headers
        assert temp_spec.row_count() == orig_spec.row_count() + 2
        appended = temp_spec.rows[-2:]
        assert appended[0].raw == "1 1 0 0 2 0 0 0"
        assert appended[1].raw == "2 2 0 0 4 0 0 0"
        assert not appended[0].is_malformed and not appended[1].is_malformed
    finally:
        os.unlink(temp_path)


def test_original_file_never_modified():
    before = Path(_CALC).read_bytes()
    temp_path, _ = write_temp_with_rows(
        _CALC, _calc_spec_name(), [InjectedRow("5 3 0 0 8 0 0 0")],
    )
    os.unlink(temp_path)
    assert Path(_CALC).read_bytes() == before


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _calc_spec():
    return extract_test_specs(parse_dig_file(_CALC))[0]


def test_validate_rejects_wrong_cell_count():
    with pytest.raises(ValueError, match="cells"):
        validate_rows(_calc_spec(), [InjectedRow("1 2 3")])


def test_validate_rejects_unknown_token():
    with pytest.raises(ValueError, match="unrecognized"):
        validate_rows(_calc_spec(), [InjectedRow("1 1 0 0 banana 0 0 0")])


def test_validate_rejects_loop_expression():
    with pytest.raises(ValueError, match="loop expression"):
        validate_rows(_calc_spec(), [InjectedRow("(n+1) 1 0 0 2 0 0 0")])


def test_validate_rejects_multiline_and_empty():
    with pytest.raises(ValueError, match="single line"):
        validate_rows(_calc_spec(), [InjectedRow("1 1 0 0 2 0 0 0\n2 2 0 0 4 0 0 0")])
    with pytest.raises(ValueError, match="empty"):
        validate_rows(_calc_spec(), [InjectedRow("   # only a comment")])
    with pytest.raises(ValueError, match="No rows"):
        validate_rows(_calc_spec(), [])


def test_validate_allows_trailing_comment_and_special_tokens():
    # clock / don't-care / hex / parenthesized negative are all legal cells
    validate_rows(_calc_spec(), [InjectedRow("0x1 1 0 0 (-2) x 0 1  # why: edge")])


# ---------------------------------------------------------------------------
# Multi-testcase targeting + escaping
# ---------------------------------------------------------------------------

_TWO_TC_DIG = """<?xml version="1.0" encoding="utf-8"?>
<circuit>
  <version>2</version>
  <attributes/>
  <visualElements>
    <visualElement>
      <elementName>In</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>A</string></entry>
      </elementAttributes>
      <pos x="0" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>Out</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>Y</string></entry>
      </elementAttributes>
      <pos x="100" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>Testcase</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>T1</string></entry>
        <entry><string>Testdata</string><testData><dataString>A Y
0 0</dataString></testData></entry>
      </elementAttributes>
      <pos x="0" y="100"/>
    </visualElement>
    <visualElement>
      <elementName>Testcase</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>T2</string></entry>
        <entry><string>Testdata</string><testData><dataString>A Y
1 1</dataString></testData></entry>
      </elementAttributes>
      <pos x="0" y="200"/>
    </visualElement>
  </visualElements>
  <wires>
    <wire><p1 x="0" y="0"/><p2 x="100" y="0"/></wire>
  </wires>
  <measurementOrdering/>
</circuit>
"""


def test_injection_targets_named_testcase_only(tmp_path):
    src = tmp_path / "two_tc.dig"
    src.write_text(_TWO_TC_DIG, encoding="utf-8")
    temp_path, _ = write_temp_with_rows(str(src), "T2", [InjectedRow("0 0")])
    try:
        specs = {s.name: s for s in extract_test_specs(parse_dig_file(temp_path))}
        assert specs["T1"].row_count() == 1          # untouched
        assert specs["T2"].row_count() == 2          # extended
        assert specs["T2"].rows[-1].raw == "0 0"
    finally:
        os.unlink(temp_path)


def test_unknown_testcase_name_raises(tmp_path):
    src = tmp_path / "two_tc.dig"
    src.write_text(_TWO_TC_DIG, encoding="utf-8")
    with pytest.raises(ValueError, match="No testcase named"):
        write_temp_with_rows(str(src), "nope", [InjectedRow("0 0")])


def test_comment_with_xml_specials_is_escaped(tmp_path):
    src = tmp_path / "two_tc.dig"
    src.write_text(_TWO_TC_DIG, encoding="utf-8")
    temp_path, _ = write_temp_with_rows(
        str(src), "T1", [InjectedRow("1 1 # a<b & c>d")],
    )
    try:
        text = Path(temp_path).read_text(encoding="utf-8")
        assert "a&lt;b &amp; c&gt;d" in text
        parsed = extract_test_specs(parse_dig_file(temp_path))  # still valid XML
        assert {s.name for s in parsed} == {"T1", "T2"}
    finally:
        os.unlink(temp_path)


def test_inject_rows_text_ordinal_bounds():
    with pytest.raises(ValueError, match="not found"):
        inject_rows_text("<x>no blocks here</x>", 0, [InjectedRow("1")])


# ---------------------------------------------------------------------------
# Inject + rerun through the real Digital CLI (jar-gated)
# ---------------------------------------------------------------------------

_needs_jar = pytest.mark.skipif(
    find_digital_jar() is None, reason="Digital.jar not configured",
)


@_needs_jar
def test_rerun_flags_wrong_added_row_and_keeps_originals_green():
    spec_name = _calc_spec_name()
    good = InjectedRow("5 3 0 0 8 0 0 0", origin="coach")       # duplicate of row 0
    bad = InjectedRow("5 3 0 0 9 0 0 0", origin="coach")        # wrong expected Result
    out = rerun_with_rows(_CALC, spec_name, [good, bad])
    assert out.ok, out.warning
    originals = [r for r in out.rows if not r["added"]]
    added = [r for r in out.rows if r["added"]]
    assert all(r["status"] == "passed" for r in originals)
    assert [r["origin"] for r in added] == ["coach", "coach"]
    assert added[0]["status"] == "passed"
    assert added[1]["status"] == "failed"
    assert added[1]["mismatches"], "failed injected row should carry expected-vs-found"
    assert out.all_passed is False
    assert out.added_all_passed is False


@_needs_jar
def test_rerun_all_added_pass_sets_mode_b_lock_signal():
    out = rerun_with_rows(
        _CALC, _calc_spec_name(), [InjectedRow("5 3 0 0 8 0 0 0")],
    )
    assert out.ok
    assert out.added_all_passed is True
    assert out.all_passed is True
    assert out.temp_path is None                     # cleaned up by default
    leftovers = glob.glob("data/sample_circuits/tier3_realistic/dlc_row_l3_*.dig")
    assert leftovers == []


@_needs_jar
def test_rerun_keep_temp_hands_ownership_to_caller():
    out = rerun_with_rows(
        _CALC, _calc_spec_name(), [InjectedRow("5 3 0 0 8 0 0 0")],
        keep_temp=True,
    )
    assert out.ok and out.temp_path and os.path.exists(out.temp_path)
    os.unlink(out.temp_path)

# ---------------------------------------------------------------------------
# 2.10: second testcase + program-ROM extension
# ---------------------------------------------------------------------------

from dlc.l3.oracle import (              # noqa: E402
    add_testcase_text,
    extend_program_rom_text,
    find_program_rom,
    parse_program_words,
    rerun_with_second,
)

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"

_ROMISH = """<circuit><visualElements>
    <visualElement>
      <elementName>ROM</elementName>
      <elementAttributes>
        <entry><string>AddrBits</string><int>3</int></entry>
        <entry><string>isProgramMemory</string><boolean>true</boolean></entry>
        <entry><string>Data</string><data>13,93</data></entry>
      </elementAttributes>
      <pos x="0" y="40"/>
    </visualElement>
  </visualElements><wires/></circuit>"""


def test_program_rom_found_extended_and_capacity_guarded():
    words, addr_bits, _span = find_program_rom(_ROMISH)
    assert words == [0x13, 0x93] and addr_bits == 3
    out = extend_program_rom_text(_ROMISH, [0x628E33])
    assert "<data>13,93,628e33</data>" in out
    assert find_program_rom(out)[0] == [0x13, 0x93, 0x628E33]
    with pytest.raises(ValueError):                 # 3 addr bits = 8 words max
        extend_program_rom_text(_ROMISH, [1] * 7)
    assert find_program_rom("<circuit><visualElements/></circuit>") is None


def test_parse_program_words_accepts_hex_rejects_junk():
    assert parse_program_words(["628e33", "0x40430EB3"]) == [0x628E33, 0x40430EB3]
    with pytest.raises(ValueError):
        parse_program_words(["not-hex"])
    with pytest.raises(ValueError):
        parse_program_words(["1ffffffff"])          # > 32 bits


def test_add_testcase_text_second_spec_original_untouched(tmp_path):
    original = Path(_CALC).read_text(encoding="utf-8")
    spec = extract_test_specs(parse_dig_file(_CALC))[0]
    label = f"{spec.name}_second"
    ds = " ".join(spec.headers) + "\n1 1 0 0 2 0 0 0"
    out = add_testcase_text(original, label, ds)
    p = tmp_path / "calc.dig"
    p.write_text(out, encoding="utf-8")
    specs = extract_test_specs(parse_dig_file(str(p)))
    second = next(s for s in specs if s.name == label)
    assert second.headers == spec.headers and second.row_count() == 1
    base = next(s for s in specs if s.name == spec.name)
    assert base.row_count() == spec.row_count()     # official spec untouched
    assert base.raw_data_string == spec.raw_data_string


@_needs_jar
def test_rerun_with_second_runs_both_specs_and_guards_base():
    spec_name = extract_test_specs(parse_dig_file(_AND))[0].name
    out = rerun_with_second(_AND, spec_name, [InjectedRow("1 0 0")], [])
    assert out.ok is True
    assert out.spec_name == f"{spec_name}_second"
    assert out.spec_index == 1                      # appended after spec 0
    assert [r["status"] for r in out.rows] == ["passed"]
    assert out.rows[0]["added"] is True and out.rows[0]["origin"] == "coach"
    assert out.base_spec == {"name": spec_name, "total": 4,
                             "passed": 4, "all_passed": True}
    assert out.all_passed is True and out.added_all_passed is True


@_needs_jar
def test_rerun_with_second_needs_a_program_rom_for_words():
    spec_name = extract_test_specs(parse_dig_file(_AND))[0].name
    out = rerun_with_second(_AND, spec_name, [InjectedRow("1 0 0")], ["13"])
    assert out.ok is False
    assert "program memory" in (out.warning or "")


# ---------------------------------------------------------------------------
# append-mode program injection — rows join the OFFICIAL testcase
# ---------------------------------------------------------------------------

from types import SimpleNamespace                    # noqa: E402

from dlc.l3 import oracle as _om                     # noqa: E402
from dlc.l3.oracle import rerun_with_program         # noqa: E402

_PROGFX = """<?xml version="1.0" encoding="utf-8"?>
<circuit>
  <version>2</version>
  <attributes/>
  <visualElements>
    <visualElement>
      <elementName>ROM</elementName>
      <elementAttributes>
        <entry><string>AddrBits</string><int>3</int></entry>
        <entry><string>isProgramMemory</string><boolean>true</boolean></entry>
        <entry><string>Data</string><data>13,93</data></entry>
      </elementAttributes>
      <pos x="0" y="40"/>
    </visualElement>
    <visualElement>
      <elementName>Testcase</elementName>
      <elementAttributes>
        <entry>
          <string>Label</string>
          <string>prog</string>
        </entry>
        <entry>
          <string>Testdata</string>
          <testData>
            <dataString>Clk Q
0 X
C 1</dataString>
          </testData>
        </entry>
      </elementAttributes>
      <pos x="0" y="200"/>
    </visualElement>
  </visualElements>
  <wires/>
</circuit>"""


def _fake_per_row(statuses_by_spec=None):
    """per_row_run_auto stand-in: every row passes unless overridden."""
    def fake(spec, path, jar_path=None, timeout=60.0):
        wanted = (statuses_by_spec or {}).get(spec.name, {})
        return [SimpleNamespace(spec_name=spec.name, row_index=r.line_index,
                                status=wanted.get(r.line_index, "passed"),
                                error_message=None, mismatches=[])
                for r in spec.rows if not r.is_malformed]
    return fake


@pytest.fixture()
def _progfx(tmp_path, monkeypatch):
    p = tmp_path / "prog.dig"
    p.write_text(_PROGFX, encoding="utf-8")
    monkeypatch.setattr(_om, "find_digital_jar", lambda: "fake.jar")
    monkeypatch.setattr(_om, "per_row_run_auto", _fake_per_row())
    return p


def test_rerun_with_program_appends_to_official_spec(_progfx):
    out = rerun_with_program(
        str(_progfx), "prog", [InjectedRow("C 5"), InjectedRow("C 9")],
        ["628e33", "40628433"], keep_temp=True)
    try:
        assert out.ok is True
        assert out.spec_name == "prog" and out.spec_index == 0
        assert out.rom_program == "13,93,628e33,40628433"
        # official rows first (origin "original"), coach rows appended
        assert [r["added"] for r in out.rows] == [False, False, True, True]
        assert [r["origin"] for r in out.rows] == [
            "original", "original", "coach", "coach"]
        assert out.base_spec == {"name": "prog", "total": 2, "passed": 2,
                                 "all_passed": True}
        assert out.all_passed is True and out.added_all_passed is True
        # the temp really carries the extended ROM + appended rows;
        # no second testcase anywhere
        temp = Path(out.temp_path).read_text(encoding="utf-8")
        assert "<data>13,93,628e33,40628433</data>" in temp
        specs = extract_test_specs(parse_dig_file(out.temp_path))
        assert [s.name for s in specs] == ["prog"]
        assert specs[0].row_count() == 4
    finally:
        os.unlink(out.temp_path)


def test_rerun_with_program_reports_official_regression(_progfx, monkeypatch):
    monkeypatch.setattr(_om, "per_row_run_auto",
                        _fake_per_row({"prog": {1: "failed"}}))
    out = rerun_with_program(
        str(_progfx), "prog", [InjectedRow("C 5")], ["628e33"])
    assert out.ok is True
    assert out.base_spec == {"name": "prog", "total": 2, "passed": 1,
                             "all_passed": False}
    assert out.all_passed is False          # regression trips the lock…
    assert out.added_all_passed is True     # …even with the new row green


def test_rerun_with_program_validation_errors(_progfx):
    out = rerun_with_program(str(_progfx), "prog", [InjectedRow("C 5")], [])
    assert out.ok is False and "at least one ROM word" in out.warning
    out = rerun_with_program(str(_progfx), "prog",
                             [InjectedRow("C 5")], ["13", "93"])
    assert out.ok is False and "one row per word" in out.warning
    out = rerun_with_program(str(_progfx), "prog",
                             [InjectedRow("C 5")], ["nope"])
    assert out.ok is False and "hex" in out.warning


def test_rerun_with_program_needs_a_program_rom(tmp_path, monkeypatch):
    monkeypatch.setattr(_om, "find_digital_jar", lambda: "fake.jar")
    monkeypatch.setattr(_om, "per_row_run_auto", _fake_per_row())
    spec_name = extract_test_specs(parse_dig_file(_AND))[0].name
    out = rerun_with_program(_AND, spec_name, [InjectedRow("1 0 0")], ["13"])
    assert out.ok is False and "program memory" in (out.warning or "")
