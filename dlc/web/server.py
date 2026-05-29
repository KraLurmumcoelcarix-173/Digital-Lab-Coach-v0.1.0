"""
FastAPI app for the Digital Lab Coach local web UI.

Run with: `uv run python -m dlc.web.server`.

Endpoints:
  GET  /                       index.html
  GET  /static/...             JS, CSS, images
  POST /api/circuit            multipart upload of one OR MORE .dig
                               files (a parent + its subcircuits).
                               Returns {"files": [{filename, graph,
                               summary, error}, ...]}.
  GET  /api/health             readiness probe
"""
from pathlib import Path
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.web.graph_export import circuit_summary, to_cytoscape
from dlc.analyzer import check_all_l1


STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Digital Lab Coach", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


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

    return {"files": results}


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