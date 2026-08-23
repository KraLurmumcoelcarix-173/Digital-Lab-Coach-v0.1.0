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

os.environ["DLC_PROXY_SELF"] = "1"

CALL_BUDGETS = {"modeA": 8, "modeB": 10, "grade": 20, "explain": 20}
_DEFAULT_BUDGET = 12


def _global_daily_calls() -> int:
    try:
        return int(os.environ.get("DLC_GLOBAL_DAILY_CALLS", "") or 600)
    except ValueError:
        return 600


def _global_daily_usd() -> float:
    try:
        return float(os.environ.get("DLC_GLOBAL_DAILY_USD", "") or 20.0)
    except ValueError:
        return 20.0

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


def _check_admin(token: str | None, header_token: str | None = None) -> None:
    want = os.environ.get("DLC_ADMIN_TOKEN")
    got = header_token or token or ""
    if not want or got != want:
        raise HTTPException(status_code=401, detail="bad admin token")


def _est_usd(conn, day: str | None = None) -> float:
    q = ("SELECT model, COALESCE(SUM(in_tokens),0),"
         "COALESCE(SUM(out_tokens),0) FROM llm_calls")
    args: tuple = ()
    if day:
        q += "WHERE day = ?"
        args = (day,)
    q += "GROUP BY model"
    spend = 0.0
    for model, i, o in conn.execute(q, args):
        pin, pout = _PRICES.get(model, (5.0, 25.0))
        spend += (i * pin + o * pout) / 1e6
    return spend


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
        conn.execute("BEGIN IMMEDIATE")
        (day_calls,) = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE day = ?",
            (day,)).fetchone()
        if (day_calls >= _global_daily_calls()
                or _est_usd(conn, day) >= _global_daily_usd()):
            conn.execute("ROLLBACK")
            return {"ok": False, "text": None,
                    "error": ("The course server has reached its daily"
                              "capacity — please try again tomorrow. All"
                              "deterministic checks still work."),
                    "limit_hit": True, "capacity_hit": True,
                    "usage": None, "model": req.model}
        (used,) = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE install_id = ?"
            "AND feature = ? AND day = ?",
            (req.install_id, req.feature, day)).fetchone()
        if used >= budget:
            conn.execute("ROLLBACK")
            return {"ok": False, "text": None,
                    "error": (f"Daily limit reached for {req.feature} on"
                              f"this machine — it resets tomorrow."),
                    "limit_hit": True, "usage": None, "model": req.model}
        cur = conn.execute(
            "INSERT INTO llm_calls (install_id, day, ts, feature, "
            "model, ok, in_tokens, out_tokens, ms, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (req.install_id, day, time.time(), req.feature, req.model,
             0, None, None, None, "pending"))
        row_id = cur.lastrowid
        conn.execute("COMMIT")
        t0 = time.monotonic()
        from dlc.llm.client import call_llm
        r = call_llm(req.prompt, model=req.model,
                     max_tokens=req.max_tokens, system=req.system,
                     effort=req.effort)
        ms = int((time.monotonic() - t0) * 1000)
        u = r.get("usage") or {}
        with conn:
            conn.execute(
                "UPDATE llm_calls SET ts = ?, ok = ?, in_tokens = ?, "
                "out_tokens = ?, ms = ?, error = ? WHERE id = ?",
                (time.time(), 1 if r.get("ok") else 0,
                 u.get("input_tokens"), u.get("output_tokens"), ms,
                 (r.get("error") or "")[:300] or None, row_id))
        r["limit"] = {"feature": req.feature, "used": used + 1,
                      "budget": budget}
        return r
    finally:
        conn.close()


@app.on_event("startup")
def _startup_sanity() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key and not key.startswith("sk-"):
        print("WARNING: ANTHROPIC_API_KEY does not look like a real key"
              "(expected it to start with 'sk-'). LLM relays will fail"
              "with 401 until it is fixed.")
    if not os.environ.get("DLC_COURSE_TOKEN"):
        print("WARNING: DLC_COURSE_TOKEN is not set — the proxy will"
              "accept requests from ANYONE who finds the URL.")


@app.get("/v1/health")
def health() -> dict:
    conn = _db()
    try:
        (m,) = conn.execute("SELECT COUNT(*) FROM machines").fetchone()
        (e,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        day = date.today().isoformat()
        (day_calls,) = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE day = ?",
            (day,)).fetchone()
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        return {"ok": True, "machines": m, "events": e,
                "key_configured": bool(key),
                "key_format_ok": key.startswith("sk-") if key else False,
                "course_token_set": bool(os.environ.get("DLC_COURSE_TOKEN")),
                "today_calls": day_calls,
                "today_est_usd": round(_est_usd(conn, day), 2),
                "global_daily_calls": _global_daily_calls(),
                "global_daily_usd": _global_daily_usd()}
    finally:
        conn.close()


@app.get("/admin/summary")
def summary(token: str | None = Query(default=None),
            x_dlc_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(token, x_dlc_admin_token)
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


@app.get("/admin/daily")
def daily(token: str | None = Query(default=None),
          x_dlc_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(token, x_dlc_admin_token)
    conn = _db()
    try:
        llm = [dict(zip(("day", "install_id", "feature", "calls",
                         "ok_calls", "in_tokens", "out_tokens"), row))
               for row in conn.execute(
                   "SELECT day, install_id, feature, COUNT(*),"
                   "COALESCE(SUM(ok),0), COALESCE(SUM(in_tokens),0),"
                   "COALESCE(SUM(out_tokens),0) FROM llm_calls"
                   "GROUP BY day, install_id, feature"
                   "ORDER BY day DESC, install_id")]
        spend = {}
        for d, model, i, o in conn.execute(
                "SELECT day, model, COALESCE(SUM(in_tokens),0),"
                "COALESCE(SUM(out_tokens),0) FROM llm_calls"
                "GROUP BY day, model"):
            pin, pout = _PRICES.get(model, (5.0, 25.0))
            spend[d] = spend.get(d, 0.0) + (i * pin + o * pout) / 1e6
        activity = [dict(zip(("day", "install_id", "events"), row))
                    for row in conn.execute(
                        "SELECT date(received_at, 'unixepoch'), install_id,"
                        "COUNT(*) FROM events"
                        "GROUP BY date(received_at, 'unixepoch'), install_id"
                        "ORDER BY 1 DESC, install_id")]
        return {"llm": llm,
                "spend_by_day": [{"day": d, "est_usd": round(v, 2)}
                                 for d, v in sorted(spend.items(),
                                                    reverse=True)],
                "activity": activity}
    finally:
        conn.close()


@app.get("/admin/export.csv", response_class=PlainTextResponse)
def export_csv(token: str | None = Query(default=None),
               x_dlc_admin_token: str | None = Header(default=None),
               table: str = Query(default="events")) -> str:
    _check_admin(token, x_dlc_admin_token)
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


_ADMIN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DLC course dashboard</title>
<style>
 body{font:14px/1.45 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#111827}
 header{background:#111827;color:#f9fafb;padding:14px 20px;display:flex;
        justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
 header h1{font-size:16px;margin:0}
 main{max-width:1100px;margin:0 auto;padding:18px}
 .tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
 .tile{background:#fff;border:1px solid #e5e7eb;border-radius:8px;
       padding:10px 16px;min-width:120px}
 .tile b{display:block;font-size:20px}
 .tile span{color:#6b7280;font-size:12px}
 .warn{color:#b45309}
 h2{font-size:15px;margin:22px 0 8px}
 table{border-collapse:collapse;width:100%;background:#fff;
       border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
 th,td{padding:6px 10px;border-bottom:1px solid #eef0f3;text-align:left;
       font-size:13px;white-space:nowrap}
 th{background:#f3f4f6;color:#374151}
 tr:last-child td{border-bottom:none}
 #gate{max-width:420px;margin:80px auto;background:#fff;padding:24px;
       border:1px solid #e5e7eb;border-radius:10px}
 #gate input{width:100%;padding:8px;margin:10px 0;box-sizing:border-box}
 #gate button{padding:8px 16px}
 .err{color:#b91c1c}
 .muted{color:#6b7280}
 button.ghost{background:none;border:1px solid #6b7280;color:#f9fafb;
              border-radius:6px;padding:4px 10px;cursor:pointer}
</style></head><body>
<header><h1>Digital Lab Coach — course dashboard</h1>
<button class="ghost" id="logout" style="display:none">forget token</button>
</header>
<main>
 <div id="gate">
   <h2 style="margin-top:0">Admin token</h2>
   <p class="muted">Stored only in this browser. Only the instructor
      holding DLC_ADMIN_TOKEN can load data.</p>
   <input id="tok" type="password" placeholder="admin token">
   <button id="go">Open dashboard</button>
   <p id="gate-err" class="err"></p>
 </div>
 <div id="dash" style="display:none">
   <div class="tiles" id="tiles"></div>
   <h2>Machines</h2><div id="machines"></div>
   <h2>Per-day activity</h2><div id="daily"></div>
   <h2>Per-day LLM usage</h2><div id="llm"></div>
 </div>
</main>
<script>
const $=(id)=>document.getElementById(id);
function tok(){try{return localStorage.getItem("dlc_admin_token")||""}catch(e){return ""}}
function setTok(v){try{localStorage.setItem("dlc_admin_token",v)}catch(e){}}
async function api(path){
  const r=await fetch(path,{headers:{"X-DLC-Admin-Token":tok()}});
  if(r.status===401)throw new Error("bad token");
  return r.json();
}
function table(rows,cols){
  if(!rows||!rows.length)return '<p class="muted">nothing yet</p>';
  const h=cols.map(c=>`<th>${c}</th>`).join("");
  const b=rows.map(r=>"<tr>"+cols.map(c=>`<td>${r[c]??""}</td>`)
    .join("")+"</tr>").join("");
  return `<table><tr>${h}</tr>${b}</table>`;
}
async function load(){
  const health=await fetch("/v1/health").then(r=>r.json());
  const s=await api("/admin/summary");
  const d=await api("/admin/daily");
  $("gate").style.display="none";$("dash").style.display="block";
  $("logout").style.display="inline-block";
  const cap=health.today_calls>=health.global_daily_calls||
            health.today_est_usd>=health.global_daily_usd;
  $("tiles").innerHTML=[
   [s.machines.length,"machines"],[health.events,"events"],
   [health.today_calls+" / "+health.global_daily_calls,"LLM calls today"],
   ["$"+health.today_est_usd+" / $"+health.global_daily_usd,"spend today (est)"],
   ["$"+s.llm_spend_est_usd,"spend all-time (est)"],
   [cap?"TRIPPED":"ok","capacity breaker",cap],
   [health.key_format_ok?"ok":"CHECK KEY","API key",!health.key_format_ok],
  ].map(([v,l,warn])=>`<div class="tile"><b class="${warn?"warn":""}">${v}</b><span>${l}</span></div>`).join("");
  $("machines").innerHTML=table(s.machines,
   ["install_id","first_seen","last_seen","id_source","app_version",
    "events","llm_calls"]);
  const act={};
  for(const a of d.activity){(act[a.day]=act[a.day]||[]).push(a)}
  $("daily").innerHTML=table(d.activity,["day","install_id","events"]);
  const spend={};for(const s2 of d.spend_by_day)spend[s2.day]=s2.est_usd;
  const llmRows=d.llm.map(r=>({...r,
    est_day_usd:spend[r.day]!==undefined?("$"+spend[r.day]):""}));
  $("llm").innerHTML=table(llmRows,
   ["day","install_id","feature","calls","ok_calls","in_tokens",
    "out_tokens","est_day_usd"]);
}
$("go").onclick=async()=>{setTok($("tok").value.trim());
  try{await load()}catch(e){$("gate-err").textContent=
    "That token was rejected — check it and try again."}};
$("logout").onclick=()=>{setTok("");location.reload()};
if(tok()){load().catch(()=>{$("gate").style.display="block"})}
</script></body></html>"""


@app.get("/admin/view")
def admin_view():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_ADMIN_PAGE)
