"""
Unit tests for PresignUpload Lambda handler.

All AWS calls are mocked so no real resources are needed.
"""

from __future__ import annotations

import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# ── path setup so tests can import Lambda code ────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/shared"))

from functions.presign_upload import app as presign_app


# ── helpers ───────────────────────────────────────────────────────────────────

def _jwt_event(
    user_id: str,
    claim_id: str,
    body: dict | None = None,
) -> dict:
    """Build a minimal API GW HTTP event with a Cognito JWT authorizer context."""
    return {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {"sub": user_id}
                }
            }
        },
        "pathParameters": {"claimId": claim_id},
        "body": json.dumps(body if body is not None else {"contentType": "application/pdf"}),
    }


_FAKE_PRESIGNED = {
    "url": "https://epf-sentinel-docs-dev.s3.amazonaws.com/",
    "fields": {
        "Content-Type": "application/pdf",
        "key": "user-1/claim-1/doc-1.pdf",
        "AWSAccessKeyId": "AKIAIOSFODNN7EXAMPLE",
        "policy": "base64encodedpolicy==",
        "signature": "base64signature==",
    },
}


class TestPresignUpload(unittest.TestCase):

    def _make_mocked_handler(
        self,
        *,
        claim_exists: bool = True,
        presigned: dict | None = None,
    ):
        """Return a patched handler call context."""
        presigned = presigned or _FAKE_PRESIGNED

        mock_ddb = MagicMock()
        mock_ddb.get_item.return_value = {"Item": {"claimId": {"S": "claim-1"}}} if claim_exists else {}
        mock_ddb.put_item.return_value = {}

        mock_s3 = MagicMock()
        mock_s3.generate_presigned_post.return_value = presigned

        def _boto3_client(service, **kwargs):
            if service == "dynamodb":
                return mock_ddb
            if service == "s3":
                return mock_s3
            return MagicMock()

        return _boto3_client, mock_ddb, mock_s3

    # ── content-type validation ───────────────────────────────────────────────

    def test_invalid_content_type_rejected(self):
        boto3_client, _, _ = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c1", {"contentType": "application/octet-stream"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertEqual(body["error"]["code"], "INVALID_CONTENT_TYPE")

    def test_missing_content_type_rejected(self):
        boto3_client, _, _ = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c1", {}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertEqual(body["error"]["code"], "INVALID_CONTENT_TYPE")

    def test_valid_pdf_returns_200_with_presigned_post(self):
        boto3_client, _, mock_s3 = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c1", {"contentType": "application/pdf"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertIn("documentId", body)
        self.assertIn("url", body)
        self.assertIn("fields", body)
        self.assertEqual(body["expiresIn"], 300)

    def test_valid_image_png_returns_200(self):
        boto3_client, _, _ = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c1", {"contentType": "image/png"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 200)

    def test_valid_image_jpeg_returns_200(self):
        boto3_client, _, _ = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c1", {"contentType": "image/jpeg"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 200)

    # ── S3 key structure ──────────────────────────────────────────────────────

    def test_s3_key_embeds_user_and_claim(self):
        """The S3 key must follow {userId}/{claimId}/{documentId}.{ext}."""
        boto3_client, _, mock_s3 = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("user-abc", "claim-xyz", {"contentType": "application/pdf"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 200)
        call_kwargs = mock_s3.generate_presigned_post.call_args
        key = call_kwargs[1]["Key"] if call_kwargs[1] else call_kwargs[0][1]
        parts = key.split("/")
        self.assertEqual(parts[0], "user-abc")
        self.assertEqual(parts[1], "claim-xyz")
        self.assertTrue(parts[2].endswith(".pdf"))

    def test_10mb_condition_present_in_presigned_post(self):
        """The content-length-range condition must be in the Conditions list."""
        boto3_client, _, mock_s3 = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            presign_app.handler(
                _jwt_event("u1", "c1", {"contentType": "application/pdf"}),
                MagicMock(),
            )
        call_kwargs = mock_s3.generate_presigned_post.call_args
        conditions = call_kwargs[1].get("Conditions", [])
        length_conditions = [
            c for c in conditions
            if isinstance(c, list) and c[0] == "content-length-range"
        ]
        self.assertTrue(
            len(length_conditions) == 1,
            "content-length-range condition must be present exactly once",
        )
        _, min_size, max_size = length_conditions[0]
        self.assertEqual(min_size, 1)
        self.assertEqual(max_size, 10_485_760)

    # ── tenancy ───────────────────────────────────────────────────────────────

    def test_claim_not_found_returns_404(self):
        boto3_client, _, _ = self._make_mocked_handler(claim_exists=False)
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c-missing", {"contentType": "application/pdf"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 404)

    def test_missing_jwt_returns_401(self):
        event = {
            "requestContext": {"authorizer": {}},
            "pathParameters": {"claimId": "c1"},
            "body": json.dumps({"contentType": "application/pdf"}),
        }
        resp = presign_app.handler(event, MagicMock())
        self.assertEqual(resp["statusCode"], 401)

    # ── DynamoDB stub written ─────────────────────────────────────────────────

    def test_processing_stub_written_to_dynamodb(self):
        boto3_client, mock_ddb, _ = self._make_mocked_handler()
        with patch.object(presign_app.boto3, "client", side_effect=boto3_client):
            resp = presign_app.handler(
                _jwt_event("u1", "c1", {"contentType": "application/pdf"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 200)
        mock_ddb.put_item.assert_called_once()
        put_call = mock_ddb.put_item.call_args[1]
        item = put_call["Item"]
        self.assertEqual(item["status"]["S"], "PROCESSING")
        self.assertEqual(item["contentType"]["S"], "application/pdf")


if __name__ == "__main__":
    unittest.main()
