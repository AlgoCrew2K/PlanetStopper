"""
RED tests — Managed Sleeves P3: sleeve + rule CRUD routes (AC-1, AC-4, AC-6).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard):

    GET  /api/sleeves            -> {"sleeves": [<sleeve dict>, ...]}
    POST /api/sleeves             body: {"name": str, "capital_usd": float,
                                          "envelope": dict}
                                   -> 200 {"status": "success", "sleeve": {...}}
                                      (or "id"); sleeve starts in SHADOW status
                                      (database.create_sleeve's own contract) —
                                      missing name/capital_usd -> 400 naming
                                      the missing field.
    GET  /api/sleeves/<id>/rules -> {"rules": [<rule dict>, ...]}
    POST /api/sleeves/<id>/rules   body: a full rule doc (when/if/then/limits
                                   + name), validated via
                                   sleeves.rules.schema.validate_rule_doc ->
                                   400 with field-level errors on an invalid
                                   doc; a VALID doc is stored via
                                   database.create_sleeve_rule with mode
                                   forced to "SHADOW" regardless of any "mode"
                                   the client sent (AC-6: every rule is BORN
                                   in SHADOW; arming is a separate ceremony —
                                   a route must never let a client arm a rule
                                   at creation time).
                                   Unknown sleeve_id -> 404.

Routes are plain @app.route functions directly in app.py (matching every
other mutating route in this file, e.g. POST /api/settings) — this is also
why tests/app/test_dashboard_no_order_path.py's whole-app.py route scan
automatically covers these new routes without any test-file change (per the
P3 kickoff brief's "KNOWN AUTOMATIC SCAN" note).

CSRF and dashboard auth are globally disabled for the test suite by the
autouse fixtures in tests/conftest.py; DB is isolated per-test.
"""

from __future__ import annotations

import pytest

import app as app_module
import database


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _valid_rule_doc(**overrides) -> dict:
    """A schema-valid rule doc per sleeves.rules.schema.validate_rule_doc's
    documented contract (docs/generated/sleeves.md — AC-4/AC-7/AC-20)."""
    doc = {
        "name": "crud-test-rule",
        "when": {"symbol": "SPY"},
        "if": {
            "op": "compare",
            "sense": "sma_20",
            "comparator": ">",
            "value": 100.0,
        },
        "then": [
            {
                "type": "buy",
                "sizing": {"mode": "risk_pct", "risk_pct": 0.01},
                "stop_loss_pct": 0.05,
            }
        ],
        "limits": {
            "cooldown_sec": 3600,
            "max_fires_per_day": 2,
            "rearm_ticks": 3,
            "market_hours_only": True,
        },
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# Route-called-function existence (hasattr guard, per Testing Strategy)
# ---------------------------------------------------------------------------


class TestRouteCalledFunctionsExist:
    def test_database_sleeve_accessors_exist(self):
        for fn_name in (
            "create_sleeve",
            "get_all_sleeves",
            "get_sleeve",
            "create_sleeve_rule",
            "get_sleeve_rules_for_sleeve",
            "get_sleeve_rule",
        ):
            assert hasattr(database, fn_name) and callable(getattr(database, fn_name)), (
                f"database.{fn_name} must exist and be callable — the sleeve "
                f"CRUD routes call it directly."
            )

    def test_sleeves_rules_schema_validator_exists(self):
        pytest.importorskip(
            "sleeves.rules.schema", reason="RED phase — sleeves.rules.schema not implemented yet"
        )
        import sleeves.rules.schema as schema

        assert hasattr(schema, "validate_rule_doc") and callable(schema.validate_rule_doc), (
            "sleeves.rules.schema.validate_rule_doc must exist — the rule-create "
            "route must validate every incoming rule doc through it, never a "
            "route-local ad-hoc check."
        )


# ---------------------------------------------------------------------------
# POST /api/sleeves — create
# ---------------------------------------------------------------------------


class TestCreateSleeve:
    def test_create_sleeve_with_valid_body_returns_success_and_persists(self, client):
        payload = {"name": "crud-test-sleeve-1", "capital_usd": 5000.0, "envelope": {}}
        resp = client.post("/api/sleeves", json=payload)

        assert resp.status_code == 200, (
            f"POST /api/sleeves with a valid body must return 200; got "
            f"{resp.status_code}: {resp.get_data(as_text=True)[:300]!r}"
        )
        stored = database.get_sleeve_by_name(payload["name"])
        assert stored is not None, "the sleeve must be persisted via database.create_sleeve"
        assert stored["capital_usd"] == pytest.approx(payload["capital_usd"], rel=1e-9), (
            "persisted capital_usd must equal the request body's value, never "
            "a default/hardcoded figure."
        )
        assert stored["status"] == "SHADOW", (
            f"a newly created sleeve must start in SHADOW status (AC-1) — got {stored['status']!r}"
        )

    def test_create_sleeve_missing_name_returns_400(self, client):
        resp = client.post("/api/sleeves", json={"capital_usd": 1000.0})
        assert resp.status_code == 400, f"missing 'name' must return 400; got {resp.status_code}"
        body = resp.get_json()
        message = str(body.get("message") or body.get("reason") or "")
        assert "name" in message.lower(), (
            "400 body must name the missing field for operator clarity"
        )

    def test_create_sleeve_missing_capital_usd_returns_400(self, client):
        resp = client.post("/api/sleeves", json={"name": "no-capital-sleeve"})
        assert resp.status_code == 400, (
            f"missing 'capital_usd' must return 400; got {resp.status_code}"
        )
        body = resp.get_json()
        message = str(body.get("message") or body.get("reason") or "")
        assert "capital" in message.lower(), "400 body must name the missing capital_usd field"

    def test_create_sleeve_non_numeric_capital_usd_returns_400_not_500(self, client):
        resp = client.post(
            "/api/sleeves", json={"name": "bad-capital-sleeve", "capital_usd": "not-a-number"}
        )
        assert resp.status_code == 400, (
            f"a non-numeric capital_usd must be a graceful 400, never a 500 "
            f"crash; got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# GET /api/sleeves — list
# ---------------------------------------------------------------------------


class TestListSleeves:
    def test_list_sleeves_includes_created_sleeve(self, client):
        database.create_sleeve("list-test-sleeve", 2500.0, envelope_json="{}")

        resp = client.get("/api/sleeves")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "sleeves" in body, "response must carry a 'sleeves' list key"
        names = [s.get("name") for s in body["sleeves"]]
        assert "list-test-sleeve" in names, (
            f"the newly created sleeve must appear in GET /api/sleeves; got names={names}"
        )


# ---------------------------------------------------------------------------
# POST /api/sleeves/<id>/rules — create a rule (born SHADOW, schema-validated)
# ---------------------------------------------------------------------------


class TestCreateSleeveRule:
    def test_create_valid_rule_is_born_shadow_even_if_client_requests_paper(self, client):
        sleeve_id = database.create_sleeve("rule-crud-sleeve", 5000.0, envelope_json="{}")

        payload = _valid_rule_doc(mode="PAPER")  # client tries to arm at creation time
        resp = client.post(f"/api/sleeves/{sleeve_id}/rules", json=payload)

        assert resp.status_code == 200, (
            f"POST rules with a schema-valid doc must return 200; got "
            f"{resp.status_code}: {resp.get_data(as_text=True)[:300]!r}"
        )
        stored_rules = database.get_sleeve_rules_for_sleeve(sleeve_id)
        assert stored_rules, "the rule must be persisted via database.create_sleeve_rule"
        assert stored_rules[0]["mode"] == "SHADOW", (
            "AC-6: every rule must be BORN in SHADOW regardless of any 'mode' "
            f"field the client sent in the create payload; got "
            f"{stored_rules[0]['mode']!r}. Arming is a separate ceremony."
        )

    def test_create_rule_with_invalid_doc_returns_400_with_field_errors(self, client):
        sleeve_id = database.create_sleeve("rule-crud-invalid-sleeve", 5000.0, envelope_json="{}")

        # An entry (buy) action with NO declared exit is a documented schema
        # violation (schema.py's _validate_buy_exit_fields, AC-7).
        invalid_payload = _valid_rule_doc(
            then=[{"type": "buy", "sizing": {"mode": "risk_pct", "risk_pct": 0.01}}]
        )
        resp = client.post(f"/api/sleeves/{sleeve_id}/rules", json=invalid_payload)

        assert resp.status_code == 400, (
            f"a schema-invalid rule doc must return 400; got {resp.status_code}"
        )
        body = resp.get_json()
        assert body.get("errors") or body.get("message") or body.get("reason"), (
            "400 response must surface field-level validation errors from "
            "sleeves.rules.schema.validate_rule_doc, not an empty body"
        )
        assert not database.get_sleeve_rules_for_sleeve(sleeve_id), (
            "an invalid rule doc must never be persisted"
        )

    def test_create_rule_for_unknown_sleeve_returns_404(self, client):
        resp = client.post("/api/sleeves/999999/rules", json=_valid_rule_doc())
        assert resp.status_code == 404, (
            f"POST rules against a non-existent sleeve_id must return 404; got {resp.status_code}"
        )

    def test_list_rules_for_sleeve_includes_created_rule(self, client):
        sleeve_id = database.create_sleeve("rule-list-sleeve", 5000.0, envelope_json="{}")
        client.post(f"/api/sleeves/{sleeve_id}/rules", json=_valid_rule_doc(name="list-me-rule"))

        resp = client.get(f"/api/sleeves/{sleeve_id}/rules")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "rules" in body
        names = [r.get("name") for r in body["rules"]]
        assert "list-me-rule" in names, f"expected the created rule in the list; got names={names}"
