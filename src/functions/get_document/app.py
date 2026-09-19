"""
GetDocument Lambda — GET /claims/{claimId}/documents/{documentId}

Returns the Document record from DynamoDB.

Tenant-scope: verifies that the claim belongs to the authenticated user
before returning any document data (no existence leak).

extractedText is omitted from the response by default (it can be large).
Pass ?includeText=true to receive it.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.http import internal_error, not_found, ok, _response
from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
DOCUMENTS_TABLE: str = os.environ.get("DOCUMENTS_TABLE_NAME", f"epf-sentinel-Documents-{STAGE}")


def _get_user_id(event: dict) -> str:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "")
    )


def _unmarshal(item: dict) -> dict:
    """Convert DynamoDB attribute map to plain Python dict."""
    result = {}
    for key, val in item.items():
        if "S" in val:
            result[key] = val["S"]
        elif "N" in val:
            n = val["N"]
            result[key] = int(n) if "." not in n else float(n)
        elif "BOOL" in val:
            result[key] = val["BOOL"]
        elif "NULL" in val:
            result[key] = None
        else:
            result[key] = val
    return result


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point — GET /claims/{claimId}/documents/{documentId}"""
    request_id = getattr(context, "aws_request_id", "")
    set_correlation_id(request_id)

    log.info("get_document.request.received", stage=STAGE)

    user_id = _get_user_id(event)
    if not user_id:
        return _response(401, {"error": "Unauthorized"})

    path_params = event.get("pathParameters") or {}
    claim_id: str = path_params.get("claimId", "")
    document_id: str = path_params.get("documentId", "")

    if not claim_id or not document_id:
        return _response(400, {"error": "Missing claimId or documentId path parameters"})

    include_text: bool = (
        (event.get("queryStringParameters") or {}).get("includeText", "").lower() == "true"
    )

    # ── Tenant-scope: verify claim ownership ─────────────────────────────────
    ddb = boto3.client("dynamodb")
    try:
        claim_resp = ddb.get_item(
            TableName=CLAIMS_TABLE,
            Key={
                "userId":  {"S": user_id},
                "claimId": {"S": claim_id},
            },
            ProjectionExpression="claimId",
        )
        if "Item" not in claim_resp:
            return not_found()
    except (BotoCoreError, ClientError) as exc:
        log.error("get_document.claims.fetch_failed", claimId=claim_id, error=str(exc))
        return internal_error()

    # ── Fetch Document ───────────────────────────────────────────────────────
    try:
        doc_resp = ddb.get_item(
            TableName=DOCUMENTS_TABLE,
            Key={
                "userId":     {"S": user_id},
                "documentId": {"S": document_id},
            },
        )
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "get_document.dynamo.fetch_failed",
            documentId=document_id,
            error=str(exc),
        )
        return internal_error()

    if "Item" not in doc_resp:
        return not_found()

    doc = _unmarshal(doc_resp["Item"])

    # Verify the document belongs to the requested claim (extra safety check)
    if doc.get("claimId") != claim_id:
        log.warning(
            "get_document.claim_mismatch",
            userId=user_id,
            documentId=document_id,
            documentClaimId=doc.get("claimId"),
            requestedClaimId=claim_id,
        )
        return not_found()

    if not include_text:
        doc.pop("extractedText", None)

    log.info(
        "get_document.completed",
        documentId=document_id,
        status=doc.get("status"),
    )

    return ok(doc)
