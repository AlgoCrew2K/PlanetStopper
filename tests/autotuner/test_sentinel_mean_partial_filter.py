"""RED tests — A4 sentinel-mean: the haircut pre-filter must exclude partial-sentinel CPCV means.

Audit finding sentinel-mean (validation/overfitting.md, GATE2-PLAN A4):
  The haircut pre-filter at autotuner.py:2269 and autotuner.py:2800 uses an
  EXACT-float compare `t.value != math_engine._SORTINO_SENTINEL` (1e6) to drop
  zero-downside degenerate trials. A CPCV trial's value is the MEAN across
  _CPCV_N_PATHS path scores (autotuner.py:2190-2194). If ONE path returns the
  Sortino sentinel (an all-non-negative path), the trial's mean is
  ~= (1e6 + small)/_CPCV_N_PATHS ~= 200000.2 — which is != 1e6, so it SURVIVES
  the exact-float filter. A degenerate trial (one zero-downside path) then
  dominates the BHY haircut / selection.

  The codebase comments at both filter sites already reference a
  `filter_sortino_sentinels` helper that "excludes ... zero-downside trials",
  but no such helper exists — the filter is an inline exact-float compare that
  misses partial-sentinel means.

What the fix must do (autotuner.py):
  Extract a named `filter_sortino_sentinels(trials)` (the contract the comments
  already name) that excludes BOTH pure-sentinel trials AND partial-sentinel CPCV
  means (value at or above _SORTINO_SENTINEL / _CPCV_N_PATHS), and route both
  filter sites through it.

RED markers:
  - filter_sortino_sentinels does not exist yet (AttributeError).
  - Once it exists, it must exclude a partial-sentinel mean (~200000.2) while
    keeping a genuine trial (value 0.5).

No producer-computed value is hardcoded: the partial-sentinel mean and the
detection threshold are derived at runtime from the live _SORTINO_SENTINEL and
_CPCV_N_PATHS constants.

Fixture: tests/fixtures/math/sentinel_mean_partial_filter.json
"""

from __future__ import annotations

import json
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "sentinel_mean_partial_filter.json"


@pytest.fixture(scope="module")
def sentinel_mean_fixture() -> dict:
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/sentinel_mean_partial_filter.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _make_trial(value):
    return type(
        "FakeTrial",
        (),
        {
            "value": value,
            "user_attrs": {"daily_returns": [0.5, 0.3, 0.4]},
            "params": {"X": 1.0},
            "number": 0,
        },
    )()


def _partial_sentinel_mean(small_path_scores):
    """Derive the partial-sentinel CPCV mean from live constants: one path is the
    sentinel, the rest are the small scores; the trial value is their mean."""
    import autotuner
    import math_engine

    path_scores = [math_engine._SORTINO_SENTINEL] + list(small_path_scores)
    # Pad/truncate to _CPCV_N_PATHS so the mean matches a real CPCV aggregate.
    n = autotuner._CPCV_N_PATHS
    path_scores = (path_scores + [0.0] * n)[:n]
    return sum(path_scores) / len(path_scores)


class TestFilterSortinoSentinelsHelperExists:
    """The named filter_sortino_sentinels helper (per the existing comments) must exist."""

    def test_filter_sortino_sentinels_function_exists(self):
        """autotuner must expose filter_sortino_sentinels — the contract the
        autotuner.py:2269 / :2800 comments already reference. RED until added."""
        import autotuner

        assert hasattr(autotuner, "filter_sortino_sentinels"), (
            "autotuner must expose filter_sortino_sentinels(trials) — the helper the "
            "filter-site comments (autotuner.py:2279, :2810) already name. The inline "
            "exact-float filter misses partial-sentinel CPCV means; extract the helper "
            "and route both filter sites through it. RED until added."
        )
        assert callable(autotuner.filter_sortino_sentinels), (
            "filter_sortino_sentinels must be callable."
        )


class TestFilterSortinoSentinelsExcludesPartialMean:
    """filter_sortino_sentinels must exclude partial-sentinel CPCV means."""

    def test_partial_sentinel_mean_is_excluded(self, sentinel_mean_fixture):
        """A CPCV-mean trial with ONE sentinel path (~200000.2) must be EXCLUDED.

        The mean is derived from live _SORTINO_SENTINEL / _CPCV_N_PATHS. The exact-
        float filter misses it (200000.2 != 1e6); the fix must catch it.
        """
        import autotuner

        if not hasattr(autotuner, "filter_sortino_sentinels"):
            pytest.fail(
                "autotuner.filter_sortino_sentinels missing — RED until the helper is "
                "added (see TestFilterSortinoSentinelsHelperExists)."
            )

        sc = sentinel_mean_fixture["scenarios"]["partial_sentinel_cpcv_mean"]
        partial_mean = _partial_sentinel_mean(sc["small_path_scores"])
        partial_trial = _make_trial(partial_mean)

        kept = autotuner.filter_sortino_sentinels([partial_trial])
        kept_values = [t.value for t in kept]

        assert partial_mean not in kept_values, (
            f"A partial-sentinel CPCV mean ({partial_mean!r}) survived the filter. "
            "One path returned the Sortino sentinel (1e6); the trial mean "
            f"~= _SORTINO_SENTINEL/_CPCV_N_PATHS = "
            f"{__import__('math_engine')._SORTINO_SENTINEL / autotuner._CPCV_N_PATHS!r}. "
            "The exact-float filter (t.value != _SORTINO_SENTINEL) misses it; the fix "
            "must detect partial-sentinel means (value >= _SORTINO_SENTINEL/_CPCV_N_PATHS)."
        )

    def test_pure_sentinel_is_excluded(self):
        """A pure-sentinel trial (value == _SORTINO_SENTINEL) must be EXCLUDED
        (the original exact-float behavior must be preserved)."""
        import autotuner
        import math_engine

        if not hasattr(autotuner, "filter_sortino_sentinels"):
            pytest.fail("autotuner.filter_sortino_sentinels missing — RED.")

        pure = _make_trial(math_engine._SORTINO_SENTINEL)
        kept = autotuner.filter_sortino_sentinels([pure])
        assert kept == [], (
            "A pure-sentinel trial (value == _SORTINO_SENTINEL) must be excluded; "
            f"got {[t.value for t in kept]!r}."
        )

    def test_genuine_trial_is_kept(self, sentinel_mean_fixture):
        """A genuine trial (small finite value) must be KEPT — the filter must not
        over-exclude. Pins that the partial-sentinel detection does not also drop
        legitimate trials."""
        import autotuner

        if not hasattr(autotuner, "filter_sortino_sentinels"):
            pytest.fail("autotuner.filter_sortino_sentinels missing — RED.")

        sc = sentinel_mean_fixture["scenarios"]["partial_sentinel_cpcv_mean"]
        genuine = _make_trial(sc["genuine_trial_value"])
        kept = autotuner.filter_sortino_sentinels([genuine])
        kept_values = [t.value for t in kept]
        assert sc["genuine_trial_value"] in kept_values, (
            f"A genuine trial (value={sc['genuine_trial_value']!r}) must be KEPT; "
            f"got kept values {kept_values!r}. The filter must not over-exclude."
        )

    def test_mixed_set_keeps_only_genuine(self, sentinel_mean_fixture):
        """A mixed set [genuine, partial-sentinel-mean, pure-sentinel] must keep
        ONLY the genuine trial."""
        import autotuner
        import math_engine

        if not hasattr(autotuner, "filter_sortino_sentinels"):
            pytest.fail("autotuner.filter_sortino_sentinels missing — RED.")

        sc = sentinel_mean_fixture["scenarios"]["partial_sentinel_cpcv_mean"]
        genuine = _make_trial(sc["genuine_trial_value"])
        partial = _make_trial(_partial_sentinel_mean(sc["small_path_scores"]))
        pure = _make_trial(math_engine._SORTINO_SENTINEL)

        kept = autotuner.filter_sortino_sentinels([genuine, partial, pure])
        kept_values = [t.value for t in kept]
        assert kept_values == [sc["genuine_trial_value"]], (
            f"filter_sortino_sentinels must keep ONLY the genuine trial; "
            f"kept {kept_values!r}. Both the pure sentinel and the partial-sentinel "
            "mean must be excluded."
        )


# ===========================================================================
# Adversarial REVISE — a mean-threshold filter leaves a gap when the OTHER paths
# are net-negative: the one-sentinel mean dips BELOW _SORTINO_SENTINEL/_CPCV_N_PATHS
# and survives. Correct detection is at the PATH-SCORE level (any path == sentinel),
# which requires the objective to persist per-path scores. This is the Sortino-branch
# reachable case (CRRA-EU never emits the sentinel). See team-lead escalation re:
# whether to persist path_scores (correct) or accept the mean-threshold caveat (LOW).
# ===========================================================================


class TestPartialSentinelNetNegativePathsGap:
    """A one-sentinel-path mean with net-negative other paths must STILL be excluded."""

    def _persist_path_scores(self, trial, path_scores):
        """Attach per-path scores to a fake trial's user_attrs (the contract a
        path-level fix would rely on)."""
        trial.user_attrs["path_scores"] = list(path_scores)
        return trial

    def test_net_negative_partial_sentinel_mean_is_excluded(self, sentinel_mean_fixture):
        """A CPCV mean with ONE sentinel path and net-NEGATIVE other paths
        (mean < _SORTINO_SENTINEL/_CPCV_N_PATHS) must STILL be excluded.

        A pure aggregate-value threshold filter `value < _SORTINO_SENTINEL/_CPCV_N_PATHS`
        WRONGLY KEEPS this (the negative paths drag the mean below the threshold). The
        correct fix detects the sentinel at the PATH-SCORE level. The trial here carries
        its per-path scores under user_attrs['path_scores'] so a path-level filter can
        see the sentinel; an aggregate-only filter cannot.
        """
        import autotuner
        import math_engine

        if not hasattr(autotuner, "filter_sortino_sentinels"):
            pytest.fail("autotuner.filter_sortino_sentinels missing — RED.")

        sc = sentinel_mean_fixture["scenarios"]["partial_sentinel_cpcv_mean_net_negative_paths"]
        n = autotuner._CPCV_N_PATHS
        # One sentinel path + (n-1) net-negative paths.
        path_scores = [math_engine._SORTINO_SENTINEL] + list(sc["other_path_scores"])
        path_scores = (path_scores + [0.0] * n)[:n]
        mean_value = sum(path_scores) / len(path_scores)

        # Sanity: this mean is BELOW the naive threshold (the gap a mean-filter leaves).
        threshold = math_engine._SORTINO_SENTINEL / autotuner._CPCV_N_PATHS
        assert mean_value < threshold, (
            f"Fixture precondition: the net-negative partial-sentinel mean "
            f"({mean_value!r}) must be BELOW the naive threshold ({threshold!r}) so the "
            "mean-only filter's gap is exercised. If it is not below, re-tune the "
            "other_path_scores more negative."
        )

        trial = self._persist_path_scores(_make_trial(mean_value), path_scores)
        kept = autotuner.filter_sortino_sentinels([trial])

        assert kept == [], (
            f"A partial-sentinel CPCV mean with net-negative other paths "
            f"(value={mean_value!r} < threshold {threshold!r}) survived the filter — it "
            "still contains a zero-downside SENTINEL path and is degenerate.\n"
            "  GAP: a `value < _SORTINO_SENTINEL/_CPCV_N_PATHS` mean-threshold filter "
            "cannot catch this (negative paths drag the mean below the threshold; a path "
            "can reach ~-999 at the CRRA wealth floor, or large-negative Sortino). The "
            "correct fix detects the sentinel at the PATH-SCORE level: persist per-path "
            "scores (user_attrs['path_scores']) and exclude any trial where "
            "any(score == _SORTINO_SENTINEL)."
        )
