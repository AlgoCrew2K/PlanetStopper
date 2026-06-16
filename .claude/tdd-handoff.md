# TDD Handoff — lens-technicals ROUND 2 (proxy-universe fix)

**Branch:** feat/lens-technicals
**Base:** 652d913 (origin/main)
**RED commit:** 7e823d9
**RED state:** 5 failed, 41 passed (46 total)
**Phase:** green

## Defect summary (PM live-gate failure)

`_build_technicals_section` sources the universe from `database.load_state()`
`logic_holdings`. At 03:00 / weekends / flat markets, `logic_holdings={}` on
all 11 live symphonies (confirmed by PM live functional test). The lens's
PRIMARY consumer is `lens_pipeline.run_pipeline()` at 03:00 -- so the lens is
perpetually `available=False` at the exact time it is needed.

`logic_holdings` is a RUNTIME field populated only during market-hours
execution cycles. It is the wrong source for an off-hours lens.

## Fix contract

When `logic_holdings` yields no tickers, fall back to a named module-level
constant `_PROXY_UNIVERSE` (a market-proxy breadth basket) in
`advisors/lens_technicals.py`. The proxy is a FLOOR -- live holdings tickers
are MERGED with proxy tickers, not replaced.

[PM-ASSUMED] choice: named proxy basket constant (SPY + core sector ETFs) is
preferable to Composer `/score` calls (those are network I/O, heavyweight at
03:00, and off the execution-path spec).

## Test run command

```
pytest tests/ai_advisor/test_lens_technicals.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Target: 46 passed, 0 failed.

## What the tests need

### 1. ADD to `advisors/lens_technicals.py`

Add a named module-level constant (no magic literals -- AC-6 / math_engine.py
coding standard):

```python
# Market-proxy breadth basket -- stable universe for off-hours / flat-holding
# runs (e.g. nightly Prism at 03:00 when logic_holdings is empty on all
# symphonies). Chosen as major-cap equity benchmarks covering US large-cap,
# tech, small-cap, and international breadth. Source: standard institutional
# breadth-monitoring basket (see Investopedia "market breadth indicators").
_PROXY_UNIVERSE: list[str] = [
    "SPY",   # S&P 500 -- US large-cap benchmark
    "QQQ",   # Nasdaq 100 -- tech/growth
    "IWM",   # Russell 2000 -- US small-cap
    "EFA",   # MSCI EAFE -- developed international
    "AGG",   # US aggregate bond -- risk-off signal
    "GLD",   # Gold -- inflation / safe-haven
    "XLF",   # Financials sector
    "XLE",   # Energy sector
    "XLV",   # Health care sector
    "XLI",   # Industrials sector
]
```

(The exact tickers are PM-ASSUMED; document the choice with the source comment.
The tests check the constant exists, is a non-empty list[str], and that its
tickers reach `_get_bars` when holdings are empty. The tests do NOT hardcode
which specific tickers must be in the proxy.)

### 2. MODIFY `_build_technicals_section` in `ai_advisor.py`

The current logic (around line 464-479):

```python
    try:
        bot_state = database.load_state()
        tickers: set[str] = set()
        for entry in bot_state.values():
            if isinstance(entry, dict):
                for ticker in entry.get("logic_holdings", {}).keys():
                    if ticker:
                        tickers.add(ticker)
    except Exception:
        tickers = set()

    universe = sorted(tickers)
```

Must become:

```python
    try:
        bot_state = database.load_state()
        tickers: set[str] = set()
        for entry in bot_state.values():
            if isinstance(entry, dict):
                for ticker in entry.get("logic_holdings", {}).keys():
                    if ticker:
                        tickers.add(ticker)
    except Exception:
        tickers = set()

    # Merge with the proxy basket: live holdings may be empty off-hours
    # (logic_holdings={} at 03:00 / weekends / flat markets -- PM live-gate
    # finding). The proxy is a FLOOR that ensures the nightly Prism pipeline
    # always receives a real universe. Live tickers are merged in on top.
    tickers.update(lens_technicals._PROXY_UNIVERSE)
    universe = sorted(tickers)
```

The lazy import `from advisors import lens_technicals` is already at the top
of `_build_technicals_section` -- use the already-imported module, do NOT add
a second import.

### 3. Update docstring in `_build_technicals_section`

Update the docstring to say the universe is sourced from `logic_holdings`
MERGED WITH `_PROXY_UNIVERSE` (floor), replacing the current text that only
mentions `bot_state`. Example:

```
    Universe is sourced from the UNION of live bot_state logic_holdings and
    lens_technicals._PROXY_UNIVERSE (a named market-proxy basket).  The proxy
    is a floor so the nightly Prism pipeline (03:00, off-hours) always
    receives a real universe even when symphonies hold nothing.
```

### 4. Files to commit (path-scoped -- NEVER git add -A)

```
git add advisors/lens_technicals.py ai_advisor.py
```

## Edge cases to watch

- `_PROXY_UNIVERSE` is used BEFORE the universe is passed to
  `_fetch_technicals`. Do NOT add the proxy inside `_fetch_technicals` -- the
  wiring fix is in `_build_technicals_section` only.
- `tickers.update(lens_technicals._PROXY_UNIVERSE)` must come AFTER the
  `try/except` block (the proxy is always applied, even if `load_state()`
  raises).
- Do NOT change `_fetch_technicals` signature or internals -- the 5 existing
  math/schema tests mock it directly and will fail if its contract changes.
- `test_empty_state_with_proxy_still_available` passes a pre-built
  `return_value` dict keyed by `_PROXY_UNIVERSE` tickers. This means
  `_PROXY_UNIVERSE` must be defined at import time (it is a module constant).

## Status Log

- [2026-06-16] test-writer (LEAD): RED 7e823d9 -- 5 failed / 41 passed.
  TestProxyUniverseGuard (6 tests): 5 RED + 1 passing
  (test_genuine_unavailability_when_bars_fail passes because the existing
  exception path already handles bar-fetch failures correctly -- correct
  behavior that must be preserved after the fix).

## After GREEN

Send `SendMessage` to `team-lead` with:
"GREEN: <SHA> -- 46 passed / 0 failed. Ready for review."
