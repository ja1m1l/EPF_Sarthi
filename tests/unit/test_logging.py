# EPF Sentinel - pytest unit tests for shared/logging.py
#
# What is real vs stubbed
# -----------------------
# * Real  : StructuredLogger, _StructuredFormatter, get_logger,
#           set_correlation_id, _correlation_id.
# * Stubbed: The root logger's StreamHandler stream is swapped to
#            io.StringIO per-capture so JSON lines can be inspected
#            without hitting stdout/CloudWatch.  Named: _capture_log().
# * sys.path is configured by the root conftest.py (adds src/shared + src).

from __future__ import annotations

import io
import json
import logging

import pytest

import shared.logging as slog
from shared.logging import (
    StructuredLogger,
    get_logger,
    set_correlation_id,
)


# -----------------------------------------------------------------
# Capture helper
# -----------------------------------------------------------------

def _capture_log(logger: StructuredLogger, level: str, msg: str, **kw) -> dict:
    """
    Emit one log line via *logger* and return the parsed JSON dict.

    Stubs: replaces the root logger's handlers with a single
    StreamHandler pointing at an io.StringIO buffer for the duration
    of the call, then restores the original handlers.
    """
    buf = io.StringIO()
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    root.handlers.clear()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(slog._StructuredFormatter())
    root.addHandler(handler)
    try:
        getattr(logger, level)(msg, **kw)
        raw = buf.getvalue().strip()
        return json.loads(raw) if raw else {}
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)


# -----------------------------------------------------------------
# Formatter shape
# -----------------------------------------------------------------

class TestStructuredFormatter:
    def test_required_keys_present(self):
        log = get_logger("test.formatter")
        payload = _capture_log(log, "info", "test.event")
        for key in ("ts", "level", "service", "correlationId", "event"):
            assert key in payload, f"Missing key: {key}"

    def test_level_is_uppercase(self):
        payload = _capture_log(get_logger("test.level"), "warning", "test.warn")
        assert payload["level"] == "WARNING"

    def test_ts_has_utc_offset(self):
        payload = _capture_log(get_logger("test.ts"), "info", "ts.check")
        assert "+00:00" in payload["ts"], f"No UTC offset: {payload['ts']!r}"

    def test_event_matches_message(self):
        payload = _capture_log(get_logger("test.event"), "info", "my.custom.event")
        assert payload["event"] == "my.custom.event"

    def test_extra_fields_merged(self):
        payload = _capture_log(
            get_logger("test.extras"), "info", "test.extras",
            claimId="abc", amountPaise=100
        )
        assert payload["claimId"] == "abc"
        assert payload["amountPaise"] == 100

    def test_service_name(self):
        payload = _capture_log(get_logger("test.service"), "info", "svc.check")
        assert payload["service"] == "epf-sentinel"


# -----------------------------------------------------------------
# Correlation ID
# -----------------------------------------------------------------

class TestCorrelationId:
    def test_set_correlation_id_appears_in_log(self):
        set_correlation_id("req-xyz-123")
        payload = _capture_log(get_logger("test.corr"), "info", "corr.check")
        assert payload["correlationId"] == "req-xyz-123"

    def test_no_correlation_id_generates_uuid(self):
        slog._current_correlation_id = ""
        payload = _capture_log(get_logger("test.uuid"), "info", "uuid.check")
        cid = payload["correlationId"]
        assert len(cid) == 36
        assert cid.count("-") == 4


# -----------------------------------------------------------------
# StructuredLogger public API
# -----------------------------------------------------------------

class TestStructuredLoggerAPI:
    def test_all_levels_emit(self):
        log = get_logger("test.levels")
        root = logging.getLogger()
        original_level = root.level
        # Lower the root level to DEBUG so debug messages are not filtered
        # before they reach the handler.  Restored in the finally block.
        root.setLevel(logging.DEBUG)
        try:
            for level in ("debug", "info", "warning", "error", "critical"):
                payload = _capture_log(log, level, f"test.{level}")
                assert payload.get("event") == f"test.{level}", (
                    f"Level {level!r} produced empty payload: {payload!r}"
                )
        finally:
            root.setLevel(original_level)

    def test_warn_alias(self):
        payload = _capture_log(get_logger("test.warn_alias"), "warn", "warn.alias")
        assert payload["level"] == "WARNING"

    def test_get_logger_idempotent(self):
        a = get_logger("test.idempotent")
        b = get_logger("test.idempotent")
        assert a is b
