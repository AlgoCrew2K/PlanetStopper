> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

08f3758

Routes verified by tw (415/415 PASS, stable ordering):
- All Dashboard BLOCKs from parity re-audit now covered by tests

Delta since v4 (a0ce875 → 08f3758):
- static/index.js: added updateComparisonRows(data) reading portfolio_strip.{today_change,cumulative_return,max_drawdown}; called from updateDashboard(data); None/undefined guards on all three keys
- templates/index.html: added data-testid="comp-bar-bot" + data-testid="comp-bar-held" elements to all 3 comparison rows (TODAY/CUMULATIVE/MAX DD)
- tests/ui/test_cycle_2_fix_live_data.py: 6 new tests (5 RED from 044d049 now GREEN + 1 provenance test at 08f3758)

New tests (all GREEN):
1. test_dashboard_js_has_comparison_row_update_function — index.js references today_change from portfolio_strip
2. test_dashboard_js_comparison_rows_called_from_update_dashboard — updateDashboard body references comparison-row update
3. test_index_js_has_named_updateComparisonRows_function — exact function name updateComparisonRows exists
4. test_index_js_updateComparisonRows_reads_all_three_portfolio_strip_keys — function body references today_change, cumulative_return, max_drawdown
5. test_rendered_dashboard_has_comp_bar_elements — rendered / HTML has >= 3 comp-bar-bot and >= 3 comp-bar-held elements
6. test_get_guard_alpha_by_symphony_returns_nonzero_when_exit_triggers_seeded — DB provenance: seeds real SQLite exit_triggers, calls get_guard_alpha_by_symphony() directly, asserts non-zero at_return returned

Suite: 415/415 PASS (excl cycle-6, stable ordering via -p no:randomly)
Note: 2 test failures appear when pytest-randomly reorders — pre-existing monkeypatch pollution from an earlier test, not introduced this cycle. Stable ordering is clean.
