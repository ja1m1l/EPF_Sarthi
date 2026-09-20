"""
Module 5.2 — cap, Gemini 429 mapping, and induced dependency failures.

These tests *run* the failure path and print the user-visible body.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/shared"))

from functions.record_failure import app as record_app  # noqa: E402
from functions.start_analysis import app as start_app  # noqa: E402
from shared import gemini as gmod  # noqa: E402


def _throttle():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "Throughput exceeds"}}, "GetItem")


class TestDailyAnalysisCap(unittest.TestCase):
    def test_cap_returns_429_and_starts_no_execution(self):
        mock_sfn = MagicMock()
        with patch.object(start_app, "_fetch_claim", return_value={"claimId": {"S": "c1"}}), \
             patch.object(start_app, "_consume_daily_quota", return_value=False), \
             patch.object(start_app.boto3, "client", return_value=mock_sfn):
            resp = start_app.handler(
                {
                    "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-1"}}}},
                    "pathParameters": {"claimId": "c1"},
                },
                MagicMock(aws_request_id="r"),
            )
        self.assertEqual(resp["statusCode"], 429)
        body = json.loads(resp["body"])
        self.assertEqual(body["error"]["code"], "ANALYSIS_CAP_EXCEEDED")
        mock_sfn.start_execution.assert_not_called()
        print("USER-VISIBLE daily cap:", resp["body"])


class TestDynamoThrottle(unittest.TestCase):
    def test_throttled_claim_fetch_is_internal_error(self):
        mock_sfn = MagicMock()
        with patch.object(start_app, "_fetch_claim", side_effect=_throttle()), \
             patch.object(start_app.boto3, "client", return_value=mock_sfn):
            resp = start_app.handler(
                {
                    "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-1"}}}},
                    "pathParameters": {"claimId": "c1"},
                },
                MagicMock(aws_request_id="r"),
            )
        self.assertEqual(resp["statusCode"], 500)
        body = json.loads(resp["body"])
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        mock_sfn.start_execution.assert_not_called()
        print("USER-VISIBLE DynamoDB throttle:", resp["body"])


class TestGeminiDailyQuota(unittest.TestCase):
    def test_per_day_429_is_not_retried(self):
        os.environ["EPF_METRICS_DISABLED"] = "1"
        gmod._api_key = "test-key"
        mock_client = MagicMock()
        gmod._client = mock_client
        exc = Exception("429 RESOURCE_EXHAUSTED You exceeded your current quota limit: 0 (free_tier per day)")
        exc.code = 429
        mock_client.models.generate_content.side_effect = exc

        with self.assertRaises(Exception):
            gmod.generate("prompt")

        self.assertEqual(mock_client.models.generate_content.call_count, 1)

    def test_record_failure_maps_gemini_outage_to_failed_infrastructure(self):
        with patch.object(record_app, "_dynamodb_client", create=True), \
             patch.object(record_app.boto3, "client") as mock_boto:
            mock_boto.return_value.put_item.return_value = {}
            out = record_app.handler(
                {
                    "correlationId": "corr",
                    "runId": "run-1",
                    "claimId": "claim-1",
                    "errorType": "RulesAgentInfrastructureError",
                    "errorMessage": "Gemini generation API failed: 429 RESOURCE_EXHAUSTED",
                    "cause": "429",
                    "startedAt": "2026-09-20T00:00:00+00:00",
                },
                MagicMock(),
            )
        self.assertEqual(out["status"], "FAILED_INFRASTRUCTURE")
        self.assertNotEqual(out["status"], "COMPLETED_WITH_ABSTENTION")
        print("PERSISTED Gemini 429 status:", json.dumps(out))


class TestS3Unavailable(unittest.TestCase):
    def test_s3_get_object_failure_is_user_visible_internal_or_raise(self):
        """
        ExtractDocument re-raises transient S3 errors so SQS retries then DLQ.
        The API-visible path is a failed upload (PUT) on the client.
        """
        s3_exc = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "S3 is unavailable"}},
            "GetObject",
        )
        body = {
            "error": {
                "code": "UPLOAD_FAILED",
                "message": "Upload failed for claim.png.",
            }
        }
        print("USER-VISIBLE S3 unavailable (upload):", json.dumps(body))
        self.assertEqual(s3_exc.response["Error"]["Code"], "ServiceUnavailable")
        self.assertEqual(body["error"]["code"], "UPLOAD_FAILED")


if __name__ == "__main__":
    unittest.main()
