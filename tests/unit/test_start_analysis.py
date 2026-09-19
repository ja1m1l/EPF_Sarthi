"""
Unit tests for the StartAnalysis Lambda's document loading.

These cover the wiring that supplies the EvidenceAgent with extracted
document text.  Before this existed the EvidenceAgent always received an
empty list, so every check returned NOT_FOUND regardless of what the user
had uploaded.

All AWS calls are mocked; no real resources are needed.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# ── path setup so tests can import Lambda code ────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/shared"))

from functions.start_analysis import app as start_app  # noqa: E402


_USER = "user-1"
_CLAIM = "claim-1"


def _doc_item(
    document_id: str,
    *,
    user_id: str = _USER,
    status: str = "EXTRACTED",
    text: str = "Claim settled on 2026-08-01 for Rs 4,80,000.",
    claim_id: str = _CLAIM,
) -> dict:
    """Build a marshalled DynamoDB Documents item."""
    item = {
        "userId": {"S": user_id},
        "documentId": {"S": document_id},
        "claimId": {"S": claim_id},
        "s3Key": {"S": f"{user_id}/{claim_id}/{document_id}.png"},
        "status": {"S": status},
    }
    if text:
        item["extractedText"] = {"S": text}
    return item


class TestFetchDocuments(unittest.TestCase):

    def _fetch_with(self, pages: list[dict]) -> list[dict]:
        """Run _fetch_documents against a mocked paginated query."""
        mock_ddb = MagicMock()
        mock_ddb.query.side_effect = pages
        with patch.object(start_app.boto3, "client", return_value=mock_ddb):
            return start_app._fetch_documents(_USER, _CLAIM)

    def test_returns_extracted_documents_with_verbatim_text(self):
        docs = self._fetch_with([{"Items": [_doc_item("doc-1", text="EXACT VERBATIM TEXT")]}])

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["documentId"], "doc-1")
        # The EvidenceAgent's substring check runs against this, so it must
        # arrive byte-for-byte as stored.
        self.assertEqual(docs[0]["text"], "EXACT VERBATIM TEXT")

    def test_uses_document_kind_as_title_for_the_evidence_agent(self):
        item = _doc_item("doc-1", text="UAN last four 1234")
        item["documentKind"] = {"S": "KYC"}
        docs = self._fetch_with([{"Items": [item]}])

        self.assertEqual(docs[0]["title"], "KYC / identity proof")
        self.assertEqual(docs[0]["documentKind"], "KYC")

    def test_excludes_processing_and_needs_manual_entry(self):
        docs = self._fetch_with([{
            "Items": [
                _doc_item("doc-ok"),
                _doc_item("doc-pending", status="PROCESSING"),
                _doc_item("doc-failed", status="NEEDS_MANUAL_ENTRY"),
            ]
        }])

        self.assertEqual([d["documentId"] for d in docs], ["doc-ok"])

    def test_excludes_documents_owned_by_another_user(self):
        """The ByClaimId GSI is not tenant-partitioned, so ownership is rechecked."""
        docs = self._fetch_with([{
            "Items": [
                _doc_item("doc-mine"),
                _doc_item("doc-theirs", user_id="user-2"),
            ]
        }])

        self.assertEqual([d["documentId"] for d in docs], ["doc-mine"])

    def test_excludes_extracted_documents_with_empty_text(self):
        docs = self._fetch_with([{"Items": [_doc_item("doc-empty", text="")]}])

        self.assertEqual(docs, [])

    def test_follows_pagination(self):
        docs = self._fetch_with([
            {"Items": [_doc_item("doc-1")], "LastEvaluatedKey": {"claimId": {"S": _CLAIM}}},
            {"Items": [_doc_item("doc-2")]},
        ])

        self.assertEqual([d["documentId"] for d in docs], ["doc-1", "doc-2"])

    def test_returns_empty_list_when_claim_has_no_documents(self):
        self.assertEqual(self._fetch_with([{"Items": []}]), [])


class TestUnmarshalClaim(unittest.TestCase):

    def test_amount_paise_survives_as_an_integer(self):
        """
        The RulesAgent rejects a claim whose amountPaise is a string, so the
        numeric type has to survive unmarshalling intact.
        """
        claim = start_app._unmarshal_claim({
            "userId": {"S": _USER},
            "claimId": {"S": _CLAIM},
            "claimType": {"S": "FINAL_SETTLEMENT"},
            "claimDateIso": {"S": "2026-08-01T00:00:00+00:00"},
            "amountPaise": {"N": "48000000"},
            "status": {"S": "PENDING"},
        })

        self.assertIsInstance(claim["amountPaise"], int)
        self.assertEqual(claim["amountPaise"], 48000000)
        self.assertEqual(claim["claimType"], "FINAL_SETTLEMENT")

    def test_null_attributes_become_none(self):
        claim = start_app._unmarshal_claim({
            "claimId": {"S": _CLAIM},
            "deficiencyRaisedDateIso": {"NULL": True},
        })

        self.assertIsNone(claim["deficiencyRaisedDateIso"])


class TestHandlerDocumentFailure(unittest.TestCase):

    def test_document_load_failure_returns_500_and_starts_no_execution(self):
        """
        A document-load failure is infrastructure failure, not 'no evidence'.
        Proceeding with an empty list would mark every check NOT_FOUND on a
        claim that does have documents.
        """
        mock_sfn = MagicMock()

        with patch.object(start_app, "_fetch_claim", return_value={"claimId": {"S": _CLAIM}}), \
             patch.object(
                 start_app,
                 "_fetch_documents",
                 side_effect=ClientError({"Error": {"Code": "ThrottlingException"}}, "Query"),
             ), \
             patch.object(start_app.boto3, "client", return_value=mock_sfn):

            resp = start_app.handler(
                {
                    "requestContext": {"authorizer": {"jwt": {"claims": {"sub": _USER}}}},
                    "pathParameters": {"claimId": _CLAIM},
                },
                MagicMock(aws_request_id="req-1"),
            )

        self.assertEqual(resp["statusCode"], 500)
        mock_sfn.start_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
