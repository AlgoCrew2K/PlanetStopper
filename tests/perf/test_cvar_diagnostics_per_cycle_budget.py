"""
RED tests — shadow-logging-pattern: cvar_diagnostics per-cycle write budget (H-3).

These tests assert the benchmark obligation from the shadow-logging-pattern plan
(feature-plans/decision-science/phase-1/shadow-logging-pattern/plan.md §Golden-
fixture tests 1, 2, 6).

They differ from the H4 helper latency tests (test_telemetry_helper_per_cycle_latency.py)
in one critical way: they exercise the **production call chain**
(`record_cvar_diagnostic`) rather than the raw `write_telemetry_row` helper.
This catches any overhead introduced by the thin wrapper (argument marshalling,
dict construction) that the helper-level bench misses.

Contract:
  - 100 sequential `record_cvar_diagnostic(...)` calls on a WAL-mode DB.
  - Median per-call wall-clock < 50 ms.
  - 99th-percentile per-call < 200 ms.
  - Two independent 100-call runs yield medians within ±20% of each other.

These thresholds match the record_shadow_observation precedent — that function's
production measured behaviour under similar conditions is the council §A.3 implicit
baseline.  50 ms leaves ample headroom within the 1-minute (60,000 ms) cadence.

Marked @pytest.mark.perf — excluded from the default pytest run.
Opt-in: pytest -m perf

Fixture provenance:
  tests/fixtures/math/cvar_diagnostics_per_cycle_bench.json
  — schema-derived from 021_cvar_diagnostics column spec; no producer values.

Binding references:
  - shadow-logging-pattern plan §Golden-fixture tests 1, 2, 6
  - h4-telemetry-helper plan deliverable 4
  - Architecture constraint 1: no blocking I/O on execution path
  - council §A.3 H-3: "non-zero non-blocking I/O cost" + "explicit benchmark obligation"
  - record_shadow_observation precedent: database.py:1147-1194
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

_ITERATIONS = 100

# Thresholds: shadow-logging-pattern plan §Golden-fixture tests 1 and 2.
# 50 ms is the median budget; 200 ms is the P99 tail guard.
# Both match the record_shadow_observation precedent the council cites.
_MEDIAN_BUDGET_MS = 50.0
_P99_BUDGET_MS = 200.0

# Reproducibility tolerance: ±20% of the first run's median (plan §Golden-fixture test 6).
_REPRODUCIBILITY_TOLERANCE_FRACTION = 0.20
_REPRODUCIBILITY_MIN_ABS_MS = 1.0  # avoid div-by-zero on extremely fast runners

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "math"
    / "cvar_diagnostics_per_cycle_bench.json"
)


@pytest.fixture(scope="module")
def bench_fixture() -> dict:
    """Load the benchmark fixture.

    RED: this fixture file does not exist yet — the test fails at collection.
    GREEN: sqlite-specialist creates it with the 021_cvar_diagnostics column shape.
    """
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------


def _create_cvar_table(conn: sqlite3.Connection) -> None:
    """Create cvar_diagnostics with the 021_cvar_diagnostics column spec.

    ts_utc has DEFAULT so callers may omit it, matching the migration DDL.
    """
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
    """Isolated WAL-mode DB with cvar_diagnostics; mirrors production DB setup.

    RED: passes once 021_cvar_diagnostics.sql is added to _MIGRATION_FILES and
    record_cvar_diagnostic exists — the cvar_diagnostics table must be present
    for the INSERT to succeed.
    """
    db_path = str(tmp_path / "perf_cvar.db")
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


def _run_bench(fixture: dict, n: int, run_tag: str) -> list[float]:
    """Execute n sequential record_cvar_diagnostic calls; return per-call ms list.

    Uses the production call chain (record_cvar_diagnostic), not the raw helper.
    cycle_id is varied per iteration so each INSERT is distinct (no UNIQUE key
    collision).  run_tag distinguishes the two reproducibility runs from each other
    and from the latency runs.
    """
    row = fixture["row_dict"]
    latencies: list[float] = []
    for i in range(n):
        cycle_id = f"{run_tag}_CYCLE_{i:04d}"
        t0 = time.perf_counter()
        db.record_cvar_diagnostic(
            cycle_id=cycle_id,
            symphony_id=row["symphony_id"],
            cvar_5pct=row["cvar_5pct"],
            cvar_5pct_stderr=row["cvar_5pct_stderr"],
            cvar_n_tail=row["cvar_n_tail"],
            cvar_5pct_long=row["cvar_5pct_long"],
            cvar_n_tail_long=row["cvar_n_tail_long"],
            mode="live",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1_000
        latencies.append(elapsed_ms)
    return latencies


def _p99(values: list[float]) -> float:
    """Return the 99th percentile (last element of the top 1%) of a list."""
    sorted_vals = sorted(values)
    # With 100 samples the 99th percentile is the 99th value (index 98).
    idx = max(0, int(len(sorted_vals) * 0.99) - 1)
    return sorted_vals[idx]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_record_cvar_diagnostic_median_latency_within_budget(perf_db, bench_fixture):
    """Shadow-logging-pattern plan §Golden-fixture test 1: median < 50 ms.

    Tests the production call chain (record_cvar_diagnostic), not the raw helper.
    50 ms leaves >1200× headroom within the 60,000 ms cadence window.
    Tolerance is absolute: any median above 50 ms fails regardless of P99.
    """
    latencies = _run_bench(bench_fixture, _ITERATIONS, "MEDIAN_RUN")
    median_ms = statistics.median(latencies)

    assert median_ms < _MEDIAN_BUDGET_MS, (
        f"record_cvar_diagnostic median latency {median_ms:.2f} ms exceeds "
        f"budget {_MEDIAN_BUDGET_MS} ms over {_ITERATIONS} iterations. "
        "H-3: per-cycle cvar_diagnostics write must fit within the 1-minute cadence. "
        "Check for missing WAL mode, lock contention, or unintentional sync I/O."
    )


def test_record_cvar_diagnostic_p99_latency_within_budget(perf_db, bench_fixture):
    """Shadow-logging-pattern plan §Golden-fixture test 2: P99 < 200 ms.

    Tail-latency guard against GC pauses and OS scheduling jitter on shared CI
    runners.  200 ms = 4× the median budget; still 300× below the 60,000 ms
    cycle window.
    """
    latencies = _run_bench(bench_fixture, _ITERATIONS, "P99_RUN")
    p99_ms = _p99(latencies)

    assert p99_ms < _P99_BUDGET_MS, (
        f"record_cvar_diagnostic P99 latency {p99_ms:.2f} ms exceeds "
        f"budget {_P99_BUDGET_MS} ms over {_ITERATIONS} iterations. "
        "H-3 P99 guard: tail spikes on the production call chain must not stall "
        "the execution path."
    )


def test_record_cvar_diagnostic_benchmark_is_reproducible(perf_db, bench_fixture):
    """Shadow-logging-pattern plan §Golden-fixture test 6: two runs yield medians within ±20%.

    Guards against a flaky CI runner masking a regression.  If the first run is
    extremely fast (< 1 ms median), the tolerance floor of 1 ms prevents a
    spurious failure on a cold-cache second run.

    Two separate run_tags so the INSERT cycle_ids do not collide.
    """
    run1 = _run_bench(bench_fixture, _ITERATIONS, "REPRO_A")
    run2 = _run_bench(bench_fixture, _ITERATIONS, "REPRO_B")

    m1 = statistics.median(run1)
    m2 = statistics.median(run2)

    tolerance_ms = max(m1 * _REPRODUCIBILITY_TOLERANCE_FRACTION, _REPRODUCIBILITY_MIN_ABS_MS)
    assert abs(m1 - m2) <= tolerance_ms, (
        f"Benchmark medians diverged between two runs: "
        f"run1={m1:.2f} ms, run2={m2:.2f} ms (tolerance={tolerance_ms:.2f} ms). "
        "Possible causes: flaky CI runner, OS scheduling variance, or a lock-contention "
        "regression that only appears on warm repeated writes."
    )


def test_record_cvar_diagnostic_exists_as_callable():
    """Contract gate: record_cvar_diagnostic must exist in database module.

    RED until the implementation ships.  Prevents the benchmark tests from
    producing misleading AttributeError tracebacks instead of explicit RED failures.
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "database.record_cvar_diagnostic does not exist. "
        "shadow-logging-pattern plan deliverable: sqlite-specialist must implement it "
        "as a thin wrapper over write_telemetry_row."
    )
    assert callable(db.record_cvar_diagnostic), (
        "database.record_cvar_diagnostic is not callable."
    )
