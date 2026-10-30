"""Explicit demo mode.

This is a deterministic stand-in for the Qloo API, driven by a small synthetic
taste graph.  It exists so the planner can be exercised without credentials,
and it is labelled ``data_mode="fixture"`` everywhere it surfaces.  It is NOT a
fallback: the live client raises on failure instead of reaching for this.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "demo_qloo.json"


class FixtureQlooTransport:
    def __init__(self, path: str | Path = DEFAULT_FIXTURE) -> None:
        self.path = Path(path)
        with self.path.open("r", encoding="utf-8") as handle:
            self.data = json.load(handle)
        self.seeds: dict[str, dict[str, Any]] = {
            key.lower(): value for key, value in (self.data.get("seeds") or {}).items()
        }

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        if "/search" in url:
            return self._search(str(params.get("query", "")))
        if "/insights" in url:
            return self._insights(params)
        raise ValueError(f"fixture transport does not implement {url}")

    def _search(self, query: str) -> dict[str, Any]:
        seed = self.seeds.get(query.strip().lower())
        if not seed:
            return {"success": True, "results": []}
        if seed.get("kind") == "tag":
            return {
                "success": True,
                "results": {
                    "tags": [
                        {
                            "id": seed["id"],
                            "name": seed["name"],
                            "type": "urn:tag:genre:place:restaurant",
                            "parents": [{"type": "urn:entity:place"}],
                            "popularity": 0.9,
                        }
                    ]
                },
            }
        return {
            "success": True,
            "results": [
                {
                    "entity_id": seed["id"],
                    "name": seed["name"],
                    "subtype": seed["domain"],
                    "popularity": 0.9,
                }
            ],
        }

    def _insights(self, params: Mapping[str, Any]) -> dict[str, Any]:
        seed_ids: list[str] = []
        for key in ("signal.interests.entities", "signal.interests.tags"):
            raw = params.get(key) or []
            seed_ids.extend(raw.split(",") if isinstance(raw, str) else raw)
        wanted = set(seed_ids)
        max_price = params.get("filter.price_level.max")
        take = int(params.get("take") or 20)

        scored: list[tuple[float, dict[str, Any]]] = []
        for venue in self.data.get("venues", []):
            scores = {sid: float(score) for sid, score in (venue.get("affinity") or {}).items() if sid in wanted}
            if not scores:
                continue
            if max_price is not None and venue.get("price_level", 0) > int(max_price):
                continue
            affinity = sum(scores.values()) / len(scores)
            entity = {
                "entity_id": venue["id"],
                "name": venue["name"],
                "type": "urn:entity",
                "subtype": "urn:entity:place",
                "properties": {
                    "address": venue.get("address"),
                    "geocode": {"name": venue.get("city")},
                    "business_rating": venue.get("rating"),
                    "price_level": venue.get("price_level"),
                    "tags": [{"name": tag} for tag in venue.get("tags", [])],
                },
                "query": {
                    "affinity": round(affinity, 4),
                    "explainability": {sid: round(score, 4) for sid, score in scores.items()},
                },
            }
            scored.append((affinity, entity))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return {
            "success": True,
            "results": {"entities": [entity for _, entity in scored[:take]], "duration": 3},
        }
