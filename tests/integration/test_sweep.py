"""
Integration tests for the SweepFunction watchdog loop.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import boto3
import pytest

# Attempt to import the sweep function handler
try:
    from functions.sweep_function.app import handler
except ImportError:
    # Handle pytest running from project root
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "../../src"))
    from functions.sweep_function.app import handler

from shared.models import Claim, ClaimStatus, ClaimType, AnalysisRun

@pytest.fixture
def dynamodb():
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"))

@pytest.fixture
def claims_table_name():
    # Attempt to get from environment or deduce from SAM outputs
    return os.environ.get("CLAIMS_TABLE_NAME", "epf-sentinel-Claims-dev")

@pytest.fixture
def runs_table_name():
    return os.environ.get("ANALYSIS_RUNS_TABLE_NAME", "epf-sentinel-AnalysisRuns-dev")

@pytest.fixture
def transitions_table_name():
    return os.environ.get("STATUS_TRANSITIONS_TABLE_NAME", "epf-sentinel-StatusTransitions-dev")


def test_sweep_function_deduplication(dynamodb, user_a, claims_table_name, runs_table_name, transitions_table_name, monkeypatch):
    """
    Seed 3 claims straddling the deadline boundary, run the sweep twice, 
    and assert exactly one notification per genuine transition and zero on the second run.
    """
    # Setup env vars for handler
    monkeypatch.setenv("STAGE", "dev")
    monkeypatch.setenv("CLAIMS_TABLE_NAME", claims_table_name)
    monkeypatch.setenv("ANALYSIS_RUNS_TABLE_NAME", runs_table_name)
    monkeypatch.setenv("STATUS_TRANSITIONS_TABLE_NAME", transitions_table_name)
    monkeypatch.setenv("STATUS_NOTIFICATIONS_TOPIC_ARN", "arn:aws:sns:dummy")

    claims_table = dynamodb.Table(claims_table_name)
    runs_table = dynamodb.Table(runs_table_name)
    
    # Clean up any existing data for user_a
    # We'll just create new unique claims
    
    today = date.today()
    
    test_claims = []
    
    # Claim 1: WITHIN SLA (e.g. filed today, 20 days timeline)
    # Expected: No transition
    c1 = Claim.new(
        userId=user_a.email,
        claimType=ClaimType.FINAL_SETTLEMENT,
        claimDateIso=today.isoformat(),
        amountPaise=10000,
        status=ClaimStatus.SUBMITTED
    )
    
    # Claim 2: APPROACHING SLA (e.g. filed 18 days ago, 20 days timeline)
    # Expected: Transition to APPROACHING
    c2 = Claim.new(
        userId=user_a.email,
        claimType=ClaimType.FINAL_SETTLEMENT,
        claimDateIso=(today - timedelta(days=18)).isoformat(),
        amountPaise=20000,
        status=ClaimStatus.UNDER_PROCESS
    )
    
    # Claim 3: OVERDUE SLA (e.g. filed 25 days ago, 20 days timeline)
    # Expected: Transition to OVERDUE
    c3 = Claim.new(
        userId=user_a.email,
        claimType=ClaimType.FINAL_SETTLEMENT,
        claimDateIso=(today - timedelta(days=25)).isoformat(),
        amountPaise=30000,
        status=ClaimStatus.PENDING
    )
    
    for c in [c1, c2, c3]:
        # Create a mock AnalysisRun for each claim
        run = AnalysisRun.new(
            claimId=c.claimId,
            correlationId=str(uuid.uuid4()),
            expiresAt=int(today.strftime("%s")) + 86400
        )
        
        # Inject rules agent output so SLA engine can run
        run.rulesAgentOutputJson = json.dumps({
            "applicable": True,
            "timelineDays": 20,
            "timelineBasis": "CALENDAR"
        })
        
        # Inject previous SLA status as WITHIN (so c2 and c3 will trigger transition)
        run.slaAgentOutputJson = json.dumps({
            "status": "WITHIN"
        })
        
        c.latestRunId = run.runId
        
        claims_table.put_item(Item=c.to_dict())
        runs_table.put_item(Item=run.to_dict())
        
        test_claims.append(c)
        
    # Run the sweep for the first time
    with patch("functions.sweep_function.app.sns") as mock_sns:
        result1 = handler({}, {})
        
        # Should have processed our claims (and possibly others depending on DB state)
        assert result1["claimsProcessed"] >= 3
        
        # We expect exactly 2 notifications (for c2 and c3)
        # Note: In a shared DB, there might be other claims. We filter to ensure our specific claims got notified.
        notified_claim_ids = []
        for call in mock_sns.publish.call_args_list:
            msg = json.loads(call.kwargs["Message"])
            notified_claim_ids.append(msg["claimId"])
            
        assert c2.claimId in notified_claim_ids
        assert c3.claimId in notified_claim_ids
        assert c1.claimId not in notified_claim_ids

    # Run the sweep for the second time
    with patch("functions.sweep_function.app.sns") as mock_sns2:
        result2 = handler({}, {})
        
        notified_claim_ids_2 = []
        for call in mock_sns2.publish.call_args_list:
            msg = json.loads(call.kwargs["Message"])
            notified_claim_ids_2.append(msg["claimId"])
            
        # Due to deduplication, our claims should NOT be notified again
        assert c2.claimId not in notified_claim_ids_2
        assert c3.claimId not in notified_claim_ids_2
        assert c1.claimId not in notified_claim_ids_2
