"""The fixture transport must answer every endpoint the live client calls.

A missing route used to surface as a confusing "ungrounded taste" for a tag
seed, which made demo mode look broken rather than explicit.
"""

import unittest
from pathlib import Path

from sociosense.agent import SocioSenseAgent
from sociosense.cli import build_members, build_requirements
from sociosense.clients.facts import JsonFactsProvider
from sociosense.clients.qloo import QlooClient
from sociosense.config import Settings, load_profile
from sociosense.demo import FixtureQlooTransport

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "examples" / "demo_qloo.json"
PROFILE = ROOT / "examples" / "family.json"
FACTS = ROOT / "examples" / "demo_facts.json"


class FixtureTransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = FixtureQlooTransport(GRAPH)

    def test_tags_endpoint_returns_tag_seeds(self):
        payload = self.transport.get_json(
            "https://api.qloo.com/v2/tags", {"filter.query": "mediterranean", "take": 8}
        )
        tags = payload["results"]["tags"]
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]["id"], "seed-mediterranean")
        self.assertTrue(tags[0]["type"].startswith("urn:tag:"))

    def test_tags_endpoint_does_not_resolve_entities(self):
        payload = self.transport.get_json(
            "https://api.qloo.com/v2/tags", {"filter.query": "The Bear"}
        )
        self.assertEqual(payload["results"]["tags"], [])

    def test_unknown_tag_is_empty_not_an_error(self):
        payload = self.transport.get_json(
            "https://api.qloo.com/v2/tags", {"filter.query": "martian"}
        )
        self.assertEqual(payload["results"]["tags"], [])

    def test_unsupported_route_still_raises(self):
        with self.assertRaises(ValueError):
            self.transport.get_json("https://api.qloo.com/v2/unknown")


class FixturePlanTests(unittest.TestCase):
    def test_tag_and_cuisine_seeds_all_resolve(self):
        client = QlooClient("fixture", transport=FixtureQlooTransport(GRAPH), cache_ttl_s=0)
        agent = SocioSenseAgent(
            client, JsonFactsProvider(FACTS), Settings(resolve_workers=2), data_mode="fixture"
        )

        result = agent.plan(
            label="demo",
            members=build_members(load_profile(PROFILE)),
            requirements=build_requirements(load_profile(PROFILE), Settings()),
            location="Bengaluru",
        )

        # Synthwave is deliberately a weak match in the fixture (the search result
        # is "The Midnight"), so it stays ungrounded. What must not happen is a
        # transport-shaped error: every failure should be a matching decision.
        errors = [issue.error or "" for issue in result.unresolved_seeds]
        self.assertTrue(errors)
        self.assertFalse([error for error in errors if "does not implement" in error])
        self.assertNotIn("mediterranean", [issue.seed.query for issue in result.unresolved_seeds])
        self.assertTrue(result.recommendations)
        self.assertTrue(any("cuisine" in event.step for event in result.trace))


if __name__ == "__main__":
    unittest.main()
