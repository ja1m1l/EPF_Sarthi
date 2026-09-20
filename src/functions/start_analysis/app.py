"""
StartAnalysis Lambda — POST /claims/{claimId}/analyze

Starts a Step Functions Express execution for the requested claim,
creates an AnalysisRun row in DynamoDB with status=RUNNING,
and returns 202 with the runId immediately.

Security: Cognito JWT authorizer.  userId extracted from JWT claims.
Tenant-scope: claim must belong to the authenticated user.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import BotoCoreError, ClientError
from decimal import Decimal

from shared.clock import today_ist
from shared.http import internal_error, not_found, ok, too_many_requests, _response
from shared.logging import get_logger, set_correlation_id
from shared.metrics import analysis_started, daily_cap_hit
from shared.models import AnalysisRun, Claim, utc_now_iso

log = get_logger(__name__)

STAGE: str = os.environ.get("STAGE", "dev")
CLAIMS_TABLE: str = os.environ.get("CLAIMS_TABLE_NAME", f"epf-sentinel-Claims-{STAGE}")
ANALYSIS_RUNS_TABLE: str = os.environ.get(
    "ANALYSIS_RUNS_TABLE_NAME", f"epf-sentinel-AnalysisRuns-{STAGE}"
)
DOCUMENTS_TABLE: str = os.environ.get(
    "DOCUMENTS_TABLE_NAME", f"epf-sentinel-Documents-{STAGE}"
)
STATE_MACHINE_ARN: str = os.environ.get("ANALYSIS_SFN_ARN", "")
QUOTA_TABLE: str = os.environ.get("ANALYSIS_QUOTA_TABLE_NAME", "")
DAILY_ANALYSIS_CAP: int = int(os.environ.get("DAILY_ANALYSIS_CAP", "20"))
TTL_90_DAYS: int = 90 * 24 * 60 * 60

# Only EXTRACTED documents carry text the EvidenceAgent's substring check can
# run against.  PROCESSING and NEEDS_MANUAL_ENTRY documents are deliberately
# excluded so a failed extraction never reaches the model as if it were a
# successful read.
_EVIDENCE_READY_STATUS: str = "EXTRACTED"

_DOCUMENT_KIND_TITLES: dict[str, str] = {
    "KYC": "KYC / identity proof",
    "BANK": "Bank account details",
    "DATE_OF_EXIT": "Date of exit / relieving letter",
    "DEFICIENCY": "Deficiency communication from EPFO",
    "CLAIM_AMOUNT": "Claim form / settlement amount",
}


def _get_user_id(event: dict) -> str:
    """Extract userId from Cognito JWT authorizer context."""
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "")
    )


def _fetch_claim(user_id: str, claim_id: str) -> dict | None:
    """Fetch claim from DynamoDB; returns None if not found."""
    ddb = boto3.client("dynamodb")
    resp = ddb.get_item(
        TableName=CLAIMS_TABLE,
        Key={
            "userId":  {"S": user_id},
            "claimId": {"S": claim_id},
        },
    )
    return resp.get("Item")


_deserializer = TypeDeserializer()


def _unmarshal_claim(item: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a marshalled DynamoDB claim into the plain dict the pipeline expects.

    Numeric attributes must survive as numbers: amountPaise is integer paise
    and the RulesAgent rejects it outright if it arrives as a string.
    TypeDeserializer yields Decimal for N attributes, so whole numbers are
    narrowed back to int here.
    """
    claim: dict[str, Any] = {}
    for key, value in item.items():
        deserialized = _deserializer.deserialize(value)
        if isinstance(deserialized, Decimal):
            deserialized = int(deserialized) if deserialized % 1 == 0 else float(deserialized)
        claim[key] = deserialized
    return claim


def _fetch_documents(user_id: str, claim_id: str) -> list[dict[str, Any]]:
    """
    Load the claim's extracted documents for the EvidenceAgent.

    Queries the Documents ByClaimId GSI and returns only documents whose
    status is EXTRACTED and whose owner matches the authenticated user.
    The ownership filter is a second tenancy check on top of the claim-scope
    check already performed by the caller: the GSI is partitioned by claimId
    alone, so it is not tenant-partitioned by itself.

    ``text`` is passed through verbatim — the EvidenceAgent downgrades any
    CONFIRMED verdict whose excerpt is not a literal substring of it.
    """
    ddb = boto3.client("dynamodb")
    documents: list[dict[str, str]] = []
    start_key: dict | None = None

    while True:
        kwargs: dict[str, Any] = {
            "TableName": DOCUMENTS_TABLE,
            "IndexName": "ByClaimId",
            "KeyConditionExpression": "claimId = :cid",
            "ExpressionAttributeValues": {":cid": {"S": claim_id}},
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key

        resp = ddb.query(**kwargs)

        for item in resp.get("Items", []):
            if item.get("userId", {}).get("S") != user_id:
                log.warning(
                    "start_analysis.documents.owner_mismatch",
                    claimId=claim_id,
                    documentId=item.get("documentId", {}).get("S", ""),
                )
                continue
            if item.get("status", {}).get("S") != _EVIDENCE_READY_STATUS:
                continue

            text = item.get("extractedText", {}).get("S", "")
            if not text:
                continue

            document_id = item.get("documentId", {}).get("S", "")
            kind = item.get("documentKind", {}).get("S", "")
            title = _DOCUMENT_KIND_TITLES.get(kind) or item.get("s3Key", {}).get("S", document_id)
            payload: dict[str, Any] = {
                "documentId": document_id,
                "title": title,
                "text": text,
            }
            if kind:
                payload["documentKind"] = kind
            documents.append(payload)

        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break

    return documents


def _consume_daily_quota(user_id: str) -> bool:
    """
    Increment today's analysis count for this user.

    Returns False when the per-user daily cap is already exhausted.
    Disabled when ANALYSIS_QUOTA_TABLE_NAME is unset (unit tests).
    """
    if not QUOTA_TABLE or DAILY_ANALYSIS_CAP <= 0:
        return True

    day = today_ist().isoformat()
    expires_at = int(time.time()) + 3 * 24 * 60 * 60
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(QUOTA_TABLE)
    try:
        table.update_item(
            Key={"userId": user_id, "dayIso": day},
            UpdateExpression="ADD analysisCount :one SET expiresAt = if_not_exists(expiresAt, :ttl)",
            ConditionExpression="attribute_not_exists(analysisCount) OR analysisCount < :cap",
            ExpressionAttributeValues={
                ":one": 1,
                ":cap": DAILY_ANALYSIS_CAP,
                ":ttl": expires_at,
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            daily_cap_hit()
            log.warning("start_analysis.daily_cap_hit", userId=user_id, dayIso=day, cap=DAILY_ANALYSIS_CAP)
            return False
        raise


def _save_run(run: AnalysisRun, claim: dict) -> None:
    """Persist initial RUNNING run record to DynamoDB."""
    ddb = boto3.client("dynamodb")
    expires_at = int(time.time()) + TTL_90_DAYS
    item = {
        "claimId":       {"S": run.claimId},
        "runId":         {"S": run.runId},
        "correlationId": {"S": run.correlationId},
        "status":        {"S": run.status},
        "startedAt":     {"S": run.startedAt},
        "expiresAt":     {"N": str(expires_at)},
    }
    if run.stepFunctionExecutionArn:
        item["stepFunctionExecutionArn"] = {"S": run.stepFunctionExecutionArn}
    ddb.put_item(TableName=ANALYSIS_RUNS_TABLE, Item=item)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point — POST /claims/{claimId}/analyze."""
    request_id = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)

    log.info("start_analysis.request.received", correlationId=correlation_id, stage=STAGE)

    user_id = _get_user_id(event)
    if not user_id:
        return _response(401, {"error": "Unauthorized"})

    claim_id: str = (
        event.get("pathParameters") or {}
    ).get("claimId", "")
    if not claim_id:
        return _response(400, {"error": "Missing claimId path parameter"})

    # ── Fetch and tenant-scope the claim ────────────────────────────────────
    try:
        raw_item = _fetch_claim(user_id, claim_id)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.dynamo.fetch_failed",
            correlationId=correlation_id,
            claimId=claim_id,
            error=str(exc),
        )
        return internal_error()

    if not raw_item:
        return not_found()

    try:
        if not _consume_daily_quota(user_id):
            return too_many_requests(
                "ANALYSIS_CAP_EXCEEDED",
                f"Daily analysis cap of {DAILY_ANALYSIS_CAP} has been reached. Try again tomorrow.",
            )
    except (BotoCoreError, ClientError) as exc:
        log.error("start_analysis.quota.failed", correlationId=correlation_id, error=str(exc))
        return internal_error()

    claim_dict = _unmarshal_claim(raw_item)

    # ── Load extracted documents for the EvidenceAgent ──────────────────────
    # A failure here is infrastructure failure, not "no evidence".  Proceeding
    # with an empty list would render every check NOT_FOUND on a claim that
    # actually has documents, which is indistinguishable from a genuine
    # absence of evidence — exactly the collapse we must not allow.
    try:
        documents = _fetch_documents(user_id, claim_id)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.documents.fetch_failed",
            correlationId=correlation_id,
            claimId=claim_id,
            error=str(exc),
        )
        return internal_error()

    log.info(
        "start_analysis.documents.loaded",
        correlationId=correlation_id,
        claimId=claim_id,
        documentCount=len(documents),
    )

    # ── Start Step Functions execution ───────────────────────────────────────
    run_id = str(uuid.uuid4())
    sfn_input = {
        "correlationId": correlation_id,
        "runId": run_id,
        "claimId": claim_id,
        "claim": claim_dict,
        "documents": documents,
        "startedAt": utc_now_iso(),
    }

    sfn = boto3.client("stepfunctions")
    try:
        sfn_resp = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=run_id,  # unique per execution
            input=json.dumps(sfn_input),
        )
        execution_arn = sfn_resp.get("executionArn", "")
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.sfn.start_failed",
            correlationId=correlation_id,
            runId=run_id,
            claimId=claim_id,
            error=str(exc),
        )
        return internal_error()

    # ── Persist initial RUNNING run record ───────────────────────────────────
    run = AnalysisRun.new(
        claimId=claim_id,
        correlationId=correlation_id,
        expiresAt=int(time.time()) + TTL_90_DAYS,
        stepFunctionExecutionArn=execution_arn,
    )
    # Override the auto-generated runId with the one we sent to SFN
    run.runId = run_id  # type: ignore[misc]

    try:
        _save_run(run, claim_dict)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "start_analysis.dynamo.save_failed",
            correlationId=correlation_id,
            runId=run_id,
            error=str(exc),
        )
        # Execution already started — best-effort save failure only logged
        return internal_error()

    analysis_started()
    log.info(
        "start_analysis.started",
        correlationId=correlation_id,
        runId=run_id,
        claimId=claim_id,
        executionArn=execution_arn,
    )

    return _response(202, {
        "runId": run_id,
        "correlationId": correlation_id,
        "status": "RUNNING",
    })
