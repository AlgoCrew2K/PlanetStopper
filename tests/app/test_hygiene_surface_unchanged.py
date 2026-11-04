"""The app public surface and the database schema are what they were before the clean-up.

A clean-up of dead code, comments and shared parse helpers must change no route, no hook, no
allow-listed setting, no migration and no table. Characterization against the record taken at the
base commit (``tests/fixtures/hygiene_waves_3_4/app_database/``, written by
``produce_ref_records.py``, which refuses to run on any other tree):

* S-10.4: the URL map (every rule, method and endpoint), ``_AUTH_EXEMPT_ENDPOINTS``,
  ``_SETTINGS_WRITE_ALLOWLIST`` and the before/after-request hook names in order (the auth gate
  runs before the CSRF check). The scheduler slots are pinned by
  ``tests/app/test_scheduler_survival.py``.
* S-10.5: the migration files (CRLF-normalized digests), the order of ``_MIGRATION_FILES``, and the
  SQL text of every ``sqlite_master`` entry of a freshly migrated database.
* S-10.9: ``/ai-advisor/accept`` and ``/ai-advisor/reject`` still write one ``llm_suggestions`` row
  each into the real temporary database, with the columns the request supplies. Only the OOS
  re-validation gate is replaced (it reaches Composer); every other outgoing call raises.
"""

from __future__ import annotations

import json
import pathlib
import socket

import pytest

import ai_advisor
import app as app_module
import database
import llm_request
from tests import _hygiene_surface_facts as facts
from tests._row_readback import llm_suggestions_for_symphony

RECORDS = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "hygiene_waves_3_4" / "app_database"
)


def _record(name: str) -> dict:
    return json.loads((RECORDS / name).read_text(encoding="utf-8"))


def _difference(label: str, got: list, ref: list) -> str:
    added = [x for x in got if x not in ref]
    removed = [x for x in ref if x not in got]
    return f"{label}: added {added}, removed {removed}"


class TestUrlMapAndHooks:
    @pytest.fixture(scope="class")
    def now(self) -> dict:
        return facts.url_map_facts(app_module)

    @pytest.fixture(scope="class")
    def ref(self) -> dict:
        return _record("url_map.json")

    def test_every_rule_method_and_endpoint(self, now, ref):
        assert now["rules"] == ref["rules"], _difference("rules", now["rules"], ref["rules"])

    def test_the_auth_exempt_endpoints(self, now, ref):
        assert now["auth_exempt_endpoints"] == ref["auth_exempt_endpoints"]

    def test_the_settings_write_allowlist(self, now, ref):
        assert now["settings_write_allowlist"] == ref["settings_write_allowlist"]

    def test_the_hooks_and_their_order(self, now, ref):
        assert now["before_request_hooks"] == ref["before_request_hooks"]
        assert now["after_request_hooks"] == ref["after_request_hooks"]
        assert now["teardown_request_hooks"] == ref["teardown_request_hooks"]
        assert now["error_handlers"] == ref["error_handlers"]

    def test_the_auth_gate_still_runs_before_the_csrf_check(self, now):
        hooks = now["before_request_hooks"]["None"]
        assert hooks.index("_auth_before_request") < hooks.index("_csrf_before_request")

    def test_the_record_is_not_empty(self, ref):
        assert len(ref["rules"]) > 50 and ref["settings_write_allowlist"]


class TestSchema:
    @pytest.fixture
    def now(self) -> dict:
        database.init_db()
        database.run_migrations()
        conn = database.get_ro_connection()
        try:
            return facts.schema_facts(database, conn)
        finally:
            conn.close()

    @pytest.fixture(scope="class")
    def ref(self) -> dict:
        return _record("schema.json")

    def test_the_migration_files_are_the_bases(self, now, ref):
        assert now["migration_file_digests"] == ref["migration_file_digests"], _difference(
            "migration files",
            sorted(now["migration_file_digests"].items()),
            sorted(ref["migration_file_digests"].items()),
        )

    def test_the_migration_order_is_the_bases(self, now, ref):
        assert now["migration_order"] == ref["migration_order"]

    def test_every_table_and_index_has_the_bases_sql(self, now, ref):
        got = [tuple(r) for r in now["sqlite_master"]]
        want = [tuple(r) for r in ref["sqlite_master"]]
        assert got == want, _difference("sqlite_master entries", got, want)

    def test_the_record_is_not_empty(self, ref):
        assert len(ref["sqlite_master"]) > 20 and ref["migration_file_digests"]


# ---------------------------------------------------------------------------
# S-10.9: the two routes that write llm_suggestions
# ---------------------------------------------------------------------------

SYMPHONY_DISPLAY = "(INVEST) LQD + EYEG 5 ways Full Market"
SYMPHONY_STORED = "(invest) lqd + eyeg 5 ways full market"
POSTED_MODEL = "model-id-under-test"


@pytest.fixture
def no_outgoing_calls(monkeypatch):
    """Any socket connection a route attempts raises: no Composer, Alpaca or Anthropic call."""

    def refuse(*args, **kwargs):
        raise AssertionError("an outgoing network call was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def client(no_outgoing_calls):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _suggestion() -> dict:
    return {
        "config_key": "MAX_SQUEEZE_FLOOR",
        "current_value": 0.2,
        "suggested_value": 0.3,
        "rationale": "stated reason",
        "risk_direction": "loosens",
        "confidence": "medium",
        "data_sufficiency": "sufficient",
        "model": POSTED_MODEL,
    }


class TestDecisionRoutesWriteTheirRows:
    def test_accept_writes_one_accepted_row_with_the_request_values(self, client, monkeypatch):
        oos = {"passed": True, "detail": "re-validation passed"}
        monkeypatch.setattr(ai_advisor, "revalidate_suggestion_oos", lambda *a, **k: dict(oos))
        before = (database.get_symphony_strategy(SYMPHONY_DISPLAY) or {"params": {}})["params"]

        resp = client.post(
            "/ai-advisor/accept",
            json={"symphony_id": SYMPHONY_DISPLAY, "suggestion": _suggestion()},
        )

        assert resp.status_code == 200 and resp.get_json() == {"status": "accepted"}
        (row,) = llm_suggestions_for_symphony(SYMPHONY_STORED)
        assert row["operator_decision"] == "accepted"
        assert row["param_name"] == "MAX_SQUEEZE_FLOOR"
        assert row["model_id"] == POSTED_MODEL
        assert row["before_value"] == before.get("MAX_SQUEEZE_FLOOR")
        assert row["after_value"] == 0.3
        assert row["oos_revalidation"] == oos
        assert row["decision_at"] == row["created_at"] and row["decision_at"]
        assert row["operator_identity"] == "" and row["operator_note"] is None
        assert row["prompt_inputs"] == {} and row["generation_settings"] == {}
        assert row["raw_response"] == {} and row["validation_results"] == {}
        assert len(row["session_id"]) == 16 and int(row["session_id"], 16) >= 0

    def test_reject_writes_one_rejected_row_with_the_request_values(self, client):
        resp = client.post(
            "/ai-advisor/reject",
            json={"symphony_id": SYMPHONY_DISPLAY, "suggestion": _suggestion()},
        )

        assert resp.status_code == 200 and resp.get_json() == {"status": "rejected"}
        (row,) = llm_suggestions_for_symphony(SYMPHONY_STORED)
        assert row["operator_decision"] == "rejected"
        assert row["param_name"] == "MAX_SQUEEZE_FLOOR"
        assert row["model_id"] == POSTED_MODEL
        assert row["before_value"] == 0.2 and row["after_value"] == 0.3
        assert row["oos_revalidation"] is None
        assert row["decision_at"] == row["created_at"] and row["decision_at"]
        assert row["raw_response"] == {} and row["validation_results"] == {}

    def test_reject_with_an_empty_payload_still_writes_one_row(self, client):
        resp = client.post("/ai-advisor/reject", json={})

        assert resp.status_code == 200
        (row,) = llm_suggestions_for_symphony("")
        assert row["operator_decision"] == "rejected" and row["param_name"] == ""
        assert row["before_value"] is None and row["after_value"] is None

    def test_a_posted_model_id_that_is_not_plain_is_not_recorded(self, client, monkeypatch):
        forged = dict(_suggestion(), model="x" * 200)
        # The registry resolution is the fallback; it must not be reached over the network.
        monkeypatch.setattr(llm_request, "default_generation_model", lambda: "resolved-by-registry")

        client.post(
            "/ai-advisor/reject", json={"symphony_id": SYMPHONY_DISPLAY, "suggestion": forged}
        )

        (row,) = llm_suggestions_for_symphony(SYMPHONY_STORED)
        assert row["model_id"] == "resolved-by-registry"

    def test_a_refused_accept_writes_no_row(self, client):
        bad = dict(_suggestion(), config_key="LIVE_EXECUTION")

        resp = client.post(
            "/ai-advisor/accept", json={"symphony_id": SYMPHONY_DISPLAY, "suggestion": bad}
        )

        assert resp.get_json()["status"] == "rejected"
        assert llm_suggestions_for_symphony(SYMPHONY_STORED) == []
