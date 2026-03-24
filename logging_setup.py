#!/usr/bin/env python3
"""Centralized logging configuration for DownloadScapper.

Usage:
    from logging_setup import get_logger, setup_logging

    # At application startup:
    setup_logging(level=logging.INFO, log_file=Path("app.log"))

    # In any module:
    logger = get_logger(__name__)
    logger.info("Starting download...")
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

APP_LOGGER_NAME = "downloadscapper"

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_SHORT_FORMAT = "%(levelname)-8s %(message)s"

_configured = False


def setup_logging(
    level: int = logging.INFO,
    log_file: Path | None = None,
    fmt: str = _DEFAULT_FORMAT,
    stderr_fmt: str = _SHORT_FORMAT,
    quiet: bool = False,
) -> logging.Logger:
    """Configure the root application logger.

    Call this once at startup.  Subsequent calls are idempotent.

    Args:
        level:      Minimum log level (e.g. logging.DEBUG, logging.INFO).
        log_file:   If provided, also write structured logs to this file.
        fmt:        Format string for the file handler (full).
        stderr_fmt: Format string for the console handler (abbreviated).
        quiet:      If True, suppress console output entirely.

    Returns:
        The configured application Logger.
    """
    global _configured
    logger = logging.getLogger(APP_LOGGER_NAME)

    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    if not quiet:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(stderr_fmt))
        logger.addHandler(console)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(fmt))
            logger.addHandler(fh)
        except OSError as exc:
            logger.warning("Could not open log file %s: %s", log_file, exc)

    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger scoped under the application namespace.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A Logger whose full name is ``downloadscapper.<name>``.
    """
    if name.startswith(APP_LOGGER_NAME + ".") or name == APP_LOGGER_NAME:
        return logging.getLogger(name)
    # Strip leading package path for shorter names
    short = name.split(".")[-1] if "." in name else name
    return logging.getLogger(f"{APP_LOGGER_NAME}.{short}")


def level_from_string(value: str) -> int:
    """Convert a level name string (e.g. 'DEBUG') to a logging level int."""
    upper = value.strip().upper()
    level = logging.getLevelName(upper)
    if isinstance(level, int):
        return level
    return logging.INFO
