"""
Tier-2.5 switch-level support.
"""

import glob

import pytest
from lxml import etree

from dlc.analyzer import check_all_l1_deep
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.pin_geometry import absolute_pin_positions
from dlc.web.component_kb import library_for_inventory

TLAB_DIR = "data/sample_circuits/tier2.5_transistor"
TLAB = sorted(glob.glob(f"{TLAB_DIR}/*.dig"))

SWITCH_IMAGES = {"nfet.png", "pfet.png", "pullup.png", "pulldown.png"}


def test_transistor_lab_folder_is_present():
    assert len(TLAB) >= 12, f"expected the 12 tier-2.5 labs, found {TLAB}"


@pytest.mark.parametrize("dig_path", TLAB)
def test_transistor_lab_produces_no_l1_issues(dig_path):
    c = parse_dig_file(dig_path)
    issues = check_all_l1_deep(c)
    assert issues.issues == [], (
        f"{dig_path}: unexpected issues: "
        f"{[(i.severity.value, i.kind, i.title) for i in issues.issues]}"
    )


def test_fet_pins_are_gate_plus_two_channels():
    c = parse_dig_file(f"{TLAB_DIR}/inverter_cmos.dig")
    fets = [x for x in c.components if x.element_name in ("PFET", "NFET")]
    assert len(fets) == 2
    for comp in fets:
        pins = absolute_pin_positions(comp)
        assert sorted(s.direction for _, s in pins) == ["bidir", "bidir", "in"]
        gate = next(p for p, s in pins if s.direction == "in")
        if comp.element_name == "PFET":
            assert (gate.x, gate.y) == (comp.position.x, comp.position.y)
        else:
            assert (gate.x, gate.y) == (comp.position.x, comp.position.y + 40)


def test_channels_and_pulls_count_as_possible_drivers_only():
    c = parse_dig_file(f"{TLAB_DIR}/nor2_nmos.dig")
    nl = build_netlist(c)
    out_net = next(
        n for n in nl.nets
        if any(p.element_name == "Out" for p in n.pins)
    )
    assert out_net.possible_drivers()
    assert len(out_net.drivers()) <= 1

def _drop_wires_at(root, x, y) -> int:
    wires = root.find("wires")
    removed = 0
    for w in list(wires.findall("wire")):
        for tag in ("p1", "p2"):
            p = w.find(tag)
            if int(p.get("x")) == x and int(p.get("y")) == y:
                wires.remove(w)
                removed += 1
                break
    return removed


def _find_element(root, name):
    for ve in root.iter("visualElement"):
        if ve.findtext("elementName") == name:
            pos = ve.find("pos")
            return int(pos.get("x")), int(pos.get("y"))
    raise AssertionError(f"no {name} in fixture")


def _issue_kinds(path) -> set[str]:
    return {i.kind for i in check_all_l1_deep(parse_dig_file(str(path))).issues}


def test_cut_gate_wire_is_a_dangling_input(tmp_path):
    t = etree.parse(f"{TLAB_DIR}/inverter_cmos.dig")
    x, y = _find_element(t.getroot(), "NFET")
    assert _drop_wires_at(t.getroot(), x, y + 40) >= 1
    p = tmp_path / "gate_cut.dig"
    t.write(str(p))
    assert "dangling_input" in _issue_kinds(p)


def test_cut_output_wire_is_an_undriven_output(tmp_path):
    t = etree.parse(f"{TLAB_DIR}/inverter_cmos.dig")
    x, y = _find_element(t.getroot(), "Out")
    assert _drop_wires_at(t.getroot(), x, y) >= 1
    p = tmp_path / "out_cut.dig"
    t.write(str(p))
    assert "unused_top_output" in _issue_kinds(p)


def test_unwired_fet_is_an_orphan(tmp_path):
    t = etree.parse(f"{TLAB_DIR}/inverter_cmos.dig")
    ves = t.getroot().find("visualElements")
    orphan = etree.SubElement(ves, "visualElement")
    etree.SubElement(orphan, "elementName").text = "PFET"
    etree.SubElement(orphan, "elementAttributes")
    etree.SubElement(orphan, "pos", x="2000", y="2000")
    p = tmp_path / "orphan.dig"
    t.write(str(p))
    assert "isolated_component" in _issue_kinds(p)

def test_text_notes_get_no_pins_and_no_library_card():
    c = parse_dig_file(f"{TLAB_DIR}/halfadder_mix.dig")
    assert any(x.element_name == "Text" for x in c.components)
    nl = build_netlist(c)
    assert not any(
        p.element_name == "Text" for n in nl.nets for p in n.pins
    )
    assert library_for_inventory({"Text": 3}) == []


# ---- Layer-2 encyclopedia ---------------------------------------------

def test_switch_level_components_have_real_library_entries():
    cards = library_for_inventory(
        {"NFET": 2, "PFET": 1, "PullUp": 1, "PullDown": 1}
    )
    assert {c["key"] for c in cards} == {"NFET", "PFET", "PullUp", "PullDown"}
    for card in cards:
        assert "No encyclopedia entry" not in card["description"]
        assert card["image"] in SWITCH_IMAGES


def test_compact_facts_carry_switch_level_semantics():
    from dlc.facts.extractor import extract_facts
    from dlc.llm.explain import _compact_facts
    facts = extract_facts(
        parse_dig_file(f"{TLAB_DIR}/nand2_pmos.dig")
    ).to_dict()
    compact = _compact_facts(facts)
    block = compact.get("switch_level")
    assert block and block["elements"].get("PFET") == 2
    assert "weak" in block["semantics"].lower()

@pytest.fixture()
def uploaded_transistor_session():
    from fastapi.testclient import TestClient
    from dlc.web.server import app
    client = TestClient(app)
    with open(f"{TLAB_DIR}/inverter_cmos.dig", "rb") as f:
        up = client.post(
            "/api/circuit",
            files={"files": ("inverter_cmos.dig", f, "application/xml")},
        )
    body = up.json()
    assert body["files"][0]["error"] is None
    # the upload's own L1 pass must be clean too
    assert body["files"][0]["issues"] == []
    return client, body["session_id"]


def test_mode_b_coverage_refuses_transistor_lab(uploaded_transistor_session):
    client, sid = uploaded_transistor_session
    out = client.post(
        "/api/l3/coverage",
        json={"session_id": sid, "filename": "inverter_cmos.dig"},
    ).json()
    assert out["ok"] is False and out["unsupported"] is True
    assert "transistor" in out["warning"].lower()


def test_mode_b_propose_refuses_transistor_lab(uploaded_transistor_session):
    client, sid = uploaded_transistor_session
    out = client.post(
        "/api/l3/propose",
        json={"session_id": sid, "filename": "inverter_cmos.dig"},
    ).json()
    assert out["ok"] is False and out["unsupported"] is True
    assert out["proposals"] == []


def test_mode_a_debug_refuses_transistor_lab(uploaded_transistor_session):
    client, sid = uploaded_transistor_session
    out = client.post(
        "/api/llm/debug",
        json={"session_id": sid, "filename": "inverter_cmos.dig"},
    ).json()
    assert out["ok"] is False and out["unsupported"] is True
    assert out["mode"] == "unsupported" and out["cards"] == []
    assert "transistor" in out["warning"].lower()


def test_gate_level_files_are_not_guarded():
    gate_files = sorted(glob.glob("data/sample_circuits/tier1_minimal/*.dig"))
    assert gate_files, "tier1 sample folder missing"
    from fastapi.testclient import TestClient
    from dlc.web.server import app
    client = TestClient(app)
    name = gate_files[0].rsplit("/", 1)[-1]
    with open(gate_files[0], "rb") as f:
        up = client.post(
            "/api/circuit", files={"files": (name, f, "application/xml")},
        )
    sid = up.json()["session_id"]
    out = client.post(
        "/api/l3/coverage", json={"session_id": sid, "filename": name},
    ).json()
    assert out.get("unsupported") is not True
