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
_TEMP_PREFIX = "dlc_row_l3_"


@dataclass(frozen=True)
class InjectedRow:

    raw: str
    origin: str = "coach"


@dataclass
class InjectionOutcome:
    """Everything the caller (Mode B flow / eval harness) needs after a rerun."""

    ok: bool
    warning: str | None = None
    temp_path: str | None = None
    spec_name: str | None = None
    headers: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    all_passed: bool | None = None
    added_all_passed: bool | None = None
    spec_index: int | None = None
    base_spec: dict | None = None
    rom_program: str | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def validate_rows(spec: TestSpec, rows: list[InjectedRow]) -> None:
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


def _datastring_ordinal(circuit, target_spec: TestSpec) -> int:
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


def _is_data_line(line: str) -> bool:
    s = line.split("#", 1)[0].strip()
    return bool(s)


def inject_rows_text(source_text: str, ordinal: int, rows: list[InjectedRow],
                     insert_before: int | None = None,
                     rewrite: dict[int, str] | None = None) -> str:
    matches = list(_DATASTRING_RE.finditer(source_text))
    if ordinal < 0 or ordinal >= len(matches):
        raise ValueError(
            f"dataString block #{ordinal} not found "
            f"({len(matches)} block(s) in file)."
        )
    m = matches[ordinal]
    inner = m.group(1)
    added = [escape(r.raw) for r in rows]
    if insert_before is None and not rewrite:
        new_inner = inner + ("" if inner.endswith("\n") else "\n") + "\n".join(added)
        return source_text[: m.start(1)] + new_inner + source_text[m.end(1):]

    lines = inner.split("\n")
    out: list[str] = []
    data_idx = -1
    inserted = insert_before is None
    for line in lines:
        if _is_data_line(line):
            if data_idx >= 0:
                if not inserted and data_idx == insert_before:
                    out.extend(added)
                    inserted = True
                if rewrite and data_idx in rewrite:
                    line = escape(rewrite[data_idx])
            data_idx += 1
        out.append(line)
    if not inserted:
        while out and not out[-1].strip():
            out.pop()
        out.extend(added)
        out.append("")
    return source_text[: m.start(1)] + "\n".join(out) + source_text[m.end(1):]


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

_PROGMEM_TRUE_RE = re.compile(
    r"<string>isProgramMemory</string>\s*<boolean>true</boolean>")
_VISELEM_RE = re.compile(r"<visualElement>.*?</visualElement>", flags=re.DOTALL)
_ROMDATA_RE = re.compile(r"(<string>Data</string>\s*<data>)(.*?)(</data>)",
                         flags=re.DOTALL)
_POS_RE = re.compile(r'<pos x="(-?\d+)" y="(-?\d+)"/>')


def find_program_rom(
    source_text: str,
) -> tuple[list[int], int, tuple[int, int]] | None:
    hits = []
    for m in _VISELEM_RE.finditer(source_text):
        block = m.group(0)
        if not _PROGMEM_TRUE_RE.search(block):
            continue
        dm = _ROMDATA_RE.search(block)
        if dm is None:
            continue
        hits.append((m, dm))
    if not hits:
        return None
    if len(hits) > 1:
        raise ValueError("Several program-memory elements found; "
                         "cannot extend the program unambiguously.")
    elem_m, dm = hits[0]
    words = [int(w, 16) for w in dm.group(2).replace("\n", "").split(",")
             if w.strip()]
    ab = re.search(r"<string>AddrBits</string>\s*<int>(\d+)</int>",
                   elem_m.group(0))
    addr_bits = int(ab.group(1)) if ab else 10
    start = elem_m.start() + dm.start(2)
    end = elem_m.start() + dm.end(2)
    return words, addr_bits, (start, end)


def parse_program_words(rom_words: list[str]) -> list[int]:
    out: list[int] = []
    for w in rom_words:
        s = str(w).strip().lower().removeprefix("0x")
        if not s or not re.fullmatch(r"[0-9a-f]+", s):
            raise ValueError(f"Program word {w!r} is not a hex word.")
        v = int(s, 16)
        if v > 0xFFFFFFFF:
            raise ValueError(f"Program word {w!r} does not fit in 32 bits.")
        out.append(v)
    return out


def extend_program_rom_text(source_text: str, words: list[int],
                            insert_at: int | None = None) -> str:
    found = find_program_rom(source_text)
    if found is None:
        raise ValueError("This circuit has no program memory (ROM with "
                         "isProgramMemory) — program extension is impossible.")
    existing, addr_bits, (start, end) = found
    if len(existing) + len(words) > (1 << addr_bits):
        raise ValueError(
            f"Program would not fit: {len(existing)}+{len(words)} words "
            f"> ROM capacity {1 << addr_bits}.")
    if insert_at is None or insert_at >= len(existing):
        new_data = source_text[start:end] + "," + ",".join(f"{w:x}" for w in words)
    else:
        merged = existing[:insert_at] + list(words) + existing[insert_at:]
        new_data = ",".join(f"{w:x}" for w in merged)
    return source_text[:start] + new_data + source_text[end:]


def shift_pc_cells(rows_raw: list[str], headers: list[str], pc_col: str,
                   halt_pc: int, shift: int) -> dict[int, str]:
    if pc_col not in headers:
        return {}
    col = headers.index(pc_col)
    out: dict[int, str] = {}
    for i, raw in enumerate(rows_raw):
        body, sep, comment = raw.partition("#")
        cells = body.split()
        if col >= len(cells):
            continue
        tok = _tokenize(cells[col])
        if tok.kind != "int" or tok.value != halt_pc:
            continue
        cells[col] = f"0x{(halt_pc + shift) & 0xFFFFFFFF:X}"
        out[i] = " ".join(cells) + (f" {sep}{comment}" if sep else "")
    return out


def add_testcase_text(source_text: str, label: str, data_string: str) -> str:
    anchor = source_text.rfind("</visualElements>")
    if anchor < 0:
        raise ValueError("No <visualElements> section to add a testcase to.")
    max_y = max((int(m.group(2)) for m in _POS_RE.finditer(source_text)),
                default=0)
    elem = (
        "<visualElement>\n"
        "      <elementName>Testcase</elementName>\n"
        "      <elementAttributes>\n"
        "        <entry>\n"
        f"          <string>Label</string>\n"
        f"          <string>{escape(label)}</string>\n"
        "        </entry>\n"
        "        <entry>\n"
        "          <string>Testdata</string>\n"
        "          <testData>\n"
        f"            <dataString>{escape(data_string)}</dataString>\n"
        "          </testData>\n"
        "        </entry>\n"
        "      </elementAttributes>\n"
        f"      <pos x=\"0\" y=\"{max_y + 80}\"/>\n"
        "    </visualElement>\n    ")
    return source_text[:anchor] + elem + source_text[anchor:]


def _clock_column(circuit, spec: TestSpec) -> str | None:
    from dlc.testing.spec import match_variables_to_io
    for col, b in match_variables_to_io(spec.headers, circuit).items():
        if b is not None and b.role == "clock":
            return col
    return None


def rerun_with_second(
    dig_path: str,
    spec_name: str,
    rows: list[InjectedRow],
    rom_words: list[str] | None = None,
    *,
    suffix: str = "_second",
    jar_path: str | None = None,
    timeout: float = 60.0,
    keep_temp: bool = False,
) -> InjectionOutcome:
    jar = jar_path or find_digital_jar()
    if jar is None:
        return InjectionOutcome(
            ok=False,
            warning=("Digital.jar not configured. Open the jar picker from "
                     "the toolbar to select it."),
        )
    src_path = Path(dig_path)
    try:
        circuit = parse_dig_file(str(src_path))
        base_spec = _find_spec(circuit, spec_name)
        validate_rows(base_spec, rows)
        label = f"{spec_name}{suffix}"
        if any(s.name == label for s in extract_test_specs(circuit)):
            raise ValueError(f"A testcase named {label!r} already exists.")

        source_text = src_path.read_text(encoding="utf-8")
        words = parse_program_words(rom_words or [])
        warmups: list[str] = []
        rom_program = None
        if words:
            if len(words) != len(rows):
                raise ValueError(
                    f"Program extension needs exactly one row per word "
                    f"({len(words)} word(s), {len(rows)} row(s)).")
            found = find_program_rom(source_text)
            if found is None:
                raise ValueError("This circuit has no program memory (ROM "
                                 "with isProgramMemory).")
            clk = _clock_column(circuit, base_spec)
            if clk is None:
                raise ValueError("Program extension needs a clock column "
                                 "in the testcase header.")
            n_existing = len(found[0])
            def wrow(clkval: str) -> str:
                return " ".join(clkval if h == clk else "X"
                                for h in base_spec.headers)
            warmups = [wrow("0")] + [wrow("C")] * (n_existing - 1)
            rom_program = ",".join(f"{w:x}" for w in found[0] + words)
            source_text = extend_program_rom_text(source_text, words)

        data_string = "\n".join(
            [" ".join(base_spec.headers)] + warmups + [r.raw for r in rows])
        source_text = add_testcase_text(source_text, label, data_string)
    except ValueError as exc:
        return InjectionOutcome(ok=False, warning=str(exc))

    fd, temp_path = tempfile.mkstemp(
        suffix=".dig", prefix=_TEMP_PREFIX, dir=str(src_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(source_text)

    try:
        temp_circuit = parse_dig_file(temp_path)
        temp_specs = extract_test_specs(temp_circuit)
        second = _find_spec(temp_circuit, label)
        spec_index = next(i for i, s in enumerate(temp_specs)
                          if s.name == label)

        results = per_row_run_auto(second, temp_path, jar_path=jar,
                                   timeout=timeout)
        rows_by_idx = {r.line_index: r for r in second.rows}
        n_warm = len(warmups)
        payload: list[dict] = []
        any_bad = added_bad = False
        for rr in results:
            added = rr.row_index >= n_warm
            origin = (rows[rr.row_index - n_warm].origin
                      if added else "replay")
            src_row = rows_by_idx.get(rr.row_index)
            bad = rr.status in ("failed", "error")
            any_bad |= bad
            added_bad |= bad and added
            payload.append({
                "index": rr.row_index,
                "raw": src_row.raw if src_row else "",
                "status": rr.status,
                "error_message": rr.error_message,
                "mismatches": rr.mismatches,
                "added": added,
                "origin": origin,
            })

        base_temp = _find_spec(temp_circuit, spec_name)
        base_results = per_row_run_auto(base_temp, temp_path, jar_path=jar,
                                        timeout=timeout)
        base_passed = sum(1 for r in base_results if r.status == "passed")
        base_all = base_passed == len(base_results)

        return InjectionOutcome(
            ok=True,
            warning=("One or more rows could not be run (see status=error)."
                     if any(r.status == "error" for r in results) else None),
            temp_path=temp_path if keep_temp else None,
            spec_name=label,
            headers=list(second.headers),
            rows=payload,
            all_passed=(not any_bad) and base_all,
            added_all_passed=not added_bad,
            spec_index=spec_index,
            base_spec={"name": spec_name, "total": len(base_results),
                       "passed": base_passed, "all_passed": base_all},
            rom_program=rom_program,
        )
    finally:
        if not keep_temp:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def rerun_with_program(
    dig_path: str,
    spec_name: str,
    rows: list[InjectedRow],
    rom_words: list[str],
    *,
    jar_path: str | None = None,
    timeout: float = 60.0,
    keep_temp: bool = False,
    insert_at: int | None = None,
    insert_before_row: int | None = None,
    pc_shift: int = 0,
    pc_col: str | None = None,
) -> InjectionOutcome:
    jar = jar_path or find_digital_jar()
    if jar is None:
        return InjectionOutcome(
            ok=False,
            warning=("Digital.jar not configured. Open the jar picker from "
                     "the toolbar to select it."),
        )
    src_path = Path(dig_path)
    try:
        circuit = parse_dig_file(str(src_path))
        spec = _find_spec(circuit, spec_name)
        validate_rows(spec, rows)
        words = parse_program_words(rom_words or [])
        if not words:
            raise ValueError("Program injection needs at least one ROM word.")
        if len(words) != len(rows):
            raise ValueError(
                f"Program extension needs exactly one row per word "
                f"({len(words)} word(s), {len(rows)} row(s)).")
        source_text = src_path.read_text(encoding="utf-8")
        source_text = extend_program_rom_text(source_text, words,
                                              insert_at=insert_at)
        ordinal = _datastring_ordinal(circuit, spec)
        rewrite: dict[int, str] = {}
        if insert_at is not None and pc_shift and pc_col:
            originals = [r.raw for r in spec.rows if not r.is_malformed]
            rewrite = shift_pc_cells(originals, list(spec.headers), pc_col,
                                     4 * insert_at, pc_shift)
        source_text = inject_rows_text(
            source_text, ordinal, rows,
            insert_before=insert_before_row if insert_at is not None else None,
            rewrite=rewrite or None)
        rom_program = ",".join(f"{w:x}" for w in find_program_rom(source_text)[0])
    except ValueError as exc:
        return InjectionOutcome(ok=False, warning=str(exc))

    fd, temp_path = tempfile.mkstemp(
        suffix=".dig", prefix=_TEMP_PREFIX, dir=str(src_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(source_text)

    n_original = spec.row_count()
    first_added = (insert_before_row
                   if insert_at is not None and insert_before_row is not None
                   else n_original)
    try:
        temp_circuit = parse_dig_file(temp_path)
        temp_specs = extract_test_specs(temp_circuit)
        temp_spec = _find_spec(temp_circuit, spec_name)
        spec_index = next(i for i, s in enumerate(temp_specs)
                          if s.name == spec_name)
        results = per_row_run_auto(temp_spec, temp_path, jar_path=jar,
                                   timeout=timeout)

        rows_by_idx = {r.line_index: r for r in temp_spec.rows}
        payload: list[dict] = []
        any_bad = added_bad = False
        base_total = base_passed = 0
        for rr in results:
            added = first_added <= rr.row_index < first_added + len(rows)
            origin = (rows[rr.row_index - first_added].origin
                      if added else "original")
            src_row = rows_by_idx.get(rr.row_index)
            bad = rr.status in ("failed", "error")
            any_bad |= bad
            added_bad |= bad and added
            if not added:
                base_total += 1
                base_passed += rr.status == "passed"
            payload.append({
                "index": rr.row_index,
                "raw": src_row.raw if src_row else "",
                "status": rr.status,
                "error_message": rr.error_message,
                "mismatches": rr.mismatches,
                "added": added,
                "origin": origin,
            })

        return InjectionOutcome(
            ok=True,
            warning=("One or more rows could not be run (see status=error)."
                     if any(r.status == "error" for r in results) else None),
            temp_path=temp_path if keep_temp else None,
            spec_name=spec_name,
            headers=list(temp_spec.headers),
            rows=payload,
            all_passed=not any_bad,
            added_all_passed=not added_bad,
            spec_index=spec_index,
            base_spec={"name": spec_name, "total": base_total,
                       "passed": base_passed,
                       "all_passed": base_passed == base_total},
            rom_program=rom_program,
        )
    finally:
        if not keep_temp:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def rerun_with_rows(
    dig_path: str,
    spec_name: str,
    rows: list[InjectedRow],
    *,
    jar_path: str | None = None,
    timeout: float = 60.0,
    keep_temp: bool = False,
) -> InjectionOutcome:
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