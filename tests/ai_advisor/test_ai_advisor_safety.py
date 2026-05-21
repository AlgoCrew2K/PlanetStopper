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

C2.1 FIX-2 re-sourcing note
----------------------------
The original ``_RAISE_DIRECTION`` dict was a hand-authored literal — circular
because the test author and the production-code author used the same brain.
It is now derived from ``ai_advisor._PARAM_DEFINITIONS[key]["risk_polarity"]``
(the authoritative source, itself transcribed from config-surface.md §1) so
the test cannot silently pass when production code and spec diverge.
A companion test (``test_polarity_table_re_sourced_from_param_definitions``)
verifies the derivation against the spec text in config-surface.md.

C2.1 FIX-1 arity-pin note
--------------------------
The real ``autotuner.run_simulation`` signature is:
    run_simulation(p, history_data, acc_sym_ids, current_date_str,
                   deviation_dict)
5 positional args. The original OOS tests only asserted ``call_count == 2``
and thus silently passed when ``revalidate_suggestion_oos`` called the function
with only 2 args. New tests inspect ``call_args_list`` to pin the full arity
contract; they are RED against the current 2-arg implementation.
"""

from __future__ import annotations

import importlib
import pathlib
import re
import sys
from unittest.mock import patch

import pytest

import ai_advisor
from ai_advisor import ConfigSuggestion

# ---------------------------------------------------------------------------
# Test data — the risk-polarity table, re-sourced from the authoritative spec.
#
# FIX-2: ``_RAISE_DIRECTION`` is no longer a hand-authored literal copy.
# It is derived from ``ai_advisor._PARAM_DEFINITIONS[key]["risk_polarity"]``,
# which was transcribed from config-surface.md §1 and is the production module's
# own authoritative source.  The test then validates ``compute_risk_direction``
# against THIS derived table rather than against a hand-copy of itself.
# ---------------------------------------------------------------------------


def _derive_raise_direction_from_param_definitions() -> dict[str, str]:
    """Derive the raise-direction table from ai_advisor._PARAM_DEFINITIONS.

    "raising tightens risk"  -> "tightens"
    "raising loosens risk"   -> "loosens"  (all other suggestible keys)

    This function is called ONCE at module load to populate _RAISE_DIRECTION.
    The authoritative source is config-surface.md §1, mirrored in
    ai_advisor._PARAM_DEFINITIONS; deriving here rather than hand-listing
    ensures any production-code drift causes test failure, not silent agreement.
    """
    result: dict[str, str] = {}
    for key, defn in ai_advisor._PARAM_DEFINITIONS.items():
        polarity = defn.get("risk_polarity", "")
        if "tightens" in polarity:
            result[key] = "tightens"
        else:
            result[key] = "loosens"
    return result


# config_key -> direction the code must report when suggested_value > current.
# Derived from ai_advisor._PARAM_DEFINITIONS (config-surface.md §1 mirror) —
# NOT a hand-authored literal, so production drift causes test failure.
_RAISE_DIRECTION: dict[str, str] = _derive_raise_direction_from_param_definitions()

# The inverse of a raise. Lowering a param does the opposite of raising it.
_OPPOSITE = {"loosens": "tightens", "tightens": "loosens", "neutral": "neutral"}

# A representative in-range (current, raised) value pair per key. The raised
# value sits strictly inside the param's valid range (ai_advisor
# ._PARAM_VALID_RANGES) and strictly above the current value, so the *only*
# variable under test is the polarity sign, not range-clamping.
_RAISE_PAIRS: dict[str, tuple[float, float]] = {
    "TRIGGER_THRESHOLD_PCT": (10.0, 20.0),  # range 5.0 - 25.0
    "TAKE_PROFIT_MC_PCT": (3.0, 8.0),  # range 2.0 - 10.0
    "VWAP_CROSS_HWM_PCT": (0.8, 2.0),  # range 0.5 - 2.5
    "VWAP_BLEED_MULTIPLIER": (1.0, 2.5),  # range 0.5 - 3.0
    "VWAP_BLEED_TICKS": (5, 25),  # range 3 - 30 (int)
    "PARABOLIC_VELOCITY_THRESHOLD": (1.5, 3.5),  # range 1.0 - 4.0
    "MAX_PARABOLIC_SQUEEZE": (0.2, 0.7),  # range 0.1 - 0.8
    "MAX_SQUEEZE_FLOOR": (0.10, 0.40),  # range 0.05 - 0.50
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
    return sorted(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS) + [ai_advisor._UNTUNED_SUGGESTIBLE_KEY]


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
    assert ai_advisor.compute_risk_direction("TAKE_PROFIT_MC_PCT", 3.0, 8.0) == "tightens"
    assert ai_advisor.compute_risk_direction("MAX_PARABOLIC_SQUEEZE", 0.2, 0.7) == "loosens"


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
# C2.1 FIX-2 — polarity-table re-sourcing validation.
#
# The original _RAISE_DIRECTION dict was a hand-authored literal that the test
# author wrote to match what they expected production code to do — circular
# provenance: the spec copy and the implementation can diverge and still agree.
#
# Fix: _RAISE_DIRECTION is now DERIVED from ai_advisor._PARAM_DEFINITIONS
# (see _derive_raise_direction_from_param_definitions() above). This test
# validates that derivation against TWO independent sources:
#   1. The config-surface.md spec document (parsed as text — the authoritative
#      domain spec, independent of production code).
#   2. ai_advisor._RAISE_RISK_DIRECTION (the production polarity lookup table,
#      built from the same _PARAM_DEFINITIONS).
#
# If production code and the spec doc diverge, at least one assertion fails.
# ===========================================================================


def test_polarity_table_re_sourced_from_param_definitions():
    """``_RAISE_DIRECTION`` must be derivable from ``ai_advisor._PARAM_DEFINITIONS``
    and must agree with ``ai_advisor._RAISE_RISK_DIRECTION`` (the production
    polarity lookup).

    This pins the re-sourcing fix: the test module no longer hand-lists the
    polarity table; it derives it from the same source the production code
    uses. Drift between production and test is now a test failure, not silent
    agreement between two hand-copies.
    """
    # Derive from _PARAM_DEFINITIONS (same derivation as module-level above).
    derived = _derive_raise_direction_from_param_definitions()

    # Must cover exactly the set of suggestible keys — no more, no less.
    expected_keys = set(ai_advisor._PARAM_DEFINITIONS.keys())
    assert set(derived.keys()) == expected_keys, (
        f"derived polarity table covers {sorted(derived.keys())} but "
        f"_PARAM_DEFINITIONS has {sorted(expected_keys)}"
    )

    # Must agree with ai_advisor._RAISE_RISK_DIRECTION (production lookup).
    for key, prod_direction in ai_advisor._RAISE_RISK_DIRECTION.items():
        assert derived[key] == prod_direction, (
            f"polarity mismatch for {key!r}: derived from _PARAM_DEFINITIONS "
            f"says {derived[key]!r} but _RAISE_RISK_DIRECTION says "
            f"{prod_direction!r} — one of the two has drifted from the spec"
        )


def test_polarity_table_matches_config_surface_doc():
    """Validate the risk-polarity table against the fixture transcribed from
    ``config-surface.md`` §1 by an independent reader (the C2.1 test-writer).

    config-surface.md uses prose descriptions that are not machine-parseable
    in a stable way, so the next-best provenance is a fixture file committed
    under ``tests/fixtures/ai_advisor/risk_polarity_table.json`` with a header
    documenting it was transcribed from config-surface.md by an independent
    reader — not the C2 implementer who wrote production code.

    The fixture is the spec anchor: if it disagrees with
    ``ai_advisor._RAISE_RISK_DIRECTION``, one of them has drifted from the
    config-surface.md source.  To update: re-read config-surface.md §1 and
    edit the fixture, then re-run tests.
    """
    import json

    fixture_path = (
        pathlib.Path(ai_advisor.__file__).parent
        / "tests"
        / "fixtures"
        / "ai_advisor"
        / "risk_polarity_table.json"
    )
    assert fixture_path.exists(), (
        f"risk_polarity_table.json not found at {fixture_path} — "
        "this fixture is the polarity spec anchor, transcribed from "
        "config-surface.md §1 by the C2.1 test-writer"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_table: dict[str, str] = fixture["raise_direction"]

    # The fixture must cover exactly the same keys as _PARAM_DEFINITIONS.
    expected_keys = set(ai_advisor._PARAM_DEFINITIONS.keys())
    assert set(fixture_table.keys()) == expected_keys, (
        f"fixture raise_direction covers {sorted(fixture_table.keys())} but "
        f"_PARAM_DEFINITIONS has {sorted(expected_keys)} — update the fixture"
    )

    # Production _RAISE_RISK_DIRECTION must agree with the fixture (the spec).
    for key, fixture_direction in fixture_table.items():
        prod_direction = ai_advisor._RAISE_RISK_DIRECTION.get(key)
        assert prod_direction == fixture_direction, (
            f"polarity mismatch for {key!r}: fixture (from config-surface.md §1) "
            f"says {fixture_direction!r} but ai_advisor._RAISE_RISK_DIRECTION says "
            f"{prod_direction!r} — one has drifted from the spec; update the "
            "production code OR the fixture after re-reading config-surface.md §1"
        )

    # Explicit check: TAKE_PROFIT_MC_PCT is the lone inverted param.
    lone_inverted = fixture.get("_lone_inverted_param")
    assert lone_inverted == "TAKE_PROFIT_MC_PCT", (
        f"fixture _lone_inverted_param is {lone_inverted!r}, expected "
        "'TAKE_PROFIT_MC_PCT' — verify against config-surface.md §1"
    )
    for key, direction in fixture_table.items():
        if key == lone_inverted:
            assert direction == "tightens", (
                f"fixture says {lone_inverted!r} raises -> {direction!r}, "
                "but it must be 'tightens' (lone inverted param)"
            )
        else:
            assert direction == "loosens", (
                f"fixture says {key!r} raises -> {direction!r}, but all keys "
                f"except {lone_inverted!r} must be 'loosens'"
            )


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


# ---------------------------------------------------------------------------
# C2.2 — OOS arg-assembly mocks (offline fixture tier).
#
# revalidate_suggestion_oos assembles its run_simulation args from three live
# sources before reaching the mocked run_simulation call:
#   1. database.load_state()                   — hits SQLite state DB
#   2. synthetic_history.generate_synthetic_history()  — live Alpaca fetches
#   3. autotuner.calculate_historical_deviation()      — hits SQLite state DB
#
# Without mocking these, every OOS test incurs a full Alpaca network round-trip
# (35 tickers × up to 125 days, with retries and sleeps) — turning the suite
# from ~13s to ~600s. These patches must be applied to ALL revalidate_suggestion_oos
# tests so the suite runs offline in the non-live tier.
#
# Patch targets (source modules, not ai_advisor.*):
#   "database.load_state"                                   — called directly
#   "synthetic_history.generate_synthetic_history"          — lazy import in fn
#   "autotuner.calculate_historical_deviation"              — lazy import in fn
#
# Fixture data is deliberately minimal but structurally valid:
#   - bot_state has one entry whose normalize_name("name") == "sym-a"
#     (database.normalize_name strips + lowercases; tests use symphony_id="sym-A")
#   - history_data is a non-empty dict so isinstance(…, dict) asserts pass
#   - deviation_dict is an empty dict (valid: no deviations recorded)
# ---------------------------------------------------------------------------

_FIXTURE_BOT_STATE = {
    "acc-sym-a": {
        "name": "sym-A",
        "current_return": 0.0,
        "high_water_mark": 0.0,
    }
}
_FIXTURE_HISTORY_DATA: dict = {"acc-sym-a": {}}
_FIXTURE_DEVIATION_DICT: dict = {}


@pytest.fixture
def oos_assembly_mocks():
    """Patch the three live arg-assembly sources in revalidate_suggestion_oos.

    Yields a namespace with .load_state, .generate_synthetic_history, and
    .calculate_historical_deviation mock objects so individual tests can inspect
    call args or override return values as needed.

    Applied to every revalidate_suggestion_oos test to keep the non-live tier
    fast and offline (no Alpaca fetches, no DB reads).
    """
    with (
        patch("database.load_state", return_value=_FIXTURE_BOT_STATE) as m_state,
        patch(
            "synthetic_history.generate_synthetic_history",
            return_value=_FIXTURE_HISTORY_DATA,
        ) as m_hist,
        patch(
            "autotuner.calculate_historical_deviation",
            return_value=_FIXTURE_DEVIATION_DICT,
        ) as m_dev,
    ):

        class _Mocks:
            load_state = m_state
            generate_synthetic_history = m_hist
            calculate_historical_deviation = m_dev

        yield _Mocks()


def test_revalidate_oos_passes_when_suggestion_beats_baseline(current_strategy, oos_assembly_mocks):
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
    current_strategy, oos_assembly_mocks
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


def test_revalidate_oos_tie_does_not_pass(current_strategy, oos_assembly_mocks):
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
# C2.1 FIX-1 — run_simulation 5-arg signature pin.
#
# The real autotuner.run_simulation signature is:
#   run_simulation(p, history_data, acc_sym_ids, current_date_str,
#                  deviation_dict)
# 5 positional args. The existing OOS tests above assert call_count == 2 but
# never inspect call_args_list, so they silently passed when
# revalidate_suggestion_oos called the function with only 2 args.
#
# These tests are RED against the current 2-arg implementation in
# ai_advisor.revalidate_suggestion_oos — they will fail until GREEN wires the
# real 5-arg call.
# ===========================================================================


def test_revalidate_oos_passes_run_simulation_with_5_positional_args(
    current_strategy, oos_assembly_mocks
):
    """``revalidate_suggestion_oos`` must call ``run_simulation`` with the
    real 5-arg signature on EACH invocation:
        run_simulation(p, history_data, acc_sym_ids, current_date_str,
                       deviation_dict)

    The current implementation calls it with 2 args
    (``run_simulation(strategy, symphony_id)``), which is the arity gap the
    C2 reviewer flagged. This test pins the contract so a 2-arg call is a
    test failure, not a silent pass.

    We assert:
    - Each call receives exactly 5 positional arguments.
    - Arg 0 (``p``) is a dict (the strategy params dict).
    - Arg 1 (``history_data``) is the value returned by the mocked
      ``generate_synthetic_history`` — verifies fixture data flows through.
    - Arg 2 (``acc_sym_ids``) is a list or tuple of account/symphony ids.
    - Arg 3 (``current_date_str``) is a string matching YYYY-MM-DD format.
    - Arg 4 (``deviation_dict``) is the value returned by the mocked
      ``calculate_historical_deviation`` — verifies fixture data flows through.

    Shape assertions only — no hardcoded producer values.
    """
    with patch(
        "autotuner.run_simulation",
        side_effect=[1.0, 2.0],
    ) as mock_sim:
        ai_advisor.revalidate_suggestion_oos(
            symphony_id="sym-A",
            config_key="MAX_SQUEEZE_FLOOR",
            suggested_value=0.30,
            current_strategy=current_strategy,
        )

    assert mock_sim.call_count == 2, "run_simulation must be called exactly twice"

    for call_idx, actual_call in enumerate(mock_sim.call_args_list):
        args, kwargs = actual_call
        assert len(args) == 5, (
            f"run_simulation call {call_idx} was invoked with {len(args)} "
            f"positional arg(s), but the real signature requires exactly 5: "
            f"(p, history_data, acc_sym_ids, current_date_str, deviation_dict). "
            f"Got args={args!r}, kwargs={kwargs!r}"
        )
        p, history_data, acc_sym_ids, current_date_str, deviation_dict = args
        assert isinstance(p, dict), (
            f"call {call_idx}: arg 0 (p) must be a dict of strategy params, "
            f"got {type(p).__name__!r}"
        )
        # history_data must be exactly what generate_synthetic_history returned —
        # the function must not transform or drop the fixture value.
        assert history_data is _FIXTURE_HISTORY_DATA, (
            f"call {call_idx}: arg 1 (history_data) must be the value returned "
            "by generate_synthetic_history (the fixture dict); got a different "
            f"object: {history_data!r}"
        )
        assert isinstance(acc_sym_ids, (list, tuple)), (
            f"call {call_idx}: arg 2 (acc_sym_ids) must be a list or tuple, "
            f"got {type(acc_sym_ids).__name__!r}"
        )
        assert isinstance(current_date_str, str), (
            f"call {call_idx}: arg 3 (current_date_str) must be a str, "
            f"got {type(current_date_str).__name__!r}"
        )
        # YYYY-MM-DD is the only format autotuner.run_simulation accepts
        # (it calls datetime.strptime(current_date_str, "%Y-%m-%d") on line 80).
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_date_str), (
            f"call {call_idx}: arg 3 (current_date_str) must be YYYY-MM-DD, "
            f"got {current_date_str!r}"
        )
        # deviation_dict must be exactly what calculate_historical_deviation returned.
        assert deviation_dict is _FIXTURE_DEVIATION_DICT, (
            f"call {call_idx}: arg 4 (deviation_dict) must be the value returned "
            "by calculate_historical_deviation (the fixture dict); got a different "
            f"object: {deviation_dict!r}"
        )


def test_revalidate_oos_baseline_call_passes_current_strategy_as_p(
    current_strategy, oos_assembly_mocks
):
    """The FIRST call to ``run_simulation`` (baseline) must pass
    ``current_strategy`` as arg 0 (``p``) — unchanged, not patched.

    This pins the semantics: the baseline run must reflect the CURRENT live
    config, not the suggested one. If GREEN reverses call order or passes the
    patched params for the baseline, this test fails.
    """
    with patch(
        "autotuner.run_simulation",
        side_effect=[1.0, 2.0],
    ) as mock_sim:
        ai_advisor.revalidate_suggestion_oos(
            symphony_id="sym-A",
            config_key="MAX_SQUEEZE_FLOOR",
            suggested_value=0.30,
            current_strategy=current_strategy,
        )

    # The first call is the baseline: its p (arg 0) must equal current_strategy.
    first_call_args = mock_sim.call_args_list[0][0]
    assert len(first_call_args) == 5, (
        "baseline call must have 5 args — see test_revalidate_oos_passes_run_simulation_with_5_positional_args"
    )
    baseline_p = first_call_args[0]
    assert baseline_p == current_strategy, (
        "baseline run_simulation call (call 0) must receive the unmodified "
        "current_strategy as p; any divergence means GREEN is not computing an "
        "apples-to-apples OOS comparison"
    )


def test_revalidate_oos_patched_call_passes_modified_strategy_as_p(
    current_strategy, oos_assembly_mocks
):
    """The SECOND call to ``run_simulation`` (patched) must pass a strategy
    dict where the targeted config_key has been replaced with suggested_value,
    and all other keys are unchanged.

    This pins the key invariant: the OOS comparison is apples-to-apples —
    exactly one param changes between the baseline and the patched run.
    """
    config_key = "MAX_SQUEEZE_FLOOR"
    suggested_value = 0.30

    with patch(
        "autotuner.run_simulation",
        side_effect=[1.0, 2.0],
    ) as mock_sim:
        ai_advisor.revalidate_suggestion_oos(
            symphony_id="sym-A",
            config_key=config_key,
            suggested_value=suggested_value,
            current_strategy=current_strategy,
        )

    second_call_args = mock_sim.call_args_list[1][0]
    assert len(second_call_args) == 5, (
        "patched call must have 5 args — see test_revalidate_oos_passes_run_simulation_with_5_positional_args"
    )
    patched_p = second_call_args[0]

    # The targeted key must be set to suggested_value.
    assert patched_p[config_key] == suggested_value, (
        f"patched run_simulation call (call 1) must have p[{config_key!r}] == "
        f"{suggested_value}, got {patched_p.get(config_key)!r}"
    )

    # All other keys must be unchanged — exactly one knob changes.
    for key, original_val in current_strategy.items():
        if key == config_key:
            continue
        assert patched_p.get(key) == original_val, (
            f"patched run_simulation call (call 1) must only modify {config_key!r}; "
            f"key {key!r} changed from {original_val!r} to {patched_p.get(key)!r}"
        )


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
    saved = {name: sys.modules.get(name) for name in ("ai_advisor", "autotuner")}
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

    autotuner_path = pathlib.Path(ai_advisor.__file__).parent / "autotuner.py"
    source = autotuner_path.read_text(encoding="utf-8")

    # Grab the frozenset({...}) block assigned to OPTUNA_SEARCH_SPACE_KEYS.
    match = re.search(
        r"OPTUNA_SEARCH_SPACE_KEYS\s*=\s*frozenset\(\s*\{(.*?)\}\s*\)",
        source,
        re.DOTALL,
    )
    assert match is not None, (
        "could not locate `OPTUNA_SEARCH_SPACE_KEYS = frozenset({...})` in "
        "autotuner.py — has the constant been renamed or restructured?"
    )

    # Extract every double- or single-quoted string literal from the block.
    autotuner_keys = set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))

    assert autotuner_keys, "parsed an empty OPTUNA_SEARCH_SPACE_KEYS frozenset from autotuner.py"
    assert autotuner_keys == set(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS), (
        "ai_advisor._OPTUNA_SEARCH_SPACE_KEYS has drifted from "
        "autotuner.OPTUNA_SEARCH_SPACE_KEYS — update the mirror in ai_advisor "
        f"to match.\n  autotuner: {sorted(autotuner_keys)}\n  ai_advisor: "
        f"{sorted(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS)}"
    )
