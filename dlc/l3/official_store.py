from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULTS_PATH = (Path(__file__).parent.parent.parent
                  / "data" / "official_tests_defaults.json")


def store_path() -> Path:
    env = os.environ.get("DLC_OFFICIAL_TESTS_PATH")
    return Path(env) if env else Path.home() / ".dlc" / "official_tests.json"

import re as _re

_TEST_KEYWORD_RE = _re.compile(
    r"(?i)^(let|repeat|loop|while|init|declare|memory|program|"
    r"resetrandom|end)\b")


def validate_test_content(content: str) -> None:
    from dlc.testing.spec import _strip_inline_comment, _tokenize
    header: list[str] | None = None
    data_rows = 0
    for i, line in enumerate((content or "").splitlines(), start=1):
        text = _strip_inline_comment(line).strip()
        if not text:
            continue
        cells = text.split()
        if header is None:
            kinds = {_tokenize(c).kind for c in cells}
            if kinds <= {"int", "clock", "highZ", "dontcare", "loop_expr"}:
                raise ValueError(
                    "not Digital test format: the first line must be the "
                    "header (signal names), but line "
                    f"{i} looks like a value row: {text!r}")
            header = cells
            continue
        if _TEST_KEYWORD_RE.match(cells[0]):
            data_rows += 1
            continue
        bad = [c for c in cells if _tokenize(c).kind == "unknown"]
        if bad:
            raise ValueError(
                f"not Digital test format: line {i} has unrecognized "
                f"cell{'s' if len(bad) > 1 else ''} "
                f"{', '.join(repr(b) for b in bad)}: {text!r}")
        if len(cells) != len(header):
            raise ValueError(
                f"not Digital test format: line {i} has {len(cells)} "
                f"cells but the header has {len(header)} columns: {text!r}")
        data_rows += 1
    if header is None:
        raise ValueError("not Digital test format: no header line found.")
    if data_rows == 0:
        raise ValueError("not Digital test format: a header but no test "
                         "rows.")


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
        return {}


def _save(data: dict[str, dict]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")


def list_tests() -> list[dict]:
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


def save_test(filename: str, content: str, *,
              allow_default_override: bool = False) -> dict:
    from dlc.l3.manifest import normalized_test_hash
    name = (filename or "").strip()
    if not name:
        raise ValueError("Filename is required.")
    if "/" in name or "\\" in name or not name.lower().endswith(".dig"):
        raise ValueError("Filename must be a plain .dig name, e.g. cpu.dig")
    if not (content or "").strip():
        raise ValueError("Testcase content is required.")
    if name in _defaults() and not allow_default_override:
        raise ValueError(
            f"'{name}' is a built-in default and can't be edited by hand — "
            f"run Mode B to all-set and use 'Adopt into official tests' to "
            f"extend it.")
    validate_test_content(content)
    data = _load()
    entry = {"content": content, "sha1": normalized_test_hash(content)}
    data[name] = entry
    _save(data)
    return {"filename": name, **entry}


def delete_test(filename: str) -> bool:
    data = _load()
    if filename not in data:
        return False
    del data[filename]
    _save(data)
    return True


def get_content(filename: str) -> str | None:
    entry = _load().get(filename) or _defaults().get(filename)
    content = (entry or {}).get("content")
    return content if (content or "").strip() else None


def get_runtime_payload(filename: str, key: str) -> str | None:
    import base64
    entry = _defaults().get(filename) or {}
    blob = entry.get("runtime")
    if not blob:
        return None
    try:
        data = json.loads(base64.b64decode(blob).decode("utf-8"))
    except Exception:
        return None
    v = data.get(key) if isinstance(data, dict) else None
    return v if isinstance(v, str) and v.strip() else None


def status_for(filename: str, raw_data_string: str) -> str | None:
    entry = _load().get(filename) or _defaults().get(filename)
    if not entry or not entry.get("sha1"):
        return None
    from dlc.l3.manifest import normalized_test_hash
    return ("official"
            if normalized_test_hash(raw_data_string) == entry["sha1"]
            else "modified")
