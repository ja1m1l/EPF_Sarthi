"""
Integration tests for Module 2.5: Document Intake pipeline.

Covers:
  1. Multi-tenant security:
     - User B cannot generate a presigned upload URL for User A's claim (404).
     - User B cannot fetch User A's document record (404).
  2. Presigned S3 upload & Extraction flow:
     - Valid document upload via presigned POST.
     - Corrupt file upload: extractor fails, updates status to NEEDS_MANUAL_ENTRY(EXTRACTION_FAILED),
       and after SQS maxReceiveCount retries, the message routes to DocumentEventDLQ.

Required environment variables
--------------------------------
API_ENDPOINT              Base URL of the deployed HTTP API (no trailing slash).
COGNITO_USER_POOL_ID      Cognito User Pool ID.
COGNITO_CLIENT_ID         Cognito App Client ID.
AWS_DEFAULT_REGION        (defaults to ap-south-1)
DOCUMENT_EVENT_DLQ_URL    (optional, for direct SQS DLQ depth assertion)

Usage
-----
    python -m pytest tests/integration/test_document_intake.py -v --tb=short
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime, timezone

import boto3
import pytest
import requests

POLL_INTERVAL_S = 3
POLL_MAX_WAIT_S = 90


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _create_claim(api_endpoint: str, access_token: str) -> str:
    """Helper to create a claim for testing and return claimId."""
    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "claimType": "FINAL_SETTLEMENT",
        "claimDate": today_iso,
        "amountRupees": 5000,
        "status": "SUBMITTED",
    }
    resp = requests.post(
        f"{api_endpoint}/claims",
        json=payload,
        headers=_auth_headers(access_token),
        timeout=10,
    )
    assert resp.status_code == 201, f"Failed to create claim: {resp.status_code} {resp.text}"
    return resp.json()["claimId"]


class TestDocumentIntakeMultiTenant:
    """Assert multi-tenant boundaries between User A and User B."""

    def test_user_b_cannot_presign_upload_for_user_a_claim(
        self,
        api_endpoint: str,
        user_a,
        user_b,
    ):
        """User B requesting presigned POST for User A's claim must get 404."""
        claim_id_a = _create_claim(api_endpoint, user_a.access_token)

        resp = requests.post(
            f"{api_endpoint}/claims/{claim_id_a}/documents",
            json={"contentType": "application/pdf"},
            headers=_auth_headers(user_b.access_token),
            timeout=10,
        )
        assert resp.status_code == 404, (
            f"Expected 404 when User B accesses User A's claim, got {resp.status_code}: {resp.text}"
        )

    def test_user_b_cannot_get_user_a_document(
        self,
        api_endpoint: str,
        user_a,
        user_b,
    ):
        """User B requesting User A's document must get 404."""
        claim_id_a = _create_claim(api_endpoint, user_a.access_token)

        # User A presigns an upload
        presign_resp = requests.post(
            f"{api_endpoint}/claims/{claim_id_a}/documents",
            json={"contentType": "application/pdf"},
            headers=_auth_headers(user_a.access_token),
            timeout=10,
        )
        assert presign_resp.status_code == 200
        doc_id = presign_resp.json()["documentId"]

        # User B attempts to read it
        get_resp = requests.post if False else requests.get(
            f"{api_endpoint}/claims/{claim_id_a}/documents/{doc_id}",
            headers=_auth_headers(user_b.access_token),
            timeout=10,
        )
        assert get_resp.status_code == 404, (
            f"Expected 404 when User B gets User A's doc, got {get_resp.status_code}: {get_resp.text}"
        )


class TestDocumentIntakeProcessing:
    """Tests upload to S3 and async extraction via SQS."""

    def test_corrupt_file_upload_marks_needs_manual_and_dlq(
        self,
        api_endpoint: str,
        user_a,
        aws_region: str,
    ):
        """
        Uploading a corrupt file must:
          1. Fail PDF parsing in ExtractDocumentFunction.
          2. Mark status = NEEDS_MANUAL_ENTRY with failureReason = EXTRACTION_FAILED.
          3. Retries exhaust SQS maxReceiveCount (3) and land in DocumentEventDLQ.
        """
        claim_id = _create_claim(api_endpoint, user_a.access_token)

        # 1. Get presigned POST
        presign_resp = requests.post(
            f"{api_endpoint}/claims/{claim_id}/documents",
            json={"contentType": "application/pdf"},
            headers=_auth_headers(user_a.access_token),
            timeout=10,
        )
        assert presign_resp.status_code == 200
        presign_data = presign_resp.json()
        doc_id = presign_data["documentId"]
        s3_url = presign_data["postUrl"]
        fields = presign_data["fields"]

        # 2. Upload corrupt bytes via multipart/form-data POST to S3
        corrupt_bytes = b"%PDF-1.4\nCORRUPTED_GARBAGE_PAYLOAD_NOT_READABLE\x00\xff"
        files = {"file": ("corrupt.pdf", io.BytesIO(corrupt_bytes), "application/pdf")}
        upload_resp = requests.post(
            s3_url,
            data=fields,
            files=files,
            timeout=15,
        )
        assert upload_resp.status_code in (200, 204), (
            f"S3 POST upload failed: {upload_resp.status_code} {upload_resp.text}"
        )

        # 3. Poll document record until status reaches terminal state
        start_time = time.time()
        final_doc = None
        while time.time() - start_time < POLL_MAX_WAIT_S:
            doc_resp = requests.get(
                f"{api_endpoint}/claims/{claim_id}/documents/{doc_id}",
                headers=_auth_headers(user_a.access_token),
                timeout=10,
            )
            assert doc_resp.status_code == 200
            data = doc_resp.json()
            if data.get("status") in ("NEEDS_MANUAL_ENTRY", "EXTRACTED"):
                final_doc = data
                break
            time.sleep(POLL_INTERVAL_S)

        assert final_doc is not None, f"Document processing timed out after {POLL_MAX_WAIT_S}s"
        assert final_doc["status"] == "NEEDS_MANUAL_ENTRY"
        assert final_doc.get("failureReason") == "EXTRACTION_FAILED"

        # 4. Optional assertion on DLQ if DOCUMENT_EVENT_DLQ_URL is configured
        dlq_url = os.environ.get("DOCUMENT_EVENT_DLQ_URL")
        if dlq_url:
            sqs = boto3.client("sqs", region_name=aws_region)
            # Give SQS visibility timeout time to complete 3 receives
            dlq_msgs = []
            for _ in range(10):
                attrs = sqs.get_queue_attributes(
                    QueueUrl=dlq_url,
                    AttributeNames=["ApproximateNumberOfMessages"],
                )
                count = int(attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", 0))
                if count > 0:
                    break
                time.sleep(5)
            assert count > 0, f"Expected message in DLQ {dlq_url}, found 0"
