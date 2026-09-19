# EPF Sentinel — pytest unit tests for shared/retrieval.py
#
# What is real vs mocked
# -----------------------
# * Real  : cosine_similarity math, search sorting, cache logic.
# * Mocked: DynamoDB (boto3), Gemini embed (shared.gemini.embed).
# * No network calls.

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

from shared.retrieval import cosine_similarity, search, embed_query, _chunk_cache, invalidate_cache
from shared.models import RuleChunk


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the chunk cache before each test."""
    _chunk_cache.clear()
    yield
    _chunk_cache.clear()


def _make_chunk(chunk_id: str, text: str, embedding: list[float], **kw) -> RuleChunk:
    defaults = dict(
        ruleSetVersion="v1",
        chunkId=chunk_id,
        text=text,
        embedding=embedding,
        embeddingModel="gemini-embedding-001",
        embeddingDim=len(embedding),
        sourceUrl="https://epfindia.gov.in/charter",
        sourceTitle="EPFO Charter",
        retrievedOn="2024-10-01",
        authority="EPFO_OFFICIAL",
        headingPath="## Rules",
        tokenCount=len(text.split()),
        createdAt="2024-10-01T00:00:00+00:00",
    )
    defaults.update(kw)
    return RuleChunk(**defaults)


# ─────────────────────────────────────────────────────────────
# Cosine similarity
# ─────────────────────────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_known_value(self):
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        # dot = 4+10+18 = 32
        # |a| = sqrt(14), |b| = sqrt(77)
        expected = 32 / (math.sqrt(14) * math.sqrt(77))
        assert cosine_similarity(a, b) == pytest.approx(expected)

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_hand_check_arithmetic(self):
        """Hand-verifiable cosine similarity for a few dimensions."""
        a = [0.5, 0.5]
        b = [1.0, 0.0]
        # dot = 0.5, |a| = sqrt(0.5), |b| = 1.0
        expected = 0.5 / math.sqrt(0.5)
        assert cosine_similarity(a, b) == pytest.approx(expected)


# ─────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────

class TestSearch:
    def test_returns_sorted_descending(self, monkeypatch):
        """Top result should be the most similar chunk."""
        monkeypatch.setenv("RULECHUNKS_TABLE_NAME", "test-table")

        # Pre-populate cache to avoid DynamoDB call
        chunks = [
            _make_chunk("c1", "EPFO final settlement claims 20 days", [1.0, 0.0, 0.0]),
            _make_chunk("c2", "Unrelated text about weather", [0.0, 1.0, 0.0]),
            _make_chunk("c3", "Timeline for final settlement is 20 days", [0.9, 0.1, 0.0]),
        ]
        _chunk_cache["v1"] = chunks

        query_vec = [1.0, 0.0, 0.0]  # Most similar to c1
        results = search(query_vec, "v1", k=3)

        assert len(results) == 3
        # c1 should be first (exact match)
        assert results[0][0].chunkId == "c1"
        assert results[0][1] == pytest.approx(1.0)
        # c3 should be second
        assert results[1][0].chunkId == "c3"
        # c2 should be last (orthogonal)
        assert results[2][0].chunkId == "c2"
        assert results[2][1] == pytest.approx(0.0)

    def test_k_limits_results(self, monkeypatch):
        monkeypatch.setenv("RULECHUNKS_TABLE_NAME", "test-table")

        chunks = [
            _make_chunk(f"c{i}", f"text {i}", [float(i), 0.0, 0.0])
            for i in range(10)
        ]
        _chunk_cache["v1"] = chunks

        results = search([1.0, 0.0, 0.0], "v1", k=3)
        assert len(results) == 3

    def test_empty_version_returns_empty(self, monkeypatch):
        monkeypatch.setenv("RULECHUNKS_TABLE_NAME", "test-table")
        _chunk_cache["v1"] = []
        results = search([1.0, 0.0, 0.0], "v1", k=5)
        assert results == []


# ─────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────

class TestCache:
    def test_cache_prevents_rescan(self, monkeypatch):
        """Second call with same version should not hit DynamoDB."""
        monkeypatch.setenv("RULECHUNKS_TABLE_NAME", "test-table")

        chunk = _make_chunk("c1", "test text", [1.0, 0.0])
        _chunk_cache["v1"] = [chunk]

        # First search — from cache
        results1 = search([1.0, 0.0], "v1", k=1)
        # Manually verify cache is still there
        assert "v1" in _chunk_cache
        # Second search — should be identical (same cache)
        results2 = search([1.0, 0.0], "v1", k=1)
        assert results1[0][0].chunkId == results2[0][0].chunkId

    def test_invalidate_cache_specific_version(self):
        _chunk_cache["v1"] = [_make_chunk("c1", "text", [1.0])]
        _chunk_cache["v2"] = [_make_chunk("c2", "text", [1.0])]
        invalidate_cache("v1")
        assert "v1" not in _chunk_cache
        assert "v2" in _chunk_cache

    def test_invalidate_cache_all(self):
        _chunk_cache["v1"] = [_make_chunk("c1", "text", [1.0])]
        _chunk_cache["v2"] = [_make_chunk("c2", "text", [1.0])]
        invalidate_cache()
        assert len(_chunk_cache) == 0


# ─────────────────────────────────────────────────────────────
# embed_query
# ─────────────────────────────────────────────────────────────

class TestEmbedQuery:
    def test_embed_query_calls_gemini(self, monkeypatch):
        """embed_query should call gemini.embed with a single-element list."""
        mock_embed = MagicMock(return_value=[[0.1, 0.2, 0.3]])
        monkeypatch.setattr("shared.retrieval.embed", mock_embed)

        result = embed_query("test query")
        assert result == [0.1, 0.2, 0.3]
        mock_embed.assert_called_once_with(["test query"])


# ─────────────────────────────────────────────────────────────
# Retrieval smoke test
# ─────────────────────────────────────────────────────────────

class TestRetrievalSmokeTest:
    def test_settlement_timeline_query(self, monkeypatch):
        """
        A query about final settlement timelines should return a chunk
        whose text actually contains a timeline statement.
        """
        monkeypatch.setenv("RULECHUNKS_TABLE_NAME", "test-table")

        # Create chunks with known content
        timeline_chunk = _make_chunk(
            "timeline-1",
            "As per the EPFO Citizen's Charter, final settlement claims "
            "shall be settled within 20 days from the date of receipt of "
            "the claim in the concerned EPFO office.",
            [0.9, 0.8, 0.1],  # High similarity to query
            headingPath="## Settlement Timelines",
        )
        unrelated_chunk = _make_chunk(
            "misc-1",
            "Members can check their EPF balance through the UMANG app "
            "or by sending an SMS to 7738299899.",
            [0.1, 0.1, 0.9],  # Low similarity to query
            headingPath="## Balance Check",
        )
        _chunk_cache["v1"] = [timeline_chunk, unrelated_chunk]

        query_vec = [0.9, 0.8, 0.1]  # Similar to the timeline chunk
        results = search(query_vec, "v1", k=5)

        # Top result should be the timeline chunk
        top_chunk, top_score = results[0]
        assert "20 days" in top_chunk.text or "settlement" in top_chunk.text.lower()
        assert top_score > 0.5  # Should have high similarity
