"""C5 RED — Component 5 downstream invariant guards (AC-21 / AC-22 / AC-23).

Module(s) under guard:
  - advisors/strategy_builder_engine.py   (propose_strategies pipeline tail)
  - advisors/universe_provider.py
  - advisors/build_plan_generator.py
  - advisors/plan_tree_compiler.py
  - advisors/strategy_builder_scheduler.py
  - the rewired POST /ai-advisor/strategy-builder/run route (app.py)  [grep target]

These are INVARIANT GUARDS, not new-feature drivers. They lock the downstream
contract that the real builder must continue to honor end-to-end:

  AC-21  The FDR gate is the overfit guard over the FULL pooled batch — built-new
         (Opus) AND atlas-suggested (community) candidates flow through ONE
         evaluate_candidate_batch call; screens NEVER shrink the gate input;
         survivors carry SURVIVOR_OVERFITTING_CAVEAT; provenance survives the gate.
  AC-22  Advisory-only: no new module / the rewired route touches LIVE_EXECUTION
         as code, calls a Composer write/deploy endpoint, or mutates
         _SETTINGS_WRITE_ALLOWLIST; persisted observations are is_advisory_only=1.
  AC-23  End-to-end honest degradation: universe-source down / Anthropic down /
         Composer-backtest down each degrade to a clean empty/limited ProposalRun
         with NO crash, NO LIVE_EXECUTION side effect, NO leaked secret.

ADVERSARIAL FOCUS (a worse implementation must FAIL these):
  - AC-21 counts BOTH provenance sources in n_candidates — a regression that
    gates only built-new (or pre-shrinks via a screen) is caught.
  - AC-21 asserts the SAME single gate call receives the pooled batch — not two
    separate gate calls (which would corrupt the FDR correction).
  - AC-22's grep STRIPS comments/docstrings so a "# no LIVE_EXECUTION" line is
    NOT a false RED, and a real `LIVE_EXECUTION = ...` / env-read IS caught.
  - AC-23 drives the REAL pipeline (compile_plan + validate_tree + gate NOT
    mocked) — only the live network/SDK seams are mocked. A degenerate path that
    silently swallows the failure AND fabricates survivors is caught (assert
    empty/limited AND error/honest-reason surfaced, not a partial result).

MOCK-SEAM STRATEGY (established blast-radius pattern, tests/advisors/conftest.py +
test_builder_integration.py): mock ONLY the live seams —
  - universe_provider.get_tradeable_set / fetch_universe → controlled frozenset
  - build_plan_generator.generate_build_plans / _build_client → controlled / raise
  - composer_backtest_client.run_backtest → fixture BacktestResult
  - community_strats.load_community_strategies → fixture dict
NEVER mock compile_plan, validate_tree, the gate engine, or the math.
NO live Opus / Composer / Alpaca / Atlas in any test.

No hardcoded producer values — every quant assertion derives from the injected
fixture series or asserts shape/presence/identity.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date, timedelta
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sbe():
    import advisors.strategy_builder_engine as _sbe  # noqa: PLC0415

    return _sbe


@pytest.fixture(scope="module")
def gen():
    import advisors.build_plan_generator as _gen  # noqa: PLC0415

    return _gen


# ---------------------------------------------------------------------------
# Build-plan DSL fixtures (compile to validate_tree-clean trees via the REAL
# plan_tree_compiler). Mirrors test_builder_integration.py so the REAL C3
# compiler runs — these are NOT pre-built Composer trees.
# ---------------------------------------------------------------------------

_MEMBERSHIP = frozenset({"SPY", "QQQ", "IWM", "EFA", "AGG", "GLD", "TLT", "XLF"})


def _equal_weight_plan(plan_id: str, tickers: list[str], *, objective: str) -> dict:
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


def _make_fake_result(*, n_days: int = 120, base_return: float = 0.001):
    """A BacktestResult with a synthetic log-return series long enough for the fold.

    n_days >= 65 so backtest_gate_engine's fold transform produces a valid
    purge-respecting validation fold. Mild oscillation avoids a zero-variance
    degenerate series. The VALUES are synthetic test inputs, not producer outputs —
    no assertion below hardcodes them.
    """
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(n_days):
        returns[d.isoformat()] = base_return * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _error_result(reason: str = "network timeout"):
    from advisors.composer_backtest_client import BacktestResult  # noqa: PLC0415

    return BacktestResult(stats=None, data_warnings=[], daily_returns={}, error=reason)


def _patch_live_seams(monkeypatch, sbe, gen, *, plans, membership=_MEMBERSHIP):
    """Mock ONLY the live network/SDK seams; leave compile_plan + validate_tree REAL."""
    import advisors.universe_provider as up  # noqa: PLC0415

    monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset(membership))
    monkeypatch.setattr(
        up,
        "fetch_universe",
        lambda *a, **k: {"available": True, "symbols": frozenset(membership), "reason": ""},
        raising=False,
    )
    result = gen.GeneratorResult(plans=list(plans), reason=None)
    monkeypatch.setattr(gen, "generate_build_plans", lambda *a, **k: result, raising=False)
    if hasattr(sbe, "generate_build_plans"):
        monkeypatch.setattr(sbe, "generate_build_plans", lambda *a, **k: result, raising=False)
    if hasattr(sbe, "get_tradeable_set"):
        monkeypatch.setattr(
            sbe, "get_tradeable_set", lambda *a, **k: frozenset(membership), raising=False
        )


def _atlas_candidate(sbe, gen, sid: str, tickers: list[str]):
    """A community CandidateInfo tagged provenance='atlas-suggested' (the post-rewire tag).

    Built directly as a CandidateInfo so the test does not depend on which
    admission helper the GREEN route wires — the invariant is the PROVENANCE TAG,
    not the producer. Tree is a real validate_tree-clean equal-weight tree.
    """
    import advisors.symphony_schema as schema  # noqa: PLC0415

    tree = schema.make_root(
        f"Community {sid}",
        "daily",
        [schema.make_weight_equal([schema.make_asset(t) for t in tickers])],
    )
    return sbe.CandidateInfo(
        candidate_id=sid,
        tree=tree,
        template_id="community",
        params={"sid": sid, "provenance": gen.PROVENANCE_ATLAS_SUGGESTED},
        metrics={},
        backtest_error=None,
    )


# ===========================================================================
# AC-21 — FDR gate is the overfit guard over the FULL POOLED batch.
# ===========================================================================


class TestAC21FdrFullBatchBothSources:
    def test_gate_input_count_includes_both_provenance_sources(self, sbe, gen, monkeypatch):
        """AC-21: gated_batch.n_candidates == count of ALL successfully-backtested
        candidates from BOTH sources (built-new + atlas-suggested). A regression that
        gates only built-new, or pre-shrinks the batch via a screen, drops the count."""
        built_new_plans = [
            _equal_weight_plan("bn1", ["SPY", "QQQ", "AGG"], objective="diversify"),
            _equal_weight_plan("bn2", ["IWM", "EFA", "GLD"], objective="diversify"),
        ]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)

        atlas = [
            _atlas_candidate(sbe, gen, "atlas1", ["SPY", "TLT"]),
            _atlas_candidate(sbe, gen, "atlas2", ["XLF", "GLD"]),
        ]

        with patch(
            "advisors.strategy_builder_engine.run_backtest",
            return_value=_make_fake_result(),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
                community_candidates=atlas,
            )

        assert run.error is None, f"clean run expected; got error={run.error!r}"
        n_successful = len(run.candidates)
        # All candidates backtest the same fake series (no errors) → all succeed.
        # 2 built-new + 2 atlas = 4 successful, all in the SAME gate batch.
        assert n_successful == 4, (
            f"both provenance sources must reach the backtest; got {n_successful} "
            "successful candidates (expected 2 built-new + 2 atlas)"
        )
        assert run.gated_batch.n_candidates == n_successful, (
            "AC-21: the FDR gate must receive ALL successfully-backtested candidates "
            f"from BOTH sources; n_candidates={run.gated_batch.n_candidates} != "
            f"successful={n_successful}. Screens must NOT shrink the gate input, and "
            "atlas candidates must NOT be gated in a separate batch."
        )

    def test_single_gate_call_receives_pooled_batch(self, sbe, gen, monkeypatch):
        """AC-21: there is exactly ONE evaluate_candidate_batch call, and its batch
        contains BOTH provenance sources. Two separate gate calls would corrupt the
        BHY/Yekutieli FDR correction (each call would see a smaller N)."""
        built_new_plans = [
            _equal_weight_plan("bn1", ["SPY", "QQQ"], objective="diversify"),
        ]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)
        atlas = [_atlas_candidate(sbe, gen, "atlas1", ["TLT", "GLD"])]

        captured_batches: list[list] = []
        real_gate = sbe.evaluate_candidate_batch

        def _spy_gate(bt_candidates, *a, **k):
            captured_batches.append(list(bt_candidates))
            return real_gate(bt_candidates, *a, **k)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_spy_gate,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
                community_candidates=atlas,
            )

        # SPY benchmark in propose_strategies uses run_backtest, NOT the gate — so the
        # gate is invoked exactly once with the candidate batch.
        assert len(captured_batches) == 1, (
            "AC-21: evaluate_candidate_batch must be called EXACTLY once over the pooled "
            f"batch; got {len(captured_batches)} calls (separate gating corrupts FDR)."
        )
        batch_ids = {c.candidate_id for c in captured_batches[0]}
        assert "bn1" in batch_ids and "atlas1" in batch_ids, (
            "AC-21: the single gate batch must contain BOTH the built-new and the "
            f"atlas-suggested candidate; got ids {batch_ids}"
        )

    def test_provenance_survives_the_gate_in_persisted_rows(self, sbe, gen, monkeypatch):
        """AC-21/AC-13: provenance tags survive the gate — the persisted raw_response
        for built-new carries provenance='built-new' and for atlas carries
        'atlas-suggested'. Asserts the persisted observation, not an intermediate."""
        built_new_plans = [_equal_weight_plan("bn1", ["SPY", "QQQ", "AGG"], objective="diversify")]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)
        atlas = [_atlas_candidate(sbe, gen, "atlas1", ["TLT", "GLD"])]

        persisted: list[dict] = []

        def _capture_insert(**kwargs):
            persisted.append(kwargs)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_insert,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
                community_candidates=atlas,
            )

        assert persisted, "at least one observation must be persisted (survivor or rejected)"
        # Map each persisted row to its provenance via raw_response (template_id or params).
        by_subject = {}
        for row in persisted:
            raw = row.get("raw_response", {})
            prov = (raw.get("params") or {}).get("provenance") or raw.get("template_id")
            by_subject[row.get("subject_id")] = prov

        assert by_subject.get("bn1") == gen.PROVENANCE_BUILT_NEW, (
            "AC-13: the built-new candidate's persisted provenance must be 'built-new'; "
            f"got {by_subject.get('bn1')!r} (all: {by_subject})"
        )
        assert by_subject.get("atlas1") == gen.PROVENANCE_ATLAS_SUGGESTED, (
            "AC-13: the atlas candidate's persisted provenance must be 'atlas-suggested'; "
            f"got {by_subject.get('atlas1')!r} (all: {by_subject})"
        )

    def test_survivors_carry_overfitting_caveat(self, sbe, gen, monkeypatch):
        """AC-21: every persisted SURVIVOR (ADOPT_CANDIDATE) carries the
        SURVIVOR_OVERFITTING_CAVEAT — the mandatory honesty stamp. Rejected rows do not
        get the survivor caveat appended. We assert it on any ADOPT_CANDIDATE row."""
        built_new_plans = [_equal_weight_plan("bn1", ["SPY", "QQQ", "AGG"], objective="diversify")]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)

        persisted: list[dict] = []

        def _capture_insert(**kwargs):
            persisted.append(kwargs)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_insert,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
            )

        adopt_rows = [r for r in persisted if r.get("verdict") == "ADOPT_CANDIDATE"]
        for row in adopt_rows:
            caveats = (row.get("raw_response") or {}).get("caveats") or []
            assert sbe.SURVIVOR_OVERFITTING_CAVEAT in caveats, (
                "AC-21: every ADOPT_CANDIDATE survivor must carry "
                f"SURVIVOR_OVERFITTING_CAVEAT; row {row.get('subject_id')} caveats={caveats}"
            )


# ===========================================================================
# AC-22 — Advisory-only safety: no live surface in new modules / rewired route.
# ===========================================================================

_C5_MODULE_FILES = (
    "advisors/universe_provider.py",
    "advisors/build_plan_generator.py",
    "advisors/plan_tree_compiler.py",
    "advisors/strategy_builder_scheduler.py",
)


def _code_only_source(path: pathlib.Path) -> str:
    """Return module source with comments AND string literals (docstrings) stripped,
    so a "# no LIVE_EXECUTION" comment or a "no LIVE_EXECUTION" docstring is NOT a
    false positive. Only genuine CODE references survive.

    Implemented via the tokenizer: drop COMMENT and STRING tokens, keep the rest.
    """
    import io  # noqa: PLC0415
    import tokenize  # noqa: PLC0415

    src = path.read_text(encoding="utf-8")
    out_tokens = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out_tokens.append(tok.string)
    except tokenize.TokenError:
        # Fall back to AST-based literal stripping if tokenization is interrupted.
        return _ast_code_only(src)
    return " ".join(out_tokens)


def _ast_code_only(src: str) -> str:
    """AST fallback: unparse with docstrings removed (constants kept as repr-free)."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body.pop(0)
    return ast.dump(tree)


class TestAC22AdvisoryOnly:
    @pytest.mark.parametrize("rel", _C5_MODULE_FILES)
    def test_new_module_has_no_live_execution_code_reference(self, rel):
        """AC-22: no new C5 module touches LIVE_EXECUTION as CODE (assignment, env read,
        attribute). Docstring/comment mentions ('no LIVE_EXECUTION') are stripped first."""
        path = _REPO_ROOT / rel
        assert path.exists(), f"expected C5 module to exist: {rel}"
        code = _code_only_source(path)
        assert "LIVE_EXECUTION" not in code, (
            f"AC-22: {rel} must not reference LIVE_EXECUTION in CODE (docstrings/comments "
            "are stripped before this check). Advisory modules never read or set it."
        )

    @pytest.mark.parametrize("rel", _C5_MODULE_FILES)
    def test_new_module_has_no_settings_allowlist_mutation(self, rel):
        """AC-22: no new C5 module mutates _SETTINGS_WRITE_ALLOWLIST in code."""
        path = _REPO_ROOT / rel
        code = _code_only_source(path)
        assert "_SETTINGS_WRITE_ALLOWLIST" not in code, (
            f"AC-22: {rel} must not reference _SETTINGS_WRITE_ALLOWLIST in code."
        )

    @pytest.mark.parametrize("rel", _C5_MODULE_FILES)
    def test_new_module_has_no_composer_deploy_or_go_to_cash(self, rel):
        """AC-22: no Composer write/deploy / go-to-cash call surface in any new module."""
        path = _REPO_ROOT / rel
        code = _code_only_source(path)
        # Composer deploy / live-trade verbs that must never appear as code in an
        # advisory module. These are deliberately broad string checks on stripped code.
        for forbidden in ("go_to_cash", "go-to-cash", "/deploy", "deploy_symphony", "place_order"):
            assert forbidden not in code, (
                f"AC-22: {rel} must not contain a live-trade/deploy surface "
                f"({forbidden!r}); the builder is advisory-only."
            )

    def test_persisted_observations_are_advisory_only(self, sbe, gen, monkeypatch):
        """AC-22: every persisted observation from the builder is is_advisory_only=1."""
        built_new_plans = [_equal_weight_plan("bn1", ["SPY", "QQQ", "AGG"], objective="diversify")]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)

        persisted: list[dict] = []

        def _capture_insert(**kwargs):
            persisted.append(kwargs)

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.database.insert_advisor_observation",
                side_effect=_capture_insert,
            ),
        ):
            sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
            )

        assert persisted, "at least one observation must be persisted to assert advisory-only"
        for row in persisted:
            assert row.get("is_advisory_only") == 1, (
                "AC-22: every persisted observation must be is_advisory_only=1; "
                f"row {row.get('subject_id')} got {row.get('is_advisory_only')!r}"
            )


# ===========================================================================
# AC-23 — End-to-end honest degradation (each upstream down).
# ===========================================================================


class TestAC23HonestDegradation:
    def test_universe_source_down_degrades_to_empty(self, sbe, gen, monkeypatch):
        """AC-23: universe source down (get_tradeable_set returns empty + generator
        produces no plans) → empty ProposalRun, no crash, no fabricated survivors."""
        import advisors.universe_provider as up  # noqa: PLC0415

        monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset())
        # Empty membership → generator (real never-raising) yields no plans; emulate the
        # honest empty GeneratorResult directly so we exercise the degradation path.
        monkeypatch.setattr(
            gen,
            "generate_build_plans",
            lambda *a, **k: gen.GeneratorResult(plans=[], reason="EmptyUniverse"),
            raising=False,
        )
        if hasattr(sbe, "generate_build_plans"):
            monkeypatch.setattr(
                sbe,
                "generate_build_plans",
                lambda *a, **k: gen.GeneratorResult(plans=[], reason="EmptyUniverse"),
                raising=False,
            )

        run = sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=[],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="sym-c5",
        )
        assert run.screened_survivors == [], "no survivors when the universe source is down"
        assert run.observations_written == 0, "nothing persisted on an empty degraded run"
        # No crash: error is either None (clean empty) or a class-name string — never a leak.
        assert run.error is None or isinstance(run.error, str)

    def test_anthropic_generation_down_degrades_to_empty(self, sbe, gen, monkeypatch):
        """AC-23: Anthropic/generation down — the real generate_build_plans (D-1) degrades
        to an empty GeneratorResult when the SDK client raises. We mock the client factory
        seam to raise; the REAL generate_build_plans handles it. Result: empty ProposalRun."""
        import advisors.universe_provider as up  # noqa: PLC0415

        monkeypatch.setattr(up, "get_tradeable_set", lambda *a, **k: frozenset(_MEMBERSHIP))

        def _raise_client(*a, **k):
            raise RuntimeError("simulated Anthropic SDK failure")

        monkeypatch.setattr(gen, "_build_client", _raise_client, raising=False)

        run = sbe.propose_strategies(
            objective=sbe.Objective.diversify,
            universe=[],
            screen_config=sbe.ScreenConfig(),
            live_returns=[],
            symphony_id="sym-c5",
        )
        assert run.screened_survivors == [], "no survivors when generation is down"
        assert run.observations_written == 0, "nothing persisted when generation degrades"
        assert run.error is None or isinstance(run.error, str)

    def test_composer_backtest_down_degrades_to_empty_no_crash(self, sbe, gen, monkeypatch):
        """AC-23: Composer backtest down — run_backtest returns an error envelope for every
        candidate. The batch yields zero successfully-backtested candidates → empty gate,
        empty survivors. One backtest failure never aborts the batch; no crash."""
        built_new_plans = [
            _equal_weight_plan("bn1", ["SPY", "QQQ", "AGG"], objective="diversify"),
            _equal_weight_plan("bn2", ["IWM", "EFA", "GLD"], objective="diversify"),
        ]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)

        with patch(
            "advisors.strategy_builder_engine.run_backtest",
            return_value=_error_result("HTTP 503: composer unavailable"),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
            )

        assert run.candidates == [], "no successfully-backtested candidates when Composer is down"
        assert run.gated_batch.n_candidates == 0, "empty gate batch when no candidate backtests"
        assert run.screened_survivors == [], "no survivors when Composer is down"
        assert run.observations_written == 0, "nothing persisted when Composer is down"

    def test_top_level_failure_degrades_without_crash_or_survivors(self, sbe, gen, monkeypatch):
        """AC-23: a top-level failure inside the pipeline (here, the gate raising) degrades
        to a clean ProposalRun — NO crash, NO fabricated survivors, NO observations written.
        The error field is surfaced for triage. (The observable secret-leak guard lives at
        the route boundary in test_strategy_builder_c5_route.py, where AC-23's 'never a
        leaked secret' contract is operator-observable.)"""
        built_new_plans = [_equal_weight_plan("bn1", ["SPY", "QQQ"], objective="diversify")]
        _patch_live_seams(monkeypatch, sbe, gen, plans=built_new_plans)

        def _boom(*a, **k):
            raise RuntimeError("simulated gate failure")

        with (
            patch(
                "advisors.strategy_builder_engine.run_backtest",
                return_value=_make_fake_result(),
            ),
            patch(
                "advisors.strategy_builder_engine.evaluate_candidate_batch",
                side_effect=_boom,
            ),
        ):
            run = sbe.propose_strategies(
                objective=sbe.Objective.diversify,
                universe=[],
                screen_config=sbe.ScreenConfig(),
                live_returns=[],
                symphony_id="sym-c5",
            )

        assert run.screened_survivors == [], "no survivors on a top-level failure"
        assert run.observations_written == 0, "nothing persisted on a top-level failure"
        assert run.error is not None, "a top-level failure must surface an error for triage"


# ===========================================================================
# EDGE-1 — orphan deletion: the old unranked community_candidate_infos adapter is
# removed once the route + scheduler use the objective-matched admission.
# (FORWARD-AC: RED now — the fn still exists + the route still calls it — GREEN
# after quint-flask's route rewire AND quint-eng's adapter deletion land. Project
# standard: no dead/unused code.)
# ===========================================================================


_PRODUCTION_PY_FILES = (
    "app.py",
    "advisors/strategy_builder_engine.py",
    "advisors/strategy_builder_scheduler.py",
    "advisors/build_plan_generator.py",
)


def _production_call_references_adapter(rel: str) -> bool:
    """True if `community_candidate_infos` appears as a CODE reference (not a docstring/
    comment) in the given production file. Tokenizer-stripped so a docstring mention of
    the symbol in prose is not a false positive."""
    path = _REPO_ROOT / rel
    if not path.exists():
        return False
    code = _code_only_source(path)
    return "community_candidate_infos" in code


class TestEdge1AdapterOrphanRemoved:
    def test_old_adapter_not_referenced_in_production_code(self):
        """EDGE-1: after the rewire+deletion, community_candidate_infos must have ZERO
        production CODE references (app.py route, engine, scheduler, generator). RED now:
        app.py:3784,3808 still import + call it."""
        offenders = [rel for rel in _PRODUCTION_PY_FILES if _production_call_references_adapter(rel)]
        assert offenders == [], (
            "EDGE-1: community_candidate_infos must have no production CODE references after "
            f"the objective-matched rewire; still referenced in: {offenders}. GREEN: "
            "quint-flask removes the route's import+call; quint-eng deletes the adapter fn."
        )

    def test_old_adapter_function_is_deleted_from_engine(self, sbe):
        """EDGE-1: the orphaned adapter must be DELETED from strategy_builder_engine (no
        dead code). RED now: the fn still exists. GREEN: quint-eng deletes it AFTER the
        route rewire removes the last caller (peer-to-peer ordering)."""
        assert not hasattr(sbe, "community_candidate_infos"), (
            "EDGE-1: strategy_builder_engine.community_candidate_infos must be DELETED "
            "(orphaned after the objective-matched rewire). The propose_strategies "
            "community_candidates KWARG stays — only the unranked adapter fn dies."
        )

    def test_propose_strategies_community_kwarg_path_survives(self, sbe):
        """EDGE-1 guard: deleting the adapter must NOT remove the community_candidates kwarg
        path on propose_strategies — load_atlas_candidates output feeds it. This must stay
        GREEN before AND after the deletion (regression guard on the kwarg)."""
        import inspect  # noqa: PLC0415

        sig = inspect.signature(sbe.propose_strategies)
        assert "community_candidates" in sig.parameters, (
            "EDGE-1: the propose_strategies community_candidates kwarg must SURVIVE the "
            "adapter deletion — the objective-matched admission output feeds it."
        )
