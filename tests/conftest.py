"""Global test isolation.

Every scan consults the user's official-test store — point it into
tmp for ALL tests so a developer's real ~/.dlc/official_tests.json can
never leak into (or be touched by) a test run.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_official_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_OFFICIAL_TESTS_PATH",
                       str(tmp_path / "official_tests.json"))
