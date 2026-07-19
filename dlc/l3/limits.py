"""Daily use-limit stub.

Caps: Mode A 3/day, Mode B 2/day. Counters ALWAYS tick — dev
telemetry should show real usage — but the BLOCK only applies when
DLC_ENFORCE_LIMITS is on, so release enforcement is a single config flip.

Mode A is 3 because of the ratified rerun-on-temp contract: 
when Mode B ends all-set with accepted rows living on the coach
temp, Mode A offers a re-run analysis of that temp. Starting that re-run
does NOT require a remaining use; it books +1 modeA only when it
completes — so a student doing the full loop still gets real Mode A runs.

a Mode B scan that finds test/circuit disagreements is a
REDIRECT to Mode A and consumes no usage. That is why ``allowed()`` and
``consume()`` are separate calls: the endpoint checks ``allowed()`` up
front, then consumes only after seeing an outcome that counts.

Storage: one tiny JSON file (``~/.dlc/limits.json``; ``DLC_LIMITS_PATH``
override keeps tests away from the developer's real home), keyed by local
date so counters reset at midnight. "Per user" means per machine until the
course proxy's per-student tokens arrive.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

CAPS = {"modeA": 3, "modeB": 2}


def limits_path() -> Path:
    env = os.environ.get("DLC_LIMITS_PATH")
    if env:
        return Path(env)
    return Path.home() / ".dlc" / "limits.json"


def enforced() -> bool:
    return os.environ.get("DLC_ENFORCE_LIMITS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load() -> dict:
    """Current counters; silently resets on a new day or a corrupt file."""
    p = limits_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("date") != _today():
            raise ValueError
        used = data.get("used")
        if not isinstance(used, dict):
            raise ValueError
        return {"date": data["date"],
                "used": {m: int(used.get(m, 0)) for m in CAPS}}
    except Exception:
        return {"date": _today(), "used": {m: 0 for m in CAPS}}


def _save(data: dict) -> None:
    p = limits_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass                       # limits must never break the app


def state() -> dict:
    """JSON-safe snapshot for endpoints/UI."""
    data = _load()
    used = data["used"]
    return {
        "enforced": enforced(),
        "date": data["date"],
        "caps": dict(CAPS),
        "used": dict(used),
        "remaining": {m: max(0, CAPS[m] - used.get(m, 0)) for m in CAPS},
    }


def allowed(mode: str) -> bool:
    """May a run of `mode` start now? Always True while enforcement is off."""
    if mode not in CAPS:
        return True
    if not enforced():
        return True
    return _load()["used"].get(mode, 0) < CAPS[mode]


def consume(mode: str) -> dict:
    """Tick one use of `mode` (even when unenforced — the counters are the
    telemetry) and return the fresh state()."""
    if mode in CAPS:
        data = _load()
        data["used"][mode] = data["used"].get(mode, 0) + 1
        _save(data)
    return state()