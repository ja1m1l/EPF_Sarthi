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


class AnalysisRunStatus(str, Enum):
    """
    Terminal status codes for an AnalysisRun.

    Rules
    -----
    * These four values MUST NEVER be collapsed into fewer statuses.
    * No metric, dashboard, or downstream consumer may aggregate
      COMPLETED_WITH_ABSTENTION together with any FAILED_* status.
    """

    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ABSTENTION = "COMPLETED_WITH_ABSTENTION"
    FAILED_INFRASTRUCTURE = "FAILED_INFRASTRUCTURE"
    FAILED_VALIDATION = "FAILED_VALIDATION"


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
        raw_type = d["claimType"]
        try:
            c_type = ClaimType(raw_type)
        except (ValueError, KeyError):
            c_type = raw_type

        raw_status = d["status"]
        try:
            c_status = ClaimStatus(raw_status)
        except (ValueError, KeyError):
            c_status = raw_status

        return cls(
            userId=d["userId"],
            claimId=d["claimId"],
            claimType=c_type,
            claimDateIso=d["claimDateIso"],
            amountPaise=amount_paise,
            status=c_status,
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
    SK  chunkId         (S)  — sha256(sourceUrl + headingPath + text)[:16]

    No chunk may exist without a ``sourceUrl``.
    """

    # Primary key fields
    ruleSetVersion: str        # e.g. "2024-10-01"
    chunkId: str               # sha256(sourceUrl + headingPath + text)[:16]

    # Content
    text: str                  # raw regulation text for this chunk

    # Embedding vector
    embedding: list[float]     # from the embedding model

    # Embedding provenance (recorded per-chunk because changing the
    # embedding model invalidates every vector for that version)
    embeddingModel: str        # e.g. "gemini-embedding-001"
    embeddingDim: int          # e.g. 3072

    # Source provenance — REQUIRED; no chunk without a sourceUrl
    sourceUrl: str
    sourceTitle: str
    retrievedOn: str           # ISO date string from frontmatter
    authority: str             # "EPFO_OFFICIAL" | "SCHEME_TEXT"
    headingPath: str           # e.g. "## Timelines > ### Final Settlement"
    tokenCount: int            # approximate word-split token count

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
        embedding: list[float],
        embeddingModel: str,
        embeddingDim: int,
        sourceUrl: str,
        sourceTitle: str,
        retrievedOn: str,
        authority: str,
        headingPath: str,
        tokenCount: int,
    ) -> "RuleChunk":
        if not sourceUrl:
            raise ValueError("sourceUrl is required — no chunk may exist without one")
        return cls(
            ruleSetVersion=ruleSetVersion,
            chunkId=chunkId,
            text=text,
            embedding=embedding,
            embeddingModel=embeddingModel,
            embeddingDim=embeddingDim,
            sourceUrl=sourceUrl,
            sourceTitle=sourceTitle,
            retrievedOn=retrievedOn,
            authority=authority,
            headingPath=headingPath,
            tokenCount=tokenCount,
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
            embedding=[float(v) for v in d["embedding"]],
            embeddingModel=d["embeddingModel"],
            embeddingDim=_coerce_int(d["embeddingDim"], "embeddingDim"),
            sourceUrl=d["sourceUrl"],
            sourceTitle=d["sourceTitle"],
            retrievedOn=d["retrievedOn"],
            authority=d["authority"],
            headingPath=d["headingPath"],
            tokenCount=_coerce_int(d["tokenCount"], "tokenCount"),
            createdAt=d["createdAt"],
        )


# ─────────────────────────────────────────────────────────────
# AnalysisInput
# ─────────────────────────────────────────────────────────────

@dataclass
class AnalysisInput:
    """
    Normalised view of a Claim produced by ClaimAgent.

    Passed between Step Function states.  All money in paise (int).
    """

    claimId: str
    userId: str
    claimType: str          # string value of ClaimType enum
    claimDateIso: str
    amountPaise: int
    status: str             # string value of ClaimStatus enum
    deficiencyRaisedDateIso: Optional[str]
    correlationId: str
    normalizedAt: str       # ISO-8601 UTC

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisInput":
        return cls(
            claimId=d["claimId"],
            userId=d["userId"],
            claimType=d["claimType"],
            claimDateIso=d["claimDateIso"],
            amountPaise=_coerce_int(d["amountPaise"], "amountPaise"),
            status=d["status"],
            deficiencyRaisedDateIso=d.get("deficiencyRaisedDateIso"),
            correlationId=d["correlationId"],
            normalizedAt=d["normalizedAt"],
        )


# ─────────────────────────────────────────────────────────────
# EvidenceReport
# ─────────────────────────────────────────────────────────────

@dataclass
class EvidenceReport:
    """
    Structured output of EvidenceAgent.

    In stub mode this is a fixed canned object.  In production it will
    contain retrieved documents and relevance scores.
    """

    documents: list[dict]   # list of {title, url, relevanceScore}
    summary: str
    stubbed: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceReport":
        return cls(
            documents=list(d.get("documents", [])),
            summary=str(d.get("summary", "")),
            stubbed=bool(d.get("stubbed", True)),
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

    # Correlation ID — generated once at entry, threaded through all states
    correlationId: str

    # Workflow state — must be one of the four AnalysisRunStatus values
    # (stored as str for backwards compatibility with DynamoDB)
    status: str
    stepFunctionExecutionArn: Optional[str]

    # Per-agent outputs (stored as JSON strings to avoid DynamoDB type friction)
    claimAgentOutputJson: Optional[str] = None
    rulesAgentOutputJson: Optional[str] = None
    slaAgentOutputJson: Optional[str] = None
    evidenceAgentOutputJson: Optional[str] = None
    grievanceAgentOutputJson: Optional[str] = None

    # Failure detail (populated by RecordFailure state)
    failureDetail: Optional[str] = None

    # Legacy full-result blob (kept for backwards compat; populated by PersistRun
    # as a merged snapshot of all agent outputs)
    resultJson: Optional[str] = None

    # Timestamps (ISO-8601 with explicit +00:00)
    startedAt: str = field(default_factory=utc_now_iso)
    finishedAt: Optional[str] = None

    # TTL: Unix epoch seconds (int, not float)
    expiresAt: int = 0

    # ── factory ──────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        *,
        claimId: str,
        correlationId: str,
        expiresAt: int,
        stepFunctionExecutionArn: Optional[str] = None,
    ) -> "AnalysisRun":
        if isinstance(expiresAt, bool) or not isinstance(expiresAt, int):
            raise TypeError(
                f"expiresAt must be int (epoch seconds), got {type(expiresAt).__name__}"
            )
        now = utc_now_iso()
        return cls(
            claimId=claimId,
            runId=str(uuid.uuid4()),
            correlationId=correlationId,
            status="RUNNING",
            stepFunctionExecutionArn=stepFunctionExecutionArn,
            startedAt=now,
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
            correlationId=d.get("correlationId", ""),
            status=d["status"],
            stepFunctionExecutionArn=d.get("stepFunctionExecutionArn"),
            claimAgentOutputJson=d.get("claimAgentOutputJson"),
            rulesAgentOutputJson=d.get("rulesAgentOutputJson"),
            slaAgentOutputJson=d.get("slaAgentOutputJson"),
            evidenceAgentOutputJson=d.get("evidenceAgentOutputJson"),
            grievanceAgentOutputJson=d.get("grievanceAgentOutputJson"),
            failureDetail=d.get("failureDetail"),
            resultJson=d.get("resultJson"),
            startedAt=d.get("startedAt", utc_now_iso()),
            finishedAt=d.get("finishedAt"),
            expiresAt=_coerce_int(d["expiresAt"], "expiresAt"),
        )


# ─────────────────────────────────────────────────────────────
# RuleDecision
# ─────────────────────────────────────────────────────────────

@dataclass
class RuleDecision:
    """
    Structured outcome of the Rules Agent (select_rule).

    Attributes
    ----------
    applicable:
        True if an applicable EPFO rule and timeline was found, False if abstaining.
    timelineDays:
        Integer number of days for the claim timeline, or None if abstained.
    timelineBasis:
        "CALENDAR" | "WORKING", or None if abstained.
    citedChunkIds:
        List of RuleChunk chunkIds from which the decision was drawn.
    citedSourceUrls:
        List of source URLs corresponding to the cited chunks.
    quotedSpan:
        Verbatim excerpt from one of the cited chunks supporting the decision.
    confidence:
        "HIGH" | "MEDIUM" | "LOW".
    abstainReason:
        Reason string if applicable is False (e.g. "UNGROUNDED_QUOTE",
        "TIMELINE_NOT_IN_SOURCE", "FABRICATED_CITATION", "MODEL_OUTPUT_INVALID",
        "NO_APPLICABLE_RULE", "INVALID_CLAIM_DATA", "CONFLICTING_TIMELINE_SOURCES"),
        or None if applicable is True.
    """

    applicable: bool
    timelineDays: Optional[int]
    timelineBasis: Optional[str]
    charterTargetDays: Optional[int] = None
    citedChunkIds: list[str] = field(default_factory=list)
    citedSourceUrls: list[str] = field(default_factory=list)
    quotedSpan: str = ""
    confidence: str = "HIGH"
    abstainReason: Optional[str] = None

    @classmethod
    def abstain(
        cls,
        reason: str,
        *,
        confidence: str = "HIGH",
    ) -> "RuleDecision":
        """Factory for an abstained decision. Never carries citations or quotes."""
        return cls(
            applicable=False,
            timelineDays=None,
            timelineBasis=None,
            charterTargetDays=None,
            citedChunkIds=[],
            citedSourceUrls=[],
            quotedSpan="",
            confidence=confidence,
            abstainReason=reason,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RuleDecision":
        raw_days = d.get("timelineDays")
        timeline_days = int(raw_days) if raw_days is not None else None
        applicable = bool(d.get("applicable", False))

        raw_charter = d.get("charterTargetDays")
        charter_days = int(raw_charter) if (applicable and raw_charter is not None) else None

        abstain_reason = d.get("abstainReason")
        if not abstain_reason or str(abstain_reason).lower() in ("", "null", "none"):
            abstain_reason = None
        if applicable:
            abstain_reason = None

        cited_chunk_ids = list(d.get("citedChunkIds", [])) if applicable else []
        cited_source_urls = list(d.get("citedSourceUrls", [])) if applicable else []
        quoted_span = str(d.get("quotedSpan", "")) if applicable else ""

        return cls(
            applicable=applicable,
            timelineDays=timeline_days if applicable else None,
            timelineBasis=d.get("timelineBasis") if applicable else None,
            charterTargetDays=charter_days,
            citedChunkIds=cited_chunk_ids,
            citedSourceUrls=cited_source_urls,
            quotedSpan=quoted_span,
            confidence=str(d.get("confidence", "LOW")),
            abstainReason=abstain_reason,
        )

