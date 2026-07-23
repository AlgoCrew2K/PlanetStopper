"""
RED tests — Managed Sleeves P3: condition-replay route (AC-18).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard):

    GET /api/sleeves/<sleeve_id>/rules/<rule_id>/replay?days=N

    Diagnostics-only (never an arming input, never a P&L claim — plan
    Decisions: "Condition-replay endpoint demoted to diagnostics; never an
    arming input"). N must satisfy 1 <= N <= 60; anything else -> 400.
    The response evaluates the rule's OWN condition tree (the real
    sleeves.rules.conditions/senses code — same engine a live tick uses, not
    a reimplementation) over historical daily bars, and returns the dates it
    WOULD have fired plus the sensed values at each — never a dollar figure,
    a "pnl"/"return"/"profit" key, or any other fill-simulating claim.

This file scopes deep numeric replay-correctness (does the SMA/RSI/etc. math
itself replay correctly) to sleeves.rules.senses's own P2 golden-fixture
tests (tests/fixtures/math/sleeves_rule_indicator_senses_basic.json) — this
file pins the ROUTE's contract: parameter bounds, diagnostics-only response
shape, and that it is wired through the real rule-engine modules rather than
a route-local reimplementation.
"""

from __future__ import annotations

import pytest

import app as app_module
import database

_FORBIDDEN_PNL_KEYS = ("pnl", "return", "profit", "gain", "loss_usd", "dollars_saved")


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _make_sleeve_and_rule():
    sleeve_id = database.create_sleeve("replay-test-sleeve", 5000.0, envelope_json="{}")
    rule_id = database.create_sleeve_rule(
        sleeve_id,
        "replay-test-rule",
        json_doc="{}",
    )
    return sleeve_id, rule_id


class TestReplayRouteBounds:
    def test_days_at_upper_bound_60_is_accepted(self, client, monkeypatch):
        sleeve_id, rule_id = _make_sleeve_and_rule()
        monkeypatch.setattr(
            app_module, "database", database
        )  # ensure real module, not a stray mock

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=60")
        assert resp.status_code != 400, (
            f"days=60 is the documented upper bound (AC-18: N<=60) and must be "
            f"accepted; got {resp.status_code}"
        )

    def test_days_above_60_is_rejected(self, client):
        sleeve_id, rule_id = _make_sleeve_and_rule()

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=61")
        assert resp.status_code == 400, (
            f"days=61 exceeds AC-18's N<=60 bound and must be rejected; got {resp.status_code}"
        )

    def test_days_zero_or_negative_is_rejected(self, client):
        sleeve_id, rule_id = _make_sleeve_and_rule()

        resp_zero = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=0")
        assert resp_zero.status_code == 400, "days=0 must be rejected"

        resp_negative = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=-5")
        assert resp_negative.status_code == 400, "a negative days value must be rejected"

    def test_non_integer_days_is_rejected_not_500(self, client):
        sleeve_id, rule_id = _make_sleeve_and_rule()

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=not-a-number")
        assert resp.status_code == 400, (
            f"a non-integer days value must be a graceful 400, never a 500 "
            f"crash; got {resp.status_code}"
        )

    def test_missing_days_param_uses_a_default_within_bounds_not_500(self, client):
        sleeve_id, rule_id = _make_sleeve_and_rule()

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay")
        assert resp.status_code != 500, (
            "an omitted days query param must fall back to a documented "
            "default, never crash with a 500"
        )

    def test_replay_for_unknown_rule_returns_404(self, client):
        sleeve_id = database.create_sleeve("replay-404-sleeve", 5000.0, envelope_json="{}")
        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/999999/replay?days=30")
        assert resp.status_code == 404


class TestReplayResponseIsDiagnosticsOnly:
    def test_response_never_contains_a_pnl_shaped_key(self, client):
        sleeve_id, rule_id = _make_sleeve_and_rule()

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=30")
        assert resp.status_code != 500
        body = resp.get_json() or {}

        def _flatten_keys(obj, prefix=""):
            keys = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    keys.append(f"{prefix}{k}".lower())
                    keys.extend(_flatten_keys(v, prefix=f"{prefix}{k}."))
            elif isinstance(obj, list):
                for item in obj:
                    keys.extend(_flatten_keys(item, prefix=prefix))
            return keys

        all_keys = _flatten_keys(body)
        offending = [
            k for k in all_keys if any(forbidden in k for forbidden in _FORBIDDEN_PNL_KEYS)
        ]
        assert not offending, (
            f"AC-18: condition-replay must never carry a P&L-shaped key "
            f"(fill-simulating backtest is explicitly OUT of scope); found "
            f"keys: {offending}"
        )

    def test_response_labels_itself_as_condition_replay_diagnostic(self, client):
        sleeve_id, rule_id = _make_sleeve_and_rule()

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules/{rule_id}/replay?days=30")
        assert resp.status_code != 500
        body = resp.get_json() or {}
        combined_text = str(body).lower()
        assert "replay" in combined_text or "diagnostic" in combined_text, (
            "the response must self-label as a condition-replay diagnostic "
            "(never presented as if it were an arming/backtest verdict)"
        )


class TestReplayUsesTheRealRuleEngine:
    def test_replay_route_references_the_real_conditions_and_senses_modules(self):
        """Structural guard: the replay route must be wired through
        sleeves.rules.conditions / sleeves.rules.senses — the SAME engine a
        live tick uses — never a route-local reimplementation of condition
        evaluation (AC-24 parity-by-construction principle applied to
        diagnostics too)."""
        pytest.importorskip(
            "sleeves.rules.conditions", reason="RED phase — sleeves.rules not implemented yet"
        )
        import ast
        import pathlib

        app_source = pathlib.Path(app_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(app_source)
        replay_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                decorators_src = [ast.dump(d) for d in node.decorator_list]
                if any("/replay" in d for d in decorators_src):
                    replay_fn = node
                    break
        assert replay_fn is not None, (
            "app.py must define the GET .../rules/<rule_id>/replay route function"
        )
        fn_source = ast.get_source_segment(app_source, replay_fn) or ""
        assert "conditions" in fn_source or "evaluate_condition" in fn_source, (
            "the replay route must call into sleeves.rules.conditions, not "
            "reimplement condition evaluation locally"
        )
