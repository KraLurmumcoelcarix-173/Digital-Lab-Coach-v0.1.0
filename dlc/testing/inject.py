"""Gradescope-style official-content injection for test runs.

The autograder always runs a submission against the OFFICIAL test vectors
and the OFFICIAL instruction program — a student who uploads a cpu with a
header-only testcase or an unprogrammed Instruction Memory still gets a
real grade. The coach mirrors that at test time:

  * testcase: when a file's own tests have no data rows (missing Testcase
    element, or header-only), and the official store has a testcase for
    that FILENAME, the run uses the official rows.
  * ROM program: when a ROM in the file has an empty Data attribute and
    the official store registers a rom_program for that FILENAME, the run
    fills that Data in. (Programs are registered only for files whose ROM
    content is instructor-GIVEN — e.g. cpu.dig's Instruction Memory.
    A child circuit's ROM that IS the student's work, like the
    control-unit decode table, is never injected.)

The student's file on disk is never modified: injection writes a sibling
temp copy (same directory, so child .dig references still resolve), the
jar runs on the copy, and the caller removes it afterwards.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET


def _official_testcase(filename: str) -> str | None:
    from dlc.l3 import official_store
    return official_store.get_content(filename)


def _official_rom_program(filename: str) -> dict | None:
    from dlc.l3 import official_store
    return official_store.get_rom_program(filename)


def _entry_map(element: ET.Element) -> dict[str, ET.Element]:
    """{key: value-element} for a Digital elementAttributes block."""
    out: dict[str, ET.Element] = {}
    for entry in element.findall("./elementAttributes/entry"):
        kids = list(entry)
        if len(kids) == 2 and kids[0].tag == "string" and kids[0].text:
            out[kids[0].text] = kids[1]
    return out


def _file_has_data_rows(circuit) -> bool:
    from dlc.testing.spec import extract_test_specs
    return any(s.rows for s in extract_test_specs(circuit))


def _set_testcase(root: ET.Element, content: str) -> bool:
    """Point the file's (first) Testcase at `content`; create the element
    if the file has none. Returns True when the XML changed."""
    ves = root.find("visualElements")
    if ves is None:
        return False
    target = None
    for ve in ves.findall("visualElement"):
        if ve.findtext("elementName") == "Testcase":
            target = ve
            break
    if target is None:
        target = ET.SubElement(ves, "visualElement")
        ET.SubElement(target, "elementName").text = "Testcase"
        ET.SubElement(target, "elementAttributes")
        pos = ET.SubElement(target, "pos")
        pos.set("x", "0")
        pos.set("y", "0")
    attrs = target.find("elementAttributes")
    if attrs is None:
        attrs = ET.SubElement(target, "elementAttributes")
    # drop any existing Testdata entry, then write ours
    for entry in list(attrs):
        kids = list(entry)
        if len(kids) == 2 and kids[0].text == "Testdata":
            attrs.remove(entry)
    entry = ET.SubElement(attrs, "entry")
    ET.SubElement(entry, "string").text = "Testdata"
    td = ET.SubElement(entry, "testData")
    ET.SubElement(td, "dataString").text = content
    return True


def _fill_empty_roms(root: ET.Element, program: dict) -> int:
    """Write program['data'] into every ROM whose Data is empty/missing.
    Returns how many ROMs were filled."""
    data = (program or {}).get("data") or ""
    if not data.strip():
        return 0
    filled = 0
    for ve in root.iter("visualElement"):
        if ve.findtext("elementName") != "ROM":
            continue
        entries = _entry_map(ve)
        cur = entries.get("Data")
        if cur is not None and (cur.text or "").strip():
            continue  # student programmed it — leave it alone
        attrs = ve.find("elementAttributes")
        if attrs is None:
            attrs = ET.SubElement(ve, "elementAttributes")
        if cur is None:
            entry = ET.SubElement(attrs, "entry")
            ET.SubElement(entry, "string").text = "Data"
            ET.SubElement(entry, "data").text = data
        else:
            cur.text = data
        filled += 1
    return filled


def prepare_injected_run(path: str, filename: str) -> tuple[str | None, list[str]]:
    """Return (temp_path, notes) when injection applies to this file, or
    (None, []) when the file runs as-is.

    temp_path is a sibling copy with the official testcase and/or ROM
    program filled in; the caller runs the jar on it and unlinks it in a
    finally block. Any parse hiccup returns (None, []) — injection must
    never break a run that would have worked without it.
    """
    notes: list[str] = []
    try:
        from dlc.parser.dig_parser import parse_dig_file
        circuit = parse_dig_file(path)
        tree = ET.parse(path)
        root = tree.getroot()
        changed = False

        official = _official_testcase(filename)
        if official and not _file_has_data_rows(circuit):
            if _set_testcase(root, official):
                changed = True
                notes.append(
                    "official testcase injected (this file's own testcase "
                    "has no data rows — Gradescope does the same)"
                )

        program = _official_rom_program(filename)
        if program:
            n = _fill_empty_roms(root, program)
            if n:
                changed = True
                plural = "s" if n != 1 else ""
                notes.append(
                    f"official instruction program injected into {n} "
                    f"empty ROM{plural} (Gradescope does the same)"
                )

        if not changed:
            return None, []
        d, base = os.path.split(path)
        temp_path = os.path.join(d, f".dlc_injected__{base}")
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        return temp_path, notes
    except Exception:
        return None, []


def cleanup_injected(temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        os.unlink(temp_path)
    except OSError:
        pass
