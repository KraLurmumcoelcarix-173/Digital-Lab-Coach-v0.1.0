"""
First-run tutor endpoints
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_TUTOR_MARKER", str(tmp_path / "tutor_done"))
    from dlc.web.server import app
    return TestClient(app)


def test_state_defaults_unseen(client):
    assert client.get("/api/tutorial/state").json() == {"seen": False}


def test_seen_marker_persists(client):
    assert client.post("/api/tutorial/seen").json()["ok"] is True
    assert client.get("/api/tutorial/state").json() == {"seen": True}


def test_demo2_is_a_structural_bug_circuit(client):
    body = client.get("/api/tutorial/demo?which=2").json()
    assert body["ok"] is True and body["filename"] == "tutor_demo2.dig"
    assert "<circuit" in body["content"]


def test_demo_circuit_served_and_parseable(client, tmp_path):
    body = client.get("/api/tutorial/demo").json()
    assert body["ok"] is True
    assert body["filename"].endswith(".dig")
    assert "<circuit" in body["content"]
    from dlc.parser.dig_parser import parse_dig_file
    p = tmp_path / body["filename"]
    p.write_text(body["content"], encoding="utf-8")
    circuit = parse_dig_file(str(p))
    assert circuit.components
