"""RED tests -- feature-plans/advisor-outage-degrade.md AC-4: the run-level
result must surface a DISTINCT, honest `backtest_unavailable` signal when one
or more generated candidates were emitted tradeability-unverified by
plan_tree_compiler.compile_plan because Composer's backtest endpoint was
unreachable.

THE CRUX (flagged by team-lead + dg-fe, confirmed against sbe.py:950-958):
`strategy_builder_engine.py`'s Step 2 makes its OWN separate `run_backtest`
call per candidate to fetch full metrics; `ProposalRun.candidates` is later
filtered to `successful_candidates` (backtest_error is None). A real Composer
outage fails THAT call too, for the same reason it failed compile_plan's own
tradeability-check call -- so a candidate can be dropped from `candidates`
entirely while still having been the ONE thing this cycle exists to surface.
A rollup computed by recounting `run.candidates`/`successful_candidates`
would therefore silently read 0 in the exact scenario it must catch. The
tests below assert against `ProposalRun.backtest_unavailable` /
`.backtest_unavailable_count`, which strategy_builder_engine.py computes from
the FULL pre-Step-2-filter `candidate_infos` list -- and explicitly cover the
"candidate stripped from `candidates` AND STILL counted" case.

WHAT IS MOCKED (network/SDK only, per this suite's established discipline --
see test_builder_integration.py's _patch_builder_seams docstring): C1
(universe_provider.get_tradeable_set), C2 (build_plan_generator.
generate_build_plans), the run_backtest network seam (sbe.run_backtest -- the
SAME module-global name both compile_plan's repair loop and Step 2 read, per
dg-engine's confirmed call-site note), and _has_composer_key (deterministic,
credential-independent by construction -- matches the fix pattern already
landed in 45d57bbb for the identical CI-credential-less failure class).
NEVER MOCKED: plan_tree_compiler.compile_plan and symphony_schema.validate_tree
run for real -- this file proves the REAL compiler's degrade path threads
into the REAL engine's rollup, not a re-implementation of either.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Module fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sbe():
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415

    return _sbe


@pytest.fixture(scope="module")
def gen():
    import advisors.build_plan_generator as _gen  # noqa: PLC0415

    return _gen


@pytest.fixture(scope="module")
def up():
    import advisors.universe_provider as _up  # noqa: PLC0415

    return _up


_MEMBERSHIP = frozenset({"SPY", "QQQ", "AGG"})


def _equal_weight_plan(plan_id: str, tickers: list[str], *, objective="diversify") -> dict:
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": f"Plan {plan_id}",
        "rebalance": "daily",
        "root": {
            "kind": "weight",
            "scheme": "equal",
            "children": [{"kind": "asset", "ticker": t} for t in tickers],
        },
    }


class _AlwaysInfraRunBacktest:
    """Every call, regardless of count, returns an infra failure -- used for
    a sustained/total-outage scenario so the test isn't fragile to the exact
    number of internal run_backtest call sites (compile_plan's tradeability
    check, the per-run SPY-benchmark call, Step 2's own per-candidate call)."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, raw_value, *args, **kwargs):
        self.calls.append(raw_value)
        return _bt_err(_INFRA_ENVELOPE)


class _QueuedRunBacktest:
    """A scripted sbe.run_backtest replacement -- pops ONE queued result per
    call, in order. Since compile_plan's repair loop and propose_strategies's
    Step 2 both call the SAME sbe.run_backtest name for the SAME tree, this
    is how a test controls "tradeability-check call fails, later full-backtest
    call succeeds" (and vice versa) deterministically."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list = []

    def __call__(self, raw_value, *args, **kwargs):
        self.calls.append(raw_value)
        if self._results:
            return self._results.pop(0)
        return _bt_ok()


def _bt_ok():
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    return BacktestResult(
        stats={},
        data_warnings=[],
        daily_returns={"2026-01-02": 0.001, "2026-01-05": 0.0015, "2026-01-06": -0.0005},
        error=None,
    )


def _bt_err(envelope: str):
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    return BacktestResult(stats=None, data_warnings=[], error=envelope)


_INFRA_ENVELOPE = "transport error: ConnectionError: connection refused"


def _patch_seams(monkeypatch, sbe, gen, up, *, plans, run_backtest_fake, membership=_MEMBERSHIP):
    result = gen.GeneratorResult(plans=list(plans), reason=None)
    monkeypatch.setattr(gen, "generate_build_plans", lambda *a, **k: result, raising=False)
    if hasattr(sbe, "generate_build_plans"):
        monkeypatch.setattr(sbe, "generate_build_plans", lambda *a, **k: result, raising=False)
    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset(membership))
    if hasattr(sbe, "get_tradeable_set"):
        monkeypatch.setattr(
            sbe, "get_tradeable_set", lambda *a, **k: frozenset(membership), raising=False
        )
    # sbe imports run_backtest at module level -- the name bound in sbe's own
    # namespace (the one both compile_plan(backtest_fn=run_backtest) and Step 2
    # actually call) must be patched there, not on composer_backtest_client
    # (established in test_builder_integration.py's _patch_builder_seams).
    monkeypatch.setattr(sbe, "run_backtest", run_backtest_fake, raising=False)
    # Credential-independent by construction (45d57bbb pattern): the rollup
    # logic under test never touches real env creds regardless of this mock.
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: True, raising=False)


# ===========================================================================
# AC-4 (component level): compile_plan's degrade marker threads onto
# CandidateInfo via _generate_candidate_trees.
# ===========================================================================


def test_ac4_generate_candidate_trees_threads_tradeability_unverified(sbe, gen, up, monkeypatch):
    """MUST reflect the REAL compile_plan's infra-degrade: when every
    tradeability-check backtest call fails infra, every emitted CandidateInfo
    must carry tradeability_unverified=True and a non-None tree (degraded,
    not dropped)."""
    plans = [_equal_weight_plan("p1", ["SPY", "QQQ"])]
    fake = _QueuedRunBacktest([_bt_err(_INFRA_ENVELOPE)])
    _patch_seams(monkeypatch, sbe, gen, up, plans=plans, run_backtest_fake=fake)

    infos = sbe._generate_candidate_trees(sbe.Objective.diversify, [])

    assert infos, "an infra-degraded plan must still produce a CandidateInfo (tree emitted)"
    assert infos[0].tradeability_unverified is True
    assert isinstance(infos[0].tree, dict)


def test_ac4_generate_candidate_trees_healthy_plan_stays_unverified_false(
    sbe, gen, up, monkeypatch
):
    """Sibling: a plan that compiles + backtests cleanly must NOT be flagged."""
    plans = [_equal_weight_plan("p1", ["SPY", "QQQ"])]
    fake = _QueuedRunBacktest([_bt_ok()])
    _patch_seams(monkeypatch, sbe, gen, up, plans=plans, run_backtest_fake=fake)

    infos = sbe._generate_candidate_trees(sbe.Objective.diversify, [])
    assert infos
    assert infos[0].tradeability_unverified is False


# ===========================================================================
# AC-4 (run level): ProposalRun.backtest_unavailable / .backtest_unavailable_count.
# ===========================================================================


def test_ac4_run_level_flag_and_count_survive_step2_stripping_on_full_outage(
    sbe, gen, up, monkeypatch
):
    """THE CRUX TEST: with Composer fully unreachable, BOTH compile_plan's
    tradeability check AND Step 2's own full-backtest call fail for every
    candidate -- every candidate is stripped out of `run.candidates`
    (backtest_error set -> excluded from successful_candidates). Despite
    `run.candidates` being EMPTY, `backtest_unavailable`/`_count` must still
    honestly reflect the outage. A route/caller that recounted over
    `run.candidates` instead would read 0/False here -- exactly the silent-
    zero bug this cycle exists to kill."""
    from advisors.strategy_builder_engine import ScreenConfig as SC  # noqa: PLC0415

    plans = [
        _equal_weight_plan("p1", ["SPY", "QQQ"]),
        _equal_weight_plan("p2", ["SPY", "AGG"]),
    ]
    # Every backtest_fn call fails infra, regardless of how many are made
    # (compile_plan's tradeability check, the per-run SPY-benchmark call,
    # AND Step 2's own per-candidate call all share this seam) -- a total,
    # sustained outage.
    fake = _AlwaysInfraRunBacktest()
    _patch_seams(monkeypatch, sbe, gen, up, plans=plans, run_backtest_fake=fake)

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=[],
        screen_config=SC(),
        live_returns=[],
    )

    assert run.candidates == [], (
        "self-guard: every candidate must have been stripped by Step 2's own "
        "backtest failure for this test to actually exercise the crux scenario"
    )
    assert run.backtest_unavailable is True, (
        "backtest_unavailable must be True even though run.candidates is empty -- "
        "it must be sourced from the pre-filter list, not recounted"
    )
    assert run.backtest_unavailable_count == 2, (
        f"expected both plans counted (compiled+degraded before Step 2 ran), "
        f"got {run.backtest_unavailable_count}"
    )


def test_ac4_run_level_flag_false_and_count_zero_on_healthy_run(sbe, gen, up, monkeypatch):
    """Sibling: a fully healthy run (every backtest call succeeds) must leave
    backtest_unavailable False and count 0 -- no fabricated signal (AC-5)."""
    from advisors.strategy_builder_engine import ScreenConfig as SC  # noqa: PLC0415

    plans = [_equal_weight_plan("p1", ["SPY", "QQQ"])]
    fake = _QueuedRunBacktest([_bt_ok(), _bt_ok()])
    _patch_seams(monkeypatch, sbe, gen, up, plans=plans, run_backtest_fake=fake)

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify, universe=[], screen_config=SC(), live_returns=[]
    )

    assert run.backtest_unavailable is False
    assert run.backtest_unavailable_count == 0


def test_ac4_flag_survives_when_step2_transiently_recovers(sbe, gen, up, monkeypatch):
    """A transient blip: compile_plan's tradeability-check call fails infra
    (degrade, tree emitted unverified) but Step 2's OWN separate backtest
    call for the SAME tree succeeds moments later (Composer recovered). The
    candidate DOES survive into `run.candidates` with tradeability_unverified
    still True on its CandidateInfo, AND the run-level flag is still True --
    the operator must still know that candidate's tradeability was never
    actually confirmed, even though its return-series metrics are real."""
    from advisors.strategy_builder_engine import ScreenConfig as SC  # noqa: PLC0415

    plans = [_equal_weight_plan("p1", ["SPY", "QQQ"])]
    # Call 1 = compile_plan's tradeability check (infra fail -> degrade).
    # Call 2 = Step 2's own full-backtest call for the same tree (succeeds).
    fake = _QueuedRunBacktest([_bt_err(_INFRA_ENVELOPE), _bt_ok()])
    _patch_seams(monkeypatch, sbe, gen, up, plans=plans, run_backtest_fake=fake)

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify, universe=[], screen_config=SC(), live_returns=[]
    )

    assert run.backtest_unavailable is True
    assert run.backtest_unavailable_count == 1
    assert len(run.candidates) == 1, "the recovered candidate must survive into run.candidates"
    assert run.candidates[0].tradeability_unverified is True, (
        "surviving into run.candidates must NOT erase the unverified marker -- "
        "the operator still needs to know this specific candidate's "
        "tradeability was never confirmed"
    )


# ===========================================================================
# AC-4: community/Atlas-sourced candidates never spuriously trip the rollup.
# ===========================================================================


def test_ac4_community_candidate_defaults_unverified_false(sbe):
    """A community CandidateInfo constructed the way build_plan_generator.
    load_atlas_candidates / admit_community_candidates does (never touching
    tradeability_unverified) must default to False -- it never went through
    compile_plan's repair loop at all, so it cannot be 'infra-degraded'."""
    info = sbe.CandidateInfo(
        candidate_id="atlas-1", tree={}, template_id="atlas-suggested", params={}
    )
    assert info.tradeability_unverified is False


def test_ac4_mixed_community_and_built_new_rollup_counts_only_genuinely_degraded(
    sbe, gen, up, monkeypatch
):
    """A healthy built-new candidate plus an injected community candidate
    (default-constructed, never touched by compile_plan) must NOT trip
    backtest_unavailable -- only genuinely compiler-degraded candidates count."""
    from advisors.strategy_builder_engine import ScreenConfig as SC  # noqa: PLC0415

    plans = [_equal_weight_plan("p1", ["SPY", "QQQ"])]
    fake = _QueuedRunBacktest([_bt_ok(), _bt_ok()])
    _patch_seams(monkeypatch, sbe, gen, up, plans=plans, run_backtest_fake=fake)

    community = [
        sbe.CandidateInfo(candidate_id="atlas-1", tree={}, template_id="atlas-suggested", params={})
    ]

    run = sbe.propose_strategies(
        objective=sbe.Objective.diversify,
        universe=[],
        screen_config=SC(),
        live_returns=[],
        community_candidates=community,
    )

    assert run.backtest_unavailable is False
    assert run.backtest_unavailable_count == 0


# ===========================================================================
# Regression safety: new dataclass fields must be additive/default-safe --
# every existing CandidateInfo(...)/ProposalRun(...) call site across the
# suite constructs these WITHOUT the new kwargs.
# ===========================================================================


def test_candidateinfo_new_field_is_default_safe_for_existing_call_sites(sbe):
    """MUST NOT raise TypeError -- every pre-existing CandidateInfo(...)
    construction across the test suite omits tradeability_unverified."""
    info = sbe.CandidateInfo(candidate_id="x", tree={}, template_id="t", params={})
    assert info.tradeability_unverified is False


def test_proposalrun_new_fields_are_default_safe_for_existing_call_sites(sbe):
    """MUST NOT raise TypeError -- every pre-existing ProposalRun(...)
    construction across the test suite omits the two new AC-4 fields."""
    from advisors.backtest_gate_engine import HARVEY_LIU_FDR_Q, GatedBatch  # noqa: PLC0415

    run = sbe.ProposalRun(
        candidates=[],
        gated_batch=GatedBatch(results=[], survivors=[], n_candidates=0, fdr_q=HARVEY_LIU_FDR_Q),
        screened_survivors=[],
        observations_written=0,
    )
    assert run.backtest_unavailable is False
    assert run.backtest_unavailable_count == 0
