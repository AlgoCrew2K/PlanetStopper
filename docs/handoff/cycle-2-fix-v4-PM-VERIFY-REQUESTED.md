a0ce875

ux-expert: APPROVED @ docs/handoff/cycle-2-fix-v4-REVIEW-DONE.md
quant-code-reviewer: APPROVED @ 66f7434

Routes to verify in browser (network tab open):
- GET / (dashboard) — polling /api/state every 30s, guard-alpha hero updates from portfolio_strip.cumulative_return, triggered-card verdict shows guard_alpha from bot_state
- GET /performance — fetches /api/performance on load + window/scope change
- GET /ai-advisor — POST /ai-advisor/suggest on symphony select, autotune runs from /api/autotune-runs
- GET /history — fetches /api/history/<days> on load + window change

Full UI suite (excluding cycle-6): 409/409 PASS
