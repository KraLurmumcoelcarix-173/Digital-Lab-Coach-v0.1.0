"""
FastAPI app for the Digital Lab Coach local web UI.

Run with: `uv run python -m dlc.web.server`.

Endpoints:
  GET  /                       index.html
  GET  /static/...             JS, CSS, images
  POST /api/circuit            multipart upload of one OR MORE .dig

  GET  /api/health             readiness probe
  GET  /api/config/jar         current Digital.jar path 
  POST /api/config/jar         set Digital.jar path
  GET  /api/config/jar/browse  open the native file picker on the server 
  
  POST /api/tests              run per-row tests 
  POST /api/llm/explain        Layer 3. 
"""
from pathlib import Path
import tempfile
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dlc.analyzer import check_all_l1
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.testing.config import (
    get_configured_jar,
    set_digital_jar_path,
    prompt_for_jar_path,
)
from dlc.testing.runner import find_digital_jar, per_row_run
from dlc.testing.spec import extract_test_specs
from dlc.web.graph_export import circuit_summary, to_cytoscape


STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Digital Lab Coach", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_SESSIONS: dict[str, dict] = {}


class JarRequest(BaseModel):
    path: str


class TestsRequest(BaseModel):
    session_id: str
    filename: str
    timeout: float = 30.0


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}

@app.get("/api/config/jar")
def get_jar() -> dict:
    p = find_digital_jar()
    configured = get_configured_jar()
    return {
        "path": p,
        "configured": configured,
        "exists": bool(p and Path(p).exists()),
    }


@app.post("/api/config/jar")
def set_jar(req: JarRequest) -> dict:
    try:
        set_digital_jar_path(req.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "path": req.path}


@app.get("/api/config/jar/browse")
def browse_jar() -> dict:
    try:
        path = prompt_for_jar_path()
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not path:
        return {"ok": False, "reason": "cancelled or no tkinter available"}
    return {"ok": True, "path": path}

@app.post("/api/circuit")
async def circuit(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="No files received.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="dlc-"))
    saved: list[tuple[str, Path]] = []
    for f in files:
        if not f.filename or not f.filename.endswith(".dig"):
            continue
        name = Path(f.filename).name
        path = tmp_dir / name
        with open(path, "wb") as out:
            out.write(await f.read())
        saved.append((name, path))

    if not saved:
        raise HTTPException(
            status_code=400, detail="Please upload at least one .dig file."
        )

    session_id = uuid.uuid4().hex
    _SESSIONS[session_id] = {
        "tmp_dir": str(tmp_dir),
        "files": [{"name": n, "path": str(p)} for n, p in saved],
    }

    results: list[dict] = []
    for name, path in saved:
        try:
            c = parse_dig_file(str(path))
            nl = build_netlist(c)
            g = build_signal_graph(c, nl)
            try:
                issues_payload = check_all_l1(c).to_dict()["issues"]
                issues_error = None
            except Exception as exc:
                issues_payload = []
                issues_error = f"{type(exc).__name__}: {exc}"
            results.append({
                "filename": name,
                "graph": to_cytoscape(c, nl, g),
                "summary": circuit_summary(c, nl),
                "issues": issues_payload,
                "issues_error": issues_error,
                "error": None,
            })
        except Exception as exc:
            results.append({
                "filename": name,
                "graph": None,
                "summary": None,
                "issues": [],
                "issues_error": None,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {"session_id": session_id, "files": results}

@app.post("/api/tests")
def run_tests(req: TestsRequest) -> dict:
    session = _SESSIONS.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    target = next(
        (f for f in session["files"] if f["name"] == req.filename), None
    )
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"File {req.filename!r} not in session"
        )

    try:
        circuit = parse_dig_file(target["path"])
    except Exception as exc:
        return {
            "ok": False,
            "warning": f"Could not parse circuit for testing: {exc}",
            "all_passed": None,
            "specs": [],
        }

    specs = extract_test_specs(circuit)
    if not specs:
        return {
            "ok": True,
            "warning": None,
            "all_passed": None,
            "specs": [],
        }

    jar_path = find_digital_jar()
    if jar_path is None:
        return {
            "ok": False,
            "warning": (
                "Digital.jar not configured. Open the jar picker from "
                "the toolbar to select it."
            ),
            "all_passed": None,
            "specs": [],
        }

    spec_payloads: list[dict] = []
    any_failed = False
    any_runner_error = False

    for spec in specs:
        try:
            row_results = per_row_run(
                spec, target["path"], jar_path=jar_path, timeout=req.timeout,
            )
        except Exception as exc:
            return {
                "ok": False,
                "warning": f"Test runner crashed: {type(exc).__name__}: {exc}",
                "all_passed": None,
                "specs": [],
            }
        rows_by_idx = {row.line_index: row for row in spec.rows}
        row_payload: list[dict] = []
        for r in row_results:
            row = rows_by_idx.get(r.row_index)
            row_payload.append({
                "index": r.row_index,
                "raw": row.raw if row else "",
                "status": r.status,
                "error_message": r.error_message,
            })
            if r.status == "failed":
                any_failed = True
            if r.status == "error":
                any_runner_error = True

        spec_payloads.append({
            "name": spec.name,
            "headers": spec.headers,
            "rows": row_payload,
        })

    return {
        "ok": True,
        "warning": (
            "One or more rows could not be run (see status=error)."
            if any_runner_error else None
        ),
        "all_passed": (not any_failed) and (not any_runner_error),
        "specs": spec_payloads,
    }

@app.post("/api/llm/explain")
def llm_explain() -> dict:
    raise HTTPException(
        status_code=501,
        detail="Layer 3 LLM not yet implemented (planned for F11-F13).",
    )


def main() -> None:
    import uvicorn
    uvicorn.run(
        "dlc.web.server:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()