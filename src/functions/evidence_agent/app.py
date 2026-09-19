"""
EvidenceAgent Lambda – Step 4 of the EPF Sentinel analysis pipeline.

Stub mode (USE_STUBS=true, default): returns a fixed EvidenceReport.
Production mode: would retrieve claim-relevant documents from S3/external API.

Contract
--------
Input (state machine context from SlaAgent output):
    {
        "correlationId": "<uuid>",
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "analysisInput": { <AnalysisInput dict> },
        "rulesDecision": { ... },
        "slaResult":     { ... }
    }

Output (merged):
    {
        ...previous,
        "evidenceReport": {
            "documents": [{ "title", "url", "relevanceScore" }],
            "summary":   "<str>",
            "stubbed":   true
        }
    }
"""

from __future__ import annotations

import os
from typing import Any

from shared.logging import get_logger, set_correlation_id
from shared.models import EvidenceReport

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
USE_STUBS: bool = os.environ.get("USE_STUBS", "true").lower() in ("true", "1", "yes")


# ── Stub response ────────────────────────────────────────────────────────────

def _stub_evidence_report(claim_id: str) -> EvidenceReport:
    return EvidenceReport(
        documents=[
            {
                "title": "EPF Form-19 Settlement Guidelines",
                "url": "https://www.epfindia.gov.in/stub/form19-guidelines",
                "relevanceScore": 0.95,
            },
            {
                "title": "EPFO Citizens' Charter 2023",
                "url": "https://www.epfindia.gov.in/stub/citizens-charter",
                "relevanceScore": 0.82,
            },
        ],
        summary=(
            f"[STUB] Retrieved 2 relevant documents for claim {claim_id}. "
            "Primary evidence: Form-19 settlement guidelines confirming 20-day statutory timeline."
        ),
        stubbed=True,
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for EvidenceAgent."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "evidence_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        useStubs=USE_STUBS,
        stage=STAGE,
    )

    if USE_STUBS:
        report = _stub_evidence_report(claim_id)
        log.info(
            "evidence_agent.stub.returned",
            correlationId=correlation_id,
            claimId=claim_id,
            documentCount=len(report.documents),
        )
    else:
        # TODO(module-2.5): real evidence retrieval from S3 / document API
        raise NotImplementedError(
            "EvidenceAgent real mode not yet implemented; set USE_STUBS=true"
        )

    return {
        **event,
        "evidenceReport": report.to_dict(),
    }
