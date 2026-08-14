"""Gradescope-style official-testcase injection for test runs.

The autograder always runs a submission against the OFFICIAL test vectors:
whatever a student's own Testcase element holds — missing, header-only, or
edited — the grade comes from the instructor's rows. The coach mirrors
that: when a filename has an official test set registered
(data/official_tests_defaults.json, or a Settings entry) and the file's
own testcase does not MATCH it (normalized-content hash), test runs use
the official rows instead. A file whose testcase already matches runs
untouched.

ROM contents are deliberately NOT injected. A wrong or empty ROM is the
student's own work (e.g. the control-unit decode table, the cpu's
instruction memory) — Layer 3 exists to teach them what's wrong with it,
and official ROM data must never appear in the tool. An empty ROM is a
Layer-1 WARNING that blocks nothing.

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


def file_test_status(circuit, filename: str) -> str | None:
    """How this file's own tests relate to the official set for `filename`.

    Returns None when no official test set is registered for the filename;
    otherwise 'official' (some testcase hash-matches the official rows),
    'modified' (it has test rows, but none match), or 'missing' (no
    testcase with data rows at all).
    """
    from dlc.l3 import official_store
    from dlc.testing.spec import extract_test_specs

    if official_store.get_content(filename) is None:
        return None
    for comp in circuit.components:
        if comp.element_name != "Testcase":
            continue
        raw = comp.attributes.get("Testdata", "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        if official_store.status_for(filename, raw) == "official":
            return "official"
    specs = [s for s in extract_test_specs(circuit) if s.rows]
    return "modified" if specs else "missing"


def _replace_testcases(root: ET.Element, content: str) -> bool:
    """Drop every Testcase element and add ONE holding `content` — the
    run should exercise exactly the official rows, like the grader does.
    Returns True when the XML changed."""
    ves = root.find("visualElements")
    if ves is None:
        return False
    pos_x, pos_y = 0, 0
    for ve in list(ves.findall("visualElement")):
        if ve.findtext("elementName") == "Testcase":
            pos = ve.find("pos")
            if pos is not None:
                pos_x = pos.get("x", "0")
                pos_y = pos.get("y", "0")
            ves.remove(ve)
    target = ET.SubElement(ves, "visualElement")
    ET.SubElement(target, "elementName").text = "Testcase"
    attrs = ET.SubElement(target, "elementAttributes")
    entry = ET.SubElement(attrs, "entry")
    ET.SubElement(entry, "string").text = "Testdata"
    td = ET.SubElement(entry, "testData")
    ET.SubElement(td, "dataString").text = content
    pos = ET.SubElement(target, "pos")
    pos.set("x", str(pos_x))
    pos.set("y", str(pos_y))
    return True


def prepare_injected_run(path: str, filename: str) -> tuple[str | None, list[str]]:
    """Return (temp_path, notes) when injection applies to this file, or
    (None, []) when the file runs as-is.

    temp_path is a sibling copy whose testcases are replaced by the
    official set; the caller runs the jar on it and unlinks it in a
    finally block. Any hiccup returns (None, []) — injection must never
    break a run that would have worked without it.
    """
    try:
        from dlc.parser.dig_parser import parse_dig_file
        circuit = parse_dig_file(path)
        status = file_test_status(circuit, filename)
        if status in (None, "official"):
            return None, []
        official = _official_testcase(filename)
        if not official:
            return None, []
        tree = ET.parse(path)
        if not _replace_testcases(tree.getroot(), official):
            return None, []
        if status == "missing":
            note = ("official testcase injected (this file has no test "
                    "rows — Gradescope grades with the official tests)")
        else:
            note = ("official testcase injected in place of this file's "
                    "modified testcase (Gradescope grades with the "
                    "official tests)")
        d, base = os.path.split(path)
        temp_path = os.path.join(d, f".dlc_injected__{base}")
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        return temp_path, [note]
    except Exception:
        return None, []


def cleanup_injected(temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        os.unlink(temp_path)
    except OSError:
        pass
