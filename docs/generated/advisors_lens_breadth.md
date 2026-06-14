# advisors/lens_breadth

> 500-name S&P market-breadth producer: % of S&P 500 constituents above their 50-day and 200-day SMA — the institutional gold-standard breadth indicator, computed for free.

**Source:** `advisors/lens_breadth.py`
**Last updated:** 2026-06-14

## Overview

Computes true market breadth over the S&P 500 constituents, for free, by combining a free constituent list with the existing Alpaca daily-bar path. Off-execution-path, advisory-only, never-raising, D-1 error contract.

**Key properties:**
- **Free.** Constituent list from datahub CSV (primary) / Wikipedia (fallback); prices from the existing Alpaca IEX daily-bar path (`synthetic_history.fetch_bars`). ~17 batched requests for a nightly run.
- **Honest-availability.** Breadth is computed over the **qualifying sub-universe** (names with ≥`_MIN_QUALIFYING_BARS`=200 daily closes AND a current bar). Short-history / halted / missing names are excluded from the denominator — never imputed, never fabricated. The result reports `qualifying_count` / `total_count` (e.g. 501/503).
- **IEX-basis caveat.** Daily bars come from Alpaca's free IEX feed (IEX-exchange trades only, ~2.5% of volume). Acceptable for a 500-name aggregate; the read is "IEX-close-based", not consolidated-tape.
- **Above = at-or-above (`>=`).** A name counts as "above" its SMA when `close >= SMA`. For real prices `>` and `>=` are equivalent (an exact close-on-SMA to float precision essentially never occurs); the boundary only matters in synthetic test data. `[PM-noted micro-choice — switchable to strict `>` on request.]`
- **D-1.** Every `available=False` `reason` carries only `type(exc).__name__`.

## Public API

### `get_sp500_constituents() -> list[str]`
Fetches the current S&P 500 constituent tickers. datahub `constituents.csv` primary; Wikipedia "List of S&P 500 companies" table fallback. Enforces a **490–510 row sanity band** (`_SANITY_MIN`/`_SANITY_MAX`); a list outside the band is rejected and the fallback is tried. Returns `[]` if both sources fail or are out-of-band (honest — never a stale/fabricated list). Dot-format class tickers (`BRK.B`) pass through unchanged (datahub, Wikipedia, and Alpaca all agree on dot format).

### `compute_breadth(constituents: list[str]) -> dict`
Fetches daily bars via `synthetic_history.fetch_bars` and returns `{pct_above_50sma, pct_above_200sma, qualifying_count, total_count}`. A name qualifies for the 200-day denominator only with ≥200 closes and a current bar. Fractions are over the qualifying sub-universe, in `[0, 1]`.

### `fetch_breadth() -> dict`
Public entry. Returns `{available, pct_above_50sma, pct_above_200sma, qualifying_count, total_count, source, reason?}`. `available=False` + `reason` on empty constituents, bar-fetch failure, or zero qualifying names. Persists every run to the warehouse via `lens_warehouse.persist_lens_snapshot(lens="breadth", ...)`. Never raises.

## Constants
| Constant | Value | Purpose |
|----------|-------|---------|
| `_SANITY_MIN` / `_SANITY_MAX` | 490 / 510 | Constituent-count sanity band |
| `_MIN_QUALIFYING_BARS` | 200 | Min daily closes to qualify for the 200-SMA denominator |
| `_SMA_50_WINDOW` / `_SMA_200_WINDOW` | 50 / 200 | SMA windows |
| `_BREADTH_TIMEOUT_S` | 15.0 | HTTP timeout for constituent fetch |
| `_BAR_LOOKBACK_CALENDAR_DAYS` | 320 | Calendar lookback to ensure ≥200 trading bars |
| `_SOURCE` | `"datahub+alpaca-iex"` | Provenance string |

## Scope
Off-execution-path; advisory-only; no Flask route; no `LIVE_EXECUTION`. Live read only (current constituents on current prices — no point-in-time/survivorship handling, which is out of scope for a live breadth read). No production caller wired yet; intended to feed the technicals lens's breadth number (fast-follow) while the 14-ETF basket retains posture/momentum.

## Tests
`tests/ai_advisor/test_lens_breadth.py` (39 tests): constituent parse (datahub primary / Wikipedia fallback / freshness+sanity reject / both-fail→[]), breadth math over the qualifying sub-universe (derived from mocks, no hardcoded values), honest-availability (empty/fetch-fail/zero-qualifying → available=False+reason), warehouse persist, D-1 reasons, scope guards (no eval/exec/subprocess, no Flask route, no LIVE_EXECUTION). All network mocked — no live calls in CI.
