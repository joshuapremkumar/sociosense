"""Deterministic constraint evaluation.

The critical behaviour: a constraint that cannot be verified is reported as
``UNKNOWN`` and the venue is surfaced as "needs confirmation".  It is never
reported as satisfied, and an LLM-inferred attribute can never satisfy a hard
safety rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Candidate, CheckResult, CheckStatus, Claim, Requirements

_LEVELS = {"low": 0, "moderate": 1, "high": 2}


@dataclass(frozen=True)
class Constraint:
    attribute: str
    label: str
    allowed: tuple[str, ...] | None = None
    expects_true: bool = False

    def evaluate(self, candidate: Candidate) -> CheckResult:
        claim = candidate.facts.get(self.attribute) if candidate.facts else None
        if claim is None:
            return CheckResult(
                constraint=self.label,
                status=CheckStatus.UNKNOWN,
                detail=f"no {self.attribute} data from a verification source",
            )
        if not claim.trusted:
            return CheckResult(
                constraint=self.label,
                status=CheckStatus.UNKNOWN,
                detail=(
                    f"{self.attribute} reported by {claim.method.value} source "
                    f"({claim.source}) - not strong enough to confirm"
                ),
                evidence=claim,
            )

        ok = self._matches(claim)
        return CheckResult(
            constraint=self.label,
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            detail=self._describe(claim, ok),
            evidence=claim,
        )

    def _matches(self, claim: Claim) -> bool:
        if self.expects_true:
            return claim.value is True
        if self.allowed is None:
            return bool(claim.value)
        value = str(claim.value).strip().lower()
        return value in self.allowed

    def _describe(self, claim: Claim, ok: bool) -> str:
        target = "yes" if self.expects_true else " or ".join(self.allowed or ())
        verdict = "meets" if ok else "does not meet"
        return f"{self.attribute}={claim.value!s} {verdict} required '{target}' ({claim.source})"


def build_constraints(requirements: Requirements) -> list[Constraint]:
    """Translate user requirements into auditable checks."""

    checks: list[Constraint] = []
    if requirements.sodium:
        ceiling = requirements.sodium.strip().lower()
        allowed = tuple(level for level, rank in _LEVELS.items() if rank <= _LEVELS.get(ceiling, 1))
        checks.append(Constraint("sodium", "low-sodium menu", allowed))

    if requirements.max_noise:
        ceiling = requirements.max_noise.strip().lower()
        allowed = tuple(level for level, rank in _LEVELS.items() if rank <= _LEVELS.get(ceiling, 1))
        checks.append(Constraint("noise", "comfortable acoustics", allowed))

    if requirements.texture:
        if requirements.texture.strip().lower() == "soft":
            checks.append(Constraint("texture", "soft / easy-to-chew menu", ("soft",)))
        else:
            checks.append(Constraint("texture", "menu texture", ("soft", "mixed")))

    if requirements.step_free:
        checks.append(Constraint("step_free", "step-free access", expects_true=True))

    return checks


def evaluate(candidate: Candidate, constraints: Iterable[Constraint]) -> list[CheckResult]:
    return [constraint.evaluate(candidate) for constraint in constraints]
