"""RED tests -- feature-plans/advisor-outage-degrade.md (AC-1, AC-2, AC-3, AC-5,
AC-6, AC-7a/b/c): compile_plan's repair loop must DEGRADE (emit the already-
validate_tree-valid tree, flagged tradeability_unverified) on a Composer
INFRA/transport outage, instead of dropping the plan (tree=None) as it does
today. A genuine HTTP-400 tradeability rejection or HTTP-422 grammar rejection
is UNCHANGED -- those are real gate/grammar rejections, not an outage.

ORIGIN: this defect was surfaced by r1-test (the same test-writer as this
cycle) during the advisor-remediation-r1 CI credential-less investigation
(commit 45d57bbb) -- a direct read-only diagnostic call to Composer's live
/backtest endpoint with fully empty credentials proved the endpoint does not
enforce auth on this route (result.error is None), disproving one working
theory and confirming the actual defect is classification, not credentials:
today, EVERY infra failure envelope (5xx-exhausted, timeout, transport error,
429-exhausted) collapses into the same "unrepairable grammar reject -> drop"
branch as a genuine HTTP-422, because _parse_envelope_status only recognizes
a status IF the envelope matches "^HTTP (\\d+):" -- and several infra envelope
shapes don't even carry that prefix.

CLASSIFICATION RULE THIS FILE PINS (team-converged with dg-engine/team-lead,
see .claude/tdd-handoff.md for the full record):
  - status == 400            -> TRADEABILITY  (existing prune/retry/drop; BYTE
                                 PRESERVED -- see test_plan_tree_compiler_repair.py,
                                 which stays UNTOUCHED and must still be green)
  - status == 422             -> GRAMMAR REJECT (existing drop; BYTE PRESERVED --
                                 NOT infra, distinct reason token from the new
                                 "backtest_unavailable")
  - status in {500,502,503,504} (5xx)  -> INFRA -> degrade
  - status is None (non-parseable: timeout / transport-error / 429-exhausted /
    invalid-JSON-200 envelope strings -- none carry a colon-terminated
    "HTTP {N}:" prefix)                -> INFRA -> degrade
  - any OTHER parsed status (e.g. 401, 403, 404 -- not named in the plan's own
    edge cases)                        -> UNCHANGED existing grammar_reject_{status}
                                          drop path. Pinned explicitly (test
                                          below) so an over-broad "anything
                                          that isn't 400" implementation can't
                                          silently also degrade on these.
  - Infra classification fires IMMEDIATELY on the FIRST infra-classified
    backtest_fn call -- no additional repair-loop retry, since run_backtest
    itself already exhausted its own bounded backoff before returning that
    error string to compile_plan. current_tree at that moment (possibly
    already partially pruned by an earlier successful 400-prune) is what gets
    emitted.

CONTRACT FIELDS THIS FILE PINS:
  - CompileResult gains `tradeability_unverified: bool = False` (dataclass-
    defaulted -- existing CompileResult(...) call sites across the suite must
    not break). On infra-degrade: tree=<validated tree>, reason=
    "backtest_unavailable", tradeability_unverified=True. This is a genuine
    contract shift worth pinning explicitly: today `reason is not None`
    implies `tree is None`; after this fix that implication no longer holds
    for the backtest_unavailable case.

WHAT IS MOCKED: only the injected backtest_fn callable seam (a _FakeBacktestResult
matching composer_backtest_client.BacktestResult's .error/.stats shape) -- no
live network, no network-module patching. The math/schema engine
(symphony_schema.validate_tree, extract_tickers) is imported LIVE and never
mocked, matching this suite's established discipline.

PROVENANCE (Gate-1): the infra envelope strings below (_http_5xx, _timeout,
_transport_error, _http_429_exhausted, _invalid_json_200) are format-derived,
not invented -- test_self_guard_fixture_matches_composer_backtest_client_format
below is the CANONICAL provenance mechanism: it asserts, via
inspect.getsource, that the literal f-string templates these helpers
reproduce are byte-present in advisors/composer_backtest_client.py's real
source. This is a runtime validator against the live producer (fails on
producer drift), stronger than a static sidecar fixture file (which only
fails if someone remembers to update it) -- so no separate JSON fixture is
checked in; this self-guard IS the fixture-provenance gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from advisors import symphony_schema

# ---------------------------------------------------------------------------
# Local helpers -- deliberately duplicated (not imported) from
# test_plan_tree_compiler_repair.py, matching this suite's established
# per-file self-containment convention (see that file's own docstring:
# "shared shapes with the Component-2 generator + AC-14 file").
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compiler():
    import advisors.plan_tree_compiler as _c  # noqa: PLC0415

    return _c


def _asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _weight(scheme: str, children: list, **extra) -> dict:
    node = {"kind": "weight", "scheme": scheme, "children": children}
    node.update(extra)
    return node


def _plan(objective: str, root: dict, *, plan_id="p", name="Plan", rebalance="daily") -> dict:
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": name,
        "rebalance": rebalance,
        "root": root,
    }


@dataclass
class _FakeBacktestResult:
    error: str | None = None
    stats: dict | None = None


def _ok_result() -> _FakeBacktestResult:
    return _FakeBacktestResult(error=None, stats={"sharpe": 0.0})


def _err_result(envelope: str) -> _FakeBacktestResult:
    return _FakeBacktestResult(error=envelope, stats=None)


class _ScriptedBacktest:
    """A backtest_fn returning a scripted sequence of results, recording every
    raw_value it was called with. Exhausting the script returns a tradeability
    failure (makes an unbounded loop fail loudly rather than hang)."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[dict] = []

    def __call__(self, raw_value, *args, **kwargs):
        self.calls.append(raw_value)
        if self._results:
            return self._results.pop(0)
        return _err_result("HTTP 400: default not-tradable")


def _tickers_in(raw_value) -> set[str]:
    return symphony_schema.extract_tickers(raw_value)


# ---------------------------------------------------------------------------
# Real producer-format envelope strings, reproduced directly from
# composer_backtest_client.py's own f-string templates (file:line cited
# inline below) -- no invented shapes. See
# test_self_guard_fixture_matches_composer_backtest_client_format for the
# runtime provenance check against the live producer source.
# ---------------------------------------------------------------------------


def _http_5xx(status: int, text: str = "Service Temporarily Unavailable") -> str:
    return f"HTTP {status}: {text}"


def _http_429_exhausted(retry_after: int = 30) -> str:
    return f"HTTP 429 rate limit exceeded; Retry-After={retry_after}s"


def _timeout(exc: str = "HTTPSConnectionPool timeout") -> str:
    return f"request timed out after 120s: {exc}"


def _transport_error(exc_type: str = "ConnectionError", exc: str = "connection refused") -> str:
    return f"transport error: {exc_type}: {exc}"


def _invalid_json_200(exc: str = "Expecting value: line 1 column 1 (char 0)") -> str:
    return f"invalid JSON in 200 response: {exc}"


def _http_400(text: str) -> str:
    return f"HTTP 400: {text}"


def _http_422(text: str) -> str:
    return f"HTTP 422: {text}"


def _http_other(status: int, text: str = "forbidden") -> str:
    return f"HTTP {status}: {text}"


# A standard two-asset equal-weight plan reused across most scenarios.
def _two_asset_plan() -> dict:
    return _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))


# ===========================================================================
# Self-guard: prove the fixture reproduction is faithful to the real client.
# ===========================================================================


def test_self_guard_fixture_matches_composer_backtest_client_format():
    """CANONICAL PROVENANCE MECHANISM for this file's infra envelope helpers
    (Gate-1: no invented fixture values). Rather than a static sidecar JSON
    fixture (which only fails if a human remembers to update it),
    this asserts -- via inspect.getsource -- that the literal f-string
    templates _http_5xx/_timeout/_transport_error/_http_429_exhausted/
    _invalid_json_200 reproduce are byte-present in the REAL, LIVE advisors/
    composer_backtest_client.py source. It fails the moment the producer's
    format drifts, which a checked-in fixture file cannot do. If this test
    fails, every other test in this file is exercising a fabricated shape --
    fix the helpers (or this assertion, if the producer format genuinely
    changed) before trusting anything else here."""
    import inspect

    import advisors.composer_backtest_client as client

    source = inspect.getsource(client)
    assert 'f"HTTP {response.status_code}: {response.text[:200]}"' in source, (
        "5xx-exhausted envelope format has drifted from the fixture assumption"
    )
    assert 'f"HTTP 429 rate limit exceeded; Retry-After={retry_after:.0f}s"' in source, (
        "429-exhausted envelope format has drifted from the fixture assumption"
    )
    assert 'f"request timed out after {_BACKTEST_REQUEST_TIMEOUT}s: {exc}"' in source, (
        "timeout envelope format has drifted from the fixture assumption"
    )
    assert 'f"transport error: {type(exc).__name__}: {exc}"' in source, (
        "transport-error envelope format has drifted from the fixture assumption"
    )
    assert 'f"invalid JSON in 200 response: {exc}"' in source, (
        "invalid-JSON-200 envelope format has drifted from the fixture assumption"
    )


def test_self_guard_none_of_the_infra_envelopes_match_the_400_or_422_status():
    """Self-guard: confirm every infra envelope shape used below actually
    parses to a status OTHER than 400/422 via the compiler's real
    _parse_envelope_status -- otherwise these tests would be silently
    exercising the wrong branch."""
    import advisors.plan_tree_compiler as compiler_mod

    for envelope in (
        _http_5xx(503),
        _http_429_exhausted(),
        _timeout(),
        _transport_error(),
        _invalid_json_200(),
    ):
        status = compiler_mod._parse_envelope_status(envelope)
        assert status not in (400, 422), (
            f"envelope {envelope!r} parsed to status={status!r}, which collides "
            f"with a tradeability/grammar status -- fixture is wrong"
        )


# ===========================================================================
# AC-1 / AC-2: infra/transport failures DEGRADE (tree emitted, flagged) --
# one test per real infra envelope shape.
# ===========================================================================


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_ac1_ac2_http_5xx_degrades_tree_not_dropped(compiler, status):
    """A 5xx (transient status exhausted by run_backtest's own retries) must
    DEGRADE: the validated tree is emitted (NOT tree=None), reason=
    'backtest_unavailable', tradeability_unverified=True. MUST FAIL pre-fix:
    today this falls into the else-branch grammar_reject_{status} drop."""
    bt = _ScriptedBacktest([_err_result(_http_5xx(status))])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None, (
        f"HTTP {status} must DEGRADE (emit the tree), not drop it -- this is an "
        f"infra failure, not a genuine rejection"
    )
    assert result.reason == "backtest_unavailable"
    assert result.tradeability_unverified is True
    assert symphony_schema.validate_tree(result.tree) == []
    assert {"SPY", "QQQ"} == _tickers_in(result.tree), "un-pruned tree on first-call infra hit"
    assert len(bt.calls) == 1, "no repair-loop retry after an infra classification"


def test_ac1_ac2_429_rate_limit_exhausted_degrades_tree(compiler):
    """A 429-exhausted envelope (no colon after the digits -- non-parseable by
    the compiler's status regex) must DEGRADE, not drop."""
    bt = _ScriptedBacktest([_err_result(_http_429_exhausted())])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None
    assert result.reason == "backtest_unavailable"
    assert result.tradeability_unverified is True
    assert symphony_schema.validate_tree(result.tree) == []
    assert len(bt.calls) == 1


def test_ac1_ac2_timeout_degrades_tree(compiler):
    """A request-timeout envelope must DEGRADE, not drop."""
    bt = _ScriptedBacktest([_err_result(_timeout())])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None
    assert result.reason == "backtest_unavailable"
    assert result.tradeability_unverified is True
    assert symphony_schema.validate_tree(result.tree) == []
    assert len(bt.calls) == 1


def test_ac1_ac2_transport_error_degrades_tree(compiler):
    """A connection/DNS-class transport-error envelope must DEGRADE, not
    drop -- this is the literal 'Composer unreachable' case the plan names."""
    bt = _ScriptedBacktest([_err_result(_transport_error())])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None
    assert result.reason == "backtest_unavailable"
    assert result.tradeability_unverified is True
    assert symphony_schema.validate_tree(result.tree) == []
    assert len(bt.calls) == 1


def test_ac1_invalid_json_200_response_degrades_tree(compiler):
    """A 200 response with an unparseable body is a Composer-side oddity, not
    a genuine data rejection -- it does not parse to any status via the
    compiler's regex, so it falls into the same non-parseable-infra bucket."""
    bt = _ScriptedBacktest([_err_result(_invalid_json_200())])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None
    assert result.reason == "backtest_unavailable"
    assert result.tradeability_unverified is True


# ===========================================================================
# AC-3: genuine HTTP-400 tradeability rejection and HTTP-422 grammar
# rejection are UNCHANGED -- explicit boundary pins in THIS file (the sibling
# test_plan_tree_compiler_repair.py stays untouched and is the primary
# byte-preservation proof; these are additional regression pins scoped to
# the infra-vs-genuine-rejection boundary specifically).
# ===========================================================================


def test_ac3_genuine_400_tradeability_rejection_still_prunes_and_retries(compiler):
    """A parseable HTTP-400 naming an in-tree ticker must still PRUNE and
    RETRY -- the existing repair behavior, byte-preserved. Must NOT be
    reclassified as infra."""
    bt = _ScriptedBacktest(
        [
            _err_result(_http_400("Asset BADX is not tradable")),
            _ok_result(),
        ]
    )
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("BADX"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.reason is None, "a repaired 400 must succeed with reason=None, not degrade"
    assert result.tradeability_unverified is False
    assert "BADX" not in _tickers_in(result.tree)
    assert {"SPY", "QQQ"} <= _tickers_in(result.tree)
    assert len(bt.calls) == 2, "expected the existing prune-then-retry call pattern"


def test_ac3_genuine_422_grammar_rejection_still_drops_not_infra(compiler):
    """A parseable HTTP-422 (grammar rejection) must still DROP (tree=None) --
    it must NOT be reclassified as infra/degrade. This is the exact boundary
    the plan's Architecture section calls out: 'grammar-422' stays a distinct,
    unchanged reason token from the new 'backtest_unavailable'."""
    bt = _ScriptedBacktest(
        [
            _err_result(_http_422("node-type-not-supported: invalid grammar")),
            _err_result(_http_422("node-type-not-supported: invalid grammar")),
        ]
    )
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("TLT")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is None, "a genuine 422 grammar rejection must still DROP, not degrade"
    assert result.tradeability_unverified is False
    assert result.reason != "backtest_unavailable"
    assert result.reason is not None and result.reason != ""


def test_ac3_400_unparseable_envelope_still_drops_not_infra(compiler):
    """A 400 whose text names no in-tree ticker (unrepairable by pruning)
    must still drop with the existing 'no_in_tree_ticker_in_400' class of
    reason -- this is a genuine rejection Composer sent, NOT an outage, even
    though the compiler can't act on it. Pins the plan's own edge case:
    'today's behavior (drop with a grammar/parse reason) is preserved -- this
    is NOT the outage path.'"""
    bt = _ScriptedBacktest([_err_result(_http_400("ZZZZ not tradable")) for _ in range(20)])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is None
    assert result.tradeability_unverified is False
    assert result.reason != "backtest_unavailable"


def test_ac3_boundary_other_parsed_status_stays_on_old_drop_path_not_infra(compiler):
    """A parseable status that is neither 400, 422, nor 5xx (e.g. 403) is
    NOT named in the plan's edge cases -- pinned here so an over-broad
    'anything that isn't a genuine 400' implementation can't silently treat
    it as infra too. Existing behavior (grammar_reject_{status} drop) stays
    unchanged."""
    bt = _ScriptedBacktest([_err_result(_http_other(403, "forbidden"))])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is None, "HTTP 403 must NOT degrade -- it is not a named infra status"
    assert result.tradeability_unverified is False
    assert result.reason != "backtest_unavailable"


# ===========================================================================
# AC-2 edge cases: mid-repair infra hit, infra-on-first-call, no wasted budget.
# ===========================================================================


def test_edge_mid_repair_infra_hit_after_successful_prune_emits_partially_pruned_tree(compiler):
    """First call: 400 naming BADX -> prune succeeds (retry #2 built). Retry
    #2 hits an infra failure (503) instead of succeeding or rejecting again.
    Must degrade with the ALREADY-PRUNED tree (BADX gone), not drop, and not
    attempt a third call."""
    bt = _ScriptedBacktest(
        [
            _err_result(_http_400("Asset BADX is not tradable")),
            _err_result(_http_5xx(503)),
        ]
    )
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("BADX"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None, "mid-repair infra hit must degrade, not drop"
    assert result.reason == "backtest_unavailable"
    assert result.tradeability_unverified is True
    final_tickers = _tickers_in(result.tree)
    assert "BADX" not in final_tickers, "the earlier successful prune must survive into the degrade"
    assert {"SPY", "QQQ"} <= final_tickers
    assert symphony_schema.validate_tree(result.tree) == []
    assert len(bt.calls) == 2, "no third call after the mid-repair infra classification"


def test_edge_infra_on_first_call_emits_unpruned_original_tree(compiler):
    """Infra failure on the very FIRST backtest call (no prior prune) emits
    the un-pruned, originally-validated tree."""
    bt = _ScriptedBacktest([_err_result(_transport_error())])
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("TLT")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is not None
    assert {"SPY", "QQQ", "TLT"} == _tickers_in(result.tree)
    assert len(bt.calls) == 1


def test_edge_no_wasted_repair_budget_after_infra_classification(compiler):
    """Even though MAX_REPAIR_ATTEMPTS would allow more calls, an infra
    classification must stop the loop immediately -- not retry the outage
    the way a 400-tradeability rejection would retry a prune."""
    bt = _ScriptedBacktest([_err_result(_http_5xx(500))])
    plan = _two_asset_plan()
    compiler.compile_plan(plan, backtest_fn=bt)

    assert len(bt.calls) == 1, (
        f"expected exactly 1 backtest call on an infra hit (no retry), got {len(bt.calls)} "
        f"-- MAX_REPAIR_ATTEMPTS is {compiler.MAX_REPAIR_ATTEMPTS}, this must not be exhausted "
        f"on infra failures"
    )


# ===========================================================================
# AC-5: honest empty-state -- the degrade fields are absent/default False
# everywhere the outage path did NOT fire.
# ===========================================================================


def test_ac5_success_path_tradeability_unverified_stays_false(compiler):
    """A clean success (no repair needed) must leave tradeability_unverified
    False and reason None -- unchanged from today's behavior (AC-7c)."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.reason is None
    assert result.tradeability_unverified is False
    assert isinstance(result.tree, dict)
    assert len(bt.calls) == 1


def test_ac5_compile_only_path_no_backtest_fn_leaves_tradeability_unverified_false(compiler):
    """When backtest_fn is None (compile-only path -- tradeability was never
    even attempted), tradeability_unverified must stay False, NOT True. The
    marker exists to signal 'the outage path fired', not 'tradeability was
    never checked for any reason' -- those are different claims and must not
    be conflated (AC-5: 'the marker only appears when the outage path
    actually fired')."""
    plan = _two_asset_plan()
    result = compiler.compile_plan(plan, backtest_fn=None)

    assert isinstance(result.tree, dict)
    assert result.reason is None
    assert result.tradeability_unverified is False


def test_ac5_market_cap_drop_unaffected_by_degrade_change(compiler):
    """AC-6 sibling: the pre-existing market_cap-deprecated drop must be
    completely unaffected by this cycle -- tree=None,
    tradeability_unverified stays False (default), and the backtest seam is
    never even called (unchanged from today)."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _plan("diversify", _weight("market_cap", [_asset("AAPL"), _asset("MSFT")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)

    assert result.tree is None
    assert result.tradeability_unverified is False
    assert result.reason != "backtest_unavailable"
    assert bt.calls == [], "market_cap must never reach the backtest seam"


# ===========================================================================
# AC-6: D-1 / never-raises contract preserved on the new branch.
# ===========================================================================


def test_ac6_infra_degrade_path_never_raises_on_garbage_plan_shape(compiler):
    """D-1: even with an infra-classified backtest result, a downstream
    internal error must still degrade cleanly (never propagate) -- the infra
    branch is not exempt from the D-1 contract the rest of compile_plan
    honors."""
    bt = _ScriptedBacktest([_err_result(_http_5xx(500))])
    # Not a dict -- compile_plan's basic shape guard must still fire first,
    # never reaching the backtest seam at all.
    result = compiler.compile_plan("not-a-plan", backtest_fn=bt)
    assert result.tree is None
    assert result.reason == "InvalidPlan"
    assert bt.calls == []
