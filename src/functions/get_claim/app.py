"""
GET /claims/{claimId} — fetch a single claim for the authenticated user.

Security guarantee
------------------
``userId`` is derived **only** from the Cognito JWT ``sub`` via
``shared.auth.get_user_id()``.  The DynamoDB ``get_item`` call uses BOTH
``userId`` (from JWT) and ``claimId`` (from path) as the composite key.
If the claimId exists but belongs to another user, DynamoDB returns
nothing — the response is an opaque 404.  The caller cannot distinguish
"does not exist" from "belongs to someone else".

Environment variables
---------------------
CLAIMS_TABLE_NAME : str   DynamoDB table name injected by SAM.
STAGE             : str   Deployment stage.
"""

from __future__ import annotations

import json
import os

import boto3

from shared.auth import AuthError, get_user_id
from shared.http import internal_error, not_found, ok
from shared.logging import get_logger, set_correlation_id
from shared.models import Claim

log = get_logger(__name__)

CLAIMS_TABLE_NAME: str = os.environ["CLAIMS_TABLE_NAME"]
STAGE: str = os.environ.get("STAGE", "dev")

_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(CLAIMS_TABLE_NAME)


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for GET /claims/{claimId}."""

    request_id: str = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    set_correlation_id(request_id)

    log.info("claim.get.start", stage=STAGE)

    # ── auth — userId comes ONLY from JWT sub ─────────────────
    try:
        user_id = get_user_id(event)
    except AuthError as exc:
        log.warning("claim.get.auth_error", reason=str(exc))
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(
                {"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid authorization token"}}
            ),
        }

    # ── path parameter ────────────────────────────────────────
    claim_id: str | None = (
        event.get("pathParameters") or {}
    ).get("claimId")

    if not claim_id:
        log.warning("claim.get.missing_path_param", userId=user_id)
        return not_found()

    # ── fetch — composite key enforces tenancy ─────────────────
    # The key uses userId (from JWT) + claimId (from path).
    # If the claimId belongs to a different userId, DynamoDB returns no Item.
    # We return 404 in both cases — no existence leak.
    try:
        response = _table.get_item(
            Key={"userId": user_id, "claimId": claim_id}
        )
    except Exception:
        log.error("claim.get.dynamo_error", exc_info=True, userId=user_id, claimId=claim_id)
        return internal_error()

    item = response.get("Item")
    if not item:
        log.info("claim.get.not_found", userId=user_id, claimId=claim_id)
        return not_found()

    claim = Claim.from_dict(item)

    log.info("claim.get.succeeded", claimId=claim.claimId, userId=user_id)

    return ok(claim.to_dict())
