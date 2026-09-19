"""
Integration tests for Module 1.2 — claim CRUD endpoints.

These tests run against the **real** deployed stack.  See
tests/integration/conftest.py for setup instructions and required
environment variables.

Test inventory
--------------
test_user_a_can_create_claim        POST /claims → 201, correct body
test_user_a_can_list_claims         GET  /claims → 200, claim appears
test_user_a_can_get_claim           GET  /claims/{id} → 200
test_user_b_gets_404_for_user_a     GET  /claims/{user_a_id} as B → 404
test_cross_user_list_isolation      GET  /claims as B → does not contain A's claim
test_malformed_body_returns_400     POST /claims malformed JSON → 400, no stack trace
test_future_date_returns_400        POST /claims future date → 400 CLAIM_DATE_IN_FUTURE
test_amount_zero_returns_400        POST /claims amount=0 → 400 AMOUNT_NON_POSITIVE
test_invalid_status_returns_400     POST /claims bad status → 400 INVALID_STATUS
test_paise_round_trip               POST /claims 480000 → amountPaise == 48000000
test_unauthenticated_post           POST /claims no token → 401 or 403
"""

from __future__ import annotations

import json

import pytest
import requests

from tests.integration.conftest import CognitoTestUser, _auth_headers


# ─────────────────────────────────────────────────────────────
# Shared demo claim body
# ─────────────────────────────────────────────────────────────

_DEMO_CLAIM = {
    "claimType": "FINAL_SETTLEMENT",
    "claimDate": "2026-08-01",
    "amountRupees": "480000",
    "status": "PENDING",
}


# ─────────────────────────────────────────────────────────────
# Session-level fixture: User A creates one claim to share
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def user_a_claim(api_endpoint, user_a):
    """
    Create the demo claim as User A once per session.

    Returns the full response JSON so other tests can reuse the claimId.
    """
    resp = requests.post(
        f"{api_endpoint}/claims",
        json=_DEMO_CLAIM,
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 201, (
        f"Session fixture: expected 201 creating demo claim, got {resp.status_code}: {resp.text}"
    )
    return resp.json()


# ─────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────

def test_user_a_can_create_claim(api_endpoint, user_a, user_a_claim):
    """
    POST /claims → 201.
    The response body must contain the claim fields.
    """
    body = user_a_claim
    assert "claimId" in body, f"Response missing claimId: {body}"
    assert body["claimType"] == "FINAL_SETTLEMENT"
    assert body["status"] == "PENDING"
    assert body["claimDateIso"] == "2026-08-01"


# ─────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────

def test_user_a_can_list_claims(api_endpoint, user_a, user_a_claim):
    """
    GET /claims → 200. User A's claim appears in the list.
    """
    resp = requests.get(
        f"{api_endpoint}/claims",
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    claim_ids = [c["claimId"] for c in body.get("claims", [])]
    assert user_a_claim["claimId"] in claim_ids, (
        f"User A's claimId {user_a_claim['claimId']!r} not in list: {claim_ids}"
    )


# ─────────────────────────────────────────────────────────────
# GET single
# ─────────────────────────────────────────────────────────────

def test_user_a_can_get_own_claim(api_endpoint, user_a, user_a_claim):
    """
    GET /claims/{claimId} → 200 when fetching own claim.
    """
    claim_id = user_a_claim["claimId"]
    resp = requests.get(
        f"{api_endpoint}/claims/{claim_id}",
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["claimId"] == claim_id


# ─────────────────────────────────────────────────────────────
# Cross-tenant isolation — the critical security test
# ─────────────────────────────────────────────────────────────

def test_user_b_gets_404_for_user_a_claim(api_endpoint, user_a_claim, user_b):
    """
    GET /claims/{user_a_claimId} as User B → 404, not 403.

    404 is required (not 403) so we do not leak whether the claim exists.
    """
    claim_id = user_a_claim["claimId"]
    resp = requests.get(
        f"{api_endpoint}/claims/{claim_id}",
        headers=_auth_headers(user_b),
        timeout=10,
    )
    # Must be 404, NOT 403 — no existence leak
    assert resp.status_code == 404, (
        f"Expected 404 for cross-tenant access, got {resp.status_code}: {resp.text}"
    )
    # Must not be 403
    assert resp.status_code != 403, "Got 403 — this leaks the existence of the resource"


def test_cross_user_list_isolation(api_endpoint, user_a_claim, user_b):
    """
    GET /claims as User B must not contain User A's claim.
    """
    resp = requests.get(
        f"{api_endpoint}/claims",
        headers=_auth_headers(user_b),
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    claim_ids = [c["claimId"] for c in body.get("claims", [])]
    assert user_a_claim["claimId"] not in claim_ids, (
        f"User B's claim list contains User A's claimId — cross-tenant leak!"
    )


# ─────────────────────────────────────────────────────────────
# Validation rejections
# ─────────────────────────────────────────────────────────────

def test_malformed_body_returns_400_without_stack_trace(api_endpoint, user_a):
    """
    POST /claims with non-JSON body → 400.
    Response must not contain a stack trace or table name.
    """
    resp = requests.post(
        f"{api_endpoint}/claims",
        data="this is not json",  # raw non-JSON string
        headers={**_auth_headers(user_a), "Content-Type": "application/json"},
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    text = resp.text.lower()
    assert "traceback" not in text, "Stack trace leaked in 400 response body"
    assert "dynamodb" not in text, "Table name leaked in 400 response body"
    assert "exception" not in text, "Exception class leaked in 400 response body"

    body = resp.json()
    assert "error" in body, "Response body missing 'error' key"
    assert "code" in body["error"], "Error missing 'code' field"


def test_future_claim_date_returns_400(api_endpoint, user_a):
    """POST /claims with a future claimDate → 400 CLAIM_DATE_IN_FUTURE."""
    from datetime import date, timedelta
    future = (date.today() + timedelta(days=30)).isoformat()

    resp = requests.post(
        f"{api_endpoint}/claims",
        json={**_DEMO_CLAIM, "claimDate": future},
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "CLAIM_DATE_IN_FUTURE", (
        f"Expected code CLAIM_DATE_IN_FUTURE, got: {body}"
    )
    assert body["error"]["field"] == "claimDate"


def test_zero_amount_returns_400(api_endpoint, user_a):
    """POST /claims with amount=0 → 400 AMOUNT_NON_POSITIVE."""
    resp = requests.post(
        f"{api_endpoint}/claims",
        json={**_DEMO_CLAIM, "amountRupees": 0},
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "AMOUNT_NON_POSITIVE"
    assert body["error"]["field"] == "amountRupees"


def test_invalid_status_returns_400(api_endpoint, user_a):
    """POST /claims with an unrecognised status → 400 INVALID_STATUS."""
    resp = requests.post(
        f"{api_endpoint}/claims",
        json={**_DEMO_CLAIM, "status": "APPROVED"},
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "INVALID_STATUS"
    assert body["error"]["field"] == "status"


# ─────────────────────────────────────────────────────────────
# Paise round-trip — the critical money test (integration)
# ─────────────────────────────────────────────────────────────

def test_paise_round_trip_480000_rupees(api_endpoint, user_a):
    """
    POST /claims with amountRupees=480000 → persisted amountPaise == 48000000.

    The expected value 48000000 is a hard-coded literal from the spec.
    """
    resp = requests.post(
        f"{api_endpoint}/claims",
        json={**_DEMO_CLAIM, "amountRupees": "480000"},
        headers=_auth_headers(user_a),
        timeout=10,
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()

    amount_paise = body.get("amountPaise")
    assert amount_paise == 48_000_000, (
        f"Expected amountPaise=48000000, got {amount_paise!r}. "
        "Money must never be stored as float."
    )
    assert isinstance(amount_paise, int), (
        f"amountPaise must be an integer in the response, got {type(amount_paise).__name__}"
    )


# ─────────────────────────────────────────────────────────────
# Unauthenticated access
# ─────────────────────────────────────────────────────────────

def test_unauthenticated_post_claims_is_rejected(api_endpoint):
    """POST /claims without a token → 401 or 403 (API Gateway rejects before Lambda)."""
    resp = requests.post(
        f"{api_endpoint}/claims",
        json=_DEMO_CLAIM,
        timeout=10,
        # No Authorization header
    )
    assert resp.status_code in (401, 403), (
        f"Expected 401/403 for unauthenticated request, got {resp.status_code}: {resp.text}"
    )


def test_unauthenticated_get_claims_is_rejected(api_endpoint):
    """GET /claims without a token → 401 or 403."""
    resp = requests.get(f"{api_endpoint}/claims", timeout=10)
    assert resp.status_code in (401, 403), (
        f"Expected 401/403 for unauthenticated request, got {resp.status_code}: {resp.text}"
    )
