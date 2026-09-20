"""
PersistRun Lambda – Final step of the EPF Sentinel analysis pipeline.

Writes the completed AnalysisRun to DynamoDB, capturing:
 - All five agent outputs (as JSON strings in separate attributes)
 - A single consistent correlationId
 - TTL of 90 days from now
 - Final run status (COMPLETED or COMPLETED_WITH_ABSTENTION)

Contract
--------
Input (full state context from GrievanceAgent output):
    {
        "correlationId":  "<uuid>",
        "runId":          "<uuid>",
        "claimId":        "<str>",
        "analysisInput":  { <AnalysisInput dict> },
        "rulesDecision":  { ... },
        "slaResult":      { ... },
        "evidenceReport": { ... },
        "grievanceDraft": { ... }
    }

Output:
    {
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "correlationId": "<uuid>",
        "status":        "COMPLETED" | "COMPLETED_WITH_ABSTENTION"
    }

DynamoDB write is idempotent on (claimId, runId) — duplicate executions are safe.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.logging import get_logger, set_correlation_id
from shared.metrics import analysis_status
from shared.models import AnalysisRun, AnalysisRunStatus, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
ANALYSIS_RUNS_TABLE: str = os.environ.get(
    "ANALYSIS_RUNS_TABLE_NAME",
    f"epf-sentinel-AnalysisRuns-{STAGE}",
)
TTL_90_DAYS_SECONDS: int = 90 * 24 * 60 * 60


def _dynamodb_client():
    return boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for PersistRun."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "persist_run.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        stage=STAGE,
    )

    # ── Determine final status ───────────────────────────────────────────────
    rules_decision: dict = event.get("rulesDecision", {})
    applicable: bool = bool(rules_decision.get("applicable", False))
    final_status = (
        AnalysisRunStatus.COMPLETED.value
        if applicable
        else AnalysisRunStatus.COMPLETED_WITH_ABSTENTION.value
    )

    # ── Build per-agent JSON strings ─────────────────────────────────────────
    claim_agent_json = json.dumps(event.get("analysisInput", {}))
    rules_agent_json = json.dumps(event.get("rulesDecision", {}))
    sla_agent_json = json.dumps(event.get("slaResult", {}))
    evidence_agent_json = json.dumps(event.get("evidenceReport", {}))
    grievance_agent_json = json.dumps(event.get("grievanceDraft", {}))

    # Full merged snapshot as legacy resultJson
    result_json = json.dumps({
        "analysisInput": event.get("analysisInput", {}),
        "rulesDecision": event.get("rulesDecision", {}),
        "slaResult": event.get("slaResult", {}),
        "evidenceReport": event.get("evidenceReport", {}),
        "grievanceDraft": event.get("grievanceDraft", {}),
    })

    now = utc_now_iso()
    expires_at = int(time.time()) + TTL_90_DAYS_SECONDS

    # ── Write to DynamoDB ────────────────────────────────────────────────────
    item = {
        "claimId":                {"S": claim_id},
        "runId":                  {"S": run_id},
        "correlationId":          {"S": correlation_id},
        "status":                 {"S": final_status},
        "claimAgentOutputJson":   {"S": claim_agent_json},
        "rulesAgentOutputJson":   {"S": rules_agent_json},
        "slaAgentOutputJson":     {"S": sla_agent_json},
        "evidenceAgentOutputJson":{"S": evidence_agent_json},
        "grievanceAgentOutputJson":{"S": grievance_agent_json},
        "resultJson":             {"S": result_json},
        "startedAt":              {"S": event.get("startedAt", now)},
        "finishedAt":             {"S": now},
        "expiresAt":              {"N": str(expires_at)},
    }

    # Include stepFunctionExecutionArn if available
    sfn_arn = event.get("stepFunctionExecutionArn")
    if sfn_arn:
        item["stepFunctionExecutionArn"] = {"S": sfn_arn}

    try:
        ddb = _dynamodb_client()
        ddb.put_item(TableName=ANALYSIS_RUNS_TABLE, Item=item)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "persist_run.dynamodb.failed",
            correlationId=correlation_id,
            runId=run_id,
            claimId=claim_id,
            error=str(exc),
        )
        raise

    try:
        _pin_latest_run(event, claim_id, run_id, final_status, ddb)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "persist_run.pin_failed",
            correlationId=correlation_id,
            runId=run_id,
            claimId=claim_id,
            error=str(exc),
        )

    analysis_status(final_status, _latency_ms(event.get("startedAt")))

    log.info(
        "persist_run.completed",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        status=final_status,
        expiresAt=expires_at,
    )

    return {
        "runId": run_id,
        "claimId": claim_id,
        "correlationId": correlation_id,
        "status": final_status,
    }


def _pin_latest_run(
    event: dict[str, Any],
    claim_id: str,
    run_id: str,
    status: str,
    ddb: Any,
) -> None:
    """Remember this finished run on the Claim so later views skip re-analysis."""
    user_id = (
        (event.get("analysisInput") or {}).get("userId")
        or (event.get("claim") or {}).get("userId")
        or event.get("userId")
        or ""
    )
    if not user_id or not claim_id or not run_id:
        log.warning("persist_run.pin_skipped", claimId=claim_id, runId=run_id)
        return

    ddb.update_item(
        TableName=CLAIMS_TABLE,
        Key={"userId": {"S": user_id}, "claimId": {"S": claim_id}},
        UpdateExpression="SET latestRunId = :r, latestRunStatus = :s, updatedAt = :u",
        ExpressionAttributeValues={
            ":r": {"S": run_id},
            ":s": {"S": status},
            ":u": {"S": utc_now_iso()},
        },
    )


def _latency_ms(started_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(utc_now_iso())
        return max(0.0, (end - start).total_seconds() * 1000)
    except (TypeError, ValueError):
        return None
