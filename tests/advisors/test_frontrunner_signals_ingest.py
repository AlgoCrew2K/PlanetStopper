"""RED tests — AC-1: daily-cached Atlas pull for advisors/frontrunner_signals.py.

Module under test: advisors.frontrunner_signals.load_frontrunner_signals (NEW
module, NOT-STARTED as of this commit — implementer is fr-data, sqlite-specialist,
per team-lead's kickoff).

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-1, fr-data's posted +
team-lead-ratified interface, 2026-07-16):

    load_frontrunner_signals(*, force_refresh=False, db_path=None) -> dict
    -> {available: bool, signals: list[dict], stats: dict, fetched_at: str|None,
        source: "captplanet", reason?: str}

    - Pulls `captplanet.frontrunners` through atlas_cache.cached_pull under a
      DEDICATED cache key (`captplanet.frontrunners.fr_checks` — confirmed by
      fr-data as matching the worktree's untracked dev-cache row exactly),
      ttl_days=1 — distinct from community_strats.py's weekly
      `captplanet.strategies` key (AC-1: "separate from the weekly strategies
      key").
    - Bounded fetch: server-side projection excludes `backtest.equity_curve`
      and includes `"_id": 0` (DE-ATLAS-CACHE-001 precedent); wall-clock-bounded
      via the `_bounded_fetch_fn` pattern (DE-CS-002 precedent, mirrored from
      advisors/community_strats.py's own `_ATLAS_FETCH_TIMEOUT_S` wrapper).
    - D-1 never-raises: reason = "AtlasFetchTimeout" (bound expiry) |
      type(exc).__name__ | "AtlasCacheUnavailable" (cached_pull returned None,
      no timeout) — fr-data's posted D-1 taxonomy.
    - `db_path` kwarg: additive beyond the plan's literal AC-1 signature,
      ratified by team-lead (fetch_universe precedent) — needed so a
      non-cache-hit's warehouse persist (AC-2) is test-observable from this
      module's own public entry point.

FIXTURE PROVENANCE: `atlas_raw_docs_signals.json` — 10 raw Atlas docs pulled
VERBATIM from the worktree's `alphabot_atlas_cache.db` dev cache
(captured-from-producer; every field asserted here traces to a real doc).

TEST-DESIGN IDIOM: mirrors advisors/community_strats.py's own
`_bounded_fetch_fn`/timeout/D-1 test conventions
(tests/advisors/test_community_strats_timeout.py) — same two-layer fetch-seam
pattern (Layer 1: `atlas_cache.cached_pull` forced to call `fn()` directly;
Layer 2: `pymongo.MongoClient` controls the innermost behavior), same
deterministic `ThreadPoolExecutor`-mock timeout technique (no real sleep), same
D-1 secret-leakage guard. No live network calls anywhere in this file (AC-8).
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"

# Mirrors community_strats.py's own bound-selection rationale — the daily cache
# means this fetch runs at most once/day, so a generous bound is acceptable; the
# exact value is asserted structurally (>10s, matching the serverSelectionTimeoutMS
# floor rationale), never hardcoded against the specific constant.
_MARGIN_S = 3.0


@pytest.fixture
def mod():
    from advisors import frontrunner_signals

    return frontrunner_signals


@pytest.fixture
def raw_atlas_docs() -> list[dict]:
    return json.loads((_FIXTURES_DIR / "atlas_raw_docs_signals.json").read_text())


def _recursive_contains(obj: Any, substring: str) -> bool:
    if isinstance(obj, str):
        return substring in obj
    if isinstance(obj, dict):
        return any(_recursive_contains(v, substring) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_recursive_contains(item, substring) for item in obj)
    return False


def _make_timeout_executor_mock():
    """Deterministic timeout: fut.result() raises TimeoutError with no real sleep.
    Mirrors community_strats.py's own test helper exactly (same technique,
    same AC-3-class shutdown(wait=False) guard)."""
    mock_future = MagicMock(spec=concurrent.futures.Future)
    mock_future.result.side_effect = concurrent.futures.TimeoutError()

    mock_executor = MagicMock()
    mock_executor.submit.return_value = mock_future
    mock_executor.__enter__ = MagicMock(return_value=mock_executor)
    mock_executor.__exit__ = MagicMock(return_value=False)

    mock_executor_class = MagicMock(return_value=mock_executor)
    return mock_executor_class, mock_executor, mock_future


def _make_fast_mongo_client_mock(docs: list) -> MagicMock:
    """A MagicMock MongoClient whose collection.find(...) yields `docs`
    immediately. Mirrors community_strats.py's fixture-mock chain."""
    mock_cursor = MagicMock()
    mock_cursor.__iter__ = MagicMock(side_effect=lambda: iter(docs))
    mock_cursor.limit = MagicMock(return_value=mock_cursor)

    mock_collection = MagicMock()
    mock_collection.find.return_value = mock_cursor

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    return mock_client


# ---------------------------------------------------------------------------
# AC-1: dedicated cache key, distinct from the weekly strategies key
# ---------------------------------------------------------------------------


def test_uses_a_dedicated_cache_key_distinct_from_the_weekly_strategies_key(mod, monkeypatch):
    """AC-1: 'a DEDICATED cache key (separate from the weekly strategies key)'.
    Asserts the actual collection-name argument passed to atlas_cache.cached_pull
    is neither empty nor equal to community_strats.py's own
    "captplanet.strategies" key — a shared key would silently cross-contaminate
    the two caches (different TTLs, different payload shapes)."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    captured = {}

    def _capture_and_shortcircuit(collection_name, fetch_fn, **kwargs):
        captured["collection_name"] = collection_name
        captured["ttl_days"] = kwargs.get("ttl_days")
        return []

    with patch("advisors.atlas_cache.cached_pull", side_effect=_capture_and_shortcircuit):
        mod.load_frontrunner_signals()

    assert captured.get("collection_name"), "load_frontrunner_signals must call cached_pull with a collection_name"
    assert captured["collection_name"] != "captplanet.strategies", (
        f"cache key {captured['collection_name']!r} collides with community_strats.py's "
        "weekly key — AC-1 requires a DEDICATED key."
    )


def test_ttl_days_is_one_day_not_the_weekly_default(mod, monkeypatch):
    """AC-1: 'DAILY cache (atlas_cache.cached_pull(ttl_days=1))' — must NOT use
    atlas_cache's 7-day default (which is correct for the weekly community-strats
    cache but wrong here — a daily signal refresh at 7-day TTL would serve
    week-old RSI values as if they were today's)."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    captured = {}

    def _capture_and_shortcircuit(collection_name, fetch_fn, **kwargs):
        captured["ttl_days"] = kwargs.get("ttl_days")
        return []

    with patch("advisors.atlas_cache.cached_pull", side_effect=_capture_and_shortcircuit):
        mod.load_frontrunner_signals()

    assert captured.get("ttl_days") == 1, (
        f"expected ttl_days=1 (daily cache), got {captured.get('ttl_days')!r}. "
        "A week-old RSI value would be stale-but-silently-served."
    )


# ---------------------------------------------------------------------------
# AC-1: bounded, projected fetch
# ---------------------------------------------------------------------------


def test_fetch_projection_excludes_equity_curve_and_suppresses_object_id(mod, monkeypatch):
    """AC-1: 'server-side projection excludes backtest.equity_curve and includes
    "_id": 0' (DE-ATLAS-CACHE-001). Forces the live-fetch leg and inspects the
    projection dict actually passed to collection.find(...)."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

    mock_collection = MagicMock()
    mock_collection.find.return_value = iter([])
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    with (
        patch(
            "advisors.atlas_cache.cached_pull",
            side_effect=lambda col, fn, **kw: fn(),
        ),
        patch("pymongo.MongoClient", return_value=mock_client),
    ):
        mod.load_frontrunner_signals()

    assert mock_collection.find.called, "the fetch must call collection.find(...)"
    _, kwargs_or_args = mock_collection.find.call_args, mock_collection.find.call_args
    call = mock_collection.find.call_args
    # projection may be passed positionally (args[1]) or as a kwarg — check both.
    projection = None
    if len(call.args) >= 2 and isinstance(call.args[1], dict):
        projection = call.args[1]
    elif "projection" in call.kwargs:
        projection = call.kwargs["projection"]
    assert projection is not None, (
        f"could not locate the projection dict in find() call: args={call.args} kwargs={call.kwargs}"
    )
    assert projection.get("_id") == 0, f"projection must suppress _id (got {projection!r})"
    assert not any("equity_curve" in str(k) for k in projection if projection.get(k) not in (0, False)), (
        f"projection must exclude backtest.equity_curve (a large per-doc array); got {projection!r}"
    )


def test_unmocked_fetch_fails_fast_not_slow(mod, monkeypatch):
    """AC-8 / mirrors test_no_live_mongo_guard.py's community_strats analog:
    calling load_frontrunner_signals with NOTHING mocked and force_refresh=True
    (bypassing the cache) must resolve almost instantly via the session-autouse
    pymongo.MongoClient guard (tests/conftest.py) — proving no real network
    connection attempt occurs — and degrade honestly to available=False."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-never-actually-dialed/db")

    start = time.monotonic()
    result = mod.load_frontrunner_signals(force_refresh=True)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, (
        f"load_frontrunner_signals took {elapsed:.2f}s with an unmocked "
        f"pymongo.MongoClient — the session guard should make this fail in "
        f"milliseconds, not hang toward a real network timeout."
    )
    assert result["available"] is False


# ---------------------------------------------------------------------------
# AC-1: wall-clock timeout (mirrors community_strats.py's _bounded_fetch_fn)
# ---------------------------------------------------------------------------


def test_slow_fetch_returns_available_false_with_atlas_fetch_timeout_reason(mod, monkeypatch):
    """AC-1: when the bounded fetch's future times out, the call returns with
    available=False and reason='AtlasFetchTimeout' — not the raw
    concurrent.futures.TimeoutError class name."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    mock_executor_class, mock_executor, _fut = _make_timeout_executor_mock()

    with (
        patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn()),
        patch("concurrent.futures.ThreadPoolExecutor", mock_executor_class),
    ):
        result = mod.load_frontrunner_signals()

    assert result["available"] is False
    assert result.get("reason") == "AtlasFetchTimeout", (
        f"expected reason='AtlasFetchTimeout', got {result.get('reason')!r}"
    )
    mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_fast_fetch_returns_available_true_with_signals(mod, raw_atlas_docs, monkeypatch):
    """AC-1 happy path: a fast, valid Mongo response yields available=True with
    a non-empty signals list, well below the timeout bound."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    fast_mongo = _make_fast_mongo_client_mock(raw_atlas_docs)

    with (
        patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn()),
        patch("pymongo.MongoClient", return_value=fast_mongo),
    ):
        t0 = time.monotonic()
        result = mod.load_frontrunner_signals()
        elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"fast fetch must not incur timeout-wrapper overhead; elapsed={elapsed:.2f}s"
    assert result["available"] is True
    assert isinstance(result["signals"], list)
    assert len(result["signals"]) >= 1
    assert result["source"] == "captplanet"


# ---------------------------------------------------------------------------
# AC-1: D-1 never-raises + no secret leakage
# ---------------------------------------------------------------------------


def test_raising_mongo_returns_available_false_no_raise_no_secret_leak(mod, monkeypatch):
    """D-1: an auth-failure exception carrying a credential in its message must
    never raise out of load_frontrunner_signals, and no fragment of the message
    (the secret, the hostname, the URI) may appear anywhere in the result."""
    fake_uri = "mongodb+srv://admin:SUPERSECRET@cluster.mongodb.net/mydb"
    monkeypatch.setenv("MONGO_URI", fake_uri)
    exc = ConnectionError(f"Authentication failed for URI: {fake_uri}")

    with (
        patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn()),
        patch("pymongo.MongoClient", side_effect=exc),
    ):
        try:
            result = mod.load_frontrunner_signals()
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"load_frontrunner_signals raised {type(e).__name__}: {e}")

    assert result["available"] is False
    assert not _recursive_contains(result, "SUPERSECRET")
    assert not _recursive_contains(result, "cluster.mongodb.net")
    assert not _recursive_contains(result, fake_uri)


def test_atlas_cache_unavailable_when_no_timeout_fired(mod, monkeypatch):
    """D-1: when cached_pull returns None WITHOUT a timeout (e.g. a corrupt/locked
    cache with no stale row and a non-timeout fetch failure), the reason must be
    'AtlasCacheUnavailable' — distinct from the timeout reason — so an operator
    reading logs can tell the two failure modes apart."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

    with patch("advisors.atlas_cache.cached_pull", return_value=None):
        result = mod.load_frontrunner_signals()

    assert result["available"] is False
    assert result.get("reason") == "AtlasCacheUnavailable", (
        f"expected reason='AtlasCacheUnavailable' for a non-timeout None from "
        f"cached_pull; got {result.get('reason')!r}"
    )


def test_never_raises_across_multiple_seam_failure_modes(mod, monkeypatch):
    """D-1 comprehensive: every failure mode must degrade to a dict with
    available=False, never raise."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

    scenarios = [
        ("cached_pull raises", patch("advisors.atlas_cache.cached_pull", side_effect=RuntimeError("boom"))),
        ("cached_pull returns garbage", patch("advisors.atlas_cache.cached_pull", return_value=42)),
        ("cached_pull returns bad shape", patch("advisors.atlas_cache.cached_pull", return_value={"x": 1})),
    ]
    for name, patcher in scenarios:
        with patcher:
            try:
                result = mod.load_frontrunner_signals()
            except Exception as e:  # noqa: BLE001
                pytest.fail(f"scenario {name!r}: raised {type(e).__name__}: {e}")
            assert isinstance(result, dict), f"scenario {name!r}: must return a dict"
            assert result.get("available") is False, f"scenario {name!r}: must be available=False"


# ---------------------------------------------------------------------------
# AC-1: cache-hit path bypasses the live fetch entirely
# ---------------------------------------------------------------------------


def test_cache_hit_does_not_invoke_mongo_client(mod, raw_atlas_docs, monkeypatch):
    """AC-1: a fresh cache row must short-circuit the live-fetch leg entirely —
    pymongo.MongoClient must never be constructed on a cache HIT."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    call_count = {"n": 0}

    def _should_not_be_called(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("MongoClient called on a cache HIT")

    with (
        patch("advisors.atlas_cache.cached_pull", return_value=raw_atlas_docs),
        patch("pymongo.MongoClient", side_effect=_should_not_be_called),
    ):
        result = mod.load_frontrunner_signals(force_refresh=False)

    assert call_count["n"] == 0, "MongoClient must not be called on a cache HIT"
    assert result["available"] is True


def test_force_refresh_true_propagates_to_cached_pull(mod, monkeypatch):
    """AC-1: force_refresh=True must be forwarded to atlas_cache.cached_pull so a
    caller can bypass the daily TTL on demand."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    captured = {}

    def _capture(collection_name, fetch_fn, **kwargs):
        captured["force_refresh"] = kwargs.get("force_refresh")
        return []

    with patch("advisors.atlas_cache.cached_pull", side_effect=_capture):
        mod.load_frontrunner_signals(force_refresh=True)

    assert captured.get("force_refresh") is True
