"""Shared utility functions for the downloader package."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


def human_size(size_bytes: int | None) -> str:
    """Return a human-readable file size string, e.g. '1.23 MB'."""
    if size_bytes is None:
        return "unknown"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def sanitize_segment(value: str | None, fallback: str) -> str:
    """Sanitize a string for use as a filesystem path segment.

    Replaces characters that are illegal on Windows/macOS/Linux, collapses
    runs of whitespace, strips leading/trailing dots and spaces, and
    truncates to 120 characters.
    """
    raw = (value or "").strip()
    if not raw:
        return fallback
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:120] or fallback


def slugify_for_path(value: str | None, fallback: str) -> str:
    """Lowercase, ASCII-only slug suitable for directory names."""
    cleaned = sanitize_segment(value, fallback).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return cleaned or fallback


def normalize_header(value: str) -> str:
    """Normalise a CSV header name to a lowercase snake_case key."""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def validate_subdir_path(subdir: str | None) -> str | None:
    """Return a safe relative path, or *None* if the value is unsafe.

    Rejects:
    - Empty / whitespace-only strings
    - Null bytes
    - Absolute paths (after separator normalisation)
    - Any ``..`` component (path traversal)
    """
    if not subdir:
        return None
    if "\x00" in subdir:
        return None
    cleaned = subdir.replace("\\", "/").strip("/")
    if not cleaned:
        return None
    parts = [p for p in cleaned.split("/") if p and p != "."]
    if any(p == ".." for p in parts) or not parts:
        return None
    return "/".join(parts)


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically via a temporary file + rename.

    Guarantees that readers never see a partially-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(data)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def compute_file_hash(path: Path, algorithm: str = "sha256") -> str | None:
    """Return the hex digest of a file, or *None* on I/O error."""
    try:
        h = hashlib.new(algorithm)
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def format_speed(bps: float) -> str:
    """Return a human-readable download speed string, e.g. '1.2 MB/s'."""
    if bps < 0:
        return "? B/s"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024 or unit == "GB/s":
            return f"{bps:.1f} {unit}"
        bps /= 1024
    return f"{bps:.1f} B/s"


def format_eta(seconds: float | None) -> str:
    """Return a human-readable ETA string, e.g. '1m 23s'."""
    if seconds is None:
        return ""
    secs = int(seconds)
    if secs <= 0:
        return "0s"
    parts = []
    if secs >= 3600:
        parts.append(f"{secs // 3600}h")
        secs %= 3600
    if secs >= 60:
        parts.append(f"{secs // 60}m")
        secs %= 60
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)
