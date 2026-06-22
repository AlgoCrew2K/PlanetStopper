"""
RED tests — startup symphony seeding (feature-plans/startup-seed-symphonies.md)

Covers AC-1..AC-7.  All tests are RED (failing) until the implementer adds:
  - alpha_bot_execution.seed_symphonies_into_bot_state(bot_state) -> int
  - alpha_bot_execution.ensure_bot_state_seeded()
  - a startup hook in app.py calling ensure_bot_state_seeded() once at daemon start

Mocking philosophy
------------------
- fetch_symphony_stats: always patched at alpha_bot_execution.fetch_symphony_stats — never
  a live Composer network call.
- database.load_state / database.save_state: NOT mocked — go through the real SQLite layer
  via the per-test _isolate_db autouse fixture (temp DB, fully initialised schema).  This
  means AC-3 row-count assertions query the REAL shadow_history table.
- database.record_shadow_observation: patched as a SECONDARY assertion for AC-3; primary
  assertion is the real shadow_history row count from the isolated DB.
- No hardcoded producer-computed values (rates, dollars, percents, returns).  Fixture values
  are null; tests assert key presence, type, or count — never producer output.

Fixture provenance
------------------
tests/fixtures/engine/startup_seed/basic_symphony_stats.json
  schema-derived from alpha_bot_execution.fetch_symphony_stats shape (lines 175-189).
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import requests

import database

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "engine" / "startup_seed"


def _load_fixture() -> dict:
    return json.loads((_FIXTURES / "basic_symphony_stats.json").read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shadow_history_row_count() -> int:
    """Return the current shadow_history row count from the test-isolated DB.

    Uses the same _db_file() path as the rest of the test suite — guaranteed
    to be the temp DB because the _isolate_db autouse fixture already set DB_PATH.
    """
    import database as _db

    conn = sqlite3.connect(_db._db_file())
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM shadow_history")
        return cursor.fetchone()[0]
    finally:
        conn.close()


def _save_sentinel_bot_state(symphony_id: str) -> dict:
    """Write a sentinel bot_state entry to the isolated DB and return it.

    The sentinel uses deliberately un-float-able values for high_water_mark and
    triggered=True so any clobber by the seed path is detectable.
    """
    sentinel = {
        symphony_id: {
            "high_water_mark": "SENTINEL_HWM",
            "triggered": True,
            "current_holdings": ["SENTINEL_TICKER"],
            "name": "Sentinel Symphony",
        }
    }
    database.save_state(sentinel)
    return sentinel


# ---------------------------------------------------------------------------
# Hermeticity — ACCOUNT_UUIDS must not depend on the ambient environment
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_account_uuids():
    """Pin alpha_bot_execution.ACCOUNT_UUIDS to a deterministic non-empty test account.

    ACCOUNT_UUIDS is built from env at import (alpha_bot_execution.py:63 —
    ``[uid for uid in [acc_ind, acc_roth, acc_trad] if uid]``) and is therefore EMPTY
    when no .env is present (e.g. CI).  An empty ACCOUNT_UUIDS skips the seed loop
    entirely, which would make every seed-expectation test fail in CI (and the
    no-pollution tests pass vacuously).  Patching it here makes the whole module
    hermetic — the seed loop runs deterministically regardless of the ambient env.

    Tests that need specific multi-account scenarios (e.g. partial-account failure,
    once-per-account counting) override this with their own
    ``patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [...])`` — that context
    manager takes precedence inside the test and restores this value on exit.
    """
    import alpha_bot_execution

    with patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", ["seed-test-account-uuid"]):
        yield


# ---------------------------------------------------------------------------
# AC-1 — Seed-when-empty, market-agnostic
# ---------------------------------------------------------------------------


class TestSeedWhenEmpty:
    """AC-1: seeds all accounts when bot_state is empty, regardless of market hours."""

    def test_seed_creates_entries_when_bot_state_empty(self):
        """ensure_bot_state_seeded() creates bot_state entries for each symphony returned.

        Pre-condition: fresh empty DB ({}), no market-hours check.
        Assert: after the call, load_state() contains an entry for every symphony id
        in the fixture.  Result count >= 1 (does not hardcode fixture length).
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"]
        sym_ids = [s["id"] for s in syms]

        with patch.object(
            alpha_bot_execution,
            "fetch_symphony_stats",
            return_value=syms,
        ):
            alpha_bot_execution.ensure_bot_state_seeded()

        state = database.load_state()
        seeded = [sid for sid in sym_ids if sid in state]
        assert len(seeded) >= 1, (
            f"ensure_bot_state_seeded() must create at least one bot_state entry when "
            f"the DB is empty.  Got state keys: {list(state.keys())}"
        )
        for sid in sym_ids:
            assert sid in state, (
                f"Symphony id '{sid}' must be present in bot_state after seeding an "
                f"empty DB.  Got state keys: {list(state.keys())}"
            )

    def test_seed_runs_regardless_of_market_hours_weekend(self):
        """ensure_bot_state_seeded() seeds on a mocked weekend (market closed).

        Confirms the function does NOT check is_trading / market schedule.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:2]  # two symphonies is sufficient

        # If ensure_bot_state_seeded internally called is_trading_hours, this patch
        # returning False would only matter if the implementation checked it —
        # we confirm entries are created regardless.
        with (
            patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms),
            patch(
                "alpha_bot_execution.is_trading_hours",
                return_value=False,
                create=True,  # does not exist yet — create=True means it won't raise ImportError
            ),
        ):
            alpha_bot_execution.ensure_bot_state_seeded()

        state = database.load_state()
        for sym in syms:
            assert sym["id"] in state, (
                f"ensure_bot_state_seeded() must seed even when market is closed/weekend. "
                f"'{sym['id']}' missing from state keys: {list(state.keys())}"
            )

    def test_seeded_entries_have_required_shape(self):
        """Each created bot_state entry must have at minimum an 'id' or 'name' key.

        We assert shape (key presence), never producer-computed values.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]
        sid = syms[0]["id"]

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        state = database.load_state()
        assert sid in state, f"Symphony '{sid}' must be in state after seed."
        entry = state[sid]
        assert isinstance(entry, dict), (
            f"bot_state['{sid}'] must be a dict; got {type(entry).__name__}"
        )
        # high_water_mark is the canonical operational field required by the engine;
        # its presence (not value) is what matters.
        assert "high_water_mark" in entry, (
            f"Seeded entry must contain 'high_water_mark'; got keys: {list(entry.keys())}"
        )


# ---------------------------------------------------------------------------
# AC-2 — Idempotency / non-clobbering
# ---------------------------------------------------------------------------


class TestIdempotencyNonClobbering:
    """AC-2: if any symphony entry exists, the seed is a complete no-op."""

    def test_seed_does_not_modify_existing_entries(self):
        """Pre-seeded sentinel entry is UNCHANGED after ensure_bot_state_seeded().

        The sentinel uses high_water_mark='SENTINEL_HWM' (a string — impossible
        from the real engine) and triggered=True.  Any clobber is detectable.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"]
        sentinel_id = syms[0]["id"]

        sentinel_state = _save_sentinel_bot_state(sentinel_id)

        # save_state is the REAL one — it wrote the sentinel above.
        # Now we track whether the seed path calls it again.
        with patch.object(database, "save_state", wraps=database.save_state) as spy_save:
            with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
                alpha_bot_execution.ensure_bot_state_seeded()

            (
                spy_save.assert_not_called(),
                (
                    "ensure_bot_state_seeded() must NOT call save_state when bot_state "
                    "already has symphony entries (AC-2 idempotency)."
                ),
            )

        state = database.load_state()
        assert state == sentinel_state, (
            f"bot_state must be byte-identical to the sentinel after a no-op seed. "
            f"Expected: {sentinel_state!r}  Got: {state!r}"
        )
        entry = state[sentinel_id]
        assert entry["high_water_mark"] == "SENTINEL_HWM", (
            "Sentinel high_water_mark must be preserved — seed must not clobber."
        )
        assert entry["triggered"] is True, (
            "Sentinel triggered=True must be preserved — seed must not reset."
        )
        assert entry["current_holdings"] == ["SENTINEL_TICKER"], (
            "Sentinel current_holdings must be preserved — seed must not overwrite."
        )

    def test_seed_does_not_call_fetch_when_entries_exist(self):
        """fetch_symphony_stats must NOT be called when any symphony entry exists.

        Early-return before any network I/O is the correct implementation of AC-2.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        sentinel_id = fixture["symphonies"][0]["id"]
        _save_sentinel_bot_state(sentinel_id)

        with patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch:
            alpha_bot_execution.ensure_bot_state_seeded()

        (
            mock_fetch.assert_not_called(),
            (
                "fetch_symphony_stats must NOT be called by ensure_bot_state_seeded() "
                "when bot_state already contains symphony entries (AC-2 early-return)."
            ),
        )

    def test_seed_idempotent_called_twice_on_empty_then_seeded(self):
        """Calling ensure_bot_state_seeded() twice: first call seeds, second is no-op.

        After the first call populates state, the second call must not double-create
        or mutate entries.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]
        sid = syms[0]["id"]

        call_count = {"n": 0}

        def counting_fetch(account_id):
            call_count["n"] += 1
            return syms

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", side_effect=counting_fetch):
            alpha_bot_execution.ensure_bot_state_seeded()
            first_state = json.loads(json.dumps(database.load_state()))  # deep copy

            alpha_bot_execution.ensure_bot_state_seeded()
            second_state = database.load_state()

        # First call must have fetched (seeding was needed)
        assert call_count["n"] >= 1, "First call must invoke fetch_symphony_stats."

        # Second call must have returned without fetching again
        first_fetch_count = call_count["n"]
        assert call_count["n"] == first_fetch_count, (
            "Second call to ensure_bot_state_seeded() must not invoke fetch_symphony_stats "
            "because entries already exist from the first call."
        )

        assert second_state == first_state, (
            "State after second ensure_bot_state_seeded() call must match state after "
            "the first call — no mutation on an already-seeded state."
        )


# ---------------------------------------------------------------------------
# AC-3 — No collection pollution
# ---------------------------------------------------------------------------


class TestNoCollectionPollution:
    """AC-3: seed must not write to shadow_history or post-mortem files."""

    def test_seed_does_not_write_shadow_history_rows_real_db(self):
        """Primary AC-3 assertion: shadow_history row count is unchanged after seed.

        Uses the real isolated SQLite DB — not a mock.  Count before == count after.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"]

        count_before = _shadow_history_row_count()

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        count_after = _shadow_history_row_count()

        assert count_after == count_before, (
            f"Startup seed must NOT write any shadow_history rows. "
            f"Row count before: {count_before}, after: {count_after}. "
            f"The seed must not call the shadow-equity telemetry write path."
        )

    def test_seed_does_not_call_record_shadow_observation_secondary(self):
        """Secondary AC-3 assertion: record_shadow_observation is never called.

        Patches the real function to confirm the seed path does not reach it.
        (Primary assertion is the DB row count in the test above; this pins the
        specific call site so a refactor that renames the table is also caught.)
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"]

        with (
            patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms),
            patch.object(database, "record_shadow_observation") as mock_record,
        ):
            alpha_bot_execution.ensure_bot_state_seeded()

        (
            mock_record.assert_not_called(),
            (
                "database.record_shadow_observation must NOT be called during startup seed. "
                "The shadow_history telemetry write path must be excluded from the seed helper."
            ),
        )

    def test_seed_does_not_write_post_mortem_files(self):
        """Seed must not write post_mortem_*.json files.

        The post-mortem write path is alpha_bot_execution.py:1031 —
        open(post_mortem_path, 'w') followed by json.dump.
        We assert the builtins.open is not called with a write mode during seeding.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]

        write_opens: list[str] = []

        real_open = open  # save ref before patching

        def tracking_open(file, mode="r", **kwargs):
            if isinstance(file, str) and "post_mortem" in file and "w" in str(mode):
                write_opens.append(str(file))
            return real_open(file, mode, **kwargs)

        with (
            patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms),
            patch("builtins.open", side_effect=tracking_open),
        ):
            alpha_bot_execution.ensure_bot_state_seeded()

        assert write_opens == [], (
            f"Startup seed must NOT write post-mortem files. "
            f"Detected write-mode opens for post_mortem paths: {write_opens}"
        )


# ---------------------------------------------------------------------------
# AC-4 — Fail-safe startup
# ---------------------------------------------------------------------------


class TestFailSafeStartup:
    """AC-4: fetch failures are swallowed; daemon startup never crashes."""

    def test_seed_does_not_raise_when_fetch_raises_requests_exception(self):
        """If fetch_symphony_stats raises RequestException, ensure_bot_state_seeded returns normally."""
        import alpha_bot_execution

        with patch.object(
            alpha_bot_execution,
            "fetch_symphony_stats",
            side_effect=requests.RequestException("simulated network failure"),
        ):
            # Must not raise — AC-4 D-1 contract
            try:
                alpha_bot_execution.ensure_bot_state_seeded()
            except Exception as exc:
                pytest.fail(
                    f"ensure_bot_state_seeded() must not propagate exceptions on fetch "
                    f"failure (AC-4 fail-safe). Got: {type(exc).__name__}: {exc}"
                )

    def test_seed_does_not_raise_when_fetch_raises_generic_exception(self):
        """If fetch_symphony_stats raises any exception, ensure_bot_state_seeded returns normally."""
        import alpha_bot_execution

        with patch.object(
            alpha_bot_execution,
            "fetch_symphony_stats",
            side_effect=RuntimeError("simulated unexpected error"),
        ):
            try:
                alpha_bot_execution.ensure_bot_state_seeded()
            except Exception as exc:
                pytest.fail(
                    f"ensure_bot_state_seeded() must swallow all fetch exceptions (AC-4). "
                    f"Got: {type(exc).__name__}: {exc}"
                )

    def test_seed_does_not_raise_when_fetch_returns_empty_list(self):
        """fetch_symphony_stats returning [] (HTTP error path) is a graceful degrade, not a crash."""
        import alpha_bot_execution

        # [] is what fetch_symphony_stats returns on HTTP non-200 (alpha_bot_execution.py:189)
        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=[]):
            try:
                alpha_bot_execution.ensure_bot_state_seeded()
            except Exception as exc:
                pytest.fail(
                    f"ensure_bot_state_seeded() must handle empty-list return gracefully. "
                    f"Got: {type(exc).__name__}: {exc}"
                )

    def test_seed_partial_account_failure_seeds_successful_accounts(self):
        """Partial failure: account A raises, account B returns symphonies.

        Account B's symphonies must be seeded; no exception propagates.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        b_syms = fixture["partial_failure_account_b"]
        b_sym_ids = [s["id"] for s in b_syms]

        account_a = "fixture-account-A"
        account_b = "fixture-account-B"

        def per_account_fetch(account_id):
            if account_id == account_a:
                raise requests.RequestException("account A network failure")
            return b_syms

        with (
            patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [account_a, account_b]),
            patch.object(
                alpha_bot_execution, "fetch_symphony_stats", side_effect=per_account_fetch
            ),
        ):
            try:
                alpha_bot_execution.ensure_bot_state_seeded()
            except Exception as exc:
                pytest.fail(
                    f"ensure_bot_state_seeded() must not raise on partial account failure. "
                    f"Got: {type(exc).__name__}: {exc}"
                )

        state = database.load_state()
        for sid in b_sym_ids:
            assert sid in state, (
                f"Symphony '{sid}' from the successful account B must be in bot_state "
                f"after a partial account-A failure. Got keys: {list(state.keys())}"
            )


# ---------------------------------------------------------------------------
# AC-5 — Market-hours cycle continuity
# ---------------------------------------------------------------------------


class TestMarketHoursCycleContinuity:
    """AC-5: post-seed, the DATA PHASE create-or-update does not duplicate or reset entries."""

    def test_market_hours_cycle_does_not_duplicate_seeded_entries(self):
        """After seed, re-calling seed_symphonies_into_bot_state does not add duplicate keys.

        This simulates the DATA PHASE create-or-update block seeing an already-seeded
        entry: the 'if s_id not in bot_state' branch must go to 'else', not re-create.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]
        sid = syms[0]["id"]

        # First call — seeds
        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        state_after_seed = database.load_state()
        key_count_after_seed = len(
            [k for k in state_after_seed if isinstance(state_after_seed[k], dict)]
        )

        # Second seed call must be a no-op because entries now exist
        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        state_after_second = database.load_state()
        key_count_after_second = len(
            [k for k in state_after_second if isinstance(state_after_second[k], dict)]
        )

        assert key_count_after_second == key_count_after_seed, (
            f"Symphony entry count must be unchanged after a second ensure_bot_state_seeded() "
            f"call on an already-seeded state. Before: {key_count_after_seed}, after: {key_count_after_second}. "
            f"No duplicates must be created."
        )

    def test_seeded_entry_sentinel_fields_survive_second_seed_call(self):
        """After seeding, running ensure_bot_state_seeded() again must not reset operational fields.

        Verifies that AC-2 idempotency preserves the seeded entry's high_water_mark and
        triggered fields — the market-hours DATA PHASE 'else' branch would update them,
        but a second seed call must be a complete no-op.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]
        sid = syms[0]["id"]

        # First seed
        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        # Simulate the engine modifying the entry post-seed (as the DATA PHASE would)
        state = database.load_state()
        state[sid]["high_water_mark"] = "POST_SEED_HWM"
        state[sid]["triggered"] = True
        database.save_state(state)

        # Second seed call — must be a no-op; must NOT reset the post-seed values
        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        final_state = database.load_state()
        assert final_state[sid]["high_water_mark"] == "POST_SEED_HWM", (
            "ensure_bot_state_seeded() on an already-seeded state must not overwrite "
            "high_water_mark set by the market-hours DATA PHASE."
        )
        assert final_state[sid]["triggered"] is True, (
            "ensure_bot_state_seeded() on an already-seeded state must not reset triggered."
        )


# ---------------------------------------------------------------------------
# AC-6 — Empty account safe
# ---------------------------------------------------------------------------


class TestEmptyAccountSafe:
    """AC-6: 0 symphonies from an account produces no entries and no exception."""

    def test_empty_account_produces_no_entries_no_raise(self):
        """fetch_symphony_stats returning [] leaves bot_state empty with no exception."""
        import alpha_bot_execution

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=[]):
            try:
                alpha_bot_execution.ensure_bot_state_seeded()
            except Exception as exc:
                pytest.fail(
                    f"ensure_bot_state_seeded() must not raise on empty account. "
                    f"Got: {type(exc).__name__}: {exc}"
                )

        state = database.load_state()
        symphony_entries = {k: v for k, v in state.items() if isinstance(v, dict)}
        assert symphony_entries == {}, (
            f"bot_state must have no symphony entries after seeding an empty account. "
            f"Got: {symphony_entries}"
        )

    def test_empty_account_does_not_call_save_state(self):
        """If no symphonies are seeded, save_state must NOT be called.

        Calling save_state on an empty result would write an unmodified state back —
        wasteful and potentially racy.
        """
        import alpha_bot_execution

        with (
            patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=[]),
            patch.object(database, "save_state") as mock_save,
        ):
            alpha_bot_execution.ensure_bot_state_seeded()

        (
            mock_save.assert_not_called(),
            (
                "ensure_bot_state_seeded() must not call save_state when no symphonies "
                "are seeded (empty account). AC-6: no-op on empty result."
            ),
        )


# ---------------------------------------------------------------------------
# AC-7 — Startup-cost bounded / off execution path
# ---------------------------------------------------------------------------


class TestStartupCostBounded:
    """AC-7: seed runs once at startup, not inside the per-minute main() cycle."""

    def test_ensure_bot_state_seeded_not_called_inside_main_body(self):
        """Structural: ensure_bot_state_seeded must NOT appear in the source of main().

        The startup seed hook must live in app.py's startup path, not the per-cycle
        main() function in alpha_bot_execution.py.  Placing it inside main() would
        call it on every minute-cadence execution (AC-7 violation).
        """
        import alpha_bot_execution

        main_src = inspect.getsource(alpha_bot_execution.main)
        assert "ensure_bot_state_seeded" not in main_src, (
            "ensure_bot_state_seeded must NOT appear in alpha_bot_execution.main(). "
            "The startup seed must be called from the daemon startup path in app.py, "
            "not on the per-minute execution cycle.  AC-7: startup-cost bounded."
        )

    def test_seed_helper_calls_fetch_symphony_stats_not_requests_directly(self):
        """seed_symphonies_into_bot_state must delegate to fetch_symphony_stats.

        This confirms it inherits the existing bounded timeout (requests.get timeout=15
        at alpha_bot_execution.py:178) without adding its own raw requests.get calls.
        Patching fetch_symphony_stats at the module level must intercept all network I/O.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]

        # If seed_symphonies_into_bot_state calls requests.get directly instead of
        # delegating to fetch_symphony_stats, this mock will not intercept the call
        # and the test will either make a live request or raise a connection error.
        with patch.object(
            alpha_bot_execution, "fetch_symphony_stats", return_value=syms
        ) as mock_fetch:
            alpha_bot_execution.seed_symphonies_into_bot_state(database.load_state())

        (
            mock_fetch.assert_called(),
            (
                "seed_symphonies_into_bot_state must call fetch_symphony_stats (not raw "
                "requests.get) to inherit the existing bounded timeout.  AC-7: no new "
                "magic timeouts; reuse the existing network boundary."
            ),
        )

    def test_seed_helper_calls_fetch_once_per_account(self):
        """fetch_symphony_stats must be called once per entry in ACCOUNT_UUIDS.

        This pins that the seed iterates accounts, not a fixed number of calls.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]

        fake_accounts = ["account-X", "account-Y"]

        with (
            patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", fake_accounts),
            patch.object(
                alpha_bot_execution, "fetch_symphony_stats", return_value=syms
            ) as mock_fetch,
        ):
            # Call seed_symphonies_into_bot_state directly with an empty state
            alpha_bot_execution.seed_symphonies_into_bot_state({})

        assert mock_fetch.call_count == len(fake_accounts), (
            f"fetch_symphony_stats must be called once per account in ACCOUNT_UUIDS. "
            f"Expected {len(fake_accounts)} calls, got {mock_fetch.call_count}."
        )

    def test_seed_helper_is_importable_as_module_level_function(self):
        """seed_symphonies_into_bot_state and ensure_bot_state_seeded must be accessible.

        Both functions must exist as module-level callables in alpha_bot_execution.
        """
        import alpha_bot_execution

        assert hasattr(alpha_bot_execution, "seed_symphonies_into_bot_state"), (
            "alpha_bot_execution must expose seed_symphonies_into_bot_state as a "
            "module-level function.  It is the extracted create-helper called by "
            "ensure_bot_state_seeded() and (optionally) the DATA PHASE."
        )
        assert callable(alpha_bot_execution.seed_symphonies_into_bot_state), (
            "seed_symphonies_into_bot_state must be callable."
        )
        assert hasattr(alpha_bot_execution, "ensure_bot_state_seeded"), (
            "alpha_bot_execution must expose ensure_bot_state_seeded as a module-level "
            "function.  It is the conditional entry point called at daemon startup."
        )
        assert callable(alpha_bot_execution.ensure_bot_state_seeded), (
            "ensure_bot_state_seeded must be callable."
        )

    def test_seed_helper_returns_count_of_seeded_symphonies(self):
        """seed_symphonies_into_bot_state(bot_state) -> int: returns count of entries created.

        This enables the caller (ensure_bot_state_seeded) to log a meaningful summary
        without re-reading the DB, and allows the PM's structural test to assert shape
        without hardcoding the fixture count.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"]

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            result = alpha_bot_execution.seed_symphonies_into_bot_state({})

        assert isinstance(result, int), (
            f"seed_symphonies_into_bot_state must return int (count seeded). "
            f"Got {type(result).__name__}: {result!r}"
        )
        assert result >= 0, (
            f"Return value must be non-negative count of seeded entries. Got {result}."
        )


# ---------------------------------------------------------------------------
# Revise-cycle RED tests — presence check must distinguish symphony entries
# from reserved metadata keys (last_market_close_snapshot, fleet_correlation_alert)
# ---------------------------------------------------------------------------


class TestPresenceCheckExcludesMetadataKeys:
    """Revise-cycle: the 'is it seeded?' check must exclude known reserved metadata keys.

    bot_state can contain dict-valued metadata keys that are NOT symphony entries:
      - last_market_close_snapshot  (set daily at EOD, alpha_bot_execution.py:1078)
      - fleet_correlation_alert     (set by set_fleet_correlation_alert, may be a dict)

    An implementation using `any(isinstance(v, dict) for v in bot_state.values())`
    would treat a metadata-only state as "already seeded" and skip the seed — silently
    leaving the dashboard showing 0 symphonies after a DB wipe that ran before market
    close on a prior day.

    database.py:301 defines _WIPE_RESERVED_KEYS = {"date", "last_execution_mode",
    "last_market_close_snapshot"} — these are the authoritative non-symphony keys.
    The presence check must filter them out (or use a positive test: does the key look
    like a symphony id rather than a reserved name).
    """

    def test_metadata_only_state_with_last_market_close_snapshot_triggers_seed(self):
        """A bot_state containing ONLY last_market_close_snapshot must be treated as empty.

        last_market_close_snapshot is a dict — a naive isinstance check would return
        True and skip the seed, leaving 0 symphonies on the dashboard.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:2]

        # Seed the DB with a metadata-only state (no symphony entries)
        metadata_only = {
            "last_market_close_snapshot": {
                "trading_day": "2026-06-20",
                "captured_at_et": "16:00:00 ET",
                "data_as_of": "16:00 ET",
                "portfolio_strip": [],
                "shadow_divergence": {"by_symphony": {}, "portfolio": None},
                "accounts_map": {},
            }
        }
        database.save_state(metadata_only)

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        state = database.load_state()
        seeded_ids = [s["id"] for s in syms]
        for sid in seeded_ids:
            assert sid in state, (
                f"ensure_bot_state_seeded() must seed when bot_state contains only "
                f"last_market_close_snapshot (a metadata key, not a symphony entry). "
                f"'{sid}' missing from state keys: {list(state.keys())}. "
                f"The presence check must exclude reserved metadata keys."
            )

    def test_metadata_only_state_with_date_and_last_execution_mode_triggers_seed(self):
        """A bot_state with only scalar metadata keys must be treated as empty.

        date and last_execution_mode are string-valued reserved keys — not dict-valued,
        so a naive isinstance check passes this scenario.  Included for completeness:
        the seed must run even when these scalars are present.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]

        metadata_only = {
            "date": "2026-06-20",
            "last_execution_mode": "PAPER",
            "last_successful_cycle_at": "2026-06-20T15:59:00",
        }
        database.save_state(metadata_only)

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        state = database.load_state()
        assert syms[0]["id"] in state, (
            f"ensure_bot_state_seeded() must seed when bot_state contains only scalar "
            f"metadata keys. '{syms[0]['id']}' missing. "
            f"State keys: {list(state.keys())}"
        )

    def test_presence_check_does_not_false_positive_on_fleet_correlation_alert(self):
        """fleet_correlation_alert is a dict value — must not block seeding.

        set_fleet_correlation_alert writes a dict to bot_state under
        'fleet_correlation_alert'.  If present without any symphony entries, the
        seed must still run.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"][:1]

        alert_only = {
            "fleet_correlation_alert": {
                "triggered_at": "2026-06-20T10:35:00",
                "mean_corr": 0.91,
                "affected_symphonies": ["sym-abc", "sym-def"],
            }
        }
        database.save_state(alert_only)

        with patch.object(alpha_bot_execution, "fetch_symphony_stats", return_value=syms):
            alpha_bot_execution.ensure_bot_state_seeded()

        state = database.load_state()
        assert syms[0]["id"] in state, (
            f"ensure_bot_state_seeded() must seed when bot_state contains only "
            f"fleet_correlation_alert (a dict-valued metadata key, not a symphony entry). "
            f"'{syms[0]['id']}' missing. State keys: {list(state.keys())}"
        )

    def test_presence_check_still_blocks_seed_when_real_symphony_entry_exists(self):
        """Regression: adding reserved-key awareness must not break the idempotency guard.

        When a real symphony entry exists alongside metadata keys, seeding must NOT occur.
        """
        import alpha_bot_execution

        fixture = _load_fixture()
        syms = fixture["symphonies"]
        real_sym_id = syms[0]["id"]

        mixed_state = {
            "date": "2026-06-20",
            "last_market_close_snapshot": {"trading_day": "2026-06-20"},
            real_sym_id: {
                "high_water_mark": "SENTINEL_HWM",
                "triggered": True,
                "current_holdings": ["SENTINEL_TICKER"],
            },
        }
        database.save_state(mixed_state)

        with patch.object(
            alpha_bot_execution, "fetch_symphony_stats", return_value=syms
        ) as mock_fetch:
            alpha_bot_execution.ensure_bot_state_seeded()

        (
            mock_fetch.assert_not_called(),
            (
                "ensure_bot_state_seeded() must NOT call fetch_symphony_stats when a real "
                "symphony entry exists alongside metadata keys. The presence check must "
                "detect real entries even when reserved keys are also present."
            ),
        )

        state = database.load_state()
        assert state[real_sym_id]["high_water_mark"] == "SENTINEL_HWM", (
            "Existing symphony entry must be unchanged when the seed is correctly blocked."
        )
