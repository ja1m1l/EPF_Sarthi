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
from shared.metrics import gemini_call

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration — change model IDs here, nowhere else
# ─────────────────────────────────────────────────────────────

MODEL_CONFIG = {
    # gemini-2.5-flash was retired: generateContent returns 404
    # "no longer available to new users", and Google's own error prescribes
    # gemini-3.6-flash. Re-checked against the live model list on 2026-09-19.
    # The embedding model is deliberately unchanged — repinning it would
    # invalidate every stored vector for the current ruleSetVersion.
    "generation_model": "gemini-3.6-flash",
    "embedding_model": "gemini-embedding-001",
    "embedding_dim": 3072,
    "temperature": 0.0,          # Pinned deterministic sampling
    "request_timeout": 30,       # seconds
    "max_retries": 4,
    "backoff_base": 1.0,         # seconds
    "backoff_max": 20.0,         # seconds
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


def _is_daily_quota(exc: Exception) -> bool:
    """
    Free-tier *per-day* 429s do not recover inside a request.

    Retrying them burns remaining quota and still fails. Transient
    per-minute 429s remain retryable.
    """
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("per day", "perday", "daily", "free_tier", "free tier", "limit: 0")
    )


def _status_code(exc: Exception) -> Optional[int]:
    raw = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if raw is None:
        if "429" in str(exc) or type(exc).__name__ in ("ResourceExhausted", "TooManyRequests"):
            return 429
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
            if _is_daily_quota(exc) or not _is_retryable(exc) or attempt == max_retries:
                raise
            last_exc = exc
            delay = min(base * (2 ** attempt), cap) + random.uniform(-jitter, jitter)
            # Transient per-minute 429s: jittered backoff, still bounded.
            if _status_code(exc) == 429 or "RESOURCE_EXHAUSTED" in str(exc):
                delay = max(delay, 2.0 + random.uniform(0.1, 1.0))
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

    try:
        response = _retry(_call)
    except Exception as exc:
        gemini_call(latency_ms=(time.monotonic() - t0) * 1000, error=True, status_code=_status_code(exc))
        raise
    elapsed = time.monotonic() - t0

    vectors = [emb.values for emb in response.embeddings]
    gemini_call(latency_ms=elapsed * 1000)

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
    response_schema: Optional[dict[str, Any]] = None,
    temperature: Optional[float] = None,
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
    temperature:
        Optional sampling temperature. Defaults to MODEL_CONFIG['temperature'] (0.0).

    Returns
    -------
    str
        The model's text response.
    """
    model = MODEL_CONFIG["generation_model"]
    client = _get_client()

    effective_temp = temperature if temperature is not None else MODEL_CONFIG.get("temperature", 0.0)

    config_kwargs: dict[str, Any] = {
        "temperature": effective_temp,
    }
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema

    config = genai_types.GenerateContentConfig(**config_kwargs)

    t0 = time.monotonic()

    # The model that actually answered. It diverges from the pinned id when the
    # fallback fires, and the logged provenance must report what really ran —
    # a log that always echoes the pinned id is worse than none, because it
    # reads as confirmation that the pin held.
    served_model = model

    def _call(target_model: str):
        kwargs: dict[str, Any] = {
            "model": target_model,
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

    try:
        try:
            response = _retry(lambda: _call(model))
        except Exception as exc:
            # Per-day quota and 5xx outages stay infrastructure failures.
            # Only a retired-model 404 may fall back to another generation id.
            if _is_daily_quota(exc) or _status_code(exc) == 429:
                raise
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                fallback_model = "gemini-3.5-flash-lite" if model != "gemini-3.5-flash-lite" else "gemini-3.6-flash"
                log.warning(
                    "gemini.generate.fallback",
                    pinnedModel=model,
                    toModel=fallback_model,
                    reason=str(exc),
                )
                response = _retry(lambda: _call(fallback_model))
                served_model = fallback_model
            else:
                raise
    except Exception as exc:
        gemini_call(latency_ms=(time.monotonic() - t0) * 1000, error=True, status_code=_status_code(exc))
        raise
    elapsed = time.monotonic() - t0

    text = response.text or ""

    # Extract token usage if available
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    candidates_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    total_tokens = getattr(usage, "total_token_count", 0) if usage else 0

    gemini_call(latency_ms=elapsed * 1000, tokens=int(total_tokens or 0))

    log.info(
        "gemini.generate.completed",
        model=served_model,
        pinnedModel=model,
        latencySec=round(elapsed, 3),
        promptTokens=prompt_tokens,
        candidatesTokens=candidates_tokens,
        totalTokens=total_tokens,
    )

    return text


# ─────────────────────────────────────────────────────────────
# Public API — generate_multimodal()
# ─────────────────────────────────────────────────────────────

def generate_multimodal(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    *,
    system_instruction: Optional[str] = None,
) -> str:
    """
    Transcribe or analyse raw file bytes using the vision-capable generation model.

    Model used
    ----------
    ``MODEL_CONFIG["generation_model"]`` — currently ``gemini-3.6-flash``,
    which is multimodal and vision-capable. The model id is pinned in
    MODEL_CONFIG; change it there, nowhere else.

    Unlike :func:`generate`, this path has no fallback model: a transcription
    that cannot run must surface as a failure so the document is marked
    NEEDS_MANUAL_ENTRY, never as an empty but apparently successful read.

    Parameters
    ----------
    image_bytes:
        Raw bytes of the file (image or PDF).  Passed as an ``inline_data``
        Part so no intermediate upload step is required.
    mime_type:
        MIME type string, e.g. ``"image/jpeg"``, ``"image/png"``,
        ``"application/pdf"``.
    prompt:
        Text instruction accompanying the file, e.g.
        ``"Transcribe all visible text verbatim."``.
    system_instruction:
        Optional system instruction prepended to the conversation.

    Returns
    -------
    str
        The model's text response (transcribed / analysed text).

    Raises
    ------
    Any exception from the Gemini API that is not retryable (non-429, non-5xx)
    propagates immediately.  Retryable errors are retried with bounded
    exponential backoff per MODEL_CONFIG.
    """
    model = MODEL_CONFIG["generation_model"]
    client = _get_client()

    # Build the multimodal content: [inline_data_part, text_prompt_part]
    inline_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    config_kwargs: dict[str, Any] = {
        "temperature": MODEL_CONFIG.get("temperature", 0.0),
    }

    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction

    config = genai_types.GenerateContentConfig(**config_kwargs)

    t0 = time.monotonic()

    def _call() -> Any:
        return client.models.generate_content(
            model=model,
            contents=[inline_part, prompt],
            config=config,
        )

    try:
        response = _retry(_call)
    except Exception as exc:
        gemini_call(latency_ms=(time.monotonic() - t0) * 1000, error=True, status_code=_status_code(exc))
        raise
    elapsed = time.monotonic() - t0

    text = response.text or ""

    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    candidates_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    total_tokens = getattr(usage, "total_token_count", 0) if usage else 0

    gemini_call(latency_ms=elapsed * 1000, tokens=int(total_tokens or 0))

    log.info(
        "gemini.generate_multimodal.completed",
        model=model,
        mimeType=mime_type,
        inputBytes=len(image_bytes),
        latencySec=round(elapsed, 3),
        promptTokens=prompt_tokens,
        candidatesTokens=candidates_tokens,
        totalTokens=total_tokens,
    )

    return text
