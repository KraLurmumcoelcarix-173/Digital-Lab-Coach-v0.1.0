"""
Global test isolation.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_official_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_OFFICIAL_TESTS_PATH",
                       str(tmp_path / "official_tests.json"))


@pytest.fixture(autouse=True)
def _isolated_llm_client_config(tmp_path, monkeypatch):
    """Tests must never see the developer's real ~/.dlc/config.json or
    shell API keys: a configured course server (proxy_url) or personal
    key there would leak into every call_llm path — worst case routing
    test prompts through a live proxy."""
    import dlc.llm.client as lc
    monkeypatch.setattr(lc, "_config_path",
                        lambda: tmp_path / "dlc_config.json")
    for var in ("DLC_PROXY_URL", "DLC_PROXY_TOKEN",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
