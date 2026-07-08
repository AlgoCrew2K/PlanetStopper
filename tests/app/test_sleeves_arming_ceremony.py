"""
RED tests — Managed Sleeves P3: the arming ladder (AC-13, AC-14).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard):

    POST /api/sleeves/<sleeve_id>/rules/<rule_id>/arm
        body: {"target_mode": "PAPER"}
        SHADOW -> PAPER only. Gated on AC-13: "no arm-on-faith" — the rule
        must already have >=1 recorded database.sleeve_rule_fires row with
        mode_at_fire == "SHADOW" (database.get_sleeve_rule_fires(rule_id=...)).
        Zero recorded shadow fires -> 400, rule mode unchanged. A recorded
        fire present -> 200, rule mode becomes "PAPER".

    POST /api/sleeves/<sleeve_id>/arm-live
        body: {"confirm_sleeve_id": <int>, "confirm_phrase": "ARM LIVE TRADING"}
        The panic-flow ceremony (AC-14), modeled precisely on the sell_account
        6-gate chain (app.py — see docstring correction below):
          gate 1: confirm_sleeve_id required                       -> 400
          gate 2: confirm_sleeve_id == sleeve_id (path param)       -> 400
          gate 3: confirm_phrase required                          -> 400
          gate 4: confirm_phrase == "ARM LIVE TRADING" exactly      -> 400
          gate 5: ALPACA_LIVE_KEY and ALPACA_LIVE_SECRET both       -> 400
                  present and non-empty in the .env
          gate 6: SLEEVE_LIVE_EXECUTION truthy in the .env          -> 400
        Every invocation that passes gates 1-4 (a genuine ceremony attempt,
        as opposed to a malformed/incomplete request) fires a Discord alert
        + an ERROR-level log entry, mirroring sell_account's "audit always
        fires" contract — regardless of whether gates 5/6 subsequently block
        the arm. All six gates passing -> 200, sleeve status becomes "LIVE".

    Stale-citation correction (per s3-review's pre-read flag): the plan and
    DECISIONS.md cite app.py:2764-2845 for the sell_account ceremony model;
    the function has since moved to app.py:3176 (sell_account). The GATE
    SHAPE this file pins is unchanged by that move.

CSRF and dashboard auth are globally disabled for the test suite (autouse
fixtures in tests/conftest.py); DB is isolated per-test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app as app_module
import database

_ARM_LIVE_PHRASE = "ARM LIVE TRADING"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _base_env(*, live_keys: bool = False, sleeve_live_execution: bool = False) -> dict:
    env = {
        "ALPACA_KEY": "test-paper-key",
        "ALPACA_SECRET": "test-paper-secret",
    }
    if live_keys:
        env["ALPACA_LIVE_KEY"] = "test-live-key"
        env["ALPACA_LIVE_SECRET"] = "test-live-secret"
    if sleeve_live_execution:
        env["SLEEVE_LIVE_EXECUTION"] = "True"
    return env


# ---------------------------------------------------------------------------
# AC-13: SHADOW -> PAPER requires >=1 recorded shadow evaluation
# ---------------------------------------------------------------------------


class TestShadowToPaperArming:
    def test_arm_to_paper_with_zero_shadow_fires_returns_400(self, client):
        sleeve_id = database.create_sleeve("arm-test-sleeve-1", 5000.0, envelope_json="{}")
        rule_id = database.create_sleeve_rule(sleeve_id, "arm-test-rule-1", json_doc="{}")

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
        )

        assert resp.status_code == 400, (
            f"AC-13: arming to PAPER with zero recorded shadow fires must be "
            f"rejected (no arm-on-faith); got {resp.status_code}"
        )
        stored = database.get_sleeve_rule(rule_id)
        assert stored["mode"] == "SHADOW", "the rule must remain SHADOW when the arm is rejected"

    def test_arm_to_paper_with_recorded_shadow_fire_succeeds(self, client):
        sleeve_id = database.create_sleeve("arm-test-sleeve-2", 5000.0, envelope_json="{}")
        rule_id = database.create_sleeve_rule(sleeve_id, "arm-test-rule-2", json_doc="{}")
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"}
        )

        assert resp.status_code == 200, (
            f"AC-13: a rule with >=1 recorded shadow fire must be armable to "
            f"PAPER; got {resp.status_code}: {resp.get_data(as_text=True)[:300]!r}"
        )
        stored = database.get_sleeve_rule(rule_id)
        assert stored["mode"] == "PAPER", (
            f"expected mode PAPER after arming; got {stored['mode']!r}"
        )

    def test_arm_to_paper_confined_to_paper_host_structurally(self, client, monkeypatch):
        """AC-13: PAPER orders are structurally confined to paper-api.alpaca.markets.

        This is enforced by sleeves.alpaca_orders.resolve_host (P1 invariant 3)
        — arming to PAPER must never itself set any live-mode flag. We assert
        the route never touches SLEEVE_LIVE_EXECUTION or ALPACA_LIVE_* at all.
        """
        sleeve_id = database.create_sleeve("arm-test-sleeve-3", 5000.0, envelope_json="{}")
        rule_id = database.create_sleeve_rule(sleeve_id, "arm-test-rule-3", json_doc="{}")
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
        )

        set_key_mock = MagicMock()
        monkeypatch.setattr(app_module, "set_key", set_key_mock)

        client.post(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"})

        for call in set_key_mock.call_args_list:
            args_repr = repr(call)
            assert "SLEEVE_LIVE_EXECUTION" not in args_repr and "ALPACA_LIVE" not in args_repr, (
                "arming a rule to PAPER must never write any live-mode env key"
            )


# ---------------------------------------------------------------------------
# AC-14: PAPER -> LIVE ceremony (impossible-by-construction without live keys)
# ---------------------------------------------------------------------------


class TestPaperToLiveCeremony:
    def test_missing_confirm_sleeve_id_returns_400(self, client, monkeypatch):
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-1", 5000.0, envelope_json="{}")
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _base_env())

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live", json={"confirm_phrase": _ARM_LIVE_PHRASE}
        )
        assert resp.status_code == 400
        body = resp.get_json()
        message = str(body.get("message") or body.get("reason") or "")
        assert "confirm_sleeve_id" in message, "400 body must name the missing field"

    def test_mismatched_confirm_sleeve_id_returns_400(self, client, monkeypatch):
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-2", 5000.0, envelope_json="{}")
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _base_env())

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live",
            json={"confirm_sleeve_id": sleeve_id + 999, "confirm_phrase": _ARM_LIVE_PHRASE},
        )
        assert resp.status_code == 400

    def test_missing_confirm_phrase_returns_400(self, client, monkeypatch):
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-3", 5000.0, envelope_json="{}")
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _base_env())

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live", json={"confirm_sleeve_id": sleeve_id}
        )
        assert resp.status_code == 400

    def test_wrong_confirm_phrase_returns_400(self, client, monkeypatch):
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-4", 5000.0, envelope_json="{}")
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: _base_env())

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live",
            json={"confirm_sleeve_id": sleeve_id, "confirm_phrase": "arm live trading"},
        )
        assert resp.status_code == 400, "confirm_phrase match must be exact and case-sensitive"

    def test_correct_ceremony_but_no_live_keys_is_blocked_structurally(self, client, monkeypatch):
        """The load-bearing AC-14 invariant: even a PERFECT ceremony (correct
        sleeve id + exact phrase) must be rejected when live keys are absent —
        LIVE arming is impossible BY CONSTRUCTION, not merely by a UI gate."""
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-5", 5000.0, envelope_json="{}")
        monkeypatch.setattr(
            app_module, "dotenv_values", lambda *_a, **_k: _base_env(live_keys=False)
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live",
            json={"confirm_sleeve_id": sleeve_id, "confirm_phrase": _ARM_LIVE_PHRASE},
        )

        assert resp.status_code == 400, (
            f"a perfect ceremony without ALPACA_LIVE_KEY/ALPACA_LIVE_SECRET "
            f"present must still be rejected (AC-14 impossible-by-construction); "
            f"got {resp.status_code}"
        )
        sleeve_row = database.get_sleeve(sleeve_id)
        assert sleeve_row["status"] != "LIVE", (
            "the sleeve must NOT reach LIVE status without live keys"
        )

    def test_correct_ceremony_with_live_keys_but_no_sleeve_live_execution_flag_is_blocked(
        self, client, monkeypatch
    ):
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-6", 5000.0, envelope_json="{}")
        monkeypatch.setattr(
            app_module,
            "dotenv_values",
            lambda *_a, **_k: _base_env(live_keys=True, sleeve_live_execution=False),
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live",
            json={"confirm_sleeve_id": sleeve_id, "confirm_phrase": _ARM_LIVE_PHRASE},
        )

        assert resp.status_code == 400, (
            "live keys present but SLEEVE_LIVE_EXECUTION unset must still block LIVE arming"
        )
        sleeve_row = database.get_sleeve(sleeve_id)
        assert sleeve_row["status"] != "LIVE"

    def test_all_gates_passing_arms_the_sleeve_live(self, client, monkeypatch):
        sleeve_id = database.create_sleeve("live-ceremony-sleeve-7", 5000.0, envelope_json="{}")
        monkeypatch.setattr(
            app_module,
            "dotenv_values",
            lambda *_a, **_k: _base_env(live_keys=True, sleeve_live_execution=True),
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/arm-live",
            json={"confirm_sleeve_id": sleeve_id, "confirm_phrase": _ARM_LIVE_PHRASE},
        )

        assert resp.status_code == 200, (
            f"all six gates satisfied must arm the sleeve LIVE; got "
            f"{resp.status_code}: {resp.get_data(as_text=True)[:300]!r}"
        )
        sleeve_row = database.get_sleeve(sleeve_id)
        assert sleeve_row["status"] == "LIVE"

    def test_audit_fires_on_a_genuine_ceremony_attempt_even_when_blocked_by_missing_keys(
        self, client, monkeypatch
    ):
        """A genuine ceremony attempt (correct id + phrase) must be audited
        (Discord + ERROR log) even when subsequently blocked by missing live
        keys — mirrors sell_account's 'audit fires regardless of live_mode'
        contract. Only a MALFORMED request (missing/mismatched confirm
        fields) is exempt from the audit requirement."""
        import logging

        sleeve_id = database.create_sleeve("live-ceremony-sleeve-8", 5000.0, envelope_json="{}")
        monkeypatch.setattr(
            app_module, "dotenv_values", lambda *_a, **_k: _base_env(live_keys=False)
        )
        monkeypatch.setattr(app_module.requests, "post", MagicMock())

        log_records: list[logging.LogRecord] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                log_records.append(record)

        handler = _CapturingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        original_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)
        try:
            client.post(
                f"/api/sleeves/{sleeve_id}/arm-live",
                json={"confirm_sleeve_id": sleeve_id, "confirm_phrase": _ARM_LIVE_PHRASE},
            )
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(original_level)

        error_records = [r for r in log_records if r.levelno >= logging.ERROR]
        assert error_records, (
            "a genuine LIVE-arm ceremony attempt must emit an ERROR-level audit "
            "log entry even when ultimately blocked by missing live keys"
        )
