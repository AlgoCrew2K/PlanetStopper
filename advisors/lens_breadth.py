"""S&P 500 market-breadth lens producer.

Computes the institutional gold-standard breadth indicator:
  - % of S&P 500 constituents above their 50-day SMA
  - % of S&P 500 constituents above their 200-day SMA

Data sources:
  - Constituent list: datahub CSV (primary) / Wikipedia HTML table (fallback)
  - Daily bars: synthetic_history.fetch_bars (Alpaca IEX path, proven batching +
    page-token loop + 429 backoff)

IEX-basis caveat: Free Alpaca feed = IEX trades only; acceptable for a
500-name aggregate.

Off-execution-path. Advisory-only. Never-raising. D-1 error contract
(``reason`` values are ``type(exc).__name__`` only).

Scope boundaries: no Flask route, no execution path, no eval/exec/subprocess.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests
import synthetic_history

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers
# ---------------------------------------------------------------------------

_SANITY_MIN: int = 490          # minimum acceptable constituent count
_SANITY_MAX: int = 510          # maximum acceptable constituent count
_MIN_QUALIFYING_BARS: int = 200  # minimum daily bars for a name to qualify
_SMA_50_WINDOW: int = 50        # 50-day SMA window
_SMA_200_WINDOW: int = 200      # 200-day SMA window
_BREADTH_TIMEOUT_S: float = 15.0  # explicit HTTP timeout (seconds)

# Look-back period: calendar days to request from Alpaca to guarantee ≥200
# trading days (200 trading days ≈ ~290 calendar days; 320 gives headroom for
# weekends, holidays, and recent IPOs with partial history).
_BAR_LOOKBACK_CALENDAR_DAYS: int = 320

# Source identifier (IEX caveat embedded)
_SOURCE: str = "datahub+alpaca-iex"

# Constituent CSV URL (datahub primary)
_DATAHUB_CSV_URL: str = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
    "/main/data/constituents.csv"
)

# Wikipedia fallback URL
_WIKIPEDIA_URL: str = (
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
)


# ---------------------------------------------------------------------------
# Public: get_sp500_constituents
# ---------------------------------------------------------------------------

def get_sp500_constituents() -> list[str]:
    """Return the current S&P 500 constituent list as dot-format ticker strings.

    Tries the datahub CSV first; falls back to the Wikipedia HTML table.
    Applies a 490–510 row sanity band to both sources.  Returns ``[]`` on any
    unrecoverable failure — never a stale or fabricated list.

    Dot-format tickers (e.g. ``BRK.B``) pass through unchanged.
    """
    # Primary: datahub CSV
    try:
        tickers = _fetch_datahub_constituents()
        if _SANITY_MIN <= len(tickers) <= _SANITY_MAX:
            logger.info("lens_breadth: datahub constituents OK count=%d", len(tickers))
            return tickers
        logger.info(
            "lens_breadth: datahub count=%d outside sanity band [%d, %d]; trying fallback",
            len(tickers), _SANITY_MIN, _SANITY_MAX,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "lens_breadth: datahub constituent fetch failed (%s); trying fallback",
            type(exc).__name__,
        )

    # Fallback: Wikipedia HTML table
    try:
        tickers = _fetch_wikipedia_constituents()
        if _SANITY_MIN <= len(tickers) <= _SANITY_MAX:
            logger.info(
                "lens_breadth: wikipedia constituents OK count=%d", len(tickers)
            )
            return tickers
        logger.info(
            "lens_breadth: wikipedia count=%d outside sanity band [%d, %d]; both sources failed",
            len(tickers), _SANITY_MIN, _SANITY_MAX,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "lens_breadth: wikipedia constituent fetch failed (%s); both sources failed",
            type(exc).__name__,
        )

    return []


def _fetch_datahub_constituents() -> list[str]:
    """Fetch constituent tickers from the datahub raw CSV.

    Raises on any HTTP or parse error (caller handles).
    """
    resp = requests.get(_DATAHUB_CSV_URL, timeout=_BREADTH_TIMEOUT_S)
    resp.raise_for_status()
    lines = resp.text.splitlines()
    # First line is header ("Symbol,Name,Sector,..."); skip it.
    tickers: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        symbol = line.split(",")[0].strip()
        if symbol:
            tickers.append(symbol)
    return tickers


def _fetch_wikipedia_constituents() -> list[str]:
    """Fetch constituent tickers from the Wikipedia S&P 500 article.

    Parses the first ``wikitable sortable`` HTML table, first data column.
    Raises on any HTTP or parse error (caller handles).
    """
    resp = requests.get(_WIKIPEDIA_URL, timeout=_BREADTH_TIMEOUT_S)
    resp.raise_for_status()
    html = resp.text

    # Locate the first wikitable sortable
    table_marker = 'class="wikitable sortable'
    table_start = html.find(table_marker)
    if table_start == -1:
        raise ValueError("Wikipedia wikitable sortable not found")

    # Find the table body start (after the header row)
    tbody_start = html.find("<tbody", table_start)
    if tbody_start == -1:
        # Some Wikipedia pages omit explicit <tbody>; fall back to searching
        # rows directly from table_start.
        tbody_start = table_start

    table_end = html.find("</table>", table_start)
    if table_end == -1:
        raise ValueError("Wikipedia wikitable closing tag not found")

    table_html = html[tbody_start:table_end]

    tickers: list[str] = []
    row_start = 0
    while True:
        tr_start = table_html.find("<tr", row_start)
        if tr_start == -1:
            break
        tr_end = table_html.find("</tr>", tr_start)
        if tr_end == -1:
            break
        row_html = table_html[tr_start:tr_end]
        row_start = tr_end + 5  # advance past </tr>

        # Skip header rows (contain <th> instead of <td>)
        if "<th" in row_html:
            continue

        # Extract first <td> text
        td_start = row_html.find("<td")
        if td_start == -1:
            continue
        td_inner_start = row_html.find(">", td_start)
        if td_inner_start == -1:
            continue
        td_end = row_html.find("</td>", td_inner_start)
        if td_end == -1:
            continue

        cell_html = row_html[td_inner_start + 1 : td_end]

        # Strip any anchor tags
        text = _strip_html_tags(cell_html).strip()

        # Wikipedia uses . notation too (BRK.B); preserve as-is.
        if text:
            tickers.append(text)

    return tickers


def _strip_html_tags(raw: str) -> str:
    """Strip HTML tags from a string, returning plain text."""
    result: list[str] = []
    in_tag = False
    for ch in raw:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# Public: compute_breadth
# ---------------------------------------------------------------------------

def compute_breadth(constituents: list[str]) -> dict:
    """Compute 50-day and 200-day SMA breadth over the qualifying sub-universe.

    Parameters
    ----------
    constituents:
        List of ticker strings (dot-format OK).

    Returns
    -------
    dict with keys:
        ``pct_above_50sma``  — float in [0, 1]; 0.0 when qualifying_count == 0
        ``pct_above_200sma`` — float in [0, 1]; 0.0 when qualifying_count == 0
        ``qualifying_count`` — int; names with >= 200 bars AND a current bar
        ``total_count``      — int; always == len(constituents)

    Qualifying sub-universe: a name qualifies if and only if fetch_bars returns
    >= 200 bar records for it.  Names with < 200 bars, 0 bars, or absent from
    the result dict are excluded from the denominator.  Missing bars are never
    imputed.
    """
    total_count = len(constituents)

    end_date = date.today()
    start_date = end_date - timedelta(days=_BAR_LOOKBACK_CALENDAR_DAYS)
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    bars_by_symbol: dict[str, list[dict]] = synthetic_history.fetch_bars(
        constituents, start_str, end_str
    )

    above_50 = 0
    above_200 = 0
    qualifying_count = 0

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _MIN_QUALIFYING_BARS:
            # Short history or halted — excluded from denominator
            continue

        closes = [bar["c"] for bar in bars]
        current_price = closes[-1]

        # 50-day SMA: arithmetic mean of the last _SMA_50_WINDOW closes
        sma_50 = sum(closes[-_SMA_50_WINDOW:]) / _SMA_50_WINDOW

        # 200-day SMA: arithmetic mean of the last _SMA_200_WINDOW closes
        sma_200 = sum(closes[-_SMA_200_WINDOW:]) / _SMA_200_WINDOW

        qualifying_count += 1
        if current_price >= sma_50:
            above_50 += 1
        if current_price >= sma_200:
            above_200 += 1

    if qualifying_count == 0:
        pct_above_50sma = 0.0
        pct_above_200sma = 0.0
    else:
        pct_above_50sma = above_50 / qualifying_count
        pct_above_200sma = above_200 / qualifying_count

    return {
        "pct_above_50sma": pct_above_50sma,
        "pct_above_200sma": pct_above_200sma,
        "qualifying_count": qualifying_count,
        "total_count": total_count,
    }


# ---------------------------------------------------------------------------
# Public: fetch_breadth
# ---------------------------------------------------------------------------

def fetch_breadth() -> dict:
    """Public entry point for the breadth lens.  Never raises.

    Returns
    -------
    dict with keys:
        ``available``        — bool; always present
        ``source``           — str; always present and non-empty
        ``pct_above_50sma``  — float; present when available=True
        ``pct_above_200sma`` — float; present when available=True
        ``qualifying_count`` — int; present when available=True
        ``total_count``      — int; present when available=True
        ``reason``           — str; ONLY when available=False;
                               type(exc).__name__ for exceptions,
                               short label for non-exception unavailability
                               (e.g. "EmptyConstituents", "ZeroQualifying")

    D-1 error contract: ``reason`` is ``type(exc).__name__`` only for
    exception-path failures; never the raw message, URL, or key name.

    Persists every run to the warehouse (append-only, secret-stripped).
    """
    from advisors import lens_warehouse  # noqa: PLC0415 — lazy import

    result: dict = {"source": _SOURCE}

    # Step 1: constituent fetch
    try:
        constituents = get_sp500_constituents()
    except Exception as exc:  # noqa: BLE001
        reason = type(exc).__name__
        result["available"] = False
        result["reason"] = reason
        _persist(lens_warehouse, result)
        return result

    if not constituents:
        result["available"] = False
        result["reason"] = "EmptyConstituents"
        _persist(lens_warehouse, result)
        return result

    # Step 2: bar fetch + breadth computation
    try:
        breadth = compute_breadth(constituents)
    except Exception as exc:  # noqa: BLE001
        reason = type(exc).__name__
        result["available"] = False
        result["reason"] = reason
        _persist(lens_warehouse, result)
        return result

    # Step 3: zero-qualifying guard
    if breadth["qualifying_count"] == 0:
        result["available"] = False
        result["reason"] = "ZeroQualifying"
        result.update(breadth)
        _persist(lens_warehouse, result)
        return result

    # Step 4: success path
    result["available"] = True
    result.update(breadth)
    logger.info(
        "lens_breadth: HTTP 200 datahub+alpaca-iex qualifying=%d/%d "
        "above50=%.1f%% above200=%.1f%%",
        breadth["qualifying_count"],
        breadth["total_count"],
        breadth["pct_above_50sma"] * 100,
        breadth["pct_above_200sma"] * 100,
    )
    _persist(lens_warehouse, result)
    return result


def _persist(lens_warehouse, payload: dict) -> None:
    """Persist payload to the warehouse; silently swallow any warehouse error."""
    try:
        lens_warehouse.persist_lens_snapshot(
            lens="breadth",
            symbol=None,
            source=_SOURCE,
            available=payload.get("available", False),
            raw_payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        # Warehouse errors must never propagate — log only.
        logger.debug("lens_breadth: warehouse persist failed (%s)", type(exc).__name__)
