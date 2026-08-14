"""Gradescope-style injection (dlc/testing/inject.py) + official_store
accessors.
"""

import os
import xml.etree.ElementTree as ET

import pytest

from dlc.l3 import official_store
from dlc.testing.inject import prepare_injected_run, cleanup_injected


# A minimal cpu-shaped file: one ROM with NO Data, one header-only
# testcase. The wiring is irrelevant to injection (no jar run here).
_EMPTY_CPU = """<?xml version="1.0" encoding="utf-8"?>
<circuit>
  <version>2</version>
  <attributes/>
  <visualElements>
    <visualElement>
      <elementName>In</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>clk</string></entry>
      </elementAttributes>
      <pos x="0" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>ROM</elementName>
      <elementAttributes>
        <entry><string>AddrBits</string><int>10</int></entry>
        <entry><string>Bits</string><int>32</int></entry>
        <entry><string>Label</string><string>Instruction Memory</string></entry>
      </elementAttributes>
      <pos x="200" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>Testcase</elementName>
      <elementAttributes>
        <entry><string>Label</string><string>cpu</string></entry>
        <entry><string>Testdata</string><testData><dataString>clk ReadData1 ReadData2
</dataString></testData></entry>
      </elementAttributes>
      <pos x="0" y="200"/>
    </visualElement>
  </visualElements>
  <wires/>
</circuit>
"""


@pytest.fixture(autouse=True)
def _isolated_user_store(tmp_path, monkeypatch):
    # user layer empty -> only the shipped defaults answer lookups
    monkeypatch.setenv(
        "DLC_OFFICIAL_TESTS_PATH", str(tmp_path / "official_tests.json")
    )


def test_defaults_expose_cpu_testcase_and_rom_program():
    assert official_store.get_content("cpu.dig")
    prog = official_store.get_rom_program("cpu.dig")
    assert prog and prog["data"].startswith("fec00213")


def test_no_rom_program_for_student_authored_roms():
    # the control-unit decode table is the student's WORK — registering a
    # program for it would hand out the answer.
    assert official_store.get_rom_program("control-unit.dig") is None


def test_injects_testcase_and_rom_into_empty_cpu(tmp_path):
    p = tmp_path / "cpu.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    before = p.read_text(encoding="utf-8")

    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    try:
        assert temp and os.path.exists(temp)
        assert os.path.dirname(temp) == str(tmp_path)  # sibling: children resolve
        assert len(notes) == 2

        root = ET.parse(temp).getroot()
        datas = [
            kids[1].text
            for ve in root.iter("visualElement")
            for e in ve.iter("entry")
            if len(kids := list(e)) == 2 and kids[0].text == "Data"
        ]
        assert any((d or "").startswith("fec00213") for d in datas)
        ds = [el.text for el in root.iter("dataString")]
        assert any("ReadData1" in (t or "") and len((t or "").splitlines()) > 5
                   for t in ds)
        # the student's file itself is untouched
        assert p.read_text(encoding="utf-8") == before
    finally:
        cleanup_injected(temp)
    assert temp and not os.path.exists(temp)


def test_no_injection_when_file_has_real_tests_and_data(tmp_path):
    src = _EMPTY_CPU.replace(
        "<dataString>clk ReadData1 ReadData2\n</dataString>",
        "<dataString>clk ReadData1 ReadData2\n0 0 0\n</dataString>",
    )
    src = src.replace(
        "<entry><string>Label</string><string>Instruction Memory</string></entry>",
        "<entry><string>Label</string><string>Instruction Memory</string></entry>"
        "<entry><string>Data</string><data>1,2,3</data></entry>",
    )
    p = tmp_path / "cpu.dig"
    p.write_text(src, encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    assert temp is None and notes == []


def test_no_injection_for_unregistered_filename(tmp_path):
    p = tmp_path / "mystery.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "mystery.dig")
    # mystery.dig has no official testcase; its empty ROM alone must not
    # trigger anything (no program is registered for it either).
    assert temp is None and notes == []


def test_empty_rom_is_warning_and_never_blocks(tmp_path):
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.analyzer import check_all_l1_deep
    from dlc.web.server import _l1_error_block

    p = tmp_path / "cpu.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    c = parse_dig_file(str(p))
    issues = check_all_l1_deep(c)
    kinds = {(i.kind, i.severity.value) for i in issues.issues}
    assert ("empty_rom", "warning") in kinds
    assert not [i for i in issues.issues if i.severity.value == "error"]
    assert _l1_error_block(c) is None
