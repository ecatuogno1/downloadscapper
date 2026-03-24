"""Tests for csv_download_client helpers."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import csv_download_client as client
from downloader.models import DownloadJob, RowTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_row(
    url: str = "https://example.com/file.zip",
    method: str = "GET",
    status: str = "queued",
    subdir_hint: str | None = None,
    filename_hint: str | None = None,
    expected_bytes: int | None = None,
) -> RowTask:
    return RowTask(
        row_number=1,
        raw={"url": url},
        download_url=url,
        method=method,
        request_data=(),
        filename_hint=filename_hint,
        subdir_hint=subdir_hint,
        referer=None,
        expected_bytes=expected_bytes,
        status=status,
    )


def make_job(
    output_dir: Path,
    options: dict | None = None,
    rows: list[RowTask] | None = None,
) -> DownloadJob:
    default_options = {
        "concurrency": 1,
        "collision_strategy": "unique",
        "use_subdirectories": False,
        "timeout_seconds": 30,
        "retry_attempts": 1,
        "request_spacing_seconds": 0.0,
        "resume": False,
        "dry_run": False,
        "dedup_by_hash": False,
        "verify_ssl": True,
        "proxy": "",
        "basic_auth": "",
        "cookies_file": "",
        "custom_headers": {},
    }
    if options:
        default_options.update(options)
    return DownloadJob(
        job_id="test-job",
        file_name="test.csv",
        output_dir=output_dir,
        options=default_options,
        mappings={"url": "url"},
        headers=["url"],
        rows=rows or [],
    )


# ---------------------------------------------------------------------------
# parse_csv_text
# ---------------------------------------------------------------------------

class TestParseCsvText:
    def test_basic(self):
        headers, rows = client.parse_csv_text("url,name\nhttps://ex.com/a.zip,Game A\n")
        assert headers == ["url", "name"]
        assert rows[0]["url"] == "https://ex.com/a.zip"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            client.parse_csv_text("")

    def test_no_data_rows_raises(self):
        with pytest.raises(ValueError):
            client.parse_csv_text("url,name\n")

    def test_strips_bom(self):
        headers, _ = client.parse_csv_text("\ufeffurl\nhttps://ex.com/a.zip\n")
        assert headers[0] == "url"


# ---------------------------------------------------------------------------
# detect_mappings
# ---------------------------------------------------------------------------

class TestDetectMappings:
    def test_detects_url_column(self):
        mappings = client.detect_mappings(["final_url", "filename"])
        assert mappings["url"] == "final_url"

    def test_detects_filename_column(self):
        mappings = client.detect_mappings(["url", "filename"])
        assert mappings["filename"] == "filename"

    def test_detects_method_column(self):
        mappings = client.detect_mappings(["url", "method"])
        assert mappings["method"] == "method"

    def test_no_columns_returns_none(self):
        mappings = client.detect_mappings(["col_a", "col_b"])
        assert mappings["url"] is None


# ---------------------------------------------------------------------------
# filename_from_url
# ---------------------------------------------------------------------------

class TestFilenameFromUrl:
    def test_simple_path(self):
        assert client.filename_from_url("https://example.com/file.zip") == "file.zip"

    def test_url_encoded_name(self):
        result = client.filename_from_url("https://example.com/My%20File.iso")
        assert result == "My File.iso"

    def test_trailing_slash_returns_none(self):
        assert client.filename_from_url("https://example.com/") is None

    def test_no_path_returns_none(self):
        assert client.filename_from_url("https://example.com") is None


# ---------------------------------------------------------------------------
# parse_request_data
# ---------------------------------------------------------------------------

class TestParseRequestData:
    def test_empty_returns_empty(self):
        assert client.parse_request_data(None) == ()
        assert client.parse_request_data("") == ()

    def test_url_encoded(self):
        result = client.parse_request_data("name=foo&value=bar")
        assert ("name", "foo") in result
        assert ("value", "bar") in result

    def test_json_object(self):
        result = client.parse_request_data('{"mediaId": "123", "alt": "0"}')
        assert ("mediaId", "123") in result

    def test_json_list_of_pairs(self):
        result = client.parse_request_data('[{"name": "id", "value": "42"}]')
        assert ("id", "42") in result


# ---------------------------------------------------------------------------
# build_row_tasks
# ---------------------------------------------------------------------------

class TestBuildRowTasks:
    def test_valid_row(self):
        rows = [{"url": "https://ex.com/file.zip"}]
        mappings = {"url": "url", "filename": None, "method": None,
                    "request_data": None, "subdir": None, "referer": None, "size_bytes": None}
        tasks = client.build_row_tasks(rows, mappings)
        assert len(tasks) == 1
        assert tasks[0].status == "queued"

    def test_missing_url_marks_invalid(self):
        rows = [{"url": ""}]
        mappings = {"url": "url", "filename": None, "method": None,
                    "request_data": None, "subdir": None, "referer": None, "size_bytes": None}
        tasks = client.build_row_tasks(rows, mappings)
        assert tasks[0].status == "invalid"

    def test_bad_scheme_marks_invalid(self):
        rows = [{"url": "ftp://example.com/file.zip"}]
        mappings = {"url": "url", "filename": None, "method": None,
                    "request_data": None, "subdir": None, "referer": None, "size_bytes": None}
        tasks = client.build_row_tasks(rows, mappings)
        assert tasks[0].status == "invalid"


# ---------------------------------------------------------------------------
# reserve_destination
# ---------------------------------------------------------------------------

class TestReserveDestination:
    def test_reserves_clean_path(self, tmp_path):
        job = make_job(tmp_path)
        path, err = client.reserve_destination(job, tmp_path, "file.zip", "unique")
        assert err is None
        assert path is not None
        assert str(path) in job.reserved_paths

    def test_unique_strategy_creates_suffix(self, tmp_path):
        (tmp_path / "file.zip").write_bytes(b"existing")
        job = make_job(tmp_path)
        job.reserved_paths.add(str(tmp_path / "file.zip"))
        path, err = client.reserve_destination(job, tmp_path, "file.zip", "unique")
        assert err is None
        assert path is not None
        assert "(1)" in path.name

    def test_skip_strategy_returns_error_for_existing(self, tmp_path):
        (tmp_path / "file.zip").write_bytes(b"existing")
        job = make_job(tmp_path)
        path, err = client.reserve_destination(job, tmp_path, "file.zip", "skip")
        assert path is None
        assert err is not None


# ---------------------------------------------------------------------------
# validate_subdir_path (from csv_download_client)
# ---------------------------------------------------------------------------

class TestValidateSubdirFromClient:
    def test_traversal_rejected(self):
        from csv_download_client import validate_subdir_path
        assert validate_subdir_path("../etc") is None

    def test_normal_subdir(self):
        from csv_download_client import validate_subdir_path
        assert validate_subdir_path("PS2") == "PS2"


# ---------------------------------------------------------------------------
# human_size (client copy)
# ---------------------------------------------------------------------------

class TestHumanSizeClient:
    def test_none(self):
        assert client.human_size(None) == "unknown"

    def test_megabytes(self):
        assert "MB" in client.human_size(5 * 1024 * 1024)


# ---------------------------------------------------------------------------
# _format_speed / _format_eta
# ---------------------------------------------------------------------------

class TestFormatSpeedClient:
    def test_kb_per_sec(self):
        assert "KB/s" in client._format_speed(1500)

    def test_negative(self):
        assert "?" in client._format_speed(-1)


class TestFormatEtaClient:
    def test_minutes(self):
        result = client._format_eta(90)
        assert "m" in result

    def test_none_empty(self):
        assert client._format_eta(None) == ""


# ---------------------------------------------------------------------------
# Dry-run integration (mocked network)
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_skips_actual_request(self, tmp_path):
        """Dry-run should mark the row as completed without making any HTTP request."""
        row = make_row()
        job = make_job(tmp_path, options={"dry_run": True})
        job.rows = [row]

        # Pre-reserve a destination so transfer_download has a target_path
        # We need to mock the opener.open call to return a fake response
        fake_headers = MagicMock()
        fake_headers.get.return_value = None
        fake_headers.get_content_type.return_value = "application/zip"

        fake_response = MagicMock()
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)
        fake_response.geturl.return_value = "https://example.com/file.zip"
        fake_response.headers = fake_headers
        fake_response.status = 200

        with patch("csv_download_client.build_opener_for_job") as mock_opener_factory:
            mock_opener = MagicMock()
            mock_opener.open.return_value = fake_response
            mock_opener_factory.return_value = mock_opener
            job.host_throttle = None
            client.transfer_download(job, row)

        assert row.status == "completed"
        assert "[dry-run]" in row.message
