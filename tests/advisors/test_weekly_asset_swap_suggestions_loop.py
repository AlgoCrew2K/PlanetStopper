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
    """Reachability chain this class pins:

        database.get_latest_market_lens_cache()
            -> advisors.asset_swap_engine.extract_lens_scores(context)
            -> suggest_swaps(..., lens_scores=<real, non-empty dict>)
            -> generate_objective_directed_candidates (via suggest_swaps)
            -> _apply_lens_blend actually reorders candidates

    A test that only asserts "lens_scores is not None" would pass for a stub
    that hardcodes an empty-but-truthy dict, or wires the wrong cache section,
    or extracts scores that happen not to move anything on real correlation
    data -- none of which would actually make D live. Test 2 below closes that
    hole by capturing the EXACT correlation_data/available_assets the loop
    assembled and feeding the EXACT lens_scores it extracted through the REAL
    generate_objective_directed_candidates, proving the full chain reorders.
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
        primary-score gap between AAA and BBB (both highly correlated to SPY,
        BBB marginally more so) and an unambiguous CCC (near-zero corr, stays
        first regardless of lens). Numerically verified: |corr(SPY,AAA)| ~=
        0.961, |corr(SPY,BBB)| ~= 0.984 (gap ~0.023), |corr(SPY,CCC)| ~= 0.21.
        Baseline (no lens) order: CCC, AAA, BBB."""
        rng = random.Random(11)
        spy = [rng.gauss(0.0, 1.0) for _ in range(30)]
        aaa = [v + rng.gauss(0.0, 0.30) for v in spy]
        bbb = [v + rng.gauss(0.0, 0.20) for v in spy]
        ccc = [rng.gauss(0.0, 1.0) for _ in range(30)]
        return {"SPY": spy, "AAA": aaa, "BBB": bbb, "CCC": ccc}

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
        momentum = {"AAA": -0.20, "BBB": 0.20}
        cache_row = self._cache_row(momentum)
        monkeypatch.setattr(
            db_module, "get_latest_market_lens_cache", lambda: cache_row, raising=False
        )

        self._wire_symphony_with_held_ticker(monkeypatch, "SPY")
        self._wire_tradeable_universe(monkeypatch, frozenset({"AAA", "BBB", "CCC"}))

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

    def test_wired_lens_scores_actually_reorder_candidates_on_real_data(self, monkeypatch):
        """The adversarial core of this gap: prove the full chain is NOT dead.

        Captures the EXACT correlation_data/available_assets/objective the loop
        assembled, then calls the REAL generate_objective_directed_candidates
        twice with those SAME inputs -- once with lens_scores=None (baseline),
        once with the ACTUAL lens_scores the loop extracted and passed. If the
        wiring is genuine, BBB (marginally worse primary score, strongly
        lens-favored) must move ahead of AAA; CCC (commanding primary lead)
        must stay first regardless (AC-D2 both halves, exercised end-to-end
        through the real cache-extraction path, not a synthetic dict).
        """
        import advisors.asset_swap_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(1)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        # Extreme +/-0.20 raw momentum -- see the extraction test above for why
        # (unbounded raw signal, squashing formula is the implementer's choice).
        cache_row = self._cache_row({"AAA": -0.20, "BBB": 0.20})
        monkeypatch.setattr(
            db_module, "get_latest_market_lens_cache", lambda: cache_row, raising=False
        )

        self._wire_symphony_with_held_ticker(monkeypatch, "SPY")
        self._wire_tradeable_universe(monkeypatch, frozenset({"AAA", "BBB", "CCC"}))

        mod, run_fn = _resolve_loop_fn()
        monkeypatch.setattr(
            mod,
            "_build_correlation_data",
            lambda tickers: self._build_near_tied_correlation_fixture(),
            raising=False,
        )
        captured = self._capture_suggest_swaps_call(monkeypatch, mod)

        run_fn()

        assert captured.get("lens_scores"), (
            "Precondition failed: suggest_swaps must have received a non-empty "
            "lens_scores kwarg for this reorder proof to be meaningful."
        )

        baseline = engine.generate_objective_directed_candidates(
            symphony_id=captured["symphony_id"],
            objective=captured["objective"],
            correlation_data=captured["correlation_data"],
            available_assets=captured["available_assets"],
            lens_scores=None,
        )
        blended = engine.generate_objective_directed_candidates(
            symphony_id=captured["symphony_id"],
            objective=captured["objective"],
            correlation_data=captured["correlation_data"],
            available_assets=captured["available_assets"],
            lens_scores=captured["lens_scores"],
        )

        baseline_tickers = [c["ticker"] if isinstance(c, dict) else c for c in baseline]
        blended_tickers = [c["ticker"] if isinstance(c, dict) else c for c in blended]

        assert blended_tickers != baseline_tickers, (
            f"The lens_scores the loop actually extracted and passed to suggest_swaps "
            f"produced NO reordering versus lens_scores=None on the SAME "
            f"correlation_data/available_assets the loop assembled -- the wiring is "
            f"dead on real data even though a non-empty dict is passed.\n"
            f"  baseline (no lens): {baseline_tickers!r}\n"
            f"  blended (real wired lens_scores): {blended_tickers!r}"
        )
        assert blended_tickers.index("BBB") < blended_tickers.index("AAA"), (
            f"BBB (marginally worse primary score, strongly lens-favored via the real "
            f"MARKET_LENS_CACHE bundle) must move ahead of AAA once genuinely wired; "
            f"got blended order {blended_tickers!r}"
        )
        assert blended_tickers[0] == "CCC", (
            f"CCC's commanding primary lead (unambiguous lowest correlation) must "
            f"survive the real wired lens evidence (AC-D2: large margin never "
            f"inverted); got blended order {blended_tickers!r}"
        )

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
