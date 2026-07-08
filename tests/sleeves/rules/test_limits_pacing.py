"""
RED tests — sleeves/rules/limits.py: pacing state machine (AC-5, episode latch).

CONTRACT this file specifies for the GREEN implementer (s2-rules-impl):

    sleeves/rules/limits.py

    _REASON_MARKET_CLOSED = "market_closed"
    _REASON_LATCHED = "episode_latched"
    _REASON_COOLDOWN = "cooldown_active"
    _REASON_MAX_FIRES = "max_fires_per_day_reached"
    DEFAULT_REARM_TICKS = 3

    @dataclass(frozen=True)
    class PacingResult:
        fireable: bool
        reason: str | None        # one of the _REASON_* constants, or None
        episode_id: str | None    # newly minted iff fireable is True; else None

    def check_and_advance_pacing(
        *, rule_id: int, now_utc: datetime, market_open: bool, condition_true: bool,
        cooldown_sec: int | None = None, max_fires_per_day: int | None = None,
        rearm_ticks: int = DEFAULT_REARM_TICKS,
    ) -> PacingResult: ...

ALL pacing state lives in `sleeve_runtime` (AC-5 — the engine is a fresh
subprocess per minute, app.py:572-587,697), via the P1 `database.get_sleeve_runtime`/
`set_sleeve_runtime` accessors, keyed per rule_id. This matches the scheme
s2-rules-impl and s2-db converged on for migration 034: `sleeve_rule_fires`
is the durable AUDIT log the runner writes to after a permitted fire; it is
NOT this function's own pacing source — this function never reads or writes
`sleeve_rule_fires` at all. It holds NO caller-visible or module-level cache
of its own; every call re-reads whatever it needs from these five
string-valued keys:

  - "last_fire_ts"            — ISO datetime string (UTC) of the most recent
                                  permitted fire, or absent if never fired.
  - "fires_today_date"        — 'YYYY-MM-DD' (America/New_York trading day)
                                  the "fires_today_count" below was counted
                                  against; a mismatch against the CURRENT
                                  tick's trading day means the day rolled
                                  over and the count resets to 0.
  - "fires_today_count"       — string int, fires permitted on fires_today_date.
  - "episode_latched"         — the CURRENT episode_id string when an episode
                                  is open (latched); empty/absent when clear.
  - "consecutive_false_count" — string int, rearm bookkeeping.

ALGORITHM (pinned exactly; this is what the golden fixture trace below
replays tick-by-tick):

    1. market_open is False -> PacingResult(False, "market_closed", None).
       NO state is read or written at all -- a closed-market tick doesn't
       consume the rearm counter or otherwise perturb the state machine.
    2. Read all five keys above.
    3. condition_true is False:
       a. increment consecutive_false_count.
       b. if an episode IS latched (episode_latched non-empty) AND the
          incremented count >= rearm_ticks: clear the episode (episode_latched
          -> empty/absent) and reset consecutive_false_count to 0.
       c. else: persist the incremented count.
       d. return PacingResult(False, None, None) -- a legitimate "condition
          not met" is not a reason to log, it's just nothing happening.
    4. condition_true is True:
       a. reset consecutive_false_count to 0 (a true tick always breaks the
          false streak, latched or not).
       b. if an episode IS latched: return PacingResult(False, "episode_latched", None).
       c. cooldown_sec is set AND last_fire_ts is set AND
          (now_utc - parse(last_fire_ts)).total_seconds() < cooldown_sec:
          return PacingResult(False, "cooldown_active", None).
       d. compute trading_day = now_utc converted to America/New_York,
          `.date().isoformat()`. today_count = fires_today_count IF
          fires_today_date == trading_day ELSE 0 (day rolled over).
          max_fires_per_day is set AND today_count >= max_fires_per_day:
          return PacingResult(False, "max_fires_per_day_reached", None).
       e. PERMIT: mint a new episode_id, persist last_fire_ts=now_utc (ISO),
          fires_today_date=trading_day, fires_today_count=today_count + 1,
          episode_latched=new_episode_id, consecutive_false_count=0.
          return PacingResult(True, None, new_episode_id).

Callers (runner.py) are responsible for calling `database.insert_sleeve_rule_fire`
for every action of a permitted fire, passing back the SAME `episode_id` this
function minted -- limits.py itself never writes to sleeve_rule_fires.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip(
    "sleeves.rules.limits", reason="RED phase — sleeves.rules.limits not implemented yet"
)

import sleeves.rules.limits as limits  # noqa: E402

import database  # noqa: E402

from .conftest import UTC  # noqa: E402

_WORKTREE_ROOT = Path(__file__).resolve().parents[3]

_RULE_ID = 42


def _t0() -> datetime:
    # Mid-session UTC instant (14:30 UTC == 09:30-10:30 ET depending on DST;
    # exact ET wall-clock time is irrelevant here since market_open is always
    # passed explicitly — this file tests pacing logic, not the market-hours
    # gate itself, which is exercised in test_runner_shadow_and_precedence.py).
    return datetime(2026, 7, 8, 14, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. Market-hours gate — no state mutation while closed
# ---------------------------------------------------------------------------


class TestMarketClosedGate:
    def test_market_closed_blocks_with_no_state_mutation(self):
        before = database.get_all_sleeve_runtime_for_rule(_RULE_ID)
        result = limits.check_and_advance_pacing(
            rule_id=_RULE_ID,
            now_utc=_t0(),
            market_open=False,
            condition_true=True,
            cooldown_sec=60,
            max_fires_per_day=2,
            rearm_ticks=3,
        )
        assert result.fireable is False
        assert result.reason == "market_closed"
        assert result.episode_id is None
        after = database.get_all_sleeve_runtime_for_rule(_RULE_ID)
        assert after == before, "a market-closed tick must not mutate any pacing state"

    def test_market_closed_does_not_consume_a_rearm_tick(self):
        # Latch a rule, then feed a market-closed tick where the rearm counter
        # would otherwise have advanced -- it must NOT, so the rearm sequence
        # doesn't silently drain across a weekend/overnight closure.
        r1 = limits.check_and_advance_pacing(
            rule_id=_RULE_ID,
            now_utc=_t0(),
            market_open=True,
            condition_true=True,
            cooldown_sec=None,
            max_fires_per_day=None,
            rearm_ticks=3,
        )
        assert r1.fireable is True

        # A market-closed tick with condition_true=False must not count toward rearm.
        limits.check_and_advance_pacing(
            rule_id=_RULE_ID,
            now_utc=_t0() + timedelta(hours=16),
            market_open=False,
            condition_true=False,
            rearm_ticks=3,
        )
        consec = database.get_sleeve_runtime(_RULE_ID, "consecutive_false_count")
        assert consec in (None, "0"), "market-closed ticks must not advance the rearm counter"


# ---------------------------------------------------------------------------
# 2. Full trace replay against the golden fixture (AC-5's centerpiece)
# ---------------------------------------------------------------------------


class TestFullPacingTraceGoldenFixture:
    def test_main_trace_matches_fixture_across_a_simulated_module_restart(
        self, pacing_trace_fixture
    ):
        """Replays tests/fixtures/math/sleeves_rule_limits_pacing_basic.json's
        `ticks` list tick-by-tick, asserting fireable/reason at every step,
        and reloads sleeves.rules.limits (simulating a fresh subprocess import)
        at the fixture's `restart_before_tick_index` -- the trace MUST
        continue identically, proving every bit of pacing state survives a
        process boundary because it lives in the DB (sleeve_runtime), never in
        a module-level Python object."""
        import importlib

        params = pacing_trace_fixture["params"]
        restart_idx = pacing_trace_fixture["restart_before_tick_index"]
        t0 = _t0()
        current_limits = limits

        for tick in pacing_trace_fixture["ticks"]:
            if tick["index"] == restart_idx:
                current_limits = importlib.reload(current_limits)

            now = t0 + timedelta(seconds=tick["tick_offset_sec"])
            result = current_limits.check_and_advance_pacing(
                rule_id=_RULE_ID,
                now_utc=now,
                market_open=True,
                condition_true=tick["condition_true"],
                cooldown_sec=params["cooldown_sec"],
                max_fires_per_day=params["max_fires_per_day"],
                rearm_ticks=params["rearm_ticks"],
            )
            assert result.fireable == tick["expected_fireable"], (
                f"tick {tick['index']} (offset {tick['tick_offset_sec']}s): "
                f"fireable={result.fireable}, expected={tick['expected_fireable']}"
            )
            assert result.reason == tick["expected_reason"], (
                f"tick {tick['index']}: reason={result.reason!r}, "
                f"expected={tick['expected_reason']!r}"
            )

    def test_day_rollover_resets_fire_count_but_not_the_latch(self, pacing_trace_fixture):
        """Replays the fixture's `day_rollover_case` -- isolates that
        fires_today_count resets on a trading-day boundary while the episode
        latch is independent state that only clears via rearm_ticks
        consecutive false ticks."""
        case = pacing_trace_fixture["day_rollover_case"]
        params = case["params"]
        day0 = datetime(2026, 7, 8, 14, 30, 0, tzinfo=UTC)
        day1 = datetime(2026, 7, 9, 14, 30, 0, tzinfo=UTC)

        for tick in case["ticks"]:
            base_day = day0 if tick["day_offset"] == 0 else day1
            now = base_day + timedelta(seconds=tick["tick_offset_sec"])
            result = limits.check_and_advance_pacing(
                rule_id=_RULE_ID + 1,  # distinct rule_id -- isolate from other tests in this class
                now_utc=now,
                market_open=True,
                condition_true=tick["condition_true"],
                cooldown_sec=params["cooldown_sec"],
                max_fires_per_day=params["max_fires_per_day"],
                rearm_ticks=params["rearm_ticks"],
            )
            assert result.fireable == tick["expected_fireable"], f"tick {tick['index']}"
            assert result.reason == tick["expected_reason"], f"tick {tick['index']}"


# ---------------------------------------------------------------------------
# 3. Genuine cross-process persistence (the strongest possible AC-5 proof)
# ---------------------------------------------------------------------------

_SUBPROCESS_SCRIPT = """
import sys
sys.path.insert(0, {worktree_root!r})
import json
from datetime import datetime

import sleeves.rules.limits as limits

result = limits.check_and_advance_pacing(
    rule_id={rule_id!r}, now_utc=datetime.fromisoformat({now_iso!r}), market_open=True,
    condition_true={condition_true!r}, cooldown_sec={cooldown_sec!r},
    max_fires_per_day={max_fires_per_day!r}, rearm_ticks={rearm_ticks!r},
)
print(json.dumps({{"fireable": result.fireable, "reason": result.reason}}))
"""


def _run_subprocess_tick(
    *, rule_id, now, condition_true, cooldown_sec, max_fires_per_day, rearm_ticks
):
    script = _SUBPROCESS_SCRIPT.format(
        worktree_root=str(_WORKTREE_ROOT),
        rule_id=rule_id,
        now_iso=now.isoformat(),
        condition_true=condition_true,
        cooldown_sec=cooldown_sec,
        max_fires_per_day=max_fires_per_day,
        rearm_ticks=rearm_ticks,
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_WORKTREE_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"subprocess failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    import json as _json

    return _json.loads(proc.stdout.strip().splitlines()[-1])


class TestGenuineCrossProcessPersistence:
    def test_cooldown_and_latch_survive_across_real_os_process_boundaries(self):
        """Five GENUINE separate `python` subprocess invocations, each with a
        fresh interpreter and a fresh module import -- state can ONLY survive
        via the shared sqlite `sleeve_runtime` table (DB_PATH is inherited
        from the test's already-isolated environment). This is the most
        literal possible proof of AC-5's 'fresh subprocess per minute' engine
        model."""
        rule_id = 777
        t0 = _t0()

        # inv1: fires, episode starts.
        r1 = _run_subprocess_tick(
            rule_id=rule_id,
            now=t0,
            condition_true=True,
            cooldown_sec=120,
            max_fires_per_day=10,
            rearm_ticks=3,
        )
        assert r1["fireable"] is True

        # inv2-4: condition false for 3 consecutive (separate-process) ticks -> rearm.
        for i in range(1, 4):
            r = _run_subprocess_tick(
                rule_id=rule_id,
                now=t0 + timedelta(seconds=30 * i),
                condition_true=False,
                cooldown_sec=120,
                max_fires_per_day=10,
                rearm_ticks=3,
            )
            assert r["fireable"] is False

        # inv5: condition true again, latch is clear (rearmed), but still
        # within cooldown_sec of inv1's fire -- must be blocked by cooldown,
        # proving the cooldown state (last_fire_ts in sleeve_runtime, not just
        # the latch) survived every process boundary.
        r5 = _run_subprocess_tick(
            rule_id=rule_id,
            now=t0 + timedelta(seconds=100),
            condition_true=True,
            cooldown_sec=120,
            max_fires_per_day=10,
            rearm_ticks=3,
        )
        assert r5["fireable"] is False
        assert r5["reason"] == "cooldown_active", (
            f"expected cooldown_active (proving cross-process sleeve_runtime persistence), got {r5}"
        )
