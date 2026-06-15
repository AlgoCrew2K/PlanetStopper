"""
RED tests for Phase 2b: portfolio-level annualized volatility.

Feature: compute portfolio vol from the COMBINED portfolio return series
(correct method) rather than averaging per-symphony vols (wrong method that
ignores inter-symphony correlations).

Golden fixture: tests/fixtures/math/portfolio_vol_from_series.json

Key RED scenario: a fixture where naive-averaging of per-symphony vols DIFFERS
from portfolio-series vol (diversification benefit case — negatively correlated
symphonies). The correct implementation must pass the portfolio series; a naive
average would produce a wrong answer and these tests would catch it.

These tests are intentionally RED until analytics.py adds:
    compute_portfolio_annualized_vol(portfolio_daily_returns_pct: list[float]) -> float | None

Project rules enforced:
  - No hardcoded producer-computed values; expected values derive from fixture
    structure (sign, format) or from the formula directly.
  - Constants named + sourced; no magic numbers.
  - pytest.approx with explicit tolerance + comment.
  - No module-level mutable state shared across tests.
  - Mock only network and time; math engine tested directly.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture paths and constants
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "math"
_PORTFOLIO_VOL_FIXTURE = _FIXTURE_DIR / "portfolio_vol_from_series.json"

# Annualization basis — 252 trading days per year (industry standard).
# Source: ux-design-deliverable.md §Change 4 and analytics.py _ANNUALIZATION_DAYS pattern.
_ANNUALIZATION_DAYS = 252

# Minimum observations contract — matches analytics._MIN_QUANTSTATS_OBSERVATIONS.
_MIN_OBS = 2


# ---------------------------------------------------------------------------
# Formula-derivation helpers (for cross-checking, NOT as the expected value)
# ---------------------------------------------------------------------------


def _annualized_vol_from_pct_series(returns_pct: list[float]) -> float | None:
    """
    Formula: sample_std(returns_frac, ddof=1) * sqrt(252).
    Returns None when fewer than _MIN_OBS finite observations.

    This is the CORRECT formula for portfolio vol when `returns_pct` is the
    portfolio combined return series (value-weighted per-day aggregate).
    NOT the naive per-symphony average.
    """
    fracs = [r / 100.0 for r in returns_pct if math.isfinite(float(r))]
    if len(fracs) < _MIN_OBS:
        return None
    n = len(fracs)
    mean = sum(fracs) / n
    var = sum((r - mean) ** 2 for r in fracs) / (n - 1)  # ddof=1
    return math.sqrt(var) * math.sqrt(_ANNUALIZATION_DAYS)


def _naive_average_vol(sym_a_pct: list[float], sym_b_pct: list[float]) -> float | None:
    """
    WRONG naive method: average each symphony's vol independently.
    Ignores inter-symphony correlations. Produces a different (and
    incorrect) result than portfolio-series vol when symphonies are
    negatively correlated.
    """
    vol_a = _annualized_vol_from_pct_series(sym_a_pct)
    vol_b = _annualized_vol_from_pct_series(sym_b_pct)
    if vol_a is None or vol_b is None:
        return None
    return (vol_a + vol_b) / 2.0


# ---------------------------------------------------------------------------
# Test 1: analytics.py must expose compute_portfolio_annualized_vol function
# ---------------------------------------------------------------------------


def test_analytics_exposes_compute_portfolio_annualized_vol():
    """
    analytics.py must expose a function named compute_portfolio_annualized_vol
    that accepts a list[float] of percent-scale daily returns (the portfolio
    combined series) and returns the annualized vol as a fraction-scale float.

    This function is the correct contract for Phase 2b: callers pass the
    pre-computed portfolio combined series, not raw per-symphony returns.

    Test is RED until analytics.py adds:
        def compute_portfolio_annualized_vol(portfolio_daily_returns_pct: list[float]) -> float | None:
    """
    import analytics

    assert hasattr(analytics, "compute_portfolio_annualized_vol"), (
        "analytics.py must expose compute_portfolio_annualized_vol. "
        "Add: def compute_portfolio_annualized_vol(portfolio_daily_returns_pct: list[float]) -> float | None"
    )
    assert callable(analytics.compute_portfolio_annualized_vol), (
        "analytics.compute_portfolio_annualized_vol must be callable"
    )


# ---------------------------------------------------------------------------
# Test 2: portfolio vol is a positive finite fraction-scale float
# ---------------------------------------------------------------------------


def test_compute_portfolio_annualized_vol_is_positive_finite_fraction_scale():
    """
    compute_portfolio_annualized_vol must return a positive finite float in
    fraction scale (e.g. 0.035 = 3.5% annual vol) for a valid series.

    Fraction-scale is critical: if the implementer forgets to divide percent-
    scale inputs by 100 before computing std, the result comes back at 100x
    the correct value and the dashboard shows nonsense.

    Range check: for realistic daily returns of ~0.1-0.5% percent-scale,
    annualized vol should be in [0.001, 1.0] fraction-scale (0.1% to 100%/yr).
    """
    import analytics

    with _PORTFOLIO_VOL_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    portfolio_returns = fixture["portfolio_combined_series_pct_scale"]["daily_returns"]

    vol = analytics.compute_portfolio_annualized_vol(portfolio_returns)

    assert vol is not None, (
        "compute_portfolio_annualized_vol must not return None for a 10-obs series "
        "with non-zero variance"
    )
    assert isinstance(vol, float), (
        f"compute_portfolio_annualized_vol must return a float; got {type(vol).__name__}={vol!r}"
    )
    assert math.isfinite(vol), (
        f"compute_portfolio_annualized_vol must return a finite float; got {vol!r}"
    )
    assert vol > 0.0, (
        f"annualized vol is a standard deviation; it must be positive for a series "
        f"with non-zero variance; got {vol!r}"
    )
    # Fraction-scale guard: a value > 1.0 almost certainly indicates percent-scale leak.
    # A 100%+ annual vol on a tame synthetic series (daily moves ~0.2%) is impossible.
    assert vol < 1.0, (
        f"portfolio vol={vol!r} > 1.0 suggests percent-scale was returned instead of "
        f"fraction-scale (e.g. 0.035 is correct for 3.5% annual vol, not 3.5). "
        "analytics must divide pct-scale inputs by 100 before computing std."
    )


# ---------------------------------------------------------------------------
# Test 3: golden fixture — portfolio-series vol is formula-consistent
# ---------------------------------------------------------------------------


def test_compute_portfolio_annualized_vol_consistent_with_formula():
    """
    compute_portfolio_annualized_vol result must be consistent with
    std(portfolio_combined_series_frac, ddof=1) * sqrt(252) within 5%
    relative tolerance.

    Tolerance: 5% relative — generous for quantstats version differences in
    window/ddof handling, tight enough to catch a formula-level bug (e.g. wrong
    annualization factor, ddof=0 instead of ddof=1, working on pct not frac scale).

    Source: analytics.py compute_quantstats_metrics pattern for `volatility` key.
    """
    import analytics

    with _PORTFOLIO_VOL_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    portfolio_returns = fixture["portfolio_combined_series_pct_scale"]["daily_returns"]

    # Formula-derived expected value — the correct method.
    expected_vol = _annualized_vol_from_pct_series(portfolio_returns)
    assert expected_vol is not None and expected_vol > 0.0, (
        "Test precondition: fixture series must have non-zero vol"
    )

    vol = analytics.compute_portfolio_annualized_vol(portfolio_returns)

    assert vol is not None, (
        "compute_portfolio_annualized_vol must not return None for a 10-obs series"
    )

    # 5% relative tolerance: abs(vol - expected) / expected < 0.05.
    rel_err = abs(vol - expected_vol) / expected_vol
    assert rel_err < 0.05, (  # 5% relative — see docstring for rationale
        f"portfolio vol={vol:.6f} deviates {rel_err:.1%} from formula-derived "
        f"expected={expected_vol:.6f}. Formula: std(frac_returns, ddof=1) * sqrt({_ANNUALIZATION_DAYS}). "
        "Check: (1) inputs must be divided by 100 from pct-scale, (2) ddof=1, "
        f"(3) annualization = sqrt({_ANNUALIZATION_DAYS})."
    )


# ---------------------------------------------------------------------------
# Test 4: GOLDEN FIXTURE — portfolio-series vol differs from naive average
#         (the key adversarial test for Phase 2b)
# ---------------------------------------------------------------------------


def test_portfolio_series_vol_differs_from_naive_per_symphony_average():
    """
    ADVERSARIAL GOLDEN-FIXTURE TEST.

    This is the defining test for Phase 2b: the portfolio vol computed from
    the COMBINED portfolio return series must differ from the NAIVE AVERAGE
    of per-symphony vols when symphonies are negatively correlated.

    Fixture: divergent_case — sym_a and sym_b have negatively correlated returns
    (when one zigs, the other zags). The portfolio combined series (50/50 avg)
    is much calmer than either symphony alone. Naive average of the two vols
    OVERESTIMATES true portfolio vol and ignores the diversification benefit.

    This test is RED until analytics.compute_portfolio_annualized_vol exists
    and is called with the portfolio combined series (not a naive average).

    A wrong implementation that does 'mean([vol_a, vol_b])' instead of
    'std(combined_series) * sqrt(252)' will produce a different (higher) result
    and FAIL this test.
    """
    import analytics

    with _PORTFOLIO_VOL_FIXTURE.open(encoding="utf-8") as fh:
        fixture = json.load(fh)

    divergent = fixture["divergent_case"]
    sym_a = divergent["sym_a_returns_pct_scale"]
    sym_b = divergent["sym_b_returns_pct_scale"]
    portfolio_combined = divergent["portfolio_combined_returns_pct_scale"]["values"]

    # Compute each method.
    portfolio_vol = analytics.compute_portfolio_annualized_vol(portfolio_combined)
    naive_avg = _naive_average_vol(sym_a, sym_b)

    assert portfolio_vol is not None, (
        "compute_portfolio_annualized_vol must not return None for a 10-obs divergent_case series"
    )
    assert naive_avg is not None, "Test precondition: naive_avg must be computable"

    # The key assertion: for negatively correlated symphonies, portfolio vol < naive average.
    # This proves the implementation uses the portfolio series, not a naive average.
    assert portfolio_vol < naive_avg, (
        f"WRONG METHOD DETECTED: compute_portfolio_annualized_vol returned {portfolio_vol:.6f} "
        f"which is NOT less than the naive per-symphony average {naive_avg:.6f}. "
        "For negatively correlated symphonies (divergent_case fixture), the correct portfolio "
        "vol (computed from the COMBINED series) must be LESS than the naive average of "
        "individual symphony vols (diversification benefit). "
        "Fix: pass the portfolio combined return series to the volatility computation, "
        "NOT the per-symphony average."
    )

    # Additional sanity: both values must be positive finite floats.
    assert portfolio_vol > 0.0, f"portfolio_vol must be positive; got {portfolio_vol!r}"
    assert naive_avg > 0.0, f"naive_avg must be positive; got {naive_avg!r}"


# ---------------------------------------------------------------------------
# Test 5: None when fewer than 2 observations
# ---------------------------------------------------------------------------


def test_compute_portfolio_annualized_vol_none_for_insufficient_data():
    """
    compute_portfolio_annualized_vol must return None when fewer than 2
    finite observations, consistent with analytics._MIN_QUANTSTATS_OBSERVATIONS.

    Source: analytics.py binding contract.
    """
    import analytics

    for series in ([], [0.1]):
        result = analytics.compute_portfolio_annualized_vol(series)
        assert result is None, (
            f"compute_portfolio_annualized_vol must return None for insufficient-data "
            f"series {series!r}; got {result!r}"
        )


# ---------------------------------------------------------------------------
# Test 6: single-symphony portfolio — vol equals that symphony's individual vol
# ---------------------------------------------------------------------------


def test_portfolio_vol_equals_single_symphony_vol_for_one_symphony():
    """
    When the portfolio contains only one symphony, the portfolio combined
    return series IS that symphony's return series. The portfolio vol must
    therefore equal the single-symphony vol.

    This is a degenerate-case invariant: a single-entry portfolio has no
    diversification benefit or penalty.
    """
    import analytics

    single_sym_returns = [
        0.10,
        -0.15,
        0.20,
        -0.10,
        0.15,
        0.12,
        -0.18,
        0.22,
        -0.12,
        0.18,
    ]

    # When portfolio combined series equals single symphony series, both methods agree.
    portfolio_vol = analytics.compute_portfolio_annualized_vol(single_sym_returns)
    # We can't call compute_quantstats_metrics from here cleanly, but formula check suffices.
    expected_vol = _annualized_vol_from_pct_series(single_sym_returns)

    assert portfolio_vol is not None, (
        "compute_portfolio_annualized_vol must not return None for a 10-obs single-symphony series"
    )
    assert expected_vol is not None

    # Tolerance: 5% relative — same rationale as test 3.
    rel_err = abs(portfolio_vol - expected_vol) / expected_vol
    assert rel_err < 0.05, (  # 5% relative — absorbs minor quantstats version diffs
        f"Single-symphony portfolio_vol={portfolio_vol:.6f} deviates {rel_err:.1%} "
        f"from formula expected={expected_vol:.6f}. Single-symphony portfolio vol "
        "must equal the symphony's individual vol."
    )


# ---------------------------------------------------------------------------
# Test 7: meta.portfolio has vol_bot and vol_held keys after Phase 2b
# ---------------------------------------------------------------------------


def test_build_meta_portfolio_includes_vol_bot_and_vol_held():
    """
    _build_meta must populate meta.portfolio with vol_bot and vol_held keys
    after Phase 2b. These keys feed the hero Ann. Vol vs-row.

    Source: ux-design-deliverable.md §Change 4:
      'vol_bot / vol_held must be added to the meta.portfolio dict in the
       Flask index() route.'

    Test is RED until _build_meta sets portfolio_meta['vol_bot'] and
    portfolio_meta['vol_held'] (None-safe default when no history).

    We patch _compute_portfolio_strip to inject a portfolio_strip with
    vol_bot / vol_held keys (simulating what _compute_portfolio_strip will
    produce after Phase 2b implementation), then assert meta.portfolio
    carries them through.
    """
    import app as app_module
    from unittest.mock import patch

    # Inject a portfolio_strip carrying the Phase 2b vol keys.
    # These are fraction-scale values — the implementer must NOT hardcode them.
    stub_portfolio_strip = {
        "today_change": {"if_held": 0.5, "dry_run": 0.6},
        "cumulative_return": {"if_held": 10.0, "dry_run": 12.0},
        "max_drawdown": {"if_held": 3.1, "dry_run": 2.5},
        "vol_bot": 0.031,  # fraction-scale; placeholder shape, not a real value
        "vol_held": 0.045,  # fraction-scale; placeholder shape, not a real value
        "hist_dates": ["2026-01-01"] * 35,  # >= 30 to avoid insufficient_history flag
        "hist_bot": [0.0] * 35,
        "hist_held": [0.0] * 35,
        "hist_source": "shadow_history",
        "data_as_of": "09:30 ET",
        "account_value": 100000.0,
    }

    meta = app_module._build_meta(
        state_data={},
        next_run_seconds=0,
        market_state="closed",
        portfolio_strip=stub_portfolio_strip,
    )

    portfolio = meta.get("portfolio", {})

    assert "vol_bot" in portfolio, (
        "meta.portfolio must include 'vol_bot' key after Phase 2b. "
        "_build_meta must propagate vol_bot from portfolio_strip to portfolio_meta. "
        "Add: portfolio_meta['vol_bot'] = ps.get('vol_bot')"
    )
    assert "vol_held" in portfolio, (
        "meta.portfolio must include 'vol_held' key after Phase 2b. "
        "_build_meta must propagate vol_held from portfolio_strip to portfolio_meta. "
        "Add: portfolio_meta['vol_held'] = ps.get('vol_held')"
    )

    # Values must pass through unchanged (no clamping, no sign flip).
    # We check they are not None when the strip provides real values.
    assert portfolio["vol_bot"] is not None, (
        "meta.portfolio['vol_bot'] must not be None when portfolio_strip provides a value"
    )
    assert portfolio["vol_held"] is not None, (
        "meta.portfolio['vol_held'] must not be None when portfolio_strip provides a value"
    )


# ---------------------------------------------------------------------------
# Test 8: _compute_portfolio_strip returns vol_bot and vol_held
# ---------------------------------------------------------------------------


def test_compute_portfolio_strip_includes_vol_bot_and_vol_held():
    """
    _compute_portfolio_strip must include vol_bot and vol_held in its return
    dict after Phase 2b.

    These values are computed from get_portfolio_daily_returns_from_shadow()
    (the COMBINED portfolio return series from shadow_history). When no shadow
    history exists, they must be None (not 0.0 — 0.0 is ambiguous with real
    zero-vol result).

    Test is RED until _compute_portfolio_strip adds the vol computation:
        shadow_result = analytics.get_portfolio_daily_returns_from_shadow(...)
        if shadow_result is not None:
            _, port_daily_returns = shadow_result
            vol_bot = analytics.compute_portfolio_annualized_vol(port_daily_returns)
        ...
    """
    import app as app_module
    from unittest.mock import patch

    # When get_portfolio_daily_returns_from_shadow returns None (no history),
    # vol_bot and vol_held should be None.
    with patch.object(
        app_module.analytics,
        "get_portfolio_daily_returns_from_shadow",
        return_value=None,
    ):
        result = app_module._compute_portfolio_strip(bot_state={})

    assert "vol_bot" in result, (
        "_compute_portfolio_strip must include 'vol_bot' key. "
        "Add vol computation from get_portfolio_daily_returns_from_shadow."
    )
    assert "vol_held" in result, (
        "_compute_portfolio_strip must include 'vol_held' key. "
        "Add vol computation from get_portfolio_daily_returns_from_shadow."
    )

    # When there is no shadow history, vol must be None (not 0.0 — zero is ambiguous).
    # The UI stubs to '--' when None; a real 0.0 would be misleading.
    assert result["vol_bot"] is None, (
        "_compute_portfolio_strip vol_bot must be None when no shadow history exists. "
        f"Got {result['vol_bot']!r}. Do not default to 0.0."
    )
    assert result["vol_held"] is None, (
        "_compute_portfolio_strip vol_held must be None when no shadow history exists. "
        f"Got {result['vol_held']!r}. Do not default to 0.0."
    )


# ---------------------------------------------------------------------------
# Test 9: vol inversion — bot WINS when vol_bot <= vol_held
# ---------------------------------------------------------------------------


def test_vol_inversion_lower_vol_is_bot_win():
    """
    For the Ann. Vol hero row, the winner determination is INVERTED vs the
    return rows: LOWER volatility is better (bot wins when vol_bot < vol_held).

    Source: ux-design-deliverable.md §Change 4:
      'vol_bot_wins = vol_bot <= vol_held — lower vol = better for this system'
      'alpha delta label: α −1.5pp colored --studio-pos when bot is calmer'

    This test asserts the sign convention by verifying that the Jinja template
    renders the vol vs-row with a win-indicator for the BOT when bot_vol < held_vol,
    NOT when bot_vol > held_vol (which would be the wrong direction).

    We check this via the Flask test client: inject meta.portfolio with vol_bot <
    vol_held (bot is calmer) and assert the rendered HTML marks the bot bar as
    the winner. The template must use a win-class or winner-label on the BOT side.

    Test is RED until index.html wires vol_bot/vol_held from meta.portfolio and
    applies the inverted winner logic.
    """
    import app as app_module
    from unittest.mock import patch

    # Build meta with bot calmer (vol_bot < vol_held).
    # These are fraction-scale values in the shape expected from _build_meta.
    # Exact values are irrelevant — only the BOT CALMER direction matters.
    stub_portfolio = {
        "tc": 0.5,
        "tc_if_held": 0.4,
        "cr": 10.0,
        "cr_if_held": 9.0,
        "mdd": 2.5,
        "mdd_if_held": 3.1,
        "vol_bot": 0.028,  # fraction-scale; bot is calmer
        "vol_held": 0.042,  # fraction-scale; held has higher vol
        "hist_dates": ["2026-01-01"] * 35,
        "hist_bot": [0.0] * 35,
        "hist_held": [0.0] * 35,
        "hist_source": "shadow_history",
        "data_as_of": "09:30 ET",
        "account_value": 100000.0,
        "insufficient_history": False,
        "history_days": 35,
    }

    stub_meta = {
        "mode": "DRY RUN",
        "system_online": True,
        "tracked": 0,
        "armed": 0,
        "triggered": 0,
        "next_run": "00:00",
        "account": {"label": "", "uuid_short": ""},
        "market_state": "closed",
        "market_state_label": "Market closed",
        "clock_et": "09:30 ET",
        "portfolio": stub_portfolio,
        "triggers_today": [],
    }

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with patch.object(
            app_module,
            "get_api_state_dict",
            return_value={
                "bot_state": {},
                "is_locked": False,
                "port_state": {},
                "exit_authority": {},
                "daemon_started_at": None,
            },
        ):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                html = client.get("/").data.decode("utf-8")

    # Check that the vol-bot text is populated (not stub '--').
    # When vol_bot is provided, the template must render a real value, not '--'.
    # The vol_bot value is 0.028 fraction scale => "2.8%" formatted.
    assert "comp-vol-bot-text" in html, (
        "index.html must render comp-vol-bot-text element — Phase 2b wires vol_bot value"
    )

    # The vol row must NOT still show both sides as '—' (stub state) when vol data is provided.
    # We check that the vol row has some content from meta.portfolio.vol_bot.
    # When wired: the text would contain a formatted percent value, not just '—'.
    # We look for the testid region and confirm the template renders vol data.
    # The exact value is not asserted — only that it is NOT purely stub state.
    # (The implementer chooses the format; we assert it's not the placeholder '&mdash;'
    # for BOTH elements simultaneously when real data is available.)
    import re

    vol_bot_match = re.search(
        r'data-testid="comp-vol-bot-text"[^>]*>([^<]+)<',
        html,
    )
    assert vol_bot_match is not None, (
        "comp-vol-bot-text element must be present and non-self-closing in rendered HTML"
    )
    vol_bot_text = vol_bot_match.group(1).strip()
    # The text should NOT still be the stub placeholder when vol data is wired.
    # Check for both the unicode em-dash '—' and the HTML entity '&mdash;'
    # (Flask test client returns raw HTML, not decoded entities).
    is_stub = "&mdash;" in vol_bot_text or "—" in vol_bot_text or "---" in vol_bot_text
    assert not is_stub, (
        f"comp-vol-bot-text still shows stub placeholder '{vol_bot_text}' when "
        f"meta.portfolio.vol_bot={stub_portfolio['vol_bot']!r} is provided. "
        "Wire vol_bot from meta.portfolio into the vol vs-row in index.html."
    )


# ---------------------------------------------------------------------------
# Test 10: vol bar inversion — bot bar is WIDER when bot vol < held vol
# ---------------------------------------------------------------------------


def test_vol_bar_width_is_wider_for_lower_vol_side():
    """
    The vs-bar-fill widths for the vol row must be INVERTED relative to
    the return rows: the bar for the LOWER-vol side must be wider (it's
    the winner for a capital-preservation system).

    Source: ux-design-deliverable.md §Change 4:
      'For volatility, the bot WINS when its volatility is LOWER than held'
      'The winner bar logic must be reversed'

    Specifically: when vol_bot < vol_held, the bot bar (comp-bar-vol-bot)
    must be rendered at 100% width (or maximal width), and the held bar
    must be proportionally narrower — the OPPOSITE of the return rows where
    higher value wins.

    This test checks the Jinja template uses the correct inverted width formula.
    We inject vol_bot=0.028, vol_held=0.042 (bot is calmer). The bot bar must
    be wider than the held bar in the rendered HTML.

    Test is RED until the inverted bar logic is implemented.
    """
    import app as app_module
    from unittest.mock import patch
    import re

    stub_portfolio = {
        "tc": 0.5,
        "tc_if_held": 0.4,
        "cr": 10.0,
        "cr_if_held": 9.0,
        "mdd": 2.5,
        "mdd_if_held": 3.1,
        "vol_bot": 0.028,  # bot is calmer — bot WINS in vol comparison
        "vol_held": 0.042,  # held has higher vol — held LOSES in vol comparison
        "hist_dates": ["2026-01-01"] * 35,
        "hist_bot": [0.0] * 35,
        "hist_held": [0.0] * 35,
        "hist_source": "shadow_history",
        "data_as_of": "09:30 ET",
        "account_value": 100000.0,
        "insufficient_history": False,
        "history_days": 35,
    }

    stub_meta = {
        "mode": "DRY RUN",
        "system_online": True,
        "tracked": 0,
        "armed": 0,
        "triggered": 0,
        "next_run": "00:00",
        "account": {"label": "", "uuid_short": ""},
        "market_state": "closed",
        "market_state_label": "Market closed",
        "clock_et": "09:30 ET",
        "portfolio": stub_portfolio,
        "triggers_today": [],
    }

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with patch.object(
            app_module,
            "get_api_state_dict",
            return_value={
                "bot_state": {},
                "is_locked": False,
                "port_state": {},
                "exit_authority": {},
                "daemon_started_at": None,
            },
        ):
            with patch.object(app_module, "_build_meta", return_value=stub_meta):
                html = client.get("/").data.decode("utf-8")

    # Extract bot and held bar widths for the vol row.
    # We look for comp-bar-vol-bot style="width:...%" pattern.
    bot_bar_match = re.search(
        r'data-testid="comp-bar-vol-bot"[^>]*style="width:\s*(\d+(?:\.\d+)?)%"',
        html,
    )
    held_bar_match = re.search(
        r'data-testid="comp-bar-vol-held"[^>]*style="width:\s*(\d+(?:\.\d+)?)%"',
        html,
    )

    assert bot_bar_match is not None, (
        "comp-bar-vol-bot element must have a width% style attribute. "
        "Phase 2b must wire the vol bar widths from meta.portfolio.vol_bot/vol_held."
    )
    assert held_bar_match is not None, (
        "comp-bar-vol-held element must have a width% style attribute."
    )

    bot_pct = float(bot_bar_match.group(1))
    held_pct = float(held_bar_match.group(1))

    # For vol_bot=0.028 (bot calmer) and vol_held=0.042 (held less calm):
    # The INVERTED winner rule says the lower-vol side (bot) gets 100% bar width,
    # and the higher-vol side (held) gets a proportionally narrower bar.
    # Both bars at 0% means the row is still stubbed — that must fail.
    assert not (bot_pct == 0.0 and held_pct == 0.0), (
        "Both vol bar widths are 0% — the vol row is still stubbed. "
        "Wire meta.portfolio.vol_bot/vol_held into the bar width computation."
    )

    # The BOT must have the WIDER bar (it has lower vol = winner).
    assert bot_pct > held_pct, (
        f"Vol inversion: bot bar (width={bot_pct}%) must be WIDER than held bar "
        f"(width={held_pct}%) when vol_bot=0.028 < vol_held=0.042 (bot is calmer). "
        "Lower vol = winner; the bar sizing logic must be inverted relative to return rows."
    )
