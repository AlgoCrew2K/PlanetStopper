"""RED — /api/history/<days> today-fallback must be date-filtered + consumer-shaped.

THE BUG (VERDICT-droplet.md Finding 3, HIGH — on the operator's screen every
trading morning until the 15:54 post-mortem write):
  app.py get_history backfills ``todays_exits`` whenever it is empty — which is
  every trading day before 15:54 ET, not just "day-1 droplet" as its comment
  claims. The backfill SELECT has NO date filter (``ORDER BY ts_utc DESC LIMIT
  50``), so 50 ALL-TIME triggers render as "Today's exits". It then clobbers the
  windowed ``trigger_count`` with the feed length (76 -> 50), making the headline
  stats internally inconsistent. And the fallback rows carry
  ``ts_utc/at_return/triggered_reason`` while the consumer (static/history.js
  renderExits) reads ``rec.ts / rec.reason / rec.detail / rec.symphony_name`` —
  Time/Reason/Detail all render em-dashes and Symphony renders the raw hash id.

CONTRACT PINNED:
  1. The fallback returns ONLY exits from the current ET trading day.
  2. A day with zero exits yields an empty todays_exits — never the all-time feed.
  3. The windowed trigger_count is NEVER overwritten by the fallback feed length.
  4. Fallback rows carry the exact consumed shape: ts (truthy), symphony_name
     (the human name, not the hash), reason (truthy), detail (numeric — the
     row's at_return, renderable as a percent).
  5. (Finding 11) The POST-MORTEM path's Time column works too: the producer
     writes ``time_triggered`` but the aggregator reads ``timestamp``/``ts``
     (analytics.py:1749), so ts is ALWAYS empty even after the 15:54 write —
     today's exits must carry the entry's trigger time on both paths.

FIXTURE PROVENANCE (captured-from-producer):
  tests/fixtures/prod_droplet/ — the real production exit_triggers table (87 rows,
  2026-06-22..2026-07-09) + the 12 real post-mortem files + real symphony names,
  captured at deployed SHA 0bcbd1a (see capture_from_droplet.py). Because the
  route's "today" is the wall clock, row timestamps are shifted by a whole number
  of days so the newest production day lands on the test-run's ET today; every
  value field (symphony_id, at_return, reason, account) is untouched real data.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import analytics
import app as app_module
import database

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "prod_droplet"
_ET = ZoneInfo("America/New_York")

# Window wide enough that every (historic) fixture post-mortem file stays in
# range regardless of when the test runs.
_WIDE_WINDOW_DAYS = 3650


@pytest.fixture(scope="module")
def exit_trigger_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "exit_triggers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def positions() -> dict:
    return json.loads((_FIXTURE_DIR / "bot_state_positions.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def pm_dir(tmp_path, monkeypatch) -> Path:
    """Real production post-mortem files, served from a temp dir."""
    pm = tmp_path / "post_mortems"
    pm.mkdir()
    for src in (_FIXTURE_DIR / "post_mortems").glob("post_mortem_*.json"):
        shutil.copy(src, pm)
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))
    return pm


def _windowed_trigger_count(pm_dir: Path) -> int:
    """The true windowed count — derived by summing the real files' triggers."""
    total = 0
    for f in pm_dir.glob("post_mortem_*.json"):
        total += len(json.loads(f.read_text(encoding="utf-8")).get("triggers", []))
    return total


def _shift_and_seed_exit_triggers(rows: list[dict], newest_lands_on: date) -> list[dict]:
    """Seed the real exit_triggers rows with dates shifted so the newest
    production day lands on ``newest_lands_on``. Whole-day shift only — times,
    returns, reasons, ids are the captured production values.
    """
    newest_prod_day = max(r["ts_et"][:10] for r in rows)
    shift = newest_lands_on - date.fromisoformat(newest_prod_day)
    seeded = []
    for r in rows:
        ts_et = datetime.strptime(r["ts_et"], "%Y-%m-%dT%H:%M:%S") + shift
        ts_utc = datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ") + shift
        shifted = dict(
            r,
            ts_et=ts_et.strftime("%Y-%m-%dT%H:%M:%S"),
            ts_utc=ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        database.record_exit_trigger(
            symphony_id=shifted["symphony_id"],
            account_id=shifted["account_id"],
            triggered_reason=shifted["triggered_reason"],
            at_return=shifted["at_return"],
            cycle_id=shifted["cycle_id"],
            ts_utc=shifted["ts_utc"],
            ts_et=shifted["ts_et"],
        )
        seeded.append(shifted)
    return seeded


def _seed_symphony_names(positions: dict) -> None:
    """bot_state with the real symphony names — the name-map source."""
    database.save_state(
        {
            sym_id: {"name": entry["name"], "account": entry.get("account")}
            for sym_id, entry in positions.items()
        }
    )


def _seed_shadow_history_for_detail(positions: dict) -> dict[str, float]:
    """One shadow_history row per symphony, dated TODAY, carrying the
    captured production `current_return` snapshot (tests/fixtures/prod_droplet
    /bot_state_positions.json — real data, not fabricated).

    math-r0 AC-6 (MAPERF-06): the History Detail column emits
    at_return - current_return (guard-alpha pp), sourced from the LATEST
    shadow_history row per symphony (`ORDER BY ts_utc DESC LIMIT 1` —
    app.py's intraday backfill query). Seeding exactly one row per symphony
    makes that LATEST lookup deterministic (every today's exit for a given
    symphony resolves to the SAME current_return), so the expected detail
    value for every row is independently re-derivable here, not hardcoded.

    Returns {symphony_id: current_return} for callers to compute expected
    detail values.
    """
    current_returns: dict[str, float] = {}
    today = _today_et().isoformat()
    for i, (sym_id, entry) in enumerate(positions.items()):
        cr = entry.get("current_return")
        if cr is None:
            continue
        current_returns[sym_id] = float(cr)
        database.record_shadow_observation(
            symphony_id=sym_id,
            account_id=entry.get("account"),
            cycle_id=f"prod-fixture-shadow-{i}",
            ts_utc=f"{today}T20:00:00Z",
            ts_et=f"{today}T16:00:00",
            trading_day=today,
            current_return=cr,
            shadow_return=cr,
            is_post_trigger=0,
            trigger_id=None,
        )
    return current_returns


def _today_et() -> date:
    return datetime.now(_ET).date()


# ---------------------------------------------------------------------------
# 1 + 2: date filtering
# ---------------------------------------------------------------------------


class TestTodayFallbackIsDateFiltered:
    def test_fallback_returns_only_current_trading_day_exits(
        self, client, pm_dir, exit_trigger_rows, positions
    ):
        """With the production table re-based so its newest day is today, the
        route must return exactly today's exits — not the 50-row all-time feed.

        RED today: the fallback SELECT has no date filter and returns LIMIT 50
        across all days.

        math-r0 AC-6 (MAPERF-06): detail is now at_return - current_return
        (guard-alpha pp), sourced from the LATEST shadow_history row per
        symphony — not the raw at_return. We seed one shadow_history row per
        symphony (real captured current_return snapshots,
        _seed_shadow_history_for_detail) so detail is computable, and derive
        the expected per-row value from that same source.
        """
        _seed_symphony_names(positions)
        current_returns = _seed_shadow_history_for_detail(positions)
        seeded = _shift_and_seed_exit_triggers(exit_trigger_rows, _today_et())
        todays_rows = [r for r in seeded if r["ts_et"][:10] == _today_et().isoformat()]
        assert todays_rows, "fixture integrity: newest production day must carry exits"
        assert len(todays_rows) < len(seeded), (
            "fixture integrity: multi-day table required to prove the filter"
        )

        resp = client.get(f"/api/history/{_WIDE_WINDOW_DAYS}")
        assert resp.status_code == 200
        exits = resp.get_json()["todays_exits"]

        assert len(exits) == len(todays_rows), (
            f"todays_exits returned {len(exits)} rows; the current ET trading day has "
            f"{len(todays_rows)} real exits — the all-time feed is leaking into 'Today'"
        )
        # The rows must BE today's rows: the per-row guard-alpha-pp detail values
        # must match the multiset of today's captured
        # (at_return - current_return) values (order-independent).
        expected_details = sorted(
            round(r["at_return"] - current_returns[r["symphony_id"]], 4)
            for r in todays_rows
            if r["symphony_id"] in current_returns
        )
        got_details = sorted(
            round(float(e["detail"]), 4) for e in exits if isinstance(e.get("detail"), (int, float))
        )
        assert got_details == expected_details, "returned exit rows are not today's production rows"

    def test_zero_exit_day_yields_empty_todays_exits_not_alltime_feed(
        self, client, pm_dir, exit_trigger_rows, positions
    ):
        """Re-base the table so the newest exit was YESTERDAY: today has zero
        exits, and 'Today's exits' must be empty — the honest empty state, not 50
        historical rows with blank cells.
        """
        _seed_symphony_names(positions)
        _shift_and_seed_exit_triggers(exit_trigger_rows, _today_et() - timedelta(days=1))

        resp = client.get(f"/api/history/{_WIDE_WINDOW_DAYS}")
        assert resp.status_code == 200
        exits = resp.get_json()["todays_exits"]

        assert exits == [], (
            f"a zero-exit day must render an empty 'Today's exits'; got {len(exits)} "
            "historical rows presented as today's"
        )


# ---------------------------------------------------------------------------
# 3: the windowed headline count is not clobbered
# ---------------------------------------------------------------------------


class TestWindowedTriggerCountNotClobbered:
    def test_trigger_count_stays_the_post_mortem_window_count(
        self, client, pm_dir, exit_trigger_rows, positions
    ):
        """trigger_count is the windowed post-mortem aggregate (total_saved and
        win_rate derive from the same 76 entries). The fallback must not overwrite
        it with the feed length — that made the headline internally inconsistent
        (count said 50, dollars summed 76 entries).
        """
        _seed_symphony_names(positions)
        _shift_and_seed_exit_triggers(exit_trigger_rows, _today_et())
        expected_count = _windowed_trigger_count(pm_dir)
        assert expected_count > 0, "fixture integrity: production files carry triggers"

        resp = client.get(f"/api/history/{_WIDE_WINDOW_DAYS}")
        assert resp.status_code == 200
        body = resp.get_json()

        assert body["trigger_count"] == expected_count, (
            f"trigger_count={body['trigger_count']} was clobbered by the fallback "
            f"feed; the windowed post-mortem count derived from the real files is "
            f"{expected_count}"
        )


# ---------------------------------------------------------------------------
# 4: rows carry the shape history.js actually consumes
# ---------------------------------------------------------------------------


class TestFallbackRowsMatchConsumedShape:
    def test_fallback_rows_carry_ts_name_reason_detail(
        self, client, pm_dir, exit_trigger_rows, positions
    ):
        """static/history.js renderExits reads rec.ts / rec.symphony_name /
        rec.reason / rec.detail. The fallback rows must supply that shape —
        today they carry ts_utc/at_return/triggered_reason, so Time/Reason/Detail
        all render em-dashes and Symphony shows the raw hash.

        Shape/format assertions only; the one value equality (symphony_name) is
        against the captured production name for that id.

        math-r0 AC-6: detail requires a shadow_history current_return lookup
        to compute the guard-alpha-pp value — seed one row per symphony
        (_seed_shadow_history_for_detail) so detail is honestly numeric.
        """
        _seed_symphony_names(positions)
        _seed_shadow_history_for_detail(positions)
        _shift_and_seed_exit_triggers(exit_trigger_rows, _today_et())

        resp = client.get(f"/api/history/{_WIDE_WINDOW_DAYS}")
        assert resp.status_code == 200
        exits = resp.get_json()["todays_exits"]
        assert exits, "expected today's exits present (covered by the filter test)"

        known_names = {entry["name"] for entry in positions.values()}
        for rec in exits:
            missing = {"ts", "symphony_name", "reason", "detail"} - set(rec)
            assert not missing, f"fallback row missing consumed keys {missing}: {rec}"
            assert rec["ts"], "ts must be renderable (history.js Time column)"
            assert rec["reason"], "reason must be renderable (history.js Reason column)"
            assert isinstance(rec["detail"], (int, float)), (
                "detail must be numeric — history.js formats it as a signed percent"
            )
            assert rec["symphony_name"] in known_names, (
                f"symphony_name {rec['symphony_name']!r} is not a human name from "
                "bot_state — the raw hash id is leaking to the Symphony column"
            )


# ---------------------------------------------------------------------------
# 5 (Finding 11): the post-mortem path's Time column
# ---------------------------------------------------------------------------


class TestPostMortemPathTimeColumn:
    def test_todays_exits_from_post_mortem_file_carry_the_trigger_time(self, client, pm_dir):
        """When today's post-mortem file exists (the 15:54+ window), todays_exits
        must map each entry's ``time_triggered`` into the consumed ``ts`` field.

        RED today: analytics reads ``timestamp``/``ts`` keys the producer never
        writes, so the Time column renders an em-dash even on the healthy path.

        A real production file is copied to today's filename so the PM path (not
        the fallback) serves the rows; only the time MAPPING is asserted — the
        file's money values are known-wrong (Finding 2) and never used as oracle.
        """
        source_file = max(
            pm_dir.glob("post_mortem_*.json"),
            key=lambda f: len(json.loads(f.read_text(encoding="utf-8")).get("triggers", [])),
        )
        pm_data = json.loads(source_file.read_text(encoding="utf-8"))
        expected_times = {
            t["symphony_name"]: t["time_triggered"]
            for t in pm_data["triggers"]
            if t.get("time_triggered")
        }
        assert expected_times, "fixture integrity: source file must carry trigger times"
        # get_history_summary looks the today-file up by the LOCAL date.
        today_local = date.today().isoformat()
        (pm_dir / f"post_mortem_{today_local}.json").write_text(
            json.dumps(pm_data), encoding="utf-8"
        )

        resp = client.get(f"/api/history/{_WIDE_WINDOW_DAYS}")
        assert resp.status_code == 200
        exits = resp.get_json()["todays_exits"]
        assert exits, "the post-mortem path must serve today's exits when the file exists"

        for rec in exits:
            expected = expected_times.get(rec.get("symphony_name"))
            if expected is None:
                continue
            assert rec.get("ts") == expected, (
                f"{rec.get('symphony_name')!r}: ts={rec.get('ts')!r} does not carry the "
                f"entry's time_triggered {expected!r} — the Time column renders an "
                "em-dash on the post-mortem path"
            )
