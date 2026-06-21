"""RED tests — Atlas-fetch wall-clock timeout for advisors/community_strats.py.

Feature plan: feature-plans/community-strats-atlas-timeout.md

Bug: load_community_strategies hangs >50s when Atlas is slow/unreachable because
`serverSelectionTimeoutMS`/`connectTimeoutMS` do NOT bound `mongodb+srv://` SRV/TXT
DNS resolution. Fix: a `ThreadPoolExecutor`-based wall-clock wrapper (`_bounded_fetch_fn`)
with `shutdown(wait=False, cancel_futures=True)` so a hung worker does not block caller.

FETCH SEAM DESIGN (read before reading individual tests):
  `_fetch_fn` is a nested def inside `load_community_strategies` — NOT a module-level
  name. It cannot be patched with monkeypatch.setattr on the module. The correct seam
  uses two layers:

  Layer 1 — force the live-fetch leg to execute (bypass cache):
      patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn())
      After implementation, the `fn` that cached_pull receives is `_bounded_fetch_fn`.

  Layer 2 — control what the innermost call does:
      patch("pymongo.MongoClient", ...) — makes MongoClient sleep, raise, or return docs.
      `_fetch_fn` calls `pymongo.MongoClient(os.environ["MONGO_URI"], ...)`.

  AC-3 discriminator: MongoClient sleeps _BOUND * 5 (60s). Assert elapsed < _BOUND + 3s.
  If `with ThreadPoolExecutor() as ex:` is used (wrong), call blocks ~60s → assertion fails.
  Correct impl (`shutdown(wait=False)`) returns in ~_BOUND seconds → assertion passes.

D-1 CONTRACT:
  reason is always a fixed string or type(exc).__name__ — never str(exc).
  No MONGO_URI, no hostname, no credential in any returned value.

NO live network calls. No @pytest.mark.live. No production DB writes.
MONGO_URI env var is set to a fake value in each test that needs it.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sqlite3
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module under test and helpers
# ---------------------------------------------------------------------------

# The expected timeout constant value per the feature plan.
# Tests use this as the authoritative reference; after GREEN the implementation
# must define _ATLAS_FETCH_TIMEOUT_S = 12.0 (or a value that makes timing hold).
_BOUND = 12.0

# Wall-clock margin: thread start overhead on Windows CI.
# A join-on-exit hang (wrong impl) would block _BOUND * 5 = 60s.
_MARGIN_S = 3.0


def _recursive_contains(obj: Any, substring: str) -> bool:
    """Return True if any string value anywhere in obj contains substring."""
    if isinstance(obj, str):
        return substring in obj
    if isinstance(obj, dict):
        return any(_recursive_contains(v, substring) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_recursive_contains(item, substring) for item in obj)
    return False


def _make_fast_mongo_client_mock(docs: list) -> MagicMock:
    """Return a MagicMock MongoClient that immediately returns `docs` from find().

    Chain: client["captplanet"]["strategies"].find({}, projection) -> iter(docs).
    Handles both subscript (client[db][col]) and attribute (client.db.col) access.
    """
    mock_cursor = MagicMock()
    mock_cursor.__iter__ = MagicMock(return_value=iter(docs))
    # list(cursor) calls are used in _fetch_fn; make that work too
    mock_cursor.__len__ = MagicMock(return_value=len(docs))

    mock_collection = MagicMock()
    mock_collection.find.return_value = mock_cursor

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    mock_db.strategies = mock_collection

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_client.captplanet = mock_db
    return mock_client


def _make_valid_doc(sid: str = "sid-001", ticker: str = "SPY") -> dict:
    """Return a minimal valid projected Mongo doc with a valid edn_string tree.

    Uses symphony_schema constructors so the tree always passes validate_tree.
    """
    from advisors import symphony_schema as ss

    tree = ss.make_root(f"Test-{ticker}", "daily", [ss.make_weight_equal([ss.make_asset(ticker)])])
    return {
        "sid": sid,
        "name": f"Test Strategy {ticker}",
        "edn_string": json.dumps(tree),
        "oos_metrics": {"sharpe": 1.0},
    }


@pytest.fixture
def isolated_cache_db(tmp_path, monkeypatch):
    """Redirect ATLAS_CACHE_DB_PATH to a per-test temp file and init the schema."""
    db_path = str(tmp_path / "test_atlas_cache.db")
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", db_path)
    # Init the schema
    from advisors import atlas_cache as ac

    ac.init_atlas_cache()
    return db_path


def _seed_cache(db_path: str, docs: list) -> None:
    """Pre-seed the atlas cache with fresh docs so the next call is a cache HIT."""
    conn = sqlite3.connect(db_path)
    try:
        now_iso = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO atlas_cache (collection, fetched_at, payload) VALUES (?, ?, ?)",
            ("captplanet.strategies", now_iso, json.dumps(docs)),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def mod(isolated_cache_db):
    """Return the community_strats module after env isolation is in place.

    No reload: community_strats accesses its deps via module-attribute /
    call-time lookup (`atlas_cache.cached_pull(...)`, lazy `import pymongo`
    inside the fetch closure), and the in-test seams patch those attributes
    directly — so patch-visibility holds without re-executing the module.
    importlib.reload per test is a memory-leak anti-pattern (it installs a NEW
    module object while other already-imported modules keep references to the
    OLD one, leaking a module graph per test across a multi-file run).
    """
    from advisors import community_strats

    return community_strats


# ===========================================================================
# Class 1: TestHangIsBounded — AC-1 (hang bounded) + AC-3 (no join-on-exit)
# ===========================================================================


def _make_timeout_executor_mock():
    """Return a mock ThreadPoolExecutor whose future raises TimeoutError immediately.

    The mock bypasses the real ThreadPoolExecutor so no real thread sleeps, making
    the timeout path deterministic. The mock still exercises the exact same code
    branches as the real path:
      - ex.submit(_fetch_fn) → returns a mock future
      - fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S) → raises concurrent.futures.TimeoutError
      - _timeout_fired[0] = True is set in the except clause
      - _AtlasFetchTimeout is raised into cached_pull (or directly into the outer except)
      - ex.shutdown(wait=False, cancel_futures=True) is called in the finally block

    The mock asserts shutdown(wait=False) is called — a real guard: if the implementation
    switches to wait=True (the AC-3 bug), the mock records it and the assertion fires.
    """
    import concurrent.futures

    mock_future = MagicMock(spec=concurrent.futures.Future)
    mock_future.result.side_effect = concurrent.futures.TimeoutError()

    mock_executor = MagicMock()
    mock_executor.submit.return_value = mock_future
    mock_executor.__enter__ = MagicMock(return_value=mock_executor)
    mock_executor.__exit__ = MagicMock(return_value=False)

    mock_executor_class = MagicMock(return_value=mock_executor)
    return mock_executor_class, mock_executor, mock_future


class TestHangIsBounded:
    """AC-1: load_community_strategies returns within _ATLAS_FETCH_TIMEOUT_S + margin.
    AC-3: the timeout wrapper does NOT block on the hung worker thread at teardown.

    These are the decisive regression guards. A hang > _BOUND + _MARGIN_S fails AC-1.
    A hang > _BOUND * 5 specifically catches `with ThreadPoolExecutor() as ex:` (AC-3).
    """

    def test_slow_fetch_returns_available_false_with_atlas_fetch_timeout_reason(
        self, mod, monkeypatch
    ):
        """AC-1: when the ThreadPoolExecutor future times out, the call returns
        immediately with available=False and reason='AtlasFetchTimeout'.

        The deterministic approach: mock ThreadPoolExecutor so fut.result() raises
        concurrent.futures.TimeoutError synchronously (no real sleep). This forces the
        exact same code path as a real 36s DNS hang without the wall-clock fragility.

        Contract guard: the test MUST fail if _timeout_fired[0] were never set
        (verifiable by temporarily removing the flag-set line — reason would become
        'AtlasCacheUnavailable' or the class name 'TimeoutError'). The assertion on
        reason='AtlasFetchTimeout' catches that regression.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

        mock_executor_class, mock_executor, _mock_future = _make_timeout_executor_mock()

        with (
            # Force the live-fetch leg to execute (bypass cache TTL check)
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            # Replace ThreadPoolExecutor: fut.result() raises TimeoutError immediately
            patch("concurrent.futures.ThreadPoolExecutor", mock_executor_class),
        ):
            result = mod.load_community_strategies()

        assert result["available"] is False, (
            f"A timed-out fetch must return available=False; got {result!r}"
        )
        assert result.get("reason") == "AtlasFetchTimeout", (
            f"Timeout must surface as reason='AtlasFetchTimeout'; "
            f"got reason={result.get('reason')!r}. "
            "The implementation must set _timeout_fired[0]=True and map the timeout "
            "to this fixed string. If this fails, the flag-set or reason-mapping is broken."
        )
        # Guard: shutdown(wait=False) must have been called (AC-3 structural check)
        mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    @pytest.mark.perf
    def test_no_join_on_exit_hang_worker_still_sleeping(self, mod, monkeypatch):
        """AC-3: when the worker thread sleeps _BOUND * 5s, the call STILL returns
        within _BOUND + _MARGIN_S seconds — proving shutdown(wait=False) is used.

        `with ThreadPoolExecutor() as ex:` calls shutdown(wait=True) in __exit__,
        which blocks until the sleeping thread exits. With sleep=60s, that would
        make this test take ~60s and fail the timing assertion.

        Correct implementation uses explicit `ex.shutdown(wait=False, cancel_futures=True)`.
        The orphan thread is allowed to linger (it will exit when MongoClient errors).

        Marked @pytest.mark.perf: this is a wall-clock timing assertion (_BOUND * 5 = 60s
        sleep). On a loaded shared CI runner (2 vCPUs), OS scheduling jitter can push
        elapsed past _BOUND + _MARGIN_S = 15s even when the implementation is correct.
        Run opt-in: `pytest -m perf` on a dedicated machine.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

        sleep_secs = _BOUND * 5  # 60s — exaggerated to make the AC-3 bug obvious

        def _very_slow_mongo(*args, **kwargs):
            time.sleep(sleep_secs)
            return MagicMock()

        with (
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("pymongo.MongoClient", side_effect=_very_slow_mongo),
        ):
            t0 = time.monotonic()
            result = mod.load_community_strategies()
            elapsed = time.monotonic() - t0

        assert result["available"] is False, "Timed-out fetch must return available=False"
        assert elapsed < _BOUND + _MARGIN_S, (
            f"AC-3 FAIL: load_community_strategies took {elapsed:.2f}s when the worker "
            f"sleeps {sleep_secs:.0f}s. Expected < {_BOUND + _MARGIN_S:.1f}s. "
            "This means shutdown(wait=True) is blocking on the hung thread. "
            "The implementation MUST use ex.shutdown(wait=False, cancel_futures=True) — "
            "NEVER `with ThreadPoolExecutor() as ex:` which joins (wait=True) on exit."
        )


# ===========================================================================
# Class 2: TestFastPathUnchanged — AC-4
# ===========================================================================


class TestFastPathUnchanged:
    """AC-4: a fast, successful Atlas fetch still returns available=True with candidates.

    The timeout wrapper must NOT fire on a fast successful fetch. The happy path
    must be completely unaffected by the new timeout machinery.
    """

    def test_fast_fetch_returns_available_true_with_candidates(self, mod, monkeypatch):
        """AC-4: MongoClient returns valid docs immediately → available=True + candidates.

        Builds fixture docs using symphony_schema constructors so trees pass validate_tree.
        Does NOT assert specific metric values — shape and membership only.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        doc = _make_valid_doc(sid="fast-001", ticker="SPY")

        fast_mongo = _make_fast_mongo_client_mock([doc])

        with (
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("pymongo.MongoClient", return_value=fast_mongo),
        ):
            result = mod.load_community_strategies()

        assert result["available"] is True, (
            f"A fast successful fetch must return available=True; got {result!r}. "
            "The timeout wrapper must not interfere with the happy path."
        )
        assert isinstance(result["candidates"], list), (
            f"candidates must be a list on a successful fetch; got {type(result['candidates']).__name__}"
        )
        assert len(result["candidates"]) >= 1, (
            "A fast fetch returning valid docs must produce at least one candidate. "
            "The timeout machinery must not swallow valid results."
        )

    def test_fast_fetch_elapsed_is_well_below_bound(self, mod, monkeypatch):
        """AC-4: a fast MongoClient returns almost instantly — no timeout overhead.

        Wall-clock elapsed must be well below _BOUND (12s). If the wrapper introduces
        significant overhead for the fast path, this test catches it.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        doc = _make_valid_doc(sid="fast-002", ticker="QQQ")
        fast_mongo = _make_fast_mongo_client_mock([doc])

        fast_path_ceiling = 2.0  # well clear of 12s bound

        with (
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("pymongo.MongoClient", return_value=fast_mongo),
        ):
            t0 = time.monotonic()
            result = mod.load_community_strategies()
            elapsed = time.monotonic() - t0

        assert elapsed < fast_path_ceiling, (
            f"Fast fetch must return in < {fast_path_ceiling}s; elapsed={elapsed:.3f}s. "
            "The timeout wrapper must not block the fast path."
        )
        # Paired adversarial assertion: result must be meaningful, not just fast
        assert result["available"] is True, (
            f"Fast path must still return available=True; got {result['available']!r}"
        )


# ===========================================================================
# Class 3: TestNeverRaisingAndD1 — AC-5
# ===========================================================================


class TestNeverRaisingAndD1:
    """AC-5: all failure modes return available=False, never raise, no secret leakage.

    D-1 contract: reason is a fixed string or type(exc).__name__ — never str(exc).
    No MONGO_URI, no hostname, no credential appears anywhere in the return value.
    """

    def test_timeout_reason_is_fixed_string_not_class_name(self, mod, monkeypatch):
        """AC-5: the timeout reason must be exactly 'AtlasFetchTimeout' — not a Python
        exception class name like 'TimeoutError' or 'Future' or a str(exc) message.

        This asserts the implementation maps the timeout to a human-readable fixed string,
        not the class name of concurrent.futures.TimeoutError (which would be 'TimeoutError').

        Deterministic approach: mock ThreadPoolExecutor so fut.result() raises TimeoutError
        synchronously — no real sleep, no wall-clock fragility. The contract guard is the
        reason assertion: if the implementation returned type(exc).__name__, it would be
        'TimeoutError', not 'AtlasFetchTimeout', and both negative assertions would fire.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

        mock_executor_class, _mock_executor, _mock_future = _make_timeout_executor_mock()

        with (
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("concurrent.futures.ThreadPoolExecutor", mock_executor_class),
        ):
            result = mod.load_community_strategies()

        reason = result.get("reason", "")

        # Must NOT be the Python exception class name
        assert reason != "TimeoutError", (
            f"reason must not be 'TimeoutError' (the Python class name of "
            f"concurrent.futures.TimeoutError). Got reason={reason!r}. "
            "The implementation must map the timeout to the fixed string 'AtlasFetchTimeout'."
        )
        assert reason != "CancelledError", (
            f"reason must not be 'CancelledError'. Got reason={reason!r}."
        )
        # Must be the exact fixed string
        assert reason == "AtlasFetchTimeout", (
            f"reason must be exactly 'AtlasFetchTimeout'; got {reason!r}. "
            "Use a hardcoded string, not type(exc).__name__."
        )

    def test_raising_mongo_returns_available_false_no_raise_no_secret_leak(self, mod, monkeypatch):
        """AC-5: when MongoClient raises with a credential-containing message,
        load_community_strategies must:
        1. NOT raise (never-raising contract).
        2. Return available=False.
        3. NOT include any part of the exception message in the return value.

        This tests the existing D-1 path (auth errors before the timeout fires).
        """
        fake_uri = "mongodb+srv://admin:SECRETPASSWORD@cluster.mongodb.net/mydb"
        monkeypatch.setenv("MONGO_URI", fake_uri)

        # MongoClient raises immediately (auth failure, before DNS hang)
        exc = ConnectionError(f"Authentication failed for URI: {fake_uri}")

        with (
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("pymongo.MongoClient", side_effect=exc),
        ):
            try:
                result = mod.load_community_strategies()
            except Exception as e:
                pytest.fail(
                    f"load_community_strategies raised {type(e).__name__}: {e}. "
                    "Never-raising D-1 contract violated."
                )

        assert result["available"] is False, (
            f"A raising fetch must return available=False; got {result!r}"
        )
        # D-1: no secret in any string value
        assert not _recursive_contains(result, "SECRETPASSWORD"), (
            "D-1 FAIL: 'SECRETPASSWORD' appeared in the return value. "
            "reason must be type(exc).__name__ only — never str(exc)."
        )
        assert not _recursive_contains(result, "cluster.mongodb.net"), (
            "D-1 FAIL: MongoDB hostname appeared in the return value. "
            "str(exc) must not be surfaced."
        )
        assert not _recursive_contains(result, fake_uri), (
            "D-1 FAIL: MONGO_URI string appeared in the return value."
        )

    def test_malformed_fetch_result_returns_available_false(self, mod, monkeypatch):
        """AC-5: when the fetch returns a non-list (unexpected shape), the function
        returns available=False without raising.

        This exercises the existing shape-guard in load_community_strategies
        (the `if not isinstance(raw_docs, list)` check).
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

        with (
            # Return a non-list from cached_pull directly (no MongoClient needed)
            patch("advisors.atlas_cache.cached_pull", return_value={"unexpected": "shape"}),
        ):
            try:
                result = mod.load_community_strategies()
            except Exception as e:
                pytest.fail(
                    f"load_community_strategies raised {type(e).__name__}: {e} "
                    "on malformed fetch result. Never-raising contract violated."
                )

        assert result["available"] is False, (
            f"Malformed fetch result must yield available=False; got {result!r}"
        )

    def test_function_never_raises_on_any_seam_failure(self, mod, monkeypatch):
        """AC-5: comprehensive never-raising check across multiple failure modes.

        Each scenario exercises a different failure path. The function must absorb
        all of them and return a dict.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

        scenarios = [
            # cached_pull returns None (documented sentinel for "fetch failed, no stale row")
            (
                "cached_pull returns None",
                patch("advisors.atlas_cache.cached_pull", return_value=None),
            ),
            # cached_pull raises
            (
                "cached_pull raises",
                patch("advisors.atlas_cache.cached_pull", side_effect=RuntimeError("db gone")),
            ),
            # cached_pull returns garbage
            ("cached_pull returns int", patch("advisors.atlas_cache.cached_pull", return_value=42)),
        ]

        for scenario_name, patcher in scenarios:
            with patcher:
                try:
                    result = mod.load_community_strategies()
                except Exception as e:
                    pytest.fail(
                        f"load_community_strategies raised {type(e).__name__}: {e} "
                        f"in scenario '{scenario_name}'. Never-raising contract violated."
                    )
                assert isinstance(result, dict), (
                    f"Scenario '{scenario_name}': must return dict, got {type(result).__name__}"
                )
                assert "available" in result, (
                    f"Scenario '{scenario_name}': result must have 'available' key"
                )


# ===========================================================================
# Class 4: TestCachePathIntact — AC-6
# ===========================================================================


class TestCachePathIntact:
    """AC-6: the weekly-cache path is unchanged. A cache HIT never calls MongoClient.
    force_refresh=True still invokes the live-fetch leg. Constant exists and is correct.
    """

    def test_cache_hit_does_not_invoke_mongo_client(self, mod, isolated_cache_db, monkeypatch):
        """AC-6: when the cache has a fresh row, load_community_strategies must NOT
        call pymongo.MongoClient — the timeout wrapper wraps only the live-fetch leg.

        Pre-seeds the atlas cache with a fresh row. MongoClient is patched to raise
        immediately if called, so the test fails loudly if the timeout wrapper runs
        on the cache-hit path (which would be an unnecessary Atlas call).
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        doc = _make_valid_doc(sid="cache-hit-001", ticker="AGG")
        _seed_cache(isolated_cache_db, [doc])

        mongo_call_count = {"n": 0}

        def _should_not_be_called(*args, **kwargs):
            mongo_call_count["n"] += 1
            raise AssertionError(
                "MongoClient called on cache HIT — timeout wrapper ran unnecessarily"
            )

        with patch("pymongo.MongoClient", side_effect=_should_not_be_called):
            result = mod.load_community_strategies(force_refresh=False)

        assert mongo_call_count["n"] == 0, (
            f"MongoClient was called {mongo_call_count['n']} time(s) on a cache HIT. "
            "The timeout wrapper must only wrap the live-fetch leg. "
            "A cache HIT must bypass the fetch entirely."
        )
        assert result["available"] is True, (
            f"Cache-hit path must return available=True when the cached docs are valid; "
            f"got {result!r}"
        )

    def test_force_refresh_true_invokes_fetch_seam(self, mod, isolated_cache_db, monkeypatch):
        """AC-6: with force_refresh=True, MongoClient IS called even with a fresh cache row.

        This proves force_refresh propagates correctly through the new timeout wrapper
        (the wrapper is passed to cached_pull; cached_pull honours force_refresh).
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
        doc = _make_valid_doc(sid="force-001", ticker="TLT")
        _seed_cache(isolated_cache_db, [doc])

        mongo_call_count = {"n": 0}
        fast_mongo = _make_fast_mongo_client_mock([doc])

        original_mongo = fast_mongo

        def _counting_mongo(*args, **kwargs):
            mongo_call_count["n"] += 1
            return original_mongo

        with patch("pymongo.MongoClient", side_effect=_counting_mongo):
            result = mod.load_community_strategies(force_refresh=True)

        assert mongo_call_count["n"] >= 1, (
            f"MongoClient must be called when force_refresh=True, even with a fresh cache. "
            f"Called {mongo_call_count['n']} time(s). "
            "force_refresh must propagate through the timeout wrapper to cached_pull."
        )
        assert result["available"] is True, (
            f"force_refresh=True with valid docs must still return available=True; got {result!r}"
        )

    def test_atlas_fetch_timeout_constant_exists_and_is_positive_float_gt_10(self, mod):
        """AC-6 / Architecture: _ATLAS_FETCH_TIMEOUT_S must exist as a module-level
        named constant, be a positive float, and be > 10.0 (> serverSelectionTimeoutMS).

        The plan specifies 12.0. The > 10.0 check verifies the constant is set correctly
        so that a reachable-but-slow Atlas still has time to complete server selection.
        Named constant with source comment is mandatory (no magic numbers rule).
        """
        assert hasattr(mod, "_ATLAS_FETCH_TIMEOUT_S"), (
            "community_strats must define _ATLAS_FETCH_TIMEOUT_S as a module-level constant. "
            "Missing: the named constant (with source comment) is required by the architecture."
        )
        timeout_val = mod._ATLAS_FETCH_TIMEOUT_S
        assert isinstance(timeout_val, (int, float)), (
            f"_ATLAS_FETCH_TIMEOUT_S must be a numeric type; got {type(timeout_val).__name__}"
        )
        assert float(timeout_val) > 0.0, (
            f"_ATLAS_FETCH_TIMEOUT_S must be positive; got {timeout_val}"
        )
        assert float(timeout_val) > 10.0, (
            f"_ATLAS_FETCH_TIMEOUT_S must be > 10.0 (serverSelectionTimeoutMS is 10s, "
            f"so the wall-clock bound must exceed it so reachable Atlas can complete). "
            f"Got {timeout_val}."
        )


# ===========================================================================
# Class 4b: TestTimeoutReasonProductionPath — AC-1 / AC-5 (production seam)
# ===========================================================================


class TestTimeoutReasonProductionPath:
    """AC-1 / AC-5: verify reason='AtlasFetchTimeout' via the REAL cached_pull.

    The tests in TestHangIsBounded and TestNeverRaisingAndD1 mock cached_pull to
    call fn() directly — bypassing cached_pull's own exception handler. This means
    those tests do NOT prove the reason surfaces correctly when the real cached_pull
    is in use.

    cached_pull catches ALL exceptions from fetch_fn (its 'never raises' contract).
    If _bounded_fetch_fn raises _AtlasFetchTimeout and cached_pull swallows it,
    load_community_strategies receives None and returns reason='AtlasCacheUnavailable'
    — not 'AtlasFetchTimeout'.

    This class tests through the REAL cached_pull (only pymongo.MongoClient is patched)
    to prove the implementation surfaces the correct reason in production conditions.
    """

    def test_timeout_reason_is_atlas_fetch_timeout_via_real_cached_pull(
        self, mod, isolated_cache_db, monkeypatch
    ):
        """AC-1 / AC-5: via the REAL cached_pull, a timed-out fetch must return
        reason='AtlasFetchTimeout' — NOT reason='AtlasCacheUnavailable'.

        This is the PRODUCTION-PATH discriminator test. It uses the real cached_pull
        (ThreadPoolExecutor is mocked, not cached_pull) so the exception path goes through
        cached_pull's own exception handler. This proves the _timeout_fired[0] closure flag
        mechanism works correctly end-to-end:

          1. fut.result() raises concurrent.futures.TimeoutError (from mock)
          2. _timeout_fired[0] = True is set in the except clause
          3. _AtlasFetchTimeout is raised
          4. cached_pull's except handler catches _AtlasFetchTimeout and returns None
          5. load_community_strategies sees raw_docs is None
          6. _timeout_fired[0] is True → reason='AtlasFetchTimeout' (not 'AtlasCacheUnavailable')

        Contract guard: if step 2 were removed (flag never set), step 6 would produce
        reason='AtlasCacheUnavailable', and the decisive assertion below would FAIL.
        The test is a real guard, not a trivial pass.

        Deterministic approach: mock ThreadPoolExecutor so fut.result() raises TimeoutError
        synchronously — no real sleep, no wall-clock fragility, no 2-vCPU scheduling race.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")

        mock_executor_class, _mock_executor, _mock_future = _make_timeout_executor_mock()

        # Do NOT mock cached_pull — use the real implementation via isolated_cache_db
        # (no stale row seeded, so cached_pull will attempt a live fetch).
        # Mock ThreadPoolExecutor instead: fut.result() raises TimeoutError synchronously.
        with patch(
            "advisors.community_strats.concurrent.futures.ThreadPoolExecutor", mock_executor_class
        ):
            result = mod.load_community_strategies()

        assert result["available"] is False, (
            f"Timed-out fetch via real cached_pull must return available=False; got {result!r}"
        )
        # This is the decisive assertion: the reason must be 'AtlasFetchTimeout',
        # not 'AtlasCacheUnavailable' (which is what the implementation returns if
        # _timeout_fired[0] were never set when cached_pull swallows _AtlasFetchTimeout).
        assert result.get("reason") == "AtlasFetchTimeout", (
            f"Production-path FAIL: reason must be 'AtlasFetchTimeout' even through the real "
            f"cached_pull; got reason={result.get('reason')!r}. "
            "cached_pull catches all exceptions from fetch_fn (_AtlasFetchTimeout is swallowed "
            "and returns None). The _timeout_fired[0] closure flag is the only signal available "
            "to the outer scope. If this fails, the flag is not being set before the raise."
        )


# ===========================================================================
# Class 5: TestRouteDegradesTemplateOnly — AC-2
# ===========================================================================


class TestRouteDegradesTemplateOnly:
    """AC-2: POST /ai-advisor/strategy-builder/run degrades template-only when
    Atlas fetch times out — status 200, no hang, response has expected shape.

    Uses the Flask test client. MongoClient is patched to sleep > _BOUND so the
    timeout fires. propose_strategies is patched to return a minimal stub so the
    route can complete without live Composer calls.

    Note: the tests/app/conftest.py autouse fixture patches
    `advisors.community_strats.load_community_strategies` for ALL tests in tests/app/.
    This test file is in tests/advisors/ so that autouse fixture does NOT apply here.
    We control the seam manually.
    """

    @pytest.fixture
    def flask_client(self):
        """Flask test client without the tests/app autouse community stub."""
        import app as app_module

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            yield c

    def _make_minimal_proposal_run(self) -> Any:
        """Build a minimal ProposalRun-like namespace for propose_strategies mock."""
        gate = SimpleNamespace(
            results=[],
            survivors=[],
            n_candidates=0,
            fdr_q=0.05,
        )
        return SimpleNamespace(
            candidates=[],
            gated_batch=gate,
            screened_survivors=[],
            observations_written=0,
            error=None,
            rejected_candidates=[],
        )

    def test_post_strategy_builder_run_completes_within_bound_when_fetch_times_out(
        self, flask_client, monkeypatch
    ):
        """AC-2: POST /run must return 200 within _BOUND + _MARGIN_S when Atlas times out.

        This is the route-level decisive guard. A hung Atlas fetch will block the Flask
        request thread until MongoClient eventually errors (50s+). The timeout wrapper
        must prevent this.

        MongoClient sleeps _BOUND * 2 (24s) — past the timeout but shorter than a real hang.
        The route must complete in < _BOUND + _MARGIN_S seconds.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-route-test/db")

        sleep_secs = _BOUND * 2  # 24s — past timeout, shorter than real SRV hang

        def _slow_mongo(*args, **kwargs):
            time.sleep(sleep_secs)
            return MagicMock()

        mock_run = self._make_minimal_proposal_run()

        with (
            # Force the live-fetch leg (bypass cache)
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("pymongo.MongoClient", side_effect=_slow_mongo),
            patch(
                "advisors.strategy_builder_engine.propose_strategies",
                return_value=mock_run,
            ),
        ):
            t0 = time.monotonic()
            resp = flask_client.post(
                "/ai-advisor/strategy-builder/run",
                json={"objective": "diversify", "universe": ["SPY", "AGG"]},
                content_type="application/json",
            )
            elapsed = time.monotonic() - t0

        assert resp.status_code == 200, (
            f"POST /run with timed-out Atlas must return 200 (template-only degradation); "
            f"got {resp.status_code}. Status 500 = the route did not absorb the timeout."
        )
        assert elapsed < _BOUND + _MARGIN_S, (
            f"AC-2 FAIL: POST /run took {elapsed:.2f}s with Atlas timing out. "
            f"Expected < {_BOUND + _MARGIN_S:.1f}s. "
            "The route is hanging on the Atlas fetch. "
            "The wall-clock timeout wrapper must prevent this."
        )

    def test_post_run_with_timeout_response_has_expected_shape(self, flask_client, monkeypatch):
        """AC-2: template-only degradation when Atlas times out — response shape is preserved.

        When community candidates are unavailable (Atlas timeout), the route degrades
        to template-only. The response must still carry the standard JSON shape:
        {survivors, rejected, n_candidates, fdr_adjusted_threshold, error}.
        The error field must be None (degradation is not an error).
        """
        monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-route-test/db")

        def _slow_mongo(*args, **kwargs):
            time.sleep(_BOUND * 2)
            return MagicMock()

        mock_run = self._make_minimal_proposal_run()

        with (
            patch(
                "advisors.atlas_cache.cached_pull",
                side_effect=lambda col, fn, **kw: fn(),
            ),
            patch("pymongo.MongoClient", side_effect=_slow_mongo),
            patch(
                "advisors.strategy_builder_engine.propose_strategies",
                return_value=mock_run,
            ),
        ):
            resp = flask_client.post(
                "/ai-advisor/strategy-builder/run",
                json={"objective": "diversify", "universe": ["SPY", "AGG"]},
                content_type="application/json",
            )

        data = resp.get_json()
        assert data is not None, "POST /run must return valid JSON even when Atlas times out"

        required_keys = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold", "error"}
        missing = required_keys - set(data.keys())
        assert not missing, (
            f"Response missing required keys: {sorted(missing)}. "
            f"Template-only degradation must still return the full JSON shape."
        )
        assert data.get("error") is None, (
            f"Template-only degradation (Atlas timeout) must NOT set error in response. "
            f"Got error={data.get('error')!r}. "
            "An Atlas timeout is a degradation, not a route failure."
        )
        assert data.get("survivors") == [], (
            "Template-only degradation must return survivors=[] (no community enhancement)"
        )


# ---------------------------------------------------------------------------
# Anti-recurrence guard: importlib.reload-per-test is a memory-leak anti-pattern
# (it installs a NEW module object each call while other already-imported modules
# keep references to the OLD one — leaking a whole module graph per test across a
# multi-file run, which OOMs single-process full-tree verification). community_strats
# is accessed via module-attribute / call-time lookup, so the in-test seams (patching
# atlas_cache.cached_pull, pymongo.MongoClient, concurrent.futures.ThreadPoolExecutor)
# are visible without re-executing the module — no reload needed.
# ---------------------------------------------------------------------------


def test_no_importlib_reload_in_this_test_module():
    """Guard: this test module must not call importlib.reload (memory-leak anti-pattern).

    Detected via AST (a call to an attribute named 'reload'), so a docstring or
    comment mentioning the word does not trip it.
    """
    module_path = pathlib.Path(__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "reload":
                offenders.append(node.lineno)

    assert not offenders, (
        "importlib.reload(...) found in test_community_strats_timeout.py at lines "
        f"{offenders} — this is a per-test memory-leak anti-pattern. The community_strats "
        "module is accessed via module-attribute / call-time lookup; patch the relevant "
        "seam (atlas_cache.cached_pull / pymongo.MongoClient / ThreadPoolExecutor) directly."
    )
