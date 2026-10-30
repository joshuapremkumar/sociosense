import unittest
from pathlib import Path

from sociosense.agent import SocioSenseAgent
from sociosense.clients.facts import JsonFactsProvider, NullFactsProvider
from sociosense.clients.qloo import QlooClient
from sociosense.config import Settings
from sociosense.demo import FixtureQlooTransport
from sociosense.models import (
    Domain,
    Member,
    RecommendationStatus,
    Requirements,
    TasteSeed,
)
from sociosense.scoring import bridge_score

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "examples" / "demo_facts.json"
GRAPH = ROOT / "examples" / "demo_qloo.json"


def members():
    def seed(member_id, query, domain, kind="entity"):
        return TasteSeed(query=query, domain=Domain.from_text(domain), member_id=member_id, kind=kind)

    return [
        Member("m1", "Grandmother (72)", (seed("m1", "The Lunchbox", "movie"),
                                         seed("m1", "Bombay Jayashri", "artist"),
                                         seed("m1", "mediterranean", "place", "tag"))),
        Member("m2", "Cousin (24)", (seed("m2", "The Bear", "tv_show"),
                                     seed("m2", "Synthwave", "artist"),
                                     seed("m2", "Aesop", "brand"))),
        Member("m3", "Parent (48)", (seed("m3", "French New Wave", "movie"),
                                     seed("m3", "Jeopardy!", "tv_show"))),
    ]


REQUIREMENTS = Requirements(sodium="low", max_noise="low", texture="soft", radius_m=8000)


def make_agent(facts_path=FACTS, **settings_kwargs):
    client = QlooClient("fixture", transport=FixtureQlooTransport(GRAPH), cache_ttl_s=0)
    provider = JsonFactsProvider(facts_path) if facts_path else NullFactsProvider()
    return SocioSenseAgent(client, provider, Settings(**settings_kwargs), data_mode="fixture")


class AgentPlanTests(unittest.TestCase):
    def setUp(self):
        self.result = make_agent().plan(
            label="Family dinner", members=members(), requirements=REQUIREMENTS, location="Bengaluru"
        )
        self.by_name = {rec.venue.name: rec for rec in self.result.recommendations}

    def test_top_ready_venue_is_the_best_compromise(self):
        ready = [r for r in self.result.recommendations if r.status is RecommendationStatus.READY]
        self.assertTrue(ready)
        self.assertEqual(ready[0].venue.name, "Paper Boat Kitchen")

    def test_inferred_texture_downgrades_to_needs_confirmation(self):
        quiet = self.by_name["The Quiet Courtyard"]
        self.assertIs(quiet.status, RecommendationStatus.NEEDS_CONFIRMATION)
        joined = " ".join(quiet.caveats)
        self.assertIn("soft / easy-to-chew menu", joined)
        self.assertIn("inferred", joined)

    def test_missing_facts_are_never_ready(self):
        saffron = self.by_name["Saffron & Static"]
        self.assertIs(saffron.status, RecommendationStatus.NEEDS_CONFIRMATION)
        self.assertEqual(len(saffron.candidate.unknowns), 3)

    def test_hard_violations_are_excluded_not_ranked(self):
        excluded = {rec.venue.name: rec for rec in self.result.rejected}
        self.assertIn("Kaveri Community Table", excluded)
        self.assertIn("Vinyl & Vine", excluded)
        self.assertIn("Neon Tiffin Room", excluded)
        self.assertIn("excluded", excluded["Kaveri Community Table"].reasons[0])

    def test_trace_records_each_tool(self):
        steps = [event.step for event in self.result.trace]
        self.assertEqual(steps[0], "qloo.resolve_seeds")
        self.assertEqual(steps.count("qloo.place_affinities"), 3)
        self.assertEqual(steps[-3:], ["facts.lookup", "constraints.evaluate", "rank"])

    def test_deterministic_ordering(self):
        again = make_agent().plan(
            label="Family dinner", members=members(), requirements=REQUIREMENTS, location="Bengaluru"
        )
        self.assertEqual(
            [r.venue.entity_id for r in self.result.recommendations],
            [r.venue.entity_id for r in again.recommendations],
        )

    def test_member_affinity_covers_every_member(self):
        top = self.result.recommendations[0].candidate
        self.assertEqual(set(top.affinities), {"m1", "m2", "m3"})
        self.assertGreater(top.bridge_score, 0.6)

    def test_no_facts_provider_means_nothing_is_bookable(self):
        result = make_agent(facts_path=None).plan(
            label="Family dinner", members=members(), requirements=REQUIREMENTS, location="Bengaluru"
        )
        self.assertFalse([r for r in result.recommendations if r.status is RecommendationStatus.READY])
        self.assertTrue(all(r.candidate.facts is None for r in result.recommendations))

    def test_requirement_free_plan_skips_verification_gate(self):
        result = make_agent().plan(
            label="Drinks", members=members(), requirements=Requirements(sodium=None, max_noise=None, texture=None),
            location="Bengaluru",
        )
        self.assertTrue(all(r.status is RecommendationStatus.READY for r in result.recommendations))


class FairnessTests(unittest.TestCase):
    def test_worst_member_dominates(self):
        crowd_pleaser = bridge_score({"a": 0.95, "b": 0.15}, ["a", "b"], fairness=0.6)
        everyone_likes = bridge_score({"a": 0.75, "b": 0.70}, ["a", "b"], fairness=0.6)
        self.assertGreater(everyone_likes, crowd_pleaser)

    def test_member_with_no_observed_affinity_counts_as_zero(self):
        # mean 0.45, worst 0.0, fairness 0.5 -> 0.225
        self.assertAlmostEqual(bridge_score({"a": 0.9}, ["a", "b"], fairness=0.5), 0.225)


class UncoveredMemberTests(unittest.TestCase):
    def test_unresolvable_member_downgrades_plan(self):
        member = Member("m9", "Unknown cousin", (TasteSeed("Nonexistent Taste", Domain.MOVIE, "m9"),))
        result = make_agent().plan(
            label="Dinner", members=members() + [member], requirements=Requirements(), location="Bengaluru"
        )
        self.assertEqual(result.uncovered_members, ["m9"])
        self.assertTrue(all(r.status is RecommendationStatus.UNCOVERED_MEMBER for r in result.recommendations))
        self.assertTrue(any(i.seed.query == "Nonexistent Taste" for i in result.unresolved_seeds))


if __name__ == "__main__":
    unittest.main()
