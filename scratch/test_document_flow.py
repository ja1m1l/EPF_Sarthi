"""
Ad-hoc end-to-end check of the document -> evidence path against dev.

Signs in as the demo Cognito user, creates a claim, uploads a screenshot,
waits for extraction, runs the analysis, and prints the evidence report.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

import boto3

REGION = "ap-south-1"
API = "https://a2n6k097vi.execute-api.ap-south-1.amazonaws.com/dev"
CLIENT_ID = "7d071mqvr6st1e82ej7r0phqmq"
EMAIL = "demo@epfsentinel.test"
PASSWORD = "DemoSentinel#2026"
SCREENSHOT = "tests/assets/epfo_claim_screenshot.png"


def id_token() -> str:
    idp = boto3.client("cognito-idp", region_name=REGION)
    resp = idp.initiate_auth(
        ClientId=CLIENT_ID,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": EMAIL, "PASSWORD": PASSWORD},
    )
    return resp["AuthenticationResult"]["IdToken"]


def call(token: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={"Authorization": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def main() -> int:
    token = id_token()

    claim = call(token, "POST", "/claims", {
        "claimType": "FINAL_SETTLEMENT",
        "claimDate": "2026-08-01",
        "amountRupees": "480000",
        "status": "PENDING",
    })
    claim_id = claim["claimId"]
    print(f"claim {claim_id}  amountPaise={claim['amountPaise']}")

    presigned = call(token, "POST", f"/claims/{claim_id}/documents", {"contentType": "image/png"})
    document_id = presigned["documentId"]
    print(f"document {document_id}")

    with open(SCREENSHOT, "rb") as fh:
        payload = fh.read()
    put = urllib.request.Request(
        presigned["uploadUrl"],
        data=payload,
        method="PUT",
        headers={"Content-Type": "image/png"},
    )
    with urllib.request.urlopen(put) as resp:
        print(f"S3 PUT -> {resp.status}")

    for _ in range(45):
        time.sleep(2)
        doc = call(token, "GET", f"/claims/{claim_id}/documents/{document_id}?includeText=true")
        if doc.get("status") != "PROCESSING":
            break
    print(f"extraction status: {doc.get('status')}  method={doc.get('extractionMethod')}")
    print(f"extractedText[:300]: {str(doc.get('extractedText'))[:300]}")

    run = call(token, "POST", f"/claims/{claim_id}/analyze")
    run_id = run["runId"]
    print(f"run {run_id}")

    for _ in range(45):
        time.sleep(2)
        result = call(token, "GET", f"/claims/{claim_id}/runs/{run_id}")
        if result.get("status") != "RUNNING":
            break

    print(f"run status: {result.get('status')}")
    report = result.get("evidenceAgentOutputJson") or {}
    print(f"documents seen by EvidenceAgent: {len(report.get('documents', []))}")
    for check in report.get("checks", []):
        ref = check.get("sourceRef")
        excerpt = (ref or {}).get("excerpt", "")
        print(f"  {check['verdict']:<13} {check['label']:<36} excerpt={excerpt[:70]!r}")

    print(f"\nclaim url: /claims/{claim_id}?runId={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
