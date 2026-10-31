"""Test helpers for the timing log: a capture of its logger and a fake clock.

A test helper, not a fixture: nothing here is producer data. ``capture_timing_records`` isolates
the ``planetstopper.timing`` logger for one block (its handlers, level and nothing else are
restored on exit); ``FakeClock`` replaces ``timing_log.clock`` and moves only when a test
advances it, so every duration a test asserts is a number the test chose.

A rendered line is ``<event> <word>... <key>=<value>...``. ``parse_line`` splits it the way a
reader of the journal would: the words come first (a path such as ``/api/state?account=***`` is
a word, because a field key never starts with a slash), then the ``key=value`` fields.
"""

from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

TIMING_LOGGER_NAME = "planetstopper.timing"
_FIELD = re.compile(r"^([a-z][a-z_]*)=(.*)$")


@dataclass(frozen=True)
class ParsedLine:
    text: str
    event: str
    words: tuple[str, ...]
    fields: dict[str, str] = field(default_factory=dict)


def parse_line(text: str) -> ParsedLine:
    tokens = text.split(" ")
    words: list[str] = []
    fields: dict[str, str] = {}
    for token in tokens[1:]:
        match = _FIELD.match(token)
        if match:
            fields[match.group(1)] = match.group(2)
        else:
            words.append(token)
    return ParsedLine(text=text, event=tokens[0], words=tuple(words), fields=fields)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class TimingCapture:
    """The records the timing logger received inside a ``capture_timing_records`` block."""

    def __init__(self, handler: _ListHandler) -> None:
        self._handler = handler

    @property
    def records(self) -> list[logging.LogRecord]:
        return list(self._handler.records)

    @property
    def lines(self) -> list[str]:
        return [r.getMessage() for r in self._handler.records]

    def of(self, event: str) -> list[ParsedLine]:
        """The parsed lines whose first word is *event*, in the order they were logged."""
        return [parse_line(m) for m in self.lines if m.split(" ", 1)[0] == event]


def _is_pytest_capture(handler: logging.Handler) -> bool:
    return type(handler).__module__.startswith("_pytest")


def restore_handlers(logger: logging.Logger, saved: list[logging.Handler]) -> None:
    """Put back the handlers *saved* from before a block, except pytest's own capture handlers.

    From pytest 9 the log capture attaches a handler to every logger that does not propagate,
    for the length of each phase (setup, call, teardown), and removes it when the phase ends.
    A list copied earlier holds the handler of a phase that has since ended, so putting it back
    leaves that phase's handler on the logger for the rest of the run and every later line is
    stored again. The handlers pytest holds now stay as they are; pytest removes them itself.
    """
    live_capture = [h for h in logger.handlers if _is_pytest_capture(h)]
    logger.handlers = [h for h in saved if not _is_pytest_capture(h)] + live_capture


@contextmanager
def capture_timing_records():
    logger = logging.getLogger(TIMING_LOGGER_NAME)
    saved_handlers, saved_level = list(logger.handlers), logger.level
    handler = _ListHandler()
    logger.handlers = [handler, *(h for h in saved_handlers if _is_pytest_capture(h))]
    logger.setLevel(logging.INFO)
    try:
        yield TimingCapture(handler)
    finally:
        restore_handlers(logger, saved_handlers)
        logger.setLevel(saved_level)


@contextmanager
def capture_root_records():
    """Every record that reaches a handler on the root logger inside the block.

    ``caplog`` cannot answer "did this line reach the root handlers": from pytest 9 its handler
    is also attached to every non-propagating logger, so it sees lines that never propagate.
    This handler sits on the root logger only, which is what a propagating line would reach.
    """
    root = logging.getLogger()
    saved_level = root.level
    handler = _ListHandler()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        root.removeHandler(handler)
        root.setLevel(saved_level)


class FakeClock:
    """A monotonic clock a test advances. ``per_thread`` gives each thread its own reading.

    Durations are asserted as whole milliseconds, so tests advance by dyadic fractions of a
    second (0.125, 0.25, 0.5, 30.0) that the float sum represents exactly.
    """

    def __init__(self, start: float = 1000.0, *, per_thread: bool = False) -> None:
        self._start = start
        self._per_thread = per_thread
        self._shared = start
        self._local = threading.local()
        self._lock = threading.Lock()

    def __call__(self) -> float:
        if self._per_thread:
            return getattr(self._local, "now", self._start)
        with self._lock:
            return self._shared

    def advance(self, seconds: float) -> None:
        if self._per_thread:
            self._local.now = getattr(self._local, "now", self._start) + seconds
            return
        with self._lock:
            self._shared += seconds
