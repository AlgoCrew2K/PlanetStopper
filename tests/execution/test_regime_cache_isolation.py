"""
RED tests — Phase 3c: hard constraint 1 (NO-BLOCKING-I/O / OFFLINE LABEL).

The regime label used in the live exit decision must be read from a precomputed
cache (database), NOT by calling regime_classifier.classify_regime() on the
1-minute execution path.

Design basis:
  - PHASE3_DECISIONS.md §Sub-phase 3a: 'the classifier is an offline daily
    diagnostic; it is intentionally NOT imported by alpha_bot_execution.py or
    math_engine.py and is NEVER called on the 1-minute execution path.'
  - Team brief hard constraint 1: 'The classifier must NOT run on the 1-minute
    execution path.  Test/assert that the exit path reads a cached label, not
    classify_regime() live.'

Tests in this file:
  1. alpha_bot_execution does NOT import regime_classifier directly (structural).
  2. math_engine does NOT import regime_classifier directly (structural).
  3. database.py exposes get_cached_regime_label(symphony_id) -> str | None.
  4. database.py exposes save_regime_label(symphony_id, label, as_of_date).
  5. get_cached_regime_label returns None when no label has been saved.
  6. get_cached_regime_label returns the last-saved label for a given symphony_id.
  7. Labels from one symphony_id do not bleed to another.
  8. Stale-label sentinel: get_cached_regime_label accepts a staleness_cutoff_days
     parameter and returns None when the most-recent label is older than cutoff.
  9. The label stored by save_regime_label is round-trip identical to what
     get_cached_regime_label reads back.

All tests are RED by construction — save_regime_label and get_cached_regime_label
do not exist yet.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import database

# ---------------------------------------------------------------------------
# Project-root path (for structural AST tests)
# ---------------------------------------------------------------------------

# The worktree root is two levels up from this file (tests/execution/ -> tests/ -> root).
_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# 1. Structural: alpha_bot_execution.py must NOT import regime_classifier
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_does_not_import_regime_classifier() -> None:
    """
    Hard constraint 1: the 1-minute execution path must NEVER call
    classify_regime() live.  The first guard is that alpha_bot_execution.py
    does not import regime_classifier at all.

    If the import is absent, the call cannot happen by construction.

    Catches an implementer who routes the live call through alpha_bot_execution
    rather than reading the cached label from the database.
    """
    src_path = _WORKTREE_ROOT / "alpha_bot_execution.py"
    assert src_path.exists(), (
        f"alpha_bot_execution.py not found at {src_path}; cannot perform structural import check"
    )
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    bad_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "regime_classifier" in alias.name:
                    bad_imports.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "regime_classifier" in module:
                bad_imports.append(f"line {node.lineno}: from {module} import ...")

    assert not bad_imports, (
        "alpha_bot_execution.py MUST NOT import regime_classifier (hard "
        "constraint 1: the classifier is an offline diagnostic; it must "
        "NEVER run on the 1-minute execution path). Found imports: "
        + ", ".join(bad_imports)
        + ". The execution path reads a CACHED label from the database; "
        "dispatch classify_regime() from an offline daily job instead."
    )


# ---------------------------------------------------------------------------
# 2. Structural: math_engine.py must NOT import regime_classifier
# ---------------------------------------------------------------------------


def test_math_engine_does_not_import_regime_classifier() -> None:
    """
    Hard constraint 1 (math layer): math_engine.py provides pure computation
    functions.  It must not import regime_classifier — adding that import
    would pull the offline classifier into the execution hot-path whenever
    math_engine is imported.
    """
    src_path = _WORKTREE_ROOT / "math_engine.py"
    assert src_path.exists(), f"math_engine.py not found at {src_path}"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    bad_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "regime_classifier" in alias.name:
                    bad_imports.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "regime_classifier" in module:
                bad_imports.append(f"line {node.lineno}: from {module} import ...")

    assert not bad_imports, "math_engine.py MUST NOT import regime_classifier. Found: " + ", ".join(
        bad_imports
    )


# ---------------------------------------------------------------------------
# 3. database.get_cached_regime_label exists
# ---------------------------------------------------------------------------


def test_get_cached_regime_label_function_exists() -> None:
    """
    The cache accessor must be a callable on database.
    It does not exist yet — this test is RED.
    """
    assert hasattr(database, "get_cached_regime_label"), (
        "database.get_cached_regime_label not found. Implement the "
        "accessor that returns the most-recent regime label for a given "
        "symphony_id, or None if absent/stale."
    )
    assert callable(database.get_cached_regime_label), (
        "database.get_cached_regime_label is not callable"
    )


# ---------------------------------------------------------------------------
# 4. database.save_regime_label exists
# ---------------------------------------------------------------------------


def test_save_regime_label_function_exists() -> None:
    """
    The cache writer must be a callable on database.
    The offline daily job calls save_regime_label(symphony_id, label, as_of_date).
    """
    assert hasattr(database, "save_regime_label"), (
        "database.save_regime_label not found. Implement the writer that "
        "persists a (symphony_id, label, as_of_date) row so the execution "
        "path can read it back via get_cached_regime_label."
    )
    assert callable(database.save_regime_label), "database.save_regime_label is not callable"


# ---------------------------------------------------------------------------
# 5. get_cached_regime_label returns None when no label has been saved
# ---------------------------------------------------------------------------


def test_get_cached_regime_label_returns_none_when_absent(tmp_path, monkeypatch) -> None:
    """
    When no label has been saved for a symphony_id, get_cached_regime_label
    must return None (safe default — unknown regime -> no adjustment).

    The tmp_path / monkeypatch setup ensures we write to an isolated test DB
    (conftest.py _isolate_db autouse also runs; this test adds an explicit
    assertion that None is returned for an unseen symphony_id even after
    the DB is initialized).
    """
    result = database.get_cached_regime_label("unseen-symphony-id-xyz")
    assert result is None, (
        f"get_cached_regime_label must return None when no label has been "
        f"saved for the symphony_id, got {result!r}. Safe default: absent "
        f"label -> no regime adjustment."
    )


# ---------------------------------------------------------------------------
# 6. Round-trip: save then read back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    ["trending", "mean-reverting", "high-vol"],
)
def test_save_and_read_back_label(label: str) -> None:
    """
    save_regime_label followed by get_cached_regime_label must return the
    same label string unchanged.

    Each recognized label from the regime_classifier taxonomy must round-trip
    exactly (no coercion, no lowercasing, no truncation).
    """
    symphony_id = f"test-symphony-{label}"
    as_of_date = "2026-05-31"

    database.save_regime_label(symphony_id, label, as_of_date)
    result = database.get_cached_regime_label(symphony_id)

    assert result == label, (
        f"Round-trip mismatch: saved label={label!r} for symphony_id="
        f"{symphony_id!r}, got {result!r} on read-back. The stored label "
        f"must be returned exactly as saved."
    )


# ---------------------------------------------------------------------------
# 7. Labels do not bleed between symphony_ids
# ---------------------------------------------------------------------------


def test_labels_isolated_per_symphony_id() -> None:
    """
    save_regime_label must store the label keyed by symphony_id.  A label
    saved for symphony A must not appear when querying symphony B.

    Catches an impl that stores a single global latest-label rather than
    per-symphony state.
    """
    database.save_regime_label("symphony-A", "trending", "2026-05-31")
    database.save_regime_label("symphony-B", "mean-reverting", "2026-05-31")

    result_a = database.get_cached_regime_label("symphony-A")
    result_b = database.get_cached_regime_label("symphony-B")
    result_c = database.get_cached_regime_label("symphony-C")  # never saved

    assert result_a == "trending", (
        f"symphony-A label mismatch: expected 'trending', got {result_a!r}"
    )
    assert result_b == "mean-reverting", (
        f"symphony-B label mismatch: expected 'mean-reverting', got {result_b!r}"
    )
    assert result_c is None, f"symphony-C (never saved) must return None, got {result_c!r}"


# ---------------------------------------------------------------------------
# 8. Most-recent label wins when saved twice for same symphony_id
# ---------------------------------------------------------------------------


def test_latest_label_supersedes_earlier_label() -> None:
    """
    When save_regime_label is called twice for the same symphony_id, the
    most-recent label must be returned.

    The execution path reads the label written by the last offline daily run;
    old labels must not persist over newer ones.
    """
    symphony_id = "test-symphony-overwrite"

    database.save_regime_label(symphony_id, "trending", "2026-05-30")
    database.save_regime_label(symphony_id, "mean-reverting", "2026-05-31")

    result = database.get_cached_regime_label(symphony_id)
    assert result == "mean-reverting", (
        f"Expected latest label 'mean-reverting', got {result!r}. "
        f"The most recent save must supersede earlier saves for the same "
        f"symphony_id."
    )


# ---------------------------------------------------------------------------
# 9. Staleness cutoff: stale labels return None
# ---------------------------------------------------------------------------


def test_stale_label_returns_none_when_cutoff_specified() -> None:
    """
    Hard constraint 1 (stale-label sub-case): the team brief says
    'regime absent/unknown/stale -> current behavior (no adjustment).'

    get_cached_regime_label must accept a staleness_cutoff_days parameter.
    When the most-recent label's as_of_date is older than staleness_cutoff_days
    days before 'today', the function returns None.

    This test saves a label with as_of_date='2020-01-01' (very old) and
    asserts that a staleness_cutoff_days=7 causes None to be returned.

    'today' for this test is the as_of_date parameter of the function call,
    or the database call's current date if the function derives it internally.
    The test uses staleness_cutoff_days=1 to ensure a 2020-01-01 label is stale.
    """
    symphony_id = "test-stale-symphony"
    database.save_regime_label(symphony_id, "trending", "2020-01-01")

    result = database.get_cached_regime_label(symphony_id, staleness_cutoff_days=1)
    assert result is None, (
        f"Expected None for a stale label (as_of_date=2020-01-01, cutoff=1 day), "
        f"got {result!r}. Stale labels must be treated as absent."
    )


def test_fresh_label_not_stale_when_within_cutoff() -> None:
    """
    A label saved with today's date must NOT be treated as stale when
    staleness_cutoff_days is a reasonable window.

    This test saves with as_of_date = today (2026-05-31) and asserts the
    label is returned with staleness_cutoff_days=7.
    """
    symphony_id = "test-fresh-symphony"
    import datetime

    today = datetime.date.today().isoformat()
    database.save_regime_label(symphony_id, "high-vol", today)

    result = database.get_cached_regime_label(symphony_id, staleness_cutoff_days=7)
    assert result == "high-vol", (
        f"A fresh label (as_of_date={today}, cutoff=7 days) must be returned. Got {result!r}."
    )


# ---------------------------------------------------------------------------
# 10. get_cached_regime_label returns None when staleness_cutoff_days is 0
#     (always stale — safety escape hatch)
# ---------------------------------------------------------------------------


def test_zero_staleness_cutoff_returns_none() -> None:
    """
    staleness_cutoff_days=0 means 'always treat as stale'.  This is the
    safety escape hatch: setting cutoff=0 forces the execution path to treat
    the regime as absent (no adjustment) regardless of the stored label.
    """
    symphony_id = "test-zero-cutoff"
    import datetime

    today = datetime.date.today().isoformat()
    database.save_regime_label(symphony_id, "trending", today)

    result = database.get_cached_regime_label(symphony_id, staleness_cutoff_days=0)
    assert result is None, (
        f"staleness_cutoff_days=0 must always return None (all labels are "
        f"stale by definition), got {result!r}."
    )


# ---------------------------------------------------------------------------
# 11. get_cached_regime_label default (no cutoff) returns label without expiry
# ---------------------------------------------------------------------------


def test_no_staleness_cutoff_returns_label_regardless_of_age() -> None:
    """
    When staleness_cutoff_days is NOT specified (default), get_cached_regime_label
    returns the stored label regardless of age.  This makes the parameter
    optional and ensures backwards compatibility for callers that do not care
    about staleness.
    """
    symphony_id = "test-no-cutoff"
    database.save_regime_label(symphony_id, "mean-reverting", "2020-01-01")

    result = database.get_cached_regime_label(symphony_id)
    assert result == "mean-reverting", (
        f"Without staleness_cutoff_days, get_cached_regime_label must return "
        f"the stored label regardless of age, got {result!r}."
    )
