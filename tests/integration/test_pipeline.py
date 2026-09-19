"""
Integration test: EPF Sentinel Step Functions analysis pipeline.

Starts a real Express execution for the "demo claim" (FINAL_SETTLEMENT,
₹1,000.00) and asserts that the persisted AnalysisRun contains:
  1. Non-null outputs from all five agent states.
  2. A single, consistent correlationId that matches what the API returned.
  3. Terminal status is COMPLETED or COMPLETED_WITH_ABSTENTION (never a failure).

Required environment variables
--------------------------------
API_ENDPOINT          Base URL of the deployed HTTP API (no trailing slash).
COGNITO_USER_POOL_ID  The Cognito User Pool ID.
COGNITO_CLIENT_ID     The Cognito App Client ID.
AWS_DEFAULT_REGION    (defaults to ap-south-1 if absent)

Usage
-----
    # Export stack outputs first (see integration/conftest.py for full instructions).
    python -m pytest tests/integration/test_pipeline.py -v --tb=short
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
import requests

# Re-use the Cognito fixtures from the existing conftest.
# conftest.py already exports: api_endpoint, user_a, user_b, _auth_headers

POLL_INTERVAL_S = 2
POLL_MAX_WAIT_S = 60  # Express executions are fast; 60s is very conservative


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _create_demo_claim(api_endpoint: str, access_token: str) -> dict:
    """Create a FINAL_SETTLEMENT demo claim and return the response body."""
    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "claimType": "FINAL_SETTLEMENT",
        "claimDateIso": today_iso,
        "amountPaise": 100000,  # INR 1,000.00
        "status": "SUBMITTED",
    }
    resp = requests.post(
        f"{api_endpoint}/claims",
        json=payload,
        headers=_auth_headers(access_token),
        timeout=10,
    )
    assert resp.status_code == 201, (
        f"Failed to create demo claim: {resp.status_code} {resp.text}"
    )
    return resp.json()


def _start_analysis(api_endpoint: str, access_token: str, claim_id: str) -> dict:
    """Start analysis execution and return 202 body."""
    resp = requests.post(
        f"{api_endpoint}/claims/{claim_id}/analyze",
        headers=_auth_headers(access_token),
        timeout=15,
    )
    assert resp.status_code == 202, (
        f"Expected 202 from start_analysis, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "runId" in body, "202 response must contain runId"
    assert "correlationId" in body, "202 response must contain correlationId"
    assert body["status"] == "RUNNING", "202 response status must be RUNNING"
    return body


def _poll_run(
    api_endpoint: str,
    access_token: str,
    claim_id: str,
    run_id: str,
    max_wait: int = POLL_MAX_WAIT_S,
) -> dict:
    """Poll GET /claims/{claimId}/runs/{runId} until status is terminal."""
    deadline = time.time() + max_wait
    terminal_statuses = {
        "COMPLETED",
        "COMPLETED_WITH_ABSTENTION",
        "FAILED_INFRASTRUCTURE",
        "FAILED_VALIDATION",
    }

    while time.time() < deadline:
        resp = requests.get(
            f"{api_endpoint}/claims/{claim_id}/runs/{run_id}",
            headers=_auth_headers(access_token),
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"GET run returned {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        status = body.get("status", "")
        if status in terminal_statuses or status not in ("RUNNING", ""):
            return body
        time.sleep(POLL_INTERVAL_S)

    pytest.fail(
        f"Analysis run {run_id} did not reach a terminal status within {max_wait}s"
    )


# ─────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────

class TestAnalysisPipeline:
    """
    Integration tests for the five-state analysis pipeline.
    Uses a real Express SFN execution against the deployed stack.
    """

    def test_happy_path_all_five_agent_outputs_present(
        self,
        api_endpoint: str,
        user_a,
    ) -> None:
        """
        Happy path: Start an execution, poll until terminal, then assert:
        - Status is COMPLETED or COMPLETED_WITH_ABSTENTION (never a failure).
        - All five agent outputs are present and non-null.
        - The correlationId in the persisted run matches the one returned by the API.
        """
        token = user_a.access_token

        # 1. Create demo claim
        claim_body = _create_demo_claim(api_endpoint, token)
        claim_id = claim_body["claimId"]

        # 2. Start analysis — returns 202 immediately
        start_body = _start_analysis(api_endpoint, token, claim_id)
        run_id: str = start_body["runId"]
        api_correlation_id: str = start_body["correlationId"]

        assert api_correlation_id, "correlationId must be non-empty"

        # 3. Poll until terminal
        run = _poll_run(api_endpoint, token, claim_id, run_id)

        # 4. Status must be a success variant — NEVER a failure
        status = run.get("status", "")
        assert status in (
            "COMPLETED",
            "COMPLETED_WITH_ABSTENTION",
        ), (
            f"Expected COMPLETED or COMPLETED_WITH_ABSTENTION, got {status!r}. "
            f"failureDetail: {run.get('failureDetail')}"
        )

        # 5. All five agent outputs must be present and non-null
        assert run.get("claimAgentOutputJson") is not None, (
            "claimAgentOutputJson must be present"
        )
        assert run.get("rulesAgentOutputJson") is not None, (
            "rulesAgentOutputJson must be present"
        )
        assert run.get("slaAgentOutputJson") is not None, (
            "slaAgentOutputJson must be present"
        )
        assert run.get("evidenceAgentOutputJson") is not None, (
            "evidenceAgentOutputJson must be present"
        )
        assert run.get("grievanceAgentOutputJson") is not None, (
            "grievanceAgentOutputJson must be present"
        )

        # 6. correlationId in persisted run must match what the API returned
        persisted_correlation_id = run.get("correlationId", "")
        assert persisted_correlation_id == api_correlation_id, (
            f"correlationId mismatch: API returned {api_correlation_id!r}, "
            f"persisted run has {persisted_correlation_id!r}"
        )

    def test_claim_agent_output_matches_claim(
        self,
        api_endpoint: str,
        user_a,
    ) -> None:
        """The ClaimAgent output (analysisInput) must echo the original claim fields."""
        token = user_a.access_token

        claim_body = _create_demo_claim(api_endpoint, token)
        claim_id = claim_body["claimId"]

        start_body = _start_analysis(api_endpoint, token, claim_id)
        run_id = start_body["runId"]

        run = _poll_run(api_endpoint, token, claim_id, run_id)

        claim_agent_out = run.get("claimAgentOutputJson")
        assert isinstance(claim_agent_out, dict), (
            "claimAgentOutputJson must be a parsed dict (GetRun inline-parses JSON)"
        )
        assert claim_agent_out.get("claimId") == claim_id, (
            "analysisInput.claimId must match the original claim"
        )
        assert claim_agent_out.get("claimType") == "FINAL_SETTLEMENT"
        assert claim_agent_out.get("amountPaise") == 100000

    def test_rules_agent_output_has_applicable_field(
        self,
        api_endpoint: str,
        user_a,
    ) -> None:
        """RulesAgent output must have an 'applicable' boolean field."""
        token = user_a.access_token

        claim_body = _create_demo_claim(api_endpoint, token)
        claim_id = claim_body["claimId"]

        start_body = _start_analysis(api_endpoint, token, claim_id)
        run_id = start_body["runId"]

        run = _poll_run(api_endpoint, token, claim_id, run_id)

        rules_out = run.get("rulesAgentOutputJson")
        assert isinstance(rules_out, dict)
        assert "applicable" in rules_out, "rulesAgentOutputJson must contain 'applicable'"
        assert isinstance(rules_out["applicable"], bool)

    def test_sla_agent_output_present_when_rules_applicable(
        self,
        api_endpoint: str,
        user_a,
    ) -> None:
        """When rules applied, slaAgentOutputJson must have skipped=False and a deadline date."""
        token = user_a.access_token

        claim_body = _create_demo_claim(api_endpoint, token)
        claim_id = claim_body["claimId"]

        start_body = _start_analysis(api_endpoint, token, claim_id)
        run_id = start_body["runId"]

        run = _poll_run(api_endpoint, token, claim_id, run_id)

        rules_out = run.get("rulesAgentOutputJson", {})
        sla_out = run.get("slaAgentOutputJson")
        assert isinstance(sla_out, dict)

        if rules_out.get("applicable"):
            assert sla_out.get("skipped") is False, (
                "slaResult.skipped must be False when rules applied"
            )
            assert "deadlineDateIso" in sla_out, (
                "slaResult must contain deadlineDateIso when not skipped"
            )
            assert sla_out.get("status") in ("WITHIN", "APPROACHING", "OVERDUE")
        else:
            # When rules abstained, SLA is correctly skipped
            assert sla_out.get("skipped") is True

    def test_evidence_and_grievance_outputs_are_stubbed(
        self,
        api_endpoint: str,
        user_a,
    ) -> None:
        """EvidenceAgent and GrievanceAgent outputs must have stubbed=True in stub mode."""
        token = user_a.access_token

        claim_body = _create_demo_claim(api_endpoint, token)
        claim_id = claim_body["claimId"]

        start_body = _start_analysis(api_endpoint, token, claim_id)
        run_id = start_body["runId"]

        run = _poll_run(api_endpoint, token, claim_id, run_id)

        evidence_out = run.get("evidenceAgentOutputJson", {})
        assert isinstance(evidence_out, dict)
        assert evidence_out.get("stubbed") is True, (
            "evidenceReport.stubbed must be True in stub mode"
        )
        assert "documents" in evidence_out
        assert "summary" in evidence_out

        grievance_out = run.get("grievanceAgentOutputJson", {})
        assert isinstance(grievance_out, dict)
        assert grievance_out.get("stubbed") is True, (
            "grievanceDraft.stubbed must be True in stub mode"
        )
        assert "draftText" in grievance_out

    def test_tenant_isolation_cannot_start_analysis_on_another_users_claim(
        self,
        api_endpoint: str,
        user_a,
        user_b,
    ) -> None:
        """User B cannot start analysis on User A's claim."""
        # Create claim as User A
        claim_body = _create_demo_claim(api_endpoint, user_a.access_token)
        claim_id = claim_body["claimId"]

        # Attempt to start analysis as User B
        resp = requests.post(
            f"{api_endpoint}/claims/{claim_id}/analyze",
            headers=_auth_headers(user_b.access_token),
            timeout=10,
        )
        assert resp.status_code == 404, (
            f"User B should get 404 on User A's claim, got {resp.status_code}"
        )

    def test_get_run_tenant_isolation(
        self,
        api_endpoint: str,
        user_a,
        user_b,
    ) -> None:
        """User B cannot fetch User A's analysis run."""
        # Create and start as User A
        claim_body = _create_demo_claim(api_endpoint, user_a.access_token)
        claim_id = claim_body["claimId"]

        start_body = _start_analysis(api_endpoint, user_a.access_token, claim_id)
        run_id = start_body["runId"]

        # User B attempts to fetch the run
        resp = requests.get(
            f"{api_endpoint}/claims/{claim_id}/runs/{run_id}",
            headers=_auth_headers(user_b.access_token),
            timeout=10,
        )
        assert resp.status_code == 404, (
            f"User B should get 404 on User A's run, got {resp.status_code}"
        )
