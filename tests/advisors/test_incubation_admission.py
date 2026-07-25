"""
RED tests -- Strategy Incubation Gate admission wiring (AC-2).

`advisors.incubation` does not exist yet; every test in this file is expected
to fail RED (ImportError) until the implementer adds it, and the integration
tests additionally fail until the single admission seam is wired into
`strategy_builder_engine.propose_strategies` Step 5 (per the "single wiring
point" recon finding, frozen in .claude/tdd-handoff.md).

Coverage:
  HASH1-HASH3: candidate_hash() matches community_strats' convention exactly,
    is uuid-independent, and is structure-sensitive (property tests using the
    REAL symphony_schema constructors, not hand-rolled dicts).
  WIRE1: propose_strategies Step 5 admits every screened survivor into the
    incubation ledger via a REAL write to an isolated temp DB (end-to-end,
    not a mocked call-count assertion -- proves the wiring actually reaches
    the DB, not just that some function was referenced).
  WIRE2: the persisted advisor_observations row's raw_response carries the
    SAME candidate_hash the ledger used (the join key AC-5 depends on).
  WIRE3: admission failure (D-1) never breaks the existing
    insert_advisor_observation persist -- mirrors the shipped
    insert_frontrunner_proposal try/except precedent at
    strategy_builder_engine.py:770-789.
  SEAM1: source-level guard -- admission is wired at exactly ONE call site
    (advisors/strategy_builder_engine.py). Neither app.py's route handler nor
    advisors/strategy_builder_scheduler.py may call the admission function
    directly (both already reach it through propose_strategies).
"""

from __future__ import annotations

import pathlib
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import database as db_module
from advisors import community_strats, symphony_schema
from advisors import strategy_builder_engine as sbe
from database import init_db, run_migrations

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# HASH1-HASH3: candidate_hash() convention
# ---------------------------------------------------------------------------


def _spy_tree():
    return symphony_schema.make_root(
        "SPY Only", "daily", [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])]
    )


def _qqq_tree():
    return symphony_schema.make_root(
        "QQQ Only", "daily", [symphony_schema.make_weight_equal([symphony_schema.make_asset("QQQ")])]
    )


class TestCandidateHashConvention:
    def test_candidate_hash_matches_community_strats_convention(self):
        """HASH1: advisors.incubation.candidate_hash() must produce byte-identical
        output to community_strats' own tree-structural hash -- the two must never
        diverge (a forked implementation is an explicit anti-pattern per the handoff)."""
        from advisors.incubation import candidate_hash

        tree = _spy_tree()
        assert candidate_hash(tree) == community_strats._composition_hash(tree), (
            "advisors.incubation.candidate_hash() must delegate to (or byte-match) "
            "community_strats._composition_hash() -- do not fork the hash logic."
        )

    def test_candidate_hash_matches_an_independently_computed_reference_value(self):
        """HASH1b (ga3-rev non-tautology upgrade, 2026-07-25): the test above is
        satisfied trivially if candidate_hash() simply delegates to
        community_strats._composition_hash() (the handoff's own recommended
        implementation) -- that proves DELEGATION, not that the underlying
        algorithm is correct (both sides could share the same bug, or one could
        just call the other with no independent verification). This test computes
        the expected hash a SECOND way, inline in the test file using only
        stdlib json/hashlib -- no import of community_strats at all -- so a
        genuinely broken hash (wrong key order, wrong separator, id not stripped,
        etc.) fails here even if candidate_hash() and community_strats agree with
        each other."""
        import hashlib
        import json

        from advisors.incubation import candidate_hash

        tree = _spy_tree()

        def _strip_ids(obj):
            if isinstance(obj, dict):
                return {k: _strip_ids(v) for k, v in obj.items() if k != "id"}
            if isinstance(obj, list):
                return [_strip_ids(item) for item in obj]
            return obj

        canonical = json.dumps(_strip_ids(tree), sort_keys=True, separators=(",", ":"))
        independently_computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        assert candidate_hash(tree) == independently_computed, (
            f"candidate_hash({tree!r}) = {candidate_hash(tree)!r} does not match the "
            f"independently-computed reference hash {independently_computed!r} "
            "(strip 'id' keys recursively -> json.dumps(sort_keys=True, "
            "separators=(',',':')) -> sha256 hexdigest). A pass here proves the "
            "ALGORITHM is correct, not merely that two functions agree with each other."
        )

    def test_candidate_hash_is_independent_of_node_uuids(self):
        """HASH2: two independently-constructed, structurally-identical trees (fresh
        uuid4 node ids each time, per symphony_schema's constructor contract) must
        hash to the same value."""
        from advisors.incubation import candidate_hash

        tree_a = _spy_tree()
        tree_b = _spy_tree()
        assert tree_a["id"] != tree_b["id"], (
            "Test setup invariant broken: symphony_schema.make_root should mint a "
            "fresh uuid4 id on every call."
        )
        assert candidate_hash(tree_a) == candidate_hash(tree_b), (
            "candidate_hash() must be independent of node uuids -- two structurally "
            "identical trees with different ids must hash identically."
        )

    def test_candidate_hash_is_structure_sensitive(self):
        """HASH3: a genuinely different tree (different ticker) must hash differently."""
        from advisors.incubation import candidate_hash

        assert candidate_hash(_spy_tree()) != candidate_hash(_qqq_tree()), (
            "candidate_hash() must be structure-sensitive -- a SPY-only tree and a "
            "QQQ-only tree must not collide."
        )


# ---------------------------------------------------------------------------
# WIRE1-WIRE3 / SEAM1: end-to-end admission wiring at the Step-5 seam
# ---------------------------------------------------------------------------


def _make_fake_result(n_days: int = 150, base_return: float = 0.001, factor_cycle=None):
    from advisors.composer_backtest_client import BacktestResult

    # Default cycle is the original monotonic-positive pattern -- kept as the
    # default for any other caller of this helper (none currently pass
    # factor_cycle), but WIRE-test callers below pass a mixed-sign cycle (see
    # the second root-cause note on _routed_run_backtest).
    cycle = factor_cycle if factor_cycle is not None else [0.8, 0.9, 1.0, 1.1, 1.2]
    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * cycle[i % len(cycle)]
        d += timedelta(days=1)
    return BacktestResult(
        stats={"sharpe": 0.5, "cagr": 0.08}, data_warnings=[], daily_returns=returns
    )


# Matches propose_strategies' own internal 100%-SPY benchmark tree name
# (strategy_builder_engine.py:993-998 -- symphony_schema.make_root("SPY Benchmark", ...)).
_SPY_BENCHMARK_TREE_NAME = "SPY Benchmark"

# Mixed-sign factor cycle -- MUST include at least one negative entry per
# cycle. Second root cause (found while fixing the first, verified against
# backtest_gate_engine.py:786-799 + autotuner.compute_sortino_tstat): the
# BHY/FDR gate scores candidates via a SORTINO t-statistic, which needs a
# non-zero DOWNSIDE deviation to be meaningful. The original all-positive
# cycle [0.8,0.9,1.0,1.1,1.2] has zero down-days -> undefined/degenerate
# downside deviation -> compute_sortino_tstat falls back to t=0.0 -> p=0.5
# ("empty validation fold" sentinel per backtest_gate_engine.py:773) -> fails
# BHY regardless of how large oos_alpha is. This cycle keeps ~80% up-days
# (strong net positive drift) with one down-day per 5, giving Sortino a real
# downside sample to compute against.
_MIXED_SIGN_CYCLE = [1.5, 1.0, 0.5, -0.4, 1.2]


def _routed_run_backtest(raw_value, symphony_id: str = "", **kwargs):
    """Dispatch on tree name so the candidate genuinely beats the SPY baseline.

    Two compounding root causes found and fixed here (ga3-adv found the first,
    2026-07-25; the second surfaced while verifying the first fix):

    1. A naive `lambda *a, **k: _make_fake_result()` returns the IDENTICAL
       series for both the candidate and propose_strategies' own internal
       SPY-benchmark call (Step 2a) -- producing an exact oos_alpha TIE. The
       gate condition is non-strict (`fold.oos_alpha <= _effective_
       default_oos_alpha`), so a tie is classified as NOT beating SPY ->
       rejection_reason='below_spy_alpha'. Fixed by dispatching on tree name
       (mirrors test_incubation_tick.py's `_run_backtest_router` pattern) so
       SPY gets a clearly worse series than the candidate.
    2. An all-positive return series (zero down-days) breaks the Sortino
       t-statistic the BHY/FDR gate scores on (see _MIXED_SIGN_CYCLE above) --
       candidates were being rejected with rejection_reason='fdr_not_winner'
       (winner_p_adj=0.5, the degenerate-fold sentinel) even after fix #1
       resolved the SPY tie. Fixed by using a mixed-sign cycle with a real,
       if minority, share of down-days.
    """
    if isinstance(raw_value, dict) and raw_value.get("name") == _SPY_BENCHMARK_TREE_NAME:
        # SPY: net negative drift, same mixed-sign shape (down-days included).
        return _make_fake_result(base_return=-0.006, factor_cycle=_MIXED_SIGN_CYCLE)
    # Candidate: net strongly positive drift, same mixed-sign shape.
    return _make_fake_result(base_return=0.006, factor_cycle=_MIXED_SIGN_CYCLE)


def _make_candidate_info(candidate_id: str = "cand-1"):
    tree = _spy_tree()
    return sbe.CandidateInfo(
        candidate_id=candidate_id,
        tree=tree,
        template_id="built-new",
        params={
            "plan_id": candidate_id,
            "name": "P1",
            "objective": "diversify",
            "provenance": "built-new",
        },
        metrics={},
        backtest_error=None,
    )


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Real, isolated temp DB with the full migration stack applied -- admission
    is exercised end-to-end (a real write reaches strategy_incubation), not via a
    mocked call-count assertion."""
    db_path = str(tmp_path / "test_incubation_admission.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _run_propose_strategies_against_real_db(monkeypatch):
    """Drive propose_strategies with mocked generation/backtest (no live Composer
    call, per the structural no-live-API guard) but a REAL database write path --
    proves the admission wiring reaches the DB, not just that a function exists."""
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: True)
    monkeypatch.setattr(sbe, "_generate_candidate_trees", MagicMock(return_value=[_make_candidate_info()]))
    monkeypatch.setattr(sbe, "run_backtest", _routed_run_backtest)

    return sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=["SPY", "QQQ"],
        screen_config=sbe.ScreenConfig(),
        live_returns=[],
        symphony_id="sym-test-admission",
    )


class TestAdmissionWiringEndToEnd:
    def test_screened_survivor_is_admitted_into_incubation_ledger(self, isolated_db, monkeypatch):
        """WIRE1: a real screened survivor from propose_strategies() must produce a
        real row in the incubation ledger (database.get_incubating()), proving the
        admission adapter is actually wired at the Step-5 seam -- not merely present
        as an unwired function somewhere in the codebase."""
        from advisors.incubation import candidate_hash

        run = _run_propose_strategies_against_real_db(monkeypatch)
        assert run.screened_survivors, (
            "Test setup produced zero screened survivors -- cannot verify admission "
            f"wiring. ProposalRun: {run}"
        )

        expected_hash = candidate_hash(_spy_tree())
        incubating = db_module.get_incubating()
        hashes = {r["candidate_hash"] for r in incubating}
        assert expected_hash in hashes, (
            f"Expected candidate_hash {expected_hash!r} to be admitted into the "
            f"incubation ledger after a real propose_strategies() survivor run. "
            f"Ledger contains: {hashes}. Admission is not wired at the Step-5 seam."
        )

    def test_persisted_raw_response_carries_the_same_candidate_hash(
        self, isolated_db, monkeypatch
    ):
        """WIRE2 (AC-5 join-key contract): the advisor_observations row's
        raw_response.candidate_hash must be the SAME value the incubation ledger
        used -- this is the only join key the live-status badge relies on."""
        from advisors.incubation import candidate_hash

        _run_propose_strategies_against_real_db(monkeypatch)
        expected_hash = candidate_hash(_spy_tree())

        rows = db_module.get_advisor_observations_for_subject("strategy_proposal", "cand-1")
        assert rows, "Expected at least one persisted advisor_observations row for cand-1."
        raw = rows[-1]["raw_response"]
        assert raw.get("candidate_hash") == expected_hash, (
            f"raw_response.candidate_hash {raw.get('candidate_hash')!r} != the ledger's "
            f"candidate_hash {expected_hash!r} -- the join key AC-5 depends on is broken."
        )
        assert "incubation_status" not in raw, (
            "AC-5 (amended): raw_response must NOT carry a frozen 'incubation_status' "
            "key -- advisor_observations rows are append-only/immutable, so a status "
            "frozen at persist time could never reflect a later promotion/failure. "
            "Status is computed live via the candidate_hash join, not stored here."
        )

    def test_admission_failure_does_not_break_the_existing_persist(
        self, isolated_db, monkeypatch
    ):
        """WIRE3 (D-1): if the admission call raises, the existing
        insert_advisor_observation persist must still succeed -- mirrors the shipped
        insert_frontrunner_proposal try/except precedent (strategy_builder_engine.py
        :770-789). Simulated by making database.register_incubation_candidate raise.

        Non-vacuity note (PM-required check, 2026-07-25): a detached-worktree check
        at a commit where the Step-5 admission wiring was absent showed this test
        PASSING vacuously -- register_incubation_candidate already existed (from
        ga3-db's earlier work) but was simply never CALLED, so the mocked
        RuntimeError never fired and observations_written>=1 held anyway (the base
        persist doesn't depend on admission at all). The mock_register.assert_called()
        line below closes that gap: it fails if admission wiring is absent OR never
        reaches the mocked call, distinguishing 'admission failed gracefully' from
        'admission was never attempted.'"""
        mock_register = MagicMock(side_effect=RuntimeError("simulated DB failure"))
        monkeypatch.setattr(db_module, "register_incubation_candidate", mock_register)
        try:
            run = _run_propose_strategies_against_real_db(monkeypatch)
        except RuntimeError:
            pytest.fail(
                "propose_strategies() raised when admission failed -- admission errors "
                "must be caught and logged (D-1), never propagated to break the "
                "existing advisor_observations persistence."
            )
        assert mock_register.called, (
            "register_incubation_candidate was never called -- this test cannot "
            "verify D-1 admission-failure resilience unless admission was actually "
            "attempted (non-vacuity requirement)."
        )
        assert run.observations_written >= 1, (
            "The existing advisor_observations persist must still succeed even when "
            f"admission raises. Got observations_written={run.observations_written}."
        )


class TestResolveAdmissionMddBaseline:
    """Review Finding 1 fix seam (ga3-adv extracts; PM ruling, 2026-07-25):
    `_resolve_admission_mdd_baseline(raw_mdd: float | None) -> float | None`
    -- the pure function Step 4b calls to convert a candidate's backtest
    max_drawdown into the ledger's positive-pct-magnitude convention, OR
    signal "decline admission" via None when the baseline itself is unknown.
    Pure unit tests -- no DB, no propose_strategies, no gate.

    REACHABILITY NOTE (frozen for every reader of this class, per PM
    instruction -- state this plainly so no future reader infers a live
    production bug): as of this cycle, a candidate whose
    info.metrics["max_drawdown"] is None CANNOT reach this seam via
    propose_strategies() in production -- _passes_screens
    (strategy_builder_engine.py:516-518) already fails closed on ANY None
    metric before Step 4b ever runs, unconditionally, with no
    screen_config-dependent bypass (pinned by the existing
    tests/advisors/test_strategy_builder_engine.py::TestAdversarialCycle3::
    test_passes_screens_returns_false_when_max_drawdown_is_none, which
    verified GREEN independently before this fix). This seam is SECOND-LAYER
    DEFENSE, not a fix for a currently-exploitable bug -- it exists so the
    code never does the wrong thing (fabricating an mdd_breach verdict from
    a coerced 0.0 baseline) if a future refactor ever changes call ordering,
    _passes_screens's None-handling, or adds an alternate entry point that
    bypasses screens."""

    def test_none_input_returns_none(self):
        from advisors.strategy_builder_engine import _resolve_admission_mdd_baseline

        assert _resolve_admission_mdd_baseline(None) is None, (
            "A None backtest MDD baseline must resolve to None (the decline-"
            "admission signal), never a coerced 0.0 -- a 0.0 baseline would "
            "fabricate a risk-exceeded verdict the first time "
            "evaluate_promotion checks forward_mdd > 0 + epsilon (review "
            "Finding 1)."
        )

    def test_negative_fraction_converts_to_positive_pct_magnitude(self):
        from advisors.strategy_builder_engine import _resolve_admission_mdd_baseline

        assert _resolve_admission_mdd_baseline(-0.08) == pytest.approx(8.0), (
            "A real quantstats-convention negative fraction (-0.08 = an 8% "
            "drawdown) must convert to the ledger's positive-pct-magnitude "
            "convention (8.0) -- unchanged from the existing behavior for a "
            "real (non-None) baseline."
        )

    def test_larger_negative_fraction_converts_correctly(self):
        from advisors.strategy_builder_engine import _resolve_admission_mdd_baseline

        assert _resolve_admission_mdd_baseline(-0.15) == pytest.approx(15.0)

    def test_zero_drawdown_converts_to_zero_not_treated_as_missing(self):
        """Edge case: a genuinely zero (not None) drawdown must convert to
        0.0, never conflated with the "unknown baseline" None-decline path --
        0.0 is a real, meaningful backtest result (a monotonically
        non-decreasing candidate), distinct from "we don't know"."""
        from advisors.strategy_builder_engine import _resolve_admission_mdd_baseline

        result = _resolve_admission_mdd_baseline(0.0)
        assert result is not None, (
            "A real 0.0 drawdown must NOT be treated as a None/unknown "
            "baseline -- only an actual None input signals decline."
        )
        assert result == pytest.approx(0.0)

    def test_positive_input_still_converts_via_abs(self):
        """Robustness: quantstats' documented convention is a negative
        fraction, but the seam must not misbehave on an unexpected positive
        value -- abs() handles either sign identically."""
        from advisors.strategy_builder_engine import _resolve_admission_mdd_baseline

        assert _resolve_admission_mdd_baseline(0.05) == pytest.approx(5.0)


class TestStep4bDeclinesOnNoneSeamResult:
    """Integration proof that Step 4b correctly ACTS on a None return from
    _resolve_admission_mdd_baseline -- by mocking the seam function DIRECTLY
    (not by constructing a real None-max_drawdown candidate through the real
    screens gate, which cannot happen today -- see the reachability note on
    TestResolveAdmissionMddBaseline above). This is the honest way to prove
    Step 4b's decline-on-None BEHAVIOR without misrepresenting the
    currently-unreachable production path as live: the candidate here has a
    perfectly real, non-None computed max_drawdown (it passes the real
    screens gate normally) -- only the SEAM's return value is forced to None
    for this one test, isolating exactly "what does Step 4b do when the seam
    says decline," independent of how a None might arise in practice."""

    def test_step4b_declines_admission_when_seam_returns_none(self, isolated_db, monkeypatch):
        from advisors.incubation import candidate_hash

        monkeypatch.setattr(sbe, "_has_composer_key", lambda: True)
        monkeypatch.setattr(
            sbe,
            "_generate_candidate_trees",
            MagicMock(return_value=[_make_candidate_info("cand-declined")]),
        )
        monkeypatch.setattr(sbe, "run_backtest", _routed_run_backtest)
        monkeypatch.setattr(sbe, "_resolve_admission_mdd_baseline", lambda raw_mdd: None)

        run = sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=["SPY", "QQQ"],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="sym-test-declined-mdd",
        )

        assert run.screened_survivors, (
            "Test setup must produce a real gate survivor to reach Step 4b at "
            f"all. ProposalRun: {run}"
        )

        declined_hash = candidate_hash(_spy_tree())
        incubating_hashes = {r["candidate_hash"] for r in db_module.get_incubating()}
        assert declined_hash not in incubating_hashes, (
            "When the MDD-resolution seam returns None, Step 4b must decline "
            "admission -- the candidate must never enter the incubation "
            f"ledger. Ledger: {incubating_hashes}"
        )

        # D-1: the underlying advisor_observations persist must still succeed
        # -- declining admission is not the same as a broken persist.
        declined_rows = db_module.get_advisor_observations_for_subject(
            "strategy_proposal", "cand-declined"
        )
        assert declined_rows, (
            "Declining admission must not break the existing "
            "advisor_observations persist for the candidate (D-1)."
        )


class TestSingleAdmissionSeam:
    """SEAM1: admission is wired at exactly ONE call site (recon finding b, ruled
    accepted by the PM). Both the on-demand route and the weekly scheduler already
    reach strategy_builder_engine.propose_strategies -- neither may call the
    admission function a second time directly."""

    _FORBIDDEN_FILES = (
        _WORKTREE_ROOT / "app.py",
        _WORKTREE_ROOT / "advisors" / "strategy_builder_scheduler.py",
    )

    @pytest.mark.parametrize(
        "forbidden_file", _FORBIDDEN_FILES, ids=lambda p: p.name
    )
    def test_admission_symbols_not_referenced_outside_strategy_builder_engine(
        self, forbidden_file
    ):
        assert forbidden_file.is_file(), f"Expected file not found: {forbidden_file}"
        source = forbidden_file.read_text(encoding="utf-8")
        for symbol in ("admit_candidate", "register_incubation_candidate"):
            assert symbol not in source, (
                f"{forbidden_file.name} references {symbol!r} directly -- admission "
                "must be wired ONLY inside strategy_builder_engine.propose_strategies "
                "Step 5 (both the route and the weekly scheduler already reach it "
                "through propose_strategies; a second direct call site risks "
                "double-admission)."
            )
