"""RED tests — A4/H3: _haircut_select index-space mismatch (sentinel filter).

Audit finding H3 (validation/overfitting.md, GATE2-PLAN A4):
  _haircut_select builds its t-stat / p-value list by iterating the UNFILTERED
  `completed_trials` (autotuner.py:1472) and computes `winner_idx` over that index
  space, but returns `filtered_trials[winner_idx]` (autotuner.py:1519) where
  `filtered_trials` has the math_engine._SORTINO_SENTINEL trials removed
  (autotuner.py:1458-1460). When a sentinel trial precedes a real trial AND the
  sentinel's series wins the argmin, `winner_idx` points at the sentinel's
  position in completed-space, so `filtered_trials[winner_idx]` returns the WRONG
  trial — and the returned `winner_tstat` (the sentinel's) does not match the
  returned trial's own series. With the sentinel at the end, the same shift can
  raise IndexError.

  Classified LATENT in the verdict (both live call sites pre-filter sentinels, so
  filtered_trials == completed_trials in production). This RED pins the FUNCTION
  CONTRACT directly — a real index-space bug that a future unfiltered caller
  would hit.

What the fix must do (autotuner.py):
  Iterate `filtered_trials` (not `completed_trials`) so the t-stat index space
  matches the returned-trial index space (or drop the redundant internal filter).
  The BHY machinery, sentinel filter semantics, and return contract are otherwise
  unchanged.

RED marker:
  - test_returned_winner_tstat_matches_returned_trial_series: pre-fix the returned
    winner_tstat (sentinel's ~11.94) does NOT match the returned trial's own series
    t-stat (~0.999) — an internal inconsistency. Post-fix they match.

No producer-computed value is hardcoded: the consistency oracle recomputes the
returned trial's t-stat from its own series via the live functions and asserts
equality with the returned winner_tstat.

Fixture: tests/fixtures/math/haircut_select_sentinel_index_alignment.json
"""

from __future__ import annotations

import json
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "haircut_select_sentinel_index_alignment.json"
)


@pytest.fixture(scope="module")
def h3_fixture() -> dict:
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/haircut_select_sentinel_index_alignment.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _make_trial(name, value, returns_pct):
    return type(
        "FakeTrial",
        (),
        {
            "value": value,
            "user_attrs": {"daily_returns": returns_pct},
            "params": {"NAME": name},
            "number": 0,
        },
    )()


def _tstat_of_series(returns_pct, gamma):
    """Recompute the CRRA-EU t-stat from a raw-percent series via the live
    functions — the consistency oracle for the returned winner's own series."""
    import autotuner
    import math_engine

    u = [
        math_engine.compute_crra_utility(
            autotuner.derive_floored_wealth_argument(r / autotuner.RETURN_PCT_TO_FRACTION),
            gamma,
        )
        for r in returns_pct
    ]
    return autotuner.compute_crra_eu_tstat(u)


class TestHaircutSelectSentinelIndexAlignment:
    """_haircut_select must select from the same list it scores (filtered_trials)."""

    def test_returned_winner_tstat_matches_returned_trial_series(self, h3_fixture):
        """The returned winner_tstat must equal the t-stat of the RETURNED trial's
        OWN series. Pre-fix, a sentinel preceding a real trial makes _haircut_select
        return the real (weak) trial but with the sentinel's (strong) t-stat — an
        internal inconsistency.

        Consistency oracle: no literal pinned; recompute the returned trial's t-stat
        from its own series and assert equality with winner_tstat. Tolerance rel=1e-9
        (deterministic double-precision arithmetic).
        """
        import autotuner

        sc = h3_fixture["scenarios"]["sentinel_before_real_trial_index_mismatch"]
        import math_engine

        sentinel = _make_trial(
            "SENT_STRONG", math_engine._SORTINO_SENTINEL, sc["sentinel_trial_series_pct"]
        )
        real = _make_trial("REAL_WEAK", sc["real_trial_value"], sc["real_trial_series_pct"])
        gamma = sc["gamma"]

        # completed_trials order: sentinel BEFORE the real trial.
        winner, p_adj, winner_tstat = autotuner._haircut_select(
            [sentinel, real], tstat_fn=autotuner.compute_crra_eu_tstat, gamma=gamma
        )

        # The sentinel must never be the returned winner (it is filtered).
        if winner is not None:
            assert winner.params["NAME"] != "SENT_STRONG", (
                "_haircut_select returned the SENTINEL trial as winner — the sentinel "
                "filter (autotuner.py:1458-1460) must exclude it."
            )
            expected_tstat = _tstat_of_series(winner.user_attrs["daily_returns"], gamma)
            assert winner_tstat == pytest.approx(expected_tstat, rel=1e-9), (
                f"Returned winner_tstat={winner_tstat!r} does not match the t-stat of "
                f"the RETURNED trial's own series ({expected_tstat!r}).\n"
                "  H3 BUG: the loop iterates completed_trials (with the sentinel) but "
                "returns filtered_trials[winner_idx]; the sentinel's strong series wins "
                "the argmin, so the WEAK real trial is returned with the SENTINEL's "
                "t-stat. Fix: iterate filtered_trials so the index space is consistent."
            )

    def test_sentinel_does_not_steal_the_selection(self, h3_fixture):
        """The returned p_adj must correspond to the returned trial's own t-stat,
        not a sentinel's. If the weak real trial is returned, its p_adj must reflect
        its (low) t-stat — i.e. either it legitimately fails the gate (winner None)
        or, if returned, its t-stat is the weak one (caught by the consistency test).

        This pins that the sentinel's strong series cannot drive the SELECTION of a
        different (weak) trial.
        """
        import autotuner
        import math_engine

        sc = h3_fixture["scenarios"]["sentinel_before_real_trial_index_mismatch"]
        sentinel = _make_trial(
            "SENT_STRONG", math_engine._SORTINO_SENTINEL, sc["sentinel_trial_series_pct"]
        )
        real = _make_trial("REAL_WEAK", sc["real_trial_value"], sc["real_trial_series_pct"])
        gamma = sc["gamma"]

        winner, p_adj, winner_tstat = autotuner._haircut_select(
            [sentinel, real], tstat_fn=autotuner.compute_crra_eu_tstat, gamma=gamma
        )

        # The weak real series' own t-stat is low; if it is returned as a winner,
        # its t-stat must be that low value (not the sentinel's high one).
        real_own_tstat = _tstat_of_series(sc["real_trial_series_pct"], gamma)
        sentinel_own_tstat = _tstat_of_series(sc["sentinel_trial_series_pct"], gamma)
        assert sentinel_own_tstat > real_own_tstat, (
            "Fixture precondition: the sentinel's series must out-score the real "
            f"trial's ({sentinel_own_tstat:.4f} vs {real_own_tstat:.4f}) so the "
            "index mismatch is exercised."
        )
        if winner is not None:
            assert winner_tstat == pytest.approx(real_own_tstat, rel=1e-9), (
                f"The returned winner_tstat={winner_tstat!r} must be the real trial's "
                f"own t-stat ({real_own_tstat!r}), not the sentinel's "
                f"({sentinel_own_tstat!r}). The sentinel must not steal the selection."
            )

    def test_sentinel_only_returns_none_triple(self, h3_fixture):
        """Regression: a single sentinel-only trial must return (None, None, None).
        The sentinel filter must remain after the index-alignment fix.
        """
        import autotuner
        import math_engine

        sc = h3_fixture["scenarios"]["sentinel_only_returns_none"]
        sentinel = _make_trial(
            "SENT", math_engine._SORTINO_SENTINEL, sc["sentinel_trial_series_pct"]
        )

        result = autotuner._haircut_select(
            [sentinel], tstat_fn=autotuner.compute_crra_eu_tstat, gamma=sc["gamma"]
        )
        assert result == (None, None, None), (
            f"_haircut_select with only a sentinel trial must return "
            f"(None, None, None); got {result!r}."
        )
