"""/api/simulate returns per-net signal-flow values for a clicked test row,
plus expected-vs-found for the top-level outputs (so a failed row renders red).
Purely additive endpoint — it never invokes Digital.
"""

from fastapi.testclient import TestClient

from dlc.web.server import app

client = TestClient(app)

_BASE = "data/sample_circuits"


def _upload(paths: list[str]) -> str:
    files = []
    for p in paths:
        files.append(("files", (p.split("/")[-1], open(p, "rb"), "application/xml")))
    r = client.post("/api/circuit", files=files)
    assert r.status_code == 200
    return r.json()["session_id"]


def test_simulate_combinational_calculator_with_subcircuit():
    sid = _upload([
        f"{_BASE}/tier3_realistic/tier3_calculator.dig",
        f"{_BASE}/tier3_realistic/bool_unit.dig",
    ])
    r = client.post("/api/simulate", json={
        "session_id": sid, "filename": "tier3_calculator.dig",
        "spec_index": 0, "row_index": 0,
    }).json()
    assert r["ok"] is True
    # every net carries a value (subcircuit resolved), nothing unresolved
    assert r["net_values"], r
    assert r["unresolved_nets"] == []
    result = next(o for o in r["outputs"] if o["label"] == "Result")
    # expected + found both rendered as hex for the bus (consistent display)
    assert result["expected"] == "0x8" and result["found"] == "0x8"
    assert result["ok"] is True
    # a net value payload is shaped for the UI
    any_net = next(iter(r["net_values"].values()))
    assert set(any_net) == {"value", "bits", "hex"}


def test_simulate_failed_row_reports_expected_vs_found():
    sid = _upload([f"{_BASE}/30_bug_benchmark/bug3_wrong_cin/Wrong_cin.dig"])
    # third good row expects Sum=7; the buggy circuit actually yields 8
    r = client.post("/api/simulate", json={
        "session_id": sid, "filename": "Wrong_cin.dig",
        "spec_index": 0, "row_index": 2,
    }).json()
    assert r["ok"] is True
    sumo = next(o for o in r["outputs"] if o["label"] == "Sum")
    assert sumo["expected"] == "0x7"
    assert sumo["found"] == "0x8"
    assert sumo["ok"] is False

def test_simulate_signed_output_matches_bit_pattern_not_a_false_mismatch():
    # register-file bug: expected -60 (signed) vs evaluated 0xFFFFFFC4
    # (4294967236 unsigned) are the SAME 32-bit pattern -> must NOT be red.
    sid = _upload([f"{_BASE}/tier1_minimal/signed_passthrough.dig"])
    r = client.post("/api/simulate", json={
        "session_id": sid, "filename": "signed_passthrough.dig",
        "spec_index": 0, "row_index": 1,   # A=-60 -> Y expected -60
    }).json()
    y = next(o for o in r["outputs"] if o["label"] == "Y")
    assert y["ok"] is True                 # was a false mismatch before the fix
    assert y["expected"] == "-60"
    assert y["found"] == "-60"             # rendered signed to match the expected


def test_output_ok_and_fmt_helpers():
    from dlc.web.server import _output_ok, _fmt_output
    assert _output_ok(4294967236, -60, 32) is True     # signed/unsigned same bits
    assert _output_ok(4294967236, -61, 32) is False    # genuinely different
    assert _output_ok(5, 5, 8) is True
    assert _output_ok(None, 5, 8) is None
    assert _fmt_output(4294967236, 32, True) == "-60"
    assert _fmt_output(0x1F, 8, False) == "0x1F"
    assert _fmt_output(1, 1, False) == "1"


def test_simulate_bad_session_is_404():
    r = client.post("/api/simulate", json={
        "session_id": "nope", "filename": "x.dig",
        "spec_index": 0, "row_index": 0,
    })
    assert r.status_code == 404
