"""Mode B row proposer (dlc/l3/proposer.py) + POST /api/l3/propose.

The model is always faked — these tests never touch the network. What IS
real: the coverage scan grounding, the prompt build, and the untrusted-
output pipeline (JSON parsing, header validation, value-level dedupe).
"""

import json

import pytest
from fastapi.testclient import TestClient

from dlc.l3 import proposer
from dlc.l3.coverage import scan_tree_coverage
from dlc.web import server
from dlc.web.server import app

client = TestClient(app)

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_LIMITS_PATH", str(tmp_path / "limits.json"))
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    monkeypatch.delenv("DLC_ENFORCE_LIMITS", raising=False)


def _fake(text):
    def call(prompt, **kw):
        return {"ok": True, "text": text, "error": None,
                "usage": None, "model": kw.get("model")}
    return call


# ---------------------------------------------------------------------------
# Grounding + prompt build
# ---------------------------------------------------------------------------

def test_targets_carry_headers_io_and_existing_rows():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    assert len(targets) == 1
    t = targets[0]
    assert t["file"] == "single_and.dig"
    assert t["headers"] == ["A", "B", "Y"]
    assert len(t["existing_rows"]) == 4
    assert {i["label"] for i in t["inputs"]} == {"A", "B"}


def test_prompt_embeds_report_and_targets_not_paths():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    prompt = proposer.build_prompt(report, targets)
    assert "SPOILER GUARD" in prompt
    assert '"single_and.dig"' in prompt
    assert '"existing_rows"' in prompt
    assert "sample_circuits" not in prompt      # no filesystem paths leak


# ---------------------------------------------------------------------------
# Untrusted-output pipeline
# ---------------------------------------------------------------------------

def test_parse_tolerates_fences_and_prose():
    text = ("Here you go:\n```json\n"
            + json.dumps({"proposals": [
                {"file": "f.dig", "spec_name": "T", "rows": [" 1 0 0 "],
                 "why": "gap"}]})
            + "\n```\nGood luck!")
    got = proposer.parse_proposals(text)
    assert got == [{"file": "f.dig", "spec_name": "T",
                    "rows": ["1 0 0"], "why": "gap"}]


def test_parse_returns_empty_on_garbage():
    assert proposer.parse_proposals("no json here") == []
    assert proposer.parse_proposals('{"proposals": "nope"}') == []
    assert proposer.parse_proposals("") == []


def test_validate_drops_illegal_duplicate_and_mistargeted_rows():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    proposals = [
        {"file": "single_and.dig", "spec_name": targets[0]["spec_name"],
         # 0x1 0x1 0x1 duplicates existing "1 1 1" by VALUE; "1 0" is short;
         # "1 0 1" is new and legal (even though the circuit will fail it —
         # that is 2.4's job to discover, not the validator's).
         "rows": ["0x1 0x1 0x1", "1 0", "1 0 1"], "why": "w"},
        {"file": "ghost.dig", "spec_name": "T", "rows": ["1 1 1"], "why": "w"},
    ]
    valid, rejected = proposer.validate_and_dedupe(proposals, targets)
    assert len(valid) == 1 and valid[0]["rows"] == ["1 0 1"]
    reasons = " | ".join(r["reason"] for r in rejected)
    assert "duplicate" in reasons
    assert "columns" in reasons                 # the short row
    assert "unknown target" in reasons          # ghost.dig


def test_total_row_cap_applies_across_groups():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    sp = targets[0]["spec_name"]
    many = [{"file": "single_and.dig", "spec_name": sp,
             "rows": [f"1 0 {i % 2}" if i else "1 0 1" for i in range(10)],
             "why": "w"}]
    # craft 10 distinct legal rows: vary A/B/Y bits
    many[0]["rows"] = ["1 0 1", "0 1 1", "1 1 0", "0 0 1",
                       "0x1 0x0 0x0", "0b0 0b1 0b0", "1 1 1",  # dup of existing
                       "0 0 0",                                 # dup of existing
                       "1 0 0", "0 1 0"]
    valid, rejected = proposer.validate_and_dedupe(many, targets)
    n_valid = sum(len(v["rows"]) for v in valid)
    assert n_valid <= proposer._MAX_TOTAL_ROWS


# ---------------------------------------------------------------------------
# propose_rows end to end (fake model)
# ---------------------------------------------------------------------------

def test_propose_rows_happy_path_with_fake_model():
    report_spec = proposer.build_targets(scan_tree_coverage(_AND))[0]
    text = json.dumps({"proposals": [
        {"file": "single_and.dig", "spec_name": report_spec["spec_name"],
         "rows": ["1 0 1"], "why": "closes the A=1,B=0 gap"},
    ]})
    out = proposer.propose_rows(_AND, call=_fake(text))
    assert out["ok"] is True
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["rows"] == ["1 0 1"]
    assert out["error"] is None


def test_propose_rows_refuses_when_scan_has_flags():
    bug = ("data/sample_circuits/30_bug_benchmark/"
           "bug1_meaningless_mux_in3/tier3_calculator.dig")
    out = proposer.propose_rows(bug, call=_fake("{}"))
    assert out["ok"] is False
    assert "disagree" in out["error"]


def test_propose_rows_survives_model_failure_and_garbage():
    def dead(prompt, **kw):
        return {"ok": False, "text": None, "error": "no key", "usage": None,
                "model": kw.get("model")}
    out = proposer.propose_rows(_AND, call=dead)
    assert out["ok"] is False and "no key" in out["error"]

    out2 = proposer.propose_rows(_AND, call=_fake("not json at all"))
    assert out2["ok"] is True and out2["proposals"] == []
    assert out2["notes"]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _upload_and():
    with open(_AND, "rb") as fh:
        r = client.post("/api/circuit",
                        files=[("files", ("single_and.dig", fh, "application/xml"))])
    assert r.status_code == 200
    return r.json()["session_id"]


def test_propose_endpoint_uses_the_proposer(monkeypatch):
    spec_name = proposer.build_targets(scan_tree_coverage(_AND))[0]["spec_name"]
    text = json.dumps({"proposals": [
        {"file": "single_and.dig", "spec_name": spec_name,
         "rows": ["0 1 0"], "why": "boundary"},
    ]})
    monkeypatch.setattr(proposer, "call_llm", _fake(text))
    sid = _upload_and()
    try:
        r = client.post("/api/l3/propose", json={
            "session_id": sid, "filename": "single_and.dig",
        })
        body = r.json()
        # "0 1 0" duplicates an existing row by value -> validator drops it
        assert body["ok"] is True
        assert body["proposals"] == []
        assert any("dropped" in n for n in body["notes"])
    finally:
        server._SESSIONS.pop(sid, None)


def test_propose_endpoint_404s_on_unknown_session():
    r = client.post("/api/l3/propose", json={
        "session_id": "nope", "filename": "x.dig",
    })
    assert r.status_code == 404
