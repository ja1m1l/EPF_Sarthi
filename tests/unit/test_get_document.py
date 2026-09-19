"""
Unit tests for GetDocument Lambda handler.

All AWS calls are mocked.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/shared"))

from functions.get_document import app as get_doc_app


def _jwt_event(
    user_id: str = "user-1",
    claim_id: str = "claim-1",
    document_id: str = "doc-1",
    query_params: dict | None = None,
) -> dict:
    return {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {"sub": user_id}
                }
            }
        },
        "pathParameters": {
            "claimId": claim_id,
            "documentId": document_id,
        },
        "queryStringParameters": query_params or {},
    }


class TestGetDocument(unittest.TestCase):

    def _make_mocked_handler(
        self,
        *,
        claim_exists: bool = True,
        doc_item: dict | None = None,
    ):
        mock_ddb = MagicMock()

        def _get_item(TableName, Key, **kwargs):
            if "Claims" in TableName:
                if claim_exists:
                    return {"Item": {"claimId": {"S": Key["claimId"]["S"]}}}
                return {}
            if "Documents" in TableName:
                if doc_item is not None:
                    return {"Item": doc_item}
                return {}
            return {}

        mock_ddb.get_item.side_effect = _get_item

        def _client(service, **kwargs):
            if service == "dynamodb":
                return mock_ddb
            return MagicMock()

        return _client, mock_ddb

    def test_missing_jwt_returns_401(self):
        event = {
            "requestContext": {"authorizer": {}},
            "pathParameters": {"claimId": "c1", "documentId": "d1"},
        }
        resp = get_doc_app.handler(event, MagicMock())
        self.assertEqual(resp["statusCode"], 401)

    def test_claim_not_owned_returns_404(self):
        boto3_client, _ = self._make_mocked_handler(claim_exists=False)
        with patch.object(get_doc_app.boto3, "client", side_effect=boto3_client):
            resp = get_doc_app.handler(_jwt_event(), MagicMock())
        self.assertEqual(resp["statusCode"], 404)

    def test_document_not_found_returns_404(self):
        boto3_client, _ = self._make_mocked_handler(claim_exists=True, doc_item=None)
        with patch.object(get_doc_app.boto3, "client", side_effect=boto3_client):
            resp = get_doc_app.handler(_jwt_event(), MagicMock())
        self.assertEqual(resp["statusCode"], 404)

    def test_document_belongs_to_different_claim_returns_404(self):
        doc_item = {
            "userId": {"S": "user-1"},
            "documentId": {"S": "doc-1"},
            "claimId": {"S": "other-claim"},
            "status": {"S": "EXTRACTED"},
        }
        boto3_client, _ = self._make_mocked_handler(claim_exists=True, doc_item=doc_item)
        with patch.object(get_doc_app.boto3, "client", side_effect=boto3_client):
            resp = get_doc_app.handler(_jwt_event(claim_id="claim-1"), MagicMock())
        self.assertEqual(resp["statusCode"], 404)

    def test_get_document_success_omits_text_by_default(self):
        doc_item = {
            "userId": {"S": "user-1"},
            "documentId": {"S": "doc-1"},
            "claimId": {"S": "claim-1"},
            "status": {"S": "EXTRACTED"},
            "extractedText": {"S": "Long extracted confidential text"},
            "charCount": {"N": "31"},
        }
        boto3_client, _ = self._make_mocked_handler(claim_exists=True, doc_item=doc_item)
        with patch.object(get_doc_app.boto3, "client", side_effect=boto3_client):
            resp = get_doc_app.handler(_jwt_event(), MagicMock())
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["documentId"], "doc-1")
        self.assertEqual(body["status"], "EXTRACTED")
        self.assertNotIn("extractedText", body)

    def test_get_document_success_includes_text_when_requested(self):
        doc_item = {
            "userId": {"S": "user-1"},
            "documentId": {"S": "doc-1"},
            "claimId": {"S": "claim-1"},
            "status": {"S": "EXTRACTED"},
            "extractedText": {"S": "Long extracted confidential text"},
            "charCount": {"N": "31"},
        }
        boto3_client, _ = self._make_mocked_handler(claim_exists=True, doc_item=doc_item)
        with patch.object(get_doc_app.boto3, "client", side_effect=boto3_client):
            resp = get_doc_app.handler(
                _jwt_event(query_params={"includeText": "true"}),
                MagicMock(),
            )
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["extractedText"], "Long extracted confidential text")


if __name__ == "__main__":
    unittest.main()
