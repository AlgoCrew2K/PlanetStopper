"""
RED tests — F-3 from rev-ocon review on 78bc707.

Contract under test:
  The default CRRA gamma in _haircut_select (autotuner.py) when `gamma` is
  None must equal float(database.PHASE1_THEORY_GAMMA).

The Phase-1 canonical gamma value is declared in database.py as:
    PHASE1_THEORY_GAMMA: str = "2.0"

The _haircut_select fallback currently reads:
    _crra_gamma = gamma if gamma is not None else 2.0

The inline literal `2.0` is a magic duplicate of PHASE1_THEORY_GAMMA — a
drift hazard. If the council revises the canonical gamma, the haircut
fallback will silently diverge.

Fix: replace `2.0` with `float(database.PHASE1_THEORY_GAMMA)` or a locally
named constant derived from it.

Enforcement: source-inspection (no import of autotuner needed — avoids the
optuna/joblib import-collision noted in test_ai_advisor_safety.py:939) plus
a behavioural check via a direct `_haircut_select` call with gamma=None.

Source-inspection precedent: tests/ai_advisor/test_ai_advisor_safety.py:996
(OPTUNA_SEARCH_SPACE_KEYS drift guard).
"""

from __future__ import annotations

import pathlib
import re

import database

# ---------------------------------------------------------------------------
# Constants derived from the authoritative source — never hardcoded here.
# ---------------------------------------------------------------------------

# The canonical Phase-1 gamma from the authoritative registry.
# Tests derive the expected value from this source, not from a hand-copied literal.
_EXPECTED_GAMMA = float(database.PHASE1_THEORY_GAMMA)


# ===========================================================================
# F-3a — Source-inspection: _haircut_select must NOT contain a bare `2.0`
#         literal as the gamma fallback.
# ===========================================================================


def test_haircut_select_crra_fallback_does_not_use_bare_literal():
    """_haircut_select must not assign `2.0` as a bare literal gamma fallback.

    The literal must be replaced with an expression that references
    database.PHASE1_THEORY_GAMMA (or a constant derived from it), so that a
    future council revision to the canonical gamma propagates automatically
    to the haircut objective.

    Source-inspection only — no autotuner import (optuna/joblib collision guard;
    see tests/ai_advisor/test_ai_advisor_safety.py:939).
    """
    autotuner_path = pathlib.Path(__file__).parents[2] / "autotuner.py"
    assert autotuner_path.exists(), "autotuner.py not found"
    source = autotuner_path.read_text(encoding="utf-8")

    # Isolate the _haircut_select function body.
    # We scan from the def line to the next top-level def/class.
    lines = source.splitlines()
    in_func = False
    func_lines: list[str] = []
    for line in lines:
        if re.match(r"^def _haircut_select\b", line):
            in_func = True
        if in_func:
            if func_lines and re.match(r"^(def |class )\w", line):
                break
            func_lines.append(line)

    assert func_lines, "_haircut_select function not found in autotuner.py — has it been renamed?"

    func_source = "\n".join(func_lines)

    # The bare literal assignment pattern we forbid.
    # Match: `_crra_gamma = ... else 2.0` (with optional whitespace).
    bare_literal_pattern = re.compile(r"_crra_gamma\s*=\s*.+else\s+2\.0\b")
    assert not bare_literal_pattern.search(func_source), (
        "_haircut_select assigns `_crra_gamma = ... else 2.0` — a bare literal "
        "that duplicates database.PHASE1_THEORY_GAMMA. Replace with "
        "`float(database.PHASE1_THEORY_GAMMA)` or a named constant derived from it "
        "so that council revisions propagate automatically."
    )


def test_haircut_select_crra_fallback_references_phase1_gamma_constant():
    """_haircut_select must reference PHASE1_THEORY_GAMMA (or a derivative)
    in its gamma fallback, not a standalone numeric literal.

    Source-inspection only — no autotuner import.
    """
    autotuner_path = pathlib.Path(__file__).parents[2] / "autotuner.py"
    source = autotuner_path.read_text(encoding="utf-8")

    # Isolate function body.
    lines = source.splitlines()
    in_func = False
    func_lines: list[str] = []
    for line in lines:
        if re.match(r"^def _haircut_select\b", line):
            in_func = True
        if in_func:
            if func_lines and re.match(r"^(def |class )\w", line):
                break
            func_lines.append(line)

    func_source = "\n".join(func_lines)

    # The fallback must reference the canonical constant by name.
    has_reference = (
        "PHASE1_THEORY_GAMMA" in func_source
        or "_DEFAULT_CRRA_GAMMA" in func_source  # acceptable local alias
        or "CRRA_GAMMA_PHASE1" in func_source  # acceptable local alias
    )
    assert has_reference, (
        "_haircut_select's gamma fallback must reference database.PHASE1_THEORY_GAMMA "
        "(or a local constant derived from it). The current bare `2.0` literal is a "
        "drift hazard — a council revision to the canonical gamma will not propagate."
    )


# ===========================================================================
# F-3b — Behavioural: _haircut_select with gamma=None uses the Phase-1 value.
#
# We call _haircut_select with a minimal completed_trials list and gamma=None,
# then verify the result is identical to calling it with gamma=_EXPECTED_GAMMA.
# No assertion on the numeric output value — only equality between the two calls.
# ===========================================================================


def test_haircut_select_gamma_none_equals_phase1_gamma_value():
    """_haircut_select(trials, gamma=None) must produce the same result as
    _haircut_select(trials, gamma=float(database.PHASE1_THEORY_GAMMA)).

    This is the behavioural pin for F-3: the None default must resolve to
    exactly the Phase-1 canonical value, not some other implicit default.

    We use a mock tstat_fn that returns a known high t-statistic so all
    trials clear the FDR gate — the test is about whether gamma=None and
    gamma=_EXPECTED_GAMMA select the SAME trial, not about the FDR gate.

    No autotuner import at module scope — lazy import inside the test body
    (optuna/joblib import-collision guard; see test_ai_advisor_safety.py:939).
    """
    import importlib

    autotuner = importlib.import_module("autotuner")

    # Minimal completed_trials with distinct values and user_attrs for daily_returns.
    # Values are in-range Sortino-like scalars; we provide non-empty daily_returns
    # so the CRRA branch has something to work with if tstat_fn is swapped.
    mock_trials = []
    for i, val in enumerate([0.5, 0.8, 1.2, 0.3, 1.5]):
        t = type(
            "FakeTrial",
            (),
            {
                "value": val,
                "user_attrs": {"daily_returns": [val * 0.01] * 50},
                "params": {
                    "TRIGGER_THRESHOLD_PCT": 15.0 + i,
                    "TAKE_PROFIT_MC_PCT": 5.0,
                    "MAX_SQUEEZE_FLOOR": 0.20,
                    "VWAP_CROSS_HWM_PCT": 1.0,
                    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
                    "MAX_PARABOLIC_SQUEEZE": 0.50,
                    "VWAP_BLEED_MULTIPLIER": 1.5,
                    "VWAP_BLEED_TICKS": 10,
                },
                "number": i,
            },
        )()
        mock_trials.append(t)

    # Mock tstat_fn: returns a deterministic high t-stat based on trial value so
    # all trials clear the FDR gate. The key: the SAME tstat_fn is used for both
    # calls, so the only variable under test is the gamma= argument.
    # We use the default tstat_fn (compute_sortino_tstat) with artificially high
    # Sortino values by giving trials large .value scalars. Alternatively, use
    # a custom tstat_fn that returns a known high tstat proportional to trial.value.
    def _deterministic_tstat(series_or_value):
        # Accept either a list (CRRA branch) or a scalar + length (Sortino branch).
        # The default tstat_fn=compute_sortino_tstat is called as tstat_fn(t.value, len(series))
        # in the Sortino branch — so this won't be called directly for the Sortino path.
        # For the CRRA branch it would receive a u_series list.
        if isinstance(series_or_value, list):
            return 5.0 + len(series_or_value) * 0.01  # high t-stat
        return 5.0  # high t-stat

    # Use a high-value tstat_fn to guarantee trial selection passes the FDR gate.
    # Both calls use the SAME tstat_fn — gamma is the only difference.
    result_none = autotuner._haircut_select(
        mock_trials, n_effective=None, tstat_fn=_deterministic_tstat, gamma=None
    )
    result_explicit = autotuner._haircut_select(
        mock_trials, n_effective=None, tstat_fn=_deterministic_tstat, gamma=_EXPECTED_GAMMA
    )

    # Both calls must select the same trial (same params dict).
    # Unpack the 3-tuple: (winning_trial, p_adj_value, tstat_value).
    assert result_none is not None and result_none[0] is not None, (
        "_haircut_select with gamma=None returned (None, ...) — no trial cleared "
        "the FDR gate. Check the mock tstat_fn is producing a high enough t-stat."
    )
    assert result_explicit is not None and result_explicit[0] is not None, (
        f"_haircut_select with gamma={_EXPECTED_GAMMA} returned (None, ...) — "
        "no trial cleared the FDR gate."
    )

    winning_trial_none = result_none[0]
    winning_trial_explicit = result_explicit[0]

    # The selected trial must be the same under both calls — if gamma=None resolves
    # to PHASE1_THEORY_GAMMA, the CRRA U-transform is identical for both.
    assert winning_trial_none.params == winning_trial_explicit.params, (
        f"_haircut_select(gamma=None) selected a different trial than "
        f"_haircut_select(gamma={_EXPECTED_GAMMA}). "
        "The gamma=None fallback is not resolving to float(database.PHASE1_THEORY_GAMMA)."
    )
