"""Local web dashboard: static assets plus a JSON API over the same pipeline.

The CLI is the source of truth; this module only adds transport.  It is
loopback-only by default, has no third-party dependencies, and never invents
data: every response comes from ``SocioSenseAgent.plan``.
"""

from __future__ import annotations

import json
import webbrowser
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .agent import SocioSenseAgent
from .cli import build_client, build_members, build_requirements
from .clients.facts import JsonFactsProvider, NullFactsProvider
from .config import Settings, load_profile
from .synth import to_dict

WEB_ROOT = Path(__file__).with_name("web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
CONSTRAINTS = ("sodium", "noise", "texture", "step_free")
MAX_TEXT = 120


def parse_constraints(raw: str | None, profile: Mapping[str, Any]) -> set[str]:
    if raw is None:
        requirements = profile.get("requirements") or {}
        active = set()
        if requirements.get("sodium"):
            active.add("sodium")
        if requirements.get("max_noise"):
            active.add("noise")
        if requirements.get("texture"):
            active.add("texture")
        if requirements.get("step_free"):
            active.add("step_free")
        return active
    return {part.strip() for part in raw.split(",") if part.strip() in CONSTRAINTS}


def requirements_for(
    profile: Mapping[str, Any],
    settings: Settings,
    active: set[str],
    cuisine: str | None,
):
    base = build_requirements(dict(profile), settings)
    return replace(
        base,
        sodium="low" if "sodium" in active else None,
        max_noise="low" if "noise" in active else None,
        texture="soft" if "texture" in active else None,
        step_free="step_free" in active,
        cuisine=cuisine,
    )


def profile_payload(profile: Mapping[str, Any]) -> dict[str, Any]:
    requirements = profile.get("requirements") or {}
    return {
        "label": profile.get("label", "Family outing"),
        "location": profile.get("location", ""),
        "cuisine": requirements.get("cuisine"),
        "members": [
            {
                "id": member["id"],
                "label": member.get("label", member["id"]),
                "age_band": member.get("age_band"),
                "tastes": [
                    {
                        "query": taste["query"],
                        "domain": taste.get("domain", "movie"),
                        "kind": taste.get("kind", "entity"),
                    }
                    for taste in member.get("tastes", [])
                ],
            }
            for member in profile.get("members", [])
        ],
        "requirements": {
            "sodium": bool(requirements.get("sodium")),
            "noise": bool(requirements.get("max_noise")),
            "texture": bool(requirements.get("texture")),
            "step_free": bool(requirements.get("step_free")),
            "max_price_level": requirements.get("max_price_level", 3),
            "radius_m": requirements.get("radius_m"),
        },
    }


def make_handler(
    profile: Mapping[str, Any], agent: SocioSenseAgent, settings: Settings
) -> type[BaseHTTPRequestHandler]:
    label = profile.get("label", "Family outing")

    class Handler(BaseHTTPRequestHandler):
        server_version = "SocioSense/0.3"

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            parsed = urlparse(self.path)
            if parsed.path == "/api/profile":
                self._send_json(profile_payload(profile))
            elif parsed.path == "/api/plan":
                self._plan(parsed.query)
            elif parsed.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            elif parsed.path in ASSETS:
                self._send_asset(*ASSETS[parsed.path])
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _plan(self, query: str) -> None:
            params = parse_qs(query, keep_blank_values=True)
            active = parse_constraints(_first(params, "constraints"), profile)
            location = (_first(params, "location") or profile.get("location", "")).strip()[:MAX_TEXT]
            cuisine = _first(params, "cuisine")
            if cuisine is None:
                cuisine = (profile.get("requirements") or {}).get("cuisine")
            else:
                cuisine = cuisine.strip()[:MAX_TEXT] or None

            requirements = requirements_for(profile, settings, active, cuisine)
            try:
                result = agent.plan(
                    label=label,
                    members=build_members(dict(profile)),
                    requirements=requirements,
                    location=location,
                )
            except Exception as exc:  # surfaced to the UI, never replaced with data
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            payload = to_dict(result)
            payload["label"] = label
            payload["cuisine"] = cuisine
            payload["active_constraints"] = sorted(active)
            payload["members"] = profile_payload(profile)["members"]
            self._send_json(payload)

        def _send_asset(self, name: str, content_type: str) -> None:
            path = WEB_ROOT / name
            if not path.is_file():
                self._send_json({"error": f"missing asset {name}"}, HTTPStatus.NOT_FOUND)
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"  {self.address_string()} {fmt % args}")

    return Handler


def create_server(
    profile: Mapping[str, Any],
    agent: SocioSenseAgent,
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(profile, agent, settings))
    httpd.daemon_threads = True
    return httpd


def run_serve(args: Any, settings: Settings) -> int:
    profile = load_profile(args.profile)
    client, data_mode = build_client(settings, args.demo, args.fixture)
    facts = JsonFactsProvider(args.facts) if args.facts else NullFactsProvider()
    agent = SocioSenseAgent(client, facts, settings, data_mode=data_mode)

    httpd = create_server(profile, agent, settings, host=args.host, port=args.port)
    host, port = httpd.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"SocioSense dashboard: {url}")
    print(f"Data mode: {data_mode}  |  profile: {args.profile}")
    print("Press Ctrl+C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def _first(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None
