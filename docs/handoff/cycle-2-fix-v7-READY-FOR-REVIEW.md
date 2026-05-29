> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

9cbb13f

Routes verified by tw (429/429 PASS, stable ordering):
All BLOCKs from PM v7 dispatch now covered by tests and GREEN.

Delta since v6 (bd5865c → 9cbb13f):
- app.py: guard_alpha injection now computes triggered_at_return - current_return (was storing raw at_return)
- app.py dashboard() route: injects sym['id'] = k for each bot_state entry before template render
- static/index.js renderSparkline: reads d['return'] instead of d.bot (matches /api/chart response field)
- templates/index.html .cash-now-btn CSS: transparent bg + 1px solid border (outlined default), solid fill on :hover, font-weight 600, text-transform uppercase, letter-spacing 0.06em
- templates/index.html .cash-now-btn:disabled CSS: opacity 0.4, cursor default (triggered-card faint state)

New tests (all GREEN):
- test_guard_alpha_is_triggered_at_return_minus_current_return: guard_alpha == ta - cr, not raw at_return
- test_api_state_portfolio_strip_today_change_is_dict_not_float: portfolio_strip.today_change shape correct
- test_index_js_update_dashboard_passes_data_not_meta_to_comparison_rows: call site passes 'data'
- test_template_cash_now_button_uses_token_not_bare_hex: no bare hex in button CSS
- test_rendered_dashboard_card_spark_has_non_empty_data_sym_id: canvases have non-empty data-sym-id
- test_index_js_render_sparkline_reads_return_not_bot: reads d['return'] not d.bot
- test_cash_now_button_css_is_outlined_not_solid_fill: transparent background default
- test_cash_now_button_css_has_uppercase_text: text-transform uppercase present
- test_cash_now_button_css_font_weight_is_600: font-weight 600
- test_cash_now_button_css_has_border_styling: border: 1px solid present
- test_cash_now_button_triggered_card_has_disabled_state_css: :disabled rule present

Parity confirmed BLOCK-B closed (today_change values update in DOM post-poll).
Parity investigation pending for v7 fixes at 9cbb13f.

Suite: 429/429 PASS (excl cycle-6, stable ordering)
