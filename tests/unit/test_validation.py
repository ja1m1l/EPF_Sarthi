"""
Unit tests for shared.validation — CreateClaimRequest and parse_pydantic_error.

Every test case is in a parametrized table with one assertion per row.
The ₹4,80,000 paise round-trip is an explicit literal test (not derived
from the same arithmetic path being tested).
"""

from __future__ import annotations

import pytest
from datetime import date, timedelta

from pydantic import ValidationError as PydanticValidationError

from shared.validation import CreateClaimRequest, EPFValidationError, parse_pydantic_error


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _try_parse(body: dict) -> tuple[CreateClaimRequest | None, EPFValidationError | None]:
    """Parse body; return (request, None) on success or (None, error) on failure."""
    try:
        req = CreateClaimRequest.model_validate(body)
        return req, None
    except PydanticValidationError as exc:
        return None, parse_pydantic_error(exc)


def _valid_body(**overrides) -> dict:
    """Return a minimally valid request body, optionally with overrides."""
    body = {
        "claimType": "FINAL_SETTLEMENT",
        "claimDate": "2026-08-01",
        "amountRupees": "480000",
        "status": "PENDING",
    }
    body.update(overrides)
    return body


# ─────────────────────────────────────────────────────────────
# Validation rejection table
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("description,body,expected_code,expected_field", [
    (
        "future claimDate is rejected",
        _valid_body(claimDate=(date.today() + timedelta(days=1)).isoformat()),
        "CLAIM_DATE_IN_FUTURE",
        "claimDate",
    ),
    (
        "amount exactly 0 is rejected",
        _valid_body(amountRupees=0),
        "AMOUNT_NON_POSITIVE",
        "amountRupees",
    ),
    (
        "negative amount is rejected",
        _valid_body(amountRupees=-1),
        "AMOUNT_NON_POSITIVE",
        "amountRupees",
    ),
    (
        "amount as string zero is rejected",
        _valid_body(amountRupees="0"),
        "AMOUNT_NON_POSITIVE",
        "amountRupees",
    ),
    (
        "amount just over 1 Cr limit is rejected",
        _valid_body(amountRupees=10_000_001),
        "AMOUNT_EXCEEDS_LIMIT",
        "amountRupees",
    ),
    (
        "amount exactly at limit (1 Cr) is accepted",
        _valid_body(amountRupees=10_000_000),
        None,
        None,
    ),
    (
        "unknown claimType is rejected",
        _valid_body(claimType="ADVANCE"),
        "INVALID_CLAIM_TYPE",
        "claimType",
    ),
    (
        "unknown status is rejected",
        _valid_body(status="APPROVED"),
        "INVALID_STATUS",
        "status",
    ),
    (
        "missing claimDate is rejected",
        {k: v for k, v in _valid_body().items() if k != "claimDate"},
        "MISSING_REQUIRED_FIELD",
        "claimDate",
    ),
    (
        "missing amountRupees is rejected",
        {k: v for k, v in _valid_body().items() if k != "amountRupees"},
        "MISSING_REQUIRED_FIELD",
        "amountRupees",
    ),
    (
        "missing claimType is rejected",
        {k: v for k, v in _valid_body().items() if k != "claimType"},
        "MISSING_REQUIRED_FIELD",
        "claimType",
    ),
    (
        "amount as string parses correctly",
        _valid_body(amountRupees="480000"),
        None,
        None,
    ),
    (
        "amount as integer parses correctly",
        _valid_body(amountRupees=480000),
        None,
        None,
    ),
    (
        "today's claimDate is accepted (not future)",
        _valid_body(claimDate=date.today().isoformat()),
        None,
        None,
    ),
    (
        "optional deficiencyRaisedDate accepted when omitted",
        _valid_body(),
        None,
        None,
    ),
    (
        "optional deficiencyRaisedDate accepted when provided",
        _valid_body(deficiencyRaisedDate="2026-08-15"),
        None,
        None,
    ),
])
def test_validation_table(description, body, expected_code, expected_field):
    req, err = _try_parse(body)

    if expected_code is None:
        # Expected success
        assert err is None, f"{description}: expected success but got error {err!r}"
        assert req is not None, f"{description}: expected a request object"
    else:
        # Expected failure
        assert err is not None, f"{description}: expected error {expected_code!r} but validation passed"
        assert err.code == expected_code, (
            f"{description}: expected code={expected_code!r}, got code={err.code!r}"
        )
        assert err.field == expected_field, (
            f"{description}: expected field={expected_field!r}, got field={err.field!r}"
        )


# ─────────────────────────────────────────────────────────────
# Paise round-trip — the critical money test
# ─────────────────────────────────────────────────────────────

def test_rupees_480000_round_trips_as_exactly_48000000_paise():
    """
    ₹4,80,000 must convert to exactly 48,000,000 paise.

    The expected value is a hard-coded literal derived from the spec, not
    computed by the same arithmetic path under test.
    """
    req, err = _try_parse(_valid_body(amountRupees="480000"))
    assert err is None, f"Validation unexpectedly failed: {err!r}"
    assert req is not None

    paise = req.to_paise()
    assert paise == 48_000_000, (
        f"Expected exactly 48000000 paise, got {paise!r}. "
        "Money must use integer Decimal arithmetic, never float."
    )
    assert isinstance(paise, int), f"paise must be int, got {type(paise).__name__}"


def test_rupees_1_round_trips_as_100_paise():
    """₹1 → 100 paise exactly."""
    req, _ = _try_parse(_valid_body(amountRupees="1"))
    assert req is not None
    assert req.to_paise() == 100


def test_rupees_10_000_000_round_trips_as_1_000_000_000_paise():
    """Upper limit ₹1,00,00,000 → 1,000,000,000 paise exactly."""
    req, _ = _try_parse(_valid_body(amountRupees="10000000"))
    assert req is not None
    assert req.to_paise() == 1_000_000_000


# ─────────────────────────────────────────────────────────────
# Error mapper edge cases
# ─────────────────────────────────────────────────────────────

def test_parse_pydantic_error_on_completely_empty_body():
    """An empty body reports the first missing required field."""
    _, err = _try_parse({})
    assert err is not None
    assert err.code == "MISSING_REQUIRED_FIELD"


def test_non_parseable_date_produces_date_format_error():
    """A malformed date string produces INVALID_DATE_FORMAT, not a crash."""
    _, err = _try_parse(_valid_body(claimDate="not-a-date"))
    assert err is not None
    assert err.code == "INVALID_DATE_FORMAT"
    assert err.field == "claimDate"


def test_non_parseable_amount_produces_amount_invalid():
    """A non-numeric amount produces AMOUNT_INVALID."""
    _, err = _try_parse(_valid_body(amountRupees="fifty-thousand"))
    assert err is not None
    assert err.code == "AMOUNT_INVALID"
