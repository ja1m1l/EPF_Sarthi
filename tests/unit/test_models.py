# EPF Sentinel ? pytest unit tests for models.py serialisation round-trip
#
# What is real vs stubbed
# -----------------------
# * Real  : all model classes, Claim.new(), RuleChunk.new(), AnalysisRun.new(),
#           to_dict(), from_dict(), enum values, paise integer enforcement,
#           UTC-offset timestamp helpers.
# * No mocks, no stubs, no network, no DynamoDB.  Pure in-process logic.
# * time.time() is NOT mocked; AnalysisRun.expiresAt is passed explicitly
#   by each test so results are deterministic.
# * sys.path is configured by the root conftest.py (adds src/shared + src).

from __future__ import annotations

import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta

import pytest

from shared.models import (
    AnalysisRun,
    Claim,
    ClaimStatus,
    ClaimType,
    RuleChunk,
    datetime_to_iso,
    iso_to_datetime,
    utc_now_iso,
)


# -----------------------------------------------------------------
# Timestamp helpers
# -----------------------------------------------------------------

class TestDatetimeHelpers:
    def test_utc_now_iso_has_utc_offset(self):
        ts = utc_now_iso()
        assert ts.endswith("+00:00"), f"Expected +00:00 suffix, got: {ts!r}"

    def test_datetime_to_iso_converts_non_utc(self):
        ist = timezone(timedelta(hours=5, minutes=30))
        dt_ist = datetime(2026, 8, 1, 10, 30, 0, tzinfo=ist)
        iso = datetime_to_iso(dt_ist)
        assert "+00:00" in iso
        # 10:30 IST == 05:00 UTC
        assert "05:00:00" in iso

    def test_datetime_to_iso_rejects_naive(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            datetime_to_iso(datetime(2026, 8, 1, 10, 30, 0))

    def test_iso_to_datetime_round_trip(self):
        original = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        iso = datetime_to_iso(original)
        recovered = iso_to_datetime(iso)
        assert recovered == original

    def test_iso_to_datetime_rejects_no_offset(self):
        with pytest.raises(ValueError, match="no timezone offset"):
            iso_to_datetime("2026-08-01T12:00:00")


# -----------------------------------------------------------------
# ClaimType / ClaimStatus enums
# -----------------------------------------------------------------

class TestEnums:
    def test_claim_type_values(self):
        assert ClaimType.FINAL_SETTLEMENT.value == "FINAL_SETTLEMENT"

    def test_claim_status_all_values(self):
        expected = {"SUBMITTED", "PENDING", "UNDER_PROCESS", "REJECTED", "SETTLED"}
        actual = {s.value for s in ClaimStatus}
        assert actual == expected

    def test_enum_round_trip_from_string(self):
        assert ClaimType("FINAL_SETTLEMENT") is ClaimType.FINAL_SETTLEMENT
        assert ClaimStatus("UNDER_PROCESS") is ClaimStatus.UNDER_PROCESS


# -----------------------------------------------------------------
# Claim
# -----------------------------------------------------------------

class TestClaim:
    def _make_claim(self, **overrides) -> Claim:
        defaults = dict(
            userId="user-001",
            claimType=ClaimType.FINAL_SETTLEMENT,
            claimDateIso="2026-08-01T00:00:00+00:00",
            amountPaise=48_000_000,
            status=ClaimStatus.PENDING,
        )
        defaults.update(overrides)
        return Claim.new(**defaults)

    # paise enforcement
    def test_amount_paise_must_be_int(self):
        with pytest.raises(TypeError, match="amountPaise must be int"):
            self._make_claim(amountPaise=4800.0)

    def test_amount_paise_must_not_be_float_zero(self):
        with pytest.raises(TypeError):
            self._make_claim(amountPaise=0.0)

    def test_amount_paise_zero_int_is_valid(self):
        claim = self._make_claim(amountPaise=0)
        assert claim.amountPaise == 0

    def test_amount_paise_negative_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            self._make_claim(amountPaise=-1)

    # field types
    def test_claim_id_is_string(self):
        claim = self._make_claim()
        assert isinstance(claim.claimId, str) and len(claim.claimId) > 0

    def test_timestamps_are_utc_iso(self):
        claim = self._make_claim()
        for attr in ("createdAt", "updatedAt"):
            ts = getattr(claim, attr)
            assert ts.endswith("+00:00"), f"{attr} missing UTC offset: {ts!r}"

    def test_deficiency_date_defaults_none(self):
        assert self._make_claim().deficiencyRaisedDateIso is None

    def test_deficiency_date_can_be_set(self):
        claim = self._make_claim(deficiencyRaisedDateIso="2026-09-01T00:00:00+00:00")
        assert claim.deficiencyRaisedDateIso == "2026-09-01T00:00:00+00:00"

    # serialisation round-trip
    def test_to_dict_contains_enum_strings(self):
        d = self._make_claim().to_dict()
        assert d["claimType"] == "FINAL_SETTLEMENT"
        assert d["status"] == "PENDING"

    def test_to_dict_amount_is_int(self):
        d = self._make_claim(amountPaise=48_000_000).to_dict()
        assert isinstance(d["amountPaise"], int)
        assert d["amountPaise"] == 48_000_000

    def test_round_trip_no_deficiency(self):
        original = self._make_claim()
        assert Claim.from_dict(original.to_dict()) == original

    def test_round_trip_with_deficiency(self):
        original = self._make_claim(deficiencyRaisedDateIso="2026-09-15T08:30:00+00:00")
        recovered = Claim.from_dict(original.to_dict())
        assert recovered == original
        assert recovered.deficiencyRaisedDateIso == "2026-09-15T08:30:00+00:00"

    def test_round_trip_all_statuses(self):
        for status in ClaimStatus:
            original = self._make_claim(status=status)
            assert Claim.from_dict(original.to_dict()).status is status

    def test_json_serialisable(self):
        claim = self._make_claim()
        recovered = Claim.from_dict(json.loads(json.dumps(claim.to_dict())))
        assert recovered == claim

    def test_from_dict_accepts_int_amount(self):
        d = self._make_claim().to_dict()
        d["amountPaise"] = 48_000_000
        recovered = Claim.from_dict(d)
        assert isinstance(recovered.amountPaise, int)
        assert recovered.amountPaise == 48_000_000

    def test_from_dict_accepts_decimal_no_fraction_amount(self):
        d = self._make_claim().to_dict()
        d["amountPaise"] = Decimal("48000000")
        recovered = Claim.from_dict(d)
        assert isinstance(recovered.amountPaise, int)
        assert recovered.amountPaise == 48_000_000

    def test_from_dict_rejects_float_amount(self):
        d = self._make_claim().to_dict()
        d["amountPaise"] = 100.0
        with pytest.raises(TypeError):
            Claim.from_dict(d)

    def test_from_dict_rejects_decimal_with_fraction_amount(self):
        d = self._make_claim().to_dict()
        d["amountPaise"] = Decimal("100.50")
        with pytest.raises(ValueError):
            Claim.from_dict(d)

    def test_from_dict_rejects_bool_amount(self):
        d = self._make_claim().to_dict()
        d["amountPaise"] = True
        with pytest.raises(TypeError):
            Claim.from_dict(d)

    def test_from_dict_rejects_str_amount(self):
        d = self._make_claim().to_dict()
        d["amountPaise"] = "48000000"
        with pytest.raises(TypeError):
            Claim.from_dict(d)

    def test_two_new_claims_have_different_ids(self):
        assert self._make_claim().claimId != self._make_claim().claimId


# -----------------------------------------------------------------
# RuleChunk
# -----------------------------------------------------------------

class TestRuleChunk:
    def _make_chunk(self, **overrides) -> RuleChunk:
        defaults = dict(
            ruleSetVersion="2024-10-01",
            chunkId="chunk-001",
            text="EPFO shall settle claims within 20 days.",
            sourceRef="EPFO Circular 2024/10/01 S3.2",
            embedding=[0.1, 0.2, 0.3],
        )
        defaults.update(overrides)
        return RuleChunk.new(**defaults)

    def test_created_at_is_utc(self):
        assert self._make_chunk().createdAt.endswith("+00:00")

    def test_round_trip(self):
        original = self._make_chunk()
        assert RuleChunk.from_dict(original.to_dict()) == original

    def test_embedding_values_are_float(self):
        chunk = self._make_chunk(embedding=[1, 2, 3])
        recovered = RuleChunk.from_dict(chunk.to_dict())
        assert all(isinstance(v, float) for v in recovered.embedding)

    def test_json_serialisable(self):
        chunk = self._make_chunk()
        recovered = RuleChunk.from_dict(json.loads(json.dumps(chunk.to_dict())))
        assert recovered == chunk

    def test_long_embedding_preserves_precision(self):
        embedding = [i / 1000.0 for i in range(768)]
        chunk = self._make_chunk(embedding=embedding)
        recovered = RuleChunk.from_dict(chunk.to_dict())
        assert len(recovered.embedding) == 768
        assert recovered.embedding[0] == pytest.approx(0.0)
        assert recovered.embedding[767] == pytest.approx(0.767)


# -----------------------------------------------------------------
# AnalysisRun
# -----------------------------------------------------------------

class TestAnalysisRun:
    _EXPIRES_AT = 1_800_000_000

    def _make_run(self, **overrides) -> AnalysisRun:
        defaults = dict(claimId="claim-abc", expiresAt=self._EXPIRES_AT)
        defaults.update(overrides)
        return AnalysisRun.new(**defaults)

    def test_initial_status_is_running(self):
        assert self._make_run().status == "RUNNING"

    def test_run_id_is_string(self):
        run = self._make_run()
        assert isinstance(run.runId, str) and len(run.runId) > 0

    def test_started_at_is_utc(self):
        assert self._make_run().startedAt.endswith("+00:00")

    def test_finished_at_defaults_none(self):
        assert self._make_run().finishedAt is None

    def test_result_json_defaults_none(self):
        assert self._make_run().resultJson is None

    def test_expires_at_is_int(self):
        run = self._make_run()
        assert isinstance(run.expiresAt, int)
        assert run.expiresAt == self._EXPIRES_AT

    def test_round_trip_minimal(self):
        original = self._make_run()
        assert AnalysisRun.from_dict(original.to_dict()) == original

    def test_round_trip_with_result(self):
        run = self._make_run(
            stepFunctionExecutionArn="arn:aws:states:ap-south-1:123456789012:execution:sf:run-1"
        )
        d = run.to_dict()
        d["status"] = "SUCCEEDED"
        d["finishedAt"] = utc_now_iso()
        d["resultJson"] = json.dumps({"verdict": "SLA_BREACHED"})
        recovered = AnalysisRun.from_dict(d)
        assert recovered.status == "SUCCEEDED"
        assert recovered.finishedAt is not None
        assert recovered.resultJson is not None

    def test_json_serialisable(self):
        run = self._make_run()
        recovered = AnalysisRun.from_dict(json.loads(json.dumps(run.to_dict())))
        assert recovered == run

    def test_from_dict_accepts_int_expires_at(self):
        d = self._make_run().to_dict()
        d["expiresAt"] = self._EXPIRES_AT
        recovered = AnalysisRun.from_dict(d)
        assert isinstance(recovered.expiresAt, int)
        assert recovered.expiresAt == self._EXPIRES_AT

    def test_from_dict_accepts_decimal_no_fraction_expires_at(self):
        d = self._make_run().to_dict()
        d["expiresAt"] = Decimal(str(self._EXPIRES_AT))
        recovered = AnalysisRun.from_dict(d)
        assert isinstance(recovered.expiresAt, int)
        assert recovered.expiresAt == self._EXPIRES_AT

    def test_from_dict_rejects_float_expires_at(self):
        d = self._make_run().to_dict()
        d["expiresAt"] = 100.0
        with pytest.raises(TypeError):
            AnalysisRun.from_dict(d)

    def test_from_dict_rejects_decimal_with_fraction_expires_at(self):
        d = self._make_run().to_dict()
        d["expiresAt"] = Decimal("1700000000.75")
        with pytest.raises(ValueError):
            AnalysisRun.from_dict(d)

    def test_from_dict_rejects_bool_expires_at(self):
        d = self._make_run().to_dict()
        d["expiresAt"] = True
        with pytest.raises(TypeError):
            AnalysisRun.from_dict(d)

    def test_from_dict_rejects_str_expires_at(self):
        d = self._make_run().to_dict()
        d["expiresAt"] = str(self._EXPIRES_AT)
        with pytest.raises(TypeError):
            AnalysisRun.from_dict(d)

    def test_two_new_runs_have_different_ids(self):
        assert self._make_run().runId != self._make_run().runId
