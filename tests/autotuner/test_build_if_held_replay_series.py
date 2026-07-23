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

import autotuner
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
