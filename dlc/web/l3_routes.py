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
    a scan that finds disagreements is a REDIRECT to Mode A and is free,
    and a scan stopped by the select-coverage gate (Case 3.B — an
    input-driven mux select value no row exercises) is free as well.
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
    # disagreements => free redirect to Mode A; a select-coverage gate
    # (Case 3.B) is a free redirect too — the student owes rows, not uses
    consumed = report.total_flags == 0 and not report.select_gate
    lim = limits.consume("modeB") if consumed else limits.state()
    if consumed:
        # this use is refundable until a propose actually DELIVERS
        session = server._SESSIONS.get(req.session_id)
        if session is not None:
            session.setdefault("l3_refundable", set()).add(req.filename)
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
        result = proposer.propose_rows(target["path"], model=req.model)
    except Exception as exc:         # defense in depth
        result = {"ok": False, "proposals": [], "rejected": [],
                  "model": req.model, "notes": [],
                  "error": f"Proposer failed: {type(exc).__name__}: {exc}"}
    # a run that delivers NO new tests must not cost a
    # use — refund the scan's tick, once, and say so in plain language.
    # A delivering run clears refundability so a later empty retry can't
    # refund a use that already bought something.
    session = server._SESSIONS.get(req.session_id)
    refundable = (session is not None
                  and req.filename in session.get("l3_refundable", set()))
    if refundable:
        session["l3_refundable"].discard(req.filename)
        if not result.get("proposals"):
            result["limits"] = limits.refund("modeB")
            result["refunded"] = True
            result.setdefault("notes", []).append(
                "No usable new tests this time — that can be a coach "
                "limitation, not proof your tests are complete. Today's "
                "Coverage Coach use was refunded.")
    return result


class InjectRequest(BaseModel):
    session_id: str
    filename: str
    rows: list[str] = []
    spec_name: str | None = None    # default: the file's first testcase
    origin: str = "coach"           # provenance tag carried to result rows
    # Program-driven targets: rom_words extend the program ROM (one row per
    # word). Default: the rows are APPENDED to the official testcase on
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
            # temp-spec indices of the rows Mode B ADDED: Mode A's
            # verifier judges these by strict improvement, not perfection
            # (their expected cells are coach guesses — debugger.verify_ops)
            "coach_rows": sorted(
                r["index"] for r in (outcome.rows or [])
                if r.get("added") and isinstance(r.get("index"), int)),
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


class AdoptRequest(BaseModel):
    session_id: str
    filename: str


@router.post("/api/l3/adopt_official")
def l3_adopt_official(req: AdoptRequest) -> dict:
    """the ONE student write-path into the official-test store:
    merge the machine-verified coach rows into this lab's local official
    test. Server-side by design: the content saved is read from the
    registered coach TEMP circuit (original rows + verified coach rows),
    never from request text — a student cannot inject arbitrary rows here.
    Future scans then hold the circuit to the merged, higher bar."""
    from dlc.l3 import official_store
    from dlc.web import server   # late binding; see module docstring

    server._resolve_target(req.session_id, req.filename)   # 404 on unknown
    session = server._SESSIONS.get(req.session_id)
    temp_filename = f"{Path(req.filename).stem}__coach.dig"
    entry = next((f for f in (session or {}).get("files", [])
                  if f["name"] == temp_filename), None)
    if entry is None:
        return {"ok": False, "warning": ("No verified coach temp for this "
                                         "file — run Mode B and Accept "
                                         "first.")}
    try:
        specs = extract_test_specs(parse_dig_file(entry["path"]))
    except Exception as exc:
        return {"ok": False,
                "warning": f"Could not read the temp circuit: {exc}"}
    if not specs:
        return {"ok": False, "warning": "The temp circuit has no testcase."}
    saved = official_store.save_test(req.filename, specs[0].raw_data_string,
                                     allow_default_override=True)
    return {"ok": True, "filename": req.filename, "sha1": saved["sha1"],
            "rows": specs[0].row_count()}


class DebugRequest(BaseModel):
    session_id: str
    filename: str
    spec_index: int = 0
    model: str | None = None


@router.post("/api/llm/debug")
def llm_debug(req: DebugRequest) -> dict:
    """Mode A coordinator (explicit trigger only): per-row verdict →
    evidence clusters → one single-shot sub-agent per cluster → verified
    hypothesis cards (docs/l3_debug_contract.md).

    Scope: when this session holds a coach TEMP for the file
    (Mode B injected rows), the analysis targets THAT temp and its spec —
    the "Mode A operates on the CURRENT TEMP CIRCUIT" hand-off. The temp's
    coach-added rows are judged by strict improvement, not perfection
    (debugger.verify_ops), because their expected cells are coach guesses.

    Use limits: clear and lazy runs are free; an analysis run books one
    Mode A use ONLY when at least one verified card is delivered — an
    empty run must not cost the student a use.
    """
    from dlc.l3 import debugger      # late import keeps startup lean
    from dlc.web import server       # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)
    if not limits.allowed("modeA"):
        return {
            "ok": False,
            "limited": True,
            "warning": "Daily debug-analysis limit reached — try again "
                       "tomorrow.",
            "limits": limits.state(),
        }

    path, spec_name, on_temp = target["path"], None, False
    coach_rows = None
    session = server._SESSIONS.get(req.session_id)
    lt = (session or {}).get("l3_temp") or None
    if (lt and lt.get("for") == req.filename and lt.get("path")
            and os.path.exists(lt["path"])):
        path, spec_name, on_temp = lt["path"], lt.get("spec_name"), True
        coach_rows = lt.get("coach_rows") or None

    try:
        result = debugger.debug_circuit(
            path, spec_name=spec_name, spec_index=req.spec_index,
            model=req.model, coach_rows=coach_rows,
        )
    except Exception as exc:         # defense in depth
        return {"ok": False, "mode": "error",
                "warning": f"Debug run failed: {type(exc).__name__}: {exc}"}

    consumed = (result.get("mode") == "analysis"
                and bool(result.get("cards")))
    result["limits"] = limits.consume("modeA") if consumed else limits.state()
    result["consumed_use"] = consumed
    result["on_coach_temp"] = on_temp
    return result


class FixRetestRequest(BaseModel):
    session_id: str
    filename: str
    ops: list[dict] = []
    spec_name: str | None = None


@router.post("/api/l3/fix_retest")
def l3_fix_retest(req: FixRetestRequest) -> dict:
    """The animation's green Retest box: apply a delivered card's fix ops
    to a TEMP copy and per-row rerun the testcase — the student SEES the
    fixed circuit go green without their file being touched. Deterministic
    (apply_patch guards + jar/evaluator rerun), no model call, no daily
    use. Targets the coach temp when one is registered, exactly like
    /api/llm/debug, so the retest re-runs what the analysis analyzed."""
    from dlc.l3.patch import rerun_with_patch
    from dlc.web import server   # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)
    path = target["path"]
    spec_name = req.spec_name
    session = server._SESSIONS.get(req.session_id)
    lt = (session or {}).get("l3_temp") or None
    if (lt and lt.get("for") == req.filename and lt.get("path")
            and os.path.exists(lt["path"])):
        path, spec_name = lt["path"], spec_name or lt.get("spec_name")
    if not req.ops:
        return {"ok": False, "warning": "No fix ops to retest."}
    try:
        outcome = rerun_with_patch(path, req.ops, spec_name=spec_name)
    except Exception as exc:             # defense in depth
        return {"ok": False,
                "warning": f"Retest failed: {type(exc).__name__}: {exc}"}
    if outcome.ok:
        spec = None
        if outcome.specs:
            spec = next((s for s in outcome.specs if s["name"] == spec_name),
                        outcome.specs[0])
        return {"ok": True, "spec": spec, "all_passed": outcome.all_passed}
    if "Digital.jar" not in (outcome.warning or ""):
        return {"ok": False, "warning": outcome.warning}

    # no jar: the SAME evaluator that judged Mode A's verify re-judges here
    from dlc.l3.debugger import _offline_failing
    from dlc.l3.patch import apply_patch
    temp, rep = apply_patch(path, req.ops)
    if temp is None:
        return {"ok": False, "warning": rep.warning}
    try:
        circ = parse_dig_file(temp)
        specs = extract_test_specs(circ)
        sp = next((s for s in specs if s.name == spec_name),
                  specs[0] if specs else None)
        if sp is None:
            return {"ok": False, "warning": "The temp has no testcase."}
        failing, _det = _offline_failing(temp, sp.name)
        rows = [{"index": r.line_index, "raw": r.raw,
                 "status": "failed" if r.line_index in failing else "passed"}
                for r in sp.rows if not r.is_malformed]
        return {"ok": True,
                "spec": {"name": sp.name, "headers": list(sp.headers),
                         "rows": rows, "all_passed": not failing},
                "all_passed": not failing}
    except Exception as exc:
        return {"ok": False,
                "warning": f"Retest failed: {type(exc).__name__}: {exc}"}
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass


@router.get("/api/l3/configured")
def l3_configured() -> dict:
    """which lab files this deployment is configured for — union of
    manifest applies_to / fingerprints and the official-test store (shipped
    defaults + user entries). The L3 tab shows this so students know which
    labs get full coaching and which await instructor configuration."""
    from dlc.l3 import manifest as mf
    from dlc.l3 import official_store

    files: set[str] = set()
    for m in mf.load_manifests():
        files |= set(m.get("applies_to") or [])
        files |= set((m.get("official_tests") or {}).keys())
    files |= {t["filename"] for t in official_store.list_tests()}
    return {"ok": True, "files": sorted(files)}