"""Telemetry SQLite sink (dlc/telemetry/sink.py), the /api/telemetry
endpoint, and the session/job GC TTLs (dlc/web/server.py).

The sink is IRB-relevant plumbing: every test points DLC_TELEMETRY_DB at a
pytest tmp dir so nothing ever writes to the developer's real ~/.dlc.
"""

import time

import pytest
from fastapi.testclient import TestClient

from dlc.telemetry import sink
from dlc.web import server
from dlc.web.server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))


# ---------------------------------------------------------------------------
# Sink module
# ---------------------------------------------------------------------------

def test_log_events_stores_kind_ts_and_details():
    n = sink.log_events("sess1", [
        {"ts": 1751800000000, "kind": "l3_modeA_started", "row_count": 3},
        {"ts": 1751800001000, "kind": "tab_switch", "tab": "l3"},
    ])
    assert n == 2
    events = sink.recent_events()
    assert len(events) == 2
    newest = events[0]
    assert newest["kind"] == "tab_switch"
    assert newest["session_id"] == "sess1"
    assert newest["details"] == {"tab": "l3"}
    assert abs(newest["client_ts"] - 1751800001.0) < 1e-6   # ms -> seconds
    assert "2025" in newest["when"]          # human-readable, local time


def test_log_events_skips_malformed_entries_without_failing_the_batch():
    n = sink.log_events(None, [
        "not-a-dict",
        {"no_kind": True},
        {"kind": "ok_event"},
        {"kind": "weird", "blob": object()},     # unserializable detail
    ])
    assert n == 2
    kinds = {e["kind"] for e in sink.recent_events()}
    assert kinds == {"ok_event", "weird"}
    weird = next(e for e in sink.recent_events() if e["kind"] == "weird")
    assert weird["details"] == {"unserializable": True}


def test_recent_events_filters_by_kind_and_handles_missing_db(tmp_path, monkeypatch):
    sink.log_events("s", [{"kind": "a"}, {"kind": "b"}, {"kind": "a"}])
    assert len(sink.recent_events(kind="a")) == 2
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "nowhere" / "t.db"))
    assert sink.recent_events() == []            # no db yet -> empty, no crash


# ---------------------------------------------------------------------------
# /api/telemetry endpoint
# ---------------------------------------------------------------------------

def test_telemetry_endpoint_stores_batch():
    r = client.post("/api/telemetry", json={
        "session_id": "web1",
        "events": [{"ts": 1751800002000, "kind": "upload", "count": 2}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["stored"] == 1
    assert sink.recent_events(kind="upload")[0]["session_id"] == "web1"


def test_telemetry_endpoint_tolerates_empty_and_junk():
    r = client.post("/api/telemetry", json={"events": []})
    assert r.json() == {"ok": True, "stored": 0}
    r = client.post("/api/telemetry", json={
        "session_id": None, "events": [{"nope": 1}],
    })
    assert r.json()["stored"] == 0


# ---------------------------------------------------------------------------
# Session / job GC TTLs
# ---------------------------------------------------------------------------

def test_gc_sessions_removes_idle_sessions_and_their_tmp_dirs(tmp_path):
    d = tmp_path / "dlc-old"
    d.mkdir()
    (d / "x.dig").write_text("<circuit/>")
    server._SESSIONS["oldsess"] = {
        "tmp_dir": str(d), "files": [],
        "last_used": time.time() - server.SESSION_TTL_SECONDS - 10,
    }
    server._SESSIONS["fresh"] = {
        "tmp_dir": str(tmp_path / "dlc-fresh"), "files": [],
        "last_used": time.time(),
    }
    try:
        n = server._gc_sessions()
        assert n >= 1
        assert "oldsess" not in server._SESSIONS
        assert "fresh" in server._SESSIONS
        assert not d.exists()                    # upload dir cleaned from disk
    finally:
        server._SESSIONS.pop("fresh", None)
        server._SESSIONS.pop("oldsess", None)


def test_activity_defers_the_session_ttl():
    server._SESSIONS["busy"] = {
        "tmp_dir": None, "files": [{"name": "a.dig", "path": "/nope/a.dig"}],
        "last_used": time.time() - server.SESSION_TTL_SECONDS - 10,
    }
    try:
        # _resolve_target touches last_used, so the follow-up GC keeps it.
        server._resolve_target("busy", "a.dig")
        assert server._gc_sessions() == 0
        assert "busy" in server._SESSIONS
    finally:
        server._SESSIONS.pop("busy", None)


def test_gc_jobs_drops_stale_records():
    with server._JOBS_LOCK:
        server._JOBS["oldjob"] = {
            "ok": True, "finished": True,
            "created_at": time.time() - server.JOB_TTL_SECONDS - 10,
        }
        server._JOBS["newjob"] = {
            "ok": True, "finished": False, "created_at": time.time(),
        }
    try:
        assert server._gc_jobs() >= 1
        with server._JOBS_LOCK:
            assert "oldjob" not in server._JOBS
            assert "newjob" in server._JOBS
    finally:
        with server._JOBS_LOCK:
            server._JOBS.pop("newjob", None)
            server._JOBS.pop("oldjob", None)


def test_upload_runs_the_gc_sweep(tmp_path):
    d = tmp_path / "dlc-stale"
    d.mkdir()
    server._SESSIONS["stale"] = {
        "tmp_dir": str(d), "files": [],
        "last_used": time.time() - server.SESSION_TTL_SECONDS - 10,
    }
    path = "data/sample_circuits/tier1_minimal/single_and.dig"
    with open(path, "rb") as fh:
        r = client.post("/api/circuit",
                        files=[("files", ("single_and.dig", fh, "application/xml"))])
    assert r.status_code == 200
    new_sid = r.json()["session_id"]
    try:
        assert "stale" not in server._SESSIONS   # swept by the upload
        assert not d.exists()
        assert server._SESSIONS[new_sid]["last_used"] > 0
    finally:
        server._SESSIONS.pop(new_sid, None)
        server._SESSIONS.pop("stale", None)
