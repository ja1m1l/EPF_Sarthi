"""
HTTP response builder helpers for EPF Sentinel Lambda handlers.

Design rules
------------
* Every response body is JSON.
* Error bodies use a machine-readable ``error.code`` plus a ``field`` name
  so clients can surface field-level feedback without parsing free text.
* Stack traces, table names, and internal details **never** appear in any
  response body — use ``internal_error()`` for unexpected failures and let
  CloudWatch carry the details.

Usage
-----
    from shared.http import ok, created, bad_request, not_found, internal_error

    return ok(claim.to_dict())
    return bad_request("AMOUNT_NON_POSITIVE", "amountRupees", "Amount must be > 0")
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


# ─────────────────────────────────────────────────────────────
# JSON encoder — handles Decimal (DynamoDB numeric type)
# ─────────────────────────────────────────────────────────────

class _SafeEncoder(json.JSONEncoder):
    """Convert types that the stdlib encoder can't handle."""

    def default(self, obj: Any) -> Any:  # noqa: ANN401
        if isinstance(obj, Decimal):
            # Preserve int-ness: 480000 not 480000.0
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)


def _dumps(body: Any) -> str:
    return json.dumps(body, cls=_SafeEncoder, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
# Private builder
# ─────────────────────────────────────────────────────────────

_COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
}


def _response(status_code: int, body: Any) -> dict:
    return {
        "statusCode": status_code,
        "headers": _COMMON_HEADERS,
        "body": _dumps(body),
    }


# ─────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────

def ok(body: dict) -> dict:
    """200 OK."""
    return _response(200, body)


def created(body: dict) -> dict:
    """201 Created."""
    return _response(201, body)


def bad_request(code: str, field: str, message: str) -> dict:
    """
    400 Bad Request with a machine-readable error payload.

    Parameters
    ----------
    code:
        A SCREAMING_SNAKE_CASE string clients can ``switch`` on.
    field:
        The request field that caused the error (e.g. ``"amountRupees"``).
    message:
        A human-readable description (English only, not shown to end users).
    """
    return _response(
        400,
        {"error": {"code": code, "field": field, "message": message}},
    )


def not_found() -> dict:
    """
    404 Not Found.

    Intentionally generic — never discloses whether a resource exists
    but belongs to another user (no existence leak).
    """
    return _response(
        404,
        {"error": {"code": "NOT_FOUND", "message": "The requested resource was not found"}},
    )


def internal_error() -> dict:
    """
    500 Internal Server Error.

    Never includes a stack trace, table name, or any internal detail.
    Full error context is emitted to CloudWatch by the calling handler.
    """
    return _response(
        500,
        {"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
    )
