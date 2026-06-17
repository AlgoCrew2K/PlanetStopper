# DRAFT: CLAUDE.md ai_advisor.py key-files row update
# FOR PM TO APPLY post-merge to the primary repo CLAUDE.md
# Do NOT apply this file directly — PM copies the replacement row below into CLAUDE.md § Key Files table

## Replacement row for `ai_advisor.py`

Replace the current `ai_advisor.py` row in the Key Files table with the following (everything from `| \`ai_advisor.py\`` through `no invented composite ratios |` is replaced):

---

| `ai_advisor.py` | Claude-backed config advisor: `assemble_advisor_context` (accepts `composer_symphony_id` + `autotune_run` params; `autotune_run` is HONORED — pass a pre-fetched row to skip the internal DB fetch, or use the default `_SENTINEL` to fetch internally), `build_assessment_from_context` (per-symphony informative empty-state), `request_suggestions` (D-1 fully honored: all error paths return `type(exc).__name__` only), C2 safety gates (4 gates on the accept path: allowlist/Gate-1 blocks, risk-direction/Gate-2 logs-only non-blocking, OOS-revalidation/Gate-3 blocks, locked-var/Gate-4 blocks); 7-item suggestible allowlist (6 Optuna search-space keys + MAX_SQUEEZE_FLOOR); **fundamentals lens portfolio fan-out (DE-FUND-001)**: `_build_fundamentals_section(ticker=None)` (the 03:00 nightly Prism + `assemble_advisor_context` path) fans out SEC EDGAR companyfacts over live `logic_holdings` ∪ `_FUNDAMENTALS_PROXY_UNIVERSE` (8 large-cap COMPANY tickers — NOT ETFs, which lack companyfacts) via the extracted `_fetch_fundamentals_for_ticker(ticker)` helper; single-ticker path byte-preserved; per-ticker honest degradation; bounded SEC fan-out; no invented composite ratios; **vintage-correct selection (DE-FUND-002)**: `_SEC_KEY_CONCEPTS` restructured to `(label, candidate_tags)` tuples — Revenues unions `RevenueFromContractWithCustomerExcludingAssessedTax`/`SalesRevenueNet`/`Revenues` so migrated XBRL tags are not frozen (Mode A); selection loop sorts unioned candidate-tag entries by `(end desc, filed desc)` to pick the latest reporting period, not the oldest comparative entry from a 10-K bundle (Mode B); `key_facts` output keys stable (`ai_advisor.py:361-374`, `ai_advisor.py:1011-1073`) |

---

## What changed vs. current row

Added at the end of the existing `ai_advisor.py` description, after "no invented composite ratios":

> ; **vintage-correct selection (DE-FUND-002)**: `_SEC_KEY_CONCEPTS` restructured to `(label, candidate_tags)` tuples — Revenues unions `RevenueFromContractWithCustomerExcludingAssessedTax`/`SalesRevenueNet`/`Revenues` so migrated XBRL tags are not frozen (Mode A); selection loop sorts unioned candidate-tag entries by `(end desc, filed desc)` to pick the latest reporting period, not the oldest comparative entry from a 10-K bundle (Mode B); `key_facts` output keys stable (`ai_advisor.py:361-374`, `ai_advisor.py:1011-1073`)

## Verification

Claims backed by committed code at c72bd3a:
- `_SEC_KEY_CONCEPTS` type change: `ai_advisor.py:361` — `dict[str, tuple[str, tuple[str, ...]]]`
- Revenues candidate tags: `ai_advisor.py:362-369`
- Union + sort loop: `ai_advisor.py:1011-1045`
- Sort key `(end desc, filed desc)`: `ai_advisor.py:1039-1043`
- Outer logical keys stable: `ai_advisor.py:361-374` (outer keys: `Revenues`, `NetIncomeLoss`, `Assets`, `Liabilities`, `StockholdersEquity` — unchanged)
