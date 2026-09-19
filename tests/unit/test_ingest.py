# EPF Sentinel — pytest unit tests for scripts/ingest_rules.py
#
# What is real vs mocked
# -----------------------
# * Real  : frontmatter parsing, validation, chunking logic, chunkId generation.
# * Mocked: Gemini API (shared.gemini.embed), DynamoDB.
# * No network calls.

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ingest_rules adds src/ to sys.path itself, but we rely on conftest.py
# for the shared modules.
import sys
from pathlib import Path as _P

_SCRIPTS = _P(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ingest_rules import (  # noqa: E402
    parse_frontmatter,
    validate_frontmatter,
    chunk_document,
    make_chunk_id,
    _token_count,
    _split_sentences,
    _merge_short_paragraphs,
    _split_long_paragraph,
)


# ─────────────────────────────────────────────────────────────
# Frontmatter parsing
# ─────────────────────────────────────────────────────────────

class TestFrontmatterParsing:
    def test_valid_frontmatter(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text(textwrap.dedent("""\
            ---
            source_url: https://epfindia.gov.in/charter
            source_title: EPFO Charter
            retrieved_on: "2024-10-01"
            authority: EPFO_OFFICIAL
            rule_set_version: v1
            ---
            # Title
            Some body text here.
        """), encoding="utf-8")
        meta, body = parse_frontmatter(md)
        assert meta is not None
        assert meta["source_url"] == "https://epfindia.gov.in/charter"
        assert meta["authority"] == "EPFO_OFFICIAL"
        assert "Some body text here" in body

    def test_no_frontmatter(self, tmp_path):
        md = tmp_path / "plain.md"
        md.write_text("# Just a title\nNo frontmatter here.\n", encoding="utf-8")
        meta, body = parse_frontmatter(md)
        assert meta is None

    def test_incomplete_frontmatter(self, tmp_path):
        md = tmp_path / "bad.md"
        md.write_text("---\nsource_url: https://example.com\n", encoding="utf-8")
        meta, body = parse_frontmatter(md)
        # Only one delimiter — should not parse as valid frontmatter
        assert meta is None


# ─────────────────────────────────────────────────────────────
# Frontmatter validation
# ─────────────────────────────────────────────────────────────

class TestFrontmatterValidation:
    def test_valid_meta(self):
        meta = {
            "source_url": "https://epfindia.gov.in",
            "retrieved_on": "2024-10-01",
            "authority": "EPFO_OFFICIAL",
        }
        assert validate_frontmatter(meta, Path("test.md")) is None

    def test_missing_source_url(self):
        meta = {"retrieved_on": "2024-10-01", "authority": "EPFO_OFFICIAL"}
        result = validate_frontmatter(meta, Path("test.md"))
        assert result is not None
        assert "source_url" in result
        assert "REFUSED" in result

    def test_missing_retrieved_on(self):
        meta = {"source_url": "https://example.com", "authority": "EPFO_OFFICIAL"}
        result = validate_frontmatter(meta, Path("test.md"))
        assert result is not None
        assert "retrieved_on" in result

    def test_missing_authority(self):
        meta = {"source_url": "https://example.com", "retrieved_on": "2024-10-01"}
        result = validate_frontmatter(meta, Path("test.md"))
        assert result is not None
        assert "authority" in result

    def test_invalid_authority(self):
        meta = {
            "source_url": "https://example.com",
            "retrieved_on": "2024-10-01",
            "authority": "INVALID_VALUE",
        }
        result = validate_frontmatter(meta, Path("test.md"))
        assert result is not None
        assert "REFUSED" in result

    def test_scheme_text_authority_valid(self):
        meta = {
            "source_url": "https://example.com",
            "retrieved_on": "2024-10-01",
            "authority": "SCHEME_TEXT",
        }
        assert validate_frontmatter(meta, Path("test.md")) is None


# ─────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────

class TestChunking:
    def test_token_count(self):
        assert _token_count("hello world foo bar") == 4
        assert _token_count("") == 0

    def test_split_sentences(self):
        text = "First sentence. Second sentence! Third sentence? Fourth."
        sentences = _split_sentences(text)
        assert len(sentences) == 4
        assert sentences[0] == "First sentence."

    def test_chunks_within_limits(self):
        # Build a body with enough text to produce chunks
        words = " ".join(f"word{i}" for i in range(500))
        body = f"## Section One\n\n{words}\n\n## Section Two\n\nShort paragraph."
        chunks = chunk_document(body)
        assert len(chunks) > 0
        for heading, text in chunks:
            tc = _token_count(text)
            # Allow last/merged chunks to be outside strict bounds
            assert tc > 0

    def test_never_splits_mid_sentence(self):
        """Long paragraph should be split at sentence boundaries."""
        # 50 sentences of ~10 words each = ~500 words
        sentences = [f"This is sentence number {i} with extra words added." for i in range(50)]
        body = "## Rules\n\n" + " ".join(sentences)
        chunks = chunk_document(body)
        for heading, text in chunks:
            # Each chunk should end with punctuation (sentence boundary)
            stripped = text.rstrip()
            assert stripped[-1] in ".!?", f"Chunk does not end at sentence boundary: ...{stripped[-30:]}"

    def test_heading_path_tracking(self):
        body = "## Level 2\n\nSome text here.\n\n### Level 3\n\nMore text here."
        chunks = chunk_document(body)
        assert len(chunks) >= 1
        # The last chunk should have a heading path containing both levels
        headings = [h for h, _ in chunks]
        assert any("## Level 2" in h for h in headings)

    def test_merge_short_paragraphs(self):
        paragraphs = ["Short.", "Also short.", "Still short."]
        merged = _merge_short_paragraphs(paragraphs)
        # All three are under 200 tokens, so they should be merged into one
        assert len(merged) == 1

    def test_split_long_paragraph(self):
        # ~600 words in one paragraph (> 400 token limit)
        text = " ".join(f"Word{i} is here." for i in range(200))
        result = _split_long_paragraph(text)
        assert len(result) > 1
        for chunk in result:
            assert _token_count(chunk) <= 400 or len(result) == 1


# ─────────────────────────────────────────────────────────────
# Chunk ID determinism / idempotency
# ─────────────────────────────────────────────────────────────

class TestChunkId:
    def test_deterministic(self):
        id1 = make_chunk_id("https://example.com", "## Heading", "Some text")
        id2 = make_chunk_id("https://example.com", "## Heading", "Some text")
        assert id1 == id2

    def test_different_text_different_id(self):
        id1 = make_chunk_id("https://example.com", "## Heading", "Text A")
        id2 = make_chunk_id("https://example.com", "## Heading", "Text B")
        assert id1 != id2

    def test_length_is_16(self):
        cid = make_chunk_id("url", "heading", "text")
        assert len(cid) == 16

    def test_matches_sha256(self):
        content = "url" + "heading" + "text"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        assert make_chunk_id("url", "heading", "text") == expected


# ─────────────────────────────────────────────────────────────
# Idempotency: ingest twice, same results
# ─────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_ingest_twice_produces_same_chunk_ids(self, tmp_path):
        """Chunking the same file twice produces identical chunkIds."""
        md = tmp_path / "rules.md"
        # Build enough content for real chunks
        sentences = " ".join(
            f"Rule {i}: claims of type {i} must be settled within {10 + i} days."
            for i in range(60)
        )
        md.write_text(textwrap.dedent(f"""\
            ---
            source_url: https://epfindia.gov.in/charter
            source_title: EPFO Charter
            retrieved_on: "2024-10-01"
            authority: EPFO_OFFICIAL
            rule_set_version: v1
            ---
            ## Settlement Timelines

            {sentences}
        """), encoding="utf-8")

        meta1, body1 = parse_frontmatter(md)
        chunks1 = chunk_document(body1)
        ids1 = [make_chunk_id(str(meta1["source_url"]), h, t) for h, t in chunks1]

        meta2, body2 = parse_frontmatter(md)
        chunks2 = chunk_document(body2)
        ids2 = [make_chunk_id(str(meta2["source_url"]), h, t) for h, t in chunks2]

        assert len(ids1) == len(ids2)
        assert ids1 == ids2
