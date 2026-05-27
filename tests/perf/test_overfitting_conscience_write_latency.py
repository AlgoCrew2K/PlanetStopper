"""
RED perf test — F-4 from rev-ocon review on 78bc707.

Contract under test:
  run_overfitting_conscience(autotune_run, ledger_rows) must complete within
  100ms on a warm DB (insert_advisor_observation + pure Python compute).

Rationale: run_overfitting_conscience is called synchronously on the
autotuner path after save_autotune_run. The autotuner runs EOD (not per
minute), so 100ms is a generous soft budget — but the call must not block
indefinitely or perform unexpected I/O (e.g. accidentally calling a live
advisor endpoint). This test captures the latency constraint formally.

The 100ms budget covers:
  - compute_overfitting_conscience_observation: pure Python in-process,
    no DB calls — should complete in <1ms
  - database.insert_advisor_observation: one SQLite INSERT with WAL mode —
    expected <10ms on a warm DB

Marked @pytest.mark.perf — excluded from the default pytest run.
Opt-in: pytest -m perf

Fixture provenance:
  Inline synthetic autotune_run + ledger_rows dicts. No producer-computed
  rates/percents. Latency threshold derived from architecture budget, not
  from measured output.

Pattern precedent: tests/perf/test_cvar_diagnostics_does_not_block_save_state.py
"""

from __future__ import annotations

import time

import pytest

import database as db_module
from advisors.overfitting_conscience import run_overfitting_conscience

pytestmark = pytest.mark.perf  # excluded from default run

# ---------------------------------------------------------------------------
# Budget constant — architecture constraint (post-autotune, non-blocking).
# Source: rev-ocon F-4 requirement + dispatch brief §performance.
# ---------------------------------------------------------------------------

_WRITE_LATENCY_BUDGET_MS = 100.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def warm_db(tmp_path, monkeypatch):
    """Isolated warm DB with full migration stack applied.

    'Warm' here means init_db() + run_migrations() have already run, so the
    SQLite file exists and WAL mode is active — matching the production state
    where autotuner runs after the daemon has been up for some time.
    """
    db_path = str(tmp_path / "perf_test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    return db_path


@pytest.fixture
def minimal_autotune_run() -> dict:
    """Minimal autotune_run dict for the CLEAR path (no ledger rows).

    CLEAR path minimises compute inside the pure function — the budget
    test is measuring wall-clock, not a specific code path, so we use
    the cheapest case. A separate scenario below tests the WATCH path.
    """
    return {
        "id": 1001,
        "symphony_id": "perf-test-symphony",
        "run_timestamp": "2026-05-26T16:00:00Z",
        "spec_bundle_id": "bundle-perf-test-001",
        "n_effective": 500,
        "s_count": 0,
    }


@pytest.fixture
def minimal_autotune_run_watch() -> dict:
    """Minimal autotune_run dict for the WATCH path (S > 0, ratio <= 0.1)."""
    return {
        "id": 1002,
        "symphony_id": "perf-test-symphony-watch",
        "run_timestamp": "2026-05-26T16:01:00Z",
        "spec_bundle_id": "bundle-perf-test-002",
        "n_effective": 505,
        "s_count": 5,
    }


@pytest.fixture
def watch_ledger_rows() -> list[dict]:
    return [
        {
            "evidence_source": "BACKTEST_SELECTION",
            "n_configs_searched": 5,
            "touched_frozen_eval": 0,
            "spec_bundle_id": "bundle-perf-test-002",
            "facet_name": "perf-facet",
        }
    ]


# ===========================================================================
# F-4 — run_overfitting_conscience completes within 100ms (CLEAR path)
# ===========================================================================

def test_run_overfitting_conscience_clear_path_under_budget(
    warm_db, minimal_autotune_run
):
    """run_overfitting_conscience on the CLEAR path (no ledger rows) must
    complete within 100ms on a warm DB.

    This is the cheapest execution path — pure compute + one DB INSERT.
    If this test fails, there is unexpected I/O or a lock-contention bug.
    """
    ledger_rows: list = []

    # Warm the SQLite write path with one dummy insert so the first call
    # is not penalised by file-creation overhead.
    db_module.insert_advisor_observation(
        advisor_role="WARMUP",
        subject_type="warmup",
        subject_id="warmup-0",
        verdict="CLEAR",
    )

    start = time.monotonic()
    row_id = run_overfitting_conscience(minimal_autotune_run, ledger_rows)
    elapsed_ms = (time.monotonic() - start) * 1000.0

    assert isinstance(row_id, int) and row_id > 0, (
        "run_overfitting_conscience must return a positive integer row id"
    )
    assert elapsed_ms < _WRITE_LATENCY_BUDGET_MS, (
        f"run_overfitting_conscience (CLEAR path) took {elapsed_ms:.1f}ms — "
        f"exceeds the {_WRITE_LATENCY_BUDGET_MS}ms architecture budget. "
        "The call is synchronous on the autotuner path; unexpected I/O or "
        "lock contention must be investigated."
    )


# ===========================================================================
# F-4b — run_overfitting_conscience completes within 100ms (WATCH path)
# ===========================================================================

def test_run_overfitting_conscience_watch_path_under_budget(
    warm_db, minimal_autotune_run_watch, watch_ledger_rows
):
    """run_overfitting_conscience on the WATCH path (S > 0, indicator 1 fires)
    must also complete within 100ms. The WATCH path has slightly more work
    (facet name iteration + populated raw_response) but is still pure Python.
    """
    # Warm the write path.
    db_module.insert_advisor_observation(
        advisor_role="WARMUP",
        subject_type="warmup",
        subject_id="warmup-1",
        verdict="CLEAR",
    )

    start = time.monotonic()
    row_id = run_overfitting_conscience(
        minimal_autotune_run_watch, watch_ledger_rows
    )
    elapsed_ms = (time.monotonic() - start) * 1000.0

    assert isinstance(row_id, int) and row_id > 0
    assert elapsed_ms < _WRITE_LATENCY_BUDGET_MS, (
        f"run_overfitting_conscience (WATCH path) took {elapsed_ms:.1f}ms — "
        f"exceeds the {_WRITE_LATENCY_BUDGET_MS}ms budget."
    )


# ===========================================================================
# F-4c — Row is persisted: DB contains the observation after the call.
# ===========================================================================

def test_run_overfitting_conscience_observation_is_persisted(
    warm_db, minimal_autotune_run
):
    """Belt-and-suspenders: verify the row is actually in advisor_observations
    after run_overfitting_conscience returns — not just that it returned fast.

    Latency without correctness is meaningless.
    """
    row_id = run_overfitting_conscience(minimal_autotune_run, [])

    observations = db_module.get_advisor_observations_for_subject(
        subject_type="autotune_run",
        subject_id=str(minimal_autotune_run["id"]),
    )
    assert len(observations) == 1, (
        f"Expected 1 advisor_observation row, found {len(observations)}"
    )
    obs = observations[0]
    assert obs["id"] == row_id
    assert obs["advisor_role"] == "OVERFITTING_CONSCIENCE"
    assert obs["verdict"] == "CLEAR"
    assert obs["is_advisory_only"] == 1
