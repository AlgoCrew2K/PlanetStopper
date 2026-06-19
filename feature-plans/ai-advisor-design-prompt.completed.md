# Claude Designs Prompt — AI Advisor Full Suite

---

Design an updated **AI Advisor** page for **Planet Stopper**, an institutional-grade algorithmic risk engine with a Flask server-rendered operator dashboard. The operator is NOT a quant — they need clarity over density. The dashboard is strictly **read-only: it is never an action surface for live trades.** There are zero apply/deploy/trade buttons anywhere in these screens.

---

## Design system to match exactly

The existing dashboard uses these tokens. Every color, border, and surface in the new screens must resolve from these variables — no hardcoded hex:

**Backgrounds**
- Page background: `--studio-bg` (#f4efe2 light / #13100c dark) — warm off-white / near-black parchment
- Surface/card: `--studio-surface` = `--studio-paper` (#faf6ea / #19150f)
- Raised surface: `--studio-surface-raised` = `--studio-paper-hi` (#ffffff / #1f1a13)

**Typography**
- Primary ink: `--studio-ink` (#15120c / #eee4ce)
- Dim text (labels, metadata): `--studio-ink-dim` (rgba 62% alpha over ink)
- Muted text (section headers, uppercase labels): `--studio-ink-muted` = `--studio-ink-dim`
- Font: Manrope (primary), fallback to system-ui. UI labels are ALL-CAPS, 0.625–0.6875rem, weight 700, letter-spacing 0.1em.

**Borders / Rules**
- `--studio-border` = `--studio-rule` (rgba 10% alpha)
- Stronger rule: `--studio-rule-hi` (rgba 22% alpha)

**Semantic colors**
- Positive/accent: `--studio-pos` / `--studio-accent` (#1f7a4d / #2ea566 dark) — forest green
- Negative: `--studio-neg` (#b43a2a / #e07061 dark) — muted red
- Warning: `--studio-warn` (#b07419 / #e3a64a dark) — amber
- Plum (secondary accent for AI/chat surfaces): `--studio-plum` (#6a3f8a / #b58cc8 dark)
- Cyan (diagnostic surfaces): `--studio-cyan` (#1a6e88 / #6fb6c8 dark)

**Component language (match these exactly)**
- Cards: `border-radius: 1rem`, `border: 1px solid var(--studio-border)`, `background: var(--studio-surface)`, `padding: 1.25–1.5rem`
- Header cards (page-level): top 3px gradient bar using `linear-gradient(to right, var(--studio-accent), var(--studio-ink-dim))` — this is the established page header accent stripe
- Section labels inside cards: ALL-CAPS, 0.6875rem, weight 700, letter-spacing 0.1em, `--studio-ink-dim`
- Pills / badges: `border-radius: 999px`, `padding: 0.125rem 0.5rem`, `font-size: 0.625rem`, weight 700, ALL-CAPS, `border: 1px solid`
- Buttons (primary action): `background: var(--studio-accent)`, `color: var(--studio-white)`, `border-radius: 0.5rem`, weight 700
- Inputs / selects: `background: var(--studio-bg)`, `border: 1px solid var(--studio-border)`, `border-radius: 0.5rem`, focus ring uses `--studio-accent`
- Verdicts use semantic color classes: CLEAR → `--studio-pos`; WATCH → `--studio-warn`; BREACH/REJECTED → `--studio-neg`; unknown → `--studio-ink-dim`
- The `advisory only` badge is a tiny pill: `border: 1px solid var(--studio-border)`, `color: var(--studio-ink-dim)`, 0.5rem font, ALL-CAPS — it appears on every recommendation surface
- Sparkline / chart areas use Chart.js-style inline canvas elements; no external charting library beyond what already exists in the dashboard

**Nav chrome** — include the existing top nav bar at the top of every screen:
- "Planet Stopper guard" wordmark left; nav links: Dashboard / Performance / Advisor (active) / History / Settings; right-rail: engine status dot, ET clock, NEXT countdown, workspace chip, mode pill (DRY RUN / LIVE), Force run now, Emergency Liquidate
- The "Advisor" link is active (underline accent bar below it)

**Two-theme requirement** — show both light and dark variants for at least the main AI Advisor page layout. The warm parchment palette in light; the dark parchment (#13100c background, #eee4ce ink) in dark.

---

## Page layout — `/ai-advisor`

The page extends the existing AI Advisor page. The current page has: a page header card, a controls bar, a summary strip of chips, and a two-column grid (suggestions left, autotune panel right). The new design EXTENDS this — do not replace the autotune panel rail.

### Page header card
Same structure as the existing header: title left ("AI Advisor"), subtitle ("Diagnose correlations, explore asset swaps and logic changes, and explain any recommendation — operator applies changes manually in Composer"), back link right. Top 3px gradient accent bar. No changes to this component.

### Capability tab bar
Directly below the header card, a tab strip selects between the four capabilities. Tabs sit flush in a single horizontal bar (not a card of their own — a lightweight segmented control consistent with the existing `window-selector` button group pattern):

- **Correlations** — a scatter/grid icon, risk tier indicator 🟢
- **Asset Swaps** — a swap/arrows icon, risk tier 🟢
- **Logic Changes** — a branch/logic icon, risk tier 🟡
- **Chat** — a chat-bubble icon, risk tier 🟡

The risk tier indicator (🟢 / 🟡) is a small colored dot, not an emoji — use `--studio-pos` for green tier and `--studio-warn` for yellow tier. Active tab uses `--studio-accent` underline. Inactive tabs use `--studio-ink-dim` text.

The tab bar is the only new chrome needed. Everything below changes per tab.

---

## Screen 1 — Correlations tab (Capability 1, 🟢)

This is a pure diagnostic — no backtest, no gate, no proposals. Layout:

**Controls bar** (reuse `.adv-controls` card pattern):
- "As of" date label (read-only, shows when correlation matrix was last computed)
- A "Refresh" button (same style as the existing Run Advisor button, `--studio-accent`)
- No symphony selector needed — this is fleet-wide

**Crisis caveat banner** — a full-width notice card directly above the matrix. Use `--studio-warn-tint` background, `--studio-warn-border` border, `--studio-warn` left 3px accent bar. Text: "Correlations destabilize toward 1.0 in market stress — when de-correlation matters most, these estimates are least reliable. Use as a guide, not a guarantee." This banner is always visible, not dismissible.

**Correlation matrix** — a pairwise grid across the operator's symphonies (rows and columns = symphony names). Design as a heat-map table:
- Cell background intensity: neutral (near `--studio-surface`) at 0 correlation → saturated at ±1. Positive correlation toward `--studio-neg-tint` background (high positive = danger = warm red); negative correlation toward `--studio-accent-tint` (de-correlated = good = green tint). The diagonal cells (self-correlation = 1.0) are a distinct neutral fill.
- Each cell shows the numeric value (e.g. "0.82") in tabular-nums, weight 600; high-correlation cells (>0.65) show the value in `--studio-neg` color; low/negative in `--studio-pos`.
- Below each numeric value, a tiny sample-basis indicator in `--studio-ink-dim`, 0.6rem: "n=87d" or "n=thin" when fewer than 30 observations. The "thin" label uses `--studio-warn` color.
- Row/column headers are symphony names, truncated at ~20 chars with a tooltip. Headers use `--studio-ink`, weight 600, 0.8125rem.

**Insufficient-data state** — when fewer than 2 symphonies have enough history: replace the matrix with the `.suggestions-placeholder` pattern (centered, `--studio-surface` card, `--studio-ink-dim` text): "Insufficient return history to compute correlations. At least 2 symphonies need 30+ days of overlapping history."

**Holdings sub-section** (below the symphony matrix, optional — shown only when holding-level data exists): a separate smaller matrix or a flat list of "Holding X ↔ Symphony Y: 0.71 (n=62d)" rows in a compact table. If no data, omit entirely. Use the same heat-map coloring.

---

## Screen 2 — Asset Swaps tab (Capability 2, 🟢)

Two sub-modes coexist on the same tab — a toggle or two stacked sections:

**Sub-mode toggle**: a small segmented control at the top of the content area: "Advisor suggestions" | "Try a swap". Active segment: `--studio-surface-raised` background with light box-shadow; inactive: transparent.

### Advisor suggestions mode (default)

**Controls bar**: Symphony selector (existing `.ctrl-select` pattern) + "Run" button. When no symphony selected or no run yet: the placeholder state.

**Proposal cards** — each survivor swap renders as a card in the fluid grid (`repeat(auto-fill, minmax(28rem, 1fr))`):

Card anatomy (top to bottom):

1. **Card header row**: Left: swap label ("IALT replaces GLD in Symphony Alpha"), weight 700, 0.9375rem. Right: `advisory only` badge (always present).

2. **Objective strip**: A narrow banner inside the card using `--studio-accent-tint` background, `--studio-accent-border` border, 3px left accent bar in `--studio-accent`. Text: "Objective: reduce 0.82 correlation between Symphony Alpha and Symphony Beta" in 0.8125rem weight 600. This is mandatory — every card must explain WHY this swap was suggested.

3. **Stats comparison table** — a 2-column inline table (no outer border, just row dividers using `--studio-border`):
   - Columns: Metric | Baseline | Variant
   - Rows: Sharpe ratio, Sortino ratio, Max drawdown, Annual return
   - Metric column: `--studio-ink-dim`, 0.75rem, ALL-CAPS weight 700
   - Baseline / Variant values: tabular-nums, 0.875rem. If variant improves the metric: variant value in `--studio-pos`; if worse: `--studio-neg`; unchanged: `--studio-ink`
   - Max drawdown rows: lower is better (invert the positive/negative coloring)

4. **Return-series mini chart** — a small inline sparkline (height ~56px, full card width) showing baseline return series (thin `--studio-ink-dim` line) vs variant return series (`--studio-accent` line). A legend at bottom-right: two colored dots + labels "Baseline" / "Variant", 0.625rem.

5. **Gate verdict row**: A full-width row with left-aligned gate result. Show as a pill + description:
   - SURVIVED: pill with `--studio-pos` border + text, label "GATE: PASSED". Description text in `--studio-ink-dim`: "Cleared BHY/Yekutieli FDR veto, NN1 spec-freeze, and one-directional brake."
   - WITHHELD (should not appear in surfaced cards but shown for rejected section): pill with `--studio-neg` border + text. Description: the specific failure reason.

6. **Honest caveat** — a note in `--studio-ink-dim`, 0.75rem italic, below the gate row: "Every backtest-and-select exposes the overfitting trap. The gate is resistance, not immunity. Apply judgment." Keep it visible — this is a risk tool.

7. **Apply guidance** — a final note row in a faint `--studio-chip-bg` band at card bottom: icon (hand/cursor, not a button) + text: "To apply this swap: open Symphony Alpha in Composer and replace GLD with IALT manually." This is text only — **no button, no link, no action affordance.**

**Rejected candidates section** — below the survivors grid, a collapsible section "Candidates that did not clear the gate (N)". Collapsed by default. When expanded, shows de-emphasized versions of the same card anatomy (opacity 0.55, `--studio-neg-tint` background tint on the gate row, rejection reason prominent).

**Empty state** ("no swap cleared the gate this run"): A centered placeholder card using `--studio-neg-tint` background, `--studio-neg-border` border, `--studio-neg` left bar. Icon (gate/shield). Text (two lines): "No asset swap cleared the gate this run." / "This is a valid outcome — the gate rejected all candidates to protect against overfitting." Do NOT make this look like an error.

### Try a swap mode

**Input form** inside a card (`.adv-controls` style):
- Three fields in a horizontal flex row:
  - "ETF to try" — a text input with autocomplete/search behavior (open to the full Composer-tradeable ETF universe — no allowlist). Placeholder: "e.g. IALT, BTAL, GLD". Label: ALL-CAPS "CANDIDATE ETF"
  - "Replace" — a select showing current holdings in the selected symphony. Label: ALL-CAPS "REPLACE HOLDING"
  - "In symphony" — a select for the symphony. Label: ALL-CAPS "SYMPHONY"
- "Run backtest" button (`--studio-accent`)
- Below inputs: a note in `--studio-ink-dim`, 0.75rem: "The advisor will backtest this exact swap and run it through the acceptance gate. Only survivors are surfaced as recommendations."

When the operator submits: show a loading state on the card area (skeleton cards with pulse animation). When complete: render the single result card (same anatomy as above) or the gate-rejected treatment.

**Backtest-failed state** (per-candidate): the card renders with a `--studio-warn-tint` background tint, `--studio-warn-border` border, and a warning pill "BACKTEST FAILED" in `--studio-warn`. The card shows what is known (objective, swap label) and a reason row: "Composer API returned: {reason}". Other candidates in the batch are unaffected.

---

## Screen 3 — Logic Changes tab (Capability 3, 🟡)

Structurally identical to the Asset Swaps tab (same sub-mode toggle, same card anatomy, same states) with these differences:

1. **Tab-level risk warning** — a persistent banner at the top of the content area (below the tab bar, above the sub-mode toggle). Uses `--studio-warn-tint` background, `--studio-warn-border` border, `--studio-warn` left 3px bar. Text: "Logic-change proposals carry the highest overfitting risk. A multiple-testing correction (FDR/Yekutieli) is applied across ALL backtested candidates — raising the number of candidates raises the bar each must clear. Survivors are sound-but-unprovable: a clean gate pass is not proof of a live edge." This is non-dismissible.

2. **Card caveat is stronger**: replace the asset-swap caveat with: "Selected on backtest across N candidates. FDR correction applied (N tested, K required to pass at adjusted threshold). Overfitting risk cannot be eliminated — only resisted."

3. **Operator-initiated input fields** differ: instead of ETF/holding/symphony fields, show: "Describe the logic change" (a textarea, max 3 lines, placeholder: "e.g. change the momentum lookback from 20 to 10 days in Symphony Alpha") + symphony selector + "Run" button. Below: the same note about gate-and-surface-only behavior.

4. **FDR correction metadata** on each card's gate verdict row: add a small data line below the verdict pill: "N=12 candidates tested · adjusted threshold α=0.0042 · this candidate p=0.0031 (PASSED)" in 0.6875rem tabular-nums `--studio-ink-dim`.

---

## Screen 4 — Chat tab (Capability 4, 🟡)

This is an EXPLAIN-ONLY surface. It is not a general chatbot. Chat is always anchored to a specific artifact (a proposal card, a correlation figure, a gate verdict). There is no free-floating global chat input on this tab.

**Tab content — default state (no artifact selected)**:

A centered illustration-card (same `.suggestions-placeholder` style): icon (chat bubble with an anchor/link symbol). Two lines of text: "Select a recommendation, diagnostic figure, or gate verdict to open a contextual explanation." / "Chat explains what the advisor found and why — it does not issue trade instructions or generate new recommendations." Below that, a subtle list of example prompts in `--studio-ink-dim` italic:
- "Why did the gate reject this swap?"
- "What does this correlation mean for my portfolio?"
- "Why is this logic change considered high overfitting risk?"

**Contextual chat panel — triggered by "Discuss this" on any card**:

When the operator clicks "Discuss this" on any proposal card or correlation cell, a right-side panel slides in (fixed-width ~420px, full viewport height, `z-index` layered above page content, `--studio-paper` background, `--studio-rule-hi` left border). This is not a modal — the main page stays scrollable behind it.

Panel anatomy:

1. **Panel header**: title "Explain: [artifact name]" (e.g. "Explain: IALT swap in Symphony Alpha"). Weight 700, 1rem. Right: a close (×) button. Below title: a breadcrumb row showing the artifact context: advisor role dot + label + symphony name in `--studio-ink-dim`.

2. **Artifact summary strip**: a compact read-only snapshot of the artifact being discussed — the objective, gate verdict pill, and key stat (one Sharpe ratio or one correlation value). Uses `--studio-chip-bg` background, `--studio-border` border, `border-radius: 0.75rem`, compact padding. This anchors the conversation — always visible.

3. **Explain-only notice bar**: a persistent narrow banner: icon (info) + text "This chat explains advisor findings. It cannot apply changes, place trades, or generate new recommendations." Color: `--studio-cyan` tint background, `--studio-cyan` border, `--studio-cyan` icon. 0.75rem.

4. **Message thread**: scrollable area. AI messages in `--studio-surface` bubbles, left-aligned; operator messages in `--studio-accent-tint` bubbles with `--studio-accent-border` border, right-aligned. Each message: `border-radius: 0.75rem`, `padding: 0.75rem 1rem`, `font-size: 0.875rem`. AI message header: small "AI Advisor" label in `--studio-ink-dim` 0.625rem above the bubble. No avatar icons needed.

5. **Input row** at panel bottom: a text input (`--studio-bg` background, `--studio-border` border, `border-radius: 0.5rem`) + a send button (`--studio-accent` filled). Placeholder: "Ask about this recommendation…". Input is disabled while a response is in-flight (send button shows a spinner). **No "apply", "accept", or "deploy" button anywhere in this panel.**

6. **Chat-unavailable state**: when no LLM key is configured, the message thread area is replaced by a centered notice: "Chat unavailable: Anthropic API key not configured. Set ANTHROPIC_API_KEY to enable explanations." Uses `--studio-warn-tint`, `--studio-warn-border`, `--studio-warn` text. The input row is hidden.

**"Discuss this" affordance on proposal cards**: a subtle text-link at card bottom-left, `--studio-ink-dim` color, 0.75rem, underline on hover: "Discuss this". Clicking it opens the side panel scoped to that card. It is visually secondary to everything else on the card — intentionally de-emphasized.

---

## Screen 5 — Advisor unavailable state (no Composer API key)

When the Composer API key is not configured, the entire page content area (below the tab bar) is replaced by a single centered card:

- `--studio-warn-tint` background, `--studio-warn-border` border, 3px left bar in `--studio-warn`
- Icon (key/lock)
- Title: "Advisor unavailable" in weight 700, 1.25rem, `--studio-warn`
- Body: "The Composer API key is not configured. The AI Advisor cannot fetch symphony trees or run backtests without it." in `--studio-ink-dim`, 0.875rem
- Link: "Configure in Settings →" (plain text link, `--studio-accent`, no button)
- The tab bar is visible but all tabs show this state. The Correlations tab may be partially functional if return data is already cached — in that case, the correlations tab shows the matrix but with a caveat banner noting asset-swap and logic-change features require the key.

---

## Loading states

All loading states use skeleton cards — the same card shell (border-radius, border, padding) with `--studio-chip-bg` filled blocks in place of text content, using a CSS pulse animation (`opacity: 0.4 → 0.8 → 0.4`, 1.4s ease-in-out infinite). No spinners on top of content. The controls bar "Run" button shows a disabled state with label "Running…" while a batch is in-flight.

---

## Responsive behavior

The page uses the same `.page-wrap` container as all other pages (max-width ~1440px, `clamp` padding). The two-column grid (content + autotune rail) collapses to single column below 1100px. The correlation matrix scrolls horizontally on narrow viewports (overflow-x: auto with horizontal scroll). The chat side panel overlays at full-width on viewports below 768px (covers the main content). The proposal card grid (`auto-fill minmax(28rem, 1fr)`) naturally reflows to 1 column below ~900px.

---

## What NOT to include

- No apply, deploy, accept, or trade buttons anywhere on any screen
- No confirmation dialogs for applying recommendations — there is nothing to apply from this UI
- No progress bars implying a trade is being placed
- No success states implying a change was made to a live symphony
- No "undo" controls — again, nothing is being done
- The chat panel has no action buttons beyond "send a message" and "close panel"
- The existing autotune panel on the right rail is preserved and unchanged — do not remove it

---

## Deliverable

Produce a set of high-fidelity screen designs for the AI Advisor page in the Planet Stopper dashboard covering:

1. The full page at desktop width (~1440px) in light theme — Correlations tab active, matrix populated
2. The full page at desktop width in dark theme — Asset Swaps tab active, two survivor cards visible
3. The Asset Swaps tab — "Try a swap" sub-mode, operator input form
4. The Logic Changes tab — warning banner + one survivor card with FDR metadata visible
5. The Chat tab — side panel open, artifact summary strip visible, one exchange in thread
6. The Advisor unavailable state
7. The empty state (no survivors) for Asset Swaps

Designs must be consistent with the existing dashboard visual language described above. Every color resolves from the design token namespace described. The advisory-only boundary is visually clear on every screen.
