"""Command line entry point: ``python -m sociosense ...``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent import SocioSenseAgent
from .clients.facts import JsonFactsProvider, NullFactsProvider
from .clients.qloo import QlooClient, QlooError
from .config import ConfigError, Settings, load_env_file, load_profile
from .demo import DEFAULT_FIXTURE, FixtureQlooTransport
from .models import Domain, Member, Requirements, TasteSeed
from .synth import LLMPhraser, render_markdown, to_dict
from .transport import HttpTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sociosense", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="build a grounded family plan")
    plan.add_argument("--profile", required=True, type=Path, help="family profile JSON")
    plan.add_argument("--facts", type=Path, help="verification records for venue attributes")
    plan.add_argument("--demo", action="store_true", help="use the synthetic fixture taste graph")
    plan.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="fixture graph path")
    plan.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    plan.add_argument("--out", type=Path, help="also write the report to this file")
    plan.add_argument("--limit", type=int, default=7, help="venues shown per section")
    plan.add_argument("--llm", action="store_true", help="polish prose with an OpenAI-compatible model")
    plan.add_argument("--model", default="gpt-4o-mini")

    doctor = sub.add_parser("doctor", help="verify credentials and endpoint access")
    doctor.add_argument("--location", default="Bengaluru", help="locality used for the insights check")
    return parser


def build_members(profile: dict) -> list[Member]:
    members: list[Member] = []
    for raw in profile.get("members", []):
        seeds = tuple(
            TasteSeed(
                query=taste["query"],
                domain=Domain.from_text(taste.get("domain", "movie")),
                member_id=raw["id"],
                kind=taste.get("kind", "entity"),
            )
            for taste in raw.get("tastes", [])
        )
        members.append(
            Member(
                member_id=raw["id"],
                label=raw.get("label", raw["id"]),
                seeds=seeds,
                age_band=raw.get("age_band"),
            )
        )
    if not members:
        raise ValueError("profile contains no members")
    return members


def build_requirements(profile: dict, settings: Settings) -> Requirements:
    raw = profile.get("requirements")
    if raw is None:
        # Backwards compatibility with the original blueprint's key names.
        legacy = profile.get("senior_health_needs", {})
        raw = {
            "sodium": "low" if legacy.get("low_sodium") else None,
            "max_noise": "low" if legacy.get("low_noise") else None,
            "texture": "soft" if legacy.get("easy_to_chew") else None,
            "step_free": bool(legacy.get("step_free", False)),
        }
    return Requirements(
        sodium=raw.get("sodium"),
        max_noise=raw.get("max_noise"),
        texture=raw.get("texture"),
        step_free=bool(raw.get("step_free", False)),
        max_price_level=raw.get("max_price_level", 3),
        radius_m=raw.get("radius_m") if raw.get("radius_m") is not None else settings.default_radius_m,
        require_verification=settings.require_verification,
        cuisine=raw.get("cuisine"),
    )


def _client(settings: Settings, demo: bool, fixture: Path | None) -> tuple[QlooClient, str]:
    if demo:
        transport = FixtureQlooTransport(fixture or DEFAULT_FIXTURE)
        return QlooClient("fixture", transport=transport, cache_ttl_s=0), "fixture"
    return (
        QlooClient(
            settings.require_api_key(),
            base_url=settings.qloo_base_url,
            transport=HttpTransport(
                timeout_s=settings.request_timeout_s,
                max_retries=settings.max_retries,
            ),
            timeout_s=settings.request_timeout_s,
            max_retries=settings.max_retries,
            cache_ttl_s=settings.cache_ttl_s,
        ),
        "live",
    )


def run_plan(args: argparse.Namespace, settings: Settings) -> int:
    profile = load_profile(args.profile)
    try:
        client, data_mode = _client(settings, args.demo, args.fixture)
    except ConfigError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    facts = JsonFactsProvider(args.facts) if args.facts else NullFactsProvider()
    agent = SocioSenseAgent(client, facts, settings, data_mode=data_mode)

    result = agent.plan(
        label=profile.get("label", "Family outing"),
        members=build_members(profile),
        requirements=build_requirements(profile, settings),
        location=profile.get("location", ""),
    )

    if args.llm:
        phraser = LLMPhraser(
            os.environ.get("OPENAI_API_KEY", ""),
            model=args.model,
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        report = phraser.phrase(result, limit=args.limit)
    else:
        report = render_markdown(result, limit=args.limit)

    if args.json:
        print(json.dumps(to_dict(result), indent=2, ensure_ascii=False))
    else:
        print(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")

    return 0 if result.recommendations else 2


def run_doctor(args: argparse.Namespace, settings: Settings) -> int:
    key = settings.require_api_key()
    masked = f"{key[:8]}...{key[-4:]}" if len(key) > 14 else "set"
    print(f"Qloo endpoint: {settings.qloo_base_url}")
    print(f"API key:       {masked}")

    client = QlooClient(
        key,
        base_url=settings.qloo_base_url,
        transport=HttpTransport(timeout_s=settings.request_timeout_s, max_retries=settings.max_retries),
        cache_ttl_s=0,
    )
    failures = 0

    try:
        entities = client.search_entities("The Bear", domains=[Domain.TV_SHOW], take=1)
        if entities:
            print(f"[ok]   search          {entities[0].name} -> {entities[0].entity_id}")
        else:
            failures += 1
            print("[fail] search          no results")
    except QlooError as exc:
        failures += 1
        print(f"[fail] search          {exc}")

    try:
        tags = client.search_tags("mediterranean", take=3)
        if tags:
            print(f"[ok]   tags            {tags[0].name} -> {tags[0].tag_id}")
        else:
            failures += 1
            print("[fail] tags            no results")
    except QlooError as exc:
        failures += 1
        print(f"[fail] tags            {exc}")

    try:
        hits = client.place_affinities(
            [],
            tag_ids=[tags[0].tag_id] if tags else [],
            location=args.location,
            take=5,
        )
        if hits:
            top = hits[0]
            print(
                f"[ok]   insights        {len(hits)} dining venues near {args.location}; "
                f"top: {top.venue.name} ({top.affinity:.2f})"
            )
        else:
            failures += 1
            print(f"[fail] insights        no dining venues near {args.location}")
    except QlooError as exc:
        failures += 1
        print(f"[fail] insights        {exc}")

    return 0 if failures == 0 else 2


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()

    try:
        if args.command == "doctor":
            return run_doctor(args, settings)
        return run_plan(args, settings)
    except ConfigError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
