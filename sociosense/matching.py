"""Name-matching guards for entity/tag resolution.

Qloo's search is fuzzy.  Searching for the genre "French New Wave" returns
"The French Connection"; searching a cuisine returns a restaurant with that
name.  Silently trusting the first hit poisons every downstream score, so the
planner scores lexical overlap and refuses weak matches instead.
"""

from __future__ import annotations

import re
import unicodedata

_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "feat", "ft", "le", "la", "les", "de"})
_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _PATTERN.sub(" ", folded.lower()).strip()


def tokens(text: str) -> set[str]:
    return {token for token in normalize(text).split() if token and token not in _STOPWORDS}


def name_match_score(query: str, candidate: str) -> float:
    """Jaccard overlap, with subset queries treated as a full match.

    ``"The Bear"`` vs ``"The Bear Family & Me"`` -> 1.0 (the query is contained).
    ``"French New Wave"`` vs ``"The French Connection"`` -> 0.25 (rejected).
    """
    left, right = tokens(query), tokens(candidate)
    if not left or not right:
        return 0.0
    if left <= right or right <= left:
        return 1.0
    return len(left & right) / len(left | right)
