"""The agent.

This is an orchestration agent, not a chat loop: each step is a typed tool call
with a validated contract, and the trace records what ran and what came back.
That is deliberate - a "ReAct" agent that can decide to skip the health check
is not a safety property, it is a risk.  The LLM, when enabled, only writes
prose over the structured result (see ``synth.LLMPhraser``).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Sequence

from .clients.qloo import AffinityHit, QlooClient
from .clients.facts import FactsProvider
from .config import Settings
from .constraints import Constraint, build_constraints, evaluate
from .matching import name_match_score
from .models import (
    Candidate,
    Domain,
    Member,
    PlanResult,
    Recommendation,
    RecommendationStatus,
    Requirements,
    ResolvedSeed,
    TasteSeed,
    TraceEvent,
)
from .scoring import bridge_score, final_score, quality_score, verification_score

MAX_SEEDS_PER_CALL = 20


class SocioSenseAgent:
    def __init__(
        self,
        qloo: QlooClient,
        facts: FactsProvider,
        settings: Settings | None = None,
        *,
        data_mode: str = "live",
    ) -> None:
        self.qloo = qloo
        self.facts = facts
        self.settings = settings or Settings()
        self.data_mode = data_mode

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        *,
        label: str,
        members: Sequence[Member],
        requirements: Requirements,
        location: str,
    ) -> PlanResult:
        trace: list[TraceEvent] = []
        unresolved: list[ResolvedSeed] = []

        resolved_members, unresolved = self._resolve(members, trace)
        covered = [m for m in resolved_members if any(s.resolved_id for s in m.seeds)]
        uncovered = [m.member_id for m in resolved_members if m not in covered]

        candidates = self._collect_affinities(covered, requirements, location, trace, unresolved)
        self._enrich(candidates, trace)
        constraints = build_constraints(requirements)
        self._evaluate(candidates, constraints, trace)

        recommendations, rejected = self._rank_and_split(
            candidates, covered, requirements, trace
        )

        if uncovered:
            for rec in recommendations:
                if rec.status is RecommendationStatus.READY:
                    rec.status = RecommendationStatus.UNCOVERED_MEMBER
                    rec.caveats.append(
                        "ranking cannot represent: " + ", ".join(uncovered)
                    )

        return PlanResult(
            request_label=label,
            location=location,
            recommendations=recommendations,
            rejected=rejected,
            unresolved_seeds=unresolved,
            uncovered_members=uncovered,
            trace=trace,
            data_mode=self.data_mode,
        )

    # ----------------------------------------------------------------- steps
    def _resolve(
        self, members: Sequence[Member], trace: list[TraceEvent]
    ) -> tuple[list[Member], list[ResolvedSeed]]:
        started = time.perf_counter()
        issues: list[ResolvedSeed] = []
        resolved_count = 0
        rejected_for_name = 0

        # Resolution is network-bound and independent per seed, so it runs on a
        # bounded pool.  executor.map preserves input order, which keeps each
        # member's seed order (and therefore the report) deterministic.
        items = [(index, seed) for index, member in enumerate(members) for seed in member.seeds]
        workers = max(1, min(self.settings.resolve_workers, len(items) or 1))

        def resolve_item(item: tuple[int, TasteSeed]):
            index, seed = item
            if seed.kind == "tag":
                match, error = self._resolve_tag(seed)
            else:
                match, error = self._resolve_entity(seed)
            return index, seed, match, error

        if workers == 1:
            results = [resolve_item(item) for item in items]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(resolve_item, items))

        member_seeds: dict[int, list[TasteSeed]] = {index: [] for index in range(len(members))}
        for index, seed, match, error in results:
            if match is None:
                member_seeds[index].append(seed)
                issues.append(ResolvedSeed(seed=seed, matches=0, error=error))
                if error and error.startswith("weak name match"):
                    rejected_for_name += 1
                continue

            resolved_id, name, domain, score = match
            resolved_count += 1
            member_seeds[index].append(
                replace(
                    seed,
                    resolved_id=resolved_id,
                    resolved_name=name,
                    resolved_domain=domain or seed.domain,
                    match_score=score,
                )
            )

        resolved_members = [
            replace(member, seeds=tuple(member_seeds[index]))
            for index, member in enumerate(members)
        ]

        trace.append(
            TraceEvent(
                step="qloo.resolve_seeds",
                detail=f"resolved {resolved_count}/{len(items)} taste seeds",
                payload={
                    "unresolved": [i.seed.query for i in issues],
                    "rejected_for_weak_name_match": rejected_for_name,
                    "workers": workers,
                },
                duration_ms=_ms(started),
            )
        )
        return resolved_members, issues

    def _resolve_entity(self, seed: TasteSeed):
        try:
            matches = self.qloo.search_entities(
                seed.query, domains=[seed.domain], take=self.settings.max_search_take
            )
        except Exception as exc:  # surfaced, never replaced with a fake ID
            return None, str(exc)
        if not matches:
            return None, "no entity match"

        typed = [m for m in matches if m.domain is None or m.domain is seed.domain] or matches
        scored = [(name_match_score(seed.query, m.name), m) for m in typed]
        score, chosen = max(scored, key=lambda pair: pair[0])
        if score < self.settings.min_name_match:
            return None, (
                f"weak name match: best was '{chosen.name}' ({score:.2f}); "
                "try a specific title or artist"
            )
        return (chosen.entity_id, chosen.name, chosen.domain, score), None

    def _resolve_tag(self, seed: TasteSeed):
        try:
            matches = self.qloo.search_tags(seed.query, take=self.settings.max_search_take + 3)
        except Exception as exc:
            return None, str(exc)
        if not matches:
            return None, "no tag match"

        scored = [(name_match_score(seed.query, m.name), m) for m in matches]
        scored = [(score, tag) for score, tag in scored if score >= self.settings.min_name_match]
        if not scored:
            best = max(name_match_score(seed.query, m.name) for m in matches)
            return None, f"weak tag match ({best:.2f}); try a cuisine or genre keyword"
        score, chosen = max(
            scored,
            key=lambda pair: (
                pair[0],
                _tag_priority(pair[1].type),
                pair[1].popularity or 0.0,
            ),
        )
        return (chosen.tag_id, chosen.name, seed.domain, score), None

    def _resolve_cuisine(self, cuisine: str | None, trace: list[TraceEvent]) -> str | None:
        if not cuisine:
            return None
        seed = TasteSeed(query=cuisine, domain=Domain.PLACE, member_id="requirements", kind="tag")
        match, error = self._resolve_tag(seed)
        trace.append(
            TraceEvent(
                step="qloo.resolve_cuisine",
                detail=(
                    f"cuisine filter '{cuisine}' -> {match[1]}" if match else f"cuisine filter unresolved: {error}"
                ),
                payload={"tag_id": match[0] if match else None},
            )
        )
        return match[0] if match else None

    def _collect_affinities(
        self,
        members: Sequence[Member],
        requirements: Requirements,
        location: str,
        trace: list[TraceEvent],
        unresolved: list[ResolvedSeed],
    ) -> dict[str, Candidate]:
        pool: dict[str, Candidate] = {}
        per_member: dict[str, list[AffinityHit]] = {}
        radius = requirements.radius_m if requirements.radius_m is not None else self.settings.default_radius_m
        cuisine_tag = self._resolve_cuisine(requirements.cuisine, trace)

        for member in members:
            seed_ids = [s.resolved_id for s in member.seeds if s.resolved_id and s.kind != "tag"]
            tag_ids = [s.resolved_id for s in member.seeds if s.resolved_id and s.kind == "tag"]
            started = time.perf_counter()
            try:
                hits = self.qloo.place_affinities(
                    seed_ids[:MAX_SEEDS_PER_CALL],
                    tag_ids=tag_ids[:MAX_SEEDS_PER_CALL],
                    location=location,
                    radius_m=radius,
                    take=self.settings.max_affinity_candidates,
                    max_price_level=requirements.max_price_level,
                    dining_tags=self.settings.dining_tags,
                    filter_tags=[cuisine_tag] if cuisine_tag else None,
                )
            except Exception as exc:
                # A failed member must not silently become "no preference".
                domain = member.seeds[0].domain if member.seeds else Domain.PLACE
                unresolved.append(
                    ResolvedSeed(
                        seed=TasteSeed("(member profile)", domain, member.member_id),
                        matches=0,
                        error=f"affinity lookup failed: {exc}",
                    )
                )
                hits = []
            per_member[member.member_id] = hits
            trace.append(
                TraceEvent(
                    step="qloo.place_affinities",
                    detail=f"{len(hits)} places for {member.member_id}",
                    payload={"seeds": len(seed_ids), "location": location, "radius_m": radius},
                    duration_ms=_ms(started),
                )
            )
            for hit in hits:
                candidate = pool.get(hit.venue.entity_id)
                if candidate is None:
                    candidate = Candidate(venue=hit.venue)
                    pool[hit.venue.entity_id] = candidate
                candidate.affinities[member.member_id] = hit.affinity
                candidate.affinity_evidence[member.member_id] = "observed"
                if hit.explainability:
                    candidate.explainability[member.member_id] = hit.explainability

        # Members whose result list did not include this venue are not scored as
        # "zero affinity = dislike"; we record a conservative lower bound and are
        # explicit about it in the evidence.
        for member in members:
            member_hits = per_member.get(member.member_id) or []
            if not member_hits:
                continue
            floor = min(hit.affinity for hit in member_hits)
            assumed = floor * self.settings.missing_affinity_factor
            for candidate in pool.values():
                if member.member_id not in candidate.affinities:
                    candidate.affinities[member.member_id] = assumed
                    candidate.affinity_evidence[member.member_id] = "lower_bound"

        return pool

    def _enrich(self, pool: dict[str, Candidate], trace: list[TraceEvent]) -> None:
        started = time.perf_counter()
        verified = 0
        for candidate in pool.values():
            candidate.facts = self.facts.lookup(candidate.venue)
            if candidate.facts:
                verified += 1
        trace.append(
            TraceEvent(
                step="facts.lookup",
                detail=f"{verified}/{len(pool)} venues have an attribute source",
                payload={
                    "provider": getattr(self.facts, "name", "unknown"),
                    "matched": verified,
                    "total": len(pool),
                },
                duration_ms=_ms(started),
            )
        )

    def _evaluate(
        self, pool: dict[str, Candidate], constraints: Sequence[Constraint], trace: list[TraceEvent]
    ) -> None:
        started = time.perf_counter()
        for candidate in pool.values():
            candidate.checks = evaluate(candidate, constraints)
        trace.append(
            TraceEvent(
                step="constraints.evaluate",
                detail=f"{len(constraints)} hard constraints against {len(pool)} venues",
                payload={"constraints": [c.label for c in constraints]},
                duration_ms=_ms(started),
            )
        )

    def _rank_and_split(
        self,
        pool: dict[str, Candidate],
        covered: Sequence[Member],
        requirements: Requirements,
        trace: list[TraceEvent],
    ) -> tuple[list[Recommendation], list[Recommendation]]:
        member_ids = [m.member_id for m in covered]
        seed_names = {s.resolved_id: s.query for m in covered for s in m.seeds if s.resolved_id}

        for candidate in pool.values():
            candidate.bridge_score = bridge_score(
                candidate.affinities, member_ids, self.settings.fairness_weight
            )
            candidate.quality_score = quality_score(candidate.venue.rating)
            candidate.verification_coverage = verification_score(candidate.checks)
            candidate.final_score = final_score(candidate, self.settings.ranking)

        ready: list[Recommendation] = []
        rejected: list[Recommendation] = []
        for candidate in sorted(pool.values(), key=lambda c: c.final_score, reverse=True):
            rec = self._to_recommendation(candidate, covered, requirements, seed_names)
            if rec.status is RecommendationStatus.EXCLUDED:
                rejected.append(rec)
            else:
                ready.append(rec)

        trace.append(
            TraceEvent(
                step="rank",
                detail=f"{len(ready)} recommended, {len(rejected)} excluded",
                payload={"weights": vars(self.settings.ranking)},
                duration_ms=0.0,
            )
        )
        return ready, rejected

    def _to_recommendation(
        self,
        candidate: Candidate,
        covered: Sequence[Member],
        requirements: Requirements,
        seed_names: dict[str, str],
    ) -> Recommendation:
        failures = candidate.failures
        unknowns = candidate.unknowns
        reasons: list[str] = []
        caveats: list[str] = []

        if failures:
            return Recommendation(
                candidate=candidate,
                status=RecommendationStatus.EXCLUDED,
                reasons=[f"excluded: {f.constraint} - {f.detail}" for f in failures],
                caveats=[],
            )

        if unknowns:
            caveats.extend(f"{u.constraint}: {u.detail}" for u in unknowns)
            status = (
                RecommendationStatus.NEEDS_CONFIRMATION
                if requirements.require_verification
                else RecommendationStatus.READY
            )
        else:
            status = RecommendationStatus.READY

        for member in covered:
            value = candidate.affinities.get(member.member_id)
            if value is None:
                continue
            evidence = candidate.affinity_evidence.get(member.member_id)
            qualifier = "" if evidence == "observed" else " (estimated lower bound)"
            reasons.append(
                f"{member.label}: {value:.0%} affinity{qualifier} "
                f"from {self._top_seed(member.member_id, candidate, seed_names)}"
            )

        if candidate.facts:
            provenance = candidate.facts.provider
        else:
            provenance = None
            caveats.append("no venue-level attribute source configured")

        return Recommendation(
            candidate=candidate,
            status=status,
            reasons=reasons,
            caveats=caveats,
            provenance=provenance,
        )

    @staticmethod
    def _top_seed(member_id: str, candidate: Candidate, seed_names: dict[str, str]) -> str:
        scores = candidate.explainability.get(member_id) or {}
        if not scores:
            return "the group's taste profile"
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:2]
        labels = [seed_names.get(ident, ident) for ident, _ in ranked]
        return " + ".join(f"'{label}'" for label in labels)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _tag_priority(tag_type: str) -> int:
    order = (
        "urn:tag:genre:place",
        "urn:tag:cuisine:qloo",
        "urn:tag:category:place",
    )
    try:
        return len(order) - order.index(tag_type)
    except ValueError:
        return 0
