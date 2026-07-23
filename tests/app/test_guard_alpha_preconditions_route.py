"""
RED tests -- GET /api/guard-alpha-preconditions (AC-6, AC-7 schema half, AC-8).

The route does not exist as a real implementation yet (a stub is present so
the endpoint resolves -- see app.py guard_alpha_preconditions()); every test
below is expected to fail RED until the implementer wires the real pipeline.

DATA-SOURCING SEAM (open question flagged to PM/ga-flask, see
.claude/tdd-handoff.md): these tests monkeypatch database.load_state (for
symphony enumeration), autotuner.build_if_held_replay_series (the primary/
replay sample) and analytics.get_shadow_current_return_daily_series (the
secondary/shadow sample) -- NOT guard_preconditions.compute_persistence_stats
or classify_stop_justification, which run for REAL so the response's
rho/sharpe/verdict values are checked against the same independent reference
formulas used by the math-layer tests (tests/guard_preconditions/), giving
true end-to-end coverage of the route's wiring, not just "did it call a mock".

Contract under test:
- AC-6: per-symphony {rho, rho_ci, sharpe_daily, n_obs, verdict, sample_source}
  for the "replay" and "shadow" samples, where available.
- AC-7 (schema half): route returns valid JSON matching this shape; the
  template-rendering half of AC-7 is covered in tests/app/test_guard_alpha_
  preconditions_panel_ui.py.
- AC-8: when replay and shadow verdicts disagree, BOTH are present with their
  own n_obs -- never silently prefer one.
- Edge cases: empty DB -> honest empty state, not 500; malformed/missing data
  for one symphony does not 500 the whole route; symphony absent from shadow
  -> replay-only row with honest sample_source.
"""

from __future__ import annotations

import inspect
import json
import pathlib

import pytest

import analytics as analytics_module
import app as app_module
import autotuner as autotuner_module
import database as database_module
import guard_preconditions as gp

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math"
_ROUTE = "/api/guard-alpha-preconditions"


def _load_returns(fixture_name: str) -> list:
    return json.loads((_FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))[
        "daily_returns_pct"
    ]


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-6 / AC-8: schema + real math pipeline, both samples present and disagree
# ---------------------------------------------------------------------------


class TestResponseSchemaAndDisagreement:
    def test_both_samples_present_with_schema_and_can_disagree(self, client, monkeypatch):
        sym_id = "sym-preconditions-001"
        replay_returns = _load_returns(
            "persistence_stats_ar1_positive.json"
        )  # -> SUFFICIENT_LIKELY
        shadow_returns = _load_returns("persistence_stats_negative_edge.json")  # -> NEGATIVE_EDGE

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {sym_id: {"name": "Test Symphony", "current_return": 1.0}},
        )
        monkeypatch.setattr(
            autotuner_module,
            "build_if_held_replay_series",
            lambda sid: replay_returns if sid == sym_id else None,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: shadow_returns if sid == sym_id else None,
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data!r}"
        data = resp.get_json()
        assert data is not None and "symphonies" in data, (
            f"Response must have a top-level 'symphonies' key, got keys={list((data or {}).keys())}"
        )
        assert sym_id in data["symphonies"], f"Expected symphony {sym_id!r} in response"
        sym_entry = data["symphonies"][sym_id]

        for sample_key in ("replay", "shadow"):
            assert sample_key in sym_entry, f"Expected {sample_key!r} sample in {sym_entry!r}"
            row = sym_entry[sample_key]
            for field in ("rho", "rho_ci", "sharpe_daily", "n_obs", "verdict", "sample_source"):
                assert field in row, f"{sample_key} row missing field {field!r}: {row!r}"

        # AC-8: verdicts must disagree here (constructed on purpose) and BOTH
        # rows must be present with their OWN n_obs -- never silently prefer one.
        assert sym_entry["replay"]["verdict"] != sym_entry["shadow"]["verdict"], (
            f"Fixtures were chosen so replay and shadow verdicts DIFFER "
            f"(replay={sym_entry['replay']['verdict']!r}, "
            f"shadow={sym_entry['shadow']['verdict']!r}) -- AC-8 requires both "
            "to be surfaced, not collapsed into one."
        )
        assert sym_entry["replay"]["n_obs"] == len(replay_returns)
        assert sym_entry["shadow"]["n_obs"] == len(shadow_returns)

        # End-to-end real math check (not just "a number came back"): the
        # route's replay rho must match the SAME independent reference the
        # math-layer tests use, proving the route actually ran the real
        # compute_persistence_stats pipeline rather than fabricating a value.
        expected_stats = gp.compute_persistence_stats(replay_returns)
        assert sym_entry["replay"]["rho"] == pytest.approx(expected_stats.rho, rel=1e-6)
        assert sym_entry["replay"]["verdict"] == gp.classify_stop_justification(expected_stats)

    def test_replay_only_symphony_has_honest_sample_source(self, client, monkeypatch):
        """Edge case: symphony present in replay but absent from shadow_history
        (never triggered / new) -> replay-only row, sample_source honest."""
        sym_id = "sym-replay-only-002"
        replay_returns = _load_returns("persistence_stats_iid_control.json")

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {sym_id: {"name": "New Symphony", "current_return": 0.0}},
        )
        monkeypatch.setattr(
            autotuner_module,
            "build_if_held_replay_series",
            lambda sid: replay_returns,
        )
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: None,  # never in shadow_history
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        sym_entry = resp.get_json()["symphonies"][sym_id]
        assert "replay" in sym_entry
        assert sym_entry.get("shadow") is None, (
            f"A symphony absent from shadow_history must get shadow=None "
            f"(honest, never fabricated) -- got {sym_entry.get('shadow')!r}."
        )


# ---------------------------------------------------------------------------
# Edge cases: empty DB, malformed/missing per-symphony data
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    def test_no_symphonies_returns_200_honest_empty_state(self, client, monkeypatch):
        monkeypatch.setattr(database_module, "load_state", lambda: {})

        resp = client.get(_ROUTE)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("symphonies") == {}, (
            f"No symphonies must yield an honest empty {{}} 'symphonies' dict "
            f"(a 200 that renders blank without signaling emptiness is exactly "
            f"what AC-7's panel-level empty state must avoid), got {data!r}."
        )

    def test_one_symphony_erroring_does_not_500_the_whole_route(self, client, monkeypatch):
        """AC-6: malformed/missing-data-safe (per-symphony honest degradation,
        never a 500)."""
        good_sym = "sym-good-003"
        bad_sym = "sym-bad-003"
        good_returns = _load_returns("persistence_stats_ar1_positive.json")

        monkeypatch.setattr(
            database_module,
            "load_state",
            lambda: {
                good_sym: {"name": "Good", "current_return": 1.0},
                bad_sym: {"name": "Bad", "current_return": 1.0},
            },
        )

        def _replay(sid):
            if sid == bad_sym:
                raise RuntimeError("simulated replay-source failure")
            return good_returns

        monkeypatch.setattr(autotuner_module, "build_if_held_replay_series", _replay)
        monkeypatch.setattr(
            analytics_module,
            "get_shadow_current_return_daily_series",
            lambda sid, db_file: None,
        )

        resp = client.get(_ROUTE)

        assert resp.status_code == 200, (
            f"One symphony's data source raising must NOT 500 the whole route, "
            f"got {resp.status_code}: {resp.data!r}"
        )
        data = resp.get_json()
        assert good_sym in data["symphonies"], "The good symphony must still be present"
        # The bad symphony either degrades honestly (present with a null/error
        # marker) or is omitted -- either is acceptable, a 500 is not.


# ---------------------------------------------------------------------------
# AC-8: auth gate
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-guard-preconditions"
_TEST_SECRET_KEY = "test-secret-guard-preconditions"


@pytest.fixture
def auth_client_no_session(monkeypatch):
    """Flask test client with the auth gate ENABLED but no active session
    (mirrors tests/app/test_guard_alpha_summary_route.py's convention)."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestAuthGate:
    def test_unauthenticated_xhr_returns_401(self, auth_client_no_session):
        resp = auth_client_no_session.get(_ROUTE, headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 401, (
            f"Unauthenticated XHR to {_ROUTE} must return 401, got {resp.status_code}."
        )


# ---------------------------------------------------------------------------
# Security / scope: read-only, no settings-write, no LIVE_EXECUTION coupling
# ---------------------------------------------------------------------------


class TestReadOnlyScope:
    def test_route_is_get_only(self):
        rule = next(r for r in app_module.app.url_map.iter_rules() if r.rule == _ROUTE)
        assert "POST" not in rule.methods, (
            f"{_ROUTE} must be GET-only (read-only advisory surface), got methods={rule.methods!r}"
        )

    def test_route_source_never_references_live_execution(self):
        src = inspect.getsource(app_module.guard_alpha_preconditions)
        assert "LIVE_EXECUTION" not in src, (
            "guard_alpha_preconditions() must never reference LIVE_EXECUTION "
            "(read-only advisory surface, no engine interaction, AC-6)."
        )

    def test_route_source_contains_no_sql_write_statements(self):
        src = inspect.getsource(app_module.guard_alpha_preconditions).upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE"):
            assert forbidden not in src, (
                f"guard_alpha_preconditions() source contains {forbidden!r} -- "
                "this route must be read-only SQLite (AC-6, Security Considerations)."
            )
