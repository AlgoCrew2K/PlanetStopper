"""RED tests — Workstream C / AC-C1: run_weekly_logic_change_suggestions() loop.

Neither the function nor any module wiring it exists yet -- module-resolution
pytest.fail below gives a clean FAILED (not a collection ERROR), mirroring
tests/advisors/test_weekly_asset_swap_suggestions_loop.py's pattern.

AC-C1: "run_weekly_logic_change_suggestions() enumerates live symphonies
(database.load_state()), resolves each Composer hash, fetches its score tree,
and calls suggest_logic_changes(symphony_id, score_tree, objective, ...).
Per-symphony D-1 containment -- one symphony's failure does not block the
others. Rows persist as LOGIC_CHANGE (engine already does)."

AC-C3: "The engines themselves are UNCHANGED (their existing tests stay
green) -- this is purely the caller/loop layer." suggest_logic_changes is
mocked (the loop's job is enumeration + D-1 isolation, not re-testing the
engine -- that is tests/ai_advisor/test_logic_change_engine.py's job).

AC-C4: "No LIVE_EXECUTION interaction; advisory-only."
"""

from __future__ import annotations

import importlib

import pytest

_CANDIDATE_MODULES = (
    "advisors.weekly_suggestions_scheduler",
    "advisors.logic_change_engine",
)

_FN_NAME = "run_weekly_logic_change_suggestions"


def _resolve_loop_fn():
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
        f"{type(last_exc).__name__ if last_exc else 'none'}. AC-C1 requires a "
        f"callable weekly logic-change suggestion loop."
    )


def _fake_bot_state(n: int) -> dict:
    return {
        f"hash-{i:03d}": {"name": f"Logic Loop Symphony {i}", "account_uuid": "acc-1"}
        for i in range(n)
    }


def _patch_suggest_logic_changes(monkeypatch, loop_module, fake_fn) -> None:
    """See test_weekly_asset_swap_suggestions_loop.py's _patch_suggest_swaps for the
    rationale: patch both the canonical engine attribute and (if present) the loop
    module's own imported-name attribute."""
    import advisors.logic_change_engine as engine

    monkeypatch.setattr(engine, "suggest_logic_changes", fake_fn, raising=False)
    if hasattr(loop_module, "suggest_logic_changes"):
        monkeypatch.setattr(loop_module, "suggest_logic_changes", fake_fn, raising=False)


@pytest.fixture(autouse=True)
def _hermetic_score_fetch(monkeypatch):
    """AC-C1 names fetch its score tree as a loop responsibility -- neutralise the
    live Composer /score seam so no implementation choice touches the network."""
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


# ===========================================================================
# AC-C1 — enumerates live symphonies and calls suggest_logic_changes once each
# ===========================================================================


class TestLogicChangeLoopEnumeration:
    def test_calls_suggest_logic_changes_once_per_live_symphony(self, monkeypatch):
        import advisors.logic_change_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(3)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        mod, run_fn = _resolve_loop_fn()
        seen_symphony_ids = []

        def _fake_suggest_logic_changes(symphony_id, score_tree, objective, *a, **k):
            seen_symphony_ids.append(symphony_id)
            return engine.LogicChangeRunResult(
                gate_batch=engine._empty_gate_batch(), objective=objective
            )

        _patch_suggest_logic_changes(monkeypatch, mod, _fake_suggest_logic_changes)

        run_fn()

        assert len(seen_symphony_ids) == 3, (
            f"Expected suggest_logic_changes to be called once per live symphony (3); "
            f"got calls for {seen_symphony_ids!r}."
        )
        assert len(set(seen_symphony_ids)) == 3, (
            f"Each call must target a DISTINCT symphony; got {seen_symphony_ids!r}"
        )

    def test_empty_symphony_list_is_a_clean_noop(self, monkeypatch):
        import advisors.logic_change_engine as engine
        import database as db_module

        monkeypatch.setattr(db_module, "load_state", lambda: {}, raising=False)

        mod, run_fn = _resolve_loop_fn()
        call_count = {"n": 0}

        def _counting_suggest_logic_changes(*a, **k):
            call_count["n"] += 1
            return engine.LogicChangeRunResult(gate_batch=engine._empty_gate_batch())

        _patch_suggest_logic_changes(monkeypatch, mod, _counting_suggest_logic_changes)

        run_fn()  # must not raise

        assert call_count["n"] == 0, (
            f"No live symphonies -> suggest_logic_changes must not be called; got "
            f"{call_count['n']} call(s)."
        )


# ===========================================================================
# AC-C1 edge case + AC-C4 — per-symphony D-1 isolation
# ===========================================================================


class TestLogicChangeLoopPerSymphonyIsolation:
    def test_one_symphony_score_fetch_failure_does_not_abort_the_loop(self, monkeypatch):
        import advisors.logic_change_engine as engine
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

        def _fake_suggest_logic_changes(symphony_id, score_tree, objective, *a, **k):
            seen_symphony_ids.append(symphony_id)
            return engine.LogicChangeRunResult(
                gate_batch=engine._empty_gate_batch(), objective=objective
            )

        _patch_suggest_logic_changes(monkeypatch, mod, _fake_suggest_logic_changes)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"run_weekly_logic_change_suggestions must not propagate one symphony's "
                f"score-fetch failure (D-1); raised {exc!r}"
            )

        assert failing_hash not in seen_symphony_ids, (
            "The failing symphony must not reach suggest_logic_changes with a "
            "broken/missing score tree."
        )
        assert len(seen_symphony_ids) == 2, (
            f"The other 2 symphonies must still be processed despite one failure; got "
            f"{seen_symphony_ids!r}"
        )

    def test_one_symphony_suggest_logic_changes_exception_does_not_abort_the_loop(
        self, monkeypatch
    ):
        import advisors.logic_change_engine as engine
        import database as db_module

        bot_state = _fake_bot_state(3)
        monkeypatch.setattr(db_module, "load_state", lambda: bot_state, raising=False)

        mod, run_fn = _resolve_loop_fn()
        seen_symphony_ids = []

        def _flaky_suggest_logic_changes(symphony_id, score_tree, objective, *a, **k):
            seen_symphony_ids.append(symphony_id)
            if len(seen_symphony_ids) == 1:
                raise RuntimeError("simulated suggest_logic_changes internal failure")
            return engine.LogicChangeRunResult(
                gate_batch=engine._empty_gate_batch(), objective=objective
            )

        _patch_suggest_logic_changes(monkeypatch, mod, _flaky_suggest_logic_changes)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"run_weekly_logic_change_suggestions must not propagate a single "
                f"symphony's suggest_logic_changes exception (D-1); raised {exc!r}"
            )

        assert len(seen_symphony_ids) == 3, (
            f"All 3 symphonies must be attempted despite the first one raising; got "
            f"{seen_symphony_ids!r}"
        )


# ===========================================================================
# AC-C4 — advisory-only, no LIVE_EXECUTION touch
# ===========================================================================


class TestLogicChangeLoopNeverTouchesLiveExecution:
    def test_loop_module_source_does_not_reference_live_execution(self):
        mod, _fn = _resolve_loop_fn()
        source_path = getattr(mod, "__file__", None)
        assert source_path, f"Could not resolve source file for module {mod!r}"

        import pathlib

        source_text = pathlib.Path(source_path).read_text(encoding="utf-8")
        assert "LIVE_EXECUTION" not in source_text, (
            f"{source_path} references LIVE_EXECUTION -- the weekly logic-change "
            f"suggestion loop must be strictly advisory-only (AC-C4), no trade path."
        )
