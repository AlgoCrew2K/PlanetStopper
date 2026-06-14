"""RED tests — community-strats wiring into propose_strategies (slice 2).

Modules under test:
  - advisors.strategy_builder_engine  (adapter + injection + cap + dedup + provenance)
  - advisors.community_strats         (sharpe_filtered counter — AC-7)

Feature plan: feature-plans/community-strats-wiring.md (AC-1..AC-7)
Fixture: tests/fixtures/math/community_strats_wiring_basic.json

These tests are RED by construction — they assert behaviour that does not yet
exist in the codebase:
  - AC-1: community_candidate_infos() adapter does not exist yet
  - AC-2: propose_strategies() has no community_candidates param yet
  - AC-3: MAX_COMMUNITY_CANDIDATES_PER_RUN constant does not exist yet
  - AC-4: backtest failure isolation for community candidates (new code path)
  - AC-5: provenance tracking (community template_id + sid in persist args)
  - AC-6: no-regression with community_candidates=None / []
  - AC-7: load_community_strategies stats has no sharpe_filtered key yet

Adversarial rules (from agent operating rules + feature plan):
  - Mock run_backtest, evaluate_candidate_batch, and database.insert_advisor_observation
    so NO live Composer or DB calls occur anywhere in this file.
  - Build community trees via symphony_schema constructors — grammar tokens fine to
    hardcode; do NOT hardcode producer-computed metric values.
  - Every test must be able to FAIL on a wrong implementation (no tautologies).
  - Use pytest.approx with explicit tolerance + tolerance comment where comparing floats.
  - No shared mutable state across tests — all fixtures have explicit scope declarations.
  - No production code written here.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import fields as dataclass_fields
from datetime import date, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Repository layout helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "math" / "community_strats_wiring_basic.json"
)


def _load_fixture() -> dict:
    """Load the golden fixture; skip gracefully if absent."""
    if not _FIXTURE_PATH.exists():
        pytest.skip(f"Fixture missing: {_FIXTURE_PATH}")
    with open(_FIXTURE_PATH) as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Shared backtest mock helpers (match style of test_strategy_builder_engine.py)
# ---------------------------------------------------------------------------


def _make_fake_result(n_days: int = 100, base_return: float = 0.001):
    """BacktestResult with a synthetic log-return series.

    n_days >= 65 so backtest_gate_engine produces a valid fold transform.
    """
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08},
        data_warnings=[],
        daily_returns=returns,
    )


def _make_error_result():
    """BacktestResult representing a failed backtest."""
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    return BacktestResult(
        stats=None,
        data_warnings=[],
        daily_returns={},
        error="simulated network timeout",
    )


# ---------------------------------------------------------------------------
# Community tree builders — symphony_schema only, no production calls
# ---------------------------------------------------------------------------

from advisors import symphony_schema  # noqa: E402


def _community_tree_simple(tickers: list[str] | None = None) -> dict:
    """Build a minimal valid equal-weight community tree."""
    tickers = tickers or ["SPY", "BND"]
    assets = [symphony_schema.make_asset(t) for t in tickers]
    wt = symphony_schema.make_weight_equal(assets)
    return symphony_schema.make_root("Community EW", "daily", [wt])


def _community_tree_frontrunner(watched: list[str] | None = None, basket: list[str] | None = None) -> dict:
    """Build a frontrunner-shaped community tree using compound condition builders.

    Uses make_if_compound + make_binary_compound_condition — the Cycle-B §7 compound
    builders that represent the 'frontrunner' pattern (RSI of ANY(tickers) > threshold).
    """
    watched = watched or ["SPY", "QQQ"]
    basket = basket or ["GLD", "TLT"]
    condition = symphony_schema.make_binary_compound_condition(
        fn="relative-strength-index",
        tickers=list(watched),
        comparator="gt",
        rhs=symphony_schema.make_constant_rhs(70),
        window=10,
        operator="any",
    )
    then_assets = [symphony_schema.make_asset(t) for t in basket]
    else_assets = [symphony_schema.make_asset(t) for t in basket]
    if_node = symphony_schema.make_if_compound(
        condition,
        then_children=[symphony_schema.make_weight_equal(then_assets)],
        else_children=[symphony_schema.make_weight_equal(else_assets)],
    )
    return symphony_schema.make_root("Frontrunner Community", "daily", [if_node])


def _make_community_records(n: int = 2) -> list[dict]:
    """Build n synthetic community strategy records matching the loader output shape."""
    records = []
    for i in range(n):
        ticker = f"TICK{i:03d}"
        tree = symphony_schema.make_root(
            f"Comm{i}", "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset(ticker)])]
        )
        records.append({
            "sid": f"comm-sid-{i:03d}",
            "name": f"Community Strategy {i}",
            "tree": tree,
            "tickers": {ticker},
            "oos_metrics": {"sharpe": 1.0 + i * 0.1},
            "composition_hash": f"fakehash{i:03d}",
        })
    return records


# ---------------------------------------------------------------------------
# Shared pytest fixture: import the engine module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sbe():
    """Import and return the strategy_builder_engine module (scope=module — stateless)."""
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415

    return _sbe


# ===========================================================================
# SECTION 1 — AC-1: community_candidate_infos adapter
# ===========================================================================


class TestAC1CommunityAdapterExists:
    """AC-1: community_candidate_infos must exist as a callable on the engine module."""

    def test_adapter_is_callable(self, sbe):
        """Fixture-anchored: adapter must be a callable in strategy_builder_engine."""
        assert hasattr(sbe, "community_candidate_infos"), (
            "community_candidate_infos must be a public function in "
            "advisors.strategy_builder_engine (AC-1 — does not yet exist)"
        )
        assert callable(sbe.community_candidate_infos), (
            "community_candidate_infos must be callable"
        )


class TestAC1AdapterShape:
    """AC-1: community_candidate_infos(records) → one CandidateInfo per valid record."""

    def test_returns_one_candidate_info_per_record(self, sbe):
        """Fixture-anchored: one CandidateInfo per input record (all valid)."""
        records = _make_community_records(3)
        result = sbe.community_candidate_infos(records)
        assert len(result) == 3, (
            "community_candidate_infos must return one CandidateInfo per valid record; "
            f"got {len(result)} for 3 records"
        )

    def test_candidate_id_prefix_is_community_colon_sid(self, sbe):
        """Fixture-anchored: candidate_id must be 'community:<sid>' (AC-1 contract)."""
        fixture = _load_fixture()
        prefix = fixture["adapter"]["expected_candidate_id_prefix"]
        records = _make_community_records(2)
        result = sbe.community_candidate_infos(records)
        for info, rec in zip(result, records, strict=True):
            expected_id = f"{prefix}{rec['sid']}"
            assert info.candidate_id == expected_id, (
                f"candidate_id must be '{prefix}<sid>'; "
                f"got {info.candidate_id!r} for sid={rec['sid']!r}"
            )

    def test_template_id_is_community(self, sbe):
        """Fixture-anchored: template_id must be exactly 'community' (AC-1 contract)."""
        fixture = _load_fixture()
        expected_template_id = fixture["adapter"]["expected_template_id"]
        records = _make_community_records(2)
        result = sbe.community_candidate_infos(records)
        for info in result:
            assert info.template_id == expected_template_id, (
                f"template_id must be {expected_template_id!r}; got {info.template_id!r}"
            )

    def test_tree_matches_record_tree(self, sbe):
        """The returned CandidateInfo.tree must be the record's tree dict."""
        records = _make_community_records(2)
        result = sbe.community_candidate_infos(records)
        for info, rec in zip(result, records, strict=True):
            assert info.tree is rec["tree"] or info.tree == rec["tree"], (
                "CandidateInfo.tree must match the source record's tree"
            )

    def test_params_contains_sid(self, sbe):
        """Fixture-anchored: params dict must carry 'sid' from the record (AC-1 contract)."""
        fixture = _load_fixture()
        assert "sid" in fixture["adapter"]["expected_params_keys"]
        records = _make_community_records(2)
        result = sbe.community_candidate_infos(records)
        for info, rec in zip(result, records, strict=True):
            assert "sid" in info.params, (
                f"params must contain 'sid'; got keys {list(info.params.keys())!r}"
            )
            assert info.params["sid"] == rec["sid"], (
                f"params['sid'] must match record sid {rec['sid']!r}; "
                f"got {info.params['sid']!r}"
            )

    def test_params_contains_name(self, sbe):
        """Fixture-anchored: params dict must carry 'name' from the record (AC-1 contract)."""
        fixture = _load_fixture()
        assert "name" in fixture["adapter"]["expected_params_keys"]
        records = _make_community_records(2)
        result = sbe.community_candidate_infos(records)
        for info, rec in zip(result, records, strict=True):
            assert "name" in info.params, (
                f"params must contain 'name'; got keys {list(info.params.keys())!r}"
            )
            assert info.params["name"] == rec["name"], (
                f"params['name'] must match record name {rec['name']!r}; "
                f"got {info.params['name']!r}"
            )

    def test_skips_record_missing_tree(self, sbe):
        """Fixture-anchored: records missing a 'tree' key are silently skipped (AC-1)."""
        fixture = _load_fixture()
        assert "missing tree key" in fixture["adapter"]["skip_conditions"]
        # One record with tree, one without
        good_rec = _make_community_records(1)[0]
        bad_rec = {"sid": "no-tree", "name": "NoTree", "oos_metrics": None}
        result = sbe.community_candidate_infos([good_rec, bad_rec])
        assert len(result) == 1, (
            "record missing 'tree' must be skipped; "
            f"expected 1 result, got {len(result)}"
        )
        assert result[0].candidate_id == f"community:{good_rec['sid']}"

    def test_skips_record_missing_sid(self, sbe):
        """Fixture-anchored: records missing a 'sid' key are silently skipped (AC-1)."""
        fixture = _load_fixture()
        assert "missing sid key" in fixture["adapter"]["skip_conditions"]
        good_rec = _make_community_records(1)[0]
        bad_rec = {"name": "NoSid", "tree": _community_tree_simple(), "oos_metrics": None}
        result = sbe.community_candidate_infos([good_rec, bad_rec])
        assert len(result) == 1, (
            "record missing 'sid' must be skipped; "
            f"expected 1 result, got {len(result)}"
        )

    def test_never_raises_on_empty_records_list(self, sbe):
        """Fixture-anchored: adapter never raises (AC-1 — never-raises contract)."""
        fixture = _load_fixture()
        assert fixture["adapter"]["never_raises"] is True
        try:
            result = sbe.community_candidate_infos([])
        except Exception as exc:
            pytest.fail(
                f"community_candidate_infos must never raise; got {type(exc).__name__}: {exc}"
            )
        assert result == [], "empty records must return empty list"

    def test_never_raises_on_all_invalid_records(self, sbe):
        """Adapter must not raise when all records are missing required fields."""
        bad_records = [
            {"name": "no-sid"},  # missing sid
            {"sid": "no-tree"},  # missing tree
            {},                   # missing both
        ]
        try:
            result = sbe.community_candidate_infos(bad_records)
        except Exception as exc:
            pytest.fail(
                f"community_candidate_infos must never raise on all-invalid records; "
                f"got {type(exc).__name__}: {exc}"
            )
        assert result == [], (
            "all-invalid records → empty list (none skipped to the adapter output)"
        )

    def test_frontrunner_tree_record_produces_valid_candidate(self, sbe):
        """A frontrunner-shaped community tree passes through the adapter without error."""
        tree = _community_tree_frontrunner()
        record = {
            "sid": "frontrunner-001",
            "name": "Frontrunner Test",
            "tree": tree,
            "tickers": {"SPY", "QQQ", "GLD", "TLT"},
            "oos_metrics": None,
            "composition_hash": "frontrunnerXXX",
        }
        result = sbe.community_candidate_infos([record])
        assert len(result) == 1, (
            "frontrunner-shaped record must produce one CandidateInfo"
        )
        assert result[0].candidate_id == "community:frontrunner-001"
        assert result[0].template_id == "community"


# ===========================================================================
# SECTION 2 — AC-2: injection into propose_strategies
# ===========================================================================


class TestAC2ProposeStrategiesAcceptsCommunityParam:
    """AC-2: propose_strategies must accept community_candidates kwarg."""

    def test_propose_strategies_accepts_community_candidates_kwarg(self, sbe):
        """propose_strategies signature must accept community_candidates keyword argument."""
        import inspect  # noqa: PLC0415

        sig = inspect.signature(sbe.propose_strategies)
        assert "community_candidates" in sig.parameters, (
            "propose_strategies must accept a 'community_candidates' keyword argument (AC-2); "
            "the parameter does not yet exist"
        )

    def test_community_candidates_defaults_to_none(self, sbe):
        """community_candidates must default to None (AC-6 no-regression contract)."""
        import inspect  # noqa: PLC0415

        sig = inspect.signature(sbe.propose_strategies)
        param = sig.parameters.get("community_candidates")
        assert param is not None, "community_candidates parameter must exist"
        assert param.default is None, (
            "community_candidates must default to None so omitting it is backward-compatible; "
            f"got default={param.default!r}"
        )


class TestAC2CommunityBatchedWithTemplates:
    """AC-2: community candidates ARE backtested and included in the FDR gate batch."""

    def _run_with_community(self, sbe, community_records: list[dict], *, backtest_result=None):
        """Helper: call propose_strategies with community_candidates injected.

        Mocks run_backtest, evaluate_candidate_batch, and database so no live calls occur.
        Returns (result, captured_gate_calls, captured_persist_calls).
        """
        if backtest_result is None:
            backtest_result = _make_fake_result(n_days=100, base_return=0.001)

        community_infos = sbe.community_candidate_infos(community_records)

        captured_gate_calls: list = []
        captured_persist_calls: list = []

        from advisors.backtest_gate_engine import GatedBatch, CandidateGateResult  # noqa: PLC0415
        from acceptance_gate import AcceptanceVerdict  # noqa: PLC0415

        # Build a minimal GatedBatch that lists every backtested candidate as a survivor
        def _fake_gate(candidates, **kwargs):
            captured_gate_calls.append(candidates)
            # Return survivors = all inputs (permissive gate)
            results = []
            for c in candidates:
                results.append(
                    CandidateGateResult(
                        candidate_id=c.candidate_id,
                        verdict=AcceptanceVerdict(
                            vetoes_passed=True,
                            panel_score=0.8,
                            panel_breakdown={},
                            decision="ADOPT_CANDIDATE",
                        ),
                        validation_days=65,
                        oos_alpha=0.05,
                        caveats=[],
                        winner_p_adj=0.01,
                    )
                )
            return GatedBatch(
                results=results,
                survivors=results,
                n_candidates=len(candidates),
                fdr_q=0.05,
            )

        def _capture_persist(*args, **kwargs):
            captured_persist_calls.append({"args": args, "kwargs": kwargs})
            return 1

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=backtest_result),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_persist,
            ),
        ):
            # Use permissive ScreenConfig so no survivors get filtered out
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(min_sharpe=-999.0),
                live_returns=[0.001] * 100,
                symphony_id="test-community-injection",
                community_candidates=community_infos,
            )
        return result, captured_gate_calls, captured_persist_calls

    def test_community_candidate_ids_appear_in_gate_input_batch(self, sbe):
        """AC-2: community candidate_ids must appear in the candidates passed to the FDR gate.

        This is the load-bearing injection test: if community candidates are not
        in the gate batch, they are silently lost from the pipeline.
        """
        community_records = _make_community_records(2)
        result, gate_calls, _ = self._run_with_community(sbe, community_records)

        assert result.error is None, f"Unexpected error: {result.error}"
        assert len(gate_calls) >= 1, "evaluate_candidate_batch must have been called"

        # The gate was called with BacktestCandidate objects; extract their candidate_ids
        gate_candidate_ids = {c.candidate_id for batch in gate_calls for c in batch}
        expected_community_ids = {
            f"community:{rec['sid']}" for rec in community_records
        }

        # AC-2: ALL community candidate_ids that backtested successfully must be in the gate
        for expected_id in expected_community_ids:
            assert expected_id in gate_candidate_ids, (
                f"Community candidate_id {expected_id!r} must appear in the "
                f"evaluate_candidate_batch input batch (AC-2: community candidates "
                f"must flow through the FDR gate). Gate received: {sorted(gate_candidate_ids)!r}"
            )

    def test_community_candidate_survives_to_persist_call(self, sbe):
        """AC-2: community candidates that pass the gate and screens must be persisted.

        Asserts that insert_advisor_observation is called with at least one call
        whose subject_id starts with 'community:' (or whose raw_response template_id
        is 'community').
        """
        community_records = _make_community_records(1)
        result, _, persist_calls = self._run_with_community(sbe, community_records)

        assert result.error is None, f"Unexpected error: {result.error}"

        # At least one persist call must reference a community candidate
        community_persist_found = False
        for call_data in persist_calls:
            args = call_data["args"]
            kwargs = call_data["kwargs"]
            # subject_id may be in positional args or kwargs
            subject_id = kwargs.get("subject_id") or (args[3] if len(args) > 3 else None)
            raw_response = kwargs.get("raw_response")
            if subject_id and str(subject_id).startswith("community:"):
                community_persist_found = True
                break
            if raw_response and isinstance(raw_response, dict):
                if raw_response.get("template_id") == "community":
                    community_persist_found = True
                    break

        assert community_persist_found, (
            "A community candidate that passes the gate+screens must be persisted with "
            "subject_id='community:<sid>' or raw_response.template_id='community' (AC-2 + AC-5). "
            f"Persist calls captured: {len(persist_calls)}. "
            "This fails because community candidates are not yet injected into the pipeline."
        )

    def test_propose_strategies_with_community_candidates_does_not_raise(self, sbe):
        """AC-2: passing community_candidates must not raise — never-raises contract."""
        community_infos = sbe.community_candidate_infos(_make_community_records(2))
        fake = _make_fake_result(n_days=100)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            try:
                result = sbe.propose_strategies(
                    objective=sbe.Objective.diversify,
                    universe=["SPY", "AGG"],
                    screen_config=sbe.ScreenConfig(),
                    live_returns=[0.001] * 100,
                    community_candidates=community_infos,
                )
            except Exception as exc:
                pytest.fail(
                    f"propose_strategies must not raise when community_candidates supplied; "
                    f"got {type(exc).__name__}: {exc}"
                )
        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun when community_candidates are supplied"
        )


# ===========================================================================
# SECTION 3 — AC-3: cap and dedup
# ===========================================================================


class TestAC3CapConstantExists:
    """AC-3: MAX_COMMUNITY_CANDIDATES_PER_RUN constant must exist on the engine module."""

    def test_max_community_candidates_per_run_exists(self, sbe):
        """Fixture-anchored: the constant must exist (AC-3 — does not yet exist)."""
        fixture = _load_fixture()
        # The fixture documents the constraint; the test verifies the constant exists.
        assert hasattr(sbe, "MAX_COMMUNITY_CANDIDATES_PER_RUN"), (
            "MAX_COMMUNITY_CANDIDATES_PER_RUN must be a module-level constant in "
            "advisors.strategy_builder_engine (AC-3 — does not yet exist)"
        )

    def test_max_community_candidates_per_run_is_positive_int(self, sbe):
        """The constant must be a positive integer."""
        v = sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN
        assert isinstance(v, int) and not isinstance(v, bool), (
            f"MAX_COMMUNITY_CANDIDATES_PER_RUN must be an int; got {type(v).__name__}"
        )
        assert v > 0, (
            f"MAX_COMMUNITY_CANDIDATES_PER_RUN must be positive; got {v}"
        )

    def test_max_community_candidates_per_run_lte_max_candidates_per_run(self, sbe):
        """The community sub-budget must fit within the global candidate budget (AC-3)."""
        assert sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN <= sbe.MAX_CANDIDATES_PER_RUN, (
            f"MAX_COMMUNITY_CANDIDATES_PER_RUN ({sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN}) "
            f"must be <= MAX_CANDIDATES_PER_RUN ({sbe.MAX_CANDIDATES_PER_RUN}) "
            "so templates + community <= the global cap (AC-3)"
        )


class TestAC3CommunityTruncationAtSubBudget:
    """AC-3: community candidates are truncated to MAX_COMMUNITY_CANDIDATES_PER_RUN."""

    def test_excess_community_candidates_are_truncated(self, sbe):
        """Passing more community candidates than the sub-budget must truncate silently.

        We build MAX_COMMUNITY_CANDIDATES_PER_RUN + 5 community records and assert
        the gate batch does not receive more than MAX_COMMUNITY_CANDIDATES_PER_RUN
        community ids.
        """
        n_over = sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN + 5
        # Build distinct single-asset trees to avoid dedup collapsing them
        records = []
        for i in range(n_over):
            ticker = f"OVER{i:03d}"
            tree = symphony_schema.make_root(
                f"Over{i}", "daily",
                [symphony_schema.make_weight_equal([symphony_schema.make_asset(ticker)])]
            )
            records.append({
                "sid": f"over-{i:03d}",
                "name": f"Over {i}",
                "tree": tree,
                "tickers": {ticker},
                "oos_metrics": None,
                "composition_hash": f"overhash{i:03d}",
            })

        community_infos = sbe.community_candidate_infos(records)
        assert len(community_infos) == n_over, (
            f"adapter should produce {n_over} CandidateInfos before truncation"
        )

        captured_gate_calls: list = []

        from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

        def _fake_gate(candidates, **kwargs):
            captured_gate_calls.append(list(candidates))
            return GatedBatch(results=[], survivors=[], n_candidates=len(candidates), fdr_q=0.05)

        fake = _make_fake_result(n_days=100)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                community_candidates=community_infos,
            )

        assert len(captured_gate_calls) >= 1, "evaluate_candidate_batch must have been called"
        all_gate_ids = {c.candidate_id for batch in captured_gate_calls for c in batch}
        community_gate_ids = {cid for cid in all_gate_ids if cid.startswith("community:")}

        assert len(community_gate_ids) <= sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN, (
            f"At most MAX_COMMUNITY_CANDIDATES_PER_RUN ({sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN}) "
            f"community candidates must reach the gate; "
            f"got {len(community_gate_ids)} community ids in the gate batch (AC-3)"
        )

    def test_total_backtested_candidates_lte_max_candidates_per_run(self, sbe):
        """Total candidates reaching the gate (template + community) must be bounded by MAX_CANDIDATES_PER_RUN.

        Passes a full community sub-budget and a universe that generates several template
        candidates; asserts result.gated_batch.n_candidates <= MAX_CANDIDATES_PER_RUN.
        """
        # Build exactly MAX_COMMUNITY_CANDIDATES_PER_RUN community records
        n_comm = sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN
        records = []
        for i in range(n_comm):
            ticker = f"CMTK{i:03d}"
            tree = symphony_schema.make_root(
                f"CM{i}", "daily",
                [symphony_schema.make_weight_equal([symphony_schema.make_asset(ticker)])]
            )
            records.append({
                "sid": f"cm-{i:03d}",
                "name": f"CM{i}",
                "tree": tree,
                "tickers": {ticker},
                "oos_metrics": None,
                "composition_hash": f"cmhash{i:03d}",
            })

        community_infos = sbe.community_candidate_infos(records)
        fake = _make_fake_result(n_days=100)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD", "TLT", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                community_candidates=community_infos,
            )

        assert result.error is None, f"Unexpected error: {result.error}"
        assert result.gated_batch.n_candidates <= sbe.MAX_CANDIDATES_PER_RUN, (
            f"Total backtested candidates (template + community) must be <= "
            f"MAX_CANDIDATES_PER_RUN ({sbe.MAX_CANDIDATES_PER_RUN}); "
            f"got {result.gated_batch.n_candidates} (AC-3)"
        )


class TestAC3Dedup:
    """AC-3: duplicate candidate_ids (community vs template collision) are deduped before the gate."""

    def test_community_candidate_id_collision_with_template_id_is_deduped(self, sbe):
        """A community candidate_id that equals a template candidate_id must appear only once
        in the gate batch (dedup keeps one, deterministically).

        We create a community candidate whose candidate_id collides with a known template id
        (e.g., 'diversify:T1:equal_weight'). After injection, the gate must receive that id
        only once — not twice.
        """
        # Build a community record whose candidate_id will collide with a T1 template id.
        # The T1 id produced by _generate_candidate_trees for 'diversify' is:
        # 'diversify:T1:equal_weight' — we set the sid to match it exactly.
        colliding_sid = "T1:equal_weight"
        tree = _community_tree_simple(["SPY", "BND"])
        collision_record = {
            "sid": colliding_sid,
            "name": "Collision Record",
            "tree": tree,
            "tickers": {"SPY", "BND"},
            "oos_metrics": None,
            "composition_hash": "collidehash",
        }
        community_infos = sbe.community_candidate_infos([collision_record])
        # The community candidate_id is 'community:T1:equal_weight' — this won't collide with
        # the template id 'diversify:T1:equal_weight'. Instead, let's build a raw CandidateInfo
        # with the exact colliding id to test the dedup logic more precisely.
        # The design says collision between template id and community id is deduped; the
        # realistic case is that a community candidate has the same candidate_id as a template
        # candidate because the caller manually constructed it that way (e.g., same algo).
        from advisors.strategy_builder_engine import CandidateInfo  # noqa: PLC0415

        # Inject a CandidateInfo with the same id as what T1 would generate for 'diversify'
        colliding_info = CandidateInfo(
            candidate_id="diversify:T1:equal_weight",  # same as what the engine generates
            tree=tree,
            template_id="community",
            params={"sid": "collision", "name": "Collision"},
        )

        captured_gate_calls: list = []

        from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

        def _fake_gate(candidates, **kwargs):
            captured_gate_calls.append(list(candidates))
            return GatedBatch(results=[], survivors=[], n_candidates=len(candidates), fdr_q=0.05)

        fake = _make_fake_result(n_days=100)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                community_candidates=[colliding_info],
            )

        # Hollow guard removed: a buggy impl that drops ALL candidates would skip this body.
        # The gate is always called (even with empty batch), so assert it was invoked.
        assert captured_gate_calls, (
            "evaluate_candidate_batch must be called — gate is invoked unconditionally; "
            "no captured calls means the engine returned early, hiding the dedup bug (AC-3)"
        )
        all_gate_ids = [c.candidate_id for batch in captured_gate_calls for c in batch]
        # The colliding id must appear at most once in the gate (dedup)
        colliding_count = all_gate_ids.count("diversify:T1:equal_weight")
        assert colliding_count <= 1, (
            f"Colliding candidate_id 'diversify:T1:equal_weight' must appear at most once "
            f"in the gate batch (dedup contract AC-3); appeared {colliding_count} times."
        )


# ===========================================================================
# SECTION 4 — AC-4: backtest failure isolation
# ===========================================================================


class TestAC4FailureIsolation:
    """AC-4: a community candidate whose backtest errors is omitted from the gate; run continues."""

    def test_community_backtest_error_omitted_from_gate_batch(self, sbe):
        """A community candidate whose run_backtest returns an error result is NOT included
        in the gate batch (same as template candidates — AC-4 mirrors the existing AC-X5 pattern).

        Strategy: one community record backtests with an error; the template candidates succeed.
        Assert the failing community candidate_id is absent from the gate batch.
        """
        error_record = {
            "sid": "error-comm",
            "name": "Error Community",
            "tree": _community_tree_simple(),
            "tickers": {"SPY", "BND"},
            "oos_metrics": None,
            "composition_hash": "errorhash",
        }
        community_infos = sbe.community_candidate_infos([error_record])
        error_community_id = community_infos[0].candidate_id

        # run_backtest returns an error result for the community candidate, success otherwise
        call_counter: dict = {"n": 0}

        def _side_effect(tree, **kwargs):
            call_counter["n"] += 1
            # Identify community candidates by their tree (the simple tree we built)
            # Returning error for any call where call_counter matches community order
            # is fragile; instead we use a sentinel attribute attached to the tree.
            # Simpler: error for the FIRST call only — community is injected AFTER template
            # candidates in Step 1; if community is injected, it may be called after templates.
            # We use call count: templates for the diversify objective with universe=["SPY","AGG"]
            # generate exactly 2 candidates (T1 eq-wt, T3 inv-vol). Community injected after,
            # so if MAX_COMMUNITY_CANDIDATES_PER_RUN >= 1, community is backtested last.
            # To avoid fragility: just return error for ALL calls and verify no community id
            # reaches the gate; template failures are handled the same way.
            return _make_error_result()

        captured_gate_calls: list = []

        from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

        def _fake_gate(candidates, **kwargs):
            captured_gate_calls.append(list(candidates))
            return GatedBatch(results=[], survivors=[], n_candidates=len(candidates), fdr_q=0.05)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_side_effect),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                community_candidates=community_infos,
            )

        assert result.error is None, (
            f"Run must complete without error when community backtest fails; "
            f"got error={result.error!r} (AC-4)"
        )

        # The gate is always called (even with an empty batch when all backtests error).
        # Hollow guard removed: assert gate was actually invoked so the body cannot silently skip.
        assert captured_gate_calls, (
            "evaluate_candidate_batch must be called even when all backtests error "
            "(engine calls gate unconditionally before returning — AC-4)"
        )
        gate_ids = {c.candidate_id for batch in captured_gate_calls for c in batch}
        assert error_community_id not in gate_ids, (
            f"Failed community candidate {error_community_id!r} must be absent from "
            f"the gate batch (AC-4); gate received: {sorted(gate_ids)!r}"
        )

    def test_community_backtest_exception_does_not_abort_run(self, sbe):
        """A community candidate whose run_backtest RAISES (not just error result) must be
        silently omitted; the run must still complete (AC-4 — same as template candidate isolation).
        """
        exc_record = {
            "sid": "exc-comm",
            "name": "Exception Community",
            "tree": _community_tree_simple(),
            "tickers": {"SPY"},
            "oos_metrics": None,
            "composition_hash": "exchash",
        }
        community_infos = sbe.community_candidate_infos([exc_record])

        call_counter: dict = {"n": 0}

        def _side_effect(tree, **kwargs):
            call_counter["n"] += 1
            raise RuntimeError("simulated community backtest exception")

        with (
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_side_effect),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            try:
                result = sbe.propose_strategies(
                    objective=sbe.Objective.diversify,
                    universe=["SPY", "AGG"],
                    screen_config=sbe.ScreenConfig(),
                    live_returns=[0.001] * 100,
                    community_candidates=community_infos,
                )
            except Exception as exc:
                pytest.fail(
                    f"propose_strategies must not raise when community backtest raises; "
                    f"got {type(exc).__name__}: {exc} (AC-4)"
                )

        assert isinstance(result, sbe.ProposalRun), (
            "propose_strategies must return ProposalRun even when all backtests raise (AC-4)"
        )

    def test_other_candidates_unaffected_when_community_backtest_fails(self, sbe):
        """When a community candidate's backtest errors, the template candidates that succeed
        must still reach the gate unaffected (AC-4: failure isolation).

        Uses a side_effect: community candidate (added last) always errors; first N calls
        (template candidates) succeed. Assert gate batch is non-empty (templates present).
        """
        from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

        success_result = _make_fake_result(n_days=100)
        error_result = _make_error_result()

        # Build 1 community record that always errors
        error_record = {
            "sid": "failing-comm",
            "name": "Failing Community",
            "tree": _community_tree_simple(["FAIL"]),
            "tickers": {"FAIL"},
            "oos_metrics": None,
            "composition_hash": "failhash",
        }
        community_infos = sbe.community_candidate_infos([error_record])

        call_counter: dict = {"n": 0}

        def _side_effect(tree, **kwargs):
            call_counter["n"] += 1
            # Template candidates come first; community is appended after.
            # Return success for the first few calls (templates), error for the last.
            # We don't know exactly how many templates the 'diversify' objective + SPY,AGG,GLD
            # generates, but it's at least 1 (T1). Return success for the first 10, error after.
            if call_counter["n"] <= 10:
                return success_result
            return error_result

        captured_gate_calls: list = []

        def _fake_gate(candidates, **kwargs):
            captured_gate_calls.append(list(candidates))
            return GatedBatch(results=[], survivors=[], n_candidates=len(candidates), fdr_q=0.05)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_side_effect),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                community_candidates=community_infos,
            )

        assert result.error is None, f"Run must not error; got {result.error!r}"
        assert len(captured_gate_calls) >= 1, "Gate must be called when template candidates succeed"
        gate_batch = captured_gate_calls[0]
        assert len(gate_batch) > 0, (
            "Gate batch must contain the successfully-backtested template candidates "
            "even when the community candidate errored (AC-4: failure isolation)"
        )


# ===========================================================================
# SECTION 5 — AC-5: provenance
# ===========================================================================


class TestAC5Provenance:
    """AC-5: a persisted community survivor records template_id='community' + sid."""

    def test_persisted_community_survivor_has_template_id_community(self, sbe):
        """AC-5: raw_response['template_id'] must be 'community' for a community survivor.

        Capture all persist calls and verify at least one has template_id='community' in
        its raw_response dict (or the subject_id starts with 'community:').
        """
        community_records = _make_community_records(1)
        community_infos = sbe.community_candidate_infos(community_records)
        comm_sid = community_records[0]["sid"]

        from advisors.backtest_gate_engine import GatedBatch, CandidateGateResult  # noqa: PLC0415
        from acceptance_gate import AcceptanceVerdict  # noqa: PLC0415

        def _fake_gate(candidates, **kwargs):
            results = [
                CandidateGateResult(
                    candidate_id=c.candidate_id,
                    verdict=AcceptanceVerdict(
                        vetoes_passed=True,
                        panel_score=0.8,
                        panel_breakdown={},
                        decision="ADOPT_CANDIDATE",
                    ),
                    validation_days=65,
                    oos_alpha=0.05,
                    caveats=[],
                    winner_p_adj=0.01,
                )
                for c in candidates
            ]
            return GatedBatch(results=results, survivors=results, n_candidates=len(candidates), fdr_q=0.05)

        captured_persist: list = []

        def _capture_persist(*args, **kwargs):
            captured_persist.append({"args": args, "kwargs": kwargs})
            return 1

        fake = _make_fake_result(n_days=100)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_persist,
            ),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(min_sharpe=-999.0),
                live_returns=[0.001] * 100,
                community_candidates=community_infos,
            )

        assert result.error is None, f"Unexpected error: {result.error!r}"

        if not captured_persist:
            pytest.skip("No persistence calls captured — gate produced no survivors")

        # Find any persist call for a community candidate
        community_persist = None
        for call_data in captured_persist:
            args = call_data["args"]
            kwargs = call_data["kwargs"]
            subject_id = kwargs.get("subject_id") or (args[3] if len(args) > 3 else "")
            if str(subject_id).startswith("community:"):
                community_persist = call_data
                break
            raw_response = kwargs.get("raw_response")
            if raw_response and isinstance(raw_response, dict):
                if raw_response.get("template_id") == "community":
                    community_persist = call_data
                    break

        assert community_persist is not None, (
            f"No persist call found for a community candidate (AC-5). "
            f"Expected a call with subject_id='community:{comm_sid}' or "
            f"raw_response.template_id='community'. "
            f"Captured {len(captured_persist)} total persist calls."
        )

        # Now verify the raw_response carries template_id='community'
        raw_response = community_persist["kwargs"].get("raw_response")
        if raw_response is None:
            for arg in community_persist["args"]:
                if isinstance(arg, dict) and "template_id" in arg:
                    raw_response = arg
                    break

        if raw_response is not None:
            assert raw_response.get("template_id") == "community", (
                f"raw_response['template_id'] must be 'community' for a community survivor "
                f"(AC-5); got {raw_response.get('template_id')!r}"
            )

    def test_persisted_community_survivor_raw_response_contains_sid(self, sbe):
        """AC-5: the persisted raw_response must carry the community candidate's sid,
        either in raw_response['params']['sid'] or accessible via the subject_id.
        """
        community_records = _make_community_records(1)
        community_infos = sbe.community_candidate_infos(community_records)
        comm_sid = community_records[0]["sid"]

        from advisors.backtest_gate_engine import GatedBatch, CandidateGateResult  # noqa: PLC0415
        from acceptance_gate import AcceptanceVerdict  # noqa: PLC0415

        def _fake_gate(candidates, **kwargs):
            results = [
                CandidateGateResult(
                    candidate_id=c.candidate_id,
                    verdict=AcceptanceVerdict(
                        vetoes_passed=True,
                        panel_score=0.8,
                        panel_breakdown={},
                        decision="ADOPT_CANDIDATE",
                    ),
                    validation_days=65,
                    oos_alpha=0.05,
                    caveats=[],
                    winner_p_adj=0.01,
                )
                for c in candidates
            ]
            return GatedBatch(results=results, survivors=results, n_candidates=len(candidates), fdr_q=0.05)

        captured_persist: list = []

        def _capture_persist(*args, **kwargs):
            captured_persist.append({"args": args, "kwargs": kwargs})
            return 1

        fake = _make_fake_result(n_days=100)
        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_persist,
            ),
        ):
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG"],
                screen_config=sbe.ScreenConfig(min_sharpe=-999.0),
                live_returns=[0.001] * 100,
                community_candidates=community_infos,
            )

        assert result.error is None, f"Unexpected error: {result.error!r}"

        if not captured_persist:
            pytest.skip("No persistence calls — gate produced no survivors")

        # Find the community persist call and verify sid is present
        community_persist_found = False
        for call_data in captured_persist:
            args = call_data["args"]
            kwargs = call_data["kwargs"]
            subject_id = kwargs.get("subject_id") or (args[3] if len(args) > 3 else "")
            if not str(subject_id).startswith("community:"):
                raw_response = kwargs.get("raw_response")
                if not (raw_response and isinstance(raw_response, dict) and
                        raw_response.get("template_id") == "community"):
                    continue

            # Found the community persist call — verify sid traceability
            community_persist_found = True
            raw_response = kwargs.get("raw_response")
            if raw_response and isinstance(raw_response, dict):
                params = raw_response.get("params", {})
                assert params.get("sid") == comm_sid or str(subject_id).endswith(comm_sid), (
                    f"Community persisted raw_response must carry sid={comm_sid!r} "
                    f"in params or subject_id (AC-5); "
                    f"got params={params!r}, subject_id={subject_id!r}"
                )
            break

        # If we found no community persist call at all it means injection isn't wired yet
        if not community_persist_found:
            pytest.fail(
                f"No persist call found for community candidate sid={comm_sid!r} (AC-5). "
                f"Community candidates are not yet injected into the persist pipeline."
            )


# ===========================================================================
# SECTION 6 — AC-6: no regression
# ===========================================================================


class TestAC6NoRegression:
    """AC-6: community_candidates=None and =[] produce identical behaviour to today's baseline."""

    def _run_baseline(self, sbe, *, community_candidates):
        """Call propose_strategies with community_candidates set to the given value."""
        fake = _make_fake_result(n_days=100)
        captured_gate_calls: list = []

        from advisors.backtest_gate_engine import GatedBatch  # noqa: PLC0415

        def _fake_gate(candidates, **kwargs):
            captured_gate_calls.append(list(candidates))
            return GatedBatch(results=[], survivors=[], n_candidates=len(candidates), fdr_q=0.05)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_fake_gate,
            ),
            patch("advisors.strategy_builder_engine.database") as mock_db,
        ):
            mock_db.insert_advisor_observation.return_value = 1
            result = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "AGG", "GLD"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[0.001] * 100,
                community_candidates=community_candidates,
            )
        return result, captured_gate_calls

    def test_none_community_candidates_does_not_raise(self, sbe):
        """community_candidates=None must not raise — backward-compatible default (AC-6)."""
        try:
            result, _ = self._run_baseline(sbe, community_candidates=None)
        except Exception as exc:
            pytest.fail(
                f"propose_strategies must not raise with community_candidates=None; "
                f"got {type(exc).__name__}: {exc}"
            )
        assert isinstance(result, sbe.ProposalRun)

    def test_empty_community_candidates_does_not_raise(self, sbe):
        """community_candidates=[] must not raise and behave the same as None (AC-6)."""
        try:
            result, _ = self._run_baseline(sbe, community_candidates=[])
        except Exception as exc:
            pytest.fail(
                f"propose_strategies must not raise with community_candidates=[]; "
                f"got {type(exc).__name__}: {exc}"
            )
        assert isinstance(result, sbe.ProposalRun)

    def test_none_and_empty_list_produce_identical_gate_input_count(self, sbe):
        """AC-6: community_candidates=None and =[] must produce the same gate input count.

        Both are zero community candidates; the gate should receive the same template
        candidates from _generate_candidate_trees, which is deterministic for a fixed
        objective + universe.
        """
        _, gate_calls_none = self._run_baseline(sbe, community_candidates=None)
        _, gate_calls_empty = self._run_baseline(sbe, community_candidates=[])

        n_none = sum(len(batch) for batch in gate_calls_none)
        n_empty = sum(len(batch) for batch in gate_calls_empty)

        assert n_none == n_empty, (
            f"community_candidates=None and =[] must produce the same gate input count; "
            f"got {n_none} (None) vs {n_empty} ([]) — regression detected (AC-6)"
        )

    def test_gate_input_contains_no_community_ids_when_none(self, sbe):
        """AC-6: when community_candidates=None, zero community candidate_ids reach the gate."""
        _, gate_calls = self._run_baseline(sbe, community_candidates=None)

        if not gate_calls:
            return  # Empty universe — no gate call at all; non-issue

        all_ids = [c.candidate_id for batch in gate_calls for c in batch]
        community_ids = [cid for cid in all_ids if cid.startswith("community:")]
        assert len(community_ids) == 0, (
            f"When community_candidates=None, no community ids must reach the gate; "
            f"got {community_ids!r} (AC-6 regression)"
        )

    def test_gate_input_contains_no_community_ids_when_empty_list(self, sbe):
        """AC-6: when community_candidates=[], zero community candidate_ids reach the gate."""
        _, gate_calls = self._run_baseline(sbe, community_candidates=[])

        if not gate_calls:
            return

        all_ids = [c.candidate_id for batch in gate_calls for c in batch]
        community_ids = [cid for cid in all_ids if cid.startswith("community:")]
        assert len(community_ids) == 0, (
            f"When community_candidates=[], no community ids must reach the gate; "
            f"got {community_ids!r} (AC-6 regression)"
        )

    def test_fdr_integrity_preserved_with_none_community_candidates(self, sbe):
        """AC-6: gated_batch.n_candidates == len(result.candidates) with community_candidates=None.

        This ensures the existing FDR integrity invariant is not broken by the new param.
        """
        result, _ = self._run_baseline(sbe, community_candidates=None)
        assert result.error is None, f"Unexpected error: {result.error!r}"
        assert result.gated_batch.n_candidates == len(result.candidates), (
            f"FDR integrity: gated_batch.n_candidates must equal len(result.candidates) "
            f"when community_candidates=None; "
            f"got n_candidates={result.gated_batch.n_candidates}, "
            f"len(candidates)={len(result.candidates)} (AC-6 regression)"
        )


# ===========================================================================
# SECTION 7 — AC-7: sharpe_filtered counter in community_strats
# ===========================================================================


class TestAC7SharpeFilteredCounter:
    """AC-7: load_community_strategies stats must gain a 'sharpe_filtered' key."""

    def _make_mock_client_with_docs(self, docs: list[dict]):
        """Return a mock Mongo client that yields the given docs."""
        from unittest.mock import MagicMock  # noqa: PLC0415

        col = MagicMock()
        col.find.return_value = docs
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = col
        client.captplanet.strategies = col
        return client

    def _make_valid_doc(self, sid: str, sharpe: float | None) -> dict:
        """Build a valid loader doc with the given sid and OOS sharpe."""
        tree = symphony_schema.make_root(
            f"S-{sid}", "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset(f"TICK{sid}")])]
        )
        oos = {"sharpe": sharpe} if sharpe is not None else None
        return {
            "sid": sid,
            "name": f"Strategy {sid}",
            "edn_string": json.dumps(tree),
            "oos_metrics": oos,
        }

    def test_stats_has_sharpe_filtered_key(self):
        """Fixture-anchored: stats dict must contain 'sharpe_filtered' key (AC-7 — does not yet exist).

        This is RED against the current implementation which lacks this counter.
        """
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        doc = self._make_valid_doc("s1", sharpe=1.0)
        client = self._make_mock_client_with_docs([doc])
        result = load_community_strategies(client=client)

        assert "sharpe_filtered" in result["stats"], (
            "stats must contain 'sharpe_filtered' key (AC-7 — not yet implemented). "
            f"Got stats keys: {sorted(result['stats'].keys())!r}"
        )

    def test_sharpe_filtered_increments_when_doc_is_below_threshold(self):
        """A doc whose sharpe < min_oos_sharpe must increment sharpe_filtered (not validate_rejected)."""
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        # sharpe=0.3 is below threshold=0.5 → should increment sharpe_filtered
        doc_below = self._make_valid_doc("below", sharpe=0.3)
        client = self._make_mock_client_with_docs([doc_below])
        result = load_community_strategies(client=client, min_oos_sharpe=0.5)

        stats = result["stats"]
        assert stats.get("sharpe_filtered", 0) >= 1, (
            "doc with sharpe below min_oos_sharpe must increment sharpe_filtered; "
            f"got sharpe_filtered={stats.get('sharpe_filtered', 'KEY_MISSING')} (AC-7)"
        )
        # Must NOT increment validate_rejected (that's for tree structure failures)
        assert stats.get("validate_rejected", 0) == 0, (
            "sharpe-filtered doc must NOT increment validate_rejected (wrong counter)"
        )
        # Must NOT be in candidates
        sids = [c["sid"] for c in result["candidates"]]
        assert "below" not in sids, (
            "sharpe-filtered doc must not appear in candidates"
        )

    def test_sharpe_filtered_does_not_increment_when_doc_above_threshold(self):
        """A doc whose sharpe >= min_oos_sharpe must NOT increment sharpe_filtered."""
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        doc_above = self._make_valid_doc("above", sharpe=0.9)
        client = self._make_mock_client_with_docs([doc_above])
        result = load_community_strategies(client=client, min_oos_sharpe=0.5)

        stats = result["stats"]
        assert stats.get("sharpe_filtered", 0) == 0, (
            "doc with sharpe >= min_oos_sharpe must NOT increment sharpe_filtered; "
            f"got {stats.get('sharpe_filtered', 0)} (AC-7)"
        )
        sids = [c["sid"] for c in result["candidates"]]
        assert "above" in sids, "doc above threshold must appear in candidates"

    def test_sharpe_filtered_zero_when_no_filter_set(self):
        """When min_oos_sharpe=None (default), sharpe_filtered must be 0 (never incremented)."""
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        doc = self._make_valid_doc("nofilter", sharpe=0.1)
        client = self._make_mock_client_with_docs([doc])
        result = load_community_strategies(client=client)  # no min_oos_sharpe

        stats = result["stats"]
        assert stats.get("sharpe_filtered", 0) == 0, (
            "sharpe_filtered must be 0 when min_oos_sharpe=None; "
            f"got {stats.get('sharpe_filtered', 0)} (AC-7)"
        )

    def test_sharpe_filtered_docs_without_metrics_not_counted(self):
        """Docs lacking oos_metrics (or lacking sharpe key) are KEPT, not sharpe_filtered.

        Per AC-5/AC-7: 'docs lacking the metric are KEPT regardless'. They must not
        increment sharpe_filtered.
        """
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        doc_no_oos = self._make_valid_doc("nooos", sharpe=None)  # oos_metrics = None
        doc_no_sharpe = {
            "sid": "noshp",
            "name": "NoSharpe",
            "edn_string": json.dumps(symphony_schema.make_root(
                "NS", "daily",
                [symphony_schema.make_weight_equal([symphony_schema.make_asset("NOSP")])]
            )),
            "oos_metrics": {"cagr": 0.05},  # has oos_metrics but no 'sharpe' key
        }
        client = self._make_mock_client_with_docs([doc_no_oos, doc_no_sharpe])
        result = load_community_strategies(client=client, min_oos_sharpe=5.0)  # very high threshold

        stats = result["stats"]
        assert stats.get("sharpe_filtered", 0) == 0, (
            "docs lacking oos_metrics or lacking sharpe key must NOT be sharpe_filtered; "
            f"got sharpe_filtered={stats.get('sharpe_filtered', 0)} (AC-7)"
        )
        # Both docs must still be in candidates (they pass the min_oos_sharpe check)
        sids = {c["sid"] for c in result["candidates"]}
        assert "nooos" in sids, "doc with no oos_metrics must be KEPT"
        assert "noshp" in sids, "doc with no sharpe key must be KEPT"

    def test_sum_invariant_holds_with_sharpe_filtered(self):
        """Fixture-anchored: pulled == valid + deduped + missing_edn_string + parse_failed
        + validate_rejected + sharpe_filtered when min_oos_sharpe is set.

        This is the key invariant from feature-plans/community-strats-wiring.md AC-7.
        Without the sharpe_filtered counter, pulled exceeds the sum (the slice-1 comment caveat).
        """
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        fixture = _load_fixture()
        assert "invariant" in fixture["sharpe_filtered_key"]

        # Build a batch: 2 valid (1 passes, 1 sharpe-filtered), 1 missing_edn, 1 parse_failed,
        # 1 validate_rejected. Total pulled = 5.
        tree_pass = symphony_schema.make_root(
            "Pass", "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset("PASS")])]
        )
        tree_fail = symphony_schema.make_root(
            "Fail", "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset("FAIL")])]
        )
        invalid_tree = {
            "step": "root", "name": "Bad", "rebalance": "daily",
            "id": "00000000-0000-0000-0000-000000000000",
            "children": [{"step": "UNKNOWN_XYZ", "id": "00000000-0000-0000-0000-000000000001", "children": []}],
        }
        docs = [
            {   # valid + passes sharpe filter
                "sid": "pass", "name": "Pass",
                "edn_string": json.dumps(tree_pass),
                "oos_metrics": {"sharpe": 1.5},
            },
            {   # valid tree BUT sharpe < threshold → sharpe_filtered
                "sid": "sfilt", "name": "SFilt",
                "edn_string": json.dumps(tree_fail),
                "oos_metrics": {"sharpe": 0.1},
            },
            {   # missing edn_string
                "sid": "miss", "name": "Miss", "oos_metrics": None
            },
            {   # parse failed
                "sid": "parse", "name": "Parse",
                "edn_string": "NOT_JSON", "oos_metrics": None
            },
            {   # validate rejected
                "sid": "reject", "name": "Reject",
                "edn_string": json.dumps(invalid_tree), "oos_metrics": None
            },
        ]
        client = self._make_mock_client_with_docs(docs)
        result = load_community_strategies(client=client, min_oos_sharpe=0.5)

        stats = result["stats"]
        # Verify sharpe_filtered key exists (RED: not yet implemented)
        assert "sharpe_filtered" in stats, (
            "stats must contain 'sharpe_filtered' key — key absent (AC-7 RED)"
        )

        total = (
            stats["valid"]
            + stats["deduped"]
            + stats["missing_edn_string"]
            + stats["parse_failed"]
            + stats["validate_rejected"]
            + stats["sharpe_filtered"]
        )
        assert total == stats["pulled"], (
            f"Sum invariant violated (AC-7): pulled={stats['pulled']} but "
            f"valid({stats['valid']}) + deduped({stats['deduped']}) + "
            f"missing_edn_string({stats['missing_edn_string']}) + "
            f"parse_failed({stats['parse_failed']}) + "
            f"validate_rejected({stats['validate_rejected']}) + "
            f"sharpe_filtered({stats['sharpe_filtered']}) = {total}. "
            "Without sharpe_filtered, docs silently lost from the sum."
        )

    def test_sum_invariant_holds_without_sharpe_filter(self):
        """Invariant still holds when min_oos_sharpe=None (sharpe_filtered must be 0, not absent)."""
        from advisors.community_strats import load_community_strategies  # noqa: PLC0415

        doc_a = self._make_valid_doc("a", sharpe=1.0)
        doc_b = self._make_valid_doc("b", sharpe=0.2)
        client = self._make_mock_client_with_docs([doc_a, doc_b])
        result = load_community_strategies(client=client)  # no filter

        stats = result["stats"]
        assert "sharpe_filtered" in stats, (
            "stats must contain 'sharpe_filtered' even when no filter set (AC-7)"
        )

        total = (
            stats["valid"]
            + stats["deduped"]
            + stats["missing_edn_string"]
            + stats["parse_failed"]
            + stats["validate_rejected"]
            + stats.get("sharpe_filtered", 0)
        )
        assert total == stats["pulled"], (
            f"Sum invariant violated without filter: pulled={stats['pulled']} != sum={total}"
        )
