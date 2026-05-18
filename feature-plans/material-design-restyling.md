# Feature: Material Design Restyling
Status: ready (queued behind portmode-dev merge)
Created: 2026-05-18

## Summary

Restyle the AlphaBot v3 dashboard to adopt the Material Design visual language — palette, typography, elevation, spacing, component patterns — WITHOUT changing the stack. The dashboard stays on Flask + Jinja + Tailwind; we use Tailwind utility classes to recreate Material's design tokens. No React, no MUI components shipped to production. Operator's complaint: current Tailwind defaults look like every other AI-built page; Material Design gives it a distinctive operator-grade look.

The MUI MCP (configured operator-side) is consumed at design-time by the asset-sourcer to extract canonical Material tokens (colors, typography, elevation values, spacing rhythm, component patterns). Output is a tokens reference document; the Trio then Tailwind-ifies those tokens across every dashboard surface.

## Acceptance Criteria

### MD.1 — Design tokens reference doc
- **AC-MD.1.1**: asset-sourcer produces `docs/design/material-tokens.md` extracting from MUI MCP / public docs: Material 3 (or Material 2 — operator pick) color palette (primary, secondary, surface, error, warning, success, info), typography scale (display / headline / title / body / label, with weight + line-height), elevation system (0/1/2/3/4/6/8/12/16/24 dp), spacing scale (8dp grid), border-radius standards, motion/transition timings.
- **AC-MD.1.2**: doc maps each token to a Tailwind utility class OR a CSS custom property defined in `static/styles.css`. Single source of truth.
- **AC-MD.1.3**: dark-mode compatible palette if AlphaBot's current design is dark-mode (verify); else light-mode only.
- **AC-MD.1.4**: doc commits to main on a branch + PR for operator review BEFORE the Trio applies it.

### MD.2 — Palette swap
- **AC-MD.2.1**: All current `bg-slate-*`, `text-slate-*`, `bg-gray-*`, `text-gray-*`, `bg-amber-*`, `text-amber-*`, `bg-indigo-*`, `text-indigo-*`, etc. references audited + replaced with Material-token-mapped equivalents.
- **AC-MD.2.2**: Semantic colors (success/warning/error) consistently mapped — e.g., `text-red-600` (current) → `text-md-error-default`.
- **AC-MD.2.3**: NO raw color literals in templates (`#XXX`, `rgb(...)`) — all colors flow through the tokens defined in MD.1.

### MD.3 — Typography hierarchy
- **AC-MD.3.1**: Roboto font loaded (or Inter as a near-identical free alternative). Self-hosted in `static/fonts/` per MUI's recommendation (no Google Fonts CDN to preserve operator-LAN-only posture).
- **AC-MD.3.2**: Type scale applied across templates: headline-large for page titles, title-medium for section headers, body-medium for table cells, label-small for badges/chips.
- **AC-MD.3.3**: Font weights consistent (300 / 400 / 500 / 700 used per Material spec).
- **AC-MD.3.4**: Line heights + letter spacing per Material spec.

### MD.4 — Elevation system
- **AC-MD.4.1**: Cards / panels / modals receive proper Material elevation shadows. The current `border` + `bg-slate-800` look is replaced with `surface-level-1` (cards), `surface-level-2` (sticky banners), `surface-level-3` (modals), `surface-level-8` (popovers).
- **AC-MD.4.2**: Shadow tokens defined as CSS custom properties OR Tailwind plugin extensions in `tailwind.config.js`.

### MD.5 — Component patterns
For each existing component, produce a Material-flavored equivalent:
- **AC-MD.5.1 Button**: rounded (per Material 3), proper padding (per type scale), elevated for primary / outlined for secondary / text for tertiary. State variants (hover / active / disabled) per Material.
- **AC-MD.5.2 Chip / Pill**: rounded-full, proper density, semantic color variants. Used by R15 Shadow Perf banner, V3 fleet alert, OBSERVED-ONLY badge.
- **AC-MD.5.3 Card**: rounded-lg, surface-1 elevation, proper internal padding (16dp).
- **AC-MD.5.4 App Bar**: header section restyled as Material AppBar (top bar with title + status badges).
- **AC-MD.5.5 Modal / Dialog**: settings modal + MED-6 emergency-confirm dialog restyled per Material Dialog spec.
- **AC-MD.5.6 Banner / Snackbar**: DM market-state banner + V3 fleet banner as Material Banner (top, persistent, dismissible).
- **AC-MD.5.7 Table**: symphony status table restyled per Material Data Table — proper density (compact / default / comfortable; pick compact for the dense data display), row hover, divider tokens.

### MD.6 — Layout
- **AC-MD.6.1**: 8dp grid applied across layout — all margins, paddings, gaps snap to 8dp increments.
- **AC-MD.6.2**: Content max-width per Material breakpoints. Responsive behavior preserved.
- **AC-MD.6.3**: Spacing rhythm consistent — no ad-hoc `mt-3 mb-7` mixed values.

### MD.7 — Visual regression sweep
- **AC-MD.7.1**: ux-expert uses Playwright to capture before/after screenshots at 1920×1080 + 1280×720 + 768×1024 (responsive). Side-by-side comparison.
- **AC-MD.7.2**: NO functional regression — all data still renders, all interactions still work (hover-highlight, modal open/close, settings save, dismiss).
- **AC-MD.7.3**: Cross-reference against MUI docs — visual fidelity check against canonical Material component galleries.

### MD.8 — Accessibility
- **AC-MD.8.1**: All color combinations meet WCAG AA contrast (4.5:1 for body, 3:1 for large text).
- **AC-MD.8.2**: Focus states visible on all interactive elements.
- **AC-MD.8.3**: Existing hover behaviors (R15 cross-highlight) preserved.

## Architecture

| Surface | Files touched |
|---------|---------------|
| Design tokens reference | NEW `docs/design/material-tokens.md` |
| Token implementations | NEW `static/styles.css` OR extended `tailwind.config.js`; fonts under `static/fonts/` |
| Template restyling | `templates/index.html`, `templates/table_partial.html`, every other Jinja template |
| Component restyling | New macros under `templates/_components/` per component pattern (button, chip, card, modal) |
| JS adjustments | `static/*.js` — any JS that constructs class strings dynamically |
| Tests | `tests/dashboard/test_material_design.py` (structural assertions on token usage); Playwright e2e screenshot diff |
| ux-expert sweep | Visual regression via Playwright MCP |

**Team composition**: Quad
- `asset-sourcer` (lead for MD.1) — extracts tokens via MUI MCP / WebFetch
- `quant-test-writer` — structural RED tests (assert tokens consistently applied; no raw color literals)
- `flask-dashboard-specialist` — implements the restyle across all templates
- `ux-expert` — Playwright visual regression + cross-references MUI docs
- `quant-code-reviewer` — discipline gate

## Edge Cases

- **Operator runs the dashboard on multiple browsers (Chrome, Firefox, Edge)**: token implementations must be browser-compatible. Test in all three.
- **Operator's local font cache**: self-hosted Roboto should be cached after first load; no FOUC.
- **Dark mode vs light mode**: Material 3 supports both; AlphaBot is currently dark. Verify the Material dark palette renders correctly; don't introduce light mode unless operator explicitly wants it.
- **Existing JS that constructs class strings**: e.g., the R15 hover-highlight `.cross-highlighted` class. Token swap must preserve the JS contract.
- **MED-6 emergency button**: must stay visually high-contrast and DANGEROUS-looking even after restyle (Material Error color, not muted).

## Security Considerations

- No new external API surfaces.
- Self-hosted Roboto — no external font CDN; preserves operator-LAN-only posture per project CLAUDE.md.
- MUI MCP runs operator-side at design-time only; never reaches production.
- No XSS implications.

## Testing Strategy

- **Structural RED tests** in pytest: grep templates for forbidden patterns (raw color literals, ad-hoc spacing values, non-tokenized class strings). Assert all colors flow through tokens.
- **ux-expert Playwright sweep**: before/after screenshot comparison at 3 viewport widths. Visual diff acceptance criterion: must look distinctly Material (not generic Tailwind).
- **Cross-reference against MUI canonical**: pull a reference MUI dashboard, side-by-side with AlphaBot's restyle. Aesthetic match within tolerance.
- **Functional regression**: existing pytest suite must still pass (all 1900+ tests). Hover-highlight, modal open/close, settings save, settings dismiss — all still work.
- **Accessibility check**: ux-expert verifies WCAG AA contrast on every restyled surface.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Sequenced AFTER portmode-dev merges | portmode-dev touches the same templates (settings modal, dashboard table). Parallel restyle would conflict massively. |
| Tailwind tokens, NOT React+MUI | Operator's complaint is aesthetic (looks like every other Tailwind page), not architectural. Tailwind-ifying Material tokens preserves the existing Flask+Jinja stack with no rewrite cost. |
| Material 3 (not Material 2) | Newer spec; cleaner palette + typography; better dark-mode support. Operator confirms at MD.1 PR review. |
| Self-hosted fonts | Project's operator-LAN-only posture forbids CDN dependencies. |
| ux-expert visual sweep mandatory | Real-money operator surface — must look professional + distinctive. Playwright diff is the acceptance criterion. |
| Token PR review at MD.1 step | Operator approves the design language BEFORE the Trio applies it everywhere. Cheap to re-spec tokens; expensive to redo 50 templates. |

## Scope Boundaries

**IN:**
- All 8 AC groups above.
- Every visible Jinja template restyled.
- Tokens reference doc + Tailwind config + static/styles.css.
- Self-hosted Roboto.
- Playwright visual regression.

**OUT:**
- React migration (operator explicitly de-scoped).
- MUI components shipped to production (operator explicitly de-scoped).
- New features (the data + interactions stay the same).
- Backend changes (`/api/state` schema unchanged).
- Mobile-app layout (responsive within current breakpoints only).
- Theming engine (single Material 3 dark theme, no operator theme picker).

## Dependencies

- **portmode-dev cycle must merge first**. Touches the same templates; needs to land before this cycle starts.
- **MUI MCP enabled on operator's Claude Code** (operator action; config snippet in PM dispatch). asset-sourcer falls back to WebFetch against mui.com if MCP is unavailable.

## Hand-off

Plan saved. Sequenced AFTER portmode-dev merges. Operator approves dispatch when portmode-dev is in.
