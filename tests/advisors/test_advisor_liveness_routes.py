"""Cycle C — RED: AI Advisor liveness, route + persistence layer (RC-4/5/6 + DE-retire).

These RED tests pin the visibility / error-masking / fail-loud / DE-retire root
causes of "the AI advisor does nothing" from
`.design-handoff/advisor-liveness/DIAGNOSIS.md`.

Route-level RED discipline (memory: feedback_route_level_red_for_mocked_analytics_fns):
  We hit the REAL Flask routes with the REAL advisor producer modules; only the
  Composer NETWORK (run_backtest / fetch_symphony_score) and the DATABASE are
  mocked. A route test that mocks the whole engine module would hide a live 500
  (the broad `except` swallows the AttributeError into a 200-empty). So these
  tests exercise the genuine code path.

Scoped RED items (per the team brief — NOTHING else here):
  * RC-4 (visibility/persistence): a propose call resulting in KEEP_INCUMBENT must
    STILL write an advisor_observations row so the operator sees the engine ran;
    and the /accept OOS-revalidation rejection must be surfaced (explains the
    empty llm_suggestions).
  * RC-5 (no silent masking): the broad `except` sites on the advise path
    (app.py:2935, :3296; asset_swap_engine.py:698) must surface the error class,
    not degrade to a silent 200-empty.
  * RC-6 (fail loud): an unresolvable symphony name must FAIL LOUDLY, not the
    silent `composer_hash = symphony_id` pass-through (app.py:2901-2912).
  * DE-retire (PM decision): the advisor UI must NOT surface the Divergence
    Explainer as a producer / recommendation (CVaR-divergence core permanently
    rejected; flag stays off). The producer-role surface excludes DE.

No live API calls; no live engine that hits the network; function-scoped fixtures.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


@pytest.fixture(scope="function")
def flask_client():
    _ensure_repo_on_path()
    import app as _app
    _app.app.config["TESTING"] = True
    with _app.app.test_client() as client:
        yield client


@pytest.fixture(scope="module")
def swap_engine():
    _ensure_repo_on_path()
    return importlib.import_module("advisors.asset_swap_engine")


@pytest.fixture(scope="module")
def logic_engine():
    _ensure_repo_on_path()
    return importlib.import_module("advisors.logic_change_engine")


# Live-shaped name->hash mapping (the dropdown sends NAME; Composer needs HASH).
_SYM_NAME = "(invest) lqd + eyeg 5 ways full market"
_SYM_HASH = "abc123HASHxyz"


def _bot_state_with_mapping() -> dict:
    return {_SYM_HASH: {"name": _SYM_NAME, "account_uuid": "acc-1"}}


def _flat_returns_backtest(per_day_pct: float = 0.05, n: int = 200) -> MagicMock:
    """A deterministic non-error BacktestResult-like mock (daily_returns as fractions)."""
    fake = MagicMock()
    fake.error = None
    fake.daily_returns = {str(19000 + i): (per_day_pct / 100.0) for i in range(n)}
    fake.stats = {"sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None}
    return fake


def _varied_returns_backtest(seed: int = 11, n: int = 700, mean_pct: float = 0.5) -> MagicMock:
    """A deterministic VARIED (non-constant) BacktestResult-like mock.

    A flat constant series has zero variance -> the Sortino t-stat / BHY veto fails,
    so the gate never adopts. A gaussian series with positive mean gives the gate a
    real signal to act on (matches the existing engine tests' _make_synthetic_daily_returns).
    daily_returns are FRACTIONS (engine multiplies by 100 to get percent).
    """
    import random  # noqa: PLC0415
    rng = random.Random(seed)
    fake = MagicMock()
    fake.error = None
    fake.daily_returns = {
        str(19000 + i): (rng.gauss(mean_pct, 0.8) / 100.0) for i in range(n)
    }
    fake.stats = {"sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None}
    return fake


# ===========================================================================
# RC-4 — KEEP_INCUMBENT must STILL persist an advisor_observations row.
# ===========================================================================


class TestRC4PersistRegardlessOfVerdict:
    """The operator must see "the engine ran and kept the incumbent", not silence.

    Today asset_swap_engine only persists on ADOPT_CANDIDATE (asset_swap_engine.py:695-705).
    A KEEP_INCUMBENT or REJECT proposal writes nothing -> advisor_observations stays
    empty -> the operator concludes the advisor is dead.
    """

    def test_keep_incumbent_swap_persists_an_observation_row(self, swap_engine):
        """A propose_operator_swap that results in KEEP_INCUMBENT must write a row.

        We force KEEP_INCUMBENT with a worse-than-baseline candidate (low signal),
        and assert insert_advisor_observation was called at least once regardless of
        the verdict. Network mocked; DB persistence captured.
        """
        # Near-zero candidate signal -> gate will not ADOPT (KEEP or REJECT).
        fake_bt = _flat_returns_backtest(per_day_pct=0.0, n=200)

        persisted = []

        def _capture(**kwargs):
            persisted.append(kwargs)
            return len(persisted)

        score_tree = {"id": "sym-rc4", "type": "root", "children": [
            {"type": "asset", "ticker": "SPY", "weight": 1.0}
        ]}
        objective = swap_engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("sym-rc4", "sym-other"),
            measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=fake_bt),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            result = swap_engine.propose_operator_swap(
                symphony_id="sym-rc4",
                score_tree=score_tree,
                incumbent_asset="SPY",
                candidate_asset="IALT",
                objective=objective,
                incumbent_oos_alpha=50.0,    # strong incumbent -> candidate cannot beat it
                default_oos_alpha=50.0,
            )

        # Establish the verdict is NOT adopt (so this is the regardless-of-verdict path).
        decisions = [r.verdict.decision for r in result.gate_batch.results]
        assert "ADOPT_CANDIDATE" not in decisions, (
            "Test setup invalid: expected a non-ADOPT verdict to exercise the "
            f"persist-on-KEEP path; got {decisions!r}."
        )

        assert len(persisted) >= 1, (
            "propose_operator_swap wrote NO advisor_observations row for a "
            f"{decisions!r} verdict. RC-4: the engine must persist an observation "
            "REGARDLESS of verdict so the operator sees it ran and kept the incumbent — "
            "otherwise advisor_observations stays empty and the advisor looks dead."
        )
        # The persisted row must carry the actual verdict (not be mislabeled as ADOPT).
        verdicts_written = [k.get("verdict") for k in persisted]
        assert any(v in ("KEEP_INCUMBENT", "REJECT_VETO_FAILED") for v in verdicts_written), (
            f"The persisted observation verdict(s) {verdicts_written!r} must reflect the "
            "real gate decision (KEEP_INCUMBENT / REJECT_VETO_FAILED), not be hardcoded "
            "ADOPT_CANDIDATE. RC-4: the audit trail must be truthful."
        )

    def test_keep_incumbent_observation_is_advisory_only(self, swap_engine):
        """The regardless-of-verdict persistence must still set is_advisory_only=1.

        Guards against a fix that persists KEEP rows but forgets the advisory flag —
        a non-advisory row could be mistaken for a live decision.
        """
        fake_bt = _flat_returns_backtest(per_day_pct=0.0, n=200)
        persisted = []

        def _capture(**kwargs):
            persisted.append(kwargs)
            return len(persisted)

        score_tree = {"id": "sym-rc4b", "type": "root", "children": [
            {"type": "asset", "ticker": "SPY", "weight": 1.0}
        ]}
        objective = swap_engine.SwapObjective(
            objective_type="reduce_correlation", target_pair=None, measured_value=0.6,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=fake_bt),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            swap_engine.propose_operator_swap(
                symphony_id="sym-rc4b",
                score_tree=score_tree,
                incumbent_asset="SPY",
                candidate_asset="AGG",
                objective=objective,
                incumbent_oos_alpha=50.0,
                default_oos_alpha=50.0,
            )

        assert persisted, (
            "No observation persisted on a non-ADOPT verdict (RC-4 not satisfied)."
        )
        for kw in persisted:
            assert kw.get("is_advisory_only") in (1, True), (
                f"A regardless-of-verdict observation was written with is_advisory_only="
                f"{kw.get('is_advisory_only')!r}. It must always be 1 — the advisor never "
                "moves money."
            )

    def test_accept_oos_revalidation_rejection_is_surfaced(self, flask_client):
        """POST /ai-advisor/accept where OOS revalidation FAILS must surface the
        rejection detail to the operator — explaining why llm_suggestions stays empty.

        The route already returns {"status":"rejected","error":detail} on failure
        (app.py:3339-3340). This pins that contract: the operator must see WHY the
        accept was rejected, not a silent success or a swallowed error.
        """
        rejection_detail = "OOS revalidation failed: candidate underperformed on the holdout fold"

        with (
            patch("app.ai_advisor.enforce_suggestion_allowlist", return_value=(["k"], [])),
            patch("app.ai_advisor.check_risk_direction_agreement", return_value=None),
            patch("app.database.get_symphony_strategy",
                  return_value={"params": {}, "locked_vars": []}),
            patch(
                "app.ai_advisor.revalidate_suggestion_oos",
                return_value={"passed": False, "detail": rejection_detail},
            ),
            patch("app.database.record_llm_suggestion") as mock_record,
            patch("app.database.save_symphony_strategy") as mock_save,
        ):
            resp = flask_client.post(
                "/ai-advisor/accept",
                json={
                    "symphony_id": _SYM_NAME,
                    "suggestion": {
                        "config_key": "MOMENTUM_LOOKBACK_DAYS",
                        "current_value": 20,
                        "suggested_value": 10,
                        "rationale": "tighten",
                        "risk_direction": "neutral",
                        "confidence": "medium",
                        "data_sufficiency": "sufficient",
                    },
                },
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("status") == "rejected", (
            f"Expected status='rejected' on OOS-revalidation failure, got {data!r}. "
            "RC-4: the rejection must be surfaced so the operator understands why no "
            "suggestion was accepted (empty llm_suggestions)."
        )
        assert rejection_detail in (data.get("error") or ""), (
            f"The OOS-revalidation rejection DETAIL must be surfaced. Got error="
            f"{data.get('error')!r}; expected to contain {rejection_detail!r}."
        )
        # A rejected accept must NOT write to the audit trail or mutate strategy.
        mock_record.assert_not_called()
        mock_save.assert_not_called()


# ===========================================================================
# RC-5 — broad excepts must surface the error class, not a silent 200-empty.
# ===========================================================================


class TestRC5NoSilentMasking:
    """An injected engine error on the advise path must be REPORTED, not masked.

    Today app.py:2935 catches and returns {"error": f"evaluation error: {exc}"}, 200.
    The error message must name the failure (so the operator/log sees it). RC-5 fails
    if a genuine engine error degrades to a generic / empty success.
    """

    def test_asset_swap_evaluate_engine_error_is_reported_not_silent(self, flask_client):
        """When propose_operator_swap raises, the route must surface the error class.

        We inject a real exception into the REAL route's engine call (mock only the
        engine function to raise — the network the engine would call is the thing we
        cannot reach in a test). The response must NOT be a silent success: it must
        carry an error that names the failure.
        """
        boom = RuntimeError("composer backtest exploded: 503 upstream")

        with (
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch("symphony_logic.fetch_symphony_score",
                  return_value={"id": _SYM_HASH, "name": _SYM_NAME, "children": []}),
            patch("database.load_state", return_value=_bot_state_with_mapping()),
            patch("advisors.asset_swap_engine.propose_operator_swap", side_effect=boom),
        ):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": _SYM_NAME, "from_ticker": "SPY", "to_ticker": "QQQ"},
            )

        data = resp.get_json()
        assert data is not None
        err = (data.get("error") or "")
        assert err, (
            "The route returned NO error after the engine raised — a silent 200-empty. "
            "RC-5: an engine failure must be surfaced, never masked."
        )
        # The surfaced error must name the failure CLASS (not a generic opaque string).
        assert "RuntimeError" in err or "composer backtest exploded" in err, (
            f"The route surfaced an opaque error {err!r} that hides the failure class. "
            "RC-5: the broad except must surface the error class / message so the "
            "operator and logs can diagnose it — not degrade to a generic 'evaluation error'."
        )

    def test_persistence_failure_is_surfaced_not_swallowed(self, swap_engine):
        """A persistence failure during propose_operator_swap must be SURFACED,
        not silently logged-and-dropped.

        Originally the `_persist_survivor` call was wrapped in `try/except Exception:
        logger.warning(...)` — a recommendation was presented while its
        advisor_observations row never landed. RC-5: when insert_advisor_observation
        raises, the SwapRunResult must carry a persistence-error signal (a non-empty
        persistence_error / error field, or a per-proposal not-persisted flag).

        Note: post-RC-4 the engine persists REGARDLESS of verdict, so the
        persist-failure path is exercised on ANY decision — we use a varied series
        (not a flat constant, which fails the BHY veto with zero variance) and assert
        the failure is surfaced no matter what the gate decided.
        """
        fake_bt = _varied_returns_backtest(seed=11, n=700, mean_pct=0.5)

        score_tree = {"id": "sym-rc5p", "type": "root", "children": [
            {"type": "asset", "ticker": "SPY", "weight": 1.0}
        ]}
        objective = swap_engine.SwapObjective(
            objective_type="reduce_correlation", target_pair=None, measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=fake_bt),
            patch("database.insert_advisor_observation",
                  side_effect=RuntimeError("DB write failed: disk I/O error")),
        ):
            result = swap_engine.propose_operator_swap(
                symphony_id="sym-rc5p",
                score_tree=score_tree,
                incumbent_asset="SPY",
                candidate_asset="IALT",
                objective=objective,
                incumbent_oos_alpha=-50.0,   # very weak incumbent to encourage ADOPT
                default_oos_alpha=-50.0,
            )

        # The engine persists regardless of verdict (RC-4); the persist call fired
        # and raised, so the failure MUST be surfaced. The verdict itself is not the
        # point — the swallowed-vs-surfaced behavior is.
        decisions = [r.verdict.decision for r in result.gate_batch.results]
        assert decisions, "Test setup invalid: the gate produced no results."

        # The persistence failure must be visible somewhere on the result — a
        # persistence_error field, an error field, or a per-survivor not-persisted flag.
        surfaced = False
        run_err = (getattr(result, "persistence_error", None)
                   or getattr(result, "error", None))
        if run_err:
            surfaced = True
        else:
            for p in result.proposals:
                if getattr(p, "persistence_error", None) or getattr(p, "persist_failed", None):
                    surfaced = True
                    break
        assert surfaced, (
            "An ADOPT survivor's persistence failure was SWALLOWED — the SwapRunResult "
            "presents an adopted survivor with no signal that its advisor_observations "
            "row never landed. RC-5: the broad `except Exception` around _persist_survivor "
            "(asset_swap_engine.py:708-715) must surface the failure (e.g. a "
            "persistence_error field) so the operator is not shown a recommendation that "
            "was never recorded."
        )

    def test_suggest_route_engine_error_is_reported_not_generic(self, flask_client):
        """POST /ai-advisor/suggest must surface the underlying error class, not the
        opaque 'An internal error occurred' (app.py:3298).

        RC-5: the historical failure mode (feedback_verify_feature_works_live_not_tests_green)
        was a swallowed exception degrading to an opaque error the operator can't act on.
        """
        boom = ValueError("anthropic SDK auth failure: invalid x-api-key")

        with (
            patch("app.ai_advisor.assemble_advisor_context", side_effect=boom),
        ):
            resp = flask_client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": _SYM_NAME},
            )

        data = resp.get_json()
        assert data is not None
        err = (data.get("error") or "")
        assert err, "suggest route returned no error after the engine raised (silent masking)."
        assert err.strip().lower() != "an internal error occurred", (
            "POST /ai-advisor/suggest degraded a real engine error to the opaque "
            "'An internal error occurred'. RC-5: surface the error class so the "
            "operator/log can diagnose (e.g., ValueError / auth failure)."
        )
        assert "ValueError" in err or "auth failure" in err, (
            f"The surfaced error {err!r} hides the failure class. RC-5: name the error."
        )


# ===========================================================================
# RC-6 — unresolvable symphony name must FAIL LOUDLY, not silent pass-through.
# ===========================================================================


class TestRC6FailLoudOnUnresolvableName:
    """A name that has no bot_state hash mapping must produce an explicit error,
    NOT the silent `composer_hash = symphony_id` pass-through that backtests a
    non-hash id and silently returns empty (app.py:2901-2912).
    """

    def test_asset_swap_evaluate_unresolvable_name_fails_loudly(self, flask_client):
        """An unknown symphony NAME (absent from bot_state) must yield an explicit
        'could not resolve' error and must NOT call fetch_symphony_score with the NAME.
        """
        captured = {"fetch_called_with": "__never__"}

        def _spy_fetch(identifier):
            captured["fetch_called_with"] = identifier
            return {}  # Composer returns nothing for a non-hash id

        with (
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            # bot_state has a DIFFERENT symphony — the submitted name is unresolvable.
            patch("database.load_state",
                  return_value={"otherHASH": {"name": "some other symphony", "account_uuid": "x"}}),
            patch("symphony_logic.fetch_symphony_score", side_effect=_spy_fetch),
            patch("advisors.asset_swap_engine.propose_operator_swap") as mock_engine,
        ):
            resp = flask_client.post(
                "/ai-advisor/asset-swaps/evaluate",
                json={"symphony_id": "a name that does not exist anywhere",
                      "from_ticker": "SPY", "to_ticker": "QQQ"},
            )

        data = resp.get_json()
        assert data is not None
        err = (data.get("error") or "").lower()
        assert "resolve" in err or "not found" in err or "unknown symphony" in err, (
            f"An unresolvable symphony name produced error={data.get('error')!r}. "
            "RC-6: it must FAIL LOUDLY with an explicit 'could not resolve name to a "
            "Composer hash' error, NOT the silent `composer_hash = symphony_id` "
            "pass-through that backtests the name and returns empty."
        )
        # The route must NOT have passed the raw NAME to fetch_symphony_score
        # (that is the silent pass-through bug).
        assert captured["fetch_called_with"] != "a name that does not exist anywhere", (
            "The route passed the raw NAME to fetch_symphony_score — the silent "
            "pass-through. RC-6: a name with no hash mapping must fail before fetch."
        )
        # And the engine must never be invoked with an unresolved name.
        mock_engine.assert_not_called()

    def test_logic_change_evaluate_unresolvable_name_fails_loudly(self, flask_client):
        """Same fail-loud contract on the logic-changes/evaluate route."""
        with (
            patch("advisors.logic_change_engine._has_composer_key", return_value=True),
            patch("database.load_state",
                  return_value={"otherHASH": {"name": "some other symphony", "account_uuid": "x"}}),
            patch("symphony_logic.fetch_symphony_score", return_value={}),
            patch("advisors.logic_change_engine.propose_operator_logic_change") as mock_engine,
        ):
            resp = flask_client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={"symphony_id": "a name that does not exist anywhere",
                      "change_description": "change momentum lookback from 20 to 10 days"},
            )

        data = resp.get_json()
        assert data is not None
        err = (data.get("error") or "").lower()
        assert "resolve" in err or "not found" in err or "unknown symphony" in err, (
            f"logic-changes/evaluate produced error={data.get('error')!r} for an "
            "unresolvable name. RC-6: fail loudly, do not silently pass the name through."
        )
        mock_engine.assert_not_called()


# ===========================================================================
# DE-retire — Divergence Explainer must NOT appear as a producer / recommendation.
# ===========================================================================


class TestDivergenceExplainerRetiredFromSurface:
    """The CVaR-divergence core is permanently rejected (flag stays off). DE must
    not be surfaced as a producer/recommendation — it reads as dead, misleading
    the operator. The producer-role aggregation must exclude DIVERGENCE_EXPLAINER.
    """

    def test_advisor_roles_excludes_divergence_explainer(self):
        """app._ADVISOR_ROLES (the producer/recommendation aggregation) must not
        include DIVERGENCE_EXPLAINER.

        That list drives both the /ai-advisor page observations and
        /api/advisor-observations (the producer surface). DE in it means DE rows are
        fetched and surfaced as recommendations.
        """
        _ensure_repo_on_path()
        import app as _app
        assert "DIVERGENCE_EXPLAINER" not in _app._ADVISOR_ROLES, (
            f"_ADVISOR_ROLES still includes DIVERGENCE_EXPLAINER: {_app._ADVISOR_ROLES!r}. "
            "DE-retire (PM decision): the Divergence Explainer must NOT be surfaced as a "
            "producer/recommendation — its CVaR-divergence core is permanently rejected."
        )

    def test_api_advisor_observations_excludes_divergence_explainer_rows(self, flask_client):
        """GET /api/advisor-observations (no symphony filter) must not return any
        DIVERGENCE_EXPLAINER rows in the producers/recommendations surface.

        We stub the per-role DB accessor to return a DE row IF asked for the DE role;
        the route must never request that role (DE retired from _ADVISOR_ROLES).
        """
        de_row = {
            "id": 9001,
            "advisor_role": "DIVERGENCE_EXPLAINER",
            "verdict": "NOT_APPLICABLE",
            "raw_response": {"feature_flag": "off"},
            "subject_id": "1",
            "symphony_id": _SYM_NAME,
        }
        oc_row = {
            "id": 9002,
            "advisor_role": "OVERFITTING_CONSCIENCE",
            "verdict": "CLEAR",
            "raw_response": {},
            "subject_id": "2",
            "symphony_id": _SYM_NAME,
        }

        def _by_role(role, limit=50):
            if role == "DIVERGENCE_EXPLAINER":
                return [de_row]
            if role == "OVERFITTING_CONSCIENCE":
                return [oc_row]
            return []

        with patch("database.get_advisor_observations_for_role", side_effect=_by_role):
            resp = flask_client.get("/api/advisor-observations")

        assert resp.status_code == 200
        rows = resp.get_json()
        assert rows is not None
        roles_returned = {r.get("advisor_role") for r in rows}
        assert "DIVERGENCE_EXPLAINER" not in roles_returned, (
            f"/api/advisor-observations surfaced a DIVERGENCE_EXPLAINER row. "
            f"Roles returned: {sorted(r for r in roles_returned if r)}. "
            "DE-retire: the producer surface must exclude DE entirely."
        )
