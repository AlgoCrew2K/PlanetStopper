"""Account totals are fetched at daemon start, off the startup path.

Contract under test:
  - app._start_account_refresh(reason) starts _refresh_account_totals() on a daemon
    thread named account-refresh-<reason> and returns True at once; while one such
    refresh is in flight a second start starts nothing and returns False (one
    Composer call in flight, never two), and the slot frees when the refresh ends,
    even when it raises.
  - The __main__ block calls _start_account_refresh("startup") after the singleton
    check and the bot_state seed and before the scheduler thread starts.
  - No request, and no caller of _start_account_refresh, waits on the refresh, and the
    shared cache lock is never held across the Composer call.

Seams: requests.get is replaced at the network boundary (composer_total_stats in
tests/app/conftest.py serves the captured Composer body), so the real
_refresh_account_totals runs.  Expected strip values are recomputed from that body.
The seeded state holds one captured droplet symphony entry
(tests/fixtures/refresh_timing/02-c3/PROVENANCE.md).
"""

from __future__ import annotations

import ast
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import analytics as analytics_module
import app as app_module
import database

_ET = ZoneInfo("America/New_York")
_REFRESH_THREAD_PREFIX = "account-refresh-"
_SYMPHONY_ID = "iaSOOUsmnCJHiZvbrWfs"
_POLL_INTERVAL_S = 0.02
_WAIT_S = 5.0


def _start_account_refresh(reason: str) -> bool:
    start = getattr(app_module, "_start_account_refresh", None)
    if start is None:
        pytest.fail(
            "app._start_account_refresh(reason) is missing: it must start "
            "_refresh_account_totals() on a daemon thread named account-refresh-<reason> "
            "and return True at once (False when a refresh is already in flight)."
        )
    return start(reason)


def _refresh_threads() -> list[threading.Thread]:
    return [
        t
        for t in threading.enumerate()
        if t.name.startswith(_REFRESH_THREAD_PREFIX) and t.is_alive()
    ]


def _wait_until(predicate, timeout_s: float = _WAIT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return predicate()


def _wait_for_refresh_threads_to_finish() -> bool:
    return _wait_until(lambda: not _refresh_threads())


@pytest.fixture()
def live_client(monkeypatch, captured_symphony_entry):
    """Real routes over a temp database holding one captured symphony, market open."""
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")
    state = database.load_state() or {}
    state["last_successful_cycle_at"] = datetime.now(_ET).isoformat()
    state[_SYMPHONY_ID] = captured_symphony_entry
    database.save_state(state)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


def _live_strip(client) -> dict:
    resp = client.get("/api/state")
    assert resp.status_code == 200, f"/api/state must serve 200; got {resp.status_code}"
    return resp.get_json()["portfolio_strip"]


class TestStartupRefreshFillsTheAccountTotals:
    def test_first_poll_after_a_successful_startup_refresh_is_not_value_weighted(
        self, live_client, composer_total_stats
    ):
        assert _start_account_refresh("startup") is True
        assert _wait_until(lambda: "portfolio_cr" in app_module._account_totals_cache), (
            "The startup refresh must populate the account totals cache."
        )
        assert _wait_for_refresh_threads_to_finish()

        strip = _live_strip(live_client)

        expected_cr_pct = composer_total_stats.payload["simple_return"] * 100.0
        assert strip.get("basis") != "value_weighted", (
            "With Composer reachable at start the first poll must carry account-basis "
            "figures, not the value-weighted floor."
        )
        assert strip["account_all_time_cr"] == pytest.approx(expected_cr_pct, abs=1e-6), (
            "The strip's account-lifetime return must come from the startup refresh's "
            f"Composer body (simple_return x 100 = {expected_cr_pct})."
        )
        expected_url = (
            f"{app_module.COMPOSER_BASE_URL}/portfolio/accounts/"
            f"{composer_total_stats.account_id}/total-stats"
        )
        assert composer_total_stats.urls == [expected_url], (
            "The startup refresh must make exactly one request, to the configured account's "
            "total-stats route."
        )

    def test_unreachable_composer_at_start_leaves_the_value_weighted_floor(
        self, live_client, composer_total_stats
    ):
        composer_total_stats.mode = "connection_error"

        started = _start_account_refresh("startup")
        assert _wait_for_refresh_threads_to_finish()
        strip = _live_strip(live_client)

        assert started is True, "A failing Composer must not make the start call itself fail."
        assert composer_total_stats.call_count == 1
        assert strip.get("basis") == "value_weighted", (
            "With Composer unreachable and nothing cached the strip reads the value-weighted floor."
        )
        assert "account_basis_stale" not in strip
        assert getattr(app_module, "_account_totals_last_success_mono", None) is None, (
            "A refresh that failed must not stamp a success time."
        )

    def test_startup_refresh_runs_on_a_named_daemon_thread(self, composer_total_stats):
        composer_total_stats.mode = "hold"

        assert _start_account_refresh("startup") is True
        try:
            assert composer_total_stats.started.wait(timeout=_WAIT_S)
            threads = [t for t in _refresh_threads() if t.name == "account-refresh-startup"]
            assert len(threads) == 1, (
                "Expected one thread named account-refresh-startup while the Composer call "
                f"is held; found {[t.name for t in _refresh_threads()]!r}."
            )
            assert threads[0].daemon is True, (
                "The refresh thread must be a daemon so it never blocks process exit."
            )
        finally:
            composer_total_stats.release.set()
            _wait_for_refresh_threads_to_finish()


class TestStartupRefreshNeverBlocksTheCaller:
    def test_request_is_served_while_the_startup_refresh_is_held(
        self, live_client, composer_total_stats
    ):
        composer_total_stats.mode = "hold"
        call_started = time.monotonic()
        started = _start_account_refresh("startup")
        returned_after_s = time.monotonic() - call_started
        try:
            assert composer_total_stats.started.wait(timeout=_WAIT_S)

            strip = _live_strip(live_client)

            refresh_still_running = bool(_refresh_threads())
            released_early = composer_total_stats.release.is_set()
        finally:
            composer_total_stats.release.set()
            _wait_for_refresh_threads_to_finish()

        assert started is True
        assert returned_after_s < composer_total_stats.hold_timeout_s / 2, (
            f"_start_account_refresh took {returned_after_s:.2f}s while the Composer call "
            "was held: it must return at once, not run the refresh inline."
        )
        assert refresh_still_running and not released_early, (
            "The request must be served while the refresh is still in flight; it returned "
            "only after the held refresh had ended, so it waited on the refresh."
        )
        assert strip.get("basis") == "value_weighted", (
            "Served from the empty cache while the refresh is in flight: the Tier 2 floor."
        )

    def test_shared_cache_lock_is_free_while_the_composer_call_is_held(self, composer_total_stats):
        composer_total_stats.mode = "hold"

        assert _start_account_refresh("startup") is True
        try:
            assert composer_total_stats.started.wait(timeout=_WAIT_S)
            acquired = app_module._account_totals_cache_lock.acquire(timeout=1.0)
            if acquired:
                app_module._account_totals_cache_lock.release()
        finally:
            composer_total_stats.release.set()
            _wait_for_refresh_threads_to_finish()

        assert acquired, (
            "_account_totals_cache_lock must not be held across the Composer request: "
            "the timeout counter and the cache write need it from other threads."
        )


class TestOneAccountRefreshInFlight:
    def test_second_start_while_one_is_in_flight_starts_nothing(self, composer_total_stats, caplog):
        composer_total_stats.mode = "hold"
        assert _start_account_refresh("startup") is True
        try:
            assert composer_total_stats.started.wait(timeout=_WAIT_S)
            with caplog.at_level("INFO"):
                second = _start_account_refresh("minute")
        finally:
            composer_total_stats.release.set()
            _wait_for_refresh_threads_to_finish()

        assert second is False, "A refresh already in flight: the second start must return False."
        assert composer_total_stats.call_count == 1, (
            "The second start must not issue a second Composer request."
        )
        assert any(
            r.levelname == "INFO" and "refresh" in r.getMessage().lower() for r in caplog.records
        ), "The declined start must log one INFO line naming the refresh."

    def test_simultaneous_starts_admit_exactly_one_refresh(self, composer_total_stats):
        composer_total_stats.mode = "hold"
        callers = 8
        barrier = threading.Barrier(callers)
        results: list[bool] = []
        results_lock = threading.Lock()

        def _contend() -> None:
            barrier.wait(timeout=_WAIT_S)
            outcome = _start_account_refresh("minute")
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=_contend, daemon=True) for _ in range(callers)]
        for t in threads:
            t.start()
        try:
            assert composer_total_stats.started.wait(timeout=_WAIT_S)
            for t in threads:
                t.join(timeout=_WAIT_S)
        finally:
            composer_total_stats.release.set()
            _wait_for_refresh_threads_to_finish()

        assert sorted(results) == [False] * (callers - 1) + [True], (
            f"Exactly one of {callers} simultaneous starts may win; got {results!r}."
        )
        assert composer_total_stats.call_count == 1

    def test_slot_frees_when_the_refresh_ends(self, composer_total_stats):
        assert _start_account_refresh("startup") is True
        assert _wait_for_refresh_threads_to_finish()

        assert _start_account_refresh("minute") is True, (
            "After the first refresh ended, the next start must be admitted."
        )
        assert _wait_for_refresh_threads_to_finish()
        assert composer_total_stats.call_count == 2

    def test_slot_frees_when_the_refresh_raises(self, composer_total_stats, monkeypatch):
        thread_errors: list[object] = []
        monkeypatch.setattr(threading, "excepthook", thread_errors.append)

        def _explode() -> None:
            raise RuntimeError("simulated unexpected fault in the refresh")

        with monkeypatch.context() as patch_scope:
            patch_scope.setattr(app_module, "_refresh_account_totals", _explode)
            assert _start_account_refresh("startup") is True
            assert _wait_for_refresh_threads_to_finish()

        assert _start_account_refresh("minute") is True, (
            "A refresh that raised must not leave the in-flight slot taken."
        )
        assert _wait_for_refresh_threads_to_finish()
        assert composer_total_stats.call_count == 1


_MAIN_EVENT_ORDER = (
    "singleton",
    "seed",
    "startup_refresh",
    "scheduler_thread",
    "app_run",
)


def _main_block_events(source: str) -> dict[str, list[int]]:
    """Line numbers of the startup steps inside the module's `if __name__ == "__main__"` block."""
    events: dict[str, list[int]] = {}
    for stmt in _main_if_node(source).body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                if func.id == "_acquire_daemon_singleton":
                    name = "singleton"
                elif func.id == "ensure_bot_state_seeded":
                    name = "seed"
                elif func.id == "_start_model_warmup":
                    name = "model_warmup"
                elif func.id == "_start_account_refresh" and (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "startup"
                ):
                    name = "startup_refresh"
            elif isinstance(func, ast.Attribute):
                if (
                    func.attr == "run"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "app"
                ):
                    name = "app_run"
                elif (
                    func.attr == "start"
                    and isinstance(func.value, ast.Call)
                    and any(
                        kw.arg == "target"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id == "run_scheduler"
                        for kw in func.value.keywords
                    )
                ):
                    name = "scheduler_thread"
            if name:
                events.setdefault(name, []).append(node.lineno)
    return events


def _main_block_order_problems(source: str) -> list[str]:
    events = _main_block_events(source)
    problems: list[str] = []
    for name in _MAIN_EVENT_ORDER:
        count = len(events.get(name, []))
        if count != 1:
            problems.append(f"{name} must appear exactly once in the __main__ block; found {count}")
    if problems:
        return problems
    lines = [events[name][0] for name in _MAIN_EVENT_ORDER]
    for earlier, later, a, b in zip(
        _MAIN_EVENT_ORDER, _MAIN_EVENT_ORDER[1:], lines, lines[1:], strict=False
    ):
        if not a < b:
            problems.append(f"{earlier} (line {a}) must come before {later} (line {b})")
    for warmup_line in events.get("model_warmup", []):
        if not (events["singleton"][0] < warmup_line and events["seed"][0] < warmup_line):
            problems.append("the model warm-up must run after the singleton check and the seed")
    return problems


_MAIN_BLOCK_HEAD = "if __name__ == '__main__':\n    _acquire_daemon_singleton(p)\n"
_MAIN_BLOCK_TAIL = (
    "    threading.Thread(target=run_scheduler, daemon=True).start()\n    app.run(port=1)\n"
)
_MISPLACED_MAIN_BLOCKS = {
    "refresh before the seed": (
        _MAIN_BLOCK_HEAD
        + "    _start_account_refresh('startup')\n    ensure_bot_state_seeded()\n"
        + _MAIN_BLOCK_TAIL
    ),
    "refresh after the scheduler thread": (
        _MAIN_BLOCK_HEAD
        + "    ensure_bot_state_seeded()\n"
        + "    threading.Thread(target=run_scheduler, daemon=True).start()\n"
        + "    _start_account_refresh('startup')\n    app.run(port=1)\n"
    ),
    "no refresh": _MAIN_BLOCK_HEAD + "    ensure_bot_state_seeded()\n" + _MAIN_BLOCK_TAIL,
}


def _main_if_node(source: str) -> ast.If:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


class TestMainBlockStartsTheRefreshInOrder:
    def test_main_block_starts_the_account_refresh_between_the_seed_and_the_scheduler(self):
        # The scan must be able to fail: each misplaced layout is reported.
        for layout, source in _MISPLACED_MAIN_BLOCKS.items():
            assert _main_block_order_problems(source), f"the scan missed: {layout}"

        source = Path(app_module.__file__).read_text(encoding="utf-8")

        assert _main_block_order_problems(source) == []

    def test_main_block_calls_the_refresh_as_a_top_level_startup_statement(self):
        source = Path(app_module.__file__).read_text(encoding="utf-8")

        direct_calls = [
            stmt
            for stmt in _main_if_node(source).body
            if isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "_start_account_refresh"
        ]

        assert len(direct_calls) == 1, (
            "_start_account_refresh must be one direct statement of the __main__ block, "
            "not nested inside a function, branch or handler that may never run."
        )
