"""
Global test isolation.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_official_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_OFFICIAL_TESTS_PATH",
                       str(tmp_path / "official_tests.json"))
