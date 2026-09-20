#!/usr/bin/env python3
"""Idempotently seed the EPF Sarthi demo user, claim, and sample document."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import requests
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

REGION = "ap-south-1"
DEMO_EMAIL = "demo@epf-sentinel.example"
DEMO_MARKER = "EPF_SENTINEL_DEMO_V1"
DEMO_DOCUMENT_KIND = "CLAIM_AMOUNT"
TERMINAL_DOCUMENT_STATUSES = {"EXTRACTED", "NEEDS_MANUAL_ENTRY"}


def _stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    response = boto3.client("cloudformation", region_name=region).describe_stacks(
        StackName=stack_name
    )
    return {
        output["OutputKey"]: output["OutputValue"]
        for output in response["Stacks"][0].get("Outputs", [])
    }


def _timed(callable_, *args, **kwargs):
    started = time.monotonic()
    result = callable_(*args, **kwargs)
    return result, round((time.monotonic() - started) * 1000, 2)


def _ensure_user(
    *,
    pool_id: str,
    client_id: str,
    region: str,
    email: str,
    password: str,
) -> tuple[str, str, bool]:
    idp = boto3.client("cognito-idp", region_name=region)
    created = False
    try:
        user = idp.admin_get_user(UserPoolId=pool_id, Username=email)
    except idp.exceptions.UserNotFoundException:
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
        user = idp.admin_get_user(UserPoolId=pool_id, Username=email)
        created = True

    idp.admin_set_user_password(
        UserPoolId=pool_id,
        Username=email,
        Password=password,
        Permanent=True,
    )
    auth = idp.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
        ClientId=client_id,
    )
    attributes = {item["Name"]: item["Value"] for item in user["UserAttributes"]}
    return attributes["sub"], auth["AuthenticationResult"]["IdToken"], created


def _request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: int = 20,
) -> requests.Response:
    response = requests.request(
        method,
        url,
        json=json_body,
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"{method} {url} failed: {response.status_code} {response.text}")
    return response


def _ensure_claim(api_endpoint: str, token: str) -> tuple[dict[str, Any], bool]:
    response = _request("GET", f"{api_endpoint}/claims", token)
    for claim in response.json().get("claims", []):
        if (
            claim.get("notes") == DEMO_MARKER
            and claim.get("claimType") == "FINAL_SETTLEMENT"
            and claim.get("claimDateIso") == "2026-08-01"
            and claim.get("amountPaise") == 48_000_000
            and claim.get("status") == "PENDING"
        ):
            return claim, False

    created = _request(
        "POST",
        f"{api_endpoint}/claims",
        token,
        json_body={
            "claimType": "FINAL_SETTLEMENT",
            "claimDate": "2026-08-01",
            "amountRupees": "480000",
            "status": "PENDING",
            "notes": DEMO_MARKER,
        },
    )
    return created.json(), True


def _render_demo_png() -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is required: python -m pip install Pillow") from exc

    image = Image.new("RGB", (1100, 650), color=(247, 249, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1100, 92), fill=(23, 52, 94))
    draw.text(
        (38, 24),
        "EPF SARTHI - SYNTHETIC DEMO CLAIM",
        fill=(255, 255, 255),
    )
    draw.text(
        (38, 55),
        "Training fixture only - contains no real member information",
        fill=(210, 225, 244),
    )
    draw.rectangle((45, 125, 1055, 605), fill=(255, 255, 255), outline=(210, 218, 228))
    rows = [
        ("Member", "DEMO USER"),
        ("Demo reference", "DEMO-CLAIM-001"),
        ("Claim form", "Form 19 - PF Final Settlement"),
        ("Claim receipt date", "01-AUG-2026"),
        ("Amount claimed", "INR 4,80,000"),
        ("Current status", "PENDING"),
        ("Field office", "SYNTHETIC DEMO OFFICE"),
        ("Notice", "No UAN, bank account, credential, or real PII is present."),
    ]
    y = 160
    for label, value in rows:
        draw.text((80, y), label, fill=(91, 103, 122))
        draw.text((360, y), value, fill=(20, 31, 48))
        draw.line((80, y + 30, 1015, y + 30), fill=(230, 234, 240))
        y += 52
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _existing_document(
    *,
    table_name: str,
    claim_id: str,
    user_id: str,
    region: str,
) -> dict[str, Any] | None:
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    response = table.query(
        IndexName="ByClaimId",
        KeyConditionExpression=Key("claimId").eq(claim_id),
    )
    matches = [
        item
        for item in response.get("Items", [])
        if item.get("userId") == user_id
        and item.get("documentKind") == DEMO_DOCUMENT_KIND
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: item.get("processedAt", ""), reverse=True)
    return matches[0]


def _poll_document(
    *,
    api_endpoint: str,
    token: str,
    claim_id: str,
    document_id: str,
    timeout_seconds: int = 150,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = _request(
            "GET",
            f"{api_endpoint}/claims/{claim_id}/documents/{document_id}?includeText=true",
            token,
        )
        document = response.json()
        if document.get("status") in TERMINAL_DOCUMENT_STATUSES:
            return document
        time.sleep(2)
    raise TimeoutError(f"Document {document_id} did not finish within {timeout_seconds}s")


def _ensure_document(
    *,
    api_endpoint: str,
    token: str,
    claim_id: str,
    user_id: str,
    documents_table: str,
    region: str,
) -> tuple[dict[str, Any], bool]:
    existing = _existing_document(
        table_name=documents_table,
        claim_id=claim_id,
        user_id=user_id,
        region=region,
    )
    if existing:
        if existing.get("status") == "PROCESSING":
            existing = _poll_document(
                api_endpoint=api_endpoint,
                token=token,
                claim_id=claim_id,
                document_id=existing["documentId"],
            )
        if existing.get("status") != "EXTRACTED":
            raise RuntimeError(
                f"Existing demo document {existing['documentId']} is "
                f"{existing.get('status')}; refusing to create duplicates"
            )
        return existing, False

    presign = _request(
        "POST",
        f"{api_endpoint}/claims/{claim_id}/documents",
        token,
        json_body={
            "contentType": "image/png",
            "documentKind": DEMO_DOCUMENT_KIND,
        },
    ).json()
    uploaded = requests.put(
        presign["uploadUrl"],
        data=_render_demo_png(),
        headers={"Content-Type": "image/png"},
        timeout=30,
    )
    if not uploaded.ok:
        raise RuntimeError(f"S3 upload failed: {uploaded.status_code} {uploaded.text}")
    document = _poll_document(
        api_endpoint=api_endpoint,
        token=token,
        claim_id=claim_id,
        document_id=presign["documentId"],
    )
    if document.get("status") != "EXTRACTED":
        raise RuntimeError(
            f"Demo document extraction ended as {document.get('status')}: "
            f"{document.get('failureReason') or document.get('needsManualEntryReason')}"
        )
    return document, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", default="prod", choices=("dev", "staging", "prod"))
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--email", default=DEMO_EMAIL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stack_name = f"epf-sentinel-{args.stage}"
    outputs = _stack_outputs(stack_name, args.region)
    required = {
        "ApiEndpoint",
        "CognitoUserPoolId",
        "CognitoUserPoolClientId",
        "DocumentsTableName",
    }
    missing = sorted(required - outputs.keys())
    if missing:
        raise RuntimeError(f"{stack_name} is missing outputs: {', '.join(missing)}")

    if args.dry_run:
        print(
            json.dumps(
                {
                    "stage": args.stage,
                    "stackName": stack_name,
                    "apiEndpoint": outputs["ApiEndpoint"],
                    "demoEmail": args.email,
                    "claim": {
                        "claimType": "FINAL_SETTLEMENT",
                        "claimDate": "2026-08-01",
                        "amountRupees": "480000",
                        "status": "PENDING",
                        "marker": DEMO_MARKER,
                    },
                    "document": {
                        "kind": DEMO_DOCUMENT_KIND,
                        "contentType": "image/png",
                        "synthetic": True,
                    },
                },
                indent=2,
            )
        )
        return 0

    password = os.environ.get("DEMO_USER_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError(
            "Set DEMO_USER_PASSWORD to a strong 12+ character value; it is never logged"
        )

    evidence_dir = Path(__file__).resolve().parents[1] / "evidence" / "module-5.3"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        "stackName": stack_name,
        "demoEmail": args.email,
        "latencyMs": {},
    }

    (identity, token, user_created), user_ms = _timed(
        _ensure_user,
        pool_id=outputs["CognitoUserPoolId"],
        client_id=outputs["CognitoUserPoolClientId"],
        region=args.region,
        email=args.email,
        password=password,
    )
    report["latencyMs"]["ensureUser"] = user_ms
    report["userCreated"] = user_created
    report["userId"] = identity

    (claim, claim_created), claim_ms = _timed(
        _ensure_claim,
        outputs["ApiEndpoint"].rstrip("/"),
        token,
    )
    report["latencyMs"]["ensureClaim"] = claim_ms
    report["claimCreated"] = claim_created
    report["claim"] = {
        key: claim.get(key)
        for key in ("claimId", "claimType", "claimDateIso", "amountPaise", "status", "notes")
    }

    (document, document_created), document_ms = _timed(
        _ensure_document,
        api_endpoint=outputs["ApiEndpoint"].rstrip("/"),
        token=token,
        claim_id=claim["claimId"],
        user_id=identity,
        documents_table=outputs["DocumentsTableName"],
        region=args.region,
    )
    report["latencyMs"]["ensureDocument"] = document_ms
    report["documentCreated"] = document_created
    report["document"] = {
        key: document.get(key)
        for key in ("documentId", "status", "documentKind", "contentType", "charCount")
    }

    report_path = evidence_dir / f"seed-{args.stage}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Evidence: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ClientError, RuntimeError, TimeoutError) as exc:
        print(f"seed_demo failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
