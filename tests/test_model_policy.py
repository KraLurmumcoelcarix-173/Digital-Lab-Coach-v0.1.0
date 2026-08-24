import pytest
from fastapi.testclient import TestClient

import dlc.llm.client as lc


@pytest.fixture()
def client():
    from dlc.web.server import app
    return TestClient(app)


def _by_provider(body):
    out = {"anthropic": [], "openai": []}
    for m in body["models"]:
        out.setdefault(m["provider"], []).append(m)
    return out


def test_models_need_personal_key_without_proxy(client, monkeypatch):
    monkeypatch.setattr(lc, "_proxy_config", lambda: (None, None))
    body = client.get("/api/llm/models").json()
    assert all(not m["key_configured"] for m in body["models"])


def test_anthropic_models_enabled_through_proxy(client, monkeypatch):
    monkeypatch.setattr(lc, "_proxy_config",
                        lambda: ("http://course.example:8321", "course-t"))
    body = client.get("/api/llm/models").json()
    per = _by_provider(body)
    assert per["anthropic"] and all(
        m["key_configured"] for m in per["anthropic"])
    assert all(not m["key_configured"] for m in per["openai"])


def test_propose_request_accepts_model_field():
    from dlc.web.l3_routes import ProposeRequest
    req = ProposeRequest(session_id="s", filename="f.dig",
                         model="claude-opus-5")
    assert req.model == "claude-opus-5"
    assert ProposeRequest(session_id="s", filename="f.dig").model is None
