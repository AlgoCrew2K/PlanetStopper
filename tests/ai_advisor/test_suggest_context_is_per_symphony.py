"""advisor-fix cycle — regression spec: the Run Advisor /suggest context is
genuinely PER-SYMPHONY (the real per-symphony value this cycle delivers).

team-lead ruling: OC/SC are guardrails that are honestly uniform; the genuine
per-symphony ADVICE is the Run Advisor /suggest proposal engine, grounded in each
symphony's OWN data.  This file pins that the per-symphony grounding is real — so a
future regression that makes /suggest degenerate (shared/global context regardless
of symphony) fails loudly.

Verified (ai_advisor.py:343 assemble_advisor_context): for scope='symphony' it
threads symphony_id into:
  - database.get_latest_autotune_run(symphony_id)   -> that symphony's Optuna evidence
  - symphony_logic.get_condensed_logic(symphony_id) -> that symphony's composition
  - _build_suggestible_surface / _read_current_strategy(symphony_id) -> its params
So two different symphonies with different underlying data produce DIFFERENT context.

This test asserts the WIRING + DISTINCTNESS, NOT any Claude output (request_suggestions
is never called here).  We do NOT assert specific suggestion values — the LLM is not
exercised.  This is a structural per-symphony grounding contract.

Mocking strategy:
  * database.get_latest_autotune_run, symphony_logic.get_condensed_logic, and the
    current-strategy read are mocked PER symphony — different data per symphony_id.
  * No live DB, no network, no Anthropic call; math engine never mocked; fixtures
    function-scoped.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import ai_advisor


_SYM_A = "(invest) lqd + eyeg 5 ways full market"
_SYM_B = "corporate chaos 5 ways"


def _autotune_run_for(symphony_id: str) -> dict:
    """A per-symphony autotune_run with DISTINCT metrics keyed off the id."""
    # Distinct, plausible values per symphony — derived from the id, not shared.
    seed = sum(ord(c) for c in symphony_id)
    return {
        "id": seed,
        "symphony_id": symphony_id,
        "oos_alpha": (seed % 7) * 0.1,
        "train_alpha": (seed % 5) * 0.1,
        "baseline_decision": "Adopted AI" if seed % 2 else "Kept Default",
        "validation_sharpe": (seed % 11) * 0.1,
        "n_effective": 500,
    }


@pytest.fixture(scope="function")
def per_symphony_seams():
    """Patch the three per-symphony data seams so each symphony_id yields its OWN
    distinct data — proving the context is grounded per symphony."""

    def _latest_run(symphony_id):
        return _autotune_run_for(symphony_id)

    def _condensed(symphony_id, use_cache=True):
        # Distinct composition per symphony (different ticker set).
        return {"symphony_id": symphony_id, "tickers": [symphony_id[:3].upper(), "BIL"]}

    def _strategy(symphony_id):
        # (_read_current_strategy returns (params, locked_vars))
        return ({"MAX_SQUEEZE_FLOOR": 0.20 + (len(symphony_id) % 3) * 0.01}, [])

    with (
        patch.object(ai_advisor.database, "get_latest_autotune_run", side_effect=_latest_run),
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", side_effect=_condensed),
        patch.object(ai_advisor, "_read_current_strategy", side_effect=_strategy),
    ):
        yield


# ===========================================================================
# The /suggest context must be genuinely per-symphony.
# ===========================================================================


def test_assemble_context_threads_symphony_id_into_per_symphony_lookups(per_symphony_seams):
    """assemble_advisor_context(scope='symphony', symphony_id=X) MUST query the
    per-symphony data sources with X — not a shared/global key.

    Spies on the three per-symphony seams and asserts each is called with the
    supplied symphony_id.  A degenerate implementation that ignored symphony_id
    (returning a shared blob) would fail here.
    """
    with (
        patch.object(
            ai_advisor.database,
            "get_latest_autotune_run",
            side_effect=lambda sid: _autotune_run_for(sid),
        ) as spy_run,
        patch.object(
            ai_advisor.symphony_logic,
            "get_condensed_logic",
            side_effect=lambda sid, use_cache=True: {"symphony_id": sid},
        ) as spy_logic,
        patch.object(
            ai_advisor, "_read_current_strategy", side_effect=lambda sid: ({}, [])
        ) as spy_strategy,
    ):
        ai_advisor.assemble_advisor_context(scope="symphony", symphony_id=_SYM_A)

    assert spy_run.called and spy_run.call_args.args[0] == _SYM_A, (
        "get_latest_autotune_run was not called with the supplied symphony_id — "
        "the Optuna evidence is not grounded in THAT symphony's run."
    )
    assert spy_logic.called and spy_logic.call_args.args[0] == _SYM_A, (
        "get_condensed_logic was not called with the supplied symphony_id — the "
        "composition is not grounded in THAT symphony."
    )
    assert spy_strategy.called and spy_strategy.call_args.args[0] == _SYM_A, (
        "_read_current_strategy was not called with the supplied symphony_id — the "
        "current params/locked_vars are not THAT symphony's."
    )


def test_two_symphonies_produce_distinct_suggest_context(per_symphony_seams):
    """Two DIFFERENT symphonies must produce DIFFERENT /suggest context.

    This is the per-symphony-value invariant: the proposal engine's INPUT differs
    per symphony (its own optuna evidence + composition + params), so the
    resulting advice is genuinely symphony-specific — NOT a shared blob.

    Asserts the contexts differ AND each carries its own symphony_id.  Does NOT
    assert any Claude output (request_suggestions is never invoked).
    """
    ctx_a = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id=_SYM_A)
    ctx_b = ai_advisor.assemble_advisor_context(scope="symphony", symphony_id=_SYM_B)

    assert ctx_a["symphony_id"] == _SYM_A
    assert ctx_b["symphony_id"] == _SYM_B
    assert ctx_a != ctx_b, (
        "Two different symphonies produced IDENTICAL /suggest context — the Run "
        "Advisor input is degenerate (shared/global), so its proposals would be "
        "identical per symphony. The genuine per-symphony value is absent."
    )
    # The Optuna evidence specifically must differ (it is the symphony's own run).
    assert ctx_a["optuna_evidence"] != ctx_b["optuna_evidence"], (
        "The optuna_evidence section is identical across two symphonies — the "
        "per-symphony Optuna grounding is not threaded through."
    )
