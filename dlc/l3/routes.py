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

from fastapi import APIRouter
from pydantic import BaseModel

from dlc.l3.coverage import scan_tree_coverage

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

    The endpoint itself does not enforce the L1 lock or daily use limits:
    the board UI blocks locked circuits, and real enforcement is the
    ENFORCE_LIMITS flag's job.
    """
    from dlc.web import server   # late binding; see module docstring

    target = server._resolve_target(req.session_id, req.filename)
    try:
        report = scan_tree_coverage(target["path"])
    except Exception as exc:     # defense in depth; the scan shouldn't raise
        return {
            "ok": False,
            "warning": f"Coverage scan failed: {type(exc).__name__}: {exc}",
        }
    return {"ok": True, "warning": None, **report.to_dict()}