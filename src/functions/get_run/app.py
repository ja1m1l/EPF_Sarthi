"""
GetRun Lambda — GET /claims/{claimId}/runs/{runId}
              — GET /claims/{claimId}/runs

Returns one persisted AnalysisRun, or every run for the claim (newest first).
Tenant-scoped: validates that the claim belongs to the authenticated user.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.http import internal_error, not_found, ok, _response
from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
ANALYSIS_RUNS_TABLE: str = os.environ.get(
    "ANALYSIS_RUNS_TABLE_NAME", f"epf-sentinel-AnalysisRuns-{STAGE}"
)

_JSON_FIELDS = (
    "claimAgentOutputJson",
    "rulesAgentOutputJson",
    "slaAgentOutputJson",
    "evidenceAgentOutputJson",
    "grievanceAgentOutputJson",
    "resultJson",
    "failureDetail",
)


def _get_user_id(event: dict) -> str:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "")
    )


def _claim_exists_for_user(user_id: str, claim_id: str) -> bool:
    """Return True if the claim exists in the tenant's partition."""
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


def _unmarshal(item: dict) -> dict:
    """Convert DynamoDB attribute map to a plain Python dict."""
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


def _hydrate_run(item: dict) -> dict:
    """Unmarshal a DynamoDB item and parse embedded JSON agent outputs."""
    run_item = _unmarshal(item)
    for json_field in _JSON_FIELDS:
        raw = run_item.get(json_field)
        if raw and isinstance(raw, str):
            try:
                run_item[json_field] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return run_item


def _query_runs(claim_id: str) -> list[dict]:
    """Return every run for a claim, newest ``startedAt`` first."""
    ddb = boto3.client("dynamodb")
    items: list[dict] = []
    kwargs: dict[str, Any] = {
        "TableName": ANALYSIS_RUNS_TABLE,
        "KeyConditionExpression": "claimId = :c",
        "ExpressionAttributeValues": {":c": {"S": claim_id}},
    }
    while True:
        resp = ddb.query(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last

    runs = [_hydrate_run(item) for item in items]
    runs.sort(key=lambda run: run.get("startedAt") or "", reverse=True)
    return runs


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point — GET one run, or list runs when runId is omitted."""
    request_id = getattr(context, "aws_request_id", "")
    set_correlation_id(request_id)

    log.info("get_run.request.received", stage=STAGE)

    user_id = _get_user_id(event)
    if not user_id:
        return _response(401, {"error": "Unauthorized"})

    path_params = event.get("pathParameters") or {}
    claim_id: str = path_params.get("claimId", "")
    run_id: str = path_params.get("runId") or ""

    if not claim_id:
        return _response(400, {"error": "Missing claimId path parameter"})

    try:
        if not _claim_exists_for_user(user_id, claim_id):
            return not_found()
    except (BotoCoreError, ClientError) as exc:
        log.error("get_run.claims.fetch_failed", claimId=claim_id, error=str(exc))
        return internal_error()

    if not run_id:
        try:
            runs = _query_runs(claim_id)
        except (BotoCoreError, ClientError) as exc:
            log.error("get_run.list.fetch_failed", claimId=claim_id, error=str(exc))
            return internal_error()

        log.info("get_run.list.completed", claimId=claim_id, count=len(runs))
        return ok({"runs": runs})

    ddb = boto3.client("dynamodb")
    try:
        resp = ddb.get_item(
            TableName=ANALYSIS_RUNS_TABLE,
            Key={
                "claimId": {"S": claim_id},
                "runId":   {"S": run_id},
            },
        )
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "get_run.dynamo.fetch_failed",
            claimId=claim_id,
            runId=run_id,
            error=str(exc),
        )
        return internal_error()

    if "Item" not in resp:
        return not_found()

    run_item = _hydrate_run(resp["Item"])

    log.info(
        "get_run.completed",
        claimId=claim_id,
        runId=run_id,
        status=run_item.get("status"),
    )

    return ok(run_item)
