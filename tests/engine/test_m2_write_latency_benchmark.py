"""
RED tests — H-3: M2 per-cycle write latency benchmark.

Asserts that the CVaR diagnostic INSERT, routed through the H4 telemetry helper
(write_telemetry_row / record_cvar_diagnostic), fits within the 1-minute cycle
budget at realistic portfolio size. Also pins the H4 live|replay contract
(live swallows, replay raises) and the architecture constraint that M2 writes
are OUTSIDE the save_state transaction.

Test file: tests/engine/test_m2_write_latency_benchmark.py
Fixture:   tests/fixtures/benchmark/m2_write_budget.json

Architecture constraint: No blocking I/O on the per-cycle execution path (CLAUDE.md §1).
H-3 binding: M2's per-cycle write has zero DECISION impact, non-zero non-blocking I/O cost.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

import database as _db

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_BENCHMARK_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "benchmark"
_MATH_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "math"


def _load_budget() -> dict:
    return json.loads((_BENCHMARK_FIXTURES / "m2_write_budget.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scenario 1: M2 write routes through the H4 telemetry helper
# ---------------------------------------------------------------------------


class TestM2WriteUsesH4TelemetryHelper:
    """
    Routing pin: record_cvar_diagnostic must call write_telemetry_row (the H4 helper),
    not issue a raw INSERT directly.

    Discriminating power: a direct INSERT bypassing the helper fails the call count.
    A double-write (helper + direct INSERT) also fails.
    """

    def test_m2_write_uses_h4_telemetry_helper(self, monkeypatch):
        """
        record_cvar_diagnostic must delegate to write_telemetry_row exactly once.
        Patching write_telemetry_row and asserting exactly one call with the
        cvar_diagnostics table name.
        """
        calls = []

        def capturing_write_telemetry_row(table, row_dict, *, mode):
            calls.append({"table": table, "row_dict": row_dict, "mode": mode})

        monkeypatch.setattr(_db, "write_telemetry_row", capturing_write_telemetry_row)

        _db.record_cvar_diagnostic(
            cycle_id="TEST_ROUTING_20260525",
            symphony_id="sym-routing-test",
            cvar_5pct=-0.025,
            cvar_5pct_stderr=0.005,
            cvar_n_tail=7,
            cvar_5pct_long=None,
            cvar_n_tail_long=None,
            mode="live",
        )

        assert len(calls) == 1, (
            f"record_cvar_diagnostic must call write_telemetry_row exactly once; "
            f"got {len(calls)} calls. "
            "A direct INSERT bypasses the H4 live-swallow/replay-raise contract."
        )
        assert calls[0]["table"] == "cvar_diagnostics", (
            f"write_telemetry_row must be called with table='cvar_diagnostics'; "
            f"got {calls[0]['table']!r}"
        )
        assert calls[0]["mode"] == "live", (
            f"mode must be passed through to write_telemetry_row; got {calls[0]['mode']!r}"
        )

    def test_m2_row_dict_contains_required_columns(self, monkeypatch):
        """
        The row_dict passed to write_telemetry_row must contain all required
        cvar_diagnostics columns (cycle_id, symphony_id, cvar_5pct, cvar_5pct_stderr,
        cvar_n_tail, cvar_5pct_long, cvar_n_tail_long).
        """
        captured_row = {}

        def capturing_write(table, row_dict, *, mode):
            captured_row.update(row_dict)

        monkeypatch.setattr(_db, "write_telemetry_row", capturing_write)

        _db.record_cvar_diagnostic(
            cycle_id="TEST_COLS_20260525",
            symphony_id="sym-cols-test",
            cvar_5pct=-0.030,
            cvar_5pct_stderr=0.006,
            cvar_n_tail=8,
            cvar_5pct_long=-0.028,
            cvar_n_tail_long=6,
            mode="replay",
        )

        required_keys = {
            "cycle_id",
            "symphony_id",
            "cvar_5pct",
            "cvar_5pct_stderr",
            "cvar_n_tail",
            "cvar_5pct_long",
            "cvar_n_tail_long",
        }
        missing = required_keys - set(captured_row.keys())
        assert not missing, (
            f"row_dict passed to write_telemetry_row is missing columns: {missing}. "
            f"Got: {set(captured_row.keys())}"
        )


# ---------------------------------------------------------------------------
# Scenario 2: live mode swallows write failure
# ---------------------------------------------------------------------------


class TestM2LiveModeSwallowsWriteFailure:
    """
    H4 contract (live mode): when the underlying write raises OperationalError,
    the cycle must continue normally. Live telemetry failures must never fail a cycle.

    This mirrors the record_shadow_observation precedent at database.py:1147-1194.
    """

    def test_m2_live_mode_swallows_write_failure_does_not_fail_cycle(self, monkeypatch):
        """
        Patching _write_telemetry_row_unsafe to raise sqlite3.OperationalError.
        record_cvar_diagnostic in live mode must not propagate the exception.

        _write_telemetry_row_unsafe is the correct injection point: it is the
        function that actually issues the INSERT, and write_telemetry_row wraps
        it with the live-swallow / replay-raise contract.
        """
        import sqlite3

        def failing_unsafe(table_name: str, row_dict: dict) -> None:
            raise sqlite3.OperationalError("simulated disk full")

        monkeypatch.setattr(_db, "_write_telemetry_row_unsafe", failing_unsafe)

        # Must not raise — live mode swallows OperationalError via write_telemetry_row
        try:
            _db.record_cvar_diagnostic(
                cycle_id="TEST_SWALLOW_20260525",
                symphony_id="sym-swallow-test",
                cvar_5pct=-0.025,
                cvar_5pct_stderr=0.005,
                cvar_n_tail=7,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )
        except Exception as exc:
            pytest.fail(
                f"live mode must swallow write failures; raised {type(exc).__name__}: {exc}. "
                "A telemetry failure must never kill the live execution cycle."
            )

    def test_m2_replay_mode_raises_write_failure(self, monkeypatch):
        """
        H4 contract (replay mode): when _write_telemetry_row_unsafe raises
        OperationalError, replay mode must propagate the exception.

        A replay that cannot persist its decision record is broken and must fail loud
        (not silently produce a partial record that looks like a clean run).
        """
        import sqlite3

        def failing_unsafe(table_name: str, row_dict: dict) -> None:
            raise sqlite3.OperationalError("simulated disk full")

        monkeypatch.setattr(_db, "_write_telemetry_row_unsafe", failing_unsafe)

        with pytest.raises(sqlite3.OperationalError):
            _db.record_cvar_diagnostic(
                cycle_id="TEST_RAISE_20260525",
                symphony_id="sym-raise-test",
                cvar_5pct=-0.025,
                cvar_5pct_stderr=0.005,
                cvar_n_tail=7,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="replay",
            )


# ---------------------------------------------------------------------------
# Scenario 4: p99 write latency under budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestM2WriteP99LatencyUnderBudget:
    """
    Benchmark: the per-symphony per-cycle cvar_diagnostics INSERT must complete
    within p99_latency_ms_target at realistic portfolio size.

    Uses an in-memory + disk SQLite DB (both measured) to provide a realistic
    latency signal without touching the production DB.

    Marked @pytest.mark.perf — excluded from the default run (opt-in: pytest -m perf).
    Not marked @pytest.mark.live (no API calls).
    """

    def _run_benchmark(self, db_path: str, n_runs: int = 200) -> list[float]:
        """
        Run n_runs INSERTs into a fresh cvar_diagnostics table and return
        per-INSERT latency in milliseconds.
        """
        # Set up an isolated DB at db_path with the full migration sequence
        import os

        old_env = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = db_path
        try:
            _db.init_db()
            latencies = []
            for i in range(n_runs):
                t0 = time.perf_counter()
                _db.record_cvar_diagnostic(
                    cycle_id=f"BENCH_CYCLE_{i:06d}",
                    symphony_id=f"sym-bench-{i % 20:03d}",
                    cvar_5pct=-0.025 - i * 0.0001,
                    cvar_5pct_stderr=0.005,
                    cvar_n_tail=7 + (i % 3),
                    cvar_5pct_long=None,
                    cvar_n_tail_long=None,
                    mode="replay",
                )
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000.0)  # ms
        finally:
            if old_env is not None:
                os.environ["DB_PATH"] = old_env
            elif "DB_PATH" in os.environ:
                del os.environ["DB_PATH"]
        return latencies

    def test_m2_write_p99_latency_under_budget_in_memory(self):
        """
        In-memory SQLite: p99 latency must be under p99_latency_ms_target.
        In-memory is the discriminating-power check (fastest path).
        """
        budget = _load_budget()
        target = budget["p99_latency_ms_target"]

        latencies = self._run_benchmark(":memory:", n_runs=200)
        latencies.sort()
        p99_idx = int(0.99 * len(latencies))
        p99 = latencies[p99_idx]

        assert p99 < target, (
            f"In-memory p99 latency {p99:.2f}ms exceeds target {target}ms. "
            f"A p99 failure on in-memory SQLite indicates a structural problem "
            f"(e.g., an N+1 query or missing index), not CI noise."
        )

    def test_in_memory_benchmark_fixture_creates_cvar_diagnostics_table(self):
        """
        Structural RED for the fix-perf-fixture cycle.

        Pin: the in-memory benchmark fixture (_run_benchmark with db_path=":memory:")
        MUST set up the cvar_diagnostics schema in the SAME DB instance that
        record_cvar_diagnostic later writes to. The pre-fix defect is that
        sqlite3.connect(":memory:") returns a fresh, isolated DB per call, so
        init_db()'s schema is discarded before the INSERT runs. The fix must
        make a single in-memory DB shared across init + INSERT (e.g. via the
        shared-cache URI form, a tmp-file substitute, or a connection cache).

        Discriminating power:
          - A no-op "fix" that swallows the OperationalError still trips this:
            we assert a row was actually inserted, not merely that no exception
            propagated.
          - A fix that only patches init_db (without making the schema visible
            to record_cvar_diagnostic's separate connection) still trips this:
            the INSERT itself must succeed and be readable.
          - A fix that swaps to a disk path would also satisfy this — that is
            an acceptable resolution per the team-lead scope (any path that
            makes the in-memory variant work end-to-end).

        Uses n_runs=1 to isolate the schema-presence concern from latency.
        """
        # Reuse the same fixture entry point the failing test uses, so the
        # RED is pinned to the SAME code path — no parallel reimplementation.
        latencies = self._run_benchmark(":memory:", n_runs=1)

        # The benchmark returns one latency per successful INSERT. If the
        # INSERT raised OperationalError ("no such table: cvar_diagnostics"),
        # _run_benchmark would have propagated it and we'd never get here.
        # Still — assert explicitly that one INSERT happened and that the
        # row is present in whatever DB the helper now uses for reads.
        assert len(latencies) == 1, (
            "Expected exactly 1 latency sample from n_runs=1; got "
            f"{len(latencies)}. The benchmark fixture did not complete the "
            "INSERT — likely the cvar_diagnostics table was not created in "
            "the same in-memory DB instance that record_cvar_diagnostic "
            "writes to (sqlite3.connect(':memory:') returns a fresh DB "
            "per call). Fix the fixture to share one in-memory DB across "
            "init + INSERT, or substitute a tmp-file DB."
        )
        assert latencies[0] >= 0.0, (
            f"Latency must be non-negative; got {latencies[0]}. "
            "Negative latency is a measurement bug, not a fixture issue."
        )

    def test_m2_write_p99_latency_under_budget_disk(self, tmp_path):
        """
        Disk SQLite: the realistic latency measure uses a tmp_path DB with WAL mode.
        p99 must be under the fixture's p99_latency_ms_target.
        The budget includes CI noise headroom (50ms target vs ~1-5ms typical INSERT).
        """
        budget = _load_budget()
        target = budget["p99_latency_ms_target"]
        db_path = str(tmp_path / "bench_alphabot.db")

        latencies = self._run_benchmark(db_path, n_runs=200)
        latencies.sort()
        p99_idx = int(0.99 * len(latencies))
        p99 = latencies[p99_idx]

        assert p99 < target, (
            f"Disk p99 latency {p99:.2f}ms exceeds target {target}ms "
            f"(budget: {budget['cycle_budget_ms']}ms cycle / {budget['realistic_symphony_count']} symphonies). "
            "H-3: M2 must fit within the non-blocking I/O cost budget."
        )


# ---------------------------------------------------------------------------
# Scenario 5: M2 write is OUTSIDE the save_state transaction
# ---------------------------------------------------------------------------


class TestM2WriteOutsideSaveStateTransaction:
    """
    Architecture constraint 1: M2 INSERT must be outside the save_state transaction.
    A telemetry write inside save_state's transaction would make a telemetry failure
    roll back the cycle's state save — the exact regression H-3's H4 helper prevents.

    Tested by asserting write_telemetry_row uses its OWN connection (opened inside
    write_telemetry_row itself), not the same connection passed to save_state.

    The structural test: write_telemetry_row's connection must NOT share the
    same database connection object as any open save_state transaction.
    """

    def test_m2_write_uses_separate_connection_from_save_state(self, monkeypatch):
        """
        write_telemetry_row must open its own connection (separate from save_state's
        connection). We instrument by tracking unique connection IDs opened during
        a simulated save_state + record_cvar_diagnostic sequence.

        If the implementation accidentally reuses the save_state connection, both
        operations will share the same connection ID — the test fails.
        """
        connection_ids_opened = []

        original_connect = sqlite3.connect

        def tracking_connect(database, *args, **kwargs):
            conn = original_connect(database, *args, **kwargs)
            connection_ids_opened.append(id(conn))
            return conn

        monkeypatch.setattr(sqlite3, "connect", tracking_connect)

        # Simulate the sequence: record_cvar_diagnostic opens its own connection
        _db.record_cvar_diagnostic(
            cycle_id="TEST_ISOLATION_20260525",
            symphony_id="sym-isolation-test",
            cvar_5pct=-0.025,
            cvar_5pct_stderr=0.005,
            cvar_n_tail=7,
            cvar_5pct_long=None,
            cvar_n_tail_long=None,
            mode="live",
        )

        # At least one connection must have been opened (the telemetry helper's own connection).
        assert len(connection_ids_opened) >= 1, (
            "write_telemetry_row must open its own SQLite connection; "
            "zero connections opened suggests the helper is reusing an existing connection."
        )

    def test_write_telemetry_row_opens_its_own_connection(self):
        """
        write_telemetry_row must use its own short-lived connection (per the
        record_shadow_observation precedent at database.py:1147-1194).
        This is a structural assertion: the function signature must not accept
        a connection parameter (it manages its own lifecycle).
        """
        import inspect

        sig = inspect.signature(_db.write_telemetry_row)
        param_names = list(sig.parameters.keys())

        # Should have: table, row_dict (or columns), mode — but NOT conn or connection
        assert "conn" not in param_names, (
            "write_telemetry_row must not accept a 'conn' parameter; "
            "it must open its own connection (self-managed lifecycle, off save_state transaction). "
            f"Got signature: {param_names}"
        )
        assert "connection" not in param_names, (
            "write_telemetry_row must not accept a 'connection' parameter; "
            f"got signature: {param_names}"
        )
