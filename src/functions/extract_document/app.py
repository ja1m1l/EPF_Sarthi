"""
ExtractDocument Lambda — SQS trigger from DocumentEventQueue.

Receives S3:ObjectCreated events forwarded by SQS, extracts text from
uploaded claim documents, and persists the result to the Documents DynamoDB
table.

Extraction routing
------------------
1. Derive userId from S3 key prefix ``{userId}/{claimId}/{documentId}.{ext}``.
2. Cross-check: fetch the Claim and verify the owner matches. Mismatch →
   NEEDS_MANUAL_ENTRY(TENANT_MISMATCH) + CloudWatch alarm metric. Do NOT
   re-raise (it will not succeed on retry).
3. Download the object bytes from S3.
4. Route by content-type:
   a. application/pdf → pypdf text-layer extraction.
      - chars ≥ 20 → EXTRACTED, PDF_TEXT_LAYER, confidence=1.0.
      - chars < 20 → fall through to VLM.
   b. image/* or PDF fallthrough → Gemini gemini-2.5-flash VLM.
      - chars ≥ 20 → EXTRACTED, VLM_TRANSCRIPTION, confidence=0.85.
      - chars < 20 → NEEDS_MANUAL_ENTRY(INSUFFICIENT_TEXT). Do NOT re-raise.
5. Unhandled exceptions (corrupt file, network, Gemini 5xx exhausted) →
   update record to NEEDS_MANUAL_ENTRY(EXTRACTION_FAILED), then RE-RAISE so
   SQS retries up to maxReceiveCount=3 before delivering to the DLQ.

Text normalisation
------------------
Only whitespace is normalised: ``re.sub(r'\\s+', ' ', text).strip()``.
No other cleaning, summarisation, or transformation. The EvidenceAgent's
verbatim substring check runs against this field.

Model used
----------
gemini-2.5-flash — pinned as MODEL_CONFIG["generation_model"] in shared/gemini.py.
This model is vision-capable and handles both images and PDF inline data.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.parse
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared import gemini
from shared.logging import get_logger, set_correlation_id
from shared.models import DocumentStatus, ExtractionMethod, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
DOCUMENTS_TABLE: str = os.environ.get("DOCUMENTS_TABLE_NAME", f"epf-sentinel-Documents-{STAGE}")
MIN_CHAR_THRESHOLD: int = 20

_VLM_TRANSCRIPTION_PROMPT = (
    "Transcribe ALL visible text from this document image verbatim. "
    "Preserve line breaks as single spaces. "
    "Do not summarise, interpret, or omit any text. "
    "If the document contains no readable text, return the single word: BLANK"
)


# ── CloudWatch alarm metric ───────────────────────────────────────────────────

def _emit_metric(metric_name: str) -> None:
    """Emit a count-1 metric to the EPFSentinel CloudWatch namespace."""
    try:
        cw = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        cw.put_metric_data(
            Namespace="EPFSentinel",
            MetricData=[{"MetricName": metric_name, "Value": 1, "Unit": "Count"}],
        )
        log.info("cloudwatch.metric.published", metricName=metric_name)
    except Exception as exc:
        log.warning("cloudwatch.metric.failed", metricName=metric_name, error=str(exc))


# ── Text normalisation ────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Collapse all whitespace sequences to a single space and strip."""
    return re.sub(r"\s+", " ", text).strip()


# ── PDF text-layer extraction ─────────────────────────────────────────────────

def _extract_pdf_text_layer(pdf_bytes: bytes) -> str:
    """Use pypdf to extract the native text layer. Raises on corrupt PDFs."""
    import pypdf  # local import so Lambda only pays the import cost for PDFs

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return _normalise(" ".join(pages))


# ── DynamoDB helpers ──────────────────────────────────────────────────────────

def _get_document(user_id: str, document_id: str) -> Optional[dict]:
    ddb = boto3.client("dynamodb")
    resp = ddb.get_item(
        TableName=DOCUMENTS_TABLE,
        Key={
            "userId":     {"S": user_id},
            "documentId": {"S": document_id},
        },
    )
    return resp.get("Item")


def _get_claim_owner(claim_id: str) -> Optional[str]:
    """
    Look up the Claim by claimId to retrieve the owner's userId.
    Uses a GSI scan workaround: the Documents table stores claimId, so we
    derive the userId from the S3 key and cross-check against the Claims table.
    """
    # We query the Claims table via a Scan filter because we don't have the
    # userId PK. For tenancy the key truth is the S3 key prefix userId vs
    # the userId stored on the claim item.
    ddb = boto3.client("dynamodb")
    resp = ddb.query(
        TableName=DOCUMENTS_TABLE,
        IndexName="ByClaimId",
        KeyConditionExpression="claimId = :cid",
        ExpressionAttributeValues={":cid": {"S": claim_id}},
        ProjectionExpression="userId",
        Limit=1,
    )
    items = resp.get("Items", [])
    if items:
        return items[0].get("userId", {}).get("S")
    return None


def _get_claim_user_id_from_claims_table(claim_id: str, user_id: str) -> Optional[str]:
    """
    Verify claim ownership: returns userId if the claim belongs to user_id.
    Uses a direct get_item on the known (userId, claimId) key.
    """
    ddb = boto3.client("dynamodb")
    resp = ddb.get_item(
        TableName=CLAIMS_TABLE,
        Key={
            "userId":  {"S": user_id},
            "claimId": {"S": claim_id},
        },
        ProjectionExpression="userId",
    )
    item = resp.get("Item")
    if item:
        return item.get("userId", {}).get("S")
    return None


def _update_document_status(
    user_id: str,
    document_id: str,
    *,
    status: str,
    extracted_text: Optional[str] = None,
    extraction_method: Optional[str] = None,
    confidence: Optional[float] = None,
    failure_reason: Optional[str] = None,
) -> None:
    """Update an existing Document record with extraction results."""
    ddb = boto3.client("dynamodb")
    now = utc_now_iso()

    update_expr_parts = ["#st = :status", "processedAt = :processedAt"]
    expr_names = {"#st": "status"}
    expr_values: dict[str, Any] = {
        ":status":      {"S": status},
        ":processedAt": {"S": now},
    }

    if extracted_text is not None:
        update_expr_parts.append("extractedText = :extractedText")
        update_expr_parts.append("charCount = :charCount")
        expr_values[":extractedText"] = {"S": extracted_text}
        expr_values[":charCount"] = {"N": str(len(extracted_text))}

    if extraction_method is not None:
        update_expr_parts.append("extractionMethod = :extractionMethod")
        expr_values[":extractionMethod"] = {"S": extraction_method}

    if confidence is not None:
        update_expr_parts.append("confidence = :confidence")
        expr_values[":confidence"] = {"N": str(round(confidence, 4))}

    if failure_reason is not None:
        update_expr_parts.append("failureReason = :failureReason")
        expr_values[":failureReason"] = {"S": failure_reason}

    ddb.update_item(
        TableName=DOCUMENTS_TABLE,
        Key={
            "userId":     {"S": user_id},
            "documentId": {"S": document_id},
        },
        UpdateExpression="SET " + ", ".join(update_expr_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


# ── SQS/S3 event parsing ──────────────────────────────────────────────────────

def _parse_s3_event(sqs_record: dict) -> tuple[str, str, str]:
    """
    Extract (bucket, key, content_type) from an SQS record wrapping an S3 event.
    Returns (bucket, key, content_type). content_type may be empty string.
    """
    body_str = sqs_record.get("body", "{}")
    try:
        body = json.loads(body_str)
    except json.JSONDecodeError:
        raise ValueError(f"SQS record body is not valid JSON: {body_str[:200]}")

    s3_records = body.get("Records", [])
    if not s3_records:
        raise ValueError("No S3 Records in SQS body")

    s3_rec = s3_records[0]
    bucket = s3_rec["s3"]["bucket"]["name"]
    key = urllib.parse.unquote_plus(s3_rec["s3"]["object"]["key"])
    # content-type is in the object metadata (not always present in the event)
    content_type = s3_rec.get("s3", {}).get("object", {}).get("contentType", "")
    return bucket, key, content_type


def _key_to_parts(key: str) -> tuple[str, str, str, str]:
    """
    Parse ``{userId}/{claimId}/{documentId}.{ext}`` → (userId, claimId, documentId, ext).
    """
    parts = key.split("/")
    if len(parts) != 3:
        raise ValueError(f"S3 key does not match expected format: {key!r}")
    user_id = parts[0]
    claim_id = parts[1]
    filename = parts[2]
    name, _, ext = filename.rpartition(".")
    document_id = name
    return user_id, claim_id, document_id, ext


# ── Core extraction logic ─────────────────────────────────────────────────────

def _process_record(sqs_record: dict) -> None:
    """Process a single SQS/S3 record end-to-end."""
    # 1. Parse event
    bucket, key, event_content_type = _parse_s3_event(sqs_record)
    user_id_from_key, claim_id, document_id, ext = _key_to_parts(key)

    set_correlation_id(document_id)

    log.info(
        "extract_document.started",
        bucket=bucket,
        key=key,
        userId=user_id_from_key,
        claimId=claim_id,
        documentId=document_id,
    )

    # 2. Tenancy cross-check: verify the claim is owned by the userId in the key
    try:
        owner = _get_claim_user_id_from_claims_table(claim_id, user_id_from_key)
    except Exception as exc:
        log.error(
            "extract_document.claims_lookup_failed",
            claimId=claim_id,
            error=str(exc),
        )
        raise  # transient — let SQS retry

    if owner is None:
        # Claim not found under this userId → tenant mismatch
        log.error(
            "extract_document.tenant_mismatch",
            userId=user_id_from_key,
            claimId=claim_id,
            documentId=document_id,
        )
        _emit_metric("DocumentTenantMismatch")
        _update_document_status(
            user_id_from_key,
            document_id,
            status=DocumentStatus.NEEDS_MANUAL_ENTRY.value,
            failure_reason="TENANT_MISMATCH",
        )
        return  # deterministic failure — do NOT re-raise

    # 3. Download object from S3
    try:
        s3 = boto3.client("s3")
        s3_obj = s3.get_object(Bucket=bucket, Key=key)
        file_bytes: bytes = s3_obj["Body"].read()
        content_type: str = (
            event_content_type
            or s3_obj.get("ContentType", "")
            or f"image/{ext}" if ext in ("png", "jpg", "jpeg") else "application/pdf"
        )
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "extract_document.s3_download_failed",
            bucket=bucket,
            key=key,
            error=str(exc),
        )
        _update_document_status(
            user_id_from_key,
            document_id,
            status=DocumentStatus.NEEDS_MANUAL_ENTRY.value,
            failure_reason="EXTRACTION_FAILED",
        )
        raise  # transient — let SQS retry → DLQ

    # 4. Extraction routing
    extracted_text: Optional[str] = None
    method: Optional[str] = None
    confidence: Optional[float] = None
    failure_reason: Optional[str] = None
    final_status: str = DocumentStatus.NEEDS_MANUAL_ENTRY.value

    is_pdf = content_type == "application/pdf" or ext == "pdf"

    if is_pdf:
        # Try PDF text layer first
        try:
            pdf_text = _extract_pdf_text_layer(file_bytes)
        except Exception as exc:
            log.error(
                "extract_document.pdf_parse_failed",
                documentId=document_id,
                error=str(exc),
                errorType=type(exc).__name__,
            )
            # Corrupt file — mark and re-raise for DLQ
            _update_document_status(
                user_id_from_key,
                document_id,
                status=DocumentStatus.NEEDS_MANUAL_ENTRY.value,
                failure_reason="EXTRACTION_FAILED",
            )
            raise  # re-raise → SQS retry → DLQ

        if len(pdf_text) >= MIN_CHAR_THRESHOLD:
            extracted_text = pdf_text
            method = ExtractionMethod.PDF_TEXT_LAYER.value
            confidence = 1.0
            final_status = DocumentStatus.EXTRACTED.value
            log.info(
                "extract_document.pdf_text_layer.success",
                documentId=document_id,
                charCount=len(extracted_text),
            )
        else:
            log.info(
                "extract_document.pdf_text_layer.insufficient",
                documentId=document_id,
                charCount=len(pdf_text),
                fallback="VLM",
            )
            # Fall through to VLM below

    # VLM path — images or PDF with insufficient text layer
    if final_status != DocumentStatus.EXTRACTED.value:
        try:
            vlm_mime = content_type if content_type else (
                "image/jpeg" if ext in ("jpg", "jpeg") else
                "image/png" if ext == "png" else
                "application/pdf"
            )
            raw_text = gemini.generate_multimodal(
                image_bytes=file_bytes,
                mime_type=vlm_mime,
                prompt=_VLM_TRANSCRIPTION_PROMPT,
                system_instruction=(
                    "You are a document digitisation engine. "
                    "Return ONLY the verbatim text visible in the document. "
                    "Never add commentary, headers, or markdown formatting."
                ),
            )
            vlm_text = _normalise(raw_text)
        except Exception as exc:
            log.error(
                "extract_document.vlm_failed",
                documentId=document_id,
                error=str(exc),
                errorType=type(exc).__name__,
            )
            _update_document_status(
                user_id_from_key,
                document_id,
                status=DocumentStatus.NEEDS_MANUAL_ENTRY.value,
                failure_reason="EXTRACTION_FAILED",
            )
            raise  # re-raise → SQS retry → DLQ

        if len(vlm_text) >= MIN_CHAR_THRESHOLD and vlm_text.upper() != "BLANK":
            extracted_text = vlm_text
            method = ExtractionMethod.VLM_TRANSCRIPTION.value
            confidence = 0.85
            final_status = DocumentStatus.EXTRACTED.value
            log.info(
                "extract_document.vlm.success",
                documentId=document_id,
                charCount=len(extracted_text),
            )
        else:
            failure_reason = "INSUFFICIENT_TEXT"
            final_status = DocumentStatus.NEEDS_MANUAL_ENTRY.value
            log.warning(
                "extract_document.vlm.insufficient_text",
                documentId=document_id,
                charCount=len(vlm_text) if vlm_text else 0,
            )

    # 5. Persist result
    _update_document_status(
        user_id_from_key,
        document_id,
        status=final_status,
        extracted_text=extracted_text,
        extraction_method=method,
        confidence=confidence,
        failure_reason=failure_reason,
    )

    log.info(
        "extract_document.completed",
        documentId=document_id,
        status=final_status,
        method=method,
        charCount=len(extracted_text) if extracted_text else 0,
    )


# ── Lambda entry point ────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """
    Lambda entry point — SQS trigger.

    Processes each SQS record individually.  On failure, re-raises the
    exception so Lambda reports the record as failed (enabling per-message
    DLQ routing via ReportBatchItemFailures).
    """
    failed_ids = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            _process_record(record)
        except Exception as exc:
            log.error(
                "extract_document.record_failed",
                messageId=message_id,
                error=str(exc),
                errorType=type(exc).__name__,
            )
            failed_ids.append({"itemIdentifier": message_id})

    # SQS partial batch failure response — only failed messages are retried
    if failed_ids:
        return {"batchItemFailures": failed_ids}

    return {}
