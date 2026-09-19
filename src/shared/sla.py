"""
EPF Sentinel SLA Engine.

Pure Python module implementing deterministic Service Level Agreement (SLA)
computations for EPFO member claims under both statutory (Scheme) and
aspirational (Citizens' Charter) timelines.

CONSTRAINTS & PURITY RULES:
- Pure computational module: ZERO network calls, ZERO LLMs, ZERO boto3.
- Clock purity: This module NEVER calls datetime.now() or datetime.today().
  All dates are interpreted in Indian Standard Time (Asia/Kolkata: UTC+05:30).
  Callers MUST obtain the current local date via `shared.clock.today_ist()`
  and pass it explicitly as the `today` argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Optional

# ─────────────────────────────────────────────────────────────
# Typed Exceptions
# ─────────────────────────────────────────────────────────────

class SlaError(ValueError):
    """Base exception for SLA calculation errors."""
    pass


class FutureClaimDateError(SlaError):
    """Raised when claim_date is strictly after today (future claim date)."""
    pass


class DeficiencyDateOrderError(SlaError):
    """Raised when deficiency_raised_date is before claim_date or after today."""
    pass


class SlaBasisError(SlaError):
    """Raised when basis is not 'CALENDAR' or 'WORKING'."""
    pass


class SlaTimelineDaysError(SlaError):
    """Raised when timeline_days is negative."""
    pass


# ─────────────────────────────────────────────────────────────
# Result Dataclass
# ─────────────────────────────────────────────────────────────

SlaStatus = Literal["WITHIN", "APPROACHING", "OVERDUE"]


@dataclass(frozen=True)
class SlaResult:
    """
    Result of SLA computation for an EPFO claim.

    Attributes:
    -----------
    deadlineDate:
        The computed deadline date for the claim.
    elapsedDays:
        Days elapsed since the clock started (or restarted after deficiency).
        Counted in calendar days for CALENDAR basis, working days for WORKING basis.
    remainingDays:
        Days remaining until the deadlineDate. Negative if the deadline has passed.
        Counted in calendar days for CALENDAR basis, working days for WORKING basis.
    clockStartDate:
        The anchor date where the active SLA clock began.
        Equal to deficiency_raised_date if a deficiency was raised, else claim_date.
    status:
        "WITHIN" (remainingDays > 3),
        "APPROACHING" (0 <= remainingDays <= 3),
        "OVERDUE" (remainingDays < 0).
    explanation:
        Plain-English summary naming the exact dates, basis, and arithmetic used.
    priorElapsedDays:
        Days elapsed before a deficiency was raised (if applicable).
        Never combined into elapsedDays; reported strictly as a separate figure.
    """
    deadlineDate: date
    elapsedDays: int
    remainingDays: int
    clockStartDate: date
    status: SlaStatus
    explanation: str
    priorElapsedDays: Optional[int] = None


# ─────────────────────────────────────────────────────────────
# Working Day Arithmetic Helpers
# ─────────────────────────────────────────────────────────────

def is_working_day(d: date, holidays: frozenset[date]) -> bool:
    """
    Return True if `d` is a working day.

    Working days exclude:
    - Saturdays (d.weekday() == 5)
    - Sundays (d.weekday() == 6)
    - Gazetted / EPFO holidays in `holidays`
    """
    return d.weekday() < 5 and d not in holidays


def add_working_days(start: date, days: int, holidays: frozenset[date]) -> date:
    """
    Advance forward from `start` by `days` working days.

    Precise Semantics:
    ------------------
    1. Start Day Counting:
       The start date `start` is Day 0 (the baseline anchor) and does NOT count as
       one of the elapsed working days. Counting begins on the next day.
       For example, starting on Monday with days=1 yields Tuesday.

    2. Behaviour When Start is a Weekend or Holiday:
       If `start` falls on a Saturday, Sunday, or holiday, it is still treated as Day 0.
       The first working day counted is the very first working day strictly following `start`.
       For example, starting on a Saturday with days=1 yields Monday (or Tuesday if Monday is a holiday).

    3. Return Invariant:
       The returned date is ALWAYS a verified working day (never a Saturday, Sunday, or holiday).
    """
    if days < 0:
        raise SlaTimelineDaysError(f"Working days to add cannot be negative: {days}")

    if days == 0:
        # 0 working days: return start if working day, else advance to next working day
        current = start
        while not is_working_day(current, holidays):
            current += timedelta(days=1)
        assert is_working_day(current, holidays), f"Invariant violated: {current} must be a working day"
        return current

    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if is_working_day(current, holidays):
            added += 1

    assert is_working_day(current, holidays), f"Invariant violated: {current} must be a working day"
    return current


def count_working_days(start: date, end: date, holidays: frozenset[date]) -> int:
    """
    Count working days strictly after `start` up to and including `end` (half-open interval `(start, end]`).

    - If start == end: returns 0.
    - If start < end: counts every working day in `(start, end]`.
    - If start > end: returns negative count of working days in `(end, start]`.
    """
    if start == end:
        return 0

    if start < end:
        count = 0
        cur = start + timedelta(days=1)
        while cur <= end:
            if is_working_day(cur, holidays):
                count += 1
            cur += timedelta(days=1)
        return count
    else:
        # start > end
        count = 0
        cur = end + timedelta(days=1)
        while cur <= start:
            if is_working_day(cur, holidays):
                count += 1
            cur += timedelta(days=1)
        return -count


# ─────────────────────────────────────────────────────────────
# Main Public API: compute_sla()
# ─────────────────────────────────────────────────────────────

def compute_sla(
    claim_date: date,
    timeline_days: int,
    basis: Literal["CALENDAR", "WORKING"],
    today: date,
    deficiency_raised_date: Optional[date],
    holidays: frozenset[date],
) -> SlaResult:
    """
    Compute SLA metrics, deadlines, and status for an EPFO claim.

    Parameters:
    -----------
    claim_date:
        The initial filing date of the claim (IST).
    timeline_days:
        The SLA duration in days.
    basis:
        "CALENDAR" or "WORKING".
    today:
        Current evaluation date (IST), passed by caller (e.g. from clock.today_ist()).
    deficiency_raised_date:
        Optional date when a deficiency was raised, resetting the active clock.
    holidays:
        Required frozenset of holiday dates to exclude under WORKING basis.

    Returns:
    --------
    SlaResult
        Complete calculation with deadline, elapsed/remaining days, status, and explanation.

    Raises:
    -------
    FutureClaimDateError:
        If claim_date > today.
    DeficiencyDateOrderError:
        If deficiency_raised_date < claim_date or deficiency_raised_date > today.
    SlaBasisError:
        If basis not in ("CALENDAR", "WORKING").
    SlaTimelineDaysError:
        If timeline_days < 0.
    """
    # 1. Validation
    if basis not in ("CALENDAR", "WORKING"):
        raise SlaBasisError(f"Invalid basis '{basis}': must be 'CALENDAR' or 'WORKING'")

    if timeline_days < 0:
        raise SlaTimelineDaysError(f"timeline_days cannot be negative: {timeline_days}")

    if claim_date > today:
        raise FutureClaimDateError(
            f"claim_date ({claim_date.isoformat()}) cannot be in the future relative to today ({today.isoformat()})"
        )

    if deficiency_raised_date is not None:
        if deficiency_raised_date < claim_date:
            raise DeficiencyDateOrderError(
                f"deficiency_raised_date ({deficiency_raised_date.isoformat()}) cannot be before claim_date ({claim_date.isoformat()})"
            )
        if deficiency_raised_date > today:
            raise DeficiencyDateOrderError(
                f"deficiency_raised_date ({deficiency_raised_date.isoformat()}) cannot be in the future relative to today ({today.isoformat()})"
            )

    # 2. Clock Start & Deficiency Tracking
    if deficiency_raised_date is not None:
        clock_start = deficiency_raised_date
        if basis == "CALENDAR":
            prior_elapsed: Optional[int] = (deficiency_raised_date - claim_date).days
        else:
            prior_elapsed = count_working_days(claim_date, deficiency_raised_date, holidays)
    else:
        clock_start = claim_date
        prior_elapsed = None

    # 3. Deadline Calculation
    if basis == "CALENDAR":
        deadline = clock_start + timedelta(days=timeline_days)
    else:
        deadline = add_working_days(clock_start, timeline_days, holidays)

    # 4. Elapsed & Remaining Days
    if basis == "CALENDAR":
        elapsed = (today - clock_start).days
        remaining = (deadline - today).days
    else:
        elapsed = count_working_days(clock_start, today, holidays)
        remaining = count_working_days(today, deadline, holidays)

    # 5. Status Determination
    if remaining < 0:
        status: SlaStatus = "OVERDUE"
    elif remaining <= 3:
        status = "APPROACHING"
    else:
        status = "WITHIN"

    # 6. Plain-English Explanation String
    basis_label = "calendar days" if basis == "CALENDAR" else "working days"

    if deficiency_raised_date is not None:
        prefix = (
            f"Clock restarted on {clock_start.isoformat()} due to deficiency "
            f"(prior elapsed: {prior_elapsed} {basis_label}). "
        )
    else:
        prefix = f"Clock started on {clock_start.isoformat()}. "

    if remaining < 0:
        relative_phrase = f"Deadline was {deadline.isoformat()} ({timeline_days} {basis_label}). "
        outcome_phrase = f"As of {today.isoformat()}, {elapsed} {basis_label} have elapsed ({abs(remaining)} {basis_label} overdue)."
    elif remaining == 0:
        relative_phrase = f"Deadline is today {deadline.isoformat()} ({timeline_days} {basis_label}). "
        outcome_phrase = f"As of {today.isoformat()}, {elapsed} {basis_label} have elapsed (0 {basis_label} remaining)."
    else:
        relative_phrase = f"Deadline is {deadline.isoformat()} ({timeline_days} {basis_label}). "
        outcome_phrase = f"As of {today.isoformat()}, {elapsed} {basis_label} have elapsed ({remaining} {basis_label} remaining)."

    explanation = prefix + relative_phrase + outcome_phrase

    return SlaResult(
        deadlineDate=deadline,
        elapsedDays=elapsed,
        remainingDays=remaining,
        clockStartDate=clock_start,
        status=status,
        explanation=explanation,
        priorElapsedDays=prior_elapsed,
    )
