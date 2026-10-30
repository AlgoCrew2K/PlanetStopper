"""Import stub for the shared timing emitter; the implementer replaces every body."""

import io
import logging
import sys
import time

TIMING_LOGGER_NAME = "planetstopper.timing"
timing_logger = logging.getLogger(TIMING_LOGGER_NAME)
clock = time.monotonic


def make_daemon_stdout(buffer):
    return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace")


def attach_stream_handler(stream):
    return None


def configure_daemon_output():
    sys.stdout = make_daemon_stdout(sys.stdout.buffer)


def emit(event, *words, **fields):
    return None


def AccessLogMiddleware(wsgi_app):  # noqa: N802
    return wsgi_app
