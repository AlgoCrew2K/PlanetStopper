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

Two independently-overridable knobs by design, both resolved here:
  ADVISOR_SUGGESTION_MODEL: config-suggestion / build-plan generation call
    sites (ai_advisor.request_suggestions,
    advisors.build_plan_generator.generate_build_plans).
  ADVISOR_SYNTHESIS_MODEL: nightly Market Prism council synthesis.
Each knob is an operator override when set; otherwise the Models API resolver
picks the newest active model of the knob's family from a cached listing.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import enum
import logging
import os
import threading
import time  # module attribute on purpose: tests replace model_config.time
from collections.abc import Iterator
from datetime import UTC, datetime

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


logger = logging.getLogger(__name__)

MODEL_UNAVAILABLE_TEXT = "model unavailable"

# The Models API listing is refreshed when the last successful one is older than this.
_RELIST_INTERVAL_SECONDS = 24 * 60 * 60  # seconds
_LIST_PAGE_LIMIT = 1000  # records per page (API maximum)
_MODELS_LIST_TIMEOUT_SECONDS = 10.0  # seconds; bounds the wait while the listing lock is held
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class Knob(enum.Enum):
    """The two operator-overridable model settings; each value is its env var name."""

    SUGGESTION = "ADVISOR_SUGGESTION_MODEL"
    SYNTHESIS = "ADVISOR_SYNTHESIS_MODEL"


_KNOB_FAMILY = {Knob.SUGGESTION: "fable", Knob.SYNTHESIS: "opus"}


@dataclasses.dataclass(frozen=True)
class ModelChoice:
    """A resolved or overridden model, or the unavailable placeholder."""

    model_id: str = ""
    display_name: str = ""
    source: str = "unavailable"
    env_var: str = ""

    @property
    def badge_text(self) -> str:
        """Text for a UI badge: the override id, the display name, or unavailable."""
        if self.source == "override":
            return self.model_id
        if self.source == "resolved":
            return self.display_name
        return MODEL_UNAVAILABLE_TEXT


_UNAVAILABLE = ModelChoice()

# Module-level mutable state; _reset_registry() clears it.
_registry: dict[str, ModelChoice] = {}
_last_success: float | None = None
_attempt_seq = 0
_listing_lock = threading.Lock()
_run_pin: contextvars.ContextVar[dict[str, ModelChoice] | None] = contextvars.ContextVar(
    "model_config_run_pin", default=None
)


def _override(knob: Knob) -> ModelChoice | None:
    value = os.environ.get(knob.value, "").strip()
    if not value:
        return None
    return ModelChoice(model_id=value, display_name=value, source="override", env_var=knob.value)


def _created_at(item: object) -> datetime:
    value = getattr(item, "created_at", None)
    if not isinstance(value, datetime):
        return _EPOCH
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _select(records: list[object], family: str, line_mode: bool) -> ModelChoice:
    """Newest active family record: by `line` if listed, else by `claude-<family>-` prefix."""
    best = None
    for item in records:
        extra = getattr(item, "model_extra", None) or {}
        if line_mode:
            if extra.get("line") != family:
                continue
        elif not str(item.id).startswith(f"claude-{family}-"):
            continue
        if extra.get("lifecycle") not in (None, "active"):
            continue
        if best is None or _created_at(item) > _created_at(best):
            best = item
    if best is None:
        return _UNAVAILABLE
    return ModelChoice(model_id=best.id, display_name=best.display_name, source="resolved")


def _list_all(client: object) -> dict[str, ModelChoice]:
    # One request per page: no SDK retries, so a Retry-After is never waited out under the lock.
    listing_client = client.with_options(max_retries=0, timeout=_MODELS_LIST_TIMEOUT_SECONDS)
    records = list(listing_client.models.list(limit=_LIST_PAGE_LIMIT))
    line_mode = any("line" in (getattr(r, "model_extra", None) or {}) for r in records)
    found = {f: _select(records, f, line_mode) for f in _KNOB_FAMILY.values()}
    return {f: c for f, c in found.items() if c.source == "resolved"}


def _list_locked(client: object) -> None:
    """Run one listing attempt; the caller holds _listing_lock."""
    global _registry, _last_success, _attempt_seq
    try:
        _registry = _list_all(client)
        _last_success = time.monotonic()
    except Exception as exc:
        logger.warning("model listing failed: %s", type(exc).__name__)
        logger.debug("model listing failed", exc_info=True)
    _attempt_seq += 1


def _list_once(client: object, seq_before: int) -> None:
    """One listing attempt shared by every caller that was waiting on it."""
    with _listing_lock:
        if _attempt_seq != seq_before:
            return
        _list_locked(client)


def _is_fresh() -> bool:
    last = _last_success
    return last is not None and time.monotonic() - last < _RELIST_INTERVAL_SECONDS


def _pin(family: str, choice: ModelChoice) -> None:
    pins = _run_pin.get()
    if pins is None:
        return
    if choice.source == "resolved":
        pins[family] = choice
    else:
        pins.pop(family, None)


def resolve(knob: Knob, client: object) -> ModelChoice:
    """Resolve the knob's model; never raises."""
    override = _override(knob)
    if override is not None:
        return override
    family = _KNOB_FAMILY[knob]
    pins = _run_pin.get()
    if pins is not None and family in pins:
        return pins[family]
    seq_before = _attempt_seq
    if not _is_fresh():
        _list_once(client, seq_before)
    choice = _registry.get(family, _UNAVAILABLE)
    _pin(family, choice)
    return choice


def peek(knob: Knob) -> ModelChoice:
    """The cached resolution without listing or locking."""
    return _override(knob) or _registry.get(_KNOB_FAMILY[knob], _UNAVAILABLE)


def invalidate(knob: Knob, model_id: str, client: object) -> ModelChoice:
    """Drop the family's resolution if it is still `model_id` and re-list once."""
    global _registry, _last_success
    family = _KNOB_FAMILY[knob]
    with _listing_lock:  # a listing in flight may be about to replace the entry
        pins = _run_pin.get()
        pinned = pins.get(family) if pins is not None else None
        registered = _registry.get(family)
        current = pinned or registered
        if current is not None and current.model_id != model_id:
            return current  # a late 404 for an already replaced model
        if pinned is not None and registered is not None and registered.model_id != model_id:
            pins[family] = registered  # the process-wide entry is newer than the run's pin
            return registered
        _registry = {f: c for f, c in _registry.items() if f != family}
        _last_success = None  # a failed listing must be retried by the next call
        if pins is not None:
            pins.pop(family, None)
        _list_locked(client)
        choice = _registry.get(family, _UNAVAILABLE)
        _pin(family, choice)
        return choice


@contextlib.contextmanager
def pinned_run() -> Iterator[None]:
    """Keep each family's first resolution for the duration of one run."""
    if _run_pin.get() is not None:
        yield
        return
    token = _run_pin.set({})
    try:
        yield
    finally:
        _run_pin.reset(token)


def _reset_registry() -> None:
    global _registry, _last_success, _attempt_seq
    _registry = {}
    _last_success = None
    _attempt_seq = 0
