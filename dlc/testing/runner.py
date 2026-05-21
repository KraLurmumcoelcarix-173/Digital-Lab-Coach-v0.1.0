"""
F4: Digital subprocess runner, per-row pass/fail via repeated CLI calls.

Digital's CLI test mode runs an entire Testcase's rows at once and
reports `<name>: passed` / `<name>: failed (N%)`. It does NOT identify
which specific rows failed. To get per-row pass/fail, we:

  1. Take the original .dig file.
  2. For each row of the target Testcase, write a temp .dig with the
     Testcase's `<dataString>` replaced by a SINGLE row (the header
     line + that one data row).
  3. Invoke Digital: `java -cp <jar> CLI test -circ <temp>.dig`.
  4. Parse the CLI output (Stage 3) — pass or fail of THAT row.
  5. Aggregate into a list of PerRowResult.

Locating Digital.jar:
  - `DIGITAL_JAR` env var takes precedence.
  - Otherwise, a small list of common install paths is probed.
  - If still not found, the runner returns "error" results across the
    board with a clear message — the L3 prompt builder treats this as
    "we only have overall results" and proceeds.

Limitation and Cost: 

- One Java subprocess per row. For multiple tests, 
this can be slow but tractable. We surface error states for
"jar missing", "java missing", "timeout", "parse failure" so the
caller can degrade gracefully.

- Multi-Testcase .dig files: we use a regex substitution on the FIRST
`<dataString>...</dataString>` block. 
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from dlc.testing.results import parse_cli_output
from dlc.testing.run import PerRowResult
from dlc.testing.spec import TestSpec


_DATASTRING_RE = re.compile(r'(<dataString>).*?(</dataString>)', flags=re.DOTALL)



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


def run_digital_cli(
    dig_path: str,
    jar_path: str,
    timeout: float = 30.0,
) -> tuple[int, str]:
    
    cmd = ["java", "-cp", jar_path, "CLI", "test", "-circ", dig_path]
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
    """Build a temp .dig that contains exactly one row in its Testcase."""
    src = Path(original_dig_path).read_text()
    new_body = " ".join(headers) + "\n" + row_raw
    new_content, count = _DATASTRING_RE.subn(
        lambda m: m.group(1) + new_body + m.group(2),
        src,
        count=1,
    )
    if count == 0:
        new_content = src
    fd, path = tempfile.mkstemp(suffix=".dig", prefix="dlc_row_")
    with os.fdopen(fd, "w") as f:
        f.write(new_content)
    return path


# Public API

def per_row_run(
    spec: TestSpec,
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 30.0,
) -> list[PerRowResult]:

    if jar_path is None:
        jar_path = find_digital_jar()
    if jar_path is None:
        msg = (
            "Digital.jar not found. Set the DIGITAL_JAR env var to your "
            "Digital.jar path (download from "
            "https://github.com/hneemann/Digital/releases)."
        )
        return [
            PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status="error", error_message=msg,
            )
            for row in spec.rows
        ]

    results: list[PerRowResult] = []
    for row in spec.rows:
        if row.is_malformed:
            results.append(PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status="no_run", error_message="malformed row",
            ))
            continue
        if any(t.kind == "loop_expr" for t in row.values):
            results.append(PerRowResult(
                spec_name=spec.name, row_index=row.line_index,
                status="no_run",
                error_message="row contains loop expression; expansion not implemented",
            ))
            continue

        temp_path = _write_single_row_dig(
            original_dig_path, spec.headers, row.raw,
        )
        try:
            code, output = run_digital_cli(temp_path, jar_path, timeout=timeout)
            if code == -1:
                results.append(PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error", error_message="Digital CLI timed out",
                    raw_output=output,
                ))
                continue
            if code == -2:
                results.append(PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error", error_message="java not on PATH",
                    raw_output=output,
                ))
                continue
            if code < 0:
                results.append(PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error", error_message=output,
                ))
                continue
            run = parse_cli_output(output)
            tc = run.by_name().get(spec.name)
            if tc is None and len(run.testcases) == 1:
                tc = run.testcases[0]
            if tc is None:
                names_seen = ", ".join(t.name for t in run.testcases) or "<none>"
                results.append(PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status="error",
                    error_message=(
                        f"Digital ran but emitted no matching result line for "
                        f"testcase {spec.name!r}; saw: {names_seen}"
                    ),
                    raw_output=output,
                ))
            else:
                results.append(PerRowResult(
                    spec_name=spec.name, row_index=row.line_index,
                    status=tc.status, raw_output=output,
                ))
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return results


def attach_per_row_results(
    test_runs: list,           
    original_dig_path: str,
    jar_path: str | None = None,
    timeout: float = 30.0,
) -> None:
    for run in test_runs:
        if not run.spec.rows:
            continue
        run.per_row_results = per_row_run(
            run.spec, original_dig_path,
            jar_path=jar_path, timeout=timeout,
        )