"""offline audit: every LLM touchpoint must FAIL SOFT when there is
no internet — a clear message, never a crash, and every deterministic
feature (scans, gates, synthesis, jar verification) keeps working.

Context for the ratified rule: API keys are only tickets — the models run
in the providers' clouds, so every LLM call needs the network no matter
whose key is configured. These tests simulate the offline case at the SDK
boundary."""

import pytest

import dlc.llm.client as lc
from dlc.l3 import proposer


class _OfflineSDK:
    """Stands in for Anthropic()/OpenAI(): every call dies like a dead NIC."""
    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, name):
        return _OfflineSDK()

    def create(self, *a, **kw):
        raise ConnectionError("getaddrinfo failed: no network")

    def __call__(self, *a, **kw):
        raise ConnectionError("getaddrinfo failed: no network")


@pytest.fixture()
def _offline(monkeypatch):
    monkeypatch.setattr(lc, "Anthropic", _OfflineSDK, raising=False)
    monkeypatch.setattr(lc, "OpenAI", _OfflineSDK, raising=False)
    monkeypatch.setattr(lc, "get_api_key", lambda p=None: "sk-test")


def test_call_llm_fails_soft_with_a_clear_message(_offline):
    resp = lc.call_llm("hi", model=lc.DEFAULT_MODEL)
    assert resp["ok"] is False
    assert "internet connection" in (resp["error"] or "")


def test_propose_rows_offline_reports_and_never_raises(_offline, monkeypatch):
    monkeypatch.setenv("DLC_LIMITS_PATH", "/tmp/does-not-matter-limits.json")
    out = proposer.propose_rows(
        "data/sample_circuits/tier1_minimal/single_and.dig")
    assert out["ok"] is False
    assert "internet connection" in (out["error"] or "")
    assert out["proposals"] == []


def test_deterministic_paths_work_fully_offline(_offline):
    # the scan, the manifest machinery, and program synthesis never touch
    # the network — the RISC-V coach can even BUILD extensions offline
    from dlc.l3 import manifest as mf
    from dlc.l3.coverage import scan_tree_coverage
    import json
    from pathlib import Path
    report = scan_tree_coverage(
        "data/sample_circuits/tier1_minimal/single_and.dig")
    assert report.circuits and report.circuits[0].has_testcases
    lab5 = json.loads(Path("data/manifests/lab5.json").read_text())
    syn = mf.synthesize_program_extension(
        lab5, [0xFEC00213], ["add"], ["clk", "ReadData1", "ReadData2"], "clk")
    assert syn and syn["program_words"]
