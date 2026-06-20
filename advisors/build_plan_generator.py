"""Stub — implementation pending (TDD RED phase). Component 2 + 2b.

advisors/build_plan_generator.py — the Opus Build-Plan Generator. This is an inert
RED-phase stub: it exposes the public surface the tests resolve (the four-value
Objective enum, named constants, the _build_client seam, and the generate/admit/pool
functions) but contains NO real logic. The implementer (sb2-impl) fills these in to
turn the RED tests GREEN per .claude/tdd-handoff.md and the canonical build-plan DSL.
"""

from __future__ import annotations

import enum

# Reuse the existing community cap so the bound is single-sourced (the engine already
# defines MAX_COMMUNITY_CANDIDATES_PER_RUN); re-export here for the AC-12 bound tests.
from advisors.strategy_builder_engine import MAX_COMMUNITY_CANDIDATES_PER_RUN  # noqa: F401

# AC-10 named tunable constant.
N_PLANS_PER_OBJECTIVE: int = 12

# AC-13 explicit provenance tags.
PROVENANCE_BUILT_NEW = "built-new"
PROVENANCE_ATLAS_SUGGESTED = "atlas-suggested"


class Objective(enum.Enum):
    """Four-value objective enum (AC-8). Stub — values pinned, logic pending."""

    diversify = "diversify"
    cut_drawdown = "cut_drawdown"
    lift_risk_adjusted = "lift_risk_adjusted"
    volatility_mitigation = "volatility_mitigation"


def _build_client():  # pragma: no cover - stub
    """SDK factory seam (mirrors ai_advisor._build_client). Stub — patched in tests."""
    raise NotImplementedError("build_plan_generator._build_client")


def plan_tickers(plan):  # pragma: no cover - stub
    """Walk a build-plan and return every referenced ticker. Stub."""
    raise NotImplementedError("plan_tickers")


def generate_build_plans(objective, membership_set, *, n_plans=N_PLANS_PER_OBJECTIVE):  # noqa: ARG001
    """Generate N objective-shaped build-plans. Stub — returns a trivially-wrong result."""
    raise NotImplementedError("generate_build_plans")


def admit_community_candidates(community_result, objective, *, max_candidates=None):  # noqa: ARG001
    """Objective-matched Atlas admission. Stub."""
    raise NotImplementedError("admit_community_candidates")


def load_atlas_candidates(objective, *, max_candidates=None):  # noqa: ARG001
    """Load + admit community strategies via the weekly cache. Stub."""
    raise NotImplementedError("load_atlas_candidates")


def pool_candidates(built_new, atlas_suggested):  # noqa: ARG001
    """Pool built-new + atlas-suggested into one provenance-preserving list. Stub."""
    raise NotImplementedError("pool_candidates")
