"""
GrievanceAgent Lambda – Step 5 of the EPF Sentinel analysis pipeline.

Stub mode (USE_STUBS=true, default): returns a fixed draft grievance string.
Production mode: would call an LLM to draft a personalised grievance letter.

Contract
--------
Input (full state context from EvidenceAgent):
    {
        "correlationId": "<uuid>",
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "analysisInput": { <AnalysisInput dict> },
        "rulesDecision": { ... },
        "slaResult":     { ... },
        "evidenceReport": { ... }
    }

Output (merged):
    {
        ...previous,
        "grievanceDraft": {
            "draftText":  "<str>",
            "stubbed":    true
        }
    }
"""

from __future__ import annotations

import os
from typing import Any

from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
USE_STUBS: bool = os.environ.get("USE_STUBS", "true").lower() in ("true", "1", "yes")


def _stub_grievance_draft(claim_id: str, analysis_input: dict) -> dict[str, Any]:
    claim_type = analysis_input.get("claimType", "FINAL_SETTLEMENT")
    amount_paise = analysis_input.get("amountPaise", 0)
    amount_inr = amount_paise / 100.0

    draft = (
        f"[STUB DRAFT]\n\n"
        f"To,\nThe Regional Provident Fund Commissioner,\n\n"
        f"Subject: Grievance regarding delayed settlement of {claim_type} claim (ID: {claim_id})\n\n"
        f"Dear Sir/Madam,\n\n"
        f"I am writing to bring to your attention the undue delay in processing my EPFO "
        f"claim of type {claim_type} amounting to INR {amount_inr:,.2f} (Claim ID: {claim_id}).\n\n"
        f"As per the EPF Scheme, the statutory settlement timeline is 20 calendar days. "
        f"My claim has not been settled within this period.\n\n"
        f"I request you to kindly expedite the processing of my claim.\n\n"
        f"Yours faithfully,\n[Member Name]\n[UAN]\n[Contact]\n"
    )
    return {"draftText": draft, "stubbed": True}


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for GrievanceAgent."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "grievance_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        useStubs=USE_STUBS,
        stage=STAGE,
    )

    analysis_input: dict = event.get("analysisInput", {})

    if USE_STUBS:
        draft = _stub_grievance_draft(claim_id, analysis_input)
        log.info(
            "grievance_agent.stub.returned",
            correlationId=correlation_id,
            claimId=claim_id,
            draftLength=len(draft["draftText"]),
        )
    else:
        # TODO(module-2.6): real LLM-backed grievance drafting
        raise NotImplementedError(
            "GrievanceAgent real mode not yet implemented; set USE_STUBS=true"
        )

    return {
        **event,
        "grievanceDraft": draft,
    }
