"""Shared LLM model-routing accessor(s) for the AI Advisor suite.

AC-16 (operator directive 2026-07-13): every SUGGESTION-producing LLM call
(as opposed to the nightly Market Prism SYNTHESIS call, which keeps its own
independent accessor at ai_advisor.resolve_advisor_model()/ADVISOR_SYNTHESIS_MODEL)
routes through a single named, env-overridable accessor here. Deliberately a
separate top-level module (not folded into ai_advisor.py) so it stays a
zero-dependency import any current or future advisors/* producer can take
without pulling in ai_advisor.py's full surface (requests/pydantic/database/
symphony_logic) or fighting the CC-2 lazy-import convention those modules
already follow for ai_advisor.

Two independently-overridable knobs by design:
  ADVISOR_SUGGESTION_MODEL — this module. Config-suggestion / build-plan
    generation call sites (ai_advisor.request_suggestions,
    advisors.build_plan_generator.generate_build_plans).
  ADVISOR_SYNTHESIS_MODEL — ai_advisor.resolve_advisor_model(). Nightly
    Market Prism council synthesis. Untouched by this module.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import time  # noqa: F401  (clock seam: tests patch model_config.time)

_ADVISOR_SUGGESTION_MODEL_ENV_VAR = "ADVISOR_SUGGESTION_MODEL"
_ADVISOR_SUGGESTION_MODEL_DEFAULT = "claude-fable-5"


def get_advisor_suggestion_model() -> str:
    """Return the configured suggestion-producing model ID.

    Reads ADVISOR_SUGGESTION_MODEL at call time so a daemon restart is not
    required to pick up a change (mirrors ai_advisor.resolve_advisor_model()'s
    pattern). Default: claude-fable-5 (operator directive 2026-07-13).
    Independent of ADVISOR_SYNTHESIS_MODEL — setting one never affects the
    other.
    """
    return os.environ.get(_ADVISOR_SUGGESTION_MODEL_ENV_VAR, _ADVISOR_SUGGESTION_MODEL_DEFAULT)


# --- import-signature stubs for the model-resolution cycle; the implementer replaces every body ---
MODEL_UNAVAILABLE_TEXT = "model unavailable"


class Knob(enum.Enum):
    SUGGESTION = "ADVISOR_SUGGESTION_MODEL"
    SYNTHESIS = "ADVISOR_SYNTHESIS_MODEL"


@dataclasses.dataclass(frozen=True)
class ModelChoice:
    model_id: str = ""
    display_name: str = ""
    source: str = "unavailable"
    env_var: str = ""
    badge_text = ""  # stub: becomes a property


def resolve(knob, client):
    return ModelChoice()


def peek(knob):
    return ModelChoice()


def invalidate(knob, model_id, client):
    return ModelChoice()


@contextlib.contextmanager
def pinned_run():
    yield


def _reset_registry():
    return None
