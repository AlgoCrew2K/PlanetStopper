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
import time
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

# ---------------------------------------------------------------------------
# Constituent fetch retry policy (mirrors lens_options_proxy.py pattern)
# ---------------------------------------------------------------------------

# Exponential backoff base (seconds) for constituent HTTP retries.
_CONSTITUENT_BACKOFF_BASE_S: float = 1.0

# Per-sleep cap to prevent runaway waits.
_CONSTITUENT_BACKOFF_CAP_S: float = 8.0

# Maximum fetch attempts per constituent source (1 initial + 2 retries).
_CONSTITUENT_MAX_ATTEMPTS: int = 3

# Hard ceiling on cumulative retry wait: base + 2*base = 1 + 2 = 3 s.
MAX_CONSTITUENT_RETRY_WAIT_SECONDS: float = (
    _CONSTITUENT_BACKOFF_BASE_S + _CONSTITUENT_BACKOFF_BASE_S * 2
)  # 3.0 s

# HTTP status codes that warrant a constituent-fetch retry (transient errors).
_CONSTITUENT_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# ---------------------------------------------------------------------------
# Body-size safety caps
# ---------------------------------------------------------------------------

# Maximum bytes accepted from any constituent HTTP response body.
# Wikipedia pages can be large; 4 MB is ample for the S&P 500 table and
# provides a hard ceiling against runaway memory allocation.
_MAX_HTML_BYTES: int = 4_000_000  # 4 MB

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

    Applies bounded exponential backoff (_CONSTITUENT_MAX_ATTEMPTS attempts,
    _CONSTITUENT_BACKOFF_BASE_S base, capped at _CONSTITUENT_BACKOFF_CAP_S).
    Retries on timeout, connection errors, and _CONSTITUENT_RETRYABLE_HTTP_STATUSES.
    Response body is capped at _MAX_HTML_BYTES to prevent unbounded allocation.
    Raises on all failures after exhausting retries (caller handles).
    """
    last_exc: Exception | None = None
    for attempt in range(_CONSTITUENT_MAX_ATTEMPTS):
        try:
            resp = requests.get(_DATAHUB_CSV_URL, timeout=_BREADTH_TIMEOUT_S)
            if resp.status_code in _CONSTITUENT_RETRYABLE_HTTP_STATUSES:
                if attempt < _CONSTITUENT_MAX_ATTEMPTS - 1:
                    sleep_s = min(
                        _CONSTITUENT_BACKOFF_BASE_S * (2 ** attempt),
                        _CONSTITUENT_BACKOFF_CAP_S,
                    )
                    logger.debug(
                        "lens_breadth: datahub returned %d; retrying in %.1fs (attempt %d/%d)",
                        resp.status_code, sleep_s,
                        attempt + 1, _CONSTITUENT_MAX_ATTEMPTS,
                    )
                    time.sleep(sleep_s)
                    continue
                resp.raise_for_status()  # final attempt — surface the error

            resp.raise_for_status()
            # Cap body size to _MAX_HTML_BYTES before decode (datahub CSV is
            # small, but an unexpectedly large response is treated as a failure).
            raw_bytes = resp.content[:_MAX_HTML_BYTES]
            if len(resp.content) > _MAX_HTML_BYTES:
                raise ValueError(
                    f"datahub response exceeds {_MAX_HTML_BYTES} bytes"
                )
            lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
            # First line is header ("Symbol,Name,Sector,..."); skip it.
            tickers: list[str] = []
            for line in lines[1:]:
                if not line.strip():
                    continue
                symbol = line.split(",")[0].strip()
                if symbol:
                    tickers.append(symbol)
            return tickers

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < _CONSTITUENT_MAX_ATTEMPTS - 1:
                sleep_s = min(
                    _CONSTITUENT_BACKOFF_BASE_S * (2 ** attempt),
                    _CONSTITUENT_BACKOFF_CAP_S,
                )
                logger.debug(
                    "lens_breadth: datahub transport error %s; retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__, sleep_s,
                    attempt + 1, _CONSTITUENT_MAX_ATTEMPTS,
                )
                time.sleep(sleep_s)
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Exhausted datahub constituent fetch retries")  # pragma: no cover


def _fetch_wikipedia_constituents() -> list[str]:
    """Fetch constituent tickers from the Wikipedia S&P 500 article.

    Parses the first ``wikitable sortable`` HTML table, first data column.
    Applies bounded exponential backoff (_CONSTITUENT_MAX_ATTEMPTS attempts).
    Retries on timeout, connection errors, and _CONSTITUENT_RETRYABLE_HTTP_STATUSES.
    Uses stream=True with a chunk-capped read so the body never exceeds
    _MAX_HTML_BYTES in memory; raises ValueError if the ceiling is hit.
    Raises on any HTTP or parse error (caller handles).
    """
    last_exc: Exception | None = None
    for attempt in range(_CONSTITUENT_MAX_ATTEMPTS):
        try:
            resp = requests.get(
                _WIKIPEDIA_URL,
                timeout=_BREADTH_TIMEOUT_S,
                stream=True,
            )
            if resp.status_code in _CONSTITUENT_RETRYABLE_HTTP_STATUSES:
                resp.close()
                if attempt < _CONSTITUENT_MAX_ATTEMPTS - 1:
                    sleep_s = min(
                        _CONSTITUENT_BACKOFF_BASE_S * (2 ** attempt),
                        _CONSTITUENT_BACKOFF_CAP_S,
                    )
                    logger.debug(
                        "lens_breadth: wikipedia returned %d; retrying in %.1fs (attempt %d/%d)",
                        resp.status_code, sleep_s,
                        attempt + 1, _CONSTITUENT_MAX_ATTEMPTS,
                    )
                    time.sleep(sleep_s)
                    continue
                resp.raise_for_status()  # final attempt

            resp.raise_for_status()

            # Read body with a hard ceiling; abort if the page is pathologically large.
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > _MAX_HTML_BYTES:
                    resp.close()
                    raise ValueError(
                        f"Wikipedia response exceeds {_MAX_HTML_BYTES} bytes"
                    )
                chunks.append(chunk)
            html = b"".join(chunks).decode("utf-8", errors="replace")
            break  # success — exit retry loop

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < _CONSTITUENT_MAX_ATTEMPTS - 1:
                sleep_s = min(
                    _CONSTITUENT_BACKOFF_BASE_S * (2 ** attempt),
                    _CONSTITUENT_BACKOFF_CAP_S,
                )
                logger.debug(
                    "lens_breadth: wikipedia transport error %s; retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__, sleep_s,
                    attempt + 1, _CONSTITUENT_MAX_ATTEMPTS,
                )
                time.sleep(sleep_s)
                continue
            raise
    else:
        # All attempts exhausted via the retryable-HTTP branch without breaking
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Exhausted wikipedia constituent fetch retries")  # pragma: no cover

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

        # Guard: exclude any bar that is missing "c" or has a non-numeric value.
        # A single malformed bar must not raise a KeyError / TypeError that
        # darkens the whole name; if too few valid closes remain the qualifying
        # check (len < _MIN_QUALIFYING_BARS) already excludes this name.
        closes = [
            bar["c"]
            for bar in bars
            if isinstance(bar, dict)
            and "c" in bar
            and isinstance(bar["c"], (int, float))
        ]
        if len(closes) < _MIN_QUALIFYING_BARS:
            continue  # re-apply qualifying threshold after filtering
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

    # Step 3: zero-qualifying guard.
    # Do NOT merge pct_above_50sma / pct_above_200sma into the unavailable
    # result — those are 0.0 placeholders that contradict the docstring
    # contract ("present when available=True").  Diagnostic counts are safe
    # to include for debugging, but percentage fields are withheld.
    if breadth["qualifying_count"] == 0:
        result["available"] = False
        result["reason"] = "ZeroQualifying"
        result["qualifying_count"] = breadth["qualifying_count"]
        result["total_count"] = breadth["total_count"]
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
