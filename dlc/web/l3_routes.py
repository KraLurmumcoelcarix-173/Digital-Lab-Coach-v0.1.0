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
    scan_path, on_temp = target["path"], False
    _s = server._SESSIONS.get(req.session_id)
    _lt = (_s or {}).get("l3_temp") or None
    if (_lt and _lt.get("for") == req.filename and _lt.get("path")
            and os.path.exists(_lt["path"])):
        # after Accept-Fix (or an accepted injection) the session
        # coaches the TEMP — Mode B continues on the fixed circuit
        scan_path, on_temp = _lt["path"], True
    if not limits.allowed("modeB"):
        return {
            "ok": False,
            "limited": True,
            "warning": "Daily Coverage Coach limit reached — try again tomorrow.",
            "limits": limits.state(),
        }
    try:
        report = scan_tree_coverage(scan_path)
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
        "on_coach_temp": on_temp,
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
    prop_path = target["path"]
    _s = server._SESSIONS.get(req.session_id)
    _lt = (_s or {}).get("l3_temp") or None
    if (_lt and _lt.get("for") == req.filename and _lt.get("path")
            and os.path.exists(_lt["path"])):
        prop_path = _lt["path"]          # 3.11: propose on the fixed temp
    try:
        result = proposer.propose_rows(prop_path, model=req.model)
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
    base_path = target["path"]
    _s = server._SESSIONS.get(req.session_id)
    _lt = (_s or {}).get("l3_temp") or None
    prev_coach_rows: list[int] = []
    if (_lt and _lt.get("for") == req.filename and _lt.get("path")
            and os.path.exists(_lt["path"])):
        # rows inject ON TOP of the current temp (e.g. an accepted
        # fix), never silently resetting to the unfixed original
        base_path = _lt["path"]
        prev_coach_rows = list(_lt.get("coach_rows") or [])

    spec_name = req.spec_name
    if spec_name is None:
        try:
            specs = extract_test_specs(parse_dig_file(base_path))
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
            base_path, spec_name, rows, req.rom_words, keep_temp=True,
        )
    elif req.rom_words:
        outcome = rerun_with_program(
            base_path, spec_name, rows, req.rom_words, keep_temp=True,
        )
    else:
        outcome = rerun_with_rows(
            base_path, spec_name, rows, keep_temp=True,
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
            "coach_rows": sorted(set(prev_coach_rows) | {
                r["index"] for r in (outcome.rows or [])
                if r.get("added") and isinstance(r.get("index"), int)}),
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


_ROM_HINT = (
    "Check your ROM data: this analysis ran with the course program "
    "loaded into your empty ROM, so the fix above covers the logic only "
    "— your own file's ROM is still unprogrammed. Fill it in before "
    "submitting."
)


def _rom_injected_notes(notes: list[str] | None) -> bool:
    """Did prepare_injected_run fill ROM(s) for this run? Keyed on the
    marker substring the inject note carries (see inject.py)."""
    return any("course program was loaded" in n for n in (notes or []))


def _apply_rom_hint(result: dict, rom_injected: bool) -> None:
    """r38: a verified fix produced on a rom-injected run must remind the
    student their OWN rom is still empty — the fix alone will not make
    their submission pass. Rides every card's fix (a dedicated field plus
    the student-visible explanation) and flags the result for the
    benchmark."""
    result["rom_injected"] = rom_injected
    if not rom_injected:
        return
    for card in result.get("cards") or []:
        fix = card.get("fix") or {}
        fix["rom_hint"] = _ROM_HINT
        expl = (fix.get("explanation_for_student") or "").rstrip()
        if expl and not expl.endswith("."):
            expl += "."
        fix["explanation_for_student"] = (expl + " " + _ROM_HINT).strip()
        card["fix"] = fix


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

    # Gradescope-style: when NOT on a coach temp, an empty/modified
    # testcase means Mode A must debug against the OFFICIAL rows —
    # otherwise a header-only testcase yields zero failing rows and the
    # board wrongly reports "every row passes". Same sibling-temp
    # mechanism as the Dashboard test runs.
    inj_temp, inj_notes = (None, [])
    if not on_temp:
        from dlc.testing.inject import (
            prepare_injected_run, cleanup_injected,
        )
        inj_temp, inj_notes = prepare_injected_run(path, req.filename)
        if inj_temp:
            path = inj_temp
            spec_name = None         # injected spec is the only one

    rom_injected = _rom_injected_notes(inj_notes)
    try:
        result = debugger.debug_circuit(
            path, spec_name=spec_name, spec_index=req.spec_index,
            model=req.model, coach_rows=coach_rows,
            # the REAL filename decides the control-unit lazy exemption —
            # coach temps carry generated names the path check would miss
            lazy_exempt=debugger._lazy_exempt_name(req.filename),
            rom_injected=rom_injected,
        )
    except Exception as exc:         # defense in depth
        return {"ok": False, "mode": "error",
                "warning": f"Debug run failed: {type(exc).__name__}: {exc}"}
    finally:
        if inj_temp:
            cleanup_injected(inj_temp)
    if inj_notes:
        result["injected"] = inj_notes
    _apply_rom_hint(result, rom_injected)

    consumed = (result.get("mode") == "analysis"
                and bool(result.get("cards")))
    result["limits"] = limits.consume("modeA") if consumed else limits.state()
    result["consumed_use"] = consumed
    result["on_coach_temp"] = on_temp
    return result


class AcceptFixRequest(BaseModel):
    session_id: str
    filename: str
    ops: list[dict] = []
    spec_name: str | None = None


@router.post("/api/l3/accept_fix")
def l3_accept_fix(req: AcceptFixRequest) -> dict:
    """ACCEPT FIX: apply a CONFIRMED card's ops to a TEMP
    copy ONLY — the student's original .dig is never touched — and
    register that temp as the session coach temp (replacing any previous
    one). From then on /api/llm/debug, /api/l3/coverage and
    /api/l3/propose target the FIXED temp: re-running Mode A shows the
    rows passing, and Mode B continues on the fixed circuit. The UI
    double-confirms before calling. Applies on the CURRENT analysis
    target (the registered temp when one exists — the fix was computed
    against it), so a case-3 fix lands on top of the injected rows."""
    from dlc.l3.patch import apply_patch
    from dlc.testing.runner import per_row_run_auto
    from dlc.web import server   # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)
    if not req.ops:
        return {"ok": False, "warning": "No fix ops to accept."}
    path = target["path"]
    session = server._SESSIONS.get(req.session_id)
    lt = (session or {}).get("l3_temp") or None
    prev_coach_rows: list[int] = []
    spec_name = req.spec_name
    on_prev_temp = False
    if (lt and lt.get("for") == req.filename and lt.get("path")
            and os.path.exists(lt["path"])):
        path = lt["path"]
        prev_coach_rows = list(lt.get("coach_rows") or [])
        spec_name = spec_name or lt.get("spec_name")
        on_prev_temp = True

    temp, rep = apply_patch(path, req.ops)
    if temp is None:
        return {"ok": False, "warning": rep.warning}

    # Gradescope-style: when the fix was computed against the student's
    # RAW file (no coach temp), the new temp is tool-owned scratch and an
    # empty/modified testcase gets the official rows written in directly
    # — otherwise the "show the green" rerun below sees zero rows and
    # reports all-fixed on nothing. A temp descending from Mode B keeps
    # its coach-added rows untouched.
    if not on_prev_temp:
        from dlc.testing.inject import inject_official_tests_in_place
        if inject_official_tests_in_place(temp, req.filename):
            spec_name = None   # injected spec ("official") is the only one

    temp_filename = f"{Path(req.filename).stem}__coach.dig"
    if session is not None:
        for f in list(session["files"]):
            if f["name"] == temp_filename:
                session["files"].remove(f)
                if f["path"] != temp:
                    try:
                        os.remove(f["path"])
                    except OSError:
                        pass
        session["files"].append({"name": temp_filename, "path": temp})
        session["l3_temp"] = {
            "for": req.filename,
            "name": temp_filename,
            "path": temp,
            "spec_name": spec_name,
            # rows Mode B added remain coach-guessed on the fixed temp
            "coach_rows": prev_coach_rows,
        }

    # show the green: per-row rerun of the FIXED temp (jar or evaluator).
    # run through the same injection as every other run — a
    # rom-injected lab (cpu.dig) must re-test WITH the course program in
    # its empty ROM, or a verified wiring fix would show every row red.
    # The rom-filled sibling is runner-scoped and removed right after;
    # the registered coach temp itself never stores official rom data.
    inj2 = None
    try:
        from dlc.testing.inject import (
            prepare_injected_run, cleanup_injected,
        )
        inj2, inj2_notes = prepare_injected_run(temp, req.filename)
        run_path = inj2 or temp
        circ = parse_dig_file(run_path)
        specs = extract_test_specs(circ)
        sp = next((s for s in specs if s.name == spec_name),
                  specs[0] if specs else None)
        if sp is None:
            return {"ok": True, "temp_filename": temp_filename,
                    "spec": None, "all_passed": None}
        raw_by_idx = {r.line_index: r.raw for r in sp.rows
                      if not r.is_malformed}
        from dlc.testing.runner import find_digital_jar
        jar = find_digital_jar()
        if jar:
            rs = per_row_run_auto(sp, run_path, jar_path=jar)
            rows = [{"index": r.row_index,
                     "raw": raw_by_idx.get(r.row_index, ""),
                     "status": "passed" if r.status == "passed"
                               else "failed"} for r in rs]
        else:
            from dlc.l3.debugger import _offline_failing
            failing, _det = _offline_failing(run_path, sp.name)
            rows = [{"index": r.line_index, "raw": r.raw,
                     "status": "failed" if r.line_index in failing
                               else "passed"}
                    for r in sp.rows if not r.is_malformed]
        allp = all(r["status"] == "passed" for r in rows)
        out = {"ok": True, "temp_filename": temp_filename,
               "spec": {"name": sp.name, "headers": list(sp.headers),
                        "rows": rows, "all_passed": allp},
               "all_passed": allp}
        if inj2_notes:
            out["injected"] = inj2_notes
        return out
    except Exception as exc:             # registered fine; rerun best-effort
        return {"ok": True, "temp_filename": temp_filename, "spec": None,
                "all_passed": None,
                "warning": (f"fix accepted onto the temp, but the rerun "
                            f"failed: {type(exc).__name__}: {exc}")}
    finally:
        if inj2:
            cleanup_injected(inj2)


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