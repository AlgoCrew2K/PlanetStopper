"""A real anthropic client over an in-process HTTP transport.

The fake serves ``GET /v1/models`` pages (following ``after_id`` the way the
live API does) and records every request's method, path, query, headers, JSON
body and timeout. Tests hand the client to the code under test through its
existing client seam, so request-level facts (what model was named, which
parameters were sent) are asserted on the outgoing HTTP request and the SDK's
own parsing runs on the reply.

Models listings are scripted per request: a queued outcome is an error fixture
(``{"status", "body"}``), a transport exception instance, or a page envelope,
consumed in order; with no outcome queued the fake serves ``pages``. A gate
holds every listing request until released, so a test can start several
threads while one listing is in flight.

The Messages endpoint is added by the request-helper cycle; until then a call
to ``/v1/messages`` is answered 501 and listed in ``misuse``, because the SDK
turns an exception raised inside a transport handler into a connection error
and would hide it, and no test may be answered with an invented reply.
"""

from __future__ import annotations

import json
import pathlib
import threading
from collections import deque
from dataclasses import dataclass

import anthropic
import httpx

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "anthropic"
FAKE_BASE_URL = "http://anthropic-fake.test"
FAKE_API_KEY = "test-api-key-not-real"
_GATE_WAIT_SECONDS = 15.0


def load_fixture(relative_path: str):
    """Return the parsed JSON of a file under ``tests/fixtures/anthropic``."""
    return json.loads((FIXTURES_DIR / relative_path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    query: dict
    headers: dict
    body: dict | None
    timeout: dict


class AnthropicHttpFake:
    """In-process Anthropic API. Create one per test; every client it makes shares its state."""

    def __init__(self, pages=None):
        self._lock = threading.Lock()
        self._pages = list(pages) if pages is not None else []
        self._outcomes: deque = deque()
        self._gate: threading.Event | None = None
        self._in_flight = 0
        self.max_concurrent_listings = 0
        self.requests: list[RecordedRequest] = []
        self.misuse: list[str] = []
        self.listing_started = threading.Event()

    @classmethod
    def with_captured_page(cls) -> AnthropicHttpFake:
        return cls(pages=[load_fixture("models/page_captured.json")])

    # -- scripting -----------------------------------------------------
    def set_pages(self, pages) -> None:
        with self._lock:
            self._pages = list(pages)

    def queue_listing_outcome(self, outcome) -> None:
        """Queue what the next listing request gets: error fixture, exception instance or page."""
        with self._lock:
            self._outcomes.append(outcome)

    def hold_listings(self) -> threading.Event:
        """Block every listing request until ``release_listings`` (returns the gate)."""
        self.listing_started.clear()
        self._gate = threading.Event()
        return self._gate

    def release_listings(self) -> None:
        if self._gate is not None:
            self._gate.set()

    # -- observation ---------------------------------------------------
    @property
    def listing_requests(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.path == "/v1/models"]

    @property
    def listing_sequences(self) -> int:
        """Listings started: a sequence begins with a request that carries no ``after_id``."""
        return sum(1 for r in self.listing_requests if "after_id" not in r.query)

    # -- client --------------------------------------------------------
    def make_client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key=FAKE_API_KEY,
            base_url=FAKE_BASE_URL,
            http_client=httpx.Client(transport=httpx.MockTransport(self._handle)),
            max_retries=0,
        )

    @property
    def client(self) -> anthropic.Anthropic:
        if not hasattr(self, "_client"):
            self._client = self.make_client()
        return self._client

    # -- transport -----------------------------------------------------
    def _record(self, request: httpx.Request) -> RecordedRequest:
        body = json.loads(request.content) if request.content else None
        recorded = RecordedRequest(
            method=request.method,
            path=request.url.path,
            query=dict(request.url.params),
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
            timeout=dict(request.extensions.get("timeout", {})),
        )
        with self._lock:
            self.requests.append(recorded)
        return recorded

    def _handle(self, request: httpx.Request) -> httpx.Response:
        recorded = self._record(request)
        if recorded.path == "/v1/models" and request.method == "GET":
            return self._handle_listing(recorded)
        return self._misuse(
            f"the fake serves only GET /v1/models; got {request.method} {recorded.path}"
        )

    def _misuse(self, message: str) -> httpx.Response:
        with self._lock:
            self.misuse.append(message)
        return httpx.Response(
            501, json={"type": "error", "error": {"type": "api_error", "message": message}}
        )

    def _handle_listing(self, recorded: RecordedRequest) -> httpx.Response:
        with self._lock:
            self._in_flight += 1
            self.max_concurrent_listings = max(self.max_concurrent_listings, self._in_flight)
        try:
            self.listing_started.set()
            if self._gate is not None and not self._gate.wait(_GATE_WAIT_SECONDS):
                return self._misuse("listing gate was never released")
            with self._lock:
                outcome = self._outcomes.popleft() if self._outcomes else None
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, dict) and "status" in outcome:
                body = outcome["body"]
                return httpx.Response(
                    outcome["status"], json=body, headers={"request-id": body.get("request_id", "")}
                )
            if isinstance(outcome, dict):
                return httpx.Response(200, json=outcome)
            try:
                return httpx.Response(200, json=self._page_for(recorded.query.get("after_id")))
            except LookupError as exc:
                return self._misuse(str(exc))
        finally:
            with self._lock:
                self._in_flight -= 1

    def _page_for(self, after_id):
        with self._lock:
            pages = list(self._pages)
        if after_id is None:
            return pages[0]
        for index, page in enumerate(pages[:-1]):
            if page["last_id"] == after_id:
                return pages[index + 1]
        raise LookupError(f"listing asked for a page after unknown cursor {after_id!r}")
