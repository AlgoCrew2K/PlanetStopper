"""RED test -- BL-2 blast-radius finding: alpha_bot_execution.py's
``augment_optimization_results_with_selection_stats`` (alpha_bot_execution.py:32-49)
must not treat the additive ``_batch_summary`` key as a symphony id.

Feature plan: feature-plans/audit-bl2-autotuner-loop-isolation.md
Source: docs/audit/TWO-WEEK-REVIEW-2026-08-04.md Sec4 Finding T2, Sec6 Backlog BL-2

Background
----------
run_autotuner()'s return gains an additive
``optimization_results["_batch_summary"] = {"attempted": N, "completed": M}``
key (BL-2 AC-2). Per team-lead's required production-tree blast-radius sweep
(beyond tests/ and reporting.py:654), a SECOND production consumer of
run_autotuner()'s return was found:

    alpha_bot_execution.py:1178-1183 (the daemon's EOD autotune block, gated
    on the pre-existing "aborted" discriminator) calls
    ``augment_optimization_results_with_selection_stats(autotuner_changes)``
    BEFORE handing the result to ``reporting.send_eod_discord_post``.

That function (alpha_bot_execution.py:32-49) iterates its input as
``{symphony_id: {...}}``:

    for sym_id, sym_data in optimization_results.items():
        run_row = database.get_latest_autotune_run(sym_id)
        if run_row:
            sym_data["_selection_stats"] = {...}

An additive ``_batch_summary`` key would be silently mis-treated as a
symphony id here -- ``database.get_latest_autotune_run("_batch_summary")``
is a spurious DB query for a nonexistent symphony (returns None per that
accessor's own documented contract, so this does NOT crash today -- but it
is a real, wasted, semantically-wrong query, and any future accessor
behavior change for an unrecognized id is unguarded against this specific
call).

Note: this call site is asymmetric across the two production ``run_autotuner``
callers -- ``app.py``'s ``/api/force_eod`` route (app.py:3846-3852) passes
``autotuner_changes`` DIRECTLY to ``reporting.send_eod_discord_post`` with NO
call to this augment function at all, so it is unaffected by this finding.
Only the daemon's Friday/weekend autotune block routes through it.

This mirrors the EXISTING "risk-engine-specialist RED-gap finding" precedent
in tests/autotuner/test_history_shortfall_graceful_abort.py
(``test_abort_marker_does_not_reach_the_symphony_selection_stats_augmentation``),
which pins the identical class of bug for the PRE-EXISTING ``{"aborted": True,
...}`` marker shape. That precedent test allows EITHER resolution (the call
site guards the augment call, OR augment itself skips non-symphony keys) --
this test does the same for ``_batch_summary``, asserting the OBSERVABLE
contract only (no DB query issued for "_batch_summary", and the
_batch_summary value survives unmutated), not a specific implementation
mechanism.

What this test pins
--------------------
- ``augment_optimization_results_with_selection_stats`` must never call
  ``database.get_latest_autotune_run("_batch_summary")``.
- The ``_batch_summary`` entry must survive the augmentation call BYTE-FOR-
  BYTE unchanged (no ``_selection_stats`` key injected into it).
- A real symphony entry sharing the SAME call still gets augmented normally
  (the fix must not accidentally skip real symphonies too).

Provenance
----------
No producer-computed value asserted -- pure structural/behavioral pins via a
hand-built optimization_results dict and a monkeypatched
database.get_latest_autotune_run spy.

No live DB, no live API. -n0 only.
"""

from __future__ import annotations

import alpha_bot_execution


def test_augment_selection_stats_skips_batch_summary_key(monkeypatch):
    """augment_optimization_results_with_selection_stats must exclude the
    additive "_batch_summary" top-level key from its per-symphony iteration:
    it must never issue database.get_latest_autotune_run("_batch_summary"),
    and the _batch_summary value must survive unchanged (no _selection_stats
    key injected into it).

    MUST FAIL on current code: the unguarded
    ``for sym_id, sym_data in optimization_results.items()`` loop treats
    every top-level key -- including "_batch_summary" -- as a symphony id.
    """
    queried_ids = []

    def _fake_get_latest_autotune_run(sym_id):
        queried_ids.append(sym_id)
        if sym_id == "sym-a":
            return {
                "naive_sharpe": 1.1,
                "selection_tstat": 0.9,
                "frozen_eval_sharpe": 1.0,
            }
        return None

    monkeypatch.setattr(
        alpha_bot_execution.database, "get_latest_autotune_run", _fake_get_latest_autotune_run
    )

    optimization_results = {
        "sym-a": {"maxSqueezeFloor": {"old": 1.0, "new": 2.0}},
        "_batch_summary": {"attempted": 2, "completed": 2},
    }

    result = alpha_bot_execution.augment_optimization_results_with_selection_stats(
        optimization_results
    )

    assert "_batch_summary" not in queried_ids, (
        f"augment_optimization_results_with_selection_stats queried "
        f"database.get_latest_autotune_run('_batch_summary') -- the BL-2 "
        f"batch-summary key must be excluded from per-symphony iteration. "
        f"Queried ids: {queried_ids!r}"
    )
    assert result["_batch_summary"] == {"attempted": 2, "completed": 2}, (
        f"the _batch_summary value must survive augmentation unchanged -- "
        f"augment must never inject a _selection_stats key into it. Got: "
        f"{result['_batch_summary']!r}"
    )
    assert "sym-a" in queried_ids, (
        "the fix must not accidentally skip real symphony entries sharing "
        "the same call -- 'sym-a' must still be queried normally."
    )
    assert result["sym-a"].get("_selection_stats") == {
        "naive_sharpe": 1.1,
        "selection_tstat": 0.9,
        "frozen_eval_sharpe": 1.0,
    }, (
        f"the real symphony entry must still be augmented with its "
        f"_selection_stats as before -- got: {result['sym-a']!r}"
    )
