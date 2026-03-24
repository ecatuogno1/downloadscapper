"""Tests for the config module."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from config import DEFAULT_CONFIG, _deep_merge, _simple_toml_parse, load_config


class TestDeepMerge:
    def test_scalar_override(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {"b": 99})
        assert base["b"] == 99
        assert base["a"] == 1

    def test_nested_merge(self):
        base = {"ui": {"host": "127.0.0.1", "port": 8765}}
        _deep_merge(base, {"ui": {"port": 9000}})
        assert base["ui"]["port"] == 9000
        assert base["ui"]["host"] == "127.0.0.1"

    def test_new_key_added(self):
        base = {"a": 1}
        _deep_merge(base, {"b": 2})
        assert base["b"] == 2

    def test_empty_override(self):
        base = {"a": 1}
        _deep_merge(base, {})
        assert base == {"a": 1}


class TestSimpleTomlParse:
    def test_parses_sections(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("[ui]\nport = 9000\n", encoding="utf-8")
        result = _simple_toml_parse(f)
        assert result["ui"]["port"] == 9000

    def test_parses_booleans(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("[ui]\nno_browser = true\n", encoding="utf-8")
        result = _simple_toml_parse(f)
        assert result["ui"]["no_browser"] is True

    def test_parses_string_values(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text('[downloads]\nproxy = "http://proxy:8080"\n', encoding="utf-8")
        result = _simple_toml_parse(f)
        assert result["downloads"]["proxy"] == "http://proxy:8080"

    def test_ignores_comments(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("# This is a comment\n[ui]\nport = 8000\n", encoding="utf-8")
        result = _simple_toml_parse(f)
        assert result["ui"]["port"] == 8000

    def test_parses_floats(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("[downloads]\nrequest_spacing_seconds = 1.5\n", encoding="utf-8")
        result = _simple_toml_parse(f)
        assert result["downloads"]["request_spacing_seconds"] == 1.5


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        # Point config search to a directory with no config.toml
        monkeypatch.chdir(tmp_path)
        cfg = load_config(config_path=tmp_path / "nonexistent.toml")
        assert cfg["ui"]["port"] == DEFAULT_CONFIG["ui"]["port"]

    def test_merges_file_over_defaults(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("[ui]\nport = 9999\n", encoding="utf-8")
        cfg = load_config(config_path=f)
        assert cfg["ui"]["port"] == 9999
        # Other defaults should remain
        assert cfg["ui"]["host"] == DEFAULT_CONFIG["ui"]["host"]

    def test_does_not_mutate_defaults(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("[ui]\nport = 1234\n", encoding="utf-8")
        original_port = DEFAULT_CONFIG["ui"]["port"]
        load_config(config_path=f)
        assert DEFAULT_CONFIG["ui"]["port"] == original_port

    def test_corrupt_file_returns_defaults(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_bytes(b"\xff\xfe invalid utf-8 \x00")
        cfg = load_config(config_path=f)
        # Should silently fall back to defaults
        assert "ui" in cfg
