"""
RED tests — H4 telemetry helper: per-cycle latency budget (H-3 benchmark).

Contract under test (h4-telemetry-helper plan deliverable 4):
  100 sequential write_telemetry_row(..., mode="live") calls on a realistic
  cvar_diagnostics row; median per-call wall-clock < 50 ms; 99th-percentile < 200 ms.

Thresholds match the record_shadow_observation precedent (database.py:1147-1194),
which is the production benchmark the council §A.3 cites.

This test is marked @pytest.mark.perf and excluded from the default pytest run
(analogous to the test_live_* exclusion in CLAUDE.md Known Gotchas).
Default run: excluded.  Opt-in: pytest -m perf

Binding references:
  - h4-telemetry-helper plan §Deliverables (4)
  - shadow-logging-pattern plan §Golden-fixture tests (1)(2)
  - Architecture constraint 1: no blocking I/O on the execution path
  - council §A.3 H-3: "non-zero non-blocking I/O cost" + "explicit benchmark obligation"
  - record_shadow_observation precedent: database.py:1147-1194

Fixture provenance:
  tests/fixtures/math/telemetry_helper_write_basic.json
  — schema-derived; no producer-computed values asserted.
  The column shape is a realistic cvar_diagnostics row (all eight columns,
  including the §B.6 second-window columns).
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import statistics
import time

import pytest

import database as db

pytestmark = pytest.mark.perf  # excluded from default run

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Number of iterations for the latency sample.  100 gives a stable median
# and a meaningful 99th percentile (the 99th sample is the 99th of 100 rows).
_ITERATIONS = 100

# Thresholds match the record_shadow_observation precedent and the
# shadow-logging-pattern plan §Golden-fixture tests (1)(2).
_MEDIAN_BUDGET_MS = 50.0   # per-call median must be below this
_P99_BUDGET_MS = 200.0     # 99th-percentile must be below this

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "math"
    / "telemetry_helper_write_basic.json"
)


@pytest.fixture(scope="module")
def telemetry_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------


def _create_cvar_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cvar_diagnostics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id         TEXT    NOT NULL,
            ts_utc           TEXT    NOT NULL DEFAULT (datetime('now')),
            symphony_id      TEXT    NOT NULL,
            cvar_5pct        REAL    DEFAULT NULL,
            cvar_5pct_stderr REAL    DEFAULT NULL,
            cvar_n_tail      INTEGER DEFAULT NULL,
            cvar_5pct_long   REAL    DEFAULT NULL,
            cvar_n_tail_long INTEGER DEFAULT NULL
        )
        """
    )
    conn.commit()


@pytest.fixture
def perf_db(tmp_path, monkeypatch):
    """Isolated WAL-mode DB with cvar_diagnostics; matches production DB setup."""
    db_path = str(tmp_path / "perf_telemetry.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        _create_cvar_table(conn)
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------


def _run_benchmark(table: str, base_row: dict, n: int) -> list[float]:
    """Execute n sequential write_telemetry_row calls; return per-call ms list."""
    latencies: list[float] = []
    for i in range(n):
        row = {**base_row, "cycle_id": f"BENCH_CYCLE_{i:04d}"}
        t0 = time.perf_counter()
        db.write_telemetry_row(table, row, mode="live")
        elapsed_ms = (time.perf_counter() - t0) * 1_000
        latencies.append(elapsed_ms)
    return latencies


def _p99(values: list[float]) -> float:
    """Return the 99th percentile of a list."""
    sorted_vals = sorted(values)
    idx = max(0, int(len(sorted_vals) * 0.99) - 1)
    return sorted_vals[idx]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_per_call_median_latency_within_budget(perf_db, telemetry_fixture):
    """Binding H-3 benchmark: median per-call latency must be < 50 ms.

    This threshold matches the record_shadow_observation precedent — that
    function's production measured behaviour under similar conditions is the
    council's implicit baseline.  50 ms leaves ample headroom within the
    1-minute (60,000 ms) cadence budget.
    """
    base_row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    latencies = _run_benchmark(table, base_row, _ITERATIONS)
    median_ms = statistics.median(latencies)

    assert median_ms < _MEDIAN_BUDGET_MS, (
        f"Median per-call latency {median_ms:.2f} ms exceeds budget "
        f"{_MEDIAN_BUDGET_MS} ms over {_ITERATIONS} iterations. "
        "H-3 requires the per-cycle write to fit within the 1-minute cadence budget."
    )


def test_per_call_p99_latency_within_budget(perf_db, telemetry_fixture):
    """Binding H-3 benchmark: 99th-percentile per-call latency must be < 200 ms.

    The P99 guard covers tail-latency spikes on shared CI runners (shadow-
    logging-pattern plan §Golden-fixture test 2).  200 ms is 4× the median
    budget, allowing for GC pauses and OS scheduling jitter while still
    being well inside the 60-second cycle window.
    """
    base_row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    latencies = _run_benchmark(table, base_row, _ITERATIONS)
    p99_ms = _p99(latencies)

    assert p99_ms < _P99_BUDGET_MS, (
        f"P99 per-call latency {p99_ms:.2f} ms exceeds budget "
        f"{_P99_BUDGET_MS} ms over {_ITERATIONS} iterations. "
        "H-3 P99 guard: tail spikes must not stall the execution path."
    )


def test_benchmark_is_reproducible_within_tolerance(perf_db, telemetry_fixture):
    """H-3 reproducibility: two independent runs yield similar medians (±20%).

    Guards against a flaky CI runner masking a regression
    (shadow-logging-pattern plan §Golden-fixture test 6).
    """
    base_row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]

    run1 = _run_benchmark(table, {**base_row, "cycle_id": "REPRO_RUN1_{:04d}"}, _ITERATIONS)
    run2 = _run_benchmark(table, {**base_row, "cycle_id": "REPRO_RUN2_{:04d}"}, _ITERATIONS)

    m1 = statistics.median(run1)
    m2 = statistics.median(run2)

    # Tolerance: the second run must be within ±20% of the first
    # (or at most 1 ms absolute to avoid a division-by-zero on very fast runs)
    tolerance_ms = max(m1 * 0.20, 1.0)
    assert abs(m1 - m2) <= tolerance_ms, (
        f"Benchmark medians diverged: run1={m1:.2f} ms, run2={m2:.2f} ms "
        f"(tolerance={tolerance_ms:.2f} ms). Possible flaky CI runner or "
        "a regression that appears only on repeated writes (e.g., lock contention)."
    )


def test_live_mode_swallowed_error_within_budget(perf_db):
    """Live-mode swallow latency: even on DB error, write_telemetry_row returns
    within the budget (the swallow must not wait for a lock timeout).

    The sqlite3.connect timeout default is 10 s; a live-mode swallow on a
    non-existent table raises OperationalError immediately — no lock wait.
    This test asserts the swallow path is fast (<200 ms) so a broken table
    does not stall the execution path.
    """
    row = {
        "cycle_id": "CYCLE_SWALLOW_BENCH_001",
        "symphony_id": "SYM_SWALLOW",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    t0 = time.perf_counter()
    db.write_telemetry_row("nonexistent_table", row, mode="live")
    elapsed_ms = (time.perf_counter() - t0) * 1_000

    assert elapsed_ms < _P99_BUDGET_MS, (
        f"Live-mode swallow took {elapsed_ms:.2f} ms; must be < {_P99_BUDGET_MS} ms. "
        "A slow swallow blocks the execution path — check that sqlite3.OperationalError "
        "is raised immediately (not after a lock timeout)."
    )
