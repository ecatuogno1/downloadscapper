"""Tests for project_paths platform-aware directory resolution."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from project_paths import (
    APP_DATA_DIR,
    DEFAULT_DOWNLOADS_ROOT,
    DEFAULT_SCRAPE_SAVE_DIR,
    DEFAULT_STATE_DIR,
    _downloads_dir,
    _user_data_dir,
)


class TestUserDataDir:
    def test_returns_path(self):
        result = _user_data_dir("myapp")
        assert isinstance(result, Path)

    def test_ends_with_app_name(self):
        result = _user_data_dir("myapp")
        assert result.name == "myapp"

    def test_different_apps_different_paths(self):
        assert _user_data_dir("app1") != _user_data_dir("app2")


class TestDownloadsDir:
    def test_returns_path(self):
        assert isinstance(_downloads_dir(), Path)

    def test_path_is_absolute(self):
        assert _downloads_dir().is_absolute()


class TestDefaultPaths:
    def test_app_data_dir_is_absolute(self):
        assert APP_DATA_DIR.is_absolute()

    def test_downloads_root_is_absolute(self):
        assert DEFAULT_DOWNLOADS_ROOT.is_absolute()

    def test_scrape_save_dir_is_absolute(self):
        assert DEFAULT_SCRAPE_SAVE_DIR.is_absolute()

    def test_state_dir_is_absolute(self):
        assert DEFAULT_STATE_DIR.is_absolute()

    def test_downloads_root_under_downloads(self):
        # Downloads root should be somewhere under the user's Downloads folder
        # or the app data dir — just verify it contains "downloadscapper"
        assert "downloadscapper" in str(DEFAULT_DOWNLOADS_ROOT).lower() or \
               "csv-download" in str(DEFAULT_DOWNLOADS_ROOT).lower()
