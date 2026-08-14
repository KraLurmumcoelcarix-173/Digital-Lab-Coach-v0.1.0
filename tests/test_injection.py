"""Gradescope-style injection (dlc/testing/inject.py).

Runs use the OFFICIAL test rows whenever a file's own testcase is
missing, header-only, or modified relative to the registered official
set; an EMPTY ROM is filled in the run copy from the course runtime
payload when one is registered for the filename. The payload is stored
opaquely (base64) and must never surface in any UI — students only see
a note that the course program was loaded. A ROM with any data, and the
student's real file, are never touched. All fixtures are synthetic.
"""

import json
import os
import xml.etree.ElementTree as ET

import pytest

from dlc.l3 import official_store
from dlc.testing.inject import (
    prepare_injected_run, cleanup_injected, file_test_status,
)


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


def _with_testcase(rows: str) -> str:
    return _EMPTY_CPU.replace(
        "<dataString>clk ReadData1 ReadData2\n</dataString>",
        f"<dataString>{rows}</dataString>",
    )


@pytest.fixture(autouse=True)
def _isolated_user_store(tmp_path, monkeypatch):
    # user layer empty -> only the shipped defaults answer lookups
    monkeypatch.setenv(
        "DLC_OFFICIAL_TESTS_PATH", str(tmp_path / "official_tests.json")
    )


def test_defaults_expose_cpu_testcase_and_hidden_runtime():
    assert official_store.get_content("cpu.dig")
    # the course program ships ONLY as an opaque runtime payload: no
    # plaintext program data anywhere, and no legacy accessor
    assert not hasattr(official_store, "get_rom_program")
    defaults_path = os.path.join(
        os.path.dirname(__file__), "..", "data",
        "official_tests_defaults.json",
    )
    assert "fec00213" not in open(defaults_path).read()
    rom = official_store.get_runtime_payload("cpu.dig", "rom")
    assert rom and rom.startswith("fec00213")
    # never registered for student-authored ROMs, and never listed
    assert official_store.get_runtime_payload("control-unit.dig", "rom") is None
    assert "fec00213" not in json.dumps(official_store.list_tests())
    assert "runtime" not in json.dumps(official_store.list_tests())


def test_status_missing_modified_official(tmp_path):
    from dlc.parser.dig_parser import parse_dig_file

    p = tmp_path / "cpu.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    assert file_test_status(parse_dig_file(str(p)), "cpu.dig") == "missing"

    p.write_text(_with_testcase("clk ReadData1 ReadData2\n0 0 0\n"),
                 encoding="utf-8")
    assert file_test_status(parse_dig_file(str(p)), "cpu.dig") == "modified"

    p.write_text(_with_testcase(official_store.get_content("cpu.dig")),
                 encoding="utf-8")
    assert file_test_status(parse_dig_file(str(p)), "cpu.dig") == "official"

    q = tmp_path / "mystery.dig"
    q.write_text(_EMPTY_CPU, encoding="utf-8")
    assert file_test_status(parse_dig_file(str(q)), "mystery.dig") is None


def test_injects_official_testcase_when_missing(tmp_path):
    p = tmp_path / "cpu.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    before = p.read_text(encoding="utf-8")

    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    try:
        assert temp and os.path.exists(temp)
        assert os.path.dirname(temp) == str(tmp_path)  # sibling: children resolve
        assert any("no test rows" in n for n in notes)

        root = ET.parse(temp).getroot()
        ds = [el.text or "" for el in root.iter("dataString")]
        assert any("ReadData1" in t and len(t.splitlines()) > 5 for t in ds)
        # the RUN COPY gets the course program in its empty ROM
        datas = [
            kids[1].text
            for ve in root.iter("visualElement")
            for e in ve.iter("entry")
            if len(kids := list(e)) == 2 and kids[0].text == "Data"
        ]
        assert any((d or "").startswith("fec00213") for d in datas)
        assert any("course program was loaded" in n for n in notes)
        # the student's file itself is untouched (ROM stays empty there)
        assert p.read_text(encoding="utf-8") == before
    finally:
        cleanup_injected(temp)
    assert temp and not os.path.exists(temp)


def test_injects_official_testcase_when_modified(tmp_path):
    p = tmp_path / "cpu.dig"
    p.write_text(_with_testcase("clk ReadData1 ReadData2\n0 0 0\nC 1 1\n"),
                 encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    try:
        assert temp and notes and "modified" in notes[0]
        root = ET.parse(temp).getroot()
        tcs = [ve for ve in root.iter("visualElement")
               if ve.findtext("elementName") == "Testcase"]
        assert len(tcs) == 1          # replaced, not appended
        ds = [el.text or "" for el in root.iter("dataString")]
        assert any("ReadData1" in t and len(t.splitlines()) > 5 for t in ds)
        assert not any("C 1 1" in t for t in ds)
    finally:
        cleanup_injected(temp)


def test_official_testcase_kept_but_empty_rom_still_filled(tmp_path):
    # testcase matches official -> kept verbatim; the empty ROM alone
    # still gets the course program in the run copy.
    p = tmp_path / "cpu.dig"
    p.write_text(_with_testcase(official_store.get_content("cpu.dig")),
                 encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    try:
        assert temp and len(notes) == 1
        assert "course program was loaded" in notes[0]
        root = ET.parse(temp).getroot()
        tcs = [ve for ve in root.iter("visualElement")
               if ve.findtext("elementName") == "Testcase"]
        assert len(tcs) == 1          # their (official-matching) testcase kept
    finally:
        cleanup_injected(temp)


def test_no_injection_when_tests_official_and_rom_programmed(tmp_path):
    src2 = _with_testcase(official_store.get_content("cpu.dig")).replace(
        "<entry><string>Label</string><string>Instruction Memory</string></entry>",
        "<entry><string>Label</string><string>Instruction Memory</string></entry>"
        "<entry><string>Data</string><data>1,2,3</data></entry>",
    )
    p = tmp_path / "cpu.dig"
    p.write_text(src2, encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    assert temp is None and notes == []


def test_no_injection_for_unregistered_filename(tmp_path):
    p = tmp_path / "mystery.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "mystery.dig")
    # mystery.dig has no official testcase; its empty ROM alone must not
    # trigger anything (ROMs are never injected).
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


def test_injected_testcase_is_labeled(tmp_path):
    # An unlabeled testcase prints as "unnamed" in Digital's CLI output
    # while our spec extractor names it "Testcase_<index>" — every
    # runner's name-matching then misses and per-row runs error out
    # (found live on a real control-unit upload). The injected element
    # must carry the canonical label, and the spec must pick it up.
    from dlc.testing.inject import INJECTED_TEST_LABEL
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs

    p = tmp_path / "cpu.dig"
    p.write_text(_EMPTY_CPU, encoding="utf-8")
    temp, notes = prepare_injected_run(str(p), "cpu.dig")
    try:
        assert temp and notes
        specs = extract_test_specs(parse_dig_file(temp))
        assert [s.name for s in specs] == [INJECTED_TEST_LABEL]
        assert specs[0].rows
    finally:
        cleanup_injected(temp)
