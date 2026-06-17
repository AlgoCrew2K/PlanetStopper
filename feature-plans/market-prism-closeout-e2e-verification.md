# Feature: Market Prism (AI Council) Closeout — Full End-to-End Verification
Status: ready
Created: 2026-06-17

## Summary

This is the **operator-mandated closeout verification** for the AI Council (the Market
Prism system): *"the closeout must be a full end-to-end verification of EVERY feature in
the AI council — not a partial proof-run, not just the orchestration."* It is **not** a
plan to build anything new. It is the exhaustive acceptance protocol that proves every
shipped Market Prism feature works **live, end-to-end, on real data** — producer →
consumer → persistence → rendered Overview tab → audit trail — AND that each feature's
**documentation matches its verified live behavior**. A doc claim contradicted by live
behavior (the recently-found "macro stub" mislabel class of defect) is a closeout failure,
not a footnote.

The council is the multi-agent overnight market read: **5 lens producers** feeding **5
analyst agents** + **1 synthesizer**, coordinating via a real Claude Code Agent Team, each
writing an auditable deliberation trail keyed to one `run_id`, producing exactly one
`MARKET_PRISM` `advisor_observations` row that the Overview tab renders. Supporting
infrastructure: the audit-log DB foundation (migration 032 + accessors + CLI writer), the
nightly lens pipeline (`run_pipeline`, the programmatic Cycle-4 path), the lens data
warehouse (third DB), and the configurable synthesis model (C1 / PR #39).

This closeout is structured as a **verification matrix** (one row per feature) plus a
**capstone live multi-analyst run** (Phase-3 observed proof run) and an explicit
**operator sign-off gate** before any Phase-4 unattended scheduling is enabled.

**Adversarial-completeness stance:** the inventory below was built from the actual code
(`ai_advisor.py`, `advisors/lens_*.py`, `advisors/lens_pipeline.py`, `database.py`,
`templates/ai_advisor.html`, `app.py`, the 6 `.claude/agents/prism-*.md` files), the
market-prism + lens feature-plans, DECISIONS.md, and the project CLAUDE.md key-files
table — then expanded to catch anything the existing phase-3 plan omitted. **Assume a
feature was missed until the whole surface is swept; the matrix below is the proof of the
sweep.** Verified facts carry a `file:line`; anything not directly verifiable in code is
labeled `[interpretation]` and must be confirmed during execution.

---

## Feature Inventory (what "EVERY feature in the AI council" means)

Enumerated and cross-verified against source on branch base `origin/main @ 348dc26`.
**21 verification-bearing features** across 6 groups. The existing
`market-prism-phase3-observed-proof-run.md` plan covers only the capstone orchestration run
(its AC-1..AC-5) — it does **NOT** independently verify the 5 lens producers, the
warehouse, the audit-log foundation primitives, the C1 model config, or the per-feature
doc-accuracy checks. This closeout is a strict superset.

| # | Group | Feature |
|---|-------|---------|
| F1 | Lens producers | Technicals lens (`lens_technicals._fetch_technicals` → `_build_technicals_section`) |
| F2 | Lens producers | Sentiment/GDELT lens (`lens_gdelt._fetch_gdelt_sentiment` + artlist → `_build_sentiment_section`) |
| F3 | Lens producers | Derivatives lens (`lens_options_proxy._fetch_options_proxy` → `_build_derivatives_section`) **+ VIX/VXV freshness guard** |
| F4 | Lens producers | Macro lens (FRED series fetch → `_build_macro_section`) |
| F5 | Lens producers | Fundamentals lens (`_fetch_fundamentals_for_ticker` + portfolio fan-out → `_build_fundamentals_section`) |
| F6 | Lens infra | Universe floors: `lens_technicals._PROXY_UNIVERSE` + `_FUNDAMENTALS_PROXY_UNIVERSE` (off-hours non-empty universe) |
| F7 | Lens infra | Honest-availability lens-block contract (`{lens, available, reason, payload, sources}`, D-1 `type(exc).__name__`) |
| F8 | Lens infra | Citation/source validation (`build_citation`) across lenses |
| F9 | Warehouse | Lens Data Warehouse third DB (`lens_warehouse.init_warehouse_db` / `persist_lens_snapshot` / `get_lens_snapshots`, `_strip_secrets`, pytest sentinel) |
| F10 | Warehouse | Warehouse wiring: sentiment (GDELT) + macro (FRED) persist after each fetch |
| F11 | Audit-log | Migration 032 `prism_audit_log` + `insert_prism_audit_entry` / `get_prism_audit_for_run` |
| F12 | Audit-log | Agent-callable CLI writer `advisors/prism_audit_write.py` (STDIN content, prints row id, D-1) |
| F13 | Nightly pipeline | `lens_pipeline.run_pipeline` 4-pass (isolation → citation validation → synthesis → MARKET_PRISM persistence) |
| F14 | Nightly pipeline | 03:00 scheduler wiring (`run_scheduler` / daemon thread, CC-2 lazy import) |
| F15 | Council agents | 5 analyst agents (`prism-{technicals,sentiment,derivatives,macro,fundamentals}-analyst`, model=opus) |
| F16 | Council agents | Synthesizer lead (`prism-synthesizer`): run_id, kickoff, clarifying Q&A, conditional debate ≤3, integrated synthesis |
| F17 | Council agents | Audit-DB-as-source-of-truth protocol (synthesizer derives availability from audit rows, not inbox) |
| F18 | Persistence | Exactly one `MARKET_PRISM` `advisor_observations` row per `run_id` (`insert_advisor_observation`, `is_advisory_only=1`) |
| F19 | Overview tab | Market Prism block render (`templates/ai_advisor.html`: sentiment chip, rationale, per-lens digest, cited sources, empty state) + `get_latest_market_prism_summary()` + `app.py` prefetch |
| F20 | Model config | C1 `ADVISOR_SYNTHESIS_MODEL` env var (default Opus 4.8) — **PR #39, OPEN, unmerged** |
| F21 | Capstone | Observed multi-analyst proof run: real deliberation, complete audit trail, one MARKET_PRISM row, rendered Overview, operator sign-off |

**Verified source anchors (sample, full anchors in the matrix):**
- 5 lens builders: `ai_advisor.py:456` (technicals), `:530` (sentiment), `:651` (derivatives), `:732` (macro), `:1076` (fundamentals).
- 5-lens set + `_call_lens_section`: `advisors/lens_pipeline.py:38-71`.
- Freshness guard: `advisors/lens_options_proxy.py:347-366` (`reason="stale_data"`).
- Audit accessors: `database.py:1217` (`insert_prism_audit_entry`), `:1252` (`get_prism_audit_for_run`), `:1180` (`get_latest_market_prism_summary`), `:1053` (`insert_advisor_observation`).
- Warehouse: `advisors/lens_warehouse.py:110/127/181`; sentiment+macro wiring `ai_advisor.py:585-630` / `:757-855`.
- CLI writer: `advisors/prism_audit_write.py` (whole file).
- Overview prefetch: `app.py:2948-3019`; render block `templates/ai_advisor.html:942-976`.
- 6 council agents: `.claude/agents/prism-synthesizer.md` + 5 `prism-*-analyst.md` (all `model: opus`).
- C1 unmerged: `advisors/lens_pipeline.py:284` still hardcodes `claude-haiku-4-5-20251001`; PR #39 (`feat/advisor-synthesis-model-config`) OPEN.

---

## Acceptance Criteria

Each AC is a closeout gate. The closeout PASSES only when **every** AC passes; any FAIL
loops back to the owning feature's cycle (do not paper over a degenerate result).

- [ ] **AC-1 (Dependency precondition):** C1 (PR #39) is merged to origin and the running
  :8090 daemon is on the deployed code that includes it. The hardcoded
  `claude-haiku-4-5-20251001` literal no longer governs the production synthesis path;
  `ADVISOR_SYNTHESIS_MODEL` (default Opus 4.8) does. Verified by reading the deployed
  `lens_pipeline.py` on the running tree + a config probe.
- [ ] **AC-2 (Per-lens live E2E):** For EACH of the 5 lenses (F1–F5), a live call through
  the real producer reaches `available=True` with **real, non-stub values** when its data
  source is reachable AND keys are present; AND the honest-degradation path returns
  `available=False` with a `type(exc).__name__`-only / informative reason when the source
  is unreachable or a key is absent. Both arms observed (not inferred).
- [ ] **AC-3 (Universe floors):** With live `logic_holdings` empty (off-hours / flat), the
  technicals and fundamentals lenses still receive a non-empty universe from their proxy
  floors (F6), so breadth/fan-out are computed on a real basket — not hollow `available=False`.
- [ ] **AC-4 (Warehouse persistence):** After a live sentiment and macro fetch, new rows
  appear in the third DB (`alphabot_warehouse.db`) via `get_lens_snapshots`; secrets are
  stripped from `raw_json`; the pytest sentinel blocks opening the real warehouse under
  pytest (F9, F10).
- [ ] **AC-5 (Audit-log foundation):** The CLI writer (F12) writes a row and prints its id;
  `get_prism_audit_for_run` returns it; migration 032 is the last wired migration (F11).
- [ ] **AC-6 (Nightly pipeline non-hollow):** A live `run_pipeline()` (non-dry-run) writes
  exactly one `MARKET_PRISM` row, runs all 5 lenses with per-lens isolation, validates
  citations, and synthesizes; when lenses have real data the verdict is a real sentiment,
  NOT `limited-inputs` (F13). The 03:00 scheduler is wired and would fire (F14).
- [ ] **AC-7 (Council deliberation live):** The multi-analyst council (F15–F17) runs on
  real data under observation: all 5 analysts file `initial_read` rows; clarifying Q&A
  occurs where relevant; debate fires ONLY on genuine disagreement (≤3 rounds) and is
  ABSENT when analysts converge; the synthesizer derives availability from audit rows, not
  its inbox.
- [ ] **AC-8 (One row per run):** Exactly one `MARKET_PRISM` row exists for the capstone
  `run_id` (F18). A retry/duplicate is a defect to fix before sign-off.
- [ ] **AC-9 (Overview renders, eyes-on):** The Overview tab renders the produced report on
  the live :8090 page; the PM Reads the screenshot **with its own eyes** and describes the
  sentiment chip, rationale, per-lens digest, and cited sources BEFORE asserting
  correctness; the informative empty-state renders when no row exists (F19).
- [ ] **AC-10 (Doc-accuracy sweep):** For EVERY feature F1–F20, the feature's documentation
  (`docs/generated/`, the CLAUDE.md key-files row, DECISIONS.md) matches the verified live
  behavior. Any contradicted claim (e.g. a "stub" label on a live producer) is filed and
  corrected as part of closeout — a doc/behavior mismatch is a closeout FAIL.
- [ ] **AC-11 (Operator sign-off gate):** The PM surfaces the capstone artifacts (rendered
  Overview screenshot + full audit-trail dump + lens-coverage/debate/spend note) to the
  operator and receives explicit sign-off. Phase-4 unattended scheduling stays
  **hard-blocked** until sign-off is received.
- [ ] **AC-12 (No execution-path contamination):** No closeout step touches
  `LIVE_EXECUTION`, trade orders, or position state. Every verified surface is advisory-only
  (`is_advisory_only=1` on the MARKET_PRISM row).

---

## Architecture

This feature introduces **no new code**. It is an observed operational verification that
exercises the shipped Market Prism deliverables on real data, plus a doc-accuracy
reconciliation pass. The PM (or a read-only verifier agent for the non-council probes)
drives it; the capstone council run is an operator-gated Claude Code Agent Team run.

**Two execution layers exist and BOTH are verified (they are not the same path):**
1. **Programmatic Cycle-4 path** — `lens_pipeline.run_pipeline()` calls each
   `_build_*_section()` directly, synthesizes via `_synthesize_via_claude` (model = C1
   env var post-PR-#39), writes the `MARKET_PRISM` row. This is what the 03:00 daemon
   thread fires. (F13/F14)
2. **Multi-analyst council path** — the `prism-synthesizer` Agent Team where each analyst
   pulls its lens via `_call_lens_section` and writes its own audit trail; the synthesizer
   writes the `MARKET_PRISM` row. This is the human-deliberation layer that
   `market-prism-overview.md` says *"replaces how that row is produced."* (F15–F18)

   **[interpretation]** These two writers both target the same `MARKET_PRISM` row family.
   The closeout must confirm which path is authoritative for the nightly row post-Epic-A
   and that they do not race or double-write on the same night. Confirm during execution.

**Live environment under verification:**
- Running daemon at `:8090` on the **deployed** post-PR-#39 code (AC-1).
- Live state DB (`alphabot_state.db`) — `advisor_observations` + `prism_audit_log`.
- Third DB `alphabot_warehouse.db` — lens snapshots.
- Real external APIs: GDELT (key-less), FRED (`FRED_API_KEY`), SEC EDGAR (UA header),
  Alpaca (technicals bars), Anthropic (Opus 4.8 for the council; synthesis model for the
  pipeline).

**Integration points exercised (read-only / advisory writes only):**
`ai_advisor._build_*_section` · `advisors/lens_*.py` producers · `advisors/lens_pipeline.py`
· `advisors/lens_warehouse.py` · `advisors/prism_audit_write.py` · `database.py` accessors ·
`templates/ai_advisor.html` Overview block · `app.py` prefetch + 03:00 scheduler · the 6
`.claude/agents/prism-*.md` role files.

## Design-System Mapping

N/A for the backend producers/infra. The one UI surface — the Overview tab Market Prism
block (`templates/ai_advisor.html:942-976`) — is verified by a live render + eyes-on
screenshot read (AC-9), asserting the design-system sentiment-chip classes
(`prism-sentiment-chip--{risk-on,risk-off,neutral,limited-inputs}`) render, not specific
RGB values.

---

## Verification Matrix

Columns: **Feature** | **Producer/Consumer files** | **Live E2E check** | **Expected
evidence (PASS)** | **Doc-accuracy check** | **Pass/Fail**.

> Run order: Group A (lens producers + infra) and Group B (warehouse/audit/pipeline) are
> independent probes runnable before the council run. Group C (council) is the capstone and
> depends on A/B passing. AC-1 (C1 merge + deploy) gates everything.

### Group A — Lens producers + lens infra (F1–F8)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F1 Technicals** | `advisors/lens_technicals.py` `_fetch_technicals` / `_PROXY_UNIVERSE`; `ai_advisor._build_technicals_section` (`:456`) | On the deployed tree, call `ai_advisor._build_technicals_section()` against live Alpaca; then force the failure arm (break Alpaca creds / empty universe). | `available=True` with real `ma_posture`, `breadth` (0..1), `momentum` over the proxy basket; AND failure arm returns `available=False` + `reason` (no `str(exc)`), no traceback. | CLAUDE.md `lens_technicals` row + DECISIONS lens-technicals entry match: 50/200 SMA, breadth excludes insufficient-history tickers, 20-day momentum, proxy floor `_PROXY_UNIVERSE`. Flag any "stub" wording. | |
| **F2 Sentiment/GDELT** | `advisors/lens_gdelt.py` `_fetch_gdelt_sentiment` + artlist; `ai_advisor._build_sentiment_section` (`:530`) | Call `_build_sentiment_section()` live (GDELT key-less); observe tone + artlist citations; force artlist-only and tone-only arms (per-source isolation). | `available=True` with a numeric tone and ≥1 validated source; per-source isolation proven (one signal can fail, the other succeeds); `reason` is type-name only on full failure. | `.claude/gdelt-contract.md` + CLAUDE.md sentiment wiring match live shape (timelinetone endpoint, normalization). Confirm GDELT key-less claim. | |
| **F3 Derivatives + freshness guard** | `advisors/lens_options_proxy.py` `_fetch_options_proxy` (`:313`), freshness guard (`:347-366`); `ai_advisor._build_derivatives_section` (`:651`) | Call `_build_derivatives_section()` live with `FRED_API_KEY` set; THEN reproduce the stale-data arm (point at an old observation / simulate >threshold age) and confirm `reason="stale_data"`, `available=False`. | Fresh path: `available=True` with `vix_level`, `vix_term_structure.regime`, `risk_read`, `as_of_date` recent; stale path: `available=False` `reason="stale_data"` — stale FRED data NOT served as current (the PR #37 fix). | CLAUDE.md derivatives row + DECISIONS/PR-#37 entry describe the freshness guard accurately (threshold, `stale_data` reason). | |
| **F4 Macro** | FRED series fetch `ai_advisor._build_macro_section` (`:732`); `_FRED_SERIES` | Call `_build_macro_section()` live with `FRED_API_KEY`; then unset the key for the degradation arm. | `available=True` with ≥1 FRED series (10y/UNRATE/CPI/FedFunds) carrying value+date + clickable source per series; key-absent arm → `available=False` with the informative "register free at fred.stlouisfed.org" reason. **NOT a stub.** | **HIGH-RISK doc check (the "macro stub" mislabel class):** confirm no doc anywhere calls macro a stub/placeholder — it is a live FRED producer (`:780-869`). File + correct any stale "stub" wording. | |
| **F5 Fundamentals fan-out** | `ai_advisor._fetch_fundamentals_for_ticker` + `_build_fundamentals_section` (`:1076`), `_FUNDAMENTALS_PROXY_UNIVERSE` | Call `_build_fundamentals_section()` (ticker=None, portfolio path) live against SEC EDGAR; verify fan-out over holdings∪proxy; confirm single-ticker path (`ticker="AAPL"`) byte-preserved; force all-fail arm. | Portfolio path: `available=True` with `per_ticker_results` for ≥1 company ticker + deduped SEC sources; per-ticker honest degradation (one ticker fails, others resolve); all-fail → `available=False`. Single-ticker path unchanged (PR #38 fix — dead lens now live). | CLAUDE.md fundamentals row (DE-FUND-001) matches: 8 large-cap COMPANY tickers (not ETFs), bounded fan-out, no invented composite ratios. | |
| **F6 Universe floors** | `lens_technicals._PROXY_UNIVERSE`; `ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE` | With live `logic_holdings` empty (verify via `database.load_state()`), confirm both lenses still build a real universe (proxy floor applied). | Technicals breadth + fundamentals fan-out computed on the proxy basket despite empty holdings — no hollow `available=False` from an empty universe (DE-TECH-002). | CLAUDE.md describes both proxy floors and the off-hours rationale; confirm the floor tickers listed match code. | |
| **F7 Honest-availability contract** | All 5 builders; `lens_pipeline._call_lens_section` (`:54`) D-1 wrapper | Across F1–F5 failure arms, assert the fixed 5-key contract `{lens, available, reason, payload, sources}` and that `reason` is only `type(exc).__name__` (no message/URL/key leak — FRED embeds the key in the URL). | Every failure arm: 5-key dict, `payload=None`, `sources=[]`, `reason` a bare type name or curated string — no `str(exc)`, no API key, no traceback. | DECISIONS DE-ML-001 (honest-availability contract) matches live behavior. | |
| **F8 Citation validation** | `ai_advisor.build_citation`; `lens_pipeline._validate_and_filter_sources` (`:128`) | Feed a malformed citation through a live lens; confirm it is dropped, not surfaced. | Only well-formed `{title,url,published,lens}` sources appear in the Overview "cited sources"; malformed entries silently dropped. | CLAUDE.md / DECISIONS citation convention (DE-ML-002, `raw_response` JSON) matches. | |

### Group B — Warehouse, audit-log foundation, nightly pipeline (F9–F14)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F9 Lens Data Warehouse** | `advisors/lens_warehouse.py` `init_warehouse_db` (`:110`), `persist_lens_snapshot` (`:127`), `get_lens_snapshots` (`:181`), `_strip_secrets` | `init_warehouse_db()` on the live `alphabot_warehouse.db`; `persist_lens_snapshot` with a payload containing a fake `api_key`; read it back via `get_lens_snapshots`; confirm the pytest sentinel raises when opening the real warehouse under pytest. | Row round-trips; `raw_json` has the secret key stripped (recursive `_strip_secrets`); append-only; WAL; sentinel blocks pytest from the real file. **Third DB confirmed distinct** from state + optimization (no cross-DB join). | CLAUDE.md `lens_warehouse` row (DW-1) matches: third DB, append-only, secret-strip set, engine-agnostic `raw_json`. | |
| **F10 Warehouse wiring** | `ai_advisor._build_sentiment_section` (`:585-630`) + `_build_macro_section` (`:757-855`) persist calls | After live F2 + F4 calls, query `get_lens_snapshots("sentiment")` and `("macro")`; confirm new rows for this run. | Sentiment (GDELT) and macro (FRED) each wrote a snapshot row (available True AND False paths both persist); off-execution-path lazy import (no module-level warehouse import). | CLAUDE.md states sentiment+macro are wired "non-hollow"; confirm no OTHER lens claims warehouse wiring it doesn't have. | |
| **F11 Migration 032 + accessors** | `database.py` migration 032, `insert_prism_audit_entry` (`:1217`), `get_prism_audit_for_run` (`:1252`) | `python -c "import database; print(database._MIGRATION_FILES[-1])"` → `032_prism_audit_log.sql`; insert + read-back a row on the live DB. | Last wired migration is 032; round-trip returns the row keyed by `run_id`, ordered append-only. | CLAUDE.md database row lists migration 032 + both accessors with correct signatures. | |
| **F12 CLI writer** | `advisors/prism_audit_write.py` (whole file) | `echo "test read" \| python -m advisors.prism_audit_write --run-id <rid> --role technicals_analyst --phase initial_read` with `DB_PATH` set to live DB; confirm it prints a positive row id; confirm a missing-arg / bad invocation prints only a type name (D-1) and exits non-zero. | STDOUT = positive integer row id; the row is readable via `get_prism_audit_for_run`; error arm leaks no traceback/message. | CLAUDE.md `prism_audit_write` row matches the `--run-id/--role/--phase` + STDIN contract. | |
| **F13 Nightly pipeline 4-pass** | `advisors/lens_pipeline.run_pipeline` (`:314`), `_collect_lenses` (`:98`), `_synthesize_via_claude` (`:243`) | Run `run_pipeline()` (non-dry-run) live with all keys present; inspect the returned dict + the written `MARKET_PRISM` row. | Exactly one MARKET_PRISM row written; all 5 lenses attempted with per-lens isolation (`lenses_attempted=5`); citations validated; a REAL `verdict` (not `limited-inputs` when lenses have data); `available_lens_count` reflects reality. | CLAUDE.md `lens_pipeline` row + DECISIONS DE-CY4-001 match; **confirm the synthesis-model line reflects C1 (env var), not the old hardcoded Haiku** (AC-1 dependency). | |
| **F14 03:00 scheduler** | `app.py` `run_scheduler` (`:437-443`) → `_run_lens_pipeline` (`:425`) → daemon thread → `_lens_pipeline_worker` (`:410`) → `run_pipeline` lazy import (`:417`) | Confirm the daemon registers the 03:00 Prism job (`schedule.every().day.at("03:00").do(_run_lens_pipeline)`, `app.py:443`) on the running tree; check daemon log for the scheduled entry. | The 03:00 job is registered and invokes `run_pipeline` via a daemon thread + lazy import (never module-level — CC-2), so the scheduler thread returns immediately. | CLAUDE.md states 03:00 daily via `run_scheduler()`; confirm time (`03:00`, off-hours, no overlap with `:00` execution path) + lazy-import boundary documented accurately. | |

### Group C — Council agents, persistence, Overview, model config (F15–F20)

| Feature | Producer / Consumer | Live E2E check | Expected evidence (PASS) | Doc-accuracy check | P/F |
|---|---|---|---|---|---|
| **F15 5 analyst agents** | `.claude/agents/prism-{technicals,sentiment,derivatives,macro,fundamentals}-analyst.md` | During the capstone run (F21), each analyst pulls its lens via `_call_lens_section`, forms a real read, and files `phase=initial_read` to the audit log. | 5 distinct `initial_read` audit rows (one per analyst role), each reflecting its real lens data; model=opus confirmed in each agent file. | Each analyst file's described lens + role string matches its audit `agent_role`; no analyst references a lens it doesn't own. | |
| **F16 Synthesizer orchestration** | `.claude/agents/prism-synthesizer.md` | During F21: synthesizer generates `run_id`, kicks off all 5, facilitates clarifying Q&A, decides debate, integrates, writes synthesis + MARKET_PRISM row. | `run_id` immutable across the run; `phase=synthesis` row present; conditional-debate protocol respected (see F17); a genuinely integrated read (cross-lens reasoning, not a concatenation — AC-7 in phase-2 plan). | `prism-synthesizer.md` debate protocol (≤3 rounds, clarifications ≠ debate) matches the runbook + phase-2 plan. | |
| **F17 Debate/clarification protocol** | synthesizer + analysts; audit `phase` tags | In F21: confirm clarifications are tagged `phase=clarification`, debate rounds `phase=debate_round_N` ONLY on genuine disagreement, and ABSENT when analysts converge. | Audit trail shows correct phase tags; NO spurious `debate_round_*` rows when reads converge; ≤3 debate rounds if disagreement; synthesizer derived availability from audit rows (F17 protocol), not inbox. | Runbook step 4/7 + phase-2 AC-3/AC-4/AC-5 match observed trail behavior. | |
| **F18 One MARKET_PRISM row/run** | `database.insert_advisor_observation` (`:1053`, `is_advisory_only=1`) | After F21: `SELECT count(*) ... WHERE advisor_role='MARKET_PRISM' AND raw_response run_id=<rid>`. | Exactly 1 row; `is_advisory_only=1`; `verdict` matches synthesizer's `overall_sentiment`; `run_id` matches the audit trail. | DECISIONS DE-ML-003 (MARKET_PRISM advisory-only, DB-enforced flag) matches. | |
| **F19 Overview tab render** | `templates/ai_advisor.html:942-976`; `database.get_latest_market_prism_summary` (`:1180`); `app.py:2948-3019` prefetch | Load `GET /ai-advisor` on live :8090 after F21; capture a screenshot; **Read it with the Read tool (eyes-on)**; describe chip + rationale + per-lens digest + cited sources. Also verify the empty-state arm (no row) renders informatively. | The rendered block shows the capstone sentiment chip (correct semantic class), rationale text, per-lens digest (5 lenses with available flags), and clickable cited sources; empty-state arm renders the informative message, not a blank/error. PM describes the screenshot before asserting. | CLAUDE.md `templates/ai_advisor.html` Overview row matches what renders (chip/rationale/digest/sources/empty-state). | |
| **F20 C1 model config** | `advisors/lens_pipeline._synthesize_via_claude` (`:243`, hardcoded `:284` today); PR #39 | After PR #39 merge+deploy: probe that the synthesis path reads `ADVISOR_SYNTHESIS_MODEL` (default Opus 4.8) and no hardcoded Haiku literal governs prod; confirm tests don't fire real Opus. | Deployed `lens_pipeline.py` reads the env var; default resolves to Opus 4.8; no `claude-haiku-4-5-20251001` literal on the prod synthesis path. | DECISIONS / `docs/generated/` document `ADVISOR_SYNTHESIS_MODEL` + default; CLAUDE.md `lens_pipeline` row updated from "Claude Haiku synthesis" to the configurable model. **This doc line is currently stale and MUST be reconciled at closeout.** | |

### Capstone — Phase-3 observed multi-analyst run (F21)

| Feature | Live E2E check | Expected evidence (PASS) | Doc-accuracy | P/F |
|---|---|---|---|---|
| **F21 Observed proof run** | PM drives the real `prism-synthesizer` Agent Team once on real data under direct observation (single `run_id`, real Opus spend). Follows `market-prism-runbook.md` steps 1–8. | (1) One real integrated `MARKET_PRISM` row — NOT "Synthesis unavailable", NOT a stub, NOT degenerate `limited-inputs` when lenses have data; (2) `get_prism_audit_for_run(run_id)` returns the full trail (≥5 initial_read + clarifications + any debate + 1 synthesis); (3) exactly one row per run_id; (4) Overview renders it (F19, eyes-on); (5) PM surfaces both artifacts + operator note (lens coverage, debate summary, Opus spend) and receives sign-off. | The runbook + phase-3 plan describe exactly this run; reconcile the overview epic-status (🟡 in progress) to reflect closeout outcome. | |

---

## Edge Cases

- **C1 not merged before closeout:** AC-1 fails → closeout is BLOCKED. Do not verify the
  pipeline synthesis path against the stale hardcoded-Haiku code and call it done; the doc
  line ("Claude Haiku synthesis") would also stay wrong. Merge + deploy PR #39 first.
- **Off-hours / flat holdings (empty `logic_holdings`):** expected at 03:00 / weekends.
  The proxy floors (F6) must still yield a real universe; if a lens returns
  `available=False` purely because the universe was empty, that is a hollow-wiring
  regression, not honest degradation.
- **All lenses genuinely unavailable (keys missing / APIs down):** a `limited-inputs`
  verdict is acceptable ONLY then. A `limited-inputs` verdict while lenses HAVE data is a
  defect (degenerate synthesis) — loop back, do not sign off.
- **Stale FRED data (derivatives):** the freshness guard must fire (`reason="stale_data"`).
  If stale data is served as current, PR #37's fix has regressed — closeout FAIL.
- **Dead fundamentals lens:** if `_build_fundamentals_section(ticker=None)` returns
  `available=False` with a non-empty universe, PR #38's fan-out fix has regressed.
- **Duplicate MARKET_PRISM rows for one run_id:** a retry/double-write is a defect; the
  synthesizer checks for an existing row before writing (runbook constraint). Verify count==1.
- **Two writers race (pipeline vs council on the same night):** `[interpretation]` confirm
  the nightly authoritative writer post-Epic-A; ensure the 03:00 pipeline and an
  operator-driven council run do not both write a row for the same logical night without a
  clear precedence. Resolve during execution; document the decision.
- **Overview renders but is visually wrong:** a green render-poll + 0 console errors is
  necessary but NOT sufficient — the PM must read the screenshot. (Per the standing
  "actually LOOK at the render" rule.)
- **Doc says stub, code is live (or vice-versa):** any doc/behavior contradiction is a
  closeout FAIL filed against AC-10 and corrected before sign-off. The macro-stub mislabel
  is the canonical example to hunt for.
- **Analyst files a read but its message never reaches the synthesizer (inbox lag):** the
  synthesizer must derive availability from audit-DB rows, not its inbox (F17). If it marks
  a lens non-responsive that DID file an `initial_read`, that is a protocol regression.
- **Operator unavailable for sign-off:** Phase-4 stays hard-blocked. The PM does not
  unilaterally clear AC-11.

## Security Considerations

- **Real API keys in use:** `FRED_API_KEY`, SEC UA string, Alpaca creds, `ANTHROPIC_API_KEY`
  (Opus 4.8). All must be present in the verification environment. No key is logged, echoed
  to the audit log, the Overview tab, Discord, or any closeout artifact.
- **D-1 contract everywhere:** every failure arm verified surfaces `type(exc).__name__`
  only. FRED embeds the API key in the request URL, so `str(exc)` on a macro/derivatives
  failure could leak the key — AC-7 explicitly checks the reason is a bare type name.
- **Warehouse secret-strip:** AC-4 confirms `_strip_secrets` removes
  `{api_key,token,secret,password,Authorization}` recursively from `raw_json` before persist.
- **Advisory-only / no execution path:** AC-12 — nothing in the closeout touches
  `LIVE_EXECUTION`, trade orders, or position state. The MARKET_PRISM row is
  `is_advisory_only=1` (DB-enforced). The Overview tab is read-only.
- **Prompt injection:** lens data from external APIs (GDELT/FRED/SEC) enters analyst
  prompts during the council run. Analysts treat all external data as untrusted text. No
  new mitigation is added at closeout beyond what Phase-2 established; the closeout confirms
  no analyst executes external content.
- **Opus spend:** the capstone council run incurs real multi-agent Opus 4.8 spend (operator-
  authorized). Bounded debate (≤3 rounds) and bounded clarification caps runaway spend; the
  operator note records total spend.

## Testing Strategy

**No new automated tests** — this is an observed live verification, not a codepath. The
acceptance bar is the PM's direct observation of each matrix row on the real environment +
the operator's sign-off. Existing unit suites remain a necessary-but-not-sufficient
backstop (they did not catch the dead fundamentals lens, the stale-VIX serve, or the macro
mislabel — those needed live verification).

**Execution protocol:**
1. **Precondition (AC-1):** merge PR #39, deploy to the :8090 tree, confirm the deployed
   `lens_pipeline.py` reads `ADVISOR_SYNTHESIS_MODEL`. Confirm all keys present + Opus spend
   authorized. Verify HEAD SHA of the running tree.
2. **Group A probes (F1–F8):** for each lens, drive both the success arm and the failure
   arm via a read-only verifier agent (or PM) calling the real `_build_*_section()` on the
   deployed tree; record evidence in the matrix.
3. **Group B probes (F9–F14):** warehouse round-trip + secret-strip; CLI writer round-trip
   + D-1 error arm; migration-032 check; a live `run_pipeline()` non-dry-run; scheduler
   wiring confirmation.
4. **Group C / capstone (F15–F21):** drive the `prism-synthesizer` Agent Team once on real
   data per the runbook; collect the audit trail; verify one-row-per-run; render + eyes-on
   the Overview tab.
5. **Doc-accuracy sweep (AC-10):** for every feature, diff the doc claim against verified
   behavior; file + correct contradictions (the macro-stub mislabel class). The doc-writer
   on the closeout team lands corrections before sign-off.
6. **Operator sign-off (AC-11):** surface artifacts; receive explicit go-ahead before
   Phase-4.

**Fixture provenance:** N/A — this is a live run on real APIs, not a fixture-backed test.
Evidence is captured-from-live (screenshots, audit-DB dumps, query outputs), not authored.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Closeout is a strict SUPERSET of `market-prism-phase3-observed-proof-run.md` | Phase-3 covers only the capstone run (AC-1..AC-5). The operator mandated "EVERY feature" — that requires the per-lens, warehouse, audit-foundation, pipeline, and C1 rows + per-feature doc checks that phase-3 omits. |
| Both execution layers (pipeline + council) verified separately | They are different writers of the MARKET_PRISM row; verifying only the council leaves the 03:00 nightly path (what actually runs blind) unproven. |
| Per-feature doc-accuracy is an AC, not a footnote | A doc claim contradicted by live behavior (macro "stub" mislabel) is a real defect class this closeout exists to catch. |
| Both arms (success + honest-degradation) verified per lens | The dead-fundamentals + stale-VIX defects were degradation-path failures; verifying only the happy path is what let them ship. |
| C1 (PR #39) merge+deploy is a hard precondition (AC-1) | The pipeline synthesis path and its doc line are wrong until C1 lands; verifying against stale hardcoded-Haiku code would bake in a doc/behavior mismatch. |
| Capstone council run is operator-gated; Phase-4 hard-blocked on sign-off | "Prove it before trusting it to run blind" — the operator must see the real artifacts before any unattended schedule. |
| Eyes-on screenshot read is mandatory for AC-9 | A render-poll + 0 console errors is necessary but not sufficient for visual correctness. |

## Scope Boundaries

- **IN:** live end-to-end verification of all 21 features (F1–F21) on the real environment;
  both success and honest-degradation arms per lens; warehouse + audit-foundation + pipeline
  + scheduler probes; the capstone observed multi-analyst council run; per-feature
  doc-accuracy reconciliation (corrections filed + landed); operator sign-off gate; the
  C1 merge+deploy precondition check.
- **OUT:** building any new code or new tests (this is verification, not development);
  enabling Phase-4 unattended scheduling (hard-blocked until AC-11 sign-off — Phase-4 is its
  own feature, `market-prism-phase4-unattended-scheduling.md`); any change to the lens
  producers, the council agents, or the Overview tab beyond doc corrections; reprocessing
  historical runs; Epic-B lens quality enrichment beyond what is already shipped.

**Dependencies:**
1. **C1 / PR #39** (`feat/advisor-synthesis-model-config`) merged to origin AND deployed to
   the running :8090 tree — gates AC-1 and the F13/F20 doc reconciliation.
2. The :8090 daemon running the **deployed** post-PR-#39 code (not a stale tree).
3. Keys present: `FRED_API_KEY`, `ANTHROPIC_API_KEY`, Alpaca creds, SEC UA; Opus 4.8 spend
   authorized.
4. Phases 1 + 2 (audit-log foundation + council agents) merged and clean on main
   (already on `348dc26`).
5. Operator available for the AC-11 sign-off gate.

**Closeout team composition (when executed):** a non-TDD verification Agent Team —
read-only verifier(s) driving the Group A/B probes, the `prism-synthesizer` + 5 analysts
for the capstone council run, a `doc-gen` doc-writer landing the AC-10 corrections, and a
synthesizing lead reconciling the matrix into one verdict. No Toxic Pair (no code written).
