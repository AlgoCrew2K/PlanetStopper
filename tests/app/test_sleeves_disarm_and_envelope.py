"""
RED tests — Managed Sleeves P3: disarm (AC-12) and envelope widen/narrow (AC-3).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard):

    POST /api/sleeves/<sleeve_id>/disarm
        One-click, per-sleeve kill switch (AC-12):
          - every OPEN (non-terminal) sleeve_orders row for the sleeve is
            cancelled via sleeves.alpaca_orders.cancel_order; a FILLED order
            is left alone (nothing to cancel).
          - positions and broker-side stops are NEVER touched — no call to
            any position-closing/liquidating function.
          - the sleeve's status AND every one of its rules' mode revert to
            "SHADOW" — autonomy stops; re-arming requires going through the
            arm ceremony again (this file does not re-test AC-13/AC-14's own
            gates, only that disarm actually reverts state so a subsequent
            arm attempt is required).

    POST /api/sleeves/<sleeve_id>/envelope
        body: {"envelope": {...}}                      -- narrow: applies immediately
        body: {"envelope": {...}, "confirm_sleeve_id": <int>,
               "confirm_phrase": "WIDEN ENVELOPE"}      -- widen: same ceremony shape
        AC-3: sleeves.envelope.is_envelope_widened(old, new) decides which path
        applies. A widen submitted WITHOUT the ceremony fields (or with a wrong
        phrase) is rejected and the envelope is NOT changed. A narrow (or an
        unchanged envelope) applies immediately with no ceremony fields required.

CSRF and dashboard auth are globally disabled for the test suite (autouse
fixtures in tests/conftest.py); DB is isolated per-test.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import database

_WIDEN_PHRASE = "WIDEN ENVELOPE"

_NARROW_ENVELOPE = {
    "allowlist": ["SPY"],
    "max_position_pct": 0.10,
    "max_order_usd": 500.0,
    "max_daily_turnover_usd": 1000.0,
    "long_only": True,
}

_WIDER_ENVELOPE = {
    "allowlist": ["SPY", "QQQ"],  # added ticker -> strictly less restrictive
    "max_position_pct": 0.50,
    "max_order_usd": 5000.0,
    "max_daily_turnover_usd": 10000.0,
    "long_only": True,
}


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-12: disarm
# ---------------------------------------------------------------------------


class TestDisarmSleeve:
    def test_disarm_cancels_open_orders_but_never_closes_positions(self, client):
        sleeve_id = database.create_sleeve("disarm-test-sleeve-1", 5000.0, envelope_json="{}")

        open_client_order_id = "disarm-test-open-order"
        database.insert_sleeve_order(
            client_order_id=open_client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=5.0,
            status="accepted",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(open_client_order_id, "alpaca-open-order-id-1")

        filled_client_order_id = "disarm-test-filled-order"
        database.insert_sleeve_order(
            client_order_id=filled_client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=3.0,
            status="filled",
            reserved_price=500.0,
        )
        database.attach_alpaca_order_id(filled_client_order_id, "alpaca-filled-order-id-1")

        with (
            patch("sleeves.alpaca_orders.cancel_order") as mock_cancel,
            patch("sleeves.alpaca_orders.close_position", create=True) as mock_close,
            patch("sleeves.alpaca_orders.liquidate_position", create=True) as mock_liquidate,
        ):
            resp = client.post(f"/api/sleeves/{sleeve_id}/disarm")

        assert resp.status_code == 200, (
            f"disarm on a sleeve with open orders must succeed; got "
            f"{resp.status_code}: {resp.get_data(as_text=True)[:300]!r}"
        )

        cancelled_ids = {
            c.kwargs.get("order_id") or (c.args[0] if c.args else None)
            for c in mock_cancel.call_args_list
        }
        assert "alpaca-open-order-id-1" in cancelled_ids, (
            "the open (non-terminal) order must be cancelled by disarm"
        )
        assert "alpaca-filled-order-id-1" not in cancelled_ids, (
            "an already-filled order must NOT be cancelled — nothing to cancel"
        )

        mock_close.assert_not_called()
        mock_liquidate.assert_not_called()

    def test_disarm_reverts_sleeve_and_rule_modes_to_shadow(self, client):
        sleeve_id = database.create_sleeve("disarm-test-sleeve-2", 5000.0, envelope_json="{}")
        rule_id = database.create_sleeve_rule(sleeve_id, "disarm-test-rule", json_doc="{}")
        database.insert_sleeve_rule_fire(
            rule_id=rule_id,
            sleeve_id=sleeve_id,
            action="buy",
            rule_class="ENTRY",
            mode_at_fire="SHADOW",
        )
        client.post(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/arm", json={"target_mode": "PAPER"})
        assert database.get_sleeve_rule(rule_id)["mode"] == "PAPER", (
            "fixture setup sanity: the rule must be PAPER before testing disarm"
        )

        with patch("sleeves.alpaca_orders.cancel_order"):
            resp = client.post(f"/api/sleeves/{sleeve_id}/disarm")
        assert resp.status_code == 200

        assert database.get_sleeve(sleeve_id)["status"] == "SHADOW", (
            "disarm must revert the sleeve's own status to SHADOW"
        )
        assert database.get_sleeve_rule(rule_id)["mode"] == "SHADOW", (
            "disarm must revert every one of the sleeve's rules to SHADOW mode — "
            "re-arming requires going through the arm ceremony again"
        )

    def test_disarm_on_sleeve_with_no_open_orders_is_a_no_op_success(self, client):
        sleeve_id = database.create_sleeve("disarm-test-sleeve-3", 5000.0, envelope_json="{}")

        with patch("sleeves.alpaca_orders.cancel_order") as mock_cancel:
            resp = client.post(f"/api/sleeves/{sleeve_id}/disarm")

        assert resp.status_code == 200
        mock_cancel.assert_not_called()


# ---------------------------------------------------------------------------
# AC-3: envelope widen requires re-ceremony; narrow is immediate
# ---------------------------------------------------------------------------


class TestEnvelopeWidenNarrow:
    def test_narrow_envelope_applies_immediately_with_no_ceremony(self, client):
        sleeve_id = database.create_sleeve(
            "envelope-test-sleeve-1", 5000.0, envelope_json=json.dumps(_WIDER_ENVELOPE)
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/envelope", json={"envelope": _NARROW_ENVELOPE}
        )

        assert resp.status_code == 200, (
            f"a narrowing envelope change must apply immediately with no "
            f"ceremony fields; got {resp.status_code}: "
            f"{resp.get_data(as_text=True)[:300]!r}"
        )
        stored = json.loads(database.get_sleeve(sleeve_id)["envelope_json"])
        assert stored == _NARROW_ENVELOPE, "the narrower envelope must be persisted as sent"

    def test_widen_envelope_without_ceremony_fields_is_rejected(self, client):
        sleeve_id = database.create_sleeve(
            "envelope-test-sleeve-2", 5000.0, envelope_json=json.dumps(_NARROW_ENVELOPE)
        )

        resp = client.post(f"/api/sleeves/{sleeve_id}/envelope", json={"envelope": _WIDER_ENVELOPE})

        assert resp.status_code == 400, (
            f"AC-3: widening the envelope without the re-confirmation ceremony "
            f"must be rejected; got {resp.status_code}"
        )
        stored = json.loads(database.get_sleeve(sleeve_id)["envelope_json"])
        assert stored == _NARROW_ENVELOPE, "a rejected widen must never mutate the stored envelope"

    def test_widen_envelope_with_wrong_ceremony_phrase_is_rejected(self, client):
        sleeve_id = database.create_sleeve(
            "envelope-test-sleeve-3", 5000.0, envelope_json=json.dumps(_NARROW_ENVELOPE)
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/envelope",
            json={
                "envelope": _WIDER_ENVELOPE,
                "confirm_sleeve_id": sleeve_id,
                "confirm_phrase": "widen envelope",  # wrong case
            },
        )

        assert resp.status_code == 400
        stored = json.loads(database.get_sleeve(sleeve_id)["envelope_json"])
        assert stored == _NARROW_ENVELOPE

    def test_widen_envelope_with_correct_ceremony_succeeds(self, client):
        sleeve_id = database.create_sleeve(
            "envelope-test-sleeve-4", 5000.0, envelope_json=json.dumps(_NARROW_ENVELOPE)
        )

        resp = client.post(
            f"/api/sleeves/{sleeve_id}/envelope",
            json={
                "envelope": _WIDER_ENVELOPE,
                "confirm_sleeve_id": sleeve_id,
                "confirm_phrase": _WIDEN_PHRASE,
            },
        )

        assert resp.status_code == 200, (
            f"a correct widen ceremony must succeed; got {resp.status_code}: "
            f"{resp.get_data(as_text=True)[:300]!r}"
        )
        stored = json.loads(database.get_sleeve(sleeve_id)["envelope_json"])
        assert stored == _WIDER_ENVELOPE, "the widened envelope must be persisted after ceremony"

    def test_widen_uses_is_envelope_widened_to_classify_not_a_route_local_heuristic(self):
        """Structural guard: the route must decide widen-vs-narrow via
        sleeves.envelope.is_envelope_widened — never a route-local ad-hoc
        comparison that could silently drift from the P1 envelope semantics."""
        pytest.importorskip(
            "sleeves.envelope", reason="RED phase — sleeves.envelope not implemented yet"
        )
        import ast
        import pathlib

        app_source = pathlib.Path(app_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(app_source)
        envelope_route_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                decorators_src = [ast.dump(d) for d in node.decorator_list]
                if any("/envelope" in d for d in decorators_src):
                    envelope_route_fn = node
                    break
        assert envelope_route_fn is not None, (
            "app.py must define the POST /api/sleeves/<sleeve_id>/envelope route function"
        )
        fn_source = ast.get_source_segment(app_source, envelope_route_fn) or ""
        assert "is_envelope_widened" in fn_source, (
            "the envelope route must call sleeves.envelope.is_envelope_widened "
            "to classify widen vs narrow — not a local heuristic"
        )
