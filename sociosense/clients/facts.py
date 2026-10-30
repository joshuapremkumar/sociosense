"""Venue attribute providers.

Qloo's graph answers *"will these people like this place?"*.  It does not
answer *"is the food soft and low-sodium, and is the room quiet enough for a
hearing aid?"*.  Conflating the two is how a recommendation engine ends up
inventing dietary facts, so the two sources are kept separate behind this
interface.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..matching import normalize
from ..models import Claim, ClaimMethod, Venue, VenueFacts


class FactsProvider(Protocol):
    name: str

    def lookup(self, venue: Venue) -> VenueFacts | None: ...


class NullFactsProvider:
    """No verification source configured: every attribute is honestly unknown."""

    name = "none"

    def lookup(self, venue: Venue) -> VenueFacts | None:
        return None


class JsonFactsProvider:
    """Provider backed by a reviewed JSON file.

    Intended for venue-sourced data (a manager's accessibility sheet, a
    dietitian-reviewed menu, a calibrated sound reading) or for tests.  Each
    attribute is a claim and must state how it was obtained.
    """

    name = "json"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        self._venues: dict[str, dict[str, Any]] = raw.get("venues", raw)
        #: Fallback index so "Cafe Noir" can match a record stored as "Café Noir".
        self._norm_venues: dict[str, dict[str, Any]] = {
            normalize(str(key)): value for key, value in self._venues.items()
        }

    def lookup(self, venue: Venue) -> VenueFacts | None:
        record = self._venues.get(venue.entity_id) or self._venues.get(venue.name)
        if not record and venue.name:
            record = self._norm_venues.get(normalize(venue.name))
        if not record:
            return None
        claims: dict[str, Claim] = {}
        retrieved = record.get("retrieved_at")
        retrieved_at = _parse_dt(retrieved) if retrieved else None
        for attribute, payload in (record.get("claims") or {}).items():
            if isinstance(payload, dict):
                claims[attribute] = Claim(
                    value=payload.get("value"),
                    source=payload.get("source") or record.get("source") or self.path.name,
                    method=ClaimMethod(payload.get("method", "declared")),
                    confidence=float(payload.get("confidence", 1.0)),
                    retrieved_at=retrieved_at,
                )
            else:  # shorthand: "sodium": "low"
                claims[attribute] = Claim(
                    value=payload,
                    source=record.get("source") or self.path.name,
                    method=ClaimMethod(record.get("method", "declared")),
                    retrieved_at=retrieved_at,
                )
        return VenueFacts(
            venue_id=venue.entity_id,
            claims=claims,
            provider=record.get("provider", self.name),
        )


def _parse_dt(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
