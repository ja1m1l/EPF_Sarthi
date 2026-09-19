"""
Retrieval module for EPF Sentinel.

Provides ``embed_query()`` and ``search()`` for brute-force cosine-similarity
retrieval over RuleChunk vectors stored in DynamoDB.

Chunks are cached in Lambda module scope keyed by ``rule_set_version`` so
warm invocations do not re-scan DynamoDB.

Usage inside a Lambda handler:
    from shared.retrieval import embed_query, search

    qvec = embed_query("how long does a final settlement claim take")
    results = search(qvec, rule_set_version="2024-10-01", k=5)
    for chunk, score in results:
        print(f"{score:.4f}  {chunk.text[:80]}")
"""

from __future__ import annotations

import math
import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key

from shared.gemini import embed
from shared.logging import get_logger
from shared.models import RuleChunk

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Module-scope chunk cache
# ─────────────────────────────────────────────────────────────

_chunk_cache: dict[str, list[RuleChunk]] = {}


def _get_table_name() -> str:
    name = os.environ.get("RULECHUNKS_TABLE_NAME", "")
    if not name:
        raise RuntimeError(
            "RULECHUNKS_TABLE_NAME env var is not set"
        )
    return name


def _load_chunks(rule_set_version: str) -> list[RuleChunk]:
    """Load all chunks for a version from DynamoDB, or return from cache."""
    if rule_set_version in _chunk_cache:
        log.info(
            "retrieval.cache.hit",
            ruleSetVersion=rule_set_version,
            chunkCount=len(_chunk_cache[rule_set_version]),
        )
        return _chunk_cache[rule_set_version]

    table_name = _get_table_name()
    region = os.environ.get("AWS_REGION", "ap-south-1")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    log.info(
        "retrieval.scan.start",
        ruleSetVersion=rule_set_version,
        tableName=table_name,
    )

    chunks: list[RuleChunk] = []
    last_key = None
    while True:
        query_kwargs = {
            "KeyConditionExpression": Key("ruleSetVersion").eq(rule_set_version),
        }
        if last_key:
            query_kwargs["ExclusiveStartKey"] = last_key

        resp = table.query(**query_kwargs)
        for item in resp.get("Items", []):
            chunks.append(RuleChunk.from_dict(item))

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    _chunk_cache[rule_set_version] = chunks

    log.info(
        "retrieval.scan.completed",
        ruleSetVersion=rule_set_version,
        chunkCount=len(chunks),
    )

    return chunks


def invalidate_cache(rule_set_version: Optional[str] = None) -> None:
    """
    Clear the chunk cache.

    If *rule_set_version* is given, only that version is evicted.
    Otherwise the entire cache is cleared.
    """
    if rule_set_version:
        _chunk_cache.pop(rule_set_version, None)
    else:
        _chunk_cache.clear()


# ─────────────────────────────────────────────────────────────
# Cosine similarity
# ─────────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Compute cosine similarity between two vectors of equal length.

    Returns a value in [-1, 1].  Returns 0.0 if either vector has
    zero magnitude.
    """
    if len(a) != len(b):
        raise ValueError(
            f"Vector length mismatch: {len(a)} vs {len(b)}"
        )

    dot = 0.0
    mag_a = 0.0
    mag_b = 0.0
    for ai, bi in zip(a, b):
        dot += ai * bi
        mag_a += ai * ai
        mag_b += bi * bi

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    return dot / (math.sqrt(mag_a) * math.sqrt(mag_b))


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def embed_query(text: str) -> list[float]:
    """
    Embed a single query text using the Gemini embedding model.

    Returns the embedding vector as a list of floats.
    """
    vectors = embed([text])
    return vectors[0]


def search(
    query_vector: list[float],
    rule_set_version: str,
    k: int = 5,
) -> list[tuple[RuleChunk, float]]:
    """
    Brute-force cosine similarity search over all chunks for the given
    version.

    Parameters
    ----------
    query_vector:
        The embedding vector for the query.
    rule_set_version:
        Which rule set version to search in.
    k:
        Number of top results to return.

    Returns
    -------
    list[tuple[RuleChunk, float]]
        ``(chunk, cosine_score)`` sorted descending by score.
    """
    chunks = _load_chunks(rule_set_version)

    if not chunks:
        log.warning(
            "retrieval.search.empty",
            ruleSetVersion=rule_set_version,
        )
        return []

    scored: list[tuple[RuleChunk, float]] = []
    for chunk in chunks:
        score = cosine_similarity(query_vector, chunk.embedding)
        scored.append((chunk, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    results = scored[:k]

    log.info(
        "retrieval.search.completed",
        ruleSetVersion=rule_set_version,
        totalChunks=len(chunks),
        k=k,
        topScore=round(results[0][1], 4) if results else 0.0,
    )

    return results
