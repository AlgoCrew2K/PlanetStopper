cycle-2-fix PM browser-verify requested

Branch: feat/studio-design-handoff
HEAD: 5c1559e (test) / impl GREEN at 29e05e6

ux-expert: APPROVED at 29e05e6 (docs/handoff/cycle-2-fix-REVIEW-DONE.md)
test suite: 396/396 PASS (excluding cycle-6 settings)
cycle-2-fix file: 43/43 PASS

PM must personally verify in a browser before closing the cycle:

1. Start the daemon: python app.py
2. Open http://localhost:5000 (Dashboard)
   - Network tab: confirm /api/state polling fires every ~60s
   - Guard Alpha headline: confirm value is non-zero and updates on each poll
   - Symphony cards: sparkline canvases present, MC dial shows a value (not empty box)
   - Active cards: verdict pill present (Good call / Early exit)
   - Standby cards: Cash Now button present
   - Hero chart: Bot vs Held 60-day chart renders with data points (not flat line)
3. Open http://localhost:5000/performance
   - 7 metric rows each have a metric-bar div (visible bar, not empty)
   - Cumulative returns chart renders
4. Open http://localhost:5000/history
   - Daily alpha strip SVG renders with bars
   - 4 reason cards each have a reason-bar div
5. Open http://localhost:5000/ai-advisor
   - Select a symphony, click Run Advisor
   - Suggestion cards appear with confidence badge, OOS badge, projected impact
   - Autotune runs panel populates from /api/autotune-runs
6. Tweaks panel (button top-right of nav)
   - Density default is Balanced (not Roomy)
   - Theme toggle changes all screens live
7. Google Fonts: browser DevTools > Network > filter "fonts.googleapis" — 7 families loading

NITs carried to next Dashboard cycle (non-blocking for PM verify):
- Detail panel slide-over shows static "--" values (openDetailPanel not yet wired to sym data)
- Risk math panel absent from detail panel

Scope expansion confirmed: Studio rebuild cycle covers Dashboard + Performance + Advisor + History.
Cycle 6 (Settings) resumes after PM browser-verify sign-off on this cycle.
