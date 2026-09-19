"""
ClaimAgent Lambda – Step 1 of the EPF Sentinel analysis pipeline.

Receives the raw SFN input, validates the Claim, and produces a normalised
AnalysisInput that is passed to every subsequent state.

Contract
--------
Input  (from StartExecution / previous state):
    {
        "correlationId": "<uuid>",
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "claim":         { <Claim dict> }
    }

Output (merged into state machine context via ResultPath):
    {
        "correlationId": "<uuid>",
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "claim":         { <Claim dict> },
        "analysisInput": { <AnalysisInput dict> }
    }

Errors
------
ValidationError  — raised for any structurally invalid Claim field.
                   Step Functions Catch routes this to RecordFailure with
                   FAILED_VALIDATION. Never retried.
"""

from __future__ import annotations

import os
from typing import Any

from shared.logging import get_logger, set_correlation_id
from shared.models import AnalysisInput, Claim, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")


class ValidationError(Exception):
    """Raised when the incoming Claim fails structural validation."""


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for ClaimAgent."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "claim_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        stage=STAGE,
    )

    # ── 1. Extract claim dict ────────────────────────────────────────────────
    claim_data = event.get("claim")
    if not claim_data or not isinstance(claim_data, dict):
        raise ValidationError("Missing or non-dict 'claim' in pipeline input")

    # ── 2. Deserialise and structurally validate ─────────────────────────────
    try:
        claim = Claim.from_dict(claim_data)
    except (TypeError, ValueError, KeyError) as exc:
        log.warning(
            "claim_agent.validation.failed",
            correlationId=correlation_id,
            claimId=claim_id,
            error=str(exc),
        )
        raise ValidationError(f"Invalid claim data: {exc}") from exc

    # ── 3. Produce AnalysisInput ─────────────────────────────────────────────
    analysis_input = AnalysisInput(
        claimId=claim.claimId,
        userId=claim.userId,
        claimType=claim.claimType.value if hasattr(claim.claimType, "value") else str(claim.claimType),
        claimDateIso=claim.claimDateIso,
        amountPaise=claim.amountPaise,
        status=claim.status.value if hasattr(claim.status, "value") else str(claim.status),
        deficiencyRaisedDateIso=claim.deficiencyRaisedDateIso,
        correlationId=correlation_id,
        normalizedAt=utc_now_iso(),
    )

    log.info(
        "claim_agent.completed",
        correlationId=correlation_id,
        claimId=claim.claimId,
        claimType=analysis_input.claimType,
        status=analysis_input.status,
    )

    # Return the full event context merged with analysisInput so Step Functions
    # can thread it through remaining states.
    return {
        **event,
        "analysisInput": analysis_input.to_dict(),
    }
