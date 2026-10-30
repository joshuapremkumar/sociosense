"""Scoring.

Two ideas make this different from "take the highest affinity hit":

1. **Fairness weighting.**  A family outing fails if one person hates it.  The
   bridge score blends the mean affinity with the *worst* member's affinity.
2. **Evidence-aware ranking.**  Affinity alone is not a reason to recommend a
   restaurant to someone with a dietary restriction, so verified-constraint
   coverage is part of the score.
"""

from __future__ import annotations

from .config import RankingWeights
from .models import Candidate, CheckStatus


def bridge_score(affinities: dict[str, float], members: list[str], fairness: float) -> float:
    """Blend mean and minimum affinity across the members in scope."""
    if not members:
        return 0.0
    # A member in scope with no observed affinity counts as zero rather than
    # being dropped from the average; otherwise one failed lookup would make a
    # venue look like a better family match than it is.
    values = [affinities.get(m, 0.0) for m in members]
    mean = sum(values) / len(values)
    worst = min(values)
    return _clamp01((1.0 - fairness) * mean + fairness * worst)


def quality_score(rating: float | None) -> float:
    """Map a 0-5 business rating onto 0..1, centred on 3.5."""
    if rating is None:
        return 0.5  # neutral, not a penalty for missing metadata
    return _clamp01((rating - 3.0) / 2.0)


def verification_score(checks) -> float:
    if not checks:
        return 1.0  # nothing was required
    passed = sum(1 for check in checks if check.status is CheckStatus.PASS)
    return passed / len(checks)


def final_score(candidate: Candidate, weights: RankingWeights) -> float:
    return _clamp01(
        weights.bridge * candidate.bridge_score
        + weights.quality * candidate.quality_score
        + weights.verification * candidate.verification_coverage
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
