"""RED tests — cycle/advisor-informative.

Problem: For 8/11 live symphonies the AI Advisor returns an empty suggestions
list, and the UI shows a blank box with the generic message "did not find a
well-supported edit". The advisor looks broken and identical across symphonies.

This file covers AC1-AC6 of the advisor-informative acceptance criteria:

  AC1 — ``assessment`` object always present alongside ``suggestions``.
  AC2 — Two symphonies in different tuning states produce different payloads.
  AC3 — Determine whether ConfigSuggestionsResponse carries an assessment field
         the route currently discards, or whether assessment must be built from
         autotune_run context. (Resolved: see module docstring below.)
  AC4 — static/ai_advisor.js must parse and render the assessment in the
         empty-suggestions branch; ``node --check`` must pass.
  AC5 — vol/atr regime block must be marked available:False with a reason when
         the DB columns are absent (not available:True with all-null fields).
  AC6 — Full-tree regression: no new test failures; D-1 security contract holds.

AC3 RESOLUTION (verified by reading ai_advisor.py and database.py):
  ``ConfigSuggestionsResponse`` (ai_advisor.py:197-204) has ONLY a
  ``suggestions: list[ConfigSuggestion]`` field — NO summary/assessment/rationale
  at the response level. The route (app.py:3329) extracts only ``.suggestions``.
  The assessment MUST BE BUILT FROM autotune_run context already in
  ``assemble_advisor_context``: specifically ``optuna_evidence`` carries
  ``baseline_decision``, ``oos_alpha``, ``fallback_oos_alpha``,
  ``default_oos_alpha``, and ``available``. A code comment must document this.

  Correct approach: build_assessment_from_context() in ai_advisor.py that
  reads the assembled context dict and returns an ``assessment`` dict; the
  route calls it and includes ``assessment`` in every response.

Vol/atr bug (AC5):
  ``_build_volatility_regime`` (ai_advisor.py:212-232) sets ``available: True``
  when autotune_run is not None, but ``_autotune_run_row_to_dict``
  (database.py:718-735) returns NO ``symphony_vol``, ``atr_pct_14d``,
  ``vol_window_range``, or ``atr_pct_window_range`` keys — so all four fields
  are ALWAYS None even when available=True. This is a silent lie.

Mocking strategy:
  Route-level tests (AC1, AC2, AC6): hit the real ``ai_advisor`` module;
  mock BOTH the app.py database binding (``app_module.database``) AND the
  ai_advisor module's own database binding (``ai_advisor.database``). Both
  bindings must be mocked because ``assemble_advisor_context`` calls
  ``database.get_latest_autotune_run`` through ai_advisor's OWN import binding
  (~ai_advisor.py:501) — a separate object from app_module.database. Without
  patching both, ``assemble_advisor_context`` hits the real SQLite and the
  tests are non-hermetic. Do NOT mock ``ai_advisor`` wholesale — a fully-mocked
  module test passes while the live route 500s/blanks.

  Unit tests (AC5): call ``ai_advisor._build_volatility_regime`` directly
  with controlled autotune_run dicts.

Fixture discipline:
  Expected values in assertions are derived from the fixture dicts fed into the
  route, never hardcoded producer outputs.

B-1 HERMETICITY FIX:
  All route-level tests now patch BOTH ``app_module.database`` (covers the route)
  AND ``ai_advisor.database`` (covers ``assemble_advisor_context``'s own import
  binding at ai_advisor.py:501). Without the ai_advisor.database patch, the
  route test passes even when ``assemble_advisor_context`` builds assessment={}
  because the isinstance guard at app.py silently replaces a MagicMock result.
  The new RED test ``test_route_level_db_mock_covers_ai_advisor_database_binding``
  asserts both that ``ai_advisor.database.get_latest_autotune_run`` was called
  AND that the assessment is non-empty (not masked by the isinstance guard).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

import ai_advisor
import app as app_module
from ai_advisor import ConfigSuggestionsResponse

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "ai_advisor"
)


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


# Two fixture autotune_run dicts in different tuning states.
# Each key in these dicts corresponds exactly to what _autotune_run_row_to_dict
# returns (database.py:718-735) — no invented keys.
_FIXTURE_REVERTED = _load_fixture("autotune_run_reverted_to_fallback.json")
_FIXTURE_ADOPTED = _load_fixture("autotune_run_adopted_ai.json")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_empty_llm_response() -> ConfigSuggestionsResponse:
    """Return an empty-suggestions response — the valid Claude abstention case."""
    return ConfigSuggestionsResponse(suggestions=[])


def _make_anthropic_client_mock() -> MagicMock:
    """Build a mock Anthropic client whose messages.parse() returns an empty
    suggestions list, simulating Claude abstaining (the common case for
    symphonies with no statistically-significant edge).

    We construct a ParsedTextBlock-shaped mock so the extraction loop in
    request_suggestions (ai_advisor.py:531-535) resolves a valid instance.
    """
    response_instance = _make_empty_llm_response()
    parsed_block = MagicMock()
    parsed_block.parsed_output = response_instance

    sdk_response = MagicMock()
    sdk_response.content = [parsed_block]

    client_mock = MagicMock()
    client_mock.messages.parse.return_value = sdk_response
    return client_mock


@pytest.fixture
def client():
    """Flask test client — no port bound."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC1 — assessment object always present, even when suggestions is empty.
# ---------------------------------------------------------------------------


class TestAC1AssessmentAlwaysPresent:
    """POST /ai-advisor/suggest must return an ``assessment`` object alongside
    ``suggestions`` on every call, even when Claude returns an empty list.

    The data lives in the assembled context (optuna_evidence section) —
    the route must surface it rather than silently discarding it.
    """

    def test_suggest_returns_assessment_key_alongside_suggestions(
        self, client, monkeypatch
    ):
        """Route-level: ``assessment`` key must be present in the JSON response.

        Mocks BOTH app_module.database (the route's DB binding) AND
        ai_advisor.database (assemble_advisor_context's own import binding at
        ai_advisor.py:501). Without the ai_advisor.database patch the test is
        non-hermetic — assemble_advisor_context can fall through to real SQLite.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            # ai_advisor.database is a separate module-level binding; patch it so
            # assemble_advisor_context (ai_advisor.py:501) never touches real SQLite.
            ai_db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "symphony_no_edge"},
                content_type="application/json",
            )

        assert resp.status_code == 200, (
            f"Route must return 200 even for empty suggestions; got {resp.status_code}"
        )
        body = resp.get_json()
        assert body is not None
        assert "error" not in body, (
            f"Route must not return an error for an empty-suggestions response; got: {body}"
        )
        assert "assessment" in body, (
            "Response must include an 'assessment' key alongside 'suggestions'. "
            "For 8/11 live symphonies the suggestions list is empty and the UI "
            "shows nothing — the assessment carries per-symphony context that explains "
            "why no edit is recommended. Key is missing from the current response."
        )

    def test_assessment_carries_required_fields(self, client, monkeypatch):
        """The ``assessment`` object must carry baseline_decision, oos_alpha (or
        a null with reason), fallback_oos_alpha, default_oos_alpha, and summary.

        Values are asserted for presence and type only — never hardcoded numbers
        (the fixture is the source of truth for what is fed in).
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "symphony_no_edge"},
                content_type="application/json",
            )

        body = resp.get_json()
        assert "assessment" in body, "assessment key must be present (see AC1 test above)"
        assessment = body["assessment"]

        assert isinstance(assessment, dict), (
            f"assessment must be a dict, got {type(assessment).__name__!r}"
        )

        # Required fields per AC1.
        required_fields = (
            "baseline_decision",
            "oos_alpha",
            "fallback_oos_alpha",
            "default_oos_alpha",
            "summary",
        )
        for field in required_fields:
            assert field in assessment, (
                f"assessment must include {field!r} — it is required by AC1 and "
                "must be surfaced from the autotune_run context. "
                f"Present keys: {sorted(assessment.keys())!r}"
            )

        assert isinstance(assessment["summary"], str) and assessment["summary"], (
            "assessment.summary must be a non-empty string explaining why no edit "
            "is suggested when the suggestions list is empty"
        )

    def test_assessment_baseline_decision_matches_fixture(self, client, monkeypatch):
        """assessment.baseline_decision must reflect the value from the autotune_run
        fixture, not a hardcoded fallback string.

        Derived from the fixture to avoid hardcoding producer output.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "symphony_no_edge"},
                content_type="application/json",
            )

        body = resp.get_json()
        # Derive expected value from the fixture — not a literal.
        expected_decision = _FIXTURE_REVERTED["baseline_decision"]
        assessment = body["assessment"]
        assert assessment["baseline_decision"] == expected_decision, (
            f"assessment.baseline_decision must equal the fixture's baseline_decision "
            f"({expected_decision!r}); got {assessment.get('baseline_decision')!r}. "
            "The route must read this from the autotune_run context, not hardcode it."
        )

    def test_assessment_oos_alpha_matches_fixture(self, client, monkeypatch):
        """assessment.oos_alpha must reflect the value from the autotune_run fixture.

        For a Reverted-to-Fallback symphony the fixture has oos_alpha=null
        (database sentinel for -inf / no valid trial). The assessment must
        surface null here, not a magic number.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "symphony_no_edge"},
                content_type="application/json",
            )

        body = resp.get_json()
        expected_oos = _FIXTURE_REVERTED["oos_alpha"]  # None in fixture
        assessment = body["assessment"]
        assert assessment["oos_alpha"] == expected_oos, (
            f"assessment.oos_alpha must match the fixture value ({expected_oos!r}); "
            f"got {assessment.get('oos_alpha')!r}. "
            "When Optuna found no statistically-significant edge, oos_alpha is null."
        )

    def test_assessment_present_when_no_autotune_run_exists(self, client, monkeypatch):
        """When no autotune run exists for a symphony (get_latest_autotune_run
        returns None), the route must still return an assessment object — just
        with nulls and a message explaining Optuna has not yet run.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = None  # Optuna never ran
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = None  # Optuna never ran
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "untuned_symphony"},
                content_type="application/json",
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert "assessment" in body, (
            "assessment must be present even when no autotune run exists — "
            "it must explain that Optuna has not yet run for this symphony"
        )
        assessment = body["assessment"]
        assert "summary" in assessment and assessment["summary"], (
            "assessment.summary must explain the no-autotune-run state"
        )


# ---------------------------------------------------------------------------
# AC2 — Differentiation between symphonies in different tuning states.
# ---------------------------------------------------------------------------


class TestAC2Differentiation:
    """Two symphonies in different tuning states must produce different response
    payloads. The current bug: every symphony returns effectively the same
    blank box, so the advisor appears broken.
    """

    def _call_suggest(self, client, monkeypatch, autotune_run_fixture: dict) -> dict:
        """Helper: POST /ai-advisor/suggest with the given autotune_run fixture.

        Patches both app_module.database and ai_advisor.database so the test is
        hermetic — assemble_advisor_context calls database.get_latest_autotune_run
        through ai_advisor's own module-level binding (ai_advisor.py:501).

        Returns the parsed JSON body.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(autotune_run_fixture)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = dict(autotune_run_fixture)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": autotune_run_fixture["symphony_id"]},
                content_type="application/json",
            )
        return resp.get_json()

    def test_two_different_tuning_states_produce_different_assessment_summary(
        self, client, monkeypatch
    ):
        """A symphony with 'Reverted to Fallback' and one with 'Adopted AI' must
        produce different assessment.summary strings.

        The assessment.summary is the per-symphony explanation of why no edit
        is suggested (or why one is). If both symphonies get the same generic
        message the fix is incomplete.
        """
        body_reverted = self._call_suggest(client, monkeypatch, _FIXTURE_REVERTED)
        body_adopted = self._call_suggest(client, monkeypatch, _FIXTURE_ADOPTED)

        assert "assessment" in body_reverted, "Reverted fixture must have assessment"
        assert "assessment" in body_adopted, "Adopted fixture must have assessment"

        summary_reverted = body_reverted["assessment"].get("summary", "")
        summary_adopted = body_adopted["assessment"].get("summary", "")

        assert summary_reverted != summary_adopted, (
            "Two symphonies with different tuning states must produce different "
            "assessment.summary strings. Both got the same generic message, which "
            "means the advisor is ignoring per-symphony context. "
            f"Reverted summary: {summary_reverted!r}. "
            f"Adopted summary: {summary_adopted!r}."
        )

    def test_two_different_tuning_states_produce_different_baseline_decision(
        self, client, monkeypatch
    ):
        """Reverted-to-Fallback vs Adopted-AI: assessment.baseline_decision must
        differ because the fixture values differ.

        Values derived from the fixtures — never hardcoded.
        """
        body_reverted = self._call_suggest(client, monkeypatch, _FIXTURE_REVERTED)
        body_adopted = self._call_suggest(client, monkeypatch, _FIXTURE_ADOPTED)

        decision_reverted = body_reverted["assessment"]["baseline_decision"]
        decision_adopted = body_adopted["assessment"]["baseline_decision"]

        # The fixture values differ — derive the expected difference from them.
        assert _FIXTURE_REVERTED["baseline_decision"] != _FIXTURE_ADOPTED["baseline_decision"], (
            "Fixture precondition: the two fixtures must have different baseline_decision "
            "values; update the fixture files if they accidentally match."
        )
        assert decision_reverted != decision_adopted, (
            "assessment.baseline_decision must differ between the two symphonies "
            "because the fixture values differ. "
            f"Reverted: {decision_reverted!r}, Adopted: {decision_adopted!r}. "
            "The route is not threading per-symphony autotune context into the assessment."
        )

    def test_no_two_symphonies_return_byte_identical_bodies(self, client, monkeypatch):
        """Two symphonies in different tuning states must NOT return byte-identical
        JSON responses.

        The original defect: every empty-suggestions response returned the same
        generic message regardless of symphony. The fix must make the JSON bodies
        distinguishable.
        """
        body_reverted = self._call_suggest(client, monkeypatch, _FIXTURE_REVERTED)
        body_adopted = self._call_suggest(client, monkeypatch, _FIXTURE_ADOPTED)

        # Normalize key order so the comparison is semantic, not JSON-serialization order.
        str_reverted = json.dumps(body_reverted, sort_keys=True)
        str_adopted = json.dumps(body_adopted, sort_keys=True)

        assert str_reverted != str_adopted, (
            "Reverted-to-Fallback and Adopted-AI symphonies returned byte-identical "
            "JSON bodies. The advisor is returning a generic response that ignores "
            "per-symphony context. The assessment section must differ between symphonies."
        )


# ---------------------------------------------------------------------------
# AC3 — build_assessment_from_context must exist in ai_advisor.
# ---------------------------------------------------------------------------


class TestAC3AssessmentBuilderExists:
    """Verify the internal function that builds assessment from context exists
    and is callable. The route calls it and includes its output in the response.

    This also documents the AC3 resolution: the assessment is built from context,
    not discarded from a response-model field that doesn't exist.
    """

    def test_build_assessment_from_context_function_exists_in_ai_advisor(self):
        """ai_advisor must expose a ``build_assessment_from_context`` function
        (or equivalent: ``_build_assessment_from_context``).

        The function takes the assembled context dict and returns an assessment
        dict. The route calls it so every response includes the assessment.
        If this fails: the implementer has not yet added the function.
        """
        # Accept either public or private naming convention.
        has_public = hasattr(ai_advisor, "build_assessment_from_context")
        has_private = hasattr(ai_advisor, "_build_assessment_from_context")
        assert has_public or has_private, (
            "ai_advisor must expose ``build_assessment_from_context`` (or "
            "``_build_assessment_from_context``) — the function that reads the "
            "assembled context dict and returns an assessment dict for the route. "
            "AC3 resolution: since ConfigSuggestionsResponse has no summary/assessment "
            "field, the assessment must be built from context (optuna_evidence section)."
        )

    def test_build_assessment_from_context_returns_required_keys_for_reverted_fixture(self):
        """build_assessment_from_context(context) must return a dict with
        baseline_decision, oos_alpha, fallback_oos_alpha, default_oos_alpha,
        and summary when fed a context assembled from the Reverted fixture.

        Values are asserted for presence/type only; the summary must be non-empty.
        """
        fn = getattr(
            ai_advisor,
            "build_assessment_from_context",
            getattr(ai_advisor, "_build_assessment_from_context", None),
        )
        assert fn is not None, "function not found — see test above"

        # Simulate what assemble_advisor_context produces for the reverted fixture.
        context = {
            "scope": "symphony",
            "symphony_id": _FIXTURE_REVERTED["symphony_id"],
            "optuna_evidence": {
                "available": True,
                "baseline_decision": _FIXTURE_REVERTED["baseline_decision"],
                "oos_alpha": _FIXTURE_REVERTED["oos_alpha"],
                "fallback_oos_alpha": _FIXTURE_REVERTED["fallback_oos_alpha"],
                "default_oos_alpha": _FIXTURE_REVERTED["default_oos_alpha"],
                "train_alpha": _FIXTURE_REVERTED["train_alpha"],
            },
        }

        result = fn(context)

        assert isinstance(result, dict), (
            f"build_assessment_from_context must return a dict, got {type(result).__name__!r}"
        )
        for field in ("baseline_decision", "oos_alpha", "fallback_oos_alpha", "default_oos_alpha", "summary"):
            assert field in result, (
                f"build_assessment_from_context must return a dict with key {field!r}; "
                f"got keys: {sorted(result.keys())!r}"
            )
        assert isinstance(result["summary"], str) and result["summary"], (
            "build_assessment_from_context must return a non-empty summary string"
        )

    def test_build_assessment_returns_values_matching_input_context(self):
        """Values in the returned assessment must be derived from the context passed in,
        not hardcoded. Feed different context values → get different output values.
        """
        fn = getattr(
            ai_advisor,
            "build_assessment_from_context",
            getattr(ai_advisor, "_build_assessment_from_context", None),
        )
        assert fn is not None, "function not found — see test_build_assessment_from_context_function_exists_in_ai_advisor"

        # Context A: adopted symphony with finite oos_alpha from the adopted fixture.
        context_a = {
            "scope": "symphony",
            "symphony_id": _FIXTURE_ADOPTED["symphony_id"],
            "optuna_evidence": {
                "available": True,
                "baseline_decision": _FIXTURE_ADOPTED["baseline_decision"],
                "oos_alpha": _FIXTURE_ADOPTED["oos_alpha"],
                "fallback_oos_alpha": _FIXTURE_ADOPTED["fallback_oos_alpha"],
                "default_oos_alpha": _FIXTURE_ADOPTED["default_oos_alpha"],
                "train_alpha": _FIXTURE_ADOPTED["train_alpha"],
            },
        }
        # Context B: reverted symphony — all alphas null.
        context_b = {
            "scope": "symphony",
            "symphony_id": _FIXTURE_REVERTED["symphony_id"],
            "optuna_evidence": {
                "available": True,
                "baseline_decision": _FIXTURE_REVERTED["baseline_decision"],
                "oos_alpha": _FIXTURE_REVERTED["oos_alpha"],
                "fallback_oos_alpha": _FIXTURE_REVERTED["fallback_oos_alpha"],
                "default_oos_alpha": _FIXTURE_REVERTED["default_oos_alpha"],
                "train_alpha": _FIXTURE_REVERTED["train_alpha"],
            },
        }

        result_a = fn(context_a)
        result_b = fn(context_b)

        # baseline_decision must be passed through from each respective context.
        assert result_a["baseline_decision"] == _FIXTURE_ADOPTED["baseline_decision"], (
            "build_assessment_from_context must thread baseline_decision from the "
            "context's optuna_evidence into the returned assessment"
        )
        assert result_b["baseline_decision"] == _FIXTURE_REVERTED["baseline_decision"]

        # oos_alpha must be passed through.
        assert result_a["oos_alpha"] == pytest.approx(
            _FIXTURE_ADOPTED["oos_alpha"], abs=1e-9
        ), (
            # approx tolerance: 1e-9 — float round-trip through JSON, no meaningful diff
            "build_assessment_from_context must pass oos_alpha from optuna_evidence"
        )
        # Reverted fixture has null oos_alpha.
        assert result_b["oos_alpha"] is None, (
            "build_assessment_from_context must return null oos_alpha when the "
            "fixture carries null (no valid trial found by Optuna)"
        )

        # summaries must differ — they are per-symphony.
        assert result_a["summary"] != result_b["summary"], (
            "build_assessment_from_context must produce different summaries for "
            "different tuning states — the summary must reflect the context, not be generic"
        )


# ---------------------------------------------------------------------------
# AC4 — static/ai_advisor.js parses and renders the assessment.
# ---------------------------------------------------------------------------


class TestAC4JSRendersAssessment:
    """static/ai_advisor.js must:
    1. Pass ``node --check`` (no syntax errors).
    2. In the empty-suggestions branch (suggestions.length === 0), render the
       assessment fields rather than the current generic string.
    """

    _JS_PATH = (
        pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js"
    )

    def test_ai_advisor_js_passes_node_check(self):
        """``node --check static/ai_advisor.js`` must exit 0 (no syntax errors).

        A JS parse error passes every Python test but silently breaks the page.
        This test makes a JS parse error a pytest failure.
        """
        result = subprocess.run(
            ["node", "--check", str(self._JS_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"node --check static/ai_advisor.js failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}\n"
            "A JS syntax error makes the entire AI Advisor page non-functional "
            "while all Python tests pass green."
        )

    def test_ai_advisor_js_references_assessment_in_empty_branch(self):
        """The empty-suggestions branch in getSuggestions must reference
        ``assessment`` so it can render per-symphony content.

        Current code (ai_advisor.js:88-96) has a hardcoded generic string —
        'No suggestions — the advisor did not find a well-supported edit at
        this time.' — with no reference to ``assessment``. This test asserts
        the fix: the word ``assessment`` must appear in the empty branch handler.
        """
        source = self._JS_PATH.read_text(encoding="utf-8")

        assert "assessment" in source, (
            "static/ai_advisor.js must reference 'assessment' in the empty-suggestions "
            "branch so it can render per-symphony content (baseline_decision, summary, "
            "oos_alpha). Currently the JS discards the assessment and shows a generic "
            "message identical for every symphony."
        )

    def test_ai_advisor_js_empty_branch_renders_assessment_summary(self):
        """The empty-suggestions branch must render assessment.summary, not a
        hardcoded generic string identical for every symphony.

        We assert the source contains ``assessment.summary`` (or
        ``body.assessment.summary`` / ``data.assessment.summary``) — any form
        that extracts the summary field from the response.
        """
        source = self._JS_PATH.read_text(encoding="utf-8")

        has_assessment_summary = (
            "assessment.summary" in source
            or "assessment['summary']" in source
            or 'assessment["summary"]' in source
        )
        assert has_assessment_summary, (
            "static/ai_advisor.js must access assessment.summary in the empty-branch "
            "so the UI shows per-symphony context (e.g. 'No statistically-significant "
            "tuning edge: 500/500 trials failed the FDR significance gate; "
            "out-of-sample guard-alpha negative') rather than the generic hardcoded "
            "'did not find a well-supported edit' message."
        )


# ---------------------------------------------------------------------------
# AC5 — Vol/ATR regime block: available:False with reason when columns absent.
# ---------------------------------------------------------------------------


class TestAC5HonestRegimeBlock:
    """_build_volatility_regime must be honest about data availability.

    Bug: the function marks available:True when autotune_run is not None,
    but _autotune_run_row_to_dict never sets symphony_vol, atr_pct_14d,
    vol_window_range, or atr_pct_window_range — so the dict is always all-null
    while being marked available:True. That is a silent lie.

    Fix: check whether the actual vol/atr fields are present and non-null
    before marking available:True. When they are all absent (the current
    universal state), mark available:False with a reason string.
    """

    def _call_build_volatility_regime(self, autotune_run: dict | None) -> dict:
        return ai_advisor._build_volatility_regime(autotune_run)

    def test_regime_unavailable_when_autotune_run_is_none(self):
        """When autotune_run is None the regime must be available:False."""
        result = self._call_build_volatility_regime(None)
        assert result["available"] is False, (
            "_build_volatility_regime(None) must return available:False; "
            f"got available={result.get('available')!r}"
        )

    def test_regime_unavailable_when_vol_atr_fields_absent_from_autotune_run(self):
        """The core AC5 case: autotune_run is not None but contains NO
        symphony_vol or atr_pct_14d keys (as _autotune_run_row_to_dict
        currently returns — database.py:718-735 has no such columns).

        The function must return available:False, not available:True with
        all-null fields. An all-null-but-marked-available block silently tells
        Claude 'I have regime data' when there is none — it fabricates context.
        """
        # autotune_run as _autotune_run_row_to_dict returns it — no vol/atr keys.
        autotune_run_no_vol = {
            "id": 1,
            "run_timestamp": "2026-06-01T09:30:00",
            "symphony_id": "some_symphony",
            "oos_alpha": None,
            "train_alpha": None,
            "baseline_decision": "Reverted to Fallback",
            "fallback_oos_alpha": -1.0,
            "default_oos_alpha": -2.0,
            "selection_tstat": None,
            "naive_sharpe": 0.1,
            "validation_sharpe": None,
            "frozen_eval_sharpe": None,
            "math_mode": "per_symphony",
            "account_id": None,
            "sortino_sentinel_pct": None,
            "pbo": None,
            # Deliberately no symphony_vol, atr_pct_14d, vol_window_range,
            # atr_pct_window_range — this matches what _autotune_run_row_to_dict
            # actually returns (database.py:718-735 has no such columns).
        }

        result = self._call_build_volatility_regime(autotune_run_no_vol)

        assert result["available"] is False, (
            "_build_volatility_regime must return available:False when the autotune_run "
            "dict has no symphony_vol / atr_pct_14d keys (the real DB schema has no "
            "such columns — they are never populated). "
            "Current behavior: returns available:True with all-null fields, "
            "which is a silent lie that tells Claude 'regime data available' when none exists."
        )
        assert "reason" in result and result["reason"], (
            "When available is False, _build_volatility_regime must include a non-empty "
            "'reason' key explaining why (e.g. 'vol/atr columns not yet in autotune schema')"
        )

    def test_regime_available_when_vol_fields_are_present_and_non_null(self):
        """If a future schema update adds symphony_vol and atr_pct_14d, the
        function must return available:True when those fields are present and
        non-null. This is the forward-compatibility check.
        """
        autotune_run_with_vol = {
            "id": 1,
            "run_timestamp": "2026-06-01T09:30:00",
            "symphony_id": "some_symphony",
            "oos_alpha": 1.5,
            "train_alpha": 2.0,
            "baseline_decision": "Adopted AI",
            "fallback_oos_alpha": 0.5,
            "default_oos_alpha": -0.3,
            "selection_tstat": 2.1,
            "naive_sharpe": 0.9,
            "validation_sharpe": 0.8,
            "frozen_eval_sharpe": 0.85,
            "math_mode": "per_symphony",
            "account_id": None,
            "sortino_sentinel_pct": None,
            "pbo": 0.22,
            # Future schema additions:
            "symphony_vol": 0.018,
            "atr_pct_14d": 0.025,
        }

        result = self._call_build_volatility_regime(autotune_run_with_vol)

        assert result["available"] is True, (
            "_build_volatility_regime must return available:True when symphony_vol "
            "and atr_pct_14d are present and non-null in the autotune_run dict. "
            f"Got: {result!r}"
        )
        # vol_20d must be populated from the fixture value.
        assert result["vol_20d"] is not None, (
            "vol_20d must be populated when symphony_vol is present"
        )

    def test_regime_block_in_context_marks_unavailable_for_real_db_schema(self, monkeypatch):
        """Integration: assemble_advisor_context must build a volatility_regime
        block with available:False when get_latest_autotune_run returns a real
        DB row (no vol/atr columns).

        This is the production path that currently silently lies to Claude.
        """
        # Patch DB reads — no SQLite I/O.
        with (
            patch.object(ai_advisor, "database") as db_mock,
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = None
            db_mock.DEFAULT_STRATEGY = {}
            db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            context = ai_advisor.assemble_advisor_context(
                scope="symphony",
                symphony_id=_FIXTURE_REVERTED["symphony_id"],
            )

        regime = context["volatility_regime"]
        assert regime["available"] is False, (
            "assemble_advisor_context must produce volatility_regime.available=False "
            "when the autotune_run row has no symphony_vol / atr_pct_14d data "
            "(the real DB schema never populates these fields). "
            f"Current behavior returns available=True with all-null fields. "
            f"Got regime: {regime!r}"
        )


# ---------------------------------------------------------------------------
# AC6 — Security contract: D-1 — route must never echo str(exc).
# ---------------------------------------------------------------------------


class TestAC6SecurityContracts:
    """AC6 regression: security contract D-1 must hold after the assessment
    changes. The route must never echo raw str(exc) to the browser.
    """

    def test_suggest_route_does_not_echo_raw_exception_string(self, client, monkeypatch):
        """When the route encounters an exception, the JSON response must NOT
        contain a raw str(exc) value. Only the exception class name is
        permitted (D-1 security contract, app.py:3332-3336).

        We force an exception by making _build_client raise a RuntimeError
        with a synthetic 'secret' payload. The response must NOT echo the
        payload back.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
        secret_payload = "sk-ant-api03-supersecret-key-not-for-browser"

        def _raise_with_secret():
            raise RuntimeError(f"ANTHROPIC_API_KEY={secret_payload} is invalid")

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", side_effect=_raise_with_secret),
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = None
            # ai_advisor.database patched for hermeticity even though the route
            # will raise before completing context assembly.
            ai_db_mock.get_latest_autotune_run.return_value = None
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "some_sym"},
                content_type="application/json",
            )

        # D-1: status must not be 500 (which would mean an unhandled exception).
        assert resp.status_code != 500, (
            "Route must handle exceptions gracefully — a 500 exposes stack traces"
        )

        body = resp.get_json()
        raw_response = json.dumps(body)

        assert secret_payload not in raw_response, (
            "D-1 security contract violated: the raw exception string (containing "
            f"a synthetic secret payload) was echoed in the route response. "
            "Only the exception class name is permitted in the JSON error field."
        )

    def test_suggest_route_assessment_does_not_leak_internal_paths(self, client, monkeypatch):
        """The assessment object must not contain internal file paths, exception
        messages, or any str(exc) content. Derived from D-1.
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "symphony_no_edge"},
                content_type="application/json",
            )

        body = resp.get_json()
        raw = json.dumps(body).lower()

        # Fragments that indicate an internal path was leaked.
        for bad_fragment in ("traceback", "file \"", "users\\paulm", "/home/", "c:\\"):
            assert bad_fragment not in raw, (
                f"D-1 security contract: the response must not contain internal path "
                f"fragment {bad_fragment!r}. Got response: {json.dumps(body)!r}"
            )


# ---------------------------------------------------------------------------
# B-1 — Route-level DB mock must cover ai_advisor.database binding.
# ---------------------------------------------------------------------------


class TestB1RouteDbMockHermeticity:
    """B-1 (reviewer finding): the route-level tests previously mocked ONLY
    ``app_module.database``, leaving ``ai_advisor.database`` — a SEPARATE
    module-level import binding in ai_advisor.py (~line 501) — unmocked.

    ``assemble_advisor_context`` calls ``database.get_latest_autotune_run``
    through that binding.  When only ``app_module.database`` is mocked, the
    masking isinstance guard at app.py:3351 silently returns ``assessment: {}``
    whenever ``assemble_advisor_context`` produces a non-dict result (e.g. a
    MagicMock from the unmocked ``ai_advisor.database``).  The tests then pass
    for the wrong reason — the assessment object is empty, not built from
    fixture data.

    This test is written RED against the code state where ``assemble_advisor_context``
    does NOT check ``autotune_run is _SENTINEL`` before overwriting ``autotune_run``
    (ai_advisor.py:497-501 unconditionally calls ``database.get_latest_autotune_run``
    regardless of the passed-in argument).  Going GREEN requires EITHER:
      (a) ``assemble_advisor_context`` respects the sentinel and skips the internal
          DB call when a value was passed (then the route's pre-fetch covers it), OR
      (b) the sentinel check is implemented AND the ai_advisor.database path is also
          covered by the test mocks in all scenarios.

    The fix removes the isinstance guard from app.py that masked the issue.
    """

    def test_route_level_db_mock_covers_ai_advisor_database_binding(
        self, client, monkeypatch
    ):
        """Proves that ``ai_advisor.database.get_latest_autotune_run`` is actually
        exercised under the route test mock setup AND that the assessment is non-empty.

        Failure modes this test catches:
          (1) ``assemble_advisor_context`` calls ``ai_advisor.database.get_latest_autotune_run``
              with an unmocked binding → returns a real DB value or raises → assessment broken.
          (2) The isinstance guard at app.py silently swallows a bad assessment → {} returned
              → user sees no per-symphony context even when the mock is set up correctly.

        The test asserts BOTH that ``ai_advisor.database.get_latest_autotune_run``
        was called (proving the mock covered the right binding) AND that the
        returned assessment is a non-empty dict (proving the masking guard is absent).
        """
        monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)

        with (
            patch.object(app_module, "database") as db_mock,
            patch.object(ai_advisor, "database") as ai_db_mock,
            patch.object(ai_advisor, "_build_client", return_value=_make_anthropic_client_mock()),
            patch.object(ai_advisor, "symphony_logic") as logic_mock,
        ):
            db_mock.load_state.return_value = {}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.get_latest_autotune_run.return_value = dict(_FIXTURE_REVERTED)
            ai_db_mock.get_symphony_strategy.return_value = {"parameters": {}, "locked_vars": []}
            ai_db_mock.DEFAULT_STRATEGY = {}
            ai_db_mock.DEFAULT_LOCKED_VARS = []
            logic_mock.get_condensed_logic.return_value = None

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "symphony_no_edge"},
                content_type="application/json",
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assessment = body.get("assessment", {})

        # Part 1: ai_advisor.database.get_latest_autotune_run must have been
        # called — proving the mock covered assemble_advisor_context's own import
        # binding.  If this fails, the sentinel check in assemble_advisor_context
        # is broken and the internal DB call falls through to real SQLite.
        assert ai_db_mock.get_latest_autotune_run.call_count >= 1, (
            "ai_advisor.database.get_latest_autotune_run was NOT called during the "
            "route request. This means assemble_advisor_context bypassed its own "
            "database binding (expected when the sentinel check is correctly implemented "
            "and the route pre-fetches autotune_run). If the sentinel check is absent, "
            "this call count will be >=1 (the internal fetch runs unguarded). "
            "Either way the test mock must be present to ensure hermetic isolation."
        )

        # Part 2: the assessment must be a non-empty dict derived from the fixture.
        # If this fails, the isinstance masking guard is present and swallowing the
        # assessment — the fix requires removing it from app.py.
        assert isinstance(assessment, dict) and assessment != {}, (
            "assessment must be a non-empty dict built from the fixture data. "
            f"Got: {assessment!r}. "
            "If assessment == {{}} the isinstance masking guard at app.py is present "
            "and must be removed — it silently hides the isolation gap by replacing "
            "any non-dict assessment (e.g. a MagicMock) with an empty dict."
        )

        # Part 3: the assessment must contain the fixture's baseline_decision.
        # This is the end-to-end proof that fixture data flows through to the response.
        expected_decision = _FIXTURE_REVERTED["baseline_decision"]
        assert assessment.get("baseline_decision") == expected_decision, (
            f"assessment.baseline_decision must equal the fixture value "
            f"({expected_decision!r}); got {assessment.get('baseline_decision')!r}. "
            "The fixture data is not flowing through assemble_advisor_context → "
            "build_assessment_from_context → route response."
        )
