# EPF Sentinel — pytest unit & property tests for shared/sla.py and shared/clock.py
#
# Tests SLA computations under CALENDAR and WORKING bases, boundary conditions,
# deficiency resets, leap day arithmetic, typed error hierarchy, and hypothesis properties.

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shared.clock import today_ist
from shared.sla import (
    DeficiencyDateOrderError,
    FutureClaimDateError,
    SlaBasisError,
    SlaError,
    SlaResult,
    SlaTimelineDaysError,
    add_working_days,
    compute_sla,
    is_working_day,
)


# ─────────────────────────────────────────────────────────────
# 1. Explicit Test Table (One Assertion per Row, Literal Expected)
# ─────────────────────────────────────────────────────────────

class TestSlaExplicitTable:
    """Explicit, hand-computed test cases with literal values."""

    def test_demo_case_calendar_overdue_and_explanation(self):
        """
        Demo case:
        Claim on 2026-08-01, 20 calendar days, today 2026-09-18, no deficiency.
        August has 31 days.
        Deadline: 2026-08-01 + 20 days = 2026-08-21.
        Elapsed: 2026-09-18 - 2026-08-01 = 48 days.
        Remaining: 2026-08-21 - 2026-09-18 = -28 days.
        Status: OVERDUE.
        """
        claim_date = date(2026, 8, 1)
        timeline_days = 20
        basis: Literal["CALENDAR", "WORKING"] = "CALENDAR"
        today = date(2026, 9, 18)
        deficiency_date = None
        holidays: frozenset[date] = frozenset()

        res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis=basis,
            today=today,
            deficiency_raised_date=deficiency_date,
            holidays=holidays,
        )

        assert res.deadlineDate == date(2026, 8, 21)
        assert res.clockStartDate == date(2026, 8, 1)
        assert res.elapsedDays == 48
        assert res.remainingDays == -28
        assert res.status == "OVERDUE"
        assert res.priorElapsedDays is None

        # Pinned exact explanation literal
        expected_explanation = (
            "Clock started on 2026-08-01. "
            "Deadline was 2026-08-21 (20 calendar days). "
            "As of 2026-09-18, 48 calendar days have elapsed (28 calendar days overdue)."
        )
        assert res.explanation == expected_explanation

    def test_working_day_case_spanning_two_weekends_and_one_holiday(self):
        """
        Working-day case spanning 2 weekends + 1 holiday:
        Start: Monday 2026-08-03.
        Timeline: 10 working days.
        Holiday: Wednesday 2026-08-12.
        Weekends: Aug 8-9 (Sat-Sun), Aug 15-16 (Sat-Sun).

        Working days added:
        1: Aug 4 (Tue)
        2: Aug 5 (Wed)
        3: Aug 6 (Thu)
        4: Aug 7 (Fri)
        [Aug 8-9 Weekend]
        5: Aug 10 (Mon)
        6: Aug 11 (Tue)
        [Aug 12 Holiday]
        7: Aug 13 (Thu)
        8: Aug 14 (Fri)
        [Aug 15-16 Weekend]
        9: Aug 17 (Mon)
        10: Aug 18 (Tue)

        Deadline: 2026-08-18 (always a verified working day).
        """
        claim_date = date(2026, 8, 3)
        timeline_days = 10
        holidays = frozenset([date(2026, 8, 12)])
        today = date(2026, 8, 18)

        res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="WORKING",
            today=today,
            deficiency_raised_date=None,
            holidays=holidays,
        )

        assert res.deadlineDate == date(2026, 8, 18)
        assert is_working_day(res.deadlineDate, holidays) is True
        assert res.elapsedDays == 10
        assert res.remainingDays == 0
        assert res.status == "APPROACHING"
        assert res.priorElapsedDays is None

    def test_comparison_identical_inputs_different_deadlines(self):
        """Identical claim_date and timeline_days yield strictly different deadlines under both bases."""
        claim_date = date(2026, 8, 3)
        timeline_days = 10
        holidays = frozenset([date(2026, 8, 12)])
        today = date(2026, 8, 10)

        cal_res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="CALENDAR",
            today=today,
            deficiency_raised_date=None,
            holidays=holidays,
        )
        work_res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="WORKING",
            today=today,
            deficiency_raised_date=None,
            holidays=holidays,
        )

        # Calendar deadline: 2026-08-03 + 10d = 2026-08-13
        # Working deadline: 2026-08-18 (skipping 2 weekends and 1 holiday)
        assert cal_res.deadlineDate == date(2026, 8, 13)
        assert work_res.deadlineDate == date(2026, 8, 18)
        assert cal_res.deadlineDate != work_res.deadlineDate
        assert work_res.deadlineDate > cal_res.deadlineDate

    def test_deficiency_reset_reports_two_distinct_numbers(self):
        """
        Deficiency reset case:
        Claim: 2026-08-01. Deficiency raised: 2026-08-15. Today: 2026-08-25.
        Timeline: 20 calendar days.

        Assert:
        - clockStartDate resets to 2026-08-15.
        - priorElapsedDays = 14 (separate).
        - elapsedDays = 10 (since reset).
        - deadlineDate = 2026-09-04 (2026-08-15 + 20d).
        - remainingDays = 10.
        - status = 'WITHIN'.
        - Explanation never merges priorElapsedDays and elapsedDays.
        """
        claim_date = date(2026, 8, 1)
        deficiency_date = date(2026, 8, 15)
        today = date(2026, 8, 25)
        timeline_days = 20

        res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="CALENDAR",
            today=today,
            deficiency_raised_date=deficiency_date,
            holidays=frozenset(),
        )

        assert res.clockStartDate == date(2026, 8, 15)
        assert res.priorElapsedDays == 14
        assert res.elapsedDays == 10
        assert res.deadlineDate == date(2026, 9, 4)
        assert res.remainingDays == 10
        assert res.status == "WITHIN"

        # Explicit explanation check confirming distinct reporting
        expected_explanation = (
            "Clock restarted on 2026-08-15 due to deficiency (prior elapsed: 14 calendar days). "
            "Deadline is 2026-09-04 (20 calendar days). "
            "As of 2026-08-25, 10 calendar days have elapsed (10 calendar days remaining)."
        )
        assert res.explanation == expected_explanation
        assert "24" not in res.explanation  # Must NOT report combined 14 + 10 = 24

    @pytest.mark.parametrize(
        "today_offset,expected_remaining,expected_status",
        [
            (10, 0, "APPROACHING"),   # Remaining exactly 0 (due date)
            (7, 3, "APPROACHING"),    # Remaining exactly 3
            (11, -1, "OVERDUE"),      # Remaining exactly -1 (1 day overdue)
            (6, 4, "WITHIN"),         # Remaining exactly 4 (within SLA)
        ],
    )
    def test_boundary_cases(self, today_offset: int, expected_remaining: int, expected_status: str):
        """Boundary cases: remaining exactly 0, 3, -1, 4."""
        claim_date = date(2026, 8, 1)
        timeline_days = 10
        today = claim_date + timedelta(days=today_offset)

        res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="CALENDAR",
            today=today,
            deficiency_raised_date=None,
            holidays=frozenset(),
        )

        assert res.remainingDays == expected_remaining
        assert res.status == expected_status

    def test_leap_day_crossing_calendar(self):
        """
        Leap day crossing:
        In 2024 (leap year), Feb has 29 days.
        Claim: 2024-02-20. Timeline: 15 calendar days.
        2024-02-20 + 15 days crosses Feb 29 and reaches 2024-03-06.
        """
        claim_date = date(2024, 2, 20)
        timeline_days = 15
        today = date(2024, 3, 1)

        res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="CALENDAR",
            today=today,
            deficiency_raised_date=None,
            holidays=frozenset(),
        )

        assert res.deadlineDate == date(2024, 3, 6)
        assert res.elapsedDays == 10
        assert res.remainingDays == 5
        assert res.status == "WITHIN"

    def test_leap_day_claim_date_29_feb(self):
        """
        Claim filed on Leap Day (2024-02-29):
        Timeline: 7 calendar days.
        Today: 2024-03-05.
        Hand-computed literal:
        - Deadline: 2024-02-29 + 7d = 2024-03-07.
        - Elapsed: 2024-03-05 - 2024-02-29 = 5 days.
        - Remaining: 2024-03-07 - 2024-03-05 = 2 days.
        - Status: 'APPROACHING' (2 days remaining).
        """
        claim_date = date(2024, 2, 29)
        timeline_days = 7
        today = date(2024, 3, 5)

        res = compute_sla(
            claim_date=claim_date,
            timeline_days=timeline_days,
            basis="CALENDAR",
            today=today,
            deficiency_raised_date=None,
            holidays=frozenset(),
        )

        assert res.deadlineDate == date(2024, 3, 7)
        assert res.elapsedDays == 5
        assert res.remainingDays == 2
        assert res.status == "APPROACHING"


# ─────────────────────────────────────────────────────────────
# 2. Typed Error Hierarchy & Validation Tests
# ─────────────────────────────────────────────────────────────

class TestSlaTypedErrors:
    def test_future_claim_date_raises_typed_error(self):
        """claim_date > today must raise FutureClaimDateError (subclass of ValueError)."""
        with pytest.raises(FutureClaimDateError) as exc_info:
            compute_sla(
                claim_date=date(2026, 9, 20),
                timeline_days=20,
                basis="CALENDAR",
                today=date(2026, 9, 18),
                deficiency_raised_date=None,
                holidays=frozenset(),
            )
        assert issubclass(FutureClaimDateError, (ValueError, SlaError))
        assert "cannot be in the future" in str(exc_info.value)

    def test_deficiency_before_claim_raises_typed_error(self):
        """deficiency_raised_date < claim_date must raise DeficiencyDateOrderError."""
        with pytest.raises(DeficiencyDateOrderError) as exc_info:
            compute_sla(
                claim_date=date(2026, 8, 10),
                timeline_days=20,
                basis="CALENDAR",
                today=date(2026, 9, 1),
                deficiency_raised_date=date(2026, 8, 5),
                holidays=frozenset(),
            )
        assert issubclass(DeficiencyDateOrderError, (ValueError, SlaError))
        assert "cannot be before claim_date" in str(exc_info.value)

    def test_deficiency_in_future_raises_typed_error(self):
        """deficiency_raised_date > today must raise DeficiencyDateOrderError."""
        with pytest.raises(DeficiencyDateOrderError) as exc_info:
            compute_sla(
                claim_date=date(2026, 8, 10),
                timeline_days=20,
                basis="CALENDAR",
                today=date(2026, 8, 20),
                deficiency_raised_date=date(2026, 8, 25),
                holidays=frozenset(),
            )
        assert "cannot be in the future relative to today" in str(exc_info.value)

    def test_invalid_basis_raises_typed_error(self):
        """Invalid basis string raises SlaBasisError."""
        with pytest.raises(SlaBasisError) as exc_info:
            compute_sla(
                claim_date=date(2026, 8, 1),
                timeline_days=20,
                basis="HOURLY",  # type: ignore
                today=date(2026, 8, 15),
                deficiency_raised_date=None,
                holidays=frozenset(),
            )
        assert issubclass(SlaBasisError, (ValueError, SlaError))
        assert "Invalid basis" in str(exc_info.value)

    def test_negative_timeline_days_raises_typed_error(self):
        """Negative timeline_days raises SlaTimelineDaysError."""
        with pytest.raises(SlaTimelineDaysError) as exc_info:
            compute_sla(
                claim_date=date(2026, 8, 1),
                timeline_days=-5,
                basis="CALENDAR",
                today=date(2026, 8, 15),
                deficiency_raised_date=None,
                holidays=frozenset(),
            )
        assert issubclass(SlaTimelineDaysError, (ValueError, SlaError))
        assert "timeline_days cannot be negative" in str(exc_info.value)

    def test_add_working_days_weekend_start_and_invariant(self):
        """Documented add_working_days assertions: weekend start and guaranteed working day return."""
        holidays = frozenset([date(2026, 8, 10)])  # Mon Aug 10 is holiday
        # Start on Saturday 2026-08-08 with days=1:
        # Sat Aug 8 is Day 0. Sun Aug 9 is skipped. Mon Aug 10 is holiday.
        # Target 1st working day is Tuesday Aug 11.
        res_date = add_working_days(date(2026, 8, 8), 1, holidays)
        assert res_date == date(2026, 8, 11)
        assert is_working_day(res_date, holidays) is True


# ─────────────────────────────────────────────────────────────
# 3. Property-Based Testing with Hypothesis (max_examples=1000)
# ─────────────────────────────────────────────────────────────

# Strategies for arbitrary valid inputs
dates_strategy = st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31))
timeline_days_strategy = st.integers(min_value=1, max_value=120)


class TestSlaHypothesisProperties:
    """Property tests running 1,000 random inputs per property."""

    @settings(max_examples=1000)
    @given(start=dates_strategy, timeline=timeline_days_strategy)
    def test_property_calendar_deadline_exact_difference(self, start: date, timeline: int):
        """For CALENDAR basis: deadlineDate - clockStartDate == timeline_days."""
        today = start + timedelta(days=timeline // 2)
        res = compute_sla(
            claim_date=start,
            timeline_days=timeline,
            basis="CALENDAR",
            today=today,
            deficiency_raised_date=None,
            holidays=frozenset(),
        )
        assert (res.deadlineDate - res.clockStartDate).days == timeline

    @settings(max_examples=1000)
    @given(
        start=dates_strategy,
        timeline=timeline_days_strategy,
        basis=st.sampled_from(["CALENDAR", "WORKING"]),
        offset=st.integers(min_value=0, max_value=150),
    )
    def test_property_elapsed_plus_remaining_equals_timeline_days(
        self,
        start: date,
        timeline: int,
        basis: Literal["CALENDAR", "WORKING"],
        offset: int,
    ):
        """Invariant: elapsedDays + remainingDays == timeline_days across both bases."""
        today = start + timedelta(days=offset)
        # Generate arbitrary sparse holidays
        holidays = frozenset([
            start + timedelta(days=5),
            start + timedelta(days=12),
        ])

        res = compute_sla(
            claim_date=start,
            timeline_days=timeline,
            basis=basis,
            today=today,
            deficiency_raised_date=None,
            holidays=holidays,
        )
        assert res.elapsedDays + res.remainingDays == timeline

    @settings(max_examples=1000)
    @given(
        start=dates_strategy,
        timeline=timeline_days_strategy,
        basis=st.sampled_from(["CALENDAR", "WORKING"]),
        offset=st.integers(min_value=0, max_value=150),
    )
    def test_property_status_is_pure_function_of_remaining_days(
        self,
        start: date,
        timeline: int,
        basis: Literal["CALENDAR", "WORKING"],
        offset: int,
    ):
        """Status is a pure function: remaining < 0 -> OVERDUE, 0..3 -> APPROACHING, >3 -> WITHIN."""
        today = start + timedelta(days=offset)
        res = compute_sla(
            claim_date=start,
            timeline_days=timeline,
            basis=basis,
            today=today,
            deficiency_raised_date=None,
            holidays=frozenset(),
        )
        if res.remainingDays < 0:
            assert res.status == "OVERDUE"
        elif 0 <= res.remainingDays <= 3:
            assert res.status == "APPROACHING"
        else:
            assert res.status == "WITHIN"

    @settings(max_examples=1000)
    @given(
        start=dates_strategy,
        timeline=timeline_days_strategy,
        holiday_offset=st.integers(min_value=1, max_value=20),
    )
    def test_property_working_deadline_is_always_working_day(
        self,
        start: date,
        timeline: int,
        holiday_offset: int,
    ):
        """For WORKING basis: deadlineDate is ALWAYS a verified working day."""
        holidays = frozenset([start + timedelta(days=holiday_offset)])
        today = start
        res = compute_sla(
            claim_date=start,
            timeline_days=timeline,
            basis="WORKING",
            today=today,
            deficiency_raised_date=None,
            holidays=holidays,
        )
        assert is_working_day(res.deadlineDate, holidays) is True
        assert res.deadlineDate.weekday() < 5
        assert res.deadlineDate not in holidays

    @settings(max_examples=1000)
    @given(
        start=dates_strategy,
        timeline=timeline_days_strategy,
        basis=st.sampled_from(["CALENDAR", "WORKING"]),
        offset=st.integers(min_value=0, max_value=100),
    )
    def test_property_monotonicity_of_remaining_days(
        self,
        start: date,
        timeline: int,
        basis: Literal["CALENDAR", "WORKING"],
        offset: int,
    ):
        """Monotonicity: As today advances forward by 1 day, remainingDays never increases."""
        today_1 = start + timedelta(days=offset)
        today_2 = today_1 + timedelta(days=1)
        holidays = frozenset([start + timedelta(days=7)])

        res1 = compute_sla(
            claim_date=start,
            timeline_days=timeline,
            basis=basis,
            today=today_1,
            deficiency_raised_date=None,
            holidays=holidays,
        )
        res2 = compute_sla(
            claim_date=start,
            timeline_days=timeline,
            basis=basis,
            today=today_2,
            deficiency_raised_date=None,
            holidays=holidays,
        )
        # remainingDays must decrease or stay same (e.g. over a non-working day under WORKING basis)
        assert res2.remainingDays <= res1.remainingDays
