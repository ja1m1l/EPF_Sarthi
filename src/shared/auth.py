"""
Authentication helpers for EPF Sentinel.

Design rule
-----------
``get_user_id`` is the **only** function in the codebase that produces a
``userId``.  Every Lambda handler must call it; the value must never be
read from the request body, path parameters, or query string.

The Cognito JWT authorizer on API Gateway validates the token signature
before the Lambda is invoked.  By the time ``handler()`` runs, the
``requestContext.authorizer.jwt.claims`` dict is already verified.
"""

from __future__ import annotations


class AuthError(Exception):
    """Raised when the JWT sub claim is absent or malformed."""


def get_user_id(event: dict) -> str:
    """
    Extract the Cognito ``sub`` claim from the API Gateway v2 JWT context.

    Parameters
    ----------
    event:
        The raw Lambda event dict forwarded by API Gateway HTTP API.

    Returns
    -------
    str
        The ``sub`` claim value — used as ``userId`` throughout the system.

    Raises
    ------
    AuthError
        If the JWT claims are absent, or ``sub`` is missing or empty.
        Callers should respond with HTTP 401 when this is raised.

    Notes
    -----
    API Gateway HTTP API (v2) stores verified JWT claims at:
        event["requestContext"]["authorizer"]["jwt"]["claims"]

    This function never inspects ``body``, ``pathParameters``, or
    ``queryStringParameters``.
    """
    try:
        claims: dict = (
            event["requestContext"]["authorizer"]["jwt"]["claims"]
        )
        if not isinstance(claims, dict):
            raise AuthError("JWT claims not present or invalid format in requestContext")
    except (KeyError, TypeError) as exc:
        raise AuthError(
            "JWT claims not present in requestContext — is the authorizer configured?"
        ) from exc

    sub: str | None = claims.get("sub")
    if not sub:
        raise AuthError("JWT 'sub' claim is missing or empty")

    return sub
