"""Mode B row proposer: ONE hidden model call, grounded on the
coverage report, proposing non-redundant new test rows.

Trust boundary: the model's output is treated as UNTRUSTED text. Every
proposed row is (a) parsed from a strict JSON envelope, (b) validated
against the target testcase's real header with the oracle's validator,
and (c) deduplicated token-by-token against the rows the testcase already
contains and against the other proposals. Only survivors reach the UI,
and none of them touches a file until the student accepts them — at which
point /api/l3/inject machine-verifies the lot on a temp copy.

The prompt (prompts/l3_coverage_proposer_v1.txt) carries the v1 DRAFT of
the F13 spoiler-guard: test rows only, gap-naming "why", no fix
talk. The full guard rules + tests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dlc.l3.coverage import TreeCoverageReport, scan_tree_coverage
from dlc.l3.oracle import InjectedRow, validate_rows
from dlc.llm.client import DEFAULT_MODEL, call_llm
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import _tokenize, extract_test_specs

_PROMPT_DIR = Path(__file__).parent.parent.parent / "prompts"
_PROMPT_NAME = "l3_coverage_proposer_v1.txt"

_MAX_EXISTING_ROWS_SHOWN = 40    # keep the prompt lean on loop-heavy specs
_MAX_TOTAL_ROWS = 6              # hard cap across all accepted proposals


def _load_prompt() -> str:
    return (_PROMPT_DIR / _PROMPT_NAME).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Grounding: which files can receive rows, and what do they look like
# ---------------------------------------------------------------------------

def build_targets(report: TreeCoverageReport) -> list[dict]:
    """One entry per injectable circuit in the tree (has a testcase and a
    parseable file): headers, IO widths, and existing rows — everything the
    model needs to write a legal, non-redundant row."""
    targets: list[dict] = []
    for cov in report.circuits:
        if not cov.has_testcases or not cov.path:
            continue
        try:
            circuit = parse_dig_file(cov.path)
            specs = extract_test_specs(circuit)
        except Exception:
            continue
        if not specs:
            continue
        spec = specs[0]              # Mode B injects into the first testcase
        existing = [r.raw.strip() for r in spec.rows if not r.is_malformed]
        shown = existing[:_MAX_EXISTING_ROWS_SHOWN]
        targets.append({
            "file": cov.file,
            "spec_name": spec.name,
            "headers": list(spec.headers),
            "inputs": [{"label": c.label, "bits": c.bit_width()}
                       for c in circuit.inputs() if c.label],
            "outputs": [{"label": c.label, "bits": c.bit_width()}
                        for c in circuit.outputs() if c.label],
            "existing_rows": shown,
            "existing_rows_omitted": max(0, len(existing) - len(shown)),
        })
    return targets


def build_prompt(report: TreeCoverageReport, targets: list[dict]) -> str:
    template = _load_prompt()
    # token replace (not str.format): the template and the JSON both
    # contain literal braces, and Charles hand-edits the prompt file.
    slim = report.to_dict()
    for c in slim["circuits"]:
        c.pop("flags", None)         # clean scans have none; drop the field
        c.pop("path", None)          # never leak local filesystem paths
    return (template
            .replace("<<REPORT_JSON>>", json.dumps(slim, indent=1))
            .replace("<<TARGETS_JSON>>", json.dumps(targets, indent=1)))


# ---------------------------------------------------------------------------
# Untrusted-output handling
# ---------------------------------------------------------------------------

def parse_proposals(text: str) -> list[dict]:
    """Extract the proposals list from the model's reply. Tolerates prose
    or code fences around ONE JSON object; returns [] when nothing sane."""
    if not text:
        return []
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    raw = obj.get("proposals")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        rows = p.get("rows")
        if not isinstance(rows, list):
            continue
        rows = [r.strip() for r in rows if isinstance(r, str) and r.strip()]
        if not rows:
            continue
        out.append({
            "file": str(p.get("file", "")),
            "spec_name": str(p.get("spec_name", "")),
            "rows": rows,
            "why": str(p.get("why", "")).strip(),
        })
    return out


def _row_key(raw: str, headers: list[str]) -> tuple:
    """Comparison key for redundancy: token VALUES, not spelling — `10`,
    `0xA` and `0b1010` are the same row cell."""
    cells = raw.split("#", 1)[0].split()
    key = []
    for cell in cells[:len(headers)]:
        tok = _tokenize(cell)
        key.append(("v", tok.value) if tok.kind == "int" else ("r", tok.raw.upper()))
    return tuple(key)


def validate_and_dedupe(
    proposals: list[dict], targets: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split the model's proposals into (valid, rejected). Rejected entries
    carry a `reason`; valid rows are legal for their spec, non-duplicate,
    and capped at _MAX_TOTAL_ROWS across all groups."""
    by_file = {t["file"]: t for t in targets}
    seen: dict[str, set] = {t["file"]: set() for t in targets}
    for t in targets:
        for raw in t["existing_rows"]:
            seen[t["file"]].add(_row_key(raw, t["headers"]))

    valid: list[dict] = []
    rejected: list[dict] = []
    total = 0
    for p in proposals:
        t = by_file.get(p["file"])
        if t is None or p["spec_name"] != t["spec_name"]:
            rejected.append({**p, "reason": "unknown target file or testcase"})
            continue
        good_rows: list[str] = []
        bad: list[tuple[str, str]] = []
        for raw in p["rows"]:
            if total + len(good_rows) >= _MAX_TOTAL_ROWS:
                bad.append((raw, f"over the {_MAX_TOTAL_ROWS}-row cap"))
                continue
            try:
                # header-shape + token legality, same validator inject uses
                _validate_against_headers(raw, t["headers"], p["spec_name"])
            except ValueError as exc:
                bad.append((raw, str(exc)))
                continue
            k = _row_key(raw, t["headers"])
            if k in seen[p["file"]]:
                bad.append((raw, "duplicate of an existing or proposed row"))
                continue
            seen[p["file"]].add(k)
            good_rows.append(raw)
        if good_rows:
            total += len(good_rows)
            valid.append({**p, "rows": good_rows})
        for raw, reason in bad:
            rejected.append({"file": p["file"], "spec_name": p["spec_name"],
                             "rows": [raw], "why": p.get("why", ""),
                             "reason": reason})
    return valid, rejected


def _validate_against_headers(raw: str, headers: list[str], spec_name: str) -> None:
    """Reuse the oracle's row validator without re-parsing the file: build a
    minimal spec-shaped object carrying just the headers."""
    class _HeaderOnly:
        pass
    shim = _HeaderOnly()
    shim.headers = list(headers)
    shim.name = spec_name
    validate_rows(shim, [InjectedRow(raw=raw)])


# ---------------------------------------------------------------------------
# The one hidden call
# ---------------------------------------------------------------------------

def propose_rows(
    dig_path: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    call=None,
) -> dict:
    """Scan → prompt → ONE model call → parse/validate/dedupe.

    Returns {ok, proposals, rejected, model, error, notes}. `call` is
    injectable (and resolved at RUN time, so tests may also monkeypatch
    module-level call_llm) — tests never touch the network. A scan that
    still has disagreements refuses to propose (the UI redirects to
    Mode A first).
    """
    if call is None:
        call = call_llm
    report = scan_tree_coverage(dig_path)
    if report.total_flags > 0:
        return {"ok": False, "proposals": [], "rejected": [],
                "model": None,
                "error": ("Tests and circuit still disagree somewhere — "
                          "resolve that before asking for new rows."),
                "notes": []}
    targets = build_targets(report)
    if not targets:
        return {"ok": False, "proposals": [], "rejected": [],
                "model": None,
                "error": "No testcase anywhere in this tree to extend.",
                "notes": []}

    prompt = build_prompt(report, targets)
    used_model = model or DEFAULT_MODEL
    resp = call(prompt, model=used_model, max_tokens=1500)
    if not resp.get("ok"):
        return {"ok": False, "proposals": [], "rejected": [],
                "model": used_model,
                "error": resp.get("error") or "Model call failed.",
                "notes": []}

    proposals = parse_proposals(resp.get("text") or "")
    if not proposals:
        return {"ok": True, "proposals": [], "rejected": [],
                "model": used_model, "error": None,
                "notes": ["The model proposed nothing usable this time — "
                          "run it again or add rows by hand."]}
    valid, rejected = validate_and_dedupe(proposals, targets)
    notes = []
    if rejected:
        notes.append(f"{sum(len(r['rows']) for r in rejected)} proposed "
                     f"row(s) were dropped by the validator.")
    return {"ok": True, "proposals": valid, "rejected": rejected,
            "model": used_model, "error": None, "notes": notes}
