"""
RED tests for float('-inf') / float('inf') / float('nan') coercion in
database._autotune_run_row_to_dict.

Bug: /api/autotune-runs returns HTTP 200 but JSON body contains literal
`-Infinity` at position ~999 (confirmed by operator curl + python probe).
Python's json.dumps emits `Infinity`/`-Infinity`/`NaN` for float sentinels;
JS JSON.parse rejects these per RFC 8259 §6 — result: "No number after minus
sign in JSON at position 999" in the browser's recent-runs panel.

Root cause: _autotune_run_row_to_dict passes float('-inf') sentinel values
from the DB directly into the dict without coercion. Optuna trials that
produce no valid trades return float('-inf') as alpha/Sharpe sentinels.

Fix: add _finite_or_none(x) helper in database.py; apply to every numeric
field in _autotune_run_row_to_dict. Single source of truth — all consumers
(Discord reporting, dashboard) benefit automatically.

Acceptance criteria:
  AC-INF.1 : _autotune_run_row_to_dict with float('-inf') in any numeric
             field returns None for that field.
  AC-INF.2 : json.dumps(get_all_autotune_runs()) succeeds without allow_nan.
  AC-INF.3 : json.loads(json.dumps(row)) round-trips cleanly (no JS-parse error).
  AC-INF.4 : Finite values pass through unchanged.
  AC-INF.5 : float('nan') and float('inf') (positive) also coerced to None.

Fixture provenance:
  tests/fixtures/database/autotune_inf_row.json
  Hand-authored. Schema-derived from database._autotune_run_row_to_dict
  14-column SELECT shape. PA-18.
"""

from __future__ import annotations

import json
import math
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures" / "database" / "autotune_inf_row.json"
)

_NUMERIC_FIELDS = (
    "oos_alpha",
    "train_alpha",
    "fallback_oos_alpha",
    "default_oos_alpha",
    "selection_tstat",
    "naive_sharpe",
    "validation_sharpe",
    "frozen_eval_sharpe",
    "sortino_sentinel_pct",
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_raw_row(overrides: dict) -> tuple:
    """Build a 14-element tuple matching _autotune_run_row_to_dict's SELECT order."""
    defaults = {
        "run_timestamp": "2026-05-16T16:00:01",
        "symphony_id": "sym-alpha",
        "oos_alpha": 1.5,
        "train_alpha": 2.0,
        "baseline_decision": "HOLD",
        "fallback_oos_alpha": 0.5,
        "default_oos_alpha": 2.1,
        "selection_tstat": 0.8,
        "naive_sharpe": 1.1,
        "validation_sharpe": 0.9,
        "frozen_eval_sharpe": None,
        "math_mode": "per_symphony",
        "account_id": "ACC-INDIVIDUAL",
        "sortino_sentinel_pct": None,
    }
    defaults.update(overrides)
    return (
        defaults["run_timestamp"],
        defaults["symphony_id"],
        defaults["oos_alpha"],
        defaults["train_alpha"],
        defaults["baseline_decision"],
        defaults["fallback_oos_alpha"],
        defaults["default_oos_alpha"],
        defaults["selection_tstat"],
        defaults["naive_sharpe"],
        defaults["validation_sharpe"],
        defaults["frozen_eval_sharpe"],
        defaults["math_mode"],
        defaults["account_id"],
        defaults["sortino_sentinel_pct"],
    )


# ===========================================================================
# AC-INF.1: float('-inf') in numeric fields → None in returned dict
# ===========================================================================

class TestNegInfCoercedToNone:
    """
    AC-INF.1: _autotune_run_row_to_dict must coerce float('-inf') to None
    in every numeric field. This is the exact sentinel Optuna emits for
    trials that produce no valid trades.
    """

    @pytest.mark.parametrize("field", [
        "oos_alpha",
        "train_alpha",
        "fallback_oos_alpha",
        "default_oos_alpha",
        "selection_tstat",
        "naive_sharpe",
        "validation_sharpe",
        "frozen_eval_sharpe",
        "sortino_sentinel_pct",
    ])
    def test_neg_inf_field_coerced_to_none(self, field):
        """AC-INF.1: float('-inf') in {field} → None."""
        import database as db_module

        row = _make_raw_row({field: float("-inf")})
        result = db_module._autotune_run_row_to_dict(row)

        assert result[field] is None, (
            f"_autotune_run_row_to_dict must coerce float('-inf') to None in field {field!r}. "
            f"Got {result[field]!r}. "
            "Python json.dumps emits literal -Infinity which JS JSON.parse rejects per RFC 8259."
        )


# ===========================================================================
# AC-INF.5: float('inf') and float('nan') also coerced to None
# ===========================================================================

class TestPosInfAndNanCoercedToNone:
    """
    AC-INF.5: float('inf') and float('nan') must also be coerced to None.
    Both produce non-RFC-8259 JSON tokens (Infinity, NaN) that JS rejects.
    """

    def test_pos_inf_coerced_to_none(self):
        """AC-INF.5: float('inf') in oos_alpha → None."""
        import database as db_module

        row = _make_raw_row({"oos_alpha": float("inf")})
        result = db_module._autotune_run_row_to_dict(row)

        assert result["oos_alpha"] is None, (
            f"float('inf') must be coerced to None. Got {result['oos_alpha']!r}."
        )

    def test_nan_coerced_to_none(self):
        """AC-INF.5: float('nan') in naive_sharpe → None."""
        import database as db_module

        row = _make_raw_row({"naive_sharpe": float("nan")})
        result = db_module._autotune_run_row_to_dict(row)

        assert result["naive_sharpe"] is None, (
            f"float('nan') must be coerced to None. Got {result['naive_sharpe']!r}."
        )


# ===========================================================================
# AC-INF.4: Finite values pass through unchanged
# ===========================================================================

class TestFiniteValuesUnchanged:
    """
    AC-INF.4: Finite float values must not be modified by the coercion.
    """

    def test_finite_oos_alpha_unchanged(self):
        """AC-INF.4: finite oos_alpha value survives round-trip unchanged."""
        import database as db_module

        row = _make_raw_row({"oos_alpha": 3.14})
        result = db_module._autotune_run_row_to_dict(row)

        assert result["oos_alpha"] == pytest.approx(3.14, rel=1e-9), (
            f"Finite value 3.14 must pass through unchanged. Got {result['oos_alpha']!r}."
        )

    def test_none_field_unchanged(self):
        """AC-INF.4: None (SQL NULL) must remain None — not coerced or raised."""
        import database as db_module

        row = _make_raw_row({"frozen_eval_sharpe": None})
        result = db_module._autotune_run_row_to_dict(row)

        assert result["frozen_eval_sharpe"] is None, (
            f"SQL NULL (Python None) must remain None. Got {result['frozen_eval_sharpe']!r}."
        )

    def test_zero_field_unchanged(self):
        """AC-INF.4: 0.0 is a valid finite value — must not be coerced to None."""
        import database as db_module

        row = _make_raw_row({"selection_tstat": 0.0})
        result = db_module._autotune_run_row_to_dict(row)

        assert result["selection_tstat"] == pytest.approx(0.0, abs=1e-12), (
            f"0.0 is finite — must not be coerced to None. Got {result['selection_tstat']!r}."
        )

    def test_string_fields_not_coerced(self):
        """AC-INF.4: string fields (baseline_decision, math_mode, account_id) must be
        returned verbatim — _finite_or_none must never be applied to non-numeric columns.
        Guards against a too-broad application of the coercion helper that would turn
        string sentinels like 'HOLD' or 'per_symphony' into None.
        """
        import database as db_module

        row = _make_raw_row({
            "baseline_decision": "HOLD",
            "math_mode": "per_symphony",
            "account_id": "ACC-INDIVIDUAL",
        })
        result = db_module._autotune_run_row_to_dict(row)

        assert result["baseline_decision"] == "HOLD", (
            f"String field baseline_decision must not be coerced. Got {result['baseline_decision']!r}."
        )
        assert result["math_mode"] == "per_symphony", (
            f"String field math_mode must not be coerced. Got {result['math_mode']!r}."
        )
        assert result["account_id"] == "ACC-INDIVIDUAL", (
            f"String field account_id must not be coerced. Got {result['account_id']!r}."
        )


# ===========================================================================
# AC-INF.2: json.dumps succeeds without allow_nan
# ===========================================================================

class TestJsonDumpsWithoutAllowNan:
    """
    AC-INF.2: json.dumps(row_dict) must not raise ValueError when the row
    contains formerly-infinite fields. This is the direct browser-failure path:
    Flask's jsonify uses json.dumps without allow_nan=True, so any surviving
    float('-inf') raises ValueError in Python AND emits non-parseable JSON.
    """

    def test_row_with_neg_inf_is_json_serializable_after_coercion(self):
        """AC-INF.2: dict from row with float('-inf') fields serializes without allow_nan."""
        import database as db_module

        row = _make_raw_row({
            "oos_alpha": float("-inf"),
            "train_alpha": float("-inf"),
            "selection_tstat": float("-inf"),
            "naive_sharpe": float("nan"),
            "validation_sharpe": float("inf"),
        })
        result = db_module._autotune_run_row_to_dict(row)

        try:
            # allow_nan=False mirrors strict RFC 8259 compliance — Flask's jsonify
            # emits non-finite floats as literal Infinity/-Infinity/NaN which
            # JS JSON.parse rejects. Python's default allow_nan=True masks the bug.
            serialized = json.dumps(result, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise AssertionError(
                f"json.dumps(allow_nan=False) raised {type(exc).__name__}: {exc}. "
                "_autotune_run_row_to_dict must coerce non-finite floats to None "
                "so the dict is JSON-serializable without allow_nan. "
                "Browser receives literal -Infinity/NaN and JSON.parse rejects it."
            ) from exc

        assert serialized  # non-empty string


# ===========================================================================
# AC-INF.3: json.loads(json.dumps(row)) round-trips cleanly
# ===========================================================================

class TestJsonRoundTrip:
    """
    AC-INF.3: Simulates the browser: json.loads on the serialized row must
    succeed. This is the exact failure path the operator observed.
    """

    def test_row_with_inf_round_trips_through_json(self):
        """AC-INF.3: json.loads(json.dumps(row)) completes without error."""
        import database as db_module

        row = _make_raw_row({
            "oos_alpha": float("-inf"),
            "fallback_oos_alpha": float("-inf"),
        })
        result = db_module._autotune_run_row_to_dict(row)

        try:
            parsed = json.loads(json.dumps(result))
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"json.loads raised JSONDecodeError: {exc}. "
                "This reproduces the browser failure: 'No number after minus sign'. "
                "Coerce float('-inf') → None in _autotune_run_row_to_dict."
            ) from exc

        # The formerly-inf fields must be null in the round-tripped result
        assert parsed["oos_alpha"] is None, (
            f"After round-trip, oos_alpha must be null. Got {parsed['oos_alpha']!r}."
        )
        assert parsed["fallback_oos_alpha"] is None, (
            f"After round-trip, fallback_oos_alpha must be null. Got {parsed['fallback_oos_alpha']!r}."
        )
