"""RED tests -- advisors/retirement_checklist.py (AC-6, AC-9).

feature-plans/retirement-approval-lifecycle.md AC-6: build_checklist(
recommendation: dict, bot_state: dict) -> dict returns a deterministic,
advisory wind-down checklist -- NO LLM. Return shape (pinned in
.claude/tdd-handoff.md):
    {
        "candidate_id": str,
        "candidate_name": str | None,
        "tickers": list[str],
        "steps": list[str],
        "holdings_unavailable": bool,
    }

Ticker extraction from bot_state[candidate_id]["logic_holdings"] is
defensive over weight-representation shape (float vs {"weight": x}). Honest
off-hours degrade: an empty/missing logic_holdings sets
holdings_unavailable=True and NEVER fabricates a ticker.

Expected state: RED until advisors/retirement_checklist.py exists.
"""

from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE_REL_PATH = "advisors/retirement_checklist.py"

_SAMPLE_REC = {
    "candidate_id": "sym-candidate-1",
    "sibling_id": "sym-sibling-1",
    "correlation": 0.8,
    "basis_label": "actual-traded (bot) daily returns",
}


def _read_source() -> str:
    path = REPO_ROOT / _MODULE_REL_PATH
    if not path.exists():
        pytest.fail(f"expected module source not found: {_MODULE_REL_PATH}")
    return path.read_text(encoding="utf-8")


# ===========================================================================
# Module existence + public API
# ===========================================================================


def test_module_is_importable():
    import advisors.retirement_checklist as rc_mod  # noqa: F401


def test_module_exposes_build_checklist_callable():
    import advisors.retirement_checklist as rc_mod

    assert callable(getattr(rc_mod, "build_checklist", None))


# ===========================================================================
# Deterministic output, return-shape pin
# ===========================================================================


def test_returns_all_five_pinned_keys():
    import advisors.retirement_checklist as rc_mod

    bot_state = {"sym-candidate-1": {"name": "Candidate Symphony", "logic_holdings": {"AAPL": 0.5}}}
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)

    expected_keys = {"candidate_id", "candidate_name", "tickers", "steps", "holdings_unavailable"}
    assert expected_keys <= set(result.keys()), (
        f"build_checklist must return at least {expected_keys}, got {sorted(result.keys())}."
    )


def test_candidate_id_is_echoed_from_the_recommendation():
    import advisors.retirement_checklist as rc_mod

    bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {"AAPL": 1.0}}}
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
    assert result["candidate_id"] == "sym-candidate-1"


def test_candidate_name_resolved_from_bot_state_when_present():
    import advisors.retirement_checklist as rc_mod

    bot_state = {"sym-candidate-1": {"name": "My Candidate Symphony", "logic_holdings": {"AAPL": 1.0}}}
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
    assert result["candidate_name"] == "My Candidate Symphony"


def test_candidate_name_none_when_not_resolvable():
    import advisors.retirement_checklist as rc_mod

    bot_state = {}  # candidate not present at all
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
    assert result["candidate_name"] is None


def test_steps_are_a_non_empty_fixed_list_referencing_composer():
    """AC-6: 'the fixed manual steps (advisory prose the operator performs
    in Composer)' -- steps must be present regardless of holdings
    availability, and must reference Composer (the manual wind-down venue)."""
    import advisors.retirement_checklist as rc_mod

    bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {"AAPL": 1.0}}}
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)

    assert isinstance(result["steps"], list) and len(result["steps"]) > 0, (
        "steps must be a non-empty list."
    )
    joined_steps = " ".join(result["steps"])
    assert "Composer" in joined_steps, (
        f"The checklist steps must reference Composer (the manual venue), got: {result['steps']!r}"
    )


# ===========================================================================
# Ticker extraction -- weight-shape variance (the AC-6 defensive requirement)
# ===========================================================================


class TestTickerExtractionWeightShapeVariance:
    def test_float_weight_shape(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"logic_holdings": {"AAPL": 0.5, "MSFT": 0.5}}}
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert set(result["tickers"]) == {"AAPL", "MSFT"}
        assert result["holdings_unavailable"] is False

    def test_dict_wrapped_weight_shape(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {
            "sym-candidate-1": {
                "logic_holdings": {"AAPL": {"weight": 0.5}, "MSFT": {"weight": 0.5}}
            }
        }
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert set(result["tickers"]) == {"AAPL", "MSFT"}
        assert result["holdings_unavailable"] is False

    def test_mixed_float_and_dict_weight_shape_in_the_same_holdings(self):
        """Adversarial: a single logic_holdings dict mixing both
        representations across different tickers must still extract the
        full ticker set -- a real defensive-coding failure mode is only
        handling ONE shape and silently dropping tickers in the other."""
        import advisors.retirement_checklist as rc_mod

        bot_state = {
            "sym-candidate-1": {
                "logic_holdings": {
                    "AAPL": 0.4,
                    "MSFT": {"weight": 0.35},
                    "GOOGL": 0.25,
                }
            }
        }
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert set(result["tickers"]) == {"AAPL", "MSFT", "GOOGL"}, (
            f"Mixed weight-shape holdings must yield the FULL ticker set, "
            f"got {result['tickers']!r}."
        )


# ===========================================================================
# Honest off-hours degrade -- never fabricate tickers
# ===========================================================================


class TestOffHoursDegrade:
    def test_empty_logic_holdings_marks_unavailable_and_yields_no_tickers(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {}}}
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert result["holdings_unavailable"] is True
        assert result["tickers"] == [], "An empty logic_holdings must never fabricate tickers."

    def test_missing_logic_holdings_key_marks_unavailable(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"name": "X"}}  # no logic_holdings key at all
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert result["holdings_unavailable"] is True
        assert result["tickers"] == []

    def test_candidate_entirely_absent_from_bot_state_marks_unavailable(self):
        import advisors.retirement_checklist as rc_mod

        result = rc_mod.build_checklist(_SAMPLE_REC, {})
        assert result["holdings_unavailable"] is True
        assert result["tickers"] == []

    def test_off_hours_note_appears_in_steps_and_mentions_composer(self):
        """AC-6: 'an explicit "current holdings unavailable (off-hours) —
        view live positions in Composer" note' -- checked by substance
        (unavailable + Composer), not by pinning the exact wording verbatim,
        so a copy-edit doesn't spuriously break this test."""
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {}}}
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        joined = " ".join(result["steps"]).lower()
        assert "unavailable" in joined
        assert "composer" in joined


# ===========================================================================
# D-1 never-raises / robustness
# ===========================================================================


def test_malformed_recommendation_missing_candidate_id_never_raises():
    import advisors.retirement_checklist as rc_mod

    try:
        result = rc_mod.build_checklist({}, {})
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"build_checklist raised on a recommendation missing candidate_id: {exc!r}")
    assert result["holdings_unavailable"] is True


def test_bot_state_none_never_raises():
    """Adversarial: a caller passing bot_state=None (e.g. database.load_state()
    itself degraded) must not crash the checklist builder."""
    import advisors.retirement_checklist as rc_mod

    try:
        result = rc_mod.build_checklist(_SAMPLE_REC, None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"build_checklist raised on bot_state=None: {exc!r}")
    assert result["holdings_unavailable"] is True
    assert result["tickers"] == []


def test_logic_holdings_with_non_dict_entry_value_never_raises():
    """Adversarial: a holdings value that is neither a bare number nor a
    {'weight': x} dict (e.g. a bare string or None) must not crash ticker
    extraction -- degrade that single entry, never the whole call."""
    import advisors.retirement_checklist as rc_mod

    bot_state = {
        "sym-candidate-1": {
            "logic_holdings": {"AAPL": 0.5, "WEIRD": None, "ALSO_WEIRD": "not-a-number"}
        }
    }
    try:
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"build_checklist raised on a malformed logic_holdings entry: {exc!r}")
    assert "AAPL" in result["tickers"], "A well-formed sibling entry must still be extracted."


# ===========================================================================
# No LLM / no exec import (redundant fast local guard -- authoritative scan
# lives in tests/security/test_retirement_action_no_trade_boundary.py)
# ===========================================================================


def test_module_never_imports_ai_advisor_or_anthropic():
    source = _read_source()
    assert "ai_advisor" not in source
    assert "anthropic" not in source.lower()


def test_module_never_imports_composer_draft_client_or_alpha_bot_execution():
    source = _read_source()
    assert "composer_draft_client" not in source
    assert "alpha_bot_execution" not in source
