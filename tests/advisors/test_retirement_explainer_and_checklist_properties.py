"""RED tests -- adversarial property invariants for retirement_explainer /
retirement_checklist (AC-1, AC-6, AC-9).

hypothesis (6.152.6 confirmed available -- see
tests/advisors/test_retirement_recommender_properties.py) drives the two
pure-function-shaped properties below. The exhaustive static AST call-graph
proof that NEITHER module ever reaches ai_advisor._build_client already
lives in tests/security/test_retirement_action_no_trade_boundary.py's
TestChecklistModuleNeverReachesLlm (whole-module AST walk, not just one
function) -- not duplicated here.

CONTRACT RECONCILIATION (2026-08-26): build_checklist's return shape here
uses the FINAL, team-lead-mediated, peer-converged keys ("holdings"/
"holdings_available", not the earlier "tickers"/"holdings_unavailable"
draft) -- see tests/advisors/test_retirement_checklist.py's module
docstring for the full reconciled contract.

Expected state: RED until advisors/retirement_explainer.py and
advisors/retirement_checklist.py exist.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Property 1: explain_recommendation never mutates its input dict, regardless
# of success or failure -- it is the PRODUCER (app.py's tick worker, AC-2)
# that stamps 'explanation' onto a rec, never explain_recommendation itself.
# A None-explanation outcome must therefore never strip/alter any of the
# rec's own deterministic evidence fields (AC-9: "the explainer never gates
# a recommendation").
# ---------------------------------------------------------------------------


def _rec_strategy():
    return st.fixed_dictionaries(
        {
            "candidate_id": st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=("Cs",))),
            "sibling_id": st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=("Cs",))),
            "correlation": st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
            "uncertainty_gate_passed": st.booleans(),
            "structural_redundancy_gate_passed": st.booleans(),
        }
    )


class TestExplainerNeverMutatesInput:
    @given(rec=_rec_strategy())
    @settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_success_path_never_mutates_the_input_dict(self, rec):
        import advisors.retirement_explainer as re_mod
        from types import SimpleNamespace

        before = copy.deepcopy(rec)
        fake_client = MagicMock()
        fake_client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="some explanation")]
        )
        with patch("ai_advisor._build_client", return_value=fake_client):
            re_mod.explain_recommendation(rec)

        assert rec == before, (
            f"explain_recommendation mutated its input rec dict. "
            f"Before={before!r}, after={rec!r}."
        )

    @given(rec=_rec_strategy())
    @settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_failure_path_never_mutates_the_input_dict(self, rec):
        import advisors.retirement_explainer as re_mod

        before = copy.deepcopy(rec)
        with patch("ai_advisor._build_client", side_effect=RuntimeError("down")):
            result = re_mod.explain_recommendation(rec)

        assert result is None
        assert rec == before, (
            f"explain_recommendation mutated its input rec dict on the failure "
            f"path. Before={before!r}, after={rec!r}."
        )


# ---------------------------------------------------------------------------
# Property 2: build_checklist's ticker-set output is invariant under
# weight-representation (float vs {"weight": x}), and NEVER fabricates a
# ticker not present in the input logic_holdings.
# ---------------------------------------------------------------------------


_TICKER_ALPHABET = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5)


@st.composite
def _holdings_and_expected_tickers(draw):
    tickers = draw(st.lists(_TICKER_ALPHABET, min_size=0, max_size=6, unique=True))
    holdings = {}
    for t in tickers:
        use_dict_shape = draw(st.booleans())
        weight = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
        holdings[t] = {"weight": weight} if use_dict_shape else weight
    return holdings, set(tickers)


class TestChecklistHoldingsExtractionInvariant:
    @given(data=_holdings_and_expected_tickers())
    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_holdings_set_matches_logic_holdings_keys_regardless_of_weight_shape(self, data):
        import advisors.retirement_checklist as rc_mod

        holdings, expected_tickers = data
        bot_state = {"cand-prop": {"name": "X", "logic_holdings": holdings}}
        rec = {"candidate_id": "cand-prop", "sibling_id": "sib-prop"}

        result = rc_mod.build_checklist(rec, bot_state)

        assert set(result["holdings"]) == expected_tickers, (
            f"Holdings extraction depends on weight-representation shape: "
            f"expected {expected_tickers}, got {set(result['holdings'])} for "
            f"logic_holdings={holdings!r}."
        )
        # holdings_available must be exactly the non-emptiness of the input --
        # never a fabricated signal decoupled from the real data.
        assert result["holdings_available"] == (len(holdings) > 0)

    @given(data=_holdings_and_expected_tickers())
    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_never_fabricates_a_ticker_outside_the_input_holdings(self, data):
        import advisors.retirement_checklist as rc_mod

        holdings, expected_tickers = data
        bot_state = {"cand-prop2": {"name": "X", "logic_holdings": holdings}}
        rec = {"candidate_id": "cand-prop2", "sibling_id": "sib-prop2"}

        result = rc_mod.build_checklist(rec, bot_state)

        fabricated = set(result["holdings"]) - expected_tickers
        assert not fabricated, (
            f"build_checklist fabricated ticker(s) not present in the input "
            f"logic_holdings: {fabricated}."
        )

    @given(data=_holdings_and_expected_tickers())
    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_holdings_are_always_sorted(self, data):
        """Contract pin: 'holdings: list[str] -- SORTED tickers.'"""
        import advisors.retirement_checklist as rc_mod

        holdings, _expected_tickers = data
        bot_state = {"cand-prop3": {"name": "X", "logic_holdings": holdings}}
        rec = {"candidate_id": "cand-prop3", "sibling_id": "sib-prop3"}

        result = rc_mod.build_checklist(rec, bot_state)

        assert result["holdings"] == sorted(result["holdings"]), (
            f"build_checklist's holdings must always be sorted, got {result['holdings']!r}."
        )
