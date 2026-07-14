"""
Sprint 3 audit-fix Cycle A — RED tests for S3-AUDIT-004 (HIGH) producer side:
  All three producers (Overfitting Conscience, Spec Critic, Divergence
  Explainer) MUST populate the new advisor_observations.symphony_id column
  when they call insert_advisor_observation.

The column is added by migration 025 (see test_audit_fix_migration_025_symphony_id);
this file pins the OUTBOUND-write contract on the producer modules: every
persisted observation row carries the symphony_id of the autotune_run /
spec_bundle / cvar diagnostic it describes.

Coverage:
  - run_overfitting_conscience       — symphony_id sourced from autotune_run["symphony_id"]
  - run_spec_critic                  — symphony_id sourced from a caller-supplied kwarg or
                                       resolved from the bundle (implementer's choice).
                                       This test asserts the run accepts a symphony_id
                                       kwarg and writes it through.
  - run_divergence_explainer         — symphony_id sourced from autotune_run["symphony_id"]
                                       (off-flag and on-flag paths both write).

Mocking strategy:
  - Patch database.insert_advisor_observation; inspect the kwargs passed.
  - Pure-compute fixtures avoid any DB writes — the contract under test is the
    OUTBOUND call signature.

No hardcoded producer-computed values; every assertion is on the symphony_id
plumbing, never on CRRA-EU/CVaR numerics.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch


def _import(module: str):
    return importlib.import_module(module)


def _valid_oc_run() -> dict:
    return {
        "id": 7,
        "symphony_id": "DefensiveAlpha",
        "run_timestamp": "2026-05-27T00:00:00Z",
        "spec_bundle_id": "bundle-hash-abc",
        "n_effective": 100,
        "s_count": 0,
    }


def _valid_de_run() -> dict:
    return {
        "id": 11,
        "symphony_id": "OffensiveBeta",
        "spec_bundle_id": "bundle-hash-xyz",
    }


# ---------------------------------------------------------------------------
# 1. Overfitting Conscience persists symphony_id.
# ---------------------------------------------------------------------------


def test_run_overfitting_conscience_passes_symphony_id_to_insert():
    """run_overfitting_conscience MUST pass autotune_run['symphony_id'] through
    to database.insert_advisor_observation as the symphony_id kwarg.

    Verified by patching the insert function and inspecting the kwargs.
    """
    mod = _import("advisors.overfitting_conscience")
    run = _valid_oc_run()

    with patch.object(mod.database, "insert_advisor_observation", return_value=99) as mocked:
        mod.run_overfitting_conscience(run, ledger_rows=[])

    assert mocked.called, "insert_advisor_observation was not called"
    _, kwargs = mocked.call_args
    assert "symphony_id" in kwargs, (
        f"insert_advisor_observation called without symphony_id kwarg. "
        f"kwargs keys: {sorted(kwargs.keys())!r}.  The OC producer must pass "
        "autotune_run['symphony_id'] so /api/advisor-observations symphony "
        "filter can find the row (S3-AUDIT-004)."
    )
    assert kwargs["symphony_id"] == run["symphony_id"], (
        f"OC persisted symphony_id={kwargs['symphony_id']!r}; expected "
        f"{run['symphony_id']!r} from autotune_run."
    )


# ---------------------------------------------------------------------------
# 2. Divergence Explainer persists symphony_id (both feature-flag branches).
# ---------------------------------------------------------------------------


def test_run_divergence_explainer_writes_nothing_when_flag_off():
    """CONTRACT CHANGE (PM-adjudicated 2026-07-13, AC-14 Mechanism 2 —
    advisor-remediation-r1, flagged by r1-engine's regression sweep at
    commit 82479560): this test previously asserted symphony_id propagation
    on the NOT_APPLICABLE row written when SECOND_WINDOW_CVAR_ENABLED is
    off. That premise is now VOID — database.get_advisor_observations_for_
    symphony has no role filter, so the old NOT_APPLICABLE stub row leaked
    onto /api/advisor-observations?symphony_id= on every autotune run; the
    approved fix is for run_divergence_explainer to write NOTHING (and
    return None) when the flag is off, rather than persisting a stub row at
    all. There is therefore no insert call left to check symphony_id
    propagation on for this path — asserting the opposite (no write
    happens) is the corrected, honest contract. The flag_on sibling test
    below is unaffected and still asserts symphony_id propagation on the
    real INFORMATIONAL row.
    """
    mod = _import("advisors.divergence_explainer")
    run = _valid_de_run()

    with patch.object(mod.database, "insert_advisor_observation", return_value=21) as mocked:
        result = mod.run_divergence_explainer(run, cvar_row=None, second_window_enabled=False)

    assert not mocked.called, (
        "DE producer (flag off) must write NOTHING per the AC-14 Mechanism 2 "
        "contract change — insert_advisor_observation was called."
    )
    assert result is None, (
        f"DE producer (flag off) must return None (no row id — there is no row); got {result!r}"
    )


def test_run_divergence_explainer_passes_symphony_id_when_flag_on():
    """When the feature flag is on, the INFORMATIONAL row must also carry symphony_id."""
    mod = _import("advisors.divergence_explainer")
    run = _valid_de_run()

    # Minimal cvar_row so the on-flag path has data to render.  Values are
    # arbitrary — the contract under test is symphony_id propagation, not CVaR
    # numerics.
    cvar_row = {
        "cvar_5pct": -0.01,
        "cvar_n_tail": 5,
        "cvar_5pct_long": -0.02,
        "cvar_n_tail_long": 25,
    }

    with patch.object(mod.database, "insert_advisor_observation", return_value=22) as mocked:
        mod.run_divergence_explainer(run, cvar_row=cvar_row, second_window_enabled=True)

    assert mocked.called
    _, kwargs = mocked.call_args
    assert kwargs.get("symphony_id") == run["symphony_id"], (
        f"DE (flag on) symphony_id={kwargs.get('symphony_id')!r}; expected {run['symphony_id']!r}."
    )


# ---------------------------------------------------------------------------
# 3. Spec Critic persists symphony_id (caller-supplied path).
# ---------------------------------------------------------------------------


def test_run_spec_critic_accepts_and_persists_symphony_id_kwarg():
    """run_spec_critic must accept a symphony_id kwarg and forward it to insert.

    The Spec Critic's subject_type is "spec_bundle", but the route filter is by
    symphony name — the producer must accept the caller's symphony_id and
    write it on the persisted row.  The caller (autotuner.py:1318-1324) knows
    the symphony (normalized_name) at the call site.

    This RED test asserts:
      - run_spec_critic accepts a symphony_id kwarg.
      - That kwarg is passed through to insert_advisor_observation.
    """
    mod = _import("advisors.spec_critic")
    facets_rows = [
        {
            "facet_name": "gamma",
            "freeze_discipline": "THEORY",
            "frozen_at": "2026-05-01",
        },
        {
            "facet_name": "utility_family",
            "freeze_discipline": "THEORY",
            "frozen_at": "2026-05-01",
        },
        {
            "facet_name": "wealth_argument",
            "freeze_discipline": "THEORY",
            "frozen_at": "2026-05-01",
        },
    ]

    with patch.object(mod.database, "insert_advisor_observation", return_value=33) as mocked:
        # Implementer-friendly contract: symphony_id is a NAMED kwarg on run_spec_critic.
        mod.run_spec_critic("bundle-hash-abc", facets_rows, symphony_id="DefensiveAlpha")

    assert mocked.called, "insert_advisor_observation not called"
    _, kwargs = mocked.call_args
    assert kwargs.get("symphony_id") == "DefensiveAlpha", (
        f"Spec Critic symphony_id={kwargs.get('symphony_id')!r}; expected "
        "'DefensiveAlpha'.  run_spec_critic must accept a symphony_id kwarg and "
        "forward it to insert_advisor_observation."
    )
