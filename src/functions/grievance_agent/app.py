"""
GrievanceAgent Lambda – Step 5 of the EPF Sentinel analysis pipeline.

Generates an EPFiGMS-ready draft grievance using Gemini when USE_STUBS=false.
Redacts PII (UAN, bank account, password) in code BEFORE outbound Gemini request.
Enforces header disclaimer, negative constraints, and post-generation date/rupee grounding check.

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
            "stubbed":    false
        }
    }
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Optional

from shared import gemini
from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
USE_STUBS: bool = os.environ.get("USE_STUBS", "false").lower() in ("true", "1", "yes")
HEADER_DISCLAIMER = "DISCLAIMER: This is a user-reviewable draft generated from user-supplied data and is not legal advice."


class GrievanceError(Exception):
    """Raised when grievance drafting fails or is ungrounded."""


# ── Code-level PII Redaction ─────────────────────────────────────────────────

def redact_pii(data: Any) -> Any:
    """
    Redact UAN (12-digit numbers), bank account numbers (9-18 digit numbers),
    and passwords from strings/dicts/lists in code BEFORE outbound model calls.
    """
    if isinstance(data, str):
        # 12-digit UAN pattern
        s = re.sub(r"\b\d{12}\b", "[REDACTED_UAN]", data)
        # 9 to 18 digit Bank Account Number pattern
        s = re.sub(r"\b\d{9,18}\b", "[REDACTED_BANK_ACCOUNT]", s)
        # Password patterns
        s = re.sub(r"(?i)(password|pass|pwd|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", s)
        return s
    elif isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if k_lower in ("uan", "bankaccount", "accountnumber", "password", "secret", "bank_account", "bank_account_number"):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_pii(v)
        return cleaned
    elif isinstance(data, list):
        return [redact_pii(item) for item in data]
    return data


# ── Grounding Verification ───────────────────────────────────────────────────

def _extract_run_facts(run_context: dict[str, Any]) -> tuple[set[str], set[float]]:
    """Extract all valid date strings and numeric rupee amounts from structured run data."""
    dates: set[str] = set()
    amounts: set[float] = set()

    def _walk(obj: Any):
        if isinstance(obj, str):
            # YYYY-MM-DD
            found_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", obj)
            for d in found_dates:
                dates.add(d)
        elif isinstance(obj, (int, float)):
            val = float(obj)
            amounts.add(val)
            # If val is paise (>= 100), also add rupee equivalent
            if val >= 100:
                amounts.add(round(val / 100.0, 2))
                amounts.add(round(val / 100.0, 0))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if "date" in k.lower() and isinstance(v, str):
                    found_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", v)
                    for d in found_dates:
                        dates.add(d)
                if ("amount" in k.lower() or "paise" in k.lower()) and isinstance(v, (int, float)):
                    val = float(v)
                    amounts.add(val)
                    if "paise" in k.lower():
                        amounts.add(round(val / 100.0, 2))
                        amounts.add(round(val / 100.0, 0))
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(run_context)
    return dates, amounts


def _verify_draft_grounding(draft_text: str, run_context: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Check that every date (YYYY-MM-DD) and rupee figure in draft_text exists in run_context.
    Returns (is_grounded, ungrounded_reasons).
    """
    known_dates, known_amounts = _extract_run_facts(run_context)
    ungrounded = []

    # 1. Check dates
    draft_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", draft_text))
    for d in draft_dates:
        if d not in known_dates:
            ungrounded.append(f"Date '{d}' not found in structured run data ({known_dates})")

    # 2. Check rupee figures (e.g. INR 1,000.00, ₹1000, Rs. 1000)
    rupee_matches = re.findall(r"(?:INR|₹|Rs\.?)\s*([\d,]+(?:\.\d{2})?)", draft_text, re.IGNORECASE)
    for rm in rupee_matches:
        raw_num = rm.replace(",", "")
        try:
            val = float(raw_num)
            if val not in known_amounts:
                ungrounded.append(f"Rupee figure '{rm}' ({val}) not found in structured run data ({known_amounts})")
        except ValueError:
            pass

    return len(ungrounded) == 0, ungrounded


# ── Stub Draft ───────────────────────────────────────────────────────────────

def _stub_grievance_draft(claim_id: str, analysis_input: dict) -> dict[str, Any]:
    claim_type = analysis_input.get("claimType", "FINAL_SETTLEMENT")
    amount_paise = analysis_input.get("amountPaise", 0)
    amount_inr = amount_paise / 100.0

    draft = (
        f"{HEADER_DISCLAIMER}\n\n"
        f"To,\nThe Regional Provident Fund Commissioner,\n\n"
        f"Subject: Grievance regarding delayed settlement of {claim_type} claim (ID: {claim_id})\n\n"
        f"Dear Sir/Madam,\n\n"
        f"I am writing to bring to your attention the delay in processing my EPFO "
        f"claim of type {claim_type} amounting to INR {amount_inr:,.2f} (Claim ID: {claim_id}).\n\n"
        f"As per the EPF Scheme, the statutory settlement timeline is 20 calendar days. "
        f"My claim has not been settled within this period.\n\n"
        f"I request you to kindly expedite the processing of my claim.\n\n"
        f"Yours faithfully,\n[Member Name]\n[Contact]\n"
    )
    return {"draftText": draft, "stubbed": True}


# ── Gemini Generator ─────────────────────────────────────────────────────────

def _generate_draft_with_gemini(redacted_context: dict[str, Any], feedback_prompt: str = "") -> str:
    prompt = (
        "Generate an EPFiGMS-ready grievance draft using ONLY the provided structured analysis run data.\n"
        f"Analysis Run Data:\n{json.dumps(redacted_context, indent=2)}\n\n"
        "Draft Requirements:\n"
        "1. MUST start with the exact header line:\n"
        f"   {HEADER_DISCLAIMER}\n"
        "2. Include claim details (claimId, claimType, claimDate, amount in INR).\n"
        "3. Include computed timeline with cited rule and source URL (from rulesDecision).\n"
        "4. Include specific issue and evidence findings (from evidenceReport).\n"
        "5. Include clear requested action.\n"
        "6. NEGATIVE CONSTRAINTS:\n"
        "   - NEVER assert wrongdoing, negligence, or bad faith.\n"
        "   - NEVER promise an outcome or a timeline for resolution.\n"
        "   - Restate ONLY facts present in the analysis run data. Do not invent dates or monetary amounts.\n"
    )
    if feedback_prompt:
        prompt += f"\nCRITICAL CORRECTION REQUIRED FROM PREVIOUS ATTEMPT:\n{feedback_prompt}\n"

    system_instruction = (
        "You are an expert grievance drafting assistant for EPFO members. "
        "Draft formal, polite, factual grievances strictly grounded in user-supplied data."
    )

    draft_text = gemini.generate(
        prompt=prompt,
        system_instruction=system_instruction,
    )

    # Ensure header disclaimer is present
    if not draft_text.startswith(HEADER_DISCLAIMER):
        draft_text = f"{HEADER_DISCLAIMER}\n\n{draft_text}"

    return draft_text


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for GrievanceAgent."""
    use_stubs = os.environ.get("USE_STUBS", "false").lower() in ("true", "1", "yes")
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "grievance_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        useStubs=use_stubs,
        stage=STAGE,
    )

    analysis_input: dict = event.get("analysisInput", {})

    if use_stubs:
        draft_res = _stub_grievance_draft(claim_id, analysis_input)
    else:
        # 1. Pre-request redaction in code BEFORE outbound Gemini request is built
        redacted_context = redact_pii(event)

        # 2. Attempt 1 generation
        draft_text = _generate_draft_with_gemini(redacted_context)

        # 3. Post-generation grounding check
        is_grounded, ungrounded_reasons = _verify_draft_grounding(draft_text, event)

        if not is_grounded:
            log.warning(
                "grievance_agent.draft_ungrounded.retrying",
                correlationId=correlation_id,
                reasons=ungrounded_reasons,
            )
            # Retry ONCE with jittered backoff and strict feedback
            time.sleep(max(0.1, 0.4 + random.uniform(-0.1, 0.2)))
            feedback = "The previous draft was ungrounded: " + "; ".join(ungrounded_reasons)
            draft_text = _generate_draft_with_gemini(redacted_context, feedback_prompt=feedback)

            # Re-verify grounding post-retry
            is_grounded, ungrounded_reasons = _verify_draft_grounding(draft_text, event)
            if not is_grounded:
                log.error(
                    "grievance_agent.draft_ungrounded.failed",
                    correlationId=correlation_id,
                    reasons=ungrounded_reasons,
                )
                raise GrievanceError(
                    f"DRAFT_UNGROUNDED: Generated grievance draft contains facts not present in AnalysisRun: {ungrounded_reasons}"
                )

        draft_res = {
            "draftText": draft_text,
            "stubbed": False,
        }

    log.info(
        "grievance_agent.completed",
        correlationId=correlation_id,
        claimId=claim_id,
        draftLength=len(draft_res["draftText"]),
    )

    return {
        **event,
        "grievanceDraft": draft_res,
    }
