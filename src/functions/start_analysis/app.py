"""
StartAnalysis Lambda — POST /claims/{claimId}/analyze

Starts a Step Functions Express execution for the requested claim,
creates an AnalysisRun row in DynamoDB with status=RUNNING,
and returns 202 with the runId immediately.

Security: Cognito JWT authorizer.  userId extracted from JWT claims.
Tenant-scope: claim must belong to the authenticated user.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.http import internal_error, not_found, ok, _response
from shared.logging import get_logger, set_correlation_id
from shared.models import AnalysisRun, Claim, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
ANALYSIS_RUNS_TABLE: str = os.environ.get(
    "ANALYSIS_RUNS_TABLE_NAME", f"epf-sentinel-AnalysisRuns-{STAGE}"
)
STATE_MACHINE_ARN: str = os.environ.get("ANALYSIS_SFN_ARN", "")
TTL_90_DAYS: int = 90 * 24 * 60 * 60


def _get_user_id(event: dict) -> str:
    """Extract userId from Cognito JWT authorizer context."""
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "")
    )


def _fetch_claim(user_id: str, claim_id: str) -> dict | None:
    """Fetch claim from DynamoDB; returns None if not found."""
    ddb = boto3.client("dynamodb")
    resp = ddb.get_item(
        TableName=CLAIMS_TABLE,
        Key={
            "userId":  {"S": user_id},
            "claimId": {"S": claim_id},
        },
    )
    return resp.get("Item")


def _save_run(run: AnalysisRun, claim: dict) -> None:
    """Persist initial RUNNING run record to DynamoDB."""
    ddb = boto3.client("dynamodb")
    expires_at = int(time.time()) + TTL_90_DAYS
    item = {
        "claimId":       {"S": run.claimId},
        "runId":         {"S": run.runId},
        "correlationId": {"S": run.correlationId},
        "status":        {"S": run.status},
        "startedAt":     {"S": run.startedAt},
        "expiresAt":     {"N": str(expires_at)},
    }
    if run.stepFunctionExecutionArn:
        item["stepFunctionExecutionArn"] = {"S": run.stepFunctionExecutionArn}
    ddb.put_item(TableName=ANALYSIS_RUNS_TABLE, Item=item)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point — POST /claims/{claimId}/analyze."""
    request_id = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)

    log.info("start_analysis.request.received", correlationId=correlation_id, stage=STAGE)

    user_id = _get_user_id(event)
    if not user_id:
        return _response(401, {"error": "Unauthorized"})

    claim_id: str = (
        event.get("pathParameters") or {}
    ).get("claimId", "")
    if not claim_id:
        return _response(400, {"error": "Missing claimId path parameter"})

    # ── Fetch and tenant-scope the claim ────────────────────────────────────
    try:
        raw_item = _fetch_claim(user_id, claim_id)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.dynamo.fetch_failed",
            correlationId=correlation_id,
            claimId=claim_id,
            error=str(exc),
        )
        return internal_error()

    if not raw_item:
        return not_found()

    # Unmarshal DynamoDB item to plain dict for SFN payload
    claim_dict = {k: list(v.values())[0] for k, v in raw_item.items()}

    # ── Start Step Functions execution ───────────────────────────────────────
    run_id = str(uuid.uuid4())
    sfn_input = {
        "correlationId": correlation_id,
        "runId": run_id,
        "claimId": claim_id,
        "claim": claim_dict,
        "startedAt": utc_now_iso(),
    }

    sfn = boto3.client("stepfunctions")
    try:
        sfn_resp = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=run_id,  # unique per execution
            input=json.dumps(sfn_input),
        )
        execution_arn = sfn_resp.get("executionArn", "")
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.sfn.start_failed",
            correlationId=correlation_id,
            runId=run_id,
            claimId=claim_id,
            error=str(exc),
        )
        return internal_error()

    # ── Persist initial RUNNING run record ───────────────────────────────────
    run = AnalysisRun.new(
        claimId=claim_id,
        correlationId=correlation_id,
        expiresAt=int(time.time()) + TTL_90_DAYS,
        stepFunctionExecutionArn=execution_arn,
    )
    # Override the auto-generated runId with the one we sent to SFN
    run.runId = run_id  # type: ignore[misc]

    try:
        _save_run(run, claim_dict)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.dynamo.save_failed",
            correlationId=correlation_id,
            runId=run_id,
            error=str(exc),
        )
        # Execution already started — best-effort save failure only logged
        return internal_error()

    log.info(
        "start_analysis.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        executionArn=execution_arn,
    )

    return _response(202, {
        "runId": run_id,
        "correlationId": correlation_id,
        "status": "RUNNING",
    })
