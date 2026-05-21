a0ce875

Routes verified by tw (409/409 PASS):
- All 3 Dashboard BLOCKs resolved across 2 commits (24c4afc + a0ce875)

Delta since v3 review (f5fc010 → a0ce875):
- static/index.js renderGuardAlpha: reads portfolio_strip.cumulative_return.{dry_run,if_held} (not meta.portfolio.cr)
- app.py /api/state: injects guard_alpha into triggered bot_state entries via database.get_guard_alpha_by_symphony()
- database.py: new get_guard_alpha_by_symphony() read-only query on exit_triggers table
