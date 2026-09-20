"""
DELETE /account — erase the authenticated user's stored application data.

Security
--------
``userId`` is taken only from the Cognito JWT ``sub``. The handler never
accepts a user id from the body or path. It does not delete the Cognito
login itself; the frontend calls Cognito ``deleteUser`` after this succeeds.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from shared.auth import AuthError, get_user_id
from shared.http import internal_error, ok
from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")


def _table_name(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _query_all(table, key_condition) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": key_condition}
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items


def _delete_s3_prefix(s3, *, bucket: str, prefix: str) -> int:
    deleted = 0
    token: str | None = ""
    while token is not None:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        listing = s3.list_objects_v2(**kwargs)
        objects = [{"Key": obj["Key"]} for obj in listing.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            deleted += len(objects)
        token = listing.get("NextContinuationToken")
    return deleted


def erase_user_data(
    user_id: str,
    *,
    claims_table,
    documents_table,
    runs_table,
    transitions_table,
    quota_table,
    s3,
    bucket: str,
) -> dict[str, int]:
    """Delete DynamoDB rows and S3 objects owned by ``user_id``."""
    claims = _query_all(claims_table, Key("userId").eq(user_id))
    documents = _query_all(documents_table, Key("userId").eq(user_id))
    quota = _query_all(quota_table, Key("userId").eq(user_id))

    objects_removed = _delete_s3_prefix(s3, bucket=bucket, prefix=f"{user_id}/")

    runs_removed = 0
    transitions_removed = 0
    for claim in claims:
        claim_id = claim["claimId"]
        runs = _query_all(runs_table, Key("claimId").eq(claim_id))
        for run in runs:
            runs_table.delete_item(Key={"claimId": claim_id, "runId": run["runId"]})
            runs_removed += 1
        transitions = _query_all(transitions_table, Key("claimId").eq(claim_id))
        for row in transitions:
            transitions_table.delete_item(
                Key={"claimId": claim_id, "transitionKey": row["transitionKey"]}
            )
            transitions_removed += 1
        claims_table.delete_item(Key={"userId": user_id, "claimId": claim_id})

    for document in documents:
        documents_table.delete_item(
            Key={"userId": user_id, "documentId": document["documentId"]}
        )

    for row in quota:
        quota_table.delete_item(Key={"userId": user_id, "dayIso": row["dayIso"]})

    return {
        "claimsRemoved": len(claims),
        "documentsRemoved": len(documents),
        "runsRemoved": runs_removed,
        "objectsRemoved": objects_removed,
    }


def handler(event: dict, context: object) -> dict:
    request_id: str = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    set_correlation_id(request_id)

    try:
        user_id = get_user_id(event)
    except AuthError as exc:
        log.warning("account.delete.auth_error", reason=str(exc))
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(
                {"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid authorization token"}}
            ),
        }

    dynamodb = boto3.resource("dynamodb")
    s3 = boto3.client("s3")
    try:
        counts = erase_user_data(
            user_id,
            claims_table=dynamodb.Table(_table_name("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")),
            documents_table=dynamodb.Table(
                _table_name("DOCUMENTS_TABLE_NAME", f"epf-sentinel-Documents-{STAGE}")
            ),
            runs_table=dynamodb.Table(
                _table_name("ANALYSIS_RUNS_TABLE_NAME", f"epf-sentinel-AnalysisRuns-{STAGE}")
            ),
            transitions_table=dynamodb.Table(
                _table_name(
                    "STATUS_TRANSITIONS_TABLE_NAME",
                    f"epf-sentinel-StatusTransitions-{STAGE}",
                )
            ),
            quota_table=dynamodb.Table(
                _table_name("QUOTA_TABLE_NAME", f"epf-sentinel-AnalysisQuota-{STAGE}")
            ),
            s3=s3,
            bucket=_table_name("DOCS_BUCKET_NAME", f"epf-sentinel-docs-{STAGE}"),
        )
    except (BotoCoreError, ClientError):
        log.error("account.delete.failed", exc_info=True)
        return internal_error()

    log.info("account.delete.completed", **counts)
    return ok({"deleted": True, **counts})
