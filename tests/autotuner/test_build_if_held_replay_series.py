"""
RED tests -- autotuner.build_if_held_replay_series, CACHE-HIT-ONLY contract
(PM ruling, 2026-07-23, resolving the replay-data-sourcing open question
flagged during RED-writing -- see .claude/tdd-handoff.md and this function's
docstring in autotuner.py).

THE CONTRACT: this function may read synthetic_history's existing file cache
but must NEVER trigger a live fetch (synthetic_history.fetch_bars, the sole
network entry point for bar data in this codebase). On a cache miss it
returns None -- it does not fall through to synthetic_history's normal
fetch-on-miss behavior (unlike generate_synthetic_history, which DOES fetch
on a miss; build_if_held_replay_series is a deliberately truncated,
read-only-of-the-cache sibling).

WHY: Architecture Constraint 5 (UI never reruns the engine), request latency
(a cold fetch can take minutes inside a dashboard GET), and the standing
bill-protection directive (a dashboard refresh must never drive Alpaca fetch
volume).

monkeypatch.chdir(tmp_path) guarantees an empty "cache" directory (synthetic_
history.py's cache dir is CWD-relative) so this test never depends on -- or
pollutes -- the real repo's cache/ directory.
"""

from __future__ import annotations

import pytest

import autotuner
import database
import synthetic_history


class TestCacheMissNeverFetches:
    def test_cold_cache_returns_none_without_calling_fetch_bars(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # guarantees an empty ./cache dir -- no real cache can hit

        fetch_calls: list = []

        def _tracking_fetch_bars(*args, **kwargs):
            fetch_calls.append((args, kwargs))
            return []

        monkeypatch.setattr(synthetic_history, "fetch_bars", _tracking_fetch_bars)

        result = autotuner.build_if_held_replay_series("sym-cache-miss-001")

        assert fetch_calls == [], (
            f"synthetic_history.fetch_bars was called {len(fetch_calls)} time(s) "
            "on a cold cache -- build_if_held_replay_series must be CACHE-HIT-ONLY "
            "and NEVER trigger a live fetch (PM ruling 2026-07-23)."
        )
        assert result is None, (
            f"Expected None on a cache miss (honest degradation, no fabricated "
            f"data), got {result!r}."
        )


class TestWarmCacheReturnsRealStatsWithoutFetching:
    def test_warm_cache_returns_real_series_and_never_calls_fetch_bars(self, tmp_path, monkeypatch):
        """PM ruling requirement (c) (2026-07-23 follow-up): 'warm cache ->
        real replay stats WITHOUT any fetch call'. Mocks
        synthetic_history.load_cached_history -- the cache-READ boundary --
        to simulate a HIT, regardless of how the cache-file KEY is derived
        internally (robust to a future refactor sharing key-derivation with
        generate_synthetic_history, which the PM separately directed ga-impl
        to do). Proves the real if-held series flows through via the REAL
        collect_if_held_daily_returns on a genuine hit, with zero
        fetch_bars calls -- the positive-path sibling of the cold-cache test
        above."""
        monkeypatch.chdir(tmp_path)

        sym_id = "sym-warm-cache-001"
        history_data = {
            sym_id: {
                "2026-01-05": [{"return": 1.5}],
                "2026-01-06": [{"return": -2.0}],
                "2026-01-07": [{"return": 0.3}],
            }
        }

        monkeypatch.setattr(
            database,
            "load_state",
            lambda: {sym_id: {"current_holdings": [{"ticker": "SPY"}]}},
        )
        monkeypatch.setattr(
            synthetic_history,
            "load_cached_history",
            lambda cache_file: history_data,
        )

        fetch_calls: list = []

        def _tracking_fetch_bars(*args, **kwargs):
            fetch_calls.append((args, kwargs))
            return []

        monkeypatch.setattr(synthetic_history, "fetch_bars", _tracking_fetch_bars)

        result = autotuner.build_if_held_replay_series(sym_id)

        expected = autotuner.collect_if_held_daily_returns(history_data, sym_id)
        assert result == pytest.approx(expected), (
            f"Expected the REAL collect_if_held_daily_returns output on the "
            f"cache-hit data ({expected!r}), got {result!r} -- a warm cache "
            "must produce genuinely computed stats, not a fabricated or "
            "partial value."
        )
        assert fetch_calls == [], (
            f"synthetic_history.fetch_bars was called {len(fetch_calls)} "
            "time(s) even though the cache HIT -- a warm cache must never "
            "touch the network either (PM ruling 2026-07-23)."
        )
