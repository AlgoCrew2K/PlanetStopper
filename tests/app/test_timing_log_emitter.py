"""RED tests: the shared timing emitter renders one safe line per event.

``timing_log.emit(event, *words, **fields)`` is the one writer of every timing line (cycle,
composer, access). Contract:

  - The line is ``<event> <word>... <key>=<value>...`` on the ``planetstopper.timing`` logger at
    INFO, with the logger outside the ``alphabot`` hierarchy and not propagating, so no root
    handler can print it a second time.
  - Every word and value passes one sanitizer that percent-encodes anything that could split,
    forge or extend a line; ``None`` renders as ``-``. The rendered text is a ``%s`` argument,
    never the logging format string.
  - ``emit`` never raises into its caller.
  - ``attach_stream_handler(stream)`` is idempotent and prefixes each line with its own UTC time.
"""

from __future__ import annotations

import io
import logging
import re
from urllib.parse import unquote

import pytest

import timing_log
from tests._timing_capture import parse_line


class TestLoggerPlacement:
    def test_the_logger_is_outside_the_alphabot_hierarchy_and_does_not_propagate(self):
        assert timing_log.TIMING_LOGGER_NAME == "planetstopper.timing"
        assert timing_log.timing_logger is logging.getLogger("planetstopper.timing")
        assert timing_log.timing_logger.propagate is False

    def test_the_logger_emits_info(self):
        assert timing_log.timing_logger.getEffectiveLevel() == logging.INFO

    def test_a_line_never_reaches_the_root_handlers(self, timing_records, caplog):
        with caplog.at_level(logging.DEBUG):
            timing_log.emit("probe", "ok", n=1)

        assert timing_records.lines == ["probe ok n=1"]
        assert [r for r in caplog.records if r.name.startswith("planetstopper")] == []


class TestRendering:
    def test_words_then_fields_in_the_order_given(self, timing_records):
        timing_log.emit("composer", "GET", "/portfolio/{account}/x", status=200, ms=640)

        assert timing_records.lines == ["composer GET /portfolio/{account}/x status=200 ms=640"]

    def test_none_renders_as_a_dash(self, timing_records):
        timing_log.emit("cycle", exit_code=None)

        assert timing_records.lines == ["cycle exit_code=-"]

    def test_the_record_is_logged_at_info(self, timing_records):
        timing_log.emit("probe", "ok")

        (record,) = timing_records.records
        assert record.levelno == logging.INFO


class TestSanitizer:
    HOSTILE = "a b%c\r\nd\te\x00f\u00e9\u2192g"

    def test_a_value_cannot_split_or_forge_a_line(self, timing_records):
        timing_log.emit("probe", "ok", note=self.HOSTILE)

        (text,) = timing_records.lines
        assert text.isascii() and text.isprintable(), f"unsafe characters survived: {text!r}"
        parsed = parse_line(text)
        assert list(parsed.fields) == ["note"], "the value must stay one field"
        assert unquote(parsed.fields["note"]) == self.HOSTILE, "the encoding must be reversible"

    @pytest.mark.parametrize(
        ("raw", "encoded"),
        [(" ", "%20"), ("%", "%25"), ("\r", "%0D"), ("\n", "%0A")],
    )
    def test_each_line_splitting_character_is_percent_encoded(self, timing_records, raw, encoded):
        timing_log.emit("probe", "ok", note=f"x{raw}y")

        (text,) = timing_records.lines
        assert parse_line(text).fields["note"].upper() == f"x{encoded}y".upper()

    def test_a_word_is_sanitized_like_a_value(self, timing_records):
        timing_log.emit("probe", "/path with space\r\nforged event=1")

        (text,) = timing_records.lines
        assert "\n" not in text and "\r" not in text
        assert parse_line(text).fields == {}, "a forged key=value must not become a field"

    def test_the_text_is_never_used_as_the_format_string(self, timing_records):
        timing_log.emit("probe", "100%s", note="%d%d%(x)s")

        (text,) = timing_records.lines
        parsed = parse_line(text)
        assert [unquote(w) for w in parsed.words] == ["100%s"]
        assert unquote(parsed.fields["note"]) == "%d%d%(x)s"


class TestNeverRaises:
    def test_an_object_whose_text_conversion_fails_is_swallowed_and_later_lines_still_log(
        self, timing_records
    ):
        class Unprintable:
            def __str__(self):
                raise RuntimeError("no text for you")

        assert timing_log.emit("probe", Unprintable(), n=Unprintable()) is None

        timing_log.emit("probe", "after")
        assert timing_records.lines[-1] == "probe after"

    def test_a_logging_fault_is_swallowed_and_later_lines_still_log(self, timing_records):
        def fault(_record):
            raise RuntimeError("filter fault")

        timing_log.timing_logger.addFilter(fault)
        try:
            assert timing_log.emit("probe", "during") is None
        finally:
            timing_log.timing_logger.removeFilter(fault)

        timing_log.emit("probe", "after")
        assert timing_records.lines[-1] == "probe after"


class TestStreamHandler:
    @pytest.fixture()
    def logger_state(self):
        logger = timing_log.timing_logger
        saved = list(logger.handlers)
        yield logger
        logger.handlers = saved

    def test_attaching_the_same_stream_twice_writes_each_line_once(self, logger_state):
        stream = io.StringIO()

        timing_log.attach_stream_handler(stream)
        timing_log.attach_stream_handler(stream)
        timing_log.emit("probe", "ok", n=1)

        lines = stream.getvalue().splitlines()
        assert len(lines) == 1, f"one handler per stream, got: {lines!r}"

    def test_each_line_is_prefixed_with_its_own_utc_time(self, logger_state):
        stream = io.StringIO()
        timing_log.attach_stream_handler(stream)

        timing_log.emit("probe", "ok", n=1)

        (line,) = stream.getvalue().splitlines()
        assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z\s+probe ok n=1$", line), line
