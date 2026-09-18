"""
Health-check Lambda handler.

Route: GET /health
Returns: {"status": "ok", "version": "<git sha>", "time": "<ISO-8601 UTC>"}

Environment variables
---------------------
GIT_SHA : str   Short Git SHA injected by SAM/CI (default "local").
STAGE   : str   Deployment stage (default "dev").
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from shared.logging import get_logger, set_correlation_id

log = get_logger(__name__)

GIT_SHA: str = os.environ.get("GIT_SHA", "local")
STAGE: str = os.environ.get("STAGE", "dev")


def handler(event: dict, context: object) -> dict:
    """AWS Lambda entry point for GET /health."""

    # Propagate the API Gateway request ID as the correlation ID so every
    # log line emitted by this invocation carries the same tracing token.
    request_id: str = (
        event.get("requestContext", {}).get("requestId", "")
        or getattr(context, "aws_request_id", "")
    )
    set_correlation_id(request_id)

    log.info(
        "health.request.received",
        method=event.get("requestContext", {}).get("http", {}).get("method", "GET"),
        stage=STAGE,
    )

    body = {
        "status": "ok",
        "version": GIT_SHA,
        "time": datetime.now(tz=timezone.utc).isoformat(),
    }

    log.info("health.response.sent", version=GIT_SHA, stage=STAGE)

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body),
    }
