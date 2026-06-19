"""RED tests — community-strats route wiring (HF-1).

Module under test: app.ai_advisor_strategy_builder_run
Route: POST /ai-advisor/strategy-builder/run

These are ROUTE-LAYER tests only. The ENGINE-LAYER tests live in
tests/advisors/test_community_strats_wiring.py (which tests
strategy_builder_engine.community_candidate_infos / propose_strategies
directly). This file tests that the Flask handler calls the right helpers
with the right arguments and degrades correctly.

Tests are RED by construction: the route currently calls propose_strategies()
WITHOUT the community_candidates kwarg and WITHOUT calling
load_community_strategies at all. Every AC-1, AC-2, AC-3, AC-4 test will
fail until the route wiring is implemented. AC-5 and AC-6 tests are
constraint guards that pass both before and after implementation.

Mocking strategy:
  - advisors.community_strats.load_community_strategies  — source module patch
  - advisors.strategy_builder_engine.community_candidate_infos  — source module patch
  - advisors.strategy_builder_engine.propose_strategies  — source module patch
  - database.load_state  — autouse-stubbed by tests/ui/conftest.py
  - CSRF  — autouse-disabled by tests/conftest.py

All patches target the source modules. The route handler uses lazy
`from X import Y` imports inside the function body; each call re-imports
from the source module namespace, so patching the source is the correct seam.

Rules:
  - No live Atlas, Composer, or DB calls.
  - No hardcoded producer-computed metric values; assert shape/membership/kwarg
    forwarding only.
  - Every test has at least one assertion that can fail on a wrong implementation.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — fake objects returned by mocked collaborators
# ---------------------------------------------------------------------------


def _make_fake_proposal_run(*, error: str | None = None) -> MagicMock:
    """Minimal ProposalRun-shaped MagicMock that the route handler can serialise.

    The route's response-building section accesses:
      run.error, run.gated_batch, run.candidates, run.screened_survivors,
      run.gated_batch.results, run.gated_batch.n_candidates, run.gated_batch.fdr_q

    We set these explicitly so tests that focus on kwarg-forwarding / call
    patterns don't crash in the serialisation code downstream.
    """
    run = MagicMock()
    run.error = error

    # Build a minimal gated_batch with no survivors and no rejected candidates.
    gate = MagicMock()
    gate.n_candidates = 0
    gate.fdr_q = 0.05
    gate.results = []
    gate.survivors = []
    run.gated_batch = gate

    # No candidates, no survivors.
    run.candidates = []
    run.screened_survivors = []

    return run


def _make_community_result(*, n_candidates: int = 2, available: bool = True) -> dict:
    """Minimal community_result dict matching load_community_strategies output shape.

    Does NOT use real tree objects — the route handler passes the result to
    community_candidate_infos, which is mocked. We only need the dict shape.
    """
    if not available:
        return {
            "available": False,
            "reason": "AtlasUnavailable",
            "candidates": [],
            "stats": {"pulled": 0, "valid": 0},
            "source": "captplanet",
        }
    return {
        "available": True,
        "candidates": [
            {
                "sid": f"sid-{i:04d}",
                "name": f"Community Strat {i}",
            }
            for i in range(n_candidates)
        ],
        "stats": {"pulled": n_candidates, "valid": n_candidates},
        "source": "captplanet",
    }


def _make_fake_candidate_infos(n: int) -> list:
    """Return N opaque MagicMock objects standing in for CandidateInfo instances.

    The route passes these through to propose_strategies unchanged; we only
    care about count and identity, not internal structure.
    """
    return [MagicMock(name=f"CandidateInfo-{i}") for i in range(n)]


# ---------------------------------------------------------------------------
# Shared fixture — Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def sb_client():
    """Flask test client for the strategy-builder route."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post_run(client, body: dict | None = None) -> flask.wrappers.Response:  # type: ignore[name-defined]  # noqa: F821
    """POST /ai-advisor/strategy-builder/run with a JSON body."""
    payload = body or {"objective": "diversify", "universe": ["SPY", "QQQ"]}
    return client.post(
        "/ai-advisor/strategy-builder/run",
        data=json.dumps(payload),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Context managers — patch all three collaborators together
# ---------------------------------------------------------------------------


def _patch_all(
    *,
    n_community_candidates: int = 2,
    available: bool = True,
    load_raises: Exception | None = None,
    propose_raises: Exception | None = None,
    propose_error: str | None = None,
):
    """Return a context manager that patches load, adapter, and propose_strategies.

    Captures the call_args on propose_strategies so tests can inspect kwargs.
    Returns (load_mock, adapter_mock, propose_mock).
    """
    community_result = _make_community_result(
        n_candidates=n_community_candidates, available=available
    )
    adapter_output = _make_fake_candidate_infos(n_community_candidates if available else 0)
    fake_run = _make_fake_proposal_run(error=propose_error)

    load_mock = MagicMock(return_value=community_result)
    if load_raises is not None:
        load_mock.side_effect = load_raises

    adapter_mock = MagicMock(return_value=adapter_output)

    propose_mock = MagicMock(return_value=fake_run)
    if propose_raises is not None:
        propose_mock.side_effect = propose_raises

    return (
        patch("advisors.community_strats.load_community_strategies", load_mock),
        patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
        patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        load_mock,
        adapter_mock,
        propose_mock,
        adapter_output,
    )


# ===========================================================================
# SECTION 1 — AC-1: community candidates forwarded as community_candidates kwarg
# ===========================================================================


class TestAC1CommunityKwargForwarding:
    """AC-1: the route must call propose_strategies(community_candidates=<adapter_output>)."""

    def test_community_candidates_kwarg_forwarded_to_propose_strategies(self, sb_client):
        """AC-1: community_candidates kwarg passed to propose_strategies equals adapter output.

        RED: currently the route calls propose_strategies() without community_candidates.
        This test asserts the kwarg IS present and matches what community_candidate_infos
        returned.
        """
        n = 2
        community_result = _make_community_result(n_candidates=n)
        adapter_output = _make_fake_candidate_infos(n)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200

        assert propose_mock.called, "propose_strategies must be called; it was not called at all"

        _, kwargs = propose_mock.call_args
        assert "community_candidates" in kwargs, (
            "propose_strategies must be called with community_candidates= kwarg; "
            f"actual kwargs: {list(kwargs.keys())}"
        )
        forwarded = kwargs["community_candidates"]
        assert forwarded == adapter_output, (
            "community_candidates forwarded to propose_strategies must equal "
            "the adapter output; got a different object or value"
        )

    def test_community_candidates_kwarg_count_matches_adapter_output(self, sb_client):
        """AC-1: len(forwarded community_candidates) == len(adapter output).

        RED: without community wiring, propose_strategies is called with no
        community_candidates kwarg, so this check cannot even start.
        """
        n = 3
        community_result = _make_community_result(n_candidates=n)
        adapter_output = _make_fake_candidate_infos(n)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        _, kwargs = propose_mock.call_args
        forwarded = kwargs.get("community_candidates")
        assert forwarded is not None, (
            "community_candidates must be forwarded; got None/missing kwarg"
        )
        assert len(forwarded) == n, (
            f"expected {n} community_candidates forwarded; got {len(forwarded)}"
        )

    def test_community_candidates_forwarded_value_is_list_not_none(self, sb_client):
        """AC-1: the forwarded community_candidates must be a list, not None.

        Guards against an impl that passes community_candidates=None when
        candidates exist.
        """
        n = 1
        community_result = _make_community_result(n_candidates=n)
        adapter_output = _make_fake_candidate_infos(n)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        _, kwargs = propose_mock.call_args
        forwarded = kwargs.get("community_candidates")
        assert isinstance(forwarded, list), (
            f"community_candidates must be a list; got {type(forwarded).__name__}"
        )

    def test_community_candidate_infos_called_with_load_result_and_max_cap(self, sb_client):
        """AC-1: adapter called with the community_result from load AND max_candidates kwarg.

        RED: currently community_candidate_infos is never called from this route.
        """
        community_result = _make_community_result(n_candidates=2)
        adapter_output = _make_fake_candidate_infos(2)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert adapter_mock.called, "community_candidate_infos must be called; it was not called"

        _, adapter_kwargs = adapter_mock.call_args
        # First positional arg must be the community_result dict from load
        adapter_args, _ = adapter_mock.call_args
        assert len(adapter_args) >= 1, (
            "community_candidate_infos must receive the community_result as positional arg"
        )
        assert adapter_args[0] == community_result, (
            "community_candidate_infos must receive the dict returned by "
            "load_community_strategies; got a different object"
        )
        # max_candidates must be forwarded
        assert "max_candidates" in adapter_kwargs, (
            "community_candidate_infos must be called with max_candidates= kwarg; "
            f"actual kwargs: {list(adapter_kwargs.keys())}"
        )
        assert adapter_kwargs["max_candidates"] > 0, "max_candidates must be a positive integer cap"


# ===========================================================================
# SECTION 2 — AC-2: empty / unavailable → template-only, route completes
# ===========================================================================


class TestAC2TemplateDegradation:
    """AC-2: route must complete a template-only run when community load is empty/unavailable."""

    def test_empty_candidates_list_route_completes(self, sb_client):
        """AC-2: community_result with available=True but empty candidates → 200.

        RED: currently load_community_strategies is not called at all, so the
        response shape is the same regardless; but the kwarg forwarding part
        is absent.
        """
        community_result = _make_community_result(n_candidates=0)
        adapter_output = []  # adapter returns [] for empty candidates
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            f"route must return 200 when community load has no candidates; got {resp.status_code}"
        )

    def test_available_false_route_completes(self, sb_client):
        """AC-2: available=False in community_result → 200, no error raised.

        RED: without the wiring, load is never called; this tests that
        the route gracefully handles an unavailable community result.
        """
        community_result = _make_community_result(available=False)
        adapter_output = []
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            f"route must return 200 when available=False; got {resp.status_code}"
        )

    def test_empty_load_response_has_required_json_keys(self, sb_client):
        """AC-2: response must carry survivors/rejected/n_candidates/fdr_adjusted_threshold."""
        community_result = _make_community_result(n_candidates=0)
        adapter_output = []
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body = json.loads(resp.data)
        required = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold"}
        missing = required - body.keys()
        assert not missing, (
            f"response must have keys {required}; missing: {missing}. "
            f"Actual keys: {set(body.keys())}"
        )

    def test_unavailable_response_has_required_json_keys(self, sb_client):
        """AC-2: available=False → response still has required keys."""
        community_result = _make_community_result(available=False)
        adapter_output = []
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body = json.loads(resp.data)
        required = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold"}
        missing = required - body.keys()
        assert not missing, (
            f"response must have keys {required} even when available=False; missing: {missing}"
        )

    def test_empty_community_propose_still_called(self, sb_client):
        """AC-2: propose_strategies must be called even when community load returns [].

        Template-only path must complete — not short-circuit when community empty.
        """
        community_result = _make_community_result(n_candidates=0)
        adapter_output = []
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert propose_mock.called, (
            "propose_strategies must still be called when community candidates is []; "
            "template-only run must not be skipped"
        )


# ===========================================================================
# SECTION 3 — AC-3: bill-protection — force_refresh never True
# ===========================================================================


class TestAC3BillProtection:
    """AC-3: load_community_strategies must never be called with force_refresh=True."""

    def test_load_community_strategies_called_without_force_refresh_true(self, sb_client):
        """AC-3: route must not pass force_refresh=True to load_community_strategies.

        RED: currently load_community_strategies is never called from the route,
        so assert load_mock.called also fails. After impl, both assertions must pass.
        """
        community_result = _make_community_result(n_candidates=1)
        adapter_output = _make_fake_candidate_infos(1)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        # First: the route must call load_community_strategies at all.
        assert load_mock.called, (
            "load_community_strategies must be called by the route; it was not called. "
            "This is the core community-wiring RED test."
        )

        # Second: it must NOT be called with force_refresh=True.
        _, kwargs = load_mock.call_args
        force_refresh_value = kwargs.get("force_refresh", False)
        assert force_refresh_value is not True, (
            "load_community_strategies must NOT be called with force_refresh=True; "
            f"got force_refresh={force_refresh_value!r}. "
            "force_refresh=True would bypass the weekly cache and bill the Atlas provider."
        )

    def test_load_community_strategies_is_called_exactly_once(self, sb_client):
        """AC-3: load_community_strategies is called exactly once per request.

        Calling it multiple times per request would multiply Atlas read costs.
        """
        community_result = _make_community_result(n_candidates=1)
        adapter_output = _make_fake_candidate_infos(1)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert load_mock.call_count == 1, (
            f"load_community_strategies must be called exactly once per request; "
            f"called {load_mock.call_count} times"
        )


# ===========================================================================
# SECTION 4 — AC-4: never-raising / D-1
# ===========================================================================


class TestAC4NeverRaisingD1:
    """AC-4: load or adapter failure must not break the route; propose failure yields error classname."""

    def test_community_load_raises_route_does_not_500(self, sb_client):
        """AC-4: load_community_strategies raising must not cause a 500.

        RED: without the try/except wrapper around the community load block,
        a raised exception propagates and crashes the route (500) or is caught
        by the outer propose_strategies try/except, returning {"error": ...}
        without ever calling propose_strategies. After impl, the route degrades
        to template-only and returns 200 with the normal response shape.
        """
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(side_effect=RuntimeError("atlas is down"))
        adapter_mock = MagicMock()
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            "route must return 200 when community load raises; "
            f"got {resp.status_code}. Community load must be best-effort."
        )

    def test_community_load_raises_response_does_not_leak_exception_str(self, sb_client):
        """AC-4 / D-1: exception message must not appear in the response body.

        The exception could contain a MONGO_URI or credential path. The D-1
        contract requires only type(exc).__name__ in logs; nothing in the response.
        """
        secret_message = "mongodb+srv://admin:S3cr3t@cluster.mongo.net"
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(side_effect=ConnectionError(secret_message))
        adapter_mock = MagicMock()
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body_str = resp.data.decode("utf-8", errors="replace")
        assert secret_message not in body_str, (
            "D-1 violation: exception message from community load appears in response body. "
            "The message could contain credentials/paths. Log only the class name."
        )

    def test_propose_strategies_still_called_when_community_load_raises(self, sb_client):
        """AC-4: when community load raises, propose_strategies must still be called.

        The route must degrade to template-only (community_candidates=[]) and
        proceed — not abort the entire proposal run.
        """
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(side_effect=RuntimeError("atlas timeout"))
        adapter_mock = MagicMock()
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        assert propose_mock.called, (
            "propose_strategies must still be called when community load raises; "
            "community failure must be best-effort / template-only fallback"
        )

    def test_propose_strategies_raises_returns_error_classname(self, sb_client):
        """AC-4 / D-1: propose_strategies raising → {"error": "<ClassName>"} not a 500.

        This is the existing outer try/except behaviour — assert it is preserved
        after community wiring.
        """
        community_result = _make_community_result(n_candidates=1)
        adapter_output = _make_fake_candidate_infos(1)

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(side_effect=ConnectionError("boom"))

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200, (
            f"propose_strategies raising must not 500; got {resp.status_code}"
        )
        body = json.loads(resp.data)
        assert "error" in body, (
            f"response must have 'error' key when propose_strategies raises; "
            f"got keys: {list(body.keys())}"
        )
        assert body["error"] == "ConnectionError", (
            f"error must be the exception class name 'ConnectionError'; got {body['error']!r}"
        )

    def test_propose_strategies_raises_body_has_no_exception_str(self, sb_client):
        """AC-4 / D-1: exception message must not leak into the response body."""
        secret_detail = "internal path /opt/app/config.yaml line 42"
        community_result = _make_community_result(n_candidates=1)
        adapter_output = _make_fake_candidate_infos(1)

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(side_effect=ValueError(secret_detail))

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body_str = resp.data.decode("utf-8", errors="replace")
        assert secret_detail not in body_str, (
            "D-1 violation: exception message from propose_strategies appears in response. "
            "Only type(exc).__name__ is permitted in the response."
        )

    def test_propose_strategies_raises_error_value_is_bare_classname(self, sb_client):
        """AC-4 / D-1: the error value must be a bare class name — no punctuation/path/traceback."""
        community_result = _make_community_result(n_candidates=0)
        adapter_output = []

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(side_effect=TypeError("cannot do this"))

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body = json.loads(resp.data)
        error_val = body.get("error", "")
        # A bare class name has no spaces, colons, or slashes
        assert isinstance(error_val, str), "error value must be a string"
        assert " " not in error_val, (
            f"error value must be bare class name (no spaces); got {error_val!r}"
        )
        assert ":" not in error_val, (
            f"error value must be bare class name (no colon); got {error_val!r}"
        )
        assert "/" not in error_val, (
            f"error value must be bare class name (no slash); got {error_val!r}"
        )
        assert error_val == "TypeError", f"error value must be 'TypeError'; got {error_val!r}"


# ===========================================================================
# SECTION 5 — AC-5: off-execution-path boundary + no allowlist/LIVE_EXECUTION
# ===========================================================================


class TestAC5BoundaryPreserved:
    """AC-5: constraint guards — these must pass both before and after implementation.

    They verify that the implementation does NOT do bad things:
    - No module-level import of load_community_strategies
    - No community key in _SETTINGS_WRITE_ALLOWLIST
    - No LIVE_EXECUTION reference in handler source
    These are GREEN before impl and must remain GREEN after impl.
    """

    def test_load_community_strategies_not_module_level_attr(self):
        """AC-5: load_community_strategies must NOT be a module-level attr of app.

        The import must stay inside the function body (lazy). A module-level
        import would couple community_strats to the live 1-minute execution path.
        """
        import app as app_module

        assert not hasattr(app_module, "load_community_strategies"), (
            "load_community_strategies must not be imported at app module level; "
            "it must be a lazy import inside the route handler to stay off the "
            "1-minute execution path (CC-2 boundary)"
        )

    def test_community_candidate_infos_not_module_level_attr(self):
        """AC-5: community_candidate_infos must NOT be a module-level attr of app."""
        import app as app_module

        assert not hasattr(app_module, "community_candidate_infos"), (
            "community_candidate_infos must not be imported at app module level; "
            "it must be a lazy import inside the route handler"
        )

    def test_SETTINGS_WRITE_ALLOWLIST_does_not_contain_community_key(self):
        """AC-5: _SETTINGS_WRITE_ALLOWLIST must not include any community-related key.

        The strategy-builder route is advisory-only and must never be gated via
        the settings allowlist.
        """
        import app as app_module

        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        # No community-related key should be in the allowlist
        community_keys = {k for k in allowlist if "community" in k.lower()}
        assert not community_keys, (
            f"_SETTINGS_WRITE_ALLOWLIST must not contain community-related keys; "
            f"found: {community_keys}"
        )

    def test_LIVE_EXECUTION_not_referenced_in_handler_source(self):
        """AC-5: the handler must not reference LIVE_EXECUTION in executable code.

        The strategy-builder route is advisory-only; any interaction with the
        LIVE_EXECUTION flag in code would be a critical scope violation.

        The existing docstring documents "No LIVE_EXECUTION interaction anywhere"
        (a statement of intent) — that is acceptable. We check only non-docstring
        lines so the documentation statement does not trigger a false failure.
        """
        import ast
        import textwrap

        import app as app_module

        raw_src = inspect.getsource(app_module.ai_advisor_strategy_builder_run)
        # Dedent so ast.parse works reliably on method source extracted by inspect.
        src = textwrap.dedent(raw_src)

        try:
            tree = ast.parse(src)
        except SyntaxError:
            # If ast.parse fails (e.g. decorator syntax in stripped source),
            # fall back to a line-by-line check that skips comment/docstring lines.
            non_doc_lines = []
            in_docstring = False
            for line in src.splitlines():
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    in_docstring = not in_docstring
                    continue
                if not in_docstring and not stripped.startswith("#"):
                    non_doc_lines.append(line)
            code_only = "\n".join(non_doc_lines)
            assert "LIVE_EXECUTION" not in code_only, (
                "LIVE_EXECUTION must not appear in non-docstring, non-comment handler code"
            )
            return

        # Walk the AST — collect all Name/Attribute nodes and string constants
        # that appear in executable positions (not docstrings).
        # Docstrings are Expr(value=Constant(s)) at the function body start;
        # ast.get_docstring strips them. Check remaining nodes for the identifier.
        class _Checker(ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def visit_Name(self, node):
                if node.id == "LIVE_EXECUTION":
                    self.found = True
                self.generic_visit(node)

            def visit_Attribute(self, node):
                if node.attr == "LIVE_EXECUTION":
                    self.found = True
                self.generic_visit(node)

        # Remove docstring from the function body before walking.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Drop the first statement if it's a docstring constant.
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body = node.body[1:]

        checker = _Checker()
        checker.visit(tree)
        assert not checker.found, (
            "LIVE_EXECUTION must not appear as a Name or Attribute reference in "
            "ai_advisor_strategy_builder_run executable code; "
            "the route is advisory-only and must not interact with the execution path"
        )


# ===========================================================================
# SECTION 6 — AC-6: no regression — empty community → same response shape
# ===========================================================================


class TestAC6NoRegression:
    """AC-6: with community wiring producing [], template-only response shape is unchanged."""

    def test_empty_community_and_raise_both_produce_same_response_keys(self, sb_client):
        """AC-6: both empty-load and load-raises paths return identical response key sets.

        This guards against a scenario where community wiring changes the
        response shape for the template-only path.
        """
        fake_run = _make_fake_proposal_run()

        # Path 1: load returns empty candidates
        load_mock_empty = MagicMock(return_value=_make_community_result(n_candidates=0))
        adapter_mock_empty = MagicMock(return_value=[])
        propose_mock_1 = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock_empty),
            patch(
                "advisors.strategy_builder_engine.community_candidate_infos",
                adapter_mock_empty,
            ),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock_1),
        ):
            resp1 = _post_run(sb_client)

        # Path 2: load raises → degrade
        load_mock_raise = MagicMock(side_effect=RuntimeError("atlas down"))
        adapter_mock_2 = MagicMock()
        propose_mock_2 = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock_raise),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock_2),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock_2),
        ):
            resp2 = _post_run(sb_client)

        keys1 = set(json.loads(resp1.data).keys())
        keys2 = set(json.loads(resp2.data).keys())

        assert keys1 == keys2, (
            "Response key sets must be identical for empty-community and "
            "community-load-raises paths; "
            f"empty-load keys: {keys1}, load-raises keys: {keys2}"
        )

    def test_happy_path_response_shape_keys_present(self, sb_client):
        """AC-6: happy-path response (with community candidates) has the required keys.

        Asserts shape only — no specific metric values.
        """
        community_result = _make_community_result(n_candidates=2)
        adapter_output = _make_fake_candidate_infos(2)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        assert resp.status_code == 200
        body = json.loads(resp.data)

        required_keys = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold"}
        missing = required_keys - body.keys()
        assert not missing, (
            f"Happy-path response must have keys {required_keys}; missing: {missing}"
        )
        # Structural type checks — no hardcoded values
        assert isinstance(body["survivors"], list), "survivors must be a list"
        assert isinstance(body["rejected"], list), "rejected must be a list"
        assert isinstance(body["n_candidates"], int), "n_candidates must be an int"
        # fdr_adjusted_threshold can be None or a number
        assert body["fdr_adjusted_threshold"] is None or isinstance(
            body["fdr_adjusted_threshold"], (int, float)
        ), (
            f"fdr_adjusted_threshold must be None or numeric; "
            f"got {type(body['fdr_adjusted_threshold']).__name__}"
        )


# ===========================================================================
# SECTION 7 — Security boundary
# ===========================================================================


class TestSecurityBoundary:
    """Security constraints — must hold both before and after implementation."""

    def test_no_live_execution_interaction_in_handler(self):
        """Security: handler must not reference LIVE_EXECUTION in executable code.

        LIVE_EXECUTION controls whether real trades execute. Any code-level
        interaction from an advisory route is a critical security scope violation.

        The existing docstring mentions LIVE_EXECUTION as documentation ("No
        LIVE_EXECUTION interaction anywhere") — that is acceptable. We check
        only for AST-level Name/Attribute usage in executable code.
        """
        import ast
        import textwrap

        import app as app_module

        raw_src = inspect.getsource(app_module.ai_advisor_strategy_builder_run)
        src = textwrap.dedent(raw_src)

        try:
            tree = ast.parse(src)
        except SyntaxError:
            pytest.skip("Cannot parse handler source — fallback to string check skipped")
            return

        # Strip docstrings from function bodies before walking.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body = node.body[1:]

        class _Checker(ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def visit_Name(self, node):
                if node.id == "LIVE_EXECUTION":
                    self.found = True
                self.generic_visit(node)

            def visit_Attribute(self, node):
                if node.attr == "LIVE_EXECUTION":
                    self.found = True
                self.generic_visit(node)

        checker = _Checker()
        checker.visit(tree)
        assert not checker.found, (
            "Security: LIVE_EXECUTION must not appear as a Name or Attribute "
            "in ai_advisor_strategy_builder_run executable code"
        )

    def test_force_refresh_not_true_prevents_atlas_abuse(self, sb_client):
        """Security: a caller cannot force repeated Atlas reads by hammering the route.

        The weekly-cache path (force_refresh=False) is the only acceptable call.
        This is also AC-3 — duplicated in the security block because the bill
        protection is a security concern (abuse via Atlas cost amplification).
        """
        community_result = _make_community_result(n_candidates=1)
        adapter_output = _make_fake_candidate_infos(1)
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(return_value=community_result)
        adapter_mock = MagicMock(return_value=adapter_output)
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            _post_run(sb_client)

        if load_mock.called:
            _, kwargs = load_mock.call_args
            assert kwargs.get("force_refresh") is not True, (
                "Security: force_refresh=True would allow callers to exhaust "
                "the weekly Atlas quota on demand"
            )

    def test_community_load_exception_message_not_in_response(self, sb_client):
        """Security / D-1: credential string in exception message must not reach the client."""
        credential_string = "apikey=ABCDEF1234567890"
        fake_run = _make_fake_proposal_run()

        load_mock = MagicMock(side_effect=ValueError(f"Failed with {credential_string}"))
        adapter_mock = MagicMock()
        propose_mock = MagicMock(return_value=fake_run)

        with (
            patch("advisors.community_strats.load_community_strategies", load_mock),
            patch("advisors.strategy_builder_engine.community_candidate_infos", adapter_mock),
            patch("advisors.strategy_builder_engine.propose_strategies", propose_mock),
        ):
            resp = _post_run(sb_client)

        body_str = resp.data.decode("utf-8", errors="replace")
        assert credential_string not in body_str, (
            "Security / D-1: credential string in exception message must not "
            "appear in the response body"
        )

    def test_route_not_in_settings_write_allowlist(self):
        """Security: the strategy-builder run route must NOT be in _SETTINGS_WRITE_ALLOWLIST.

        Being in the allowlist would allow it to write arbitrary .env keys,
        including LIVE_EXECUTION.
        """
        import app as app_module

        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        # The allowlist keys are env var names; none should be community/strategy-builder
        problematic = {k for k in allowlist if "strategy" in k.lower() or "community" in k.lower()}
        assert not problematic, (
            f"Security: no community/strategy keys should be in _SETTINGS_WRITE_ALLOWLIST; "
            f"found: {problematic}"
        )
