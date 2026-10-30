"""SocioSense - culturally grounded family outing planner on top of Qloo."""

from .agent import SocioSenseAgent
from .config import ConfigError, RankingWeights, Settings, load_profile
from .constraints import build_constraints
from .models import (
    Candidate,
    CheckResult,
    CheckStatus,
    Claim,
    ClaimMethod,
    Domain,
    Member,
    PlanResult,
    Recommendation,
    RecommendationStatus,
    Requirements,
    TasteSeed,
    Venue,
)
from .synth import LLMPhraser, render_markdown, to_dict

__version__ = "0.2.0"

__all__ = [
    "Candidate",
    "CheckResult",
    "CheckStatus",
    "Claim",
    "ClaimMethod",
    "ConfigError",
    "Domain",
    "LLMPhraser",
    "Member",
    "PlanResult",
    "RankingWeights",
    "Recommendation",
    "RecommendationStatus",
    "Requirements",
    "Settings",
    "SocioSenseAgent",
    "TasteSeed",
    "Venue",
    "build_constraints",
    "load_profile",
    "render_markdown",
    "to_dict",
]
