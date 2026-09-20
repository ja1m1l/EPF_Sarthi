"""
CloudWatch metrics for EPF Sentinel.

Namespace: EPFSentinel

The four terminal analysis statuses are emitted as the same metric name
with a Status dimension — never collapsed into a single Failure series.
"""

from __future__ import annotations

import os
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from shared.logging import get_logger

log = get_logger(__name__)

NAMESPACE = "EPFSentinel"

# Terminal statuses — each is a separate dashboard series.
TERMINAL_STATUSES = (
    "COMPLETED",
    "COMPLETED_WITH_ABSTENTION",
    "FAILED_INFRASTRUCTURE",
    "FAILED_VALIDATION",
)


def _client():
    return boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def put_metric(
    name: str,
    value: float,
    *,
    unit: str = "Count",
    dimensions: Optional[dict[str, str]] = None,
) -> None:
    """Best-effort PutMetricData. Failures are logged, never raised."""
    if os.environ.get("EPF_METRICS_DISABLED") == "1":
        return
    metric: dict = {
        "MetricName": name,
        "Value": float(value),
        "Unit": unit,
    }
    if dimensions:
        metric["Dimensions"] = [{"Name": k, "Value": str(v)} for k, v in dimensions.items()]
    try:
        _client().put_metric_data(Namespace=NAMESPACE, MetricData=[metric])
    except (BotoCoreError, ClientError, Exception) as exc:
        log.warning("metrics.put_failed", metric=name, error=str(exc))


def analysis_started() -> None:
    put_metric("AnalysisStarted", 1)


def analysis_status(status: str, latency_ms: Optional[float] = None) -> None:
    """Emit one of the four terminal statuses as its own series."""
    put_metric("AnalysisStatus", 1, dimensions={"Status": status})
    if status in ("COMPLETED", "COMPLETED_WITH_ABSTENTION"):
        put_metric("AnalysisCompleted", 1)
    if latency_ms is not None:
        put_metric("AnalysisLatencyMs", latency_ms, unit="Milliseconds")


def gemini_call(
    *,
    latency_ms: float,
    tokens: int = 0,
    error: bool = False,
    status_code: Optional[int] = None,
) -> None:
    put_metric("GeminiCalls", 1)
    put_metric("GeminiLatencyMs", latency_ms, unit="Milliseconds")
    if tokens:
        put_metric("GeminiTokens", tokens)
    if error:
        code = str(status_code or "unknown")
        put_metric("GeminiErrors", 1, dimensions={"StatusCode": code})


def daily_cap_hit() -> None:
    put_metric("DailyAnalysisCapHit", 1)
