"""
RED-phase tests for the cycleat-fix cycle — two reviewer BLOCKs from the
data-vs-action split merge (46fe019) plus a CR unit-error:

  BLOCK 1 — /api/state must surface last_successful_cycle_at at the TOP LEVEL
             of its JSON response (not only nested in 'state').

  BLOCK 2 — The EOD post-mortem path (alpha_bot_execution.py ~line 506) must
             set last_successful_cycle_at BEFORE calling save_state, so that
             after-16:00 cycles update the field.

  BLOCK 3 — index.html must contain a staleness badge element with a
             recognisable id so the JS can show/hide it based on cycle age.

  BLOCK 4 — The staleness badge must read data from data-attribute or from the
             top-level /api/state field (i.e. JS must reference it).

  BLOCK 5 — analytics.py get_symphony_cumulative_return must return a
             PERCENT-scaled value (simple_return * 100), not the raw Composer
             decimal. TC already converts correctly; CR does not.
             The template (table_partial.html + index.html JS) renders CR with
             fmtPct(v) which only appends '%' — it expects a percent value,
             not a decimal. 0.65976 → 0.66% (wrong); 65.976 → 65.98% (right).

All tests are RED on the HEAD b7b6b0f codebase.  None touch math_engine.
No live API calls — all external I/O mocked.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

import alpha_bot_execution
import app as app_module


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    def _et(hour: int, minute: int, weekday_date: str = "2025-05-14") -> datetime:
        y, mo, d = map(int, weekday_date.split("-"))
        return datetime(y, mo, d, hour, minute, 0, tzinfo=_ET)

except Exception:  # pragma: no cover
    def _et(hour: int, minute: int, weekday_date: str = "2025-05-14") -> datetime:
        y, mo, d = map(int, weekday_date.split("-"))
        return datetime(y, mo, d, hour, minute, 0,
                        tzinfo=timezone(timedelta(hours=-4)))


# EOD post-mortem window: 16:01 ET on a weekday
_EOD_16_01 = _et(16, 1)
_DATE_STR = "2025-05-14"

# Minimal bot_state that satisfies engine guards without triggering live paths
_BASE_STATE = {
    "date": _DATE_STR,
    "last_execution_mode": False,
    "post_mortem_run": None,  # not yet run today
}

_ACCOUNT_ID = "acct-cycleat-fix-001"
_SYM_ID = "sym-cycleat-fix-001"
_SYM_NAME = "CycleatFix Test Symphony"

# Minimal Composer-style symphony response
_MINIMAL_SYM = {
    "id": _SYM_ID,
    "name": _SYM_NAME,
    "last_percent_change": 0.005,
    "holdings": [{"ticker": "SPY", "allocation": 1.0}],
}

_MOCK_ALPACA_HISTORY = {
    "SPY": [
        {"t": "2025-05-14T09:30:00-04:00", "c": 520.0},
        {"t": "2025-05-14T10:00:00-04:00", "c": 521.0},
    ]
}


def _make_eod_bot_state() -> dict:
    """bot_state seeded with the minimum fields the EOD path reads."""
    state = copy.deepcopy(_BASE_STATE)
    state[_SYM_ID] = {
        "name": _SYM_NAME,
        "account": _ACCOUNT_ID,
        "armed": False,
        "triggered": False,
        "current_return": 0.5,
        "current_value": 10000.0,
        "holdings": [{"ticker": "SPY", "allocation": 1.0}],
    }
    return state


_MINIMAL_SYM_LIST = [copy.deepcopy(_MINIMAL_SYM)]


def _run_eod_patched(initial_bot_state: dict | None = None) -> dict:
    """
    Run alpha_bot_execution.main() under 16:01 ET (EOD post-mortem window).

    Uses the same mock pattern as test_reliability_expansion._run_main_patched.
    Returns the final bot_state captured from the last save_state call.
    """
    if initial_bot_state is None:
        initial_bot_state = _make_eod_bot_state()

    captured: list[dict] = []
    date_str = _EOD_16_01.strftime("%Y-%m-%d")

    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting"), \
         patch.object(alpha_bot_execution, "autotuner") as mock_autotuner, \
         patch.object(alpha_bot_execution, "fetch_symphony_stats",
                      return_value=_MINIMAL_SYM_LIST), \
         patch.object(alpha_bot_execution, "fetch_alpaca_history",
                      return_value=_MOCK_ALPACA_HISTORY), \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                      return_value={"SPY": {"vwap": 520.0, "last_price": 521.0}}), \
         patch.object(alpha_bot_execution, "get_current_et",
                      return_value=_EOD_16_01), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "fake-key"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "fake-alpaca-key"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution, "EXECUTION_START_TIME", "10:30"), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
         patch.object(alpha_bot_execution, "math_engine") as mock_math:

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = copy.deepcopy(initial_bot_state)
        mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s
        mock_db.save_state.side_effect = lambda s: captured.append(copy.deepcopy(s))
        mock_db.release_lock.return_value = None
        mock_db.save_chart_history.return_value = None
        mock_autotuner.run_autotuner.return_value = None

        mock_math.run_monte_carlo.return_value = 80.0
        mock_math.calculate_20d_vol.return_value = 0.15
        mock_math.compute_vwap_signals.return_value = (0.0, 0.0)
        mock_math.compute_para_arm_decision.return_value = (0.0, False)
        mock_math.compute_time_squeeze_decay.return_value = (1.0, 0.5)
        mock_math.compute_active_trailing_stop.return_value = 2.0
        mock_math.compute_breakeven_update.return_value = (0, False, -2.0)
        mock_math.compute_exit_confirmation.return_value = (0, False)
        mock_math.compute_vwap_breakdown_update.return_value = (0, 0, False, False)
        mock_math.compute_vwap_bleed_arm_threshold.return_value = 0.5

        alpha_bot_execution.main()

    return captured[-1] if captured else {}


# ===========================================================================
# BLOCK 1 — /api/state must surface last_successful_cycle_at at the top level
# ===========================================================================

class TestApiStateTopLevelSurface:
    """
    /api/state JSON must include last_successful_cycle_at as a TOP-LEVEL key,
    not only nested inside 'state'.  The frontend reads top-level keys by
    convention and currently receives None.
    """

    def test_last_successful_cycle_at_at_top_level_of_api_state(self):
        """
        When bot_state contains last_successful_cycle_at, the /api/state
        response must expose it at the TOP LEVEL (data.last_successful_cycle_at),
        in addition to being nested in data.state.last_successful_cycle_at.

        RED: the current /api/state route (app.py ~line 315) does not include
        last_successful_cycle_at as a top-level key.  Only 'state' is returned
        and the frontend reads top-level keys — so it sees None.
        """
        timestamp = "2025-05-14T10:45:00-04:00"
        mock_state = {
            "date": _DATE_STR,
            "last_execution_mode": False,
            "last_successful_cycle_at": timestamp,
        }

        with app_module.app.test_client() as client, \
             patch.object(app_module.database, "load_state",
                          return_value=mock_state), \
             patch.object(app_module, "schedule") as mock_schedule, \
             patch.object(app_module.database, "normalize_name",
                          side_effect=lambda n: n.strip().lower()), \
             patch.dict("os.environ", {"LIVE_EXECUTION": "False"}, clear=False):

            mock_schedule.get_jobs.return_value = []
            resp = client.get("/api/state")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None, "/api/state must return valid JSON"

        top_level = data.get("last_successful_cycle_at")
        assert top_level == timestamp, (
            f"last_successful_cycle_at must appear at the TOP LEVEL of /api/state "
            f"JSON (data.last_successful_cycle_at), not only nested in data.state. "
            f"Expected {timestamp!r}, got top-level={top_level!r}. "
            f"Fix: add 'last_successful_cycle_at': state_data.get('last_successful_cycle_at') "
            f"to the jsonify() dict in app.py's /api/state route."
        )

    def test_top_level_matches_nested_state_value(self):
        """
        The top-level last_successful_cycle_at must equal the value in
        state.last_successful_cycle_at — they must be in sync (same source).

        RED: top-level key is absent, so they cannot be equal.
        """
        timestamp = "2025-05-14T14:00:00-04:00"
        mock_state = {
            "date": _DATE_STR,
            "last_execution_mode": False,
            "last_successful_cycle_at": timestamp,
        }

        with app_module.app.test_client() as client, \
             patch.object(app_module.database, "load_state",
                          return_value=mock_state), \
             patch.object(app_module, "schedule") as mock_schedule, \
             patch.object(app_module.database, "normalize_name",
                          side_effect=lambda n: n.strip().lower()), \
             patch.dict("os.environ", {"LIVE_EXECUTION": "False"}, clear=False):

            mock_schedule.get_jobs.return_value = []
            resp = client.get("/api/state")

        data = resp.get_json()
        top_level = data.get("last_successful_cycle_at")
        nested = data.get("state", {}).get("last_successful_cycle_at")

        assert top_level is not None, (
            "top-level last_successful_cycle_at must not be None when the field "
            "exists in bot_state."
        )
        assert top_level == nested, (
            f"top-level last_successful_cycle_at={top_level!r} must equal "
            f"state.last_successful_cycle_at={nested!r}. They must share the same "
            f"source value — no transformation, no re-computation."
        )

    def test_absent_field_returns_null_not_missing_key(self):
        """
        When bot_state has no last_successful_cycle_at (first deploy), the
        top-level key in /api/state must be present with value null (JSON null),
        not absent entirely.  This keeps the JS side simpler: it can always read
        data.last_successful_cycle_at and check for null rather than checking for
        key existence.

        RED: top-level key is wholly absent today.
        """
        mock_state = {
            "date": _DATE_STR,
            "last_execution_mode": False,
        }

        with app_module.app.test_client() as client, \
             patch.object(app_module.database, "load_state",
                          return_value=mock_state), \
             patch.object(app_module, "schedule") as mock_schedule, \
             patch.object(app_module.database, "normalize_name",
                          side_effect=lambda n: n.strip().lower()), \
             patch.dict("os.environ", {"LIVE_EXECUTION": "False"}, clear=False):

            mock_schedule.get_jobs.return_value = []
            resp = client.get("/api/state")

        data = resp.get_json()
        assert "last_successful_cycle_at" in data, (
            "last_successful_cycle_at must be a key in the /api/state top-level "
            "response even when absent from bot_state (value should be null). "
            "This allows the JS to check data.last_successful_cycle_at === null "
            "rather than 'last_successful_cycle_at' in data."
        )
        assert data["last_successful_cycle_at"] is None, (
            f"When absent from bot_state, the top-level last_successful_cycle_at "
            f"must be JSON null (Python None), not {data['last_successful_cycle_at']!r}."
        )


# ===========================================================================
# BLOCK 2 — EOD post-mortem path must set last_successful_cycle_at
# ===========================================================================

class TestEodPostMortemSetsLastSuccessfulCycleAt:
    """
    The EOD post-mortem branch (alpha_bot_execution.py ~line 486-506) calls
    save_state WITHOUT first setting last_successful_cycle_at.  After 16:00 ET
    the field goes stale.  These tests assert it is set BEFORE save_state.
    """

    def test_eod_post_mortem_sets_last_successful_cycle_at_before_save(self):
        """
        After running the EOD post-mortem path (16:01 ET), bot_state must
        contain last_successful_cycle_at set to the current ET timestamp,
        and it must be present in the state passed to save_state.

        RED: the EOD branch at ~line 506 calls save_state(bot_state) without
        setting the field.  The field is absent (or stale) in the saved state.
        """
        final = _run_eod_patched()

        assert "last_successful_cycle_at" in final, (
            "bot_state must contain last_successful_cycle_at after the EOD "
            "post-mortem cycle runs.  Currently the EOD branch (~line 506) calls "
            "save_state without setting this field — so it remains absent or "
            "retains a stale value from the pre-16:00 cycle. "
            "Fix: add bot_state['last_successful_cycle_at'] = current_et.isoformat() "
            "at alpha_bot_execution.py before the save_state call on the EOD path."
        )

    def test_eod_post_mortem_last_successful_cycle_at_is_iso_string(self):
        """
        The value written by the EOD path must be a parseable ISO-8601 datetime
        string.  The dashboard badge parses it via new Date() (JS) or
        datetime.fromisoformat() (Python tests).

        RED: field is absent on EOD path, so this also fails today.
        """
        final = _run_eod_patched()

        value = final.get("last_successful_cycle_at", "")
        assert isinstance(value, str) and value.strip(), (
            f"last_successful_cycle_at must be a non-empty string; got {value!r}"
        )
        try:
            datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            pytest.fail(
                f"last_successful_cycle_at={value!r} is not a valid ISO-8601 "
                f"datetime string: {exc}. Use current_et.isoformat()."
            )

    def test_eod_post_mortem_timestamp_matches_mock_et(self):
        """
        The EOD timestamp must reflect the mocked current_et (16:01) rather
        than a stale pre-gate timestamp or wall-clock datetime.now().

        This ensures the field is set DURING the EOD execution, not carried
        forward from a prior cycle.

        RED: field is absent on EOD path.
        """
        final = _run_eod_patched()

        value = final.get("last_successful_cycle_at")
        assert value is not None, (
            "last_successful_cycle_at must be set by the EOD post-mortem path."
        )
        parsed = datetime.fromisoformat(value)
        # Must be within the 16:xx hour (hour 16 in ET = UTC-4 → hour 20 UTC)
        # Allow any minute within 16:xx to avoid brittle sub-minute assertions.
        # We mock get_current_et to return 16:01 — the stored value must be ≥ 16:00.
        et_offset = timedelta(hours=-4)  # EDT
        if parsed.tzinfo is None:
            # naive datetimes are treated as local; just check hour
            assert parsed.hour == 16, (
                f"EOD last_successful_cycle_at hour must be 16 (ET); got {parsed.hour}. "
                f"Value: {value!r}"
            )
        else:
            # Convert to ET for comparison
            parsed_et = parsed.astimezone(timezone(et_offset))
            assert parsed_et.hour == 16, (
                f"EOD last_successful_cycle_at must be in the 16:xx ET hour; "
                f"got {parsed_et.hour}:xx. Value: {value!r}"
            )


# ===========================================================================
# BLOCK 3 — index.html must contain the staleness badge element
# ===========================================================================

class TestStalenessBadgeMarkupInIndexHtml:
    """
    The dashboard's index.html must contain a staleness badge element with
    id='cycle-staleness-badge'.  pytest cannot verify visual rendering, but
    it can assert the DOM anchor is present in the rendered HTML.
    """

    def test_index_html_renders_cycle_staleness_badge_element(self):
        """
        GET / (the dashboard root) must return HTML that contains an element
        with id="cycle-staleness-badge".  This is the DOM anchor the JS uses
        to show/hide the stale-data warning.

        RED: no such element exists in index.html today.
        """
        with app_module.app.test_client() as client:
            resp = client.get("/")

        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'id="cycle-staleness-badge"' in html, (
            'index.html must contain an element with id="cycle-staleness-badge" '
            "in the header banner area.  The JS staleness logic targets this id. "
            "RED: the element does not exist in the current template. "
            "Fix: add a <span id=\"cycle-staleness-badge\" ...> element to the "
            "header section of templates/index.html (e.g. near the status "
            "indicator or the exec-start-display span)."
        )

    def test_staleness_badge_starts_hidden(self):
        """
        The badge must start hidden (no alarming state on fresh load).
        Convention: the element must have the 'hidden' CSS class or a
        data-hidden attribute when the template is rendered without JS.

        RED: element doesn't exist yet.
        """
        with app_module.app.test_client() as client:
            resp = client.get("/")

        html = resp.data.decode("utf-8")
        # Find the badge snippet; it must include 'hidden' in the same tag
        import re
        match = re.search(
            r'<[^>]*id="cycle-staleness-badge"[^>]*>',
            html
        )
        assert match is not None, (
            'id="cycle-staleness-badge" element not found in rendered HTML. '
            "Must be present before checking its initial state."
        )
        tag_text = match.group(0)
        assert "hidden" in tag_text, (
            f"The staleness badge element must be initially hidden (contain "
            f"the 'hidden' CSS class or data-hidden attribute) so it does not "
            f"alarm operators on a fresh page load before the JS has evaluated "
            f"the cycle timestamp. Tag found: {tag_text!r}"
        )


# ===========================================================================
# BLOCK 4 — JS must reference last_successful_cycle_at from the API response
# ===========================================================================

class TestStalenessBadgeJsWiring:
    """
    The JS in index.html must read data.last_successful_cycle_at from the
    /api/state response and use it to drive the staleness badge.

    pytest cannot execute JS — this test asserts source-code wiring: that the
    JS block in index.html references the field name.
    """

    def test_js_references_last_successful_cycle_at_field(self):
        """
        The rendered index.html must contain a JS reference to
        'last_successful_cycle_at' — either reading it from the API response
        (data.last_successful_cycle_at) or from a data-attribute set by the
        route.

        RED: no such JS reference exists in index.html today.
        """
        with app_module.app.test_client() as client:
            resp = client.get("/")

        html = resp.data.decode("utf-8")
        assert "last_successful_cycle_at" in html, (
            "index.html must contain a JS reference to 'last_successful_cycle_at' "
            "(e.g. 'data.last_successful_cycle_at' in renderState()) to drive the "
            "staleness badge visibility.  "
            "RED: the field name does not appear anywhere in the template. "
            "Fix: in the renderState() function (or a helper it calls), read "
            "data.last_successful_cycle_at and toggle the "
            "cycle-staleness-badge element's visibility based on age."
        )

    def test_js_references_cycle_staleness_badge_id(self):
        """
        The JS block must reference the badge by its id='cycle-staleness-badge'
        so that the DOM update lands on the right element.

        RED: badge element and JS reference both absent today.
        """
        with app_module.app.test_client() as client:
            resp = client.get("/")

        html = resp.data.decode("utf-8")
        assert "cycle-staleness-badge" in html, (
            "index.html must reference 'cycle-staleness-badge' in its JS block "
            "(e.g. document.getElementById('cycle-staleness-badge')) AND in the "
            "HTML markup (as id=\"cycle-staleness-badge\"). "
            "Both are absent today."
        )


# ===========================================================================
# BLOCK 5 — CR 100x unit error: get_symphony_cumulative_return must return
#           PERCENT-scaled value, not raw Composer decimal
# ===========================================================================

class TestCumulativeReturnPercentScaling:
    """
    analytics.py get_symphony_cumulative_return returns raw Composer simple_return
    (e.g. 0.65976) but the dashboard renders it via fmtPct(v) = v.toFixed(2)+'%',
    which expects a percent value.  Result: 0.65976 renders as +0.66% instead
    of +65.98%.  The helper must return simple_return * 100 (like TC does).

    Root cause confirmed by code inspection:
      - TC: returns last_percent_change * 100  (correct)
      - CR: returns simple_return              (missing * 100)
      - MDD: returns max_drawdown raw          (template multiplies by 100, correct)

    Fix site: analytics.py:get_symphony_cumulative_return — add * 100.
    Note: existing test_m1_helpers.py TestGetSymphonyCumulativeReturn asserts
    the raw value and will also need updating after the fix.
    """

    # Load fixture at class level to avoid repeated file I/O
    _FIXTURE_PATH = (
        "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM"
        "/.claude/worktrees/cycleat-team/tests/fixtures/composer/symphony_stats_meta.json"
    )

    @pytest.fixture(scope="class")
    def fixture_symphonies(self):
        import json
        with open(self._FIXTURE_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload.get("symphonies", [])

    @pytest.fixture(scope="class")
    def normal_symphony(self, fixture_symphonies):
        """symphony[1]: simple_return=0.65976, net_deposits=658.5."""
        return fixture_symphonies[1]

    @pytest.fixture(scope="class")
    def twr_symphony(self, fixture_symphonies):
        """symphony[0]: simple_return=0.0, net_deposits=0.0, TWR fallback."""
        return fixture_symphonies[0]

    def test_cr_if_held_is_percent_not_decimal_for_normal_symphony(self, normal_symphony):
        """
        get_symphony_cumulative_return must return simple_return * 100.

        Fixture anchor: normal_symphony.simple_return = 0.65976 = 65.976%.
        Expected if_held: 65.976, NOT 0.65976.

        RED: current code returns 0.65976 (raw decimal).
        """
        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(normal_symphony, bot_state_entry=None)

        raw = normal_symphony["simple_return"]       # 0.65976
        expected_pct = raw * 100                     # 65.976
        raw_threshold = 5.0                          # no real CR is < 5% of the expected pct

        assert result["if_held"] == pytest.approx(expected_pct, rel=1e-6), (
            f"get_symphony_cumulative_return.if_held must be simple_return * 100 = {expected_pct}; "
            f"got {result['if_held']!r}. "
            f"If the value is ~{raw!r}, the helper is returning the raw Composer decimal — "
            f"it must multiply by 100 like get_symphony_today_change does for last_percent_change."
        )

    def test_cr_dry_run_is_percent_not_decimal_for_normal_symphony(self, normal_symphony):
        """
        dry_run CR must also be percent-scaled (equals if_held for non-triggered).

        RED: same root cause as if_held.
        """
        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(normal_symphony, bot_state_entry=None)

        raw = normal_symphony["simple_return"]
        expected_pct = raw * 100

        assert result["dry_run"] == pytest.approx(expected_pct, rel=1e-6), (
            f"get_symphony_cumulative_return.dry_run must be simple_return * 100 = {expected_pct}; "
            f"got {result['dry_run']!r}."
        )

    def test_twr_fallback_cr_is_percent_not_decimal(self, twr_symphony):
        """
        TWR fallback: if_held CR must be time_weighted_return * 100.

        Fixture: symphony[0].time_weighted_return = 3.13212 → expected 313.212%.
        Note: Composer TWR is also a decimal, same unit as simple_return.

        RED: current code returns raw 3.13212.
        """
        from analytics import get_symphony_cumulative_return

        assert twr_symphony["simple_return"] == pytest.approx(0.0, abs=1e-9), (
            "fixture assumption violated: twr_symphony.simple_return must be 0.0"
        )
        assert twr_symphony["net_deposits"] == pytest.approx(0.0, abs=1e-9), (
            "fixture assumption violated: twr_symphony.net_deposits must be 0.0"
        )

        result = get_symphony_cumulative_return(twr_symphony, bot_state_entry=None)

        raw_twr = twr_symphony["time_weighted_return"]   # 3.13212
        expected_pct = raw_twr * 100                      # 313.212

        assert result["if_held"] == pytest.approx(expected_pct, rel=1e-6), (
            f"TWR fallback: if_held CR must be time_weighted_return * 100 = {expected_pct}; "
            f"got {result['if_held']!r}. "
            f"The TWR field is also a Composer decimal — it must be scaled by 100."
        )

    def test_cr_magnitude_is_operator_readable_percent(self, fixture_symphonies):
        """
        Property test: for any non-None CR result from the fixture, the value
        must be in a plausible percent range for a real portfolio.

        Invariant: |CR| < 10000 (i.e. not in decimal [0,1] range which would
        be < 1.0 for all realistic Composer symphonies).

        This test catches the category of bug where the helper returns a decimal
        and the value never exceeds ~1.5 for any real symphony.

        RED: all fixture symphonies have simple_return < 2.0 (decimal), so
        current code returns < 2.0.  After the fix, values should be < 200 (%)
        but easily > 1.0 for the normals.
        """
        from analytics import get_symphony_cumulative_return

        has_nonzero = False
        for sym in fixture_symphonies:
            result = get_symphony_cumulative_return(sym, bot_state_entry=None)
            if result["if_held"] is None:
                continue
            v = result["if_held"]
            raw = sym.get("simple_return")
            if raw is None:
                continue
            raw_f = float(raw)
            if raw_f == 0.0 and float(sym.get("net_deposits", 1.0)) == 0.0:
                # TWR fallback — compare against time_weighted_return * 100
                twr = sym.get("time_weighted_return")
                if twr is None:
                    continue
                expected = float(twr) * 100.0
            else:
                expected = raw_f * 100.0

            # The core invariant: result must equal raw * 100 (percent-scaled).
            # Use rel tolerance of 1e-6 — same as the targeted per-symphony tests.
            assert v == pytest.approx(expected, rel=1e-6), (
                f"CR if_held={v!r} must equal simple_return*100={expected!r}. "
                f"Symphony id={sym.get('id')!r}, simple_return={raw!r}. "
                f"If result equals simple_return (not *100), the * 100 scaling is missing."
            )
            if abs(raw_f) > 0.0:
                has_nonzero = True

        assert has_nonzero, (
            "All 11 fixture symphonies returned None or zero-CR — "
            "fixture may be invalid."
        )

    def test_portfolio_cr_is_percent_not_decimal(self, fixture_symphonies):
        """
        get_portfolio_cumulative_return must also return a percent-scaled value,
        since it delegates to get_symphony_cumulative_return.

        Build a minimal symphonies list from the fixture (with 'value' field) and
        assert the portfolio aggregate CR is > 1.5 (not a raw decimal).

        RED: same root cause, propagates through _value_weighted_portfolio.
        """
        from analytics import get_portfolio_cumulative_return

        symphonies = []
        for sym in fixture_symphonies:
            if sym.get("simple_return") is not None and sym.get("simple_return") != 0.0:
                symphonies.append({
                    "id": sym.get("id", ""),
                    "value": 10000.0,  # equal weight for simplicity
                    "last_percent_change": sym.get("last_percent_change", 0.0),
                    "simple_return": sym.get("simple_return"),
                    "net_deposits": sym.get("net_deposits", 1.0),
                    "time_weighted_return": sym.get("time_weighted_return"),
                    "max_drawdown": sym.get("max_drawdown"),
                })

        assert len(symphonies) >= 2, (
            f"Need at least 2 non-TWR-fallback symphonies from fixture; got {len(symphonies)}"
        )

        # bot_state has no entries for these symphonies (non-triggered)
        result = get_portfolio_cumulative_return(symphonies, bot_state={})

        assert result["if_held"] is not None, (
            "Portfolio CR must not be None when symphonies have valid simple_return."
        )
        v = result["if_held"]
        assert abs(v) > 1.5, (
            f"Portfolio CR if_held={v!r} looks like a raw decimal (< 1.5). "
            f"Expected a percent value like ~65. "
            f"Root cause: get_symphony_cumulative_return not scaling by 100."
        )
