"""AppState — the central job registry with thread-safe access and persistence.

This module extracts the ``AppState`` class (and related helpers) from the
monolithic ``csv_download_client.py`` so that it can be imported and tested
independently.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from downloader.models import DiscoveryJob, DownloadJob
from downloader.utils import atomic_write
from logging_setup import get_logger

if TYPE_CHECKING:
    pass

_log = get_logger(__name__)


class AppState:
    """In-memory job registry with optional disk persistence.

    All access to the internal job dicts is protected by a single lock so
    that the HTTP request handler threads can safely read/write state while
    download worker threads run concurrently.

    Persistence uses :func:`~downloader.utils.atomic_write` to guarantee
    that readers never observe partially-written state files.
    """

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

    # ------------------------------------------------------------------
    # Download jobs
    # ------------------------------------------------------------------

    def add_job(self, job: DownloadJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job
        self.persist_job(job)

    def get_job(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[DownloadJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def _job_path(self, job_id: str) -> Path:
        return self.state_dir / "jobs" / f"{job_id}.json"

    def persist_job(self, job: DownloadJob) -> None:
        """Atomically persist job state to disk."""
        from csv_download_client import serialize_job  # avoid circular at import time
        target = self._job_path(job.job_id)
        data = json.dumps(serialize_job(job), indent=2).encode("utf-8")
        try:
            atomic_write(target, data)
        except OSError as exc:
            _log.warning("Could not persist job %s: %s", job.job_id, exc)

    # ------------------------------------------------------------------
    # Discovery jobs
    # ------------------------------------------------------------------

    def add_discovery_job(self, job: DiscoveryJob) -> None:
        with self._lock:
            self._discovery_jobs[job.job_id] = job
        self.persist_discovery_job(job)

    def get_discovery_job(self, job_id: str) -> DiscoveryJob | None:
        with self._lock:
            return self._discovery_jobs.get(job_id)

    def list_discovery_jobs(self) -> list[DiscoveryJob]:
        with self._lock:
            return sorted(
                self._discovery_jobs.values(), key=lambda j: j.created_at, reverse=True
            )

    def _discovery_job_path(self, job_id: str) -> Path:
        return self.state_dir / "discovery" / f"{job_id}.json"

    def persist_discovery_job(self, job: DiscoveryJob) -> None:
        """Atomically persist discovery job state to disk."""
        from csv_download_client import serialize_discovery_job
        target = self._discovery_job_path(job.job_id)
        data = json.dumps(serialize_discovery_job(job), indent=2).encode("utf-8")
        try:
            atomic_write(target, data)
        except OSError as exc:
            _log.warning("Could not persist discovery job %s: %s", job.job_id, exc)

    # ------------------------------------------------------------------
    # State restoration from disk
    # ------------------------------------------------------------------

    def load_persisted_state(self) -> None:
        """Re-hydrate jobs from disk after an application restart."""
        from csv_download_client import deserialize_job, deserialize_discovery_job

        for path in sorted((self.state_dir / "jobs").glob("*.json")):
            try:
                job = deserialize_job(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                _log.debug("Skipping corrupt job file %s: %s", path.name, exc)
                continue
            with self._lock:
                self._jobs[job.job_id] = job

        for path in sorted((self.state_dir / "discovery").glob("*.json")):
            try:
                job = deserialize_discovery_job(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                _log.debug("Skipping corrupt discovery file %s: %s", path.name, exc)
                continue
            with self._lock:
                self._discovery_jobs[job.job_id] = job

        _log.info(
            "Loaded %d download job(s) and %d discovery job(s) from disk.",
            len(self._jobs),
            len(self._discovery_jobs),
        )
