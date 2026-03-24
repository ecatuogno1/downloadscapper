"""DownloadScapper downloader sub-package.

This package houses the modular components extracted from the monolithic
``csv_download_client.py``.  The top-level ``csv_download_client.py`` still
exists as a thin shell that wires everything together and starts the HTTP
server, but the actual logic now lives in these sub-modules:

- ``models``       — dataclasses: RowTask, DownloadJob, DiscoveryJob, …
- ``state``        — AppState (job registry + persistence)
- ``serialization``— serialize / deserialize helpers
- ``utils``        — shared utility functions (human_size, sanitize_segment, …)
- ``jobs``         — HTTP download execution (transfer_download, run_job, …)
- ``media``        — yt-dlp / media download execution
- ``discovery``    — web-crawl discovery job runner
- ``database``     — SQLite search-index queries
- ``server``       — BaseHTTPRequestHandler subclass + routing
"""

from downloader.models import DownloadJob, DiscoveryJob, DiscoveryRecordItem, RowTask
from downloader.state import AppState
from downloader.utils import human_size, sanitize_segment, slugify_for_path

__all__ = [
    "AppState",
    "DiscoveryJob",
    "DiscoveryRecordItem",
    "DownloadJob",
    "RowTask",
    "human_size",
    "sanitize_segment",
    "slugify_for_path",
]
