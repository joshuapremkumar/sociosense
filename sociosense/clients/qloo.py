"""Qloo v2 client.

Reference: https://docs.qloo.com/reference/insights-api-deep-dive
Endpoints:  GET /v2/search          (entity resolution)
            GET /v2/tags            (tag resolution)
            GET /v2/insights        (cross-category affinity)
Auth:       X-Api-Key header

Notes learned from live responses:

* ``query.explainability`` nests an array under ``signal.interests.entities``.
* Entity IDs are returned upper-case in explainability but lower-case in search,
  so all IDs are normalised to lower-case before comparison.
* A bare ``filter.type=urn:entity:place`` returns landmarks and streets, not
  dining venues.  ``filter.tags=urn:tag:category:place:restaurant`` is the
  supported way to restrict results to restaurants.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..models import Domain, Venue
from ..transport import HttpTransport, TransportError

DEFAULT_DINING_TAGS = ("urn:tag:category:place:restaurant",)

#: Preference order when several tags share the same name.
_TAG_TYPE_PRIORITY = (
    "urn:tag:genre:place",
    "urn:tag:cuisine:qloo",
    "urn:tag:category:place",
    "urn:tag:genre:music",
)


class QlooError(RuntimeError):
    """Base error. Never swallowed into fabricated results."""


class QlooAuthError(QlooError):
    pass


@dataclass(frozen=True)
class EntityMatch:
    entity_id: str
    name: str
    domain: Domain | None
    popularity: float | None = None


@dataclass(frozen=True)
class TagMatch:
    tag_id: str
    name: str
    type: str
    parents: tuple[str, ...] = ()
    popularity: float | None = None


@dataclass(frozen=True)
class AffinityHit:
    venue: Venue
    affinity: float
    explainability: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class QlooClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.qloo.com",
        transport: HttpTransport | Any | None = None,
        timeout_s: float = 10.0,
        max_retries: int = 3,
        cache_ttl_s: float = 900.0,
        clock=time.monotonic,
    ) -> None:
        if not api_key:
            raise QlooAuthError("an API key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._transport = transport or HttpTransport(timeout_s=timeout_s, max_retries=max_retries)
        self._cache_ttl = cache_ttl_s
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        #: Seed resolution runs on a thread pool, so cache access is guarded.
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def search_entities(
        self,
        query: str,
        *,
        domains: Sequence[Domain] | None = None,
        take: int = 5,
        location: str | None = None,
    ) -> list[EntityMatch]:
        """Resolve a free-text taste into Qloo entity IDs.

        ``location`` maps to ``filter.location``, which accepts a lat/lon pair,
        WKT geometry or a Qloo locality UUID - not a city name.  Location-aware
        ranking belongs on the insights call (``filter.location.query``).
        """
        if not query.strip():
            return []
        params: dict[str, Any] = {"query": query, "take": min(max(take, 1), 200)}
        if domains:
            params["types"] = [d.value for d in domains]
        if location:
            params["filter.location"] = location

        payload = self._get("/v2/search", params)
        results = payload.get("results") or []
        if isinstance(results, dict):
            results = results.get("entities") or []
        matches: list[EntityMatch] = []
        for item in results:
            entity_id = item.get("entity_id") or item.get("id")
            if not entity_id:
                continue
            matches.append(
                EntityMatch(
                    entity_id=str(entity_id).lower(),
                    name=item.get("name") or query,
                    domain=_coerce_domain(item.get("subtype") or item.get("type")),
                    popularity=_as_float(item.get("popularity")),
                )
            )
        return matches

    def search_tags(self, query: str, *, take: int = 8) -> list[TagMatch]:
        """Resolve a genre/cuisine/mood string to Qloo tag IDs."""
        if not query.strip():
            return []
        payload = self._get("/v2/tags", {"filter.query": query, "take": min(max(take, 1), 50)})
        results = payload.get("results") or []
        if isinstance(results, dict):
            results = results.get("tags") or []
        tags: list[TagMatch] = []
        for item in results:
            tag_id = item.get("id") or item.get("tag_id")
            if not tag_id:
                continue
            tags.append(
                TagMatch(
                    tag_id=str(tag_id).lower(),
                    name=str(item.get("name") or query),
                    type=str(item.get("type") or ""),
                    parents=tuple(str(p.get("type")) for p in item.get("parents") or [] if isinstance(p, Mapping)),
                    popularity=_as_float(item.get("popularity")),
                )
            )
        return tags

    def place_affinities(
        self,
        seed_ids: Sequence[str],
        *,
        tag_ids: Sequence[str] | None = None,
        location: str | None = None,
        radius_m: int = 8_000,
        take: int = 40,
        max_price_level: int | None = 3,
        dining_tags: Sequence[str] | None = DEFAULT_DINING_TAGS,
        filter_tags: Sequence[str] | None = None,
        explainability: bool = True,
    ) -> list[AffinityHit]:
        """Cross-domain affinity for dining venues.

        ``seed_ids`` and ``tag_ids`` are *taste signals* ("people like this").
        ``filter_tags`` are *constraints* ("places tagged this"), e.g. a cuisine.
        """
        seed_ids = list(dict.fromkeys(s.lower() for s in seed_ids))
        tag_ids = list(dict.fromkeys(t.lower() for t in (tag_ids or [])))
        if not seed_ids and not tag_ids:
            return []

        params: dict[str, Any] = {
            "filter.type": Domain.PLACE.value,
            "take": min(max(take, 1), 50),
            "sort_by": "affinity",
        }
        if seed_ids:
            params["signal.interests.entities"] = seed_ids[:20]
        if tag_ids:
            params["signal.interests.tags"] = tag_ids[:20]

        required_tags = list(dict.fromkeys([*(dining_tags or ()), *(filter_tags or [])]))
        if required_tags:
            params["filter.tags"] = required_tags
            params["operator.filter.tags"] = "intersection"

        if location:
            params["filter.location.query"] = location
            params["filter.location.radius"] = radius_m
        if max_price_level is not None:
            params["filter.price_level.max"] = max_price_level
        if explainability:
            params["feature.explainability"] = True

        payload = self._get("/v2/insights", params)
        results = payload.get("results") or {}
        entities = results.get("entities") if isinstance(results, dict) else results
        hits: list[AffinityHit] = []
        for item in entities or []:
            venue = parse_venue(item)
            if venue is None or not _looks_like_dining(venue):
                continue
            hits.append(
                AffinityHit(
                    venue=venue,
                    affinity=_clamp01(
                        _as_float(_nested(item, "query", "affinity"))
                        or _as_float(item.get("affinity"))
                        or 0.0
                    ),
                    explainability=_parse_explainability(item),
                    raw=item,
                )
            )
        hits.sort(key=lambda hit: hit.affinity, reverse=True)
        return hits

    # -------------------------------------------------------------- internals
    def _get(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        key = f"{path}?{'&'.join(f'{k}={v}' for k, v in sorted(params.items()))}"
        now = self._clock()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self._cache_ttl:
                return cached[1]

        url = f"{self._base}{path}"
        headers = {"X-Api-Key": self._api_key, "Content-Type": "application/json", "Accept": "application/json"}
        try:
            payload = self._transport.get_json(url, params, headers)
        except TransportError as exc:
            if exc.status in {401, 403}:
                raise QlooAuthError("Qloo rejected the API key") from exc
            raise QlooError(f"Qloo request failed ({exc.status or 'network'}): {exc}") from exc

        if payload.get("success") is False:
            raise QlooError(f"Qloo returned an error: {payload.get('message') or payload}")
        with self._cache_lock:
            self._cache[key] = (now, payload)
        return payload


def parse_venue(item: Mapping[str, Any]) -> Venue | None:
    entity_id = item.get("entity_id") or item.get("id")
    name = item.get("name")
    if not entity_id or not name:
        return None
    props = item.get("properties") or {}
    geocode = props.get("geocode") or {}
    coords = props.get("coordinates") or props.get("coordinate") or {}
    lat = _as_float(props.get("latitude") or geocode.get("latitude") or coords.get("latitude"))
    lon = _as_float(props.get("longitude") or geocode.get("longitude") or coords.get("longitude"))
    images = props.get("image") or {}
    return Venue(
        entity_id=str(entity_id).lower(),
        name=str(name),
        address=props.get("address"),
        city=geocode.get("name") or props.get("city") or props.get("neighborhood"),
        latitude=lat,
        longitude=lon,
        rating=_as_float(props.get("business_rating") or props.get("rating")),
        price_level=_as_int(props.get("price_level")),
        tags=tuple(_tag_names(props.get("tags"))),
        source_url=images.get("url") if isinstance(images, dict) else None,
        cuisine=_clean_text(props.get("cuisine_description")),
        description=_clean_text(props.get("short_description") or props.get("description")),
        menu_url=props.get("menu_url"),
        good_for=tuple(_string_list(props.get("good_for"))),
    )


def _looks_like_dining(venue: Venue) -> bool:
    """Drop landmarks and residential blocks that slip past the tag filter."""
    return bool(venue.cuisine or venue.price_level is not None or venue.menu_url or venue.good_for)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _tag_names(tags: Any) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        if isinstance(tag, Mapping):
            name = tag.get("name") or tag.get("id")
            if name:
                out.append(str(name))
        elif tag:
            out.append(str(tag))
    return out


def _parse_explainability(item: Mapping[str, Any]) -> dict[str, float]:
    """Flatten ``query.explainability`` into ``{entity_id: score}``.

    Live shape::

        {"signal.interests.entities": [{"entity_id": "...", "score": 0.13}]}
    """
    block = _nested(item, "query", "explainability") or {}
    scores: dict[str, float] = {}

    def absorb(entries: Any) -> None:
        if isinstance(entries, Mapping):
            for key, value in entries.items():
                if key == "entities":
                    absorb(value)
                elif isinstance(value, (int, float)):
                    scores[str(key).lower()] = float(value)
                elif isinstance(value, list):
                    absorb(value)
        elif isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                ident = entry.get("entity_id") or entry.get("id") or entry.get("name")
                score = _as_float(entry.get("score") or entry.get("affinity"))
                if ident and score is not None:
                    scores[str(ident).lower()] = score

    absorb(block)
    return scores


def _coerce_domain(value: Any) -> Domain | None:
    if not isinstance(value, str):
        return None
    try:
        return Domain(value)
    except ValueError:
        return None


def _nested(data: Mapping[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _clamp01(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, value))
