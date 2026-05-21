# Behavior Audit — Final Verification — 2026-05-20

**Branch tip audited:** 4a8e1e719cde4202d9635d048351a2d5f4a7afac
**Working tree:** clean
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0 (Gecko engine)
**Daemon:** http://127.0.0.1:5000 — HTTP 200 confirmed
**Viewport:** 1440×900, light theme
**Console errors at audit start:** 0

---

## Targeted re-checks (items from VERIFY-behavior-2026-05-20.md)

| ID | Description | Verdict | Evidence |
|---|---|---|---|
| D-BEH-05 | ET clock — never ticked (element missing) | **CLOSED** | `[data-testid="et-clock"]` element found in DOM. Clock read at t=0: `09:51:43`, re-read at t+2s: `09:51:45`. Ticking confirmed. `chrome.js` `updateClock()` writing to element every 1s. |
| D-BEH-R03 | ET clock element absent from `_chrome.html` | **CLOSED** | Same fix as D-BEH-05. `data-testid="et-clock"` now present. |
| D-BEH-R02 | Sparkline `Canvas exceeds max size` — 198 `InvalidStateError` per poll | **CLOSED** | Zero console errors after full `loadState` cycle. 11 sparkline canvases found. `domH: 40` (device pixels at 1.25× DPR = 32px CSS), `renderedH: 32px`, `dataUrlLen: 582` (drawing, well above 200-byte blank threshold). Height wrapper fix confirmed effective. |
| D-BEH-R01 | Market dot color and label text disagree | **STILL OPEN — partial fix only** | API `market_state: "open"` → `updateMarketDot` correctly leaves dot green (no `.closed` class). Dot is now consistent with API. However, Jinja renders `market_state_label` as a static `<span>` at page-load; `updateStatusStrip` updates chip counts only, never the label text. After `loadState` completes, dot = green (open) but label text = "Market closed" (stale Jinja render). Strip `innerText`: `"Market closed\nTrailing stop: 0\nTake-profit: 0\nVWAP: 0\n09:51:29 AM ET"`. Fix needed: `updateStatusStrip` must also update the market label span text from `meta.market_state_label`. |
| D-BEH-08 | Math overlays toggle — cannot confirm without active cards | **CONDITIONAL — unchanged** | Dev DB has no armed or triggered symphonies at audit time. Zero active-section cards, zero `[data-testid="mc-dial"]` elements in DOM. Cannot verify CSS `[data-math-overlays]` rule hides/shows dials at runtime. Mark CONDITIONAL until a dev fixture with an active symphony is available. |

---

## Summary

- **CLOSED this pass:** D-BEH-05, D-BEH-R03, D-BEH-R02 (3 items)
- **STILL OPEN:** D-BEH-R01 (partial fix — dot correct, label stale), D-BEH-08 (conditional)
- **New regressions found:** none

---

## Exhaustiveness declaration

I re-verified every item listed in the targeted sweep at HEAD `4a8e1e7`: ET clock element presence and tick cadence (2s interval confirmed), sparkline canvas height and error-free rendering (0 console errors, 32px rendered height, dataUrl > 200 bytes), market dot class vs API state vs label text (API-dot agreement confirmed, label-text gap identified), math overlays toggle (no active cards in dev, CONDITIONAL). No additional interactive elements were tested beyond the five in scope — this was a targeted re-check, not a full sweep.

**Leave-state:** Browser at http://127.0.0.1:5000/, 1440×900, light theme, dev server running.
