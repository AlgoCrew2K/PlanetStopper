"""Engine cycle sequences recorded at the reference and compared at GREEN (hygiene-waves-3-4, S-5.4).

``run_sequences`` drives real ``alpha_bot_execution.main()`` cycles through the harness of
``tests/execution/test_regime_label_session_read.py`` and returns, per scenario and tick, the
decision-relevant ``bot_state`` projection, the exit alerts sent and the sell calls made. The
console is NOT part of the record: the dip line is the one thing the plan changes, and it is
checked on its own in ``test_dip_line_confirmation_count.py``.

Live execution is switched on with ``execute_sell_to_cash`` replaced by a recorder, so an outgoing
order is observable as a recorded call and nothing reaches Composer.
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import patch

import alpha_bot_execution
import math_engine
from tests.execution.test_regime_label_session_read import (
    _ABOVE_STOP_PCT,
    _D,
    _QUALIFYING_PCT,
    _projection,
    _save,
)

LABELS = (None, "trending", "mean-reverting", "high-vol")
# dips, an above-stop recovery, then enough qualifying ticks to fire under every label
DIP_SEQUENCE = (0.47, 0.47, _ABOVE_STOP_PCT, 0.47, 0.47, 0.47, 0.47, 0.47, 0.47)
TP_PROBS = (1.0, 1.0, 7.0, 7.0, 7.0, 7.0, 7.0, 7.0)


def _clear_labels() -> None:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        conn.execute("DELETE FROM regime_label_cache")
        conn.commit()
    finally:
        conn.close()


def _tick_record(engine, sells: list) -> dict:
    sym = engine.sym()
    return {
        "state": _projection(sym),
        "alerts": engine.env["reporting"].send_discord_alert.call_count,
        "sells": [list(c.args) for c in sells],
    }


def run_sequences(engine) -> dict:
    out: dict = {}
    for label in LABELS:
        _clear_labels()
        engine.seed()
        engine.env["reporting"].send_discord_alert.reset_mock()
        if label is not None:
            _save(label, _D)
        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
            patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True) as sell,
        ):
            ticks = []
            for pct in DIP_SEQUENCE:
                engine.tick(pct)
                ticks.append(_tick_record(engine, sell.call_args_list))
        out[f"dip_sequence__{label}"] = ticks

    for label in (None, "mean-reverting"):
        _clear_labels()
        engine.seed()
        engine.env["reporting"].send_discord_alert.reset_mock()
        if label is not None:
            _save(label, _D)
        feed = iter(TP_PROBS)
        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
            patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True) as sell,
            patch.object(math_engine, "run_monte_carlo", side_effect=lambda *a, **k: next(feed)),
        ):
            ticks = []
            for _ in TP_PROBS:
                engine.tick(_ABOVE_STOP_PCT)
                ticks.append(_tick_record(engine, sell.call_args_list))
        out[f"take_profit_sequence__{label}"] = ticks
    return out
