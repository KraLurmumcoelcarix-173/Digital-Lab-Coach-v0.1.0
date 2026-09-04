"""
The Layer 3 replay runner (scripts/l3_replay.py): the shipped example
cases must stay green on the current code, and a broken expectation
must be reported as a failed case, not swallowed.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from dlc.testing.runner import find_digital_jar

_SCRIPT = Path("scripts/l3_replay.py")
_CASES = Path("data/l3_replay/cases_example.json")


def _load_runner():
    spec = importlib.util.spec_from_file_location("l3_replay", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recorded_call_answers_in_order_then_says_so():
    mod = _load_runner()
    call = mod.recorded_call([{"a": 1}, "raw text"])
    assert json.loads(call("p")["text"]) == {"a": 1}
    assert call("p")["text"] == "raw text"
    third = call("p")
    assert third["text"] == "{}" and "no reply recorded" in third["model"]
    assert call.asked == 3


def test_check_reports_every_broken_expectation():
    mod = _load_runner()
    got = {"mode": "lazy", "confirmed": False, "cards": 0, "llm_calls": 3, "wall_s": 99.0}
    problems = mod.check({"mode": "analysis", "confirmed": True, "min_cards": 1,
                          "max_llm_calls": 2, "max_wall_s": 60}, got)
    assert len(problems) == 5
    assert mod.check({}, got) == []


@pytest.mark.skipif(find_digital_jar() is None, reason="Digital.jar not configured")
def test_example_cases_replay_green_with_the_jar():
    mod = _load_runner()
    lines = []
    passed, failed = mod.run_cases(_CASES, out=lines.append)
    assert failed == 0 and passed == 3, "\n".join(lines)
    assert all(line.startswith("PASS") for line in lines[:-1]), "\n".join(lines)
