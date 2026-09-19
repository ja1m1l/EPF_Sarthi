"""
Live end-to-end verification script for Module 2.5 (Document Intake).

Executes all 5 required verification steps against real AWS resources:
1. Raw presigned URL response + actual PUT upload with response code.
2. Real claim screenshot upload -> SQS -> Lambda extraction -> raw DynamoDB document item with full extractedText.
3. Raw SQS and DLQ message counts before and after corrupt-file test + persisted status.
4. Deliberately blank image extraction -> status NEEDS_MANUAL_ENTRY(INSUFFICIENT_TEXT).
5. Cross-user test transcript with real HTTP status codes.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone

import boto3
import requests

REGION = "ap-south-1"
STAGE = "dev"
STACK_NAME = f"epf-sentinel-{STAGE}"


def get_stack_outputs() -> dict[str, str]:
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK_NAME)
    outputs = {}
    for o in resp["Stacks"][0].get("Outputs", []):
        outputs[o["OutputKey"]] = o["OutputValue"]
    return outputs


def create_cognito_user(idp, pool_id: str, client_id: str, email_prefix: str) -> tuple[str, str, str]:
    """Create a confirmed test user and return (email, password, access_token)."""
    alphabet = string.ascii_letters + string.digits
    password = "T3st!" + "".join(secrets.choice(alphabet) for _ in range(12))
    email = f"{email_prefix}-{secrets.token_hex(4)}@example.com"

    idp.admin_create_user(
        UserPoolId=pool_id,
        Username=email,
        TemporaryPassword=password,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    idp.admin_set_user_password(
        UserPoolId=pool_id,
        Username=email,
        Password=password,
        Permanent=True,
    )
    auth_resp = idp.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
        ClientId=client_id,
    )
    token = auth_resp["AuthenticationResult"]["AccessToken"]
    return email, password, token


def delete_cognito_user(idp, pool_id: str, email: str):
    try:
        idp.admin_delete_user(UserPoolId=pool_id, Username=email)
    except Exception:
        pass


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_claim(api_endpoint: str, token: str) -> str:
    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    resp = requests.post(
        f"{api_endpoint}/claims",
        json={
            "claimType": "FINAL_SETTLEMENT",
            "claimDateIso": today_iso,
            "amountPaise": 14850000,
            "status": "SUBMITTED",
        },
        headers=auth_headers(token),
        timeout=10,
    )
    assert resp.status_code == 201, f"Create claim failed: {resp.status_code} {resp.text}"
    return resp.json()["claimId"]


def get_queue_counts(sqs, queue_url: str) -> dict[str, int]:
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )["Attributes"]
    return {
        "visible": int(attrs.get("ApproximateNumberOfMessages", 0)),
        "not_visible": int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        "delayed": int(attrs.get("ApproximateNumberOfMessagesDelayed", 0)),
    }


def main():
    print("=" * 70)
    print("EPF SENTINEL - MODULE 2.5 LIVE END-TO-END VERIFICATION")
    print("=" * 70)

    outputs = get_stack_outputs()
    api_endpoint = outputs["ApiEndpoint"].rstrip("/")
    pool_id = outputs["CognitoUserPoolId"]
    client_id = outputs["CognitoUserPoolClientId"]
    docs_table = outputs["DocumentsTableName"]
    doc_queue_url = outputs["DocumentEventQueueUrl"]
    doc_dlq_url = outputs["DocumentEventDLQUrl"]

    idp = boto3.client("cognito-idp", region_name=REGION)
    ddb = boto3.client("dynamodb", region_name=REGION)
    sqs = boto3.client("sqs", region_name=REGION)

    print(f"Stack API:       {api_endpoint}")
    print(f"Documents Table: {docs_table}")
    print(f"Document Queue:  {doc_queue_url}")
    print(f"Document DLQ:    {doc_dlq_url}")

    # Create User A and User B
    print("\n[Setup] Creating real test users in Cognito...")
    email_a, pass_a, token_a = create_cognito_user(idp, pool_id, client_id, "test-user-a")
    email_b, pass_b, token_b = create_cognito_user(idp, pool_id, client_id, "test-user-b")
    print(f"User A: {email_a}")
    print(f"User B: {email_b}")

    try:
        # Create claim for User A
        claim_id_a = create_claim(api_endpoint, token_a)
        print(f"Created Claim for User A: {claim_id_a}")

        # ---------------------------------------------------------------------
        # 1. CROSS-USER TEST
        # ---------------------------------------------------------------------
        print("\n" + "=" * 50)
        print("STEP 1: CROSS-USER SECURITY TEST")
        print("=" * 50)

        # 1.1 User B attempts to presign upload for User A's claim
        resp_b_presign = requests.post(
            f"{api_endpoint}/claims/{claim_id_a}/documents",
            json={"contentType": "image/png"},
            headers=auth_headers(token_b),
            timeout=10,
        )
        print(f"User B presign User A claim -> HTTP Status: {resp_b_presign.status_code}")
        print(f"Response body: {resp_b_presign.text.strip()}")
        assert resp_b_presign.status_code == 404, f"Expected 404, got {resp_b_presign.status_code}"

        # 1.2 User A presigns upload
        resp_a_presign = requests.post(
            f"{api_endpoint}/claims/{claim_id_a}/documents",
            json={"contentType": "image/png"},
            headers=auth_headers(token_a),
            timeout=10,
        )
        print(f"\nUser A presign own claim -> HTTP Status: {resp_a_presign.status_code}")
        doc_a_data = resp_a_presign.json()
        doc_id_a = doc_a_data["documentId"]
        upload_url = doc_a_data["url"]
        print(f"Generated documentId: {doc_id_a}")

        # 1.3 User B attempts to fetch User A's document record
        resp_b_get = requests.get(
            f"{api_endpoint}/claims/{claim_id_a}/documents/{doc_id_a}",
            headers=auth_headers(token_b),
            timeout=10,
        )
        print(f"\nUser B get User A document -> HTTP Status: {resp_b_get.status_code}")
        print(f"Response body: {resp_b_get.text.strip()}")
        assert resp_b_get.status_code == 404, f"Expected 404, got {resp_b_get.status_code}"

        # ---------------------------------------------------------------------
        # 2. RAW PRESIGNED URL RESPONSE & ACTUAL CURL PUT
        # ---------------------------------------------------------------------
        print("\n" + "=" * 50)
        print("STEP 2: PRESIGNED URL & CURL PUT UPLOAD")
        print("=" * 50)

        # Redact signature for presentation
        redacted_data = dict(doc_a_data)
        # Redact signature param if present in url query
        parsed_url = urllib.parse.urlparse(upload_url)
        q_params = urllib.parse.parse_qs(parsed_url.query)
        for sig_key in ["X-Amz-Signature", "Signature"]:
            if sig_key in q_params:
                q_params[sig_key] = ["[REDACTED_SIGNATURE]"]
        redacted_query = urllib.parse.urlencode(q_params, doseq=True)
        redacted_url = urllib.parse.urlunparse(parsed_url._replace(query=redacted_query))
        redacted_data["url"] = redacted_url
        if "uploadUrl" in redacted_data:
            redacted_data["uploadUrl"] = redacted_url
        if "fields" in redacted_data and isinstance(redacted_data["fields"], dict):
            fields_copy = dict(redacted_data["fields"])
            for k in ["signature", "policy"]:
                if k in fields_copy:
                    fields_copy[k] = "[REDACTED]"
            redacted_data["fields"] = fields_copy

        print("Raw Presigned URL Response (Signature Redacted):")
        print(json.dumps(redacted_data, indent=2))

        # Perform actual upload with curl command via subprocess
        screenshot_path = os.path.abspath("tests/assets/epfo_claim_screenshot.png")
        print(f"\nUploading screenshot file: {screenshot_path}")

        # Execute actual curl command
        curl_cmd = [
            "curl", "-s", "-w", "\\nHTTP_STATUS:%{http_code}\\n",
            "-X", "PUT",
            "-H", "Content-Type: image/png",
            "--upload-file", screenshot_path,
            upload_url,
        ]
        curl_res = subprocess.run(curl_cmd, capture_output=True, text=True)
        print(f"curl stdout:\n{curl_res.stdout.strip()}")
        if curl_res.stderr:
            print(f"curl stderr:\n{curl_res.stderr.strip()}")

        # ---------------------------------------------------------------------
        # 3. REAL CLAIM SCREENSHOT EXTRACTION & RAW DYNAMODB ITEM
        # ---------------------------------------------------------------------
        print("\n" + "=" * 50)
        print("STEP 3: REAL CLAIM SCREENSHOT EXTRACTION & DYNAMODB ITEM")
        print("=" * 50)
        print(f"Waiting for async SQS -> ExtractDocumentFunction processing for {doc_id_a}...")

        # Poll DynamoDB
        ddb_item = None
        for attempt in range(25):
            time.sleep(3)
            resp = ddb.get_item(
                TableName=docs_table,
                Key={"userId": {"S": email_a}, "documentId": {"S": doc_id_a}},
            )
            item = resp.get("Item", {})
            status = item.get("status", {}).get("S", "")
            print(f"  Attempt {attempt + 1}: status = {status}")
            if status in ("EXTRACTED", "NEEDS_MANUAL_ENTRY"):
                ddb_item = item
                break

        assert ddb_item is not None, "Extraction timed out"
        print("\nRaw DynamoDB Document Item:")
        print(json.dumps(ddb_item, indent=2))

        # ---------------------------------------------------------------------
        # 4. DELIBERATELY BLANK IMAGE EXTRACTION
        # ---------------------------------------------------------------------
        print("\n" + "=" * 50)
        print("STEP 4: BLANK IMAGE EXTRACTION (INSUFFICIENT TEXT)")
        print("=" * 50)

        blank_path = os.path.abspath("tests/assets/blank_document.png")
        presign_blank = requests.post(
            f"{api_endpoint}/claims/{claim_id_a}/documents",
            json={"contentType": "image/png"},
            headers=auth_headers(token_a),
            timeout=10,
        ).json()
        doc_id_blank = presign_blank["documentId"]
        blank_upload_url = presign_blank["url"]
        print(f"Blank doc documentId: {doc_id_blank}")

        # Upload blank image
        put_blank = requests.put(
            blank_upload_url,
            data=open(blank_path, "rb").read(),
            headers={"Content-Type": "image/png"},
            timeout=15,
        )
        print(f"Blank image upload response code: {put_blank.status_code}")

        # Poll DynamoDB for blank document
        print(f"Waiting for processing of blank document {doc_id_blank}...")
        blank_ddb_item = None
        for attempt in range(25):
            time.sleep(3)
            resp = ddb.get_item(
                TableName=docs_table,
                Key={"userId": {"S": email_a}, "documentId": {"S": doc_id_blank}},
            )
            item = resp.get("Item", {})
            status = item.get("status", {}).get("S", "")
            print(f"  Attempt {attempt + 1}: status = {status}")
            if status in ("EXTRACTED", "NEEDS_MANUAL_ENTRY"):
                blank_ddb_item = item
                break

        assert blank_ddb_item is not None, "Blank doc extraction timed out"
        print("\nRaw DynamoDB Blank Document Item:")
        print(json.dumps(blank_ddb_item, indent=2))

        # ---------------------------------------------------------------------
        # 5. CORRUPT FILE TEST & SQS / DLQ MESSAGE COUNTS
        # ---------------------------------------------------------------------
        print("\n" + "=" * 50)
        print("STEP 5: CORRUPT FILE TEST & SQS / DLQ MESSAGE COUNTS")
        print("=" * 50)

        counts_before_queue = get_queue_counts(sqs, doc_queue_url)
        counts_before_dlq = get_queue_counts(sqs, doc_dlq_url)
        print(f"DocumentEventQueue counts BEFORE: {counts_before_queue}")
        print(f"DocumentEventDLQ counts BEFORE:    {counts_before_dlq}")

        corrupt_path = os.path.abspath("tests/assets/corrupt_document.pdf")
        presign_corrupt = requests.post(
            f"{api_endpoint}/claims/{claim_id_a}/documents",
            json={"contentType": "application/pdf"},
            headers=auth_headers(token_a),
            timeout=10,
        ).json()
        doc_id_corrupt = presign_corrupt["documentId"]
        corrupt_upload_url = presign_corrupt["url"]
        print(f"Corrupt doc documentId: {doc_id_corrupt}")

        # Upload corrupt file
        put_corrupt = requests.put(
            corrupt_upload_url,
            data=open(corrupt_path, "rb").read(),
            headers={"Content-Type": "application/pdf"},
            timeout=15,
        )
        print(f"Corrupt file upload response code: {put_corrupt.status_code}")

        # Poll DynamoDB for corrupt document status
        print(f"Waiting for corrupt doc {doc_id_corrupt} to be marked NEEDS_MANUAL_ENTRY...")
        corrupt_ddb_item = None
        for attempt in range(25):
            time.sleep(3)
            resp = ddb.get_item(
                TableName=docs_table,
                Key={"userId": {"S": email_a}, "documentId": {"S": doc_id_corrupt}},
            )
            item = resp.get("Item", {})
            status = item.get("status", {}).get("S", "")
            print(f"  Attempt {attempt + 1}: status = {status}")
            if status in ("EXTRACTED", "NEEDS_MANUAL_ENTRY"):
                corrupt_ddb_item = item
                break

        assert corrupt_ddb_item is not None, "Corrupt doc extraction timed out"
        print("\nRaw DynamoDB Corrupt Document Item:")
        print(json.dumps(corrupt_ddb_item, indent=2))

        # Check SQS and DLQ counts after retries
        print("\nChecking DLQ message count (polling up to 60s for SQS redrive to DLQ)...")
        counts_after_dlq = counts_before_dlq
        for _ in range(12):
            counts_after_queue = get_queue_counts(sqs, doc_queue_url)
            counts_after_dlq = get_queue_counts(sqs, doc_dlq_url)
            print(f"  Queue: {counts_after_queue} | DLQ: {counts_after_dlq}")
            if counts_after_dlq["visible"] > counts_before_dlq["visible"]:
                break
            time.sleep(5)

        print(f"\nFinal DocumentEventQueue counts AFTER: {counts_after_queue}")
        print(f"Final DocumentEventDLQ counts AFTER:    {counts_after_dlq}")

    finally:
        print("\n[Teardown] Cleaning up test users from Cognito...")
        delete_cognito_user(idp, pool_id, email_a)
        delete_cognito_user(idp, pool_id, email_b)
        print("Teardown complete.")


if __name__ == "__main__":
    main()
