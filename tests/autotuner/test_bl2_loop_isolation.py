"""RED tests -- BL-2: per-symphony optimization loop exception isolation +
batch visibility (autotuner.py:2558-3296).

Feature plan: feature-plans/audit-bl2-autotuner-loop-isolation.md
Source: docs/audit/TWO-WEEK-REVIEW-2026-08-04.md Sec4 Finding T2, Sec6 Backlog BL-2

Background
----------
run_autotuner()'s per-symphony loop (``for normalized_name in symphony_names:``,
autotuner.py:2558-3296) has NO surrounding try/except. Optuna study.optimize,
CPCV/PBO, the OOS adoption cascade, and database.save_autotune_run are all
entirely unguarded -- only the two TAIL advisor-producer calls (Overfitting
Conscience, Divergence Explainer) are already isolated in their own local
try/except. An uncaught exception ANYWHERE in the unguarded portion of one
symphony's processing propagates out of the whole for-loop and aborts
run_autotuner() for every symphony not yet reached.

What these tests pin
---------------------
- AC-1: an uncaught exception anywhere in one symphony's per-symphony body is
  caught, logged via logging.error(..., exc_info=True) naming the symphony,
  and the loop continues -- never propagating out of run_autotuner().
- AC-2: run_autotuner()'s return gains
  optimization_results["_batch_summary"] = {"attempted": N, "completed": M}
  -- the concrete shape pinned for this RED cycle (team-lead approved).
- AC-4: a symphony's isolated exception does not retroactively affect
  already-processed sibling symphonies' autotune_runs rows.
- AC-5: a zero-exception batch is byte-identical to today except the
  additive _batch_summary counters.

Design decisions (team-lead approved, see RED-plan SendMessage)
-----------------------------------------------------------------
1. ``symphony_names = set()`` (autotuner.py:2547) has non-deterministic
   string-hash-based iteration order (no PYTHONHASHSEED pin). Tests assert
   ORDER-INDEPENDENT invariants only: which symphonies' autotune_runs rows
   exist/don't-exist by normalized name, and aggregate attempted/completed
   counts -- never "first/last symphony processed" positional claims.
2. Two deterministic exception-injection points, both keyed on a kwarg
   (never timing-based): EARLY via optuna.create_study's ``study_name``
   kwarg (format ``f"{study_timestamp}__{normalized_name}"``,
   autotuner.py:2755), and LATE via database.save_autotune_run's
   ``symphony_id`` kwarg (autotuner.py:3218-3239) -- the plan's explicit
   edge case ("exception during save_autotune_run itself").
3. INV-1 honesty boundary: the real 2026-07-24 partial-batch incident was
   root-caused (``.claude/inv-findings-2026-08-05.md``) to a deploy-restart
   SIGTERM, which a try/except CANNOT catch. No test in this file claims
   SIGTERM/deploy-restart coverage -- this isolation guards the
   uncaught-exception class only (test_exception_isolation_logs_via_
   logging_error_with_exc_info_naming_symphony asserts the log message does
   NOT claim SIGTERM coverage).

Fixture/harness pattern reused (house lesson
``feedback_consumer_suite_discovery_before_sufficiency`` -- reuse before
hand-rolling): the isolated tmp-DB fixture + ``_autotuner_patches`` mock-study
harness from ``tests/autotuner/test_autotune_runs_persistence.py`` (the
SIMPLER of the two sibling harnesses -- it does NOT set ``fake_study.trials``
explicitly, relying on the documented incidental TypeError-on-iteration ->
``completed_trials = []`` swallow inside autotuner.py, exactly as that file's
own tests already rely on), extended here to multiple symphonies via an
``optuna.create_study`` ``side_effect`` keyed on the ``study_name`` kwarg.

Every test in this file MUST FAIL until autotuner.py gains the AC-1 outer
try/except + AC-2 _batch_summary counters.

No live APIs, no real Optuna storage. SQLite DB redirected to tmp_path via
the isolated_db fixture. -n0 only.
"""

from __future__ import annotations

import contextlib
import io
import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: isolated, fresh SQLite DB per test.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect database.DB_FILE to a per-test temp path and initialise schema.

    Function scope (default) -- every test gets an empty DB. Monkeypatch is
    restored automatically after each test, so DB_FILE is never permanently
    changed in the module-level singleton.
    """
    import database as db_module  # lazy -- see module docstring import-collision note

    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Multi-symphony fixture helpers (self-contained per this suite's own
# per-file convention -- not imported from sibling test files).
# ---------------------------------------------------------------------------

_NAMES = ["Symphony Alpha", "Symphony Beta", "Symphony Gamma"]


def _build_multi_symphony_bot_state(names: list[str]) -> dict:
    """N-symphony bot_state -- one entry per name, sym_id = 'sym-<i>'."""
    return {
        f"sym-{i}": {"name": name, "account_uuid": f"acc-{i}"}
        for i, name in enumerate(names, start=1)
    }


def _build_multi_symphony_history(sym_ids: list[str], n_days: int = 5) -> dict:
    """Minimal N-day synthetic history, one entry per sym_id, all sharing the
    SAME date range (mirrors autotuner.py's date-union-across-symphonies
    partition logic -- all_dates is the union of every sym_id's dates)."""
    tick = {
        "return": 2.0,
        "mc_prob": 50.0,
        "vol": 1.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, n_days + 1)]
    return {sym_id: {d: [tick] for d in dates} for sym_id in sym_ids}


def _fallback_params() -> dict:
    """Fallback params derived from the real DEFAULT_STRATEGY -- no hardcoded values."""
    import database  # lazy import

    return database.DEFAULT_STRATEGY.copy()


def _ai_best_params() -> dict:
    """Valid Optuna best_params -- includes all 7 OPTUNA_SEARCH_SPACE_KEYS so
    schema validation in run_autotuner passes."""
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 0.51,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _no_trigger_vwap_side_effect(**kwargs):
    """VWAP helper side effect that never fires a break -- all param sets get alpha 0."""
    return (0, 0, False, False)


def _make_phase1_spec_bundle() -> int:
    """Insert a minimal all-THEORY Phase-1 spec bundle and return its integer id.

    Required since run_autotuner demands an explicit spec_bundle_id (NN1
    spec-freeze discipline). utility_family=CRRA drives _objective_kind ==
    "crra_eu" inside run_autotuner.
    """
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)
    _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    for name, value in canon_facets.items():
        _db.insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name=name,
            facet_value=value,
            freeze_discipline="THEORY",
            justification="Phase-1 test fixture -- all-THEORY bundle",
        )
    return bundle_id


def _make_create_study_side_effect(best_params, *, broken_symphony=None, broken_exc=None):
    """Return a side_effect for optuna.create_study keyed on the study_name
    kwarg's normalized-name suffix (autotuner.py:2755:
    study_name=f"{study_timestamp}__{normalized_name}"). Exactly the symphony
    matching broken_symphony gets a study whose .optimize() raises
    broken_exc; every other symphony gets a normal working fake study
    (mirrors test_autotune_runs_persistence.py's simpler _autotuner_patches
    -- .trials deliberately left unset).
    """

    def _side_effect(*args, **kwargs):
        study_name = kwargs.get("study_name", "")
        fake_study = MagicMock(name=f"fake_study[{study_name}]")
        fake_study.best_params = best_params
        fake_study.best_value = 1.0
        if broken_symphony is not None and study_name.endswith(f"__{broken_symphony}"):
            fake_study.optimize = MagicMock(side_effect=broken_exc)
        else:
            fake_study.optimize = MagicMock(return_value=None)
        return fake_study

    return _side_effect


@contextlib.contextmanager
def _autotuner_patches_multi(
    best_params,
    fallback,
    *,
    sym_ids,
    broken_symphony=None,
    broken_exc=None,
    vwap_side_effect=None,
    n_days=5,
):
    """Wire the mocks needed for one multi-symphony run_autotuner invocation."""
    if vwap_side_effect is None:
        vwap_side_effect = _no_trigger_vwap_side_effect

    import database  # lazy import

    create_study_side_effect = _make_create_study_side_effect(
        best_params, broken_symphony=broken_symphony, broken_exc=broken_exc
    )
    history = _build_multi_symphony_history(sym_ids, n_days=n_days)

    with (
        patch("autotuner.optuna.create_study", side_effect=create_study_side_effect),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": fallback.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.DEFAULT_STRATEGY", database.DEFAULT_STRATEGY.copy()),
        patch("autotuner.math_engine.compute_vwap_breakdown_update", side_effect=vwap_side_effect),
    ):
        yield


def _run_autotuner_multi(names, *, broken_symphony=None, broken_exc=None, n_days=5):
    """Run run_autotuner() over N symphonies through the multi-symphony patch
    harness; suppress stdout noise. EARLY injection point (study.optimize)."""
    import inspect

    import autotuner  # lazy import

    ai = _ai_best_params()
    fallback = _fallback_params()
    bot_state = _build_multi_symphony_bot_state(names)
    sym_ids = list(bot_state.keys())
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    with _autotuner_patches_multi(
        ai,
        fallback,
        sym_ids=sym_ids,
        broken_symphony=broken_symphony,
        broken_exc=broken_exc,
        n_days=n_days,
    ):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(
                bot_state,
                "2026-05-10",
                [f"acc-{i}" for i in range(1, len(names) + 1)],
                **extra,
            )
    return result, buf.getvalue()


def _run_autotuner_multi_with_late_failure(names, *, broken_symphony, broken_exc, n_days=5):
    """LATE injection point: database.save_autotune_run raises for exactly
    ``broken_symphony`` (matched on its symphony_id= kwarg), calling through
    to the REAL implementation for every other symphony -- proving the
    isolation guard extends through the persistence call itself."""
    import inspect

    import autotuner  # lazy import
    import database as db_module  # lazy import

    ai = _ai_best_params()
    fallback = _fallback_params()
    bot_state = _build_multi_symphony_bot_state(names)
    sym_ids = list(bot_state.keys())
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    real_save_autotune_run = db_module.save_autotune_run

    def _selective_save_autotune_run(*args, **kwargs):
        if kwargs.get("symphony_id") == broken_symphony:
            raise broken_exc
        return real_save_autotune_run(*args, **kwargs)

    with (
        _autotuner_patches_multi(ai, fallback, sym_ids=sym_ids, n_days=n_days),
        patch("autotuner.database.save_autotune_run", side_effect=_selective_save_autotune_run),
    ):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(
                bot_state,
                "2026-05-10",
                [f"acc-{i}" for i in range(1, len(names) + 1)],
                **extra,
            )
    return result, buf.getvalue()


def _run_autotuner_multi_with_oc_failure(names, *, broken_symphony, broken_exc, n_days=5):
    """Injection point INSIDE Overfitting Conscience's ALREADY-isolated call
    (autotuner.py:3264-3277's own pre-existing try/except) -- proves BL-2's
    new outer guard does not double-count an advisor-producer outage that was
    already caught internally."""
    import inspect

    import autotuner  # lazy import

    ai = _ai_best_params()
    fallback = _fallback_params()
    bot_state = _build_multi_symphony_bot_state(names)
    sym_ids = list(bot_state.keys())
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    def _oc_side_effect(oc_run, *args, **kwargs):
        if isinstance(oc_run, dict) and oc_run.get("symphony_id") == broken_symphony:
            raise broken_exc
        return None

    with (
        _autotuner_patches_multi(ai, fallback, sym_ids=sym_ids, n_days=n_days),
        patch("autotuner._oc.run_overfitting_conscience", side_effect=_oc_side_effect),
    ):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(
                bot_state,
                "2026-05-10",
                [f"acc-{i}" for i in range(1, len(names) + 1)],
                **extra,
            )
    return result, buf.getvalue()


def _run_autotuner_multi_with_mid_window_failure(
    names, *, broken_symphony_sym_id, broken_exc, n_days=5
):
    """MID-WINDOW injection point (bl2review CONFIRMED finding): raises on
    the SECOND autotuner.run_simulation call for the target symphony's
    sym_id -- landing after optimization_results[normalized_name] = {}
    (autotuner.py:2952) and its eval_window_days stamp (:2960), but before
    the fallback_oos_alpha assignment (:2970-2976) completes. That is
    squarely inside the ~200-line window where a PARTIAL
    optimization_results entry exists (only eval_window_days, no delta keys,
    no _baseline_chosen) -- distinct from both the EARLY injection point
    (before the dict entry is created at all) and the LATE one (after it is
    fully populated).

    Per-target-sym_id call count: call #1 is the pre-window oos_alpha
    computation (autotuner.py:2938) -- allowed through to the REAL
    run_simulation so the per-symphony body actually reaches the window.
    Call #2 is fallback_oos_alpha, the FIRST run_simulation call INSIDE the
    window -- this is the one that raises. Sibling symphonies (a different
    sym_id on every call) always route to the real function, unaffected.
    """
    import inspect

    import autotuner  # lazy import

    ai = _ai_best_params()
    fallback = _fallback_params()
    bot_state = _build_multi_symphony_bot_state(names)
    sym_ids = list(bot_state.keys())
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    real_run_simulation = autotuner.run_simulation
    _call_counts: dict[str, int] = {}

    def _mid_window_run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
        if acc_sym_ids == [broken_symphony_sym_id]:
            _call_counts[broken_symphony_sym_id] = _call_counts.get(broken_symphony_sym_id, 0) + 1
            if _call_counts[broken_symphony_sym_id] == 2:
                raise broken_exc
        return real_run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

    with (
        _autotuner_patches_multi(ai, fallback, sym_ids=sym_ids, n_days=n_days),
        patch("autotuner.run_simulation", side_effect=_mid_window_run_simulation),
    ):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(
                bot_state,
                "2026-05-10",
                [f"acc-{i}" for i in range(1, len(names) + 1)],
                **extra,
            )
    return result, buf.getvalue()


# ===========================================================================
# AC-1: isolation -- no propagation.
# ===========================================================================


def test_one_symphony_uncaught_exception_is_isolated_not_propagated(isolated_db):
    """AC-1: an uncaught exception raised inside ONE symphony's per-symphony
    loop body (here: study.optimize() itself) must be caught and logged, and
    run_autotuner() must return normally -- the exception must never
    propagate out of run_autotuner() and abort the whole batch.

    MUST FAIL on current code: the per-symphony loop body has no surrounding
    try/except (autotuner.py:2558-3290), so an uncaught exception from
    study.optimize() propagates straight out of run_autotuner().
    """
    import database

    broken_name = database.normalize_name(_NAMES[1])

    # Must not raise.
    result, _stdout = _run_autotuner_multi(
        _NAMES,
        broken_symphony=broken_name,
        broken_exc=RuntimeError("boom - injected optimize failure"),
    )

    assert result is not None, (
        "run_autotuner() returned None -- AC-1 requires a normal (non-crash) "
        "return even when one symphony's processing raises."
    )


def test_sibling_symphonies_still_persist_rows_after_one_fails(isolated_db):
    """AC-1 continuation + AC-4 no-rollback, combined and order-independent
    (symphony_names = set() at autotuner.py:2547 has non-deterministic
    string-hash-based iteration order -- see module docstring design
    decision 1): after one symphony's exception is isolated, the OTHER two
    symphonies' autotune_runs rows must exist (continuation), and neither
    depends on WHERE in iteration order the failure occurred (no rollback of
    already-completed work).

    MUST FAIL on current code: the unguarded exception aborts run_autotuner()
    before completing the batch.
    """
    import database

    broken_name = database.normalize_name(_NAMES[1])
    healthy_names = {database.normalize_name(n) for n in _NAMES} - {broken_name}

    _run_autotuner_multi(_NAMES, broken_symphony=broken_name, broken_exc=RuntimeError("boom"))

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT symphony_id FROM autotune_runs")
    persisted_ids = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert persisted_ids == healthy_names, (
        f"Expected exactly the two healthy symphonies' rows ({healthy_names!r}) "
        f"to be persisted after the third's exception was isolated; got "
        f"{persisted_ids!r}. The failing symphony must have ZERO rows; both "
        f"healthy siblings must have their rows regardless of iteration order."
    )


def test_exception_isolation_logs_via_logging_error_with_exc_info_naming_symphony(
    isolated_db, caplog
):
    """AC-1: the isolated exception must be logged via
    logging.error(..., exc_info=True) -- never a bare print -- naming the
    failing symphony. INV-1 honesty boundary: this isolation guards the
    uncaught-exception class only; the log message must NOT claim
    SIGTERM/deploy-restart coverage (the real 07-24 incident was a SIGTERM,
    which a try/except cannot catch -- see module docstring design decision 3).
    """
    import database

    broken_name = database.normalize_name(_NAMES[1])
    caplog.set_level(logging.ERROR)

    _run_autotuner_multi(
        _NAMES, broken_symphony=broken_name, broken_exc=RuntimeError("boom - injected")
    )

    isolation_records = [
        r for r in caplog.records if r.levelno == logging.ERROR and broken_name in r.getMessage()
    ]
    assert isolation_records, (
        f"No ERROR-level log record naming the failing symphony ({broken_name!r}) "
        f"was found. AC-1 requires logging.error(...) naming the symphony and "
        f"the isolation boundary. Captured ERROR messages: "
        f"{[r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]}"
    )
    assert any(r.exc_info is not None for r in isolation_records), (
        "The isolation log record must carry exc_info (logging.error(..., "
        "exc_info=True)) per the plan's explicit style requirement -- never a "
        "bare print or an exc_info-less log call."
    )

    combined_text = " ".join(r.getMessage().lower() for r in isolation_records)
    assert "sigterm" not in combined_text, (
        "the isolation log message must not claim SIGTERM coverage -- INV-1 "
        "root-caused the real 07-24 incident to a deploy-restart SIGTERM, "
        "which a try/except cannot catch; this guard covers uncaught "
        "exceptions only."
    )
    assert "deploy-restart" not in combined_text and "deploy restart" not in combined_text, (
        "the isolation log message must not claim deploy-restart coverage -- "
        "see INV-1 honesty boundary in this file's module docstring."
    )


# ===========================================================================
# AC-2: attempted/completed batch-summary counts.
# ===========================================================================


def test_attempted_and_completed_counts_correct_with_one_failure(isolated_db):
    """AC-2: run_autotuner()'s return must carry
    optimization_results["_batch_summary"] == {"attempted": 3, "completed": 2}
    when exactly one of three symphonies' processing raises.

    MUST FAIL on current code: no _batch_summary key exists at all today.
    """
    import database

    broken_name = database.normalize_name(_NAMES[1])

    result, _stdout = _run_autotuner_multi(
        _NAMES, broken_symphony=broken_name, broken_exc=RuntimeError("boom")
    )

    assert isinstance(result, dict), f"run_autotuner() must return a dict; got {type(result)}"
    batch_summary = result.get("_batch_summary")
    assert batch_summary is not None, (
        "result['_batch_summary'] is missing -- AC-2 requires "
        "optimization_results['_batch_summary'] = {'attempted': N, 'completed': M} "
        "on every non-aborted return."
    )
    assert batch_summary == {"attempted": 3, "completed": 2}, (
        f"expected {{'attempted': 3, 'completed': 2}} for a 3-symphony batch "
        f"with exactly one isolated failure; got {batch_summary!r}"
    )


def test_attempted_and_completed_counts_correct_with_zero_failures(isolated_db):
    """AC-5 regression: a batch with zero exceptions has
    attempted == completed == len(symphony_names).
    """
    result, _stdout = _run_autotuner_multi(_NAMES)

    batch_summary = result.get("_batch_summary")
    assert batch_summary == {"attempted": 3, "completed": 3}, (
        f"expected {{'attempted': 3, 'completed': 3}} for a clean 3-symphony "
        f"batch; got {batch_summary!r}"
    )


def test_zero_exception_batch_db_rows_and_shape_unchanged_besides_batch_summary(isolated_db):
    """AC-5 fuller regression: with zero exceptions, every symphony's
    non-_batch_summary entry in optimization_results is present and every
    autotune_runs row is written exactly as before BL-2 -- the new counters
    are PURELY ADDITIVE. Mirrors test_autotune_runs_persistence.py Test 3's
    non-None-column assertions, extended to 3 symphonies.
    """
    import database

    expected_ids = {database.normalize_name(n) for n in _NAMES}

    result, _stdout = _run_autotuner_multi(_NAMES)

    assert "_batch_summary" in result, (
        "result['_batch_summary'] is missing -- this test's non-summary-key "
        "comparison below would vacuously pass without this explicit check "
        "(AC-2's new key must actually be present, not merely absent-and-"
        "therefore-excluded-from-nothing)."
    )
    non_summary_keys = {k for k in result.keys() if k != "_batch_summary"}
    assert non_summary_keys == expected_ids, (
        f"result's non-_batch_summary keys must be exactly the 3 normalized "
        f"symphony names; got {non_summary_keys!r}, expected {expected_ids!r}"
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symphony_id, oos_alpha, train_alpha, baseline_decision FROM autotune_runs"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 3, (
        f"Expected 3 autotune_runs rows for a clean 3-symphony batch; got {len(rows)}"
    )
    persisted_ids = {r[0] for r in rows}
    assert persisted_ids == expected_ids, (
        f"persisted symphony_ids must be exactly the 3 normalized names; got {persisted_ids!r}"
    )
    for _sid, oos_alpha, train_alpha, baseline_decision in rows:
        assert oos_alpha is not None, "oos_alpha must be persisted for every clean run"
        assert train_alpha is not None, "train_alpha must be persisted for every clean run"
        assert baseline_decision is not None, (
            "baseline_decision must be persisted for every clean run"
        )


# ===========================================================================
# Edge cases.
# ===========================================================================


def test_all_symphonies_failing_returns_zero_completed_without_crashing(isolated_db):
    """Edge case: ALL symphonies' processing raises. attempted == N,
    completed == 0, zero autotune_runs rows, and run_autotuner() must still
    return normally (no crash).

    The shared fake study ALWAYS raises regardless of study_name (a single
    optuna.create_study side_effect targeting every symphony at once),
    proving the isolation guard handles the N-of-N case too.
    """
    import inspect

    import autotuner  # lazy import
    import database

    def _always_raise(*args, **kwargs):
        raise RuntimeError("boom - injected for every symphony")

    ai = _ai_best_params()
    fallback = _fallback_params()
    bot_state = _build_multi_symphony_bot_state(_NAMES)
    sym_ids = list(bot_state.keys())
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    history = _build_multi_symphony_history(sym_ids, n_days=5)
    with (
        patch("autotuner.optuna.create_study", side_effect=_always_raise),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": fallback.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.DEFAULT_STRATEGY", database.DEFAULT_STRATEGY.copy()),
        patch(
            "autotuner.math_engine.compute_vwap_breakdown_update",
            side_effect=_no_trigger_vwap_side_effect,
        ),
    ):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(
                bot_state, "2026-05-10", ["acc-1", "acc-2", "acc-3"], **extra
            )

    assert result is not None, "run_autotuner() must not return None/crash when ALL symphonies fail"
    batch_summary = result.get("_batch_summary")
    assert batch_summary == {"attempted": 3, "completed": 0}, (
        f"expected {{'attempted': 3, 'completed': 0}} when every symphony's "
        f"processing raises; got {batch_summary!r}"
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM autotune_runs")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 0, f"Expected zero autotune_runs rows when every symphony fails; got {count}"


def test_late_stage_exception_in_save_autotune_run_leaves_no_partial_row(isolated_db):
    """Edge case named in the plan: an exception raised INSIDE
    database.save_autotune_run itself (autotuner.py:3218-3239, near the very
    end of the per-symphony body) must ALSO be caught by AC-1's outer guard
    -- proving the guard wraps the WHOLE per-symphony body, not just the
    early Optuna optimize() call. No partial/corrupt row is left behind for
    the failing symphony (the injected exception fires before the real
    save_autotune_run call is even attempted -- a strict subcase of "the
    INSERT either fully succeeds before the exception or doesn't run at
    all"); sibling symphonies (whose save_autotune_run calls succeed for
    real) still persist their rows.
    """
    import database

    broken_name = database.normalize_name(_NAMES[1])
    healthy_names = {database.normalize_name(n) for n in _NAMES} - {broken_name}

    result, _stdout = _run_autotuner_multi_with_late_failure(
        _NAMES,
        broken_symphony=broken_name,
        broken_exc=RuntimeError("boom - injected save failure"),
    )

    assert result is not None, (
        "run_autotuner() must not crash on a late-stage save_autotune_run failure"
    )
    batch_summary = result.get("_batch_summary")
    assert batch_summary == {"attempted": 3, "completed": 2}, (
        f"expected {{'attempted': 3, 'completed': 2}}; got {batch_summary!r}"
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT symphony_id FROM autotune_runs")
    persisted_ids = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert broken_name not in persisted_ids, (
        f"the symphony whose save_autotune_run call raised ({broken_name!r}) "
        f"must have NO row in autotune_runs -- no partial/corrupt row."
    )
    assert persisted_ids == healthy_names, (
        f"the two healthy siblings must still have their rows persisted; "
        f"got {persisted_ids!r}, expected {healthy_names!r}"
    )


def test_oc_de_internal_failure_still_counts_symphony_as_completed(isolated_db, caplog):
    """Adversarial pin: Overfitting Conscience's OWN existing try/except
    (autotuner.py:3264-3277) already catches and logs an OC producer
    exception BEFORE it could ever reach AC-1's new outer guard. This must
    NOT be double-counted as a batch-level isolation failure -- the symphony
    still completes (its optimization work + save_autotune_run succeeded;
    only the advisory OC observation failed, a pre-existing, unrelated
    outage class). attempted == completed == N, all 3 autotune_runs rows
    persist, and the existing "Overfitting Conscience observation
    PERSISTENCE FAILED" log line still fires unchanged.
    """
    import database

    broken_name = database.normalize_name(_NAMES[1])
    caplog.set_level(logging.ERROR)

    result, _stdout = _run_autotuner_multi_with_oc_failure(
        _NAMES,
        broken_symphony=broken_name,
        broken_exc=RuntimeError("boom - injected OC failure"),
    )

    batch_summary = result.get("_batch_summary")
    assert batch_summary == {"attempted": 3, "completed": 3}, (
        f"an OC-internal failure (already caught by OC's own pre-existing "
        f"try/except) must NOT be counted as a batch-level isolation failure -- "
        f"expected {{'attempted': 3, 'completed': 3}}; got {batch_summary!r}"
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT symphony_id FROM autotune_runs")
    persisted_ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    expected_ids = {database.normalize_name(n) for n in _NAMES}
    assert persisted_ids == expected_ids, (
        f"all 3 symphonies' autotune_runs rows must still persist despite the "
        f"OC-internal failure; got {persisted_ids!r}"
    )

    oc_failure_logged = any(
        "Overfitting Conscience observation PERSISTENCE FAILED" in r.getMessage()
        for r in caplog.records
    )
    assert oc_failure_logged, (
        "the pre-existing OC PERSISTENCE FAILED log line must still fire "
        "unchanged -- BL-2's new outer guard must not swallow or alter the "
        "existing OC isolation behavior."
    )


# ===========================================================================
# bl2review CONFIRMED finding: MID-WINDOW failure must leave NO stale entry.
# ===========================================================================


def test_mid_window_exception_leaves_no_stale_partial_entry(isolated_db):
    """Adversarial pin (bl2review CONFIRMED finding): an uncaught exception
    raised INSIDE the ~200-line window between
    optimization_results[normalized_name] = {} (autotuner.py:2952) and the
    real delta keys / "_baseline_chosen" landing (autotuner.py:3157-3162) is
    correctly caught by AC-1's outer guard -- but the STALE PARTIAL entry
    (e.g. only "eval_window_days", no delta keys, no "_baseline_chosen")
    remains in optimization_results. reporting.py's per-symphony shape guard
    then skips that non-{old,new} entry and renders "Optimal parameters
    retained." for that symphony -- an ACTIVE FALSE SUCCESS for a symphony
    whose optimization actually failed.

    This file's EARLY (study.optimize) and LATE (database.save_autotune_run)
    injection points bracket this window from OUTSIDE but never hit inside
    it -- this test is the missing MID-WINDOW case.

    Required property: the failed symphony must have NO key at all in the
    returned optimization_results -- matching the EARLY-failure case's
    correct behavior (an exception raised before
    optimization_results[normalized_name] is even created never leaves an
    entry). This also structurally prevents the fake-success embed
    downstream in reporting.py, since its per-symphony loop only ever
    iterates keys actually present in the dict.

    MUST FAIL on current HEAD (825b9878): the outer except catches the
    exception and continues, but nothing removes the stale
    {"eval_window_days": {...}} entry already written at :2952/:2960.

    Intended GREEN (per team-lead's ruling, NOT implemented by this test):
    optimization_results.pop(normalized_name, None) inside the except block.
    """
    import database

    broken_name = _NAMES[1]
    broken_normalized = database.normalize_name(broken_name)
    # sym_id convention from _build_multi_symphony_bot_state: "sym-<i>",
    # 1-indexed -- _NAMES[1] ("Symphony Beta") is the 2nd entry -> "sym-2".
    broken_sym_id = "sym-2"

    result, _stdout = _run_autotuner_multi_with_mid_window_failure(
        _NAMES,
        broken_symphony_sym_id=broken_sym_id,
        broken_exc=RuntimeError("boom - injected mid-window failure"),
    )

    assert result is not None, (
        "run_autotuner() must not crash on a mid-window failure -- AC-1's "
        "outer guard must catch it, same as the early/late injection points."
    )
    assert broken_normalized not in result, (
        f"the failed symphony ({broken_normalized!r}) must have NO key at "
        f"all in optimization_results -- a stale partial entry "
        f"({result.get(broken_normalized)!r}) would render as a FALSE "
        f"SUCCESS downstream in reporting.py (the shape guard skips the "
        f"non-{{old,new}} eval_window_days entry, leaving 'Optimal "
        f"parameters retained.' for a symphony that actually failed)."
    )

    # Companion sanity (cheap, same test): the two healthy siblings must be
    # genuinely, fully populated (real _baseline_chosen present) -- proving
    # the eventual fix doesn't accidentally strip healthy entries too.
    healthy_normalized = {database.normalize_name(n) for n in _NAMES} - {broken_normalized}
    for sym_key in healthy_normalized:
        assert sym_key in result, f"healthy sibling {sym_key!r} must still be present in result"
        assert "_baseline_chosen" in result[sym_key], (
            f"healthy sibling {sym_key!r} must be FULLY populated (carry "
            f"'_baseline_chosen'), not left as a partial eval_window_days "
            f"stub; got keys: {list(result[sym_key].keys())!r}"
        )
