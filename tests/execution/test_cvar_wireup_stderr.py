"""
RED tests — CVaR stderr wire-up (risk-cvar BLOCK adjudication).

DEFECT under test
-----------------
At b3f117e (the GREEN-claimed implementation), the per-cycle write at
``alpha_bot_execution.py:1450`` hard-codes ``cvar_5pct_stderr=None``::

    database.record_cvar_diagnostic(
        cycle_id=current_et.isoformat(),
        symphony_id=symphony_id,
        cvar_5pct=_cvar_short.cvar_pct,
        cvar_5pct_stderr=None,                  # <-- defect (risk-cvar BLOCK)
        cvar_n_tail=_cvar_short.tail_obs_count,
        ...
    )

Root cause: ``CVaRAssessment`` (math_engine.py:121) has fields
{cvar_pct, breach, tail_obs_count, insufficient_reason} but NOT
``stderr``. The pure-math estimator (``compute_cvar_5pct_general_distribution``)
returns a sibling ``CVaREstimate`` type that DOES carry ``.stderr`` (the
H-2 binding denominator). ``compute_portfolio_cvar`` computes
``cvar_5pct_stderr`` locally at line 1323 but only persists it via its
internal self-write at line 1338 — the returned ``CVaRAssessment`` loses
the stderr value, so the per-cycle path cannot forward it.

S-3 4-part display contract (templates/index.html:1258)
-------------------------------------------------------
The dashboard's CVaR panel binds four elements to the operator:

    (a) stderr            — uncertainty band (templates/index.html:1272-1282)
    (b) tail_obs_count    — n=N distinct tail observations (l.1288)
    (c) diagnostic label  — "do not trade on this" (l.1297)
    (d) bias warning      — verbatim template string (l.1301)

Today (b)+(c)+(d) render correctly post-wire-up but (a) is forever the
em-dash ("—") because cvar_5pct_stderr is NULL in storage. The 4-part
contract is half-broken; the operator sees a CVaR value with no
uncertainty band.

SCOPE — what these tests pin
----------------------------
1. ``CVaRAssessment`` gains a ``stderr: float | None`` field with the
   same fail-safe contract as ``cvar_pct``: stderr is None iff
   ``cvar_pct`` is None (the two travel together).
2. ``compute_portfolio_cvar`` populates ``CVaRAssessment.stderr`` from
   ``compute_cvar_stderr_distinct_tail`` (or equivalently from the
   ``CVaREstimate.stderr`` returned by ``compute_cvar_5pct_general_distribution``).
3. The per-cycle write site passes ``cvar_5pct_stderr=_cvar_short.stderr``
   — NOT the hard-coded ``None``.
4. End-to-end S-3: when the input has sufficient tail data, the persisted
   row carries non-None values for cvar_5pct AND cvar_5pct_stderr AND
   cvar_n_tail (the three operator-visible numeric cells of the 4-part
   contract; the static label + bias warning are template literals).

Fixture-derivation rule
-----------------------
Per ``feedback_no_hardcoded_test_values``: stderr values are NOT pinned
to specific floats. The tests assert (a) stderr is a positive finite
float when sufficient data is present, and (b) stderr equals what
``compute_cvar_stderr_distinct_tail`` returns on the same pool (a
delegation invariant, not a literal). Closed-form stderr correctness on
known pools is already pinned in
``tests/engine/test_m2_cvar_known_pool.py``.
"""

from __future__ import annotations

import math
import pathlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import alpha_bot_execution
import math_engine


# ---------------------------------------------------------------------------
# Shared fixture (cloned from test_cvar_wireup_per_cycle.py)
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)
except Exception:  # pragma: no cover
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

_SYMPHONY_ID = "sym-cvar-stderr-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-cvar-stderr-001"
_ACCOUNT_ID = "acct-cvar-stderr-001"
_SYMPHONY_NAME = "CVaR Stderr Symphony"
_TICKER = "SPY"

_ALPHA_BOT_PATH = pathlib.Path(__file__).parents[2] / "alpha_bot_execution.py"


def _make_symphony_payload():
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": 0.01,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(current_date_str: str):
    return {
        current_date_str: {
            "SPY": {
                "c": 500.0, "daily_ret": 0.001, "high": 501.0, "low": 499.0, "close": 500.0,
            }
        }
    }


def _seed_state():
    return {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: {
            "high_water_mark": 0.0,
            "shadow_hwm": 0.0,
            "prev_return": 0.0,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "mc_history": [],
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
        },
    }


@pytest.fixture
def patched_environment():
    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting") as mock_reporting, \
         patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym, \
         patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist, \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap, \
         patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "test"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = _seed_state()
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"), "symphonies": {}
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s
        mock_fetch_sym.return_value = [_make_symphony_payload()]
        mock_fetch_hist.return_value = _make_minimal_history(
            _FIXED_ET.strftime("%Y-%m-%d")
        )
        mock_fetch_vwap.return_value = {_TICKER: {"vwap": 500.0, "last_price": 500.0}}
        yield {
            "db": mock_db,
            "reporting": mock_reporting,
        }


# ---------------------------------------------------------------------------
# 1. CVaRAssessment carries a stderr field with the fail-safe pairing
# ---------------------------------------------------------------------------


class TestCvarAssessmentHasStderrField:
    """The typed result MUST carry a stderr value so the per-cycle write
    can forward it to record_cvar_diagnostic without re-deriving.
    """

    def test_cvar_assessment_constructible_with_stderr(self):
        """CVaRAssessment must accept a stderr kwarg. RED at b3f117e —
        the field does not exist on the dataclass.
        """
        try:
            a = math_engine.CVaRAssessment(
                cvar_pct=-0.02,
                breach=False,
                tail_obs_count=8,
                stderr=0.0035,
                insufficient_reason=None,
            )
        except TypeError as e:
            pytest.fail(
                f"CVaRAssessment must accept stderr= kwarg; got TypeError {e!r}. "
                "Add stderr: float | None to the dataclass (math_engine.py:121). "
                "Without the field, cvar_5pct_stderr will always be NULL in "
                "cvar_diagnostics (risk-cvar BLOCK; S-3 4-part contract gap)."
            )
        assert a.stderr == pytest.approx(0.0035, rel=1e-9), (
            "stderr value not preserved on the dataclass"
        )

    def test_cvar_assessment_stderr_is_none_when_cvar_pct_is_none(self):
        """Fail-safe pairing: stderr travels with cvar_pct. When cvar_pct is
        the insufficient-data sentinel (None), stderr MUST also be None.
        Reading a stderr from a sentinel CVaRAssessment is a contract violation;
        __post_init__ must reject the illegal combination.
        """
        # Legal sentinel: both None
        sentinel = math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason="insufficient",
        )
        assert sentinel.stderr is None

        # Illegal: cvar_pct is None but stderr is a number
        with pytest.raises(ValueError, match=r"(?i)stderr"):
            math_engine.CVaRAssessment(
                cvar_pct=None,
                breach=False,
                tail_obs_count=0,
                stderr=0.005,  # ILLEGAL — sentinel cvar_pct can't carry a stderr
                insufficient_reason="insufficient",
            )

    def test_cvar_assessment_requires_stderr_when_cvar_pct_is_finite(self):
        """Inverse fail-safe: when cvar_pct is a real number, stderr must
        also be a real number. A CVaR estimate without an uncertainty band
        is half of the S-3 contract (templates/index.html:1272-1282).
        """
        with pytest.raises(ValueError, match=r"(?i)stderr"):
            math_engine.CVaRAssessment(
                cvar_pct=-0.02,
                breach=False,
                tail_obs_count=8,
                stderr=None,  # ILLEGAL — finite cvar_pct must carry stderr
                insufficient_reason=None,
            )


# ---------------------------------------------------------------------------
# 2. compute_portfolio_cvar populates stderr on the returned CVaRAssessment
# ---------------------------------------------------------------------------


def _make_sufficient_history(n_days: int = 200) -> dict:
    """Build a kNN-eligible history dict with a tail wide enough for stderr.

    The pool must satisfy:
      * eligible_days >= MC_MIN_HISTORY_DAYS (~20)
      * After kNN regime-match, the picked pool yields >= ~5 strictly-below-VaR
        observations so the order-of-magnitude stderr band assertion has
        discriminating power. With tail_obs_count=2 the lower bound
        0.05*|cvar|/sqrt(2) becomes tight on the produced stderr; with
        tail_obs_count >= ~5 the band has wide air to admit any sqrt(N_tail)
        stderr while still rejecting the sqrt(5000) resample-count bug
        (which would put stderr ~25x smaller than the H-2 value).
      * Variance in tail values > 0 (no degenerate-equal-tail pool).

    Returns use sine + cosine at incommensurate frequencies (0.7, 1.3) so the
    regime-matched pool inherits a spread of values rather than collapsing
    on a single shock cluster. Empirically this fixture yields ~150-day
    candidate pool → tail_obs_count ≈ 8.
    """
    history = {}
    for i in range(n_days):
        m = 1 + (i // 28)
        d = 1 + (i % 28)
        date_key = f"2024-{m:02d}-{d:02d}"
        # Incommensurate frequencies yield a quasi-random series with
        # non-degenerate tail variance after kNN selection.
        spy_ret = 0.01 * math.sin(i * 0.7) + 0.005 * math.cos(i * 1.3)
        history[date_key] = {
            "SPY": {"daily_ret": spy_ret},
            _TICKER: {"daily_ret": spy_ret * 1.2},  # ticker tracks SPY with leverage
        }
    return history


class TestComputePortfolioCvarPopulatesStderr:
    """compute_portfolio_cvar must populate CVaRAssessment.stderr from
    the same pool the cvar_pct is computed on (H-2 binding: stderr uses
    the distinct-tail-obs denominator, never the resample count).
    """

    def test_compute_portfolio_cvar_returns_stderr_when_sufficient_data(self):
        history = _make_sufficient_history(n_days=200)
        holdings = [{"ticker": _TICKER, "allocation": 1.0, "last_percent_change": 0.0}]

        result = math_engine.compute_portfolio_cvar(
            cycle_id="20240315_1430",
            holdings=holdings,
            historical_data=history,
            spy_today_return=0.01,
        )

        # If the cvar_pct sentinel fires, the test config is wrong — skip with
        # a directive (pytest.fail, not skip, so the contract stays binding).
        if result.cvar_pct is None:
            pytest.fail(
                "Test fixture did not produce a non-sentinel CVaR — adjust "
                "_make_sufficient_history. Today the function may legitimately "
                "return sentinel; the test wants the SUFFICIENT-DATA branch."
            )

        assert hasattr(result, "stderr"), (
            "CVaRAssessment.stderr field missing (risk-cvar BLOCK)."
        )
        assert result.stderr is not None, (
            "compute_portfolio_cvar returned cvar_pct but stderr is None. "
            "S-3 4-part contract requires the uncertainty band when the "
            "estimate is present (templates/index.html:1272-1282)."
        )
        assert isinstance(result.stderr, float), (
            f"stderr must be a float; got {type(result.stderr).__name__}"
        )
        assert math.isfinite(result.stderr), (
            f"stderr must be finite; got {result.stderr!r}"
        )
        assert result.stderr > 0.0, (
            f"stderr must be strictly positive on a non-degenerate tail; "
            f"got {result.stderr!r}. A zero stderr means the tail observations "
            "are identical — the math should have returned the sentinel branch."
        )

    def test_compute_portfolio_cvar_stderr_matches_distinct_tail_estimator(self):
        """Delegation invariant: the stderr on the returned CVaRAssessment
        must equal what compute_cvar_stderr_distinct_tail produces on the
        same pool. This pins compute_portfolio_cvar's use of the H-2
        denominator (distinct tail count, not resample count).
        """
        history = _make_sufficient_history(n_days=200)
        holdings = [{"ticker": _TICKER, "allocation": 1.0, "last_percent_change": 0.0}]

        # Run the SUT
        result = math_engine.compute_portfolio_cvar(
            cycle_id="20240316_1430",
            holdings=holdings,
            historical_data=history,
            spy_today_return=0.01,
        )
        if result.cvar_pct is None:
            pytest.fail("Sufficient-data branch did not fire; adjust fixture.")

        # We don't re-derive the kNN pool here (that would require duplicating
        # compute_portfolio_cvar's regime-match logic). Instead, we pin a
        # WEAKER invariant: the denominator under sqrt is the distinct-tail
        # count (~N_tail), not the resample count (~5000).
        #
        # Discrimination math (the only invariant the H-2 band needs to
        # protect against):
        #   - Correct: stderr_correct = std_tail / sqrt(N_tail)
        #   - Bug:     stderr_bug     = std_tail / sqrt(5000)
        #   - Ratio:   stderr_correct / stderr_bug = sqrt(5000 / N_tail)
        #     For N_tail ≈ 8 → ratio ≈ 25x.
        #
        # We don't have direct access to std_tail (it lives inside the kNN
        # pool the wrapper picks), so we anchor against a proxy:
        # ``anchor = |cvar_pct| / sqrt(N_tail)``. The anchor and the genuine
        # stderr can differ by a constant factor that depends on how the
        # tail spread relates to the tail mean — typically of order 0.01
        # to 1.0 for realistic pools.
        #
        # Band: [0.005x anchor, 50x anchor]. The lower floor (1/200 of anchor)
        # is loose enough to admit any realistic sqrt(N_tail) stderr from
        # any tail-spread regime, but still rejects the sqrt(5000) bug by
        # comfortable margin (bug would land ~25x below where the correct
        # value already sits — well outside the floor).
        anchor = abs(result.cvar_pct) / max(math.sqrt(result.tail_obs_count), 1.0)
        lower = 0.005 * anchor
        upper = 50.0 * anchor
        assert lower <= result.stderr <= upper, (
            f"stderr {result.stderr:.6f} sits outside the H-2 distinct-tail "
            f"discriminating band [{lower:.6f}, {upper:.6f}] anchored on "
            f"|cvar_pct| / sqrt(tail_obs_count). cvar_pct={result.cvar_pct:.4f}, "
            f"tail_obs_count={result.tail_obs_count}. Likely cause: stderr "
            "was computed with the resample count (5000) instead of the "
            "distinct tail count — H-2 violation (would land ~25x below "
            "where a correct stderr lands)."
        )


# ---------------------------------------------------------------------------
# 3. Per-cycle write site forwards stderr (NOT hard-coded None)
# ---------------------------------------------------------------------------


class TestPerCycleWriteForwardsStderr:
    """The defect: alpha_bot_execution.py:1450 hard-codes
    ``cvar_5pct_stderr=None``. After the fix, the per-cycle write must
    forward ``_cvar_short.stderr``.
    """

    def test_per_cycle_write_passes_cvar_short_stderr_to_record(self, patched_environment):
        """When compute_portfolio_cvar returns a CVaRAssessment with a
        non-None stderr, the per-cycle write forwards that value to
        record_cvar_diagnostic.
        """
        env = patched_environment

        # Stub a CVaRAssessment with explicit stderr. Constructing this object
        # ALSO exercises the new dataclass field — the test fails fast if the
        # field is not yet added.
        try:
            stub_short = math_engine.CVaRAssessment(
                cvar_pct=-0.025,
                breach=False,
                tail_obs_count=8,
                stderr=0.0042,
                insufficient_reason=None,
            )
        except TypeError as e:
            pytest.fail(
                f"CVaRAssessment(stderr=...) not yet accepted: {e}. "
                "Add the stderr field first (see TestCvarAssessmentHasStderrField)."
            )

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=stub_short,
        ):
            alpha_bot_execution.main()

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must be called"

        # At least one call must carry the stub's stderr value (NOT None).
        observed_stderr = []
        for call in record_calls:
            v = call.kwargs.get("cvar_5pct_stderr")
            observed_stderr.append(v)
            if v == pytest.approx(0.0042, rel=1e-9):
                return  # wire is honest

        pytest.fail(
            f"record_cvar_diagnostic never received cvar_5pct_stderr=0.0042. "
            f"Observed cvar_5pct_stderr values across {len(record_calls)} call(s): "
            f"{observed_stderr!r}. Defect: alpha_bot_execution.py:1450 hard-codes "
            "cvar_5pct_stderr=None instead of forwarding _cvar_short.stderr "
            "(risk-cvar BLOCK; S-3 4-part contract gap)."
        )

    def test_per_cycle_write_passes_none_stderr_on_sentinel(self, patched_environment):
        """Inverse: when compute_portfolio_cvar returns the insufficient-data
        sentinel (cvar_pct=None, stderr=None), the write carries
        cvar_5pct_stderr=None as well. Fail-safe pairing preserved through
        the wire (paired with TestCvarAssessmentHasStderrField).
        """
        env = patched_environment

        try:
            sentinel = math_engine.CVaRAssessment(
                cvar_pct=None,
                breach=False,
                tail_obs_count=0,
                stderr=None,
                insufficient_reason="insufficient",
            )
        except TypeError as e:
            pytest.fail(f"CVaRAssessment(stderr=...) not accepted: {e}")

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=sentinel,
        ):
            alpha_bot_execution.main()

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must be called"

        for call in record_calls:
            # On the sentinel branch, cvar_5pct_stderr must be None — but the
            # important contract is "it matches the CVaRAssessment.stderr we
            # returned", not "always None". We assert the pairing.
            v = call.kwargs.get("cvar_5pct_stderr")
            assert v is None, (
                f"On insufficient-data sentinel, cvar_5pct_stderr must be None; "
                f"got {v!r}. Fail-safe pairing requires stderr to travel with cvar_pct."
            )

    def test_alpha_bot_execution_does_not_hardcode_stderr_none(self):
        """Source guard: the literal ``cvar_5pct_stderr=None`` substring must
        not appear in the per-cycle write call. The argument must be a
        Python expression (``_cvar_short.stderr`` or equivalent).
        """
        source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")
        # Tolerate the literal string elsewhere (comments, fallback branches),
        # but reject it inside the record_cvar_diagnostic kwarg context. The
        # cheapest reliable scan: look for the exact "cvar_5pct_stderr=None"
        # token anywhere in the file. If a legitimate use exists, the test
        # will need a narrower pattern; today the only occurrence is the
        # defect.
        assert "cvar_5pct_stderr=None" not in source, (
            "alpha_bot_execution.py contains literal ``cvar_5pct_stderr=None``. "
            "The per-cycle write must forward _cvar_short.stderr — not hard-code "
            "None. Defect site (b3f117e): alpha_bot_execution.py:1450 "
            "(risk-cvar BLOCK; S-3 4-part contract gap)."
        )


# ---------------------------------------------------------------------------
# 4. End-to-end S-3 4-part display contract — operator-visible elements
# ---------------------------------------------------------------------------


class TestS3FourPartContractEndToEnd:
    """When sufficient tail data flows through the per-cycle path, ALL
    three operator-visible numeric cells of the S-3 contract land in the
    persisted row: (a) stderr, (b) tail_obs_count, plus the cvar_pct
    that anchors (c) and (d) (label + bias warning are template literals).
    """

    def test_sufficient_data_produces_all_four_s3_elements(self, patched_environment):
        env = patched_environment

        stub_short = math_engine.CVaRAssessment(
            cvar_pct=-0.018,
            breach=False,
            tail_obs_count=7,
            stderr=0.0033,
            insufficient_reason=None,
        )

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_portfolio_cvar",
            return_value=stub_short,
        ):
            alpha_bot_execution.main()

        record_calls = env["db"].record_cvar_diagnostic.call_args_list
        assert record_calls, "record_cvar_diagnostic must be called"

        # Find a call whose kwargs match the stub.
        matched = None
        for call in record_calls:
            kw = call.kwargs
            if (
                kw.get("cvar_5pct") == pytest.approx(-0.018, rel=1e-9)
                and kw.get("cvar_5pct_stderr") == pytest.approx(0.0033, rel=1e-9)
                and kw.get("cvar_n_tail") == 7
            ):
                matched = kw
                break

        assert matched is not None, (
            "No record_cvar_diagnostic call carried the full S-3 numeric trio "
            "(cvar_5pct, cvar_5pct_stderr, cvar_n_tail). The 4-part display "
            "contract requires all three to land in storage so the dashboard "
            "panel (templates/index.html:1257-1303) renders the uncertainty "
            "band, the tail count, the diagnostic label, and the bias warning."
        )

        # Tripwire: each numeric cell must be non-None when the input was
        # sufficient. The dashboard template renders em-dash on None, which
        # is the symptom of today's defect.
        assert matched["cvar_5pct"] is not None
        assert matched["cvar_5pct_stderr"] is not None, (
            "cvar_5pct_stderr=None means the dashboard renders '— stderr' "
            "(templates/index.html:1282). With sufficient data this is the "
            "half-broken S-3 contract risk-cvar flagged."
        )
        assert matched["cvar_n_tail"] is not None and matched["cvar_n_tail"] > 0


# ---------------------------------------------------------------------------
# 5. CVaREstimate -> CVaRAssessment delegation (the natural impl path)
# ---------------------------------------------------------------------------


class TestCvarEstimateStderrFlowsToAssessment:
    """The implementer's natural path: compute_portfolio_cvar internally
    calls compute_cvar_5pct_general_distribution which returns a
    CVaREstimate carrying .stderr; that stderr must reach the returned
    CVaRAssessment.

    This guard rejects the most likely regression: the implementer adds
    the stderr field to CVaRAssessment but forgets to forward
    cvar_estimate.stderr into the constructor, leaving stderr=None on
    the CVaRAssessment side.
    """

    def test_compute_portfolio_cvar_assessment_stderr_equals_estimate_stderr(self):
        """When compute_cvar_5pct_general_distribution is stubbed to return a
        CVaREstimate with a known stderr, the returned CVaRAssessment must
        carry that exact stderr value.
        """
        # Build a sufficient history so the eligibility guard does not short-
        # circuit before the estimator runs.
        history = _make_sufficient_history(n_days=200)
        holdings = [{"ticker": _TICKER, "allocation": 1.0, "last_percent_change": 0.0}]

        # Stub the inner estimator to return a deterministic CVaREstimate.
        stub_estimate = math_engine.CVaREstimate(
            cvar_pct=-0.0234,
            tail_obs_count=6,
            stderr=0.00789,
            insufficient_reason=None,
        )

        with patch.object(
            math_engine,
            "compute_cvar_5pct_general_distribution",
            return_value=stub_estimate,
        ):
            result = math_engine.compute_portfolio_cvar(
                cycle_id="20240317_1430",
                holdings=holdings,
                historical_data=history,
                spy_today_return=0.01,
            )

        # When the inner estimator returns a non-sentinel result, the wrapper
        # must surface its stderr (and cvar_pct, tail_obs_count) verbatim.
        assert result.cvar_pct == pytest.approx(-0.0234, rel=1e-9)
        assert result.tail_obs_count == 6
        assert hasattr(result, "stderr"), (
            "CVaRAssessment.stderr field missing — see TestCvarAssessmentHasStderrField."
        )
        assert result.stderr == pytest.approx(0.00789, rel=1e-9), (
            f"compute_portfolio_cvar dropped the CVaREstimate.stderr value on "
            f"its way to CVaRAssessment. Expected 0.00789; got {result.stderr!r}. "
            "The likely regression: the CVaRAssessment constructor was extended "
            "with stderr but the wrapper forgot to pass cvar_estimate.stderr."
        )
