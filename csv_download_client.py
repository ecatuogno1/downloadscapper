#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import email.utils
import io
import json
import mimetypes
import os
import shutil
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from project_paths import default_workspace_dir
from website_download_summary import CrawlLink, DownloadRecord, RequestSettings, run_discovery

APP_NAME = "DownloadScapper"
USER_AGENT = "Mozilla/5.0 (compatible; CSVDownloadClient/1.0; +http://127.0.0.1)"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
DEFAULT_WORKERS = 1
DEFAULT_TIMEOUT = 60
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "csv-download-client"
DEFAULT_SCRAPE_SAVE_DIR = Path.home() / "Downloads" / "downloadscapper-scrapes"
DEFAULT_STATE_DIR = Path.home() / "Downloads" / "downloadscapper-state"
CHUNK_SIZE = 64 * 1024
LOG_LIMIT = 200
RECENT_ROWS_LIMIT = 24
PREVIEW_ROW_LIMIT = 8
STATIC_DIR = Path(__file__).resolve().parent / "download_client_ui"
DEFAULT_DATABASE_PATH = default_workspace_dir() / "download-index.sqlite3"
DEFAULT_DOWNLOAD_RETRIES = 4
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_REQUEST_SPACING_SECONDS = 2.0
DEFAULT_DISCOVERY_MAX_PAGES = 250
DEFAULT_DISCOVERY_WORKERS = 6
MAX_HISTORY_ITEMS = 20
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

URL_COLUMN_CANDIDATES = [
    "final_url",
    "download_url",
    "url",
    "link",
    "href",
]
FILENAME_COLUMN_CANDIDATES = [
    "filename",
    "file_name",
    "name",
    "title",
]
METHOD_COLUMN_CANDIDATES = [
    "method",
    "http_method",
]
REQUEST_DATA_COLUMN_CANDIDATES = [
    "request_data",
    "form_data",
    "payload",
    "body",
    "post_data",
]
SUBDIR_COLUMN_CANDIDATES = [
    "system_name",
    "folder",
    "subdirectory",
    "subdir",
    "category",
    "group",
]
REFERER_COLUMN_CANDIDATES = [
    "source_page",
    "referer",
    "referrer",
]
SIZE_COLUMN_CANDIDATES = [
    "size_bytes",
    "bytes",
    "content_length",
]


@dataclass
class RowTask:
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
    reserved_paths: set[str] = field(default_factory=set)
    manifest_path: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    worker_thread: threading.Thread | None = field(default=None, repr=False)
    host_throttle: "HostThrottle | None" = field(default=None, repr=False)
    active_processes: list[subprocess.Popen[str]] = field(default_factory=list, repr=False)


@dataclass
class DiscoveryRecordItem:
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
    pass


class DownloadRetryableError(Exception):
    pass


class HostThrottle:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self._lock = threading.Lock()
        self._next_allowed_by_host: dict[str, float] = {}

    def wait(self, url: str) -> None:
        if self.interval_seconds <= 0:
            return
        host = parse.urlparse(url).netloc.lower()
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


class AppState:
    def __init__(
        self,
        default_output_dir: Path,
        scrape_save_dir: Path,
        state_dir: Path,
        timeout_seconds: int,
    ) -> None:
        self.default_output_dir = default_output_dir
        self.scrape_save_dir = scrape_save_dir
        self.state_dir = state_dir
        self.timeout_seconds = timeout_seconds
        self.started_at = time.time()
        self._jobs: dict[str, DownloadJob] = {}
        self._discovery_jobs: dict[str, DiscoveryJob] = {}
        self._lock = threading.Lock()
        (self.state_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "discovery").mkdir(parents=True, exist_ok=True)

    def add_job(self, job: DownloadJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job
        self.persist_job(job)

    def get_job(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def add_discovery_job(self, job: DiscoveryJob) -> None:
        with self._lock:
            self._discovery_jobs[job.job_id] = job
        self.persist_discovery_job(job)

    def get_discovery_job(self, job_id: str) -> DiscoveryJob | None:
        with self._lock:
            return self._discovery_jobs.get(job_id)

    def list_jobs(self) -> list[DownloadJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def list_discovery_jobs(self) -> list[DiscoveryJob]:
        with self._lock:
            return sorted(self._discovery_jobs.values(), key=lambda job: job.created_at, reverse=True)

    def _job_path(self, job_id: str) -> Path:
        return self.state_dir / "jobs" / f"{job_id}.json"

    def _discovery_job_path(self, job_id: str) -> Path:
        return self.state_dir / "discovery" / f"{job_id}.json"

    def persist_job(self, job: DownloadJob) -> None:
        self._job_path(job.job_id).write_text(
            json.dumps(serialize_job(job), indent=2),
            encoding="utf-8",
        )

    def persist_discovery_job(self, job: DiscoveryJob) -> None:
        self._discovery_job_path(job.job_id).write_text(
            json.dumps(serialize_discovery_job(job), indent=2),
            encoding="utf-8",
        )

    def load_persisted_state(self) -> None:
        for path in sorted((self.state_dir / "jobs").glob("*.json")):
            try:
                job = deserialize_job(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            with self._lock:
                self._jobs[job.job_id] = job
        for path in sorted((self.state_dir / "discovery").glob("*.json")):
            try:
                job = deserialize_discovery_job(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            with self._lock:
                self._discovery_jobs[job.job_id] = job


STATE: AppState | None = None


def timestamp_to_iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def human_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "unknown"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def detect_site_profile(start_url: str) -> str:
    parsed = parse.urlparse(start_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host.endswith("vimm.net") or "/vault" in path:
        return "vimm"
    return "generic"


def log_discovery(job: DiscoveryJob, message: str) -> None:
    line = f"{datetime.now().astimezone().strftime('%H:%M:%S')} {message}"
    with job.lock:
        job.logs.append(line)
        if len(job.logs) > LOG_LIMIT:
            job.logs[:] = job.logs[-LOG_LIMIT:]
    if STATE is not None:
        STATE.persist_discovery_job(job)


def discovery_record_to_payload(record: DiscoveryRecordItem) -> dict[str, Any]:
    return {
        "id": record.record_id,
        "url": record.url,
        "final_url": record.final_url,
        "source_page": record.source_page,
        "reason": record.reason,
        "anchor_text": record.anchor_text,
        "method": record.method,
        "request_data": [{"name": key, "value": value} for key, value in record.request_data],
        "status_code": record.status_code,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "size_human": record.size_human,
        "filename": record.filename,
        "error_message": record.error_message,
        "inspection_status": record.inspection_status,
    }


def summarize_discovery(job: DiscoveryJob) -> dict[str, Any]:
    with job.lock:
        return {
            "job_id": job.job_id,
            "source_type": job.source_type,
            "start_url": job.start_url,
            "scan_mode": job.scan_mode,
            "depth_limit": job.depth_limit,
            "profile": job.profile,
            "source_file_name": job.source_file_name,
            "status": job.status,
            "created_at": timestamp_to_iso(job.created_at),
            "started_at": timestamp_to_iso(job.started_at),
            "finished_at": timestamp_to_iso(job.finished_at),
            "summary": dict(job.summary),
            "pages": [dict(page) for page in job.pages],
            "records": [discovery_record_to_payload(record) for record in job.records],
            "artifacts": {
                "directory": job.artifact_dir,
                "csv_path": job.csv_path,
                "json_path": job.json_path,
                "imported_csv_path": job.imported_csv_path,
            },
            "logs": list(job.logs[-40:]),
            "error": job.error,
        }


def summarize_discovery_history_item(job: DiscoveryJob) -> dict[str, Any]:
    with job.lock:
        summary = dict(job.summary)
        return {
            "job_id": job.job_id,
            "source_type": job.source_type,
            "start_url": job.start_url,
            "scan_mode": job.scan_mode,
            "depth_limit": job.depth_limit,
            "profile": job.profile,
            "source_file_name": job.source_file_name,
            "status": job.status,
            "created_at": timestamp_to_iso(job.created_at),
            "started_at": timestamp_to_iso(job.started_at),
            "finished_at": timestamp_to_iso(job.finished_at),
            "summary": {
                "download_links_found": summary.get("download_links_found", len(job.records)),
                "pages_scanned": summary.get("pages_scanned", 0),
                "known_sizes": summary.get("known_sizes", 0),
                "unknown_sizes": summary.get("unknown_sizes", 0),
                "total_known_bytes": summary.get("total_known_bytes", 0),
                "total_known_human": summary.get("total_known_human", "0 B"),
                "candidate_count": summary.get("candidate_count"),
                "inspected_count": summary.get("inspected_count"),
            },
            "artifacts": {
                "directory": job.artifact_dir,
                "csv_path": job.csv_path,
                "json_path": job.json_path,
                "imported_csv_path": job.imported_csv_path,
            },
            "error": job.error,
        }


def serialize_row_task(row: RowTask) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "raw": dict(row.raw),
        "download_url": row.download_url,
        "method": row.method,
        "request_data": list(row.request_data),
        "filename_hint": row.filename_hint,
        "subdir_hint": row.subdir_hint,
        "referer": row.referer,
        "expected_bytes": row.expected_bytes,
        "status": row.status,
        "message": row.message,
        "final_url": row.final_url,
        "output_path": row.output_path,
        "bytes_downloaded": row.bytes_downloaded,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "updated_at": row.updated_at,
        "task_kind": row.task_kind,
        "metadata": dict(row.metadata),
    }


def deserialize_row_task(payload: dict[str, Any]) -> RowTask:
    return RowTask(
        row_number=int(payload.get("row_number") or 0),
        raw={str(key): str(value or "") for key, value in (payload.get("raw") or {}).items()},
        download_url=payload.get("download_url"),
        method=str(payload.get("method") or "GET"),
        request_data=tuple(
            (str(name), str(value))
            for name, value in (payload.get("request_data") or [])
        ),
        filename_hint=payload.get("filename_hint"),
        subdir_hint=payload.get("subdir_hint"),
        referer=payload.get("referer"),
        expected_bytes=payload.get("expected_bytes"),
        status=str(payload.get("status") or "queued"),
        message=str(payload.get("message") or "Queued"),
        final_url=payload.get("final_url"),
        output_path=payload.get("output_path"),
        bytes_downloaded=int(payload.get("bytes_downloaded") or 0),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        updated_at=float(payload.get("updated_at") or time.time()),
        task_kind=str(payload.get("task_kind") or "http"),
        metadata=dict(payload.get("metadata") or {}),
    )


def serialize_job(job: DownloadJob) -> dict[str, Any]:
    with job.lock:
        return {
            "job_id": job.job_id,
            "file_name": job.file_name,
            "output_dir": str(job.output_dir),
            "options": dict(job.options),
            "mappings": dict(job.mappings),
            "headers": list(job.headers),
            "rows": [serialize_row_task(row) for row in job.rows],
            "job_kind": job.job_kind,
            "source": dict(job.source),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "status": job.status,
            "cancel_requested": job.cancel_requested,
            "logs": list(job.logs),
            "manifest_path": job.manifest_path,
        }


def deserialize_job(payload: dict[str, Any]) -> DownloadJob:
    rows = [deserialize_row_task(item) for item in payload.get("rows") or []]
    job = DownloadJob(
        job_id=str(payload.get("job_id") or uuid.uuid4().hex[:8]),
        file_name=str(payload.get("file_name") or "restored-job"),
        output_dir=Path(payload.get("output_dir") or DEFAULT_OUTPUT_DIR).expanduser().resolve(),
        options=dict(payload.get("options") or {}),
        mappings={str(key): value for key, value in (payload.get("mappings") or {}).items()},
        headers=[str(item) for item in (payload.get("headers") or [])],
        rows=rows,
        job_kind=str(payload.get("job_kind") or "http"),
        source=dict(payload.get("source") or {}),
    )
    with job.lock:
        job.created_at = float(payload.get("created_at") or time.time())
        job.started_at = payload.get("started_at")
        job.finished_at = payload.get("finished_at")
        job.status = str(payload.get("status") or "completed")
        job.cancel_requested = bool(payload.get("cancel_requested"))
        job.logs = [str(item) for item in (payload.get("logs") or [])]
        job.manifest_path = payload.get("manifest_path")
    if job.status in {"queued", "running"}:
        with job.lock:
            job.status = "cancelled"
            job.finished_at = time.time()
            job.cancel_requested = True
        for row in job.rows:
            if row.status in {"queued", "active"}:
                row.status = "cancelled"
                row.message = "Interrupted by application restart"
                row.finished_at = time.time()
                row.updated_at = time.time()
    return job


def serialize_discovery_job(job: DiscoveryJob) -> dict[str, Any]:
    payload = summarize_discovery(job)
    payload["created_at_raw"] = job.created_at
    payload["started_at_raw"] = job.started_at
    payload["finished_at_raw"] = job.finished_at
    return payload


def deserialize_discovery_job(payload: dict[str, Any]) -> DiscoveryJob:
    job = DiscoveryJob(
        job_id=str(payload.get("job_id") or uuid.uuid4().hex[:8]),
        source_type=str(payload.get("source_type") or "url"),
        start_url=str(payload.get("start_url") or ""),
        scan_mode=str(payload.get("scan_mode") or "single_page"),
        depth_limit=int(payload.get("depth_limit") or 0),
        profile=str(payload.get("profile") or "generic"),
        source_file_name=payload.get("source_file_name"),
    )
    with job.lock:
        job.created_at = float(payload.get("created_at_raw") or time.time())
        job.started_at = payload.get("started_at_raw")
        job.finished_at = payload.get("finished_at_raw")
        job.status = str(payload.get("status") or "completed")
        job.summary = dict(payload.get("summary") or {})
        job.pages = [dict(item) for item in (payload.get("pages") or [])]
        job.records = [
            DiscoveryRecordItem(
                record_id=str(item.get("id") or ""),
                url=str(item.get("url") or ""),
                final_url=str(item.get("final_url") or ""),
                source_page=str(item.get("source_page") or ""),
                reason=str(item.get("reason") or ""),
                anchor_text=str(item.get("anchor_text") or ""),
                method=str(item.get("method") or "GET"),
                request_data=tuple(
                    (str(pair.get("name") or ""), str(pair.get("value") or ""))
                    for pair in (item.get("request_data") or [])
                ),
                status_code=item.get("status_code"),
                content_type=item.get("content_type"),
                size_bytes=item.get("size_bytes"),
                size_human=item.get("size_human"),
                filename=item.get("filename"),
                error_message=item.get("error_message"),
                inspection_status=str(item.get("inspection_status") or "ready"),
            )
            for item in (payload.get("records") or [])
        ]
        job.logs = [str(item) for item in (payload.get("logs") or [])]
        job.error = payload.get("error")
        artifacts = payload.get("artifacts") or {}
        job.artifact_dir = artifacts.get("directory")
        job.csv_path = artifacts.get("csv_path")
        job.json_path = artifacts.get("json_path")
        job.imported_csv_path = artifacts.get("imported_csv_path")
    if job.status == "running":
        with job.lock:
            job.status = "failed"
            job.error = "Interrupted by application restart"
            job.finished_at = time.time()
    return job


def build_discovery_pages(records: list[DiscoveryRecordItem]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        page = grouped.setdefault(
            record.source_page,
            {
                "source_page": record.source_page,
                "item_count": 0,
                "known_total_bytes": 0,
                "known_total_human": "0 B",
                "record_ids": [],
            },
        )
        page["item_count"] += 1
        page["record_ids"].append(record.record_id)
        if record.size_bytes is not None:
            page["known_total_bytes"] += record.size_bytes

    pages = list(grouped.values())
    for page in pages:
        page["known_total_human"] = human_size(page["known_total_bytes"])
    pages.sort(
        key=lambda item: (-int(item["known_total_bytes"]), -int(item["item_count"]), str(item["source_page"]))
    )
    return pages


def discovery_record_id(url: str, method: str, request_data: tuple[tuple[str, str], ...]) -> str:
    key = f"{method.upper()}|{url}|{request_data_to_text(request_data)}"
    return f"rec-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]}"


def build_discovery_summary_from_items(
    start_url: str,
    stats: Any,
    items: list[DiscoveryRecordItem],
    *,
    inspected_count: int | None = None,
    candidate_count: int | None = None,
) -> dict[str, Any]:
    known_sizes = [item.size_bytes for item in items if item.size_bytes is not None]
    unknown_count = sum(1 for item in items if item.size_bytes is None)
    total_bytes = sum(known_sizes)
    extension_counts = Counter(
        Path(item.filename or item.final_url).suffix.lower() or "[no extension]"
        for item in items
    )
    largest = sorted(
        [item for item in items if item.size_bytes is not None],
        key=lambda item: item.size_bytes or 0,
        reverse=True,
    )[:10]
    return {
        "start_url": start_url,
        "pages_scanned": stats.pages_scanned,
        "pages_blocked_by_robots": stats.pages_blocked_by_robots,
        "robots_enabled": stats.robots_enabled,
        "robots_url": stats.robots_url,
        "crawl_delay_seconds": stats.crawl_delay_seconds,
        "download_links_found": len(items),
        "known_sizes": len(known_sizes),
        "unknown_sizes": unknown_count,
        "total_known_bytes": total_bytes,
        "total_known_human": human_size(total_bytes),
        "is_lower_bound": unknown_count > 0,
        "extensions": dict(extension_counts.most_common()),
        "largest_files": [
            {
                "url": item.final_url,
                "method": item.method,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "size_human": item.size_human or "unknown",
            }
            for item in largest
        ],
        "inspected_count": inspected_count,
        "candidate_count": candidate_count,
    }


def build_stream_item_from_candidate(candidate: CrawlLink) -> DiscoveryRecordItem:
    return DiscoveryRecordItem(
        record_id=discovery_record_id(candidate.url, candidate.method, candidate.request_data),
        url=candidate.url,
        final_url=candidate.url,
        source_page=candidate.source_page,
        reason=candidate.reason,
        anchor_text=candidate.anchor_text,
        method=candidate.method,
        request_data=tuple(candidate.request_data),
        status_code=None,
        content_type=candidate.inline_content_type,
        size_bytes=candidate.inline_size_bytes,
        size_human=human_size(candidate.inline_size_bytes) if candidate.inline_size_bytes is not None else None,
        filename=candidate.filename or filename_from_url(candidate.url),
        error_message=None,
        inspection_status="pending",
    )


def apply_inspected_record_to_item(item: DiscoveryRecordItem, record: DownloadRecord) -> None:
    item.url = record.url
    item.final_url = record.final_url
    item.source_page = record.source_page
    item.reason = record.reason
    item.anchor_text = record.anchor_text
    item.method = record.method
    item.request_data = tuple(record.request_data)
    item.status_code = record.status_code
    item.content_type = record.content_type
    item.size_bytes = record.size_bytes
    item.size_human = human_size(record.size_bytes) if record.size_bytes is not None else None
    item.filename = record.filename
    item.error_message = record.error_message
    item.inspection_status = "ready"


def discovery_items_to_csv_rows(items: list[DiscoveryRecordItem]) -> list[dict[str, Any]]:
    return [
        {
            "url": item.url,
            "final_url": item.final_url,
            "source_page": item.source_page,
            "reason": item.reason,
            "anchor_text": item.anchor_text,
            "method": item.method,
            "request_data": request_data_to_text(item.request_data),
            "status_code": item.status_code,
            "content_type": item.content_type,
            "size_bytes": item.size_bytes,
            "size_human": item.size_human or "",
            "filename": item.filename,
            "error_message": item.error_message,
        }
        for item in items
    ]


def write_discovery_csv(path: Path, items: list[DiscoveryRecordItem]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "url",
                "final_url",
                "source_page",
                "reason",
                "anchor_text",
                "method",
                "request_data",
                "status_code",
                "content_type",
                "size_bytes",
                "size_human",
                "filename",
                "error_message",
            ],
        )
        writer.writeheader()
        writer.writerows(discovery_items_to_csv_rows(items))
    return path


def write_discovery_json(path: Path, job: DiscoveryJob, items: list[DiscoveryRecordItem]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": job.summary,
        "records": [
            {
                "id": item.record_id,
                "url": item.url,
                "final_url": item.final_url,
                "source_page": item.source_page,
                "reason": item.reason,
                "anchor_text": item.anchor_text,
                "method": item.method,
                "request_data": list(item.request_data),
                "status_code": item.status_code,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "size_human": item.size_human,
                "filename": item.filename,
                "error_message": item.error_message,
            }
            for item in items
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def save_discovery_artifacts(
    state: AppState,
    job: DiscoveryJob,
    items: list[DiscoveryRecordItem],
    *,
    raw_import_csv: str | None = None,
) -> None:
    label = (
        job.source_file_name
        or parse.urlparse(job.start_url).netloc
        or "scrape"
    )
    artifact_dir = create_scrape_artifact_dir(state.scrape_save_dir, label)
    csv_path = write_discovery_csv(artifact_dir / "discovery.csv", items)
    json_path = write_discovery_json(artifact_dir / "discovery.json", job, items)
    imported_csv_path: str | None = None
    if raw_import_csv is not None:
        input_name = sanitize_segment(job.source_file_name, "uploaded.csv")
        imported_path = artifact_dir / input_name
        imported_path.write_text(raw_import_csv, encoding="utf-8")
        imported_csv_path = str(imported_path)
    with job.lock:
        job.artifact_dir = str(artifact_dir)
        job.csv_path = str(csv_path)
        job.json_path = str(json_path)
        job.imported_csv_path = imported_csv_path


def make_discovery_settings(timeout_seconds: int) -> RequestSettings:
    return RequestSettings(
        timeout=max(5, min(timeout_seconds, 120)),
        retries=2,
        backoff=1.0,
        crawl_workers=DEFAULT_DISCOVERY_WORKERS,
    )


def build_discovery_job(
    *,
    source_type: str,
    start_url: str,
    scan_mode: str,
    depth_limit: int,
    source_file_name: str | None = None,
) -> DiscoveryJob:
    return DiscoveryJob(
        job_id=str(uuid.uuid4())[:8],
        source_type=source_type,
        start_url=start_url,
        scan_mode=scan_mode,
        depth_limit=depth_limit,
        profile=detect_site_profile(start_url),
        source_file_name=source_file_name,
    )


def start_discovery_job(state: AppState, job: DiscoveryJob) -> None:
    state.add_discovery_job(job)
    worker = threading.Thread(
        target=run_discovery_job,
        args=(state, job),
        daemon=True,
        name=f"discovery-job-{job.job_id}",
    )
    job.worker_thread = worker
    worker.start()


def run_discovery_job(state: AppState, job: DiscoveryJob) -> None:
    with job.lock:
        job.status = "running"
        job.started_at = time.time()
    state.persist_discovery_job(job)
    log_discovery(job, f"Starting {job.scan_mode} discovery.")

    max_depth = 0 if job.scan_mode == "single_page" else job.depth_limit
    max_pages = 1 if job.scan_mode == "single_page" else DEFAULT_DISCOVERY_MAX_PAGES

    def update_live_summary(*, inspected_count: int | None = None, candidate_count: int | None = None) -> None:
        with job.lock:
            current_items = list(job.records)
        summary = build_discovery_summary_from_items(
            job.start_url,
            type("StatsView", (), {
                "pages_scanned": job.summary.get("pages_scanned", 0),
                "pages_blocked_by_robots": job.summary.get("pages_blocked_by_robots", 0),
                "robots_enabled": job.summary.get("robots_enabled", False),
                "robots_url": job.summary.get("robots_url"),
                "crawl_delay_seconds": job.summary.get("crawl_delay_seconds", 0.0),
            })(),
            current_items,
            inspected_count=inspected_count,
            candidate_count=candidate_count,
        )
        summary["detected_profile"] = job.profile
        summary["scan_mode"] = job.scan_mode
        summary["depth_limit"] = job.depth_limit
        if summary.get("candidate_count") is None:
            summary["candidate_count"] = len(current_items)
        if summary.get("inspected_count") is None:
            summary["inspected_count"] = len(current_items)
        with job.lock:
            job.summary = summary
            job.pages = build_discovery_pages(current_items)

    def on_candidate(candidate: CrawlLink, stats: Any) -> None:
        record_id = discovery_record_id(candidate.url, candidate.method, candidate.request_data)
        with job.lock:
            existing_ids = {item.record_id for item in job.records}
            if record_id not in existing_ids:
                job.records.append(build_stream_item_from_candidate(candidate))
            job.summary = {
                "pages_scanned": stats.pages_scanned,
                "pages_blocked_by_robots": stats.pages_blocked_by_robots,
                "robots_enabled": stats.robots_enabled,
                "robots_url": stats.robots_url,
                "crawl_delay_seconds": stats.crawl_delay_seconds,
            }
        update_live_summary(candidate_count=len(job.records))

    def on_record(candidate: CrawlLink, record: DownloadRecord | None, stats: Any, inspected_count: int, candidate_count: int) -> None:
        record_id = discovery_record_id(candidate.url, candidate.method, candidate.request_data)
        with job.lock:
            job.summary = {
                "pages_scanned": stats.pages_scanned,
                "pages_blocked_by_robots": stats.pages_blocked_by_robots,
                "robots_enabled": stats.robots_enabled,
                "robots_url": stats.robots_url,
                "crawl_delay_seconds": stats.crawl_delay_seconds,
            }
            index = next((i for i, item in enumerate(job.records) if item.record_id == record_id), None)
            if record is None:
                if index is not None:
                    job.records.pop(index)
            else:
                if index is None:
                    job.records.append(build_stream_item_from_candidate(candidate))
                    index = len(job.records) - 1
                apply_inspected_record_to_item(job.records[index], record)
        update_live_summary(inspected_count=inspected_count, candidate_count=candidate_count)

    try:
        summary, records, _stats = run_discovery(
            start_url=job.start_url,
            max_pages=max_pages,
            allow_subdomains=False,
            ignore_robots=False,
            settings=make_discovery_settings(state.timeout_seconds),
            inspect_workers=DEFAULT_DISCOVERY_WORKERS,
            max_depth=max_depth,
            on_candidate=on_candidate,
            on_record=on_record,
        )
        items = [
            DiscoveryRecordItem(
                record_id=discovery_record_id(record.url, record.method, tuple(record.request_data)),
                url=record.url,
                final_url=record.final_url,
                source_page=record.source_page,
                reason=record.reason,
                anchor_text=record.anchor_text,
                method=record.method,
                request_data=tuple(record.request_data),
                status_code=record.status_code,
                content_type=record.content_type,
                size_bytes=record.size_bytes,
                size_human=human_size(record.size_bytes) if record.size_bytes is not None else None,
                filename=record.filename,
                error_message=record.error_message,
                inspection_status="ready",
            )
            for record in records
        ]
        summary["detected_profile"] = job.profile
        summary["scan_mode"] = job.scan_mode
        summary["depth_limit"] = job.depth_limit

        with job.lock:
            job.summary = summary
            job.records = items
            job.pages = build_discovery_pages(items)
        save_discovery_artifacts(state, job, items)
        with job.lock:
            job.status = "completed"
            job.finished_at = time.time()
        state.persist_discovery_job(job)
        log_discovery(job, f"Discovery finished with {len(items)} downloadable items.")
    except ValueError as exc:
        with job.lock:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = time.time()
        state.persist_discovery_job(job)
        log_discovery(job, f"Discovery failed: {exc}")
    except Exception as exc:
        with job.lock:
            job.status = "failed"
            job.error = f"Unexpected error: {exc}"
            job.finished_at = time.time()
        state.persist_discovery_job(job)
        log_discovery(job, f"Discovery failed unexpectedly: {exc}")


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def sanitize_segment(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:120] or fallback


def slugify_for_path(value: str | None, fallback: str) -> str:
    cleaned = sanitize_segment(value, fallback).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return cleaned or fallback


def request_data_to_text(value: tuple[tuple[str, str], ...]) -> str:
    if not value:
        return ""
    return parse.urlencode(list(value))


def create_scrape_artifact_dir(base_dir: Path, label: str) -> Path:
    now = datetime.now().astimezone()
    day_dir = base_dir / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    run_dir = day_dir / f"{now.strftime('%H%M%S')}-{slugify_for_path(label, 'scrape')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def parse_request_data(raw: str | None) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    text = raw.strip()
    if not text:
        return ()
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return tuple((str(key), str(value)) for key, value in payload.items())
        if isinstance(payload, list):
            pairs: list[tuple[str, str]] = []
            for item in payload:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    pairs.append((str(item["name"]), str(item["value"])))
            if pairs:
                return tuple(pairs)
    return tuple(parse.parse_qsl(text, keep_blank_values=True))


def parse_optional_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_csv_text(csv_text: str) -> tuple[list[str], list[dict[str, str]]]:
    cleaned = csv_text.lstrip("\ufeff")
    stream = io.StringIO(cleaned)
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise ValueError("The CSV file needs a header row.")

    headers = [header.strip() for header in reader.fieldnames]
    if not any(headers):
        raise ValueError("The CSV file header row is empty.")

    rows: list[dict[str, str]] = []
    for raw_row in reader:
        row = {header: (raw_row.get(header) or "").strip() for header in headers}
        if any(value for value in row.values()):
            rows.append(row)
    if not rows:
        raise ValueError("The CSV file does not contain any data rows.")
    return headers, rows


def detect_mapping(headers: list[str], candidates: list[str]) -> str | None:
    normalized = {normalize_header(header): header for header in headers}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def detect_mappings(headers: list[str]) -> dict[str, str | None]:
    return {
        "url": detect_mapping(headers, URL_COLUMN_CANDIDATES),
        "filename": detect_mapping(headers, FILENAME_COLUMN_CANDIDATES),
        "method": detect_mapping(headers, METHOD_COLUMN_CANDIDATES),
        "request_data": detect_mapping(headers, REQUEST_DATA_COLUMN_CANDIDATES),
        "subdir": detect_mapping(headers, SUBDIR_COLUMN_CANDIDATES),
        "referer": detect_mapping(headers, REFERER_COLUMN_CANDIDATES),
        "size_bytes": detect_mapping(headers, SIZE_COLUMN_CANDIDATES),
    }


def build_preview(csv_text: str, file_name: str | None) -> dict[str, Any]:
    headers, rows = parse_csv_text(csv_text)
    mappings = detect_mappings(headers)
    url_header = mappings.get("url")
    valid_rows = 0
    if url_header:
        valid_rows = sum(1 for row in rows if row.get(url_header, "").strip())
    preview_rows = rows[:PREVIEW_ROW_LIMIT]
    warnings: list[str] = []
    if not url_header:
        warnings.append("No obvious download URL column was detected. Pick the URL column manually.")
    elif valid_rows < len(rows):
        warnings.append("Some rows are missing a download URL and will be marked invalid.")
    return {
        "file_name": file_name or "uploaded.csv",
        "headers": headers,
        "row_count": len(rows),
        "valid_url_rows": valid_rows,
        "preview_rows": preview_rows,
        "mappings": mappings,
        "warnings": warnings,
    }


def import_scraped_csv(csv_text: str, file_name: str) -> tuple[dict[str, Any], list[DiscoveryRecordItem]]:
    headers, rows = parse_csv_text(csv_text)
    mappings = detect_mappings(headers)
    url_header = mappings.get("url") or detect_mapping(headers, ["final_url", "url", "download_url", "href", "link"])
    if not url_header:
        raise ValueError("The imported scrape CSV needs a final_url or url column.")

    filename_header = mappings.get("filename")
    method_header = mappings.get("method")
    request_data_header = mappings.get("request_data")
    referer_header = mappings.get("referer") or detect_mapping(headers, ["source_page"])
    size_header = mappings.get("size_bytes")
    reason_header = detect_mapping(headers, ["reason"])
    anchor_text_header = detect_mapping(headers, ["anchor_text", "text"])
    content_type_header = detect_mapping(headers, ["content_type", "mime_type", "type"])
    status_code_header = detect_mapping(headers, ["status_code"])
    error_message_header = detect_mapping(headers, ["error_message", "error"])

    items: list[DiscoveryRecordItem] = []
    for index, row in enumerate(rows, start=1):
        final_url = (row.get(url_header) or "").strip()
        if not final_url:
            continue
        request_data = parse_request_data(row.get(request_data_header) if request_data_header else None)
        size_bytes = parse_optional_int(row.get(size_header) if size_header else None)
        status_code = parse_optional_int(row.get(status_code_header) if status_code_header else None)
        source_page = (row.get(referer_header) or final_url).strip() if referer_header else final_url
        items.append(
            DiscoveryRecordItem(
                record_id=f"rec-{index}",
                url=final_url,
                final_url=final_url,
                source_page=source_page or final_url,
                reason=(row.get(reason_header) or "imported csv").strip() if reason_header else "imported csv",
                anchor_text=(row.get(anchor_text_header) or "").strip() if anchor_text_header else "",
                method=(row.get(method_header) or "GET").strip().upper() if method_header else "GET",
                request_data=request_data,
                status_code=status_code,
                content_type=(row.get(content_type_header) or "").strip() or None if content_type_header else None,
                size_bytes=size_bytes,
                size_human=human_size(size_bytes) if size_bytes is not None else None,
                filename=(
                    ((row.get(filename_header) or "").strip() or filename_from_url(final_url))
                    if filename_header
                    else filename_from_url(final_url)
                ),
                error_message=(row.get(error_message_header) or "").strip() or None if error_message_header else None,
            )
        )

    known_sizes = [item.size_bytes for item in items if item.size_bytes is not None]
    total_known_bytes = sum(known_sizes)
    summary = {
        "start_url": "",
        "pages_scanned": len({item.source_page for item in items}),
        "pages_blocked_by_robots": 0,
        "robots_enabled": False,
        "robots_url": None,
        "crawl_delay_seconds": 0.0,
        "download_links_found": len(items),
        "known_sizes": len(known_sizes),
        "unknown_sizes": len(items) - len(known_sizes),
        "total_known_bytes": total_known_bytes,
        "total_known_human": human_size(total_known_bytes),
        "is_lower_bound": len(items) != len(known_sizes),
        "extensions": dict(Counter(Path(item.filename or item.final_url).suffix.lower() or "[no extension]" for item in items)),
        "largest_files": [
            {
                "url": item.final_url,
                "method": item.method,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "size_human": item.size_human or "unknown",
            }
            for item in sorted(items, key=lambda entry: entry.size_bytes or -1, reverse=True)[:10]
        ],
    }
    return summary, items


def build_row_tasks(
    rows: list[dict[str, str]],
    mappings: dict[str, str | None],
) -> list[RowTask]:
    tasks: list[RowTask] = []
    url_header = mappings.get("url")
    filename_header = mappings.get("filename")
    method_header = mappings.get("method")
    request_data_header = mappings.get("request_data")
    subdir_header = mappings.get("subdir")
    referer_header = mappings.get("referer")
    size_header = mappings.get("size_bytes")

    for index, row in enumerate(rows, start=2):
        download_url = row.get(url_header, "").strip() if url_header else ""
        method = row.get(method_header, "").strip().upper() if method_header else "GET"
        task = RowTask(
            row_number=index,
            raw=row,
            download_url=download_url or None,
            method=method or "GET",
            request_data=parse_request_data(row.get(request_data_header) if request_data_header else None),
            filename_hint=(row.get(filename_header, "").strip() or None) if filename_header else None,
            subdir_hint=(row.get(subdir_header, "").strip() or None) if subdir_header else None,
            referer=(row.get(referer_header, "").strip() or None) if referer_header else None,
            expected_bytes=parse_optional_int(row.get(size_header) if size_header else None),
        )
        if not task.download_url:
            task.status = "invalid"
            task.message = "Missing download URL"
            task.finished_at = time.time()
        elif parse.urlparse(task.download_url).scheme not in {"http", "https"}:
            task.status = "invalid"
            task.message = "Unsupported URL scheme"
            task.finished_at = time.time()
        elif task.method not in {"GET", "POST"}:
            task.status = "invalid"
            task.message = f"Unsupported HTTP method: {task.method}"
            task.finished_at = time.time()
        task.updated_at = time.time()
        tasks.append(task)
    return tasks


def create_job(
    *,
    file_name: str,
    output_dir: Path,
    options: dict[str, Any],
    mappings: dict[str, str | None],
    headers: list[str],
    rows: list[dict[str, str]],
) -> DownloadJob:
    return DownloadJob(
        job_id=str(uuid.uuid4())[:8],
        file_name=file_name,
        output_dir=output_dir,
        options=dict(options),
        mappings=dict(mappings),
        headers=list(headers),
        rows=build_row_tasks(rows, mappings),
    )


def create_job_from_tasks(
    *,
    file_name: str,
    output_dir: Path,
    options: dict[str, Any],
    mappings: dict[str, str | None],
    headers: list[str],
    tasks: list[RowTask],
) -> DownloadJob:
    return DownloadJob(
        job_id=str(uuid.uuid4())[:8],
        file_name=file_name,
        output_dir=output_dir,
        options=dict(options),
        mappings=dict(mappings),
        headers=list(headers),
        rows=tasks,
    )


def yt_dlp_binary() -> str | None:
    search_paths = []
    current_path = os.environ.get("PATH", "")
    if current_path:
        search_paths.append(current_path)
    if os.defpath and os.defpath not in search_paths:
        search_paths.append(os.defpath)

    version_dir = f"{sys.version_info.major}.{sys.version_info.minor}"
    common_candidates = [
        Path("/opt/homebrew/bin/yt-dlp"),
        Path("/usr/local/bin/yt-dlp"),
        Path("/usr/bin/yt-dlp"),
        Path.home() / ".local" / "bin" / "yt-dlp",
        Path.home() / "Library" / "Python" / version_dir / "bin" / "yt-dlp",
        Path(sys.executable).resolve().parent / "yt-dlp",
    ]

    for search_path in search_paths:
        resolved = shutil.which("yt-dlp", path=search_path)
        if resolved:
            return resolved

    for candidate in common_candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def build_media_job(
    *,
    source_url: str,
    output_dir: Path,
    options: dict[str, Any],
) -> DownloadJob:
    media_options = dict(options)
    row = RowTask(
        row_number=1,
        raw={
            "url": source_url,
            "download_type": str(media_options.get("download_type") or "video"),
            "format": str(media_options.get("format") or "any"),
            "quality": str(media_options.get("quality") or "best"),
            "playlist_limit": str(media_options.get("playlist_limit") or 0),
            "subdir": str(media_options.get("subdir") or ""),
        },
        download_url=source_url,
        method="GET",
        request_data=(),
        filename_hint=None,
        subdir_hint=str(media_options.get("subdir") or "") or None,
        referer=None,
        expected_bytes=None,
        task_kind="media",
        metadata={
            "download_type": str(media_options.get("download_type") or "video"),
            "format": str(media_options.get("format") or "any"),
            "quality": str(media_options.get("quality") or "best"),
            "playlist_limit": max(0, int(media_options.get("playlist_limit") or 0)),
            "subdir": str(media_options.get("subdir") or "").strip(),
            "cookies_path": str(media_options.get("cookies_path") or "").strip(),
        },
    )
    return DownloadJob(
        job_id=str(uuid.uuid4())[:8],
        file_name=f"media-{slugify_for_path(source_url, 'media')}.txt",
        output_dir=output_dir,
        options=dict(media_options),
        mappings={"url": "url"},
        headers=["url", "download_type", "format", "quality", "playlist_limit", "subdir"],
        rows=[row],
        job_kind="media",
        source={
            "source_url": source_url,
            "download_type": row.metadata["download_type"],
            "format": row.metadata["format"],
            "quality": row.metadata["quality"],
            "playlist_limit": row.metadata["playlist_limit"],
            "subdir": row.metadata["subdir"],
        },
    )


def media_target_dir(job: DownloadJob, row: RowTask) -> Path:
    subdir = sanitize_segment(str(row.metadata.get("subdir") or ""), "")
    if not subdir:
        return job.output_dir
    return (job.output_dir / subdir).resolve()


def media_video_format_selector(output_format: str, quality: str) -> str:
    target_height = "" if quality in {"best", "worst"} else f"[height<={quality}]"
    if output_format == "mp4":
        return (
            f"bestvideo[ext=mp4]{target_height}+bestaudio[ext=m4a]"
            f"/best[ext=mp4]{target_height}/best{target_height}"
        )
    return f"bestvideo{target_height}+bestaudio/best{target_height}"


def media_audio_quality_value(quality: str) -> str:
    return {
        "best": "0",
        "320": "0",
        "192": "3",
        "128": "6",
    }.get(quality, "5")


def build_media_command(job: DownloadJob, row: RowTask) -> list[str]:
    binary = yt_dlp_binary()
    if not binary:
        raise ValueError("yt-dlp is not installed on this machine.")

    metadata = row.metadata
    target_dir = media_target_dir(job, row)
    target_dir.mkdir(parents=True, exist_ok=True)

    download_type = str(metadata.get("download_type") or "video")
    output_format = str(metadata.get("format") or "any")
    quality = str(metadata.get("quality") or "best")
    playlist_limit = max(0, int(metadata.get("playlist_limit") or 0))
    cookies_path = str(metadata.get("cookies_path") or "").strip()

    cmd = [
        binary,
        "--newline",
        "--progress",
        "--progress-template",
        "download:PROGRESS:%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--print",
        "before_dl:TITLE:%(title)s",
        "--print",
        "after_move:FILE:%(filepath)s",
        "-P",
        str(target_dir),
        "-o",
        "%(title)s.%(ext)s",
    ]

    if cookies_path:
        cmd.extend(["--cookies", cookies_path])
    if playlist_limit > 0:
        cmd.extend(["--playlist-end", str(playlist_limit)])
    if download_type == "audio":
        cmd.extend(
            [
                "-f",
                f"bestaudio[ext={output_format}]/bestaudio/best",
                "-x",
                "--audio-format",
                output_format,
                "--audio-quality",
                media_audio_quality_value(quality),
            ]
        )
    else:
        if output_format not in {"any", "mp4"}:
            output_format = "any"
        cmd.extend(["-f", media_video_format_selector(output_format, quality)])
        if output_format == "mp4":
            cmd.extend(["--merge-output-format", "mp4"])

    cmd.append(str(row.download_url or ""))
    return cmd


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def build_tasks_from_discovery(
    records: list[DiscoveryRecordItem],
    *,
    start_row_number: int = 2,
) -> list[RowTask]:
    tasks: list[RowTask] = []
    for offset, record in enumerate(records):
        tasks.append(
            RowTask(
                row_number=start_row_number + offset,
                raw={
                    "url": record.url,
                    "final_url": record.final_url,
                    "filename": record.filename or "",
                    "method": record.method,
                    "request_data": parse.urlencode(list(record.request_data)),
                    "source_page": record.source_page,
                    "size_bytes": str(record.size_bytes or ""),
                },
                download_url=record.url,
                method=record.method or "GET",
                request_data=tuple(record.request_data),
                filename_hint=record.filename,
                subdir_hint=None,
                referer=record.source_page or None,
                expected_bytes=record.size_bytes,
            )
        )
    return tasks


def start_job(state: AppState, job: DownloadJob) -> None:
    job.output_dir.mkdir(parents=True, exist_ok=True)
    state.add_job(job)
    worker = threading.Thread(target=run_job, args=(job,), daemon=True, name=f"download-job-{job.job_id}")
    job.worker_thread = worker
    worker.start()


def add_job_log(job: DownloadJob, message: str) -> None:
    line = f"{datetime.now().astimezone().strftime('%H:%M:%S')} {message}"
    with job.lock:
        job.logs.append(line)
        if len(job.logs) > LOG_LIMIT:
            job.logs[:] = job.logs[-LOG_LIMIT:]
    if STATE is not None:
        STATE.persist_job(job)


def update_row(job: DownloadJob, row: RowTask, **changes: Any) -> None:
    with job.lock:
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = time.time()
    if STATE is not None and any(key != "bytes_downloaded" for key in changes):
        STATE.persist_job(job)


def summarize_job(job: DownloadJob) -> dict[str, Any]:
    with job.lock:
        rows = list(job.rows)
        summary = Counter(row.status for row in rows)
        bytes_downloaded = sum(row.bytes_downloaded for row in rows)
        expected_known = [row.expected_bytes for row in rows if row.expected_bytes is not None]
        recent_rows = sorted(rows, key=lambda item: item.updated_at, reverse=True)[:RECENT_ROWS_LIMIT]
        logs = list(job.logs[-40:])
        payload_rows = [
            {
                "row_number": row.row_number,
                "status": row.status,
                "message": row.message,
                "download_url": row.download_url,
                "final_url": row.final_url,
                "filename_hint": row.filename_hint,
                "output_path": row.output_path,
                "bytes_downloaded": row.bytes_downloaded,
                "expected_bytes": row.expected_bytes,
                "source_page": row.raw.get("source_page"),
                "started_at": timestamp_to_iso(row.started_at),
                "finished_at": timestamp_to_iso(row.finished_at),
            }
            for row in recent_rows
        ]
        failed_rows = [
            {
                "row_number": row.row_number,
                "status": row.status,
                "message": row.message,
                "download_url": row.download_url,
                "filename_hint": row.filename_hint,
                "source_page": row.raw.get("source_page"),
            }
            for row in rows
            if row.status == "failed"
        ][:50]
        total_rows = len(rows)
        finished = sum(summary.get(key, 0) for key in ("completed", "failed", "skipped", "invalid", "cancelled"))
        progress_fraction = (finished / total_rows) if total_rows else 0.0
        return {
            "job_id": job.job_id,
            "file_name": job.file_name,
            "job_kind": job.job_kind,
            "status": job.status,
            "created_at": timestamp_to_iso(job.created_at),
            "started_at": timestamp_to_iso(job.started_at),
            "finished_at": timestamp_to_iso(job.finished_at),
            "cancel_requested": job.cancel_requested,
            "output_dir": str(job.output_dir),
            "options": job.options,
            "source": dict(job.source),
            "mappings": job.mappings,
            "manifest_path": job.manifest_path,
            "summary": {
                "total_rows": total_rows,
                "queued": summary.get("queued", 0),
                "active": summary.get("active", 0),
                "completed": summary.get("completed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
                "invalid": summary.get("invalid", 0),
                "cancelled": summary.get("cancelled", 0),
                "bytes_downloaded": bytes_downloaded,
                "bytes_downloaded_human": human_size(bytes_downloaded),
                "expected_bytes": sum(expected_known) if expected_known else None,
                "expected_bytes_human": human_size(sum(expected_known)) if expected_known else None,
                "progress_fraction": progress_fraction,
            },
            "recent_rows": payload_rows,
            "failed_rows": failed_rows,
            "logs": logs,
        }


def load_database_stats(db_path: Path) -> dict[str, Any]:
    payload = {
        "available": db_path.exists(),
        "db_path": str(db_path),
        "source_files": 0,
        "observed_rows": 0,
        "unique_downloads": 0,
        "systems": 0,
        "domains": 0,
        "total_bytes": 0,
        "total_human": "0 B",
        "top_domains": [],
    }
    if not db_path.exists():
        return payload

    conn = sqlite3.connect(db_path)
    try:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS unique_downloads,
                COUNT(DISTINCT CASE WHEN system_name != '' THEN system_name END) AS systems,
                COUNT(DISTINCT CASE WHEN site_domain != '' THEN site_domain END) AS domains,
                SUM(size_bytes) AS total_bytes
            FROM downloads
            """
        ).fetchone()
        source_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS source_files,
                SUM(row_count) AS observed_rows
            FROM sources
            """
        ).fetchone()
        payload.update(
            {
                "source_files": int(source_totals[0] or 0),
                "observed_rows": int(source_totals[1] or 0),
                "unique_downloads": int(totals[0] or 0),
                "systems": int(totals[1] or 0),
                "domains": int(totals[2] or 0),
                "total_bytes": int(totals[3] or 0),
                "total_human": human_size(int(totals[3] or 0)),
            }
        )
        payload["top_domains"] = [
            {
                "site_domain": row[0],
                "downloads": int(row[1] or 0),
                "total_bytes": int(row[2] or 0),
                "total_human": human_size(int(row[2] or 0)),
            }
            for row in conn.execute(
                """
                SELECT site_domain, COUNT(*) AS downloads, SUM(size_bytes) AS total_bytes
                FROM downloads
                WHERE site_domain != ''
                GROUP BY site_domain
                ORDER BY downloads DESC, total_bytes DESC
                LIMIT 10
                """
            )
        ]
        return payload
    finally:
        conn.close()


def search_database(db_path: Path, query: str, limit: int) -> dict[str, Any]:
    normalized_query = query.strip()
    payload = {
        "available": db_path.exists(),
        "db_path": str(db_path),
        "query": normalized_query,
        "limit": limit,
        "results": [],
    }
    if not db_path.exists() or not normalized_query:
        return payload

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.system_name,
                    d.base_type,
                    d.filename,
                    d.site_domain,
                    d.size_bytes,
                    d.size_human,
                    d.method,
                    d.final_url,
                    d.source_page,
                    COUNT(o.id) AS observations
                FROM download_search s
                JOIN downloads d ON d.id = s.rowid
                LEFT JOIN observations o ON o.download_id = d.id
                WHERE download_search MATCH ?
                GROUP BY d.id
                ORDER BY bm25(download_search), d.size_bytes DESC
                LIMIT ?
                """,
                (normalized_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            like_query = f"%{normalized_query}%"
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.system_name,
                    d.base_type,
                    d.filename,
                    d.site_domain,
                    d.size_bytes,
                    d.size_human,
                    d.method,
                    d.final_url,
                    d.source_page,
                    COUNT(o.id) AS observations
                FROM downloads d
                LEFT JOIN observations o ON o.download_id = d.id
                WHERE d.filename LIKE ?
                   OR d.system_name LIKE ?
                   OR d.source_page LIKE ?
                   OR d.final_url LIKE ?
                   OR d.site_domain LIKE ?
                GROUP BY d.id
                ORDER BY d.size_bytes DESC, d.filename ASC
                LIMIT ?
                """,
                (like_query, like_query, like_query, like_query, like_query, limit),
            ).fetchall()
        payload["results"] = [
            {
                "id": int(row["id"]),
                "system_name": row["system_name"],
                "base_type": row["base_type"],
                "filename": row["filename"],
                "site_domain": row["site_domain"],
                "size_bytes": int(row["size_bytes"] or 0),
                "size_human": row["size_human"] or human_size(int(row["size_bytes"] or 0)),
                "method": row["method"],
                "final_url": row["final_url"],
                "source_page": row["source_page"],
                "observations": int(row["observations"] or 0),
            }
            for row in rows
        ]
        return payload
    finally:
        conn.close()


def content_disposition_filename(headers) -> str | None:
    disposition = headers.get("Content-Disposition", "")
    if not disposition:
        return None
    filename_star = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition, re.IGNORECASE)
    if filename_star:
        return parse.unquote(filename_star.group(1).strip().strip("\"'"))
    filename_match = re.search(r'filename\s*=\s*"([^"]+)"', disposition, re.IGNORECASE)
    if filename_match:
        return filename_match.group(1).strip()
    filename_match = re.search(r"filename\s*=\s*([^;]+)", disposition, re.IGNORECASE)
    if filename_match:
        return filename_match.group(1).strip().strip("\"'")
    return None


def filename_from_url(url: str) -> str | None:
    parsed = parse.urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return None
    name = Path(path).name
    return parse.unquote(name) or None


def filename_from_content_type(content_type: str | None, fallback: str) -> str:
    if not content_type:
        return fallback
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if not guessed:
        return fallback
    if fallback.endswith(guessed):
        return fallback
    return f"{fallback}{guessed}"


def origin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def reserve_destination(
    job: DownloadJob,
    target_dir: Path,
    filename: str,
    collision_strategy: str,
) -> tuple[Path | None, str | None]:
    target_dir.mkdir(parents=True, exist_ok=True)
    base_name = sanitize_segment(filename, "download.bin")
    base_path = target_dir / base_name

    with job.lock:
        if collision_strategy == "overwrite":
            job.reserved_paths.add(str(base_path))
            return base_path, None

        candidate = base_path
        counter = 1
        while True:
            in_use = str(candidate) in job.reserved_paths
            exists = candidate.exists()
            existing_zero_byte = exists and candidate.is_file() and candidate.stat().st_size == 0
            if not in_use and not exists:
                job.reserved_paths.add(str(candidate))
                return candidate, None
            if not in_use and existing_zero_byte:
                job.reserved_paths.add(str(candidate))
                return candidate, None
            if collision_strategy == "skip":
                return None, "File already exists"
            stem = base_path.stem
            suffix = base_path.suffix
            candidate = target_dir / f"{stem} ({counter}){suffix}"
            counter += 1


def release_destination(job: DownloadJob, path: Path | None) -> None:
    if path is None:
        return
    with job.lock:
        job.reserved_paths.discard(str(path))


def write_manifest(job: DownloadJob) -> None:
    manifest_dir = job.output_dir / ".csv-download-client"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"job-{job.job_id}.json"
    payload = summarize_job(job)
    payload["source"] = dict(job.source)
    with job.lock:
        payload["rows"] = [
            {
                "row_number": row.row_number,
                "status": row.status,
                "message": row.message,
                "download_url": row.download_url,
                "final_url": row.final_url,
                "output_path": row.output_path,
                "bytes_downloaded": row.bytes_downloaded,
                "expected_bytes": row.expected_bytes,
                "raw": row.raw,
                "task_kind": row.task_kind,
                "metadata": row.metadata,
            }
            for row in job.rows
        ]
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with job.lock:
        job.manifest_path = str(manifest_path)


def choose_filename(row: RowTask, final_url: str, headers) -> str:
    header_name = content_disposition_filename(headers)
    if header_name:
        return sanitize_segment(header_name, f"download-row-{row.row_number}")
    if row.filename_hint:
        return sanitize_segment(row.filename_hint, f"download-row-{row.row_number}")
    url_name = filename_from_url(final_url)
    if url_name:
        return sanitize_segment(url_name, f"download-row-{row.row_number}")
    fallback = f"download-row-{row.row_number}"
    return filename_from_content_type(headers.get("Content-Type"), fallback)


def parse_content_length(headers) -> int | None:
    raw = headers.get("Content-Length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def build_request(row: RowTask, method_override: str | None = None) -> request.Request:
    assert row.download_url
    method = (method_override or row.method).upper()
    headers = {"User-Agent": USER_AGENT}
    if row.referer:
        headers["Referer"] = row.referer
        if method != "GET" and (origin := origin_from_url(row.referer)):
            headers["Origin"] = origin

    url = row.download_url
    data: bytes | None = None
    if row.request_data:
        if method == "GET":
            split_url = parse.urlsplit(url)
            merged_query = parse.parse_qsl(split_url.query, keep_blank_values=True)
            merged_query.extend(row.request_data)
            url = parse.urlunsplit(
                (
                    split_url.scheme,
                    split_url.netloc,
                    split_url.path,
                    parse.urlencode(merged_query),
                    split_url.fragment,
                )
            )
        else:
            data = parse.urlencode(list(row.request_data)).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    return request.Request(url, headers=headers, data=data, method=method)


def should_fallback_to_post(row: RowTask, method_override: str | None, exc: error.HTTPError) -> bool:
    attempted_method = (method_override or row.method).upper()
    return (
        attempted_method == "GET"
        and row.method == "POST"
        and bool(row.request_data)
        and exc.code in {400, 401, 403, 404, 405}
    )


def retry_delay_seconds(attempt_number: int) -> float:
    return DEFAULT_RETRY_BACKOFF_SECONDS * (2 ** max(0, attempt_number - 1))


def retry_after_seconds_from_http_error(exc: error.HTTPError) -> float | None:
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if target.tzinfo is None:
        return None
    return max(0.0, (target - datetime.now(target.tzinfo)).total_seconds())


def should_retry_url_error(exc: error.URLError) -> bool:
    reason = exc.reason
    if isinstance(reason, TimeoutError):
        return True
    if isinstance(reason, str):
        lowered = reason.lower()
        return "timed out" in lowered or "temporarily unavailable" in lowered
    return True


def transfer_download(job: DownloadJob, row: RowTask, method_override: str | None = None) -> None:
    target_path: Path | None = None
    temp_path: Path | None = None
    req = build_request(row, method_override=method_override)
    try:
        if job.host_throttle is not None:
            job.host_throttle.wait(req.full_url)
        with request.urlopen(req, timeout=job.options["timeout_seconds"]) as response:
            final_url = response.geturl()
            content_length = parse_content_length(response.headers)
            expected_bytes = row.expected_bytes or content_length
            filename = choose_filename(row, final_url, response.headers)
            subdir_name = (
                sanitize_segment(row.subdir_hint, "downloads")
                if job.options["use_subdirectories"] and row.subdir_hint
                else ""
            )
            target_dir = job.output_dir / subdir_name if subdir_name else job.output_dir
            target_path, reservation_error = reserve_destination(
                job,
                target_dir,
                filename,
                job.options["collision_strategy"],
            )
            if reservation_error:
                update_row(
                    job,
                    row,
                    status="skipped",
                    message=reservation_error,
                    final_url=final_url,
                    expected_bytes=expected_bytes,
                    finished_at=time.time(),
                )
                add_job_log(job, f"Row {row.row_number}: skipped {filename} ({reservation_error.lower()}).")
                return

            assert target_path is not None
            temp_path = target_path.with_name(f"{target_path.name}.part")
            if temp_path.exists():
                temp_path.unlink()

            update_row(job, row, final_url=final_url, expected_bytes=expected_bytes, message=f"Downloading {filename}")
            bytes_downloaded = 0
            with open(temp_path, "wb") as handle:
                while True:
                    if job.cancel_requested:
                        raise DownloadCancelled()
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    bytes_downloaded += len(chunk)
                    update_row(job, row, bytes_downloaded=bytes_downloaded)

            if content_length is not None and bytes_downloaded != content_length:
                raise DownloadRetryableError(
                    f"Incomplete download body: received {bytes_downloaded} of {content_length} bytes"
                )
            if bytes_downloaded == 0 and (content_length or row.expected_bytes):
                raise DownloadRetryableError("Received an empty download body")

            temp_path.replace(target_path)
            update_row(
                job,
                row,
                status="completed",
                message="Download complete",
                output_path=str(target_path),
                bytes_downloaded=bytes_downloaded,
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: saved {target_path.name}.")
    except DownloadRetryableError:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        raise
    except DownloadCancelled:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        raise
    finally:
        release_destination(job, target_path)


def download_row(job: DownloadJob, row: RowTask) -> None:
    if row.status == "invalid":
        return
    if job.cancel_requested:
        update_row(
            job,
            row,
            status="cancelled",
            message="Cancelled before start",
            finished_at=time.time(),
        )
        return

    update_row(job, row, status="active", message="Opening connection", started_at=time.time(), bytes_downloaded=0)
    max_attempts = max(1, int(job.options.get("retry_attempts") or DEFAULT_DOWNLOAD_RETRIES))
    method_override: str | None = "GET" if row.request_data else None
    attempt = 1

    while attempt <= max_attempts:
        update_row(
            job,
            row,
            status="active",
            message=f"Opening connection (attempt {attempt}/{max_attempts})",
            output_path=None,
            bytes_downloaded=0,
            finished_at=None,
        )
        try:
            transfer_download(job, row, method_override=method_override)
            return
        except error.HTTPError as exc:
            if should_fallback_to_post(row, method_override, exc):
                method_override = "POST"
                add_job_log(job, f"Row {row.row_number}: GET failed with HTTP {exc.code}, retrying as POST.")
                update_row(job, row, message=f"Retrying as POST after GET HTTP {exc.code}")
                continue
            if exc.code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
                delay = max(retry_delay_seconds(attempt), retry_after_seconds_from_http_error(exc) or 0.0)
                add_job_log(job, f"Row {row.row_number}: HTTP {exc.code}, retrying in {delay:.1f}s.")
                update_row(job, row, message=f"HTTP {exc.code}; retrying in {delay:.1f}s")
                time.sleep(delay)
                attempt += 1
                continue
            update_row(
                job,
                row,
                status="failed",
                message=f"HTTP {exc.code}: {exc.reason}",
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: HTTP {exc.code} for {row.download_url}.")
            return
        except DownloadRetryableError as exc:
            if attempt < max_attempts:
                delay = retry_delay_seconds(attempt)
                add_job_log(job, f"Row {row.row_number}: {exc}. Retrying in {delay:.1f}s.")
                update_row(job, row, message=f"{exc}. Retrying in {delay:.1f}s")
                time.sleep(delay)
                attempt += 1
                continue
            update_row(
                job,
                row,
                status="failed",
                message=str(exc),
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: {exc}.")
            return
        except DownloadCancelled:
            update_row(
                job,
                row,
                status="cancelled",
                message="Cancelled during download",
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: cancelled.")
            return
        except error.URLError as exc:
            if should_retry_url_error(exc) and attempt < max_attempts:
                delay = retry_delay_seconds(attempt)
                add_job_log(job, f"Row {row.row_number}: network error, retrying in {delay:.1f}s.")
                update_row(job, row, message=f"Network error; retrying in {delay:.1f}s")
                time.sleep(delay)
                attempt += 1
                continue
            update_row(
                job,
                row,
                status="failed",
                message=f"Network error: {exc.reason}",
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: network error for {row.download_url}.")
            return
        except OSError as exc:
            update_row(
                job,
                row,
                status="failed",
                message=f"Filesystem error: {exc}",
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: filesystem error while writing the file.")
            return
        except Exception as exc:
            update_row(
                job,
                row,
                status="failed",
                message=f"Unexpected error: {exc}",
                finished_at=time.time(),
            )
            add_job_log(job, f"Row {row.row_number}: unexpected error while downloading.")
            return


def download_media_row(job: DownloadJob, row: RowTask) -> None:
    if not row.download_url:
        update_row(job, row, status="invalid", message="Missing media URL", finished_at=time.time())
        return
    if job.cancel_requested:
        update_row(job, row, status="cancelled", message="Cancelled before start", finished_at=time.time())
        return

    target_dir = media_target_dir(job, row)
    size_before = directory_size(target_dir)
    output_paths: list[str] = []
    update_row(job, row, status="active", message="Preparing yt-dlp", started_at=time.time(), bytes_downloaded=0)
    try:
        cmd = build_media_command(job, row)
    except ValueError as exc:
        update_row(job, row, status="failed", message=str(exc), finished_at=time.time())
        add_job_log(job, f"Media job failed before start: {exc}")
        return

    add_job_log(job, f"Launching yt-dlp for {row.download_url}.")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    with job.lock:
        job.active_processes.append(process)

    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            if job.cancel_requested:
                process.terminate()
            if line.startswith("PROGRESS:"):
                _, payload = line.split("PROGRESS:", 1)
                downloaded, total, estimate, percent, speed, eta = (payload.split("|") + ["", "", "", "", "", ""])[:6]
                total_bytes = parse_optional_int(total) or parse_optional_int(estimate)
                update_row(
                    job,
                    row,
                    message=f"{percent.strip() or 'Working'} • {speed.strip() or 'speed unknown'} • ETA {eta.strip() or 'unknown'}",
                    bytes_downloaded=parse_optional_int(downloaded) or row.bytes_downloaded,
                    expected_bytes=total_bytes or row.expected_bytes,
                )
                continue
            if line.startswith("TITLE:"):
                title = line.split("TITLE:", 1)[1].strip()
                update_row(job, row, message=f"Downloading {title}")
                continue
            if line.startswith("FILE:"):
                output_path = line.split("FILE:", 1)[1].strip()
                output_paths.append(output_path)
                update_row(job, row, output_path=output_path)
                continue
            add_job_log(job, line)
        return_code = process.wait()
    finally:
        with job.lock:
            job.active_processes = [active for active in job.active_processes if active is not process]

    if job.cancel_requested:
        update_row(job, row, status="cancelled", message="Cancelled during yt-dlp run", finished_at=time.time())
        add_job_log(job, f"Media job cancelled for {row.download_url}.")
        return

    size_after = directory_size(target_dir)
    bytes_downloaded = max(0, size_after - size_before)
    if return_code == 0:
        update_row(
            job,
            row,
            status="completed",
            message="Media download complete",
            bytes_downloaded=max(bytes_downloaded, row.bytes_downloaded),
            output_path=output_paths[-1] if output_paths else str(target_dir),
            finished_at=time.time(),
        )
        add_job_log(job, f"Media job finished for {row.download_url}.")
        return

    update_row(
        job,
        row,
        status="failed",
        message=f"yt-dlp exited with status {return_code}",
        bytes_downloaded=max(bytes_downloaded, row.bytes_downloaded),
        finished_at=time.time(),
    )
    add_job_log(job, f"Media job failed for {row.download_url} with exit status {return_code}.")


def run_job(job: DownloadJob) -> None:
    if job.job_kind == "media":
        run_media_job(job)
        return

    valid_rows = [row for row in job.rows if row.status == "queued"]
    with job.lock:
        job.status = "running"
        job.started_at = time.time()
    add_job_log(job, f"Starting job with {len(valid_rows)} downloadable rows.")

    worker_count = max(1, min(int(job.options["concurrency"]), len(valid_rows) or 1))
    job.host_throttle = HostThrottle(float(job.options.get("request_spacing_seconds") or DEFAULT_REQUEST_SPACING_SECONDS))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(download_row, job, row) for row in valid_rows]
        for future in as_completed(futures):
            future.result()

    with job.lock:
        if job.cancel_requested:
            job.status = "cancelled"
        elif any(row.status == "failed" for row in job.rows):
            job.status = "completed_with_errors"
        else:
            job.status = "completed"
        job.finished_at = time.time()

    add_job_log(job, f"Job finished with status {job.status}.")
    write_manifest(job)
    if STATE is not None:
        STATE.persist_job(job)


def run_media_job(job: DownloadJob) -> None:
    valid_rows = [row for row in job.rows if row.status == "queued"]
    with job.lock:
        job.status = "running"
        job.started_at = time.time()
    add_job_log(job, f"Starting media job with {len(valid_rows)} item(s).")

    for row in valid_rows:
        download_media_row(job, row)
        if job.cancel_requested:
            break

    with job.lock:
        if job.cancel_requested:
            job.status = "cancelled"
        elif any(row.status == "failed" for row in job.rows):
            job.status = "completed_with_errors"
        else:
            job.status = "completed"
        job.finished_at = time.time()

    add_job_log(job, f"Media job finished with status {job.status}.")
    write_manifest(job)
    if STATE is not None:
        STATE.persist_job(job)


class DownloadClientHandler(BaseHTTPRequestHandler):
    server_version = "CSVDownloadClient/1.0"

    def do_GET(self) -> None:
        parsed_path = parse.urlparse(self.path)
        route = parsed_path.path
        query_params = parse.parse_qs(parsed_path.query)
        if route == "/":
            self.serve_static("index.html")
            return
        if route == "/advanced":
            self.serve_static("advanced.html")
            return
        if route == "/history":
            self.serve_static("history.html")
            return
        if route == "/database":
            self.serve_static("database.html")
            return
        if route.startswith("/assets/"):
            relative = route.removeprefix("/assets/")
            self.serve_static(relative)
            return
        if route == "/api/app-info":
            self.send_json(
                {
                    "app_name": APP_NAME,
                    "default_output_dir": str(self.require_state().default_output_dir),
                    "scrape_save_dir": str(self.require_state().scrape_save_dir),
                    "state_dir": str(self.require_state().state_dir),
                    "database_path": str(DEFAULT_DATABASE_PATH),
                    "started_at": timestamp_to_iso(self.require_state().started_at),
                    "wizard_route": "/",
                    "advanced_route": "/advanced",
                    "history_route": "/history",
                    "database_route": "/database",
                    "yt_dlp_available": yt_dlp_binary() is not None,
                }
            )
            return
        if route == "/api/history":
            jobs = [summarize_job(job) for job in self.require_state().list_jobs()[:MAX_HISTORY_ITEMS]]
            discovery_jobs = [
                summarize_discovery_history_item(job)
                for job in self.require_state().list_discovery_jobs()[:MAX_HISTORY_ITEMS]
            ]
            self.send_json({"jobs": jobs, "discovery_jobs": discovery_jobs})
            return
        if route == "/api/database/stats":
            self.send_json(load_database_stats(DEFAULT_DATABASE_PATH))
            return
        if route == "/api/database/search":
            raw_limit = query_params.get("limit", ["20"])[0]
            try:
                limit = max(1, min(100, int(raw_limit)))
            except ValueError:
                limit = 20
            query = query_params.get("q", [""])[0]
            self.send_json(search_database(DEFAULT_DATABASE_PATH, query, limit))
            return
        if route.startswith("/api/discovery-jobs/"):
            job_id = route.rsplit("/", 1)[-1]
            job = self.require_state().get_discovery_job(job_id)
            if not job:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Discovery job not found.")
                return
            self.send_json(summarize_discovery(job))
            return
        if route.startswith("/api/jobs/"):
            job_id = route.rsplit("/", 1)[-1]
            job = self.require_state().get_job(job_id)
            if not job:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Job not found.")
                return
            self.send_json(summarize_job(job))
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Route not found.")

    def do_POST(self) -> None:
        parsed_path = parse.urlparse(self.path)
        route = parsed_path.path
        if route == "/api/preview":
            payload = self.read_json()
            if payload is None:
                return
            csv_text = str(payload.get("csv_text") or "")
            file_name = str(payload.get("file_name") or "uploaded.csv")
            try:
                preview = build_preview(csv_text, file_name)
            except ValueError as exc:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_json(preview)
            return
        if route == "/api/jobs":
            payload = self.read_json()
            if payload is None:
                return
            self.handle_create_job(payload)
            return
        if route == "/api/discovery-jobs":
            payload = self.read_json()
            if payload is None:
                return
            self.handle_create_discovery_job(payload)
            return
        if route == "/api/discovery-imports":
            payload = self.read_json()
            if payload is None:
                return
            self.handle_import_discovery_csv(payload)
            return
        if route == "/api/download-jobs/from-discovery":
            payload = self.read_json()
            if payload is None:
                return
            self.handle_create_download_job_from_discovery(payload)
            return
        if route == "/api/media-jobs":
            payload = self.read_json()
            if payload is None:
                return
            self.handle_create_media_job(payload)
            return
        if route.startswith("/api/jobs/") and route.endswith("/retry-failed"):
            job_id = route.split("/")[-2]
            job = self.require_state().get_job(job_id)
            if not job:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Job not found.")
                return
            self.handle_retry_failed_job(job)
            return
        if route.startswith("/api/jobs/") and route.endswith("/cancel"):
            job_id = route.split("/")[-2]
            job = self.require_state().get_job(job_id)
            if not job:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Job not found.")
                return
            with job.lock:
                job.cancel_requested = True
                active_processes = list(job.active_processes)
            for process in active_processes:
                try:
                    process.terminate()
                except Exception:
                    continue
            add_job_log(job, "Cancel requested.")
            self.send_json({"ok": True, "job_id": job_id})
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Route not found.")

    def handle_create_job(self, payload: dict[str, Any]) -> None:
        csv_text = str(payload.get("csv_text") or "")
        file_name = str(payload.get("file_name") or "uploaded.csv")
        mappings = payload.get("mappings") or {}
        options = payload.get("options") or {}

        try:
            headers, rows = parse_csv_text(csv_text)
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        detected = detect_mappings(headers)
        merged_mappings = {
            key: str(mappings.get(key) or detected.get(key) or "") or None
            for key in ("url", "filename", "method", "request_data", "subdir", "referer", "size_bytes")
        }
        if not merged_mappings.get("url"):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "A URL column is required to start downloads.")
            return

        raw_output_dir = str(options.get("output_dir") or self.require_state().default_output_dir)
        output_dir = Path(raw_output_dir).expanduser().resolve()
        concurrency = max(1, min(int(options.get("concurrency") or DEFAULT_WORKERS), 16))
        collision_strategy = str(options.get("collision_strategy") or "unique")
        if collision_strategy not in {"unique", "overwrite", "skip"}:
            collision_strategy = "unique"

        job = create_job(
            file_name=file_name,
            output_dir=output_dir,
            options={
                "output_dir": str(output_dir),
                "concurrency": concurrency,
                "collision_strategy": collision_strategy,
                "use_subdirectories": bool(options.get("use_subdirectories", True)),
                "timeout_seconds": max(5, min(int(options.get("timeout_seconds") or self.require_state().timeout_seconds), 600)),
                "retry_attempts": DEFAULT_DOWNLOAD_RETRIES,
                "request_spacing_seconds": DEFAULT_REQUEST_SPACING_SECONDS,
            },
            mappings=merged_mappings,
            headers=headers,
            rows=rows,
        )
        start_job(self.require_state(), job)
        self.send_json({"job_id": job.job_id, "status": job.status}, status=HTTPStatus.CREATED)

    def handle_create_discovery_job(self, payload: dict[str, Any]) -> None:
        start_url = str(payload.get("start_url") or "").strip()
        if not start_url:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "A start URL is required.")
            return

        parsed = parse.urlparse(start_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "URL must include http:// or https:// and a hostname.")
            return

        scan_mode = str(payload.get("scan_mode") or "single_page").strip()
        if scan_mode not in {"single_page", "deep_dive"}:
            scan_mode = "single_page"

        try:
            depth_limit = int(payload.get("depth_limit") or 2)
        except (TypeError, ValueError):
            depth_limit = 2
        depth_limit = max(1, min(depth_limit, 5))

        job = build_discovery_job(
            source_type="url",
            start_url=start_url,
            scan_mode=scan_mode,
            depth_limit=depth_limit,
        )
        start_discovery_job(self.require_state(), job)
        self.send_json(
            {
                "job_id": job.job_id,
                "status": job.status,
                "profile": job.profile,
            },
            status=HTTPStatus.CREATED,
        )

    def handle_import_discovery_csv(self, payload: dict[str, Any]) -> None:
        csv_text = str(payload.get("csv_text") or "")
        file_name = str(payload.get("file_name") or "uploaded.csv")
        if not csv_text.strip():
            self.send_error_json(HTTPStatus.BAD_REQUEST, "A scrape CSV is required.")
            return
        try:
            summary, items = import_scraped_csv(csv_text, file_name)
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        job = build_discovery_job(
            source_type="csv",
            start_url="",
            scan_mode="imported_csv",
            depth_limit=0,
            source_file_name=file_name,
        )
        with job.lock:
            job.profile = "imported"
            job.status = "completed"
            job.started_at = time.time()
            job.summary = {
                **summary,
                "detected_profile": "imported",
                "scan_mode": "imported_csv",
                "depth_limit": 0,
                "candidate_count": len(items),
                "inspected_count": len(items),
            }
            job.records = items
            job.pages = build_discovery_pages(items)
            job.finished_at = time.time()
        save_discovery_artifacts(self.require_state(), job, items, raw_import_csv=csv_text)
        log_discovery(job, f"Imported {len(items)} downloadable rows from {file_name}.")
        self.require_state().add_discovery_job(job)
        self.send_json(
            {
                "job_id": job.job_id,
                "status": job.status,
                "profile": job.profile,
            },
            status=HTTPStatus.CREATED,
        )

    def handle_create_download_job_from_discovery(self, payload: dict[str, Any]) -> None:
        discovery_job_id = str(payload.get("discovery_job_id") or "").strip()
        discovery_job = self.require_state().get_discovery_job(discovery_job_id)
        if discovery_job is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Discovery job not found.")
            return

        selected_ids_raw = payload.get("selected_record_ids")
        if not isinstance(selected_ids_raw, list) or not selected_ids_raw:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "At least one selected record is required.")
            return
        selected_ids = {str(item) for item in selected_ids_raw}

        with discovery_job.lock:
            if discovery_job.status != "completed":
                self.send_error_json(HTTPStatus.BAD_REQUEST, "Discovery job is not ready for downloads.")
                return
            selected_records = [
                record for record in discovery_job.records if record.record_id in selected_ids
            ]
        if not selected_records:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "No matching discovery records were selected.")
            return

        options = payload.get("options") or {}
        raw_output_dir = str(options.get("output_dir") or self.require_state().default_output_dir)
        output_dir = Path(raw_output_dir).expanduser().resolve()
        concurrency = max(1, min(int(options.get("concurrency") or DEFAULT_WORKERS), 16))
        collision_strategy = str(options.get("collision_strategy") or "unique")
        if collision_strategy not in {"unique", "overwrite", "skip"}:
            collision_strategy = "unique"

        tasks = build_tasks_from_discovery(selected_records)
        job = create_job_from_tasks(
            file_name=f"discovery-{discovery_job.job_id}.json",
            output_dir=output_dir,
            options={
                "output_dir": str(output_dir),
                "concurrency": concurrency,
                "collision_strategy": collision_strategy,
                "use_subdirectories": False,
                "timeout_seconds": max(5, min(int(options.get("timeout_seconds") or self.require_state().timeout_seconds), 600)),
                "retry_attempts": DEFAULT_DOWNLOAD_RETRIES,
                "request_spacing_seconds": DEFAULT_REQUEST_SPACING_SECONDS,
            },
            mappings={
                "url": "url",
                "filename": "filename",
                "method": "method",
                "request_data": "request_data",
                "subdir": None,
                "referer": "source_page",
                "size_bytes": "size_bytes",
            },
            headers=["url", "filename", "method", "request_data", "source_page", "size_bytes"],
            tasks=tasks,
        )
        start_job(self.require_state(), job)
        self.send_json(
            {
                "job_id": job.job_id,
                "status": job.status,
                "selected_count": len(selected_records),
            },
            status=HTTPStatus.CREATED,
        )

    def handle_create_media_job(self, payload: dict[str, Any]) -> None:
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "A media URL is required.")
            return
        parsed = parse.urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Media URL must include http:// or https:// and a hostname.")
            return
        if yt_dlp_binary() is None:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "yt-dlp is not installed on this machine.")
            return

        options = payload.get("options") or {}
        raw_output_dir = str(options.get("output_dir") or self.require_state().default_output_dir)
        output_dir = Path(raw_output_dir).expanduser().resolve()
        download_type = str(options.get("download_type") or "video").strip().lower()
        if download_type not in {"video", "audio"}:
            download_type = "video"
        output_format = str(options.get("format") or ("any" if download_type == "video" else "mp3")).strip().lower()
        allowed_formats = {"video": {"any", "mp4"}, "audio": {"mp3", "m4a", "opus"}}[download_type]
        if output_format not in allowed_formats:
            output_format = "any" if download_type == "video" else "mp3"
        quality = str(options.get("quality") or ("best" if download_type == "video" else "192")).strip().lower()
        try:
            playlist_limit = max(0, int(options.get("playlist_limit") or 0))
        except (TypeError, ValueError):
            playlist_limit = 0
        subdir = sanitize_segment(str(options.get("subdir") or "").strip(), "")
        cookies_path = str(options.get("cookies_path") or "").strip()

        job = build_media_job(
            source_url=source_url,
            output_dir=output_dir,
            options={
                "output_dir": str(output_dir),
                "download_type": download_type,
                "format": output_format,
                "quality": quality,
                "playlist_limit": playlist_limit,
                "subdir": subdir,
                "cookies_path": cookies_path,
            },
        )
        start_job(self.require_state(), job)
        self.send_json(
            {
                "job_id": job.job_id,
                "status": job.status,
                "job_kind": job.job_kind,
            },
            status=HTTPStatus.CREATED,
        )

    def handle_retry_failed_job(self, source_job: DownloadJob) -> None:
        if source_job.job_kind == "media":
            job = build_media_job(
                source_url=str(source_job.source.get("source_url") or ""),
                output_dir=source_job.output_dir,
                options={
                    **source_job.options,
                    "download_type": source_job.source.get("download_type") or source_job.options.get("download_type") or "video",
                    "format": source_job.source.get("format") or source_job.options.get("format") or "any",
                    "quality": source_job.source.get("quality") or source_job.options.get("quality") or "best",
                    "playlist_limit": source_job.source.get("playlist_limit") or source_job.options.get("playlist_limit") or 0,
                    "subdir": source_job.source.get("subdir") or source_job.options.get("subdir") or "",
                },
            )
            start_job(self.require_state(), job)
            self.send_json(
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "source_job_id": source_job.job_id,
                },
                status=HTTPStatus.CREATED,
            )
            return

        failed_rows = [dict(row.raw) for row in source_job.rows if row.status == "failed"]
        if not failed_rows:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "This job does not have any failed rows to retry.")
            return

        job = create_job(
            file_name=f"{source_job.file_name} (retry failed)",
            output_dir=source_job.output_dir,
            options=source_job.options,
            mappings=source_job.mappings,
            headers=source_job.headers,
            rows=failed_rows,
        )
        start_job(self.require_state(), job)
        self.send_json(
            {
                "job_id": job.job_id,
                "status": job.status,
                "retried_rows": len(failed_rows),
                "source_job_id": source_job.job_id,
            },
            status=HTTPStatus.CREATED,
        )

    def serve_static(self, relative_path: str) -> None:
        safe_path = (STATIC_DIR / relative_path).resolve()
        if STATIC_DIR not in safe_path.parents and safe_path != STATIC_DIR:
            self.send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
            return
        if not safe_path.exists() or not safe_path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
            return
        content_type = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
        payload = safe_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict[str, Any] | None:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Missing request body.")
            return None
        try:
            length = int(length_header)
        except ValueError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header.")
            return None
        raw_body = self.rfile.read(length)
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.")
            return None

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def require_state(self) -> AppState:
        if STATE is None:
            raise RuntimeError("Application state was not initialized.")
        return STATE

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DownloadScapper web app locally.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host. Default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port. Default: {DEFAULT_PORT}")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Default destination directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--scrape-save-dir",
        default=str(DEFAULT_SCRAPE_SAVE_DIR),
        help=f"Directory where discovered scrape CSV/JSON files are archived. Default: {DEFAULT_SCRAPE_SAVE_DIR}",
    )
    parser.add_argument(
        "--state-dir",
        default=str(DEFAULT_STATE_DIR),
        help=f"Directory where app job/discovery state is persisted. Default: {DEFAULT_STATE_DIR}",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-download timeout in seconds. Default: {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server without opening a browser tab.",
    )
    return parser.parse_args()


def main() -> None:
    global STATE

    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scrape_save_dir = Path(args.scrape_save_dir).expanduser().resolve()
    scrape_save_dir.mkdir(parents=True, exist_ok=True)
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    STATE = AppState(
        default_output_dir=output_dir,
        scrape_save_dir=scrape_save_dir,
        state_dir=state_dir,
        timeout_seconds=args.timeout_seconds,
    )
    STATE.load_persisted_state()

    server = ThreadingHTTPServer((args.host, args.port), DownloadClientHandler)
    app_url = f"http://{args.host}:{args.port}"
    print(f"{APP_NAME} running at {app_url}")
    print(f"Default output directory: {output_dir}")
    print(f"Scrape archive directory: {scrape_save_dir}")
    print(f"State directory: {state_dir}")
    if not args.no_browser:
        try:
            webbrowser.open(app_url)
            print("Opened browser automatically.")
        except Exception:
            print("Browser auto-open failed. Open the URL manually.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
