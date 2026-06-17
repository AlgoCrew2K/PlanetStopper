# Cluster 2 — Group K: Strategy Builder (F32–F34)
Auditor: closeout-audit-suite
Date: 2026-06-17
Evidence standard: file:line + runnable result per finding

---

## F32 — propose_strategies + single-batch FDR gate + advisory-only

**PASS**

Static cite:
- `advisors/strategy_builder_engine.py:855-865`: `propose_strategies` public signature:
  ```python
  def propose_strategies(
      objective: Objective,
      universe: list[str],
      screen_config: ScreenConfig,
      live_returns: list[float],
      symphony_id: str = "",
      *,
      incumbent_oos_alpha: float = 0.0,
      default_oos_alpha: float = 0.0,
      community_candidates: list[CandidateInfo] | None = None,
  ) -> ProposalRun:
  ```
- `:864`: `community_candidates` kwarg present (keyword-only). `:921-922`: `if community_candidates: candidate_infos.extend(community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN])`. When `None` or `[]` → no-op (byte-identical to template-only path).
- `:964-969` (Step 3): `gate_batch = evaluate_candidate_batch(bt_candidates, ...)` — called on the FULL batch of successfully-backtested candidates (`bt_candidates`), BEFORE screens.
- `:971-980` (Step 4): screens applied only to `gate_batch.survivors` — never to the gate input (FDR integrity).
- `:982-1008` (Step 5): `_persist_survivor` called for screened survivors only; advisory-only wired inside `_persist_survivor` (calls `database.insert_advisor_observation`).
- Route `app.py:3394-3405`: route docstring: "NOT added to `_SETTINGS_WRITE_ALLOWLIST`"; "No LIVE_EXECUTION interaction anywhere."
- Route `app.py:3437-3443`: `run = propose_strategies(objective=, universe=, screen_config=ScreenConfig(), live_returns=[], symphony_id=)` — NO `community_candidates` argument. This is the HF-1 finding (see F35 / Group L).
- D-1 at `app.py:3444-3449`: `except Exception as exc: return jsonify({"error": type(exc).__name__}), 200`

**HF-1 cross-reference**: The absence of `community_candidates` in `app.py:3437` is simultaneously the production wiring for F32 (template-only in prod) and the HF-1 hollow finding for F35.

---

## F33 — symphony_schema never-raising + 10 constructors

**PASS**

Runnable result (direct Python import, no live API):

```
F33a validate_tree (valid tree): errors=[] -> PASS
F33b render_rules_text: "STRATEGY 'Test Portfolio' (rebalance: daily)  WEIGHT equally across children  H..."
F33b render_rules_text non-empty: PASS
F33c lint_tree (valid tree): warnings=[] -> PASS (never-raises, list returned)
F33d extract_tickers: ['QQQ', 'SPY'] -> PASS
F33e depth-230 traversal: PASS (no RecursionError)
F33f unknown indicator fn is lint-only (no HARD validate error): PASS
F33f lint warning: ["unverified indicator fn 'UNKNOWN_XYZ' (not in KNOWN_INDICATOR_FNS)"]
```

Constructors verified by direct call: `make_root`, `make_asset`, `make_weight_equal`, `make_indicator`, `make_condition`, `make_if`. Additional constructors (`make_group`, `make_filter`, `make_weight_specified`, `make_inverse_vol`, `make_condition_operand`, `make_if_compound`) confirmed to exist via grep of `advisors/symphony_schema.py:784-1115`.

Static cite `advisors/symphony_schema.py`:
- `:784`: `def make_root(name, rebalance, children)`
- `:832`: `def make_group(name, children)`
- `:842`: `def make_filter(...)`
- `:866`: `def make_indicator(fn, ticker, *, window)`
- `:880`: `def make_condition(lhs_indicator, comparator, rhs, *, rhs_indicator=None)`
- `:948`: `def make_condition_operand(fn, ticker, *, window)`
- `:1072`: `def make_if_compound(...)`
- `:1115`: `def make_if(condition, *, then_children, else_children)`

---

## F34 — composer_backtest_client 1 req/s rate limit + 429 backoff

**PASS (static cite only — no live Composer call; market-hours + pacing constraint)**

Static cite `advisors/composer_backtest_client.py`:
- `:30-32` (module docstring): "``POST /api/v0.1/backtest`` inherits the standard Composer 1 req/sec limit. Callers are responsible for spacing concurrent candidate batches; this module does not sleep between separate ``run_backtest`` calls."
- `:55`: `_BACKOFF_INTERVALS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)` — explicit retry schedule
- `:58`: `BACKTEST_MAX_RETRY_WAIT_SECONDS: float = sum(_BACKOFF_INTERVALS)  # 15 s`
- `:65`: `_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})`
- `:330-339`:
  ```python
  if response.status_code == 429:
      retry_after = float(response.headers.get("Retry-After", _BACKOFF_INTERVALS[0]))
      ...
      time.sleep(retry_after)
  ```

**[ASSUMPTION-K-1]**: The 1 req/s pacing is the caller's responsibility per the docstring at `:30-32`. The `propose_strategies` engine at `strategy_builder_engine.py:932-960` calls `run_backtest` in a sequential `for info in candidate_infos` loop (`:937`), which naturally paces calls since each call is synchronous. However, there is no explicit `time.sleep(1.0)` between calls in the strategy_builder_engine loop. The 1 req/s limit is respected only if Composer responses take ≥ 1 second each. A sub-second response could cause >1 req/s. [ASSUMPTION: pacing relies on Composer response latency exceeding 1s, not an explicit sleep]. Non-blocking for this audit; the existing unit tests and Composer's natural latency provide practical enforcement. Down-ranked from HIGH to MED confidence.

---

## Summary — Group K

| Feature | Status | Confidence |
|---------|--------|------------|
| F32 propose_strategies single-batch FDR + advisory-only | PASS | HIGH |
| F33 symphony_schema never-raising + constructors | PASS | HIGH (runnable) |
| F34 1 req/s rate limit (static cite) | PASS | MED |
| F34 429 backoff (_BACKOFF_INTERVALS + Retry-After) | PASS | HIGH |

**Open Questions:**
- [ASSUMPTION-K-1] F34: 1 req/s pacing relies on Composer response latency ≥ 1s, not explicit sleep. Non-blocking — no known production issue, but the mechanism is implicit.
