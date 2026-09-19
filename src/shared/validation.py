"""
Pydantic v2 request schemas and validation helpers for EPF Sentinel.

Design rules
------------
* Validation failures return 400 with a machine-readable ``code`` and a
  ``field`` name — never a stack trace.
* Money is validated as a decimal/string/int and converted to integer
  paise by ``CreateClaimRequest.to_paise()``.  Float inputs are accepted
  but the conversion is integer arithmetic (never ``int(float * 100)``
  which can drift).
* ``claimDate`` must not be in the future (UTC).  The boundary is checked
  against ``date.today()`` which is UTC on Lambda.

Error codes emitted
-------------------
CLAIM_DATE_IN_FUTURE   claimDate is after today
AMOUNT_NON_POSITIVE    amountRupees <= 0
AMOUNT_EXCEEDS_LIMIT   amountRupees > 10,000,000
AMOUNT_INVALID         amountRupees cannot be parsed as a number
INVALID_CLAIM_TYPE     claimType is not a recognised enum value
INVALID_STATUS         status is not a recognised enum value
MISSING_REQUIRED_FIELD a required field is absent from the body
VALIDATION_ERROR       fallback for any other Pydantic validation error
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import ValidationError as PydanticValidationError

from shared.clock import today_ist

from shared.models import ClaimStatus, ClaimType


# ─────────────────────────────────────────────────────────────
# Typed validation error
# ─────────────────────────────────────────────────────────────

class EPFValidationError(Exception):
    """
    Structured validation failure carrying a machine-readable code.

    Raised by :func:`parse_pydantic_error` and caught by handlers which
    convert it to a 400 response via :func:`shared.http.bad_request`.
    """

    def __init__(self, code: str, field: str, message: str) -> None:
        self.code = code
        self.field = field
        self.message = message
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# Request schema
# ─────────────────────────────────────────────────────────────

class CreateClaimRequest(BaseModel):
    """
    Validated body for ``POST /claims``.

    ``amountRupees`` accepts string, int, or Decimal inputs and is
    converted to integer paise by :meth:`to_paise`.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    claimType: ClaimType
    claimDate: date
    amountRupees: Any          # coerced and validated in the field validator
    status: ClaimStatus
    deficiencyRaisedDate: Optional[date] = None
    notes: Optional[str] = None

    # ── validators ────────────────────────────────────────────

    @field_validator("amountRupees", mode="before")
    @classmethod
    def validate_amount(cls, v: Any) -> Decimal:
        """Parse and range-check the rupee amount."""
        try:
            amount = Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("AMOUNT_INVALID")

        if amount <= 0:
            raise ValueError("AMOUNT_NON_POSITIVE")
        if amount > Decimal("10000000"):
            raise ValueError("AMOUNT_EXCEEDS_LIMIT")

        return amount

    @field_validator("claimDate", mode="after")
    @classmethod
    def validate_claim_date(cls, v: date) -> date:
        """Reject claim dates in the future (IST)."""
        if v > today_ist():
            raise ValueError("CLAIM_DATE_IN_FUTURE")
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def validate_notes(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        text = str(v).strip()
        if not text:
            return None
        if len(text) > 2000:
            raise ValueError("NOTES_TOO_LONG")
        return text

    # ── money helper ──────────────────────────────────────────

    def to_paise(self) -> int:
        """
        Convert ``amountRupees`` to integer paise.

        Uses :class:`decimal.Decimal` arithmetic throughout — never float —
        so ₹4,80,000 → exactly 48,000,000 paise, guaranteed.
        """
        return int(self.amountRupees * 100)


# ─────────────────────────────────────────────────────────────
# Error mapper
# ─────────────────────────────────────────────────────────────

# Maps Pydantic enum-validation field names to EPF error codes.
_ENUM_CODE_MAP: dict[str, str] = {
    "claimType": "INVALID_CLAIM_TYPE",
    "status": "INVALID_STATUS",
}


def parse_pydantic_error(exc: PydanticValidationError) -> EPFValidationError:
    """
    Map the **first** Pydantic v2 validation error to an
    :class:`EPFValidationError` with a machine-readable code.

    Only the first error is surfaced so the response shape stays simple and
    deterministic (avoids leaking schema details via a large error array).
    """
    errors = exc.errors(include_url=False)
    if not errors:
        return EPFValidationError("VALIDATION_ERROR", "body", "Validation failed")

    err = errors[0]
    loc = err.get("loc", ())
    field = ".".join(str(part) for part in loc) if loc else "body"
    error_type: str = err.get("type", "")
    msg: str = err.get("msg", "Validation error")

    # ── custom ValueError("CODE") from our validators ─────────
    if error_type == "value_error":
        ctx_error = err.get("ctx", {}).get("error")
        code = str(ctx_error) if ctx_error else "VALIDATION_ERROR"
        return EPFValidationError(code, field, msg)

    # ── Pydantic enum rejection ───────────────────────────────
    if error_type == "enum":
        code = _ENUM_CODE_MAP.get(field, "INVALID_ENUM_VALUE")
        return EPFValidationError(code, field, msg)

    # ── missing required field ────────────────────────────────
    if error_type == "missing":
        return EPFValidationError("MISSING_REQUIRED_FIELD", field, f"'{field}' is required")

    # ── date parse failures ───────────────────────────────────
    if error_type in ("date_from_datetime_parsing", "date_parsing", "date_type"):
        return EPFValidationError("INVALID_DATE_FORMAT", field, msg)

    # ── fallback ──────────────────────────────────────────────
    return EPFValidationError("VALIDATION_ERROR", field, msg)
