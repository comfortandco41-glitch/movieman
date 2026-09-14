"""Structured logging with job context.

Provides a consistent logging format that includes job_id and stage
when available. Filters out secret values from log output.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Optional


# Patterns that look like secrets (tokens, keys, etc.)
_SECRET_PATTERNS = [
    re.compile(r"\b\d+:[A-Za-z0-9_-]{35}\b"),  # Telegram bot token
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),   # Google API key
]


class SecretFilter(logging.Filter):
    """Filter that redacts potential secrets from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern in _SECRET_PATTERNS:
                record.msg = pattern.sub("****REDACTED****", record.msg)
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    fmt = "%(asctime)s %(levelname)-8s %(name)-25s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    handler.addFilter(SecretFilter())
    root.addHandler(handler)

    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)


class JobLogger:
    """Logger wrapper that includes job_id and stage in every message.

    Usage:
        log = JobLogger("abc123", "download")
        log.info("started")  # -> "job=abc123 stage=download started"
    """

    def __init__(
        self,
        job_id: str,
        stage: Optional[str] = None,
        logger_name: str = "pipeline",
    ) -> None:
        self._logger = logging.getLogger(logger_name)
        self._job_id = job_id
        self._stage = stage

    def _format(self, msg: str) -> str:
        parts = [f"job={self._job_id}"]
        if self._stage:
            parts.append(f"stage={self._stage}")
        parts.append(msg)
        return " ".join(parts)

    def with_stage(self, stage: str) -> "JobLogger":
        """Return a new JobLogger with updated stage."""
        return JobLogger(self._job_id, stage, self._logger.name)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(self._format(msg), *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(self._format(msg), *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(self._format(msg), *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(self._format(msg), *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(self._format(msg), *args, **kwargs)
