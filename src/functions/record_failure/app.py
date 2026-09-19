"""
RecordFailure Lambda – Error sink for the EPF Sentinel analysis pipeline.

Wired as the Catch target from every state in the Step Functions workflow.
Maps the error type to the correct AnalysisRunStatus (never collapsing
FAILED_INFRASTRUCTURE and FAILED_VALIDATION into the same bucket).

Contract
--------
Input (from Step Functions Catch):
    {
        "correlationId":  "<uuid>",
        "runId":          "<uuid>",
        "claimId":        "<str>",
        "errorType":      "<ErrorType string>",     # e.g. "ValidationError"
        "errorMessage":   "<str>",
        "cause":          "<str>"
    }

Output:
    {
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "correlationId": "<uuid>",
        "status":        "FAILED_INFRASTRUCTURE" | "FAILED_VALIDATION"
    }

Status mapping
--------------
ValidationError (from ClaimAgent)  → FAILED_VALIDATION
Everything else                    → FAILED_INFRASTRUCTURE

Note: COMPLETED_WITH_ABSTENTION is NOT a failure and will never be set here.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.logging import get_logger, set_correlation_id
from shared.models import AnalysisRunStatus, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
ANALYSIS_RUNS_TABLE: str = os.environ.get(
    "ANALYSIS_RUNS_TABLE_NAME",
    f"epf-sentinel-AnalysisRuns-{STAGE}",
)
TTL_90_DAYS_SECONDS: int = 90 * 24 * 60 * 60

# Error types that map to FAILED_VALIDATION (never retried by SFN)
_VALIDATION_ERROR_TYPES: frozenset[str] = frozenset({
    "ValidationError",
    "ValueError",
})


def _map_error_to_status(error_type: str) -> str:
    if error_type in _VALIDATION_ERROR_TYPES:
        return AnalysisRunStatus.FAILED_VALIDATION.value
    return AnalysisRunStatus.FAILED_INFRASTRUCTURE.value


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for RecordFailure."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")
    error_type: str = event.get("errorType", "UnknownError")
    error_message: str = event.get("errorMessage", "")
    cause: str = event.get("cause", "")

    final_status = _map_error_to_status(error_type)

    log.error(
        "record_failure.recording",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        errorType=error_type,
        errorMessage=error_message,
        finalStatus=final_status,
        stage=STAGE,
    )

    failure_detail = json.dumps({
        "errorType": error_type,
        "errorMessage": error_message,
        "cause": cause,
    })

    now = utc_now_iso()
    expires_at = int(time.time()) + TTL_90_DAYS_SECONDS

    item: dict[str, Any] = {
        "claimId":       {"S": claim_id},
        "runId":         {"S": run_id},
        "correlationId": {"S": correlation_id},
        "status":        {"S": final_status},
        "failureDetail": {"S": failure_detail},
        "startedAt":     {"S": event.get("startedAt", now)},
        "finishedAt":    {"S": now},
        "expiresAt":     {"N": str(expires_at)},
    }

    sfn_arn = event.get("stepFunctionExecutionArn")
    if sfn_arn:
        item["stepFunctionExecutionArn"] = {"S": sfn_arn}

    try:
        ddb = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        ddb.put_item(TableName=ANALYSIS_RUNS_TABLE, Item=item)
    except (BotoCoreError, ClientError) as exc:
        # Log but do not re-raise — this is already the error handler.
        # Re-raising here would cause an infinite catch loop.
        log.error(
            "record_failure.dynamodb.failed",
            correlationId=correlation_id,
            runId=run_id,
            error=str(exc),
        )

    return {
        "runId": run_id,
        "claimId": claim_id,
        "correlationId": correlation_id,
        "status": final_status,
    }
