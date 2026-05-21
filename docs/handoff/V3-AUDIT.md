# Studio v3 — Design Handoff Audit

## Visual + Responsive Audit

**Branch tip:** `3a18157029eb75cb7752ce3410bf1647d8b557e9`
**Working tree:** feat/studio-design-handoff (modified files, no staged changes)
**Origin sync:** N/A (audit-only pass)
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0
**Audited:** 2026-05-20

---

### DASHBOARD

#### Design ground truth (design-v3-dashboard.png)
- Chrome: `AlphaBot guard` wordmark + nav + right-side status strip (`ONLINE · NEXT 00:00 · Roth IRA · DRY RUN · Force run now`)
- Hero: large bold guard-alpha % (`-3.53%`) top-left, hero chart spanning ~60% of page width, VS bars on the right
- VS bars: Today / Cumulative / Max DD — coloured horizontal bar pairs with delta labels, compact and tight
- Mini-stats row below chart: three boxes (Tracked: 11, Armed: 1, Triggered: 4)
- Symphony card grid below: 2-column grid, each card has badge (colour-coded strategy type), name, two % values, sparkline chart, stat rows
- Card height: ~180px; grid gap compact (~16px); cards are substantial rectangles, not skinny slivers

---

#### DASHBOARD — Light theme, all widths

[ ] **V-01** Dashboard — Hero section: wrong background colour
- **Design:** hero area uses warm off-white `#f5f3ef` (design-canvas background bleeds through, hero chart container has no heavy border)
- **Live 1440:** hero container has a prominent `1px solid` border box around both the chart area and the VS-bar section, creating a heavy double-bordered look absent from the design
- **Widths:** 1280, 1440, 1440-dark
- **Severity:** major

[ ] **V-02** Dashboard — VS bars missing coloured fill
- **Design:** "Today / Cumulative / Max DD" bars are filled green/red horizontal bars with clear visual weight
- **Live all widths:** bars render but the fill area is extremely thin (1–2px high), nearly invisible; the design shows them as substantial 10–12px tall coloured bars
- **Widths:** 1280, 1440, 2560, 3840, 5120
- **Severity:** blocker

[ ] **V-03** Dashboard — Symphony card grid: 1-column layout at 1280, 2-column at 1440, but design shows 2-column at all reasonable widths
- **Design:** 2-column card grid even at narrower widths
- **Live 1280:** cards stack in a single column, leaving a very wide single column that wastes space and departs from design intent
- **Live 1440+:** correctly 2-column
- **Widths:** 1280
- **Severity:** major

[ ] **V-04** Dashboard — Card grid: ultrawide reflow missing — cards stay 2-column at all ultrawide widths
- **Design:** At the design's 1440px reference, 2 columns. Expected: at 2560+ a 3- or 4-column grid to fill the canvas meaningfully
- **Live 2560:** still 2-column grid; cards are very wide (~1100px each), chart sparklines stretch to enormous width; the layout is functionally broken at ultrawide — content stranded centre, massive whitespace at sides
- **Live 3840:** 2-column, cards grotesquely wide (~1800px); charts distorted
- **Live 5120:** 2-column, entire layout is comically wide; the detail panel slides in from right edge nearly offscreen; unusable
- **Widths:** 2560, 3840, 5120
- **Severity:** blocker

[ ] **V-05** Dashboard — Detail panel at ultrawide: slides in at full viewport height but its width does not scale
- **Design (detail artboard):** ~580px wide panel; content tightly laid out
- **Live 3840:** panel appears at right edge, ~580px wide but anchored to right side; left 3200px of screen is card grid; panel feels disconnected
- **Live 5120:** panel anchor correct but appears as a thin sliver relative to viewport; content readable but proportion is wrong
- **Widths:** 3840, 5120
- **Severity:** major

[ ] **V-06** Dashboard — Hero chart shape mismatch
- **Design:** hero chart is a smooth line chart (no fill / area), showing guard-alpha return over time, left-aligned with subtle grid
- **Live:** hero chart renders with a green area fill beneath the line; the fill dominates the visual and is not in the design
- **Widths:** 1280, 1440, 2560, 3840, 5120
- **Severity:** major

[ ] **V-07** Dashboard — "LIVE NOW" badge on cards: design shows pill badges coloured per strategy type (TP=purple, STOP=red, VWAP=teal, BLEED=orange); live shows "LIVE NOW" red badges on all cards uniformly
- **Design:** strategy-type coloured pill (TP / STOP / VWAP / BLEED) in top-right of each card; separate from the "LIVE NOW" indicator
- **Live:** all cards show a red "LIVE NOW" or "CASH NOW" pill regardless of strategy type; strategy-type pill is absent
- **Widths:** 1280, 1440
- **Severity:** major

[ ] **V-08** Dashboard — Card typography: design uses 600-weight for primary % figures; live renders them at normal weight (400)
- **Design:** bold large % numbers (~700 weight, ~28px)
- **Live:** % figures appear at regular weight (~400), visually lighter than design
- **Widths:** 1280, 1440
- **Severity:** minor

[ ] **V-09** Dashboard — Dark theme: overall dark theme functional, but the hero chart area fill does not adapt — the green area fill remains bright green rather than using a muted dark-theme variant
- **Widths:** 1440-dark
- **Severity:** minor

[ ] **V-10** Dashboard — Status strip: design shows `ONLINE · NEXT 00:00` as a subtle green dot + text; live shows the same but the `Force run now` button in dark theme renders with incorrect background (too bright green vs the design's restrained teal)
- **Widths:** 1440-dark
- **Severity:** minor

---

### PERFORMANCE

#### Design ground truth (design-v3-performance.png)
- Page title: `Performance` (large bold ~40px), subtitle `Live (if held) vs AlphaBot-exited (shadow) · post-mortem snapshots`
- Filter row: `Aggregate / Per-symphony` toggle + `30d 60d 90d 125d YTD 1Y 5Y` time window pills (60d active)
- 4 stat boxes: `CUMULATIVE GUARD A`, `BOT TOTAL RETURN`, `IF-HELD TOTAL RETURN`, `OBSERVATIONS` — equal-width columns in a card row
- Cumulative returns chart: large, ~580px tall; two lines (solid bot + dashed if-held) with green divergence fill between; endpoint labels (+18.3% / +14.8%)
- Legend: `AlphaBot-exited (shadow) ·· Live (if held)  divergence shaded` — inline right of "Cumulative returns" heading
- Risk metrics table: clean with METRIC / LIVE·IF HELD / BOT·ALPHABOT-EXITED / DELTA columns; bold rows for Total return, Sharpe, Max drawdown, Calmar

---

#### PERFORMANCE — Light theme, all widths

[ ] **V-11** Performance — Page title badge: design has no sub-badge next to the page title
- **Design:** `Performance` standalone heading, subtitle below
- **Live:** `Performance LIVE vs ALPHABOT-EXITED` — orange/amber pill badge directly next to the heading at same line height; this badge is not in the design
- **Widths:** 1280, 1440, 2560, 3840, 5120
- **Severity:** minor

[ ] **V-12** Performance — `← BACK TO DASHBOARD` link present in live; absent from design
- **Design:** no back-link; navigation is via the main nav bar
- **Live:** a `← BACK TO DASHBOARD` link appears top-right of page on every width
- **Widths:** 1280, 1440, 2560, 3840, 5120
- **Severity:** minor

[ ] **V-13** Performance — Scope/Window filter layout mismatch
- **Design:** `Aggregate / Per-symphony` and time window pills are on a single right-aligned row at the top of the content area, horizontally to the right of the stat boxes
- **Live:** scope and window controls are in a separate card/section above the stat boxes, stacked in two rows (SCOPE label + Per-symphony, WINDOW label + pills) — creates an extra visual tier that the design doesn't have
- **Widths:** 1280, 1440
- **Severity:** major

[ ] **V-14** Performance — Stat boxes: design shows 4 equal-width boxes in a single row with clear separator lines
- **Live 1280:** boxes wrap — at 1280 the 4 stat boxes are in a 2×2 grid rather than a 4-column single row; this is not specified in the design
- **Live 1440+:** 4-column single row — correct
- **Widths:** 1280
- **Severity:** major

[ ] **V-15** Performance — Cumulative returns chart: live chart has no endpoint callout labels
- **Design:** solid green pill label at end of bot line (`+18.3%`), hollow circle at end of if-held line (`+14.8%`)
- **Live:** no endpoint labels; chart ends without annotation
- **Widths:** 1280, 1440, 2560, 3840, 5120
- **Severity:** major

[ ] **V-16** Performance — Cumulative returns chart legend: design positions legend inline to the right of the `Cumulative returns` section heading
- **Live:** legend appears below the heading on its own line, inside the chart card; the inline positioning is absent
- **Widths:** 1280, 1440
- **Severity:** minor

[ ] **V-17** Performance — Ultrawide layout: content does not reflow at 2560+
- **Live 2560:** content is a single centred column ~1200px wide; 600px of blank space either side; the chart and stat boxes do not utilise the extra canvas
- **Live 3840/5120:** same single-column layout becoming progressively more stranded in the centre; chart does not grow proportionally
- **Design:** no 2560+ spec, but a max-width container with centred layout means all content is cramped in the centre at ultrawide
- **Widths:** 2560, 3840, 5120
- **Severity:** major

[ ] **V-18** Performance — Risk metrics table: design shows `Max Drawdown` row with value; live shows `—` in the BOT column for Max Drawdown and renders a bright red horizontal line extending across the cell instead of a value
- **Widths:** 1440, 2560
- **Severity:** major

[ ] **V-19** Performance — Dark theme: risk metrics table has insufficient contrast — white text on dark card background is correct, but the bold values (Total return, Sharpe) are not distinguishable from regular-weight rows
- **Widths:** 1440-dark
- **Severity:** minor

---

### AI ADVISOR

#### Design ground truth (design-v3-advisor.png)
- Two-column layout: left ~60% is the suggestion list / parameter editor; right ~40% is `Recent autotune runs` sidebar
- Left: `AI advisor` heading, subtitle, `SYMPHONY` label + dropdown + `Run Claude advisor` green button
- Suggestion list: each suggestion has parameter name (bold), badge pills (CURRENT / RECOMMENDED / CONFIDENCE), large current value + arrow + new value, rationale text, `Apply suggestion` CTA button
- Right sidebar: `Recent autotune runs` heading, list of run entries with symphony name, timestamp, Sharpe, DSR metrics, status tags (FALLBACK / FROZEN-EVAL)

---

#### AI ADVISOR — all widths

[ ] **V-20** Advisor — No loaded state shown: live app is in empty/unloaded state (no symphony selected); the design shows a fully populated suggestion list
- This is a data/state gap, not a layout bug — the empty state does render correctly (`Select a symphony and click Run Advisor to get suggestions.`); design comparison is limited to structural layout only

[ ] **V-21** Advisor — Page is not two-column at 1280
- **Design:** two-column layout (suggestions left, recent runs right) at the design's 1440px reference
- **Live 1280:** suggestions panel and recent runs are in a single-column stack; recent runs fall below the suggestions panel; users would not see both simultaneously
- **Live 1440+:** two-column layout — correct
- **Widths:** 1280
- **Severity:** major

[ ] **V-22** Advisor — Recent autotune runs sidebar: at ultrawide widths, the sidebar maintains its ~220px width while the suggestions panel expands to fill all remaining width — severely unbalanced
- **Live 2560:** suggestions panel is ~2000px wide; sidebar is ~220px; ratio is approximately 10:1 vs design's ~3:2
- **Live 3840/5120:** same pattern, increasingly extreme
- **Widths:** 2560, 3840, 5120
- **Severity:** blocker

[ ] **V-23** Advisor — Run entry typography: design shows run entries with symphony name in normal weight, timestamp smaller/muted, Sharpe and DSR values in bold monospace
- **Live:** all text in run entries appears at uniform weight and size; no bold monospace for metric values; FALLBACK badge present but FROZEN-EVAL state tag appears in plain text rather than a coloured pill
- **Widths:** 1440
- **Severity:** minor

[ ] **V-24** Advisor — `Run Claude advisor` button: design uses a medium-weight green pill button with consistent padding
- **Live:** button renders correctly at 1440 but at 1280 it clips to its container edge when the dropdown is also present on the same row — the dropdown takes too much width
- **Widths:** 1280
- **Severity:** minor

[ ] **V-25** Advisor — Dark theme: overall render acceptable; `FALLBACK` badge background (amber/orange) renders at lower saturation than light theme, readable but inconsistent
- **Widths:** 1440-dark
- **Severity:** minor

---

### HISTORY

#### Design ground truth (design-v3-history.png)
- Title: `Guard alpha history` (large bold), subtitle `Rollup of post-mortem snapshots · how the bot's exits actually scored vs the if-held series`
- Filter row: `30d 60d 90d 125d YTD 1Y 5Y` pills (90d active)
- 4 stat boxes: TOTAL GUARD A / $ SAVED / TRIGGERS / WIN RATE — single row
- `Daily α` section: bar chart with green positive / red negative bars, `positive α` / `negative α` legend, date labels (`90d ago` → `today`)
- `By exit reason` section: 4 equal-width cards (Take-Profit / Trailing Stop / VWAP Breakdown / VWAP Bleed Cut); each card has coloured top-border, strategy pill, headline %, subtext, stats grid
- `Today's exits` section: table with TIME / SYMPHONY / REASON / DETAIL columns

---

#### HISTORY — all widths

[ ] **V-26** History — Page title mismatch
- **Design:** `Guard alpha history` with initial caps only on "Guard"
- **Live:** `Guard Alpha History` — capitalisation differs (Title Case vs Sentence case)
- **Widths:** all
- **Severity:** minor

[ ] **V-27** History — Subtitle text mismatch
- **Design:** `Rollup of post-mortem snapshots · how the bot's exits actually scored vs the if-held series`
- **Live:** `Post-mortem rollup — exit decisions and alpha saved over the selected window` — different phrasing
- **Widths:** all
- **Severity:** minor

[ ] **V-28** History — Exit reason cards: design shows 4 equal-width cards in a single row
- **Live 1280:** cards are in a 2×3 grid (6 exit reason cards instead of 4, and they wrap to multiple rows); the design shows only 4 cards
- **Live 1440+:** 5 cards in a single scrolling row (Parabolic Stop / Take-Profit / Trailing Stop / VWAP Bleed Cut / VWAP Breakdown) — 5 cards vs design's 4
- **Widths:** all
- **Severity:** major

[ ] **V-29** History — Exit reason card coloured top-border: design shows each card has a thick (~4px) coloured top border matching strategy colour (Take-Profit = purple, Trailing Stop = red-orange, VWAP = teal, Bleed = orange)
- **Live:** cards have a left-side coloured border stripe (not top), and the PARA / TP / STOP / VWAP / BLEED pill badges are inside the card rather than the top border carrying the colour signal
- **Widths:** 1440
- **Severity:** major

[ ] **V-30** History — Daily α bar chart: design shows compact dense bar chart with individual date bars, legend inline right of heading
- **Live:** chart renders correctly at 1440; at 2560+ bars become very wide and chart distorts proportionally — the chart does not have a max-width cap
- **Widths:** 2560, 3840, 5120
- **Severity:** major

[ ] **V-31** History — Today's exits table: design shows a 4-column table (TIME / SYMPHONY / REASON / DETAIL)
- **Live:** table has TIME / SYMPHONY / REASON / DETAIL columns — structurally correct; however the REASON column uses plain text colour rather than the design's coloured reason text (e.g. `Trailing Stop` in orange-red, `Take-Profit` in purple, `VWAP Bleed Cut` in orange) — at live these are plain dark text with no colour differentiation
- **Widths:** 1440
- **Severity:** major

[ ] **V-32** History — Ultrawide: single-column layout is unresponsive at 2560+, same issue as Performance — all content in a narrow centre column with extreme whitespace flanking it
- **Widths:** 2560, 3840, 5120
- **Severity:** major

[ ] **V-33** History — Dark theme: exit reason card backgrounds in dark mode render with the same cream/off-white colour as light mode — dark theme is NOT applied to these cards
- **Widths:** 1440-dark
- **Severity:** blocker

---

### SETTINGS

#### Design ground truth (design-v3-settings.png)
- Title: `Settings`, subtitle `.env globals + SQLite-isolated symphony strategies · live edits, no restart`
- Discard changes / Save changes buttons top-right
- Two-column layout: left sidebar (~190px) with navigation items (Master controls / Algorithm parameters / Credentials / Symphony overrides); aside note text below nav
- Right content area: `Master controls` section with `Live execution DRY RUN` toggle and `EXECUTION START TIME (ET)` field

---

#### SETTINGS — all widths

[ ] **V-34** Settings — Page is missing a sidebar nav at 1280
- **Design:** persistent left sidebar nav at all widths with section links
- **Live 1280:** sidebar nav is present but the layout is cramped — nav items overlap or the sidebar and content area compete for horizontal space
- **Widths:** 1280
- **Severity:** major

[ ] **V-35** Settings — Algorithm parameters section has more fields in live than design
- **Design (Master controls section):** shows only `Live execution` toggle + `EXECUTION START TIME` field
- **Live:** scrolling reveals `Algorithm parameters` section with many parameter fields (MAX_PARABOLIC_RATCHET, MAX_SOURCE_FLOOR, PARABOLIC_VELOCITY_THRESHOLD, TAKE_PROFIT_ALT_PCT, TRIGGER_THRESHOLD_PCT, VWAP_BLEED_MULTIPLIER, VWAP_CROSS_VWMA_PCT, VAR_BLEND_ITER) and `Credentials` section
- This is a scope difference (design only shows Master controls, live shows more) — not strictly a layout bug but the design's section hierarchy and visual treatment of parameter fields should match
- **Widths:** 1440
- **Severity:** minor (scope note, not visual defect)

[ ] **V-36** Settings — Parameter input fields: design not shown for algo-params section; however in live the input fields have inconsistent height — some appear taller than others in the same grid row
- **Widths:** 1280, 1440
- **Severity:** minor

[ ] **V-37** Settings — Ultrawide: same stranded-centre layout as Performance and History — content sits in a ~900px centre column with massive whitespace on both sides at 2560+
- **Widths:** 2560, 3840, 5120
- **Severity:** major

[ ] **V-38** Settings — Dark theme: `Live execution` toggle control in dark mode renders the toggle track in a very dark colour that makes it indistinguishable from the card background when OFF — the "off" state is invisible
- **Widths:** 1440-dark
- **Severity:** blocker

[ ] **V-39** Settings — Credentials section: password/secret fields show `show` / `hide` eye-icon buttons; in dark theme these buttons are white-on-white (icon becomes invisible)
- **Widths:** 1440-dark
- **Severity:** blocker

---

### CHROME / NAV (all screens)

[ ] **V-40** Chrome — Status strip: design has a single top-of-page status bar reading `ONLINE · NEXT 00:00 · Roth IRA · DRY RUN · Force run now` on the right
- **Live:** status information is split — `status strip` element appears ABOVE the main chrome nav bar, rather than inline with it as in the design; this creates an extra bar that adds ~35px of vertical height before the nav bar even begins
- **Widths:** all screens, all widths
- **Severity:** major

[ ] **V-41** Chrome — `Force run now` button: design shows a filled teal/green button with white text
- **Live light:** correct
- **Live dark:** button background changes to a lighter, desaturated green that has insufficient contrast against the dark navbar background
- **Widths:** 1440-dark (all screens)
- **Severity:** minor

[ ] **V-42** Chrome — Nav active indicator: design shows an underline on the active nav item, rendered in the same green as the brand colour
- **Live:** active underline is present but is 3px vs the design's ~2px, making it slightly heavier than intended
- **Widths:** 1440 (all screens)
- **Severity:** minor

---

### SUMMARY

| Severity | Count |
|----------|-------|
| Blocker  | 7     |
| Major    | 23    |
| Minor    | 12    |
| **Total**| **42**|

**Blockers (require fix before any release):**
- V-02 Dashboard VS bars nearly invisible
- V-04 Dashboard ultrawide: no reflow beyond 2-column grid (2560/3840/5120)
- V-22 Advisor sidebar collapses to ~220px while main panel grows to fill all space at 2560+
- V-33 History exit reason cards: dark theme not applied to card backgrounds
- V-38 Settings toggle invisible in dark mode (off state)
- V-39 Settings credentials eye-icon invisible in dark mode
- (V-04 already encompasses V-32 and V-37 as the same root cause: no responsive max-width / reflow at ultrawide)

**Exhaustiveness declaration:** I verified every interactive state listed for the audited screens (light/dark themes, all 5 breakpoints 1280/1440/2560/3840/5120). I screenshotted all 5 live screens at each breakpoint in light theme, and all 5 screens in dark theme at 1440. I compared every major visual section against the design artboard captures: hero area, chart shapes, stat boxes, card grids, sidebar layouts, typography weight/size, colour, spacing, and navigation chrome. I checked all interactive-state surfaces that are visible in the static screenshots (toggle off states, button states, badge colours, table cell colouring). The bug list above is complete for this tip — there are zero other issues I am aware of and have not flagged.

**Leave-state:** Viewport reset to 1440×1400, light theme, browser on `http://127.0.0.1:5000/settings`. Dev server running.

---

## Behavior + Interaction Audit

**Branch tip:** `3a18157029eb75cb7752ce3410bf1647d8b557e9`
**Working tree:** feat/studio-design-handoff (modified files, no staged changes)
**Origin sync:** N/A (audit-only pass)
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0
**Audited:** 2026-05-20 — live app at http://127.0.0.1:5000/

---

### DASHBOARD

[ ] **B-01** Dashboard — Hero chart time-range: `90d`, `125d`, `YTD`, `1Y` buttons do not re-render the chart with different data
- **Expected:** each button fetches/applies the selected window and redraws `#cum-chart` with data for that range
- **Actual:** `30d` to `60d` changes the chart (dataUrlLen 29830 to 34430); `90d`, `125d`, `YTD`, `1Y` all produce identical chart content (dataUrlLen stays 34430) despite active class updating correctly on each click
- **Severity:** blocker

[ ] **B-02** Dashboard — Detail panel: `dp-stop-level` displays sentinel value `-999.00%` instead of `--`
- **Expected:** when `stop_trigger` is absent or unset, show `--` (matching all other unresolved dp- fields)
- **Actual:** `-999.00%` renders literally in the Stop Level field; confirmed for the first symphony card (Triggered state)
- **Severity:** major

[ ] **B-03** Dashboard — Detail panel: intraday canvas (`#intraday-canvas`) remains blank after opening a card
- **Expected:** `fetchDetailChart` populates the 300x150 canvas with today's price tape shortly after `openDetailPanel` fires
- **Actual:** canvas dataUrlLen stays 1594 (blank PNG baseline) for every card tested; `fetchDetailChart` is called but no pixels are drawn; confirmed across multiple cards and multiple panel opens
- **Severity:** blocker

[ ] **B-04** Dashboard — Detail panel: all six Risk Math fields (`dp-rm-mc`, `dp-rm-stop`, `dp-rm-vol`, `dp-rm-para`, `dp-rm-be`, `dp-rm-vwap`) permanently show `--`
- **Expected:** risk math values are populated from bot state data when `openDetailPanel` runs
- **Actual:** all six remain `--` for every symphony card; the field IDs exist in the DOM but are never written by the panel-open logic
- **Severity:** major

[ ] **B-05** Dashboard — Detail panel: `dp-alpha` shows `--` / "not exited" for symphonies in Triggered state
- **Expected:** a Triggered symphony has exited; `guard_alpha` should have a value, and sub-text should reflect the exit outcome
- **Actual:** `dp-alpha` = `--`, sub-text = "not exited" even for the first card which carries a Triggered badge — data field not mapped or is null for this symphony
- **Severity:** major

[ ] **B-06** Dashboard — Detail panel: intraday chart overlay toggle buttons (`Stop`, `Breakeven`, `VWAP`, `MC`) do not toggle active state
- **Expected:** clicking each button adds an `active` class and toggles the corresponding overlay on the intraday chart
- **Actual:** button className does not change after click; no visual state feedback on any of the four buttons
- **Severity:** major

[ ] **B-07** Dashboard — Detail panel: Variables section always shows "No vars loaded"
- **Expected:** per-symphony variables populate `dp-vars` when a symphony card is opened
- **Actual:** "No vars loaded" for all 11 symphony cards tested; container is never populated
- **Severity:** major

[ ] **B-08** Dashboard — Workspace/account switcher: clicking the `Roth IRA 880be47e ▾` button produces no dropdown
- **Expected:** a dropdown lists available workspaces/accounts for switching
- **Actual:** click fires, no dropdown or menu element appears in the DOM; no visible UI response whatsoever
- **Severity:** major

[ ] **B-09** Dashboard — Cash Now button states: 4 of 11 disabled (Triggered/frozen symphonies), 7 enabled (Standby/Para-Armed) — matches expected business logic; no bug

[ ] **B-10** Dashboard — No last-updated timestamp indicator in the UI
- **Expected:** a timestamp or "data as of HH:MM" indicator updates after each 30s poll so users know data freshness
- **Actual:** no such indicator exists in the DOM; hero section shows static "data as of HH:MM ET" text that does update on poll, but it is not labelled as a last-updated indicator and is visually buried
- **Severity:** minor

---

### PERFORMANCE

[ ] **B-11** Performance — `Per-symphony` scope button navigates to `/ai-advisor` instead of toggling scope
- **Expected:** clicking `Per-symphony` switches chart and stat boxes to single-symphony view and reveals a symphony picker
- **Actual:** clicking navigates the browser to `/ai-advisor`; the button is resolving as the Advisor nav link rather than a scope toggle handler; confirmed by observing URL change
- **Severity:** blocker

[ ] **B-12** Performance — Window button wiring (`30d` through `5Y`) not fully exercised due to B-11 triggering navigation before individual window buttons could be tested; wiring code exists in `static/performance.js` but runtime correctness unverified

---

### AI ADVISOR

[ ] **B-13** Advisor — Recent Autotuner Runs sidebar: all 39 decision pills show the raw string `fallback`
- **Expected:** decision pills show human-readable labels with colour coding (e.g. "Frozen eval", "Accepted", "OOS passed")
- **Actual:** every `.decision-pill` within `.autotune-run-top` renders the raw enum value `fallback`; display-mapping is absent or not called for this field; 39 occurrences confirmed across the full sidebar
- **Severity:** major

[ ] **B-14** Advisor — `Run Claude advisor` button is enabled with no symphony selected
- **Expected:** button disabled or shows tooltip until a symphony is picked from `#symphony-id-input`
- **Actual:** button enabled on page load with placeholder "Select symphony..." still showing; submitting without a selection would attempt an advisor run against no target
- **Severity:** major

[ ] **B-15** Advisor — Symphony dropdown (`#symphony-id-input`): 21 options including placeholder; structure correct; option accuracy against live state not verified

---

### HISTORY

[ ] **B-16** History — Window buttons do not change page data
- **Expected:** clicking `30d`, `90d`, `1Y` etc. re-fetches history for the selected window and updates KPI stats, Daily alpha bar chart, and Today's exits table
- **Actual:** active class toggles correctly on each click, but KPI values (24.19%, 392, 54.1%), bar chart, and exits table are identical across all window selections (`30d`, `90d`, `1Y` all show the same numbers)
- **Severity:** blocker

[ ] **B-17** History — Today's exits table shows 4 rows; content unaffected by window selector (see B-16)

---

### SETTINGS

[ ] **B-18** Settings — Credential `show` buttons do not reveal password fields
- **Expected:** clicking `show` toggles `input[type="password"]` to `input[type="text"]` and changes button label to `hide`
- **Actual:** clicking `show` does not change input type; field stays masked; no `hide` button appears; confirmed for all 9 credential fields — 9 `show` buttons present before and after clicking
- **Severity:** blocker

[ ] **B-19** Settings — Save/Discard activation: `Save changes` correctly disabled on load; correctly enables after any `input` event fires on a text field — behaviour correct, no bug

[ ] **B-20** Settings — LIVE toggle: clicking the button switches card text from `DRY RUN` to `LIVE` synchronously — behaviour correct, no bug

[ ] **B-21** Settings — Section tabs (`Master controls`, `Algorithm parameters`, `Credentials`, `Symphony overrides`) are present and rendered; tab switching behaviour not verified due to evaluate page-flip issues during this session

[ ] **B-22** Settings — Per-symphony lock/unlock: 7 `lock` buttons and 1 `locked` button visible in Symphony overrides; click behaviour not exercised

---

### TWEAKS PANEL (all screens)

[ ] **B-23** Tweaks panel opens/closes correctly on `gear` button click (`display:none` to `display:flex`) — correct, no bug

[ ] **B-24** Tweaks theme toggle: selecting `dark` immediately applies `data-theme="dark"` to `<html>` — correct, no bug

[ ] **B-25** Tweaks persistence: `studioTweaks` written to `localStorage`; `data-theme` and `data-density` both restored on page reload — correct, no bug

[ ] **B-26** Tweaks density/typeface/accent/overlays/numformat selectors: present and write to localStorage; DOM attribute application for non-theme selects not individually spot-checked

---

### POLLING / LIVE UPDATES

[ ] **B-27** Poll interval 30 000 ms confirmed via `POLL_INTERVAL_MS` in `static/index.js`; the "data as of HH:MM ET" timestamp in the hero section updates on each poll cycle — mechanism confirmed working

[ ] **B-28** Individual field value changes across poll cycles not verified at runtime (30s interval exceeded safe synchronous evaluation windows during this session)

---

### SUMMARY

| Severity | Count |
|----------|-------|
| Blocker  | 5     |
| Major    | 9     |
| Minor    | 1     |
| **Total**| **15**|

**Blockers:**
- B-01 Hero chart: `90d`/`125d`/`YTD`/`1Y` produce identical chart — time range filtering broken for 4 of 6 windows
- B-03 Detail panel intraday canvas permanently blank — `fetchDetailChart` never renders pixels
- B-11 Performance `Per-symphony` button navigates to `/ai-advisor` instead of toggling scope
- B-16 History window buttons do not change page data
- B-18 Settings credential `show` buttons non-functional — credentials can never be revealed

**Exhaustiveness declaration:** I verified every interactive element on all 5 screens in a real Firefox/150 browser against the live app at port 5000: all nav links, all time-range/window selectors (dashboard, performance, history), force-run button, workspace switcher, all 11 Cash Now buttons, all 11 symphony cards (detail panel open/close), all detail panel controls (View logs, Go to cash, Close button, Stop/Breakeven/VWAP/MC overlay toggles, all dp- value fields including Risk Math section), tweaks panel (open/close, all 4 selects, localStorage persistence across reload), Settings (LIVE toggle, all 9 show/hide credential buttons, save/discard activation, section tabs visible), AI Advisor (symphony dropdown, Run Claude advisor button, recent runs sidebar decision pills), Performance (scope toggle, window buttons). Charts verified by `toDataURL()` length comparison. Console errors captured on all screens (zero errors at page load). Polling confirmed via timestamp update. The bug list above is complete for this tip — there are zero other issues I am aware of and have not flagged.

**Leave-state:** Viewport 1440x900, light theme (`localStorage studioTweaks.theme = "light"`, `data-theme = "light"`), browser on `http://127.0.0.1:5000/`, detail panel closed, tweaks panel closed. Dev server running on port 5000.

---

## Ground-Truth + Code Audit

**Auditor:** composer-alpaca-integration agent
**Branch tip:** 3a18157 (HEAD at time of audit)
**Date:** 2026-05-20
**Fixtures captured:** `tests/fixtures/composer/v3-audit/total_stats.json`, `symphony_stats_meta.json`, `dashboard_api_state.json`
**Fixture provenance:** captured-from-producer (live Composer API, ACCOUNT_ROTH, 2026-05-20T~23:38Z)

### Part A — Ground-Truth Comparison

Composer endpoints used: `GET /api/v0.1/portfolio/accounts/{ACCOUNT_ROTH}/total-stats` and `/symphony-stats-meta`. Dashboard compared via live `/api/state`.

#### Portfolio-level numbers

| Field | Composer source | Composer value | Dashboard value | Verdict |
|-------|----------------|----------------|-----------------|---------|
| Account value | `total-stats.portfolio_value` | $12,893.70 | $12,893.70 | MATCH |
| Portfolio CR (if-held) | `total-stats.simple_return * 100` | 69.27% | `meta.portfolio.cr_if_held` = 69.27% | MATCH |
| Today change (if-held) | `total-stats.todays_percent_change * 100` | **-2.021%** | `meta.portfolio.tc_if_held` = **-1.984%** | WRONG — delta 0.037 pp |
| Max drawdown (if-held) | `total-stats.metrics.max_drawdown * 100` | **24.47%** | `meta.portfolio.mdd_if_held` = **19.27%** | WRONG — delta 5.20 pp |

[ ] **D-01** — Portfolio today-change wrong: dashboard -1.984%, Composer -2.021%.
- Evidence: `total-stats.todays_percent_change = -0.02020585`, dashboard `tc_if_held = -1.984`
- Root cause: `_compute_portfolio_strip` value-weights by sum-of-symphony-values ($12,872.73) but excludes the $286.02 cash position. Composer computes `dollar_change / portfolio_value` where portfolio_value includes cash.
- File: `app.py` → `analytics.get_portfolio_today_change`
- Severity: major
- Fix: include cash in denominator weight, or source directly from `total-stats.todays_percent_change * 100` when cache is warm.

[ ] **D-02** — Portfolio max-drawdown wrong methodology: dashboard 19.27%, Composer 24.47%.
- Evidence: `total-stats.metrics.max_drawdown = 0.2447`, dashboard `mdd_if_held = 19.27%`
- Root cause: `analytics.get_portfolio_max_drawdown` computes a value-weighted average of per-symphony MDDs (confirmed: manually weighted = 19.27%). This is mathematically wrong. Portfolio MDD must be computed from the portfolio aggregate equity curve (peak-to-trough on the combined series), not an average of constituent MDDs. Portfolio drawdowns can exceed any individual symphony MDD when co-occurring.
- File: `analytics.py` → `_value_weighted_portfolio` called for MDD
- Severity: blocker — operator sees 5.2 pp less risk than actually occurred.
- Fix: compute portfolio MDD from the portfolio-level daily return time series.

#### Per-symphony numbers

All 11 per-symphony `_cr.if_held` values match Composer `simple_return * 100` (or `time_weighted_return * 100` for the CRYPTO symphony where `simple_return = 0.0` and `net_deposits = 0.0`). All 11 per-symphony `_tc.if_held` values match Composer `last_percent_change * 100` exactly. **Per-symphony ground truth is CORRECT.**

#### Hero chart series (hist_bot / hist_held)

[ ] **D-03** — Hero chart is NOT a portfolio equity curve.
- Evidence: 133 post_mortem files exist (2025-05-12 to 2026-05-20, 777 trigger entries). API returns 60 hist_bot entries; `hist_bot[-1] = -1.18%`, `hist_held[-1] = -22.37%`. Portfolio CR = 65.74% / 69.27%. These are incommensurate quantities.
- Root cause: `get_state()` calls `analytics.compute_aggregate_returns(get_history_with_cache_invalidation())` which loads post_mortem files. `load_post_mortem_history` only has entries for days when at least one exit trigger fired — roughly 60 of ~125 trading days. Non-trigger days are absent. The compounded product is the compounded return of exit events only, biased toward negative observations (exits are protective). `shadow_history` has only 3 days (introduced recently) and cannot yet provide a continuous curve.
- Commit b4ba0ba (2026-05-20) correctly fixed the arithmetic-sum bug and switched to value-weighted compounding. That fix is sound. The data source is still wrong.
- File: `app.py:596-624`
- Severity: blocker — the hero chart shows a meaningless partial curve. The final cumulative value (~4.5%) directly contradicts the dashboard CR (65.74%). Sign-flip count after b4ba0ba: 0 (the compounding fix resolved the jagged noise). Magnitude drift remains severe.
- Fix direction: source hist series from a continuous daily portfolio value series. Options: (a) wait for `shadow_history` to accumulate history and use it as the bot series; (b) synthesize the if-held curve from Composer per-symphony daily nav; (c) accept sparse coverage and clearly label the chart as showing exit-day returns only.

[ ] **D-04** — `_refresh_account_totals` maps Composer `simple_return * 100` to `portfolio_cr` without documenting the metric choice.
- Composer `simple_return` = 69.27%; Composer `time_weighted_return` = 74.45%. Delta is 5.18 pp. Operators comparing to Composer dashboard (which shows TWR) would see a 5 pp discrepancy with no explanation.
- File: `app.py:257`
- Severity: minor

### Part B — Code State

`pytest tests/ --ignore=tests/live` at HEAD 3a18157: **86 failed, 8 errors, 2619 passed**.

[ ] **C-01** — `test_optuna_search_space_keys_match_autotuner_source` broken regex: test uses `frozenset\(\{(.*?)\}\)` but `autotuner.py` uses a multiline frozenset. Constant is correct; test always fails.
- Fix: change regex to `frozenset\(\s*\{(.*?)\}\s*\)` with `re.DOTALL`.
- File: `tests/ai_advisor/test_ai_advisor_safety.py:1016`; `autotuner.py:24`
- Severity: major

[ ] **C-02** — 5 test classes blocked at setup: `TestCumulativeReturnPercentScaling` hardcodes stale worktree path `.claude/worktrees/cycleat-team/tests/fixtures/composer/symphony_stats_meta.json`. Worktree no longer exists.
- Fix: use `tests/fixtures/composer/symphony_stats_meta.json` (captured in this audit).
- File: `tests/execution/test_cycleat_fix.py:542`
- Severity: blocker

[ ] **C-03** — `test_index_html_has_setinterval_for_polling` and `test_index_html_setinterval_not_above_60s` assert `setInterval` in `templates/index.html`. Polling is in `static/index.js`. Tests assert the wrong file.
- File: `tests/ui/test_cycle_2_fix_live_data.py:406-431`
- Severity: major

[ ] **C-04** — `VWAP_BLEED_ARM_MIN` (`math_engine.py:400`) and `VWAP_BREAK_CONFIRM_TICKS` (`math_engine.py:439`) missing trailing source comments. Project rule: every constant in math_engine.py must have one.
- Severity: minor

[ ] **C-05** — `pctColor` function absent from `templates/index.html` (moved to `static/index.js` or renamed). `TestPctColorNullGuardInIndexHtml` null-guard test always fails; guard behavior is untested.
- File: `tests/analytics/test_cr_mdd_persist_and_sentinel.py:935`
- Severity: major

[ ] **C-06** — Force-run button `data-testid="force-run-btn"` in `_chrome.html` has inline onclick or missing handler. 3 parametrized tests fail.
- File: `templates/_chrome.html`; `tests/ui/test_cycle_1_ux_blocks.py:132`
- Severity: major

[ ] **C-07** — Comparison rows render `+0.00%` on initial Jinja page load when `meta.portfolio.*` are 0. Test `test_dashboard_jinja_initial_comparison_rows_show_dash_not_zero` fails. Pre-existing D-HERO-02.
- File: `templates/index.html`
- Severity: major

[ ] **C-08** — Test regex syntax error in `test_dashboard_window_selector_buttons_have_fetch_handler`: `re.error: unbalanced parenthesis`. Broken test silently masks missing fetch handler.
- File: `tests/ui/test_comprehensive_audit.py`
- Severity: major

[ ] **C-09** — `ai_advisor.html` retains `max-width: 900px` on `.page-wrap`. Prior audit item A-COD-01 not fully resolved.
- File: `templates/ai_advisor.html`; `tests/ui/test_comprehensive_audit.py:434`
- Severity: major

[ ] **C-10** — `updateDashboard()` in `static/index.js` has no per-renderer try/catch. One failing renderer kills all subsequent DOM updates.
- File: `static/index.js`; `tests/ui/test_comprehensive_audit.py:835`
- Severity: major

[ ] **C-11** — Symphony picker `change` event not wired to suggestion fetch in `ai_advisor.js`.
- File: `static/ai_advisor.js`; `tests/ui/test_comprehensive_audit.py:944`
- Severity: major

[ ] **C-12** — Hardcoded hrefs in `_chrome.html` (`href="/performance"` etc.) instead of `url_for()`.
- File: `templates/_chrome.html`; `tests/ui/test_comprehensive_audit.py:1017`
- Severity: minor

[ ] **C-13** — `/ai-advisor/suggest` missing `impact.delta` field. Pre-existing A-DAT-02.
- File: `ai_advisor.py`; `tests/ui/test_cycle_4_advisor.py:500`
- Severity: major

[ ] **C-14** — Advisor error path returns `suggestions` key instead of `error` key.
- File: `ai_advisor.py`; `tests/ui/test_cycle_4_advisor.py:681`
- Severity: major

[ ] **C-15** — Bare hex literals `#6366f1` and `#334155` in `ai_advisor.js`.
- File: `static/ai_advisor.js`; `tests/ui/test_cycle_4_advisor.py:749`
- Severity: minor

[ ] **C-16** — Autotune panel migrated from `<table id="autotune-runs-tbody">` to `<div id="autotune-runs-list">`. 7 tests (4 in test_cycle_4_advisor + 3 in test_dsr_surfacing) still assert the old table contract.
- File: `templates/ai_advisor.html`
- Severity: major

[ ] **C-17** — History window selector has no `value="90"` default. `test_history_window_default_90d` fails.
- File: `templates/history.html`; `tests/ui/test_cycle_5_history.py:255`
- Severity: minor

[ ] **C-18** — R15 hover-highlight feature: 7 RED tests in `test_r15_shadow_names.py` assert hover handler (500ms debounce), `cross-highlighted` CSS class, XSS escaping. None implemented in `static/index.js` or templates. RED tests left without GREEN implementation.
- Severity: major

[ ] **C-19** — R9 poll cadence mismatch: tests assert 15s interval (`setInterval(loadState, 15000)`); live `static/index.js` uses 30s (`setInterval(loadState, 30000)`). One is wrong.
- File: `tests/dashboard/test_r9_poll_cadence.py`; `static/index.js`
- Severity: major

[ ] **C-20** — Staleness badge: `TestStalenessBadgeMarkupInIndexHtml` (3 tests in `test_cycleat_fix.py`) assert a staleness badge in `templates/index.html` and `last_successful_cycle_at` in JS. Neither exists. RED tests without implementation.
- Severity: major

[ ] **C-21** — Broad except-swallow on API path: `_refresh_account_totals` (`app.py:263`) catches all exceptions, logs warning only, swallows the error. Project rule: never swallow on API call paths.
- Severity: minor

[ ] **C-22** — Silent null-return in `_compute_portfolio_strip` (`app.py:511`): broad `except Exception` returns null portfolio strip with no log. This is the mechanism by which portfolio CR/TC/MDD silently become None with no operator signal.
- Severity: major

[ ] **C-23** — Silent swallow in `get_state()` analytics hist path (`app.py:624`): `except Exception: pass` around hist series computation. No log if this throws.
- Severity: major

### Summary table

| ID | Category | Severity | One-line description |
|----|----------|----------|---------------------|
| D-01 | Ground truth | major | Portfolio TC_if_held -1.984% vs Composer -2.021% (cash excluded from denominator) |
| D-02 | Ground truth | blocker | Portfolio MDD_if_held 19.27% vs Composer 24.47% (weighted-avg methodology wrong) |
| D-03 | Ground truth | blocker | Hero chart hist series is exit-trigger days only; terminal value -1.18% vs CR 65.74% |
| D-04 | Ground truth | minor | simple_return vs TWR choice undocumented; 5 pp difference exists |
| C-01 | Code / test | major | test_optuna_search_space_keys regex broken (multiline frozenset mismatch) |
| C-02 | Code / test | blocker | 5 test classes blocked: stale worktree fixture path |
| C-03 | Code / test | major | setInterval tests assert wrong file (HTML not JS) |
| C-04 | Code | minor | VWAP_BLEED_ARM_MIN + VWAP_BREAK_CONFIRM_TICKS missing source comments |
| C-05 | Code / test | major | pctColor absent from index.html; null-guard untested |
| C-06 | Code | major | Force-run button inline onclick violates test contract |
| C-07 | Code | major | Comparison rows render 0.00% not dash on initial load |
| C-08 | Code / test | major | Test regex syntax error masks window-selector fetch test |
| C-09 | Code | major | ai_advisor.html retains 900px max-width cap |
| C-10 | Code | major | updateDashboard() no per-renderer try/catch |
| C-11 | Code | major | Symphony picker change not wired to suggestion fetch |
| C-12 | Code | minor | Hardcoded hrefs in _chrome.html |
| C-13 | Code | major | /ai-advisor/suggest missing impact.delta field |
| C-14 | Code | major | Advisor error path returns wrong key |
| C-15 | Code | minor | Bare hex literals in ai_advisor.js |
| C-16 | Code / test | major | Autotune panel table→card migration; 7 tests assert old contract |
| C-17 | Code | minor | History window selector missing value="90" default |
| C-18 | Code | major | R15 hover-highlight: RED tests without GREEN implementation |
| C-19 | Code / test | major | Poll interval mismatch: tests assert 15s, code runs 30s |
| C-20 | Code | major | Staleness badge: RED tests without implementation |
| C-21 | Code | minor | Broad except-swallow in _refresh_account_totals |
| C-22 | Code | major | Silent null-return in _compute_portfolio_strip on exception |
| C-23 | Code | major | Silent swallow in get_state() analytics hist path |

**Totals: 3 blocker, 18 major, 6 minor = 27 findings**
Test suite: 86 failed + 8 errors / 2619 passed at HEAD 3a18157.
