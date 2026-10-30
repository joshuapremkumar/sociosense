"""Domain model for SocioSense.

Design rule: every fact that reaches the user carries provenance.  A venue
attribute is never a bare string; it is a ``Claim`` with a source and a method,
so the planner can tell "the venue declares a low-sodium menu" apart from
"a language model guessed it".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


class Domain(str, Enum):
    """Qloo entity types used as taste seeds."""

    ARTIST = "urn:entity:artist"
    BOOK = "urn:entity:book"
    BRAND = "urn:entity:brand"
    MOVIE = "urn:entity:movie"
    PERSON = "urn:entity:person"
    PLACE = "urn:entity:place"
    PODCAST = "urn:entity:podcast"
    TV_SHOW = "urn:entity:tv_show"
    VIDEOGAME = "urn:entity:videogame"

    @classmethod
    def from_text(cls, value: str) -> "Domain":
        """Accept ``movie``, ``urn:entity:movie`` or ``tv_show``/``tv``."""
        text = value.strip().lower()
        aliases = {"tv": "tv_show", "series": "tv_show", "music": "artist", "band": "artist"}
        text = aliases.get(text, text)
        if text.startswith("urn:entity:"):
            return cls(text)
        return cls(f"urn:entity:{text}")


class ClaimMethod(str, Enum):
    """How an attribute value was obtained, weakest to strongest."""

    INFERRED = "inferred"          # LLM / heuristic guess - never sufficient for safety
    CROWDSOURCED = "crowdsourced"  # review text, community database
    DECLARED = "declared"          # venue website / menu / staff statement
    MEASURED = "measured"          # instrumented (e.g. calibrated sound meter)


#: Methods strong enough to satisfy a hard accessibility/dietary constraint.
TRUSTED_METHODS = frozenset({ClaimMethod.DECLARED, ClaimMethod.MEASURED})


@dataclass(frozen=True)
class Claim:
    """A single attribute assertion with provenance."""

    value: Any
    source: str
    method: ClaimMethod
    confidence: float = 1.0
    retrieved_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("a claim must name its source")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    @property
    def trusted(self) -> bool:
        """True when the claim is strong enough to gate a hard constraint."""
        return self.method in TRUSTED_METHODS and self.confidence >= 0.6


@dataclass(frozen=True)
class TasteSeed:
    """One cultural preference, owned by exactly one family member.

    ``kind`` is ``entity`` for a concrete work/person/brand (resolved through
    /v2/search) or ``tag`` for a genre/cuisine/mood (resolved through /v2/tags).
    """

    query: str
    domain: Domain
    member_id: str
    kind: str = "entity"
    resolved_id: str | None = None
    resolved_name: str | None = None
    resolved_domain: Domain | None = None
    match_score: float | None = None


@dataclass(frozen=True)
class Member:
    member_id: str
    label: str
    seeds: tuple[TasteSeed, ...]
    age_band: str | None = None


@dataclass(frozen=True)
class Requirements:
    """Hard and soft rules for the outing."""

    max_noise: str | None = None          # low | moderate
    texture: str | None = None            # soft
    sodium: str | None = None             # low
    step_free: bool = False
    require_verification: bool = True
    max_price_level: int | None = 3
    radius_m: int | None = None
    cuisine: str | None = None            # e.g. "mediterranean" -> genre tag filter


@dataclass(frozen=True)
class VenueFacts:
    """Attribute claims for one venue, from an external verification source."""

    venue_id: str
    claims: dict[str, Claim] = field(default_factory=dict)
    provider: str = "none"

    def get(self, attribute: str) -> Claim | None:
        return self.claims.get(attribute)


@dataclass(frozen=True)
class Venue:
    """A place returned by Qloo, with its identity fields only."""

    entity_id: str
    name: str
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    rating: float | None = None
    price_level: int | None = None
    tags: tuple[str, ...] = ()
    source_url: str | None = None
    cuisine: str | None = None
    description: str | None = None
    menu_url: str | None = None
    good_for: tuple[str, ...] = ()


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"   # no trustworthy evidence - never reported as a pass


@dataclass(frozen=True)
class CheckResult:
    constraint: str
    status: CheckStatus
    detail: str
    evidence: Claim | None = None


@dataclass
class Candidate:
    venue: Venue
    affinities: dict[str, float] = field(default_factory=dict)       # member_id -> 0..1
    affinity_evidence: dict[str, str] = field(default_factory=dict)  # "observed" | "lower_bound"
    explainability: dict[str, dict[str, float]] = field(default_factory=dict)
    facts: VenueFacts | None = None
    checks: list[CheckResult] = field(default_factory=list)
    bridge_score: float = 0.0
    quality_score: float = 0.0
    verification_coverage: float = 0.0
    final_score: float = 0.0

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.FAIL]

    @property
    def unknowns(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.UNKNOWN]


class RecommendationStatus(str, Enum):
    READY = "ready"                          # every hard constraint passed with evidence
    NEEDS_CONFIRMATION = "needs_confirmation"  # at least one hard constraint unverified
    EXCLUDED = "excluded"                    # a hard constraint was violated
    UNCOVERED_MEMBER = "uncovered_member"    # a family member had no resolvable seeds


@dataclass
class Recommendation:
    candidate: Candidate
    status: RecommendationStatus
    reasons: list[str]
    caveats: list[str]
    provenance: str | None = None

    @property
    def venue(self) -> Venue:
        return self.candidate.venue


@dataclass
class ResolvedSeed:
    seed: TasteSeed
    matches: int
    error: str | None = None


@dataclass
class TraceEvent:
    step: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class PlanResult:
    request_label: str
    location: str
    recommendations: list[Recommendation]
    rejected: list[Recommendation]
    unresolved_seeds: list[ResolvedSeed]
    uncovered_members: list[str]
    trace: list[TraceEvent]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data_mode: str = "live"   # "live" | "fixture"

    def all_venues(self) -> Iterable[Venue]:
        for rec in self.recommendations:
            yield rec.venue
        for rec in self.rejected:
            yield rec.venue
