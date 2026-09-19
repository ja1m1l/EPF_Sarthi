"""
Unit tests for ClaimAgent Lambda (Step 1).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from functions.claim_agent.app import (
    ValidationError,
    _parse_and_validate_amount,
    _parse_and_validate_date,
    _validate_enum,
    handler,
)
from shared.models import ClaimStatus, ClaimType


class TestClaimAgentValidators:
    def test_parse_and_validate_date_valid(self):
        assert _parse_and_validate_date("2024-10-15") == "2024-10-15"
        assert _parse_and_validate_date("15 Oct 2024") == "2024-10-15"

    def test_parse_and_validate_date_invalid(self):
        assert _parse_and_validate_date("not-a-date") is None
        assert _parse_and_validate_date(None) is None
        assert _parse_and_validate_date(12345) is None

    def test_parse_and_validate_amount_paise(self):
        assert _parse_and_validate_amount(100000) == 100000
        assert _parse_and_validate_amount(1000.0) == 100000
        assert _parse_and_validate_amount(50.50) == 5050

    def test_parse_and_validate_amount_invalid(self):
        assert _parse_and_validate_amount(-500) is None
        assert _parse_and_validate_amount("invalid") is None
        assert _parse_and_validate_amount(None) is None

    def test_validate_enum_valid(self):
        assert _validate_enum("FINAL_SETTLEMENT", ClaimType) == "FINAL_SETTLEMENT"
        assert _validate_enum("SUBMITTED", ClaimStatus) == "SUBMITTED"

    def test_validate_enum_invalid(self):
        assert _validate_enum("INVALID_TYPE", ClaimType) is None
        assert _validate_enum("UNKNOWN", ClaimStatus) is None
        assert _validate_enum(None, ClaimStatus) is None


class TestClaimAgentHandler:
    def test_stub_mode_success(self, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "true")
        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "claim": {
                "userId": "user-1",
                "claimId": "claim-123",
                "claimType": "FINAL_SETTLEMENT",
                "claimDateIso": "2024-10-15",
                "amountPaise": 100000,
                "status": "SUBMITTED",
                "createdAt": "2024-10-15T00:00:00+00:00",
                "updatedAt": "2024-10-15T00:00:00+00:00",
            },
        }
        res = handler(event, None)
        assert "analysisInput" in res
        assert res["analysisInput"]["amountPaise"] == 100000

    @patch("shared.gemini.generate")
    def test_gemini_mode_success(self, mock_generate, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        mock_generate.return_value = '{"claimType": "FINAL_SETTLEMENT", "claimDateIso": "2024-10-15", "amountPaise": 100000, "status": "SUBMITTED", "deficiencyRaisedDateIso": null}'

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "claim": {
                "userId": "user-1",
                "claimId": "claim-123",
                "freeText": "I filed a final settlement claim on 2024-10-15 for 1000 rupees.",
            },
        }
        res = handler(event, None)
        assert "analysisInput" in res
        assert res["analysisInput"]["claimType"] == "FINAL_SETTLEMENT"
        assert res["analysisInput"]["amountPaise"] == 100000

    @patch("shared.gemini.generate")
    def test_gemini_mode_invalid_enum_dropped_raises_validation_error(self, mock_generate, monkeypatch):
        monkeypatch.setenv("USE_STUBS", "false")
        # Gemini returns an invalid status "INVALID_STATUS"
        mock_generate.return_value = '{"claimType": "FINAL_SETTLEMENT", "claimDateIso": "2024-10-15", "amountPaise": 100000, "status": "INVALID_STATUS", "deficiencyRaisedDateIso": null}'

        event = {
            "correlationId": "corr-123",
            "runId": "run-123",
            "claimId": "claim-123",
            "claim": {"userId": "user-1"},
        }
        with pytest.raises(ValidationError, match="status"):
            handler(event, None)
