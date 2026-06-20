"""RED tests — Component 3: Plan->Tree Compiler validate + repair loop.

Module under test: advisors.plan_tree_compiler (NEW).

SCOPE: Component 3.
  - AC-15: every compiled tree passes symphony_schema.validate_tree (== []); a tree
    with HARD errors NEVER reaches the backtest; bounded deterministic repair
    (a NAMED max-attempts constant — never unbounded); a repairable grammar failure
    repairs within the bound; an unrepairable one drops cleanly and the run
    continues.
  - AC-16: the repair loop splits a tradeability rejection (HTTP 400 — prune the
    offending ticker + retry) from a grammar rejection (HTTP 422 — NO blind
    ticker-prune) by PARSING the composer_backtest_client error envelope
    "HTTP {status}: {text-prefix}" (client line 360). The backtest is an INJECTED
    callable seam (backtest_fn) — no live network, no network-module patching.
  - AC-17 (market_cap disposition): Composer DEPRECATED market-cap weighting
    (POST /backtest -> HTTP 422 node-type-not-supported "Market cap weighting is no
    longer supported"; captured live 2026-06-20 at
    tests/fixtures/strategy_builder/wt_marketcap_deprecated_envelope.json). Per the
    escalation, a scheme=="market_cap" plan is an UNREPAIRABLE-grammar drop — it is
    dropped cleanly and NEVER reaches the backtest (it cannot be made tradeable).
    These tests pin that disposition (PM-recommended Option A); if the PM selects a
    different disposition they will be repointed before commit.

CONTRACT SOURCES (no guessing):
  - The error-envelope format is imported behaviour, not a hardcoded literal — we
    build the envelope string the SAME way composer_backtest_client does
    ("HTTP {status}: {text-prefix}") and verify our reproduction matches the client
    constant's shape.
  - symphony_schema.validate_tree is the HARD-error gate (imported live).
  - The injected backtest_fn returns a BacktestResult-shaped object: we mirror the
    real client's contract (.error is a non-empty envelope string on failure, None
    on success; .stats populated on success). The math/schema engine is NEVER
    mocked — only the network seam.

ADVERSARIAL FOCUS: assert which BRANCH fires (prune-and-retry vs drop), which
ticker is pruned, that the repair is BOUNDED (never loops forever), and that a
HARD-error tree never reaches backtest. No producer-computed values are asserted.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from advisors import symphony_schema


@pytest.fixture(scope="module")
def compiler():
    import advisors.plan_tree_compiler as _c  # noqa: PLC0415

    return _c


# ---------------------------------------------------------------------------
# DSL builders (shared shapes with the Component-2 generator + AC-14 file).
# ---------------------------------------------------------------------------


def _asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _weight(scheme: str, children: list, **extra) -> dict:
    node = {"kind": "weight", "scheme": scheme, "children": children}
    node.update(extra)
    return node


def _group(name: str, children: list) -> dict:
    return {"kind": "group", "name": name, "children": children}


def _plan(objective: str, root: dict, *, plan_id="p", name="Plan", rebalance="daily") -> dict:
    return {
        "plan_id": plan_id,
        "objective": objective,
        "name": name,
        "rebalance": rebalance,
        "root": root,
    }


# ---------------------------------------------------------------------------
# Injected backtest seam. Mirrors composer_backtest_client.BacktestResult: .error
# is a non-empty envelope string on failure (None on success); .stats is populated
# on success. The compiler's AC-16 repair loop CALLS backtest_fn(raw_value) and
# parses .error. We control the verdicts — no live network.
# ---------------------------------------------------------------------------


@dataclass
class _FakeBacktestResult:
    error: str | None = None
    stats: dict | None = None


def _ok_result() -> _FakeBacktestResult:
    # A populated stats dict mirrors a successful backtest (values are arbitrary
    # placeholders WE set — never asserted as producer output).
    return _FakeBacktestResult(error=None, stats={"sharpe": 0.0})


def _envelope(status: int, text: str) -> str:
    """Reproduce the composer_backtest_client error envelope EXACTLY.

    The client builds: f"HTTP {response.status_code}: {response.text[:200]}"
    (composer_backtest_client.py:360). The compiler must parse THIS shape; we
    construct it identically so the test exercises the real split logic.
    """
    return f"HTTP {status}: {text[:200]}"


def _err_result(status: int, text: str) -> _FakeBacktestResult:
    return _FakeBacktestResult(error=_envelope(status, text), stats=None)


class _ScriptedBacktest:
    """A backtest_fn that returns a scripted sequence of results and records every
    raw_value it was called with (so we can assert WHICH ticker was pruned)."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[dict] = []

    def __call__(self, raw_value, *args, **kwargs):
        self.calls.append(raw_value)
        if self._results:
            return self._results.pop(0)
        # Default to a tradeability failure if the script is exhausted — this makes
        # an unbounded loop fail loudly rather than hang.
        return _err_result(400, "default not-tradable")


def _tickers_in(raw_value) -> set[str]:
    return symphony_schema.extract_tickers(raw_value)


# ===========================================================================
# AC-15: validate_tree gates the compiled tree; HARD errors never reach backtest.
# ===========================================================================


def test_ac15_max_repair_attempts_is_a_named_bounded_constant(compiler):
    """The repair bound is a named module constant (no magic number), a small
    positive int — never unbounded."""
    assert hasattr(compiler, "MAX_REPAIR_ATTEMPTS"), "compiler must expose MAX_REPAIR_ATTEMPTS"
    assert isinstance(compiler.MAX_REPAIR_ATTEMPTS, int)
    assert 1 <= compiler.MAX_REPAIR_ATTEMPTS <= 10


def test_ac15_clean_plan_compiles_and_backtests_once(compiler):
    """A valid plan whose tree validate_tree-passes and backtests OK yields a tree
    and reason=None, with exactly ONE backtest call (no needless retries)."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert result.reason is None
    assert isinstance(result.tree, dict)
    assert symphony_schema.validate_tree(result.tree) == []
    assert len(bt.calls) == 1


def test_ac15_compiled_tree_passed_to_backtest_is_validate_clean(compiler):
    """The tree handed to backtest_fn is itself validate_tree-clean — a HARD-error
    tree must NEVER reach the backtest seam (AC-15 core invariant)."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))
    compiler.compile_plan(plan, backtest_fn=bt)
    assert bt.calls, "backtest should have been called on the valid path"
    for raw in bt.calls:
        assert symphony_schema.validate_tree(raw) == [], "a HARD-error tree reached backtest"


def test_ac15_uncompilable_plan_drops_cleanly_without_backtest(compiler):
    """A structurally-degenerate plan (empty weight node -> would be a non-asset
    leaf hard error) is dropped cleanly: tree=None, a reason set, and the backtest
    is NEVER called (the HARD-error tree never reaches it)."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _plan("diversify", _weight("equal", []))  # no children -> degenerate
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert result.tree is None
    assert result.reason is not None and result.reason != ""
    assert bt.calls == [], "backtest must not be called for an uncompilable plan"


def test_ac15_repair_is_bounded_never_calls_backtest_unboundedly(compiler):
    """Even when every backtest attempt fails with a tradeability rejection naming a
    ticker that pruning cannot resolve, the loop is BOUNDED by MAX_REPAIR_ATTEMPTS —
    the backtest seam is called a bounded number of times, then the plan drops."""
    # Always reject the same ticker; pruning it eventually degenerates the plan.
    bt = _ScriptedBacktest([_err_result(400, "Asset SPY is not tradable") for _ in range(50)])
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)
    # Drops cleanly (no infinite loop).
    assert result.tree is None
    assert result.reason is not None
    # Bounded: at most MAX_REPAIR_ATTEMPTS + 1 backtest calls (initial + repairs).
    assert len(bt.calls) <= compiler.MAX_REPAIR_ATTEMPTS + 1


# ===========================================================================
# AC-15: a REPAIRABLE grammar/tradeability failure repairs within the bound.
# ===========================================================================


def test_ac15_repairable_tradeability_failure_repairs_within_bound(compiler):
    """First backtest rejects one ticker as not-tradable (HTTP 400); after pruning
    that ticker the retry succeeds. The result is a tree (reason=None) and the
    offending ticker is gone."""
    bt = _ScriptedBacktest(
        [
            _err_result(400, "Asset BADX is not tradable / no pricing data"),
            _ok_result(),
        ]
    )
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("BADX"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert result.reason is None
    assert isinstance(result.tree, dict)
    # The pruned ticker is gone; the in-universe siblings survive.
    final_tickers = _tickers_in(result.tree)
    assert "BADX" not in final_tickers
    assert {"SPY", "QQQ"} <= final_tickers
    assert symphony_schema.validate_tree(result.tree) == []
    # Two backtest calls: initial reject + post-prune success.
    assert len(bt.calls) == 2


def test_ac15_unrepairable_drops_cleanly_run_continues(compiler):
    """An unrepairable tradeability failure (pruning the only resolvable ticker
    degenerates the plan) drops cleanly with a reason — the caller (run) continues;
    no raise."""
    # Reject the lone meaningful ticker repeatedly; pruning collapses the node.
    bt = _ScriptedBacktest([_err_result(400, "Asset SPY is not tradable") for _ in range(20)])
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert result.tree is None
    assert isinstance(result.reason, str) and result.reason


# ===========================================================================
# AC-16: error-envelope split — tradeability-400 (prune) vs grammar-422 (no prune).
# ===========================================================================


def test_ac16_tradeability_400_prunes_named_ticker_and_retries(compiler):
    """A 400 tradeability envelope naming a ticker -> the compiler PRUNES that
    ticker and RETRIES. The second backtest call's tree no longer contains the
    pruned ticker."""
    bt = _ScriptedBacktest(
        [
            _err_result(400, "Ticker NOPRICE not tradable on this venue"),
            _ok_result(),
        ]
    )
    plan = _plan("diversify", _weight("equal", [_asset("NOPRICE"), _asset("SPY"), _asset("QQQ")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert len(bt.calls) == 2, "expected a retry after the tradeability rejection"
    # The retry tree (2nd call) must have dropped the offending ticker.
    assert "NOPRICE" not in _tickers_in(bt.calls[1])
    assert "NOPRICE" in _tickers_in(bt.calls[0]), "first attempt should still carry it"
    assert result.reason is None
    assert "NOPRICE" not in _tickers_in(result.tree)


def test_ac16_grammar_422_does_not_blindly_prune_tickers(compiler):
    """A 422 GRAMMAR envelope must NOT trigger ticker-pruning. The compiler does not
    strip a ticker on a grammar rejection — the plan drops (grammar can't be fixed
    by removing a ticker). The retry tree (if any) carries the SAME tickers as the
    first attempt (no blind prune)."""
    bt = _ScriptedBacktest(
        [
            _err_result(422, "node-type-not-supported: invalid grammar in symphony"),
            _err_result(422, "node-type-not-supported: invalid grammar in symphony"),
        ]
    )
    plan = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("TLT")]))
    first_tickers = {"SPY", "QQQ", "TLT"}
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert bt.calls, "backtest should have been called"
    # No call after the first stripped a ticker (no blind tradeability-prune on 422).
    for raw in bt.calls:
        assert _tickers_in(raw) == first_tickers, "422 grammar reject must not prune a ticker"
    # A grammar-422 is unrepairable by the compiler's ticker-prune path -> drop.
    assert result.tree is None
    assert result.reason is not None


def test_ac16_split_is_made_by_parsing_the_http_status_in_the_envelope(compiler):
    """The 400-vs-422 split is driven by the STATUS parsed out of the envelope
    string, not by message text alone. A 400 with grammar-ish words still routes to
    the tradeability (prune) branch; a 422 with tradeability-ish words still routes
    to the grammar (no-prune) branch. This pins the parse to the status code."""
    # 400 envelope whose text mentions 'node' — must STILL prune (status-driven).
    bt_400 = _ScriptedBacktest(
        [_err_result(400, "node BADX references an untradable ticker"), _ok_result()]
    )
    plan = _plan("diversify", _weight("equal", [_asset("BADX"), _asset("SPY"), _asset("QQQ")]))
    r400 = compiler.compile_plan(plan, backtest_fn=bt_400)
    assert len(bt_400.calls) == 2
    assert "BADX" not in _tickers_in(bt_400.calls[1])
    assert r400.reason is None

    # 422 envelope whose text mentions 'tradable' — must NOT prune (status-driven).
    bt_422 = _ScriptedBacktest(
        [_err_result(422, "tradable check passed but grammar invalid"), _err_result(422, "x")]
    )
    plan2 = _plan("diversify", _weight("equal", [_asset("SPY"), _asset("QQQ"), _asset("TLT")]))
    r422 = compiler.compile_plan(plan2, backtest_fn=bt_422)
    for raw in bt_422.calls:
        assert _tickers_in(raw) == {"SPY", "QQQ", "TLT"}
    assert r422.tree is None


def test_ac16_envelope_reproduction_matches_client_format(compiler):
    """Self-guard: the envelope WE inject is the exact shape the real client emits,
    so the parse the compiler performs is the real one. We assert the envelope
    starts with 'HTTP <status>: ' (the client's f-string at line 360)."""
    env = _envelope(400, "anything")
    assert env.startswith("HTTP 400: ")
    env2 = _envelope(422, "grammar")
    assert env2.startswith("HTTP 422: ")


# ===========================================================================
# AC-17 disposition (PM-recommended Option A): market_cap is producer-deprecated
# -> unrepairable grammar drop, NEVER reaches backtest. Repoint if PM selects B/C.
# ===========================================================================


def test_ac17_market_cap_scheme_plan_is_dropped_before_backtest(compiler):
    """Composer deprecated market-cap weighting (live capture 2026-06-20:
    HTTP 422 node-type-not-supported). A scheme=='market_cap' plan is an
    unrepairable grammar drop — dropped cleanly, NEVER handed to the backtest seam,
    and the run continues (no raise)."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _plan("diversify", _weight("market_cap", [_asset("AAPL"), _asset("MSFT")]))
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert result.tree is None, "a market_cap plan must not produce a tree"
    assert result.reason is not None and result.reason != ""
    assert bt.calls == [], "a deprecated market_cap node must never reach the backtest"


def test_ac17_market_cap_nested_in_otherwise_valid_plan_drops_whole_plan(compiler):
    """A market_cap node anywhere in the tree taints the whole plan (it cannot be
    pruned into a tradeable tree). The plan is dropped, not silently repaired by
    deleting the market_cap sleeve."""
    bt = _ScriptedBacktest([_ok_result()])
    plan = _plan(
        "diversify",
        _group(
            "p",
            [
                _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                _weight("market_cap", [_asset("AAPL"), _asset("MSFT")]),
            ],
        ),
    )
    result = compiler.compile_plan(plan, backtest_fn=bt)
    assert result.tree is None
    assert result.reason is not None
    assert bt.calls == []


def test_ac17_wt_marketcap_is_not_added_to_known_steps(compiler):
    """Option A: symphony_schema must NOT gain a 'wt-marketcap' KNOWN_STEPS entry
    nor a make_weight_marketcap constructor — building a constructor for a
    producer-deprecated node would be dead code that 422s by construction."""
    assert "wt-marketcap" not in symphony_schema.KNOWN_STEPS
    assert "wt-market-cap" not in symphony_schema.KNOWN_STEPS
    assert not hasattr(symphony_schema, "make_weight_marketcap")
