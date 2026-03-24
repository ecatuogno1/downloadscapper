"""Data model dataclasses for the DownloadScapper download pipeline.

All dataclasses are defined here so that other modules can import them without
creating circular dependencies.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RowTask:
    """Represents a single row from a CSV file mapped to a download task."""

    row_number: int
    raw: dict[str, str]
    download_url: str | None
    method: str
    request_data: tuple[tuple[str, str], ...]
    filename_hint: str | None
    subdir_hint: str | None
    referer: str | None
    expected_bytes: int | None
    status: str = "queued"
    message: str = "Queued"
    final_url: str | None = None
    output_path: str | None = None
    bytes_downloaded: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    task_kind: str = "http"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DownloadJob:
    """Container for a batch download job and its associated state."""

    job_id: str
    file_name: str
    output_dir: Path
    options: dict[str, Any]
    mappings: dict[str, str | None]
    headers: list[str]
    rows: list[RowTask]
    job_kind: str = "http"
    source: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    status: str = "queued"
    cancel_requested: bool = False
    logs: list[str] = field(default_factory=list)
    #: Paths currently being written to (collision prevention).
    reserved_paths: set[str] = field(default_factory=set)
    manifest_path: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    worker_thread: threading.Thread | None = field(default=None, repr=False)
    host_throttle: "HostThrottle | None" = field(default=None, repr=False)
    active_processes: list[subprocess.Popen[str]] = field(default_factory=list, repr=False)


@dataclass
class DiscoveryRecordItem:
    """A single discovered downloadable resource from a crawl."""

    record_id: str
    url: str
    final_url: str
    source_page: str
    reason: str
    anchor_text: str
    method: str
    request_data: tuple[tuple[str, str], ...]
    status_code: int | None
    content_type: str | None
    size_bytes: int | None
    size_human: str | None
    filename: str | None
    error_message: str | None
    inspection_status: str = "ready"


@dataclass
class DiscoveryJob:
    """State container for a web-crawl discovery job."""

    job_id: str
    source_type: str
    start_url: str
    scan_mode: str
    depth_limit: int
    profile: str
    source_file_name: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    status: str = "queued"
    summary: dict[str, Any] = field(default_factory=dict)
    pages: list[dict[str, Any]] = field(default_factory=list)
    records: list[DiscoveryRecordItem] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    artifact_dir: str | None = None
    csv_path: str | None = None
    json_path: str | None = None
    imported_csv_path: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    worker_thread: threading.Thread | None = field(default=None, repr=False)


class DownloadCancelled(Exception):
    """Raised inside a download worker to signal user cancellation."""


class DownloadRetryableError(Exception):
    """Raised when a download can be retried (incomplete body, transient error)."""


class HostThrottle:
    """Rate-limiter that spaces requests to the same host by a minimum interval."""

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self._lock = threading.Lock()
        self._next_allowed_by_host: dict[str, float] = {}

    def wait(self, url: str) -> None:
        if self.interval_seconds <= 0:
            return
        from urllib import parse as _parse
        host = _parse.urlparse(url).netloc.lower()
        if not host:
            return
        with self._lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, now)
            sleep_for = max(0.0, next_allowed - now)
            scheduled = max(now, next_allowed) + self.interval_seconds
            self._next_allowed_by_host[host] = scheduled
        if sleep_for > 0:
            time.sleep(sleep_for)
