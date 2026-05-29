> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

bd5865c

Routes verified by tw (417/417 PASS, stable ordering):
- All Dashboard BLOCKs from ux v5 review now covered by tests and GREEN

Delta since v5 (08f3758 → bd5865c):
- templates/index.html: added 6 data-testid attributes to vs-val spans so updateComparisonRows() querySelector calls resolve correctly on each poll:
  - comp-today-bot-text, comp-today-held-text (Today row)
  - comp-cumulative-bot-text, comp-cumulative-held-text (Cumulative row)
  - comp-mdd-bot-text, comp-mdd-held-text (Max DD row)

New tests (all GREEN):
- test_rendered_dashboard_has_comp_text_testids — rendered / contains all 6 data-testid text-value attributes
- test_index_js_updateComparisonRows_uses_dynamic_text_selector_pattern — JS function body uses comp-{id}-bot-text / held-text pattern and assigns textContent

Suite: 417/417 PASS (excl cycle-6, stable ordering)
