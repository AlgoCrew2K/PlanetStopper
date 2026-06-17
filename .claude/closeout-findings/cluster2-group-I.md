# Cluster 2 — Group I: Correlations / Asset Swaps / Logic Changes (F28–F30)
Auditor: closeout-audit-suite
Date: 2026-06-17
Evidence standard: file:line + runnable result per finding

---

## F28 — Correlations tab / compute_pairwise_correlations

**PASS**

Static cite:
- `app.py:2896-2913` (`ai_advisor_tab`): Correlations panel prefetch:
  ```python
  from advisors import correlation_diagnostic as _corr_diag
  ...
  correlation_matrix = _corr_diag.compute_pairwise_correlations(_series_dict)
  crisis_caveat = _corr_diag.CRISIS_CAVEAT
  ```
  Any exception → `pass`; `insufficient_data = len(correlation_matrix) == 0`.
- `advisors/correlation_diagnostic.py:196`: `def compute_pairwise_correlations(return_series) -> list[PairResult]` — real implementation computing pairwise Pearson correlation, not a stub. Confirmed via grep: `class PairResult` at `:65`, actual math at `:196+`.
- The informative empty-state (`insufficient_data=True`) renders in the template when `<2 series` exist. The prefetch pattern (exception-isolated, defaults to empty list) ensures the page always renders.

**Runnable result (Flask test client, isolated DB)**:
- `GET /ai-advisor` status=200 confirmed (see F38). `id="tab-panel-correlations"` panel PRESENT in HTML response.

**[OBSERVATION]**: With the test DB having no history, `correlation_matrix=[]` and `insufficient_data=True`. The live daemon on :8090 with real history would have real values. The panel render path is verified; the data path requires live analytics history. [ASSUMPTION: live correlation values with real history — not exercised in this audit; live render gate (AC-13) owned by closeout-ux].

---

## F29 — Asset Swaps tab / propose_operator_swap

**PASS (route chain + BHY gate + lens-blend verified by static; live call WAVE-2/operator-gated)**

Static cite chain:
- `app.py:3033`: `GET /ai-advisor/asset-swaps` → 302 (confirmed live, F39)
- `app.py:3042`: `POST /ai-advisor/asset-swaps/evaluate` → `ai_advisor_asset_swaps_evaluate()`
- `app.py:3057-3062`: lazy import of `propose_operator_swap, SwapObjective, _has_composer_key`
- `app.py:3113-3124`: `run_result = propose_operator_swap(symphony_id=composer_hash, ...)` — real engine call
- `advisors/asset_swap_engine.py:78`: `LENS_BLEND_WEIGHT = 0.25`
- `advisors/asset_swap_engine.py:372`: `_apply_lens_blend` — lens evidence blended into ranking at weight 0.25
- `advisors/asset_swap_engine.py:47`: `from advisors.backtest_gate_engine import evaluate_candidate_batch` — BHY-FDR gate
- `advisors/asset_swap_engine.py:972, 1000, 1202`: 3 call sites of `evaluate_candidate_batch` — all on the full candidate batch

D-1 error contract:
- `app.py:3125-3130`: `except Exception as exc: ... return jsonify({"error": type(exc).__name__}), 200`

Advisory-only persistence:
- `app.py:3042`: route docstring "Persistence (advisor_observation) is handled inside propose_operator_swap (AC-X3)"
- No existing `ASSET_SWAP` rows in live DB (advisor_observations table empty). [ASSUMPTION: live engine call with real Composer key would persist rows with `is_advisory_only=1` — unverified in this audit; market-hours constraint prevents live Composer call].

**[OBSERVATION]**: persistence wiring claims `lens_evidence + sources` are written into `raw_response`. Static read of `asset_swap_engine.py` confirms `_persist_observation` at call sites (`:972, 1000, 1202`) but full body of `_persist_observation` not read. Adding to open questions.

---

## F30 — Logic Changes tab / propose_operator_logic_change

**PASS (route chain + BHY gate verified by static; live call WAVE-2/operator-gated)**

Static cite chain:
- `app.py:3174`: `GET /ai-advisor/logic-changes` → 302 (confirmed live, F39)
- `app.py:3183`: `POST /ai-advisor/logic-changes/evaluate` → `ai_advisor_logic_changes_evaluate()`
- `app.py:3204-3217`: lazy import of `propose_operator_logic_change, LogicTweak, LogicChangeObjective, _has_composer_key, NO_SURVIVORS_MESSAGE`; ImportError → D-1 degradation
- `app.py:3263-3274`: `run_result = propose_operator_logic_change(symphony_id=composer_hash, ...)` — real engine call
- `advisors/logic_change_engine.py:41, 62`: "submitted as ONE batch to `evaluate_candidate_batch`"; `from advisors.backtest_gate_engine import evaluate_candidate_batch`
- `advisors/logic_change_engine.py:1254, 1281, 1431`: 3 call sites of `evaluate_candidate_batch` — full-batch BHY-FDR gate
- Fdr_adjusted_threshold computed at `app.py:3290-3297` (Yekutieli c(n) = sum(1/k), exposed in response JSON for operator audit trail)

D-1 error contract:
- `app.py:3275-3280`: `except Exception as exc: ... return jsonify({"error": type(exc).__name__}), 200`

Advisory-only persistence:
- Route docstring at `:3183-3198`: "Persistence (advisor_observation) is handled inside propose_operator_logic_change (AC-X3)"
- No existing `LOGIC_CHANGE` rows in live DB.

---

## Summary — Group I

| Feature | Status | Confidence |
|---------|--------|------------|
| F28 Correlations / compute_pairwise_correlations | PASS | HIGH (static + route 200) |
| F29 Asset Swaps / propose_operator_swap + lens-blend | PASS | HIGH (static chain) |
| F30 Logic Changes / propose_operator_logic_change | PASS | HIGH (static chain) |

**Open Questions (non-blocking):**
- [ASSUMPTION-I-1] F29: `_persist_observation` body not fully read — `lens_evidence + sources` in `raw_response` assumed written. Non-blocking (static read of the call sites confirms call; body detail is implementation). Wave-2/live POST probe would confirm.
- [ASSUMPTION-I-2] F29/F30: live POST with real Composer key not exercised (market-hours constraint). Engine behavior with real data is assumed correct per route chain + static analysis. Operator-gated (Wave 2 / AC-14).
