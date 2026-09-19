"""
Unit tests for ExtractDocument Lambda handler.

All AWS and Gemini calls are mocked.
PDF bytes are created on the fly using pypdf.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/shared"))

from functions.extract_document import app as extract_app

# ── helpers ───────────────────────────────────────────────────────────────────

_DOCUMENT_ID = "doc-0001"
_USER_ID = "user-aaa"
_CLAIM_ID = "claim-bbb"
_BUCKET = "epf-sentinel-docs-dev-123456789"
_S3_KEY = f"{_USER_ID}/{_CLAIM_ID}/{_DOCUMENT_ID}.pdf"


def _sqs_record(
    key: str = _S3_KEY,
    bucket: str = _BUCKET,
    content_type: str = "",
) -> dict:
    """Build a minimal SQS record wrapping an S3 ObjectCreated event."""
    body = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key, "contentType": content_type},
                }
            }
        ]
    }
    return {"messageId": "msg-001", "body": json.dumps(body)}


def _sqs_event(record: dict | None = None) -> dict:
    return {"Records": [record or _sqs_record()]}


def _make_boto3_client(
    *,
    claim_exists: bool = True,
    file_bytes: bytes = b"PDF content placeholder",
    content_type: str = "application/pdf",
):
    """Return a boto3.client factory that yields mocked DDB/S3/CW clients."""
    mock_ddb = MagicMock()
    # Claims table: get_item returns Item if claim_exists
    mock_ddb.get_item.return_value = (
        {"Item": {"userId": {"S": _USER_ID}}} if claim_exists else {}
    )
    mock_ddb.query.return_value = {"Items": []}
    mock_ddb.update_item.return_value = {}

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": io.BytesIO(file_bytes),
        "ContentType": content_type,
    }

    mock_cw = MagicMock()
    mock_cw.put_metric_data.return_value = {}

    def _client(service, **kwargs):
        if service == "dynamodb":
            return mock_ddb
        if service == "s3":
            return mock_s3
        if service == "cloudwatch":
            return mock_cw
        return MagicMock()

    return _client, mock_ddb, mock_s3, mock_cw


def _make_pdf_with_text(text: str) -> bytes:
    """Create a minimal valid PDF using pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf.read()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestExtractDocumentPdfTextLayer(unittest.TestCase):

    def test_pdf_text_layer_extracted_no_gemini_call(self):
        """A PDF with a native text layer should use PDF_TEXT_LAYER, never call Gemini."""
        rich_text = "This is the EPF settlement letter confirming account details verified."
        pdf_bytes = _make_pdf_with_text(rich_text)

        boto3_client, mock_ddb, _, _ = _make_boto3_client(file_bytes=pdf_bytes)

        mock_reader = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = rich_text
        mock_reader.pages = [mock_page]

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch("pypdf.PdfReader", return_value=mock_reader), \
             patch.object(extract_app.gemini, "generate_multimodal") as mock_vlm:
            result = extract_app.handler(_sqs_event(), MagicMock())

        mock_vlm.assert_not_called()
        update_call = mock_ddb.update_item.call_args[1]
        expr = update_call["UpdateExpression"]
        vals = update_call["ExpressionAttributeValues"]
        self.assertIn("EXTRACTED", vals[":status"]["S"])
        self.assertIn("PDF_TEXT_LAYER", vals[":extractionMethod"]["S"])
        self.assertEqual(result, {})

    def test_corrupt_pdf_reraises_for_dlq(self):
        """A corrupt PDF (pypdf raises) must re-raise so the message goes to the DLQ."""
        corrupt_bytes = b"THIS IS NOT A PDF AT ALL \x00\xff"

        boto3_client, mock_ddb, _, _ = _make_boto3_client(file_bytes=corrupt_bytes)

        # pypdf will raise; VLM should also be attempted and can fail
        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch.object(extract_app.gemini, "generate_multimodal", side_effect=RuntimeError("VLM failed")):
            result = extract_app.handler(_sqs_event(), MagicMock())

        # Partial batch failure — messageId must be in batchItemFailures
        self.assertIn("batchItemFailures", result)
        failed_ids = [f["itemIdentifier"] for f in result["batchItemFailures"]]
        self.assertIn("msg-001", failed_ids)


class TestExtractDocumentScannedPdfFallback(unittest.TestCase):

    def test_scanned_pdf_falls_back_to_vlm(self):
        """A PDF whose text layer yields < 20 chars must trigger VLM transcription."""
        sparse_pdf = _make_pdf_with_text("")   # empty text layer

        long_vlm_text = "Verified EPF member details: name, UAN, bank account confirmed."
        boto3_client, mock_ddb, _, _ = _make_boto3_client(file_bytes=sparse_pdf)

        mock_reader = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader.pages = [mock_page]

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch("pypdf.PdfReader", return_value=mock_reader), \
             patch.object(extract_app.gemini, "generate_multimodal", return_value=long_vlm_text):
            extract_app.handler(_sqs_event(), MagicMock())

        vals = mock_ddb.update_item.call_args[1]["ExpressionAttributeValues"]
        self.assertEqual(vals[":status"]["S"], "EXTRACTED")
        self.assertEqual(vals[":extractionMethod"]["S"], "VLM_TRANSCRIPTION")
        self.assertAlmostEqual(float(vals[":confidence"]["N"]), 0.85)


class TestExtractDocumentImage(unittest.TestCase):

    def test_image_jpeg_goes_to_vlm(self):
        """An image/jpeg must skip PDF extraction and go directly to VLM."""
        jpeg_key = f"{_USER_ID}/{_CLAIM_ID}/{_DOCUMENT_ID}.jpg"
        vlm_text = "EPF Passbook: Member Name Ramesh Kumar, UAN 100123456789"

        boto3_client, mock_ddb, _, _ = _make_boto3_client(
            file_bytes=b"\xff\xd8\xff\xe0" + b"\x00" * 100,  # minimal JPEG header
            content_type="image/jpeg",
        )

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch.object(extract_app.gemini, "generate_multimodal", return_value=vlm_text) as mock_vlm:
            extract_app.handler(_sqs_event(_sqs_record(key=jpeg_key)), MagicMock())

        mock_vlm.assert_called_once()
        call_kwargs = mock_vlm.call_args[1] if mock_vlm.call_args[1] else {}
        call_args = mock_vlm.call_args[0]
        mime = call_kwargs.get("mime_type") or (call_args[1] if len(call_args) > 1 else "")
        self.assertIn("jpeg", mime)

        vals = mock_ddb.update_item.call_args[1]["ExpressionAttributeValues"]
        self.assertEqual(vals[":status"]["S"], "EXTRACTED")

    def test_image_png_goes_to_vlm(self):
        """An image/png must go directly to VLM."""
        png_key = f"{_USER_ID}/{_CLAIM_ID}/{_DOCUMENT_ID}.png"
        vlm_text = "PAN card: ABCDE1234F, Date of Birth 01-01-1985"

        boto3_client, mock_ddb, _, _ = _make_boto3_client(
            file_bytes=b"\x89PNG\r\n" + b"\x00" * 100,
            content_type="image/png",
        )

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch.object(extract_app.gemini, "generate_multimodal", return_value=vlm_text):
            extract_app.handler(_sqs_event(_sqs_record(key=png_key)), MagicMock())

        vals = mock_ddb.update_item.call_args[1]["ExpressionAttributeValues"]
        self.assertEqual(vals[":status"]["S"], "EXTRACTED")


class TestExtractDocumentInsufficientText(unittest.TestCase):

    def test_vlm_under_20_chars_needs_manual_entry(self):
        """If VLM returns < 20 chars, status must be NEEDS_MANUAL_ENTRY(INSUFFICIENT_TEXT)."""
        jpeg_key = f"{_USER_ID}/{_CLAIM_ID}/{_DOCUMENT_ID}.jpg"
        boto3_client, mock_ddb, _, _ = _make_boto3_client(
            file_bytes=b"\xff\xd8" + b"\x00" * 10,
            content_type="image/jpeg",
        )

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch.object(extract_app.gemini, "generate_multimodal", return_value="short"):
            # Should NOT raise — insufficient text is deterministic, not retried
            result = extract_app.handler(_sqs_event(_sqs_record(key=jpeg_key)), MagicMock())

        # No batch failures
        self.assertEqual(result, {})

        vals = mock_ddb.update_item.call_args[1]["ExpressionAttributeValues"]
        self.assertEqual(vals[":status"]["S"], "NEEDS_MANUAL_ENTRY")
        self.assertEqual(vals[":failureReason"]["S"], "INSUFFICIENT_TEXT")

    def test_vlm_blank_response_needs_manual_entry(self):
        """VLM returning 'BLANK' must be treated as < 20 chars."""
        jpeg_key = f"{_USER_ID}/{_CLAIM_ID}/{_DOCUMENT_ID}.jpg"
        boto3_client, mock_ddb, _, _ = _make_boto3_client(
            file_bytes=b"\xff\xd8" + b"\x00" * 10,
            content_type="image/jpeg",
        )

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch.object(extract_app.gemini, "generate_multimodal", return_value="BLANK"):
            result = extract_app.handler(_sqs_event(_sqs_record(key=jpeg_key)), MagicMock())

        self.assertEqual(result, {})
        vals = mock_ddb.update_item.call_args[1]["ExpressionAttributeValues"]
        self.assertEqual(vals[":status"]["S"], "NEEDS_MANUAL_ENTRY")


class TestExtractDocumentTenancy(unittest.TestCase):

    def test_tenant_mismatch_sets_needs_manual_and_emits_metric(self):
        """
        When the userId in the S3 key does not match the claim owner,
        the document must be marked NEEDS_MANUAL_ENTRY(TENANT_MISMATCH)
        and a CloudWatch metric must be emitted.
        The exception must NOT be re-raised (no DLQ for policy violations).
        """
        boto3_client, mock_ddb, _, mock_cw = _make_boto3_client(claim_exists=False)

        with patch.object(extract_app.boto3, "client", side_effect=boto3_client), \
             patch.object(extract_app.gemini, "generate_multimodal") as mock_vlm:
            result = extract_app.handler(_sqs_event(), MagicMock())

        # No re-raise
        self.assertEqual(result, {})
        # Gemini must never be called for a mismatch
        mock_vlm.assert_not_called()
        # CloudWatch metric emitted
        mock_cw.put_metric_data.assert_called_once()
        metric_data = mock_cw.put_metric_data.call_args[1]["MetricData"][0]
        self.assertEqual(metric_data["MetricName"], "DocumentTenantMismatch")
        # DynamoDB updated to NEEDS_MANUAL_ENTRY
        vals = mock_ddb.update_item.call_args[1]["ExpressionAttributeValues"]
        self.assertEqual(vals[":status"]["S"], "NEEDS_MANUAL_ENTRY")
        self.assertEqual(vals[":failureReason"]["S"], "TENANT_MISMATCH")


class TestExtractDocumentTextNormalisation(unittest.TestCase):

    def test_text_stored_verbatim_with_only_whitespace_normalisation(self):
        """
        Multi-space sequences and newlines must be collapsed to single spaces.
        No other transformation may occur.
        """
        raw_text = "Hello   World\n\nThis   is\ta\ttest"
        expected = "Hello World This is a test"

        normalised = extract_app._normalise(raw_text)
        self.assertEqual(normalised, expected)

    def test_no_other_transformation(self):
        """Special characters, punctuation, and numbers must be preserved verbatim."""
        raw_text = "UAN: 100123456789, Amount: ₹48,000.00 (FINAL_SETTLEMENT)"
        normalised = extract_app._normalise(raw_text)
        self.assertEqual(normalised, raw_text)


if __name__ == "__main__":
    unittest.main()
