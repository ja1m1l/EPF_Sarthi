"""
Unit tests for EvidenceAgent Lambda (Step 4).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from functions.evidence_agent.app import handler
from shared.models import EvidenceReport


class TestEvidenceAgentHandler:
    def test_stub_mode(self, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "true")
        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {"claimId": "claim-123"},
        }
        res = handler(event, None)
        assert "evidenceReport" in res
        assert res["evidenceReport"]["stubbed"] is True
        assert len(res["evidenceReport"]["checks"]) == 5

    @patch("shared.gemini.generate")
    def test_zero_documents_returns_all_not_found(self, mock_generate, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {"claimId": "claim-123"},
            "documents": [],
        }
        res = handler(event, None)
        assert "evidenceReport" in res
        report = res["evidenceReport"]
        assert report["stubbed"] is False
        assert len(report["checks"]) == 5
        for c in report["checks"]:
            assert c["verdict"] == "NOT_FOUND"
            assert c["sourceRef"] is None
        mock_generate.assert_not_called()

    @patch("functions.evidence_agent.app._put_downgrade_metric")
    @patch("shared.gemini.generate")
    def test_verbatim_excerpt_valid_confirmed(self, mock_generate, mock_metric, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        doc_text = "Member Aadhaar card 123456789012 verified. Bank account IFSC SBIN0001234 verified."
        mock_response = {
            "summary": "Evidence verified",
            "checks": [
                {
                    "checkId": "kyc_present",
                    "label": "KYC present",
                    "verdict": "CONFIRMED",
                    "sourceRef": {
                        "documentId": "doc-1",
                        "excerpt": "Member Aadhaar card 123456789012 verified.",
                    },
                    "note": "Aadhaar present.",
                }
            ],
        }
        mock_generate.return_value = json.dumps(mock_response)

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {"claimId": "claim-123"},
            "documents": [{"documentId": "doc-1", "title": "KYC Doc", "text": doc_text}],
        }
        res = handler(event, None)
        checks = res["evidenceReport"]["checks"]
        kyc_check = next(c for c in checks if c["checkId"] == "kyc_present")
        assert kyc_check["verdict"] == "CONFIRMED"
        assert kyc_check["sourceRef"]["excerpt"] == "Member Aadhaar card 123456789012 verified."
        mock_metric.assert_not_called()

    @patch("functions.evidence_agent.app._put_downgrade_metric")
    @patch("shared.gemini.generate")
    def test_unsupported_confirmed_downgraded_to_not_found_and_emits_metric(self, mock_generate, mock_metric, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        doc_text = "Some random document text."
        # Model returns CONFIRMED with hallucinated excerpt
        mock_response = {
            "summary": "Evidence check",
            "checks": [
                {
                    "checkId": "kyc_present",
                    "label": "KYC present",
                    "verdict": "CONFIRMED",
                    "sourceRef": {
                        "documentId": "doc-1",
                        "excerpt": "Hallucinated excerpt that does not exist in text",
                    },
                    "note": "Aadhaar verified.",
                }
            ],
        }
        mock_generate.return_value = json.dumps(mock_response)

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {"claimId": "claim-123"},
            "documents": [{"documentId": "doc-1", "title": "Doc", "text": doc_text}],
        }
        res = handler(event, None)
        checks = res["evidenceReport"]["checks"]
        kyc_check = next(c for c in checks if c["checkId"] == "kyc_present")
        # Downgraded to NOT_FOUND
        assert kyc_check["verdict"] == "NOT_FOUND"
        assert kyc_check["sourceRef"] is None
        mock_metric.assert_called_once()

    @patch("shared.gemini.generate")
    def test_contradicted_verdict_preserved(self, mock_generate, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        doc_text = "The applicant exit date is 2023-01-01, which contradicts form date."
        mock_response = {
            "summary": "Contradiction found",
            "checks": [
                {
                    "checkId": "date_of_exit_present",
                    "label": "Date of exit present",
                    "verdict": "CONTRADICTED",
                    "sourceRef": {
                        "documentId": "doc-1",
                        "excerpt": "The applicant exit date is 2023-01-01",
                    },
                    "note": "Date of exit conflicts.",
                }
            ],
        }
        mock_generate.return_value = json.dumps(mock_response)

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {"claimId": "claim-123"},
            "documents": [{"documentId": "doc-1", "title": "Exit Doc", "text": doc_text}],
        }
        res = handler(event, None)
        checks = res["evidenceReport"]["checks"]
        exit_check = next(c for c in checks if c["checkId"] == "date_of_exit_present")
        assert exit_check["verdict"] == "CONTRADICTED"
        assert exit_check["sourceRef"]["excerpt"] == "The applicant exit date is 2023-01-01"
