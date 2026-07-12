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
"""

from __future__ import annotations

import importlib

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

        monkeypatch.setattr(up, "get_tradeable_set", lambda **kw: frozenset({"AAA", "BBB"}), raising=False)
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
            "The failing symphony must not reach suggest_swaps with a broken/missing "
            "score tree."
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
