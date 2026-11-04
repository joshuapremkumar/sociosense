import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sociosense.agent import SocioSenseAgent
from sociosense.clients.facts import JsonFactsProvider
from sociosense.clients.qloo import QlooClient
from sociosense.config import Settings, load_profile
from sociosense.demo import FixtureQlooTransport
from sociosense.serve import create_server, parse_constraints

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "examples" / "demo_qloo.json"
PROFILE = ROOT / "examples" / "family.json"
FACTS = ROOT / "examples" / "demo_facts.json"


class ConstraintParsingTests(unittest.TestCase):
    def test_missing_query_uses_profile_defaults(self):
        profile = load_profile(PROFILE)
        active = parse_constraints(None, profile)
        self.assertEqual(active, {"sodium", "noise", "texture"})

    def test_empty_query_means_no_constraints(self):
        self.assertEqual(parse_constraints("", load_profile(PROFILE)), set())

    def test_unknown_keys_are_ignored(self):
        self.assertEqual(parse_constraints("sodium,bogus,noise", load_profile(PROFILE)), {"sodium", "noise"})


class DashboardServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        profile = load_profile(PROFILE)
        client = QlooClient("fixture", transport=FixtureQlooTransport(GRAPH), cache_ttl_s=0)
        agent = SocioSenseAgent(client, JsonFactsProvider(FACTS), Settings(), data_mode="fixture")
        cls.server = create_server(profile, agent, Settings(), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=30) as response:
            return response.status, response.read(), response.headers

    def get_json(self, path: str):
        status, body, _ = self.get(path)
        return status, json.loads(body)

    def test_index_is_served_with_assets(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("SocioSense", text)
        self.assertIn("/assets/app.js", text)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))

    def test_profile_endpoint_lists_members(self):
        status, payload = self.get_json("/api/profile")
        self.assertEqual(status, 200)
        self.assertEqual(payload["location"], "Bengaluru")
        self.assertEqual(len(payload["members"]), 3)
        self.assertTrue(payload["requirements"]["sodium"])

    def test_plan_endpoint_returns_fixture_plan(self):
        status, payload = self.get_json("/api/plan")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data_mode"], "fixture")
        self.assertEqual(payload["label"], "Three-generation family dinner")
        self.assertTrue(payload["recommendations"])
        self.assertEqual(len(payload["members"]), 3)
        self.assertTrue(all("cuisine" in rec for rec in payload["recommendations"]))

    def test_constraint_query_changes_the_rules(self):
        _, relaxed = self.get_json("/api/plan?constraints=")
        self.assertEqual(relaxed["active_constraints"], [])
        names = [check["constraint"] for rec in relaxed["recommendations"] for check in rec["constraints"]]
        self.assertFalse([name for name in names if "sodium" in name])

        _, strict = self.get_json("/api/plan?constraints=sodium")
        self.assertEqual(strict["active_constraints"], ["sodium"])
        names = [check["constraint"] for rec in strict["recommendations"] for check in rec["constraints"]]
        self.assertTrue([name for name in names if "sodium" in name])

    def test_unknown_path_is_404(self):
        try:
            self.get("/nope")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
            exc.close()
        else:
            self.fail("expected a 404")


if __name__ == "__main__":
    unittest.main()
