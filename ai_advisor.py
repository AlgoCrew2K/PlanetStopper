"""Claude-backed config advisor — context assembly + structured-output client.

Cycle C1 scope: assemble a prompt-ready context blob for a symphony (or the
global surface), call Claude for structured config suggestions, and parse the
response. The *safety gates* on the suggestions (allowlist enforcement on what
Claude emits, risk-direction cross-check, OOS re-validation) are C2.

Real-money-critical input governance is in C1 scope: ``assemble_advisor_context``
reads a CURATED ALLOWLIST of config values — never ``dict(os.environ)`` and
never a raw ``.env`` dump. No credential, account id, safety flag, or
methodology knob may ever enter the context that reaches Claude.

The feature is on-demand operator-assist: a Claude call failure is "no
suggestion this click", zero engine impact. ``request_suggestions`` therefore
NEVER raises — every failure mode degrades to ``(None, error_message)``.
"""

from __future__ import annotations

import logging
import os
import time

import requests
import requests.exceptions

from pydantic import BaseModel

import database
import symphony_logic

logger = logging.getLogger(__name__)

# The 6 Optuna search-space keys — the validation contract in autotuner.py.
# Duplicated here as a frozenset literal (kept in sync with
# OPTUNA_SEARCH_SPACE_KEYS) rather than imported: importing
# ``autotuner`` pulls in optuna + joblib, whose process-pool/resource-tracker
# import side effects interact badly with the anthropic SDK import and pytest's
# output capture. The set is small, stable, and asserted against the live
# autotuner contract by the C1 test suite.
# Vars in database.DEFAULT_LOCKED_VARS are excluded — they are never
# suggested by Optuna and must not be adopted via the AI advisor.
_OPTUNA_SEARCH_SPACE_KEYS = frozenset(
    {
        "TAKE_PROFIT_MC_PCT",
        "VWAP_CROSS_HWM_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "MAX_PARABOLIC_SQUEEZE",
    }
)

# ---------------------------------------------------------------------------
# Model + SDK configuration.
# ---------------------------------------------------------------------------

_MAX_TOKENS = 2048
# Explicit client-side timeout — never rely on the SDK/urllib3 default.
_REQUEST_TIMEOUT_SECONDS = 30.0


def resolve_advisor_model() -> str:
    """Return the configured advisor synthesis model ID.

    Reads ADVISOR_SYNTHESIS_MODEL at call time so a daemon restart is not
    required to pick up a change.  Default: claude-opus-4-8 (Opus 4.8).
    """
    return os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")


# ---------------------------------------------------------------------------
# Suggestible config surface — the 7-item ALLOWLIST (config-surface.md §1).
# An allowlist, not a denylist: anything not enumerated here is structurally
# excluded from the context. The 6 Optuna search-space keys are read from
# _OPTUNA_SEARCH_SPACE_KEYS so this never drifts from the optimizer.
# ---------------------------------------------------------------------------

# The one strategy param Optuna structurally never tunes — hand-set only.
_UNTUNED_SUGGESTIBLE_KEY = "MAX_SQUEEZE_FLOOR"

# Per-param: one-line definition + risk polarity (does RAISING it loosen or
# tighten risk?). Element 1 of the 8 must-have prompt elements
# (prompt-methodology.md §1.1). Claude cannot reason about whether a suggestion
# is safe without the polarity.
# NOTE: TRIGGER_THRESHOLD_PCT is included here for prompt context (the operator
# sees it in the suggestible surface as a locked param), but it is NOT in
# _OPTUNA_SEARCH_SPACE_KEYS and NOT in _SUGGESTIBLE_ALLOWLIST — it is a locked
# var and must never appear in the allowed partition of enforce_suggestion_allowlist.
_PARAM_DEFINITIONS: dict[str, dict[str, str]] = {
    "TRIGGER_THRESHOLD_PCT": {
        "definition": (
            "MC-probability ceiling that arms the risk guard; x2 is the "
            "disarm level. Raising it arms the guard in a wider band."
        ),
        "risk_polarity": "raising loosens risk",
    },
    "TAKE_PROFIT_MC_PCT": {
        "definition": (
            "MC-probability floor below which take-profit arming triggers (exceptional-gain exit)."
        ),
        "risk_polarity": "raising tightens risk",
    },
    "VWAP_CROSS_HWM_PCT": {
        "definition": ("High-water-mark-relative band for the VWAP-breakdown state machine."),
        "risk_polarity": "raising loosens risk",
    },
    "VWAP_BLEED_MULTIPLIER": {
        "definition": ("Multiplier on 20-day volatility setting the VWAP-bleed arming threshold."),
        "risk_polarity": "raising loosens risk",
    },
    "VWAP_BLEED_TICKS": {
        "definition": (
            "Consecutive-tick count required before a VWAP-bleed cut fires; "
            "raising it delays the exit."
        ),
        "risk_polarity": "raising loosens risk",
    },
    "PARABOLIC_VELOCITY_THRESHOLD": {
        "definition": ("Return-velocity threshold that arms the parabolic squeeze."),
        "risk_polarity": "raising loosens risk",
    },
    "MAX_PARABOLIC_SQUEEZE": {
        "definition": (
            "Cap on the parabolic-squeeze multiplier applied to the trailing "
            "stop; below 1.0 it tightens the stop."
        ),
        "risk_polarity": "raising loosens risk",
    },
    _UNTUNED_SUGGESTIBLE_KEY: {
        "definition": (
            "Floor on the squeeze multiplier applied to the active stop. "
            "Optuna structurally never tunes this — hand-set only; the "
            "highest-value advisory target."
        ),
        "risk_polarity": "raising loosens risk",
    },
}

# Hard min/max valid ranges. The 6 Optuna search-space keys mirror the
# suggest_* bounds in autotuner.objective() (autotuner.py:306-312).
# TRIGGER_THRESHOLD_PCT is included for display context only (it is locked, not
# suggestible). MAX_SQUEEZE_FLOOR has no Optuna range; 0.05-0.50 is the
# research placeholder (config-surface.md §1, Layer B).
_PARAM_VALID_RANGES: dict[str, dict[str, float | str]] = {
    "TRIGGER_THRESHOLD_PCT": {"low": 5.0, "high": 25.0, "type": "float"},
    "TAKE_PROFIT_MC_PCT": {"low": 2.0, "high": 10.0, "type": "float"},
    "VWAP_CROSS_HWM_PCT": {
        "low": 0.3,
        "high": 2.0,
        "type": "float",
    },  # V1 calibration bounds (autotuner.py _SS_VWAP_CROSS_HWM_V1_MIN/MAX)
    "VWAP_BLEED_MULTIPLIER": {"low": 0.5, "high": 3.0, "type": "float"},
    "VWAP_BLEED_TICKS": {"low": 3, "high": 30, "type": "int"},
    "PARABOLIC_VELOCITY_THRESHOLD": {"low": 1.0, "high": 4.0, "type": "float"},
    "MAX_PARABOLIC_SQUEEZE": {"low": 0.1, "high": 0.8, "type": "float"},
    _UNTUNED_SUGGESTIBLE_KEY: {"low": 0.05, "high": 0.50, "type": "float"},
}

# The non-negotiable risk invariants Claude must treat as hard constraints —
# element 8 (prompt-methodology.md §1.1).
_RISK_INVARIANTS = [
    "The trailing-stop ratchet is monotonic — it moves up, never down.",
    "The breakeven latch is one-way — once latched it does not unlatch.",
    "Exit-confirmation tick gates exist deliberately; a suggestion that "
    "functionally loosens a live stop must be self-flagged as risk-increasing.",
]

# The data window + its limits — element 7 (prompt-methodology.md §1.1).
_DATA_WINDOW = {
    "trading_days": 125,
    "methodology": "80/20 walk-forward analysis (WFA)",
    "history_source": "synthetic replay history (Alpaca-derived)",
    "regimes_note": (
        "The 125-day synthetic replay window may not cover every market "
        "regime (vol spikes, gap events, low-volume sessions). If the current "
        "volatility regime is outside the window's observed range, the correct "
        "answer is data_sufficiency: insufficient — decline."
    ),
}

# Operator-assist role framing — element 9 (prompt-methodology.md §1.1).
_ROLE_FRAMING = (
    "You are an operator-assist analyst. A human operator reviews, accepts, or "
    "rejects every suggestion you produce. You are NOT tuning the system — you "
    "propose hypotheses for a human and a walk-forward validator to test. Your "
    "suggestions are unvalidated hypotheses; Optuna's are walk-forward "
    "validated. Prefer fewer, well-justified suggestions over many. An empty "
    "suggestions list is a valid, encouraged answer when nothing is "
    "well-supported — do not fabricate to fill the list."
)


# ---------------------------------------------------------------------------
# Pydantic schemas — the structured-output contract.
# ---------------------------------------------------------------------------


class ConfigSuggestion(BaseModel):
    """One proposed config edit with its rationale and self-classified risk."""

    config_key: str
    current_value: float | int | str
    suggested_value: float | int | str
    rationale: str
    risk_direction: str  # "loosens" | "tightens" | "neutral"
    confidence: str
    data_sufficiency: str
    oos_status: str = "pending"  # "passed" | "rejected" | "pending"
    oos_reason: str | None = None
    impact: dict = {"metric": "sharpe", "delta": 0.0}


class ConfigSuggestionsResponse(BaseModel):
    """The full structured response — zero or more suggestions.

    An empty ``suggestions`` list is valid: it is the abstention escape hatch
    ("the config looks sound"), not an error.
    """

    suggestions: list[ConfigSuggestion]


# ---------------------------------------------------------------------------
# Context assembly.
# ---------------------------------------------------------------------------


def _build_volatility_regime(autotune_run: dict | None) -> dict:
    """Volatility regime context — element 6.

    Reports current 20-day vol and 14-day ATR% against their 125-day window
    range. Values come from the autotune run when available; when Optuna has
    not run, the section is still well-shaped but marked unavailable so Claude
    can invoke the insufficient-data escape hatch.

    Honest availability: ``_autotune_run_row_to_dict`` (database.py:718-735)
    does not project ``symphony_vol`` or ``atr_pct_14d`` columns — those
    columns do not yet exist in the schema. Marking ``available:True`` with
    all-null fields fabricates regime context for Claude. We mark
    ``available:False`` whenever the actual vol/atr fields are absent or null,
    and set ``available:True`` only when the fields are genuinely present and
    non-null (forward-compatible once the schema gains those columns).
    """
    if autotune_run is None:
        return {
            "vol_20d": None,
            "vol_20d_window_range": None,
            "atr_pct_14d": None,
            "atr_pct_14d_window_range": None,
            "available": False,
            "reason": "Optuna has not run for this symphony — no vol/atr data available.",
        }

    vol_20d = autotune_run.get("symphony_vol")
    atr_pct_14d = autotune_run.get("atr_pct_14d")

    # Both fields must be non-null to claim regime data is available.
    # Currently neither column exists in the autotune_runs schema, so this
    # path is never True — but it will be once the schema is extended.
    if vol_20d is not None and atr_pct_14d is not None:
        return {
            "vol_20d": vol_20d,
            "vol_20d_window_range": autotune_run.get("vol_window_range"),
            "atr_pct_14d": atr_pct_14d,
            "atr_pct_14d_window_range": autotune_run.get("atr_pct_window_range"),
            "available": True,
        }

    return {
        "vol_20d": None,
        "vol_20d_window_range": None,
        "atr_pct_14d": None,
        "atr_pct_14d_window_range": None,
        "available": False,
        "reason": (
            "vol/atr columns not yet in autotune schema — "
            "symphony_vol and atr_pct_14d are not projected by "
            "_autotune_run_row_to_dict (database.py:718-735)."
        ),
    }


# ---------------------------------------------------------------------------
# Cycle-2 multi-lens producers — GDELT sentiment, SEC EDGAR fundamentals,
# FRED macro.  Each replaces its Cycle-1 stub with a real fetcher that:
#   - makes HTTP calls with explicit timeouts,
#   - retries with bounded exponential backoff,
#   - returns available=False + reason on any error (D-1: reason carries only
#     type(exc).__name__, never str(exc) which may contain keys/URLs),
#   - returns available=True with real payload + citation-validated sources on
#     success.
# Technicals + derivatives remain cycle-1 stubs (Cycle-2b).
# ---------------------------------------------------------------------------

# Maximum total wall-clock seconds the retry loop will wait across all
# backoff sleeps before giving up.  Named constant per custom instructions.
_FETCH_MAX_BACKOFF_TOTAL_WAIT_S: float = 8.0

# Hard cap on the number of GET attempts inside _fetch_with_backoff.
# Defense-in-depth against delay collapsing to 0.0 (which would allow an
# infinite spin when total_waited == _FETCH_MAX_BACKOFF_TOTAL_WAIT_S).
# With a 1.0s initial delay doubling each retry the budget is naturally
# spent in ~4-5 attempts; 6 is the safety ceiling.
# Source: custom instructions §4 "No unbounded retry loops."
_FETCH_MAX_ATTEMPTS: int = 6

# Seconds to wait for a response from any external lens API.
_FETCH_TIMEOUT_S: float = 15.0

# User-Agent string sent with SEC EDGAR requests.  The SEC requires a
# descriptive UA including a contact address; missing UA is the primary cause
# of 403s.  (FREE-DATA-SOURCES.md §5)
_SEC_USER_AGENT: str = "PlanetStopper advisor contact@alphabotpm.example"

# GDELT 2.0 DOC API — free, no key, artlist mode returning JSON.
_GDELT_ARTLIST_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance+sourcelang:eng"
    "&mode=artlist"
    "&maxrecords=10"
    "&format=json"
    "&timespan=1440"  # last 24 hours
)

# FRED series to fetch: id → (label, release-page URL for click-through).
# Kept small to stay within the 120 req/min rate limit and avoid latency.
_FRED_SERIES: dict[str, tuple[str, str]] = {
    "DGS10": (
        "10-Year Treasury Constant Maturity Rate",
        "https://fred.stlouisfed.org/series/DGS10",
    ),
    "UNRATE": (
        "Unemployment Rate",
        "https://fred.stlouisfed.org/series/UNRATE",
    ),
    "CPIAUCSL": (
        "Consumer Price Index (CPI-U)",
        "https://fred.stlouisfed.org/series/CPIAUCSL",
    ),
    "FEDFUNDS": (
        "Federal Funds Effective Rate",
        "https://fred.stlouisfed.org/series/FEDFUNDS",
    ),
}
_FRED_OBSERVATIONS_URL: str = "https://api.stlouisfed.org/fred/series/observations"

# SEC EDGAR ticker→CIK lookup (company tickers JSON) — no auth required.
_SEC_TICKERS_URL: str = "https://data.sec.gov/submissions/{cik_padded}.json"
_SEC_COMPANYFACTS_URL: str = "https://data.sec.gov/api/xbrl/companyfacts/{cik_padded}.json"
_SEC_TICKERS_JSON_URL: str = "https://data.sec.gov/files/company_tickers.json"

# GAAP concepts extracted for the fundamentals payload.
# Each entry maps a logical concept key → (display_label, ordered_candidate_tags).
# Candidate tags are tried as a union: all present tags' entries are merged and the
# entry with the most-recent `end` (filed desc as tiebreak) wins.  This handles XBRL
# concept deprecations (e.g. Revenues → RevenueFromContractWithCustomerExcludingAssessedTax)
# without losing the freshest data when a company has partially migrated between tags.
# Outer logical keys are STABLE — they are the key_facts output keys consumed by the
# synthesis prompt and the Overview render.
_SEC_KEY_CONCEPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Revenues": (
        "Revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ),
    ),
    "NetIncomeLoss": ("Net Income / Loss", ("NetIncomeLoss",)),
    "Assets": ("Total Assets", ("Assets",)),
    "Liabilities": ("Total Liabilities", ("Liabilities",)),
    "StockholdersEquity": ("Stockholders Equity", ("StockholdersEquity",)),
}

# Proxy company-ticker floor for the portfolio fan-out path.  These are large-cap
# individual company tickers across sectors — NOT ETFs (ETFs have no SEC companyfacts
# entries and would cause every CIK lookup and companyfacts fetch to fail).
# This basket guarantees a non-empty fundamentals universe at 03:00 / off-hours /
# flat markets when logic_holdings is empty (mirrors _PROXY_UNIVERSE in lens_technicals).
# Source: S&P 500 representative cross-sector companies (2024 large-cap constituents).
_FUNDAMENTALS_PROXY_UNIVERSE: frozenset[str] = frozenset(
    {
        "AAPL",  # Apple Inc. — Technology
        "MSFT",  # Microsoft Corp. — Technology
        "GOOGL",  # Alphabet Inc. — Communication Services
        "AMZN",  # Amazon.com Inc. — Consumer Discretionary
        "NVDA",  # NVIDIA Corp. — Technology (semiconductors)
        "JPM",  # JPMorgan Chase & Co. — Financials
        "XOM",  # Exxon Mobil Corp. — Energy
        "JNJ",  # Johnson & Johnson — Health Care
    }
)


def _fetch_with_backoff(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
) -> requests.Response:
    """GET url with explicit timeout and bounded exponential backoff.

    Retries on connection errors and 429 responses.  Raises the final
    exception if retries are exhausted.  Never waits longer than
    _FETCH_MAX_BACKOFF_TOTAL_WAIT_S in total across all sleeps, and never
    exceeds _FETCH_MAX_ATTEMPTS total attempts.

    The retry predicate ``_can_retry`` is the single authoritative gate: it
    is true ONLY when all three conditions hold simultaneously —
      1. attempt < _FETCH_MAX_ATTEMPTS (hard attempt ceiling),
      2. delay > 0.0 (delay has not collapsed to zero),
      3. total_waited + delay <= _FETCH_MAX_BACKOFF_TOTAL_WAIT_S (budget not
         exhausted).
    This prevents the infinite-spin that occurred when delay collapsed to 0.0
    after the budget was spent (total_waited == _FETCH_MAX_BACKOFF_TOTAL_WAIT_S
    made condition 3 ``8.0 <= 8.0 = True`` forever).

    Args:
        url: the full request URL.
        headers: optional HTTP headers dict.
        params: optional query-parameter dict.

    Returns:
        A requests.Response with status_code set.  Callers must call
        raise_for_status() themselves if they need to treat 4xx/5xx as errors.
    """
    delay = 1.0
    total_waited = 0.0
    attempt = 0
    while True:
        attempt += 1
        # Single authoritative retry predicate — evaluated after every attempt.
        # All three conditions must hold simultaneously for a retry to be safe:
        #   1. attempt < _FETCH_MAX_ATTEMPTS (hard ceiling, prevents infinite spin
        #      when delay collapses to 0.0 after budget is exhausted),
        #   2. delay > 0.0 (delay must be positive — a zero sleep is not a retry),
        #   3. total_waited + delay <= budget (time budget is not yet spent).
        # The original code lacked condition 1 and 2, causing the collapse:
        # when total_waited == 8.0, min(delay*2, 8.0-8.0) == 0.0, so condition 3
        # became 8.0+0.0 <= 8.0 = True forever → time.sleep(0) spin + OOM.
        can_retry = (
            attempt < _FETCH_MAX_ATTEMPTS
            and delay > 0.0
            and total_waited + delay <= _FETCH_MAX_BACKOFF_TOTAL_WAIT_S
        )
        try:
            resp = requests.get(
                url,
                headers=headers or {},
                params=params,
                timeout=_FETCH_TIMEOUT_S,
            )
            if resp.status_code == 429 and can_retry:
                logger.info("lens fetch 429 (attempt %d) — backing off %.1fs", attempt, delay)
                time.sleep(delay)
                total_waited += delay
                delay = min(delay * 2, _FETCH_MAX_BACKOFF_TOTAL_WAIT_S - total_waited)
                continue
            # Budget exhausted or non-429: return to caller.
            return resp
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            if not can_retry:
                raise
            logger.info(
                "lens fetch %s (attempt %d) — backing off %.1fs",
                type(exc).__name__,
                attempt,
                delay,
            )
            time.sleep(delay)
            total_waited += delay
            delay = min(delay * 2, _FETCH_MAX_BACKOFF_TOTAL_WAIT_S - total_waited)


def _build_technicals_section(_data: object = None) -> dict:
    """Technicals lens block — Alpaca price/trend/breadth producer.

    Fetches MA posture (50-day and 200-day SMA), market breadth (fraction of
    universe above 50-day SMA), and 20-day momentum from synthetic_history
    daily bars.  Honest availability: returns available=False when bars are
    absent or the fetch fails.

    Universe is sourced from the UNION of live bot_state logic_holdings and
    lens_technicals._PROXY_UNIVERSE (a named market-proxy basket).  The proxy
    is a floor so the nightly Prism pipeline (03:00, off-hours) always
    receives a real universe even when symphonies hold nothing (logic_holdings
    is empty at 03:00 / weekends / flat markets — PM live-gate finding).

    CC-2: lazy import keeps the Alpaca client off the module-level import path.
    D-1: reason is type(exc).__name__ only — never str(exc).

    Args:
        _data: unused; reserved for caller pre-injection (test-mockable hook).

    Returns:
        Lens block dict with keys: lens, available, reason (on False),
        payload (on True), sources.
    """
    from advisors import lens_technicals  # noqa: PLC0415

    # Source the universe from live bot_state holdings so the producer has
    # real tickers to fetch bars for.  An empty universe would propagate to
    # _get_bars([]) → synthetic_history.fetch_bars([], ...) → {} → always
    # available=False, which is hollow wiring (reviewer finding: round 1).
    try:
        bot_state = database.load_state()
        tickers: set[str] = set()
        for entry in bot_state.values():
            if isinstance(entry, dict):
                for ticker in entry.get("logic_holdings", {}):
                    if ticker:  # filter out empty strings
                        tickers.add(ticker)
    except Exception:
        tickers = set()

    # Merge with the proxy basket: live holdings may be empty off-hours
    # (logic_holdings={} at 03:00 / weekends / flat markets — PM live-gate
    # finding). The proxy is a FLOOR that ensures the nightly Prism pipeline
    # always receives a real universe. Live tickers are merged in on top.
    tickers.update(lens_technicals._PROXY_UNIVERSE)
    universe = sorted(tickers)

    try:
        result = lens_technicals._fetch_technicals(universe)
    except Exception as exc:
        result = {"available": False, "reason": type(exc).__name__}

    if result.get("available"):
        return {
            "lens": "technicals",
            "available": True,
            "payload": {
                "ma_posture": result.get("ma_posture"),
                "breadth": result.get("breadth"),
                "momentum": result.get("momentum"),
            },
            "sources": [],
        }
    else:
        return {
            "lens": "technicals",
            "available": False,
            "reason": result.get("reason", "unavailable"),
            "payload": None,
            "sources": [],
        }


def _build_sentiment_section(_data: object = None) -> dict:
    """Sentiment / news lens block — multi-source two-facet producer (lens-news-events upgrade).

    Single-path architecture:
      news_corpus.build_news_corpus() — multi-source corpus (8 RSS/Atom feeds + GDELT artlist)
      plus GDELT AvgTone scalar.  A single lens_gdelt._fetch_gdelt_sentiment() call inside
      news_corpus delivers both tone and artlist articles (≤2 GDELT GETs total per run).

    available=True iff news_corpus returns tone or a non-empty corpus.
    Payload carries: tone_score, corpus (Facet B ranked articles), events (Facet A legacy shape
    for render compatibility), article_count.
    sources: built from corpus via build_citation() — feeds lens_pipeline aggregation +
    the Overview "Cited sources" block.

    GDELT is key-less; no env-var gate.  D-1: reason is type(exc).__name__ only.
    CC-2: all advisors modules imported lazily (never at module level).

    Args:
        _data: unused; reserved for caller pre-injection (test-mockable hook).

    Returns:
        Lens block dict with keys: lens, available, reason (on False),
        payload (on True), sources.
    """
    _lens = "sentiment"

    # --- Primary path: multi-source corpus builder (CC-2: lazy import) ---
    corpus_result: dict = {"available": False, "tone": None, "corpus": [], "reason": None}
    try:
        from advisors import news_corpus as _news_corpus  # noqa: PLC0415

        corpus_result = _news_corpus.build_news_corpus()
    except Exception as exc:
        logger.debug("news_corpus.build_news_corpus failed: %s", type(exc).__name__)

    corpus_available = bool(corpus_result.get("available"))

    if not corpus_available:
        reason = corpus_result.get("reason") or "no_news_events"
        # DW-1: persist unavailability event to warehouse (lazy import, CC-2).
        try:
            from advisors import lens_warehouse  # noqa: PLC0415

            lens_warehouse.persist_lens_snapshot(
                lens="sentiment",
                symbol=None,
                source="news_corpus",
                available=False,
                raw_payload={"reason": reason},
            )
        except Exception:  # noqa: BLE001
            pass  # D-1: warehouse errors never surface to callers
        return {
            "lens": _lens,
            "available": False,
            "payload": None,
            "sources": [],
            "reason": reason,
        }

    tone_score = corpus_result.get("tone")
    corpus = corpus_result.get("corpus", [])

    # events: legacy shape mapped from corpus articles for render compatibility (AC-5)
    if corpus:
        events = [
            {
                "title": art.get("title", ""),
                "domain": art.get("domain", ""),
                "seendate": art.get("published", ""),
            }
            for art in corpus
        ]
    else:
        events = []

    logger.info(
        "multi-source sentiment: tone=%s corpus_size=%d events=%d",
        tone_score,
        len(corpus),
        len(events),
    )

    # DW-1: persist this lens snapshot to the warehouse (lazy import, CC-2).
    try:
        from advisors import lens_warehouse  # noqa: PLC0415

        lens_warehouse.persist_lens_snapshot(
            lens="sentiment",
            symbol=None,
            source="news_corpus",
            available=True,
            raw_payload={"tone_score": tone_score, "corpus_size": len(corpus)},
        )
    except Exception:  # noqa: BLE001
        pass  # D-1: warehouse errors never surface to callers

    sources: list[dict] = []
    for art in corpus:
        citation = build_citation(
            {
                "title": art.get("title", ""),
                "url": art.get("url", ""),
                "published": art.get("published", ""),
                "lens": _lens,
            }
        )
        if citation is not None:
            sources.append(citation)

    return {
        "lens": _lens,
        "available": True,
        "payload": {
            "tone_score": tone_score,
            "corpus": corpus,
            "events": events,  # AC-5: events in legacy shape for render compatibility
            "article_count": len(corpus),
        },
        "sources": sources,
    }


def _build_derivatives_section(_data: object = None) -> dict:
    """Derivatives / volatility lens block — FRED VIXCLS + VXVCLS producer.

    Fetches VIX spot (VIXCLS) and 3-month VIX (VXVCLS) from FRED via the
    options-proxy lens producer.  Requires FRED_API_KEY in env; returns
    available=False with the proxy reason when the key is absent, FRED is
    down, or data is unavailable.

    Lazy-imports advisors.lens_options_proxy inside the function body (CC-2
    boundary) — never at module level.  D-1: reason is always
    type(exc).__name__ only (enforced by the proxy; this function adds a
    defense-in-depth try/except so any unexpected raise from the proxy or
    the import itself also degrades cleanly).

    Args:
        _data: unused; reserved for caller pre-injection.

    Returns:
        Lens block dict: {lens, available, payload, sources} on success;
        {lens, available, reason, payload, sources} on failure.
    """
    _lens = "derivatives"
    try:
        from advisors import lens_options_proxy as _proxy_mod  # CC-2: lazy import

        result = _proxy_mod._fetch_options_proxy()
    except Exception as exc:  # noqa: BLE001
        # Defense-in-depth: proxy contract guarantees never-raise, but guard
        # against import errors or future regressions. D-1: type name only —
        # str(exc) may contain FRED_API_KEY embedded in a request URL.
        exc_type = type(exc).__name__
        logger.warning("Derivatives lens unavailable (caller guard): %s", exc_type)
        return {
            "lens": _lens,
            "available": False,
            "reason": exc_type,
            "payload": None,
            "sources": [],
        }

    if not result.get("available"):
        return {
            "lens": _lens,
            "available": False,
            "reason": result.get("reason", "unavailable"),
            "payload": None,
            "sources": [],
        }

    as_of = result.get("as_of_date", "")
    citation = build_citation(
        {
            "title": "VIXCLS / VXVCLS (CBOE Vol Index)",
            "url": "https://fred.stlouisfed.org/series/VIXCLS",
            "published": as_of,
            "lens": _lens,
        }
    )
    sources = [citation] if citation is not None else []

    logger.info(
        "Derivatives lens: vix=%.2f regime=%s risk_read=%s as_of=%s",
        result.get("vix_level", 0),
        result.get("vix_term_structure", {}).get("regime", "unknown"),
        result.get("risk_read", "unknown"),
        as_of,
    )

    return {
        "lens": _lens,
        "available": True,
        "payload": {
            "vix_level": result.get("vix_level"),
            "vix_term_structure": result.get("vix_term_structure"),
            "risk_read": result.get("risk_read"),
            "as_of_date": as_of,
        },
        "sources": sources,
    }


def _build_macro_section(_data: object = None) -> dict:
    """Macro / economic lens block — FRED API producer (Cycle 2).

    Fetches observations for a small set of key FRED series (10-yr Treasury,
    unemployment, CPI, Fed funds rate).  Requires FRED_API_KEY in env; returns
    available=False with an informative reason when the key is absent or empty.

    Each fetched series produces a source {title, url, published, lens} using
    the series' FRED release-page URL as the clickable citation.

    D-1: error reasons carry only type(exc).__name__; FRED embeds the API key
    as a URL query parameter, so str(exc) must never appear in reason.

    Args:
        _data: unused; reserved for caller pre-injection.

    Returns:
        Lens block dict with keys: lens, available, reason (on False),
        payload (on True), sources (on True).
    """
    _lens = "macro"
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if not fred_key:
        # AC-4: persist this lens snapshot to the warehouse (lazy import, CC-2).
        try:
            from advisors import lens_warehouse  # noqa: PLC0415

            lens_warehouse.persist_lens_snapshot(
                lens="macro",
                symbol=None,
                source="fred",
                available=False,
                raw_payload={"reason": "FRED_API_KEY_not_configured"},
            )
        except Exception:  # noqa: BLE001
            pass  # D-1: warehouse errors never surface to callers
        return {
            "lens": _lens,
            "available": False,
            "reason": "FRED_API_KEY not configured — register free at fred.stlouisfed.org",
            "payload": None,
            "sources": [],
        }

    series_data: dict[str, dict] = {}
    sources: list[dict] = []
    last_exc_class: str | None = None

    for series_id, (label, release_url) in _FRED_SERIES.items():
        try:
            resp = _fetch_with_backoff(
                _FRED_OBSERVATIONS_URL,
                params={
                    "series_id": series_id,
                    "api_key": fred_key,
                    "file_type": "json",
                    "limit": 10,
                    "sort_order": "desc",
                },
            )
            resp.raise_for_status()
            obs_data = resp.json()
        except Exception as exc:
            # D-1: log exc detail internally only; never surface str(exc)
            logger.debug("FRED fetch %s failed: %s", series_id, exc)
            last_exc_class = type(exc).__name__
            continue

        observations = obs_data.get("observations", [])
        if observations:
            # Use the most recent observation value and date.
            latest = observations[0]
            series_data[series_id] = {
                "label": label,
                "value": latest.get("value"),
                "date": latest.get("date"),
            }
            # Build one clickable source per series using the release page URL.
            published = latest.get("date", obs_data.get("realtime_end", ""))
            citation = build_citation(
                {
                    "title": label,
                    "url": release_url,
                    "published": published,
                    "lens": _lens,
                }
            )
            if citation is not None:
                sources.append(citation)

    if not series_data:
        # All series fetches failed.
        reason = (
            f"{last_exc_class} fetching FRED macro series"
            if last_exc_class
            else "FRED returned no observations"
        )
        # AC-4: persist this lens snapshot to the warehouse (lazy import, CC-2).
        try:
            from advisors import lens_warehouse  # noqa: PLC0415

            lens_warehouse.persist_lens_snapshot(
                lens="macro",
                symbol=None,
                source="fred",
                available=False,
                raw_payload={"reason": reason},
            )
        except Exception:  # noqa: BLE001
            pass  # D-1: warehouse errors never surface to callers
        return {
            "lens": _lens,
            "available": False,
            "reason": reason,
            "payload": None,
            "sources": [],
        }

    logger.info("FRED macro: %d series fetched", len(series_data))
    # AC-4: persist this lens snapshot to the warehouse (lazy import, CC-2).
    try:
        from advisors import lens_warehouse  # noqa: PLC0415

        lens_warehouse.persist_lens_snapshot(
            lens="macro",
            symbol=None,
            source="fred",
            available=True,
            raw_payload={"series": series_data},
        )
    except Exception:  # noqa: BLE001
        pass  # D-1: warehouse errors never surface to callers
    return {
        "lens": _lens,
        "available": True,
        "payload": {"series": series_data},
        "sources": sources,
    }


# Fast-path CIK table for common tickers — avoids an extra HTTP round-trip
# for the most frequently queried symbols.  Values are verified SEC CIKs.
# Add entries here as needed; the HTTP fallback handles unknown tickers.
_SEC_TICKER_CIK_CACHE: dict[str, str] = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "GOOG": "0001652044",
    "AMZN": "0001018724",
    "NVDA": "0001045810",
    "META": "0001326801",
    "TSLA": "0001318605",
    "BRK.B": "0001067983",
    "V": "0001403161",
    "JPM": "0000019617",
    "SPY": "0000884394",
    "QQQ": "0001468085",
}


def _sec_ticker_to_cik(ticker: str) -> str | None:
    """Resolve a ticker symbol to a zero-padded 10-digit CIK string.

    Checks the in-process fast-path cache first, then falls back to the SEC
    EDGAR company_tickers.json bulk file (no auth, UA header required).
    Returns None on any failure so the caller can degrade gracefully.

    Args:
        ticker: stock ticker symbol (e.g. "AAPL").

    Returns:
        10-digit zero-padded CIK string, or None if not found / on error.
    """
    ticker_upper = ticker.upper().strip()

    # Fast-path: check the in-process cache before making an HTTP request.
    if ticker_upper in _SEC_TICKER_CIK_CACHE:
        return _SEC_TICKER_CIK_CACHE[ticker_upper]

    # Slow-path: fetch the SEC bulk tickers JSON.
    try:
        resp = _fetch_with_backoff(
            _SEC_TICKERS_JSON_URL,
            headers={"User-Agent": _SEC_USER_AGENT},
        )
        resp.raise_for_status()
        tickers_json = resp.json()
    except Exception as exc:
        logger.debug("SEC ticker→CIK lookup failed for %s: %s", ticker, exc)
        return None

    for _entry_key, entry in tickers_json.items():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik_int = entry.get("cik_str") or entry.get("cik")
            if cik_int is not None:
                return str(int(cik_int)).zfill(10)
    return None


def _fetch_fundamentals_for_ticker(ticker: str) -> dict:
    """Fetch SEC EDGAR companyfacts for a single company ticker.

    Extracted from the original single-ticker body of _build_fundamentals_section
    to enable the portfolio fan-out path (AC-1 fix).  Returned block uses the
    existing per-ticker shape: {available, payload: {entity_name, cik, key_facts},
    sources}.  The 'lens' key is intentionally absent — callers set it.

    MANDATORY User-Agent header: the SEC requires a descriptive UA string;
    missing UA is the primary cause of 403 responses (FREE-DATA-SOURCES.md §5).

    D-1: error reasons carry only type(exc).__name__.

    Args:
        ticker: stock ticker symbol (must be non-empty; callers must validate).

    Returns:
        Dict with keys: available, reason (on False), payload (on True),
        sources (on True).
    """
    _lens = "fundamentals"
    ticker = str(ticker).strip().upper()

    # Resolve ticker → CIK using the SEC bulk tickers file.
    cik_padded = _sec_ticker_to_cik(ticker)
    if cik_padded is None:
        return {
            "available": False,
            "reason": f"SEC EDGAR CIK not found for ticker {ticker}",
            "payload": None,
            "sources": [],
        }

    companyfacts_url = _SEC_COMPANYFACTS_URL.format(cik_padded=f"CIK{cik_padded}")

    try:
        resp = _fetch_with_backoff(
            companyfacts_url,
            headers={"User-Agent": _SEC_USER_AGENT},
        )
        resp.raise_for_status()
        facts_data = resp.json()
    except Exception as exc:
        logger.debug("SEC EDGAR companyfacts fetch failed for %s: %s", ticker, exc)
        return {
            "available": False,
            "reason": f"{type(exc).__name__} fetching SEC EDGAR fundamentals",
            "payload": None,
            "sources": [],
        }

    entity_name = facts_data.get("entityName", ticker)
    cik_int = facts_data.get("cik", cik_padded)
    us_gaap = facts_data.get("facts", {}).get("us-gaap", {})

    key_facts: dict[str, object] = {}
    sources: list[dict] = []
    seen_accessions: set[str] = set()

    for concept, (label, candidate_tags) in _SEC_KEY_CONCEPTS.items():
        try:
            # Union entries across ALL candidate tags present in us-gaap.
            # This handles XBRL concept deprecations: a company may have data under
            # an old tag (e.g. Revenues) AND a newer tag (e.g. RevenueFromContract…);
            # we union them and pick the entry with the freshest `end` date.
            all_tag_entries: list[tuple[str, dict]] = []  # (unit_type, entry)
            for tag in candidate_tags:
                tag_data = us_gaap.get(tag)
                if not tag_data:
                    continue
                # Walk the units (usually USD) for the most recent annual filing.
                for unit_type, unit_entries in tag_data.get("units", {}).items():
                    if not isinstance(unit_entries, list) or not unit_entries:
                        continue
                    # Prefer 10-K entries; fall back to all entries when none present.
                    annual_entries = [e for e in unit_entries if e.get("form") == "10-K"]
                    entries_to_check = annual_entries or unit_entries
                    for e in entries_to_check:
                        all_tag_entries.append((unit_type, e))

            if not all_tag_entries:
                continue

            # Sort the union by (end desc, filed desc) — end is the primary key so that
            # the most-recently-reported period wins regardless of filing order.
            entries_sorted = sorted(
                all_tag_entries,
                key=lambda te: (
                    te[1].get("end", "") or "",
                    te[1].get("filed", "") or "",
                ),
                reverse=True,
            )
            unit_type, latest_entry = entries_sorted[0]
            key_facts[concept] = {
                "label": label,
                "value": latest_entry.get("val"),
                "unit": unit_type,
                "end": latest_entry.get("end"),
                "filed": latest_entry.get("filed"),
                "form": latest_entry.get("form"),
            }
            # Build a clickable source from the filing accession number.
            accn = latest_entry.get("accn", "")
            if accn and accn not in seen_accessions:
                seen_accessions.add(accn)
                filing_url = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&CIK={cik_padded}&type=10-K&dateb=&owner=include&count=10"
                )
                filed_date = latest_entry.get("filed", "")
                citation = build_citation(
                    {
                        "title": f"{entity_name} {latest_entry.get('form', 'Filing')} ({filed_date})",
                        "url": filing_url,
                        "published": filed_date,
                        "lens": _lens,
                    }
                )
                if citation is not None:
                    sources.append(citation)
        except Exception:
            # Never raise on a malformed concept block; skip and continue to next concept.
            continue

    if not key_facts:
        return {
            "available": False,
            "reason": f"SEC EDGAR returned no recognized key facts for {ticker}",
            "payload": None,
            "sources": [],
        }

    # Deduplicate sources by URL.
    seen_urls: set[str] = set()
    unique_sources: list[dict] = []
    for src in sources:
        if src["url"] not in seen_urls:
            seen_urls.add(src["url"])
            unique_sources.append(src)

    logger.info(
        "SEC EDGAR fundamentals: %s (%s), %d key facts, %d sources",
        entity_name,
        ticker,
        len(key_facts),
        len(unique_sources),
    )
    return {
        "available": True,
        "payload": {
            "entity_name": entity_name,
            "cik": cik_int,
            "key_facts": key_facts,
        },
        "sources": unique_sources,
    }


def _build_fundamentals_section(_data: object = None, *, ticker: str | None = None) -> dict:
    """Fundamentals lens block — SEC EDGAR companyfacts producer (Cycle 2 / Cycle 3 fan-out).

    When called with a ticker symbol (e.g. ticker="AAPL"), returns the existing
    per-symphony single-ticker shape (AC-3 regression guard).

    When called without a ticker (portfolio path, AC-1 fix), derives a universe
    from the union of live logic_holdings and _FUNDAMENTALS_PROXY_UNIVERSE, fans
    out _fetch_fundamentals_for_ticker across each company ticker, and aggregates
    available results into a portfolio payload.  The proxy floor guarantees a
    non-empty universe at 03:00 / off-hours / flat markets (mirrors the technicals
    lens universe pattern — DE-TECH-002 equivalent for fundamentals).

    MANDATORY User-Agent header: the SEC requires a descriptive UA string;
    missing UA is the primary cause of 403 responses (FREE-DATA-SOURCES.md §5).

    Sources carry SEC EDGAR filing URLs (https://data.sec.gov/...) as
    clickable citations (CC-4).

    D-1: error reasons carry only type(exc).__name__.

    CC-2: database.load_state() is called lazily inside the function body —
    never at module import time.

    Args:
        _data: unused; reserved for caller pre-injection.
        ticker: stock ticker symbol.  When provided, uses the single-ticker path
            (AC-3 preserved).  When None (default), uses the portfolio fan-out path.

    Returns:
        Lens block dict with keys: lens, available, reason (on False),
        payload (on True), sources (on True).
    """
    _lens = "fundamentals"

    # --- Single-ticker path (AC-3: preserved unchanged) ---
    if ticker is not None and str(ticker).strip():
        result = _fetch_fundamentals_for_ticker(ticker)
        if result.get("available"):
            return {
                "lens": _lens,
                "available": True,
                "payload": result["payload"],
                "sources": result.get("sources", []),
            }
        else:
            return {
                "lens": _lens,
                "available": False,
                "reason": result.get("reason", "unavailable"),
                "payload": None,
                "sources": [],
            }

    # --- Portfolio fan-out path (AC-1 fix: ticker=None) ---
    # Derive the universe from live logic_holdings ∪ _FUNDAMENTALS_PROXY_UNIVERSE.
    # CC-2: lazy DB access — no module-level import of database state.
    try:
        bot_state = database.load_state()
        holdings_tickers: set[str] = set()
        for entry in bot_state.values():
            if isinstance(entry, dict):
                for t in entry.get("logic_holdings", {}):
                    if t:
                        holdings_tickers.add(t)
    except Exception:
        holdings_tickers = set()

    # Merge with the proxy floor; sort for deterministic ordering.
    universe: list[str] = sorted(holdings_tickers | set(_FUNDAMENTALS_PROXY_UNIVERSE))

    if not universe:
        return {
            "lens": _lens,
            "available": False,
            "reason": "no fundamentals universe: holdings and proxy are both empty",
            "payload": None,
            "sources": [],
        }

    # Fan out: per-ticker failures degrade only that ticker (AC-4).
    per_ticker_results: dict[str, dict] = {}
    all_sources: list[dict] = []
    seen_source_urls: set[str] = set()

    for t in universe:
        try:
            result = _fetch_fundamentals_for_ticker(t)
        except Exception as exc:
            # Defense-in-depth: _fetch_fundamentals_for_ticker is never-raising,
            # but guard here to ensure per-ticker isolation regardless.
            result = {"available": False, "reason": type(exc).__name__}

        if result.get("available"):
            per_ticker_results[t] = result["payload"]
            for src in result.get("sources", []):
                url = src.get("url", "")
                if url and url not in seen_source_urls:
                    seen_source_urls.add(url)
                    all_sources.append(src)
        # Failed tickers are omitted from per_ticker_results (honest degradation)

    if not per_ticker_results:
        return {
            "lens": _lens,
            "available": False,
            "reason": "no fundamentals available: all tickers failed SEC EDGAR fetch",
            "payload": None,
            "sources": [],
        }

    logger.info(
        "SEC EDGAR portfolio fundamentals: %d/%d tickers resolved",
        len(per_ticker_results),
        len(universe),
    )
    return {
        "lens": _lens,
        "available": True,
        "payload": {
            "tickers": per_ticker_results,
            "coverage": {
                "available": len(per_ticker_results),
                "universe": len(universe),
            },
        },
        "sources": all_sources,
    }


def build_citation(citation: dict) -> dict | None:
    """Validate and return a structured citation {title, url, published, lens}.

    A well-formed citation requires all four fields and a valid http/https URL.
    Returns the citation dict unchanged on success; returns None for any
    malformed input.  Never raises — invalid input produces None.

    A claim with no source (or a None return) must be suppressed by the caller
    (CC-4: citation-missing = no claim).

    Args:
        citation: dict with keys title, url, published, lens.

    Returns:
        The citation dict if valid; None if any required field is missing or
        the url is not a well-formed http/https URL.
    """
    if not isinstance(citation, dict):
        return None

    # All four fields are required and must be non-empty strings.
    for field in ("title", "url", "published", "lens"):
        value = citation.get(field)
        if not isinstance(value, str) or not value.strip():
            return None

    url = citation["url"].strip()

    # Only http and https schemes are permitted for clickable news links.
    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    # Reject bare scheme with no host (e.g. "http://").
    scheme_end = url.index("://") + 3
    if len(url) <= scheme_end or "/" not in url[scheme_end:] and len(url[scheme_end:]) == 0:
        host_part = url[scheme_end:].split("/")[0]
        if not host_part:
            return None

    return citation


# Alias for tests that look up either name.
validate_citation = build_citation


def _build_optuna_section(autotune_run: dict | None) -> dict:
    """The Optuna OOS-vs-train evidence — element 3.

    Carries train_alpha, oos_alpha, fallback_oos_alpha, default_oos_alpha and
    baseline_decision. When ``get_latest_autotune_run`` returns None (Optuna
    has not run for this symphony yet) the section is well-shaped but flagged
    absent — assembly must not break (refinement 5).
    """
    if not isinstance(autotune_run, dict):
        # Guard against None (Optuna not yet run) and any non-dict value
        # (e.g. test mocks that replace the whole database module).
        return {
            "available": False,
            "note": (
                "Optuna has not run for this symphony yet — no walk-forward "
                "validation evidence is available. Treat the live config as "
                "unvalidated and weight data_sufficiency accordingly."
            ),
            "train_alpha": None,
            "oos_alpha": None,
            "fallback_oos_alpha": None,
            "default_oos_alpha": None,
            "baseline_decision": None,
        }
    return {
        "available": True,
        "run_timestamp": autotune_run.get("run_timestamp"),
        "train_alpha": autotune_run.get("train_alpha"),
        "oos_alpha": autotune_run.get("oos_alpha"),
        "oos_train_gap": _safe_gap(autotune_run.get("oos_alpha"), autotune_run.get("train_alpha")),
        "fallback_oos_alpha": autotune_run.get("fallback_oos_alpha"),
        "default_oos_alpha": autotune_run.get("default_oos_alpha"),
        "baseline_decision": autotune_run.get("baseline_decision"),
    }


def _safe_gap(oos: object, train: object) -> float | None:
    """OOS-minus-train alpha gap, or None if either operand is missing."""
    if isinstance(oos, (int, float)) and isinstance(train, (int, float)):
        return float(oos) - float(train)
    return None


def _build_suggestible_surface(symphony_id: str | None) -> list[dict]:
    """The 7-item allowlisted config surface, each with definition + range +
    current live value + locked flag.

    Elements 1, 2, 4, 5. The current live values come from the per-symphony
    ``symphony_strategies`` row (falling back to DEFAULT_STRATEGY) — NEVER from
    os.environ. The set of keys is the 6 Optuna search-space keys plus the
    untuned MAX_SQUEEZE_FLOOR: a curated allowlist, not a denylist.
    """
    current_params, locked_vars = _read_current_strategy(symphony_id)

    allowlist_keys = sorted(_OPTUNA_SEARCH_SPACE_KEYS) + [_UNTUNED_SUGGESTIBLE_KEY]

    surface: list[dict] = []
    for key in allowlist_keys:
        definition = _PARAM_DEFINITIONS.get(key, {})
        surface.append(
            {
                "config_key": key,
                "definition": definition.get("definition", ""),
                "risk_polarity": definition.get("risk_polarity", ""),
                "valid_range": _PARAM_VALID_RANGES.get(key),
                "current_live_value": current_params.get(key),
                "locked": key in locked_vars,
                "optuna_tuned": key in _OPTUNA_SEARCH_SPACE_KEYS,
            }
        )
    return surface


def _read_current_strategy(
    symphony_id: str | None,
) -> tuple[dict, list]:
    """Read the per-symphony live params + locked_vars from the state DB.

    Falls back to ``database.DEFAULT_STRATEGY`` / ``DEFAULT_LOCKED_VARS`` if no
    row exists or the read fails — assembly must stay well-shaped. Reads ONLY
    the curated strategy surface; never the environment.
    """
    default_params = dict(getattr(database, "DEFAULT_STRATEGY", {}))
    default_locked = list(getattr(database, "DEFAULT_LOCKED_VARS", []))

    if symphony_id is None:
        return default_params, default_locked

    try:
        strategy = database.get_symphony_strategy(symphony_id)
    except Exception:  # noqa: BLE001 - degrade to defaults, never break assembly
        logger.debug(
            "get_symphony_strategy failed for %s; using DEFAULT_STRATEGY",
            symphony_id,
            exc_info=True,
        )
        return default_params, default_locked

    if not strategy:
        return default_params, default_locked

    params = strategy.get("parameters") or {}
    locked = strategy.get("locked_vars")
    if locked is None:
        locked = default_locked
    # Layer the live params over the defaults so every allowlist key resolves.
    merged = {**default_params, **params}
    return merged, list(locked)


# Sentinel used by assemble_advisor_context to distinguish "caller did not
# pass autotune_run" (fetch from DB) from "caller explicitly passed None"
# (Optuna has not run — skip the DB fetch).
_SENTINEL = object()


def build_assessment_from_context(context: dict) -> dict:
    """Build a per-symphony assessment dict from the assembled context.

    AC3 resolution: ``ConfigSuggestionsResponse`` (ai_advisor.py:197-204) has
    only a ``suggestions: list[ConfigSuggestion]`` field — no summary/assessment
    at the response level. The route extracts only ``.suggestions`` and
    previously discarded the assembled context entirely. The assessment is
    therefore built here from ``context["optuna_evidence"]``, which already
    carries ``baseline_decision``, ``oos_alpha``, ``fallback_oos_alpha``,
    ``default_oos_alpha``, and ``available``.

    Returns a dict with at minimum:
      - ``baseline_decision``: the autotuner's decision for this symphony.
      - ``oos_alpha``: finite float or None (sentinel for -inf / no valid trial).
      - ``fallback_oos_alpha``: the fallback OOS guard-alpha.
      - ``default_oos_alpha``: the default OOS guard-alpha.
      - ``summary``: a human-readable string explaining the tuning state.

    The summary is per-symphony — two symphonies in different states produce
    different summaries so the UI is differentiated rather than generic.
    """
    evidence: dict = context.get("optuna_evidence") or {}
    baseline_decision = evidence.get("baseline_decision")
    oos_alpha = evidence.get("oos_alpha")
    fallback_oos_alpha = evidence.get("fallback_oos_alpha")
    default_oos_alpha = evidence.get("default_oos_alpha")
    available = evidence.get("available", False)

    if not available:
        summary = (
            "Optuna has not yet run for this symphony — no walk-forward "
            "validation evidence is available. Config is unvalidated; "
            "Claude is reasoning without OOS data."
        )
    elif oos_alpha is None:
        # -inf sentinel: all trials were haircut-rejected by FDR gate.
        summary = (
            f"No statistically-significant tuning edge: all optimizer trials "
            f"failed the FDR significance gate; out-of-sample guard-alpha is "
            f"negative (fallback={fallback_oos_alpha}, default={default_oos_alpha}). "
            f"Baseline decision: {baseline_decision}. Holding current config."
        )
    else:
        # Guard oos_alpha format: only apply .4f when it is a real numeric type.
        # A MagicMock or unexpected value must not crash the summary builder.
        oos_str = f"{oos_alpha:.4f}" if isinstance(oos_alpha, (int, float)) else str(oos_alpha)
        summary = (
            f"Optimizer found a validated edge: OOS alpha={oos_str}, "
            f"baseline decision={baseline_decision}. "
            f"Fallback OOS alpha={fallback_oos_alpha}, "
            f"default OOS alpha={default_oos_alpha}."
        )

    return {
        "baseline_decision": baseline_decision,
        "oos_alpha": oos_alpha,
        "fallback_oos_alpha": fallback_oos_alpha,
        "default_oos_alpha": default_oos_alpha,
        "summary": summary,
    }


def assemble_advisor_context(
    scope: str,
    symphony_id: str | None = None,
    composer_symphony_id: str | None = None,
    autotune_run=_SENTINEL,
) -> dict:
    """Assemble the prompt-ready context blob for the Claude config advisor.

    Carries all 8 must-have prompt elements plus role framing
    (prompt-methodology.md §1.1):

      1. Per-param definition + risk polarity.
      2. Valid range of every suggestible param.
      3. The Optuna OOS-vs-train delta + baseline_decision.
      4. Current live value of each param.
      5. locked_vars.
      6. Volatility regime context.
      7. The data window + its limits.
      8. Risk invariants as hard constraints.
      9. Operator-assist role + task framing.

    Real-money-critical: the config surface is the 7-item curated ALLOWLIST.
    This function NEVER reads ``dict(os.environ)`` or dumps ``.env``. No
    credential, account id, safety flag, or methodology knob can enter the
    returned dict.

    Args:
        scope: "symphony" (per-symphony advisory) or "global".
        symphony_id: required when ``scope == "symphony"``. Used as the key for
            all state-DB lookups (autotune_runs, symphony_strategies) — keyed by
            normalized name in this project.
        composer_symphony_id: optional Composer hash ID for the symphony. When
            supplied it is passed to ``symphony_logic.get_condensed_logic`` so
            the Composer ``/score`` API receives the hash it expects; if omitted,
            ``symphony_id`` is used for the logic fetch (backward-compatible).
        autotune_run: optional pre-fetched autotune run dict. When supplied the
            internal ``database.get_latest_autotune_run`` call is skipped —
            the caller is responsible for fetching (useful when the caller
            already holds a DB reference that may be patched in tests).

    Returns:
        A well-shaped context dict — even when Optuna has not run for the
        symphony (the Optuna section is then marked absent, not omitted).

    Raises:
        ValueError: if ``scope == "symphony"`` and ``symphony_id`` is None.
    """
    if scope == "symphony" and symphony_id is None:
        raise ValueError(
            "scope='symphony' requires a symphony_id — refusing to assemble a contextless prompt."
        )

    # Resolve the autotune_run: use the caller-supplied value when provided
    # (non-_SENTINEL), otherwise fetch from DB. This avoids a redundant DB
    # round-trip when the route already holds a reference (e.g. app.py's
    # ai_advisor_suggest fetches it to build the assessment context and passes
    # it here so both uses share the same row).
    if autotune_run is _SENTINEL:
        autotune_run = None
        _fetch_autotune = True
    else:
        _fetch_autotune = False

    condensed_logic: dict | None = None
    if scope == "symphony":
        # P1 dependency — Optuna walk-forward metrics. May be None (not tuned).
        if _fetch_autotune:
            autotune_run = database.get_latest_autotune_run(symphony_id)
        # P2 dependency — condensed symphony logic / composition.
        # Use the Composer hash ID when available; the Composer /score endpoint
        # requires the hash, not the normalized name (bug fix: passing the
        # normalized name produced HTTP 400 and an all-empty logic struct).
        logic_id = composer_symphony_id if composer_symphony_id is not None else symphony_id
        condensed_logic = symphony_logic.get_condensed_logic(logic_id)

    try:
        _derivatives_block = _build_derivatives_section()
    except Exception as _exc:
        _derivatives_block = {
            "lens": "derivatives",
            "available": False,
            "reason": type(_exc).__name__,
            "payload": None,
            "sources": [],
        }

    context: dict = {
        "scope": scope,
        "symphony_id": symphony_id,
        # Element 9 — role + task framing.
        "role_framing": _ROLE_FRAMING,
        # Elements 1, 2, 4, 5 — the allowlisted suggestible surface.
        "suggestible_surface": _build_suggestible_surface(symphony_id),
        "locked_vars": _read_current_strategy(symphony_id)[1],
        # Element 3 — Optuna OOS-vs-train evidence.
        "optuna_evidence": _build_optuna_section(autotune_run),
        # Element 6 — volatility regime context.
        "volatility_regime": _build_volatility_regime(autotune_run),
        # Element 7 — the data window + its limits.
        "data_window": _DATA_WINDOW,
        # Element 8 — hard risk invariants.
        "risk_invariants": _RISK_INVARIANTS,
        # P2 — condensed symphony logic / composition.
        "symphony_logic": condensed_logic,
        # Cycle-1 multi-lens scaffold — 5 stub lens blocks.
        # Each is available=False until the fast-follow producer connects its
        # free-data source.  Honest degradation: never fabricates analytical
        # context (CC-3 data-wall, GATE-1-AC §1).
        "technicals": _build_technicals_section(),
        "sentiment": _build_sentiment_section(),
        "derivatives": _derivatives_block,
        "macro": _build_macro_section(),
        "fundamentals": _build_fundamentals_section(),
    }
    return context


# ---------------------------------------------------------------------------
# Claude client + structured-output request.
# ---------------------------------------------------------------------------


def _build_client():
    """Construct the anthropic SDK client.

    Factory seam: tests patch ``ai_advisor._build_client``. Constructing the
    client here (not inline in ``request_suggestions``) means a missing or
    invalid ``ANTHROPIC_API_KEY`` surfaces as a construction failure that
    ``request_suggestions`` catches and degrades gracefully on.

    Raises:
        RuntimeError: if the SDK is unavailable or no API key is configured.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the Claude config advisor is "
            "unavailable until an API key is configured."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - SDK is a declared dep
        raise RuntimeError(f"the anthropic SDK is not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=api_key)


def _build_messages(context: dict) -> list[dict]:
    """Render the assembled context dict into the Claude messages payload."""
    import json

    return [
        {
            "role": "user",
            "content": (
                "Analyze the following Planet Stopper risk-engine context and "
                "propose 0..N config edits. Each suggestion must cite specific "
                "supplied numbers in its rationale. Stay strictly within the "
                "stated valid ranges. Never emit a suggested value for a "
                "locked param. If no edit is well-supported, return an empty "
                "suggestions list.\n\n" + json.dumps(context, default=str, indent=2)
            ),
        }
    ]


def request_suggestions(
    context: dict,
) -> tuple[ConfigSuggestionsResponse | None, str | None]:
    """Call Claude for structured config suggestions.

    On-demand operator-assist: a failure is "no suggestion this click", zero
    engine impact. This function NEVER raises — every failure mode (client
    construction failure, SDK error, malformed/unparseable response) degrades
    to ``(None, error_message)`` where ``error_message`` is a non-empty string
    the UI can show the operator.

    An empty suggestions list is a valid NON-error response — it flows through
    as ``(ConfigSuggestionsResponse(suggestions=[]), None)``.

    Returns:
        ``(ConfigSuggestionsResponse, None)`` on success;
        ``(None, error_message)`` on any failure.
    """
    # Build the client behind the factory seam — catch construction failure.
    try:
        client = _build_client()
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        # D-1 security contract: do NOT embed str(exc) in the browser-facing
        # message — exception text may contain API keys or internal paths.
        # exc_info detail is logged server-side only.
        msg = f"Claude advisor unavailable: could not build client ({type(exc).__name__})."
        logger.warning("ai_advisor: client construction failed: %s", exc, exc_info=True)
        return None, msg

    # Call Claude's structured-output endpoint. anthropic 0.85.0 exposes
    # client.messages.parse(..., output_format=<PydanticModel>) which returns
    # an SDK response whose .content list carries one or more ParsedTextBlocks;
    # each ParsedTextBlock has a .parsed_output field holding the validated
    # Pydantic instance. We walk content and take the first non-None
    # .parsed_output (see the extraction loop below).
    try:
        sdk_response = client.messages.parse(
            model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),
            max_tokens=_MAX_TOKENS,
            output_format=ConfigSuggestionsResponse,
            messages=_build_messages(context),
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - the whole contract: never raise
        # D-1 security contract: do NOT embed str(exc) in the browser-facing
        # message — exception text may contain API keys or internal paths.
        # Full detail is logged server-side via exc_info=True; the UI sees only
        # the error class name, mirroring the client-construction failure path.
        msg = (
            f"Claude advisor request failed ({type(exc).__name__}). Try again, or decide manually."
        )
        logger.warning(
            "ai_advisor: messages.parse failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None, msg

    # Extract + validate the parsed structured output. On anthropic 0.85.0 the
    # validated Pydantic instance lives on each ParsedTextBlock inside
    # response.content, not on a top-level .parsed attribute (which does not
    # exist on ParsedMessage). Walk the content blocks and take the first
    # non-None parsed_output. A malformed response (all parsed_output=None, or
    # empty content) degrades gracefully — never raise.
    parsed = None
    for block in getattr(sdk_response, "content", None) or []:
        candidate = getattr(block, "parsed_output", None)
        if candidate is not None:
            parsed = candidate
            break
    if parsed is None:
        msg = (
            "Claude returned an unparseable response (no structured output). "
            "No suggestions this call."
        )
        logger.warning("ai_advisor: SDK response had no .parsed payload")
        return None, msg

    if isinstance(parsed, ConfigSuggestionsResponse):
        return parsed, None

    # The SDK handed back something — coerce it through the schema so a
    # dict-shaped payload still validates, and a wrong-shape payload (e.g. a
    # bare string) fails validation gracefully rather than raising upstream.
    try:
        if isinstance(parsed, BaseModel):
            response = ConfigSuggestionsResponse.model_validate(parsed.model_dump())
        elif isinstance(parsed, dict):
            response = ConfigSuggestionsResponse.model_validate(parsed)
        else:
            raise TypeError(
                f"parsed output is {type(parsed).__name__}, not a ConfigSuggestionsResponse"
            )
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        msg = (
            f"Claude returned a response that did not match the expected "
            f"schema ({type(exc).__name__}). No suggestions this call."
        )
        logger.warning("ai_advisor: response failed schema validation: %s", exc)
        return None, msg

    return response, None


# ---------------------------------------------------------------------------
# C2 — safety gates on the suggestions Claude emits.
#
# Four independent layers, defense-in-depth on top of C1's context allowlist:
#   1. enforce_suggestion_allowlist — structural rejection of any config_key
#      Claude emits that is not one of the 9 suggestible params (hallucinated
#      keys, credentials, LIVE_EXECUTION, methodology knobs).
#   2. compute_risk_direction / check_risk_direction_agreement — the engine
#      computes risk polarity itself and flags any contradiction with Claude's
#      self-reported risk_direction. A real-money system never trusts a model's
#      self-classification.
#   3. revalidate_suggestion_oos — routes an accepted suggestion through the
#      autotuner's run_simulation OOS gate before it can reach live config.
#   4. enforce_locked_var_gate — rejects any suggestion that touches a variable
#      locked by the current theory bundle (NN1 spec-freeze enforcement).
# ---------------------------------------------------------------------------

# The 7-item suggestible allowlist: the 6 Optuna search-space keys plus the one
# untuned hand-set key. Derived from the C1 constants so it cannot drift.
# (config-surface.md §1 — an allowlist, not a denylist.)
_SUGGESTIBLE_ALLOWLIST = frozenset(_OPTUNA_SEARCH_SPACE_KEYS) | {_UNTUNED_SUGGESTIBLE_KEY}

# Risk-direction outcomes.
_LOOSENS = "loosens"
_TIGHTENS = "tightens"
_NEUTRAL = "neutral"

# Per-key polarity when a param's value is RAISED (suggested > current).
# Derived from the ``risk_polarity`` field of _PARAM_DEFINITIONS
# (config-surface.md §1). Every suggestible key is "raising loosens risk"
# EXCEPT TAKE_PROFIT_MC_PCT, the lone inverted param ("raising tightens risk").
# Built from _PARAM_DEFINITIONS rather than re-listed so it cannot drift.
_RAISE_RISK_DIRECTION: dict[str, str] = {
    key: (_TIGHTENS if definition.get("risk_polarity") == "raising tightens risk" else _LOOSENS)
    for key, definition in _PARAM_DEFINITIONS.items()
}

# Inverse of a raise: lowering a param does the opposite of raising it.
_OPPOSITE_DIRECTION = {
    _LOOSENS: _TIGHTENS,
    _TIGHTENS: _LOOSENS,
    _NEUTRAL: _NEUTRAL,
}


def enforce_suggestion_allowlist(
    suggestions: list[ConfigSuggestion],
) -> tuple[list[ConfigSuggestion], list[ConfigSuggestion]]:
    """Partition suggestions into (allowed, rejected) by ``config_key``.

    Defense-in-depth: even though the C1 *context* is allowlisted, Claude could
    hallucinate a ``config_key`` that was never in the prompt — or emit a
    credential, the ``LIVE_EXECUTION`` master safety flag, an account UUID, or a
    methodology knob. Any ``config_key`` not in the 7-item suggestible allowlist
    is structurally routed to ``rejected``; it must be impossible for such a key
    to reach a live config write.

    Args:
        suggestions: the suggestions Claude emitted (may be empty).

    Returns:
        ``(allowed, rejected)`` — two lists, order-preserving, with every input
        suggestion in exactly one partition (no drop, no duplication).
    """
    allowed: list[ConfigSuggestion] = []
    rejected: list[ConfigSuggestion] = []
    for suggestion in suggestions:
        if suggestion.config_key in _SUGGESTIBLE_ALLOWLIST:
            allowed.append(suggestion)
        else:
            rejected.append(suggestion)
    return allowed, rejected


def compute_risk_direction(config_key: str, current_value, suggested_value) -> str:
    """Compute, code-side, whether a suggestion loosens or tightens risk.

    The engine never trusts Claude's self-reported ``risk_direction``; it
    derives the direction itself from each param's documented risk polarity
    (config-surface.md §1, mirrored in ``_RAISE_RISK_DIRECTION``).

    Polarity rule: for every suggestible key, RAISING the value loosens risk —
    EXCEPT ``TAKE_PROFIT_MC_PCT``, the lone inverted param, where raising the
    value tightens risk. Lowering a value is the exact inverse of raising it.
    An unchanged value (suggested == current) is always ``neutral``.

    Args:
        config_key: one of the 9 suggestible keys.
        current_value: the current live value.
        suggested_value: Claude's proposed value.

    Returns:
        ``"loosens"`` | ``"tightens"`` | ``"neutral"``.
    """
    if suggested_value == current_value:
        return _NEUTRAL

    raise_direction = _RAISE_RISK_DIRECTION.get(config_key)
    if raise_direction is None:
        # Not a recognised suggestible key — no polarity is defined, so the
        # engine cannot classify the risk delta. The allowlist gate is the
        # layer that rejects such keys; here we simply decline to guess.
        return _NEUTRAL

    if suggested_value > current_value:
        return raise_direction
    return _OPPOSITE_DIRECTION[raise_direction]


def check_risk_direction_agreement(suggestion: ConfigSuggestion) -> dict:
    """Cross-check Claude's self-reported risk direction against the engine's.

    Claude self-classifies each suggestion's ``risk_direction``; a real-money
    system never trusts that. This gate computes the direction independently
    and reports whether the two agree, surfacing BOTH directions so the
    operator UI can show a contradiction explicitly.

    Args:
        suggestion: the ConfigSuggestion to check.

    Returns:
        ``{agrees: bool, code_direction: str, claimed_direction: str}``.
    """
    code_direction = compute_risk_direction(
        suggestion.config_key,
        suggestion.current_value,
        suggestion.suggested_value,
    )
    claimed_direction = suggestion.risk_direction
    return {
        "agrees": code_direction == claimed_direction,
        "code_direction": code_direction,
        "claimed_direction": claimed_direction,
    }


def revalidate_suggestion_oos(
    symphony_id: str,
    config_key: str,
    suggested_value,
    current_strategy: dict,
) -> dict:
    """Re-validate an accepted suggestion through the autotuner's OOS gate.

    A Claude suggestion is an *unvalidated hypothesis*; Optuna's output is
    walk-forward validated. Before an accepted suggestion can reach live config,
    it must pass the same out-of-sample gate Optuna's own output faces: its OOS
    alpha must strictly beat the current strategy's baseline OOS alpha.

    ``run_simulation`` is called TWICE over the same history window —
    apples-to-apples: once with ``current_strategy`` (the baseline), once with
    the strategy patched to ``suggested_value``.

    Pass rule is strict ``>``: a TIE does NOT pass — a tie buys no validated
    improvement, mirroring the autotuner's own strict-positive cascade rule.

    The ``autotuner`` import is LAZY (inside this function body, not at module
    scope): importing ``autotuner`` pulls in optuna + joblib, whose process-pool
    / resource-tracker import side effects collide with the anthropic SDK import
    under pytest's output capture. Deferring the import past that fragile window
    is mandatory.

    Args:
        symphony_id: the symphony the suggestion targets.
        config_key: the config key the suggestion edits.
        suggested_value: Claude's proposed value for ``config_key``.
        current_strategy: the current per-symphony strategy dict — the baseline.

    Returns:
        ``{passed: bool, oos_alpha: float, baseline_oos_alpha: float,
        detail: str}``.
    """
    # Lazy imports — deferred past the anthropic-SDK / optuna import-collision
    # window. Module-scope imports of autotuner or synthetic_history would break
    # the C2 import-guard tests; keep them inside the function body.
    # current_date_str: today in YYYY-MM-DD, the format autotuner.run_simulation
    # expects (it calls datetime.strptime(current_date_str, "%Y-%m-%d")).
    from datetime import datetime as _datetime

    import synthetic_history as _synthetic_history
    from autotuner import calculate_historical_deviation, run_simulation

    current_date_str = _datetime.now().strftime("%Y-%m-%d")

    # bot_state: the live engine state from the state DB. Used to build history
    # and to identify which account keys belong to this symphony.
    bot_state = database.load_state()

    # acc_sym_ids: the bot_state keys whose normalized symphony name matches
    # symphony_id. Mirrors the derivation in autotuner.run_autotuner's objective
    # closure (autotuner.py line 314).
    acc_sym_ids = [
        k
        for k, v in bot_state.items()
        if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == symphony_id
    ]

    # history_data: 125-day synthetic replay history. This call is on the
    # operator-accept path (rare human action), NOT the 1-minute engine cycle.
    # Latency is acceptable because generate_synthetic_history maintains a
    # date+holdings-keyed file cache; the autotuner fills this cache each cycle
    # before market open, so the cold-fetch case (no cache) only occurs when
    # the autotuner has not yet run — already a degraded-data situation.
    history_data = _synthetic_history.generate_synthetic_history(bot_state, current_date_str)

    # deviation_dict: 45-day trailing execution-deviation penalties by exit reason.
    deviation_dict = calculate_historical_deviation(current_date_str)

    # Patch the strategy to the suggested value — same window, only one knob
    # changes, so the OOS comparison is apples-to-apples.
    patched_strategy = dict(current_strategy)
    patched_strategy[config_key] = suggested_value

    # Call run_simulation twice: baseline first, then the patched strategy.
    baseline_oos_alpha = run_simulation(
        current_strategy, history_data, acc_sym_ids, current_date_str, deviation_dict
    )
    patched_oos_alpha = run_simulation(
        patched_strategy, history_data, acc_sym_ids, current_date_str, deviation_dict
    )

    # Strict `>` — a tie does not pass (autotuner strict-positive cascade rule).
    passed = patched_oos_alpha > baseline_oos_alpha

    if passed:
        detail = (
            f"OOS re-validation PASSED for {config_key}={suggested_value} on "
            f"{symphony_id}: patched OOS alpha {patched_oos_alpha} strictly "
            f"beats baseline {baseline_oos_alpha}."
        )
    else:
        detail = (
            f"OOS re-validation FAILED for {config_key}={suggested_value} on "
            f"{symphony_id}: patched OOS alpha {patched_oos_alpha} does not "
            f"strictly beat baseline {baseline_oos_alpha} — not greenlit for a "
            f"live config write."
        )

    return {
        "passed": passed,
        "oos_alpha": patched_oos_alpha,
        "baseline_oos_alpha": baseline_oos_alpha,
        "detail": detail,
    }
