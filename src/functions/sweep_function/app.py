"""
SweepFunction – Daily watchdog for EPF Sentinel SLA timelines.
"""

from __future__ import annotations

import json
import os
import uuid
import time
from collections import defaultdict
from datetime import date
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

from shared.clock import today_ist
from shared.logging import get_logger, set_correlation_id
from shared.models import Claim, ClaimStatus, AnalysisRun
from shared.sla import compute_sla, SlaError, SlaResult

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE_NAME: str = os.environ.get("CLAIMS_TABLE_NAME", "")
ANALYSIS_RUNS_TABLE_NAME: str = os.environ.get("ANALYSIS_RUNS_TABLE_NAME", "")
STATUS_TRANSITIONS_TABLE_NAME: str = os.environ.get("STATUS_TRANSITIONS_TABLE_NAME", "")
STATUS_NOTIFICATIONS_TOPIC_ARN: str = os.environ.get("STATUS_NOTIFICATIONS_TOPIC_ARN", "")

MAX_CLAIMS_PER_SWEEP = 500
MAX_NOTIFICATIONS_PER_USER = 5

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)

    claims_table_name = os.environ.get("CLAIMS_TABLE_NAME", CLAIMS_TABLE_NAME)
    runs_table_name = os.environ.get("ANALYSIS_RUNS_TABLE_NAME", ANALYSIS_RUNS_TABLE_NAME)
    transitions_table_name = os.environ.get(
        "STATUS_TRANSITIONS_TABLE_NAME",
        STATUS_TRANSITIONS_TABLE_NAME,
    )
    
    log.info("sweep_function.started", correlationId=correlation_id, stage=STAGE)
    
    claims_table = dynamodb.Table(claims_table_name)
    runs_table = dynamodb.Table(runs_table_name)
    transitions_table = dynamodb.Table(transitions_table_name)
    
    today = today_ist()
    
    user_notification_counts: dict[str, int] = defaultdict(int)
    
    claims_processed = 0
    notifications_sent = 0
    
    # We must scan because byStatus GSI is partitioned by userId.
    scan_kwargs = {
        "IndexName": "byStatus",
        "FilterExpression": Attr("status").is_in([
            ClaimStatus.SUBMITTED.value,
            ClaimStatus.PENDING.value,
            ClaimStatus.UNDER_PROCESS.value
        ])
    }
    
    done = False
    start_key = None
    
    while not done and claims_processed < MAX_CLAIMS_PER_SWEEP:
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key
            
        response = claims_table.scan(**scan_kwargs)
        items = response.get("Items", [])
        
        for item in items:
            if claims_processed >= MAX_CLAIMS_PER_SWEEP:
                log.warning("sweep_function.claims_cap_hit", limit=MAX_CLAIMS_PER_SWEEP)
                boto3.client('cloudwatch').put_metric_data(
                    Namespace="EPFSentinel",
                    MetricData=[{
                        "MetricName": "SweepClaimsCapHit",
                        "Value": 1,
                        "Unit": "Count"
                    }]
                )
                break
                
            claim = Claim.from_dict(item)
            claims_processed += 1
            
            if not claim.latestRunId:
                continue
                
            # Fetch latest AnalysisRun
            run_resp = runs_table.get_item(Key={"claimId": claim.claimId, "runId": claim.latestRunId})
            run_item = run_resp.get("Item")
            if not run_item:
                continue
                
            run = AnalysisRun.from_dict(run_item)
            
            # Need rules output to compute SLA
            if not run.rulesAgentOutputJson:
                continue
                
            try:
                rules_output = json.loads(run.rulesAgentOutputJson)
            except json.JSONDecodeError:
                continue
                
            if not rules_output.get("applicable") or not rules_output.get("timelineDays") or not rules_output.get("timelineBasis"):
                continue
                
            timeline_days = int(rules_output["timelineDays"])
            basis = rules_output["timelineBasis"]
            
            # Compute current SLA
            claim_date = date.fromisoformat(claim.claimDateIso)
            deficiency_iso = claim.deficiencyRaisedDateIso
            deficiency_date = date.fromisoformat(deficiency_iso) if deficiency_iso else None
            
            try:
                # We assume no holidays are configured yet
                sla_result = compute_sla(
                    claim_date=claim_date,
                    timeline_days=timeline_days,
                    basis=basis,
                    today=today,
                    deficiency_raised_date=deficiency_date,
                    holidays=frozenset()
                )
            except SlaError as e:
                log.error("sweep_function.sla_compute_error", claimId=claim.claimId, error=str(e))
                continue
                
            # Get previous SLA status from run.slaAgentOutputJson
            prev_status = None
            if run.slaAgentOutputJson:
                try:
                    sla_output = json.loads(run.slaAgentOutputJson)
                    prev_status = sla_output.get("status")
                except json.JSONDecodeError:
                    pass
            
            current_status = sla_result.status
            
            if current_status in ("APPROACHING", "OVERDUE") and current_status != prev_status:
                # Candidate for notification!
                # Dedupe check
                dedupe_key = f"{prev_status or 'NONE'}#{current_status}#{sla_result.deadlineDate.isoformat()}"
                
                try:
                    transitions_table.put_item(
                        Item={
                            "claimId": claim.claimId,
                            "transitionKey": dedupe_key,
                            # TTL for 90 days
                            "expiresAt": int(time.time()) + 90 * 86400,
                            "status": current_status
                        },
                        ConditionExpression="attribute_not_exists(claimId) AND attribute_not_exists(transitionKey)"
                    )
                except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
                    # Already notified for this transition
                    continue
                    
                # User cap check
                if user_notification_counts[claim.userId] >= MAX_NOTIFICATIONS_PER_USER:
                    log.warning("sweep_function.user_cap_hit", userId=claim.userId)
                    boto3.client('cloudwatch').put_metric_data(
                        Namespace="EPFSentinel",
                        MetricData=[{
                            "MetricName": "UserNotificationCapHit",
                            "Value": 1,
                            "Unit": "Count"
                        }]
                    )
                    continue
                    
                # Publish to SNS
                message = {
                    "claimId": claim.claimId,
                    "userId": claim.userId,
                    "previousStatus": prev_status,
                    "newStatus": current_status,
                    "deadline": sla_result.deadlineDate.isoformat(),
                    "explanation": sla_result.explanation
                }
                sns.publish(
                    TopicArn=STATUS_NOTIFICATIONS_TOPIC_ARN,
                    Message=json.dumps(message),
                    Subject=f"EPF Claim {claim.claimId} SLA is {current_status}"
                )
                
                user_notification_counts[claim.userId] += 1
                notifications_sent += 1

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None
        
    log.info("sweep_function.completed", claimsProcessed=claims_processed, notificationsSent=notifications_sent)
    return {
        "claimsProcessed": claims_processed,
        "notificationsSent": notifications_sent
    }
