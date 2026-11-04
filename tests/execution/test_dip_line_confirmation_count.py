"""The engine's dip line names the confirmation count in force (hygiene-waves-3-4, AC-5).

When an armed, untriggered symphony's below-stop count goes 0 to 1 the engine prints a warning
line. At the reference it ends ``Awaiting 3-tick confirmation...`` whatever the regime label; the
slice makes it name ``_regime_exit_ticks``, the count handed to ``compute_exit_confirmation`` on
that tick (S-5.1 to S-5.3), and nothing else changes (S-5.4 to S-5.6).

Harness: the real ``alpha_bot_execution.main()`` cycles of
``tests/execution/test_regime_label_session_read.py`` (``_Engine`` and its ``engine`` fixture):
Composer, Alpaca, Discord and the clock are faked there, label rows are written by the real
``database.save_regime_label`` into the per-test database, stdout comes through ``capsys``.

Expected values: the count of a label is the real ``math_engine.apply_regime_exit_adjustment``
result; the count handed to the stop is recorded off the real ``compute_exit_confirmation`` call;
the transitions of S-5.4 are the reference's, recorded by
``tests/fixtures/hygiene_waves_3_4/engine_math/produce_ref_records.py``.

At the reference only the default-count labels pass S-5.1 to S-5.3; the labels that change the
count (2 and 5) fail until the line names the count in force.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import math_engine
from tests.execution._dip_sequence import run_sequences
from tests.execution.test_main_pipeline import (
    _SYMPHONY_NAME,
    patched_environment,  # noqa: F401
)
from tests.execution.test_regime_label_session_read import (  # noqa: F401
    _ABOVE_STOP_PCT,
    _D,
    _NEXT,
    _PREV,
    _QUALIFYING_PCT,
    _save,
    engine,
)

REF_SEQUENCES = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "hygiene_waves_3_4"
    / "engine_math"
    / "engine_ref_sequences.json"
)

_DIP_PREFIX = f"  ⚠️ {_SYMPHONY_NAME[:35]} dipped below stop. Awaiting "
_DIP_LINE = re.compile(re.escape(_DIP_PREFIX) + r"(\d+)-tick confirmation\.\.\.$")
_REF_FORMAT = f"{_DIP_PREFIX}3-tick confirmation..."

# label cases of S-5.2: (id, label to save or None, session date of the row)
_LABEL_CASES = [
    ("trending", "trending", _D),
    ("mean_reverting", "mean-reverting", _D),
    ("high_vol", "high-vol", _D),
    ("no_row", None, None),
    ("row_for_another_session", "trending", _PREV),
    ("row_for_the_next_session", "mean-reverting", _NEXT),
    ("unrecognized_label", "sideways", _D),
]


def _expected_count(label: str | None, row_date: str | None) -> int:
    effective = label if row_date == _D else None
    return math_engine.apply_regime_exit_adjustment(effective, math_engine.EXIT_CONFIRM_TICKS)


def _dip_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "dipped below stop." in ln]


def _clear_labels() -> None:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        conn.execute("DELETE FROM regime_label_cache")
        conn.commit()
    finally:
        conn.close()


class _RecordConfirmationTicks:
    """Wraps the real ``compute_exit_confirmation``, recording ``exit_confirm_ticks``."""

    def __init__(self):
        self.ticks: list[int] = []
        self._real = math_engine.compute_exit_confirmation

    def __call__(self, *args, **kwargs):
        self.ticks.append(kwargs["exit_confirm_ticks"])
        return self._real(*args, **kwargs)


class TestDipLineNamesTheCountInForce:
    @pytest.mark.parametrize(("case", "label", "row_date"), _LABEL_CASES)
    def test_dip_line_names_the_count_the_label_gives_that_tick(
        self,
        engine,
        capsys,
        case,
        label,
        row_date,  # noqa: F811
    ):
        if label is not None:
            _save(label, row_date)
        capsys.readouterr()
        engine.tick(_QUALIFYING_PCT)
        lines = _dip_lines(capsys.readouterr().out)
        assert len(lines) == 1, f"{case}: expected one dip line, got {len(lines)}"
        match = _DIP_LINE.match(lines[0])
        assert match, f"{case}: the line does not keep the unchanged prefix and suffix"
        assert int(match.group(1)) == _expected_count(label, row_date)

    def test_mean_reverting_session_prints_the_five_tick_line_of_the_plan_example(
        self,
        engine,
        capsys,  # noqa: F811
    ):
        _save("mean-reverting", _D)
        capsys.readouterr()
        engine.tick(_QUALIFYING_PCT)
        assert _DIP_PREFIX + "5-tick confirmation..." in capsys.readouterr().out

    @pytest.mark.parametrize(("case", "label", "row_date"), _LABEL_CASES)
    def test_printed_count_and_the_stop_argument_come_from_one_value_on_that_tick(
        self,
        engine,
        capsys,
        case,
        label,
        row_date,  # noqa: F811
    ):
        if label is not None:
            _save(label, row_date)
        recorder = _RecordConfirmationTicks()
        capsys.readouterr()
        with patch.object(math_engine, "compute_exit_confirmation", side_effect=recorder):
            engine.tick(_QUALIFYING_PCT)
        (line,) = _dip_lines(capsys.readouterr().out)
        printed = int(_DIP_LINE.match(line).group(1))
        assert len(recorder.ticks) == 1
        assert printed == recorder.ticks[0]


class TestOnlyTheCountDiffers:
    def _stdout_and_calls(self, engine, capsys, label):  # noqa: F811
        _clear_labels()
        engine.seed()
        engine.env["db"].log_symphony_event.reset_mock()
        if label is not None:
            _save(label, _D)
        capsys.readouterr()
        engine.tick(_QUALIFYING_PCT)
        out = capsys.readouterr().out
        events = [tuple(c.args) for c in engine.env["db"].log_symphony_event.call_args_list]
        return out, events

    @pytest.mark.parametrize("label", ["trending", "mean-reverting", "high-vol"])
    def test_with_the_count_masked_the_console_and_events_equal_the_no_row_run(
        self,
        engine,
        capsys,
        label,  # noqa: F811
    ):
        base_out, base_events = self._stdout_and_calls(engine, capsys, None)
        out, events = self._stdout_and_calls(engine, capsys, label)

        def mask(text: str) -> str:
            # the wake-up banner carries the wall-clock time of the run
            text = re.sub(r"\[\d\d:\d\d:\d\d\]", "[HH:MM:SS]", text)
            return re.sub(r"Awaiting \d+-tick", "Awaiting N-tick", text)

        assert mask(out) == mask(base_out)
        assert events == base_events  # no event, log or database write is added

    def test_the_line_with_the_count_masked_equals_the_reference_line(self, engine, capsys):  # noqa: F811
        capsys.readouterr()
        engine.tick(_QUALIFYING_PCT)
        (line,) = _dip_lines(capsys.readouterr().out)
        mask = lambda s: re.sub(r"Awaiting \d+-tick", "Awaiting N-tick", s)  # noqa: E731
        assert mask(line) == mask(_REF_FORMAT)


class TestNoRepeatLineWhileCountingUp:
    def test_further_below_stop_ticks_print_no_further_dip_line(self, engine, capsys):  # noqa: F811
        _save("mean-reverting", _D)
        capsys.readouterr()
        lines = []
        for _ in range(4):  # counts 1..4, the stop needs 5
            engine.tick(_QUALIFYING_PCT)
            lines.extend(_dip_lines(capsys.readouterr().out))
        assert len(lines) == 1

    def test_a_recovery_then_a_new_dip_prints_one_new_line(self, engine, capsys):  # noqa: F811
        _save("mean-reverting", _D)
        capsys.readouterr()
        engine.tick(_QUALIFYING_PCT)
        engine.tick(_QUALIFYING_PCT)
        engine.tick(_ABOVE_STOP_PCT)
        engine.tick(_QUALIFYING_PCT)
        assert len(_dip_lines(capsys.readouterr().out)) == 2


class TestDecisionsAreTheReferenceDecisions:
    def test_state_transitions_alerts_and_sell_calls_equal_the_reference_sequences(
        self,
        engine,  # noqa: F811
    ):
        ref = json.loads(REF_SEQUENCES.read_text(encoding="utf-8"))
        now = run_sequences(engine)
        assert set(now) == set(ref)
        for name in ref:
            assert now[name] == ref[name], name

    def test_the_recorded_sequences_exercise_a_fire_a_recovery_and_a_take_profit(self):
        ref = json.loads(REF_SEQUENCES.read_text(encoding="utf-8"))
        fired = [
            any(t["state"]["triggered"] for t in ticks)
            for name, ticks in ref.items()
            if name.startswith("dip_sequence")
        ]
        assert all(fired) and len(fired) == 4
        assert any(
            any(t["sells"] for t in ticks)
            for name, ticks in ref.items()
            if name.startswith("dip_sequence")
        )
        assert any(
            any(t["state"]["tp_armed"] for t in ticks)
            for name, ticks in ref.items()
            if name.startswith("take_profit")
        )
