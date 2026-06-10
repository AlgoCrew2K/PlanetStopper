"""Claude-backed config advisor — context assembly + structured-output client.

Cycle C1 scope: assemble a prompt-ready context blob for a symphony (or the
global surface), call Claude for structured config suggestions, and parse the
response. The *safety gates* on the suggestions (allowlist enforcement on what
Claude emits, risk-direction cross-check, OOS re-validation) are C2.

Real-money-critical input governance is in C1 scope: ``assemble_advisor_context``
reads a CURATED ALLOWLIST of config values — never ``dict(os.environ)`` and
never a raw ``.env`` dump. No credential, account id, safety flag, or
methodology knob may ever enter the context that reaches Claude.

The feature is on-demand operator-assist: a Claude call failure is "no
suggestion this click", zero engine impact. ``request_suggestions`` therefore
NEVER raises — every failure mode degrades to ``(None, error_message)``.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel

import database
import symphony_logic

logger = logging.getLogger(__name__)

# The 6 Optuna search-space keys — the validation contract in autotuner.py.
# Duplicated here as a frozenset literal (kept in sync with
# OPTUNA_SEARCH_SPACE_KEYS) rather than imported: importing
# ``autotuner`` pulls in optuna + joblib, whose process-pool/resource-tracker
# import side effects interact badly with the anthropic SDK import and pytest's
# output capture. The set is small, stable, and asserted against the live
# autotuner contract by the C1 test suite.
# Vars in database.DEFAULT_LOCKED_VARS are excluded — they are never
# suggested by Optuna and must not be adopted via the AI advisor.
_OPTUNA_SEARCH_SPACE_KEYS = frozenset(
    {
        "TAKE_PROFIT_MC_PCT",
        "VWAP_CROSS_HWM_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "MAX_PARABOLIC_SQUEEZE",
    }
)

# ---------------------------------------------------------------------------
# Model + SDK configuration.
# ---------------------------------------------------------------------------

# Opus 4.7 — analytical reasoning over quant data (claude-api-mechanics.md §2).
_CLAUDE_MODEL = "claude-opus-4-7"
_MAX_TOKENS = 2048
# Explicit client-side timeout — never rely on the SDK/urllib3 default.
_REQUEST_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Suggestible config surface — the 7-item ALLOWLIST (config-surface.md §1).
# An allowlist, not a denylist: anything not enumerated here is structurally
# excluded from the context. The 6 Optuna search-space keys are read from
# _OPTUNA_SEARCH_SPACE_KEYS so this never drifts from the optimizer.
# ---------------------------------------------------------------------------

# The one strategy param Optuna structurally never tunes — hand-set only.
_UNTUNED_SUGGESTIBLE_KEY = "MAX_SQUEEZE_FLOOR"

# Per-param: one-line definition + risk polarity (does RAISING it loosen or
# tighten risk?). Element 1 of the 8 must-have prompt elements
# (prompt-methodology.md §1.1). Claude cannot reason about whether a suggestion
# is safe without the polarity.
# NOTE: TRIGGER_THRESHOLD_PCT is included here for prompt context (the operator
# sees it in the suggestible surface as a locked param), but it is NOT in
# _OPTUNA_SEARCH_SPACE_KEYS and NOT in _SUGGESTIBLE_ALLOWLIST — it is a locked
# var and must never appear in the allowed partition of enforce_suggestion_allowlist.
_PARAM_DEFINITIONS: dict[str, dict[str, str]] = {
    "TRIGGER_THRESHOLD_PCT": {
        "definition": (
            "MC-probability ceiling that arms the risk guard; x2 is the "
            "disarm level. Raising it arms the guard in a wider band."
        ),
        "risk_polarity": "raising loosens risk",
    },
    "TAKE_PROFIT_MC_PCT": {
        "definition": (
            "MC-probability floor below which take-profit arming triggers (exceptional-gain exit)."
        ),
        "risk_polarity": "raising tightens risk",
    },
    "VWAP_CROSS_HWM_PCT": {
        "definition": ("High-water-mark-relative band for the VWAP-breakdown state machine."),
        "risk_polarity": "raising loosens risk",
    },
    "VWAP_BLEED_MULTIPLIER": {
        "definition": ("Multiplier on 20-day volatility setting the VWAP-bleed arming threshold."),
        "risk_polarity": "raising loosens risk",
    },
    "VWAP_BLEED_TICKS": {
        "definition": (
            "Consecutive-tick count required before a VWAP-bleed cut fires; "
            "raising it delays the exit."
        ),
        "risk_polarity": "raising loosens risk",
    },
    "PARABOLIC_VELOCITY_THRESHOLD": {
        "definition": ("Return-velocity threshold that arms the parabolic squeeze."),
        "risk_polarity": "raising loosens risk",
    },
    "MAX_PARABOLIC_SQUEEZE": {
        "definition": (
            "Cap on the parabolic-squeeze multiplier applied to the trailing "
            "stop; below 1.0 it tightens the stop."
        ),
        "risk_polarity": "raising loosens risk",
    },
    _UNTUNED_SUGGESTIBLE_KEY: {
        "definition": (
            "Floor on the squeeze multiplier applied to the active stop. "
            "Optuna structurally never tunes this — hand-set only; the "
            "highest-value advisory target."
        ),
        "risk_polarity": "raising loosens risk",
    },
}

# Hard min/max valid ranges. The 6 Optuna search-space keys mirror the
# suggest_* bounds in autotuner.objective() (autotuner.py:306-312).
# TRIGGER_THRESHOLD_PCT is included for display context only (it is locked, not
# suggestible). MAX_SQUEEZE_FLOOR has no Optuna range; 0.05-0.50 is the
# research placeholder (config-surface.md §1, Layer B).
_PARAM_VALID_RANGES: dict[str, dict[str, float | str]] = {
    "TRIGGER_THRESHOLD_PCT": {"low": 5.0, "high": 25.0, "type": "float"},
    "TAKE_PROFIT_MC_PCT": {"low": 2.0, "high": 10.0, "type": "float"},
    "VWAP_CROSS_HWM_PCT": {
        "low": 0.3,
        "high": 2.0,
        "type": "float",
    },  # V1 calibration bounds (autotuner.py _SS_VWAP_CROSS_HWM_V1_MIN/MAX)
    "VWAP_BLEED_MULTIPLIER": {"low": 0.5, "high": 3.0, "type": "float"},
    "VWAP_BLEED_TICKS": {"low": 3, "high": 30, "type": "int"},
    "PARABOLIC_VELOCITY_THRESHOLD": {"low": 1.0, "high": 4.0, "type": "float"},
    "MAX_PARABOLIC_SQUEEZE": {"low": 0.1, "high": 0.8, "type": "float"},
    _UNTUNED_SUGGESTIBLE_KEY: {"low": 0.05, "high": 0.50, "type": "float"},
}

# The non-negotiable risk invariants Claude must treat as hard constraints —
# element 8 (prompt-methodology.md §1.1).
_RISK_INVARIANTS = [
    "The trailing-stop ratchet is monotonic — it moves up, never down.",
    "The breakeven latch is one-way — once latched it does not unlatch.",
    "Exit-confirmation tick gates exist deliberately; a suggestion that "
    "functionally loosens a live stop must be self-flagged as risk-increasing.",
]

# The data window + its limits — element 7 (prompt-methodology.md §1.1).
_DATA_WINDOW = {
    "trading_days": 125,
    "methodology": "80/20 walk-forward analysis (WFA)",
    "history_source": "synthetic replay history (Alpaca-derived)",
    "regimes_note": (
        "The 125-day synthetic replay window may not cover every market "
        "regime (vol spikes, gap events, low-volume sessions). If the current "
        "volatility regime is outside the window's observed range, the correct "
        "answer is data_sufficiency: insufficient — decline."
    ),
}

# Operator-assist role framing — element 9 (prompt-methodology.md §1.1).
_ROLE_FRAMING = (
    "You are an operator-assist analyst. A human operator reviews, accepts, or "
    "rejects every suggestion you produce. You are NOT tuning the system — you "
    "propose hypotheses for a human and a walk-forward validator to test. Your "
    "suggestions are unvalidated hypotheses; Optuna's are walk-forward "
    "validated. Prefer fewer, well-justified suggestions over many. An empty "
    "suggestions list is a valid, encouraged answer when nothing is "
    "well-supported — do not fabricate to fill the list."
)


# ---------------------------------------------------------------------------
# Pydantic schemas — the structured-output contract.
# ---------------------------------------------------------------------------


class ConfigSuggestion(BaseModel):
    """One proposed config edit with its rationale and self-classified risk."""

    config_key: str
    current_value: float | int | str
    suggested_value: float | int | str
    rationale: str
    risk_direction: str  # "loosens" | "tightens" | "neutral"
    confidence: str
    data_sufficiency: str
    oos_status: str = "pending"  # "passed" | "rejected" | "pending"
    oos_reason: str | None = None
    impact: dict = {"metric": "sharpe", "delta": 0.0}


class ConfigSuggestionsResponse(BaseModel):
    """The full structured response — zero or more suggestions.

    An empty ``suggestions`` list is valid: it is the abstention escape hatch
    ("the config looks sound"), not an error.
    """

    suggestions: list[ConfigSuggestion]


# ---------------------------------------------------------------------------
# Context assembly.
# ---------------------------------------------------------------------------


def _build_volatility_regime(autotune_run: dict | None) -> dict:
    """Volatility regime context — element 6.

    Reports current 20-day vol and 14-day ATR% against their 125-day window
    range. Values come from the autotune run when available; when Optuna has
    not run, the section is still well-shaped but marked unavailable so Claude
    can invoke the insufficient-data escape hatch.

    Honest availability: ``_autotune_run_row_to_dict`` (database.py:718-735)
    does not project ``symphony_vol`` or ``atr_pct_14d`` columns — those
    columns do not yet exist in the schema. Marking ``available:True`` with
    all-null fields fabricates regime context for Claude. We mark
    ``available:False`` whenever the actual vol/atr fields are absent or null,
    and set ``available:True`` only when the fields are genuinely present and
    non-null (forward-compatible once the schema gains those columns).
    """
    if autotune_run is None:
        return {
            "vol_20d": None,
            "vol_20d_window_range": None,
            "atr_pct_14d": None,
            "atr_pct_14d_window_range": None,
            "available": False,
            "reason": "Optuna has not run for this symphony — no vol/atr data available.",
        }

    vol_20d = autotune_run.get("symphony_vol")
    atr_pct_14d = autotune_run.get("atr_pct_14d")

    # Both fields must be non-null to claim regime data is available.
    # Currently neither column exists in the autotune_runs schema, so this
    # path is never True — but it will be once the schema is extended.
    if vol_20d is not None and atr_pct_14d is not None:
        return {
            "vol_20d": vol_20d,
            "vol_20d_window_range": autotune_run.get("vol_window_range"),
            "atr_pct_14d": atr_pct_14d,
            "atr_pct_14d_window_range": autotune_run.get("atr_pct_window_range"),
            "available": True,
        }

    return {
        "vol_20d": None,
        "vol_20d_window_range": None,
        "atr_pct_14d": None,
        "atr_pct_14d_window_range": None,
        "available": False,
        "reason": (
            "vol/atr columns not yet in autotune schema — "
            "symphony_vol and atr_pct_14d are not projected by "
            "_autotune_run_row_to_dict (database.py:718-735)."
        ),
    }


def _build_optuna_section(autotune_run: dict | None) -> dict:
    """The Optuna OOS-vs-train evidence — element 3.

    Carries train_alpha, oos_alpha, fallback_oos_alpha, default_oos_alpha and
    baseline_decision. When ``get_latest_autotune_run`` returns None (Optuna
    has not run for this symphony yet) the section is well-shaped but flagged
    absent — assembly must not break (refinement 5).
    """
    if not isinstance(autotune_run, dict):
        # Guard against None (Optuna not yet run) and any non-dict value
        # (e.g. test mocks that replace the whole database module).
        return {
            "available": False,
            "note": (
                "Optuna has not run for this symphony yet — no walk-forward "
                "validation evidence is available. Treat the live config as "
                "unvalidated and weight data_sufficiency accordingly."
            ),
            "train_alpha": None,
            "oos_alpha": None,
            "fallback_oos_alpha": None,
            "default_oos_alpha": None,
            "baseline_decision": None,
        }
    return {
        "available": True,
        "run_timestamp": autotune_run.get("run_timestamp"),
        "train_alpha": autotune_run.get("train_alpha"),
        "oos_alpha": autotune_run.get("oos_alpha"),
        "oos_train_gap": _safe_gap(autotune_run.get("oos_alpha"), autotune_run.get("train_alpha")),
        "fallback_oos_alpha": autotune_run.get("fallback_oos_alpha"),
        "default_oos_alpha": autotune_run.get("default_oos_alpha"),
        "baseline_decision": autotune_run.get("baseline_decision"),
    }


def _safe_gap(oos: object, train: object) -> float | None:
    """OOS-minus-train alpha gap, or None if either operand is missing."""
    if isinstance(oos, (int, float)) and isinstance(train, (int, float)):
        return float(oos) - float(train)
    return None


def _build_suggestible_surface(symphony_id: str | None) -> list[dict]:
    """The 7-item allowlisted config surface, each with definition + range +
    current live value + locked flag.

    Elements 1, 2, 4, 5. The current live values come from the per-symphony
    ``symphony_strategies`` row (falling back to DEFAULT_STRATEGY) — NEVER from
    os.environ. The set of keys is the 6 Optuna search-space keys plus the
    untuned MAX_SQUEEZE_FLOOR: a curated allowlist, not a denylist.
    """
    current_params, locked_vars = _read_current_strategy(symphony_id)

    allowlist_keys = sorted(_OPTUNA_SEARCH_SPACE_KEYS) + [_UNTUNED_SUGGESTIBLE_KEY]

    surface: list[dict] = []
    for key in allowlist_keys:
        definition = _PARAM_DEFINITIONS.get(key, {})
        surface.append(
            {
                "config_key": key,
                "definition": definition.get("definition", ""),
                "risk_polarity": definition.get("risk_polarity", ""),
                "valid_range": _PARAM_VALID_RANGES.get(key),
                "current_live_value": current_params.get(key),
                "locked": key in locked_vars,
                "optuna_tuned": key in _OPTUNA_SEARCH_SPACE_KEYS,
            }
        )
    return surface


def _read_current_strategy(
    symphony_id: str | None,
) -> tuple[dict, list]:
    """Read the per-symphony live params + locked_vars from the state DB.

    Falls back to ``database.DEFAULT_STRATEGY`` / ``DEFAULT_LOCKED_VARS`` if no
    row exists or the read fails — assembly must stay well-shaped. Reads ONLY
    the curated strategy surface; never the environment.
    """
    default_params = dict(getattr(database, "DEFAULT_STRATEGY", {}))
    default_locked = list(getattr(database, "DEFAULT_LOCKED_VARS", []))

    if symphony_id is None:
        return default_params, default_locked

    try:
        strategy = database.get_symphony_strategy(symphony_id)
    except Exception:  # noqa: BLE001 - degrade to defaults, never break assembly
        logger.debug(
            "get_symphony_strategy failed for %s; using DEFAULT_STRATEGY",
            symphony_id,
            exc_info=True,
        )
        return default_params, default_locked

    if not strategy:
        return default_params, default_locked

    params = strategy.get("parameters") or {}
    locked = strategy.get("locked_vars")
    if locked is None:
        locked = default_locked
    # Layer the live params over the defaults so every allowlist key resolves.
    merged = {**default_params, **params}
    return merged, list(locked)


# Sentinel used by assemble_advisor_context to distinguish "caller did not
# pass autotune_run" (fetch from DB) from "caller explicitly passed None"
# (Optuna has not run — skip the DB fetch).
_SENTINEL = object()


def build_assessment_from_context(context: dict) -> dict:
    """Build a per-symphony assessment dict from the assembled context.

    AC3 resolution: ``ConfigSuggestionsResponse`` (ai_advisor.py:197-204) has
    only a ``suggestions: list[ConfigSuggestion]`` field — no summary/assessment
    at the response level. The route extracts only ``.suggestions`` and
    previously discarded the assembled context entirely. The assessment is
    therefore built here from ``context["optuna_evidence"]``, which already
    carries ``baseline_decision``, ``oos_alpha``, ``fallback_oos_alpha``,
    ``default_oos_alpha``, and ``available``.

    Returns a dict with at minimum:
      - ``baseline_decision``: the autotuner's decision for this symphony.
      - ``oos_alpha``: finite float or None (sentinel for -inf / no valid trial).
      - ``fallback_oos_alpha``: the fallback OOS guard-alpha.
      - ``default_oos_alpha``: the default OOS guard-alpha.
      - ``summary``: a human-readable string explaining the tuning state.

    The summary is per-symphony — two symphonies in different states produce
    different summaries so the UI is differentiated rather than generic.
    """
    evidence: dict = context.get("optuna_evidence") or {}
    baseline_decision = evidence.get("baseline_decision")
    oos_alpha = evidence.get("oos_alpha")
    fallback_oos_alpha = evidence.get("fallback_oos_alpha")
    default_oos_alpha = evidence.get("default_oos_alpha")
    available = evidence.get("available", False)

    if not available:
        summary = (
            "Optuna has not yet run for this symphony — no walk-forward "
            "validation evidence is available. Config is unvalidated; "
            "Claude is reasoning without OOS data."
        )
    elif oos_alpha is None:
        # -inf sentinel: all trials were haircut-rejected by FDR gate.
        summary = (
            f"No statistically-significant tuning edge: all optimizer trials "
            f"failed the FDR significance gate; out-of-sample guard-alpha is "
            f"negative (fallback={fallback_oos_alpha}, default={default_oos_alpha}). "
            f"Baseline decision: {baseline_decision}. Holding current config."
        )
    else:
        # Guard oos_alpha format: only apply .4f when it is a real numeric type.
        # A MagicMock or unexpected value must not crash the summary builder.
        oos_str = f"{oos_alpha:.4f}" if isinstance(oos_alpha, (int, float)) else str(oos_alpha)
        summary = (
            f"Optimizer found a validated edge: OOS alpha={oos_str}, "
            f"baseline decision={baseline_decision}. "
            f"Fallback OOS alpha={fallback_oos_alpha}, "
            f"default OOS alpha={default_oos_alpha}."
        )

    return {
        "baseline_decision": baseline_decision,
        "oos_alpha": oos_alpha,
        "fallback_oos_alpha": fallback_oos_alpha,
        "default_oos_alpha": default_oos_alpha,
        "summary": summary,
    }


def assemble_advisor_context(
    scope: str,
    symphony_id: str | None = None,
    composer_symphony_id: str | None = None,
    autotune_run=_SENTINEL,
) -> dict:
    """Assemble the prompt-ready context blob for the Claude config advisor.

    Carries all 8 must-have prompt elements plus role framing
    (prompt-methodology.md §1.1):

      1. Per-param definition + risk polarity.
      2. Valid range of every suggestible param.
      3. The Optuna OOS-vs-train delta + baseline_decision.
      4. Current live value of each param.
      5. locked_vars.
      6. Volatility regime context.
      7. The data window + its limits.
      8. Risk invariants as hard constraints.
      9. Operator-assist role + task framing.

    Real-money-critical: the config surface is the 9-item curated ALLOWLIST.
    This function NEVER reads ``dict(os.environ)`` or dumps ``.env``. No
    credential, account id, safety flag, or methodology knob can enter the
    returned dict.

    Args:
        scope: "symphony" (per-symphony advisory) or "global".
        symphony_id: required when ``scope == "symphony"``. Used as the key for
            all state-DB lookups (autotune_runs, symphony_strategies) — keyed by
            normalized name in this project.
        composer_symphony_id: optional Composer hash ID for the symphony. When
            supplied it is passed to ``symphony_logic.get_condensed_logic`` so
            the Composer ``/score`` API receives the hash it expects; if omitted,
            ``symphony_id`` is used for the logic fetch (backward-compatible).
        autotune_run: optional pre-fetched autotune run dict. When supplied the
            internal ``database.get_latest_autotune_run`` call is skipped —
            the caller is responsible for fetching (useful when the caller
            already holds a DB reference that may be patched in tests).

    Returns:
        A well-shaped context dict — even when Optuna has not run for the
        symphony (the Optuna section is then marked absent, not omitted).

    Raises:
        ValueError: if ``scope == "symphony"`` and ``symphony_id`` is None.
    """
    if scope == "symphony" and symphony_id is None:
        raise ValueError(
            "scope='symphony' requires a symphony_id — refusing to assemble a contextless prompt."
        )

    # Resolve the autotune_run: use the caller-supplied value when provided
    # (non-_SENTINEL), otherwise fetch from DB. This avoids a redundant DB
    # round-trip when the route already holds a reference (e.g. app.py's
    # ai_advisor_suggest fetches it to build the assessment context and passes
    # it here so both uses share the same row).
    if autotune_run is _SENTINEL:
        autotune_run = None
        _fetch_autotune = True
    else:
        _fetch_autotune = False

    condensed_logic: dict | None = None
    if scope == "symphony":
        # P1 dependency — Optuna walk-forward metrics. May be None (not tuned).
        if _fetch_autotune:
            autotune_run = database.get_latest_autotune_run(symphony_id)
        # P2 dependency — condensed symphony logic / composition.
        # Use the Composer hash ID when available; the Composer /score endpoint
        # requires the hash, not the normalized name (bug fix: passing the
        # normalized name produced HTTP 400 and an all-empty logic struct).
        logic_id = composer_symphony_id if composer_symphony_id is not None else symphony_id
        condensed_logic = symphony_logic.get_condensed_logic(logic_id)

    context: dict = {
        "scope": scope,
        "symphony_id": symphony_id,
        # Element 9 — role + task framing.
        "role_framing": _ROLE_FRAMING,
        # Elements 1, 2, 4, 5 — the allowlisted suggestible surface.
        "suggestible_surface": _build_suggestible_surface(symphony_id),
        "locked_vars": _read_current_strategy(symphony_id)[1],
        # Element 3 — Optuna OOS-vs-train evidence.
        "optuna_evidence": _build_optuna_section(autotune_run),
        # Element 6 — volatility regime context.
        "volatility_regime": _build_volatility_regime(autotune_run),
        # Element 7 — the data window + its limits.
        "data_window": _DATA_WINDOW,
        # Element 8 — hard risk invariants.
        "risk_invariants": _RISK_INVARIANTS,
        # P2 — condensed symphony logic / composition.
        "symphony_logic": condensed_logic,
    }
    return context


# ---------------------------------------------------------------------------
# Claude client + structured-output request.
# ---------------------------------------------------------------------------


def _build_client():
    """Construct the anthropic SDK client.

    Factory seam: tests patch ``ai_advisor._build_client``. Constructing the
    client here (not inline in ``request_suggestions``) means a missing or
    invalid ``ANTHROPIC_API_KEY`` surfaces as a construction failure that
    ``request_suggestions`` catches and degrades gracefully on.

    Raises:
        RuntimeError: if the SDK is unavailable or no API key is configured.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the Claude config advisor is "
            "unavailable until an API key is configured."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - SDK is a declared dep
        raise RuntimeError(f"the anthropic SDK is not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=api_key)


def _build_messages(context: dict) -> list[dict]:
    """Render the assembled context dict into the Claude messages payload."""
    import json

    return [
        {
            "role": "user",
            "content": (
                "Analyze the following Planet Stopper risk-engine context and "
                "propose 0..N config edits. Each suggestion must cite specific "
                "supplied numbers in its rationale. Stay strictly within the "
                "stated valid ranges. Never emit a suggested value for a "
                "locked param. If no edit is well-supported, return an empty "
                "suggestions list.\n\n" + json.dumps(context, default=str, indent=2)
            ),
        }
    ]


def request_suggestions(
    context: dict,
) -> tuple[ConfigSuggestionsResponse | None, str | None]:
    """Call Claude for structured config suggestions.

    On-demand operator-assist: a failure is "no suggestion this click", zero
    engine impact. This function NEVER raises — every failure mode (client
    construction failure, SDK error, malformed/unparseable response) degrades
    to ``(None, error_message)`` where ``error_message`` is a non-empty string
    the UI can show the operator.

    An empty suggestions list is a valid NON-error response — it flows through
    as ``(ConfigSuggestionsResponse(suggestions=[]), None)``.

    Returns:
        ``(ConfigSuggestionsResponse, None)`` on success;
        ``(None, error_message)`` on any failure.
    """
    # Build the client behind the factory seam — catch construction failure.
    try:
        client = _build_client()
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        # D-1 security contract: do NOT embed str(exc) in the browser-facing
        # message — exception text may contain API keys or internal paths.
        # exc_info detail is logged server-side only.
        msg = f"Claude advisor unavailable: could not build client ({type(exc).__name__})."
        logger.warning("ai_advisor: client construction failed: %s", exc, exc_info=True)
        return None, msg

    # Call Claude's structured-output endpoint. anthropic 0.85.0 exposes
    # client.messages.parse(..., output_format=<PydanticModel>) which returns
    # an SDK response whose .content list carries one or more ParsedTextBlocks;
    # each ParsedTextBlock has a .parsed_output field holding the validated
    # Pydantic instance. We walk content and take the first non-None
    # .parsed_output (see the extraction loop below).
    try:
        sdk_response = client.messages.parse(
            model=_CLAUDE_MODEL,
            max_tokens=_MAX_TOKENS,
            output_format=ConfigSuggestionsResponse,
            messages=_build_messages(context),
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - the whole contract: never raise
        # D-1 security contract: do NOT embed str(exc) in the browser-facing
        # message — exception text may contain API keys or internal paths.
        # Full detail is logged server-side via exc_info=True; the UI sees only
        # the error class name, mirroring the client-construction failure path.
        msg = (
            f"Claude advisor request failed ({type(exc).__name__}). "
            "Try again, or decide manually."
        )
        logger.warning(
            "ai_advisor: messages.parse failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None, msg

    # Extract + validate the parsed structured output. On anthropic 0.85.0 the
    # validated Pydantic instance lives on each ParsedTextBlock inside
    # response.content, not on a top-level .parsed attribute (which does not
    # exist on ParsedMessage). Walk the content blocks and take the first
    # non-None parsed_output. A malformed response (all parsed_output=None, or
    # empty content) degrades gracefully — never raise.
    parsed = None
    for block in getattr(sdk_response, "content", None) or []:
        candidate = getattr(block, "parsed_output", None)
        if candidate is not None:
            parsed = candidate
            break
    if parsed is None:
        msg = (
            "Claude returned an unparseable response (no structured output). "
            "No suggestions this call."
        )
        logger.warning("ai_advisor: SDK response had no .parsed payload")
        return None, msg

    if isinstance(parsed, ConfigSuggestionsResponse):
        return parsed, None

    # The SDK handed back something — coerce it through the schema so a
    # dict-shaped payload still validates, and a wrong-shape payload (e.g. a
    # bare string) fails validation gracefully rather than raising upstream.
    try:
        if isinstance(parsed, BaseModel):
            response = ConfigSuggestionsResponse.model_validate(parsed.model_dump())
        elif isinstance(parsed, dict):
            response = ConfigSuggestionsResponse.model_validate(parsed)
        else:
            raise TypeError(
                f"parsed output is {type(parsed).__name__}, not a ConfigSuggestionsResponse"
            )
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        msg = (
            f"Claude returned a response that did not match the expected "
            f"schema ({type(exc).__name__}). No suggestions this call."
        )
        logger.warning("ai_advisor: response failed schema validation: %s", exc)
        return None, msg

    return response, None


# ---------------------------------------------------------------------------
# C2 — safety gates on the suggestions Claude emits.
#
# Three independent layers, defense-in-depth on top of C1's context allowlist:
#   1. enforce_suggestion_allowlist — structural rejection of any config_key
#      Claude emits that is not one of the 9 suggestible params (hallucinated
#      keys, credentials, LIVE_EXECUTION, methodology knobs).
#   2. compute_risk_direction / check_risk_direction_agreement — the engine
#      computes risk polarity itself and flags any contradiction with Claude's
#      self-reported risk_direction. A real-money system never trusts a model's
#      self-classification.
#   3. revalidate_suggestion_oos — routes an accepted suggestion through the
#      autotuner's run_simulation OOS gate before it can reach live config.
# ---------------------------------------------------------------------------

# The 7-item suggestible allowlist: the 6 Optuna search-space keys plus the one
# untuned hand-set key. Derived from the C1 constants so it cannot drift.
# (config-surface.md §1 — an allowlist, not a denylist.)
_SUGGESTIBLE_ALLOWLIST = frozenset(_OPTUNA_SEARCH_SPACE_KEYS) | {_UNTUNED_SUGGESTIBLE_KEY}

# Risk-direction outcomes.
_LOOSENS = "loosens"
_TIGHTENS = "tightens"
_NEUTRAL = "neutral"

# Per-key polarity when a param's value is RAISED (suggested > current).
# Derived from the ``risk_polarity`` field of _PARAM_DEFINITIONS
# (config-surface.md §1). Every suggestible key is "raising loosens risk"
# EXCEPT TAKE_PROFIT_MC_PCT, the lone inverted param ("raising tightens risk").
# Built from _PARAM_DEFINITIONS rather than re-listed so it cannot drift.
_RAISE_RISK_DIRECTION: dict[str, str] = {
    key: (_TIGHTENS if definition.get("risk_polarity") == "raising tightens risk" else _LOOSENS)
    for key, definition in _PARAM_DEFINITIONS.items()
}

# Inverse of a raise: lowering a param does the opposite of raising it.
_OPPOSITE_DIRECTION = {
    _LOOSENS: _TIGHTENS,
    _TIGHTENS: _LOOSENS,
    _NEUTRAL: _NEUTRAL,
}


def enforce_suggestion_allowlist(
    suggestions: list[ConfigSuggestion],
) -> tuple[list[ConfigSuggestion], list[ConfigSuggestion]]:
    """Partition suggestions into (allowed, rejected) by ``config_key``.

    Defense-in-depth: even though the C1 *context* is allowlisted, Claude could
    hallucinate a ``config_key`` that was never in the prompt — or emit a
    credential, the ``LIVE_EXECUTION`` master safety flag, an account UUID, or a
    methodology knob. Any ``config_key`` not in the 7-item suggestible allowlist
    is structurally routed to ``rejected``; it must be impossible for such a key
    to reach a live config write.

    Args:
        suggestions: the suggestions Claude emitted (may be empty).

    Returns:
        ``(allowed, rejected)`` — two lists, order-preserving, with every input
        suggestion in exactly one partition (no drop, no duplication).
    """
    allowed: list[ConfigSuggestion] = []
    rejected: list[ConfigSuggestion] = []
    for suggestion in suggestions:
        if suggestion.config_key in _SUGGESTIBLE_ALLOWLIST:
            allowed.append(suggestion)
        else:
            rejected.append(suggestion)
    return allowed, rejected


def compute_risk_direction(config_key: str, current_value, suggested_value) -> str:
    """Compute, code-side, whether a suggestion loosens or tightens risk.

    The engine never trusts Claude's self-reported ``risk_direction``; it
    derives the direction itself from each param's documented risk polarity
    (config-surface.md §1, mirrored in ``_RAISE_RISK_DIRECTION``).

    Polarity rule: for every suggestible key, RAISING the value loosens risk —
    EXCEPT ``TAKE_PROFIT_MC_PCT``, the lone inverted param, where raising the
    value tightens risk. Lowering a value is the exact inverse of raising it.
    An unchanged value (suggested == current) is always ``neutral``.

    Args:
        config_key: one of the 9 suggestible keys.
        current_value: the current live value.
        suggested_value: Claude's proposed value.

    Returns:
        ``"loosens"`` | ``"tightens"`` | ``"neutral"``.
    """
    if suggested_value == current_value:
        return _NEUTRAL

    raise_direction = _RAISE_RISK_DIRECTION.get(config_key)
    if raise_direction is None:
        # Not a recognised suggestible key — no polarity is defined, so the
        # engine cannot classify the risk delta. The allowlist gate is the
        # layer that rejects such keys; here we simply decline to guess.
        return _NEUTRAL

    if suggested_value > current_value:
        return raise_direction
    return _OPPOSITE_DIRECTION[raise_direction]


def check_risk_direction_agreement(suggestion: ConfigSuggestion) -> dict:
    """Cross-check Claude's self-reported risk direction against the engine's.

    Claude self-classifies each suggestion's ``risk_direction``; a real-money
    system never trusts that. This gate computes the direction independently
    and reports whether the two agree, surfacing BOTH directions so the
    operator UI can show a contradiction explicitly.

    Args:
        suggestion: the ConfigSuggestion to check.

    Returns:
        ``{agrees: bool, code_direction: str, claimed_direction: str}``.
    """
    code_direction = compute_risk_direction(
        suggestion.config_key,
        suggestion.current_value,
        suggestion.suggested_value,
    )
    claimed_direction = suggestion.risk_direction
    return {
        "agrees": code_direction == claimed_direction,
        "code_direction": code_direction,
        "claimed_direction": claimed_direction,
    }


def revalidate_suggestion_oos(
    symphony_id: str,
    config_key: str,
    suggested_value,
    current_strategy: dict,
) -> dict:
    """Re-validate an accepted suggestion through the autotuner's OOS gate.

    A Claude suggestion is an *unvalidated hypothesis*; Optuna's output is
    walk-forward validated. Before an accepted suggestion can reach live config,
    it must pass the same out-of-sample gate Optuna's own output faces: its OOS
    alpha must strictly beat the current strategy's baseline OOS alpha.

    ``run_simulation`` is called TWICE over the same history window —
    apples-to-apples: once with ``current_strategy`` (the baseline), once with
    the strategy patched to ``suggested_value``.

    Pass rule is strict ``>``: a TIE does NOT pass — a tie buys no validated
    improvement, mirroring the autotuner's own strict-positive cascade rule.

    The ``autotuner`` import is LAZY (inside this function body, not at module
    scope): importing ``autotuner`` pulls in optuna + joblib, whose process-pool
    / resource-tracker import side effects collide with the anthropic SDK import
    under pytest's output capture. Deferring the import past that fragile window
    is mandatory.

    Args:
        symphony_id: the symphony the suggestion targets.
        config_key: the config key the suggestion edits.
        suggested_value: Claude's proposed value for ``config_key``.
        current_strategy: the current per-symphony strategy dict — the baseline.

    Returns:
        ``{passed: bool, oos_alpha: float, baseline_oos_alpha: float,
        detail: str}``.
    """
    # Lazy imports — deferred past the anthropic-SDK / optuna import-collision
    # window. Module-scope imports of autotuner or synthetic_history would break
    # the C2 import-guard tests; keep them inside the function body.
    # current_date_str: today in YYYY-MM-DD, the format autotuner.run_simulation
    # expects (it calls datetime.strptime(current_date_str, "%Y-%m-%d")).
    from datetime import datetime as _datetime

    import synthetic_history as _synthetic_history
    from autotuner import calculate_historical_deviation, run_simulation

    current_date_str = _datetime.now().strftime("%Y-%m-%d")

    # bot_state: the live engine state from the state DB. Used to build history
    # and to identify which account keys belong to this symphony.
    bot_state = database.load_state()

    # acc_sym_ids: the bot_state keys whose normalized symphony name matches
    # symphony_id. Mirrors the derivation in autotuner.run_autotuner's objective
    # closure (autotuner.py line 314).
    acc_sym_ids = [
        k
        for k, v in bot_state.items()
        if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == symphony_id
    ]

    # history_data: 125-day synthetic replay history. This call is on the
    # operator-accept path (rare human action), NOT the 1-minute engine cycle.
    # Latency is acceptable because generate_synthetic_history maintains a
    # date+holdings-keyed file cache; the autotuner fills this cache each cycle
    # before market open, so the cold-fetch case (no cache) only occurs when
    # the autotuner has not yet run — already a degraded-data situation.
    history_data = _synthetic_history.generate_synthetic_history(bot_state, current_date_str)

    # deviation_dict: 45-day trailing execution-deviation penalties by exit reason.
    deviation_dict = calculate_historical_deviation(current_date_str)

    # Patch the strategy to the suggested value — same window, only one knob
    # changes, so the OOS comparison is apples-to-apples.
    patched_strategy = dict(current_strategy)
    patched_strategy[config_key] = suggested_value

    # Call run_simulation twice: baseline first, then the patched strategy.
    baseline_oos_alpha = run_simulation(
        current_strategy, history_data, acc_sym_ids, current_date_str, deviation_dict
    )
    patched_oos_alpha = run_simulation(
        patched_strategy, history_data, acc_sym_ids, current_date_str, deviation_dict
    )

    # Strict `>` — a tie does not pass (autotuner strict-positive cascade rule).
    passed = patched_oos_alpha > baseline_oos_alpha

    if passed:
        detail = (
            f"OOS re-validation PASSED for {config_key}={suggested_value} on "
            f"{symphony_id}: patched OOS alpha {patched_oos_alpha} strictly "
            f"beats baseline {baseline_oos_alpha}."
        )
    else:
        detail = (
            f"OOS re-validation FAILED for {config_key}={suggested_value} on "
            f"{symphony_id}: patched OOS alpha {patched_oos_alpha} does not "
            f"strictly beat baseline {baseline_oos_alpha} — not greenlit for a "
            f"live config write."
        )

    return {
        "passed": passed,
        "oos_alpha": patched_oos_alpha,
        "baseline_oos_alpha": baseline_oos_alpha,
        "detail": detail,
    }
