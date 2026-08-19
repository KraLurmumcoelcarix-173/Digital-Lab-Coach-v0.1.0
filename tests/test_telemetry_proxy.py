"""
Telemetry chain: machine identity -> local spool -> ship ->
course proxy (limits, ingest, dedup) -> client relay routing.
"""

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

import dlc.llm.client as lc
from dlc.telemetry import machine as mach
from dlc.telemetry import ship, sink


@pytest.fixture()
def tele_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "tele.db"))
    monkeypatch.setenv("DLC_MACHINE_CACHE", str(tmp_path / "machine.json"))
    monkeypatch.setenv("DLC_PROXY_DB", str(tmp_path / "proxy.db"))
    monkeypatch.delenv("DLC_PROXY_URL", raising=False)
    monkeypatch.delenv("DLC_COURSE_TOKEN", raising=False)
    monkeypatch.delenv("DLC_ADMIN_TOKEN", raising=False)
    return tmp_path


def test_machine_id_survives_cache_deletion(tele_env, monkeypatch):
    monkeypatch.setattr(mach, "_raw_machine_identifier",
                        lambda: "GUID-AAAA-BBBB")
    a = mach.machine_identity()
    assert a["source"] == "os" and len(a["install_id"]) == 16
    assert a["issued"]
    (tele_env / "machine.json").unlink()
    b = mach.machine_identity()
    assert b["install_id"] == a["install_id"]
    monkeypatch.setattr(mach, "_raw_machine_identifier",
                        lambda: "GUID-OTHER")
    c = mach.machine_identity()
    assert c["install_id"] != a["install_id"]


def test_machine_id_stable_fallback_without_os_guid(tele_env, monkeypatch):
    monkeypatch.setattr(mach, "_raw_machine_identifier", lambda: None)
    a = mach.machine_identity()
    assert a["source"] == "stable_fallback"
    (tele_env / "machine.json").unlink()
    assert mach.machine_identity()["install_id"] == a["install_id"]


# proxy app

def _proxy_client(monkeypatch, upstream=None):
    from proxy import dlc_proxy
    if upstream is None:
        def upstream(prompt, **kw):
            return {"ok": True, "text": "{}", "error": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "stop_reason": "end_turn", "model": kw.get("model")}
    import dlc.llm.client as real
    monkeypatch.setattr(real, "call_llm", upstream)
    return TestClient(dlc_proxy.app)


def test_proxy_ingest_dedup_and_first_seen(tele_env, monkeypatch):
    pc = _proxy_client(monkeypatch)
    batch = {"install_id": "m1", "issued": "2026-08-01",
             "id_source": "os", "app_version": "0.1.0",
             "events": [{"client_row_id": 1, "kind": "upload",
                         "props": {"count": 2}},
                        {"client_row_id": 2, "kind": "tests_run_complete",
                         "props": {}}]}
    assert pc.post("/v1/events", json=batch).json()["ok"] is True
    pc.post("/v1/events", json=batch)
    h = pc.get("/v1/health").json()
    assert h["machines"] == 1 and h["events"] == 2

    monkeypatch.setenv("DLC_ADMIN_TOKEN", "adm")
    s = pc.get("/admin/summary", params={"token": "adm"}).json()
    assert s["machines"][0]["install_id"] == "m1"
    assert s["machines"][0]["first_seen"]
    assert s["machines"][0]["issued_client"] == "2026-08-01"


def test_proxy_budget_survives_redownload_and_separates_machines(
        tele_env, monkeypatch):
    from proxy import dlc_proxy
    monkeypatch.setattr(dlc_proxy, "CALL_BUDGETS", {"modeA": 2})
    pc = _proxy_client(monkeypatch)
    body = {"install_id": "mach-A", "feature": "modeA",
            "model": "claude-opus-5", "prompt": "hi"}
    assert pc.post("/v1/llm", json=body).json()["ok"] is True
    assert pc.post("/v1/llm", json=body).json()["ok"] is True
    third = pc.post("/v1/llm", json=body).json()
    assert third["ok"] is False and third["limit_hit"] is True
    pc2 = _proxy_client(monkeypatch)
    again = pc2.post("/v1/llm", json=body).json()
    assert again["ok"] is False and again["limit_hit"] is True
    other = dict(body, install_id="mach-B")
    assert pc2.post("/v1/llm", json=other).json()["ok"] is True


def test_proxy_course_token_gate(tele_env, monkeypatch):
    monkeypatch.setenv("DLC_COURSE_TOKEN", "sekrit")
    pc = _proxy_client(monkeypatch)
    body = {"install_id": "m", "feature": "modeA",
            "model": "claude-opus-5", "prompt": "hi"}
    assert pc.post("/v1/llm", json=body).status_code == 401
    ok = pc.post("/v1/llm", json=body,
                 headers={"X-DLC-Token": "sekrit"})
    assert ok.status_code == 200 and ok.json()["ok"] is True

def test_ship_moves_spool_to_proxy_and_survives_offline(tele_env,
                                                        monkeypatch):
    monkeypatch.setattr(mach, "_raw_machine_identifier", lambda: "G-1")
    sink.log_events("sess1", [{"kind": "upload", "count": 1},
                              {"kind": "tests_run_complete", "ok": True}])
    assert ship.ship_pending()["reason"] == "no_proxy"

    pc = _proxy_client(monkeypatch)
    monkeypatch.setenv("DLC_PROXY_URL", "http://course.example")

    class _Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
        def json(self):
            return self._body

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url == "http://course.example/v1/events"
        r = pc.post("/v1/events", json=json, headers=headers)
        return _Resp(r.status_code, r.json())

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    out = ship.ship_pending()
    assert out["reason"] == "ok" and out["shipped"] == 2
    assert pc.get("/v1/health").json()["events"] == 2

    assert ship.ship_pending()["reason"] == "up_to_date"
    conn = sqlite3.connect(str(tele_env / "tele.db"))
    with conn:
        conn.execute("UPDATE ship_state SET last_shipped = 0")
    conn.close()
    assert ship.ship_pending()["shipped"] == 2
    assert pc.get("/v1/health").json()["events"] == 2

    def dead_post(*a, **kw):
        raise OSError("no route to host")
    monkeypatch.setattr(httpx, "post", dead_post)
    sink.log_events("sess1", [{"kind": "upload"}])
    assert ship.ship_pending()["shipped"] == 0


# client relay routing

def test_call_llm_routes_through_proxy_when_configured(tele_env,
                                                       monkeypatch):
    monkeypatch.setattr(mach, "_raw_machine_identifier", lambda: "G-2")
    monkeypatch.setenv("DLC_PROXY_URL", "http://course.example")
    seen = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "text": "{}", "error": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "model": "claude-opus-5",
                    "limit": {"feature": "modeA", "used": 1, "budget": 8}}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(json)
        seen["url"] = url
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    r = lc.call_llm("hello", model="claude-opus-5", feature="modeA")
    assert r["ok"] is True and r["limit"]["feature"] == "modeA"
    assert seen["url"] == "http://course.example/v1/llm"
    assert seen["feature"] == "modeA"
    assert len(seen["install_id"]) == 16
    assert seen["model"] == "claude-opus-5"


def test_debugger_tags_modeA_feature(tele_env):
    seen = {}
    def call(prompt, **kw):
        seen.update(kw)
        return {"ok": True, "text": json.dumps({
            "contract": "l3.debug.v1.1", "confidence": 0.5,
            "hint": {"suspect_region": "x", "suspect_signals": [],
                     "why": "y"},
            "fix": {"ops": [{"op": "change_attribute",
                             "component_index": 16, "name": "Value",
                             "value": 0}],
                    "explanation_for_student": "z",
                    "animation_script": [{"act": "retest"}]}}),
            "error": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model": "fake"}
    from dlc.l3.debugger import debug_circuit
    debug_circuit("data/sample_circuits/30_bug_benchmark/bug3_wrong_cin/"
                  "Wrong_cin.dig", call=call, use_manifest=False,
                  failing_indices=[0, 1])
    assert seen.get("feature") == "modeA"
