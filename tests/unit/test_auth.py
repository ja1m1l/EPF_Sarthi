"""
Unit tests for shared.auth — get_user_id() and AuthError.

These run without any AWS dependencies.
"""

from __future__ import annotations

import pytest

from shared.auth import AuthError, get_user_id


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_event(sub: str | None = "test-user-sub-1234") -> dict:
    """Return a minimal API Gateway v2 event with a JWT authorizer context."""
    event: dict = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {}
                }
            },
            "requestId": "test-request-id",
        }
    }
    if sub is not None:
        event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = sub
    return event


# ─────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────

def test_get_user_id_returns_sub_from_jwt():
    """get_user_id extracts the sub claim correctly."""
    event = _make_event(sub="cognito-sub-abc-123")
    user_id = get_user_id(event)
    assert user_id == "cognito-sub-abc-123"


def test_get_user_id_returns_exact_sub_value():
    """get_user_id does not modify the sub value in any way."""
    sub = "  user-with-spaces-would-be-weird-but-passthrough  "
    event = _make_event(sub=sub)
    assert get_user_id(event) == sub


# ─────────────────────────────────────────────────────────────
# Failure cases — all must raise AuthError
# ─────────────────────────────────────────────────────────────

def test_get_user_id_raises_when_sub_is_missing():
    """AuthError is raised when the sub claim is absent."""
    event = _make_event(sub=None)  # sub key not set
    with pytest.raises(AuthError):
        get_user_id(event)


def test_get_user_id_raises_when_sub_is_empty_string():
    """AuthError is raised when sub is present but empty."""
    event = _make_event(sub="")
    with pytest.raises(AuthError):
        get_user_id(event)


def test_get_user_id_raises_when_authorizer_missing():
    """AuthError is raised when requestContext.authorizer is absent."""
    event = {"requestContext": {"requestId": "r1"}}
    with pytest.raises(AuthError):
        get_user_id(event)


def test_get_user_id_raises_when_jwt_key_missing():
    """AuthError is raised when the jwt sub-dict is absent."""
    event = {
        "requestContext": {
            "authorizer": {}  # no 'jwt' key
        }
    }
    with pytest.raises(AuthError):
        get_user_id(event)


def test_get_user_id_raises_when_request_context_missing():
    """AuthError is raised when the entire requestContext is absent."""
    event = {"body": "{}"}
    with pytest.raises(AuthError):
        get_user_id(event)


def test_get_user_id_raises_when_authorizer_is_none():
    """AuthError is raised when authorizer value is None."""
    event = {"requestContext": {"authorizer": None}}
    with pytest.raises(AuthError):
        get_user_id(event)


def test_get_user_id_raises_when_claims_is_none():
    """AuthError is raised when jwt.claims is None."""
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": None
                }
            }
        }
    }
    with pytest.raises(AuthError):
        get_user_id(event)


# ─────────────────────────────────────────────────────────────
# Strict isolation — userId must never come from body/path/query
# ─────────────────────────────────────────────────────────────

def test_get_user_id_ignores_body_userId():
    """
    A userId in the request body must have no effect on the returned value.

    This test proves the function signature: it only accepts an event dict
    and returns the JWT sub claim — it has no mechanism to read the body.
    """
    event = _make_event(sub="correct-sub")
    # Add a deceptive body field — must be silently ignored
    event["body"] = '{"userId": "attacker-supplied-id"}'
    assert get_user_id(event) == "correct-sub"


def test_get_user_id_ignores_path_userId():
    """A userId in pathParameters must have no effect."""
    event = _make_event(sub="correct-sub")
    event["pathParameters"] = {"userId": "attacker-supplied-id"}
    assert get_user_id(event) == "correct-sub"


def test_get_user_id_ignores_query_userId():
    """A userId in queryStringParameters must have no effect."""
    event = _make_event(sub="correct-sub")
    event["queryStringParameters"] = {"userId": "attacker-supplied-id"}
    assert get_user_id(event) == "correct-sub"
