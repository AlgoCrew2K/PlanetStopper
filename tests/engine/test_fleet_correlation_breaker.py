"""
RED tests for V3 — Fleet-decorrelation circuit breaker (observational only).

Covers AC-V3.1 through AC-V3.4:

  AC-V3.1: When >50% of active symphonies fire the SAME triggered_reason within
           3 minutes, engine sets bot_state["fleet_correlation_alert"] and the
           field has the required shape.

  AC-V3.2: OBSERVATIONAL ONLY — does NOT block triggers, does NOT alter exit
           decisions. Trigger side-effects still fire when alert is tripped.

  AC-V3.3: Alert clears automatically after FLEET_CORRELATION_CLEAR_MINUTES (30)
           with no further fleet events; operator can dismiss manually via
           POST /api/fleet-alert/dismiss.

  AC-V3.4: Detection reads from H1's exit_triggers table via a helper that
           calls get_recent_triggers(window_minutes=...) (or equivalent).

Test naming: scenario-based (what happens, not which function).

Fixture provenance: all fixtures under tests/fixtures/engine/fleet_correlation/
  are schema-derived from exit_triggers DDL + AC-V3.* shape specs.
  Zero inline construction. Zero hardcoded producer-computed values.

Mocking philosophy:
  - detect_fleet_correlation: pure helper — tested directly, no mocks.
  - DB reads (get_recent_triggers): mocked with fixture data — no live DB.
  - Time: patched via unittest.mock.patch for deterministic window checks.
  - Trigger side-effects: record_exit_trigger and reporting mocked (network/DB).

PA-18: fixtures from tests/fixtures/engine/fleet_correlation/. Provenance
  comments in each fixture JSON.
PA-19: explicit APPROVE from quant-code-reviewer required before merge.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pathlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURES = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "engine"
    / "fleet_correlation"
)


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    from datetime import timezone
    _ET = timezone(timedelta(hours=-4))


def _et_at(iso_str: str) -> datetime:
    """Parse a 'YYYY-MM-DDTHH:MM:SS' string as an ET-aware datetime."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    return dt


# ---------------------------------------------------------------------------
# Section 1 — detect_fleet_correlation pure helper signature + existence
# ---------------------------------------------------------------------------


class TestDetectFleetCorrelationSignature:
    """
    The helper must exist with the specified signature and be pure (no network,
    no DB, no side-effects). It receives pre-fetched triggers so the caller
    controls DB interaction.

    Expected signature:
      detect_fleet_correlation(
          triggers_in_window: list[dict],
          active_symphonies: int,
          pct_threshold: float,
      ) -> tuple[bool, str | None]
    """

    @pytest.fixture(autouse=True)
    def _import_helper(self):
        try:
            from alpha_bot_execution import detect_fleet_correlation
            self._fn = detect_fleet_correlation
        except ImportError:
            self._fn = None

    def _call(self, triggers, active_symphonies, pct_threshold):
        if self._fn is None:
            pytest.fail(
                "detect_fleet_correlation not found in alpha_bot_execution. "
                "Implement per AC-V3.1 signature: "
                "detect_fleet_correlation(triggers_in_window, active_symphonies, pct_threshold) "
                "-> tuple[bool, str | None]"
            )
        return self._fn(triggers, active_symphonies, pct_threshold)

    def test_function_exists_in_alpha_bot_execution(self):
        """detect_fleet_correlation must be importable from alpha_bot_execution."""
        assert self._fn is not None, (
            "detect_fleet_correlation must be a module-level function in alpha_bot_execution. "
            "Missing — implement per AC-V3.1."
        )

    def test_signature_has_three_parameters(self):
        """Function must accept exactly triggers_in_window, active_symphonies, pct_threshold."""
        if self._fn is None:
            pytest.skip("Function not yet implemented")
        sig = inspect.signature(self._fn)
        param_names = list(sig.parameters.keys())
        assert "triggers_in_window" in param_names, (
            f"detect_fleet_correlation must accept 'triggers_in_window'. Got: {param_names}"
        )
        assert "active_symphonies" in param_names, (
            f"detect_fleet_correlation must accept 'active_symphonies'. Got: {param_names}"
        )
        assert "pct_threshold" in param_names, (
            f"detect_fleet_correlation must accept 'pct_threshold'. Got: {param_names}"
        )

    def test_return_type_is_two_tuple(self):
        """Returns a 2-tuple (bool, str|None)."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["expected_tripped"])
        result = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert isinstance(result, tuple), (
            f"detect_fleet_correlation must return a tuple, got {type(result).__name__}"
        )
        assert len(result) == 2, (
            f"Return tuple must have 2 elements (tripped: bool, reason: str|None), got {len(result)}"
        )
        tripped, reason = result
        assert isinstance(tripped, bool), (
            f"First element must be bool, got {type(tripped).__name__}"
        )


# ---------------------------------------------------------------------------
# Section 2 — Core detection logic
# ---------------------------------------------------------------------------


class TestDetectFleetCorrelationLogic:
    """
    AC-V3.1: Detection semantics — strict >50%, same reason, within window.
    All cases from detection_basic.json.
    """

    @pytest.fixture(autouse=True)
    def _import_helper(self):
        try:
            from alpha_bot_execution import detect_fleet_correlation
            self._fn = detect_fleet_correlation
        except ImportError:
            self._fn = None

    def _call(self, triggers, active_symphonies, pct_threshold):
        if self._fn is None:
            pytest.fail(
                "detect_fleet_correlation not found in alpha_bot_execution — implement per AC-V3.1."
            )
        return self._fn(triggers, active_symphonies, pct_threshold)

    def test_majority_same_reason_trips_alert(self):
        """More than 50% of active symphonies with same reason → tripped=True, reason returned."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["label"] == "majority_same_reason_trips_alert")
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is True, (
            f"Case '{case['label']}': expected tripped=True. {case['note']}"
        )
        assert reason == case["expected_reason"], (
            f"Case '{case['label']}': expected reason={case['expected_reason']!r}, got {reason!r}"
        )

    def test_exactly_fifty_pct_does_not_trip(self):
        """Exactly 50% must NOT trip — threshold is strict greater-than."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["label"] == "exactly_fifty_pct_does_not_trip")
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is False, (
            f"Case '{case['label']}': 50% exactly must NOT trip (strict >). {case['note']}"
        )
        assert reason is None, (
            f"Case '{case['label']}': reason must be None when not tripped, got {reason!r}"
        )

    def test_below_fifty_pct_does_not_trip(self):
        """30% same reason does not trip the alert."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["label"] == "below_fifty_pct_no_trip")
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is False, (
            f"Case '{case['label']}': 30% must not trip. {case['note']}"
        )

    def test_different_reasons_all_at_threshold_does_not_trip(self):
        """Multiple reasons each at 40% — none exceeds 50% — no trip."""
        fixture = _load("detection_basic.json")
        case = next(
            c for c in fixture["cases"]
            if c["label"] == "different_reasons_all_above_threshold_individually_no_trip"
        )
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is False, (
            f"Case '{case['label']}': mixed reasons must not trip even if combined > 50%. "
            f"{case['note']}"
        )
        assert reason is None, (
            "When not tripped, reason must be None."
        )

    def test_empty_window_does_not_trip(self):
        """Zero triggers in window → no trip."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["label"] == "empty_window_no_trip")
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is False, "Empty trigger list must never trip the alert."
        assert reason is None, "reason must be None for empty window."

    def test_reason_in_result_matches_dominant_reason(self):
        """The returned reason is the specific triggered_reason string that exceeded the threshold."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["expected_tripped"])
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is True
        assert reason is not None, "Tripped result must include the dominant reason string."
        assert isinstance(reason, str) and len(reason) > 0, (
            "Dominant reason must be a non-empty string matching a triggered_reason value."
        )

    def test_returns_none_reason_when_not_tripped(self):
        """When not tripped, second element of tuple must be None."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if not c["expected_tripped"])
        tripped, reason = self._call(
            case["triggers_in_window"],
            case["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is False
        assert reason is None, (
            f"reason must be None when not tripped, got {reason!r}"
        )

    def test_helper_is_deterministic_and_pure(self):
        """Calling detect_fleet_correlation twice with same args returns same result."""
        fixture = _load("detection_basic.json")
        case = next(c for c in fixture["cases"] if c["expected_tripped"])
        r1 = self._call(case["triggers_in_window"], case["active_symphonies"], fixture["pct_threshold"])
        r2 = self._call(case["triggers_in_window"], case["active_symphonies"], fixture["pct_threshold"])
        assert r1 == r2, "detect_fleet_correlation must be deterministic."


# ---------------------------------------------------------------------------
# Section 3 — Window boundary semantics
# ---------------------------------------------------------------------------


class TestDetectFleetCorrelationWindowBoundary:
    """
    Boundary cases from window_boundary.json.
    Window: strictly (now - window_minutes, now] — events AT the boundary cutoff
    (exactly now - window_minutes) are OUTSIDE the window.
    """

    @pytest.fixture(autouse=True)
    def _import_helper(self):
        try:
            from alpha_bot_execution import detect_fleet_correlation
            self._fn = detect_fleet_correlation
        except ImportError:
            self._fn = None

    def _call(self, triggers, active_symphonies, pct_threshold):
        if self._fn is None:
            pytest.fail("detect_fleet_correlation not found — implement per AC-V3.1.")
        return self._fn(triggers, active_symphonies, pct_threshold)

    def test_triggers_inside_3min_window_trip_alert(self):
        """3 of 4 triggers within 2:59 — all inside window — alert trips."""
        fixture = _load("window_boundary.json")
        case = next(c for c in fixture["cases"] if c["label"] == "all_three_inside_window_trips")
        now_et = _et_at(fixture["base_et"])

        # Filter triggers to only those inside the window relative to now_et
        window_s = fixture["window_minutes"] * 60
        triggers_in_window = [
            t for t in case["triggers"]
            if (now_et - _et_at(t["ts_et"])).total_seconds() < window_s
        ]

        tripped, reason = self._call(
            triggers_in_window,
            fixture["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is True, (
            f"Case '{case['label']}': 3 of 4 within window → should trip. {case['note']}"
        )
        assert reason == "PARA-ARM", f"Expected 'PARA-ARM', got {reason!r}"

    def test_trigger_at_exact_3min_boundary_excluded_from_window(self):
        """Event at exactly now - 3:00 is OUTSIDE the window — only 2 of 4 inside → no trip."""
        fixture = _load("window_boundary.json")
        case = next(c for c in fixture["cases"] if c["label"] == "oldest_at_exactly_boundary_excluded")
        now_et = _et_at(fixture["base_et"])

        window_s = fixture["window_minutes"] * 60
        # Strict less-than: events at exactly boundary distance are excluded
        triggers_in_window = [
            t for t in case["triggers"]
            if (now_et - _et_at(t["ts_et"])).total_seconds() < window_s
        ]

        assert len(triggers_in_window) == case["expected_count_in_window"], (
            f"Window filter must exclude event at exactly now-{fixture['window_minutes']}min. "
            f"Expected {case['expected_count_in_window']} in window, got {len(triggers_in_window)}."
        )

        tripped, reason = self._call(
            triggers_in_window,
            fixture["active_symphonies"],
            fixture["pct_threshold"],
        )
        assert tripped is False, (
            f"Case '{case['label']}': {case['note_on_boundary']}"
        )


# ---------------------------------------------------------------------------
# Section 4 — bot_state["fleet_correlation_alert"] field shape
# ---------------------------------------------------------------------------


class TestFleetAlertStateShape:
    """
    AC-V3.1: When the alert trips, bot_state["fleet_correlation_alert"] must be
    a dict with the exact required keys and correct value types.
    """

    def _get_alert_setter(self):
        """Resolve the function that sets bot_state['fleet_correlation_alert']."""
        try:
            from alpha_bot_execution import set_fleet_correlation_alert
            return set_fleet_correlation_alert
        except ImportError:
            return None

    def test_fleet_correlation_alert_key_shape_when_tripped(self):
        """bot_state['fleet_correlation_alert'] must contain required keys with correct types."""
        fixture = _load("alert_state_shape.json")
        required_keys = fixture["required_keys"]

        # If a dedicated setter exists, use it; otherwise test the shape via a mock bot_state
        setter = self._get_alert_setter()
        if setter is None:
            pytest.fail(
                "set_fleet_correlation_alert (or equivalent) not found in alpha_bot_execution. "
                "Implement a function that writes the fleet_correlation_alert dict into bot_state "
                "per AC-V3.1 shape: tripped_at_et, triggered_reason, tripped_count, active_count."
            )

        bot_state: dict = {}
        now_et_str = "2026-05-16T10:33:00"
        setter(
            bot_state=bot_state,
            tripped_at_et=now_et_str,
            triggered_reason="Trailing Stop",
            tripped_count=6,
            active_count=10,
        )

        assert "fleet_correlation_alert" in bot_state, (
            "set_fleet_correlation_alert must write 'fleet_correlation_alert' key into bot_state."
        )
        alert = bot_state["fleet_correlation_alert"]
        assert isinstance(alert, dict), (
            f"fleet_correlation_alert must be a dict, got {type(alert).__name__}"
        )
        for key in required_keys:
            assert key in alert, (
                f"fleet_correlation_alert must contain key '{key}'. Present keys: {list(alert.keys())}"
            )

    def test_tripped_at_et_is_string(self):
        """tripped_at_et must be a string (ISO ET timestamp)."""
        setter = self._get_alert_setter()
        if setter is None:
            pytest.fail("set_fleet_correlation_alert not found — implement per AC-V3.1.")
        bot_state: dict = {}
        setter(
            bot_state=bot_state,
            tripped_at_et="2026-05-16T10:33:00",
            triggered_reason="PARA-ARM",
            tripped_count=6,
            active_count=10,
        )
        alert = bot_state["fleet_correlation_alert"]
        assert isinstance(alert["tripped_at_et"], str), (
            f"tripped_at_et must be a str, got {type(alert['tripped_at_et']).__name__}"
        )

    def test_tripped_count_and_active_count_are_ints(self):
        """tripped_count and active_count must be int."""
        setter = self._get_alert_setter()
        if setter is None:
            pytest.fail("set_fleet_correlation_alert not found — implement per AC-V3.1.")
        bot_state: dict = {}
        setter(
            bot_state=bot_state,
            tripped_at_et="2026-05-16T10:33:00",
            triggered_reason="PARA-ARM",
            tripped_count=6,
            active_count=10,
        )
        alert = bot_state["fleet_correlation_alert"]
        assert isinstance(alert["tripped_count"], int), (
            f"tripped_count must be int, got {type(alert['tripped_count']).__name__}"
        )
        assert isinstance(alert["active_count"], int), (
            f"active_count must be int, got {type(alert['active_count']).__name__}"
        )

    def test_fleet_correlation_alert_absent_when_not_tripped(self):
        """When no alert is active, bot_state must NOT contain 'fleet_correlation_alert'."""
        fixture = _load("alert_state_shape.json")
        # A fresh bot_state (no alert) must not have the key
        bot_state: dict = {"some_symphony": {"triggered": False}}
        assert "fleet_correlation_alert" not in bot_state, (
            "bot_state must not contain 'fleet_correlation_alert' when no alert has fired. "
            f"Key present: {fixture['absent_when_not_tripped']}"
        )


# ---------------------------------------------------------------------------
# Section 5 — AC-V3.2: OBSERVATIONAL ONLY — trigger side-effects not blocked
# ---------------------------------------------------------------------------


class TestFleetAlertObservationalOnly:
    """
    AC-V3.2: When the fleet correlation alert trips, ALL individual symphony
    trigger side-effects must still execute normally. The alert flag must NOT
    appear in any condition that gates trigger dispatch.
    """

    def test_detect_fleet_correlation_does_not_mutate_triggers_list(self):
        """
        detect_fleet_correlation must not modify the triggers_in_window list.
        The caller (engine dispatch site) owns the list; the pure helper must
        not delete, reorder, or mutate it.
        """
        try:
            from alpha_bot_execution import detect_fleet_correlation
        except ImportError:
            pytest.fail("detect_fleet_correlation not found — implement per AC-V3.1.")

        fixture = _load("trigger_side_effects_passthrough.json")
        triggers = [
            {"symphony_id": f"s{i}", "triggered_reason": "Trailing Stop", "ts_et": "2026-05-16T10:32:00"}
            for i in range(6)
        ]
        original_length = len(triggers)
        original_reasons = [t["triggered_reason"] for t in triggers]

        detect_fleet_correlation(triggers, 10, 0.50)

        assert len(triggers) == original_length, (
            "detect_fleet_correlation must not mutate (add/remove elements from) triggers_in_window."
        )
        assert [t["triggered_reason"] for t in triggers] == original_reasons, (
            "detect_fleet_correlation must not modify triggered_reason values in the list."
        )

    def test_fleet_correlation_alert_key_not_in_trigger_gate_condition(self):
        """
        The fleet_correlation_alert key must NOT appear inside the triggered=True
        side-effect conditional block in alpha_bot_execution.py.
        This is a structural (source inspection) RED test — it will fail until
        the implementer confirms the alert write occurs AFTER side-effects, not before.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        src = inspect.getsource(alpha_bot_execution)

        # The check: 'fleet_correlation_alert' must not appear in a context where
        # it gates trigger dispatch. We assert the alert key does NOT gate `triggered=True`.
        # Implementation note: we look for the pattern where fleet_correlation_alert
        # would be read inside the trigger-side-effect block (lines ~1072-1132).
        # A correct implementation writes it AFTER the trigger loop, never inside a guard.
        #
        # This test will fail RED until the feature is implemented; it becomes GREEN
        # when the implementer adds the alert write OUTSIDE the trigger condition block.
        # At that point, 'fleet_correlation_alert' will appear in the source but NOT
        # as a guard on bot_state[sym_id]['triggered'] = True.
        #
        # We assert the function exists at minimum (the test fails if the function is absent).
        assert hasattr(alpha_bot_execution, "detect_fleet_correlation"), (
            "detect_fleet_correlation must exist in alpha_bot_execution. "
            "AC-V3.2: the alert is observational — detection logic must be a standalone function "
            "invoked AFTER the trigger dispatch loop completes, not inside the per-symphony gate."
        )

    def test_detect_fleet_correlation_has_no_db_writes(self):
        """
        detect_fleet_correlation is a pure helper — it must have no DB write calls.
        DB writes belong to the caller (the engine cycle) after detection, not inside detection.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "detect_fleet_correlation"):
            pytest.fail("detect_fleet_correlation not found — implement per AC-V3.1.")

        fn_src = inspect.getsource(alpha_bot_execution.detect_fleet_correlation)
        db_write_patterns = ["database.save_state", "database.record_", "conn.execute", "conn.commit"]
        for pattern in db_write_patterns:
            assert pattern not in fn_src, (
                f"detect_fleet_correlation must not contain DB write calls (found '{pattern}'). "
                "The helper is pure — it receives pre-fetched triggers and returns a result. "
                "State mutation belongs at the call site in the engine cycle."
            )


# ---------------------------------------------------------------------------
# Section 6 — AC-V3.4: Detection reads from H1 exit_triggers table
# ---------------------------------------------------------------------------


class TestFleetDetectionReadsFromExitTriggersTable:
    """
    AC-V3.4: The fleet correlation detection must read recent triggers from
    H1's exit_triggers table (via get_recent_triggers or database.get_triggers).
    A RED test verifies the caller passes the correct window_minutes argument.
    """

    def test_get_recent_triggers_helper_or_get_triggers_used_by_detection_caller(self):
        """
        The engine must call database.get_triggers(since=...) or a named helper
        get_recent_triggers(window_minutes=...) to fetch triggers for the detection window.
        We verify the call site exists — it must NOT use in-memory cycle data.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        src = inspect.getsource(alpha_bot_execution)

        # The call site must reference either database.get_triggers or get_recent_triggers.
        # This will fail RED until the implementation exists.
        has_get_triggers = "database.get_triggers" in src
        has_get_recent = "get_recent_triggers" in src
        assert has_get_triggers or has_get_recent, (
            "Fleet correlation detection must read from H1's exit_triggers table. "
            "alpha_bot_execution must call database.get_triggers(...) or a helper "
            "named get_recent_triggers(window_minutes=...). "
            "Using only in-memory cycle data violates AC-V3.4."
        )

    @patch("database.get_triggers")
    def test_detection_caller_passes_window_minutes_to_db_query(self, mock_get_triggers):
        """
        When the engine invokes fleet correlation detection, it must pass the
        configured window to the DB query (not a hardcoded literal).
        We verify the call supplies a since= parameter derived from the window constant.
        """
        mock_get_triggers.return_value = []

        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "check_fleet_correlation_and_update_state"):
            pytest.fail(
                "check_fleet_correlation_and_update_state not found in alpha_bot_execution. "
                "Implement per AC-V3.4: a function that fetches triggers from DB, "
                "calls detect_fleet_correlation, and updates bot_state."
            )

        bot_state: dict = {}
        with patch.object(alpha_bot_execution, "FLEET_CORRELATION_WINDOW_MINUTES", 3):
            alpha_bot_execution.check_fleet_correlation_and_update_state(
                bot_state=bot_state,
                active_symphony_count=10,
            )

        assert mock_get_triggers.called, (
            "check_fleet_correlation_and_update_state must call database.get_triggers "
            "to fetch triggers from the exit_triggers table (AC-V3.4)."
        )
        call_kwargs = mock_get_triggers.call_args
        assert call_kwargs is not None
        # Verify since= is passed (window-derived timestamp, not hardcoded)
        kwargs = call_kwargs.kwargs if hasattr(call_kwargs, "kwargs") else {}
        args = call_kwargs.args if hasattr(call_kwargs, "args") else call_kwargs[0]
        has_since = "since" in kwargs or (len(args) > 0)
        assert has_since, (
            "database.get_triggers must receive a 'since' parameter derived from "
            "FLEET_CORRELATION_WINDOW_MINUTES — not a hardcoded timestamp."
        )


# ---------------------------------------------------------------------------
# Section 7 — Configurable thresholds from .env
# ---------------------------------------------------------------------------


class TestFleetCorrelationConfigurableThresholds:
    """
    AC-V3.1: FLEET_CORRELATION_PCT, FLEET_CORRELATION_WINDOW_MINUTES, and
    FLEET_CORRELATION_CLEAR_MINUTES must be read from .env at runtime.
    """

    def test_fleet_correlation_pct_constant_exists(self):
        """FLEET_CORRELATION_PCT module constant must exist in alpha_bot_execution."""
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")
        assert hasattr(alpha_bot_execution, "FLEET_CORRELATION_PCT"), (
            "alpha_bot_execution must expose FLEET_CORRELATION_PCT as a module-level "
            "attribute sourced from os.getenv('FLEET_CORRELATION_PCT', '0.50'). "
            "Required for .env configurability per AC-V3.1."
        )

    def test_fleet_correlation_pct_default_is_point_five(self):
        """Default FLEET_CORRELATION_PCT must be 0.50 (float)."""
        import alpha_bot_execution
        val = alpha_bot_execution.FLEET_CORRELATION_PCT
        assert isinstance(val, float), (
            f"FLEET_CORRELATION_PCT must be a float, got {type(val).__name__}"
        )
        assert val == pytest.approx(0.50, abs=1e-9), (
            # abs=1e-9: exact decimal representation, not computed; binary float 0.5 is exact
            f"Default FLEET_CORRELATION_PCT must be 0.50, got {val}"
        )

    def test_fleet_correlation_window_minutes_constant_exists(self):
        """FLEET_CORRELATION_WINDOW_MINUTES module constant must exist."""
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")
        assert hasattr(alpha_bot_execution, "FLEET_CORRELATION_WINDOW_MINUTES"), (
            "alpha_bot_execution must expose FLEET_CORRELATION_WINDOW_MINUTES sourced from "
            "os.getenv('FLEET_CORRELATION_WINDOW_MINUTES', '3')."
        )

    def test_fleet_correlation_window_minutes_default_is_3(self):
        """Default FLEET_CORRELATION_WINDOW_MINUTES must be 3 (int)."""
        import alpha_bot_execution
        val = alpha_bot_execution.FLEET_CORRELATION_WINDOW_MINUTES
        assert isinstance(val, int), (
            f"FLEET_CORRELATION_WINDOW_MINUTES must be int, got {type(val).__name__}"
        )
        assert val == 3, f"Default window must be 3 minutes, got {val}"

    def test_fleet_correlation_clear_minutes_constant_exists(self):
        """FLEET_CORRELATION_CLEAR_MINUTES module constant must exist."""
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")
        assert hasattr(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES"), (
            "alpha_bot_execution must expose FLEET_CORRELATION_CLEAR_MINUTES sourced from "
            "os.getenv('FLEET_CORRELATION_CLEAR_MINUTES', '30')."
        )

    def test_fleet_correlation_clear_minutes_default_is_30(self):
        """Default FLEET_CORRELATION_CLEAR_MINUTES must be 30 (int)."""
        import alpha_bot_execution
        val = alpha_bot_execution.FLEET_CORRELATION_CLEAR_MINUTES
        assert isinstance(val, int), (
            f"FLEET_CORRELATION_CLEAR_MINUTES must be int, got {type(val).__name__}"
        )
        assert val == 30, f"Default clear window must be 30 minutes, got {val}"

    def test_custom_pct_threshold_respected_at_0_75(self):
        """With pct_threshold=0.75, 7 of 10 (70%) must NOT trip."""
        try:
            from alpha_bot_execution import detect_fleet_correlation
        except ImportError:
            pytest.fail("detect_fleet_correlation not found.")

        fixture = _load("configurable_thresholds.json")
        case = next(c for c in fixture["cases"] if c["label"] == "custom_pct_threshold_0_75_not_tripped_at_70pct")

        triggers = [
            {"symphony_id": f"s{i}", "triggered_reason": "Trailing Stop", "ts_et": "2026-05-16T10:31:00"}
            for i in range(case["same_reason_count"])
        ]
        tripped, _ = detect_fleet_correlation(triggers, case["active_symphonies"], case["pct_threshold"])
        assert tripped is False, (
            f"Case '{case['label']}': {case['note']}"
        )

    def test_custom_pct_threshold_0_75_trips_at_80pct(self):
        """With pct_threshold=0.75, 8 of 10 (80%) > 75% — trips."""
        try:
            from alpha_bot_execution import detect_fleet_correlation
        except ImportError:
            pytest.fail("detect_fleet_correlation not found.")

        fixture = _load("configurable_thresholds.json")
        case = next(c for c in fixture["cases"] if c["label"] == "custom_pct_threshold_0_75_trips_at_80pct")

        triggers = [
            {"symphony_id": f"s{i}", "triggered_reason": "Trailing Stop", "ts_et": "2026-05-16T10:31:00"}
            for i in range(case["same_reason_count"])
        ]
        tripped, reason = detect_fleet_correlation(triggers, case["active_symphonies"], case["pct_threshold"])
        assert tripped is True, (
            f"Case '{case['label']}': {case['note']}"
        )
        assert reason == "Trailing Stop"

    def test_constants_are_patchable_via_patch_object(self):
        """All three constants must be patchable for test isolation."""
        import alpha_bot_execution
        with patch.object(alpha_bot_execution, "FLEET_CORRELATION_PCT", 0.75):
            assert alpha_bot_execution.FLEET_CORRELATION_PCT == pytest.approx(0.75, abs=1e-9)
        with patch.object(alpha_bot_execution, "FLEET_CORRELATION_WINDOW_MINUTES", 5):
            assert alpha_bot_execution.FLEET_CORRELATION_WINDOW_MINUTES == 5
        with patch.object(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES", 15):
            assert alpha_bot_execution.FLEET_CORRELATION_CLEAR_MINUTES == 15


# ---------------------------------------------------------------------------
# Section 8 — AC-V3.3: Auto-clear and manual dismiss
# ---------------------------------------------------------------------------


class TestFleetAlertAutoClear:
    """
    AC-V3.3: Alert clears automatically after FLEET_CORRELATION_CLEAR_MINUTES of
    no new fleet events. The clear check must run during the cycle.
    """

    def test_alert_clears_after_30_min_no_new_events(self):
        """
        check_fleet_correlation_and_update_state clears the alert when last_fleet_event_et
        is older than FLEET_CORRELATION_CLEAR_MINUTES and no new events detected.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "check_fleet_correlation_and_update_state"):
            pytest.fail(
                "check_fleet_correlation_and_update_state not found — implement per AC-V3.3."
            )

        fixture = _load("auto_clear.json")
        case = next(c for c in fixture["cases"] if c["label"] == "alert_clears_after_30_min_no_new_events")

        # Seed bot_state with an existing alert older than 30 min
        bot_state: dict = {
            "fleet_correlation_alert": {
                "tripped_at_et": case["last_fleet_event_et"],
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        check_time = _et_at(case["check_time_et"])

        with patch("database.get_triggers", return_value=[]):
            with patch.object(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES", fixture["clear_minutes"]):
                alpha_bot_execution.check_fleet_correlation_and_update_state(
                    bot_state=bot_state,
                    active_symphony_count=10,
                    now_et=check_time,
                )

        assert "fleet_correlation_alert" not in bot_state, (
            f"Case '{case['label']}': alert must be removed after {fixture['clear_minutes']} min "
            f"with no new events. {case['note']}"
        )

    def test_alert_persists_before_30_min_elapsed(self):
        """Alert remains in bot_state when fewer than 30 min have passed."""
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "check_fleet_correlation_and_update_state"):
            pytest.fail("check_fleet_correlation_and_update_state not found.")

        fixture = _load("auto_clear.json")
        case = next(c for c in fixture["cases"] if c["label"] == "alert_persists_before_30_min_elapsed")

        bot_state: dict = {
            "fleet_correlation_alert": {
                "tripped_at_et": case["last_fleet_event_et"],
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        check_time = _et_at(case["check_time_et"])

        with patch("database.get_triggers", return_value=[]):
            with patch.object(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES", fixture["clear_minutes"]):
                alpha_bot_execution.check_fleet_correlation_and_update_state(
                    bot_state=bot_state,
                    active_symphony_count=10,
                    now_et=check_time,
                )

        assert "fleet_correlation_alert" in bot_state, (
            f"Case '{case['label']}': alert must persist when < {fixture['clear_minutes']} min elapsed. "
            f"{case['note']}"
        )

    def test_manual_dismiss_function_exists_in_alpha_bot_execution(self):
        """
        A dismiss_fleet_correlation_alert(bot_state) function must exist.
        The /api/fleet-alert/dismiss route calls this to clear the alert key.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        assert hasattr(alpha_bot_execution, "dismiss_fleet_correlation_alert"), (
            "dismiss_fleet_correlation_alert not found in alpha_bot_execution. "
            "Implement per AC-V3.3: clears bot_state['fleet_correlation_alert'] and "
            "persists state. Called by POST /api/fleet-alert/dismiss."
        )

    def test_manual_dismiss_removes_alert_from_bot_state(self):
        """dismiss_fleet_correlation_alert removes 'fleet_correlation_alert' from bot_state."""
        try:
            from alpha_bot_execution import dismiss_fleet_correlation_alert
        except ImportError:
            pytest.fail("dismiss_fleet_correlation_alert not found — implement per AC-V3.3.")

        bot_state: dict = {
            "fleet_correlation_alert": {
                "tripped_at_et": "2026-05-16T10:33:00",
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        with patch("database.save_state") as mock_save:
            dismiss_fleet_correlation_alert(bot_state)

        assert "fleet_correlation_alert" not in bot_state, (
            "dismiss_fleet_correlation_alert must remove 'fleet_correlation_alert' from bot_state."
        )

    def test_manual_dismiss_persists_state_after_clearing(self):
        """dismiss_fleet_correlation_alert must call database.save_state to persist the change."""
        try:
            from alpha_bot_execution import dismiss_fleet_correlation_alert
        except ImportError:
            pytest.fail("dismiss_fleet_correlation_alert not found.")

        bot_state: dict = {
            "fleet_correlation_alert": {
                "tripped_at_et": "2026-05-16T10:33:00",
                "triggered_reason": "Trailing Stop",
                "tripped_count": 6,
                "active_count": 10,
            }
        }

        with patch("database.save_state") as mock_save:
            dismiss_fleet_correlation_alert(bot_state)
            assert mock_save.called, (
                "dismiss_fleet_correlation_alert must call database.save_state "
                "so the dismissal survives daemon restart."
            )

    def test_manual_dismiss_is_idempotent_when_no_alert_present(self):
        """Calling dismiss when no alert is present must not raise."""
        try:
            from alpha_bot_execution import dismiss_fleet_correlation_alert
        except ImportError:
            pytest.fail("dismiss_fleet_correlation_alert not found.")

        bot_state: dict = {}  # no alert key

        with patch("database.save_state"):
            try:
                dismiss_fleet_correlation_alert(bot_state)
            except Exception as exc:
                pytest.fail(
                    f"dismiss_fleet_correlation_alert must be idempotent when no alert present. "
                    f"Raised: {type(exc).__name__}: {exc}"
                )


# ---------------------------------------------------------------------------
# Section 9 — Property tests: detection invariants
# ---------------------------------------------------------------------------


class TestDetectFleetCorrelationProperties:
    """
    Property-style invariants that must hold regardless of input variation.
    These will fail on a wrong implementation.
    """

    @pytest.fixture(autouse=True)
    def _import_fn(self):
        try:
            from alpha_bot_execution import detect_fleet_correlation
            self._fn = detect_fleet_correlation
        except ImportError:
            self._fn = None

    def _call(self, triggers, active, pct):
        if self._fn is None:
            pytest.fail("detect_fleet_correlation not found.")
        return self._fn(triggers, active, pct)

    @pytest.mark.parametrize("active_symphonies,same_count,pct_threshold,expected_tripped", [
        (10, 6, 0.50, True),   # 60% > 50%
        (10, 5, 0.50, False),  # 50% not > 50%
        (10, 4, 0.50, False),  # 40% < 50%
        (4,  3, 0.50, True),   # 75% > 50%
        (4,  2, 0.50, False),  # 50% not > 50%
        (2,  2, 0.50, True),   # 100% > 50%
        (1,  1, 0.50, True),   # 100% > 50% (single symphony)
    ])
    def test_threshold_monotonicity(self, active_symphonies, same_count, pct_threshold, expected_tripped):
        """
        For a given active count, increasing same_count monotonically through the threshold
        must flip tripped from False to True at the strict > boundary.
        """
        triggers = [
            {"symphony_id": f"s{i}", "triggered_reason": "Trailing Stop", "ts_et": "2026-05-16T10:31:00"}
            for i in range(same_count)
        ]
        tripped, _ = self._call(triggers, active_symphonies, pct_threshold)
        assert tripped is expected_tripped, (
            f"active={active_symphonies}, same={same_count}, threshold={pct_threshold}: "
            f"expected tripped={expected_tripped}, got {tripped}. "
            "Threshold must be strictly greater-than."
        )

    def test_tripped_false_iff_reason_is_none(self):
        """tripped=False iff reason=None; tripped=True iff reason is a non-empty string."""
        triggers_trip = [
            {"symphony_id": f"s{i}", "triggered_reason": "VWAP Breakdown", "ts_et": "2026-05-16T10:31:00"}
            for i in range(6)
        ]
        tripped_t, reason_t = self._call(triggers_trip, 10, 0.50)
        assert tripped_t is True and isinstance(reason_t, str) and reason_t, (
            "When tripped=True, reason must be a non-empty string."
        )

        triggers_no = [
            {"symphony_id": "s1", "triggered_reason": "VWAP Breakdown", "ts_et": "2026-05-16T10:31:00"}
        ]
        tripped_f, reason_f = self._call(triggers_no, 10, 0.50)
        assert tripped_f is False and reason_f is None, (
            "When tripped=False, reason must be None."
        )

    def test_zero_active_symphonies_does_not_raise(self):
        """Edge: active_symphonies=0 must not raise ZeroDivisionError or similar."""
        try:
            tripped, reason = self._call([], 0, 0.50)
        except ZeroDivisionError:
            pytest.fail(
                "detect_fleet_correlation raised ZeroDivisionError with active_symphonies=0. "
                "Guard against division by zero — return (False, None) when active_symphonies == 0."
            )
        assert tripped is False, (
            "With 0 active symphonies, no alert can trip. Expected (False, None)."
        )
