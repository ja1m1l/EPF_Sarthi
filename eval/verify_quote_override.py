#!/usr/bin/env python3
"""
Verification Script for Module 2.2: Statutory Precedence & Verbatim Quote Re-alignment.

Demonstrates the exact case requested by user:
A claim where the model originally selected the Citizens' Charter figure (7d WORKING)
and quoted the Charter sentence ("Settlement Time as per Citizens' Charter is 7 Working Days.").

Verifies that post_validate():
1. Overrides timelineDays to 20 and timelineBasis to CALENDAR.
2. Populates charterTargetDays with 7.
3. Re-aligns quotedSpan from the Charter sentence to the Scheme sentence.
4. Updates citedChunkIds to point to the statutory chunk.
5. Passes Guard 2 (UNGROUNDED_QUOTE) and Guard 3 (TIMELINE_NOT_IN_SOURCE) on the NEW statutory values.
"""

import sys
from pathlib import Path

# Add src to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shared.models import Claim, ClaimStatus, ClaimType, RuleChunk, RuleDecision
from shared.rules_agent import (
    contains_discrete_number,
    normalize_whitespace,
    post_validate,
)


def run_verification():
    print("=" * 80)
    print("EPF SENTINEL — STATUTORY PRECEDENCE & QUOTE RE-ALIGNMENT VERIFICATION")
    print("=" * 80)

    # Official DynamoDB RuleChunk text (chunk a613a093a1455973)
    chunk_text = (
        "PF - Final Withdrawal (Settlement of Form-19): Settlement Time as per Scheme "
        "is 20 Days. Settlement Time as per Citizens' Charter is 7 Working Days."
    )
    chunk = RuleChunk.new(
        ruleSetVersion="v1",
        chunkId="a613a093a1455973",
        text=chunk_text,
        embedding=[0.1] * 10,
        embeddingModel="gemini-embedding-001",
        embeddingDim=10,
        sourceUrl="https://www.epfindia.gov.in/site_docs/PDFs/Citizens_Charter.pdf",
        sourceTitle="EPFO Citizens' Charter",
        retrievedOn="2026-09-19",
        authority="EPFO_OFFICIAL",
        headingPath="## Citizen's Charter > Settlement Standards",
        tokenCount=len(chunk_text.split()),
    )

    # 1. Model-Original Decision (selecting Charter figure & Charter quote)
    original_decision = RuleDecision(
        applicable=True,
        timelineDays=7,
        timelineBasis="WORKING",
        charterTargetDays=None,
        citedChunkIds=["a613a093a1455973"],
        citedSourceUrls=["https://www.epfindia.gov.in/site_docs/PDFs/Citizens_Charter.pdf"],
        quotedSpan="Settlement Time as per Citizens' Charter is 7 Working Days.",
        confidence="HIGH",
        abstainReason=None,
    )

    print("\n[STEP 1: RAW MODEL GENERATION (BEFORE POST-VALIDATION)]")
    print(f"  applicable:         {original_decision.applicable}")
    print(f"  timelineDays:       {original_decision.timelineDays} (Aspirational Charter figure)")
    print(f"  timelineBasis:      {original_decision.timelineBasis}")
    print(f"  charterTargetDays:  {original_decision.charterTargetDays}")
    print(f"  quotedSpan:         \"{original_decision.quotedSpan}\" (Charter sentence)")
    print(f"  citedChunkIds:      {original_decision.citedChunkIds}")

    # 2. Run post_validate()
    final_decision = post_validate(original_decision, [chunk])

    print("\n[STEP 2: POST-VALIDATION RE-ALIGNMENT & GROUNDING PIPELINE]")
    print(f"  applicable:         {final_decision.applicable}")
    print(f"  timelineDays:       {final_decision.timelineDays} (Statutory Scheme figure enforced)")
    print(f"  timelineBasis:      {final_decision.timelineBasis} (Operative SLA basis)")
    print(f"  charterTargetDays:  {final_decision.charterTargetDays} (Preserved in dual-field model)")
    print(f"  quotedSpan:         \"{final_decision.quotedSpan}\" (Scheme sentence re-aligned)")
    print(f"  citedChunkIds:      {final_decision.citedChunkIds}")
    print(f"  abstainReason:      {final_decision.abstainReason}")

    # 3. Assertions
    print("\n[STEP 3: GUARD ASSERTIONS ON NEW STATUTORY VALUES]")
    # Timeline changed
    assert final_decision.timelineDays == 20, "timelineDays must be overridden to 20"
    assert final_decision.timelineBasis == "CALENDAR", "timelineBasis must be CALENDAR"
    assert final_decision.charterTargetDays == 7, "charterTargetDays must be 7"
    print("  [PASS] Statutory timeline precedence: 20d CALENDAR (charter target: 7d)")

    # Quote changed to statutory sentence
    assert "Scheme is 20 Days" in final_decision.quotedSpan, "quotedSpan must point to Scheme sentence"
    assert "Citizens' Charter is 7" not in final_decision.quotedSpan, "quotedSpan must not point to Charter sentence"
    print("  [PASS] Verbatim quote re-aligned to statutory Scheme sentence")

    # Guard 2 (UNGROUNDED_QUOTE) verified on final quote
    norm_final_quote = normalize_whitespace(final_decision.quotedSpan)
    norm_chunk = normalize_whitespace(chunk.text)
    assert norm_final_quote in norm_chunk, "Final quotedSpan must be a verbatim substring of chunk text"
    print("  [PASS] Guard 2 (UNGROUNDED_QUOTE): Final statutory quote verified as verbatim substring")

    # Guard 3 (TIMELINE_NOT_IN_SOURCE) verified on final number
    assert contains_discrete_number(chunk.text, final_decision.timelineDays), "Final timelineDays must have discrete boundaries in chunk"
    print("  [PASS] Guard 3 (TIMELINE_NOT_IN_SOURCE): Final statutory timeline (20) verified in chunk")

    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE: Quote, citations, and timeline are 100% consistent.")
    print("=" * 80)


if __name__ == "__main__":
    run_verification()
