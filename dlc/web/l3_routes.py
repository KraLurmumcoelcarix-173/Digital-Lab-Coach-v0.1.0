"""Layer-3 web endpoints, kept in their own APIRouter.

server.py stays the single FastAPI app; every L3 endpoint (Mode B coverage
now; Mode A debug, row injection, fix verify later) registers here so L3
rounds never have to touch the big server module again.

Session helpers are reached through the server MODULE at request time
(`server._resolve_target(...)`), not imported by name: server.py imports this
module while it is itself still initializing, so name imports would see a
half-built module — attribute lookup at call time is always safe.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from dlc.l3 import limits
from dlc.l3.coverage import scan_tree_coverage
from dlc.l3.oracle import (
    InjectedRow,
    rerun_with_program,
    rerun_with_rows,
    rerun_with_second,
)
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs

router = APIRouter()


class CoverageRequest(BaseModel):
    session_id: str
    filename: str


@router.post("/api/l3/coverage")
def l3_coverage(req: CoverageRequest) -> dict:
    """Mode B's deterministic pass: tree-wide wrong-test scan + coverage
    report for the selected file.

    Synchronous on purpose — the whole lab5 cpu tree scans in under a
    second, and the evaluator never shells out to Digital.jar. The response
    is TreeCoverageReport.to_dict() plus an `ok` flag; scan-level problems
    (unparseable file, unresolved children, evaluator errors) surface as
    `notes` inside the report, never as HTTP errors. Unknown session or
    filename still 404s like every other endpoint.

    Use limits : a clean scan consumes one Mode B use;
    a scan that finds disagreements is a REDIRECT to Mode A and is free.
    Counters always tick; the block applies only under DLC_ENFORCE_LIMITS.
    The L1 lock stays a board-UI concern.
    """
    from dlc.web import server   # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)
    if not limits.allowed("modeB"):
        return {
            "ok": False,
            "limited": True,
            "warning": "Daily Coverage Coach limit reached — try again tomorrow.",
            "limits": limits.state(),
        }
    try:
        report = scan_tree_coverage(target["path"])
    except Exception as exc:     # defense in depth; the scan shouldn't raise
        return {
            "ok": False,
            "warning": f"Coverage scan failed: {type(exc).__name__}: {exc}",
        }
    consumed = report.total_flags == 0     # disagreements => free redirect
    lim = limits.consume("modeB") if consumed else limits.state()
    return {
        "ok": True,
        "warning": None,
        "consumed_use": consumed,
        "limits": lim,
        **report.to_dict(),
    }


class ProposeRequest(BaseModel):
    session_id: str
    filename: str
    model: str | None = None


@router.post("/api/l3/propose")
def l3_propose(req: ProposeRequest) -> dict:
    """Mode B's ONE hidden model call: propose non-redundant new test
    rows grounded on the fresh coverage report. Proposing is free — the
    Mode B daily use was already consumed by the clean scan that unlocks
    this button. Every returned row has passed the deterministic validator
    (legal shape, non-duplicate); nothing is written anywhere until the
    student accepts, which goes through /api/l3/inject's machine-verify.
    """
    from dlc.l3 import proposer      # late import keeps startup lean
    from dlc.web import server       # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)
    try:
        return proposer.propose_rows(target["path"], model=req.model)
    except Exception as exc:         # defense in depth
        return {"ok": False, "proposals": [], "rejected": [],
                "model": req.model, "notes": [],
                "error": f"Proposer failed: {type(exc).__name__}: {exc}"}


class InjectRequest(BaseModel):
    session_id: str
    filename: str
    rows: list[str] = []
    spec_name: str | None = None    # default: the file's first testcase
    origin: str = "coach"           # provenance tag carried to result rows
    # Program-driven targets: rom_words extend the program ROM (one row per
    # word). 2.11 default: the rows are APPENDED to the official testcase on
    # the temp copy — state carries over from the official rows, which re-run
    # ahead of the new ones as the regression guard (response carries
    # base_spec). as_second=True forces the ISOLATED path instead: a fresh
    # '<spec>_second' testcase with machine-generated warm-up replay rows,
    # for rows that must not run under the official rows' end state.
    as_second: bool = False
    rom_words: list[str] = []


@router.post("/api/l3/inject")
def l3_inject(req: InjectRequest) -> dict:
    """Mode B's accept-flow: inject rows into a TEMP copy of the
    selected file and re-run its testcase per-row through the real Digital
    runner. The student's original file is never touched.

    The temp file is kept and registered into the session under
    '<stem>__coach.dig' (replacing any previous coach temp for the same
    file), so /api/simulate, /api/tests and Mode A can target it — that IS
    the ratified "Mode A operates on the CURRENT TEMP CIRCUIT" hand-off.

    Response = InjectionOutcome.to_dict() plus:
      outcome:  "all_set"   — every row (old + new) passes; Mode B's lock signal
                "rows_fail" — some row fails; UI pushes these to Mode A
                "error"     — validation / jar / runner problem (see warning)
      temp_filename: the session name the temp file was registered under.
    Injection consumes no Mode B use — the use was counted by the clean
    coverage scan that produced these proposals.
    """
    from dlc.web import server   # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)

    spec_name = req.spec_name
    if spec_name is None:
        try:
            specs = extract_test_specs(parse_dig_file(target["path"]))
        except Exception as exc:
            return {"ok": False, "outcome": "error",
                    "warning": f"Could not parse circuit: {exc}"}
        if not specs:
            return {"ok": False, "outcome": "error",
                    "warning": "This file has no testcase to inject into."}
        spec_name = specs[0].name

    rows = [InjectedRow(raw=r, origin=req.origin or "coach")
            for r in req.rows if isinstance(r, str)]
    if req.as_second:
        outcome = rerun_with_second(
            target["path"], spec_name, rows, req.rom_words, keep_temp=True,
        )
    elif req.rom_words:
        outcome = rerun_with_program(
            target["path"], spec_name, rows, req.rom_words, keep_temp=True,
        )
    else:
        outcome = rerun_with_rows(
            target["path"], spec_name, rows, keep_temp=True,
        )
    body = outcome.to_dict()
    if not outcome.ok:
        return {**body, "outcome": "error", "temp_filename": None}

    # Register the temp copy in the session (replace any previous one).
    temp_filename = f"{Path(req.filename).stem}__coach.dig"
    session = server._SESSIONS.get(req.session_id)
    if session is not None and outcome.temp_path:
        for f in list(session["files"]):
            if f["name"] == temp_filename:
                session["files"].remove(f)
                if f["path"] != outcome.temp_path:
                    try:
                        os.remove(f["path"])
                    except OSError:
                        pass
        session["files"].append(
            {"name": temp_filename, "path": outcome.temp_path},
        )
        session["l3_temp"] = {
            "for": req.filename,
            "name": temp_filename,
            "path": outcome.temp_path,
            # for as_second this is '<spec>_second' — Mode A targets it
            "spec_name": outcome.spec_name or spec_name,
        }

    return {
        **body,
        "outcome": "all_set" if outcome.all_passed else "rows_fail",
        "temp_filename": temp_filename,
    }


class UninjectRequest(BaseModel):
    session_id: str
    filename: str


@router.post("/api/l3/uninject")
def l3_uninject(req: UninjectRequest) -> dict:
    from dlc.web import server   

    server._resolve_target(req.session_id, req.filename)   # 404 on unknown
    temp_filename = f"{Path(req.filename).stem}__coach.dig"
    session = server._SESSIONS.get(req.session_id)
    removed = False
    if session is not None:
        for f in list(session["files"]):
            if f["name"] == temp_filename:
                session["files"].remove(f)
                removed = True
                try:
                    os.remove(f["path"])
                except OSError:
                    pass
        lt = session.get("l3_temp")
        if lt and lt.get("name") == temp_filename:
            session["l3_temp"] = None
    return {"ok": True, "removed": removed, "temp_filename": temp_filename}