"""RED tests — Workstream C / AC-C2: run_weekly_asset_swap_suggestions() loop.

Neither the function nor any module wiring it exists yet -- ImportError /
AttributeError is the first RED signal (module-resolution pytest.fail below
gives a clean FAILED, not a collection ERROR, per the merge gate's
zero-ERRORS check -- same pattern as tests/advisors/test_builder_scheduler.py).

AC-C2: "run_weekly_asset_swap_suggestions() -- same enumeration [as C1]; assembles
the ticker-level return-series correlation_data via a synthetic_history.fetch_bars
-style step over a candidate pool from universe_provider.get_tradeable_set();
objective defaults to reduce_correlation (v1); calls suggest_swaps(...).
lens_scores is wired through (via extract_lens_scores) ONLY after D is GREEN.
Per-symphony D-1; rows persist as ASSET_SWAP."

AC-C3: "The engines themselves are UNCHANGED (their existing tests stay
green) -- this is purely the caller/loop layer that did not exist." We
therefore MOCK suggest_swaps (the loop's job is enumeration + D-1 isolation,
NOT re-testing the engine -- that's tests/ai_advisor/test_asset_swap_engine.py's
job) and assert it is invoked correctly, never re-implement its internals.

AC-C4: "No LIVE_EXECUTION interaction; advisory-only."

Hermetic: fetch_symphony_score, universe_provider.get_tradeable_set, and
synthetic_history.fetch_bars are ALL mocked -- these are the three plausible
live-network seams named in AC-C2's architecture description. No live Composer/
Alpaca calls fire from this test module under any implementation choice.

PM-ROUTED FOLLOW-UP (2026-07-12, D now GREEN): the committed loop
(advisors/weekly_suggestions_scheduler.py, commit 9d3da841) calls suggest_swaps
with NO lens_scores kwarg at all (defaults to None), and its own docstring
explicitly defers lens wiring to "a follow-up cycle now that Workstream D is
GREEN". The plan's AC-C2 wording -- "lens_scores is wired through (via
extract_lens_scores) ONLY after D is GREEN" -- means SEQUENCED after D within
THIS cycle, not deferred to a separate one. D is now GREEN (63ede739 lens-blend
formula + c61a3086 AC-D3 seed fix), so the fixed lens-blend math is currently
DEAD in the only real production path (propose_operator_swap does not use
_apply_lens_blend at all). See TestAssetSwapLoopWiresRealLensScoresAfterDIsGreen
below.
"""

from __future__ import annotations

import importlib
import random
from unittest.mock import MagicMock, patch

import pytest

# Candidate modules where the loop function might live -- the Architecture
# section names a new advisors/weekly_suggestions_scheduler.py as the home for
# the orchestrator + "two new per-symphony loop functions", but does not force
# them into that exact module. Resolve leniently (impl owns the module choice;
# the test pins the callable's BEHAVIOUR).
_CANDIDATE_MODULES = (
    "advisors.weekly_suggestions_scheduler",
    "advisors.asset_swap_engine",
)

_FN_NAME = "run_weekly_asset_swap_suggestions"


def _resolve_loop_fn():
    """Import each candidate module and look for _FN_NAME. pytest.fail (a true
    RED FAILURE, not a collection ERROR) if none exposes it."""
    last_exc = None
    tried = []
    for modpath in _CANDIDATE_MODULES:
        try:
            mod = importlib.import_module(modpath)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            tried.append(f"{modpath} (import failed: {type(exc).__name__})")
            continue
        fn = getattr(mod, _FN_NAME, None)
        if callable(fn):
            return mod, fn
        tried.append(f"{modpath} (no attribute {_FN_NAME!r})")
    pytest.fail(
        f"No module exposes {_FN_NAME}() (tried: {tried}); last import error: "
        f"{type(last_exc).__name__ if last_exc else 'none'}. AC-C2 requires a "
        f"callable weekly asset-swap suggestion loop."
    )


def _fake_bot_state(n: int) -> dict:
    """n live symphonies, each with a resolvable Composer hash key + display name."""
    return {
        f"hash-{i:03d}": {"name": f"Swap Loop Symphony {i}", "account_uuid": "acc-1"}
        for i in range(n)
    }


def _patch_suggest_swaps(monkeypatch, loop_module, fake_fn) -> None:
    """Patch suggest_swaps everywhere it could plausibly be referenced.

    A `from advisors.asset_swap_engine import suggest_swaps` inside the loop
    module would bind a NEW name that patching advisors.asset_swap_engine
    alone cannot reach -- patch both the canonical engine module attribute and
    (if present) the loop module's own attribute, so the test is robust to
    either import style the implementer chooses.
    """
    import advisors.asset_swap_engine as engine

    monkeypatch.setattr(engine, "suggest_swaps", fake_fn, raising=False)
    if hasattr(loop_module, "suggest_swaps"):
        monkeypatch.setattr(loop_module, "suggest_swaps", fake_fn, raising=False)


@pytest.fixture(autouse=True)
def _hermetic_network_seams(monkeypatch):
    """Neutralise the three plausible live-network seams named in AC-C2 so no
    implementation choice can make this test module touch a real network."""
    try:
        import symphony_logic

        monkeypatch.setattr(
            symphony_logic,
            "fetch_symphony_score",
            lambda symphony_id: {"name": symphony_id, "children": []},
            raising=False,
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        import advisors.universe_provider as up

        monkeypatch.setattr(
            up, "get_tradeable_set", lambda **kw: frozenset({"AAA", "BBB"}), raising=False
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        import synthetic_history

        monkeypatch.setattr(
            synthetic_history,
            "fetch_bars",
            lambda tickers, start, end, timeframe="1Day": {t: [0.1, 0.2, -0.1] for t in tickers},
            raising=False,
        )
    except Exception:  # noqa: BLE001
        pass


# ===========================================================================
# AC-C2 — enumerates live symphonies and calls suggest_swaps once each
# ===========================================================================


class TestAssetSwapLoopEnumeration:
    def test_calls_suggest_swaps_once_per_live_symphony(self, monkeypatch):
        import advisors.asset_swap_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(3)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        mod, run_fn = _resolve_loop_fn()
        seen_symphony_ids = []

        def _fake_suggest_swaps(symphony_id, score_tree, objective, correlation_data, *a, **k):
            seen_symphony_ids.append(symphony_id)
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _fake_suggest_swaps)

        run_fn()

        assert len(seen_symphony_ids) == 3, (
            f"Expected suggest_swaps to be called once per live symphony (3); got calls "
            f"for {seen_symphony_ids!r}."
        )
        assert len(set(seen_symphony_ids)) == 3, (
            f"Each call must target a DISTINCT symphony; got {seen_symphony_ids!r}"
        )

    def test_empty_symphony_list_is_a_clean_noop(self, monkeypatch):
        import advisors.asset_swap_engine as engine
        import database as db_module

        monkeypatch.setattr(db_module, "load_state", lambda: {}, raising=False)

        mod, run_fn = _resolve_loop_fn()
        call_count = {"n": 0}

        def _counting_suggest_swaps(*a, **k):
            call_count["n"] += 1
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch())

        _patch_suggest_swaps(monkeypatch, mod, _counting_suggest_swaps)

        run_fn()  # must not raise

        assert call_count["n"] == 0, (
            f"No live symphonies -> suggest_swaps must not be called; got "
            f"{call_count['n']} call(s)."
        )


# ===========================================================================
# AC-C2 edge case + AC-C4 — per-symphony D-1 isolation
# ===========================================================================


class TestAssetSwapLoopPerSymphonyIsolation:
    def test_one_symphony_score_fetch_failure_does_not_abort_the_loop(self, monkeypatch):
        """A hard exception fetching one symphony's Composer score tree must not
        prevent suggest_swaps from being called for the OTHER symphonies (AC-C2 edge
        case: 'One symphony's Composer /score or bar fetch fails mid-loop -> D-1
        contained, next symphony proceeds')."""
        import advisors.asset_swap_engine as engine
        import database as db_module
        import symphony_logic

        bot_state = _fake_bot_state(3)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        failing_hash = "hash-001"

        def _flaky_fetch(symphony_id):
            if symphony_id == failing_hash:
                raise RuntimeError("simulated Composer /score outage")
            return {"name": symphony_id, "children": []}

        monkeypatch.setattr(symphony_logic, "fetch_symphony_score", _flaky_fetch, raising=False)

        mod, run_fn = _resolve_loop_fn()
        seen_symphony_ids = []

        def _fake_suggest_swaps(symphony_id, score_tree, objective, correlation_data, *a, **k):
            seen_symphony_ids.append(symphony_id)
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _fake_suggest_swaps)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"run_weekly_asset_swap_suggestions must not propagate one symphony's "
                f"score-fetch failure (D-1); raised {exc!r}"
            )

        assert failing_hash not in seen_symphony_ids, (
            "The failing symphony must not reach suggest_swaps with a broken/missing score tree."
        )
        assert len(seen_symphony_ids) == 2, (
            f"The other 2 symphonies must still be processed despite one failure; got "
            f"{seen_symphony_ids!r}"
        )

    def test_one_symphony_suggest_swaps_exception_does_not_abort_the_loop(self, monkeypatch):
        """D-1: even if suggest_swaps itself raises for one symphony (rather than
        degrading internally, which it is documented to never do -- but the LOOP must
        still defend against a caller bug or unexpected exception), the other
        symphonies must still be processed."""
        import advisors.asset_swap_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(3)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        mod, run_fn = _resolve_loop_fn()
        seen_symphony_ids = []

        def _flaky_suggest_swaps(symphony_id, score_tree, objective, correlation_data, *a, **k):
            seen_symphony_ids.append(symphony_id)
            if len(seen_symphony_ids) == 1:
                raise RuntimeError("simulated suggest_swaps internal failure")
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _flaky_suggest_swaps)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"run_weekly_asset_swap_suggestions must not propagate a single "
                f"symphony's suggest_swaps exception (D-1); raised {exc!r}"
            )

        assert len(seen_symphony_ids) == 3, (
            f"All 3 symphonies must be attempted despite the first one raising; got "
            f"{seen_symphony_ids!r}"
        )


# ===========================================================================
# AC-C2 — objective defaults to reduce_correlation (v1 scope boundary)
# ===========================================================================


class TestAssetSwapLoopObjectiveDefault:
    def test_default_objective_is_reduce_correlation(self, monkeypatch):
        import advisors.asset_swap_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(1)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        mod, run_fn = _resolve_loop_fn()
        seen_objectives = []

        def _fake_suggest_swaps(symphony_id, score_tree, objective, correlation_data, *a, **k):
            seen_objectives.append(objective.objective_type)
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _fake_suggest_swaps)

        run_fn()

        assert seen_objectives == ["reduce_correlation"], (
            f"AC-C2 v1 scope: the weekly asset-swap loop's default objective must be "
            f"'reduce_correlation'; got {seen_objectives!r}"
        )


# ===========================================================================
# AC-C4 — advisory-only, no LIVE_EXECUTION touch
# ===========================================================================


class TestAssetSwapLoopNeverTouchesLiveExecution:
    def test_loop_module_source_does_not_reference_live_execution(self):
        """Static guard: the module hosting the loop function must not reference
        LIVE_EXECUTION anywhere in its source (advisory-only, AC-C4)."""
        mod, _fn = _resolve_loop_fn()
        source_path = getattr(mod, "__file__", None)
        assert source_path, f"Could not resolve source file for module {mod!r}"

        import pathlib

        source_text = pathlib.Path(source_path).read_text(encoding="utf-8")
        assert "LIVE_EXECUTION" not in source_text, (
            f"{source_path} references LIVE_EXECUTION -- the weekly asset-swap "
            f"suggestion loop must be strictly advisory-only (AC-C4), no trade path."
        )


# ===========================================================================
# PM-routed follow-up — lens_scores must be REAL and must ACTUALLY reorder
# candidates on real data, not just be a non-None kwarg (D is GREEN; this
# closes the "math fixed but dead in production" gap).
# ===========================================================================


class TestAssetSwapLoopWiresRealLensScoresAfterDIsGreen:
    """Reachability chain this class pins (R2-3 UPDATE, 2026-07-14 — see the
    retired test_wired_lens_scores_actually_reorder_candidates_on_real_data
    below for the full rationale): the "actually reorders candidates" leg of
    this chain no longer applies — generate_objective_directed_candidates was
    DELETED and its LLM-reasoned replacement does not do lens-blended
    statistical ranking. What remains pinned:

        database.get_latest_market_lens_cache()
            -> advisors.asset_swap_engine.extract_lens_scores(context)
            -> suggest_swaps(..., lens_scores=<real, non-empty dict>)

    A test that only asserts "lens_scores is not None" would pass for a stub
    that hardcodes an empty-but-truthy dict, or wires the wrong cache section
    -- test_lens_scores_extracted_from_market_lens_cache_and_passed_to_
    suggest_swaps below closes that hole by asserting the EXACT value passed
    equals extract_lens_scores() applied to the real cache bundle.
    """

    def _cache_row(self, momentum: dict | None) -> dict:
        """Build a MARKET_LENS_CACHE row shaped like database.
        get_latest_market_lens_cache()'s real return contract (raw_response
        already JSON-deserialized, per its docstring).

        STALE-FIXTURE FIX (2026-07-12, live droplet-DB E2E follow-up): the prior
        version put a fabricated "ticker_scores" key inside the technicals
        payload -- NO real producer ever emits that key. The REAL technicals
        payload shape (ai_advisor.py:542-552, advisors/lens_technicals.py:
        265-272) is {"ma_posture": {ticker: {...}}, "breadth": float,
        "momentum": {ticker: float}} -- momentum is the only real per-ticker
        signal (an UNBOUNDED raw 20-day return, not a [0,1] score). The other
        4 lenses are honestly unavailable, same as a real partial-lens night --
        sentiment/derivatives/macro are market-wide even when available (see
        tests/ai_advisor/test_cycle3_lens_informed_swaps.py), so leaving them
        unavailable here keeps this fixture focused on the technicals path
        without re-testing market-wide exclusion (already covered there)."""
        has_scores = bool(momentum)
        return {
            "id": 1,
            "advisor_role": "MARKET_LENS_CACHE",
            "raw_response": {
                "captured_at": "2026-07-12T00:00:00+00:00",
                "lenses": {
                    "technicals": {
                        "lens": "technicals",
                        "available": has_scores,
                        "payload": (
                            {"ma_posture": None, "breadth": None, "momentum": momentum}
                            if has_scores
                            else None
                        ),
                        "sources": [],
                    },
                    **{
                        name: {
                            "lens": name,
                            "available": False,
                            "payload": None,
                            "sources": [],
                        }
                        for name in ("sentiment", "derivatives", "macro", "fundamentals")
                    },
                },
            },
        }

    def _build_near_tied_correlation_fixture(self) -> dict:
        """A real (non-degenerate) correlation fixture with a genuine SMALL
        primary-score gap between QQQ and AGG (both highly correlated to SPY,
        AGG marginally more so) and an unambiguous GLD (near-zero corr, stays
        first regardless of lens). Numerically verified: |corr(SPY,QQQ)| ~=
        0.961, |corr(SPY,AGG)| ~= 0.984 (gap ~0.023), |corr(SPY,GLD)| ~= 0.21.
        Baseline (no lens) order: GLD, QQQ, AGG.

        Tickers are REAL lens_technicals._PROXY_UNIVERSE members (not synthetic
        "AAA"/"BBB"/"CCC") -- the candidate-pool-sourcing fix (PM-routed
        follow-up, 2026-07-12 second E2E) means the loop's candidate pool now
        comes from PROXY_UNIVERSE ∪ live logic_holdings, not an arbitrary
        get_tradeable_set() sample, so these tests use tickers that are
        actually reachable through that real pool regardless of how
        get_tradeable_set() is mocked."""
        rng = random.Random(11)
        spy = [rng.gauss(0.0, 1.0) for _ in range(30)]
        qqq = [v + rng.gauss(0.0, 0.30) for v in spy]
        agg = [v + rng.gauss(0.0, 0.20) for v in spy]
        gld = [rng.gauss(0.0, 1.0) for _ in range(30)]
        return {"SPY": spy, "QQQ": qqq, "AGG": agg, "GLD": gld}

    def _wire_symphony_with_held_ticker(self, monkeypatch, ticker: str) -> None:
        import symphony_logic

        monkeypatch.setattr(
            symphony_logic,
            "fetch_symphony_score",
            lambda symphony_id: {"name": symphony_id, "ticker": ticker, "children": []},
            raising=False,
        )

    def _wire_tradeable_universe(self, monkeypatch, tickers: frozenset) -> None:
        import advisors.universe_provider as up

        monkeypatch.setattr(up, "get_tradeable_set", lambda **kw: tickers, raising=False)

    def _capture_suggest_swaps_call(self, monkeypatch, mod) -> dict:
        """Patch suggest_swaps with a spy that records the FULL call shape
        (including the lens_scores kwarg, if any) and returns the captured
        dict (mutated in-place by the spy, read after run_fn())."""
        import advisors.asset_swap_engine as engine

        captured: dict = {}

        def _spy(
            symphony_id, score_tree, objective, correlation_data, available_assets=None, **kwargs
        ):
            captured["symphony_id"] = symphony_id
            captured["score_tree"] = score_tree
            captured["objective"] = objective
            captured["correlation_data"] = correlation_data
            captured["available_assets"] = available_assets
            captured["lens_scores"] = kwargs.get("lens_scores")
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _spy)
        return captured

    def test_lens_scores_extracted_from_market_lens_cache_and_passed_to_suggest_swaps(
        self, monkeypatch
    ):
        import advisors.asset_swap_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(1)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        # Real per-ticker signal is technicals.momentum -- an UNBOUNDED raw 20-day
        # return (~+/-0.05..0.15 in practice), not a pre-normalised [0,1] score.
        # extreme +/-0.20 values here so the extracted (squashed) scores stay
        # clearly separated regardless of the implementer's exact squashing formula.
        momentum = {"QQQ": -0.20, "AGG": 0.20}
        cache_row = self._cache_row(momentum)
        monkeypatch.setattr(
            db_module, "get_latest_market_lens_cache", lambda: cache_row, raising=False
        )

        self._wire_symphony_with_held_ticker(monkeypatch, "SPY")
        # Covers both possible pool-sourcing implementations: a hardcoded
        # PROXY_UNIVERSE ∪ holdings pool (tradeable_set is irrelevant) OR one
        # that additionally intersects against get_tradeable_set() for safety.
        self._wire_tradeable_universe(monkeypatch, frozenset({"QQQ", "AGG", "GLD", "SPY"}))

        mod, run_fn = _resolve_loop_fn()
        monkeypatch.setattr(
            mod,
            "_build_correlation_data",
            lambda tickers: self._build_near_tied_correlation_fixture(),
            raising=False,
        )
        captured = self._capture_suggest_swaps_call(monkeypatch, mod)

        run_fn()

        assert captured, "suggest_swaps was never called."
        actual_lens_scores = captured.get("lens_scores")
        assert actual_lens_scores, (
            f"suggest_swaps must be called with a non-empty lens_scores= kwarg once a "
            f"live MARKET_LENS_CACHE bundle exists (D is GREEN -- lens_scores must no "
            f"longer be unconditionally None). Got: {actual_lens_scores!r}"
        )

        # The passed lens_scores must be the REAL output of extract_lens_scores on the
        # cache's lens bundle -- not a hand-rolled/placeholder dict. Derived via the
        # real function on the same fixture input (never hardcoded).
        expected_lens_scores = engine.extract_lens_scores(cache_row["raw_response"]["lenses"])
        assert actual_lens_scores == expected_lens_scores, (
            f"lens_scores passed to suggest_swaps must equal extract_lens_scores() "
            f"applied to the real MARKET_LENS_CACHE bundle.\n"
            f"  expected: {expected_lens_scores!r}\n"
            f"  actual:   {actual_lens_scores!r}"
        )

    # test_wired_lens_scores_actually_reorder_candidates_on_real_data RETIRED
    # (R2-3, 2026-07-14): called the REAL generate_objective_directed_candidates
    # to prove lens_scores genuinely reordered its statistical ranking. That
    # deterministic generator was DELETED ([PM-ASSUMED Q4]) and replaced by the
    # LLM-reasoned generate_reasoned_swap_candidates, which does not do
    # lens-blended statistical ranking at all — selection is the LLM's. This is
    # an intentional, Q4-mandated production behavior change: the weekly
    # scheduler's lens_scores kwarg still reaches suggest_swaps (proven by the
    # sibling test_lens_scores_extracted_from_market_lens_cache_and_passed_to_
    # suggest_swaps above, unchanged) and still feeds per-candidate rationale/
    # persistence evidence via _build_candidate_lens_evidence (unchanged), but
    # no longer drives candidate SELECTION order — that guarantee, specific to
    # the deleted generator, is retired along with it. _apply_lens_blend itself
    # remains behaviorally unchanged and callable (AC-12); its own coverage
    # lives in tests/ai_advisor/test_lens_blend_efficacy.py (generator-independent).

    def test_no_cache_row_degrades_to_falsy_lens_scores(self, monkeypatch):
        """Cold-start (no MARKET_LENS_CACHE row yet): must degrade honestly to
        lens_scores=None/{} -- never fabricate scores, never crash."""
        import database as db_module

        bot_state = _fake_bot_state(1)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)
        monkeypatch.setattr(db_module, "get_latest_market_lens_cache", lambda: None, raising=False)

        mod, run_fn = _resolve_loop_fn()
        captured = self._capture_suggest_swaps_call(monkeypatch, mod)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"A cold-start (no cache row) must not raise; raised {exc!r}")

        assert not captured.get("lens_scores"), (
            f"With no MARKET_LENS_CACHE row, lens_scores passed to suggest_swaps must "
            f"be falsy (None or {{}}) -- honest degradation, never fabricated. Got: "
            f"{captured.get('lens_scores')!r}"
        )

    def test_cache_row_with_no_available_lenses_degrades_to_falsy_lens_scores(self, monkeypatch):
        """A cache row exists but every lens is available=False (a genuinely bad
        night) -- extract_lens_scores() honestly returns {}; the loop must pass
        that through as falsy, never substitute a fabricated score."""
        import database as db_module

        bot_state = _fake_bot_state(1)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        empty_cache_row = self._cache_row(momentum=None)
        monkeypatch.setattr(
            db_module, "get_latest_market_lens_cache", lambda: empty_cache_row, raising=False
        )

        mod, run_fn = _resolve_loop_fn()
        captured = self._capture_suggest_swaps_call(monkeypatch, mod)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"An all-unavailable cache row must not raise; raised {exc!r}")

        assert not captured.get("lens_scores"), (
            f"With a MARKET_LENS_CACHE row whose lenses are all available=False, "
            f"lens_scores passed to suggest_swaps must be falsy -- got "
            f"{captured.get('lens_scores')!r}"
        )

    def test_fetch_lens_scores_returns_non_empty_on_real_shaped_technicals_cache_row(
        self, monkeypatch
    ):
        """PM-requested direct check: _fetch_lens_scores() (the loop's own helper
        that wraps database.get_latest_market_lens_cache() + extract_lens_scores())
        must return a NON-EMPTY dict when the cache row has a real-shaped,
        available technicals.momentum block -- called directly, not just observed
        indirectly via the suggest_swaps spy above."""
        import database as db_module

        mod, _run_fn = _resolve_loop_fn()
        fetch_fn = getattr(mod, "_fetch_lens_scores", None)
        assert callable(fetch_fn), (
            f"{mod.__name__} must expose a callable _fetch_lens_scores() helper "
            f"(the read-only MARKET_LENS_CACHE -> extract_lens_scores wrapper)."
        )

        cache_row = self._cache_row({"AAA": -0.20, "BBB": 0.20})
        monkeypatch.setattr(
            db_module, "get_latest_market_lens_cache", lambda: cache_row, raising=False
        )

        result = fetch_fn()

        assert result, (
            f"_fetch_lens_scores() must return a non-empty dict on a real-shaped, "
            f"available technicals cache row; got {result!r}"
        )
        assert "AAA" in result and "BBB" in result, (
            f"_fetch_lens_scores() must surface per-ticker scores for every ticker "
            f"in technicals.momentum; got keys {sorted(result.keys())!r}"
        )

    def test_fetch_lens_scores_returns_empty_on_cold_cache(self, monkeypatch):
        """Regression pin: _fetch_lens_scores() must degrade to {} (not raise, not
        fabricate) when there is no cache row yet."""
        import database as db_module

        mod, _run_fn = _resolve_loop_fn()
        fetch_fn = getattr(mod, "_fetch_lens_scores", None)
        assert callable(fetch_fn), f"{mod.__name__} must expose _fetch_lens_scores()."

        monkeypatch.setattr(db_module, "get_latest_market_lens_cache", lambda: None, raising=False)

        result = fetch_fn()
        assert result == {} or not result, (
            f"_fetch_lens_scores() must degrade to an empty/falsy result on a cold "
            f"cache; got {result!r}"
        )


# ===========================================================================
# PM-routed follow-up #2 (2026-07-12, second live-droplet-DB E2E re-run) —
# extract_lens_scores now works (lens_scores is real), but the candidate POOL
# never overlaps the lens-covered universe, so lens_evidence stayed {} end to
# end. Root cause (E2E-confirmed): candidate_pool = sorted(get_tradeable_set())
# [:_ASSET_SWAP_CANDIDATE_POOL_SIZE] (weekly_suggestions_scheduler.py: the
# alphabetical-first-15 of the ~12,748-symbol universe) never intersects
# lens_technicals._PROXY_UNIVERSE (the 10 tickers the technicals lens actually
# scores) union live logic_holdings. _build_candidate_lens_evidence(ticker,
# lens_scores) (asset_swap_engine.py:758-780) returns {} whenever
# lens_scores.get(ticker) misses -- which it always does for an alphabetical
# candidate pool.
#
# PM DESIGN CALL [PM-ASSUMED]: candidate pool = the LENS-COVERED universe
# (lens_technicals._PROXY_UNIVERSE ∪ live logic_holdings across all live
# symphonies) so swaps are both sensible (real market-proxy / actually-held
# tickers, not an alphabetical accident) AND lens-informed. Broad
# correlation-screened discovery across the FULL ~12,748-symbol universe is a
# documented FUTURE enhancement, out of this cycle's scope. The current
# alphabetical pool provides ZERO value today (garbage swap targets) --
# replacing it loses nothing.
#
# DESIGN QUESTION PINNED (per PM open question to the fixtures): the
# swap-out-target exclusion is PER-SYMPHONY -- each symphony's own candidate
# pool excludes THAT symphony's own held ticker(s) (no swap-into-self for
# itself), not a global cross-symphony exclusion. A ticker held by symphony A
# is a perfectly valid swap candidate for symphony B (no cross-symphony
# conflict) -- only a symphony swapping into an asset it ALREADY holds is a
# no-op. This mirrors the engine's own existing per-symphony filter
# (suggest_swaps skips `candidate_asset in present_tickers`, extracted from
# THIS symphony's own score_tree) -- the loop-level pool exclusion is
# defense-in-depth / explicit-by-construction, not a new behavioural class.
# ===========================================================================


class TestAssetSwapLoopCandidatePoolSourcing:
    def _wire_bot_state_with_holdings(self, monkeypatch, entries: dict) -> dict:
        """entries: {symphony_hash: {"name": ..., "logic_holdings": {ticker: weight, ...}}}.
        logic_holdings mirrors the exact bot_state field name
        _build_technicals_section/_build_fundamentals_section read
        (ai_advisor.py:520-526, :1184-1190) -- NOT the Composer score_tree
        structure (extract_tickers), a separate bot_state-level field."""
        import database as db_module

        monkeypatch.setattr(db_module, "load_state", lambda: entries, raising=False)
        return entries

    def _wire_garbage_alphabetical_universe(self, monkeypatch) -> frozenset:
        """A tradeable universe engineered so the OLD sorted(...)[:15] sample is
        pure garbage relative to the lens-covered universe -- every entry here
        sorts alphabetically BEFORE every real lens_technicals._PROXY_UNIVERSE
        ticker (which start at 'A' for AGG but these start even earlier with a
        leading digit-like prefix), so a correct fix must NOT rely on
        get_tradeable_set() alphabetical order to reach PROXY_UNIVERSE tickers."""
        import advisors.universe_provider as up

        garbage = frozenset({f"0GARBAGE{i:02d}" for i in range(20)})
        monkeypatch.setattr(up, "get_tradeable_set", lambda **kw: garbage, raising=False)
        return garbage

    def test_candidate_pool_sourced_from_proxy_universe_not_alphabetical_sample(self, monkeypatch):
        """The candidate pool passed to suggest_swaps must be built from
        lens_technicals._PROXY_UNIVERSE (∪ live logic_holdings), NOT
        sorted(get_tradeable_set())[:N] -- proven by making the alphabetical
        sample pure garbage (sorts before every real ticker) and asserting the
        garbage does NOT dominate the pool while real proxy tickers DO appear."""
        import advisors.asset_swap_engine as engine
        from advisors.lens_technicals import _PROXY_UNIVERSE

        self._wire_bot_state_with_holdings(
            monkeypatch,
            {
                "hash-000": {
                    "name": "Pool Sourcing Symphony",
                    "account_uuid": "acc-1",
                    "logic_holdings": {},
                }
            },
        )
        garbage = self._wire_garbage_alphabetical_universe(monkeypatch)

        mod, run_fn = _resolve_loop_fn()

        captured = {}

        def _spy(
            symphony_id, score_tree, objective, correlation_data, available_assets=None, **kwargs
        ):
            captured["available_assets"] = available_assets
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _spy)

        run_fn()

        assert "available_assets" in captured, "suggest_swaps was never called."
        pool = set(captured["available_assets"] or [])

        naive_alphabetical_sample = set(sorted(garbage)[:15])
        assert not (pool <= naive_alphabetical_sample), (
            f"Candidate pool must NOT be the naive sorted(get_tradeable_set())[:15] "
            f"alphabetical garbage sample -- got pool={sorted(pool)!r}, naive sample "
            f"would have been {sorted(naive_alphabetical_sample)!r}."
        )
        overlap = pool & set(_PROXY_UNIVERSE)
        assert len(overlap) >= 5, (
            f"Candidate pool must be substantially sourced from "
            f"lens_technicals._PROXY_UNIVERSE (the lens-covered universe) -- expected "
            f"at least 5 of {sorted(_PROXY_UNIVERSE)!r} to appear; got pool="
            f"{sorted(pool)!r} (overlap={sorted(overlap)!r})."
        )

    def test_candidate_pool_includes_live_logic_holdings(self, monkeypatch):
        """Live logic_holdings (a real position some symphony currently holds)
        must be reachable as a swap candidate too -- not just the fixed
        PROXY_UNIVERSE floor. Uses a ticker outside PROXY_UNIVERSE so this is
        unambiguously attributable to the logic_holdings union, not the proxy
        floor."""
        import advisors.asset_swap_engine as engine

        held_elsewhere_ticker = "MSFT"  # not a PROXY_UNIVERSE member
        self._wire_bot_state_with_holdings(
            monkeypatch,
            {
                "hash-000": {
                    "name": "Pool Sourcing Symphony A",
                    "account_uuid": "acc-1",
                    "logic_holdings": {held_elsewhere_ticker: 1.0},
                }
            },
        )
        self._wire_garbage_alphabetical_universe(monkeypatch)

        mod, run_fn = _resolve_loop_fn()
        captured = {}

        def _spy(
            symphony_id, score_tree, objective, correlation_data, available_assets=None, **kwargs
        ):
            captured["available_assets"] = available_assets
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _spy)

        run_fn()

        pool = set(captured.get("available_assets") or [])
        assert held_elsewhere_ticker in pool, (
            f"Live logic_holdings ({held_elsewhere_ticker!r}) must be unioned into the "
            f"candidate pool alongside lens_technicals._PROXY_UNIVERSE; got pool="
            f"{sorted(pool)!r}"
        )

    def test_candidate_pool_excludes_this_symphonys_own_held_ticker(self, monkeypatch):
        """PER-SYMPHONY exclusion (design question pinned above): a symphony
        whose OWN Composer score_tree holds a PROXY_UNIVERSE ticker (AGG) must
        not see AGG offered back to itself as a swap candidate (no
        swap-into-self) -- even though AGG is otherwise a normal pool member."""
        import advisors.asset_swap_engine as engine
        import symphony_logic

        self._wire_bot_state_with_holdings(
            monkeypatch,
            {
                "hash-000": {
                    "name": "Self Swap Symphony",
                    "account_uuid": "acc-1",
                    "logic_holdings": {},
                }
            },
        )
        self._wire_garbage_alphabetical_universe(monkeypatch)
        monkeypatch.setattr(
            symphony_logic,
            "fetch_symphony_score",
            lambda symphony_id: {"name": symphony_id, "ticker": "AGG", "children": []},
            raising=False,
        )

        mod, run_fn = _resolve_loop_fn()
        captured = {}

        def _spy(
            symphony_id, score_tree, objective, correlation_data, available_assets=None, **kwargs
        ):
            captured["available_assets"] = available_assets
            return engine.SwapRunResult(gate_batch=engine._empty_gate_batch(), objective=objective)

        _patch_suggest_swaps(monkeypatch, mod, _spy)

        run_fn()

        pool = set(captured.get("available_assets") or [])
        assert "AGG" not in pool, (
            f"The candidate pool for a symphony that already holds AGG must exclude "
            f"AGG (no swap-into-self); got pool={sorted(pool)!r}"
        )

    def test_persisted_asset_swap_rows_carry_non_empty_lens_evidence_end_to_end(self, monkeypatch):
        """THE end-to-end proof the live E2E checks: with a real-shaped lens
        cache AND the fixed (lens-covered) candidate pool, a REAL (unmocked)
        suggest_swaps() run must persist at least one ASSET_SWAP row whose
        raw_response["lens_evidence"] is NON-EMPTY.

        suggest_swaps persists EVERY gated proposal regardless of verdict (RC-4,
        asset_swap_engine.py:1287-1318) -- so this only needs the backtest layer
        mocked (no live Composer calls), not a forced ADOPT_CANDIDATE winner.
        """
        import advisors.asset_swap_engine as engine
        import database as db_module
        import symphony_logic
        from advisors import symphony_schema

        # Held ticker deliberately OUTSIDE PROXY_UNIVERSE so it never collides
        # with (and is never confused for) a swap candidate.
        held_ticker = "MSFT"
        # R2-3: a REAL, structurally-valid Composer tree (asset_swap_engine's
        # new validate_tree guard rejects the old {"ticker": ..., "children":
        # []} root-carries-a-ticker minimal dict this test used pre-R2-3 --
        # validate_tree requires the real "step" vocabulary). Built via the
        # real symphony_schema constructors so it is genuinely valid.
        monkeypatch.setattr(
            symphony_logic,
            "fetch_symphony_score",
            lambda symphony_id: symphony_schema.make_root(
                symphony_id,
                "daily",
                [symphony_schema.make_weight_equal([symphony_schema.make_asset(held_ticker)])],
            ),
            raising=False,
        )
        self._wire_bot_state_with_holdings(
            monkeypatch,
            {
                "hash-000": {
                    "name": "Lens Evidence E2E Symphony",
                    "account_uuid": "acc-1",
                    "logic_holdings": {held_ticker: 1.0},
                }
            },
        )
        self._wire_garbage_alphabetical_universe(monkeypatch)

        # Real-shaped MARKET_LENS_CACHE: technicals.momentum covers a real
        # PROXY_UNIVERSE ticker (AGG) with a strong, unambiguous value.
        cache_row = {
            "id": 1,
            "advisor_role": "MARKET_LENS_CACHE",
            "raw_response": {
                "captured_at": "2026-07-12T00:00:00+00:00",
                "lenses": {
                    "technicals": {
                        "lens": "technicals",
                        "available": True,
                        "payload": {"ma_posture": None, "breadth": None, "momentum": {"AGG": 0.12}},
                        "sources": [],
                    },
                    **{
                        name: {"lens": name, "available": False, "payload": None, "sources": []}
                        for name in ("sentiment", "derivatives", "macro", "fundamentals")
                    },
                },
            },
        }
        monkeypatch.setattr(
            db_module, "get_latest_market_lens_cache", lambda: cache_row, raising=False
        )

        # Mock the true network boundaries -- Composer backtest AND the LLM seam
        # (R2-3: generate_reasoned_swap_candidates replaced the deleted
        # deterministic generate_objective_directed_candidates; candidate
        # SELECTION is the LLM's now, not something this end-to-end persistence
        # test should depend on being reachable/deterministic without
        # credentials). suggest_swaps' own gate/lens-evidence/persist logic
        # downstream of generation still runs FOR REAL.
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {"sharpe": 1.0}
        mock_bt_result.daily_returns = {
            f"day{i}": 0.001 * (1 if i % 2 == 0 else -1) for i in range(30)
        }
        mock_bt_result.data_warnings = []

        captured_inserts = []

        def _capture_insert(**kwargs):
            captured_inserts.append(kwargs)

        import advisors.asset_swap_engine as _ase_mod

        reasoned_pairs = [
            _ase_mod.SwapCandidate(
                incumbent_asset=held_ticker, candidate_asset="AGG", rationale="x"
            )
        ]
        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch("database.insert_advisor_observation", side_effect=_capture_insert),
            patch(
                "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                return_value=reasoned_pairs,
            ),
        ):
            mod, run_fn = _resolve_loop_fn()
            run_fn()

        assert captured_inserts, (
            "insert_advisor_observation was never called -- suggest_swaps must persist "
            "every gated proposal (RC-4), regardless of verdict, once the candidate "
            "pool contains at least one backtestable candidate."
        )

        asset_swap_rows = [kw for kw in captured_inserts if kw.get("advisor_role") == "ASSET_SWAP"]
        assert asset_swap_rows, (
            f"Expected at least one persisted ASSET_SWAP row; got roles="
            f"{[kw.get('advisor_role') for kw in captured_inserts]!r}"
        )

        non_empty_lens_evidence_rows = [
            row
            for row in asset_swap_rows
            if isinstance(row.get("raw_response"), dict)
            and row["raw_response"].get("lens_evidence")
        ]
        assert non_empty_lens_evidence_rows, (
            f"THE END-TO-END GAP: at least one persisted ASSET_SWAP row must carry a "
            f"NON-EMPTY lens_evidence (AC-4) once the candidate pool is lens-covered "
            f"and a real-shaped lens cache exists. Got raw_response['lens_evidence'] "
            f"values: {[row.get('raw_response', {}).get('lens_evidence') for row in asset_swap_rows]!r}. "
            f"If this is still {{}}, the candidate pool never overlapped AGG (the only "
            f"ticker with lens evidence in this fixture) -- verify the pool sourcing "
            f"fix actually reaches lens_technicals._PROXY_UNIVERSE."
        )
