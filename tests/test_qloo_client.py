import io
import json
import unittest
from unittest import mock

from sociosense.clients.qloo import QlooAuthError, QlooClient, QlooError
from sociosense.models import Domain
from sociosense.transport import HttpTransport, RecordingTransport, TransportError


class StubTransport:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error
        self.calls = 0

    def get_json(self, url, params=None, headers=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "success": True,
            "results": [
                {"entity_id": "abc", "name": "The Bear", "subtype": "urn:entity:tv_show"},
                {"entity_id": "def", "name": "Bear Grylls", "subtype": "urn:entity:person"},
            ],
        }
        self.inner = StubTransport(self.payload)
        self.transport = RecordingTransport(self.inner)
        self.client = QlooClient("secret", transport=self.transport, cache_ttl_s=0)

    def test_search_uses_v2_search_and_domain_filter(self):
        matches = self.client.search_entities("The Bear", domains=[Domain.TV_SHOW])
        call = self.transport.calls[0]

        self.assertTrue(call.url.endswith("/v2/search"))
        self.assertEqual(call.params["query"], "The Bear")
        self.assertEqual(call.params["types"], ["urn:entity:tv_show"])
        self.assertEqual(call.headers["X-Api-Key"], "secret")
        self.assertEqual(matches[0].entity_id, "abc")
        self.assertEqual(matches[0].domain, Domain.TV_SHOW)

    def test_empty_query_makes_no_request(self):
        self.assertEqual(self.client.search_entities("   "), [])
        self.assertEqual(self.transport.calls, [])


class InsightsTests(unittest.TestCase):
    def test_places_call_uses_entity_signals_and_geo_params(self):
        payload = {
            "success": True,
            "results": {
                "entities": [
                    {
                        "entity_id": "p1",
                        "name": "Paper Boat Kitchen",
                        "properties": {
                            "address": "12th Main",
                            "geocode": {"name": "Bengaluru"},
                            "business_rating": 4.6,
                            "price_level": 2,
                        },
                        "query": {"affinity": 0.83, "explainability": {"seed-bear": 0.6}},
                    }
                ]
            },
        }
        transport = RecordingTransport(StubTransport(payload))
        client = QlooClient("secret", transport=transport, cache_ttl_s=0)

        hits = client.place_affinities(["seed-bear", "seed-aesop"], location="Bengaluru", radius_m=5000)

        params = transport.calls[0].params
        self.assertTrue(transport.calls[0].url.endswith("/v2/insights"))
        self.assertEqual(params["filter.type"], "urn:entity:place")
        self.assertEqual(params["signal.interests.entities"], ["seed-bear", "seed-aesop"])
        self.assertEqual(params["filter.location.query"], "Bengaluru")
        self.assertEqual(params["filter.location.radius"], 5000)
        self.assertEqual(hits[0].venue.name, "Paper Boat Kitchen")
        self.assertAlmostEqual(hits[0].affinity, 0.83)
        self.assertEqual(hits[0].explainability["seed-bear"], 0.6)

    def test_avoids_duplicate_seed_ids(self):
        transport = RecordingTransport(StubTransport({"success": True, "results": {"entities": []}}))
        client = QlooClient("secret", transport=transport, cache_ttl_s=0)
        client.place_affinities(["a", "a", "b"])
        self.assertEqual(transport.calls[0].params["signal.interests.entities"], ["a", "b"])


class FailureTests(unittest.TestCase):
    def test_401_becomes_auth_error(self):
        client = QlooClient("bad", transport=StubTransport(error=TransportError("nope", status=401)))
        with self.assertRaises(QlooAuthError):
            client.search_entities("anything")

    def test_server_error_never_returns_fabricated_results(self):
        client = QlooClient("key", transport=StubTransport(error=TransportError("boom", status=500)))
        with self.assertRaises(QlooError):
            client.place_affinities(["seed"])

    def test_auth_required_at_construction(self):
        with self.assertRaises(QlooAuthError):
            QlooClient("")


class TagSearchTests(unittest.TestCase):
    def test_parses_nested_results_tags(self):
        payload = {
            "success": True,
            "results": {
                "tags": [
                    {
                        "id": "urn:tag:genre:place:restaurant:mediterranean",
                        "name": "Mediterranean",
                        "type": "urn:tag:genre:place",
                        "parents": [{"type": "urn:entity:place"}],
                    }
                ]
            },
        }
        transport = RecordingTransport(StubTransport(payload))
        client = QlooClient("key", transport=transport, cache_ttl_s=0)

        tags = client.search_tags("mediterranean")

        self.assertEqual(transport.calls[0].params["filter.query"], "mediterranean")
        self.assertEqual(tags[0].tag_id, "urn:tag:genre:place:restaurant:mediterranean")
        self.assertEqual(tags[0].parents, ("urn:entity:place",))


class TagSeedInsightsTests(unittest.TestCase):
    def test_dining_filter_tags_and_signal_tags_are_sent(self):
        transport = RecordingTransport(StubTransport({"success": True, "results": {"entities": []}}))
        client = QlooClient("key", transport=transport, cache_ttl_s=0)

        client.place_affinities(
            ["seed-bear"],
            tag_ids=["urn:tag:genre:place:restaurant:mediterranean"],
            location="Bengaluru",
            filter_tags=["urn:tag:genre:place:restaurant:mediterranean"],
        )

        params = transport.calls[0].params
        self.assertEqual(params["filter.tags"], ["urn:tag:category:place:restaurant",
                                                 "urn:tag:genre:place:restaurant:mediterranean"])
        self.assertEqual(params["operator.filter.tags"], "intersection")
        self.assertEqual(params["signal.interests.tags"], ["urn:tag:genre:place:restaurant:mediterranean"])

    def test_empty_signals_make_no_request(self):
        transport = RecordingTransport(StubTransport({"success": True}))
        client = QlooClient("key", transport=transport, cache_ttl_s=0)
        self.assertEqual(client.place_affinities([]), [])
        self.assertEqual(transport.calls, [])


class LiveShapeTests(unittest.TestCase):
    """Shapes observed in real hackathon responses (trimmed and re-typed by hand)."""

    def payload(self):
        return {
            "success": True,
            "results": {
                "entities": [
                    {
                        "entity_id": "VENUE1",
                        "name": "Cafe Noir",
                        "properties": {
                            "address": "12 Main",
                            "geocode": {"name": "Bengaluru"},
                            "business_rating": 4.4,
                            "price_level": 3,
                            "cuisine_description": "French cafe.",
                            "good_for": ["dinner"],
                        },
                        "query": {
                            "affinity": 0.91,
                            "explainability": {
                                "signal.interests.entities": [
                                    {"entity_id": "4C305B2F-AAAA", "score": 0.31},
                                    {"entity_id": "EBB7D5B5-BBBB", "score": 0.22},
                                ]
                            },
                        },
                    },
                    {
                        "entity_id": "LANDMARK",
                        "name": "Some Palace",
                        "properties": {"address": "Palace Rd", "geocode": {"name": "Bengaluru"}},
                        "query": {"affinity": 0.99},
                    },
                ]
            },
        }

    def test_nested_explainability_is_flattened_and_lowercased(self):
        transport = RecordingTransport(StubTransport(self.payload()))
        client = QlooClient("key", transport=transport, cache_ttl_s=0)
        hits = client.place_affinities(["seed-bear"])
        self.assertEqual(hits[0].explainability["4c305b2f-aaaa"], 0.31)
        self.assertEqual(hits[0].explainability["ebb7d5b5-bbbb"], 0.22)

    def test_non_dining_place_is_dropped(self):
        transport = RecordingTransport(StubTransport(self.payload()))
        client = QlooClient("key", transport=transport, cache_ttl_s=0)
        hits = client.place_affinities(["seed-bear"])
        self.assertEqual([hit.venue.name for hit in hits], ["Cafe Noir"])

    def test_venue_details_are_parsed(self):
        transport = RecordingTransport(StubTransport(self.payload()))
        client = QlooClient("key", transport=transport, cache_ttl_s=0)
        venue = client.place_affinities(["seed-bear"])[0].venue
        self.assertEqual(venue.entity_id, "venue1")
        self.assertEqual(venue.cuisine, "French cafe.")
        self.assertEqual(venue.good_for, ("dinner",))


class CachingTests(unittest.TestCase):
    def test_second_identical_call_is_cached(self):
        inner = StubTransport({"success": True, "results": []})
        transport = RecordingTransport(inner)
        client = QlooClient("key", transport=transport, cache_ttl_s=600)
        client.search_entities("Aesop")
        client.search_entities("Aesop")
        self.assertEqual(inner.calls, 1)


class HttpTransportRetryTests(unittest.TestCase):
    def test_retries_on_429_honouring_retry_after(self):
        sleeps: list[float] = []
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = json.dumps({"success": True}).encode()
        import urllib.error

        http_error = urllib.error.HTTPError("https://x", 429, "rate", {"Retry-After": "2"}, io.BytesIO(b""))
        transport = HttpTransport(max_retries=2, sleeper=sleeps.append)

        with mock.patch("urllib.request.urlopen", side_effect=[http_error, ok]):
            payload = transport.get_json("https://api.qloo.com/v2/search", {"query": "x"})

        self.assertTrue(payload["success"])
        self.assertEqual(sleeps, [2.0])

    def test_gives_up_after_max_retries(self):
        import urllib.error

        http_error = urllib.error.HTTPError("https://x", 503, "unavailable", {}, io.BytesIO(b""))
        transport = HttpTransport(max_retries=1, sleeper=lambda _: None)
        try:
            with mock.patch("urllib.request.urlopen", side_effect=[http_error, http_error]):
                with self.assertRaises(TransportError):
                    transport.get_json("https://api.qloo.com/v2/search")
        finally:
            http_error.close()


if __name__ == "__main__":
    unittest.main()
