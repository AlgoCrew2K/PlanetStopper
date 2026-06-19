"""
Cluster 4 — AC-6 / Decision D5: the exponential recency-decay weighting is
REMOVED from the autotuner selection objective.

RED tests, written before the implementation.

Audit M-8: `_collect_sim_returns` appends `guard_alpha · weight` where
`weight = exp(-_GUARD_ALPHA_DECAY_RATE · days_ago)` (decay rate 0.015, ~46-day
half-life), and `compute_sortino_ratio` then divides by plain `n = len(returns)`
— an UNNORMALIZED weighted Sortino.

Decision D5 (resolved 2026-05-22, feature-plans/autotuner-statistics.md): REMOVE
the recency weighting from the selection objective (not normalize). Reasons:
  - Walk-forward CV already tests on the most recent fold — recency relevance is
    structural; an in-objective decay weight double-counts it and biases
    parameter selection toward the last ~6 weeks.
  - Under Decision D3 the Harvey & Liu haircut's t-statistic `Sortino · sqrt(T)`
    is scale-sensitive; an unnormalized weighted Sortino's fold-dependent total
    weight mass would directly distort the selection gate.
  - `_GUARD_ALPHA_DECAY_RATE = 0.015` is an unsourced half-life constant.
Removal fixes all three at once and deletes the constant.

After D5: `_collect_sim_returns` and `run_simulation` append RAW `guard_alpha`;
`compute_sortino_ratio` receives an unweighted series and its plain-`n`
denominator is then correct.

Reference: feature-plans/autotuner-statistics.md, Decision D5; audit M-8.
"""

from __future__ import annotations

import ast
import contextlib
import io
import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


def _import_autotuner():
    import autotuner

    return autotuner


def _autotuner_tree() -> ast.Module:
    return ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))


# ===========================================================================
# D5 — the decay-rate constant and its consumption are gone
# ===========================================================================


def test_guard_alpha_decay_rate_constant_is_deleted():
    """
    D5: `_GUARD_ALPHA_DECAY_RATE` must no longer exist in autotuner.py.

    It is the unsourced 0.015 half-life constant. With recency weighting removed
    it has no consumer; an unused named constant is still dead code.
    """
    autotuner = _import_autotuner()
    assert not hasattr(autotuner, "_GUARD_ALPHA_DECAY_RATE"), (
        "autotuner._GUARD_ALPHA_DECAY_RATE must be DELETED (Decision D5) — the "
        "recency-decay weighting is removed from the selection objective."
    )

    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    assert "_GUARD_ALPHA_DECAY_RATE" not in src, (
        "autotuner.py still references `_GUARD_ALPHA_DECAY_RATE`. D5 removes the "
        "constant and every use of it."
    )


def test_no_exponential_decay_weight_in_autotuner():
    """
    D5: no `math.exp(...)` recency-decay weight may be computed in autotuner.py's
    objective path.

    AST check: there must be no call to `math.exp` AT ALL in autotuner.py whose
    argument contains a unary-minus anywhere — that is the `exp(-rate·days_ago)`
    decay-weight shape regardless of how the minus binds (`-rate * days_ago`
    parses as `(-rate) * days_ago`, so the USub is nested, not at the top of the
    argument). The objective must append raw guard-alpha; the autotuner has no
    other legitimate `math.exp` use.
    """
    tree = _autotuner_tree()

    offending: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exp"
            and node.args
        ):
            arg_has_usub = any(
                isinstance(sub, ast.UnaryOp) and isinstance(sub.op, ast.USub)
                for sub in ast.walk(node.args[0])
            )
            if arg_has_usub:
                offending.append(f"line {getattr(node, 'lineno', '?')}: math.exp(<...-...>)")

    assert not offending, (
        "autotuner.py still computes an exponential decay weight "
        "(`math.exp(-rate·days_ago)`). D5 removes the recency weighting from the "
        "selection objective. Offending:\n  " + "\n  ".join(offending)
    )


def test_days_ago_decay_weight_is_not_applied_to_guard_alpha():
    """
    D5: `days_ago` must not drive a multiplicative weight on guard-alpha.

    The pre-D5 code computes `days_ago = (current_dt - date).days` then
    `weight = exp(-rate · days_ago)` and appends `guard_alpha · weight`. AST
    check: no name containing "weight" may be multiplied with a guard-alpha
    name in the objective. After D5 the appended value is raw `guard_alpha`.
    """
    tree = _autotuner_tree()

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            names = {s.id.lower() for s in (node.left, node.right) if isinstance(s, ast.Name)}
            has_weight = any("weight" in n for n in names)
            has_alpha = any("guard_alpha" in n or n == "alpha" for n in names)
            if has_weight and has_alpha:
                offending.append(f"line {getattr(node, 'lineno', '?')}: guard-alpha × weight")

    assert not offending, (
        "autotuner.py still multiplies guard-alpha by a recency weight. D5 "
        "removes the weighting — the objective must append RAW guard_alpha. "
        "Offending:\n  " + "\n  ".join(offending)
    )


# ===========================================================================
# AC-6 behavioural — _collect_sim_returns appends raw guard-alpha
# ===========================================================================


def test_collect_sim_returns_value_is_independent_of_day_age(monkeypatch):
    """
    AC-6 / D5: a triggered day's contribution to `_collect_sim_returns` must NOT
    depend on how many days in the past the day is.

    Pre-D5, an identical guard-alpha on an OLDER day produced a SMALLER appended
    value (the decay weight). After D5 the appended value is raw guard-alpha —
    the same triggered outcome contributes the same value regardless of date.

    Construction: the SAME two-tick triggering day is replayed twice, once dated
    2 days before `current_date` and once dated 60 days before. The exit fires on
    tick 0 (return 3.0) and the day's EOD is tick 1 (return 5.0), so
    `guard_alpha = triggered_return - eod_return = 3.0 - 5.0 = -2.0` — a NON-ZERO
    value, so a decay weight would actually change the appended number. With the
    decay weight the two `_collect_sim_returns` outputs would differ; after D5
    they must be IDENTICAL.

    The math-engine exit helpers are stubbed so the exit fires deterministically
    on tick 0; the deviation_dict penalty is 0.0 so the appended value is the
    pure guard-alpha. Tolerance 1e-9 absolute — after D5 the values are
    bit-identical.
    """
    from unittest.mock import patch

    autotuner = _import_autotuner()

    # Tick 0 triggers via the VWAP stub (vwap_diff -9.9); tick 1 is the EOD bar
    # with a DIFFERENT return so guard_alpha = 3.0 - 5.0 = -2.0 is non-zero — a
    # decay weight would scale it, an unweighted append would not.
    ticks = [
        {"return": 3.0, "vwap_diff": -9.9, "vol": 1.0, "mc_prob": 50.0, "valid_vwap_weight": 1.0},
        {"return": 5.0, "vwap_diff": 0.0, "vol": 1.0, "mc_prob": 50.0, "valid_vwap_weight": 1.0},
    ]
    deviation_dict = {
        "VWAP Breakdown": 0.0,
        "VWAP Bleed Cut": 0.0,
        "Take-Profit": 0.0,
        "Trailing Stop": 0.0,
    }

    def _collect_for_date(day_date: str, current_date: str) -> list:
        history = {"sym-A": {day_date: list(ticks)}}
        with (
            patch(
                "autotuner.math_engine.compute_para_arm_decision",
                side_effect=lambda **kw: (0.0, False),
            ),
            patch(
                "autotuner.math_engine.compute_time_squeeze_decay",
                side_effect=lambda tr: (1.5, 0.5),
            ),
            patch(
                "autotuner.math_engine.compute_active_trailing_stop",
                side_effect=lambda *a, **kw: 5.0,
            ),
            patch(
                "autotuner.math_engine.compute_breakeven_update",
                side_effect=lambda *a, **kw: (a[3], a[4], a[2]),
            ),
            patch(
                "autotuner.math_engine.compute_tp_confirmation",
                side_effect=lambda **kw: (kw.get("tp_armed", False), 0, False),
            ),
            patch(
                "autotuner.math_engine.compute_exit_confirmation",
                side_effect=lambda **kw: (0, False),
            ),
            patch(
                "autotuner.math_engine.compute_vwap_bleed_arm_threshold",
                side_effect=lambda *a, **kw: -100.0,
            ),
            patch(
                "autotuner.math_engine.compute_vwap_breakdown_update",
                side_effect=lambda **kw: (
                    (0, 0, True, False)
                    if kw.get("weighted_vwap_diff", 0.0) <= -9.0
                    else (0, 0, False, False)
                ),
            ),
            patch("autotuner._replay_grace_minutes", return_value=0),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return autotuner._collect_sim_returns(
                {}, history, ["sym-A"], current_date, deviation_dict
            )

    current_date = "2026-05-10"
    recent = _collect_for_date("2026-05-08", current_date)  # 2 days ago
    old = _collect_for_date("2026-03-11", current_date)  # ~60 days ago

    assert len(recent) == 1 and len(old) == 1, (
        "Both replays must produce exactly one triggered-day observation; "
        f"got recent={recent!r}, old={old!r}."
    )
    assert recent[0] == pytest.approx(old[0], abs=1e-9), (
        f"AC-6 / D5: a triggered day's contribution must be independent of its "
        f"age. The same guard-alpha on a 2-day-old vs a 60-day-old day produced "
        f"different values (recent={recent[0]!r}, old={old[0]!r}) — the "
        f"recency-decay weight is still applied. D5 requires raw guard-alpha."
    )


# ===========================================================================
# AC-6 — compute_sortino_ratio scale-invariance regression guard
# ===========================================================================


def test_sortino_ratio_is_scale_invariant():
    """
    AC-6 regression guard: `compute_sortino_ratio` must be invariant to a uniform
    rescaling of its input series.

    The Sortino ratio is `mean(r) / downside_deviation(r)` — numerator and
    denominator both scale linearly with the series, so the ratio is scale-free.
    This property protected nothing while the inputs were pre-weighted by an
    unnormalized decay weight; with D5 removing the weighting it must hold cleanly
    and it guards the haircut's scale-sensitive t-stat against any future scale
    regression in the Sortino.

    Tolerance: 1e-9 absolute — the scale factors cancel exactly.
    """
    autotuner = _import_autotuner()

    series = [2.0, -1.0, 3.0, -0.5, 1.5, -2.0]
    base = autotuner.compute_sortino_ratio(series)

    for factor in (0.5, 2.0, 10.0):
        scaled = autotuner.compute_sortino_ratio([r * factor for r in series])
        assert scaled == pytest.approx(base, abs=1e-9), (
            f"compute_sortino_ratio is not scale-invariant: scaling the series "
            f"by {factor} changed the Sortino from {base!r} to {scaled!r}. The "
            f"ratio's numerator and denominator both scale linearly — they must "
            f"cancel."
        )


def test_sortino_ratio_degenerate_cases_preserved():
    """
    AC-6 regression guard: removing the recency weighting must not disturb
    `compute_sortino_ratio`'s degenerate-case contract.

      - empty series  -> 0.0
      - zero downside (all returns >= target) -> the 1e6 sentinel (finite, so
        Optuna TPE and the sentinel filter still work).
    """
    autotuner = _import_autotuner()

    assert autotuner.compute_sortino_ratio([]) == 0.0, (
        "compute_sortino_ratio([]) must return 0.0 (empty-series contract)."
    )

    all_upside = autotuner.compute_sortino_ratio([1.0, 2.0, 3.0, 0.5])
    assert all_upside == pytest.approx(1e6, abs=1.0), (
        f"compute_sortino_ratio of an all-non-downside series must return the "
        f"1e6 zero-downside sentinel; got {all_upside!r}."
    )
    assert math.isfinite(all_upside), (
        "The zero-downside sentinel must be finite so Optuna TPE and "
        "filter_sortino_sentinels keep working."
    )
