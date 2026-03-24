"""Tests for the logging_setup module."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from logging_setup import (
    APP_LOGGER_NAME,
    get_logger,
    level_from_string,
    setup_logging,
)


class TestGetLogger:
    def test_returns_logger_under_namespace(self):
        logger = get_logger("mymodule")
        assert logger.name.startswith(APP_LOGGER_NAME)

    def test_returns_same_logger_for_same_name(self):
        assert get_logger("x") is get_logger("x")

    def test_full_qualified_name_not_double_prefixed(self):
        logger = get_logger(f"{APP_LOGGER_NAME}.mymodule")
        assert not logger.name.startswith(f"{APP_LOGGER_NAME}.{APP_LOGGER_NAME}")


class TestLevelFromString:
    def test_debug(self):
        assert level_from_string("DEBUG") == logging.DEBUG

    def test_info(self):
        assert level_from_string("INFO") == logging.INFO

    def test_warning(self):
        assert level_from_string("WARNING") == logging.WARNING

    def test_error(self):
        assert level_from_string("ERROR") == logging.ERROR

    def test_case_insensitive(self):
        assert level_from_string("debug") == logging.DEBUG

    def test_unknown_returns_info(self):
        assert level_from_string("UNKNOWN_LEVEL") == logging.INFO


class TestSetupLogging:
    def test_returns_logger(self):
        logger = setup_logging(level=logging.WARNING)
        assert isinstance(logger, logging.Logger)

    def test_writes_to_log_file(self, tmp_path):
        # Reset the configured flag so this test can configure fresh
        import logging_setup
        logging_setup._configured = False

        log_path = tmp_path / "test.log"
        setup_logging(level=logging.DEBUG, log_file=log_path)
        logger = get_logger("test_file_logging")
        logger.debug("hello from test")

        # Flush handlers
        for handler in logging.getLogger(APP_LOGGER_NAME).handlers:
            handler.flush()

        assert log_path.exists()
        assert "hello from test" in log_path.read_text(encoding="utf-8")

        # Cleanup: remove file handler to avoid interfering with other tests
        root_logger = logging.getLogger(APP_LOGGER_NAME)
        for handler in list(root_logger.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                root_logger.removeHandler(handler)
        logging_setup._configured = False
