"""Output rendering.

Two renderers:

* ``render_markdown`` / ``to_dict`` - deterministic, built only from the
  structured ``PlanResult``.  Safe default.
* ``LLMPhraser`` - optional natural-language polish.  The model receives a
  *closed* list of venues and must return JSON that references them by index.
  Anything it invents fails validation and the deterministic text is used
  instead.  The model never chooses the venue.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Any

from .models import PlanResult, Recommendation, RecommendationStatus, Venue

DISCLAIMER = (
    "Affinity scores are Qloo's relative taste-graph estimates for these seeds, "
    "not quality or safety ratings. Dietary and accessibility items marked "
    "'needs confirmation' have not been verified by the venue; confirm directly, "
    "especially for medical diets. This is not medical advice."
)


def _empty_ready_message(result: PlanResult) -> str:
    """Say *why* the section is empty instead of always blaming missing facts."""
    for event in result.trace:
        if event.step != "facts.lookup":
            continue
        payload = event.payload or {}
        provider = payload.get("provider")
        matched = payload.get("matched")
        total = payload.get("total")
        if provider and provider != "none" and matched == 0 and total:
            return (
                f"_No venue in this shortlist has a verification record. The facts source "
                f"matched 0 of {total} venues; records are keyed by Qloo entity_id or exact "
                "venue name._"
            )
        break
    return (
        "_Nothing passed every hard constraint with evidence. Add `--facts` with "
        "venue-verified records, or call the venues in the shortlist below._"
    )


def render_markdown(result: PlanResult, limit: int = 7) -> str:
    lines: list[str] = []
    lines.append("# SocioSense - culturally grounded family plan")
    lines.append("")
    lines.append(f"**Occasion:** {result.request_label}  ")
    lines.append(f"**Area:** {result.location}  ")
    lines.append(f"**Data mode:** `{result.data_mode}`")
    if result.data_mode != "live":
        lines.append("")
        lines.append("> **Fixture data.** Venues below are synthetic demo records, not real places.")
    lines.append("")

    ready = [r for r in result.recommendations if r.status is RecommendationStatus.READY]
    pending = [r for r in result.recommendations if r.status is not RecommendationStatus.READY]

    lines.append("## Ready to book")
    lines.append("")
    if not ready:
        lines.append(_empty_ready_message(result))
        lines.append("")
    for index, rec in enumerate(ready[:limit], start=1):
        lines.extend(_venue_block(index, rec, show_caveats=True))

    if pending:
        lines.append("## Worth a phone call")
        lines.append(
            "These venues fit the taste profile but need a check before booking: "
            "a hard constraint is unverified, or part of the group could not be represented."
        )
        lines.append("")
        for index, rec in enumerate(pending[:limit], start=1):
            lines.extend(_venue_block(index, rec, show_caveats=True))

    if result.rejected:
        lines.append("## Excluded")
        for rec in result.rejected[:limit]:
            reason = rec.reasons[0] if rec.reasons else "hard constraint failed"
            lines.append(f"- **{rec.venue.name}** - {reason}")
        lines.append("")

    if result.unresolved_seeds:
        lines.append("## Taste seeds that could not be grounded")
        for item in result.unresolved_seeds:
            lines.append(
                f"- `{item.seed.query}` ({item.seed.domain.value}, {item.seed.member_id})"
                f" - {item.error or 'no match'}"
            )
        lines.append("")

    if result.uncovered_members:
        lines.append("## Family members with no usable seeds")
        lines.append(", ".join(result.uncovered_members) + " - the ranking cannot claim to cover them.")
        lines.append("")

    lines.append("## Trace")
    for event in result.trace:
        lines.append(f"- `{event.step}` ({event.duration_ms:.0f} ms) - {event.detail}")
    lines.append("")
    lines.append("---")
    lines.append(f"_{DISCLAIMER}_")
    return "\n".join(lines)


def trace_block(result: PlanResult) -> list[str]:
    lines = ["## Trace"]
    for event in result.trace:
        lines.append(f"- `{event.step}` ({event.duration_ms:.0f} ms) - {event.detail}")
    lines.extend(["", "---", f"_{DISCLAIMER}_"])
    return lines


def _venue_block(index: int, rec: Recommendation, show_caveats: bool = False) -> list[str]:
    venue = rec.venue
    candidate = rec.candidate
    lines = [f"### {index}. {venue.name}", ""]
    where = _format_address(venue)
    if where:
        lines.append(f"- **Where:** {where}")
    lines.append(f"- **Bridge score:** {candidate.bridge_score:.0%} (worst-member weighted)")
    if venue.rating is not None:
        lines.append(f"- **Rating:** {venue.rating:.1f}/5")
    if venue.price_level is not None:
        lines.append(f"- **Price level:** {'$' * venue.price_level}")
    if rec.provenance:
        lines.append(f"- **Attribute source:** {rec.provenance}")
    if venue.cuisine:
        snippet = venue.cuisine if len(venue.cuisine) <= 220 else venue.cuisine[:217] + "..."
        lines.append(f"- **Qloo cuisine note:** {snippet}")
    for reason in rec.reasons:
        lines.append(f"- {reason}")
    if show_caveats:
        for caveat in rec.caveats:
            lines.append(f"- _Unverified:_ {caveat}")
    lines.append("")
    return lines


def _format_address(venue: Venue) -> str:
    """Keep the first two address segments plus city; Qloo addresses are long."""
    parts = [part.strip() for part in (venue.address or "").split(",") if part.strip()]
    if not parts:
        return venue.city or ""
    short = parts[:2]
    joined = ", ".join(short)
    if venue.city and venue.city.lower() not in joined.lower():
        joined = f"{joined}, {venue.city}"
    return joined


def to_dict(result: PlanResult) -> dict[str, Any]:
    return {
        "request": result.request_label,
        "location": result.location,
        "data_mode": result.data_mode,
        "generated_at": result.generated_at.isoformat(),
        "recommendations": [_rec_dict(r) for r in result.recommendations],
        "excluded": [_rec_dict(r) for r in result.rejected],
        "unresolved_seeds": [
            {"query": u.seed.query, "domain": u.seed.domain.value, "member": u.seed.member_id, "error": u.error}
            for u in result.unresolved_seeds
        ],
        "uncovered_members": result.uncovered_members,
        "trace": [asdict(event) for event in result.trace],
        "disclaimer": DISCLAIMER,
    }


def _rec_dict(rec: Recommendation) -> dict[str, Any]:
    venue = rec.venue
    candidate = rec.candidate
    return {
        "entity_id": venue.entity_id,
        "name": venue.name,
        "address": venue.address,
        "city": venue.city,
        "rating": venue.rating,
        "price_level": venue.price_level,
        "cuisine": venue.cuisine,
        "status": rec.status.value,
        "bridge_score": round(candidate.bridge_score, 4),
        "final_score": round(candidate.final_score, 4),
        "affinities": {k: round(v, 4) for k, v in candidate.affinities.items()},
        "affinity_evidence": candidate.affinity_evidence,
        "constraints": [
            {
                "constraint": check.constraint,
                "status": check.status.value,
                "detail": check.detail,
                "source": check.evidence.source if check.evidence else None,
                "method": check.evidence.method.value if check.evidence else None,
            }
            for check in candidate.checks
        ],
        "reasons": rec.reasons,
        "caveats": rec.caveats,
        "provenance": rec.provenance,
    }


class OutlineValidationError(ValueError):
    pass


class LLMPhraser:
    """Optional prose layer with a hard grounding check.

    The prompt forbids new venues.  Validation is structural, not prompt-based:
    the response must reference venues by index into the list we supplied, and
    unknown indices or extra venue fields cause a fallback.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def phrase(self, result: PlanResult, limit: int = 5) -> str:
        recs = [r for r in result.recommendations if r.candidate][:limit]
        if not recs:
            return render_markdown(result)
        outline = [
            {
                "index": i,
                "name": r.venue.name,
                "area": r.venue.city or result.location,
                "bridge": round(r.candidate.bridge_score, 3),
                "evidence": r.reasons,
                "caveats": r.caveats,
            }
            for i, r in enumerate(recs, start=1)
        ]
        payload = {
            "model": self.model,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You write short, warm prose for a family dining plan. "
                        "Use ONLY the venues and facts in the JSON provided. "
                        "Never add a venue, address, rating, dietary claim or price. "
                        "Do not restate affinity numbers as percentages of correctness. "
                        "Return JSON: {\"intro\": string, \"notes\": "
                        "[{\"index\": int, \"text\": string}], \"caveat\": string}"
                    ),
                },
                {"role": "user", "content": json.dumps({"venues": outline}, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            self._validate(parsed, len(recs))
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, OutlineValidationError, TimeoutError):
            return render_markdown(result)

        lines = [parsed["intro"].strip(), ""]
        for note in parsed["notes"]:
            rec = recs[note["index"] - 1]
            lines.append(f"**{rec.venue.name}** - {rec.candidate.bridge_score:.0%} bridge  ")
            lines.append(note["text"].strip())
            lines.append("")
        if parsed.get("caveat"):
            lines.append(f"> {parsed['caveat'].strip()}")
        lines.append("")
        lines.extend(trace_block(result))
        return "\n".join(lines)

    @staticmethod
    def _validate(parsed: Any, count: int) -> None:
        if not isinstance(parsed, dict) or "intro" not in parsed or "notes" not in parsed:
            raise OutlineValidationError("missing intro/notes")
        for note in parsed["notes"]:
            if not isinstance(note, dict) or not isinstance(note.get("index"), int):
                raise OutlineValidationError("note without integer index")
            if not 1 <= note["index"] <= count:
                raise OutlineValidationError(f"venue index {note['index']} is out of range")
            if not isinstance(note.get("text"), str):
                raise OutlineValidationError("note text must be a string")
