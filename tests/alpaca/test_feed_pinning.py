"""
RED-phase pinning tests for the Alpaca market-data `feed=iex` fix.

CONTEXT
-------
`synthetic_history.py` (which generates training/replay data for the math
engine and Optuna walk-forward studies) calls Alpaca with `feed=iex`. The
live execution path in `alpha_bot_execution.py` omits the `feed=` parameter
entirely, which causes Alpaca to default to SIP. This creates a
training/execution feed mismatch: Optuna-tuned parameters live in the IEX
distribution but production runs on SIP. It also causes a silent failure
mode on Basic-tier accounts (SIP returns 403 -> `live_vwaps` empty -> VWAP
signals never fire, with no operator alarm).

The fix (Option A per docs/research/alpaca/feed-pinning-recommendation.md)
is to add `feed=iex` to every Alpaca market-data URL in
`alpha_bot_execution.py`. These tests PIN that fix once applied AND guard
against future drift where a new call site forgets the parameter.

DESIGN
------
- Pure static source scan. No live API calls, no math-engine mocking, no
  imports of the modules under inspection (avoids triggering daemon side
  effects on import).
- Regex line-scanning is intentionally tolerant of formatting: f-string vs
  concat vs `.format()`, single vs double quotes, extra spaces around `=`.
  We match against the raw URL string content rather than Python AST so
  that a refactor to `urllib.parse.urlencode` still works as long as the
  resulting URL contains `feed=iex`.
- The negative anti-drift test scopes ONLY to `alpha_bot_execution.py`
  source (NOT docs/research/, which contains buggy URL examples
  illustrating the current state pre-fix).

EXPECTED STATE AT AUTHORING
---------------------------
RED. `alpha_bot_execution.py` currently omits `feed=` on both call sites;
all `feed=iex`-asserting tests must fail until the production fix lands.
The consistency test on `synthetic_history.py` should pass today (acts as
an alignment pin against future drift in the OTHER direction).
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Source-file paths. Resolved once at import time; tests read fresh each run.
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALPHA_BOT_PATH = REPO_ROOT / "alpha_bot_execution.py"
SYNTHETIC_HISTORY_PATH = REPO_ROOT / "synthetic_history.py"

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
# `STOCKS_BARS_URL_LINE`: matches any source line that constructs a URL
# pointing at Alpaca's `/stocks/bars` market-data endpoint. We intentionally
# match the substring "stocks/bars" rather than the full base URL so this
# survives a future refactor that moves the base into a constant or builder.
STOCKS_BARS_URL_LINE = re.compile(r"/stocks/bars\?")

# `FEED_IEX_TOKEN`: tolerant matcher for `feed=iex` somewhere on a URL line.
# Accepts optional whitespace around `=` (defensive against future
# pre-formatted dict-style or kwarg-style URL composition).
FEED_IEX_TOKEN = re.compile(r"feed\s*=\s*iex", re.IGNORECASE)

# `TIMEFRAME_1DAY` / `TIMEFRAME_1MIN`: identify which call site a line
# represents. Used to disambiguate the daily-bars vs 1-minute-bars assertions.
TIMEFRAME_1DAY = re.compile(r"timeframe\s*=\s*1Day", re.IGNORECASE)
TIMEFRAME_1MIN = re.compile(r"timeframe\s*=\s*1Min", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_source(path: pathlib.Path) -> str:
    """Read a source file as UTF-8 text. Fails loudly if the file is missing.

    We do NOT import the module under test — importing `alpha_bot_execution`
    would trigger module-level side effects (config loading, scheduler init)
    that are inappropriate for a static-analysis test.
    """
    assert path.exists(), f"Required source file missing: {path}"
    return path.read_text(encoding="utf-8")


def _stocks_bars_url_lines(source: str) -> list[tuple[int, str]]:
    """Return (1-indexed line number, line text) for every line constructing
    a URL pointing at `/stocks/bars`.

    Comments and docstrings are intentionally NOT filtered: if a developer
    puts an Alpaca URL inside a docstring as an example, that example should
    also follow the pinning convention so it doesn't mislead future readers.
    The negative anti-drift test is the only consumer of this helper.
    """
    lines = source.splitlines()
    return [(idx + 1, line) for idx, line in enumerate(lines) if STOCKS_BARS_URL_LINE.search(line)]


# ---------------------------------------------------------------------------
# Test 1: daily-bars URL must pin feed=iex
# ---------------------------------------------------------------------------


def test_alpha_bot_daily_bars_url_pins_feed_iex():
    """The 3-year Monte Carlo history fetch must request the IEX feed.

    Math layers (`calculate_20d_vol`, `calculate_14d_atr_pct`,
    `run_monte_carlo`) are calibrated on IEX-distribution training data
    produced by `synthetic_history.py`. The live daily-bars call must use
    the same feed to keep the Optuna-tuned parameter space valid.
    """
    source = _read_source(ALPHA_BOT_PATH)
    candidates = [line for _, line in _stocks_bars_url_lines(source) if TIMEFRAME_1DAY.search(line)]

    assert candidates, (
        "Could not find any /stocks/bars URL with timeframe=1Day in "
        "alpha_bot_execution.py. Either the call site was removed or the "
        "regex needs updating to match a new construction style."
    )

    missing = [line for line in candidates if not FEED_IEX_TOKEN.search(line)]
    assert not missing, (
        "Daily-bars Alpaca URL(s) in alpha_bot_execution.py do not pin "
        "`feed=iex`. The live execution path will default to SIP, creating "
        "a training/execution feed mismatch against IEX-calibrated math "
        "layers. Offending lines:\n  " + "\n  ".join(missing)
    )


# ---------------------------------------------------------------------------
# Test 2: 1-minute-bars URL must pin feed=iex
# ---------------------------------------------------------------------------


def test_alpha_bot_minute_bars_url_pins_feed_iex():
    """The intraday VWAP fetch must request the IEX feed.

    On Basic-tier Alpaca accounts, SIP returns HTTP 403 -> `live_vwaps`
    silently empty -> `compute_vwap_signals` never fires. Operators have no
    alarm. Pinning IEX is required for the VWAP signal to function on
    Basic accounts at all, and required for distribution alignment on
    Algo Trader Plus accounts (which can access either feed).
    """
    source = _read_source(ALPHA_BOT_PATH)
    candidates = [line for _, line in _stocks_bars_url_lines(source) if TIMEFRAME_1MIN.search(line)]

    assert candidates, (
        "Could not find any /stocks/bars URL with timeframe=1Min in "
        "alpha_bot_execution.py. Either the call site was removed or the "
        "regex needs updating to match a new construction style."
    )

    missing = [line for line in candidates if not FEED_IEX_TOKEN.search(line)]
    assert not missing, (
        "Intraday 1-minute-bars Alpaca URL(s) in alpha_bot_execution.py "
        "do not pin `feed=iex`. On Basic-tier accounts this causes silent "
        "VWAP failure (SIP -> 403 -> empty live_vwaps). On Algo Trader "
        "Plus accounts it creates a training/execution distribution "
        "mismatch. Offending lines:\n  " + "\n  ".join(missing)
    )


# ---------------------------------------------------------------------------
# Test 3: anti-drift negative — no /stocks/bars URL in alpha_bot may omit feed=
# ---------------------------------------------------------------------------


def test_no_alpha_bot_stocks_bars_url_omits_feed_iex():
    """Anti-drift: every Alpaca market-data URL in alpha_bot_execution.py
    must include `feed=iex`.

    Catches the subtler regression: a future contributor adds a third call
    site (e.g., quote snapshots, additional timeframe) and forgets the
    `feed=` param. The two timeframe-specific tests above only protect the
    two known sites; this test protects ALL current and future sites.

    Scope is deliberately narrow to the production source — docs/research/
    contains illustrative examples of the buggy pre-fix URL strings and
    must NOT be scanned.
    """
    source = _read_source(ALPHA_BOT_PATH)
    url_lines = _stocks_bars_url_lines(source)

    assert url_lines, (
        "Found zero /stocks/bars URLs in alpha_bot_execution.py. Either "
        "this file no longer talks to Alpaca market-data (in which case "
        "this whole module should be deleted) or the matcher regex is "
        "broken."
    )

    offenders = [(lineno, line) for lineno, line in url_lines if not FEED_IEX_TOKEN.search(line)]
    formatted = "\n  ".join(f"L{lineno}: {line.strip()}" for lineno, line in offenders)
    assert not offenders, (
        "One or more /stocks/bars URLs in alpha_bot_execution.py omit "
        "`feed=iex`. Every Alpaca market-data call site on the live "
        "execution path must pin the IEX feed to match the IEX-trained "
        f"math engine. Offenders:\n  {formatted}"
    )


# ---------------------------------------------------------------------------
# Test 4: consistency — synthetic_history.py must also pin feed=iex
# ---------------------------------------------------------------------------


def test_synthetic_history_pins_feed_iex():
    """Alignment pin: `synthetic_history.py` (training-data producer) must
    continue to use `feed=iex`.

    If synthetic_history ever drifts to SIP (or to feed-omitted default),
    the production fix in alpha_bot_execution.py becomes the NEW mismatch.
    This test guards the reference side of the contract.
    """
    source = _read_source(SYNTHETIC_HISTORY_PATH)
    url_lines = _stocks_bars_url_lines(source)

    assert url_lines, (
        "Found zero /stocks/bars URLs in synthetic_history.py. Training-"
        "data producer no longer talks to Alpaca? Investigate before "
        "merging."
    )

    offenders = [(lineno, line) for lineno, line in url_lines if not FEED_IEX_TOKEN.search(line)]
    formatted = "\n  ".join(f"L{lineno}: {line.strip()}" for lineno, line in offenders)
    assert not offenders, (
        "synthetic_history.py has drifted: one or more /stocks/bars URLs "
        "no longer pin `feed=iex`. If this drift is intentional, the "
        "production fix in alpha_bot_execution.py must follow to the same "
        f"feed in lockstep. Offenders:\n  {formatted}"
    )


# ---------------------------------------------------------------------------
# Sanity guard — ensure the test file is not accidentally a no-op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regex,sample,should_match",
    [
        (FEED_IEX_TOKEN, "url = f'.../bars?feed=iex&x=1'", True),
        (FEED_IEX_TOKEN, "url = f'.../bars?feed = iex'", True),
        (FEED_IEX_TOKEN, "url = f'.../bars?adjustment=split'", False),
        (STOCKS_BARS_URL_LINE, "f'{BASE}/stocks/bars?symbols=AAPL'", True),
        (STOCKS_BARS_URL_LINE, "f'{BASE}/account'", False),
        (TIMEFRAME_1DAY, "...timeframe=1Day&start=...", True),
        (TIMEFRAME_1DAY, "...timeframe=1Min&start=...", False),
        (TIMEFRAME_1MIN, "...timeframe=1Min&start=...", True),
        (TIMEFRAME_1MIN, "...timeframe=1Day&start=...", False),
    ],
)
def test_regex_sanity(regex: re.Pattern[str], sample: str, should_match: bool):
    """Self-tests for the matcher regexes. If these break, the production
    pinning tests above are silently meaningless. Kept separate so a regex
    bug surfaces with a different failure signal than a real fix regression.
    """
    matched = bool(regex.search(sample))
    assert matched is should_match, (
        f"Regex {regex.pattern!r} on sample {sample!r}: "
        f"expected match={should_match}, got match={matched}"
    )
