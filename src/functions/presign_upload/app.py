"""
PresignUpload Lambda — POST /claims/{claimId}/documents

Generates a presigned S3 POST URL so the client can upload a document
directly to S3 without routing the bytes through API Gateway.

Security model
--------------
* S3-enforced Conditions in the presigned POST:
    - content-length-range  1 .. 10_485_760  (10 MB hard cap)
    - eq $Content-Type <allowlisted mime type>
  These conditions are checked by S3, not by the client — they cannot be
  bypassed by a UI bug or a raw HTTP request.
* Only image/png, image/jpeg, application/pdf are accepted.
* The S3 key embeds {userId}/{claimId}/{documentId}.{ext} so tenancy is
  verifiable from the key alone.

Contract
--------
Request body (JSON):
    { "contentType": "application/pdf" }

Response 200:
    {
        "documentId": "<uuid>",
        "url":        "<presigned POST URL>",
        "fields":     { <form fields the client must include before the file> },
        "expiresIn":  300
    }

Errors:
    400 — missing/invalid contentType
    404 — claim not found or belongs to another user
    500 — S3 presign failure or DynamoDB write failure
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.http import bad_request, internal_error, not_found, _response
from shared.logging import get_logger, set_correlation_id
from shared.models import Document, DocumentStatus, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
DOCUMENTS_TABLE: str = os.environ.get("DOCUMENTS_TABLE_NAME", f"epf-sentinel-Documents-{STAGE}")
DOCS_BUCKET: str = os.environ.get("DOCS_BUCKET_NAME", f"epf-sentinel-docs-{STAGE}")
PRESIGN_EXPIRY_SECONDS: int = 300       # 5 minutes
TTL_30_DAYS: int = 30 * 24 * 60 * 60

# Strict allow-list — reject everything else at the Lambda level
_ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/png":       "png",
    "image/jpeg":      "jpg",
    "application/pdf": "pdf",
}


def _get_user_id(event: dict) -> str:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "")
    )


def _claim_owned_by(user_id: str, claim_id: str) -> bool:
    """Return True if the claim exists in DynamoDB and belongs to user_id."""
    ddb = boto3.client("dynamodb")
    resp = ddb.get_item(
        TableName=CLAIMS_TABLE,
        Key={
            "userId":  {"S": user_id},
            "claimId": {"S": claim_id},
        },
        ProjectionExpression="claimId",
    )
    return "Item" in resp


def _save_processing_stub(doc: Document) -> None:
    """Write the initial PROCESSING stub to the Documents table."""
    ddb = boto3.client("dynamodb")
    item: dict[str, Any] = {
        "userId":      {"S": doc.userId},
        "documentId":  {"S": doc.documentId},
        "claimId":     {"S": doc.claimId},
        "s3Key":       {"S": doc.s3Key},
        "contentType": {"S": doc.contentType},
        "status":      {"S": doc.status},
        "expiresAt":   {"N": str(doc.expiresAt)},
    }
    ddb.put_item(TableName=DOCUMENTS_TABLE, Item=item)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point — POST /claims/{claimId}/documents"""
    request_id = getattr(context, "aws_request_id", "")
    set_correlation_id(request_id)

    log.info("presign_upload.request.received", stage=STAGE)

    user_id = _get_user_id(event)
    if not user_id:
        return _response(401, {"error": "Unauthorized"})

    claim_id: str = (event.get("pathParameters") or {}).get("claimId", "")
    if not claim_id:
        return bad_request("MISSING_PATH_PARAM", "claimId", "claimId is required")

    # ── Parse and validate contentType ──────────────────────────────────────
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return bad_request("INVALID_JSON", "body", "Request body must be valid JSON")

    content_type: str = body.get("contentType", "").strip()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        return bad_request(
            "INVALID_CONTENT_TYPE",
            "contentType",
            f"Allowed types: {', '.join(_ALLOWED_CONTENT_TYPES)}",
        )

    ext = _ALLOWED_CONTENT_TYPES[content_type]

    # ── Tenant-scope claim ───────────────────────────────────────────────────
    try:
        if not _claim_owned_by(user_id, claim_id):
            return not_found()
    except (BotoCoreError, ClientError) as exc:
        log.error("presign_upload.claims.fetch_failed", claimId=claim_id, error=str(exc))
        return internal_error()

    # ── Generate presigned POST ──────────────────────────────────────────────
    document_id = str(uuid.uuid4())
    s3_key = f"{user_id}/{claim_id}/{document_id}.{ext}"

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    try:
        presigned_post = s3.generate_presigned_post(
            Bucket=DOCS_BUCKET,
            Key=s3_key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},          # exact content-type
                ["content-length-range", 1, 10_485_760], # 1 byte – 10 MB
            ],
            ExpiresIn=PRESIGN_EXPIRY_SECONDS,
        )
        presigned_put = s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": DOCS_BUCKET,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=PRESIGN_EXPIRY_SECONDS,
        )
    except (BotoCoreError, ClientError) as exc:
        log.error("presign_upload.presign.failed", claimId=claim_id, error=str(exc))
        return internal_error()

    # ── Persist PROCESSING stub ──────────────────────────────────────────────
    doc = Document.new_processing(
        userId=user_id,
        documentId=document_id,
        claimId=claim_id,
        s3Key=s3_key,
        contentType=content_type,
        expiresAt=int(time.time()) + TTL_30_DAYS,
    )

    try:
        _save_processing_stub(doc)
    except (BotoCoreError, ClientError) as exc:
        log.error("presign_upload.dynamo.save_failed", documentId=document_id, error=str(exc))
        return internal_error()

    log.info(
        "presign_upload.completed",
        userId=user_id,
        claimId=claim_id,
        documentId=document_id,
        s3Key=s3_key,
        contentType=content_type,
    )

    return _response(200, {
        "documentId": document_id,
        "url":        presigned_put,
        "uploadUrl":  presigned_put,
        "postUrl":    presigned_post["url"],
        "fields":     presigned_post["fields"],
        "expiresIn":  PRESIGN_EXPIRY_SECONDS,
    })
