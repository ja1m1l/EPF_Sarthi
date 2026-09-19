"""
Demonstration Suite for Module 3.2 EPF Sentinel Gemini-Backed Agents.
Runs all 4 scenarios requested by the user and prints detailed literal outputs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ["GEMINI_SECRET_NAME"] = "epf-sentinel/gemini-api-key"
os.environ["AWS_REGION"] = "ap-south-1"
os.environ["USE_STUBS"] = "false"

from functions.claim_agent import app as claim_agent
from functions.evidence_agent import app as evidence_agent
from functions.grievance_agent import app as grievance_agent
from shared.models import AnalysisRun, utc_now_iso


def run_demo_claim():
    print("=" * 80)
    print("SCENARIO 1: DEMO CLAIM (HAPPY PATH)")
    print("=" * 80)

    correlation_id = "demo-corr-uuid-001"
    run_id = "demo-run-uuid-001"
    claim_id = "claim-demo-999"

    input_event = {
        "correlationId": correlation_id,
        "runId": run_id,
        "claimId": claim_id,
        "claim": {
            "userId": "user-demo-42",
            "claimId": claim_id,
            "claimType": "FINAL_SETTLEMENT",
            "claimDateIso": "2024-10-15",
            "amountPaise": 100000,
            "status": "SUBMITTED",
        },
    }

    # Step 1: ClaimAgent
    out1 = claim_agent.handler(input_event, None)
    
    # Step 2: Rules (simulated decision for pipeline)
    rules_decision = {
        "applicable": True,
        "timelineDays": 20,
        "timelineBasis": "CALENDAR",
        "charterTargetDays": 7,
        "citedChunkIds": ["chunk-rule-19-01"],
        "citedSourceUrls": ["https://www.epfindia.gov.in/site_docs/PDFs/Downloads_PDFs/EPF_Scheme_1952.pdf"],
        "quotedSpan": "Settlement Time as per Scheme is 20 Days.",
        "confidence": "HIGH",
        "abstainReason": None,
    }
    out2 = {**out1, "rulesDecision": rules_decision}

    # Step 3: SlaAgent (simulated SLA output)
    sla_result = {
        "claimId": claim_id,
        "skipped": False,
        "timelineDays": 20,
        "timelineBasis": "CALENDAR",
        "claimDateIso": "2024-10-15",
        "deadlineDateIso": "2024-11-04",
        "elapsedCalendarDays": 35,
        "remainingCalendarDays": -15,
        "status": "OVERDUE",
    }
    out3 = {**out2, "slaResult": sla_result}

    # Step 4: EvidenceAgent (Demo document)
    doc = {
        "documentId": "doc-form19-pdf",
        "title": "Form 19 Claim Acknowledgement",
        "text": (
            "EPFO Final Settlement Claim Acknowledgement\n"
            "Claim ID: claim-demo-999\n"
            "Claim Type: FINAL_SETTLEMENT\n"
            "Claim Date: 2024-10-15\n"
            "Aadhaar & PAN KYC details verified successfully.\n"
            "Bank details (State Bank of India A/C 98765432101) verified.\n"
            "Date of Exit: 2024-09-30 recorded.\n"
            "No deficiency communication issued.\n"
            "Claim Amount: INR 1,000.00 confirmed."
        ),
    }
    out4 = evidence_agent.handler({**out3, "documents": [doc]}, None)

    # Step 5: GrievanceAgent
    out5 = grievance_agent.handler(out4, None)

    # Persisted AnalysisRun snapshot
    analysis_run = AnalysisRun(
        claimId=claim_id,
        runId=run_id,
        correlationId=correlation_id,
        status="COMPLETED",
        stepFunctionExecutionArn="arn:aws:states:ap-south-1:814454905497:execution:epf-sentinel-analysis-dev:demo-exec-001",
        claimAgentOutputJson=json.dumps(out5["analysisInput"]),
        rulesAgentOutputJson=json.dumps(out5["rulesDecision"]),
        slaAgentOutputJson=json.dumps(out5["slaResult"]),
        evidenceAgentOutputJson=json.dumps(out5["evidenceReport"]),
        grievanceAgentOutputJson=json.dumps(out5["grievanceDraft"]),
        finishedAt=utc_now_iso(),
        expiresAt=1800000000,
    )

    print("\n--- COMPLETE RAW AnalysisRun ITEM ---")
    print(json.dumps(analysis_run.to_dict(), indent=2))

    print("\n--- GENERATED GRIEVANCE DRAFT IN FULL ---")
    print(out5["grievanceDraft"]["draftText"])

    print("\n--- STRUCTURED RUN DATA NEXT TO DRAFT ---")
    print("analysisInput:", json.dumps(out5["analysisInput"], indent=2))
    print("rulesDecision:", json.dumps(out5["rulesDecision"], indent=2))
    print("slaResult:", json.dumps(out5["slaResult"], indent=2))
    print("evidenceReport:", json.dumps(out5["evidenceReport"], indent=2))

    return out5


def run_contradicting_document_scenario():
    print("\n" + "=" * 80)
    print("SCENARIO 2: DELIBERATELY CONTRADICTING DOCUMENT")
    print("=" * 80)

    correlation_id = "contradict-corr-uuid-002"
    run_id = "contradict-run-uuid-002"
    claim_id = "claim-contradict-123"

    analysis_input = {
        "claimId": claim_id,
        "userId": "user-demo-42",
        "claimType": "FINAL_SETTLEMENT",
        "claimDateIso": "2024-10-15",
        "amountPaise": 100000, # INR 1,000.00
        "status": "SUBMITTED",
        "deficiencyRaisedDateIso": None,
        "correlationId": correlation_id,
        "normalizedAt": utc_now_iso(),
    }

    # Document explicitly states a conflicting claim amount of INR 50,000.00
    doc = {
        "documentId": "doc-contradict-01",
        "title": "EPFO Field Office Audit Report",
        "text": (
            "EPFO Audit Note for Claim claim-contradict-123:\n"
            "Claimed amount recorded on field office ledger is INR 50,000.00, which differs from submitted amount.\n"
            "Date of Exit: 2024-09-30 recorded.\n"
            "KYC and Bank details verified."
        ),
    }

    event = {
        "correlationId": correlation_id,
        "runId": run_id,
        "claimId": claim_id,
        "analysisInput": analysis_input,
        "documents": [doc],
    }

    res = evidence_agent.handler(event, None)
    print("\n--- EVIDENCE REPORT CHECKS ---")
    print(json.dumps(res["evidenceReport"]["checks"], indent=2))


def run_zero_documents_scenario():
    print("\n" + "=" * 80)
    print("SCENARIO 3: ZERO DOCUMENTS ATTACHED")
    print("=" * 80)

    correlation_id = "zero-docs-corr-uuid-003"
    run_id = "zero-docs-run-uuid-003"
    claim_id = "claim-nodocs-456"

    analysis_input = {
        "claimId": claim_id,
        "userId": "user-demo-42",
        "claimType": "FINAL_SETTLEMENT",
        "claimDateIso": "2024-10-15",
        "amountPaise": 100000,
        "status": "SUBMITTED",
        "deficiencyRaisedDateIso": None,
        "correlationId": correlation_id,
        "normalizedAt": utc_now_iso(),
    }

    event = {
        "correlationId": correlation_id,
        "runId": run_id,
        "claimId": claim_id,
        "analysisInput": analysis_input,
        "documents": [],
    }

    res = evidence_agent.handler(event, None)
    print("\n--- EVIDENCE REPORT CHECKS (ZERO DOCUMENTS) ---")
    print(json.dumps(res["evidenceReport"]["checks"], indent=2))


def run_pii_redaction_scenario():
    print("\n" + "=" * 80)
    print("SCENARIO 4: PII REDACTION IN CODE BEFORE OUTBOUND GEMINI REQUEST")
    print("=" * 80)

    correlation_id = "pii-corr-uuid-004"
    run_id = "pii-run-uuid-004"
    claim_id = "claim-pii-789"

    event = {
        "correlationId": correlation_id,
        "runId": run_id,
        "claimId": claim_id,
        "analysisInput": {
            "claimId": claim_id,
            "userId": "user-demo-42",
            "claimType": "FINAL_SETTLEMENT",
            "claimDateIso": "2024-10-15",
            "amountPaise": 100000,
            "status": "SUBMITTED",
            "deficiencyRaisedDateIso": None,
            "correlationId": correlation_id,
            "normalizedAt": utc_now_iso(),
        },
        "rulesDecision": {
            "applicable": True,
            "timelineDays": 20,
            "timelineBasis": "CALENDAR",
            "citedSourceUrls": ["https://epfindia.gov.in"],
        },
        "slaResult": {"status": "OVERDUE"},
        "evidenceReport": {"checks": []},
        # PII present in free text input fields:
        "uan": "123456789012",
        "bankAccount": "9876543210123",
        "password": "SuperSecretPassword123!",
        "freeTextNotes": "User member UAN 123456789012 and SBI Bank Account 9876543210123 with password=MyPass123!",
    }

    redacted_event = grievance_agent.redact_pii(event)
    print("\n--- CODE-REDACTED OUTBOUND CONTEXT SENT TO GEMINI ---")
    print(json.dumps(redacted_event, indent=2))

    res = grievance_agent.handler(event, None)
    print("\n--- GENERATED GRIEVANCE DRAFT OUTPUT ---")
    print(res["grievanceDraft"]["draftText"])


if __name__ == "__main__":
    run_demo_claim()
    run_contradicting_document_scenario()
    run_zero_documents_scenario()
    run_pii_redaction_scenario()
