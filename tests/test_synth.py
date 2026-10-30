import json
import unittest

from sociosense.agent import SocioSenseAgent
from sociosense.clients.qloo import QlooClient
from sociosense.config import Settings
from sociosense.demo import FixtureQlooTransport
from sociosense.synth import LLMPhraser, OutlineValidationError, render_markdown, to_dict
from tests.test_agent import GRAPH, REQUIREMENTS, make_agent, members


class _NoMatchProvider:
    """A configured facts source that has no record for any venue."""

    name = "json"

    def lookup(self, venue):
        return None


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = make_agent().plan(
            label="Family dinner", members=members(), requirements=REQUIREMENTS, location="Bengaluru"
        )

    def test_dict_output_is_json_serialisable(self):
        payload = to_dict(self.result)
        text = json.dumps(payload)
        self.assertIn("Paper Boat Kitchen", text)
        self.assertEqual(payload["data_mode"], "fixture")
        self.assertTrue(payload["disclaimer"])

    def test_markdown_flags_fixture_data(self):
        report = render_markdown(self.result)
        self.assertIn("Fixture data", report)
        self.assertIn("Ready to book", report)
        self.assertIn("Worth a phone call", report)
        self.assertIn("not medical advice", report)

    def test_no_venue_appears_without_going_through_checks(self):
        for rec in self.result.recommendations:
            self.assertTrue(rec.candidate.checks or rec.status.name == "READY")

    def test_empty_ready_section_names_the_real_cause(self):
        client = QlooClient("fixture", transport=FixtureQlooTransport(GRAPH), cache_ttl_s=0)
        agent = SocioSenseAgent(client, _NoMatchProvider(), Settings(), data_mode="fixture")
        result = agent.plan(
            label="Family dinner", members=members(), requirements=REQUIREMENTS, location="Bengaluru"
        )
        report = render_markdown(result)
        self.assertIn("matched 0 of", report)
        self.assertNotIn("Add `--facts`", report)


class OutlineGuardTests(unittest.TestCase):
    def test_accepts_in_range_indices(self):
        LLMPhraser._validate({"intro": "hi", "notes": [{"index": 1, "text": "good"}]}, 2)

    def test_rejects_out_of_range_index(self):
        with self.assertRaises(OutlineValidationError):
            LLMPhraser._validate({"intro": "hi", "notes": [{"index": 3, "text": "invented"}]}, 2)

    def test_rejects_missing_notes(self):
        with self.assertRaises(OutlineValidationError):
            LLMPhraser._validate({"intro": "hi"}, 1)


if __name__ == "__main__":
    unittest.main()
