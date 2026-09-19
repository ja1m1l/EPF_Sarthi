"""
Gemini API client for EPF Sentinel.

The single shared client every module uses. Exposes ``generate()`` and
``embed()``. The API key is read from AWS Secrets Manager at cold start
and cached in module scope.

Design rules
------------
* Model IDs are pinned in ``MODEL_CONFIG`` — change there, nowhere else.
* Retries ONLY on 429 (ResourceExhausted) and 5xx (ServerError).
* Bounded exponential backoff with jitter: 1 s, 2 s, 4 s ± random.
* Logs model id, wall-clock latency, and reported token usage on every call.
* No SageMaker endpoint, no manual console step beyond creating the
  secret value once in Secrets Manager.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Optional

import boto3
from google import genai
from google.genai import types as genai_types

from shared.logging import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration — change model IDs here, nowhere else
# ─────────────────────────────────────────────────────────────

MODEL_CONFIG = {
    "generation_model": "gemini-2.5-flash",
    "embedding_model": "gemini-embedding-001",
    "embedding_dim": 3072,
    "request_timeout": 30,       # seconds
    "max_retries": 3,
    "backoff_base": 1.0,         # seconds
    "backoff_max": 8.0,          # seconds
    "jitter_range": 0.5,         # ± seconds
}


# ─────────────────────────────────────────────────────────────
# Module-scope caches (cold-start once per Lambda container)
# ─────────────────────────────────────────────────────────────

_api_key: Optional[str] = None
_client: Optional[genai.Client] = None


def _get_api_key() -> str:
    """Fetch the Gemini API key from Secrets Manager, caching the result."""
    global _api_key
    if _api_key is not None:
        return _api_key

    secret_name = os.environ.get("GEMINI_SECRET_NAME", "")
    if not secret_name:
        raise RuntimeError(
            "GEMINI_SECRET_NAME env var is not set — "
            "cannot retrieve the Gemini API key from Secrets Manager"
        )

    region = os.environ.get("AWS_REGION", "ap-south-1")
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_name)

    # The secret may be a plain string or a JSON object with a "key" field.
    raw = resp["SecretString"]
    try:
        parsed = json.loads(raw)
        _api_key = parsed.get("key", parsed.get("api_key", raw))
    except (json.JSONDecodeError, TypeError):
        _api_key = raw

    log.info(
        "gemini.api_key.loaded",
        secretName=secret_name,
        region=region,
    )
    return _api_key


def _get_client() -> genai.Client:
    """Return a cached google-genai Client."""
    global _client
    if _client is not None:
        return _client
    _client = genai.Client(api_key=_get_api_key())
    return _client


# ─────────────────────────────────────────────────────────────
# Retry helper — only 429 and 5xx
# ─────────────────────────────────────────────────────────────

def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is a retryable 429 or 5xx error."""
    # google-genai raises google.api_core.exceptions for HTTP errors
    # Check for status code attribute (covers ResourceExhausted, ServerError, etc.)
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status_code is not None:
        code = int(status_code)
        return code == 429 or 500 <= code < 600

    # Some google-genai versions raise errors with grpc_status_code
    grpc_code = getattr(exc, "grpc_status_code", None)
    if grpc_code is not None:
        # RESOURCE_EXHAUSTED = 8, INTERNAL = 13, UNAVAILABLE = 14
        return grpc_code in (8, 13, 14)

    # Check the exception class name as fallback
    name = type(exc).__name__
    if name in ("ResourceExhausted", "TooManyRequests"):
        return True
    if name in ("InternalServerError", "ServiceUnavailable", "ServerError"):
        return True

    return False


def _retry(fn, *args, **kwargs):
    """
    Call *fn* with bounded exponential backoff + jitter.

    Retries only on 429 / 5xx. All other errors propagate immediately.
    """
    max_retries = MODEL_CONFIG["max_retries"]
    base = MODEL_CONFIG["backoff_base"]
    cap = MODEL_CONFIG["backoff_max"]
    jitter = MODEL_CONFIG["jitter_range"]

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_retryable(exc) or attempt == max_retries:
                raise
            last_exc = exc
            delay = min(base * (2 ** attempt), cap) + random.uniform(-jitter, jitter)
            delay = max(0.1, delay)
            log.warning(
                "gemini.retry",
                attempt=attempt + 1,
                maxRetries=max_retries,
                delaySec=round(delay, 2),
                error=str(exc),
                errorType=type(exc).__name__,
            )
            time.sleep(delay)

    # Should not reach here, but safety net
    if last_exc:
        raise last_exc


# ─────────────────────────────────────────────────────────────
# Public API — embed()
# ─────────────────────────────────────────────────────────────

def embed(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using the configured embedding model.

    Parameters
    ----------
    texts:
        One or more text strings to embed. The google-genai SDK handles
        batching internally.

    Returns
    -------
    list[list[float]]
        One embedding vector per input text.
    """
    if not texts:
        return []

    model = MODEL_CONFIG["embedding_model"]
    client = _get_client()

    t0 = time.monotonic()

    def _call():
        return client.models.embed_content(
            model=model,
            contents=texts,
        )

    response = _retry(_call)
    elapsed = time.monotonic() - t0

    vectors = [emb.values for emb in response.embeddings]

    # Log per-call metrics
    log.info(
        "gemini.embed.completed",
        model=model,
        inputCount=len(texts),
        outputDim=len(vectors[0]) if vectors else 0,
        latencySec=round(elapsed, 3),
    )

    return vectors


# ─────────────────────────────────────────────────────────────
# Public API — generate()
# ─────────────────────────────────────────────────────────────

def generate(
    prompt: str,
    *,
    system_instruction: Optional[str] = None,
    response_schema: Optional[dict] = None,
) -> str:
    """
    Generate text using the configured generation model.

    Parameters
    ----------
    prompt:
        The user prompt.
    system_instruction:
        Optional system instruction prepended to the conversation.
    response_schema:
        Optional JSON schema for structured output.

    Returns
    -------
    str
        The model's text response.
    """
    model = MODEL_CONFIG["generation_model"]
    client = _get_client()

    config_kwargs: dict[str, Any] = {}
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema

    config = genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

    t0 = time.monotonic()

    def _call():
        kwargs: dict[str, Any] = {
            "model": model,
            "contents": prompt,
        }
        if system_instruction:
            kwargs["config"] = genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                **(config_kwargs if config_kwargs else {}),
            )
        elif config:
            kwargs["config"] = config
        return client.models.generate_content(**kwargs)

    response = _retry(_call)
    elapsed = time.monotonic() - t0

    text = response.text or ""

    # Extract token usage if available
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    candidates_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    total_tokens = getattr(usage, "total_token_count", 0) if usage else 0

    log.info(
        "gemini.generate.completed",
        model=model,
        latencySec=round(elapsed, 3),
        promptTokens=prompt_tokens,
        candidatesTokens=candidates_tokens,
        totalTokens=total_tokens,
    )

    return text
