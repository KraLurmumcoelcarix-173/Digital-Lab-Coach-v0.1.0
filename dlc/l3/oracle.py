"""L3 oracle, part 1: temp-circuit ROW injection + per-row rerun.

Mode B's accept-flow ("inject the coach's new rows into a temp copy and
auto-rerun") and the Phase-4 coverage metric both need one primitive:

    given a .dig file, a testcase, and some new data rows,
    produce a TEMP circuit whose testcase carries the extra rows,
    run Digital per-row on it, and say which rows (old and new) pass.

Guarantees:
  * The student's original file is NEVER modified. The temp copy is
    byte-identical outside the targeted <dataString> block, so parsing,
    netlists, and every other testcase are untouched.
  * The temp file is written NEXT TO the original (same directory), so
    relative subcircuit references resolve exactly as they do for the
    original. Its name matches the ``dlc_row_*.dig`` pattern already
    covered by .gitignore, so a crashed run can't dirty the repo.
  * Injected rows are validated against the testcase header BEFORE
    anything is written: cell count must match, every cell must tokenize
    to a known kind, and loop expressions are rejected (injected rows
    must be concrete).
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import TestSpec, extract_test_specs, _tokenize, _strip_inline_comment
from dlc.testing.runner import find_digital_jar, per_row_run_auto


_DATASTRING_RE = re.compile(r"<dataString>(.*?)</dataString>", flags=re.DOTALL)
_TEMP_PREFIX = "dlc_row_l3_"   # matches the dlc_row_*.dig gitignore glob


@dataclass(frozen=True)
class InjectedRow:
    """One concrete data row to append to a testcase.

    ``raw`` is the whitespace-separated cell line exactly as it should
    appear in the dataString (an optional trailing ``# comment`` is
    allowed and ignored by Digital and by our parser alike). ``origin``
    is a provenance label carried through to the result rows so the UI
    can tell coach-proposed rows from student-typed ones.
    """

    raw: str
    origin: str = "coach"


@dataclass
class InjectionOutcome:
    """Everything the caller (Mode B flow / eval harness) needs after a rerun."""

    ok: bool
    warning: str | None = None
    temp_path: str | None = None          # populated only when keep_temp=True
    spec_name: str | None = None
    headers: list[str] = field(default_factory=list)
    # One entry per executed row of the TEMP spec, original rows first:
    # {index, raw, status, mismatches, error_message, added, origin}
    rows: list[dict] = field(default_factory=list)
    all_passed: bool | None = None        # every row (old + new)
    added_all_passed: bool | None = None  # just the injected rows (Mode B's lock signal)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_rows(spec: TestSpec, rows: list[InjectedRow]) -> None:
    """Raise ValueError unless every row is a concrete, well-formed data line
    for `spec`'s header. Run BEFORE any file is written."""
    if not spec.headers:
        raise ValueError(
            f"Testcase {spec.name!r} has no header line; cannot inject rows."
        )
    if not rows:
        raise ValueError("No rows to inject.")
    for i, row in enumerate(rows):
        raw = row.raw
        if "\n" in raw or "\r" in raw:
            raise ValueError(f"Injected row {i} must be a single line: {raw!r}")
        data = _strip_inline_comment(raw).strip()
        if not data:
            raise ValueError(f"Injected row {i} is empty after comment strip: {raw!r}")
        cells = data.split()
        if len(cells) != len(spec.headers):
            raise ValueError(
                f"Injected row {i} has {len(cells)} cells but testcase "
                f"{spec.name!r} has {len(spec.headers)} columns "
                f"({' '.join(spec.headers)}): {raw!r}"
            )
        for cell in cells:
            tok = _tokenize(cell)
            if tok.kind == "unknown":
                raise ValueError(
                    f"Injected row {i} has an unrecognized cell {cell!r}: {raw!r}"
                )
            if tok.kind == "loop_expr":
                raise ValueError(
                    f"Injected row {i} contains a loop expression {cell!r}; "
                    f"injected rows must be concrete values: {raw!r}"
                )


# ---------------------------------------------------------------------------
# Text-level injection (byte-preserving outside the target block)
# ---------------------------------------------------------------------------

def _datastring_ordinal(circuit, target_spec: TestSpec) -> int:
    """Which <dataString> block (in file order) belongs to `target_spec`.

    Testcase elements appear in `circuit.components` in document order, and
    exactly the ones carrying a Testdata attribute own a dataString block.
    """
    ordinal = 0
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Testcase" or "Testdata" not in comp.attributes:
            continue
        if idx == target_spec.component_index:
            return ordinal
        ordinal += 1
    raise ValueError(
        f"Testcase {target_spec.name!r} (component {target_spec.component_index}) "
        f"has no dataString block to inject into."
    )


def inject_rows_text(source_text: str, ordinal: int, rows: list[InjectedRow]) -> str:
    """Append `rows` to the `ordinal`-th <dataString> block of `source_text`.

    Every byte outside that block is preserved verbatim. Appended lines are
    XML-escaped (test tokens never need it, but a trailing comment might).
    """
    matches = list(_DATASTRING_RE.finditer(source_text))
    if ordinal < 0 or ordinal >= len(matches):
        raise ValueError(
            f"dataString block #{ordinal} not found "
            f"({len(matches)} block(s) in file)."
        )
    m = matches[ordinal]
    inner = m.group(1)
    appended = "\n".join(escape(r.raw) for r in rows)
    new_inner = inner + ("" if inner.endswith("\n") else "\n") + appended
    return source_text[: m.start(1)] + new_inner + source_text[m.end(1):]


def _find_spec(circuit, spec_name: str) -> TestSpec:
    specs = extract_test_specs(circuit)
    for spec in specs:
        if spec.name == spec_name:
            return spec
    names = ", ".join(repr(s.name) for s in specs) or "<none>"
    raise ValueError(f"No testcase named {spec_name!r} in this circuit; saw: {names}")


def write_temp_with_rows(
    dig_path: str,
    spec_name: str,
    rows: list[InjectedRow],
) -> tuple[str, TestSpec]:
    """Write the temp .dig (original + injected rows) next to the original.

    Returns (temp_path, original_spec). The caller owns the temp file's
    lifetime. Raises ValueError on validation/targeting problems; the
    original file is never touched.
    """
    src_path = Path(dig_path)
    circuit = parse_dig_file(str(src_path))
    spec = _find_spec(circuit, spec_name)
    validate_rows(spec, rows)
    ordinal = _datastring_ordinal(circuit, spec)

    source_text = src_path.read_text(encoding="utf-8")
    new_text = inject_rows_text(source_text, ordinal, rows)

    fd, temp_path = tempfile.mkstemp(
        suffix=".dig", prefix=_TEMP_PREFIX, dir=str(src_path.parent),
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_text)
    return temp_path, spec


# ---------------------------------------------------------------------------
# Inject + rerun (the oracle call Mode B's accept-flow makes)
# ---------------------------------------------------------------------------

def rerun_with_rows(
    dig_path: str,
    spec_name: str,
    rows: list[InjectedRow],
    *,
    jar_path: str | None = None,
    timeout: float = 60.0,
    keep_temp: bool = False,
) -> InjectionOutcome:
    """Inject `rows` into `spec_name` of a temp copy and run Digital per-row.

    Result rows mirror the /api/tests per-row payload, each tagged with
    ``added`` (was this an injected row?) and ``origin`` (its provenance).
    ``added_all_passed`` is Mode B's "you're all set!" signal. This utility
    does NOT apply the L1 gate — Mode B guarantees L1-clean upstream.
    """
    jar = jar_path or find_digital_jar()
    if jar is None:
        return InjectionOutcome(
            ok=False,
            warning=(
                "Digital.jar not configured. Open the jar picker from the "
                "toolbar to select it."
            ),
        )

    try:
        temp_path, original_spec = write_temp_with_rows(dig_path, spec_name, rows)
    except ValueError as exc:
        return InjectionOutcome(ok=False, warning=str(exc))

    n_original = original_spec.row_count()
    try:
        temp_circuit = parse_dig_file(temp_path)
        temp_spec = _find_spec(temp_circuit, spec_name)
        row_results = per_row_run_auto(
            temp_spec, temp_path, jar_path=jar, timeout=timeout,
        )

        rows_by_idx = {r.line_index: r for r in temp_spec.rows}
        payload: list[dict] = []
        any_failed = any_error = False
        added_failed = added_error = False
        for rr in row_results:
            added = rr.row_index >= n_original
            origin = rows[rr.row_index - n_original].origin if added else "original"
            src_row = rows_by_idx.get(rr.row_index)
            payload.append({
                "index": rr.row_index,
                "raw": src_row.raw if src_row else "",
                "status": rr.status,
                "error_message": rr.error_message,
                "mismatches": rr.mismatches,
                "added": added,
                "origin": origin,
            })
            if rr.status == "failed":
                any_failed = True
                added_failed |= added
            if rr.status == "error":
                any_error = True
                added_error |= added

        return InjectionOutcome(
            ok=True,
            warning=(
                "One or more rows could not be run (see status=error)."
                if any_error else None
            ),
            temp_path=temp_path if keep_temp else None,
            spec_name=spec_name,
            headers=list(temp_spec.headers),
            rows=payload,
            all_passed=(not any_failed) and (not any_error),
            added_all_passed=(not added_failed) and (not added_error),
        )
    finally:
        if not keep_temp:
            try:
                os.unlink(temp_path)
            except OSError:
                pass