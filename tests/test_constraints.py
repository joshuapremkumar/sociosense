import unittest
from datetime import datetime, timezone

from sociosense.constraints import build_constraints, evaluate
from sociosense.models import (
    Candidate,
    CheckStatus,
    Claim,
    ClaimMethod,
    Requirements,
    Venue,
    VenueFacts,
)


def candidate_with(claims):
    venue = Venue(entity_id="v1", name="Test Venue")
    facts = VenueFacts(venue_id="v1", claims=claims, provider="test") if claims is not None else None
    return Candidate(venue=venue, facts=facts)


def declared(attribute, value):
    return Claim(value=value, source="venue menu", method=ClaimMethod.DECLARED, confidence=0.9)


class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.constraints = build_constraints(Requirements(sodium="low", max_noise="low", texture="soft"))

    def test_missing_facts_are_unknown_not_pass(self):
        result = evaluate(candidate_with(None), self.constraints)
        self.assertTrue(all(check.status is CheckStatus.UNKNOWN for check in result))

    def test_inferred_claim_cannot_satisfy_a_hard_constraint(self):
        claims = {
            "sodium": Claim("low", "generic cuisine heuristic", ClaimMethod.INFERRED, 0.9),
            "noise": declared("noise", "low"),
            "texture": declared("texture", "soft"),
        }
        checks = {c.constraint: c for c in evaluate(candidate_with(claims), self.constraints)}
        self.assertIs(checks["low-sodium menu"].status, CheckStatus.UNKNOWN)
        self.assertIs(checks["comfortable acoustics"].status, CheckStatus.PASS)

    def test_measured_claim_is_trusted(self):
        claims = {"noise": Claim("low", "sound meter", ClaimMethod.MEASURED, 0.8)}
        checks = {c.constraint: c for c in evaluate(candidate_with(claims), self.constraints)}
        self.assertIs(checks["comfortable acoustics"].status, CheckStatus.PASS)

    def test_violation_fails(self):
        claims = {
            "sodium": declared("sodium", "high"),
            "noise": declared("noise", "moderate"),
            "texture": declared("texture", "soft"),
        }
        checks = {c.constraint: c for c in evaluate(candidate_with(claims), self.constraints)}
        self.assertIs(checks["low-sodium menu"].status, CheckStatus.FAIL)
        self.assertIs(checks["comfortable acoustics"].status, CheckStatus.FAIL)

    def test_low_confidence_declared_claim_is_not_trusted(self):
        claims = {"sodium": Claim("low", "old web page", ClaimMethod.DECLARED, 0.4)}
        checks = {c.constraint: c for c in evaluate(candidate_with(claims), self.constraints)}
        self.assertIs(checks["low-sodium menu"].status, CheckStatus.UNKNOWN)

    def test_no_requirements_means_no_checks(self):
        self.assertEqual(build_constraints(Requirements()), [])

    def test_step_free_requires_explicit_true(self):
        constraints = build_constraints(Requirements(step_free=True))
        unknown = evaluate(candidate_with(None), constraints)
        self.assertIs(unknown[0].status, CheckStatus.UNKNOWN)
        claims = {"step_free": declared("step_free", False)}
        self.assertIs(evaluate(candidate_with(claims), constraints)[0].status, CheckStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
