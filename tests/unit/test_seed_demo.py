from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"
SPEC = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
assert SPEC and SPEC.loader
seed_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_demo)


def _response(body: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = body
    return response


def test_ensure_claim_reuses_exact_marked_claim():
    claim = {
        "claimId": "claim-1",
        "claimType": "FINAL_SETTLEMENT",
        "claimDateIso": "2026-08-01",
        "amountPaise": 48_000_000,
        "status": "PENDING",
        "notes": seed_demo.DEMO_MARKER,
    }
    with patch.object(seed_demo, "_request", return_value=_response({"claims": [claim]})) as request:
        result, created = seed_demo._ensure_claim("https://api.example", "token")

    assert result == claim
    assert created is False
    request.assert_called_once_with("GET", "https://api.example/claims", "token")


def test_ensure_claim_creates_current_api_contract_when_missing():
    with patch.object(
        seed_demo,
        "_request",
        side_effect=[
            _response({"claims": []}),
            _response({"claimId": "claim-2"}),
        ],
    ) as request:
        result, created = seed_demo._ensure_claim("https://api.example", "token")

    assert result["claimId"] == "claim-2"
    assert created is True
    assert request.call_args_list[1].kwargs["json_body"] == {
        "claimType": "FINAL_SETTLEMENT",
        "claimDate": "2026-08-01",
        "amountRupees": "480000",
        "status": "PENDING",
        "notes": seed_demo.DEMO_MARKER,
    }


def test_render_demo_png_is_a_real_png():
    png = seed_demo._render_demo_png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1_000


def test_ensure_document_reuses_extracted_demo_document():
    existing = {
        "documentId": "document-1",
        "documentKind": seed_demo.DEMO_DOCUMENT_KIND,
        "status": "EXTRACTED",
    }
    with (
        patch.object(seed_demo, "_existing_document", return_value=existing),
        patch.object(seed_demo, "_request") as request,
    ):
        result, created = seed_demo._ensure_document(
            api_endpoint="https://api.example",
            token="token",
            claim_id="claim-1",
            user_id="user-1",
            documents_table="documents",
            region="ap-south-1",
        )

    assert result == existing
    assert created is False
    request.assert_not_called()
