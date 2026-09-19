"""
ClaimAgent Lambda – Step 1 of the EPF Sentinel analysis pipeline.

Receives raw input, extracts and normalises claim fields using Gemini when USE_STUBS=false,
strictly code-validates all fields post-generation, and produces AnalysisInput.

Contract
--------
Input  (from StartExecution / previous state):
    {
        "correlationId": "<uuid>",
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "claim":         { <Claim dict or dict with free text> }
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
ValidationError  — raised for any structurally invalid or missing Claim field.
                   Step Functions Catch routes this to RecordFailure with
                   FAILED_VALIDATION. Never retried.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from dateutil import parser as date_parser

from shared import gemini
from shared.logging import get_logger, set_correlation_id
from shared.models import AnalysisInput, Claim, ClaimStatus, ClaimType, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
USE_STUBS: bool = os.environ.get("USE_STUBS", "false").lower() in ("true", "1", "yes")


class ValidationError(Exception):
    """Raised when the incoming Claim fails structural validation."""


# ── Code Validation Helpers ───────────────────────────────────────────────────

def _parse_and_validate_date(raw: Any) -> Optional[str]:
    """Parse raw date with a real date parser. Return YYYY-MM-DD or ISO string, or None if invalid."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = date_parser.parse(raw)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_and_validate_amount(raw: Any) -> Optional[int]:
    """
    Validate amount and convert to integer paise.
    Returns int >= 0, or None if invalid/negative/non-numeric.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        val = float(raw)
        if val < 0:
            return None
        # If integer and >= 100, could be paise
        if isinstance(raw, int) and raw >= 100:
            return raw
        # Convert rupees (e.g. 1000.0) to paise (100000)
        return int(round(val * 100))
    except Exception:
        return None


def _validate_enum(raw: Any, enum_cls: Any) -> Optional[str]:
    """Check enum value against allowlist. Return string value or None if invalid."""
    if not raw or not isinstance(raw, str):
        return None
    clean = raw.strip().upper()
    valid_values = {e.value for e in enum_cls}
    if clean in valid_values:
        return clean
    return None


def _extract_with_gemini(claim_data: dict[str, Any]) -> dict[str, Any]:
    """Call Gemini to extract claim fields from structured + free text input."""
    prompt = (
        "Extract and normalize EPF claim fields from the following input data.\n"
        f"Input Data:\n{json.dumps(claim_data, indent=2)}\n\n"
        "Return a valid JSON object with the following schema:\n"
        "{\n"
        '  "claimType": "FINAL_SETTLEMENT",\n'
        '  "claimDateIso": "YYYY-MM-DD",\n'
        '  "amountPaise": 100000,\n'
        '  "status": "SUBMITTED" | "PENDING" | "UNDER_PROCESS" | "REJECTED" | "SETTLED",\n'
        '  "deficiencyRaisedDateIso": "YYYY-MM-DD" or null\n'
        "}\n"
    )

    schema = {
        "type": "OBJECT",
        "properties": {
            "claimType": {"type": "STRING"},
            "claimDateIso": {"type": "STRING"},
            "amountPaise": {"type": "INTEGER"},
            "status": {"type": "STRING"},
            "deficiencyRaisedDateIso": {"type": "STRING", "nullable": True},
        },
        "required": ["claimType", "claimDateIso", "amountPaise", "status"],
    }

    response_text = gemini.generate(
        prompt=prompt,
        system_instruction="You are an expert EPF claim parser. Extract claim details precisely.",
        response_schema=schema,
    )

    try:
        extracted = json.loads(response_text)
    except json.JSONDecodeError:
        extracted = {}

    return extracted


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for ClaimAgent."""
    use_stubs = os.environ.get("USE_STUBS", "false").lower() in ("true", "1", "yes")
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "claim_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        useStubs=use_stubs,
        stage=STAGE,
    )

    # ── 1. Extract claim dict ────────────────────────────────────────────────
    claim_data = event.get("claim")
    if not claim_data or not isinstance(claim_data, dict):
        raise ValidationError("Missing or non-dict 'claim' in pipeline input")

    user_id = claim_data.get("userId", "user-default")
    effective_claim_id = claim_data.get("claimId") or claim_id or "claim-default"

    # ── 2. Extraction & Normalization ─────────────────────────────────────────
    if use_stubs:
        # Stub mode: direct structural deserialisation
        try:
            claim = Claim.from_dict(claim_data)
            claim_type = claim.claimType.value if hasattr(claim.claimType, "value") else str(claim.claimType)
            claim_date_iso = claim.claimDateIso
            amount_paise = claim.amountPaise
            status = claim.status.value if hasattr(claim.status, "value") else str(claim.status)
            deficiency_date_iso = claim.deficiencyRaisedDateIso
        except Exception as exc:
            raise ValidationError(f"Invalid claim data in stub mode: {exc}") from exc
    else:
        # Gemini mode
        extracted = _extract_with_gemini(claim_data)

        # Merge extracted with structured input if available
        raw_claim_type = extracted.get("claimType") or claim_data.get("claimType")
        raw_claim_date = extracted.get("claimDateIso") or claim_data.get("claimDateIso")
        raw_amount = extracted.get("amountPaise") if "amountPaise" in extracted else claim_data.get("amountPaise")
        if raw_amount is None and "amountRupees" in claim_data:
            raw_amount = claim_data.get("amountRupees")
        raw_status = extracted.get("status") or claim_data.get("status")
        raw_deficiency_date = extracted.get("deficiencyRaisedDateIso") or claim_data.get("deficiencyRaisedDateIso")

        # Code validation & dropping invalid fields (never coercing guesses)
        claim_type = _validate_enum(raw_claim_type, ClaimType)
        claim_date_iso = _parse_and_validate_date(raw_claim_date)
        amount_paise = _parse_and_validate_amount(raw_amount)
        status = _validate_enum(raw_status, ClaimStatus)
        deficiency_date_iso = _parse_and_validate_date(raw_deficiency_date) if raw_deficiency_date else None

        # Check required fields post-validation
        missing_fields = []
        if not claim_type:
            missing_fields.append("claimType")
        if not claim_date_iso:
            missing_fields.append("claimDateIso")
        if amount_paise is None:
            missing_fields.append("amountPaise")
        if not status:
            missing_fields.append("status")

        if missing_fields:
            log.warning(
                "claim_agent.validation.dropped_missing_fields",
                correlationId=correlation_id,
                missingFields=missing_fields,
            )
            raise ValidationError(f"Missing or invalid claim fields post-validation: {', '.join(missing_fields)}")

    # ── 3. Produce AnalysisInput ─────────────────────────────────────────────
    analysis_input = AnalysisInput(
        claimId=effective_claim_id,
        userId=user_id,
        claimType=claim_type,
        claimDateIso=claim_date_iso,
        amountPaise=amount_paise,
        status=status,
        deficiencyRaisedDateIso=deficiency_date_iso,
        correlationId=correlation_id,
        normalizedAt=utc_now_iso(),
    )

    log.info(
        "claim_agent.completed",
        correlationId=correlation_id,
        claimId=analysis_input.claimId,
        claimType=analysis_input.claimType,
        status=analysis_input.status,
    )

    return {
        **event,
        "analysisInput": analysis_input.to_dict(),
    }
