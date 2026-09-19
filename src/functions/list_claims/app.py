"""
GET /claims — list all claims belonging to the authenticated user.

Security guarantee
------------------
``userId`` is derived **only** from the Cognito JWT ``sub`` via
``shared.auth.get_user_id()``.  The DynamoDB query is keyed on that
``userId``, so results are structurally scoped to the caller — no
cross-tenant filtering step is required.

Environment variables
---------------------
CLAIMS_TABLE_NAME : str   DynamoDB table name injected by SAM.
STAGE             : str   Deployment stage.
"""

from __future__ import annotations

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

from shared.auth import AuthError, get_user_id
from shared.http import internal_error, ok
from shared.logging import get_logger, set_correlation_id
from shared.models import Claim

log = get_logger(__name__)

CLAIMS_TABLE_NAME: str = os.environ["CLAIMS_TABLE_NAME"]
STAGE: str = os.environ.get("STAGE", "dev")

_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(CLAIMS_TABLE_NAME)


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for GET /claims."""

    request_id: str = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    set_correlation_id(request_id)

    log.info("claim.list.start", stage=STAGE)

    # ── auth — userId comes ONLY from JWT sub ─────────────────
    try:
        user_id = get_user_id(event)
    except AuthError as exc:
        log.warning("claim.list.auth_error", reason=str(exc))
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(
                {"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid authorization token"}}
            ),
        }

    # ── query DynamoDB ────────────────────────────────────────
    try:
        response = _table.query(
            KeyConditionExpression=Key("userId").eq(user_id)
        )
        items = response.get("Items", [])

        # Handle pagination — collect all pages
        while "LastEvaluatedKey" in response:
            response = _table.query(
                KeyConditionExpression=Key("userId").eq(user_id),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

    except Exception:
        log.error("claim.list.dynamo_error", exc_info=True, userId=user_id)
        return internal_error()

    # Deserialise via the domain model to normalise Decimal → int etc.
    # Skip a corrupt row instead of failing the whole list (empty vs 500).
    claims = []
    for item in items:
        try:
            claims.append(Claim.from_dict(item).to_dict())
        except Exception:
            log.error(
                "claim.list.item_skipped",
                exc_info=True,
                userId=user_id,
                claimId=item.get("claimId"),
            )

    claims.sort(key=lambda claim: claim.get("createdAt") or "", reverse=True)

    log.info("claim.list.succeeded", count=len(claims), userId=user_id)

    return ok({"claims": claims, "count": len(claims)})
