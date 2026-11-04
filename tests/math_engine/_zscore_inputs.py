"""Inputs and a recorder for the z-score characterization (hygiene-waves-3-4, S-3.1).

The three Monte Carlo family functions standardize two kNN features with a z-score
(``run_monte_carlo``, ``compute_portfolio_cvar``, ``compute_regime_match_quality``). Every case
below is deterministic: a seeded generator, a constant history (std 0.0 for both features) or the
existing two-regime history builder of ``tests/math_engine/test_mc_knn_standardization.py`` driven
by its own captured fixture. ``record_outputs`` maps each case to the exact bits of each function's
output (``float.hex``), so a comparison is bitwise.

Shared by ``tests/math_engine/test_zscore_shared_definition.py`` and by the reference-record
capture in ``tests/fixtures/hygiene_waves_3_4/engine_math/``.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import math_engine
from tests.math_engine.test_mc_knn_standardization import (
    _build_constant_history,
    _build_two_regime_history,
)

_KNN_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "math_engine"
    / "mc_knn_standardization"
    / "01_equal_return_opposite_vol_regime.json"
)
_SEED = 20261104
_PATHS = 1500
_NEIGHBORS = 60


def _dated(days: int) -> list[str]:
    start = date(2025, 1, 2)
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


def _random_history(days: int, seed: int, scale: float) -> dict:
    rng = np.random.default_rng(seed)
    history = {}
    for day in _dated(days):
        history[day] = {
            "SPY": {"daily_ret": float(rng.normal(0.0004, scale))},
            "AAA": {"daily_ret": float(rng.normal(0.0006, scale * 1.4))},
            "BBB": {"daily_ret": float(rng.normal(0.0002, scale * 0.8))},
        }
    return history


_HOLDINGS = [
    {"ticker": "AAA", "allocation": 0.6, "last_percent_change": -0.02},
    {"ticker": "BBB", "allocation": 0.4, "last_percent_change": 0.03},
]


def cases() -> list[tuple[str, list, dict, float]]:
    """(name, holdings, history, spy_today_return in percent)."""
    knn = json.loads(_KNN_FIXTURE.read_text(encoding="utf-8"))
    spec = knn["inputs"]["historical_data_spec"]
    two_regime = _build_two_regime_history(
        num_days=spec["num_days"],
        first_block_days=spec["calm_block_days"],
        today_return_decimal=spec["today_return_decimal"],
        first_dispersion=spec["calm_spy_dispersion"],
        second_dispersion=spec["crash_spy_dispersion"],
        first_holding_ret=spec["calm_holding_daily_ret"],
        second_holding_ret=spec["crash_holding_daily_ret"],
    )
    return [
        ("random_calm", _HOLDINGS, _random_history(150, _SEED, 0.006), -0.35),
        ("random_volatile", _HOLDINGS, _random_history(150, _SEED + 1, 0.021), 2.4),
        ("random_extreme_today", _HOLDINGS, _random_history(150, _SEED + 2, 0.009), -7.5),
        ("constant_zero_variance", _HOLDINGS, _build_constant_history(60, 0.001, 0.002), 0.1),
        (
            "two_regime_fixture",
            knn["inputs"]["holdings"],
            two_regime,
            knn["inputs"]["spy_today_return"],
        ),
    ]


def _exact(value):
    """Floats as ``float.hex`` (NaN and inf spelled), containers walked, the rest as is."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return v.hex() if math.isfinite(v) else repr(v)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _exact(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _exact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_exact(v) for v in value]
    return repr(value)


def record_outputs() -> dict:
    """The exact outputs of the three functions for every case, at the code being run."""
    out: dict = {}
    for name, holdings, history, spy_today in cases():
        out[name] = {
            "run_monte_carlo": _exact(
                math_engine.run_monte_carlo(
                    holdings,
                    history,
                    spy_today,
                    simulation_paths=_PATHS,
                    neighbor_k=_NEIGHBORS,
                    seed=7,
                )
            ),
            "compute_portfolio_cvar": _exact(
                math_engine.compute_portfolio_cvar(
                    f"hygiene-zscore-{name}",
                    holdings,
                    history,
                    spy_today,
                    simulation_paths=_PATHS,
                    neighbor_k=_NEIGHBORS,
                )
            ),
            "compute_regime_match_quality": _exact(
                math_engine.compute_regime_match_quality(history, spy_today)
            ),
        }
    return out
