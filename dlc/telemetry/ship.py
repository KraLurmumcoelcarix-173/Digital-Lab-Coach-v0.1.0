"""Ship locally-spooled telemetry to the course proxy server.

The local SQLite sink (sink.py) is the spool: events always land there
first, so the tool works fully offline. When a proxy is configured
(`proxy_url` in ~/.dlc/config.json, or DLC_PROXY_URL), ship_pending()
sends everything past the high-water mark in batches; the mark advances
only on a 2xx, so delivery is at-least-once and the proxy dedupes on
(install_id, client_row_id). Shipping runs fire-and-forget from the app
(startup + piggybacked on telemetry stores) and must never raise into
the caller.
"""

from __future__ import annotations

import json
import os
import sqlite3

from dlc.telemetry import sink
from dlc.telemetry.machine import machine_identity

_BATCH = 200
_STATE = """
CREATE TABLE IF NOT EXISTS ship_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_shipped INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO ship_state (id, last_shipped) VALUES (1, 0);
"""


def proxy_config() -> tuple[str | None, str | None]:
    try:
        from dlc.llm.client import _proxy_config
        return _proxy_config()
    except Exception:
        return None, None


def _conn() -> sqlite3.Connection:
    c = sink._connect()
    c.executescript(_STATE)
    return c


def ship_pending(timeout: float = 6.0) -> dict:
    """Send unshipped local events to the proxy. Returns a small status
    dict; never raises. No proxy configured -> {"shipped": 0,
    "reason": "no_proxy"} and the spool simply keeps growing locally."""
    url, token = proxy_config()
    if not url:
        return {"shipped": 0, "reason": "no_proxy"}
    ident = machine_identity()
    try:
        conn = _conn()
    except Exception as exc:
        return {"shipped": 0, "reason": f"spool: {type(exc).__name__}"}
    try:
        (mark,) = conn.execute(
            "SELECT last_shipped FROM ship_state WHERE id = 1").fetchone()
        rows = conn.execute(
            "SELECT id, stored_at, client_ts, session_id, kind, details "
            "FROM events WHERE id > ? ORDER BY id LIMIT ?",
            (mark, _BATCH)).fetchall()
        if not rows:
            return {"shipped": 0, "reason": "up_to_date"}
        events = []
        for rid, stored_at, client_ts, session_id, kind, details in rows:
            try:
                props = json.loads(details)
            except json.JSONDecodeError:
                props = {}
            events.append({"client_row_id": rid, "stored_at": stored_at,
                           "client_ts": client_ts, "session_id": session_id,
                           "kind": kind, "props": props})
        payload = {"install_id": ident["install_id"],
                   "issued": ident["issued"],
                   "id_source": ident["source"],
                   "app_version": os.environ.get("DLC_VERSION", "0.1.0"),
                   "events": events}
        import httpx
        resp = httpx.post(f"{url}/v1/events", json=payload,
                          headers={"X-DLC-Token": token or ""},
                          timeout=timeout)
        if resp.status_code // 100 != 2:
            return {"shipped": 0, "reason": f"http_{resp.status_code}"}
        new_mark = rows[-1][0]
        with conn:
            conn.execute(
                "UPDATE ship_state SET last_shipped = ? WHERE id = 1",
                (new_mark,))
        return {"shipped": len(rows), "reason": "ok",
                "more": len(rows) == _BATCH}
    except Exception as exc:
        return {"shipped": 0, "reason": f"{type(exc).__name__}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def ship_all(max_batches: int = 20) -> dict:
    total = 0
    for _ in range(max_batches):
        r = ship_pending()
        total += r.get("shipped", 0)
        if not r.get("more"):
            r["shipped"] = total
            return r
    return {"shipped": total, "reason": "batch_cap"}
