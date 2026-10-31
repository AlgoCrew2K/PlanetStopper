"""The timing capture helpers hand the timing logger back with the handlers it had.

From pytest 9 the log capture puts a handler on every non-propagating logger for the length of
a phase and removes it when the phase ends. A helper that restores a list of handlers copied
earlier would put a finished phase's handler back for good, so these tests play the phase end
with a stand-in that pytest's own module name marks as a capture handler.
"""

from __future__ import annotations

import logging

import pytest

from tests._timing_capture import TIMING_LOGGER_NAME, capture_timing_records, restore_handlers


class _PhaseCaptureHandler(logging.Handler):
    """Stands in for the handler pytest attaches for one phase."""

    def emit(self, record: logging.LogRecord) -> None:
        pass


_PhaseCaptureHandler.__module__ = "_pytest.logging"


class _DaemonStreamHandler(logging.Handler):
    """Stands in for a handler a test or the daemon attached to the timing logger."""

    def emit(self, record: logging.LogRecord) -> None:
        pass


@pytest.fixture()
def timing_logger():
    logger = logging.getLogger(TIMING_LOGGER_NAME)
    saved = list(logger.handlers)
    yield logger
    logger.handlers = saved


def test_a_phase_handler_removed_by_pytest_inside_the_capture_does_not_come_back(timing_logger):
    phase = _PhaseCaptureHandler()
    timing_logger.addHandler(phase)

    with capture_timing_records():
        timing_logger.removeHandler(phase)

    assert phase not in timing_logger.handlers


def test_a_handler_that_was_not_pytests_is_put_back_after_the_capture(timing_logger):
    daemon = _DaemonStreamHandler()
    timing_logger.addHandler(daemon)

    with capture_timing_records() as capture:
        assert daemon not in timing_logger.handlers, "the capture isolates the logger"
        assert capture.lines == []

    assert daemon in timing_logger.handlers


def test_restore_handlers_keeps_the_phase_handler_pytest_holds_now(timing_logger):
    earlier = _PhaseCaptureHandler()
    current = _PhaseCaptureHandler()
    daemon = _DaemonStreamHandler()
    saved = [daemon, earlier]
    timing_logger.handlers = [current]

    restore_handlers(timing_logger, saved)

    assert timing_logger.handlers == [daemon, current]
