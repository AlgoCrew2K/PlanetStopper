"""RED tests — the Mongo/pymongo seam added to
tests/tools/execution_seam_detector.py (fr-review finding, team-lead-ratified
hard AC-8 scope, frontrunner-signals cycle, 2026-07-16).

WHY THIS FILE EXISTS: fr-review verified (2026-07-16, HEAD 915ba65f, clean
tree) that NO existing test anywhere in this codebase actually EXERCISES
execution_seam_detector.py's raise behavior for either of its two pre-existing
seams (anthropic or Composer) — test_execution_seam_detector_coverage.py only
checks _R2_3_DEFAULT_TARGETS completeness via static filename globbing, it
never runs the detector itself. This file is therefore the FIRST test in this
codebase to actually exercise a seam guard's raise behavior at all — it
establishes the pattern rather than mirroring an existing one (fr-review,
explicit scope note: just the Mongo seam this cycle, NOT a retroactive
self-test for the other two — that is out of this feature's scope boundaries).

WHY THE TEST CALLS make_mongo_guard() DIRECTLY, NOT run_seam_detector():
run_seam_detector()'s own patch is applied in THIS (outer) process, then it
launches pytest.main() as a NESTED pytest session. tests/conftest.py's own
session-autouse `_no_live_mongo_atlas_connections` fixture re-establishes ITS
OWN `patch("pymongo.MongoClient", ...)` fresh inside that nested session —
being the innermost/most-recently-applied patch for the session's duration,
it would shadow whatever run_seam_detector patched beforehand, so asserting
on run_seam_detector's own mongo_calls list after a nested sub-run would
actually be proving CONFTEST's guard fires, not this tool's own — a
false-positive self-test that proves nothing about the code actually added
here. Calling make_mongo_guard() directly, applying its patch as THIS test's
own innermost context (no nested pytest.main() involved), correctly
demonstrates the real mechanism.

PATCH STYLE: `patch("pymongo.MongoClient", side_effect=guard_fn)` — a
NAME-REPLACEMENT patch, NOT `patch.object(pymongo.MongoClient, "__init__",
...)`. This distinction is load-bearing, not stylistic (RCA, caught by the
first actual pytest run of this file): tests/conftest.py's session-autouse
fixture has ALREADY replaced the `pymongo.MongoClient` NAME with a Mock
before ANY test in the outer session runs, so by the time a test here
executes, `pymongo.MongoClient` already IS a Mock — and `patch.object(<a
Mock instance>, "__init__", ...)` raises "Attempting to set unsupported
magic method '__init__'" (unittest.mock disallows setting dunder attributes
on a Mock this way). unittest.mock's "a test's own local patch overrides the
outer session fixture" guarantee (proven by
test_a_tests_own_pymongo_mock_still_overrides_the_guard in
tests/test_no_live_mongo_guard.py) holds for NAME-REPLACEMENT patches
(`patch("target", ...)`, which simply reassigns whatever is currently
there) but NOT for `patch.object`-style patches, which need to mutate an
attribute ON the current target object and fail when that object is already
a Mock. make_mongo_guard()'s own docstring in execution_seam_detector.py
carries the full RCA.

NO live network calls anywhere in this file — a harmless placeholder URI is
passed only to prove CONSTRUCTION is intercepted before any connection
attempt; the patched constructor raises before any real dial happens.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def detector():
    """Load tests/tools/execution_seam_detector.py directly (it is
    deliberately NOT named test_*.py so pytest never auto-collects it —
    mirrors test_execution_seam_detector_coverage.py's own loading idiom)."""
    module_path = _REPO_ROOT / "tests" / "tools" / "execution_seam_detector.py"
    spec = importlib.util.spec_from_file_location(
        "execution_seam_detector_mongo_check", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The decisive raise-behavior proof
# ---------------------------------------------------------------------------


def test_make_mongo_guard_raises_on_real_but_harmless_construction(detector):
    """Construct a real-but-harmless pymongo.MongoClient(...) inside the
    patched context and assert it raises LiveMongoClientConstructedError —
    the exact scenario fr-review's ask names."""
    import pymongo

    calls, guard_fn = detector.make_mongo_guard()
    assert calls == [], "sanity: no calls recorded before construction"

    with patch("pymongo.MongoClient", side_effect=guard_fn):
        with pytest.raises(detector.LiveMongoClientConstructedError):
            pymongo.MongoClient("mongodb://harmless-placeholder-never-dialed")

    assert len(calls) == 1, f"expected exactly one recorded call site, got {len(calls)}"
    assert "pymongo.MongoClient" in calls[0], (
        f"recorded call site should reference the construction context, got: {calls[0][:200]}"
    )


def test_make_mongo_guard_error_message_names_the_real_money_risk(detector):
    """Mirrors the existing Anthropic/Composer guards' actionable-message
    convention — the error must convey the stakes, not just fail silently."""
    import pymongo

    _calls, guard_fn = detector.make_mongo_guard()

    with patch("pymongo.MongoClient", side_effect=guard_fn):
        with pytest.raises(detector.LiveMongoClientConstructedError) as exc_info:
            pymongo.MongoClient("mongodb://harmless-placeholder-never-dialed")

    message = str(exc_info.value)
    assert "MongoClient" in message
    assert "seam" in message.lower() or "leak" in message.lower(), (
        f"guard message should convey the stakes (a leaking Atlas-seam mock), got: {message!r}"
    )


def test_two_separate_guards_maintain_independent_call_lists(detector):
    """Each make_mongo_guard() call returns its OWN calls list — no shared
    module-level mutable state that would let one test's construction bleed
    into another's assertion count."""
    import pymongo

    calls_a, guard_a = detector.make_mongo_guard()
    calls_b, _guard_b = detector.make_mongo_guard()

    with patch("pymongo.MongoClient", side_effect=guard_a):
        with pytest.raises(detector.LiveMongoClientConstructedError):
            pymongo.MongoClient("mongodb://a")

    assert len(calls_a) == 1
    assert len(calls_b) == 0, "guard_b's own list must be unaffected by guard_a's construction"


def test_a_tests_own_pymongo_mock_still_overrides_this_guard_too(detector):
    """Composability proof (mirrors test_no_live_mongo_guard.py's own
    equivalent test for the session-level guard): a test's own explicit
    mock, applied INSIDE this guard's context, must take precedence for its
    duration — standard unittest.mock nesting, the innermost active patch
    wins."""
    import pymongo

    _calls, guard_fn = detector.make_mongo_guard()
    sentinel_client = object()

    with patch("pymongo.MongoClient", side_effect=guard_fn):
        with patch("pymongo.MongoClient", return_value=sentinel_client):
            result = pymongo.MongoClient("mongodb://irrelevant")
            assert result is sentinel_client

        # After the inner patch exits, this guard's own raise behavior is
        # back in effect.
        with pytest.raises(detector.LiveMongoClientConstructedError):
            pymongo.MongoClient("mongodb://harmless-placeholder-never-dialed")


# ---------------------------------------------------------------------------
# run_seam_detector's return-tuple shape includes the third seam
# ---------------------------------------------------------------------------


def test_run_seam_detector_return_tuple_has_four_elements(detector):
    """Structural check: run_seam_detector must return
    (rc, anthropic_calls, composer_calls, mongo_calls) — four elements, not
    the pre-cycle three. Verified via introspection of the function's own
    return-type annotation rather than an actual sub-run (which would incur
    the nested-session shadowing this file's module docstring explains, and
    is unnecessary to prove the SHAPE contract)."""
    import inspect

    sig = inspect.signature(detector.run_seam_detector)
    return_annotation = str(sig.return_annotation)
    assert return_annotation.count(",") == 3, (
        f"expected a 4-tuple return annotation (rc, anthropic, composer, mongo), "
        f"got: {return_annotation!r}"
    )


# ---------------------------------------------------------------------------
# _FR_SIGNALS_DEFAULT_TARGETS coverage (mirrors
# test_execution_seam_detector_coverage.py's established pattern, scoped to
# this cycle's own test-file naming convention)
# ---------------------------------------------------------------------------


_FR_SIGNALS_CLUSTER_GLOBS: tuple[str, ...] = (
    "tests/advisors/test_frontrunner_signals_*.py",
    "tests/advisors/test_frontrunner_extraction_walk.py",
    "tests/advisors/test_frontrunner_detector_ac3_rebuild.py",
    "tests/advisors/test_frontrunner_builder_signal_gating.py",
    "tests/app/test_frontrunner_signals_tab_render.py",
)


def _fr_signals_cluster_files() -> set[str]:
    found: set[str] = set()
    for pattern in _FR_SIGNALS_CLUSTER_GLOBS:
        for path in _REPO_ROOT.glob(pattern):
            found.add(path.relative_to(_REPO_ROOT).as_posix())
    return found


def test_fr_signals_default_targets_cover_the_full_cluster(detector):
    cluster = _fr_signals_cluster_files()
    targets = set(detector._FR_SIGNALS_DEFAULT_TARGETS)

    missing = cluster - targets
    assert not missing, (
        f"execution_seam_detector.py's _FR_SIGNALS_DEFAULT_TARGETS is missing "
        f"{sorted(missing)} — these files match this cycle's test-cluster naming "
        f"conventions but are not covered by a default (no-args) seam-detector run."
    )


def test_fr_signals_cluster_scan_self_guard_finds_known_files():
    """Self-guard: the glob must find at least the files we know exist —
    otherwise the coverage test above proves nothing."""
    cluster = _fr_signals_cluster_files()
    assert "tests/advisors/test_frontrunner_extraction_walk.py" in cluster, (
        "self-guard failed: the cluster scan no longer finds "
        "test_frontrunner_extraction_walk.py — the glob patterns are stale."
    )
