"""RED tests -- BL-1: persist the Phase-3 PBO veto value (autotuner.py:3218-3239).

Feature plan: feature-plans/audit-bl1-persist-pbo-veto.md
Source: docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4 Finding T1, §6 Backlog BL-1

The ``database.save_autotune_run(...)`` call inside ``run_autotuner()``'s
per-symphony loop omits ``pbo=`` even though ``_pbo_value`` is computed
(autotuner.py:2854-2874) and consumed by the acceptance gate's veto decision
(autotuner.py:3060-3076, ``_pbo_veto_fired`` at :3084-3100). This is a
persistence bug, not a computation bug -- ``_pbo_value`` is correctly computed
and correctly used for the live veto decision; it simply never reaches
``autotune_runs.pbo``.

``tests/autotuner/test_pbo_migration_028.py``'s ``TestSaveAutotuneRunPboRoundTrip``
already proves the *accessor* (``database.save_autotune_run``) round-trips ``pbo``
correctly when called directly -- it never calls ``run_autotuner()`` itself, so the
integration gap at the real call site slipped through 40 consecutive live runs
undetected (40/40 NULL rows on the droplet). This file closes that exact gap: it
drives ``run_autotuner()`` itself (not the accessor) and asserts the persisted
``autotune_runs.pbo`` value on that real call site.

Fixture/harness pattern reused (house lesson
``feedback_consumer_suite_discovery_before_sufficiency`` -- reuse before
hand-rolling): ``tests/autotuner/test_autotune_runs_persistence.py``'s isolated
tmp-DB + ``_autotuner_patches`` mock-study harness, and the
``type("FakeTrial", (), {...})()`` stub idiom from
``tests/autotuner/test_haircut_select_crra_default_gamma.py`` for building trials
the haircut/PBO code paths can read ``.value``/``.params``/``.user_attrs`` from
without running a real (500-trial) Optuna study.

Every test in this file MUST FAIL until autotuner.py:3218-3239 gains
``pbo=_pbo_value``.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: isolated, fresh SQLite DB per test.
# Mirrors tests/autotuner/test_autotune_runs_persistence.py's isolated_db fixture.
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
# Minimal fixture helpers -- duplicated (not imported) from
# test_autotune_runs_persistence.py, matching this test suite's established
# per-file self-containment convention (test_o6_frozen_eval.py likewise
# carries its own separate _autotuner_patches rather than importing one).
# ---------------------------------------------------------------------------


def _build_bot_state(symphony_name: str = "Test Symphony A") -> dict:
    """Single-symphony bot_state -- minimum needed for run_autotuner to iterate."""
    return {
        "sym-1": {
            "name": symphony_name,
            "account_uuid": "acc-1",
        }
    }


def _build_history(n_days: int = 5) -> dict:
    """Minimal N-day synthetic history.

    NOTE on PBO date-partition degeneracy: PURGE_DAYS=20 dominates any small
    n_days fixture (effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS
    - EMBARGO_DAYS)), so train_dates -- and therefore
    _cpcv_eligible_dates -- is EMPTY for this 5-day fixture. compute_pbo's own
    contract (math_engine.py) returns the early-exit 0.0 when
    ``n_dates == 0`` -- still a genuine, non-fabricated float from the real
    function (K>=2 configs is what gates whether _pbo_value is computed at
    all; the DATE overlap only affects which branch of compute_pbo runs, not
    whether it returns a non-None value). This is intentional: this file
    tests PERSISTENCE fidelity, not compute_pbo's CSCV math (already covered
    by tests/math_engine/test_compute_pbo.py) -- a larger, slower fixture
    that reaches non-degenerate CSCV partitioning is unnecessary here.
    """
    tick = {
        "return": 2.0,
        "mc_prob": 50.0,
        "vol": 1.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, n_days + 1)]
    return {"sym-1": {d: [tick] for d in dates}}


def _fallback_params() -> dict:
    """Fallback params derived from the real DEFAULT_STRATEGY -- no hardcoded values."""
    import database  # lazy import

    return database.DEFAULT_STRATEGY.copy()


def _ai_best_params() -> dict:
    """Valid Optuna best_params -- includes all 7 OPTUNA_SEARCH_SPACE_KEYS so
    schema validation in run_autotuner passes.
    """
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
    "crra_eu" inside run_autotuner (autotuner.py:2422-2430), which is why the
    fake trials below carry raw-percent daily_returns for compute_crra_eu_tstat
    rather than a Sortino-shaped series.
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


def _make_fake_trial(value, params, daily_returns, cscv_date_returns, number=0):
    """Build a minimal Optuna-trial-shaped stub.

    Reuses the ``type("FakeTrial", (), {...})()`` idiom from
    tests/autotuner/test_haircut_select_crra_default_gamma.py:160-180 for the
    same _haircut_select codepath. Traced every downstream read of a trial
    object across _haircut_select, filter_sortino_sentinels,
    _trial_has_sentinel_path, compute_crra_eu_tstat, and the PBO block at
    autotuner.py:2854-2874: only ``.value``, ``.params``, and ``.user_attrs``
    are ever touched -- no ``.number`` or other FrozenTrial field is required
    by any of those paths. ``.number`` is still carried for parity with the
    existing stub idiom (defensive, not load-bearing).
    """
    return type(
        "FakeTrial",
        (),
        {
            "value": value,
            "params": dict(params),
            "user_attrs": {
                "daily_returns": list(daily_returns),
                "cscv_date_returns": dict(cscv_date_returns),
            },
            "number": number,
        },
    )()


@contextlib.contextmanager
def _autotuner_patches(
    best_params: dict, fallback: dict, study_trials: list, vwap_side_effect=None
):
    """Wire the mocks needed for one run_autotuner invocation.

    ``study_trials`` is REQUIRED (not defaulted) and assigned directly to
    ``fake_study.trials`` -- explicit, not relying on an unset-MagicMock
    attribute's incidental TypeError-on-iteration side effect (which the
    autotuner's ``try/except TypeError`` guard happens to swallow into
    ``completed_trials = []``). Being explicit here means each test's intent
    (2 real trials vs. zero trials) is visible at the call site, not implied
    by mock plumbing.

    Like test_autotune_runs_persistence.py, we do NOT mock
    ``database.save_symphony_strategy`` or ``database.save_autotune_run`` --
    both are called real, against the tmp_path SQLite redirected by the
    isolated_db fixture, exercising the full DB write path this cycle fixes.
    """
    if vwap_side_effect is None:
        vwap_side_effect = _no_trigger_vwap_side_effect

    import database  # lazy import

    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)
    fake_study.trials = study_trials

    history = _build_history(n_days=5)

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
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
        yield {"fake_study": fake_study}


def _run_autotuner_via_patches(best_params, fallback, study_trials, vwap_side_effect=None):
    """Run run_autotuner() through the patch harness; suppress stdout noise.

    Additionally spies on ``acceptance_gate.evaluate_acceptance_gate`` (wraps=
    the real function, so gate behavior is genuinely unaffected -- not a stub)
    to capture the exact ``pbo=`` kwarg the gate consumed for this run -- the
    AC-2 "matches what the gate consumed" comparison target.

    autotuner.py imports this module as ``import acceptance_gate as
    _acceptance_gate``, so patching the ``acceptance_gate`` module's own
    ``evaluate_acceptance_gate`` attribute is observed identically by
    autotuner's ``_acceptance_gate.evaluate_acceptance_gate(...)`` call (same
    module object; Python resolves the attribute dynamically at call time).

    Verified (team-lead hardening note 1) that the real call site
    (autotuner.py:3060-3076) passes ``pbo=_pbo_value`` as a KEYWORD argument
    alongside 10 other keyword args -- there is no positional form of this
    call anywhere in the codebase -- so ``gate_spy.call_args.kwargs["pbo"]``
    is a safe, non-KeyError-risking read.
    """
    import inspect

    import acceptance_gate  # lazy -- see module docstring import-collision precedent
    import autotuner  # lazy import

    bot_state = _build_bot_state()
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    with (
        _autotuner_patches(best_params, fallback, study_trials, vwap_side_effect=vwap_side_effect),
        patch.object(
            acceptance_gate,
            "evaluate_acceptance_gate",
            wraps=acceptance_gate.evaluate_acceptance_gate,
        ) as gate_spy,
    ):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"], **extra)
    return result, buf.getvalue(), gate_spy


# ===========================================================================
# Test 1 -- AC-1 / AC-2: a genuine non-None _pbo_value must reach
# autotune_runs.pbo, and the persisted value must equal the value the
# acceptance gate itself consumed for the veto decision.
# ===========================================================================


def test_run_autotuner_persists_pbo_matching_gate_consumed_value(isolated_db):
    """A real (>=2 CSCV-eligible configs) PBO computation must be persisted to
    autotune_runs.pbo, and the persisted value must be the SAME value the
    acceptance gate consumed for its veto decision -- not a re-derived or
    coincidental value. This is the exact integration gap
    test_pbo_migration_028.py structurally cannot close (it never calls
    run_autotuner()).
    """
    ai = _ai_best_params()
    fallback = _fallback_params()

    # Two trials, both carrying cscv_date_returns -- satisfies autotuner.py's
    # `len(_top_k_configs) >= 2` PBO-eligibility gate (autotuner.py:2869).
    # Both trials also pass filter_sortino_sentinels (value well below the
    # sentinel threshold, no split_scores user_attr so no sentinel-path
    # detection fires) so haircut_trials is non-empty, which the PBO block
    # requires as its own outer gate (autotuner.py:2855 `if haircut_trials:`).
    trial_a = _make_fake_trial(
        value=0.4,
        params=ai,
        daily_returns=[1.0, -0.5, 2.0, 0.3],
        cscv_date_returns={"2026-04-01": 0.01, "2026-04-02": -0.005},
        number=0,
    )
    trial_b = _make_fake_trial(
        value=0.6,
        params=ai,
        daily_returns=[0.5, 1.5, -1.0, 0.8],
        cscv_date_returns={"2026-04-01": 0.015, "2026-04-02": 0.02},
        number=1,
    )

    _result, _stdout, gate_spy = _run_autotuner_via_patches(ai, fallback, [trial_a, trial_b])

    # --- Precondition: the gate must have genuinely consumed a computed PBO. ---
    # Guards against fixture vacuity (team-lead hardening note 2): if a future
    # change to this fixture collapsed haircut_trials back to empty (e.g. a
    # .value crossing the sentinel threshold, or losing a cscv_date_returns
    # entry), _pbo_value would silently stay None and any comparison below
    # would risk trivially passing on None == None. Fail loudly here instead,
    # before trusting the persistence assertions that follow.
    assert gate_spy.call_args is not None, (
        "acceptance_gate.evaluate_acceptance_gate was never called -- "
        "run_autotuner did not reach the PHASE-3 gate for this symphony."
    )
    gate_consumed_pbo = gate_spy.call_args.kwargs["pbo"]
    assert gate_consumed_pbo is not None, (
        "The acceptance gate must have consumed a genuinely-computed PBO value "
        "for this fixture (2 trials, both carrying cscv_date_returns, satisfies "
        "autotuner.py's `len(_top_k_configs) >= 2` PBO-eligibility gate). Got "
        "pbo=None -- the fixture no longer reaches the PBO-computed branch; "
        "extend the fake trials' cscv_date_returns / adjust .value until this "
        "assertion passes again before trusting the rest of this test."
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT pbo FROM autotune_runs")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1, (
        f"Expected exactly 1 row in autotune_runs after a single-symphony "
        f"run_autotuner call; found {len(rows)}."
    )
    persisted_pbo = rows[0][0]

    # AC-1 core assertion: pbo must actually reach the DB.
    assert persisted_pbo is not None, (
        "autotune_runs.pbo is NULL even though the acceptance gate consumed a "
        f"real pbo={gate_consumed_pbo!r} for this run -- the persistence call "
        "site (autotuner.py:3218-3239) is still dropping pbo= entirely."
    )
    assert isinstance(persisted_pbo, float), (
        f"pbo must persist as a float (matches the pbo REAL column type); got {type(persisted_pbo)}"
    )
    # Shape/bounds assertion from compute_pbo's own documented contract
    # (math_engine.py: "float in [0.0, 1.0]") -- not a hardcoded producer value.
    assert 0.0 <= persisted_pbo <= 1.0, (
        f"persisted pbo={persisted_pbo} is outside compute_pbo's documented [0.0, 1.0] range."
    )
    # AC-2 core assertion -- identity/passthrough check: the persisted value
    # and the gate-consumed value must be the SAME float from the SAME run
    # (autotuner.py holds one _pbo_value local reused at both the gate call
    # and the save call). The abs=1e-12 tolerance is defensive against a
    # SQLite REAL round-trip (IEEE-754 double storage) only -- this is an
    # identity check on a passthrough value, not a re-derivation, so no
    # producer-math tolerance is being smuggled in here.
    assert persisted_pbo == pytest.approx(gate_consumed_pbo, abs=1e-12), (
        f"autotune_runs.pbo ({persisted_pbo}) must equal the value the "
        f"acceptance gate consumed for its veto decision ({gate_consumed_pbo}) "
        "-- the persisted column must reflect the exact same-run PBO "
        "computation the gate already used, never a re-derived value."
    )


# ===========================================================================
# Test 2 -- AC-3: pbo=None must still persist as SQL NULL when PBO was never
# computed (negative-case pin -- AC-1's fix must not regress into "pbo always
# fabricated as some non-null value").
# ===========================================================================


def test_run_autotuner_pbo_none_persists_as_null_when_not_computed(isolated_db):
    """When haircut_trials is empty (zero CSCV-eligible configs), _pbo_value
    stays its initialized None (autotuner.py:2854) and the persisted
    autotune_runs.pbo column must be SQL NULL -- not a fabricated 0.0 or any
    other non-null value.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()

    # Explicit empty trial list -- never relying on an unset-MagicMock
    # attribute's incidental TypeError-on-iteration side effect. Explicit is
    # more robust and self-documenting than an incidental mock behavior.
    _result, _stdout, gate_spy = _run_autotuner_via_patches(ai, fallback, [])

    assert gate_spy.call_args is not None, (
        "acceptance_gate.evaluate_acceptance_gate was never called -- "
        "run_autotuner did not reach the PHASE-3 gate for this symphony."
    )
    assert gate_spy.call_args.kwargs["pbo"] is None, (
        "Precondition check: with zero trials, the gate itself must have been "
        "called with pbo=None (no CSCV-eligible configs) -- if this fails, the "
        "fixture no longer represents the 'PBO not computed' case this test is "
        "pinning."
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT pbo FROM autotune_runs")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected exactly 1 row in autotune_runs; found {len(rows)}."
    assert rows[0][0] is None, (
        f"autotune_runs.pbo must persist as SQL NULL when PBO was never computed "
        f"(haircut_trials empty); got {rows[0][0]!r}."
    )


# ===========================================================================
# Test 3 -- source-scan pin (belt-and-suspenders alongside Tests 1-2, matching
# this test suite's house style of pairing behavioral + source-scan pins --
# see test_pbo_acceptance_gate_veto.py).
# ===========================================================================


def test_autotuner_save_autotune_run_call_site_passes_pbo_kwarg():
    """The specific database.save_autotune_run(...) call inside
    run_autotuner's per-symphony loop (autotuner.py:3218-3239) must pass a
    literal ``pbo=_pbo_value`` kwarg -- the SAME variable the gate already
    consumes (autotuner.py:3075), never a re-derived value or a hardcoded
    literal.

    Bounded to the specific call block (anchored on the
    ``_inserted_id = database.save_autotune_run(`` line, per the AC-1
    architecture note in the feature plan) rather than a whole-file "pbo"
    substring search, so this test cannot be satisfied by an unrelated pbo
    reference elsewhere in the file (e.g. the PBO computation block itself,
    or the calibration-sweep code path at :3502+, which has its own separate
    haircut_trials block with no save_autotune_run call of its own).
    """
    src = (pathlib.Path(__file__).resolve().parents[2] / "autotuner.py").read_text(encoding="utf-8")

    anchor = "_inserted_id = database.save_autotune_run("
    start = src.find(anchor)
    assert start != -1, (
        "Could not locate the `_inserted_id = database.save_autotune_run(` call "
        "site in autotuner.py -- has it been renamed/restructured? Update this "
        "test's anchor to match."
    )
    # The call spans multiple lines with one named kwarg per line, all at the
    # same 12-space indent, closed by a line that is exactly 8 spaces + ")"
    # (matching the 8-space indent of the `_inserted_id = ...` assignment
    # itself, one level in from the call's own kwargs). No kwarg value in
    # this call is itself a multi-line/parenthesized expression, so the
    # FIRST "\n        )" after `start` is guaranteed to be this call's own
    # closing paren, not a premature match.
    close_marker = "\n        )"
    end = src.find(close_marker, start)
    assert end != -1, "Could not locate the closing paren of the save_autotune_run call."
    call_block = src[start : end + len(close_marker)]

    assert "pbo=_pbo_value" in call_block, (
        "The database.save_autotune_run(...) call at autotuner.py:3218-3239 must "
        "pass pbo=_pbo_value (the SAME variable already consumed by the "
        "acceptance gate's veto decision) -- got call block:\n" + call_block
    )
