"""Gradescope-style official-testcase injection for test runs.

The autograder always runs a submission against the OFFICIAL test vectors:
whatever a student's own Testcase element holds — missing, header-only, or
edited — the grade comes from the instructor's rows. The coach mirrors
that: when a filename has an official test set registered
(data/official_tests_defaults.json, or a Settings entry) and the file's
own testcase does not MATCH it (normalized-content hash), test runs use
the official rows instead. A file whose testcase already matches runs
untouched.

EMPTY ROMs: when the course registers a runtime payload for the filename
(official_store.get_runtime_payload — an opaque, base64-stored blob that
no UI ever renders), an empty ROM's Data is filled in the RUN COPY so
the run grades the student's logic instead of an unprogrammed memory.
The data itself is never shown to the student — only a note that the
course program was loaded. A ROM that already has ANY data is never
touched (a wrong decode table stays the student's problem — Layer 3's
teaching material), the student's real file keeps its empty ROM, the
Layer-1 empty_rom WARNING still shows, and Layer 3 coaches the real
file. Payloads are registered ONLY for instructor-given ROM content
(cpu.dig's Instruction Memory) — never for ROMs that are themselves the
deliverable (control-unit decode table).

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


# The injected Testcase element's Label. It MUST be set: an unlabeled
# testcase prints as "unnamed" in Digital's CLI output while our spec
# extractor names it "Testcase_<index>", so every runner's name-matching
# misses and per-row runs error out with "no matching result line"
# (found live on a real control-unit upload, r32).
INJECTED_TEST_LABEL = "official"


def _replace_testcases(root: ET.Element, content: str) -> bool:
    """Drop every Testcase element and add ONE labeled holding `content` —
    the run should exercise exactly the official rows, like the grader
    does. Returns True when the XML changed."""
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
    lbl = ET.SubElement(attrs, "entry")
    ET.SubElement(lbl, "string").text = "Label"
    ET.SubElement(lbl, "string").text = INJECTED_TEST_LABEL
    entry = ET.SubElement(attrs, "entry")
    ET.SubElement(entry, "string").text = "Testdata"
    td = ET.SubElement(entry, "testData")
    ET.SubElement(td, "dataString").text = content
    pos = ET.SubElement(target, "pos")
    pos.set("x", str(pos_x))
    pos.set("y", str(pos_y))
    return True


def _fill_empty_roms(root: ET.Element, data: str) -> int:
    """Write `data` into every ROM whose Data is empty/missing (a ROM the
    student DID program is never touched). Returns how many were filled."""
    if not (data or "").strip():
        return 0
    filled = 0
    for ve in root.iter("visualElement"):
        if ve.findtext("elementName") != "ROM":
            continue
        entries = _entry_map(ve)
        cur = entries.get("Data")
        if cur is not None and (cur.text or "").strip():
            continue
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


def _entry_map(element: ET.Element) -> dict[str, ET.Element]:
    """{key: value-element} for a Digital elementAttributes block."""
    out: dict[str, ET.Element] = {}
    for entry in element.findall("./elementAttributes/entry"):
        kids = list(entry)
        if len(kids) == 2 and kids[0].tag == "string" and kids[0].text:
            out[kids[0].text] = kids[1]
    return out


def prepare_injected_run(path: str, filename: str) -> tuple[str | None, list[str]]:
    """Return (temp_path, notes) when injection applies to this file, or
    (None, []) when the file runs as-is.

    temp_path is a sibling copy where (a) the testcase is replaced by the
    official rows when the file's own testcase is missing or modified,
    and (b) empty ROMs are filled from the course runtime payload when
    one is registered for this filename. The caller runs the jar on it
    and unlinks it in a finally block. Any hiccup returns (None, []) —
    injection must never break a run that would have worked without it.
    """
    try:
        from dlc.parser.dig_parser import parse_dig_file
        from dlc.l3.official_store import get_runtime_payload

        circuit = parse_dig_file(path)
        status = file_test_status(circuit, filename)
        tree = ET.parse(path)
        root = tree.getroot()
        changed = False
        notes: list[str] = []

        if status in ("missing", "modified"):
            official = _official_testcase(filename)
            if official and _replace_testcases(root, official):
                changed = True
                if status == "missing":
                    notes.append(
                        "official testcase injected (this file has no "
                        "test rows — Gradescope grades with the official "
                        "tests)")
                else:
                    notes.append(
                        "official testcase injected in place of this "
                        "file's modified testcase (Gradescope grades "
                        "with the official tests)")

        rom_data = get_runtime_payload(filename, "rom")
        if rom_data:
            n = _fill_empty_roms(root, rom_data)
            if n:
                changed = True
                plural = "s" if n != 1 else ""
                # "course program was loaded" is a MARKER: the Mode A
                # route detects rom injection off this substring
                # (l3_routes._rom_injected_notes) — keep it if rewording.
                notes.append(
                    f"the course program was loaded into {n} empty "
                    f"ROM{plural} for this run so your logic could be "
                    f"tested — your file still has the ROM unprogrammed; "
                    f"fill it in before submitting")

        if not changed:
            return None, []
        d, base = os.path.split(path)
        temp_path = os.path.join(d, f".dlc_injected__{base}")
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        return temp_path, notes
    except Exception:
        return None, []


def inject_official_tests_in_place(path: str, filename: str) -> list[str]:
    """Apply testcase injection (only — never ROM) directly to `path`,
    which must be a TOOL-OWNED scratch copy (an L3 coach temp), never a
    student's real file. Returns the notes, [] when nothing applied.

    Layer 3's fix-retest loop rebuilds a temp from the student's base
    file: without this, an empty/modified testcase makes the retest see
    zero (or non-official) rows and report "all fixed" on nothing."""
    try:
        from dlc.parser.dig_parser import parse_dig_file
        circuit = parse_dig_file(path)
        status = file_test_status(circuit, filename)
        if status not in ("missing", "modified"):
            return []
        official = _official_testcase(filename)
        if not official:
            return []
        tree = ET.parse(path)
        if not _replace_testcases(tree.getroot(), official):
            return []
        tree.write(path, encoding="utf-8", xml_declaration=True)
        if status == "missing":
            return ["official testcase injected (this file has no test "
                    "rows — Gradescope grades with the official tests)"]
        return ["official testcase injected in place of the modified "
                "testcase (Gradescope grades with the official tests)"]
    except Exception:
        return []


def cleanup_injected(temp_path: str | None) -> None:
    if not temp_path:
        return
    try:
        os.unlink(temp_path)
    except OSError:
        pass
