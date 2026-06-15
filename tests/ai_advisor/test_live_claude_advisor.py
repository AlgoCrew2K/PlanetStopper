"""
Cycle C1 — Tier 2: LIVE contract test for the Claude AI Config Advisor.

This is the RUNTIME VALIDATOR that keeps the schema-derived fixture
(tests/fixtures/ai_advisor/sample_claude_response.json) honest. Tier 1 mocks
the anthropic SDK and trusts a hand-authored ConfigSuggestionsResponse
instance; this test hits the REAL Claude API with a minimal real context and
asserts the real response conforms to the SAME Pydantic schema — the fields
exist, the types are right, risk_direction is in the allowed set.

If Anthropic changes the structured-output behaviour, or our schema drifts
from what the model actually returns, this test fails loudly and tells us the
schema-derived fixture has gone stale.

It is NOT run in normal CI:
  * every test here is marked `@pytest.mark.live`
  * the `/run-tests` skill / pytest default deselects `-m 'not live'`
  * opt in explicitly with  `pytest -m live`  (or `/run-tests --include-live`)

It also self-skips cleanly if `ANTHROPIC_API_KEY` is absent from the
environment — the key is an operator prerequisite, so an accidental
`-m live` run on a machine with no key skips rather than errors.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def _anthropic_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.fixture(scope="module")
def live_suggestions_response():
    """Call the REAL Claude API once for the module with a minimal context.

    Uses ai_advisor's own context-assembly + request_suggestions so the test
    exercises the real end-to-end path, not a hand-rolled API call.
    """
    if not _anthropic_key_present():
        pytest.skip("ANTHROPIC_API_KEY not in environment — skipping live Claude test")

    import ai_advisor

    # A minimal but well-shaped context. We do NOT depend on a live DB or a
    # live Composer call here — the point of the live test is the Claude API
    # contract, not the data accessors (those are covered Tier 1). If
    # ai_advisor exposes a way to build a context from explicit data, prefer
    # it; otherwise assemble a minimal dict matching the context contract.
    context = ai_advisor.assemble_advisor_context(scope="global", symphony_id=None)

    response, error = ai_advisor.request_suggestions(context)
    # The live call should succeed with a real key; if it fails, surface it.
    assert error is None, (
        f"live Claude call returned an error: {error!r}. If this is a "
        f"transient outage, re-run; if it persists, the integration has "
        f"drifted."
    )
    assert response is not None, "live Claude call returned no response"
    return response


def test_live_response_is_config_suggestions_response(live_suggestions_response):
    """The real Claude response parses into the ConfigSuggestionsResponse
    Pydantic schema — this is the runtime validator for the schema-derived
    Tier 1 fixture."""
    import ai_advisor

    assert isinstance(live_suggestions_response, ai_advisor.ConfigSuggestionsResponse), (
        "the live Claude response must parse into ConfigSuggestionsResponse — "
        "if it does not, the structured-output schema has drifted from what "
        "the model returns"
    )


def test_live_response_suggestions_is_a_list(live_suggestions_response):
    """`suggestions` is a list (possibly empty — abstention is valid)."""
    assert isinstance(live_suggestions_response.suggestions, list), (
        "ConfigSuggestionsResponse.suggestions must be a list"
    )


def test_live_response_suggestions_conform_to_schema(live_suggestions_response):
    """Every suggestion the live model returned has all seven fields, correctly
    typed, with risk_direction in the allowed set. An empty list is allowed
    (Claude validly abstaining) — in that case this test is vacuously true,
    which is correct: there is nothing to type-check."""
    for s in live_suggestions_response.suggestions:
        assert isinstance(s.config_key, str) and s.config_key, (
            "live suggestion config_key must be a non-empty string"
        )
        assert isinstance(s.current_value, (float, int, str)), (
            "live suggestion current_value must be float | int | str"
        )
        assert isinstance(s.suggested_value, (float, int, str)), (
            "live suggestion suggested_value must be float | int | str"
        )
        assert isinstance(s.rationale, str) and s.rationale.strip(), (
            "live suggestion rationale must be a non-empty string"
        )
        assert s.risk_direction in {"loosens", "tightens", "neutral"}, (
            f"live suggestion risk_direction must be loosens/tightens/"
            f"neutral; got {s.risk_direction!r} — schema drift or prompt "
            f"under-constraint"
        )
        assert isinstance(s.confidence, str) and s.confidence, (
            "live suggestion confidence must be a non-empty string"
        )
        assert isinstance(s.data_sufficiency, str) and s.data_sufficiency, (
            "live suggestion data_sufficiency must be a non-empty string"
        )


# ===========================================================================
# C2.2 — live contract test for revalidate_suggestion_oos real assembly path.
#
# The non-live tier mocks database.load_state, generate_synthetic_history, and
# calculate_historical_deviation. This test exercises the REAL assembly path:
# it verifies that the function runs end-to-end with real data sources and that
# the run_simulation call receives structurally valid 5-arg inputs. run_simulation
# itself is still mocked (to avoid a full 125-day sim in a live contract test).
# ===========================================================================


def test_live_revalidate_oos_assembly_reaches_run_simulation_with_5_args():
    """The real ``revalidate_suggestion_oos`` assembly path (live DB + live
    Alpaca history fetch) must deliver 5 structurally valid args to
    ``run_simulation``.

    This is the live-tier companion to
    ``test_revalidate_oos_passes_run_simulation_with_5_positional_args``.
    That test mocks the assembly sources; this test runs them for real and
    pins that the assembled args are non-empty and correctly shaped.

    ``run_simulation`` itself is still mocked — the 125-day simulation is
    autotuner's concern, not the contract being tested here. What we are
    validating is that the data-fetching plumbing (DB reads + Alpaca fetch)
    returns something that can be passed to run_simulation without crashing.

    Self-skips if no symphony rows exist in the state DB (cold machine with
    no live state) so it doesn't fail gratuitously in a dev environment.
    """
    import re as _re
    from unittest.mock import patch

    import ai_advisor
    import database

    if not _anthropic_key_present():
        pytest.skip("ANTHROPIC_API_KEY not in environment — skipping live OOS assembly test")

    # Pick a symphony_id from the live state DB. Skip if none available.
    bot_state = database.load_state()
    symphony_entries = [
        (k, v) for k, v in bot_state.items() if isinstance(v, dict) and v.get("name")
    ]
    if not symphony_entries:
        pytest.skip("no symphony entries in live bot_state — skipping live OOS assembly test")

    acc_key, acc_data = symphony_entries[0]
    symphony_id = database.normalize_name(acc_data["name"])

    current_strategy = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
        "MAX_SQUEEZE_FLOOR": 0.20,
    }

    with patch("autotuner.run_simulation", return_value=1.0) as mock_sim:
        result = ai_advisor.revalidate_suggestion_oos(
            symphony_id=symphony_id,
            config_key="MAX_SQUEEZE_FLOOR",
            suggested_value=0.30,
            current_strategy=current_strategy,
        )

    assert mock_sim.call_count == 2, (
        "run_simulation must be called exactly twice in the real assembly path"
    )
    for call_idx, actual_call in enumerate(mock_sim.call_args_list):
        args = actual_call[0]
        assert len(args) == 5, (
            f"live assembly: run_simulation call {call_idx} received {len(args)} "
            "args instead of 5 — real data-fetch path is broken"
        )
        p, history_data, acc_sym_ids, current_date_str, deviation_dict = args
        assert isinstance(p, dict) and p, (
            f"live assembly call {call_idx}: p must be a non-empty strategy dict"
        )
        assert isinstance(history_data, dict), (
            f"live assembly call {call_idx}: history_data must be a dict"
        )
        assert isinstance(acc_sym_ids, (list, tuple)), (
            f"live assembly call {call_idx}: acc_sym_ids must be list or tuple"
        )
        assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_date_str), (
            f"live assembly call {call_idx}: current_date_str must be YYYY-MM-DD, "
            f"got {current_date_str!r}"
        )
        assert isinstance(deviation_dict, dict), (
            f"live assembly call {call_idx}: deviation_dict must be a dict"
        )
    assert isinstance(result, dict) and "passed" in result
