import json
import tempfile
import unittest
from pathlib import Path

from sociosense.clients.facts import JsonFactsProvider, NullFactsProvider
from sociosense.models import Venue


def write_facts(directory: str, venues: dict) -> Path:
    path = Path(directory) / "facts.json"
    path.write_text(json.dumps({"venues": venues}), encoding="utf-8")
    return path


class NormalizedFactsTests(unittest.TestCase):
    def test_accent_and_punctuation_insensitive_name_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_facts(
                tmp,
                {
                    "Café Noir": {
                        "provider": "venue sheet",
                        "claims": {
                            "sodium": {
                                "value": "low",
                                "method": "declared",
                                "source": "menu",
                                "confidence": 0.9,
                            }
                        },
                    }
                },
            )
            provider = JsonFactsProvider(path)

            facts = provider.lookup(Venue(entity_id="unknown-id", name="Cafe Noir"))

            self.assertIsNotNone(facts)
            self.assertEqual(facts.claims["sodium"].value, "low")

    def test_exact_entity_id_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_facts(
                tmp,
                {
                    "venue1": {
                        "claims": {"noise": {"value": "low", "method": "measured", "confidence": 0.9}}
                    },
                    "Other Name": {
                        "claims": {"noise": {"value": "high", "method": "measured", "confidence": 0.9}}
                    },
                },
            )
            provider = JsonFactsProvider(path)

            facts = provider.lookup(Venue(entity_id="venue1", name="Other Name"))

            self.assertEqual(facts.claims["noise"].value, "low")

    def test_unrelated_venue_still_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_facts(tmp, {"Cafe Noir": {"claims": {}}})
            provider = JsonFactsProvider(path)
            self.assertIsNone(provider.lookup(Venue(entity_id="x", name="Tikka Kebab")))

    def test_null_provider_is_honest_emptiness(self):
        self.assertIsNone(NullFactsProvider().lookup(Venue(entity_id="x", name="Anywhere")))


if __name__ == "__main__":
    unittest.main()
