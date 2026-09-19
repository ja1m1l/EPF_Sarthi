"""
EPF Sentinel Clock Module.

The single authoritative source of time in the application.
All date calculations in EPF Sentinel operate in Indian Standard Time (IST: UTC+05:30).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    """Return the current local date in Asia/Kolkata (IST: UTC+05:30)."""
    return datetime.now(IST).date()


def now_ist() -> datetime:
    """Return the current localized datetime in Asia/Kolkata (IST: UTC+05:30)."""
    return datetime.now(IST)
