"""
EvidenceAgent Lambda – Step 4 of the EPF Sentinel analysis pipeline.

Receives claim details and extracted document text, runs 5 standard evidence checks
via Gemini when USE_STUBS=false, and enforces a hard code-level check on verbatim
excerpts for CONFIRMED verdicts (downgrading to NOT_FOUND and publishing a CloudWatch metric on failure).

Contract
--------
Input (state machine context from SlaAgent output):
    {
        "correlationId": "<uuid>",
        "runId":         "<uuid>",
        "claimId":       "<str>",
        "analysisInput": { <AnalysisInput dict> },
        "rulesDecision": { ... },
        "slaResult":     { ... },
        "documents":     [ { "documentId": "<str>", "title": "<str>", "text": "<str>" } ]  (optional)
    }

Output (merged):
    {
        ...previous,
        "evidenceReport": {
            "documents": [...],
            "summary":   "<str>",
            "checks":    [ { "checkId", "label", "verdict", "sourceRef", "note" } ],
            "stubbed":   false
        }
    }
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
import boto3

from shared import gemini
from shared.logging import get_logger, set_correlation_id
from shared.models import EvidenceReport

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
USE_STUBS: bool = os.environ.get("USE_STUBS", "false").lower() in ("true", "1", "yes")

STANDARD_CHECKS = [
    {"checkId": "kyc_present", "label": "KYC present"},
    {"checkId": "bank_details_present", "label": "Bank details present"},
    {"checkId": "date_of_exit_present", "label": "Date of exit present"},
    {"checkId": "deficiency_communication_present", "label": "Deficiency communication present"},
    {"checkId": "claim_amount_consistent", "label": "Claim amount consistent"},
]


def _put_downgrade_metric() -> None:
    """Emit EPFSentinel/EvidenceDowngrade metric to CloudWatch."""
    try:
        region = os.environ.get("AWS_REGION", "ap-south-1")
        cw = boto3.client("cloudwatch", region_name=region)
        cw.put_metric_data(
            Namespace="EPFSentinel",
            MetricData=[
                {
                    "MetricName": "EvidenceDowngrade",
                    "Value": 1,
                    "Unit": "Count",
                }
            ],
        )
        log.info("cloudwatch.metric.published", metricName="EvidenceDowngrade", namespace="EPFSentinel")
    except Exception as exc:
        log.warning("cloudwatch.metric.failed", error=str(exc))


def _stub_evidence_report(claim_id: str) -> EvidenceReport:
    """Return stub evidence report when USE_STUBS=true."""
    return EvidenceReport(
        documents=[
            {
                "documentId": "doc-stub-1",
                "title": "EPF Form-19 Settlement Guidelines",
                "url": "https://www.epfindia.gov.in/stub/form19-guidelines",
                "relevanceScore": 0.95,
            }
        ],
        summary=f"[STUB] Retrieved 1 document for claim {claim_id}.",
        checks=[
            {
                "checkId": c["checkId"],
                "label": c["label"],
                "verdict": "CONFIRMED",
                "sourceRef": {
                    "documentId": "doc-stub-1",
                    "excerpt": "Settlement Time as per Scheme is 20 Days.",
                },
                "note": "Stub check confirmed.",
            }
            for c in STANDARD_CHECKS
        ],
        stubbed=True,
    )


def _evaluate_evidence_with_gemini(
    analysis_input: dict[str, Any],
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Call Gemini to evaluate 5 standard checks against uploaded document texts."""
    if not documents:
        # Zero evidence case: all checks NOT_FOUND
        checks = [
            {
                "checkId": c["checkId"],
                "label": c["label"],
                "verdict": "NOT_FOUND",
                "sourceRef": None,
                "note": "No documents provided to evaluate check.",
            }
            for c in STANDARD_CHECKS
        ]
        return checks, "No documents provided; all evidence checks marked NOT_FOUND."

    prompt = (
        "You are an evidence verification assistant for EPF claim analysis.\n"
        f"Claim Analysis Input: {json.dumps(analysis_input, indent=2)}\n\n"
        f"Uploaded Documents Text:\n{json.dumps(documents, indent=2)}\n\n"
        "Evaluate each of the following 5 standard checks:\n"
        "1. kyc_present (KYC present)\n"
        "2. bank_details_present (Bank details present)\n"
        "3. date_of_exit_present (Date of exit present)\n"
        "4. deficiency_communication_present (Deficiency communication present)\n"
        "5. claim_amount_consistent (Claim amount consistent)\n\n"
        "For each check, assign a verdict:\n"
        "- CONFIRMED: document text contains exact verbatim evidence supporting the check.\n"
        "- NOT_FOUND: document text does not contain evidence for the check.\n"
        "- CONTRADICTED: document text explicitly contradicts the claim or check.\n\n"
        "CRITICAL REQUIREMENT for CONFIRMED/CONTRADICTED: You MUST include sourceRef object with documentId "
        "and the EXACT LITERAL SUBSTRING excerpt from the document text that you relied on.\n"
        "If verdict is NOT_FOUND, sourceRef MUST be null.\n"
    )

    schema = {
        "type": "OBJECT",
        "properties": {
            "summary": {"type": "STRING"},
            "checks": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "checkId": {"type": "STRING"},
                        "label": {"type": "STRING"},
                        "verdict": {"type": "STRING"},
                        "sourceRef": {
                            "type": "OBJECT",
                            "properties": {
                                "documentId": {"type": "STRING"},
                                "excerpt": {"type": "STRING"},
                            },
                            "nullable": True,
                        },
                        "note": {"type": "STRING"},
                    },
                    "required": ["checkId", "label", "verdict", "note"],
                },
            },
        },
        "required": ["summary", "checks"],
    }

    response_text = gemini.generate(
        prompt=prompt,
        system_instruction="Analyze document evidence strictly. Verbatim excerpts are mandatory for CONFIRMED.",
        response_schema=schema,
    )

    try:
        parsed = json.loads(response_text)
        checks = parsed.get("checks", [])
        summary = parsed.get("summary", "Evidence analysis completed.")
    except Exception:
        checks = []
        summary = "Failed to parse model evidence response."

    # Ensure all 5 standard checks exist
    check_map = {c["checkId"]: c for c in checks}
    final_checks = []
    for sc in STANDARD_CHECKS:
        c_id = sc["checkId"]
        if c_id in check_map:
            final_checks.append(check_map[c_id])
        else:
            final_checks.append(
                {
                    "checkId": c_id,
                    "label": sc["label"],
                    "verdict": "NOT_FOUND",
                    "sourceRef": None,
                    "note": "Check omitted by model; defaulted to NOT_FOUND.",
                }
            )

    return final_checks, summary


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for EvidenceAgent."""
    use_stubs = os.environ.get("USE_STUBS", "false").lower() in ("true", "1", "yes")
    correlation_id: str = event.get("correlationId", "")
    set_correlation_id(correlation_id)

    run_id: str = event.get("runId", "")
    claim_id: str = event.get("claimId", "")

    log.info(
        "evidence_agent.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        useStubs=use_stubs,
        stage=STAGE,
    )

    analysis_input: dict = event.get("analysisInput", {})
    documents: list[dict[str, Any]] = event.get("documents", []) or event.get("evidenceDocuments", [])

    if use_stubs:
        report = _stub_evidence_report(claim_id)
    else:
        raw_checks, summary = _evaluate_evidence_with_gemini(analysis_input, documents)

        # Build document text lookup for verbatim substring validation
        doc_text_lookup = {}
        for d in documents:
            did = d.get("documentId") or d.get("id") or ""
            dtext = d.get("text", "")
            if did:
                doc_text_lookup[did] = dtext
            # also accumulate total text
            doc_text_lookup["__all__"] = doc_text_lookup.get("__all__", "") + " " + dtext

        validated_checks = []
        for check in raw_checks:
            check_id = check.get("checkId", "")
            label = check.get("label", "")
            verdict = check.get("verdict", "NOT_FOUND").upper()
            source_ref = check.get("sourceRef")
            note = check.get("note", "")

            # Enforce distinct verdict strings
            if verdict not in ("CONFIRMED", "NOT_FOUND", "CONTRADICTED"):
                verdict = "NOT_FOUND"

            # HARD RULE: CONFIRMED verdict is ONLY permitted when sourceRef is non-null AND excerpt is literal substring
            if verdict == "CONFIRMED":
                has_valid_excerpt = False
                if source_ref and isinstance(source_ref, dict):
                    doc_id = source_ref.get("documentId", "")
                    excerpt = source_ref.get("excerpt", "")
                    if excerpt:
                        target_text = doc_text_lookup.get(doc_id, doc_text_lookup.get("__all__", ""))
                        if excerpt in target_text:
                            has_valid_excerpt = True

                if not has_valid_excerpt:
                    log.warning(
                        "evidence_agent.verdict.downgraded",
                        correlationId=correlation_id,
                        checkId=check_id,
                        originalVerdict="CONFIRMED",
                        newVerdict="NOT_FOUND",
                        reason="sourceRef excerpt missing or not a literal substring of document text",
                        sourceRef=source_ref,
                    )
                    verdict = "NOT_FOUND"
                    source_ref = None
                    note = f"{note} (Downgraded from CONFIRMED to NOT_FOUND due to non-verbatim sourceRef excerpt)"
                    _put_downgrade_metric()

            validated_checks.append(
                {
                    "checkId": check_id,
                    "label": label,
                    "verdict": verdict,
                    "sourceRef": source_ref if verdict != "NOT_FOUND" else (source_ref if verdict == "CONTRADICTED" else None),
                    "note": note,
                }
            )

        report = EvidenceReport(
            documents=documents,
            summary=summary,
            checks=validated_checks,
            stubbed=False,
        )

    log.info(
        "evidence_agent.completed",
        correlationId=correlation_id,
        claimId=claim_id,
        documentCount=len(report.documents),
        checksCount=len(report.checks),
    )

    return {
        **event,
        "evidenceReport": report.to_dict(),
    }
