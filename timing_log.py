"""Timing lines for the daemon: one safe, greppable line per cycle, Composer call and request.

Every line is ``<event> <word>... <key>=<value>...`` on the ``planetstopper.timing`` logger. The
logger sits outside the ``alphabot`` hierarchy and does not propagate, so only the stream handler
attached by ``configure_daemon_output`` prints it (once, with its own UTC time). Logging must
never raise into, or block, the caller: ``emit`` swallows every fault.
"""

import io
import logging
import sys
import threading
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl

TIMING_LOGGER_NAME = "planetstopper.timing"
timing_logger = logging.getLogger(TIMING_LOGGER_NAME)
timing_logger.setLevel(logging.INFO)
timing_logger.propagate = False

# Read at call time as ``timing_log.clock()`` so a test can replace it. Monotonic: a wall-clock
# step cannot distort a measured duration.
clock = time.monotonic

_MS_PER_S = 1000  # milliseconds per second
_MASKED_VALUE = "***"
_PRINTABLE_ASCII_FIRST, _PRINTABLE_ASCII_LAST = 0x21, 0x7E  # visible ASCII, no space
_STREAM_CONTENT_TYPE = "text/event-stream"
_HTTP_SERVER_ERROR = 500


def make_daemon_stdout(buffer):
    """UTF-8 text stream over *buffer* that flushes at every newline."""
    return io.TextIOWrapper(
        buffer, encoding="utf-8", errors="replace", newline="\n", line_buffering=True
    )


class _UtcPrefixFormatter(logging.Formatter):
    def format(self, record):
        stamp = datetime.fromtimestamp(record.created, UTC)
        return f"{stamp:%Y-%m-%dT%H:%M:%S}.{int(record.msecs):03d}Z {record.getMessage()}"


class _TimingStreamHandler(logging.StreamHandler):
    pass


def attach_stream_handler(stream):
    """Print each timing line to *stream* with its UTC time; once per stream."""
    for handler in timing_logger.handlers:
        if isinstance(handler, _TimingStreamHandler) and handler.stream is stream:
            return
    handler = _TimingStreamHandler(stream)
    handler.setFormatter(_UtcPrefixFormatter())
    timing_logger.addHandler(handler)


def configure_daemon_output():
    sys.stdout = make_daemon_stdout(sys.stdout.buffer)
    attach_stream_handler(sys.stdout)


def _sanitize(value) -> str:
    """Percent-encode everything that could split, forge or extend a line (None is ``-``)."""
    if value is None:
        return "-"
    out = []
    for ch in str(value):
        if _PRINTABLE_ASCII_FIRST <= ord(ch) <= _PRINTABLE_ASCII_LAST and ch != "%":
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8", "replace"))
    return "".join(out)


def emit(event, *words, **fields):
    try:
        parts = [_sanitize(event), *(_sanitize(w) for w in words)]
        parts.extend(f"{key}={_sanitize(val)}" for key, val in fields.items())
        timing_logger.info("%s", " ".join(parts))
    except Exception:  # noqa: BLE001, S110 - a timing line must never break the caller
        pass


def _masked_target(path: str, query: str) -> str:
    """Path plus the query names with every value masked (values can carry ids or tokens)."""
    if not query:
        return path
    names = [f"{name}={_MASKED_VALUE}" for name, _ in parse_qsl(query, keep_blank_values=True)]
    return f"{path}?{'&'.join(names)}" if names else path


class _LoggedBody:
    """The response iterable, read lazily; the closing access line is written by ``close``."""

    def __init__(self, inner, finish):
        self._inner = inner
        self._iter = iter(inner)
        self._finish = finish
        self.size = 0
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self):
        chunk = next(self._iter)
        self.size += len(chunk)
        return chunk

    def close(self):
        try:
            inner_close = getattr(self._inner, "close", None)
            if inner_close is not None:
                inner_close()
        finally:
            with self._lock:
                first_close, self._closed = not self._closed, True
            if first_close:
                self._finish(self.size)


class AccessLogMiddleware:
    """One ``access`` line per request; reads only method, path and query from the environ."""

    def __init__(self, wsgi_app):
        self._app = wsgi_app

    def __call__(self, environ, start_response):
        started = clock()
        method = environ.get("REQUEST_METHOD", "-")
        target = _masked_target(environ.get("PATH_INFO", ""), environ.get("QUERY_STRING", ""))
        seen = {"status": _HTTP_SERVER_ERROR, "stream": False}

        def logging_start_response(status, headers, exc_info=None):
            try:
                seen["status"] = int(str(status)[:3])
                seen["stream"] = any(
                    k.lower() == "content-type" and str(v).startswith(_STREAM_CONTENT_TYPE)
                    for k, v in headers
                )
            except Exception:  # noqa: BLE001, S110
                pass
            if exc_info is None:
                return start_response(status, headers)
            return start_response(status, headers, exc_info)

        try:
            inner = self._app(environ, logging_start_response)
        except Exception as exc:
            emit(
                "access", method, target, status=_HTTP_SERVER_ERROR, bytes=0,
                error=type(exc).__name__,
            )  # fmt: skip
            raise

        def finish(size):
            elapsed = clock() - started
            if seen["stream"]:
                emit(
                    "access", method, target, status=seen["status"], stream="closed",
                    open_s=f"{elapsed:.1f}", bytes=size,
                )  # fmt: skip
            else:
                emit(
                    "access", method, target, status=seen["status"],
                    ms=round(elapsed * _MS_PER_S), bytes=size,
                )  # fmt: skip

        if seen["stream"]:
            emit("access", method, target, status=seen["status"], stream="open")
        return _LoggedBody(inner, finish)
