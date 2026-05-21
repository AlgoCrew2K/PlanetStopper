f5fc010

ux-expert: APPROVED @ docs/handoff/cycle-2-fix-v3-REVIEW-DONE.md
quant-code-reviewer: APPROVED @ 4052402

Routes to verify in browser (network tab open):
- GET / (dashboard) — polling /api/state every 30s, charts load from /api/chart/<sym>
- GET /performance — fetches /api/performance on load + window/scope change
- GET /ai-advisor — POST /ai-advisor/suggest on symphony select, runs panel from /api/autotune-runs
- GET /history — fetches /api/history/<days> on load + window change

Chart elements now present in live DOM (not just static scaffolding):
- /performance: renderMetrics emits data-testid="metric-bar" SVG per row on first fetch
- /history: renderReasonCards emits reason-bar SVG + reason-description + avg-per-exit per card
- /ai-advisor: renderSuggestions emits confidence-ring SVG arc, projected-impact-bar, 4x gate-badge per card

Full UI suite (excluding cycle-6): 406/406 PASS
