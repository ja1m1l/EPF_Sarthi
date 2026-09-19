# EPF Sentinel — pytest unit tests for shared/rules_agent.py and Lambda handler
#
# Tests code-level grounding guards, deterministic precedence,
# number boundary check, and strict FAILED vs ABSTAINED separation.

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from shared.models import Claim, ClaimStatus, ClaimType, RuleChunk, RuleDecision
from shared.rules_agent import (
    RulesAgentInfrastructureError,
    contains_discrete_number,
    emit_abstain_metric,
    emit_metric,
    normalize_whitespace,
    post_validate,
    select_rule,
)


# ─────────────────────────────────────────────────────────────
# Fixtures & Helpers
# ─────────────────────────────────────────────────────────────

def _make_claim(**overrides) -> Claim:
    defaults = dict(
        userId="user-test-01",
        claimType=ClaimType.FINAL_SETTLEMENT,
        claimDateIso="2026-08-01T00:00:00+00:00",
        amountPaise=48000000,
        status=ClaimStatus.PENDING,
    )
    defaults.update(overrides)
    return Claim.new(**defaults)


def _make_chunk(chunk_id: str, text: str) -> RuleChunk:
    return RuleChunk.new(
        ruleSetVersion="v1",
        chunkId=chunk_id,
        text=text,
        embedding=[0.1] * 10,
        embeddingModel="gemini-embedding-001",
        embeddingDim=10,
        sourceUrl="https://example.com/rules",
        sourceTitle="EPFO Rules",
        retrievedOn="2026-09-19",
        authority="EPFO_OFFICIAL",
        headingPath="## Standards",
        tokenCount=len(text.split()),
    )


# ─────────────────────────────────────────────────────────────
# 1. Number Boundary Verification (TIMELINE_NOT_IN_SOURCE check)
# ─────────────────────────────────────────────────────────────

class TestDiscreteNumberBoundary:
    def test_matches_discrete_number(self):
        assert contains_discrete_number("Settlement Time is 20 Days.", 20) is True
        assert contains_discrete_number("Within 7 working days.", 7) is True
        assert contains_discrete_number("Section 20 of Act", 20) is True
        assert contains_discrete_number("A 20-day timeline", 20) is True

    def test_rejects_larger_embedded_number(self):
        # 20 inside 120 must not match
        assert contains_discrete_number("Settlement time is 120 days.", 20) is False
        assert contains_discrete_number("Target 320 claims", 20) is False

    def test_rejects_decimal_fraction(self):
        # 20 inside 20.3 must not match
        assert contains_discrete_number("Per clause 20.3 of scheme", 20) is False
        assert contains_discrete_number("Rate is 20.5 percent", 20) is False

    def test_boundary_safe_seven_rejection(self):
        # 7 inside 72, 137, or 7.1 must not match
        assert contains_discrete_number("See paragraph 72 of Scheme", 7) is False
        assert contains_discrete_number("Total 137 days elapsed", 7) is False
        assert contains_discrete_number("Clause 7.2 guideline", 7) is False
        assert contains_discrete_number("Target 7 Working Days", 7) is True


# ─────────────────────────────────────────────────────────────
# 2. Post-Validation Guards (Deterministic Order)
# ─────────────────────────────────────────────────────────────

class TestPostValidationGuardPrecedence:
    """
    Guards run in deterministic order:
    1. FABRICATED_CITATION
    2. UNGROUNDED_QUOTE
    3. TIMELINE_NOT_IN_SOURCE
    """

    @patch("shared.rules_agent.emit_abstain_metric")
    def test_fabricated_citation_fires_first(self, mock_metric):
        chunk1 = _make_chunk("c1", "Settlement is 20 days.")
        retrieved = [chunk1]

        # Model cited 'c_fake', and also made up a quote and wrong timeline
        decision = RuleDecision(
            applicable=True,
            timelineDays=99,
            timelineBasis="CALENDAR",
            citedChunkIds=["c_fake"],
            citedSourceUrls=["https://fake.com"],
            quotedSpan="This quote does not exist anywhere.",
            confidence="HIGH",
        )

        val = post_validate(decision, retrieved)
        assert val.applicable is False
        # Guard 1 FABRICATED_CITATION takes precedence over ungrounded quote and wrong timeline
        assert val.abstainReason == "FABRICATED_CITATION"
        mock_metric.assert_called_with("FABRICATED_CITATION")

    @patch("shared.rules_agent.emit_abstain_metric")
    def test_ungrounded_quote_fires_second(self, mock_metric):
        chunk1 = _make_chunk("c1", "Settlement time is 20 days.")
        retrieved = [chunk1]

        # Citation is valid ('c1'), but quote is hallucinated, and timeline is also wrong
        decision = RuleDecision(
            applicable=True,
            timelineDays=99,
            timelineBasis="CALENDAR",
            citedChunkIds=["c1"],
            citedSourceUrls=["https://example.com/rules"],
            quotedSpan="Hallucinated text that does not exist in c1.",
            confidence="HIGH",
        )

        val = post_validate(decision, retrieved)
        assert val.applicable is False
        # Guard 2 UNGROUNDED_QUOTE takes precedence over wrong timeline
        assert val.abstainReason == "UNGROUNDED_QUOTE"
        mock_metric.assert_called_with("UNGROUNDED_QUOTE")

    @patch("shared.rules_agent.emit_abstain_metric")
    def test_timeline_not_in_source_fires_third(self, mock_metric):
        chunk1 = _make_chunk("c1", "Settlement time is 20 days per Citizens Charter 20.3.")
        retrieved = [chunk1]

        # Citation is valid ('c1'), quote is a verbatim substring,
        # but model claimed timelineDays=120 or 99 (digits not in chunk as discrete number)
        decision = RuleDecision(
            applicable=True,
            timelineDays=99,
            timelineBasis="CALENDAR",
            citedChunkIds=["c1"],
            citedSourceUrls=["https://example.com/rules"],
            quotedSpan="Settlement time is 20 days",
            confidence="HIGH",
        )

        val = post_validate(decision, retrieved)
        assert val.applicable is False
        assert val.abstainReason == "TIMELINE_NOT_IN_SOURCE"
        mock_metric.assert_called_with("TIMELINE_NOT_IN_SOURCE")

    def test_post_validate_never_substitutes_or_mutates_fields(self):
        """
        post_validate() must NEVER overwrite or substitute quotedSpan, timelineDays,
        timelineBasis, or citedChunkIds. Guards only accept or reject.
        """
        chunk1 = _make_chunk(
            "c1",
            "PF - Final Withdrawal (Settlement of Form-19): Settlement Time as per Scheme is 20 Days. Settlement Time as per Citizens' Charter is 7 Working Days.",
        )
        retrieved = [chunk1]

        original_quote = "Settlement Time as per Scheme is 20 Days."
        original_days = 20
        original_basis = "CALENDAR"
        original_citations = ["c1"]

        decision = RuleDecision(
            applicable=True,
            timelineDays=original_days,
            timelineBasis=original_basis,
            charterTargetDays=7,
            citedChunkIds=list(original_citations),
            citedSourceUrls=["https://example.com/rules"],
            quotedSpan=original_quote,
            confidence="HIGH",
        )

        val = post_validate(decision, retrieved)
        assert val.applicable is True
        # Invariant: zero mutation of model outputs
        assert val.quotedSpan == original_quote
        assert val.timelineDays == original_days
        assert val.timelineBasis == original_basis
        assert val.citedChunkIds == original_citations
        assert val.charterTargetDays == 7
        assert val.abstainReason is None

    def test_citation_provenance_is_derived_from_chunks_not_model_output(self):
        """
        The source URL and retrievedOn shown in the UI must come from the
        retrieved chunk, never from the model, so a displayed link cannot be
        one the model invented.
        """
        chunk = _make_chunk("c1", "Settlement Time as per Scheme is 20 Days.")
        decision = RuleDecision(
            applicable=True,
            timelineDays=20,
            timelineBasis="CALENDAR",
            citedChunkIds=["c1"],
            citedSourceUrls=["https://hallucinated.example.org/not-real"],
            quotedSpan="Settlement Time as per Scheme is 20 Days.",
            confidence="HIGH",
        )

        val = post_validate(decision, [chunk])

        assert val.applicable is True
        assert val.citedSourceUrls == [chunk.sourceUrl]
        assert val.citedSources == [{
            "chunkId": "c1",
            "sourceUrl": chunk.sourceUrl,
            "sourceTitle": chunk.sourceTitle,
            "retrievedOn": chunk.retrievedOn,
            "authority": chunk.authority,
        }]

    def test_abstention_carries_no_citation_provenance(self):
        chunk = _make_chunk("c1", "Settlement Time as per Scheme is 20 Days.")
        decision = RuleDecision(
            applicable=True,
            timelineDays=20,
            timelineBasis="CALENDAR",
            citedChunkIds=["c1"],
            citedSourceUrls=[],
            quotedSpan="a quote that does not appear in the chunk",
            confidence="HIGH",
        )

        val = post_validate(decision, [chunk])

        assert val.applicable is False
        assert val.abstainReason == "UNGROUNDED_QUOTE"
        assert val.citedSources == []
        assert val.citedSourceUrls == []

    @patch("shared.rules_agent.emit_metric")
    def test_charter_target_days_cleared_if_not_in_source(self, mock_metric):
        """
        charterTargetDays is only populated if it passes contains_discrete_number check.
        If the model hallucinates a number not in source, decision returns applicable
        with charterTargetDays is None and CharterTargetDowngrade metric is called exactly once.
        """
        chunk1 = _make_chunk(
            "c1",
            "Settlement Time as per Scheme is 20 Days.",
        )
        retrieved = [chunk1]

        decision = RuleDecision(
            applicable=True,
            timelineDays=20,
            timelineBasis="CALENDAR",
            charterTargetDays=99,  # 99 is not in c1
            citedChunkIds=["c1"],
            citedSourceUrls=["https://example.com/rules"],
            quotedSpan="Settlement Time as per Scheme is 20 Days.",
            confidence="HIGH",
        )

        val = post_validate(decision, retrieved)
        assert val.applicable is True
        assert val.timelineDays == 20
        assert val.charterTargetDays is None  # cleared, not repaired with code-guess
        mock_metric.assert_called_once_with("CharterTargetDowngrade")

    @patch("shared.rules_agent.emit_abstain_metric")
    def test_all_fabricated_citations_asserts_fabricated_citation(self, mock_metric):
        """
        When citedChunkIds contains multiple non-empty IDs but every single one
        is fabricated (citedChunkIds=['fake1', 'fake2']), Guard 1 asserts
        FABRICATED_CITATION and emits RuleAbstain with Reason='FABRICATED_CITATION'.
        """
        chunk1 = _make_chunk("c1", "Settlement Time as per Scheme is 20 Days.")
        retrieved = [chunk1]

        decision = RuleDecision(
            applicable=True,
            timelineDays=20,
            timelineBasis="CALENDAR",
            citedChunkIds=["fake1", "fake2"],
            citedSourceUrls=["https://fake.com/1", "https://fake.com/2"],
            quotedSpan="Settlement Time as per Scheme is 20 Days.",
            confidence="HIGH",
        )

        val = post_validate(decision, retrieved)
        assert val.applicable is False
        assert val.abstainReason == "FABRICATED_CITATION"
        mock_metric.assert_called_with("FABRICATED_CITATION")

    def test_single_timeline_without_charter_split_passes(self):
        """Single timeline chunks (e.g. death claim within 7 days) pass with no conflict."""
        chunk_death = _make_chunk(
            "c_death",
            "Settlement of death claims shall be completed within 7 days from the date of receipt.",
        )
        decision = RuleDecision(
            applicable=True,
            timelineDays=7,
            timelineBasis="CALENDAR",
            citedChunkIds=["c_death"],
            citedSourceUrls=["https://example.com/rules"],
            quotedSpan="within 7 days from the date of receipt.",
            confidence="HIGH",
        )

        val = post_validate(decision, [chunk_death])
        assert val.applicable is True
        assert val.timelineDays == 7
        assert val.charterTargetDays is None
        assert val.abstainReason is None

    @patch("shared.rules_agent.emit_abstain_metric")
    def test_conflicting_timeline_sources_abstain_preserved(self, mock_metric):
        """
        When model abstains with CONFLICTING_TIMELINE_SOURCES, post_validate() preserves it
        and emits the EPFSentinel/RuleAbstain metric with Reason='CONFLICTING_TIMELINE_SOURCES'.
        """
        decision = RuleDecision.abstain(
            reason="CONFLICTING_TIMELINE_SOURCES",
            confidence="HIGH",
        )
        val = post_validate(decision, [])
        assert val.applicable is False
        assert val.abstainReason == "CONFLICTING_TIMELINE_SOURCES"
        mock_metric.assert_called_with("CONFLICTING_TIMELINE_SOURCES")



# ─────────────────────────────────────────────────────────────
# 3. select_rule() Integration & JSON Retries
# ─────────────────────────────────────────────────────────────

class TestSelectRule:
    @patch("shared.rules_agent.emit_abstain_metric")
    @patch("shared.rules_agent.search")
    @patch("shared.rules_agent.embed_query")
    def test_empty_retrieval_abstains_immediately(self, mock_embed, mock_search, mock_metric):
        mock_embed.return_value = [0.1] * 10
        mock_search.return_value = []

        claim = _make_claim()
        decision = select_rule(claim, rule_set_version="v1")

        assert decision.applicable is False
        assert decision.abstainReason == "NO_APPLICABLE_RULE"
        mock_metric.assert_called_with("NO_APPLICABLE_RULE")

    @patch("shared.rules_agent.gemini.generate")
    @patch("shared.rules_agent.search")
    @patch("shared.rules_agent.embed_query")
    def test_retries_malformed_json_and_succeeds(self, mock_embed, mock_search, mock_generate):
        chunk1 = _make_chunk("c1", "Settlement Time as per Scheme is 20 Days.")
        mock_embed.return_value = [0.1] * 10
        mock_search.return_value = [(chunk1, 0.9)]

        valid_response = json.dumps({
            "applicable": True,
            "timelineDays": 20,
            "timelineBasis": "CALENDAR",
            "citedChunkIds": ["c1"],
            "citedSourceUrls": ["https://example.com/rules"],
            "quotedSpan": "Settlement Time as per Scheme is 20 Days.",
            "confidence": "HIGH",
            "abstainReason": None,
        })

        # First call returns invalid JSON, second call returns valid JSON
        mock_generate.side_effect = ["not a valid json {", valid_response]

        claim = _make_claim()
        decision = select_rule(claim, rule_set_version="v1")

        assert decision.applicable is True
        assert decision.timelineDays == 20
        assert mock_generate.call_count == 2

    @patch("shared.rules_agent.emit_abstain_metric")
    @patch("shared.rules_agent.gemini.generate")
    @patch("shared.rules_agent.search")
    @patch("shared.rules_agent.embed_query")
    def test_exhausted_json_retries_abstains_with_model_output_invalid(
        self, mock_embed, mock_search, mock_generate, mock_metric
    ):
        chunk1 = _make_chunk("c1", "Settlement Time as per Scheme is 20 Days.")
        mock_embed.return_value = [0.1] * 10
        mock_search.return_value = [(chunk1, 0.9)]

        # 3 consecutive malformed JSONs (initial + 2 retries)
        mock_generate.side_effect = ["bad1", "bad2", "bad3"]

        claim = _make_claim()
        decision = select_rule(claim, rule_set_version="v1")

        assert decision.applicable is False
        assert decision.abstainReason == "MODEL_OUTPUT_INVALID"
        assert mock_generate.call_count == 3
        mock_metric.assert_called_with("MODEL_OUTPUT_INVALID")


# ─────────────────────────────────────────────────────────────
# 4. Strict Error Separation: FAILED vs ABSTAINED
# ─────────────────────────────────────────────────────────────

class TestErrorSeparation:
    @patch("shared.rules_agent.search")
    @patch("shared.rules_agent.embed_query")
    def test_retrieval_infrastructure_failure_raises_exception(self, mock_embed, mock_search):
        mock_embed.side_effect = ConnectionError("DynamoDB/Gemini network failure")

        claim = _make_claim()
        # Must raise RulesAgentInfrastructureError — must NEVER turn into ABSTAINED
        with pytest.raises(RulesAgentInfrastructureError, match="Retrieval infrastructure failed"):
            select_rule(claim, rule_set_version="v1")

    @patch("shared.rules_agent.gemini.generate")
    @patch("shared.rules_agent.search")
    @patch("shared.rules_agent.embed_query")
    def test_model_api_infrastructure_failure_raises_exception(self, mock_embed, mock_search, mock_generate):
        chunk1 = _make_chunk("c1", "Rule text")
        mock_embed.return_value = [0.1] * 10
        mock_search.return_value = [(chunk1, 0.8)]
        mock_generate.side_effect = RuntimeError("Secrets Manager key invalid or quota dead")

        claim = _make_claim()
        # Must raise RulesAgentInfrastructureError — must NEVER turn into ABSTAINED
        with pytest.raises(RulesAgentInfrastructureError, match="Gemini generation API failed"):
            select_rule(claim, rule_set_version="v1")


# ─────────────────────────────────────────────────────────────
# 5. Lambda Wrapper (app.handler)
# ─────────────────────────────────────────────────────────────

class TestRulesAgentLambdaHandler:
    @patch("functions.rules_agent.app.select_rule")
    def test_handler_applicable_returns_200_applicable(self, mock_select):
        from functions.rules_agent.app import handler

        mock_select.return_value = RuleDecision(
            applicable=True,
            timelineDays=20,
            timelineBasis="CALENDAR",
            citedChunkIds=["c1"],
            citedSourceUrls=["https://epf.gov.in"],
            quotedSpan="Settlement is 20 days",
            confidence="HIGH",
        )

        event = {
            "claim": {
                "userId": "u1",
                "claimId": "c1",
                "claimType": "FINAL_SETTLEMENT",
                "claimDateIso": "2026-08-01T00:00:00+00:00",
                "amountPaise": 100000,
                "status": "PENDING",
            },
            "ruleSetVersion": "v1",
        }

        resp = handler(event, MagicMock())
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["status"] == "APPLICABLE"
        assert body["decision"]["applicable"] is True
        assert body["decision"]["timelineDays"] == 20

    @patch("functions.rules_agent.app.select_rule")
    def test_handler_abstained_returns_200_abstained(self, mock_select):
        from functions.rules_agent.app import handler

        mock_select.return_value = RuleDecision.abstain("UNGROUNDED_QUOTE")

        event = {
            "claim": {
                "userId": "u1",
                "claimId": "c1",
                "claimType": "FINAL_SETTLEMENT",
                "claimDateIso": "2026-08-01T00:00:00+00:00",
                "amountPaise": 100000,
                "status": "PENDING",
            },
            "ruleSetVersion": "v1",
        }

        resp = handler(event, MagicMock())
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["status"] == "ABSTAINED"
        assert body["decision"]["applicable"] is False
        assert body["decision"]["abstainReason"] == "UNGROUNDED_QUOTE"

    @patch("functions.rules_agent.app.select_rule")
    def test_handler_infrastructure_failure_returns_500_failed(self, mock_select):
        from functions.rules_agent.app import handler

        mock_select.side_effect = RulesAgentInfrastructureError("Secrets Manager unavailable")

        event = {
            "claim": {
                "userId": "u1",
                "claimId": "c1",
                "claimType": "FINAL_SETTLEMENT",
                "claimDateIso": "2026-08-01T00:00:00+00:00",
                "amountPaise": 100000,
                "status": "PENDING",
            },
            "ruleSetVersion": "v1",
        }

        resp = handler(event, MagicMock())
        assert resp["statusCode"] == 500
        body = json.loads(resp["body"])
        assert body["status"] == "FAILED"
        assert "Secrets Manager unavailable" in body["error"]
