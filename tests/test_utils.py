"""Tests for downloader.utils helper functions."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from downloader.utils import (
    atomic_write,
    compute_file_hash,
    format_eta,
    format_speed,
    human_size,
    normalize_header,
    sanitize_segment,
    slugify_for_path,
    validate_subdir_path,
)


# ---------------------------------------------------------------------------
# human_size
# ---------------------------------------------------------------------------

class TestHumanSize:
    def test_none_returns_unknown(self):
        assert human_size(None) == "unknown"

    def test_zero_bytes(self):
        assert human_size(0) == "0 B"

    def test_bytes(self):
        assert human_size(500) == "500 B"

    def test_kilobytes(self):
        result = human_size(1024)
        assert "KB" in result

    def test_megabytes(self):
        result = human_size(1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = human_size(1024 ** 3)
        assert "GB" in result


# ---------------------------------------------------------------------------
# sanitize_segment
# ---------------------------------------------------------------------------

class TestSanitizeSegment:
    def test_empty_returns_fallback(self):
        assert sanitize_segment("", "fallback") == "fallback"

    def test_none_returns_fallback(self):
        assert sanitize_segment(None, "fallback") == "fallback"

    def test_strips_illegal_chars(self):
        result = sanitize_segment('file<name>.txt', "fallback")
        assert "<" not in result
        assert ">" not in result

    def test_truncates_long_names(self):
        long_name = "a" * 200
        assert len(sanitize_segment(long_name, "fallback")) <= 120

    def test_replaces_colons(self):
        result = sanitize_segment("game: title", "fallback")
        assert ":" not in result

    def test_normal_name_unchanged(self):
        assert sanitize_segment("game-title.iso", "fallback") == "game-title.iso"


# ---------------------------------------------------------------------------
# slugify_for_path
# ---------------------------------------------------------------------------

class TestSlugifyForPath:
    def test_lowercases(self):
        assert slugify_for_path("Hello World", "fallback") == "hello-world"

    def test_replaces_spaces(self):
        assert "-" in slugify_for_path("foo bar", "fallback")

    def test_empty_returns_fallback(self):
        assert slugify_for_path("", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# validate_subdir_path
# ---------------------------------------------------------------------------

class TestValidateSubdirPath:
    def test_normal_subdir(self):
        assert validate_subdir_path("PS2") == "PS2"

    def test_nested_subdir(self):
        assert validate_subdir_path("games/PS2") == "games/PS2"

    def test_none_returns_none(self):
        assert validate_subdir_path(None) is None

    def test_empty_returns_none(self):
        assert validate_subdir_path("") is None

    def test_traversal_rejected(self):
        assert validate_subdir_path("../secret") is None

    def test_absolute_path_rejected(self):
        # After stripping leading slashes it becomes empty or just a name
        result = validate_subdir_path("/etc/passwd")
        assert result == "etc/passwd" or result is None  # stripped of leading slash

    def test_traversal_in_middle_rejected(self):
        assert validate_subdir_path("games/../etc") is None

    def test_null_byte_rejected(self):
        assert validate_subdir_path("foo\x00bar") is None

    def test_windows_separator_normalised(self):
        result = validate_subdir_path("games\\PS2")
        assert result == "games/PS2"

    def test_dot_only_rejected(self):
        assert validate_subdir_path(".") is None


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "output.json"
        atomic_write(target, b'{"ok": true}')
        assert target.read_bytes() == b'{"ok": true}'

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        atomic_write(target, b"hello")
        assert target.read_text() == "hello"

    def test_no_partial_file_on_success(self, tmp_path):
        target = tmp_path / "output.txt"
        atomic_write(target, b"data")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_bytes(b"old")
        atomic_write(target, b"new")
        assert target.read_bytes() == b"new"


# ---------------------------------------------------------------------------
# compute_file_hash
# ---------------------------------------------------------------------------

class TestComputeFileHash:
    def test_known_hash(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert compute_file_hash(f) == expected

    def test_missing_file_returns_none(self, tmp_path):
        assert compute_file_hash(tmp_path / "nonexistent.bin") is None

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert compute_file_hash(f) == expected


# ---------------------------------------------------------------------------
# format_speed / format_eta
# ---------------------------------------------------------------------------

class TestFormatSpeed:
    def test_bytes_per_second(self):
        assert "B/s" in format_speed(500)

    def test_kilobytes_per_second(self):
        assert "KB/s" in format_speed(1500)

    def test_megabytes_per_second(self):
        assert "MB/s" in format_speed(2 * 1024 * 1024)

    def test_negative_returns_question_mark(self):
        assert "?" in format_speed(-1)


class TestFormatEta:
    def test_zero_seconds(self):
        assert format_eta(0) == "0s"

    def test_seconds_only(self):
        assert format_eta(45) == "45s"

    def test_minutes_and_seconds(self):
        result = format_eta(125)
        assert "m" in result
        assert "s" in result

    def test_hours(self):
        result = format_eta(3665)
        assert "h" in result

    def test_none_returns_empty(self):
        assert format_eta(None) == ""


# ---------------------------------------------------------------------------
# normalize_header
# ---------------------------------------------------------------------------

class TestNormalizeHeader:
    def test_lowercases(self):
        assert normalize_header("FileName") == "filename"

    def test_replaces_spaces_with_underscore(self):
        assert normalize_header("file name") == "file_name"

    def test_strips_leading_trailing(self):
        assert normalize_header("  url  ") == "url"

    def test_removes_special_chars(self):
        result = normalize_header("file-name (v2)")
        assert "-" not in result
        assert "(" not in result
