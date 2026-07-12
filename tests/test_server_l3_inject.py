"""POST /api/l3/inject (dlc/web/l3_routes.py): Mode B's accept-flow — inject
rows into a temp copy, re-run per-row through the real Digital runner,
register the temp file in the session. Runner parts are jar-gated exactly
like the oracle's own tests.
"""

import pytest
from fastapi.testclient import TestClient

from dlc.testing.runner import find_digital_jar
from dlc.web import server
from dlc.web.server import app

client = TestClient(app)

_needs_jar = pytest.mark.skipif(
    find_digital_jar() is None, reason="Digital.jar not configured",
)

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_LIMITS_PATH", str(tmp_path / "limits.json"))
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    monkeypatch.delenv("DLC_ENFORCE_LIMITS", raising=False)


def _upload_and():
    with open(_AND, "rb") as fh:
        r = client.post("/api/circuit",
                        files=[("files", ("single_and.dig", fh, "application/xml"))])
    assert r.status_code == 200
    return r.json()["session_id"]


def _inject(sid, rows, **extra):
    return client.post("/api/l3/inject", json={
        "session_id": sid, "filename": "single_and.dig", "rows": rows, **extra,
    }).json()


@_needs_jar
def test_passing_rows_yield_all_set_and_register_the_temp_file():
    sid = _upload_and()
    try:
        body = _inject(sid, ["1 1 1", "0 1 0"])
        assert body["ok"] is True
        assert body["outcome"] == "all_set"
        assert body["all_passed"] is True and body["added_all_passed"] is True
        assert body["temp_filename"] == "single_and__coach.dig"
        # original 4 rows + 2 injected, provenance carried through
        added = [r for r in body["rows"] if r["added"]]
        assert len(added) == 2
        assert all(r["origin"] == "coach" for r in added)
        assert all(r["status"] == "passed" for r in added)
        # the temp copy is now addressable like any session file
        names = [f["name"] for f in server._SESSIONS[sid]["files"]]
        assert "single_and__coach.dig" in names
        assert server._SESSIONS[sid]["l3_temp"]["for"] == "single_and.dig"
    finally:
        server._SESSIONS.pop(sid, None)


@_needs_jar
def test_failing_row_yields_rows_fail_with_the_row_marked():
    sid = _upload_and()
    try:
        body = _inject(sid, ["0 0 1"])          # 0 AND 0 is not 1
        assert body["ok"] is True
        assert body["outcome"] == "rows_fail"
        assert body["added_all_passed"] is False
        bad = [r for r in body["rows"] if r["added"]][0]
        assert bad["status"] == "failed"
    finally:
        server._SESSIONS.pop(sid, None)


@_needs_jar
def test_reinjection_replaces_the_previous_temp_file():
    sid = _upload_and()
    try:
        first = _inject(sid, ["1 1 1"])
        second = _inject(sid, ["0 1 0", "1 0 0"])
        assert first["ok"] and second["ok"]
        names = [f["name"] for f in server._SESSIONS[sid]["files"]]
        assert names.count("single_and__coach.dig") == 1
        added = [r for r in second["rows"] if r["added"]]
        assert len(added) == 2                   # not stacked on the first temp
    finally:
        server._SESSIONS.pop(sid, None)


@_needs_jar
def test_malformed_rows_are_rejected_before_any_run():
    sid = _upload_and()
    try:
        body = _inject(sid, ["1 1"])             # wrong cell count
        assert body["ok"] is False
        assert body["outcome"] == "error"
        assert "columns" in body["warning"]
        names = [f["name"] for f in server._SESSIONS[sid]["files"]]
        assert "single_and__coach.dig" not in names
    finally:
        server._SESSIONS.pop(sid, None)


@_needs_jar
def test_unknown_spec_name_is_a_clean_error():
    sid = _upload_and()
    try:
        body = _inject(sid, ["1 1 1"], spec_name="nope")
        assert body["ok"] is False and body["outcome"] == "error"
        assert "No testcase named" in body["warning"]
    finally:
        server._SESSIONS.pop(sid, None)


def test_unknown_session_404s_without_touching_the_runner():
    r = client.post("/api/l3/inject", json={
        "session_id": "nope", "filename": "x.dig", "rows": ["1 1 1"],
    })
    assert r.status_code == 404
