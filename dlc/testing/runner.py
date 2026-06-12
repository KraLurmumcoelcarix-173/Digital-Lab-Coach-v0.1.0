"""
F4: Digital subprocess runner — per-row pass/fail, two strategies.

STRATEGY 1 (default): FAST single-invocation run, `per_file_run_fast`.

Digital's `CLI test -verbose` prints, under every FAILED testcase, its
own value table: one line per executed row in execution order, with
each mismatched output cell rendered as `E: <expected> / F: <found>`
(see dlc/testing/results.py for the full grammar). Because Digital
expands `loop(...)` blocks and sequences clock (`C`) rows exactly the
way DLC's TestSpec does — one table line per expanded spec row — table
line i corresponds 1:1 to spec.rows[i]. So ONE subprocess call yields:

  - per-row pass/fail for every testcase in the file (passed testcase
    => all its rows passed; failed testcase => rows with E:/F: cells
    failed), and
  - the exact expected-vs-found cells per failing row (PerRowResult
    .mismatches) — which is precisely what Layer 3 needs.

Digital sequences stateful rows internally, so registers/pipelines are
correct without any cumulative re-running. Wallclock: one JVM startup
(~1 s) regardless of row count, vs N startups for strategy 2.

STRATEGY 2 (fallback + reference): CUMULATIVE per-row run,
`per_row_run` / `per_row_run_iter` — one Digital call per row K with
the dataString replaced by rows 0..K (cumulative prefix keeps stateful
circuits correct). Row K's pass/fail is inferred from the delta in
Digital's failure percentage between prefix K-1 and prefix K.

The fast strategy falls back to the cumulative one, per testcase, when
the 1:1 row mapping cannot be trusted:
  - the spec has unexpanded loop expressions or malformed rows (DLC and
    Digital would disagree on the executed row list),
  - the verbose table is missing or doesn't split into header-width
    cells (older/newer Digital builds),
  - the echoed table row count != the spec row count.
`per_row_run_auto` packages exactly that policy for one spec.

Locating Digital.jar:
  - `DIGITAL_JAR` env var takes precedence.
  - Otherwise the saved config, then common install paths, are probed.
  - If still not found, the runner returns "error" results across the
    board with a clear message — the L3 prompt builder treats this as
    "we only have overall results" and proceeds.

Other notes:

- We surface error states for "jar missing", "java missing",
  "timeout", "parse failure" so callers can degrade gracefully.
- Multi-Testcase .dig files: the cumulative strategy regex-substitutes
  the FIRST `<dataString>...</dataString>` block only; the fast
  strategy handles every testcase in the file in the single call.
"""

import atexit
import time
import os
import re
import subprocess
import tempfile
from pathlib import Path

from dlc.testing.results import parse_cli_output, parse_cli_output_verbose
from dlc.testing.run import PerRowResult
from dlc.testing.spec import TestSpec


_DATASTRING_RE = re.compile(r'(<dataString>).*?(</dataString>)', flags=re.DOTALL)
_PENDING_CLEANUP: list[str] = []


def _safe_unlink(path: str) -> None:
    """Remove a temp file"""
    for _ in range(3):
        try:
            os.unlink(path)
            return
        except OSError:
            time.sleep(0.05)
    _PENDING_CLEANUP.append(path)


def _cleanup_pending() -> None:
    for p in list(_PENDING_CLEANUP):
        try:
            if os.path.exists(p):
                os.unlink(p)
            _PENDING_CLEANUP.remove(p)
        except OSError:
            pass

atexit.register(_cleanup_pending)

def find_digital_jar() -> str | None:
    env = os.environ.get("DIGITAL_JAR")
    if env and Path(env).exists():
        return env

    from dlc.testing.config import get_configured_jar
    cfg_path = get_configured_jar()
    if cfg_path is not None:
        return cfg_path

    home = Path.home()
    candidates = [
        "/usr/local/share/Digital/Digital.jar",
        "/usr/share/Digital/Digital.jar",
        "/opt/Digital/Digital.jar",
        "/Applications/Digital.app/Contents/Java/Digital.jar",
        str(Path.home() / "Digital" / "Digital.jar"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None

def ensure_digital_jar(interactive: bool = True) -> str | None:
    """Locate Digital.jar; on miss, optionally prompt the student via a
    native file dialog.
    """
    p = find_digital_jar()
    if p is not None:
        return p
    if not interactive:
        return None
    from dlc.testing.config import prompt_for_jar_path
    return prompt_for_jar_path()        

def run_digital_cli(
    dig_path: str,
    jar_path: str,
    timeout: float = 30.0,
    verbose: bool = False,
) -> tuple[int, str]:

    cmd = ["java", "-cp", jar_path, "CLI", "test", "-circ", dig_path]
    if verbose:
        cmd.append("-verbose")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "subprocess timeout"
    except FileNotFoundError:
        return -2, "java not on PATH"
    except OSError as e:
        return -3, f"OS error: {e}"

def _write_single_row_dig(
    original_dig_path: str,
    headers: list[str],
    row_raw: str,
) -> str:
    """..."""
    src_path = Path(original_dig_path)
    # .dig files are UTF-8 XML; explicit encoding both ways or Windows
    # GBK locales raise localized UnicodeDecodeError/UnicodeEncodeError
    # on any non-ASCII label/comment (docs anti-pattern 4.3).
    src = src_path.read_text(encoding="utf-8")
    new_body = " ".join(headers) + "\n" + row_raw
    new_content, count = _DATASTRING_RE.subn(
        lambda m: m.group(1) + new_body + m.group(2),
        src,
        count=1,
    )
    if count == 0:
        new_content = src
    fd, path = tempfile.mkstemp(
        suffix=".dig", prefix="dlc_row_", dir=str(src_path.parent),
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_content)
    return path


# Public API

def per_row_run_iter(
    spec: TestSpec,
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 30.0,
):
    if jar_path is None:
        jar_path = find_digital_jar()
    if jar_path is None:
        msg = (
            "Digital.jar not found. Set the DIGITAL_JAR env var or save "
            "the path via dlc.testing.config.set_digital_jar_path()."
        )
        for row in spec.rows:
            yield PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status="error", error_message=msg,
            )
        return

    prev_fail_count = 0
    runnable_so_far: list[str] = []

    for row in spec.rows:
        if row.is_malformed:
            yield PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status="no_run", error_message="malformed row",
            )
            continue
        if any(t.kind == "loop_expr" for t in row.values):
            yield PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status="no_run",
                error_message="row contains loop expression; expansion not implemented",
            )
            continue

        runnable_so_far.append(row.raw)
        prefix_text = "\n".join(runnable_so_far)
        temp_path = _write_single_row_dig(
            original_dig_path, spec.headers, prefix_text,
        )
        try:
            code, output = run_digital_cli(temp_path, jar_path, timeout=timeout)
            if code == -1:
                yield PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error", error_message="Digital CLI timed out",
                    raw_output=output,
                )
                prev_fail_count = 0
                continue
            if code == -2:
                yield PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error", error_message="java not on PATH",
                    raw_output=output,
                )
                prev_fail_count = 0
                continue
            if code < 0:
                yield PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error", error_message=output,
                )
                prev_fail_count = 0
                continue

            run = parse_cli_output(output)
            tc = run.by_name().get(spec.name)
            if tc is None and len(run.testcases) == 1:
                tc = run.testcases[0]
            if tc is None:
                names_seen = ", ".join(t.name for t in run.testcases) or "<none>"
                yield PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error",
                    error_message=(
                        f"Digital ran but emitted no matching result line for "
                        f"testcase {spec.name!r}; saw: {names_seen}"
                    ),
                    raw_output=output,
                )
                continue

            total = len(runnable_so_far)
            if tc.status == "passed":
                cur_fail_count = 0
            else:
                pct = tc.fail_pct or 0
                cur_fail_count = round(total * pct / 100)

            row_status = "failed" if cur_fail_count > prev_fail_count else "passed"
            yield PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status=row_status, raw_output=output,
            )
            prev_fail_count = cur_fail_count
        finally:
            _safe_unlink(temp_path)


def per_row_run(
    spec: TestSpec,
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 30.0,
) -> list[PerRowResult]:
    return list(per_row_run_iter(
        spec, original_dig_path, jar_path=jar_path, timeout=timeout,
    ))

def attach_per_row_results(
    test_runs: list,
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 30.0,
) -> None:
    for run in test_runs:
        if not run.spec.rows:
            continue
        run.per_row_results = per_row_run_auto(
            run.spec, original_dig_path,
            jar_path=jar_path, timeout=timeout,
        )


# Fast single-invocation strategy

def _spec_is_fast_safe(spec: TestSpec) -> bool:
    """True when DLC's expanded row list provably matches what Digital
    will execute (the precondition for the 1:1 table mapping)."""
    if spec.has_unexpanded_loops:
        return False
    if any(row.is_malformed for row in spec.rows):
        return False
    return bool(spec.rows)

def _error_rows(spec: TestSpec, msg: str, raw: str | None = None) -> list[PerRowResult]:
    return [
        PerRowResult(
            spec_name=spec.name, row_index=row.line_index,
            status="error", error_message=msg, raw_output=raw,
        )
        for row in spec.rows
    ]

def _map_section_to_rows(spec: TestSpec, section) -> list[PerRowResult] | None:
    """Map one VerboseSection onto spec.rows. None => not trustworthy,
    caller should fall back to the cumulative strategy."""
    if section.status == "passed":
        return [
            PerRowResult(spec_name=spec.name, row_index=row.line_index,
                         status="passed")
            for row in spec.rows
        ]
    if section.status == "error":
        return _error_rows(spec, section.error_message or "Digital reported an error")
    # status == "failed": need a clean table of exactly len(spec.rows) lines.
    if not section.table_ok:
        return None
    if section.headers != spec.headers:
        return None
    if len(section.row_lines) != len(spec.rows):
        return None
    out: list[PerRowResult] = []
    for row, failed, line, mism in zip(
        spec.rows, section.row_failed, section.row_lines,
        section.row_mismatches,
    ):
        out.append(PerRowResult(
            spec_name=spec.name, row_index=row.line_index,
            status="failed" if failed else "passed",
            raw_output=line if failed else None,
            mismatches=mism if failed else None,
        ))
    return out

def per_file_run_fast(
    specs: list[TestSpec],
    dig_path: str,
    jar_path: str | None = None,
    timeout: float = 60.0,
) -> tuple[dict[str, list[PerRowResult]], list[TestSpec]]:
    """ONE `CLI test -verbose` call covering every testcase in the file.

    Returns (results_by_spec_name, fallback_specs). Specs in
    fallback_specs got no trustworthy mapping — run them through the
    cumulative strategy (or accept overall-only results).
    """
    if jar_path is None:
        jar_path = find_digital_jar()
    if jar_path is None:
        msg = (
            "Digital.jar not found. Set the DIGITAL_JAR env var or save "
            "the path via dlc.testing.config.set_digital_jar_path()."
        )
        return {s.name: _error_rows(s, msg) for s in specs}, []

    code, output = run_digital_cli(dig_path, jar_path, timeout=timeout,
                                   verbose=True)
    if code == -1:
        return {s.name: _error_rows(s, "Digital CLI timed out", output)
                for s in specs}, []
    if code == -2:
        return {s.name: _error_rows(s, "java not on PATH", output)
                for s in specs}, []
    if code == -3:
        return {s.name: _error_rows(s, output) for s in specs}, []

    sections = parse_cli_output_verbose(
        output, known_names={s.name for s in specs},
    )
    results: dict[str, list[PerRowResult]] = {}
    fallback: list[TestSpec] = []
    for spec in specs:
        section = sections.get(spec.name)
        if section is None and len(specs) == 1 and len(sections) == 1:
            # Single testcase whose label Digital renders differently
            # (e.g. unnamed) — same tolerance the slow path applies.
            section = next(iter(sections.values()))
        if section is None:
            names_seen = ", ".join(sections) or "<none>"
            results[spec.name] = _error_rows(
                spec,
                f"Digital ran but emitted no matching result line for "
                f"testcase {spec.name!r}; saw: {names_seen}",
                output,
            )
            continue
        if not _spec_is_fast_safe(spec):
            fallback.append(spec)
            continue
        mapped = _map_section_to_rows(spec, section)
        if mapped is None:
            fallback.append(spec)
            continue
        results[spec.name] = mapped
    return results, fallback

def per_row_run_fast(
    spec: TestSpec,
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 60.0,
) -> list[PerRowResult] | None:
    """Fast strategy for ONE spec. None => fall back to cumulative."""
    results, fallback = per_file_run_fast(
        [spec], original_dig_path, jar_path=jar_path, timeout=timeout,
    )
    if spec.name in results:
        return results[spec.name]
    return None

def per_row_run_auto(
    spec: TestSpec,
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 30.0,
) -> list[PerRowResult]:
    """Fast strategy first; cumulative when the mapping can't be trusted."""
    fast = per_row_run_fast(
        spec, original_dig_path, jar_path=jar_path,
        timeout=max(timeout, 60.0),
    )
    if fast is not None:
        return fast
    return per_row_run(
        spec, original_dig_path, jar_path=jar_path, timeout=timeout,
    )

