"""
DLC course proxy: key custody + machine-keyed limits + telemetry.

One small server the instructor user runs; students' tools point at it via
`proxy_url` in their config.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI(title="DLC course proxy")

CALL_BUDGETS = {"modeA": 8, "modeB": 10, "grade": 20, "explain": 20}
_DEFAULT_BUDGET = 12

_PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0), "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0), "gpt-5": (1.25, 10.0),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    install_id  TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    issued_client TEXT,
    id_source   TEXT,
    app_version TEXT,
    last_seen   REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    install_id TEXT NOT NULL,
    client_row_id INTEGER NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    client_ts REAL,
    stored_at REAL,
    received_at REAL NOT NULL,
    props TEXT NOT NULL,
    UNIQUE(install_id, client_row_id)
);
CREATE INDEX IF NOT EXISTS idx_ev_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_ev_machine ON events(install_id);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    install_id TEXT NOT NULL,
    day TEXT NOT NULL,
    ts REAL NOT NULL,
    feature TEXT NOT NULL,
    model TEXT,
    ok INTEGER,
    in_tokens INTEGER,
    out_tokens INTEGER,
    ms INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_budget ON llm_calls(install_id, feature, day);
"""


def _db() -> sqlite3.Connection:
    p = Path(os.environ.get("DLC_PROXY_DB", "dlc_proxy.db"))
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    return conn


def _check_course_token(tok: str | None) -> None:
    want = os.environ.get("DLC_COURSE_TOKEN")
    if want and (tok or "") != want:
        raise HTTPException(status_code=401, detail="bad course token")


def _check_admin(token: str | None) -> None:
    want = os.environ.get("DLC_ADMIN_TOKEN")
    if not want or (token or "") != want:
        raise HTTPException(status_code=401, detail="bad admin token")


def _touch_machine(conn, install_id: str, issued=None, source=None,
                   version=None) -> None:
    with conn:
        conn.execute(
            "INSERT INTO machines (install_id, first_seen, issued_client, "
            "id_source, app_version, last_seen) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(install_id) DO UPDATE SET last_seen = ?, "
            "app_version = COALESCE(excluded.app_version, app_version)",
            (install_id, date.today().isoformat(), issued, source,
             version, time.time(), time.time()))


class EventsIn(BaseModel):
    install_id: str
    issued: str | None = None
    id_source: str | None = None
    app_version: str | None = None
    events: list[dict] = []


@app.post("/v1/events")
def ingest(req: EventsIn,
           x_dlc_token: str | None = Header(default=None)) -> dict:
    _check_course_token(x_dlc_token)
    if not req.install_id:
        raise HTTPException(status_code=400, detail="install_id required")
    conn = _db()
    try:
        _touch_machine(conn, req.install_id, req.issued, req.id_source,
                       req.app_version)
        stored = 0
        now = time.time()
        for ev in req.events[:1000]:
            if not isinstance(ev, dict) or not ev.get("kind"):
                continue
            try:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO events (install_id, "
                        "client_row_id, session_id, kind, client_ts, "
                        "stored_at, received_at, props) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (req.install_id,
                         int(ev.get("client_row_id") or 0),
                         ev.get("session_id"),
                         str(ev.get("kind"))[:64],
                         ev.get("client_ts"), ev.get("stored_at"), now,
                         json.dumps(ev.get("props") or {})[:20000]))
                stored += 1
            except (sqlite3.Error, TypeError, ValueError):
                continue
        return {"ok": True, "stored": stored}
    finally:
        conn.close()


class LlmIn(BaseModel):
    install_id: str
    feature: str = "other"
    model: str
    prompt: str
    system: str | None = None
    max_tokens: int = 3000
    effort: str | None = None


@app.post("/v1/llm")
def relay(req: LlmIn,
          x_dlc_token: str | None = Header(default=None)) -> dict:
    _check_course_token(x_dlc_token)
    if not req.install_id:
        raise HTTPException(status_code=400, detail="install_id required")
    day = date.today().isoformat()
    budget = CALL_BUDGETS.get(req.feature, _DEFAULT_BUDGET)
    conn = _db()
    try:
        _touch_machine(conn, req.install_id)
        (used,) = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE install_id = ? "
            "AND feature = ? AND day = ?",
            (req.install_id, req.feature, day)).fetchone()
        if used >= budget:
            return {"ok": False, "text": None,
                    "error": (f"Daily limit reached for {req.feature} on "
                              f"this machine — it resets tomorrow. "
                              f"(Re-downloading the tool does not reset "
                              f"it.)"),
                    "limit_hit": True, "usage": None, "model": req.model}
        t0 = time.monotonic()
        from dlc.llm.client import call_llm
        r = call_llm(req.prompt, model=req.model,
                     max_tokens=req.max_tokens, system=req.system,
                     effort=req.effort)
        ms = int((time.monotonic() - t0) * 1000)
        u = r.get("usage") or {}
        with conn:
            conn.execute(
                "INSERT INTO llm_calls (install_id, day, ts, feature, "
                "model, ok, in_tokens, out_tokens, ms, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (req.install_id, day, time.time(), req.feature, req.model,
                 1 if r.get("ok") else 0, u.get("input_tokens"),
                 u.get("output_tokens"), ms,
                 (r.get("error") or "")[:300] or None))
        r["limit"] = {"feature": req.feature, "used": used + 1,
                      "budget": budget}
        return r
    finally:
        conn.close()


@app.get("/v1/health")
def health() -> dict:
    conn = _db()
    try:
        (m,) = conn.execute("SELECT COUNT(*) FROM machines").fetchone()
        (e,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return {"ok": True, "machines": m, "events": e,
                "key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "course_token_set": bool(os.environ.get("DLC_COURSE_TOKEN"))}
    finally:
        conn.close()


@app.get("/admin/summary")
def summary(token: str | None = Query(default=None)) -> dict:
    _check_admin(token)
    conn = _db()
    try:
        machines = [dict(zip(
            ("install_id", "first_seen", "issued_client", "id_source",
             "app_version", "last_seen"), row))
            for row in conn.execute(
                "SELECT install_id, first_seen, issued_client, id_source, "
                "app_version, last_seen FROM machines "
                "ORDER BY first_seen")]
        for m in machines:
            if m["last_seen"]:
                m["last_seen"] = datetime.fromtimestamp(
                    m["last_seen"]).isoformat(timespec="seconds")
            (m["events"],) = conn.execute(
                "SELECT COUNT(*) FROM events WHERE install_id = ?",
                (m["install_id"],)).fetchone()
            (m["llm_calls"],) = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE install_id = ?",
                (m["install_id"],)).fetchone()
        kinds = conn.execute(
            "SELECT kind, COUNT(*) FROM events GROUP BY kind "
            "ORDER BY COUNT(*) DESC LIMIT 30").fetchall()
        spend = 0.0
        for model, i, o in conn.execute(
                "SELECT model, COALESCE(SUM(in_tokens),0), "
                "COALESCE(SUM(out_tokens),0) FROM llm_calls "
                "GROUP BY model"):
            pin, pout = _PRICES.get(model, (5.0, 25.0))
            spend += (i * pin + o * pout) / 1e6
        return {"machines": machines,
                "event_kinds": [{"kind": k, "n": n} for k, n in kinds],
                "llm_spend_est_usd": round(spend, 2)}
    finally:
        conn.close()


@app.get("/admin/export.csv", response_class=PlainTextResponse)
def export_csv(token: str | None = Query(default=None),
               table: str = Query(default="events")) -> str:
    _check_admin(token)
    if table not in ("events", "machines", "llm_calls"):
        raise HTTPException(status_code=400, detail="unknown table")
    conn = _db()
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        lines = [",".join(cols)]
        for row in cur:
            cells = []
            for v in row:
                s = "" if v is None else str(v)
                if any(ch in s for ch in ',"\n'):
                    s = '"' + s.replace('"', '""') + '"'
                cells.append(s)
            lines.append(",".join(cells))
        return "\n".join(lines) + "\n"
    finally:
        conn.close()
