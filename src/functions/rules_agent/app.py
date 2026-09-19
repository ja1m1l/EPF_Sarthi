"""
Rules Agent Lambda Handler.

Invokes the pure select_rule() function for a given claim.
Guarantees strict separation between FAILED (infrastructure error, HTTP 500)
and ABSTAINED (legitimate abstention, HTTP 200).
"""

from __future__ import annotations

import json
import os
from typing import Any

from shared.logging import get_logger, set_correlation_id
from shared.models import Claim, RuleDecision
from shared.rules_agent import RulesAgentInfrastructureError, select_rule

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point for Rules Agent."""
    request_id = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    set_correlation_id(request_id)

    log.info("rules_agent.request.received", stage=STAGE)

    # Support API Gateway body string or direct Step Function payload
    body = event
    if "body" in event and isinstance(event["body"], str):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "FAILED", "error": "Invalid JSON body"}),
            }

    claim_data = body.get("claim")
    if not claim_data or not isinstance(claim_data, dict):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "FAILED", "error": "Missing 'claim' in payload"}),
        }

    rule_set_version = str(body.get("ruleSetVersion", "v1"))

    try:
        if "claimId" not in claim_data:
            import uuid
            claim_data["claimId"] = str(uuid.uuid4())
        if "createdAt" not in claim_data or "updatedAt" not in claim_data:
            from shared.models import utc_now_iso
            now = utc_now_iso()
            claim_data.setdefault("createdAt", now)
            claim_data.setdefault("updatedAt", now)

        claim = Claim.from_dict(claim_data)
    except Exception as exc:
        log.warning("rules_agent.claim_parse.error", error=str(exc))
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "FAILED", "error": f"Invalid claim data: {exc}"}),
        }

    try:
        decision: RuleDecision = select_rule(claim, rule_set_version=rule_set_version)
        status = "APPLICABLE" if decision.applicable else "ABSTAINED"

        log.info(
            "rules_agent.decision.completed",
            claimId=claim.claimId,
            status=status,
            applicable=decision.applicable,
            abstainReason=decision.abstainReason,
        )

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": status,
                "decision": decision.to_dict(),
            }),
        }

    except RulesAgentInfrastructureError as exc:
        log.error("rules_agent.infrastructure.failed", claimId=claim.claimId, error=str(exc))
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": "FAILED",
                "error": str(exc),
            }),
        }
    except Exception as exc:
        log.error("rules_agent.unexpected.error", claimId=claim.claimId, error=str(exc))
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": "FAILED",
                "error": f"Internal error: {exc}",
            }),
        }
