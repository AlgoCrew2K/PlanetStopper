"""
RED tests -- synthetic_history.get_cached_synthetic_history_only backward-
probe extension (PM live-gate finding, item R2, 2026-07-24).

THE FINDING: get_cached_synthetic_history_only originally keyed on TODAY's
date only, so the guard-alpha-preconditions replay column only hits on the
exact day the weekly autotune ran -- cold 6 days a week by construction
(the droplet's newest cache file was dated 2026-07-17, six days before the
live gate). This is an availability gap, not a correctness bug: the
existing zero-network guarantee (CACHE-HIT-ONLY, PM-ruled 2026-07-23) must
be preserved exactly -- probing backward through MORE candidate cache files
is still zero network calls.

THE FIX (this file pins the contract): probe backward from current_date_str
up to AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS trading days, using the CURRENT
holdings-hash for EACH candidate date (via the shared
_resolve_history_cache_key, so the hash-derivation logic is never
duplicated here -- these tests use the real production function to compute
where a fixture file must live, not a hand-rolled hash). Newest (closest to
current_date_str) hit wins. A holdings change since the aged file was
written must still degrade honestly (the aged file's embedded hash won't
match current_date_str's freshly-computed hash for CURRENT holdings, so it's
correctly treated as a miss -- no stale-holdings data is ever silently
served).

monkeypatch.chdir(tmp_path) guarantees an empty "cache" directory (the
cache dir is CWD-relative) so these tests never depend on -- or pollute --
the real repo's cache/ directory. current_date_str is an arbitrary FIXED
anchor ("2026-07-24", a Friday), never real wall-clock time -- the function
takes it as a parameter and does no clock reads itself.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import synthetic_history

_ANCHOR = "2026-07-24"  # arbitrary fixed Friday -- never real wall-clock time
_BOT_STATE = {"sym-backward-probe-001": {"current_holdings": [{"ticker": "SPY"}]}}
_STALE_BOT_STATE = {"sym-backward-probe-001": {"current_holdings": [{"ticker": "QQQ"}]}}


def _n_business_days_before(date_str: str, n: int) -> str:
    """Deterministic weekday-only backward walk (skips Sat/Sun) -- a
    reasonable, implementation-agnostic proxy for 'trading days' that
    doesn't require this test to encode a market-holiday calendar."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    remaining = n
    while remaining > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return d.strftime("%Y-%m-%d")


def _write_cache_file(bot_state: dict, date_str: str, content: dict) -> str:
    """Writes `content` to the REAL cache-file path _resolve_history_cache_key
    computes for (bot_state, date_str) -- uses the production key-derivation
    function itself (never a hand-rolled hash) so these tests exercise the
    reader's backward-probe logic, not a re-implementation of the hashing."""
    _, _, cache_file = synthetic_history._resolve_history_cache_key(bot_state, date_str)
    assert cache_file is not None
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(content, f)
    return cache_file


class TestNamedConstant:
    def test_autotune_cache_max_age_trading_days_is_a_named_constant(self):
        assert hasattr(synthetic_history, "AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS"), (
            "synthetic_history.py must export AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS "
            "as a named module-scope constant (PM live-gate ruling, R2)."
        )
        assert synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS == 10, (
            f"Expected 10 (weekly autotune cadence + buffer, per the PM's "
            f"ruling), got {synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS!r}."
        )


class TestBackwardProbeFindsAgedHit:
    def test_finds_a_hit_several_trading_days_back_when_today_is_cold(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        aged_date = _n_business_days_before(_ANCHOR, 3)
        content = {"sym-backward-probe-001": {aged_date: [{"return": 1.5}]}}
        _write_cache_file(_BOT_STATE, aged_date, content)
        # Deliberately no file written for _ANCHOR itself -- today is cold.

        result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == content, (
            f"Expected the aged (3-trading-days-back) cache content to be "
            f"found on a cold-today probe, got {result!r}."
        )

    def test_zero_fetch_calls_during_a_successful_backward_probe(self, tmp_path, monkeypatch):
        """The backward probe must preserve the existing CACHE-HIT-ONLY
        guarantee -- more candidate files checked is still zero network."""
        monkeypatch.chdir(tmp_path)
        aged_date = _n_business_days_before(_ANCHOR, 4)
        _write_cache_file(_BOT_STATE, aged_date, {"sym-backward-probe-001": {}})

        fetch_calls: list = []

        def _tracking_fetch_bars(*args, **kwargs):
            fetch_calls.append((args, kwargs))
            return []

        monkeypatch.setattr(synthetic_history, "fetch_bars", _tracking_fetch_bars)

        synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert fetch_calls == [], (
            f"fetch_bars was called {len(fetch_calls)} time(s) during a "
            "backward probe -- the extended probe must remain zero-network."
        )


class TestNewestHitWins:
    def test_prefers_the_more_recent_of_two_valid_aged_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        newer_date = _n_business_days_before(_ANCHOR, 2)
        older_date = _n_business_days_before(_ANCHOR, 5)
        newer_content = {"sym-backward-probe-001": {newer_date: [{"return": 9.9}]}}
        older_content = {"sym-backward-probe-001": {older_date: [{"return": -1.1}]}}
        _write_cache_file(_BOT_STATE, newer_date, newer_content)
        _write_cache_file(_BOT_STATE, older_date, older_content)

        result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == newer_content, (
            f"Expected the NEWER (2-trading-days-back) file to win over the "
            f"older (5-trading-days-back) one, got {result!r}."
        )

    def test_exact_today_hit_wins_over_any_aged_file(self, tmp_path, monkeypatch):
        """The backward-probe extension must never regress the original,
        already-tested exact-today-hit path -- today, when present, is
        always the newest candidate and must win."""
        monkeypatch.chdir(tmp_path)
        today_content = {"sym-backward-probe-001": {_ANCHOR: [{"return": 3.3}]}}
        aged_date = _n_business_days_before(_ANCHOR, 1)
        aged_content = {"sym-backward-probe-001": {aged_date: [{"return": -7.7}]}}
        _write_cache_file(_BOT_STATE, _ANCHOR, today_content)
        _write_cache_file(_BOT_STATE, aged_date, aged_content)

        result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == today_content


class TestBoundedProbe:
    def test_does_not_find_a_hit_beyond_the_max_age_window(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        too_old_date = _n_business_days_before(
            _ANCHOR, synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS + 2
        )
        _write_cache_file(
            _BOT_STATE, too_old_date, {"sym-backward-probe-001": {too_old_date: [{"return": 5.0}]}}
        )

        result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result is None, (
            f"A cache file {synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS + 2} "
            "trading days back (beyond the max-age window) must NOT be found "
            f"-- the backward probe must be bounded, not unlimited. Got {result!r}."
        )

    def test_finds_a_hit_exactly_at_the_max_age_boundary(self, tmp_path, monkeypatch):
        """Boundary-inclusive: exactly AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS
        back must still be reachable (a naive off-by-one range() could
        exclude the boundary itself)."""
        monkeypatch.chdir(tmp_path)
        boundary_date = _n_business_days_before(
            _ANCHOR, synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS
        )
        content = {"sym-backward-probe-001": {boundary_date: [{"return": 2.0}]}}
        _write_cache_file(_BOT_STATE, boundary_date, content)

        result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == content, (
            f"A cache file exactly {synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS} "
            f"trading days back must be found (inclusive boundary), got {result!r}."
        )


class TestHoldingsChangeDegradesHonestly:
    def test_aged_file_from_different_holdings_is_not_used(self, tmp_path, monkeypatch):
        """A holdings change since the aged file was cached means its
        embedded hash won't match CURRENT holdings' freshly-computed hash --
        correctly a miss, never silently serving stale-holdings data."""
        monkeypatch.chdir(tmp_path)
        aged_date = _n_business_days_before(_ANCHOR, 3)
        # Written under STALE (old) holdings -- its filename hash reflects
        # _STALE_BOT_STATE, not the CURRENT _BOT_STATE the probe uses.
        _write_cache_file(
            _STALE_BOT_STATE, aged_date, {"sym-backward-probe-001": {aged_date: [{"return": 1.0}]}}
        )

        result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result is None, (
            f"An aged cache file written under DIFFERENT holdings must not "
            f"be served for the current (different) holdings-hash -- got "
            f"{result!r}. Silently serving it would be stale-holdings data "
            "leakage."
        )


# ---------------------------------------------------------------------------
# Hygiene cycle (fix/route-phantom-keys-log-noise, 2026-07-25): cache-miss
# vs corruption log classification.
#
# THE DEFECT: get_cached_synthetic_history_only's backward walk-back (up to
# AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS=10 candidates) calls load_cached_history
# per candidate, and a PLAIN MISSING file (the expected case -- the cache is
# written weekly, so most days the newest-date probes are simply absent) hits
# load_cached_history's broad `except (OSError, ...)` handler and logs a
# WARNING "corrupt or unreadable cache file ... [Errno 2] ..." -- up to ~16
# misleading WARNINGs per call for what is normal operation, not corruption.
#
# THE FIX (observable contract only -- deliberately NOT prescribing HOW
# load_cached_history/get_cached_synthetic_history_only implement the split,
# so either (a) a kwarg on load_cached_history with a behavior-preserving
# default, or (b) an os.path.exists() pre-check in the walk-back loop before
# ever calling load_cached_history, satisfies these tests):
#   - A plain miss produces AT MOST ONE compact log record per walk-back
#     call, at level <= INFO, whose message says "miss" and never "corrupt".
#   - Genuine corruption (a file that EXISTS but is unparseable) still
#     produces the existing per-file WARNING with "corrupt" wording,
#     unaffected -- and the walk-back continues past it exactly as today.
#   - Zero change to return values (a hit's content is unaffected either way).
# ---------------------------------------------------------------------------


class TestCacheMissLogClassification:
    def test_all_miss_walk_back_logs_at_most_one_record_never_saying_corrupt(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.chdir(tmp_path)  # empty cache dir -- every candidate is a plain miss

        with caplog.at_level(logging.DEBUG):
            result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result is None
        records = list(caplog.records)
        assert len(records) <= 1, (
            f"A full all-miss walk-back ({synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS + 1} "
            f"candidates) produced {len(records)} log record(s) -- expected at most 1 "
            f"(one honest summary, not one WARNING per missing file). Records: "
            f"{[r.getMessage() for r in records]!r}"
        )
        assert not any(r.levelno >= logging.WARNING for r in records), (
            f"A plain cache miss must never log at WARNING or above -- it is the "
            f"EXPECTED case (weekly cache vs daily probes), not an anomaly. Records: "
            f"{[(r.levelname, r.getMessage()) for r in records]!r}"
        )
        if records:
            msg = records[0].getMessage().lower()
            assert records[0].levelno <= logging.INFO, (
                f"The miss-summary record must be at INFO or lower, got "
                f"{records[0].levelname} ({records[0].getMessage()!r})."
            )
            assert "miss" in msg, (
                f"The miss-summary record must say 'miss', got {records[0].getMessage()!r}."
            )
            assert "corrupt" not in msg, (
                f"A plain miss must never use corruption wording, got "
                f"{records[0].getMessage()!r}."
            )

    def test_day_zero_hit_emits_zero_log_records(self, tmp_path, monkeypatch, caplog):
        # Plan edge case: "Walk-back where day-0 hits immediately -> zero
        # miss lines (nothing to summarize)."
        monkeypatch.chdir(tmp_path)
        content = {"sym-backward-probe-001": {_ANCHOR: [{"return": 4.4}]}}
        _write_cache_file(_BOT_STATE, _ANCHOR, content)

        with caplog.at_level(logging.DEBUG):
            result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == content
        assert len(caplog.records) == 0, (
            f"An immediate day-0 hit must produce zero log records (nothing was "
            f"missing to summarize), got "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
        )

    def test_mixed_miss_then_hit_logs_at_most_one_record_and_result_unchanged(
        self, tmp_path, monkeypatch, caplog
    ):
        # Plan edge case: "Walk-back where SOME days miss and an older day
        # hits -> still at most one INFO summary; the hit proceeds normally."
        monkeypatch.chdir(tmp_path)
        aged_date = _n_business_days_before(_ANCHOR, 3)
        content = {"sym-backward-probe-001": {aged_date: [{"return": 1.5}]}}
        _write_cache_file(_BOT_STATE, aged_date, content)
        # No file written for _ANCHOR or the 2 candidates in between -- those
        # are plain misses preceding the hit.

        with caplog.at_level(logging.DEBUG):
            result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == content, (
            "The hit's content must be unaffected by the log-classification "
            f"change (AC-6 return-value parity), got {result!r}."
        )
        records = list(caplog.records)
        assert len(records) <= 1, (
            f"Misses preceding a hit must still collapse to at most 1 summary "
            f"record, got {len(records)}: {[r.getMessage() for r in records]!r}"
        )
        assert not any(r.levelno >= logging.WARNING for r in records)
        if records:
            assert "corrupt" not in records[0].getMessage().lower()

    def test_corrupt_file_mid_walkback_still_warns_and_walkback_continues(
        self, tmp_path, monkeypatch, caplog
    ):
        # Plan edge case: "A genuinely corrupt file mid-walk-back -> WARNING
        # fires for that file; the walk-back continues past it exactly as
        # today." Corrupt file at day-0 (exists but unparseable), valid hit
        # at an aged date behind it.
        monkeypatch.chdir(tmp_path)
        _, _, today_cache_file = synthetic_history._resolve_history_cache_key(_BOT_STATE, _ANCHOR)
        assert today_cache_file is not None
        with open(today_cache_file, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")  # genuinely corrupt, file EXISTS

        aged_date = _n_business_days_before(_ANCHOR, 2)
        aged_content = {"sym-backward-probe-001": {aged_date: [{"return": 6.6}]}}
        _write_cache_file(_BOT_STATE, aged_date, aged_content)

        with caplog.at_level(logging.DEBUG):
            result = synthetic_history.get_cached_synthetic_history_only(_BOT_STATE, _ANCHOR)

        assert result == aged_content, (
            f"The walk-back must continue past the corrupt day-0 file and find "
            f"the valid aged hit behind it, got {result!r}."
        )
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, (
            "A genuinely corrupt (existing but unparseable) cache file must still "
            "produce a WARNING -- AC-6: corruption wording/level preserved verbatim. "
            f"All records: {[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
        )
        assert any("corrupt" in r.getMessage().lower() for r in warning_records), (
            f"The corrupt-file WARNING must use 'corrupt' wording (unchanged), got "
            f"{[r.getMessage() for r in warning_records]!r}"
        )
