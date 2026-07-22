"""official-test store (dlc/l3/official_store.py) + its settings
endpoints + how Mode B consumes it: instructor-controlled truth that works
for ANY lab, manifest or not."""

import pytest
from fastapi.testclient import TestClient

from dlc.l3 import manifest as mf
from dlc.l3 import official_store as ost
from dlc.web import server
from dlc.web.server import app

client = TestClient(app)

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"


def test_store_crud_and_normalized_matching(monkeypatch, tmp_path):
    monkeypatch.setenv("DLC_OFFICIAL_DEFAULTS_PATH",
                       str(tmp_path / "no_defaults.json"))
    assert ost.list_tests() == []
    ost.save_test("cpu.dig", "A B Y\n0 0 0\n1 1 1")
    tests = ost.list_tests()
    assert [t["filename"] for t in tests] == ["cpu.dig"]
    assert tests[0]["source"] == "user"
    # cosmetic edits still match; a changed row does not
    assert ost.status_for("cpu.dig", "A  B Y  # hi\n\n0 0 0\n1 1 1") == "official"
    assert ost.status_for("cpu.dig", "A B Y\n0 0 1\n1 1 1") == "modified"
    assert ost.status_for("other.dig", "A B Y\n0 0 0") is None
    # update overwrites, delete removes
    ost.save_test("cpu.dig", "A B Y\n1 0 0")
    assert ost.status_for("cpu.dig", "A B Y\n1 0 0") == "official"
    assert ost.delete_test("cpu.dig") is True
    assert ost.delete_test("cpu.dig") is False
    assert ost.status_for("cpu.dig", "A B Y\n1 0 0") is None


def test_store_refuses_empty_and_survives_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_OFFICIAL_DEFAULTS_PATH",
                       str(tmp_path / "no_defaults.json"))
    with pytest.raises(ValueError):
        ost.save_test("", "rows")
    with pytest.raises(ValueError):
        ost.save_test("x.dig", "   ")
    p = tmp_path / "official_tests.json"
    monkeypatch.setenv("DLC_OFFICIAL_TESTS_PATH", str(p))
    p.write_text("{not json")
    assert ost.list_tests() == []            # corrupt store never breaks scans


def test_official_status_prefers_store_over_manifest():
    # a filename with no shipped default, so the layering under test is
    # purely user-store vs manifest
    manifest = {"official_tests": {
        "widget.dig": mf.normalized_test_hash("A Y\n0 0")}}
    # manifest alone: official
    assert mf.official_status(manifest, "widget.dig", "A Y\n0 0") == "official"
    # the store disagrees => the store wins
    ost.save_test("widget.dig", "A Y\n1 1")
    assert mf.official_status(manifest, "widget.dig", "A Y\n0 0") == "modified"
    assert mf.official_status(manifest, "widget.dig", "A Y\n1 1") == "official"
    # store works with NO manifest at all
    assert mf.official_status(None, "widget.dig", "A Y\n1 1") == "official"


def test_scan_classifies_official_from_store_without_manifest(monkeypatch, tmp_path):
    """A manifest-free tree still gets official classification when the
    instructor registered the file's testcase in Settings."""
    from dlc.l3.coverage import scan_tree_coverage
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs
    monkeypatch.setenv("DLC_MANIFEST_DIR", str(tmp_path / "no_manifests"))
    spec = extract_test_specs(parse_dig_file(_AND))[0]
    ost.save_test("single_and.dig", spec.raw_data_string)
    report = scan_tree_coverage(_AND)
    cov = report.circuits[0]
    assert cov.official_test == "official"


def test_official_tests_endpoints_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("DLC_OFFICIAL_DEFAULTS_PATH",
                       str(tmp_path / "no_defaults.json"))
    r = client.post("/api/config/official_tests", json={
        "filename": "alu.dig", "content": "A B Out\n1 2 3"})
    assert r.status_code == 200 and r.json()["ok"] is True
    body = client.get("/api/config/official_tests").json()
    assert [t["filename"] for t in body["tests"]] == ["alu.dig"]
    assert body["tests"][0]["content"] == "A B Out\n1 2 3"
    r = client.post("/api/config/official_tests", json={
        "filename": "", "content": "x"})
    assert r.status_code == 400
    r = client.delete("/api/config/official_tests?filename=alu.dig")
    assert r.json()["removed"] is True
    assert client.get("/api/config/official_tests").json()["tests"] == []


# ---------------------------------------------------------------------------
# shipped defaults — always present, overridable, never deletable
# ---------------------------------------------------------------------------

def test_shipped_defaults_match_the_manifest_fingerprints():
    import json
    from pathlib import Path
    defaults = json.loads(Path("data/official_tests_defaults.json")
                          .read_text(encoding="utf-8"))
    lab5 = json.loads(Path("data/manifests/lab5.json")
                      .read_text(encoding="utf-8"))["official_tests"]
    for name in ("cpu.dig", "register-file.dig"):
        e = defaults[name]
        assert mf.normalized_test_hash(e["content"]) == e["sha1"] == lab5[name]


def test_defaults_layer_override_and_revert():
    names = {t["filename"]: t["source"] for t in ost.list_tests()}
    assert names["cpu.dig"] == "default"
    assert names["register-file.dig"] == "default"
    # default answers status_for with no user entry at all
    import json
    from pathlib import Path
    content = json.loads(Path("data/official_tests_defaults.json")
                         .read_text(encoding="utf-8"))["cpu.dig"]["content"]
    assert ost.status_for("cpu.dig", content) == "official"
    assert ost.status_for("cpu.dig", content + "\n9 9 9") == "modified"
    # a user save overrides the default...
    ost.save_test("cpu.dig", "clk R1 R2\nC 1 2")
    assert ost.status_for("cpu.dig", "clk R1 R2\nC 1 2") == "official"
    assert {t["filename"]: t["source"] for t in ost.list_tests()}[
        "cpu.dig"] == "override"
    # ...and deleting the override reverts to the shipped default
    assert ost.delete_test("cpu.dig") is True
    assert ost.status_for("cpu.dig", content) == "official"
    assert {t["filename"]: t["source"] for t in ost.list_tests()}[
        "cpu.dig"] == "default"
    # the default itself can never be deleted
    assert ost.delete_test("cpu.dig") is False
    assert "cpu.dig" in {t["filename"] for t in ost.list_tests()}
