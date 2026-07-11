"""POST /api/l3/coverage (dlc/web/l3_routes.py): Mode B's endpoint over the
tree-wide wrong-test scan + coverage report. Evaluator-only — no jar.
"""

import pytest
from fastapi.testclient import TestClient

from dlc.web import server
from dlc.web.server import app

client = TestClient(app)

_CALC_DIR = "data/sample_circuits/30_bug_benchmark/bug1_meaningless_mux_in3"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    # /api/circuit runs the GC sweep + telemetry lives next door; keep every
    # test pointed at a throwaway sink, never the developer's ~/.dlc.
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))


def _upload(*paths):
    files = []
    handles = []
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


def test_coverage_endpoint_scans_the_whole_tree():
    sid = _upload(f"{_CALC_DIR}/tier3_calculator.dig", f"{_CALC_DIR}/bool_unit.dig")
    try:
        r = client.post("/api/l3/coverage", json={
            "session_id": sid, "filename": "tier3_calculator.dig",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["root"] == "tier3_calculator.dig"
        assert [c["file"] for c in body["circuits"]] == [
            "tier3_calculator.dig", "bool_unit.dig",
        ]
        assert body["total_flags"] > 0            # bug1's seeded mismatch
        flags = body["circuits"][0]["flags"]
        assert {f["row_index"] for f in flags} == {6, 11}
        child = body["circuits"][1]
        assert child["has_testcases"] is False
        assert any("no embedded testcase" in n for n in child["notes"])
    finally:
        server._SESSIONS.pop(sid, None)


def test_coverage_endpoint_clean_circuit_reports_no_flags():
    sid = _upload("data/sample_circuits/tier1_minimal/single_and.dig")
    try:
        r = client.post("/api/l3/coverage", json={
            "session_id": sid, "filename": "single_and.dig",
        })
        body = r.json()
        assert body["ok"] is True and body["total_flags"] == 0
        c = body["circuits"][0]
        assert c["input_space_pct"] == 100.0
        assert c["specs"][0]["checked_cells"] == 4
    finally:
        server._SESSIONS.pop(sid, None)


def test_coverage_endpoint_notes_unresolved_children_instead_of_failing():
    # Parent uploaded WITHOUT its child: the missing subcircuit becomes a
    # report note; the endpoint itself still succeeds.
    sid = _upload(f"{_CALC_DIR}/tier3_calculator.dig")
    try:
        r = client.post("/api/l3/coverage", json={
            "session_id": sid, "filename": "tier3_calculator.dig",
        })
        body = r.json()
        assert body["ok"] is True
        assert [c["file"] for c in body["circuits"]] == ["tier3_calculator.dig"]
        assert any("bool_unit.dig" in n and "not scanned" in n
                   for n in body["notes"])
    finally:
        server._SESSIONS.pop(sid, None)


def test_coverage_endpoint_404s_like_every_other_endpoint():
    r = client.post("/api/l3/coverage", json={
        "session_id": "nope", "filename": "x.dig",
    })
    assert r.status_code == 404
    sid = _upload("data/sample_circuits/tier1_minimal/single_and.dig")
    try:
        r = client.post("/api/l3/coverage", json={
            "session_id": sid, "filename": "not_uploaded.dig",
        })
        assert r.status_code == 404
    finally:
        server._SESSIONS.pop(sid, None)