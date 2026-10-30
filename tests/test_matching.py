import unittest

from sociosense.matching import name_match_score, normalize, tokens


class MatchingTests(unittest.TestCase):
    def test_exact_title(self):
        self.assertEqual(name_match_score("The Bear", "The Bear"), 1.0)

    def test_containment_counts_as_full_match(self):
        self.assertEqual(name_match_score("The Bear", "The Bear Family & Me"), 1.0)

    def test_wrong_genre_match_is_rejected(self):
        # The live API returns this for "French New Wave".
        self.assertLess(name_match_score("French New Wave", "The French Connection"), 0.5)

    def test_partial_but_relevant(self):
        self.assertGreaterEqual(name_match_score("Bombay Jayashri", "Bombay Jayashri"), 0.5)

    def test_accents_and_punctuation(self):
        self.assertEqual(normalize("Lavonne Café!"), "lavonne cafe")
        self.assertEqual(tokens("The Bear"), {"bear"})


if __name__ == "__main__":
    unittest.main()
