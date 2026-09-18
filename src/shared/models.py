"""
Domain models for EPF Sentinel.

Design rules
------------
* Money is stored as **integer paise**, never float.
* All timestamps are stored as ISO-8601 strings with an explicit UTC offset
  ("+00:00").  Use `datetime_to_iso` / `iso_to_datetime` helpers.
* Dataclasses are plain Python – no ORM, no boto3 abstraction.
  Serialisation helpers produce/consume plain dicts that can be passed
  directly to ``boto3``'s DynamoDB client or ``json.dumps``.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


def _coerce_int(value: Any, field_name: str) -> int:
    """
    Coerce an untrusted value to int, strictly enforcing:
      - int -> accepted
      - decimal.Decimal with no fractional part -> accepted and converted to int
      - float (any value, including 100.0) -> raises TypeError
      - Decimal with a fractional part -> raises ValueError
      - bool, str, other types -> raises TypeError
    """
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be int or Decimal without fraction, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value % 1 != 0:
            raise ValueError(f"{field_name} Decimal cannot have a fractional part, got {value}")
        return int(value)
    if isinstance(value, float):
        raise TypeError(f"{field_name} must be int or Decimal without fraction, got float")
    raise TypeError(
        f"{field_name} must be int or Decimal without fraction, got {type(value).__name__}"
    )


# ─────────────────────────────────────────────────────────────
# Timestamp helpers
# ─────────────────────────────────────────────────────────────

def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with '+00:00' offset."""
    return datetime.now(tz=timezone.utc).isoformat()


def datetime_to_iso(dt: datetime) -> str:
    """
    Convert *dt* to an ISO-8601 string with an explicit '+00:00' offset.

    Raises
    ------
    ValueError
        If *dt* is naïve (no ``tzinfo``).
    """
    if dt.tzinfo is None:
        raise ValueError(
            f"datetime must be timezone-aware, got naïve: {dt!r}"
        )
    return dt.astimezone(timezone.utc).isoformat()


def iso_to_datetime(iso: str) -> datetime:
    """
    Parse an ISO-8601 string into a timezone-aware UTC :class:`datetime`.

    Raises
    ------
    ValueError
        If the string is not a valid ISO-8601 timestamp or has no UTC offset.
    """
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        raise ValueError(
            f"ISO string has no timezone offset, cannot determine UTC: {iso!r}"
        )
    return dt.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────

class ClaimType(str, Enum):
    """Supported claim types.  Only FINAL_SETTLEMENT is in scope for v1."""

    FINAL_SETTLEMENT = "FINAL_SETTLEMENT"


class ClaimStatus(str, Enum):
    """Lifecycle states of a claim as tracked by EPF Sentinel."""

    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    UNDER_PROCESS = "UNDER_PROCESS"
    REJECTED = "REJECTED"
    SETTLED = "SETTLED"


# ─────────────────────────────────────────────────────────────
# Claim
# ─────────────────────────────────────────────────────────────

@dataclass
class Claim:
    """
    Represents a single EPF claim tracked by the system.

    DynamoDB key
    ------------
    PK  userId   (S)
    SK  claimId  (S)

    GSI "byStatus"
    --------------
    PK  userId   (S)
    SK  status   (S)

    Money
    -----
    ``amountPaise`` is always an integer representing the amount in paise
    (1 INR = 100 paise).  It is **never** stored or transmitted as a float.
    """

    # Primary key fields
    userId: str
    claimId: str

    # Claim metadata
    claimType: ClaimType
    claimDateIso: str          # ISO-8601, the date the claim was filed with EPFO
    amountPaise: int           # Money in paise, must be >= 0

    # Lifecycle
    status: ClaimStatus

    # Timestamps (ISO-8601 with explicit +00:00)
    createdAt: str
    updatedAt: str

    # Optional fields
    deficiencyRaisedDateIso: Optional[str] = None

    # ── factory ──────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        *,
        userId: str,
        claimType: ClaimType,
        claimDateIso: str,
        amountPaise: int,
        status: ClaimStatus = ClaimStatus.SUBMITTED,
        deficiencyRaisedDateIso: Optional[str] = None,
    ) -> "Claim":
        """Create a new :class:`Claim` with a generated ``claimId`` and timestamps."""
        if isinstance(amountPaise, bool) or not isinstance(amountPaise, int):
            raise TypeError(
                f"amountPaise must be int (paise), got {type(amountPaise).__name__}"
            )
        if amountPaise < 0:
            raise ValueError(f"amountPaise must be >= 0, got {amountPaise}")

        now = utc_now_iso()
        return cls(
            userId=userId,
            claimId=str(uuid.uuid4()),
            claimType=claimType,
            claimDateIso=claimDateIso,
            amountPaise=amountPaise,
            status=status,
            deficiencyRaisedDateIso=deficiencyRaisedDateIso,
            createdAt=now,
            updatedAt=now,
        )

    # ── serialisation ─────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Serialise to a plain dict suitable for DynamoDB PutItem or JSON
        serialisation.  Enum values are stored as their string ``value``.
        """
        d = asdict(self)
        d["claimType"] = self.claimType.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        """
        Deserialise a plain dict (e.g. from DynamoDB GetItem) into a
        :class:`Claim`.
        """
        amount_paise = _coerce_int(d["amountPaise"], "amountPaise")
        if amount_paise < 0:
            raise ValueError(f"amountPaise must be >= 0, got {amount_paise}")
        return cls(
            userId=d["userId"],
            claimId=d["claimId"],
            claimType=ClaimType(d["claimType"]),
            claimDateIso=d["claimDateIso"],
            amountPaise=amount_paise,
            status=ClaimStatus(d["status"]),
            createdAt=d["createdAt"],
            updatedAt=d["updatedAt"],
            deficiencyRaisedDateIso=d.get("deficiencyRaisedDateIso"),
        )


# ─────────────────────────────────────────────────────────────
# RuleChunk
# ─────────────────────────────────────────────────────────────

@dataclass
class RuleChunk:
    """
    A single retrievable chunk of EPFO regulation text, stored in
    DynamoDB and associated with a pre-computed embedding vector.

    DynamoDB key
    ------------
    PK  ruleSetVersion  (S)
    SK  chunkId         (S)
    """

    # Primary key fields
    ruleSetVersion: str        # e.g. "2024-10-01"
    chunkId: str               # deterministic hash or sequential id

    # Content
    text: str                  # raw regulation text for this chunk
    sourceRef: str             # human-readable citation (doc name + section)

    # Embedding (stored as a list of floats from gemini-embedding-001)
    embedding: list[float]

    # Metadata
    createdAt: str             # ISO-8601 UTC

    # ── factory ──────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        *,
        ruleSetVersion: str,
        chunkId: str,
        text: str,
        sourceRef: str,
        embedding: list[float],
    ) -> "RuleChunk":
        return cls(
            ruleSetVersion=ruleSetVersion,
            chunkId=chunkId,
            text=text,
            sourceRef=sourceRef,
            embedding=embedding,
            createdAt=utc_now_iso(),
        )

    # ── serialisation ─────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RuleChunk":
        return cls(
            ruleSetVersion=d["ruleSetVersion"],
            chunkId=d["chunkId"],
            text=d["text"],
            sourceRef=d["sourceRef"],
            embedding=[float(v) for v in d["embedding"]],
            createdAt=d["createdAt"],
        )


# ─────────────────────────────────────────────────────────────
# AnalysisRun
# ─────────────────────────────────────────────────────────────

@dataclass
class AnalysisRun:
    """
    Records a single execution of the analysis Step Function workflow
    for a given claim.

    DynamoDB key
    ------------
    PK  claimId  (S)
    SK  runId    (S)

    TTL
    ---
    ``expiresAt`` is a Unix epoch integer (seconds).  DynamoDB's TTL
    daemon will delete the item after this time.  Callers should set
    this to ``now + 90 days`` (or per their data-retention policy).
    """

    # Primary key fields
    claimId: str
    runId: str

    # Workflow state
    status: str                # e.g. "RUNNING", "SUCCEEDED", "FAILED"
    stepFunctionExecutionArn: Optional[str]

    # Result payload (stored as a JSON string to avoid DynamoDB type friction)
    resultJson: Optional[str]

    # Timestamps (ISO-8601 with explicit +00:00)
    startedAt: str
    finishedAt: Optional[str]

    # TTL: Unix epoch seconds (int, not float)
    expiresAt: int

    # ── factory ──────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        *,
        claimId: str,
        expiresAt: int,
        stepFunctionExecutionArn: Optional[str] = None,
    ) -> "AnalysisRun":
        if isinstance(expiresAt, bool) or not isinstance(expiresAt, int):
            raise TypeError(
                f"expiresAt must be int (epoch seconds), got {type(expiresAt).__name__}"
            )
        return cls(
            claimId=claimId,
            runId=str(uuid.uuid4()),
            status="RUNNING",
            stepFunctionExecutionArn=stepFunctionExecutionArn,
            resultJson=None,
            startedAt=utc_now_iso(),
            finishedAt=None,
            expiresAt=expiresAt,
        )

    # ── serialisation ─────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisRun":
        return cls(
            claimId=d["claimId"],
            runId=d["runId"],
            status=d["status"],
            stepFunctionExecutionArn=d.get("stepFunctionExecutionArn"),
            resultJson=d.get("resultJson"),
            startedAt=d["startedAt"],
            finishedAt=d.get("finishedAt"),
            expiresAt=_coerce_int(d["expiresAt"], "expiresAt"),
        )

