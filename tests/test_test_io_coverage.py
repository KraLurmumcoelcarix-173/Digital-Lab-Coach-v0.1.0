"""
Purple advisory cards: top-level I/O pins the tests never touch.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from dlc.analyzer.test_io_coverage import KIND, check_test_io_coverage
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs
from dlc.web.server import app


def _xml_circuit(elements: str, wires: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<circuit>\n'
        "  <version>2</version>\n  <attributes/>\n"
        f"  <visualElements>\n{elements}  </visualElements>\n"
        f"  <wires>\n{wires}  </wires>\n</circuit>\n"
    )


def _ve(name, x, y, entries="") -> str:
    attrs = f"<elementAttributes>{entries}</elementAttributes>" if entries \
        else "<elementAttributes/>"
    return (f"    <visualElement><elementName>{name}</elementName>"
            f"{attrs}<pos x=\"{x}\" y=\"{y}\"/></visualElement>\n")


def _entry(k, v, tag="string") -> str:
    return f"<entry><string>{k}</string><{tag}>{v}</{tag}></entry>"


def _label(v) -> str:
    return _entry("Label", v)


def _testcase(data: str) -> str:
    return _ve("Testcase", 0, 200,
               _label("t") + _entry(
                   "Testdata",
                   f"<dataString>{data}</dataString>", tag="testData"))


def _wire(x1, y1, x2, y2) -> str:
    return (f"    <wire><p1 x=\"{x1}\" y=\"{y1}\"/>"
            f"<p2 x=\"{x2}\" y=\"{y2}\"/></wire>\n")


def _write(tmp_path: Path, xml: str, name: str = "purple.dig") -> str:
    p = tmp_path / name
    p.write_text(xml, encoding="utf-8")
    return str(p)


def _cards(dig_path: str):
    circuit = parse_dig_file(dig_path)
    return check_test_io_coverage(circuit, extract_test_specs(circuit))


_PARTIAL = (
    _ve("In", 0, 0, _label("a"))
    + _ve("In", 0, 80, _label("b"))
    + _ve("Out", 100, 0, _label("f"))
    + _ve("Out", 300, 300)
    + _testcase("a f\n0 0\n1 1")
)


def test_untested_pins_earn_one_purple_warning(tmp_path):
    cards = _cards(_write(tmp_path, _xml_circuit(
        _PARTIAL, _wire(0, 0, 100, 0))))
    assert len(cards) == 1
    card = cards[0]
    assert card.kind == KIND
    assert card.severity.value == "warning"
    assert "b" in card.message
    assert "(unlabeled Out at (300, 300))" in card.message
    assert "redundant" in card.message and "incomplete" in card.message
    assert card.component_indices == [1, 3]


def test_fully_covered_circuit_stays_silent(tmp_path):
    xml = _xml_circuit(
        _ve("In", 0, 0, _label("a"))
        + _ve("Out", 100, 0, _label("f"))
        + _testcase("a f\n0 0"),
        _wire(0, 0, 100, 0))
    assert _cards(_write(tmp_path, xml)) == []


def test_no_testcase_means_no_card(tmp_path):
    xml = _xml_circuit(
        _ve("In", 0, 0, _label("a")) + _ve("Out", 100, 0, _label("f")),
        _wire(0, 0, 100, 0))
    assert _cards(_write(tmp_path, xml)) == []


def test_fully_unbound_testcase_defers_to_rename_guidance(tmp_path):
    xml = _xml_circuit(
        _ve("In", 0, 0, _label("a"))
        + _ve("Out", 100, 0, _label("f"))
        + _testcase("x y\n0 0"),
        _wire(0, 0, 100, 0))
    assert _cards(_write(tmp_path, xml)) == []


def test_upload_endpoint_ships_the_purple_card(tmp_path):
    xml = _xml_circuit(_PARTIAL, _wire(0, 0, 100, 0))
    client = TestClient(app)
    resp = client.post("/api/circuit", files=[
        ("files", ("purple.dig", xml.encode("utf-8"),
                   "application/octet-stream")),
    ])
    assert resp.status_code == 200
    issues = resp.json()["files"][0]["issues"]
    purple = [i for i in issues if i["kind"] == KIND]
    assert len(purple) == 1
    assert purple[0]["severity"] == "warning"
    assert "b" in purple[0]["message"]
