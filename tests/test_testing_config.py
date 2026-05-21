"""Tests for dlc.testing.config """

import os
import json
from pathlib import Path
from unittest.mock import patch

from dlc.testing.config import (
    load_config, save_config, get_configured_jar, set_digital_jar_path,
)


def test_load_config_returns_empty_when_no_file_exists(tmp_path):
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        assert load_config() == {}


def test_save_then_load_roundtrip(tmp_path):
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        save_config({"digital_jar": "/path/to/Digital.jar", "other": 42})
        loaded = load_config()
    assert loaded == {"digital_jar": "/path/to/Digital.jar", "other": 42}


def test_corrupt_config_returns_empty_dict_not_raises(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("this is not JSON {")
    with patch("dlc.testing.config._config_file", return_value=cfg):
        assert load_config() == {}


def test_get_configured_jar_returns_path_when_set_and_exists(tmp_path):
    fake_jar = tmp_path / "Digital.jar"
    fake_jar.write_text("")
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        save_config({"digital_jar": str(fake_jar)})
        assert get_configured_jar() == str(fake_jar)


def test_get_configured_jar_returns_none_when_saved_path_missing(tmp_path):
    """If the student moved/deleted Digital.jar after setting it, fall
    through to auto-discovery rather than returning a stale path."""
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        save_config({"digital_jar": "/no/such/file.jar"})
        assert get_configured_jar() is None


def test_set_digital_jar_path_validates_path_exists(tmp_path):
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        try:
            set_digital_jar_path("/definitely/not/there.jar")
            assert False, "should have raised"
        except FileNotFoundError:
            pass


def test_set_digital_jar_path_writes_config(tmp_path):
    fake_jar = tmp_path / "Digital.jar"
    fake_jar.write_text("")
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        set_digital_jar_path(str(fake_jar))
        cfg = load_config()
    assert cfg["digital_jar"] == str(fake_jar)


def test_set_digital_jar_path_preserves_other_config_keys(tmp_path):
    fake_jar = tmp_path / "Digital.jar"
    fake_jar.write_text("")
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        save_config({"some_other_setting": "preserve_me"})
        set_digital_jar_path(str(fake_jar))
        cfg = load_config()
    assert cfg["digital_jar"] == str(fake_jar)
    assert cfg["some_other_setting"] == "preserve_me"

def test_prompt_for_jar_path_returns_none_when_user_cancels(tmp_path):
    from dlc.testing.config import prompt_for_jar_path
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        with patch("tkinter.filedialog.askopenfilename", return_value=""):
            with patch("tkinter.Tk"):
                assert prompt_for_jar_path() is None


def test_prompt_for_jar_path_saves_when_user_picks_file(tmp_path):
    fake_jar = tmp_path / "Digital.jar"
    fake_jar.write_text("")
    from dlc.testing.config import prompt_for_jar_path, get_configured_jar
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        with patch("tkinter.filedialog.askopenfilename",
                   return_value=str(fake_jar)):
            with patch("tkinter.Tk"):
                result = prompt_for_jar_path()
    assert result == str(fake_jar)
    assert get_configured_jar() == str(fake_jar)


def test_prompt_for_jar_path_handles_headless_environment(tmp_path):
    import tkinter as tk
    from dlc.testing.config import prompt_for_jar_path
    with patch("dlc.testing.config._config_dir", return_value=tmp_path):
        with patch("tkinter.Tk", side_effect=tk.TclError("no display")):
            assert prompt_for_jar_path() is None