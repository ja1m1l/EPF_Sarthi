"""
POST /claims — create a new EPF claim.

Security guarantee
------------------
``userId`` is derived **only** from the validated Cognito JWT ``sub`` claim
via ``shared.auth.get_user_id()``.  It is never read from the request body,
path parameters, or query string.  Any attempt to supply a userId in the
body is silently ignored.

Environment variables
---------------------
CLAIMS_TABLE_NAME : str   DynamoDB table name injected by SAM.
STAGE             : str   Deployment stage (dev / staging / prod).
"""

from __future__ import annotations

import json
import os

import boto3
from pydantic import ValidationError as PydanticValidationError

from shared.auth import AuthError, get_user_id
from shared.http import bad_request, created, internal_error
from shared.logging import get_logger, set_correlation_id
from shared.models import Claim
from shared.validation import CreateClaimRequest, parse_pydantic_error

log = get_logger(__name__)

CLAIMS_TABLE_NAME: str = os.environ["CLAIMS_TABLE_NAME"]
STAGE: str = os.environ.get("STAGE", "dev")

# Module-level client — shared across warm invocations.
_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(CLAIMS_TABLE_NAME)


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for POST /claims."""

    # ── correlation ID ────────────────────────────────────────
    request_id: str = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    set_correlation_id(request_id)

    log.info("claim.create.start", stage=STAGE)

    # ── auth — userId comes ONLY from JWT sub ─────────────────
    try:
        user_id = get_user_id(event)
    except AuthError as exc:
        log.warning("claim.create.auth_error", reason=str(exc))
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(
                {"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid authorization token"}}
            ),
        }

    # ── parse body ────────────────────────────────────────────
    try:
        raw_body: dict = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return bad_request("INVALID_REQUEST_BODY", "body", "Request body is not valid JSON")

    # ── validate ──────────────────────────────────────────────
    try:
        req = CreateClaimRequest.model_validate(raw_body)
    except PydanticValidationError as exc:
        err = parse_pydantic_error(exc)
        log.info("claim.create.validation_failed", code=err.code, field=err.field)
        return bad_request(err.code, err.field, err.message)

    # ── build domain object ───────────────────────────────────
    claim = Claim.new(
        userId=user_id,  # ← only source of userId
        claimType=req.claimType,
        claimDateIso=req.claimDate.isoformat(),
        amountPaise=req.to_paise(),
        status=req.status,
        deficiencyRaisedDateIso=(
            req.deficiencyRaisedDate.isoformat() if req.deficiencyRaisedDate else None
        ),
        notes=req.notes,
    )

    # ── persist ───────────────────────────────────────────────
    try:
        _table.put_item(Item=claim.to_dict())
    except Exception:
        log.error("claim.create.dynamo_error", exc_info=True, userId=user_id)
        return internal_error()

    log.info(
        "claim.create.succeeded",
        claimId=claim.claimId,
        userId=user_id,
        amountPaise=claim.amountPaise,
        stage=STAGE,
    )

    return created(claim.to_dict())
