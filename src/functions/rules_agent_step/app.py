"""
RulesAgentStep Lambda – Step 2 of the EPF Sentinel analysis pipeline.

This is the Step-Functions-facing adapter for the Rules Agent.  In stub mode
(USE_STUBS=true, the default) it returns a canned RuleDecision without calling
any model.  In production mode it invokes the real RulesAgentFunction Lambda
via boto3 Lambda invoke.

Contract
--------
Input  (state machine context threaded from ClaimAgent output):
    {
        "correlationId":  "<uuid>",
        "runId":          "<uuid>",
        "claimId":        "<str>",
        "claim":          { <Claim dict> },
        "analysisInput":  { <AnalysisInput dict> }
    }

Output (merged into event):
    {
        ...previous,
        "rulesDecision": {
            "applicable":    true | false,
            "abstainReason": null | "<str>",
            "timelineDays":  <int> | null,
            "timelineBasis": "CALENDAR" | "WORKING" | null,
            "charterTargetDays": <int> | null,
            "citedChunkIds": [...],
            "citedSourceUrls": [...],
            "quotedSpan":    "<str>",
            "confidence":    "HIGH" | "MEDIUM" | "LOW",
            "stubbed":       true | false
        }
    }

Errors
------
InfrastructureError  — raised on Lambda invoke 5xx or payload parse failure.
                       Step Functions Catch routes to RecordFailure → FAILED_INFRASTRUCTURE.
                       Retried by SFN retry policy (max 2, exp backoff).
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3

from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
USE_STUBS: bool = os.environ.get("USE_STUBS", "true").lower() in ("true", "1", "yes")
RULES_AGENT_FUNCTION_NAME: str = os.environ.get(
    "RULES_AGENT_FUNCTION_NAME",
    f"epf-sentinel-rules-agent-{STAGE}",
)


class InfrastructureError(Exception):
    """Raised when the Rules Agent Lambda invocation fails (5xx or parse error)."""


# ── Stub response ────────────────────────────────────────────────────────────

_STUB_RULE_DECISION: dict[str, Any] = {
    "applicable": True,
    "timelineDays": 20,
    "timelineBasis": "CALENDAR",
    "charterTargetDays": 7,
    "citedChunkIds": ["stub-chunk-001"],
    "citedSourceUrls": ["https://www.epfindia.gov.in/stub"],
    "quotedSpan": "Settlement Time as per Scheme is 20 Days.",
    "confidence": "HIGH",
    "abstainReason": None,
    "stubbed": True,
}


# ── Real invocation helper ───────────────────────────────────────────────────

def _invoke_rules_agent(claim_data: dict, correlation_id: str) -> dict[str, Any]:
    """Invoke the real RulesAgentFunction and return its parsed decision dict."""
    lambda_client = boto3.client("lambda")
    payload = {
        "claim": claim_data,
        "ruleSetVersion": "v1",
        "correlationId": correlation_id,
    }
    try:
        response = lambda_client.invoke(
            FunctionName=RULES_AGENT_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )
    except Exception as exc:
        raise InfrastructureError(f"Lambda invoke failed: {exc}") from exc

    status_code = response.get("StatusCode", 0)
    if status_code != 200:
        raise InfrastructureError(
            f"RulesAgent Lambda returned HTTP {status_code}"
        )

    try:
        raw_payload = response["Payload"].read()
        outer = json.loads(raw_payload)
    except Exception as exc:
        raise InfrastructureError(f"Failed to parse RulesAgent response: {exc}") from exc

    # RulesAgentFunction returns {"statusCode": 200, "body": "<json str>"}
    body_str = outer.get("body", "{}")
    try:
        body = json.loads(body_str) if isinstance(body_str, str) else body_str
    except Exception as exc:
        raise InfrastructureError(f"Failed to parse RulesAgent body JSON: {exc}") from exc

    inner_status = body.get("status", "FAILED")
    if outer.get("statusCode", 500) >= 500 or inner_status == "FAILED":
        raise InfrastructureError(
            f"RulesAgent returned FAILED: {body.get('error', 'unknown')}"
        )

    decision = body.get("decision", {})
    decision["stubbed"] = False
    return decision


# ── Handler ──────────────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for RulesAgentStep."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "rules_agent_step.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        useStubs=USE_STUBS,
        stage=STAGE,
    )

    claim_data: dict = event.get("claim", {})

    if USE_STUBS:
        decision = dict(_STUB_RULE_DECISION)
        log.info(
            "rules_agent_step.stub.returned",
            correlationId=correlation_id,
            claimId=claim_id,
            applicable=decision["applicable"],
        )
    else:
        decision = _invoke_rules_agent(claim_data, correlation_id)
        log.info(
            "rules_agent_step.completed",
            correlationId=correlation_id,
            claimId=claim_id,
            applicable=decision.get("applicable"),
            abstainReason=decision.get("abstainReason"),
        )

    return {
        **event,
        "rulesDecision": decision,
    }
