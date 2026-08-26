"""RED tests -- advisors/retirement_checklist.py (AC-6, AC-9).

feature-plans/retirement-approval-lifecycle.md AC-6: build_checklist(
recommendation: dict, bot_state: dict) -> dict returns a deterministic,
advisory wind-down checklist -- NO LLM.

CONTRACT RECONCILIATION (2026-08-26): the return shape below is the
FINAL, team-lead-mediated, peer-converged contract between ret2-explainer
(producer) and ret2-route (consumer) -- it supersedes an earlier
tickers/holdings_unavailable draft this test-writer originally pinned.
Adopted verbatim per team-lead's reconciliation message:

    {
        "candidate_id": str,
        "candidate_name": str | None,     # local hash->name lookup, None if unresolvable
        "holdings": list[str],            # SORTED tickers, [] when unavailable
        "holdings_available": bool,
        "steps": list[str],               # fixed manual wind-down prose
        "unavailable_note": str | None,   # populated ONLY when holdings_available is False
    }

Note the inverted polarity vs the original draft (holdings_available, not
holdings_unavailable) and the off-hours note living in its OWN field
(unavailable_note) rather than embedded inside steps text. sibling_id is
deliberately NOT part of this return -- ret2-route reads it from the card's
own raw_response instead.

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
# Deterministic output, return-shape pin (the reconciled contract)
# ===========================================================================


def test_returns_exactly_the_six_reconciled_keys():
    import advisors.retirement_checklist as rc_mod

    bot_state = {"sym-candidate-1": {"name": "Candidate Symphony", "logic_holdings": {"AAPL": 0.5}}}
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)

    expected_keys = {
        "candidate_id",
        "candidate_name",
        "holdings",
        "holdings_available",
        "steps",
        "unavailable_note",
    }
    assert expected_keys <= set(result.keys()), (
        f"build_checklist must return at least {expected_keys}, got {sorted(result.keys())}. "
        "This is the reconciled contract (2026-08-26) -- see this file's module docstring."
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
    availability, and must reference Composer (the manual wind-down venue).
    steps is UNAFFECTED by the off-hours note (that lives in
    unavailable_note now, not embedded in steps text)."""
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


def test_unavailable_note_is_none_when_holdings_are_available():
    import advisors.retirement_checklist as rc_mod

    bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {"AAPL": 1.0}}}
    result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
    assert result["holdings_available"] is True
    assert result["unavailable_note"] is None, (
        "unavailable_note must be None when holdings_available is True -- populated "
        "ONLY on the honest off-hours degrade path."
    )


# ===========================================================================
# Ticker extraction -- weight-shape variance (the AC-6 defensive requirement)
# ===========================================================================


class TestHoldingsExtractionWeightShapeVariance:
    def test_float_weight_shape(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"logic_holdings": {"AAPL": 0.5, "MSFT": 0.5}}}
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert set(result["holdings"]) == {"AAPL", "MSFT"}
        assert result["holdings_available"] is True

    def test_dict_wrapped_weight_shape(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {
            "sym-candidate-1": {
                "logic_holdings": {"AAPL": {"weight": 0.5}, "MSFT": {"weight": 0.5}}
            }
        }
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert set(result["holdings"]) == {"AAPL", "MSFT"}
        assert result["holdings_available"] is True

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
        assert set(result["holdings"]) == {"AAPL", "MSFT", "GOOGL"}, (
            f"Mixed weight-shape holdings must yield the FULL ticker set, "
            f"got {result['holdings']!r}."
        )

    def test_holdings_are_sorted(self):
        """Contract pin: 'holdings: list[str] -- SORTED tickers.' Deliberately
        seeded out of alphabetical insertion order to prove the function
        sorts, rather than just happening to preserve dict insertion order."""
        import advisors.retirement_checklist as rc_mod

        bot_state = {
            "sym-candidate-1": {"logic_holdings": {"MSFT": 0.3, "AAPL": 0.4, "GOOGL": 0.3}}
        }
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert result["holdings"] == ["AAPL", "GOOGL", "MSFT"], (
            f"holdings must be sorted alphabetically, got {result['holdings']!r}."
        )


# ===========================================================================
# Honest off-hours degrade -- never fabricate tickers
# ===========================================================================


class TestOffHoursDegrade:
    def test_empty_logic_holdings_marks_unavailable_and_yields_no_holdings(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {}}}
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert result["holdings_available"] is False
        assert result["holdings"] == [], "An empty logic_holdings must never fabricate tickers."

    def test_missing_logic_holdings_key_marks_unavailable(self):
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"name": "X"}}  # no logic_holdings key at all
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)
        assert result["holdings_available"] is False
        assert result["holdings"] == []

    def test_candidate_entirely_absent_from_bot_state_marks_unavailable(self):
        import advisors.retirement_checklist as rc_mod

        result = rc_mod.build_checklist(_SAMPLE_REC, {})
        assert result["holdings_available"] is False
        assert result["holdings"] == []

    def test_unavailable_note_is_populated_and_mentions_composer(self):
        """AC-6: 'an explicit "current holdings unavailable (off-hours) —
        view live positions in Composer" note' -- now its OWN field
        (unavailable_note), checked by substance (unavailable + Composer),
        not exact wording, so a copy-edit doesn't spuriously break this
        test."""
        import advisors.retirement_checklist as rc_mod

        bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {}}}
        result = rc_mod.build_checklist(_SAMPLE_REC, bot_state)

        assert isinstance(result["unavailable_note"], str) and result["unavailable_note"], (
            f"unavailable_note must be a non-empty string when holdings_available is "
            f"False, got {result['unavailable_note']!r}."
        )
        lowered = result["unavailable_note"].lower()
        assert "unavailable" in lowered
        assert "composer" in lowered

    def test_steps_are_unaffected_by_the_off_hours_degrade(self):
        """The off-hours note lives in unavailable_note now, not steps --
        steps must still be the same fixed, non-empty manual-wind-down
        prose regardless of holdings availability."""
        import advisors.retirement_checklist as rc_mod

        available_bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {"AAPL": 1.0}}}
        unavailable_bot_state = {"sym-candidate-1": {"name": "X", "logic_holdings": {}}}

        result_available = rc_mod.build_checklist(_SAMPLE_REC, available_bot_state)
        result_unavailable = rc_mod.build_checklist(_SAMPLE_REC, unavailable_bot_state)

        assert len(result_unavailable["steps"]) > 0
        assert " ".join(result_unavailable["steps"]) != "", (
            "steps must not be emptied out by the off-hours degrade path."
        )
        assert "Composer" in " ".join(result_unavailable["steps"])
        # Sanity: both paths reference Composer in steps (steps content is
        # not conditioned on holdings availability).
        assert "Composer" in " ".join(result_available["steps"])


# ===========================================================================
# D-1 never-raises / robustness
# ===========================================================================


def test_malformed_recommendation_missing_candidate_id_never_raises():
    import advisors.retirement_checklist as rc_mod

    try:
        result = rc_mod.build_checklist({}, {})
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"build_checklist raised on a recommendation missing candidate_id: {exc!r}")
    assert result["holdings_available"] is False


def test_bot_state_none_never_raises():
    """Adversarial: a caller passing bot_state=None (e.g. database.load_state()
    itself degraded) must not crash the checklist builder."""
    import advisors.retirement_checklist as rc_mod

    try:
        result = rc_mod.build_checklist(_SAMPLE_REC, None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"build_checklist raised on bot_state=None: {exc!r}")
    assert result["holdings_available"] is False
    assert result["holdings"] == []


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
    assert "AAPL" in result["holdings"], "A well-formed sibling entry must still be extracted."


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
