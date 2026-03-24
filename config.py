#!/usr/bin/env python3
"""Configuration loader for DownloadScapper.

Searches for a ``config.toml`` file in (in priority order):
1. Explicit path passed to :func:`load_config`.
2. The project root directory (next to this file).
3. The local workspace directory.
4. ``~/.config/downloadscapper/config.toml`` (XDG-style).

Merges any found file on top of :data:`DEFAULT_CONFIG` using deep merge.

On Python 3.11+ this uses the built-in ``tomllib``; on older versions it
falls back to a small hand-rolled parser that handles the subset of TOML
actually used in the default config (flat sections, string / bool / int /
float scalars).

Example ``config.toml``::

    [ui]
    port = 9000
    no_browser = true

    [downloads]
    concurrency = 4
    resume = true
    proxy = "http://proxy.example.com:8080"

    [notifications]
    webhook_url = "https://hooks.example.com/notify"

    [logging]
    level = "DEBUG"
    file = "~/.local/share/downloadscapper/app.log"
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
    _HAS_TOMLLIB = True
except ImportError:
    tomllib = None  # type: ignore[assignment]
    _HAS_TOMLLIB = False

from project_paths import default_project_dir, default_workspace_dir


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "ui": {
        "host": "127.0.0.1",
        "port": 8765,
        "no_browser": False,
    },
    "downloads": {
        # None → resolved at runtime to the platform-appropriate Downloads folder
        "output_dir": None,
        "concurrency": 1,
        "timeout_seconds": 60,
        "retry_attempts": 4,
        "request_spacing_seconds": 2.0,
        "collision_strategy": "unique",   # "unique" | "overwrite" | "skip"
        "use_subdirectories": True,
        # Resumable downloads: send Range header when a .part file exists
        "resume": True,
        # TLS verification (set False only for dev/testing with broken certs)
        "verify_ssl": True,
        # HTTP/HTTPS/SOCKS proxy, e.g. "http://user:pass@proxy:8080"
        "proxy": None,
        # HTTP Basic Auth, format "username:password"
        "basic_auth": None,
        # Path to a Netscape-format cookies.txt file for authenticated downloads
        "cookies_file": None,
        # Extra request headers sent with every HTTP download
        "custom_headers": {},
        # Content-hash deduplication: skip downloads where an identical local
        # file already exists (SHA-256 comparison)
        "dedup_by_hash": False,
    },
    "crawler": {
        "max_pages": 25,
        "workers": 8,
        "timeout": 15.0,
        "retries": 2,
        "backoff": 1.0,
        "ignore_robots": False,
        # TLS verification for crawler requests
        "verify_ssl": True,
        # Proxy for crawler requests
        "proxy": None,
        # Extra headers to send with every crawl request
        "custom_headers": {},
    },
    "notifications": {
        # POST a JSON payload to this URL when a download job finishes
        "webhook_url": None,
        "webhook_on_complete": True,
        "webhook_on_error": True,
        # Timeout (seconds) when posting the webhook
        "webhook_timeout": 10,
    },
    "logging": {
        # "DEBUG" | "INFO" | "WARNING" | "ERROR"
        "level": "INFO",
        # If set, also write structured logs to this file path (~ expanded)
        "file": None,
    },
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load configuration, merging a config.toml on top of defaults.

    Args:
        config_path: Explicit path to a ``config.toml`` file.  When *None*,
                     the standard search locations are tried.

    Returns:
        A fully populated configuration dict (deep-copied from defaults).
    """
    path = config_path or _find_config_path()
    result = copy.deepcopy(DEFAULT_CONFIG)

    if path is None or not path.exists():
        return result

    try:
        user_cfg = _parse_toml(path)
    except Exception:
        # Silently fall back to defaults rather than crashing on startup
        return result

    _deep_merge(result, user_cfg)
    return result


def get_default(section: str, key: str) -> Any:
    """Return the default value for a config key."""
    return DEFAULT_CONFIG.get(section, {}).get(key)


def write_example_config(path: Path) -> None:
    """Write a commented example config.toml to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_EXAMPLE_CONFIG, encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_config_path() -> Path | None:
    candidates = [
        default_project_dir() / "config.toml",
        default_workspace_dir() / "config.toml",
        Path.home() / ".config" / "downloadscapper" / "config.toml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _parse_toml(path: Path) -> dict[str, Any]:
    if _HAS_TOMLLIB:
        with path.open("rb") as fh:
            return tomllib.load(fh)  # type: ignore[union-attr]
    return _simple_toml_parse(path)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Recursively merge *override* into *base* in-place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _simple_toml_parse(path: Path) -> dict[str, Any]:
    """Minimal TOML parser for flat/section configs (no arrays of tables etc).

    Handles:
    - ``[section]`` headers
    - ``key = value`` (string, bool, int, float)
    - ``# comment`` lines
    """
    result: dict[str, Any] = {}
    current: dict[str, Any] = result

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        # Section header
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section not in result:
                result[section] = {}
            current = result[section]
            continue

        # Key = value
        if "=" not in line:
            continue
        key, _, raw_val = line.partition("=")
        key = key.strip()
        raw_val = raw_val.strip()

        if raw_val.lower() in ("true", "false"):
            value: Any = raw_val.lower() == "true"
        elif (raw_val.startswith('"') and raw_val.endswith('"')) or (
            raw_val.startswith("'") and raw_val.endswith("'")
        ):
            value = raw_val[1:-1]
        else:
            try:
                value = int(raw_val)
            except ValueError:
                try:
                    value = float(raw_val)
                except ValueError:
                    value = raw_val

        current[key] = value

    return result


# ---------------------------------------------------------------------------
# Example config template
# ---------------------------------------------------------------------------

_EXAMPLE_CONFIG = """\
# DownloadScapper configuration file
# Place this file in the project root, workspace root, or
# ~/.config/downloadscapper/config.toml

[ui]
# host = "127.0.0.1"
# port = 8765
# no_browser = false

[downloads]
# Default download destination (leave unset to use platform default)
# output_dir = "~/Downloads/csv-download-client"

# Parallel download workers (1-16)
# concurrency = 1

# Per-request timeout in seconds
# timeout_seconds = 60

# Retry attempts on network errors
# retry_attempts = 4

# Seconds between requests to the same host
# request_spacing_seconds = 2.0

# File collision strategy: "unique" | "overwrite" | "skip"
# collision_strategy = "unique"

# Resume partial downloads using HTTP Range requests
# resume = true

# Verify TLS certificates (disable only for dev with broken cert chains)
# verify_ssl = true

# HTTP/HTTPS proxy, e.g. "http://user:pass@proxy.example.com:8080"
# proxy = ""

# HTTP Basic Auth credentials, format "username:password"
# basic_auth = ""

# Path to a Netscape cookies.txt file
# cookies_file = ""

# Skip files whose SHA-256 hash matches an already-downloaded local file
# dedup_by_hash = false

[crawler]
# max_pages = 25
# workers = 8
# timeout = 15.0
# retries = 2
# backoff = 1.0
# ignore_robots = false
# verify_ssl = true
# proxy = ""

[notifications]
# POST JSON status updates to this URL when a job finishes
# webhook_url = ""
# webhook_on_complete = true
# webhook_on_error = true
# webhook_timeout = 10

[logging]
# level = "INFO"   # DEBUG | INFO | WARNING | ERROR
# file = ""        # e.g. "~/.local/share/downloadscapper/app.log"
"""
