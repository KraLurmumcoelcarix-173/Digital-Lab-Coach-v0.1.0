"""
DLC user-level configuration
"""

import json
from pathlib import Path


def _config_dir() -> Path:
    return Path.home() / ".dlc"


def _config_file() -> Path:
    return _config_dir() / "config.json"


def load_config() -> dict:
    p = _config_file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(d: dict) -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)
    _config_file().write_text(json.dumps(d, indent=2))


def get_configured_jar() -> str | None:
    p = load_config().get("digital_jar")
    if p and Path(p).exists():
        return p
    return None


def set_digital_jar_path(path: str) -> None:
    """One-time setup: save Digital.jar's location to ~/.dlc/config.json."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No file at {path}")
    cfg = load_config()
    cfg["digital_jar"] = str(p)
    save_config(cfg)

    

def prompt_for_jar_path() -> str | None:
    """Pop up a native file picker so the student can locate Digital.jar
    without typing anything."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None

    try:
        root = tk.Tk()
        root.withdraw()       
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Locate Digital.jar",
            filetypes=[("Java JAR", "*.jar"), ("All files", "*.*")],
        )
        root.destroy()
    except tk.TclError:
        return None

    if not path:
        return None              
    set_digital_jar_path(path)
    return path