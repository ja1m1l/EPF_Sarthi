"""
Integration test fixtures for Module 1.2.

Creates two real Cognito users (User A and User B) at session start,
authenticates them to obtain JWT tokens, and tears them down at session end.

Required environment variables
-------------------------------
API_ENDPOINT          Base URL of the deployed HTTP API (no trailing slash).
                      Example: https://abc123.execute-api.ap-south-1.amazonaws.com/dev
COGNITO_USER_POOL_ID  The Cognito User Pool ID.
COGNITO_CLIENT_ID     The Cognito App Client ID.
AWS_DEFAULT_REGION    (standard boto3 env var; defaults to ap-south-1 if absent)

The Cognito admin APIs used here (AdminCreateUser, AdminSetUserPassword,
AdminDeleteUser) require IAM permissions on the user pool.  The credentials
used must have these permissions (typically the developer's IAM role).

Usage
-----
    # Export stack outputs first:
    export API_ENDPOINT=$(aws cloudformation describe-stacks \\
        --stack-name epf-sentinel-dev \\
        --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \\
        --output text)
    export COGNITO_USER_POOL_ID=$(aws cloudformation describe-stacks \\
        --stack-name epf-sentinel-dev \\
        --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue" \\
        --output text)
    export COGNITO_CLIENT_ID=$(aws cloudformation describe-stacks \\
        --stack-name epf-sentinel-dev \\
        --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolClientId'].OutputValue" \\
        --output text)

    python -m pytest tests/integration/test_claims_api.py -v
"""

from __future__ import annotations

import os
import secrets
import string
import uuid

import boto3
import pytest
import requests


# ─────────────────────────────────────────────────────────────
# Config from environment
# ─────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        pytest.skip(f"Integration test skipped: env var {name!r} is not set")
    return val


# ─────────────────────────────────────────────────────────────
# Password generator
# ─────────────────────────────────────────────────────────────

def _strong_password() -> str:
    """Generate a password meeting the pool policy: 8+ chars, upper, lower, digit."""
    alphabet = string.ascii_letters + string.digits
    base = secrets.token_urlsafe(12)
    # Guarantee at least one of each required class
    password = (
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + base
    )
    return password


# ─────────────────────────────────────────────────────────────
# Cognito helpers
# ─────────────────────────────────────────────────────────────

class CognitoTestUser:
    """
    A Cognito user created for integration testing.

    Created via AdminCreateUser + AdminSetUserPassword so the user starts
    in CONFIRMED state without requiring a real email inbox.
    """

    def __init__(self, pool_id: str, client_id: str, region: str) -> None:
        self.pool_id = pool_id
        self.client_id = client_id
        self.region = region
        self.email = f"epf-test-{uuid.uuid4().hex[:8]}@example-integration.test"
        self.password = _strong_password()
        self.access_token: str = ""
        self._idp = boto3.client("cognito-idp", region_name=region)

    def create(self) -> None:
        """Create and confirm the user in Cognito."""
        self._idp.admin_create_user(
            UserPoolId=self.pool_id,
            Username=self.email,
            TemporaryPassword=self.password,
            MessageAction="SUPPRESS",  # do not send a real welcome email
            UserAttributes=[
                {"Name": "email", "Value": self.email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        # Set a permanent password so the user doesn't need to change it on first login
        self._idp.admin_set_user_password(
            UserPoolId=self.pool_id,
            Username=self.email,
            Password=self.password,
            Permanent=True,
        )

    def authenticate(self) -> None:
        """Exchange credentials for tokens; stores access_token."""
        resp = self._idp.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": self.email,
                "PASSWORD": self.password,
            },
            ClientId=self.client_id,
        )
        self.access_token = resp["AuthenticationResult"]["AccessToken"]

    def delete(self) -> None:
        """Remove the user from Cognito (best-effort, no exception on failure)."""
        try:
            self._idp.admin_delete_user(
                UserPoolId=self.pool_id,
                Username=self.email,
            )
        except Exception:
            pass  # Don't fail teardown if the user was already removed


# ─────────────────────────────────────────────────────────────
# Session-scoped fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_endpoint() -> str:
    return _require_env("API_ENDPOINT").rstrip("/")


@pytest.fixture(scope="session")
def cognito_pool_id() -> str:
    return _require_env("COGNITO_USER_POOL_ID")


@pytest.fixture(scope="session")
def cognito_client_id() -> str:
    return _require_env("COGNITO_CLIENT_ID")


@pytest.fixture(scope="session")
def aws_region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")


@pytest.fixture(scope="session")
def user_a(cognito_pool_id, cognito_client_id, aws_region) -> CognitoTestUser:
    """Real Cognito User A — created fresh for this test session."""
    user = CognitoTestUser(cognito_pool_id, cognito_client_id, aws_region)
    user.create()
    user.authenticate()
    yield user
    user.delete()


@pytest.fixture(scope="session")
def user_b(cognito_pool_id, cognito_client_id, aws_region) -> CognitoTestUser:
    """Real Cognito User B — distinct from User A."""
    user = CognitoTestUser(cognito_pool_id, cognito_client_id, aws_region)
    user.create()
    user.authenticate()
    yield user
    user.delete()


# ─────────────────────────────────────────────────────────────
# Request helper
# ─────────────────────────────────────────────────────────────

def _auth_headers(user: CognitoTestUser) -> dict:
    return {"Authorization": f"Bearer {user.access_token}"}
