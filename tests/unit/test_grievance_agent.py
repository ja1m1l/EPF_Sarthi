"""
Unit tests for GrievanceAgent Lambda (Step 5).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from functions.grievance_agent.app import (
    HEADER_DISCLAIMER,
    GrievanceError,
    handler,
    redact_pii,
)


class TestGrievanceAgentPIIRedaction:
    def test_redact_pii_uan_and_bank_account(self):
        text = "My UAN is 123456789012 and my bank account is 9876543210123. Password=Secret123!"
        redacted = redact_pii(text)
        assert "123456789012" not in redacted
        assert "[REDACTED_UAN]" in redacted
        assert "9876543210123" not in redacted
        assert "[REDACTED_BANK_ACCOUNT]" in redacted
        assert "Secret123!" not in redacted

    def test_redact_pii_dictionary(self):
        data = {
            "uan": "123456789012",
            "bankAccount": "9876543210123",
            "password": "my_password",
            "claim": {"text": "UAN 123456789012"},
        }
        redacted = redact_pii(data)
        assert redacted["uan"] == "[REDACTED]"
        assert redacted["bankAccount"] == "[REDACTED]"
        assert redacted["password"] == "[REDACTED]"
        assert "[REDACTED_UAN]" in redacted["claim"]["text"]


class TestGrievanceAgentHandler:
    def test_stub_mode(self, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "true")
        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {"claimId": "claim-123", "amountPaise": 100000},
        }
        res = handler(event, None)
        assert "grievanceDraft" in res
        assert res["grievanceDraft"]["stubbed"] is True
        assert res["grievanceDraft"]["draftText"].startswith(HEADER_DISCLAIMER)

    @patch("shared.gemini.generate")
    def test_gemini_mode_grounded_success(self, mock_generate, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        draft_body = (
            f"{HEADER_DISCLAIMER}\n\n"
            "To The Commissioner, Grievance for FINAL_SETTLEMENT claim claim-123 filed on 2024-10-15 "
            "for INR 1,000.00. Rule timeline is 20 days per https://epfindia.gov.in."
        )
        mock_generate.return_value = draft_body

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {
                "claimId": "claim-123",
                "claimType": "FINAL_SETTLEMENT",
                "claimDateIso": "2024-10-15",
                "amountPaise": 100000,
            },
            "rulesDecision": {
                "applicable": True,
                "timelineDays": 20,
                "citedSourceUrls": ["https://epfindia.gov.in"],
            },
            "slaResult": {"status": "OVERDUE"},
            "evidenceReport": {"checks": []},
        }

        res = handler(event, None)
        assert "grievanceDraft" in res
        draft = res["grievanceDraft"]["draftText"]
        assert draft.startswith(HEADER_DISCLAIMER)
        assert "1,000.00" in draft
        # Verify gemini prompt did NOT contain UAN or Bank account
        call_args = mock_generate.call_args[1]["prompt"]
        assert "123456789012" not in call_args

    @patch("shared.gemini.generate")
    def test_ungrounded_draft_retries_and_raises_error(self, mock_generate, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        # Model returns ungrounded date 2099-01-01 and ungrounded amount INR 99,999.00
        ungrounded_draft = (
            f"{HEADER_DISCLAIMER}\n\n"
            "Grievance for claim claim-123 filed on 2099-01-01 for INR 99,999.00."
        )
        mock_generate.return_value = ungrounded_draft

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "analysisInput": {
                "claimId": "claim-123",
                "claimDateIso": "2024-10-15",
                "amountPaise": 100000,
            },
        }

        with pytest.raises(GrievanceError, match="DRAFT_UNGROUNDED"):
            handler(event, None)
        assert mock_generate.call_count == 2
