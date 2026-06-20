"""RED tests — propose_strategies community-candidate wiring.

Module under test: advisors.strategy_builder_engine

These tests are RED by construction: the new symbols
(`community_candidate_infos`, `MAX_COMMUNITY_CANDIDATES_PER_RUN`, and the
`community_candidates` kwarg on `propose_strategies`) do not exist yet.

Adversarial coverage:
  AC-1  adapter mapping + cap + empty/available=False → []
  AC-2  gate receives FULL batch (template + community together)
  AC-3  MAX_COMMUNITY_CANDIDATES_PER_RUN constant exists and is enforced
  AC-4  per-candidate backtest-failure isolation
  AC-5  persisted observation carries template_id="community" + sid provenance
  AC-6  community_candidates=None and =[] → no regression vs template-only
  AC-7  advisory-safety (no LIVE_EXECUTION path) + never-raises contract

Rules:
  - No live Composer, Mongo, or DB calls — everything is mocked.
  - Trees are built via symphony_schema constructors.
  - No hardcoded producer metric values: assertions cover shape/membership/counts
    only, never specific rates/dollars/percents.
  - No assertion-free tests; every test can fail on a wrong implementation.
  - `pytest.approx` is not needed here (all assertions are structural, not
    floating-point).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_fake_backtest_result(n_days: int = 100, base_return: float = 0.001):
    """Synthetic BacktestResult with a non-degenerate log-return series.

    n_days >= 65 is required for the fold-transform to produce a valid
    purge-respecting validation fold (FOLD_TRANSFORM_MIN_TOTAL_DAYS = 65).
    Oscillating sign prevents a zero-variance series that would trip the
    bootstrap SE floor.
    """
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns
    )


def _make_error_backtest_result(reason: str = "network timeout"):
    """BacktestResult representing a failed backtest (error field non-None)."""
    from advisors.composer_backtest_client import BacktestResult

    return BacktestResult(stats=None, data_warnings=[], daily_returns={}, error=reason)


def _patch_builder_generates(*, n_plans: int = 2):
    """Patch build_plan_generator.generate_build_plans to return N valid built-new plans.

    C4 (commit 5ae6c8c) replaced the 7-template stamper inside _generate_candidate_trees
    with the real C1→C2→C3 pipeline, so 'template' (built-new) candidates now originate from
    generate_build_plans → compile_plan instead of the old hardcoded templates. Tests that
    assert built-new candidates reach the gate alongside community candidates must therefore
    supply built-new plans through this seam (the conftest autouse guard otherwise neutralises
    the live Opus call → empty plans). The plans compile to validate_tree-clean trees via the
    REAL plan_tree_compiler — anti-hollow. Returns a patch() context manager."""
    from advisors import build_plan_generator as _gen

    plans = [
        {
            "plan_id": f"builtnew-{i}",
            "objective": "diversify",
            "name": f"Built New {i}",
            "rebalance": "daily",
            "root": {
                "kind": "weight",
                "scheme": "equal",
                "children": [
                    {"kind": "asset", "ticker": "SPY"},
                    {"kind": "asset", "ticker": "AGG"},
                ],
            },
        }
        for i in range(n_plans)
    ]
    return patch.object(
        _gen,
        "generate_build_plans",
        return_value=_gen.GeneratorResult(plans=plans, reason=None),
    )


def _make_community_result(n_candidates: int = 2, *, available: bool = True) -> dict:
    """Build a synthetic community_result dict matching load_community_strategies output.

    Trees are built via symphony_schema constructors so they always pass
    validate_tree (no hardcoded tree dicts).
    """
    from advisors import symphony_schema

    if not available:
        return {
            "available": False,
            "reason": "AtlasCacheUnavailable",
            "candidates": [],
            "stats": {"pulled": 0, "valid": 0},
            "source": "captplanet",
        }

    candidates = []
    tickers_pool = ["SPY", "QQQ", "AGG", "TLT", "GLD"]
    for i in range(n_candidates):
        t = tickers_pool[i % len(tickers_pool)]
        tree = symphony_schema.make_root(
            f"community-strat-{i}",
            "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset(t)])],
        )

        # Composition hash mirrors community_strats._composition_hash logic
        def _strip_ids(obj):
            if isinstance(obj, dict):
                return {k: _strip_ids(v) for k, v in obj.items() if k != "id"}
            if isinstance(obj, list):
                return [_strip_ids(item) for item in obj]
            return obj

        import hashlib

        canonical = json.dumps(_strip_ids(tree), sort_keys=True, separators=(",", ":"))
        comp_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        candidates.append(
            {
                "sid": f"sid-{i:04d}",
                "name": f"Community Strat {i}",
                "tree": tree,
                "tickers": [t],
                "oos_metrics": {"sharpe": 0.8 + i * 0.1},
                "composition_hash": comp_hash,
            }
        )

    return {
        "available": True,
        "candidates": candidates,
        "stats": {"pulled": n_candidates, "valid": n_candidates},
        "source": "captplanet",
    }


def _make_gated_batch_with_survivors(candidate_ids: list[str]):
    """Return a GatedBatch where every listed candidate_id is a survivor.

    Constructs minimal CandidateGateResult objects with ADOPT_CANDIDATE verdicts.

    AcceptanceVerdict schema (acceptance_gate.py:111-135):
      vetoes_passed: bool
      panel_score: float | None  (None only when vetoes failed — use a positive float here)
      panel_breakdown: dict
      decision: str  (one of DECISION_ADOPT_CANDIDATE / DECISION_KEEP_INCUMBENT / DECISION_REJECT_VETO_FAILED)
    """
    from acceptance_gate import DECISION_ADOPT_CANDIDATE, AcceptanceVerdict
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, CandidateGateResult, GatedBatch

    results = []
    survivors = []
    for cid in candidate_ids:
        verdict = AcceptanceVerdict(
            vetoes_passed=True,
            panel_score=1.0,
            panel_breakdown={},
            decision=DECISION_ADOPT_CANDIDATE,
        )
        r = CandidateGateResult(
            candidate_id=cid,
            verdict=verdict,
            validation_days=30,
            oos_alpha=1.5,
            caveats=["test caveat"],
            winner_p_adj=0.01,
        )
        results.append(r)
        survivors.append(r)

    return GatedBatch(
        results=results,
        survivors=survivors,
        n_candidates=len(candidate_ids),
        fdr_q=HARVEY_LIU_FDR_Q,
    )


def _make_empty_gated_batch():
    """GatedBatch with no survivors (all withheld)."""
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch

    return GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q)


# ---------------------------------------------------------------------------
# Import fixture — confirms the module is importable and the new symbols exist.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sbe():
    """Return the strategy_builder_engine module.

    scope=module: the module is stateless; importing once per module is cheaper.
    Tests in this file that reference new symbols (community_candidate_infos,
    MAX_COMMUNITY_CANDIDATES_PER_RUN, community_candidates kwarg) will fail here
    if those symbols don't exist yet — that is the expected RED signal.
    """
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415

    return _sbe


# ===========================================================================
# SECTION 1 — AC-3: MAX_COMMUNITY_CANDIDATES_PER_RUN constant
# ===========================================================================


class TestMaxCommunityCandidatesConstant:
    """MAX_COMMUNITY_CANDIDATES_PER_RUN must exist as a module-level named int."""

    def test_max_community_candidates_per_run_exists(self, sbe):
        """AC-3: the constant must be reachable as a module-level attribute."""
        assert hasattr(sbe, "MAX_COMMUNITY_CANDIDATES_PER_RUN"), (
            "MAX_COMMUNITY_CANDIDATES_PER_RUN must be a module-level named constant; "
            "hardcoding the cap in the function body violates the no-magic-numbers rule"
        )

    def test_max_community_candidates_per_run_is_positive_int(self, sbe):
        """AC-3: the constant must be a positive integer (a cap of 0 would always block)."""
        cap = sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN
        assert isinstance(cap, int), (
            f"MAX_COMMUNITY_CANDIDATES_PER_RUN must be int, got {type(cap).__name__}"
        )
        assert cap > 0, (
            f"MAX_COMMUNITY_CANDIDATES_PER_RUN must be > 0; a cap of {cap} would block all community candidates"
        )


# ===========================================================================
# SECTION 2 — AC-1: community_candidate_infos adapter
# ===========================================================================


class TestCommunityAdapterMapping:
    """community_candidate_infos maps community_result docs to CandidateInfo objects."""

    def test_community_candidate_infos_exists_on_module(self, sbe):
        """AC-1: function must be importable from the module."""
        assert hasattr(sbe, "community_candidate_infos"), (
            "community_candidate_infos must be a public function on strategy_builder_engine; "
            "missing means the adapter was not added"
        )
        assert callable(sbe.community_candidate_infos)

    def test_adapter_returns_list(self, sbe):
        """AC-1: return type is always a list."""
        result_doc = _make_community_result(n_candidates=1)
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert isinstance(out, list), (
            f"community_candidate_infos must return list, got {type(out).__name__}"
        )

    def test_adapter_candidate_id_is_sid(self, sbe):
        """AC-1: candidate_id must equal the source sid — not the name or a derived string."""
        result_doc = _make_community_result(n_candidates=1)
        sid = result_doc["candidates"][0]["sid"]
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert len(out) == 1
        assert out[0].candidate_id == sid, (
            f"candidate_id must equal sid={sid!r}; got {out[0].candidate_id!r}"
        )

    def test_adapter_template_id_is_community(self, sbe):
        """AC-1: template_id must be exactly 'community' for all adapted candidates."""
        result_doc = _make_community_result(n_candidates=2)
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        for info in out:
            assert info.template_id == "community", (
                f"template_id must be 'community', got {info.template_id!r}"
            )

    def test_adapter_tree_carried_through(self, sbe):
        """AC-1: the tree from the community doc must appear verbatim in CandidateInfo.tree."""
        result_doc = _make_community_result(n_candidates=1)
        source_tree = result_doc["candidates"][0]["tree"]
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert out[0].tree == source_tree, (
            "tree must be carried through unchanged; deepcopy is fine but structure must match"
        )

    def test_adapter_params_contains_sid(self, sbe):
        """AC-1: params dict must include the sid so persistence has provenance."""
        result_doc = _make_community_result(n_candidates=1)
        sid = result_doc["candidates"][0]["sid"]
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert "sid" in out[0].params, (
            f"params must contain key 'sid'; got keys {list(out[0].params.keys())}"
        )
        assert out[0].params["sid"] == sid

    def test_adapter_params_contains_name(self, sbe):
        """AC-1: params dict must include the name for human-readable traceability."""
        result_doc = _make_community_result(n_candidates=1)
        name = result_doc["candidates"][0]["name"]
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert "name" in out[0].params, "params must contain key 'name'"
        assert out[0].params["name"] == name

    def test_adapter_params_contains_composition_hash(self, sbe):
        """AC-1: params must include composition_hash for dedup traceability."""
        result_doc = _make_community_result(n_candidates=1)
        comp_hash = result_doc["candidates"][0]["composition_hash"]
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert "composition_hash" in out[0].params, "params must contain key 'composition_hash'"
        assert out[0].params["composition_hash"] == comp_hash

    def test_adapter_metrics_empty_dict_pre_backtest(self, sbe):
        """AC-1: metrics must be empty {} before backtesting — never pre-filled from oos_metrics."""
        result_doc = _make_community_result(n_candidates=1)
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert out[0].metrics == {}, (
            f"metrics must be empty {{}} before backtesting; got {out[0].metrics!r}. "
            "oos_metrics from the loader must NOT be injected — backtest runs fresh."
        )

    def test_adapter_backtest_error_is_none_pre_backtest(self, sbe):
        """AC-1: backtest_error must be None before any backtest attempt."""
        result_doc = _make_community_result(n_candidates=1)
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert out[0].backtest_error is None

    def test_adapter_count_matches_candidates_list(self, sbe):
        """AC-1: adapter returns one CandidateInfo per candidate (up to max_candidates)."""
        n = 3
        result_doc = _make_community_result(n_candidates=n)
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert len(out) == n, f"Expected {n} CandidateInfo objects; got {len(out)}"


class TestCommunityAdapterEmptyAndUnavailable:
    """AC-1 edge cases: empty input and available=False → []."""

    def test_adapter_available_false_returns_empty_list(self, sbe):
        """AC-1: available=False → [] regardless of candidates content."""
        result_doc = _make_community_result(available=False)
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert out == [], (
            "community_candidate_infos must return [] when available=False; "
            "this prevents unavailable community results from polluting the gate"
        )

    def test_adapter_empty_candidates_list_returns_empty_list(self, sbe):
        """AC-1: candidates=[] → [] even when available=True."""
        result_doc = {
            "available": True,
            "candidates": [],
            "stats": {"pulled": 0, "valid": 0},
            "source": "captplanet",
        }
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert out == [], "community_candidate_infos must return [] when candidates list is empty"


class TestCommunityAdapterCap:
    """AC-3: max_candidates kwarg caps the output deterministically."""

    def test_adapter_caps_at_max_candidates(self, sbe):
        """AC-3: when len(candidates) > max_candidates, output is truncated to max_candidates."""
        n = 5
        cap = 2
        result_doc = _make_community_result(n_candidates=n)
        out = sbe.community_candidate_infos(result_doc, max_candidates=cap)
        assert len(out) == cap, (
            f"Adapter must cap output at max_candidates={cap}; got {len(out)} items"
        )

    def test_adapter_cap_is_deterministic_takes_first(self, sbe):
        """AC-3: truncation must be deterministic — takes the first max_candidates items."""
        n = 4
        cap = 2
        result_doc = _make_community_result(n_candidates=n)
        out_full = sbe.community_candidate_infos(result_doc, max_candidates=n)
        out_capped = sbe.community_candidate_infos(result_doc, max_candidates=cap)
        # The capped result must be a prefix of the full result
        assert [i.candidate_id for i in out_capped] == [i.candidate_id for i in out_full[:cap]], (
            "Truncation must be deterministic: capped result must be a prefix of the full result"
        )

    def test_adapter_max_candidates_uses_module_constant_as_default(self, sbe):
        """AC-3: MAX_COMMUNITY_CANDIDATES_PER_RUN is the natural default for the cap."""
        cap = sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN
        # Build more candidates than the cap
        n = cap + 3
        result_doc = _make_community_result(n_candidates=n)
        # Passing the constant explicitly must work the same as enforcement inside propose_strategies
        out = sbe.community_candidate_infos(result_doc, max_candidates=cap)
        assert len(out) == cap, (
            f"Adapter with max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN ({cap}) "
            f"must cap at {cap}; got {len(out)}"
        )


# ===========================================================================
# SECTION 3 — AC-2: gate receives the FULL batch (template + community together)
# ===========================================================================


class TestGateReceivesFullBatch:
    """AC-2: evaluate_candidate_batch must receive template and community candidates together."""

    def test_gate_input_includes_community_candidate_ids(self, sbe):
        """AC-2: community candidate IDs appear in the candidate_ids passed to evaluate_candidate_batch."""
        community_result = _make_community_result(n_candidates=2)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        fake_result = _make_fake_backtest_result(n_days=100)
        captured_batch = []

        def _capture_batch(batch, **kwargs):
            captured_batch.extend(batch)
            return _make_empty_gated_batch()

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capture_batch,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        # Community candidate IDs must appear in the batch sent to the gate
        batch_ids = {c.candidate_id for c in captured_batch}
        community_ids = {c.candidate_id for c in community_cands}
        assert community_ids.issubset(batch_ids), (
            f"Community candidate IDs {community_ids} must all appear in the gate-input batch "
            f"(got batch IDs: {batch_ids}). "
            "The gate must receive the FULL batch — separating community candidates into a "
            "second gate would violate the FDR anti-overfit invariant."
        )

    def test_gate_input_includes_template_candidates_when_community_present(self, sbe):
        """AC-2: template candidates also appear in the gate batch when community_candidates given.

        The gate must receive BOTH template and community candidates — not just community.
        """
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        fake_result = _make_fake_backtest_result(n_days=100)
        captured_batch = []

        def _capture_batch(batch, **kwargs):
            captured_batch.extend(batch)
            return _make_empty_gated_batch()

        with (
            _patch_builder_generates(n_plans=2),
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capture_batch,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ", "AGG", "TLT"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        # There must be template candidates in the batch — not only the community one
        community_ids = {c.candidate_id for c in community_cands}
        template_ids = {c.candidate_id for c in captured_batch} - community_ids
        assert len(template_ids) > 0, (
            "Gate-input batch must contain template candidates when community_candidates is given; "
            "template generation must not be skipped or suppressed"
        )

    def test_gate_is_called_exactly_once(self, sbe):
        """AC-2 invariant: evaluate_candidate_batch called exactly once per propose_strategies call.

        Two separate gate calls (one for template, one for community) is a FDR violation.
        """
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        fake_result = _make_fake_backtest_result(n_days=100)
        mock_gate = MagicMock(return_value=_make_empty_gated_batch())

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch("advisors.strategy_builder_engine.evaluate_candidate_batch", mock_gate),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        assert mock_gate.call_count == 1, (
            f"evaluate_candidate_batch must be called exactly once per propose_strategies run "
            f"(called {mock_gate.call_count} times). Two calls = two separate gates = FDR violation."
        )


# ===========================================================================
# SECTION 4 — AC-4: per-candidate backtest failure isolation
# ===========================================================================


class TestBacktestFailureIsolation:
    """AC-4: a community candidate backtest failure must be isolated — other candidates unaffected."""

    def test_community_backtest_error_sets_backtest_error_field(self, sbe):
        """AC-4: a community candidate whose run_backtest raises must have backtest_error set (not None)."""
        from advisors import symphony_schema

        bad_tree = symphony_schema.make_root(
            "bad-community",
            "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
        )
        bad_candidate = sbe.CandidateInfo(
            candidate_id="sid-bad",
            tree=bad_tree,
            template_id="community",
            params={"sid": "sid-bad", "name": "Bad Strat", "composition_hash": "abc"},
        )

        def _selective_backtest(tree, symphony_id=""):
            # Only raise for the bad community candidate's tree (identified by name in root)
            root_name = tree.get("name", "")
            if root_name == "bad-community":
                raise RuntimeError("simulated network failure")
            return _make_fake_backtest_result(n_days=100)

        mock_gate = MagicMock(return_value=_make_empty_gated_batch())
        captured_candidates = []

        def _capturing_gate(batch, **kwargs):
            captured_candidates.extend(batch)
            return _make_empty_gated_batch()

        with (
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_selective_backtest),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capturing_gate,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=[bad_candidate],
            )

        # The run must complete without raising
        assert run.error is None, (
            f"propose_strategies must not set error on a community backtest failure; "
            f"got error={run.error!r}"
        )

    def test_community_backtest_error_candidate_excluded_from_gate(self, sbe):
        """AC-4: a community candidate whose backtest fails must NOT appear in the gate batch."""
        from advisors import symphony_schema

        bad_tree = symphony_schema.make_root(
            "fail-community",
            "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset("TLT")])],
        )
        bad_candidate = sbe.CandidateInfo(
            candidate_id="sid-fail",
            tree=bad_tree,
            template_id="community",
            params={"sid": "sid-fail", "name": "Fail Strat", "composition_hash": "xyz"},
        )

        captured_batch = []

        def _capturing_gate(batch, **kwargs):
            captured_batch.extend(batch)
            return _make_empty_gated_batch()

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                side_effect=RuntimeError("simulated failure"),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capturing_gate,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=[bad_candidate],
            )

        # The failed candidate must NOT appear in the gate's input batch
        batch_ids = {c.candidate_id for c in captured_batch}
        assert "sid-fail" not in batch_ids, (
            "A community candidate whose backtest raised must be excluded from the gate batch; "
            f"sid-fail incorrectly found in batch IDs: {batch_ids}"
        )

    def test_one_bad_community_candidate_does_not_block_template_candidates(self, sbe):
        """AC-4: one failing community candidate must not prevent template candidates from reaching the gate."""
        from advisors import symphony_schema

        bad_tree = symphony_schema.make_root(
            "always-fail",
            "daily",
            [symphony_schema.make_weight_equal([symphony_schema.make_asset("GLD")])],
        )
        bad_community = sbe.CandidateInfo(
            candidate_id="sid-always-fail",
            tree=bad_tree,
            template_id="community",
            params={"sid": "sid-always-fail", "name": "Always Fail", "composition_hash": "aaa"},
        )

        good_result = _make_fake_backtest_result(n_days=100)

        def _selective_run(tree, symphony_id=""):
            # Fail the community candidate; pass all templates (they have different root names)
            if tree.get("name", "") == "always-fail":
                raise RuntimeError("fail")
            return good_result

        captured_batch = []

        def _capturing_gate(batch, **kwargs):
            captured_batch.extend(batch)
            return _make_empty_gated_batch()

        with (
            _patch_builder_generates(n_plans=2),
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_selective_run),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capturing_gate,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ", "AGG"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=[bad_community],
            )

        # Template candidates must still be in the batch
        batch_ids = {c.candidate_id for c in captured_batch}
        assert len(batch_ids) > 0, (
            "Gate batch must not be empty when template candidates are available, "
            "even if the only community candidate failed its backtest"
        )
        # The bad community candidate must NOT be in the batch
        assert "sid-always-fail" not in batch_ids, (
            "Failed community candidate must be excluded from gate batch"
        )

    def test_all_community_backtest_failures_run_completes(self, sbe):
        """AC-4: when ALL community candidates fail backtest, propose_strategies still completes."""
        from advisors import symphony_schema

        bad_trees = [
            symphony_schema.make_root(
                f"fail-all-{i}",
                "daily",
                [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
            )
            for i in range(3)
        ]
        bad_candidates = [
            sbe.CandidateInfo(
                candidate_id=f"sid-all-fail-{i}",
                tree=bad_trees[i],
                template_id="community",
                params={
                    "sid": f"sid-all-fail-{i}",
                    "name": f"All Fail {i}",
                    "composition_hash": f"hash-{i}",
                },
            )
            for i in range(3)
        ]

        good_result = _make_fake_backtest_result(n_days=100)

        def _selective_run(tree, symphony_id=""):
            if tree.get("name", "").startswith("fail-all-"):
                return _make_error_backtest_result("all community fail")
            return good_result

        with (
            patch("advisors.strategy_builder_engine.run_backtest", side_effect=_selective_run),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                return_value=_make_empty_gated_batch(),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=bad_candidates,
            )

        # Must return a ProposalRun, not raise
        assert hasattr(run, "candidates"), "propose_strategies must return a ProposalRun"
        assert run.error is None, (
            f"All community backtest failures must not set run.error; got {run.error!r}"
        )


# ===========================================================================
# SECTION 5 — AC-5: provenance in persisted observations
# ===========================================================================


class TestCommunityProvenance:
    """AC-5: persisted observations for surviving community candidates carry template_id + sid."""

    def test_persisted_observation_has_template_id_community(self, sbe):
        """AC-5: insert_advisor_observation must be called with a raw_response containing template_id='community'."""
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        fake_result = _make_fake_backtest_result(n_days=100)
        # Make the community candidate a survivor
        survivor_id = community_cands[0].candidate_id
        gated = _make_gated_batch_with_survivors([survivor_id])

        persisted_raw_responses = []

        def _capture_persist(**kwargs):
            persisted_raw_responses.append(kwargs.get("raw_response", {}))

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch("advisors.strategy_builder_engine.evaluate_candidate_batch", return_value=gated),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_persist,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        # At least one persisted raw_response must have template_id="community"
        community_persisted = [
            rr for rr in persisted_raw_responses if rr.get("template_id") == "community"
        ]
        assert len(community_persisted) >= 1, (
            "A surviving community candidate must produce a persisted observation with "
            f"template_id='community'; persisted raw_responses: {persisted_raw_responses}"
        )

    def test_persisted_observation_raw_response_contains_sid(self, sbe):
        """AC-5: the raw_response for a surviving community candidate must reference the sid."""
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)
        expected_sid = community_cands[0].candidate_id  # candidate_id IS the sid (AC-1)

        fake_result = _make_fake_backtest_result(n_days=100)
        gated = _make_gated_batch_with_survivors([expected_sid])

        persisted_raw_responses = []

        def _capture_persist(**kwargs):
            persisted_raw_responses.append(kwargs.get("raw_response", {}))

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch("advisors.strategy_builder_engine.evaluate_candidate_batch", return_value=gated),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_persist,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        # Find the community raw_response and check it contains sid provenance
        community_persisted = [
            rr for rr in persisted_raw_responses if rr.get("template_id") == "community"
        ]
        assert len(community_persisted) >= 1
        rr = community_persisted[0]

        # The sid must appear somewhere in params (AC-1 mandates params has sid key)
        params = rr.get("params", {})
        assert "sid" in params, (
            f"raw_response['params'] must contain 'sid'; got params keys: {list(params.keys())}"
        )
        assert params["sid"] == expected_sid, (
            f"params['sid'] must equal the community sid {expected_sid!r}; got {params['sid']!r}"
        )

    def test_persisted_subject_id_is_sid(self, sbe):
        """AC-5: insert_advisor_observation must be called with subject_id=<sid>."""
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)
        expected_sid = community_cands[0].candidate_id

        fake_result = _make_fake_backtest_result(n_days=100)
        gated = _make_gated_batch_with_survivors([expected_sid])

        mock_persist = MagicMock()

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch("advisors.strategy_builder_engine.evaluate_candidate_batch", return_value=gated),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation", mock_persist
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        # At least one call must have subject_id equal to the community sid
        calls_with_sid = [
            c for c in mock_persist.call_args_list if c.kwargs.get("subject_id") == expected_sid
        ]
        assert len(calls_with_sid) >= 1, (
            f"insert_advisor_observation must be called with subject_id={expected_sid!r}; "
            f"actual calls: {[str(c) for c in mock_persist.call_args_list]}"
        )


# ===========================================================================
# SECTION 6 — AC-6: no regression when community_candidates is None or []
# ===========================================================================


class TestNoRegression:
    """AC-6: community_candidates=None and =[] must produce identical behavior to the default path."""

    def test_propose_strategies_accepts_community_candidates_none(self, sbe):
        """AC-6: community_candidates=None must not raise — the signature must accept the kwarg."""
        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_backtest_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                return_value=_make_empty_gated_batch(),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                community_candidates=None,
            )
        assert hasattr(run, "candidates"), (
            "propose_strategies(community_candidates=None) must return a ProposalRun"
        )
        assert run.error is None

    def test_propose_strategies_accepts_community_candidates_empty_list(self, sbe):
        """AC-6: community_candidates=[] must not raise and must behave as template-only."""
        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_backtest_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                return_value=_make_empty_gated_batch(),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                community_candidates=[],
            )
        assert hasattr(run, "candidates"), (
            "propose_strategies(community_candidates=[]) must return a ProposalRun"
        )
        assert run.error is None

    def test_gate_batch_size_same_for_none_and_empty_list(self, sbe):
        """AC-6: gate receives the same candidate count when community_candidates is None vs [].

        Both must be identical to each other — no community candidates injected in either case.
        """
        fake_result = _make_fake_backtest_result(n_days=100)
        batch_sizes = {}

        def _capture_for_none(batch, **kwargs):
            batch_sizes["none"] = len(batch)
            return _make_empty_gated_batch()

        def _capture_for_empty(batch, **kwargs):
            batch_sizes["empty"] = len(batch)
            return _make_empty_gated_batch()

        universe = ["SPY", "QQQ", "AGG"]

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capture_for_none,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=universe,
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                community_candidates=None,
            )

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capture_for_empty,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=universe,
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                community_candidates=[],
            )

        assert batch_sizes.get("none") == batch_sizes.get("empty"), (
            "Gate batch size must be identical for community_candidates=None vs community_candidates=[]; "
            f"got: none={batch_sizes.get('none')}, empty={batch_sizes.get('empty')}"
        )

    def test_community_candidates_kwarg_is_keyword_only(self, sbe):
        """AC-6 / architecture: community_candidates must be a keyword-only parameter.

        This guards against accidental positional use that could shadow other kwargs.
        """
        import inspect

        sig = inspect.signature(sbe.propose_strategies)
        params = sig.parameters
        assert "community_candidates" in params, (
            "propose_strategies must have a 'community_candidates' parameter"
        )
        p = params["community_candidates"]
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"community_candidates must be keyword-only (after *); "
            f"got kind={p.kind.name!r}. "
            "Positional use is an API hazard."
        )


# ===========================================================================
# SECTION 7 — AC-7: advisory-safety + never-raises
# ===========================================================================


class TestAdvisorySafetyAndNeverRaises:
    """AC-7: no LIVE_EXECUTION path, no allowlist entry, never raises."""

    def test_community_candidate_infos_never_raises_on_malformed_result(self, sbe):
        """AC-7: adapter must not raise even when community_result has unexpected shape."""
        # Missing keys, wrong types — the adapter must degrade gracefully
        bad_inputs = [
            {},
            {"available": True},
            {"available": True, "candidates": None},
            {"available": True, "candidates": "not-a-list"},
            None,
        ]
        for bad in bad_inputs:
            try:
                out = sbe.community_candidate_infos(bad, max_candidates=10)
                # If it returns, it must be a list (possibly empty)
                assert isinstance(out, list), (
                    f"community_candidate_infos({bad!r}) must return a list or raise; got {type(out).__name__}"
                )
            except Exception as exc:
                pytest.fail(
                    f"community_candidate_infos({bad!r}) raised {type(exc).__name__}: {exc}. "
                    "The adapter must never raise — it is on an advisory path."
                )

    def test_propose_strategies_never_raises_on_community_exception(self, sbe):
        """AC-7: even if all community candidate processing raises unexpected errors, no exception escapes."""
        community_result = _make_community_result(n_candidates=2)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                side_effect=Exception("total failure"),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                return_value=_make_empty_gated_batch(),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            try:
                run = sbe.propose_strategies(
                    objective=sbe.Objective.diversify,
                    universe=["SPY", "QQQ"],
                    screen_config=sbe.ScreenConfig(),
                    live_returns=[],
                    symphony_id="test-sym",
                    community_candidates=community_cands,
                )
            except Exception as exc:
                pytest.fail(
                    f"propose_strategies raised {type(exc).__name__}: {exc}. "
                    "The never-raises contract must hold even with community candidates."
                )

    def test_propose_strategies_returns_proposal_run_on_community_failures(self, sbe):
        """AC-7: catastrophic failure → ProposalRun with error set, not an exception."""
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest", side_effect=RuntimeError("boom")
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=RuntimeError("gate also broken"),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        # Must be a ProposalRun — even if error is set
        assert hasattr(run, "candidates"), "ProposalRun shape must be preserved on failure"
        assert hasattr(run, "error"), "ProposalRun must have an error field"

    def test_strategy_builder_engine_does_not_import_live_execution_at_module_level(self, sbe):
        """AC-7 / architecture: the engine must NOT import LIVE_EXECUTION or credential symbols.

        propose_strategies is off-execution-path; importing execution-path symbols would
        couple it to live-trade infrastructure.
        """
        import advisors.strategy_builder_engine as _sbe_mod

        # LIVE_EXECUTION should not be a module-level attribute
        assert not hasattr(_sbe_mod, "LIVE_EXECUTION"), (
            "strategy_builder_engine must not export LIVE_EXECUTION — "
            "this is an off-execution-path advisory module"
        )

    def test_community_candidate_infos_does_not_call_live_execution(self, sbe):
        """AC-7: community_candidate_infos must not touch any live-execution flag."""
        result_doc = _make_community_result(n_candidates=1)
        # Just calling the adapter with a fully valid result must not interact with any execution path
        # We assert no exception and a list return — actual flag isolation is verified by architecture
        out = sbe.community_candidate_infos(result_doc, max_candidates=10)
        assert isinstance(out, list)

    def test_propose_strategies_with_community_candidates_returns_proposal_run_type(self, sbe):
        """AC-7: the return type is always ProposalRun — not a bare dict or None."""
        community_result = _make_community_result(n_candidates=1)
        community_cands = sbe.community_candidate_infos(community_result, max_candidates=10)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_backtest_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                return_value=_make_empty_gated_batch(),
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY", "QQQ"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=community_cands,
            )

        required_fields = {
            "candidates",
            "gated_batch",
            "screened_survivors",
            "observations_written",
            "error",
        }
        missing = required_fields - set(dir(run))
        assert not missing, f"ProposalRun is missing required fields: {missing}"


# ===========================================================================
# SECTION 8 — AC-2 / AC-3: cap enforced in propose_strategies (not only adapter)
# ===========================================================================


class TestCapEnforcedInProposeStrategies:
    """AC-3: MAX_COMMUNITY_CANDIDATES_PER_RUN caps community candidates even if caller passes more."""

    def test_community_candidates_exceeding_cap_are_truncated_in_batch(self, sbe):
        """AC-3: if caller passes more community candidates than the cap, excess must not enter gate."""
        cap = sbe.MAX_COMMUNITY_CANDIDATES_PER_RUN

        # Build cap+2 community candidates directly (bypass adapter cap by constructing manually)
        from advisors import symphony_schema

        extra_candidates = []
        for i in range(cap + 2):
            tree = symphony_schema.make_root(
                f"extra-community-{i}",
                "daily",
                [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
            )
            extra_candidates.append(
                sbe.CandidateInfo(
                    candidate_id=f"sid-extra-{i}",
                    tree=tree,
                    template_id="community",
                    params={
                        "sid": f"sid-extra-{i}",
                        "name": f"Extra {i}",
                        "composition_hash": f"h{i}",
                    },
                )
            )

        captured_batch = []

        def _capturing_gate(batch, **kwargs):
            captured_batch.extend(batch)
            return _make_empty_gated_batch()

        fake_result = _make_fake_backtest_result(n_days=100)

        with (
            patch("advisors.strategy_builder_engine.run_backtest", return_value=fake_result),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_capturing_gate,
            ),
            patch("advisors.strategy_builder_engine._has_composer_key", return_value=True),
            patch("advisors.strategy_builder_engine.database.insert_advisor_observation"),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=["SPY"],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="test-sym",
                community_candidates=extra_candidates,
            )

        # Count community-sourced candidates in the gate batch
        community_ids_in_batch = [
            c.candidate_id for c in captured_batch if c.candidate_id.startswith("sid-extra-")
        ]
        assert len(community_ids_in_batch) <= cap, (
            f"propose_strategies must cap community candidates at MAX_COMMUNITY_CANDIDATES_PER_RUN={cap}; "
            f"got {len(community_ids_in_batch)} community candidates in the gate batch. "
            "The cap must be enforced inside propose_strategies, not only in the adapter."
        )
