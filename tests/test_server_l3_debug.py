"""POST /api/llm/debug (dlc/web/l3_routes.py): Mode A endpoint wiring —
limits (consume only on delivered cards, empty runs free), coach-temp
targeting, and the limited gate. The coordinator itself is covered by
test_l3_debugger.py; here it is monkeypatched to canned results.
"""

import pytest
from fastapi.testclient import TestClient

from dlc.l3 import debugger
from dlc.web import server
from dlc.web.server import app

client = TestClient(app)

_BUG3 = "data/sample_circuits/30_bug_benchmark/bug3_wrong_cin/Wrong_cin.dig"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_LIMITS_PATH", str(tmp_path / "limits.json"))
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    monkeypatch.delenv("DLC_ENFORCE_LIMITS", raising=False)


def _upload_bug3():
    with open(_BUG3, "rb") as fh:
        r = client.post("/api/circuit",
                        files=[("files", ("Wrong_cin.dig", fh,
                                          "application/xml"))])
    assert r.status_code == 200
    return r.json()["session_id"]


def _debug(sid, **extra):
    return client.post("/api/llm/debug", json={
        "session_id": sid, "filename": "Wrong_cin.dig", **extra,
    }).json()


def _canned(mode, cards):
    def fake(path, **kw):
        fake.calls.append({"path": path, **kw})
        return {"ok": True, "mode": mode, "cards": cards, "notes": []}
    fake.calls = []
    return fake


def test_delivered_cards_consume_one_mode_a_use(monkeypatch):
    sid = _upload_bug3()
    monkeypatch.setattr(debugger, "debug_circuit",
                        _canned("analysis", [{"rank": 1}]))
    body = _debug(sid)
    assert body["ok"] is True
    assert body["consumed_use"] is True
    assert body["limits"]["used"]["modeA"] == 1


def test_empty_analysis_is_free_and_says_so(monkeypatch):
    sid = _upload_bug3()
    monkeypatch.setattr(debugger, "debug_circuit", _canned("analysis", []))
    body = _debug(sid)
    assert body["consumed_use"] is False
    assert body["limits"]["used"]["modeA"] == 0
    assert any("did not count" in n for n in body["notes"])


def test_clear_and_lazy_are_free(monkeypatch):
    sid = _upload_bug3()
    for mode in ("clear", "lazy"):
        monkeypatch.setattr(debugger, "debug_circuit", _canned(mode, []))
        body = _debug(sid)
        assert body["consumed_use"] is False
    assert body["limits"]["used"]["modeA"] == 0


def test_enforced_cap_blocks_the_fourth_run(monkeypatch):
    sid = _upload_bug3()
    monkeypatch.setattr(debugger, "debug_circuit",
                        _canned("analysis", [{"rank": 1}]))
    for _ in range(3):
        assert _debug(sid)["consumed_use"] is True
    monkeypatch.setenv("DLC_ENFORCE_LIMITS", "1")
    body = _debug(sid)
    assert body.get("limited") is True
    assert "limit" in body["warning"].lower()


def test_coach_temp_is_targeted_when_registered(monkeypatch):
    sid = _upload_bug3()
    fake = _canned("clear", [])
    monkeypatch.setattr(debugger, "debug_circuit", fake)

    body = _debug(sid)
    assert body["on_coach_temp"] is False

    # register a coach temp for this file (point it at a real path so the
    # existence check passes), exactly like /api/l3/inject does
    server._SESSIONS[sid]["l3_temp"] = {
        "for": "Wrong_cin.dig",
        "name": "Wrong_cin__coach.dig",
        "path": _BUG3,
        "spec_name": "Testcase_12",
    }
    body = _debug(sid)
    assert body["on_coach_temp"] is True
    assert fake.calls[-1]["path"] == _BUG3
    assert fake.calls[-1]["spec_name"] == "Testcase_12"
