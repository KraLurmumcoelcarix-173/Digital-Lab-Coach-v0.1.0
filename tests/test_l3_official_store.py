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
    assert ost.status_for("cpu.dig", "A  B Y  # hi\n\n0 0 0\n1 1 1") == "official"
    assert ost.status_for("cpu.dig", "A B Y\n0 0 1\n1 1 1") == "modified"
    assert ost.status_for("other.dig", "A B Y\n0 0 0") is None
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
    assert ost.list_tests() == []


def test_official_status_prefers_store_over_manifest():
    manifest = {"official_tests": {
        "widget.dig": mf.normalized_test_hash("A Y\n0 0")}}
    assert mf.official_status(manifest, "widget.dig", "A Y\n0 0") == "official"
    ost.save_test("widget.dig", "A Y\n1 1")
    assert mf.official_status(manifest, "widget.dig", "A Y\n0 0") == "modified"
    assert mf.official_status(manifest, "widget.dig", "A Y\n1 1") == "official"
    assert mf.official_status(None, "widget.dig", "A Y\n1 1") == "official"


def test_scan_classifies_official_from_store_without_manifest(monkeypatch, tmp_path):
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

def test_shipped_defaults_match_the_manifest_fingerprints():
    import json
    from pathlib import Path
    defaults = json.loads(Path("data/official_tests_defaults.json")
                          .read_text(encoding="utf-8"))
    lab5 = json.loads(Path("data/manifests/cpu.json")
                      .read_text(encoding="utf-8"))["official_tests"]
    for name in ("cpu.dig", "register-file.dig"):
        e = defaults[name]
        assert mf.normalized_test_hash(e["content"]) == e["sha1"] == lab5[name]


def test_defaults_layer_override_and_revert():
    names = {t["filename"]: t["source"] for t in ost.list_tests()}
    assert names["cpu.dig"] == "default"
    assert names["register-file.dig"] == "default"
    import json
    from pathlib import Path
    content = json.loads(Path("data/official_tests_defaults.json")
                         .read_text(encoding="utf-8"))["cpu.dig"]["content"]
    assert ost.status_for("cpu.dig", content) == "official"
    assert ost.status_for("cpu.dig", content + "\n9 9 9") == "modified"
    ost.save_test("cpu.dig", "clk R1 R2\nC 1 2", allow_default_override=True)
    assert ost.status_for("cpu.dig", "clk R1 R2\nC 1 2") == "official"
    assert {t["filename"]: t["source"] for t in ost.list_tests()}[
        "cpu.dig"] == "override"
    assert ost.delete_test("cpu.dig") is True
    assert ost.status_for("cpu.dig", content) == "official"
    assert {t["filename"]: t["source"] for t in ost.list_tests()}[
        "cpu.dig"] == "default"
    assert ost.delete_test("cpu.dig") is False
    assert "cpu.dig" in {t["filename"] for t in ost.list_tests()}

def test_defaults_cannot_be_hand_edited_but_adopt_can():
    with pytest.raises(ValueError, match="built-in default"):
        ost.save_test("cpu.dig", "clk R1 R2\nC 1 2")
    r = client.post("/api/config/official_tests", json={
        "filename": "cpu.dig", "content": "clk R1 R2\nC 1 2"})
    assert r.status_code == 400
    assert "built-in default" in r.json()["detail"]
    saved = ost.save_test("cpu.dig", "clk R1 R2\nC 1 2",
                          allow_default_override=True)
    assert saved["sha1"]
    assert {t["filename"]: t["source"] for t in ost.list_tests()}[
        "cpu.dig"] == "override"


def test_content_must_be_digital_test_format():
    with pytest.raises(ValueError, match="Digital test format"):
        ost.save_test("my.dig", "hello world\nthis is not a test")
    with pytest.raises(ValueError, match="first line must be the header"):
        ost.save_test("my.dig", "0 1 0\n1 1 1")
    with pytest.raises(ValueError, match="no test rows"):
        ost.save_test("my.dig", "A B Y")
    with pytest.raises(ValueError, match="columns"):
        ost.save_test("my.dig", "A B Y\n1 0")
    with pytest.raises(ValueError, match=".dig"):
        ost.save_test("cpu.txt", "A B\n1 0")
    ost.save_test("my.dig", "A B Y  # header\n0 0 0\nC X Z\n(-3) 0x1F 0b10")
    ost.save_test("my2.dig", "A B\nrepeat(3) 1 0\nloop(i,4) (i) (i*2)\n1 1")
    assert {t["filename"] for t in ost.list_tests()} >= {"my.dig", "my2.dig"}
    r = client.post("/api/config/official_tests", json={
        "filename": "my.dig", "content": "garbage !!"})
    assert r.status_code == 400
    assert "Digital test format" in r.json()["detail"]


def _upload(*paths):
    files, handles = [], []
    for p in paths:
        fh = open(p, "rb")
        handles.append(fh)
        files.append(("files", (p.split("/")[-1], fh, "application/xml")))
    try:
        r = client.post("/api/circuit", files=files)
    finally:
        for fh in handles:
            fh.close()
    assert r.status_code == 200
    return r.json()["session_id"]


def test_adopt_official_requires_a_verified_temp(monkeypatch, tmp_path):
    monkeypatch.setenv("DLC_LIMITS_PATH", str(tmp_path / "limits.json"))
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    sid = _upload(_AND)
    try:
        r = client.post("/api/l3/adopt_official", json={
            "session_id": sid, "filename": "single_and.dig"})
        body = r.json()
        assert body["ok"] is False and "Mode B" in body["warning"]
    finally:
        server._SESSIONS.pop(sid, None)


def test_adopt_official_merges_the_temp_spec(monkeypatch, tmp_path):
    import shutil
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs
    from dlc.l3.oracle import InjectedRow, write_temp_with_rows
    monkeypatch.setenv("DLC_LIMITS_PATH", str(tmp_path / "limits.json"))
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    sid = _upload(_AND)
    try:
        session = server._SESSIONS[sid]
        src = next(f["path"] for f in session["files"]
                   if f["name"] == "single_and.dig")
        spec_name = extract_test_specs(parse_dig_file(src))[0].name
        temp_path, _spec = write_temp_with_rows(
            src, spec_name, [InjectedRow("1 0 0")])
        session["files"].append(
            {"name": "single_and__coach.dig", "path": temp_path})
        r = client.post("/api/l3/adopt_official", json={
            "session_id": sid, "filename": "single_and.dig"})
        body = r.json()
        assert body["ok"] is True and body["rows"] == 5
        merged = extract_test_specs(parse_dig_file(temp_path))[0]
        assert ost.status_for("single_and.dig",
                              merged.raw_data_string) == "official"
        orig = extract_test_specs(parse_dig_file(src))[0]
        assert ost.status_for("single_and.dig",
                              orig.raw_data_string) == "modified"
    finally:
        server._SESSIONS.pop(sid, None)


def test_configured_labs_endpoint_unions_manifests_and_store():
    body = client.get("/api/l3/configured").json()
    files = set(body["files"])
    assert {"cpu.dig", "alu.dig", "register-file.dig",
            "bidirectional-shifter.dig", "tier3_latched_display.dig"} <= files
