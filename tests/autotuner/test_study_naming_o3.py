"""
O3 RED tests — study-name convention + archive migration.

AC-O3.1: autotuner.py uses study_name=f"{timestamp}__{normalized_name}"
         with load_if_exists=False — fresh study per run.
AC-O3.3: A new run produces a study with the timestamp prefix and does NOT
         find/extend a prior study with the same symphony name.

Migration tests (AC-O3.2):
  - optuna_001_archive_accumulated_studies.sql exists and targets optuna_studies.db
  - Migration is rename-only (LEGACY__ prefix) — no deletes
  - Migration is idempotent (running twice does not double-prefix)
  - Migration does not touch rows already in LEGACY__ or timestamp__ format

All tests are RED against current code (autotuner.py:329 uses normalized_name +
load_if_exists=True). They turn GREEN after the O3 fix.

Fixtures used:
  tests/fixtures/autotuner/study_naming/naming_pairs.json
  tests/fixtures/optuna_studies/legacy_studies_before_archive.sqlite
  tests/fixtures/optuna_studies/legacy_studies_after_archive.sqlite
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import shutil
import sqlite3
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
NAMING_PAIRS_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "autotuner" / "study_naming" / "naming_pairs.json"
)
BEFORE_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "optuna_studies" / "legacy_studies_before_archive.sqlite"
)
AFTER_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "optuna_studies" / "legacy_studies_after_archive.sqlite"
)
MIGRATION_FILE = REPO_ROOT / "migrations" / "optuna_001_archive_accumulated_studies.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_naming_pairs():
    with open(NAMING_PAIRS_FIXTURE, encoding="utf-8") as f:
        return json.load(f)["pairs"]


def _build_bot_state(symphony_name: str = "My Symphony") -> dict:
    return {"sym-1": {"name": symphony_name, "account_uuid": "acc-1"}}


def _build_history(n_days: int = 5) -> dict:
    tick = {
        "return": 1.0, "mc_prob": 50.0, "vol": 1.5,
        "vwap_diff": 0.0, "base_atr_pct": 1.0, "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, n_days + 1)]
    return {"sym-1": {d: [tick] for d in dates}}


@contextlib.contextmanager
def _autotuner_patches(captured_study_names: list, captured_load_if_exists: list):
    """
    Patch autotuner dependencies. Records the study_name and load_if_exists
    args passed to optuna.create_study for O3 assertions.
    """
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)

    import database

    def fake_create_study(study_name, storage, load_if_exists, direction):
        captured_study_names.append(study_name)
        captured_load_if_exists.append(load_if_exists)
        return fake_study

    with patch("autotuner.optuna.create_study", side_effect=fake_create_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value=_build_history()), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.get_symphony_strategy",
               return_value={"params": database.DEFAULT_STRATEGY.copy(), "locked_vars": []}), \
         patch("autotuner.database.save_symphony_strategy"), \
         patch("autotuner.database.save_autotune_run", return_value=None), \
         patch("autotuner.database.DEFAULT_STRATEGY", database.DEFAULT_STRATEGY.copy()), \
         patch("autotuner.math_engine.compute_vwap_breakdown_update",
               return_value=(0, 0, False, False)):
        yield


def _run_autotuner(symphony_name: str, captured_names: list, captured_flags: list):
    import autotuner
    import inspect
    from tests.autotuner.conftest import make_phase1_theory_bundle
    bot_state = _build_bot_state(symphony_name)
    buf = io.StringIO()
    spec_bundle_id = make_phase1_theory_bundle()
    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}
    with _autotuner_patches(captured_names, captured_flags):
        with contextlib.redirect_stdout(buf):
            autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"], **extra)


# ===========================================================================
# Test 1 — study_name has timestamp__ prefix (AC-O3.1)
# ===========================================================================

def test_study_name_has_timestamp_prefix():
    """
    optuna.create_study must be called with study_name matching
    the pattern ^<TIMESTAMP>__<NORMALIZED_NAME> — a compact ISO timestamp followed
    by __ and the normalized symphony name.

    RED: current code passes study_name=normalized_name with no timestamp.
    """
    names, _ = [], []
    _run_autotuner("My Symphony", names, _)

    assert names, "optuna.create_study was never called — run_autotuner did not execute"
    study_name = names[0]

    # Timestamp prefix: YYYYMMDDTHHMMSSffffffZ (compact UTC with optional microseconds)
    TIMESTAMP_PREFIX_RE = re.compile(r"^\d{4}\d{2}\d{2}T\d{6}")
    assert TIMESTAMP_PREFIX_RE.match(study_name), (
        f"study_name must start with a timestamp prefix (e.g. '2026-05-15T160000Z'); "
        f"got {study_name!r}. Fix: autotuner.py create_study call must use "
        f"study_name=f\"{{timestamp}}__{'{normalized_name}'}\"."
    )

    # Must contain the __ separator
    assert "__" in study_name, (
        f"study_name must contain '__' separator between timestamp and symphony name; "
        f"got {study_name!r}."
    )


# ===========================================================================
# Test 2 — load_if_exists=False (AC-O3.1)
# ===========================================================================

def test_create_study_load_if_exists_false():
    """
    optuna.create_study must be called with load_if_exists=False so that
    each run creates a fresh study rather than resuming an accumulated one.

    RED: current code passes load_if_exists=True.
    """
    _, flags = [], []
    _run_autotuner("My Symphony", _, flags)

    assert flags, "optuna.create_study was never called"
    assert flags[0] is False, (
        f"create_study must be called with load_if_exists=False; "
        f"got load_if_exists={flags[0]!r}. "
        "Studies must NOT be resumed across daily runs."
    )


# ===========================================================================
# Test 3 — study_name embeds the normalized symphony name (AC-O3.1)
# ===========================================================================

def test_study_name_embeds_normalized_symphony_name():
    """
    The portion after __ in study_name must equal the normalized symphony name
    produced by database.normalize_name(symphony_name).

    Verified against all pairs in naming_pairs.json fixture.
    RED: current code uses just normalized_name (no timestamp prefix at all).
    """
    import database

    pairs = _load_naming_pairs()
    for pair in pairs:
        normalized_name = pair["normalized_name"]
        symphony_name = normalized_name.replace("_", " ").title()

        names, _ = [], []
        _run_autotuner(symphony_name, names, _)

        assert names, f"create_study not called for symphony {symphony_name!r}"
        study_name = names[0]

        assert "__" in study_name, (
            f"No __ separator in study_name {study_name!r} for symphony {symphony_name!r}"
        )
        suffix = study_name.split("__", 1)[1]
        expected = database.normalize_name(symphony_name)
        assert suffix == expected, (
            f"study_name suffix must equal normalize_name({symphony_name!r})={expected!r}; "
            f"got suffix {suffix!r} in full name {study_name!r}."
        )


# ===========================================================================
# Test 4 — two runs produce different study names (AC-O3.3)
# ===========================================================================

def test_two_runs_produce_different_study_names():
    """
    Two separate run_autotuner invocations must produce study names that differ
    in their timestamp portion, so studies are never resumed across runs.

    This test verifies the non-reuse contract from AC-O3.3.
    RED: current code uses a fixed normalized_name, so both runs hit the same
    study_name (and load_if_exists=True would resume it).
    """
    import time as _time

    names_run1, _ = [], []
    _run_autotuner("My Symphony", names_run1, _)

    _time.sleep(0.01)  # ensure clock advances between invocations

    names_run2, _ = [], []
    _run_autotuner("My Symphony", names_run2, _)

    assert names_run1 and names_run2, "create_study not called in one or both runs"

    # The full study names should differ (timestamps differ)
    assert names_run1[0] != names_run2[0], (
        f"Two separate run_autotuner calls produced the same study_name "
        f"{names_run1[0]!r}. Each run must create a fresh study with a "
        "unique timestamp prefix."
    )

    # Both must have the __ separator
    for name in (names_run1[0], names_run2[0]):
        assert "__" in name, f"study_name {name!r} missing __ separator"

    # The normalized suffix (after __) should be identical — same symphony
    suffix1 = names_run1[0].split("__", 1)[1]
    suffix2 = names_run2[0].split("__", 1)[1]
    assert suffix1 == suffix2, (
        f"Symphony suffix changed between runs: {suffix1!r} vs {suffix2!r}. "
        "Only the timestamp prefix should vary."
    )


# ===========================================================================
# Test 5 — migration file exists and targets optuna_studies.db (AC-O3.2)
# ===========================================================================

def test_migration_file_exists_and_documents_correct_target_db():
    """
    migrations/optuna_001_archive_accumulated_studies.sql must exist and its
    header comment must mention optuna_studies.db (not alphabot_state.db).

    RED if the file does not exist.
    """
    assert MIGRATION_FILE.exists(), (
        f"Migration file not found at {MIGRATION_FILE}. "
        "Create migrations/optuna_001_archive_accumulated_studies.sql."
    )
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    assert sql.strip(), "Migration file must not be empty."

    # Must mention the correct DB name in comments
    assert "optuna_studies.db" in sql, (
        "Migration file must explicitly document that it targets optuna_studies.db, "
        "not alphabot_state.db. Add a comment: '-- Target DB: optuna_studies.db'."
    )
    assert "alphabot_state.db" not in sql.lower().replace("not alphabot_state.db", ""), (
        "Migration file must NOT reference alphabot_state.db as a target. "
        "This migration operates on Optuna's internal DB only."
    )


# ===========================================================================
# Test 6 — migration is rename-only (AC-O3.2 non-destructive)
# ===========================================================================

def test_archive_migration_is_rename_only(tmp_path):
    """
    Applying optuna_001_archive_accumulated_studies.sql to a copy of the
    before-archive fixture must produce study names matching the after-archive
    fixture — LEGACY__ prefix added, row count unchanged, no deletes.

    RED if migration SQL deletes rows or produces wrong names.
    """
    working_db = tmp_path / "optuna_studies.db"
    shutil.copy(BEFORE_FIXTURE, working_db)

    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(working_db))
    conn.executescript(sql)
    conn.commit()

    rows_after = sorted(r[0] for r in conn.execute("SELECT study_name FROM studies").fetchall())
    conn.close()

    # Load expected names from after-archive fixture
    conn_expected = sqlite3.connect(str(AFTER_FIXTURE))
    expected = sorted(
        r[0] for r in conn_expected.execute("SELECT study_name FROM studies").fetchall()
    )
    conn_expected.close()

    assert rows_after == expected, (
        f"After migration, study names must be {expected}; got {rows_after}. "
        "Migration must rename each bare study with LEGACY__ prefix, no deletions."
    )


# ===========================================================================
# Test 7 — migration is idempotent (running twice does not double-prefix)
# ===========================================================================

def test_archive_migration_is_idempotent(tmp_path):
    """
    Running optuna_001_archive_accumulated_studies.sql twice on the same DB
    must not produce LEGACY__LEGACY__ double-prefix on any row.

    RED if migration SQL lacks the NOT LIKE 'LEGACY__%' guard.
    """
    working_db = tmp_path / "optuna_studies.db"
    shutil.copy(BEFORE_FIXTURE, working_db)

    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(working_db))
    conn.executescript(sql)
    conn.executescript(sql)  # second application — must be no-op
    conn.commit()

    rows = [r[0] for r in conn.execute("SELECT study_name FROM studies").fetchall()]
    conn.close()

    for name in rows:
        assert not name.startswith("LEGACY__LEGACY__"), (
            f"study_name {name!r} has double LEGACY__ prefix after running migration "
            "twice. Migration must be idempotent: WHERE study_name NOT LIKE 'LEGACY__%'."
        )


# ===========================================================================
# Test 8 — migration preserves already-LEGACY__ rows unchanged
# ===========================================================================

def test_archive_migration_skips_already_archived_rows(tmp_path):
    """
    Rows already prefixed with LEGACY__ must not be modified by the migration.
    Rows in new timestamp__ format must also be left untouched.
    """
    working_db = tmp_path / "optuna_studies.db"
    shutil.copy(BEFORE_FIXTURE, working_db)

    conn = sqlite3.connect(str(working_db))
    # Insert a row that is already archived and one in new timestamp format
    conn.execute("INSERT INTO studies (study_name) VALUES ('LEGACY__already_archived')")
    conn.execute(
        "INSERT INTO studies (study_name) VALUES ('2026-05-15T160000Z__new_format')"
    )
    conn.commit()

    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()

    rows = {r[0] for r in conn.execute("SELECT study_name FROM studies").fetchall()}
    conn.close()

    assert "LEGACY__already_archived" in rows, (
        "LEGACY__already_archived must be unchanged after migration."
    )
    assert "LEGACY__LEGACY__already_archived" not in rows, (
        "LEGACY__already_archived must not be double-prefixed."
    )
    assert "2026-05-15T160000Z__new_format" in rows, (
        "New timestamp__ format row must be preserved unchanged by migration."
    )
