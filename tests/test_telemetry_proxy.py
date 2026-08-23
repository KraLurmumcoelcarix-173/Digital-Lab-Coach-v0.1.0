"""
Telemetry chain: machine identity -> local spool -> ship ->
course proxy (limits, ingest, dedup) -> client relay routing.
"""

import json
import os
import sqlite3
import time
from datetime import date

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
    monkeypatch.delenv("DLC_GLOBAL_DAILY_CALLS", raising=False)
    monkeypatch.delenv("DLC_GLOBAL_DAILY_USD", raising=False)
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
    monkeypatch.delenv("DLC_PROXY_SELF", raising=False)
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


def test_call_llm_goes_direct_inside_the_proxy_process(tele_env,
                                                       monkeypatch):
    import proxy.dlc_proxy
    assert os.environ.get("DLC_PROXY_SELF") == "1"
    monkeypatch.setenv("DLC_PROXY_URL", "http://course.example")

    import httpx

    def explode(*a, **kw):
        raise AssertionError("proxy process tried to relay to a proxy")
    monkeypatch.setattr(httpx, "post", explode)

    class _SDK:
        def __init__(self, *a, **kw):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kw):
            class _B:
                type = "text"
                text = "{}"

            class _U:
                input_tokens = 1
                output_tokens = 1

            class _R:
                content = [_B()]
                usage = _U()
                stop_reason = "end_turn"
            return _R()

    monkeypatch.setattr(lc, "Anthropic", _SDK, raising=False)
    monkeypatch.setattr(lc, "get_api_key", lambda p=None: "sk-test")
    r = lc.call_llm("hi", model="claude-opus-5", feature="modeA")
    assert r["ok"] is True
    assert "limit" not in r


def test_budget_counts_inflight_reservations(tele_env, monkeypatch):
    from proxy import dlc_proxy
    monkeypatch.setattr(dlc_proxy, "CALL_BUDGETS", {"modeA": 2})
    pc = _proxy_client(monkeypatch)
    body = {"install_id": "m-rsv", "feature": "modeA",
            "model": "claude-opus-5", "prompt": "hi"}
    assert pc.post("/v1/llm", json=body).json()["ok"] is True
    conn = sqlite3.connect(os.environ["DLC_PROXY_DB"])
    with conn:
        conn.execute(
            "INSERT INTO llm_calls (install_id, day, ts, feature, model, "
            "ok, in_tokens, out_tokens, ms, error) "
            "VALUES (?,?,?,?,?,0,NULL,NULL,NULL,'pending')",
            ("m-rsv", date.today().isoformat(), 0.0, "modeA",
             "claude-opus-5"))
    conn.close()
    out = pc.post("/v1/llm", json=body).json()
    assert out["ok"] is False and out["limit_hit"] is True


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

def test_global_capacity_breaker_across_machines(tele_env, monkeypatch):
    monkeypatch.setenv("DLC_GLOBAL_DAILY_CALLS", "2")
    pc = _proxy_client(monkeypatch)
    a = {"install_id": "mach-A", "feature": "modeA",
         "model": "claude-opus-5", "prompt": "hi"}
    assert pc.post("/v1/llm", json=a).json()["ok"] is True
    assert pc.post("/v1/llm", json=a).json()["ok"] is True
    b = dict(a, install_id="mach-B")
    out = pc.post("/v1/llm", json=b).json()
    assert out["ok"] is False
    assert out.get("capacity_hit") is True
    assert "daily capacity" in out["error"]
    h = pc.get("/v1/health").json()
    assert h["today_calls"] == 2 and h["global_daily_calls"] == 2


def test_limit_message_dropped_redownload_sentence(tele_env, monkeypatch):
    from proxy import dlc_proxy
    monkeypatch.setattr(dlc_proxy, "CALL_BUDGETS", {"modeA": 1})
    pc = _proxy_client(monkeypatch)
    body = {"install_id": "m-msg", "feature": "modeA",
            "model": "claude-opus-5", "prompt": "hi"}
    assert pc.post("/v1/llm", json=body).json()["ok"] is True
    out = pc.post("/v1/llm", json=body).json()
    assert out["limit_hit"] is True
    assert "resets tomorrow" in out["error"]
    assert "Re-downloading" not in out["error"]


def test_admin_header_auth_daily_and_dashboard(tele_env, monkeypatch):
    monkeypatch.setenv("DLC_ADMIN_TOKEN", "adm")
    pc = _proxy_client(monkeypatch)
    body = {"install_id": "m-dash", "feature": "modeA",
            "model": "claude-opus-5", "prompt": "hi"}
    assert pc.post("/v1/llm", json=body).json()["ok"] is True

    assert pc.get("/admin/summary").status_code == 401
    s = pc.get("/admin/summary", headers={"X-DLC-Admin-Token": "adm"})
    assert s.status_code == 200

    d = pc.get("/admin/daily", headers={"X-DLC-Admin-Token": "adm"}).json()
    assert d["llm"][0]["install_id"] == "m-dash"
    assert d["llm"][0]["calls"] == 1
    assert d["spend_by_day"] and "est_usd" in d["spend_by_day"][0]

    page = pc.get("/admin/view")
    assert page.status_code == 200
    assert "dashboard" in page.text.lower()
    assert "X-DLC-Admin-Token" in page.text


def test_proxy_401_maps_to_settings_guidance(tele_env, monkeypatch):
    monkeypatch.delenv("DLC_PROXY_SELF", raising=False)
    monkeypatch.setattr(mach, "_raw_machine_identifier", lambda: "G-3")
    monkeypatch.setenv("DLC_PROXY_URL", "http://course.example")

    class _R401:
        status_code = 401
        def json(self):
            return {"detail": "bad course token"}

    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _R401())
    r = lc.call_llm("hello", model="claude-opus-5", feature="modeA")
    assert r["ok"] is False
    assert "re-paste" in r["error"]
    assert "Settings" in r["error"]


def test_saving_course_server_verifies_token(tele_env, monkeypatch):
    from fastapi.testclient import TestClient
    from dlc.web.server import app as webapp
    monkeypatch.setattr(mach, "_raw_machine_identifier", lambda: "G-4")
    wc = TestClient(webapp)

    class _R:
        def __init__(self, code):
            self.status_code = code
        def json(self):
            return {}

    import httpx
    replies = {"code": 200}
    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: _R(replies["code"]))

    body = {"url": "http://course.example", "token": "tok"}
    assert wc.post("/api/config/proxy",
                   json=body).json()["verify"] == "accepted"
    replies["code"] = 401
    assert wc.post("/api/config/proxy",
                   json=body).json()["verify"] == "bad_token"

    def boom(*a, **kw):
        raise OSError("down")
    monkeypatch.setattr(httpx, "post", boom)
    assert wc.post("/api/config/proxy",
                   json=body).json()["verify"] == "unreachable"

    g = wc.get("/api/config/proxy", params={"verify": 1}).json()
    assert g["configured"] is True and g["verify"] == "unreachable"

    out = wc.post("/api/config/proxy", json={"url": ""}).json()
    assert out == {"ok": True, "configured": False}
