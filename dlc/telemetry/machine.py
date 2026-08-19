"""
Stable, anonymous machine identity for telemetry and limits.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import subprocess
import uuid
from datetime import date
from pathlib import Path

_SALT = "dlc-v1:"


def _cache_path() -> Path:
    env = os.environ.get("DLC_MACHINE_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".dlc" / "machine.json"


def _raw_windows() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(val) or None
    except Exception:
        return None


def _raw_macos() -> str | None:
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('"')[-2] or None
    except Exception:
        pass
    return None


def _raw_linux() -> str | None:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            raw = Path(p).read_text().strip()
            if raw:
                return raw
        except OSError:
            continue
    return None


def _raw_machine_identifier() -> str | None:
    sysname = platform.system()
    if sysname == "Windows":
        return _raw_windows()
    if sysname == "Darwin":
        return _raw_macos()
    return _raw_linux()


def _stable_fallback() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return f"fallback:{platform.system()}:{platform.node()}:{user}"


def _digest(raw: str) -> str:
    return hashlib.sha256((_SALT + raw).encode()).hexdigest()[:16]


def machine_identity() -> dict:
    """
    -> {"install_id": <16 hex>, "issued": "YYYY-MM-DD",
           "source": "os"|"stable_fallback"|"random"}.

    The cache only shortcuts recomputation and remembers the locally
    issued date; a deleted cache regenerates the SAME install_id from
    the OS identifier.
    """
    cache = _cache_path()
    cached: dict = {}
    try:
        cached = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError):
        cached = {}

    raw = _raw_machine_identifier()
    if raw:
        install_id, source = _digest(raw), "os"
    else:
        fallback = _stable_fallback()
        if fallback.endswith(":unknown:") or fallback == "fallback:::":
            install_id, source = None, "random"
        else:
            install_id, source = _digest(fallback), "stable_fallback"
    if install_id is None:
        install_id = str(cached.get("install_id") or uuid.uuid4().hex[:16])
        source = cached.get("source", "random")

    issued = cached.get("issued")
    if cached.get("install_id") != install_id or not issued:
        issued = issued if cached.get("install_id") == install_id \
            else date.today().isoformat()
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(
                {"install_id": install_id, "issued": issued,
                 "source": source}, indent=2))
        except OSError:
            pass
    return {"install_id": install_id, "issued": issued, "source": source}


def install_id() -> str:
    return machine_identity()["install_id"]
