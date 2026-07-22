"""User-configured official-test store (Settings ⚙ → Official tests).

The shipped manifests carry lab fingerprints, but the tool is meant
for anyone teaching with Digital: any instructor (or student) can register
their own official test sets locally — filename + the testcase content —
and Mode B then classifies disagreements on those files exactly like it
does for manifest-fingerprinted ones. The store is the instructor's truth
and takes precedence over manifest fingerprints.

Storage: one JSON file, ~/.dlc/official_tests.json (override with the
DLC_OFFICIAL_TESTS_PATH env var — tests point it into tmp):

    {"cpu.dig": {"content": "<dataString text>", "sha1": "<normalized>"}}

Matching is by FILENAME + normalized content hash (comments/whitespace
ignored — same normalization as the manifest fingerprints), so a cosmetic
edit doesn't break "official" while a changed row does.

a DEFAULTS layer ships with the tool (data/official_tests_defaults.json
— currently the lab5 cpu.dig + register-file.dig instructor tests, added at
the course staff's direction). Defaults are always present, release builds
included; a user entry with the same filename OVERRIDES its default, and
deleting the override reverts to the default (defaults themselves cannot
be deleted).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULTS_PATH = (Path(__file__).parent.parent.parent
                  / "data" / "official_tests_defaults.json")


def store_path() -> Path:
    env = os.environ.get("DLC_OFFICIAL_TESTS_PATH")
    return Path(env) if env else Path.home() / ".dlc" / "official_tests.json"


def _defaults() -> dict[str, dict]:
    env = os.environ.get("DLC_OFFICIAL_DEFAULTS_PATH")
    p = Path(env) if env else _DEFAULTS_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {name: e for name, e in data.items()
            if isinstance(e, dict) and not name.startswith("_")}


def _load() -> dict[str, dict]:
    p = store_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}                    # a corrupt store never breaks a scan


def _save(data: dict[str, dict]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")


def list_tests() -> list[dict]:
    """[{filename, sha1, content, source}], filename-sorted — the Settings
    list. source: 'default' (shipped), 'user', or 'override' (a user entry
    shadowing a default; deleting it reverts to the default)."""
    user = _load()
    merged: dict[str, dict] = {}
    for name, e in _defaults().items():
        merged[name] = {**e, "_source": "default"}
    for name, e in user.items():
        merged[name] = {**e,
                        "_source": "override" if name in merged else "user"}
    return [{"filename": name, "sha1": e.get("sha1", ""),
             "content": e.get("content", ""), "source": e["_source"]}
            for name, e in sorted(merged.items())]


def save_test(filename: str, content: str) -> dict:
    """Add or update one official test set. Returns the stored entry."""
    from dlc.l3.manifest import normalized_test_hash
    name = (filename or "").strip()
    if not name:
        raise ValueError("Filename is required.")
    if not (content or "").strip():
        raise ValueError("Testcase content is required.")
    data = _load()
    entry = {"content": content, "sha1": normalized_test_hash(content)}
    data[name] = entry
    _save(data)
    return {"filename": name, **entry}


def delete_test(filename: str) -> bool:
    """Delete a USER entry (defaults are permanent — deleting an override
    reverts the filename to its shipped default)."""
    data = _load()
    if filename not in data:
        return False
    del data[filename]
    _save(data)
    return True


def status_for(filename: str, raw_data_string: str) -> str | None:
    """'official' | 'modified' when the store (user entry first, then the
    shipped defaults) has this filename, else None (store silent => the
    manifest fingerprints get their turn)."""
    entry = _load().get(filename) or _defaults().get(filename)
    if not entry or not entry.get("sha1"):
        return None
    from dlc.l3.manifest import normalized_test_hash
    return ("official"
            if normalized_test_hash(raw_data_string) == entry["sha1"]
            else "modified")
