"""
Web API: tests are blocked while Layer 1 structural ERRORS exist.

The gate fires before the Digital.jar lookup, so these tests run with
or without a configured jar.
"""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from dlc.web.server import app

SAMPLES = Path(__file__).parent.parent / "data" / "sample_circuits"


BROKEN_WITH_TESTS = """<?xml version="1.0" encoding="utf-8"?>
<circuit>
  <version>2</version>
  <visualElements>
    <visualElement>
      <elementName>Out</elementName>
      <elementAttributes>
        <entry>
          <string>Label</string>
          <string>Y</string>
        </entry>
      </elementAttributes>
      <pos x="100" y="0"/>
    </visualElement>
    <visualElement>
      <elementName>Testcase</elementName>
      <elementAttributes>
        <entry>
          <string>Label</string>
          <string>gate_test</string>
        </entry>
        <entry>
          <string>Testdata</string>
          <testData>
            <dataString>Y
0</dataString>
          </testData>
        </entry>
      </elementAttributes>
      <pos x="0" y="100"/>
    </visualElement>
  </visualElements>
  <wires/>
</circuit>
"""


def _upload(client: TestClient) -> tuple[str, dict]:
    clean = (SAMPLES / "tier1_minimal" / "single_and.dig").read_bytes()
    resp = client.post("/api/circuit", files=[
        ("files", ("broken.dig", BROKEN_WITH_TESTS.encode("utf-8"),
                   "application/octet-stream")),
        ("files", ("single_and.dig", clean, "application/octet-stream")),
    ])
    assert resp.status_code == 200
    data = resp.json()
    by_name = {f["filename"]: f for f in data["files"]}
    return data["session_id"], by_name


def test_upload_reports_the_blocking_error():
    client = TestClient(app)
    _sid, by_name = _upload(client)
    errs = [i for i in by_name["broken.dig"]["issues"]
            if i["severity"] == "error"]
    assert errs, "fixture must carry at least one L1 error"
    assert not [i for i in by_name["single_and.dig"]["issues"]
                if i["severity"] == "error"]


def test_general_and_sync_runs_are_blocked_on_l1_errors():
    client = TestClient(app)
    sid, _ = _upload(client)
    for endpoint in ("/api/tests/start", "/api/tests"):
        resp = client.post(endpoint, json={
            "session_id": sid, "filename": "broken.dig", "mode": "general",
        })
        body = resp.json()
        assert body["ok"] is False
        assert body["warning"].startswith("Blocked:")


def test_per_row_job_is_blocked_on_l1_errors():
    client = TestClient(app)
    sid, _ = _upload(client)
    start = client.post("/api/tests/start", json={
        "session_id": sid, "filename": "broken.dig", "mode": "per_row",
    }).json()
    job_id = start["job_id"]
    snap = None
    for _ in range(100):
        snap = client.get(f"/api/tests/progress/{job_id}").json()
        if snap["finished"]:
            break
        time.sleep(0.05)
    assert snap and snap["finished"]
    assert snap["ok"] is False
    assert snap["warning"].startswith("Blocked:")


def test_tests_all_marks_file_blocked_but_not_the_clean_one():
    client = TestClient(app)
    sid, _ = _upload(client)
    body = client.post("/api/tests/all", json={"session_id": sid}).json()
    by_name = {f["filename"]: f for f in body["files"]}
    assert by_name["broken.dig"]["status"] == "blocked"
    assert by_name["broken.dig"]["warning"].startswith("Blocked:")
    assert by_name["single_and.dig"]["status"] != "blocked"
    assert body["summary"]["blocked"] == 1
    assert body["all_passed"] is False
