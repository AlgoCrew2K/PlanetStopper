"""
Cycle C2 — RED: safety gates for the Claude AI Config Advisor.

Tier 1 — fixture / source-inspection unit tests. No network, no live Claude
call, no real ``run_simulation`` (it is mocked). Runs every CI pass.

The three safety-gate functions under test do NOT exist yet — every test here
is RED until the C2 implementer cycle adds them to ``ai_advisor.py``. The
binding contract these tests pin (GREEN implements exactly this):

    def enforce_suggestion_allowlist(
        suggestions: list[ConfigSuggestion],
    ) -> tuple[list[ConfigSuggestion], list[ConfigSuggestion]]:
        # -> (allowed, rejected); rejected = any config_key not in the 9-item
        #    suggestible allowlist. Credentials / LIVE_EXECUTION / methodology
        #    knobs must ALWAYS land in rejected.

    def compute_risk_direction(
        config_key: str, current_value, suggested_value,
    ) -> str:
        # -> "loosens" | "tightens" | "neutral", computed code-side from each
        #    param's risk polarity (config-surface.md), NOT from Claude's claim.

    def check_risk_direction_agreement(suggestion: ConfigSuggestion) -> dict:
        # -> {agrees: bool, code_direction: str, claimed_direction: str}

    def revalidate_suggestion_oos(
        symphony_id: str, config_key: str, suggested_value,
        current_strategy: dict,
    ) -> dict:
        # -> {passed: bool, oos_alpha: float, baseline_oos_alpha: float,
        #     detail: str}. Routes an accepted suggestion through the
        #    autotuner's run_simulation OOS gate BEFORE live config write.
        # CRITICAL: imports ``autotuner`` LAZILY inside the function body.

Why these gates exist (prompt-methodology.md §2.2, §3, config-surface.md §2):
  * Allowlist enforcement — defense-in-depth: even though the *context* is
    allowlisted (C1), Claude could hallucinate a config_key. A credential or
    ``LIVE_EXECUTION`` appearing in a suggestion must be structurally rejected.
  * Independent risk-direction computation — Claude self-reports
    ``risk_direction``; a real-money system never trusts that. Code computes
    it independently and the caller flags contradictions to the operator.
  * OOS re-validation — a Claude suggestion is an *unvalidated hypothesis*;
    Optuna's output is walk-forward validated. Any accepted suggestion must
    pass the same OOS gate before reaching live config.

Mocking strategy
----------------
* ``autotuner.run_simulation`` -> patched. The lazy import inside
  ``revalidate_suggestion_oos`` does ``from autotuner import run_simulation``,
  so the name is looked up on the ``autotuner`` module at call time. We patch
  ``autotuner.run_simulation`` (the source attribute) — that is where the
  lazy ``from`` import resolves it.
* The math engine is NEVER mocked.
* No module-level mutable state — every fixture is function-scoped.

The risk-polarity table (derived from config-surface.md §1 + the
``risk_polarity`` field of ``ai_advisor._PARAM_DEFINITIONS``; binding for
GREEN's ``compute_risk_direction``):

    | config_key                   | RAISE value | LOWER value |
    |-------------------------------|-------------|-------------|
    | TRIGGER_THRESHOLD_PCT         | loosens     | tightens    |
    | TAKE_PROFIT_MC_PCT            | tightens    | loosens     |
    | VWAP_CROSS_HWM_PCT            | loosens     | tightens    |
    | VWAP_BLEED_MULTIPLIER         | loosens     | tightens    |
    | VWAP_BLEED_TICKS              | loosens     | tightens    |
    | PARABOLIC_VELOCITY_THRESHOLD  | loosens     | tightens    |
    | MAX_PARABOLIC_SQUEEZE         | loosens     | tightens    |
    | MAX_SQUEEZE_FLOOR             | loosens     | tightens    |

    Equal current/suggested value -> "neutral" for every key.
    TAKE_PROFIT_MC_PCT is the lone inverted-polarity param ("raising tightens
    risk") — every other suggestible key is "raising loosens risk".
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest

import ai_advisor
from ai_advisor import ConfigSuggestion


# ---------------------------------------------------------------------------
# Test data — the risk-polarity table, derived once and reused.
# config-surface.md §1 + ai_advisor._PARAM_DEFINITIONS[*]["risk_polarity"].
# (config_key, polarity_when_raised). "raising tightens" only for
# TAKE_PROFIT_MC_PCT; every other key is "raising loosens".
# ---------------------------------------------------------------------------

# config_key -> direction the code must report when suggested_value > current.
_RAISE_DIRECTION: dict[str, str] = {
    "TRIGGER_THRESHOLD_PCT": "loosens",
    "TAKE_PROFIT_MC_PCT": "tightens",
    "VWAP_CROSS_HWM_PCT": "loosens",
    "VWAP_BLEED_MULTIPLIER": "loosens",
    "VWAP_BLEED_TICKS": "loosens",
    "PARABOLIC_VELOCITY_THRESHOLD": "loosens",
    "MAX_PARABOLIC_SQUEEZE": "loosens",
    "MAX_SQUEEZE_FLOOR": "loosens",
}

# The inverse of a raise. Lowering a param does the opposite of raising it.
_OPPOSITE = {"loosens": "tightens", "tightens": "loosens", "neutral": "neutral"}

# A representative in-range (current, raised) value pair per key. The raised
# value sits strictly inside the param's valid range (ai_advisor
# ._PARAM_VALID_RANGES) and strictly above the current value, so the *only*
# variable under test is the polarity sign, not range-clamping.
_RAISE_PAIRS: dict[str, tuple[float, float]] = {
    "TRIGGER_THRESHOLD_PCT": (10.0, 20.0),     # range 5.0 - 25.0
    "TAKE_PROFIT_MC_PCT": (3.0, 8.0),          # range 2.0 - 10.0
    "VWAP_CROSS_HWM_PCT": (0.8, 2.0),          # range 0.5 - 2.5
    "VWAP_BLEED_MULTIPLIER": (1.0, 2.5),       # range 0.5 - 3.0
    "VWAP_BLEED_TICKS": (5, 25),               # range 3 - 30 (int)
    "PARABOLIC_VELOCITY_THRESHOLD": (1.5, 3.5),  # range 1.0 - 4.0
    "MAX_PARABOLIC_SQUEEZE": (0.2, 0.7),       # range 0.1 - 0.8
    "MAX_SQUEEZE_FLOOR": (0.10, 0.40),         # range 0.05 - 0.50
}

# Hard-exclusion keys (config-surface.md §2) — every one of these must land in
# the ``rejected`` partition of enforce_suggestion_allowlist, never ``allowed``.
_HARD_EXCLUSION_KEYS = [
    "LIVE_EXECUTION",
    "EXECUTION_START_TIME",
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "SIMULATION_PATHS",
    "NEIGHBOR_K",
    "ANTHROPIC_API_KEY",
    "symphony_name",
]


# ---------------------------------------------------------------------------
# Fixtures — all function-scoped, no shared mutable state.
# ---------------------------------------------------------------------------

@pytest.fixture
def allowlist_keys() -> list[str]:
    """The 9-item suggestible allowlist: 7 Optuna keys + MAX_SQUEEZE_FLOOR.

    Derived from ai_advisor's own constants — never re-listed as a literal, so
    this fixture cannot drift from the module under test.
    """
    return sorted(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS) + [
        ai_advisor._UNTUNED_SUGGESTIBLE_KEY
    ]


def _make_suggestion(
    config_key: str,
    current_value=10.0,
    suggested_value=12.0,
    risk_direction: str = "neutral",
) -> ConfigSuggestion:
    """Build a schema-valid ConfigSuggestion for a given key.

    Every non-key field is filled with a plausible placeholder so the only
    thing a test varies is the field it is actually exercising.
    """
    return ConfigSuggestion(
        config_key=config_key,
        current_value=current_value,
        suggested_value=suggested_value,
        rationale="placeholder rationale citing supplied window stats",
        risk_direction=risk_direction,
        confidence="medium",
        data_sufficiency="sufficient",
    )


# ===========================================================================
# Test 1 — enforce_suggestion_allowlist: partition allowed vs rejected.
# ===========================================================================

def test_allowlist_partitions_valid_keys_into_allowed(allowlist_keys):
    """Every one of the 9 allowlisted keys must land in ``allowed``.

    A suggestion list of exactly the 9 legitimate keys must pass through with
    an empty ``rejected`` partition — the gate must not be over-zealous.
    """
    suggestions = [_make_suggestion(key) for key in allowlist_keys]

    allowed, rejected = ai_advisor.enforce_suggestion_allowlist(suggestions)

    assert {s.config_key for s in allowed} == set(allowlist_keys)
    assert rejected == []


def test_allowlist_rejects_hallucinated_key():
    """A config_key Claude invented (not in any layer) must be rejected.

    Defense-in-depth: the C1 context is allowlisted, but Claude could still
    emit a key that was never in the prompt. ``FAKE_PARAM`` must partition
    into ``rejected``, not ``allowed``.
    """
    suggestions = [
        _make_suggestion("MAX_SQUEEZE_FLOOR"),
        _make_suggestion("FAKE_PARAM"),
    ]

    allowed, rejected = ai_advisor.enforce_suggestion_allowlist(suggestions)

    assert [s.config_key for s in allowed] == ["MAX_SQUEEZE_FLOOR"]
    assert [s.config_key for s in rejected] == ["FAKE_PARAM"]


@pytest.mark.parametrize("forbidden_key", _HARD_EXCLUSION_KEYS)
def test_allowlist_rejects_every_hard_exclusion_key(forbidden_key):
    """Adversarial: EVERY hard-exclusion key (config-surface.md §2) must be
    rejected — credentials, the LIVE_EXECUTION master safety flag, account
    UUIDs, methodology knobs, the API key, the DB primary key.

    A credential or LIVE_EXECUTION landing in ``allowed`` is a one-click path
    toward a real-money config write — it must be STRUCTURALLY impossible.
    """
    suggestions = [_make_suggestion(forbidden_key)]

    allowed, rejected = ai_advisor.enforce_suggestion_allowlist(suggestions)

    assert allowed == [], (
        f"hard-exclusion key {forbidden_key!r} must NEVER reach the allowed "
        f"partition — it is a credential / safety flag / methodology knob"
    )
    assert [s.config_key for s in rejected] == [forbidden_key]


def test_allowlist_mixed_list_partitions_correctly(allowlist_keys):
    """A realistic mixed list — valid keys + a hallucinated key + a forbidden
    key — must partition cleanly with no suggestion lost or duplicated.
    """
    valid_one = allowlist_keys[0]
    valid_two = allowlist_keys[1]
    suggestions = [
        _make_suggestion(valid_one),
        _make_suggestion("FAKE_PARAM"),
        _make_suggestion(valid_two),
        _make_suggestion("LIVE_EXECUTION"),
        _make_suggestion("ANTHROPIC_API_KEY"),
    ]

    allowed, rejected = ai_advisor.enforce_suggestion_allowlist(suggestions)

    assert sorted(s.config_key for s in allowed) == sorted([valid_one, valid_two])
    assert sorted(s.config_key for s in rejected) == sorted(
        ["FAKE_PARAM", "LIVE_EXECUTION", "ANTHROPIC_API_KEY"]
    )
    # Conservation: no suggestion silently dropped or duplicated.
    assert len(allowed) + len(rejected) == len(suggestions)


def test_allowlist_empty_input_returns_two_empty_lists():
    """An empty suggestion list (Claude's valid abstention) must partition to
    two empty lists, not raise.
    """
    allowed, rejected = ai_advisor.enforce_suggestion_allowlist([])
    assert allowed == []
    assert rejected == []


# ===========================================================================
# Test 2 — compute_risk_direction: per-param polarity, both directions.
# This is the load-bearing risk-math test. Expected polarity is derived from
# config-surface.md §1 (the ``risk_polarity`` of each param) and hand-verified
# in the _RAISE_DIRECTION table above.
# ===========================================================================

@pytest.mark.parametrize("config_key", sorted(_RAISE_DIRECTION))
def test_compute_risk_direction_raising_value(config_key):
    """RAISING a param's value: the code-side direction must match that
    param's documented risk polarity.

    TAKE_PROFIT_MC_PCT is the lone inverted param — raising it *tightens*
    risk. Every other suggestible key *loosens* risk when raised.
    """
    current, raised = _RAISE_PAIRS[config_key]
    expected = _RAISE_DIRECTION[config_key]

    result = ai_advisor.compute_risk_direction(config_key, current, raised)

    assert result == expected, (
        f"raising {config_key} from {current} to {raised} must be "
        f"{expected!r} per config-surface.md risk polarity, got {result!r}"
    )


@pytest.mark.parametrize("config_key", sorted(_RAISE_DIRECTION))
def test_compute_risk_direction_lowering_value(config_key):
    """LOWERING a param's value: the code-side direction must be the exact
    inverse of raising it — lowering a 'raising loosens' param tightens risk,
    lowering TAKE_PROFIT_MC_PCT loosens it.
    """
    current, raised = _RAISE_PAIRS[config_key]
    # Swap: now current is the higher value, suggested is the lower one.
    expected = _OPPOSITE[_RAISE_DIRECTION[config_key]]

    result = ai_advisor.compute_risk_direction(config_key, raised, current)

    assert result == expected, (
        f"lowering {config_key} from {raised} to {current} must be "
        f"{expected!r} (inverse of the raise polarity), got {result!r}"
    )


@pytest.mark.parametrize("config_key", sorted(_RAISE_DIRECTION))
def test_compute_risk_direction_unchanged_value_is_neutral(config_key):
    """A no-op suggestion (suggested == current) must be ``neutral`` for every
    param — there is no risk delta when nothing changes.
    """
    current, _raised = _RAISE_PAIRS[config_key]

    result = ai_advisor.compute_risk_direction(config_key, current, current)

    assert result == "neutral", (
        f"{config_key}: an unchanged value must be 'neutral', got {result!r}"
    )


def test_compute_risk_direction_take_profit_is_the_inverted_param():
    """Explicit pin of the one polarity inversion: TAKE_PROFIT_MC_PCT raises
    -> tightens, every other key raises -> loosens. If GREEN copies one
    polarity rule across all params, this test plus the parametrized raise
    test catch it.
    """
    assert (
        ai_advisor.compute_risk_direction("TAKE_PROFIT_MC_PCT", 3.0, 8.0)
        == "tightens"
    )
    assert (
        ai_advisor.compute_risk_direction("MAX_PARABOLIC_SQUEEZE", 0.2, 0.7)
        == "loosens"
    )


# ===========================================================================
# Test 3 — check_risk_direction_agreement: Claude's claim vs code computation.
# ===========================================================================

def test_risk_agreement_flags_contradiction():
    """Claude claims ``tightens`` but the code computes ``loosens`` — the gate
    must report ``agrees: False`` and surface BOTH directions so the UI can
    show the operator the contradiction.

    MAX_PARABOLIC_SQUEEZE raised (0.2 -> 0.7) loosens risk; a Claude claim of
    ``tightens`` is a self-misclassification — a strong distrust signal.
    """
    suggestion = _make_suggestion(
        "MAX_PARABOLIC_SQUEEZE",
        current_value=0.2,
        suggested_value=0.7,
        risk_direction="tightens",
    )

    result = ai_advisor.check_risk_direction_agreement(suggestion)

    assert result["agrees"] is False
    assert result["code_direction"] == "loosens"
    assert result["claimed_direction"] == "tightens"


def test_risk_agreement_confirms_correct_claim():
    """Claude claims ``loosens`` and the code agrees — ``agrees: True`` with
    both directions reported as ``loosens``.
    """
    suggestion = _make_suggestion(
        "MAX_PARABOLIC_SQUEEZE",
        current_value=0.2,
        suggested_value=0.7,
        risk_direction="loosens",
    )

    result = ai_advisor.check_risk_direction_agreement(suggestion)

    assert result["agrees"] is True
    assert result["code_direction"] == "loosens"
    assert result["claimed_direction"] == "loosens"


def test_risk_agreement_flags_inverted_param_contradiction():
    """The inverted-polarity param is the easiest place for Claude to
    mis-self-classify: raising TAKE_PROFIT_MC_PCT *tightens* risk, but a naive
    "bigger number = looser" intuition says ``loosens``. The gate must catch
    a ``loosens`` claim here as a contradiction.
    """
    suggestion = _make_suggestion(
        "TAKE_PROFIT_MC_PCT",
        current_value=3.0,
        suggested_value=8.0,
        risk_direction="loosens",
    )

    result = ai_advisor.check_risk_direction_agreement(suggestion)

    assert result["agrees"] is False
    assert result["code_direction"] == "tightens"
    assert result["claimed_direction"] == "loosens"


def test_risk_agreement_neutral_noop_when_claimed_neutral():
    """An unchanged value with a ``neutral`` claim must agree — both sides
    compute ``neutral``.
    """
    suggestion = _make_suggestion(
        "VWAP_BLEED_TICKS",
        current_value=10,
        suggested_value=10,
        risk_direction="neutral",
    )

    result = ai_advisor.check_risk_direction_agreement(suggestion)

    assert result["agrees"] is True
    assert result["code_direction"] == "neutral"
    assert result["claimed_direction"] == "neutral"


# ===========================================================================
# Test 4 + 5 — revalidate_suggestion_oos: passing and failing OOS cases.
# run_simulation is mocked. The lazy import inside revalidate_suggestion_oos
# does ``from autotuner import run_simulation`` at call time, so patching
# ``autotuner.run_simulation`` (the source attribute) is what intercepts it.
# ===========================================================================

@pytest.fixture
def current_strategy() -> dict:
    """A representative current per-symphony strategy dict — the OOS gate's
    baseline. Values are illustrative; the test never asserts these literals,
    only the pass/fail decision derived from the mocked simulation output.
    """
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
        "MAX_SQUEEZE_FLOOR": 0.20,
    }


def test_revalidate_oos_passes_when_suggestion_beats_baseline(current_strategy):
    """A suggestion whose OOS alpha BEATS the baseline OOS alpha must pass the
    gate -> ``passed: True``.

    ``run_simulation`` is called twice (baseline strategy, then the patched
    strategy). We make the suggested-strategy call return the higher alpha.
    The test asserts only the pass/fail DECISION and the relative ordering of
    the two returned alphas — never a hardcoded alpha literal.
    """
    baseline_alpha = 1.0
    suggested_alpha = 2.5  # strictly beats baseline

    # First call -> baseline, second call -> suggested config.
    with patch(
        "autotuner.run_simulation",
        side_effect=[baseline_alpha, suggested_alpha],
    ) as mock_sim:
        result = ai_advisor.revalidate_suggestion_oos(
            symphony_id="sym-A",
            config_key="MAX_SQUEEZE_FLOOR",
            suggested_value=0.30,
            current_strategy=current_strategy,
        )

    assert result["passed"] is True
    assert result["oos_alpha"] > result["baseline_oos_alpha"]
    assert isinstance(result["detail"], str) and result["detail"]
    assert mock_sim.call_count == 2


def test_revalidate_oos_fails_when_suggestion_worse_than_baseline(
    current_strategy,
):
    """A suggestion whose OOS alpha is WORSE than baseline must NOT be
    greenlit -> ``passed: False``.

    This is the load-bearing safety property: an operator-accepted suggestion
    that degrades OOS performance must be blocked from reaching live config,
    exactly the gate Optuna's own output faces.
    """
    baseline_alpha = 2.0
    suggested_alpha = 0.5  # strictly worse than baseline

    with patch(
        "autotuner.run_simulation",
        side_effect=[baseline_alpha, suggested_alpha],
    ) as mock_sim:
        result = ai_advisor.revalidate_suggestion_oos(
            symphony_id="sym-A",
            config_key="MAX_SQUEEZE_FLOOR",
            suggested_value=0.45,
            current_strategy=current_strategy,
        )

    assert result["passed"] is False
    assert result["oos_alpha"] < result["baseline_oos_alpha"]
    assert isinstance(result["detail"], str) and result["detail"]
    assert mock_sim.call_count == 2


def test_revalidate_oos_tie_does_not_pass(current_strategy):
    """A suggestion that merely TIES the baseline OOS alpha must not pass —
    the autotuner's own cascade uses a strict-positive rule (oos must strictly
    beat the baseline). A tie buys no validated improvement, so it must not be
    greenlit for a live config write.
    """
    equal_alpha = 1.5

    with patch(
        "autotuner.run_simulation",
        side_effect=[equal_alpha, equal_alpha],
    ):
        result = ai_advisor.revalidate_suggestion_oos(
            symphony_id="sym-A",
            config_key="MAX_SQUEEZE_FLOOR",
            suggested_value=0.25,
            current_strategy=current_strategy,
        )

    assert result["passed"] is False


# ===========================================================================
# Test 6 — lazy-import guard: importing ai_advisor must NOT pull in autotuner.
# Pins the import-collision fix (optuna+joblib side effects vs the anthropic
# SDK import under pytest capture).
# ===========================================================================

def test_importing_ai_advisor_does_not_import_autotuner_at_module_scope():
    """``import ai_advisor`` must NOT transitively import ``autotuner``.

    autotuner pulls in optuna + joblib, whose process-pool / resource-tracker
    import side effects collide with the anthropic SDK import under pytest's
    output capture. ``revalidate_suggestion_oos`` MUST import autotuner lazily
    inside its own body, deferring it past that fragile window.

    We drop both modules from sys.modules, re-import ai_advisor fresh, and
    assert autotuner did not come along for the ride.
    """
    saved = {
        name: sys.modules.get(name)
        for name in ("ai_advisor", "autotuner")
    }
    try:
        sys.modules.pop("ai_advisor", None)
        sys.modules.pop("autotuner", None)

        importlib.import_module("ai_advisor")

        assert "autotuner" not in sys.modules, (
            "importing ai_advisor must NOT import autotuner at module scope — "
            "revalidate_suggestion_oos must use a lazy `from autotuner import "
            "run_simulation` inside the function body"
        )
    finally:
        # Restore the original module objects so later tests see a stable
        # import state (no leaked fresh-import side effects).
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)


def test_ai_advisor_module_has_no_autotuner_attribute_until_function_runs():
    """Belt-and-suspenders companion to the sys.modules check: ai_advisor must
    not bind ``autotuner`` (or ``run_simulation``) as a module-level name. The
    only legitimate place the name appears is inside
    ``revalidate_suggestion_oos``'s body.
    """
    assert not hasattr(ai_advisor, "autotuner"), (
        "ai_advisor must not hold a module-scope `autotuner` reference"
    )
    assert not hasattr(ai_advisor, "run_simulation"), (
        "ai_advisor must not hold a module-scope `run_simulation` reference — "
        "it belongs inside revalidate_suggestion_oos as a lazy import"
    )


# ===========================================================================
# Test 7 — FU-C1-1 drift guard: ai_advisor's mirrored constant must equal the
# autotuner's OPTUNA_SEARCH_SPACE_KEYS. Source-inspection only — NO
# ``import autotuner`` (importing it is exactly the collision the lazy-import
# fix avoids). Pattern precedent:
# tests/autotuner/test_oos_baseline_selection.py:744.
# ===========================================================================

def test_optuna_search_space_keys_match_autotuner_source():
    """Drift guard: ``ai_advisor._OPTUNA_SEARCH_SPACE_KEYS`` is a deliberate
    mirror of ``autotuner.OPTUNA_SEARCH_SPACE_KEYS`` (it is NOT imported, to
    avoid the optuna/joblib import collision). If the autotuner's frozenset is
    edited, this test fails so the mirror is updated in lock-step — the exact
    drift the C1 reviewer flagged.

    We parse autotuner.py as TEXT (no import) and extract the literal contents
    of the OPTUNA_SEARCH_SPACE_KEYS frozenset, then assert set-equality with
    ai_advisor's mirror. Follows the source-inspection precedent at
    tests/autotuner/test_oos_baseline_selection.py:744.
    """
    import pathlib
    import re

    autotuner_path = (
        pathlib.Path(ai_advisor.__file__).parent / "autotuner.py"
    )
    source = autotuner_path.read_text(encoding="utf-8")

    # Grab the frozenset({...}) block assigned to OPTUNA_SEARCH_SPACE_KEYS.
    match = re.search(
        r"OPTUNA_SEARCH_SPACE_KEYS\s*=\s*frozenset\(\{(.*?)\}\)",
        source,
        re.DOTALL,
    )
    assert match is not None, (
        "could not locate `OPTUNA_SEARCH_SPACE_KEYS = frozenset({...})` in "
        "autotuner.py — has the constant been renamed or restructured?"
    )

    # Extract every double- or single-quoted string literal from the block.
    autotuner_keys = set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))

    assert autotuner_keys, (
        "parsed an empty OPTUNA_SEARCH_SPACE_KEYS frozenset from autotuner.py"
    )
    assert autotuner_keys == set(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS), (
        "ai_advisor._OPTUNA_SEARCH_SPACE_KEYS has drifted from "
        "autotuner.OPTUNA_SEARCH_SPACE_KEYS — update the mirror in ai_advisor "
        f"to match.\n  autotuner: {sorted(autotuner_keys)}\n  ai_advisor: "
        f"{sorted(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS)}"
    )
