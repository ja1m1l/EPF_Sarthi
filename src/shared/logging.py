"""
Structured JSON logger for EPF Sentinel.

Every Lambda must import this module and call get_logger(__name__).
No print(), no bare logging.basicConfig().

Log record shape:
    {
        "ts":            "<ISO-8601 UTC with +00:00>",
        "level":         "INFO" | "WARNING" | "ERROR" | "DEBUG" | "CRITICAL",
        "service":       "epf-sentinel",
        "correlationId": "<X-Correlation-Id header or auto uuid>",
        "event":         "<message string>",
        ...extra_fields
    }

Usage inside a Lambda handler:
    from shared.logging import get_logger
    log = get_logger(__name__)
    log.info("claim.submitted", claimId="abc", userId="u1")
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────
# Internal formatter
# ─────────────────────────────────────────────────────────────

class _StructuredFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    SERVICE = os.environ.get("POWERTOOLS_SERVICE_NAME", "epf-sentinel")

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "service": self.SERVICE,
            "correlationId": getattr(record, "correlationId", _correlation_id()),
            "event": record.getMessage(),
        }

        # Merge any extra fields attached via the `extra={}` kwarg or
        # the structured helpers below.
        extras: dict[str, Any] = getattr(record, "_extra_fields", {})
        payload.update(extras)

        # Capture exception info when present.
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
# Correlation-ID context (per-invocation, not per-thread)
# ─────────────────────────────────────────────────────────────

_current_correlation_id: str = ""


def set_correlation_id(correlation_id: str) -> None:
    """Call once at the top of each Lambda handler with the request ID."""
    global _current_correlation_id
    _current_correlation_id = correlation_id


def _correlation_id() -> str:
    return _current_correlation_id or str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────
# Structured logger wrapper
# ─────────────────────────────────────────────────────────────

class StructuredLogger:
    """
    Thin wrapper around :class:`logging.Logger` that injects keyword
    arguments into the log payload as extra fields.

    Examples
    --------
    log = get_logger(__name__)
    log.info("claim.submitted", claimId="abc123", amountPaise=48000000)
    log.error("dynamo.put_item.failed", exc_info=True, table="Claims")
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    # ── private helpers ───────────────────────────────────────

    def _log(
        self,
        level: int,
        event: str,
        *,
        exc_info: bool = False,
        stack_info: bool = False,
        **fields: Any,
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return

        record = self._logger.makeRecord(
            name=self._logger.name,
            level=level,
            fn="",
            lno=0,
            msg=event,
            args=(),
            exc_info=sys.exc_info() if exc_info else None,
            extra=None,
        )
        record._extra_fields = fields  # type: ignore[attr-defined]
        record.correlationId = _correlation_id()  # type: ignore[attr-defined]
        self._logger.handle(record)

    # ── public API ────────────────────────────────────────────

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, *, exc_info: bool = False, **fields: Any) -> None:
        self._log(logging.ERROR, event, exc_info=exc_info, **fields)

    def critical(self, event: str, *, exc_info: bool = False, **fields: Any) -> None:
        self._log(logging.CRITICAL, event, exc_info=exc_info, **fields)

    # Convenience alias used by older-style code.
    warn = warning


# ─────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────

_loggers: dict[str, StructuredLogger] = {}


def get_logger(name: str) -> StructuredLogger:
    """
    Return a :class:`StructuredLogger` configured for *name*.

    Idempotent – multiple calls with the same *name* return the same instance.
    The root handler is configured on first call.
    """
    if name in _loggers:
        return _loggers[name]

    _configure_root_once()

    inner = logging.getLogger(name)
    inner.propagate = True
    wrapped = StructuredLogger(inner)
    _loggers[name] = wrapped
    return wrapped


_root_configured = False


def _configure_root_once() -> None:
    global _root_configured
    if _root_configured:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    # Remove any handlers Lambda pre-installs (LambdaLoggerHandler adds
    # its own prefix; we want clean JSON only).
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    _root_configured = True
