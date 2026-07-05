"""
Tests for dlc.testing.runner — F4 per-row Digital subprocess runner.
"""

import os
from pathlib import Path
from unittest.mock import patch

from dlc.testing.spec import TestSpec, TestRow, Token
from dlc.testing.runner import (
    find_digital_jar, _write_single_row_dig,
    per_row_run, attach_per_row_results, run_digital_cli,
)
from dlc.testing.run import PerRowResult, TestRun


# Single-row .dig substitution

def test_write_single_row_dig_replaces_dataString_with_one_row(tmp_path):
    src = tmp_path / "src.dig"
    src.write_text(
        '<?xml version="1.0"?>'
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A B Y\n0 0 0\n1 1 1</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    out = _write_single_row_dig(str(src), ["A", "B", "Y"], "1 1 1")
    content = Path(out).read_text()
    assert "<dataString>A B Y\n1 1 1</dataString>" in content
    assert "0 0 0" not in content
    os.unlink(out)


def test_write_single_row_dig_no_datastring_falls_back_to_original(tmp_path):
    src = tmp_path / "src.dig"
    src.write_text("<circuit><version>2</version></circuit>")
    out = _write_single_row_dig(str(src), ["A"], "0")
    assert Path(out).read_text() == "<circuit><version>2</version></circuit>"
    os.unlink(out)

def test_find_digital_jar_uses_env_var(tmp_path):
    fake_jar = tmp_path / "Digital.jar"
    fake_jar.write_text("")
    with patch.dict(os.environ, {"DIGITAL_JAR": str(fake_jar)}, clear=False):
        assert find_digital_jar() == str(fake_jar)


def test_find_digital_jar_returns_none_when_nothing_found():
    # Isolate ALL three discovery sources: env var, the saved
    # ~/.dlc/config.json (leaks in from the developer's real machine
    # otherwise), and the common install-path probe.
    with patch.dict(os.environ, {}, clear=True):
        with patch("dlc.testing.config.get_configured_jar", return_value=None):
            with patch("dlc.testing.runner.Path") as mock_path:
                instance = mock_path.return_value
                instance.exists.return_value = False
                assert find_digital_jar() is None


# Jar missing path


def test_per_row_run_no_jar_returns_error_per_row(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0\n1</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = TestSpec(
        name="t", component_index=0, headers=["A"],
        rows=[
            TestRow(raw="0", values=[Token("0","int",0)], line_index=0),
            TestRow(raw="1", values=[Token("1","int",1)], line_index=1),
        ],
        raw_data_string="A\n0\n1", has_unexpanded_loops=False,
    )
    with patch("dlc.testing.runner.find_digital_jar", return_value=None):
        results = per_row_run(spec, str(src))
    assert len(results) == 2
    assert all(r.status == "error" for r in results)
    assert all("DIGITAL_JAR" in (r.error_message or "") for r in results)


# Mocked Digital responses


def _spec_with_rows(name, rows_text):
    rows = [
        TestRow(raw=t, values=[Token(t, "int", int(t))], line_index=i)
        for i, t in enumerate(rows_text)
    ]
    return TestSpec(
        name=name, component_index=0, headers=["A"],
        rows=rows, raw_data_string="", has_unexpanded_loops=False,
    )

def test_per_row_run_records_timeout_as_error(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", ["0"])
    with patch("dlc.testing.runner.run_digital_cli",
               return_value=(-1, "subprocess timeout")):
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert results[0].status == "error"
    assert "timed out" in (results[0].error_message or "").lower()


def test_per_row_run_records_java_missing_as_error(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", ["0"])
    with patch("dlc.testing.runner.run_digital_cli",
               return_value=(-2, "java not on PATH")):
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert results[0].status == "error"
    assert "java" in (results[0].error_message or "").lower()


def test_per_row_run_no_result_line_recorded_as_error(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", ["0"])
    with patch("dlc.testing.runner.run_digital_cli",
               return_value=(0, "some unrelated output\n")):
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert results[0].status == "error"


def test_malformed_row_recorded_as_no_run(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text("<circuit></circuit>")
    spec = TestSpec(
        name="t", component_index=0, headers=["A", "B"],
        rows=[
            TestRow(raw="0", values=[], line_index=0, is_malformed=True),
            TestRow(raw="0 1", values=[Token("0","int",0), Token("1","int",1)],
                    line_index=1, is_malformed=False),
        ],
        raw_data_string="", has_unexpanded_loops=False,
    )
    with patch("dlc.testing.runner.run_digital_cli",
               return_value=(0, "t: passed\n")) as mock_run:
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert results[0].status == "no_run"
    assert results[1].status == "passed"
    assert mock_run.call_count == 1

def test_loop_expr_row_recorded_as_no_run(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text("<circuit></circuit>")
    spec = TestSpec(
        name="t", component_index=0, headers=["A", "B"],
        rows=[
            TestRow(
                raw="(N+1) (N)", line_index=0, is_malformed=False,
                values=[Token("(N+1)", "loop_expr", None),
                        Token("(N)", "loop_expr", None)],
            ),
        ],
        raw_data_string="", has_unexpanded_loops=True,
    )
    with patch("dlc.testing.runner.run_digital_cli") as mock_run:
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert results[0].status == "no_run"
    assert "loop" in (results[0].error_message or "").lower()
    assert mock_run.call_count == 0

def test_attach_per_row_results_populates_runs_in_place(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0\n1</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", ["0", "1"])
    run = TestRun(spec=spec, bindings={}, result=None, per_row_results=[])
    with patch("dlc.testing.runner.run_digital_cli",
               return_value=(0, "t: passed\n")):
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            attach_per_row_results([run], str(src))
    assert len(run.per_row_results) == 2
    assert run.has_per_row_data
    assert run.failing_rows() == []

def test_safe_unlink_retries_then_succeeds(tmp_path):
    from dlc.testing.runner import _safe_unlink
    p = tmp_path / "f.dig"
    p.write_text("x")
    _safe_unlink(str(p))
    assert not p.exists()


def test_safe_unlink_queues_pending_on_persistent_failure(tmp_path):
    from unittest.mock import patch
    from dlc.testing.runner import _safe_unlink, _PENDING_CLEANUP
    _PENDING_CLEANUP.clear()
    fake_path = str(tmp_path / "never_unlinkable.dig")
    with patch("dlc.testing.runner.os.unlink", side_effect=OSError("locked")):
        _safe_unlink(fake_path)
    assert fake_path in _PENDING_CLEANUP
    _PENDING_CLEANUP.clear()

def test_per_row_run_cumulative_all_pass(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0\n1\n2</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", ["0", "1", "2"])
    with patch("dlc.testing.runner.run_digital_cli",
               return_value=(0, "t: passed\n")) as mock_run:
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert mock_run.call_count == 3
    assert [r.status for r in results] == ["passed", "passed", "passed"]


def test_per_row_run_cumulative_isolates_failing_row(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n0\n1\n2</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", ["0", "1", "2"])
    outputs = iter([
        (0, "t: passed\n"),
        (0, "t: failed (50%)\n"),
        (0, "t: failed (33%)\n"),
    ])
    with patch("dlc.testing.runner.run_digital_cli",
               side_effect=lambda *a, **kw: next(outputs)):
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))
    assert [r.status for r in results] == ["passed", "failed", "passed"]


def test_per_row_run_cumulative_detects_multiple_failures(tmp_path):
    src = tmp_path / "x.dig"
    src.write_text(
        '<circuit><version>2</version><visualElements>'
        '<visualElement><elementName>Testcase</elementName>'
        '<elementAttributes><entry><string>Testdata</string>'
        '<testData><dataString>A\n' + "\n".join(str(i) for i in range(12)) +
        '</dataString></testData></entry></elementAttributes>'
        '<pos x="0" y="0"/></visualElement>'
        '</visualElements><wires/></circuit>'
    )
    spec = _spec_with_rows("t", [str(i) for i in range(12)])

    def mk_output(call_idx):
        total = call_idx + 1
        if call_idx < 6:
            return (0, "t: passed\n")
        if call_idx < 11:
            pct = round(100 * 1 / total)
            return (0, f"t: failed ({pct}%)\n")
        pct = round(100 * 2 / total)
        return (0, f"t: failed ({pct}%)\n")

    call_counter = {"i": 0}
    def fake_run(*a, **kw):
        out = mk_output(call_counter["i"])
        call_counter["i"] += 1
        return out

    with patch("dlc.testing.runner.run_digital_cli", side_effect=fake_run):
        with patch("dlc.testing.runner.find_digital_jar",
                   return_value="/fake/Digital.jar"):
            results = per_row_run(spec, str(src))

    expected = ["passed"]*6 + ["failed"] + ["passed"]*4 + ["failed"]
    assert [r.status for r in results] == expected