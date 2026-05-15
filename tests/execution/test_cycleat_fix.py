"""
RED-phase tests for the cycleat-fix cycle — two reviewer BLOCKs from the
data-vs-action split merge (46fe019):

  BLOCK 1 — /api/state must surface last_successful_cycle_at at the TOP LEVEL
             of its JSON response (not only nested in 'state').

  BLOCK 2 — The EOD post-mortem path (alpha_bot_execution.py ~line 506) must
             set last_successful_cycle_at BEFORE calling save_state, so that
             after-16:00 cycles update the field.

  BLOCK 3 — index.html must contain a staleness badge element with a
             recognisable id so the JS can show/hide it based on cycle age.

  BLOCK 4 — The staleness badge must read data from data-attribute or from the
             top-level /api/state field (i.e. JS must reference it).

All four tests are RED on the HEAD b7b6b0f codebase.  None touch math_engine.
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
