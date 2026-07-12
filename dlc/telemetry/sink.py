"""Telemetry SQLite sink.

The front end has logged every interaction into ``window.dlcEventLog``
since the Layer-1 build; this module gives those events a durable local
home so L3 telemetry (the pairs: l3_modeA_started → l3_now_passing,
l3_circuit_re_uploaded, ...) records from day one.

Storage: one SQLite file at ``~/.dlc/telemetry.db`` (next to config.json,
OUTSIDE the repo — IRB-safe), overridable with the ``DLC_TELEMETRY_DB``
env var (tests point it at a tmp dir). One row per event; unknown detail
fields ride along as JSON so new event kinds never need a migration.

This local sink is sufficient for all dev/temp-web work.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stored_at  REAL NOT NULL,        -- server clock, seconds
    client_ts  REAL,                 -- the browser's Date.now()/1000, if given
    session_id TEXT,
    kind       TEXT NOT NULL,
    details    TEXT NOT NULL         -- JSON of every other field
);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""


def db_path() -> Path:
    env = os.environ.get("DLC_TELEMETRY_DB")
    if env:
        return Path(env)
    return Path.home() / ".dlc" / "telemetry.db"


def _connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    return conn


def log_events(session_id: str | None, events: list[dict]) -> int:
    """Store a batch of frontend events; returns how many were stored.

    Each event is a dict with at least ``kind``; ``ts`` (client
    milliseconds, as logEvent records) is lifted out, everything else is
    kept verbatim in the JSON details column. Malformed entries are
    skipped rather than failing the batch — telemetry must never break
    the app.
    """
    rows = []
    now = time.time()
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        if not kind or not isinstance(kind, str):
            continue
        raw_ts = ev.get("ts")
        client_ts = None
        if isinstance(raw_ts, (int, float)):
            # logEvent stores Date.now() (ms); tolerate seconds too.
            client_ts = raw_ts / 1000.0 if raw_ts > 1e11 else float(raw_ts)
        details = {k: v for k, v in ev.items() if k not in ("kind", "ts")}
        try:
            details_json = json.dumps(details)
        except (TypeError, ValueError):
            details_json = json.dumps({"unserializable": True})
        rows.append((now, client_ts, session_id, kind[:64], details_json))

    if not rows:
        return 0
    conn = _connect()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO events (stored_at, client_ts, session_id, kind, details) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
    finally:
        conn.close()
    return len(rows)


def recent_events(limit: int = 50, kind: str | None = None) -> list[dict]:
    """Latest stored events, newest first — a dev/debug convenience.

    Each entry carries `when`: the browser's click time (client_ts, falling
    back to stored_at) rendered as a local human-readable string, so a
    plain recent_events() printout needs no epoch math."""
    if not db_path().exists():
        return []
    conn = _connect()
    try:
        q = ("SELECT stored_at, client_ts, session_id, kind, details "
             "FROM events ")
        args: tuple = ()
        if kind:
            q += "WHERE kind = ? "
            args = (kind,)
        q += "ORDER BY id DESC LIMIT ?"
        args = args + (int(limit),)
        out = []
        for stored_at, client_ts, session_id, k, details in conn.execute(q, args):
            try:
                d = json.loads(details)
            except json.JSONDecodeError:
                d = {}
            ts = client_ts if client_ts is not None else stored_at
            when = datetime.fromtimestamp(ts).strftime("%a %d %b %Y %H:%M:%S")
            out.append({"when": when,
                        "stored_at": stored_at, "client_ts": client_ts,
                        "session_id": session_id, "kind": k, "details": d})
        return out
    finally:
        conn.close()
