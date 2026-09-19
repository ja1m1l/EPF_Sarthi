"""
SlaAgent Lambda – Step 3 of the EPF Sentinel analysis pipeline.

Calls the pure shared.sla.compute_sla() function.  Fully deterministic —
no model, no network, no stub needed.

Contract
--------
Input (state machine context, includes rulesDecision from RulesAgentStep):
    {
        "correlationId":  "<uuid>",
        "runId":          "<uuid>",
        "claimId":        "<str>",
        "analysisInput":  { <AnalysisInput dict> },
        "rulesDecision":  { applicable, timelineDays, timelineBasis, ... }
    }

Output (merged):
    {
        ...previous,
        "slaResult": {
            "skipped":        false,
            "deadlineDateIso": "YYYY-MM-DD",
            "elapsedDays":    <int>,
            "remainingDays":  <int>,
            "clockStartDateIso": "YYYY-MM-DD",
            "status":         "WITHIN" | "APPROACHING" | "OVERDUE",
            "explanation":    "<str>",
            "priorElapsedDays": <int> | null
        }
        OR
        "slaResult": { "skipped": true, "reason": "rules_abstained" }
    }
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Optional

from shared.clock import today_ist
from shared.logging import get_logger, set_correlation_id
from shared.sla import SlaError, compute_sla

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for SlaAgent."""
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "sla_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        stage=STAGE,
    )

    rules_decision: dict = event.get("rulesDecision", {})
    analysis_input: dict = event.get("analysisInput", {})

    # ── If rules abstained, SLA is meaningless ───────────────────────────────
    if not rules_decision.get("applicable", False):
        log.info(
            "sla_agent.skipped",
            correlationId=correlation_id,
            claimId=claim_id,
            reason="rules_abstained",
            abstainReason=rules_decision.get("abstainReason"),
        )
        return {
            **event,
            "slaResult": {"skipped": True, "reason": "rules_abstained"},
        }

    # ── Extract inputs ───────────────────────────────────────────────────────
    try:
        claim_date = date.fromisoformat(analysis_input["claimDateIso"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid claimDateIso in analysisInput: {exc}") from exc

    timeline_days: int = int(rules_decision["timelineDays"])
    basis: str = str(rules_decision["timelineBasis"])

    deficiency_iso: Optional[str] = analysis_input.get("deficiencyRaisedDateIso")
    deficiency_date: Optional[date] = (
        date.fromisoformat(deficiency_iso) if deficiency_iso else None
    )

    today: date = today_ist()

    # ── Compute SLA ──────────────────────────────────────────────────────────
    try:
        result = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis=basis,  # type: ignore[arg-type]
            today=today,
            deficiency_raised_date=deficiency_date,
            holidays=frozenset(),  # no holiday calendar in v1
        )
    except SlaError as exc:
        log.warning(
            "sla_agent.sla_error",
            correlationId=correlation_id,
            claimId=claim_id,
            error=str(exc),
        )
        raise

    sla_result = {
        "skipped": False,
        "deadlineDateIso": result.deadlineDate.isoformat(),
        "elapsedDays": result.elapsedDays,
        "remainingDays": result.remainingDays,
        "clockStartDateIso": result.clockStartDate.isoformat(),
        "status": result.status,
        "explanation": result.explanation,
        "priorElapsedDays": result.priorElapsedDays,
    }

    log.info(
        "sla_agent.completed",
        correlationId=correlation_id,
        claimId=claim_id,
        slaStatus=result.status,
        remainingDays=result.remainingDays,
    )

    return {
        **event,
        "slaResult": sla_result,
    }
