"""Configuration is explicit and fails closed.

There is deliberately no ``DEMO_KEY`` default: an application that silently
switches to fabricated data when credentials are missing is worse than one
that refuses to start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RankingWeights:
    """Weights for the final ordering. Kept explicit so they can be audited."""

    bridge: float = 0.70
    quality: float = 0.20
    verification: float = 0.10

    def __post_init__(self) -> None:
        total = self.bridge + self.quality + self.verification
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"ranking weights must sum to 1.0, got {total}")


@dataclass(frozen=True)
class Settings:
    qloo_api_key: str | None = None
    qloo_base_url: str = "https://api.qloo.com"
    request_timeout_s: float = 10.0
    max_retries: int = 3
    cache_ttl_s: float = 900.0
    default_radius_m: int = 8_000
    max_affinity_candidates: int = 40
    max_search_take: int = 5
    #: Minimum lexical overlap between a taste query and a Qloo match (0..1).
    min_name_match: float = 0.5
    #: Place tags used to keep insights results to dining venues.
    dining_tags: tuple[str, ...] = ("urn:tag:category:place:restaurant",)
    require_verification: bool = True
    ranking: RankingWeights = RankingWeights()
    #: Fraction of the "worst member" in the bridge score. Higher = more equal.
    fairness_weight: float = 0.6
    #: A venue absent from a member's result list is assumed to sit at this
    #: fraction of that member's weakest *returned* affinity (a lower bound).
    missing_affinity_factor: float = 0.5

    def __post_init__(self) -> None:
        if self.default_radius_m < 0 or self.default_radius_m > 800_000:
            raise ValueError("radius must be within 0..800000 metres (Qloo limit)")
        if not 0 < self.max_affinity_candidates <= 50:
            raise ValueError("Qloo returns at most 50 insights entities per page")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)

        def flag(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            qloo_api_key=env.get("QLOO_API_KEY") or None,
            qloo_base_url=env.get("QLOO_BASE_URL", "https://api.qloo.com").rstrip("/"),
            request_timeout_s=float(env.get("QLOO_TIMEOUT_S", "10")),
            max_retries=int(env.get("QLOO_MAX_RETRIES", "3")),
            cache_ttl_s=float(env.get("QLOO_CACHE_TTL_S", "900")),
            default_radius_m=int(env.get("SOCIO_RADIUS_M", "8000")),
            min_name_match=float(env.get("SOCIO_MIN_NAME_MATCH", "0.5")),
            dining_tags=tuple(
                tag.strip()
                for tag in env.get("SOCIO_DINING_TAGS", "urn:tag:category:place:restaurant").split(",")
                if tag.strip()
            ),
            require_verification=flag("SOCIO_REQUIRE_VERIFICATION", True),
        )

    def require_api_key(self) -> str:
        if not self.qloo_api_key:
            raise ConfigError(
                "QLOO_API_KEY is not set. SocioSense cannot ground recommendations "
                "without Qloo; use --demo for the explicitly-labelled fixture mode."
            )
        return self.qloo_api_key


def load_profile(path: str | Path) -> dict:
    import json

    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_env_file(path: str | Path = ".env") -> None:
    """Load KEY=VALUE pairs from ``.env`` without overriding real env vars."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
