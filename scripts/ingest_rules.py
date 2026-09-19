#!/usr/bin/env python3
"""
Rules ingestion pipeline for EPF Sentinel.

Parses YAML-frontmatter Markdown files from a rules directory, chunks
them by heading/paragraph into 200-400 token segments (never mid-sentence),
embeds each chunk via the Gemini API, and writes them to the DynamoDB
RuleChunks table.

Idempotent: re-running on the same file with the same version produces
identical chunkIds (sha256 of sourceUrl + headingPath + text) and no
duplicates.

Usage:
    python scripts/ingest_rules.py
    python scripts/ingest_rules.py --rules-dir rules_source --table-name epf-sentinel-RuleChunks-dev
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

# Add src/ to path so we can import shared modules
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# Default environment variables for CLI execution
os.environ.setdefault("GEMINI_SECRET_NAME", "epf-sentinel/gemini-api-key")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import boto3  # noqa: E402
import yaml   # noqa: E402

from shared.gemini import MODEL_CONFIG  # noqa: E402
from shared import gemini                # noqa: E402
from shared.models import RuleChunk, utc_now_iso  # noqa: E402
from shared.logging import get_logger    # noqa: E402

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

REQUIRED_FRONTMATTER = ("source_url", "retrieved_on", "authority")
VALID_AUTHORITIES = ("EPFO_OFFICIAL", "SCHEME_TEXT")
MIN_CHUNK_TOKENS = 200
MAX_CHUNK_TOKENS = 400
EMBED_BATCH_SIZE = 100  # Gemini API batch limit


# ─────────────────────────────────────────────────────────────
# Frontmatter parsing
# ─────────────────────────────────────────────────────────────

def parse_frontmatter(file_path: Path) -> tuple[Optional[dict], str]:
    """
    Parse YAML frontmatter from a Markdown file.

    Returns (metadata_dict, body_text) or (None, "") if parsing fails.
    """
    content = file_path.read_text(encoding="utf-8")

    if not content.startswith("---"):
        return None, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, content

    try:
        meta = yaml.safe_load(parts[1])
        if not isinstance(meta, dict):
            return None, content
    except yaml.YAMLError:
        return None, content

    body = parts[2].strip()
    return meta, body


def validate_frontmatter(meta: dict, file_path: Path) -> Optional[str]:
    """
    Validate required frontmatter fields. Returns an error message string
    if invalid, or None if valid.
    """
    for field in REQUIRED_FRONTMATTER:
        if field not in meta or not meta[field]:
            return (
                f"REFUSED: {file_path.name} — missing required frontmatter "
                f"field '{field}'. File will NOT be ingested."
            )

    authority = meta.get("authority", "")
    if authority not in VALID_AUTHORITIES:
        return (
            f"REFUSED: {file_path.name} — authority must be one of "
            f"{VALID_AUTHORITIES}, got '{authority}'. File will NOT be ingested."
        )

    return None


# ─────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────

def _token_count(text: str) -> int:
    """Approximate token count using whitespace splitting."""
    return len(text.split())


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences at '. ', '? ', '! ' boundaries."""
    # Split on sentence-ending punctuation followed by a space or end-of-string
    parts = re.split(r'(?<=[.?!])\s+', text.strip())
    return [p for p in parts if p.strip()]


def _merge_short_paragraphs(paragraphs: list[str]) -> list[str]:
    """
    Merge consecutive paragraphs that are under MIN_CHUNK_TOKENS until
    each reaches the minimum.
    """
    if not paragraphs:
        return []

    merged: list[str] = []
    current = paragraphs[0]

    for para in paragraphs[1:]:
        if _token_count(current) < MIN_CHUNK_TOKENS:
            current = current + "\n\n" + para
        else:
            merged.append(current)
            current = para

    merged.append(current)
    return merged


def _split_long_paragraph(text: str) -> list[str]:
    """
    Split a paragraph that exceeds MAX_CHUNK_TOKENS at sentence
    boundaries. Never splits mid-sentence.
    """
    if _token_count(text) <= MAX_CHUNK_TOKENS:
        return [text]

    sentences = _split_sentences(text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        stokens = _token_count(sentence)

        if current_tokens + stokens > MAX_CHUNK_TOKENS and current:
            chunks.append(" ".join(current))
            current = [sentence]
            current_tokens = stokens
        else:
            current.append(sentence)
            current_tokens += stokens

    if current:
        chunks.append(" ".join(current))

    return chunks


def chunk_document(body: str) -> list[tuple[str, str]]:
    """
    Chunk a Markdown document body by heading/paragraph into
    200-400 token segments.

    Returns a list of (heading_path, chunk_text) tuples.
    """
    # Split by heading lines (##, ###, etc.)
    heading_pattern = re.compile(r'^(#{1,6}\s+.+)$', re.MULTILINE)

    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body_parts: list[str] = []

    for line in body.split("\n"):
        if heading_pattern.match(line.strip()):
            # Save previous section
            if current_body_parts:
                section_text = "\n".join(current_body_parts).strip()
                if section_text:
                    sections.append((current_heading, section_text))
                current_body_parts = []

            # Build heading path
            if current_heading:
                current_heading = current_heading + " > " + line.strip()
            else:
                current_heading = line.strip()
        else:
            current_body_parts.append(line)

    # Don't forget the last section
    if current_body_parts:
        section_text = "\n".join(current_body_parts).strip()
        if section_text:
            sections.append((current_heading, section_text))

    # Now chunk each section
    all_chunks: list[tuple[str, str]] = []
    for heading_path, section_text in sections:
        # Split into paragraphs (double newline)
        paragraphs = [p.strip() for p in re.split(r'\n\n+', section_text) if p.strip()]

        # Merge short paragraphs
        paragraphs = _merge_short_paragraphs(paragraphs)

        # Split long paragraphs at sentence boundaries
        final_chunks: list[str] = []
        for para in paragraphs:
            final_chunks.extend(_split_long_paragraph(para))

        # Final merge pass: if last chunk is very short, merge with previous
        if len(final_chunks) > 1 and _token_count(final_chunks[-1]) < MIN_CHUNK_TOKENS:
            last = final_chunks.pop()
            final_chunks[-1] = final_chunks[-1] + "\n\n" + last

        for chunk_text in final_chunks:
            if chunk_text.strip():
                all_chunks.append((heading_path, chunk_text.strip()))

    return all_chunks


# ─────────────────────────────────────────────────────────────
# Chunk ID generation
# ─────────────────────────────────────────────────────────────

def make_chunk_id(source_url: str, heading_path: str, text: str) -> str:
    """
    Deterministic chunkId = sha256(sourceUrl + headingPath + text)[:16].
    """
    content = source_url + heading_path + text
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────
# DynamoDB operations
# ─────────────────────────────────────────────────────────────

def _item_exists(table, rule_set_version: str, chunk_id: str) -> bool:
    """Check if a chunk already exists in DynamoDB."""
    resp = table.get_item(
        Key={"ruleSetVersion": rule_set_version, "chunkId": chunk_id},
        ProjectionExpression="chunkId",
    )
    return "Item" in resp


def _convert_floats(obj: Any) -> Any:
    """Convert float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(v) for v in obj]
    return obj


# ─────────────────────────────────────────────────────────────
# Main ingestion logic
# ─────────────────────────────────────────────────────────────

def ingest_file(
    file_path: Path,
    table,
    rule_set_version: str,
) -> dict:
    """
    Ingest a single rules file.

    Returns a summary dict: {chunks_written, chunks_skipped, tokens_embedded}.
    """
    meta, body = parse_frontmatter(file_path)

    if meta is None:
        print(f"REFUSED: {file_path.name} — no valid YAML frontmatter found. "
              f"File will NOT be ingested.")
        return {"chunks_written": 0, "chunks_skipped": 0, "tokens_embedded": 0}

    error = validate_frontmatter(meta, file_path)
    if error:
        print(error)
        return {"chunks_written": 0, "chunks_skipped": 0, "tokens_embedded": 0}

    source_url = str(meta["source_url"])
    source_title = str(meta.get("source_title", file_path.stem))
    retrieved_on = str(meta["retrieved_on"])
    authority = str(meta["authority"])

    # Use frontmatter version if present, otherwise use the CLI argument
    version = str(meta.get("rule_set_version", rule_set_version))

    # Chunk the document
    raw_chunks = chunk_document(body)
    if not raw_chunks:
        print(f"WARNING: {file_path.name} — no chunks produced from body text.")
        return {"chunks_written": 0, "chunks_skipped": 0, "tokens_embedded": 0}

    print(f"  {file_path.name}: {len(raw_chunks)} chunks generated")

    # Check for duplicates first
    chunks_to_embed: list[tuple[str, str, str]] = []  # (chunk_id, heading, text)
    skipped = 0

    for heading_path, chunk_text in raw_chunks:
        chunk_id = make_chunk_id(source_url, heading_path, chunk_text)
        if _item_exists(table, version, chunk_id):
            skipped += 1
        else:
            chunks_to_embed.append((chunk_id, heading_path, chunk_text))

    if not chunks_to_embed:
        print(f"  {file_path.name}: all {skipped} chunks already exist — skipped")
        return {"chunks_written": 0, "chunks_skipped": skipped, "tokens_embedded": 0}

    # Embed in batches
    embedding_model = MODEL_CONFIG["embedding_model"]
    embedding_dim = MODEL_CONFIG["embedding_dim"]
    total_tokens = 0
    written = 0

    for batch_start in range(0, len(chunks_to_embed), EMBED_BATCH_SIZE):
        batch = chunks_to_embed[batch_start:batch_start + EMBED_BATCH_SIZE]
        texts = [t for _, _, t in batch]

        vectors = gemini.embed(texts)

        # Write each chunk to DynamoDB
        with table.batch_writer() as writer:
            for (chunk_id, heading_path, chunk_text), vector in zip(batch, vectors):
                token_count = _token_count(chunk_text)
                total_tokens += token_count

                chunk = RuleChunk.new(
                    ruleSetVersion=version,
                    chunkId=chunk_id,
                    text=chunk_text,
                    embedding=vector,
                    embeddingModel=embedding_model,
                    embeddingDim=embedding_dim,
                    sourceUrl=source_url,
                    sourceTitle=source_title,
                    retrievedOn=retrieved_on,
                    authority=authority,
                    headingPath=heading_path,
                    tokenCount=token_count,
                )

                item = _convert_floats(chunk.to_dict())
                writer.put_item(Item=item)
                written += 1

    return {"chunks_written": written, "chunks_skipped": skipped, "tokens_embedded": total_tokens}


def main():
    parser = argparse.ArgumentParser(
        description="Ingest EPFO rules into DynamoDB RuleChunks table."
    )
    parser.add_argument(
        "--rules-dir",
        default="rules_source",
        help="Directory containing .md rule files (default: rules_source/)",
    )
    parser.add_argument(
        "--table-name",
        default=os.environ.get("RULECHUNKS_TABLE_NAME", "epf-sentinel-RuleChunks-dev"),
        help="DynamoDB table name (default: epf-sentinel-RuleChunks-dev)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "ap-south-1"),
        help="AWS region (default: ap-south-1)",
    )
    parser.add_argument(
        "--rule-set-version",
        default="v1",
        help="Default rule set version if not in frontmatter (default: v1)",
    )
    args = parser.parse_args()

    rules_dir = Path(args.rules_dir)
    if not rules_dir.is_dir():
        print(f"ERROR: Rules directory '{rules_dir}' does not exist.")
        sys.exit(1)

    md_files = sorted(rules_dir.glob("*.md"))
    # Exclude README.md
    md_files = [f for f in md_files if f.name.lower() != "readme.md"]

    if not md_files:
        print(f"No .md files found in {rules_dir}/")
        sys.exit(0)

    print(f"Found {len(md_files)} rule file(s) in {rules_dir}/")
    print(f"Target table: {args.table_name} ({args.region})")
    print()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(args.table_name)

    total_files = 0
    total_written = 0
    total_skipped = 0
    total_tokens = 0

    for md_file in md_files:
        total_files += 1
        result = ingest_file(md_file, table, args.rule_set_version)
        total_written += result["chunks_written"]
        total_skipped += result["chunks_skipped"]
        total_tokens += result["tokens_embedded"]

    print()
    print("=" * 60)
    print("INGEST SUMMARY")
    print("=" * 60)
    print(f"  Files processed:           {total_files}")
    print(f"  Chunks written:            {total_written}")
    print(f"  Chunks skipped (dupes):    {total_skipped}")
    print(f"  Total tokens embedded:     {total_tokens}")
    print("=" * 60)


if __name__ == "__main__":
    main()
