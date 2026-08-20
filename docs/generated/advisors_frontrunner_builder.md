# advisors/frontrunner_builder

> Orchestrates the Frontrunner Builder pipeline: detect the incumbent frontrunner overlay, generate a candidate via Fable, splice it into the symphony, independently re-backtest and gate both sides (Gate#2's incumbent baseline is fold-matched to the candidate's own validation-fold sum, AC-G2-1..6), apply Calmar acceptance, and queue survivors for operator approval. Every build run also classifies the incumbent's FR-checks against live Atlas signal data IN MEMORY (AC-5) — gating generation and attaching provenance to accepted candidates — but does not persist or render the classification result; that layer was built and then de-productized per operator directive. See "AC-5: Signal-Gated Generation (in-memory, no persistence)" below.

**Source:** `advisors/frontrunner_builder.py`
**Last updated:** 2026-08-20 (`DE-FR-PROPOSAL-IDENTITY-001` Revise 2 — PR #126's independent `/code-review` returned 11 fix-before-merge findings (F1-F10, F12; F11 accepted-not-blocking); a new public helper `resolve_incumbent_display_name` and public constant `OVERLAY_NOT_RECORDED_TEXT` are now shared between `approve_frontrunner_proposal` and `app.py`'s render prefetch (F8/F10); `summarize_overlay` now handles nested scale-in overlay structures (F1) and never interpolates a literal `"None"` (F6); `build_proposal_symphony_name`/`_description` handle the real empty-`display_name` production case (F2) and guarantee the safety sentence survives truncation (F7); the retrofit branch's description no longer claims an incumbent relationship it doesn't have (F4); `approve_frontrunner_proposal`'s `metrics_json` guard is hardened against a truthy non-dict (F3). See "Proposal Identity & Provenance"'s "Revise 2" subsection below and `DE-FR-PROPOSAL-IDENTITY-001`'s Revise-2 section in `DECISIONS.md`. Prior: 2026-08-20, original cycle (`DE-FR-PROPOSAL-IDENTITY-001` — three new pure helpers (`build_proposal_symphony_name`, `build_proposal_symphony_description`, `summarize_overlay`) replace the old generic `"Frontrunner Candidate — <hash>"` Composer draft label with an honest identity; `_run_build_for_symphony`'s persist site additively stamps `overlay_tree`/`replaced_node_id`/`overlay_summary` into `metrics_json`; `approve_frontrunner_proposal` resolves the incumbent's display name and calls the new builders. See "Proposal Identity & Provenance" below and `DE-FR-PROPOSAL-IDENTITY-001` in `DECISIONS.md`. Prior: 2026-07-29 (`DE-ADVISOR-CACHE-001` — `_build_generation_prompt(signal_context)` split into two named helpers, `_build_stable_instructional_prefix()` and `_build_signal_context_hints_section(signal_context)`, relocating the volatile watched-tickers/Atlas-patterns/live-edge-signals hints from mid-prompt into a new trailing `"## LIVE SIGNAL CONTEXT"` section so `generate_candidate_overlay` can wrap the now-contiguous stable prefix in an ephemeral `cache_control` breakpoint; the concatenation is round-trip byte-identical to the pre-reorder prompt — see "Prompt Caching" under Internal Mechanics below). Prior: 2026-07-16 (G2 team cycle: the AC-5 signal-gating functions landed GREEN at unit-test level at `bf6f026b`, WIRED into `_run_build_for_symphony` at `95dac72c` (Cluster D), then the operator's de-productization ruling removed the classification-persistence half of that wiring the same day (AC-R2, commit `6715d654`) — the in-memory half (classify/veto/prompt-injection/provenance) was explicitly kept (AC-R3) and remains wired; see "AC-5: Signal-Gated Generation (in-memory, no persistence)" below and `DE-FR-SIGNALS-001` in `DECISIONS.md` for the full account. Also this cycle: the Gate#2 fold-vs-full baseline defect fixed to a fold-matched comparison (AC-G2-1, `570fd6fa`) plus a fix-introduced thin-incumbent regression caught and fixed (AC-G2-6, `cbacb678`) — see "Gate#2 like-for-like fold baseline" below; prior: Wave-2 UI shipped in-branch at `eb1b612` -- see DE-FRONTRUNNER-002 and "Wave-2 UI (built, 2026-07-11)" below; Wave-1 backend, frreview-APPROVED, P2-1 iterative-traversal hardening landed at `26c1364`)

## Overview

`advisors/frontrunner_builder.py` is the orchestration layer of the Frontrunner Builder (feature-plans/frontrunner-builder.md). Per live symphony, the pipeline is:

**detect** (`frontrunner_detector`) → **gather Atlas patterns** (`community_strats`, 7-day cache, once per run) → **generate** a candidate overlay via **Fable** (`claude-fable-5`) → **compile** (`plan_tree_compiler`) → **splice** into the incumbent symphony → **independently re-backtest BOTH incumbent and candidate** (`composer_backtest_client`) → **gate** (`backtest_gate_engine.evaluate_candidate_batch`, mandatory, never bypassed) → **Calmar acceptance** (`frontrunner_acceptance`) → **queue for operator approval** (`database.insert_frontrunner_proposal`).

This module is the **backend** (wave-1). The pipeline is wired into the existing **weekly** scheduler (`advisors/strategy_builder_scheduler.run_weekly_build` calls `run_frontrunner_build()` over all live symphonies after the four Strategy-Builder objectives complete, AC-1) and the `propose_strategies` **retrofit** queues its own accepted candidates onto the same `frontrunner_proposals` table (`proposal_source="strategy_builder_retrofit"`, AC-10). The on-demand `POST /ai-advisor/frontrunner-builder/run` route, the `/approve`/`/reject` routes, and the Advisor-tab UI that surfaces pending proposals for the operator to Approve/Reject were shipped in wave-2 (2026-07-11, `eb1b612`) -- see "Wave-2 UI (built, 2026-07-11)" below.

**AC-5 signal-gating status (frontrunner-signals cycle, updated post-wiring and post-de-productization):** the six functions below (see "AC-5: Signal-Gated Generation") landed correct and unit-tested in isolation at `bf6f026b`; fr-review's Cluster-D pass at that commit found ZERO production call sites (documented in full in `DE-FR-SIGNALS-001` as a caught-pre-ship "code ships ≠ feature works" gap). Wiring landed at `95dac72c` — `_run_build_for_symphony` classifies every incumbent's FR-checks against live signal data on every run, gates candidate generation on it, and attaches provenance to accepted candidates. The SAME DAY, the operator's de-productization ruling (AC-R2, `6715d654`) removed the classification-**persistence** half of that wiring (`persist_classification_run`, the classification/run-marker warehouse tables, `resolve_signals_unavailable_marker`, and the dashboard tab section that rendered them) — a one-time PM cull-analysis deliverable had been productized without an ask; see `DE-FR-SIGNALS-001` for the operator's verbatim ruling. The classify/veto/prompt-injection/provenance path (AC-R3) is unaffected and remains live in production.

**Review status:** `frreview` (quant-code-reviewer) reviewed the full `0bcbd1a..4daf0fe` wave-1 backend diff (28 commits, 32 files, +7304/-2) and returned **APPROVE** — no P0/P1 findings. Three non-blocking P2 items were dispositioned before doc-writing: P2-1 (iterative-traversal hardening, landed `26c1364`, see Internal Mechanics below), P2-2 (a landmine comment on the Atlas-hoist call site, landed `07bdc8c`, see Internal Mechanics below), and a pre-existing unrelated test-hygiene item (2 stale skips in `test_community_strats.py`, out of scope for this cycle). The AC-5 signal-gating additions (this cycle) were reviewed by fr-review's Cluster B/C/D pass at `bf6f026b` — Clusters B/C (detector/walk/classifier) PASSED; Cluster D (this module's wiring) was initially BLOCKED pending a fix, then landed at `95dac72c` and reached production. g2-review separately APPROVED the AC-G2-1..6 Gate#2 fix (`570fd6fa`, `cbacb678`) and the AC-R1..R5 de-productization rip (`6715d654`) — see `DE-FR-SIGNALS-001` for both verdicts.

Off-execution-path, advisory-only. Never raises anywhere on the module's public surface (D-1) — a per-symphony or per-candidate failure is logged and skipped, never aborting the batch.

### No-auto-trade boundary (structural)

This module never calls `composer_draft_client.save_symphony` from the unattended build/run path (`run_frontrunner_build` → `_run_build_for_symphony`) — **only** `approve_frontrunner_proposal`, the operator-driven approval function, may do that. It does not implement, import, or reference `invest_in_symphony` or any `/deploy/` endpoint. Enforced both structurally (by omission — see `advisors/composer_draft_client.py`) and by an adversarial source-scan security suite, `tests/security/test_frontrunner_no_trade_boundary.py` (10 tests), which fails if a future edit reintroduces an invest/deploy-shaped symbol, URL fragment, or a run-path call to `save_symphony`. **This boundary matters continuously**, including now that a route exists: the weekly scheduler is live-wired and calls `run_frontrunner_build()` unattended every week -- a candidate reaching the approval queue never auto-uploads. Nothing uploads to Composer without a human calling `POST /ai-advisor/proposal/approve` (wave-2, `app.py`), which is the only route in the app that calls `approve_frontrunner_proposal` -- see [app.md §Frontrunner Builder Routes](app.md) for the route-level contract. **`DE-ADVISOR-CACHE-001` (2026-07-29) reordered prompt TEXT only** — the cache-control cycle touches `_build_generation_prompt`/`generate_candidate_overlay`'s SDK-call construction exclusively; it never touches `composer_draft_client`, `approve_frontrunner_proposal`, or any write-path code, and the adversarial no-trade-boundary suite stays green, unmodified.

## Named Constants

| Name | Value | Purpose |
|------|-------|---------|
| `FABLE_MODEL` | `"claude-fable-5"` | Model used for candidate generation — operator directive |
| `MAX_OUTPUT_TOKENS` | `8192` | SDK call token ceiling; deliberately smaller than `build_plan_generator`'s (a single overlay is a small DSL fragment vs a full-symphony plan) |
| `MAX_GENERATION_ATTEMPTS` | `3` | Bounded retry for a truncated (`stop_reason="max_tokens"`) or rejected/degenerate candidate (AC-11) |
| `MAX_CASCADES_PER_SYMPHONY_RUN` | `40` | AC-12 Fable-call budget cap per symphony run. Verified against the 11 real trees (observed cascade counts: `{26, 12, 8, 4, 4, 3, 2, 1, 1, 1, 0}`, max=26); 40 gives ~1.5x headroom above the real max while still bounding a pathological/mis-parsed detection. Cascades beyond the cap are skipped with a logged reason, never silently dropped |
| `MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW` | `25` | AC-12 self-imposed runaway-creation safety valve on the approval→Composer-create path. **Not a Composer limit** — Composer documents no per-account symphony-count cap or create-time quota (Tier-1 OpenAPI + Tier-2 MCP + help-center triangulation, `composer-api-researcher`, 2026-07-11); `fetch_symphony_stats` is DEPLOYED-scoped and cannot see the undeployed symphonies this feature creates, so it cannot serve as the guard's denominator |
| `_DOF_LEDGER_SPEC_BUNDLE_SENTINEL` | `"frontrunner_builder"` | Belt-and-suspenders audit-legibility marker on DoF-ledger rows — **not** the isolation mechanism (see DoF-Ledger Isolation below) |
| `_TREE_SPLICE_PANEL_PARAMS_SENTINEL` | `{"tree_splice_candidate": 1.0}` | Identical non-empty param dict passed as candidate/incumbent/theory-prior params to the shared gate's discretionary panel — see Gate-Reachability Fix below |
| `MAX_PROPOSAL_NAME_CHARS` | `100` | AC-1 bound on the Composer draft `name` string built by `build_proposal_symphony_name`. Only `display_name` is ever truncated (with a trailing `…`) — the `#{proposal_id})` suffix always survives intact |
| `MAX_PROPOSAL_DESCRIPTION_CHARS` | `1000` | AC-2 bound on the Composer draft `description` string built by `build_proposal_symphony_description` — self-imposed only, NOT verified against Composer's real field limits (F11, Revise 2, accepted-not-blocking — that verification stays under the existing operator-gated task-zero live-create test); the truncation-with-safety-sentence-preserved restructure (F7, Revise 2) means this bound now caps everything EXCEPT the final safety sentence, which always survives intact — see "Proposal Identity & Provenance"'s Revise-2 subsection |
| `OVERLAY_NOT_RECORDED_TEXT` | `"overlay not recorded for this proposal"` | F10 (Revise 2). Public (no leading underscore). The single source of truth for the AC-6 legacy-row honest-degrade phrase — consumed by `build_proposal_symphony_description` AND, via a threaded template context variable (`overlay_not_recorded_text`), `templates/ai_advisor.html`, closing a drift risk between what were previously two independently-hardcoded copies of the same string. No trailing period — callers append their own sentence-ending punctuation |
| `_MAX_DISPLAY_NAME_CHARS_IN_DESCRIPTION` | `200` | F7 (Revise 2). Bounds `display_name` independently before embedding it in `build_proposal_symphony_description`'s assembled text — the name builder already bounded `display_name` (via `MAX_PROPOSAL_NAME_CHARS`), the description builder previously did not |
| `_SAFETY_SENTENCE` | `"Undeployed candidate — review before investing."` | F7 (Revise 2). The universal safety sentence every description carries; built and appended LAST, after truncating everything else to fit, so it is guaranteed to survive intact — see `build_proposal_symphony_description`'s "Revise 2" note below |

## Public Types

### `GenerationResult` (dataclass)

Returned by `generate_candidate_overlay`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `candidate` | `dict \| None` | The accepted build-plan-DSL overlay node (`kind="if"`/`"if_compound"`), or `None` if rejected/failed |
| `error` | `str \| None` | Reason string on rejection/failure (D-1: `type(exc).__name__` on an internal error). `None` on success |
| `compiled_tree` | `dict \| None` | The compiled Composer tree via `plan_tree_compiler.compile_plan`, when compilation succeeded |

### `ApprovalResult` (dataclass)

Returned by `approve_frontrunner_proposal`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `success` | `bool` | `True` only when the Composer create succeeded AND the created symphony verified zero-allocation |
| `symphony_id` | `str \| None` | The newly-created (or previously-created, on idempotent re-approve) Composer symphony id |
| `error` | `str \| None` | Reason string on failure (D-1) |

## API Reference

### `_build_stable_instructional_prefix() -> str`

**New (`DE-ADVISOR-CACHE-001`, 2026-07-29).** Returns the genuinely invariant leading span of the overlay-generation prompt: the intro line, the 5 numbered HARD REQUIREMENTS, the node-shape/tool-usage instructions, and both worked JSON examples (`_EXAMPLE_OVERLAY`, `_EXAMPLE_COMPOUND_OVERLAY`). Contains ZERO `signal_context`-derived content by construction — this is the exact span `generate_candidate_overlay` wraps in a `cache_control` breakpoint. Byte-identical to the corresponding leading+trailing spans of the pre-reorder `_build_generation_prompt` output concatenated together (pinned by `tests/fixtures/frontrunner_builder/generation_prompt_stable_content_baseline.json`).

**Returns:** `str` — no parameters; the same string every call, by design.

---

### `_build_signal_context_hints_section(signal_context: dict) -> str`

**New (`DE-ADVISOR-CACHE-001`, 2026-07-29).** Returns the volatile per-symphony trailing section: watched core signal tickers, Atlas-derived frontrunner patterns, and live positive-edge frontrunner signals — the same three hints that were previously interpolated MID-prompt (between the hard-requirements block and the node-shape description). Relocated here, under a `"## LIVE SIGNAL CONTEXT"` header, so `_build_stable_instructional_prefix()` can form a genuinely `signal_context`-independent cache prefix. Mirrors `build_plan_generator._build_generation_prompt`'s `"## OPERATOR CONTEXT"` append pattern. Always UNCACHED at the `generate_candidate_overlay` call site.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `signal_context` | `dict` | Same shape as `generate_candidate_overlay`'s parameter: `watched_tickers` (list), optional `atlas_patterns` (list), optional `edge_signals` (dict) |

**Returns:** `str` — the trailing section text (starts with `"\n\n## LIVE SIGNAL CONTEXT\n\n"`).

---

### `_build_generation_prompt(signal_context: dict) -> str`

Build the SDK prompt for candidate overlay generation. Now simply `_build_stable_instructional_prefix() + _build_signal_context_hints_section(signal_context)` (`DE-ADVISOR-CACHE-001`) — a round-trip-verified concatenation, byte-identical to the pre-reorder producer's output for the same inputs. `generate_candidate_overlay` still calls this function directly (rather than calling the two helpers itself) so the existing seam `test_frontrunner_builder_signal_wiring.py` spies on is preserved — see the "Prompt Caching" note under Internal Mechanics for the mid-cycle regression this preserved seam caught.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `signal_context` | `dict` | `watched_tickers` (list, the incumbent cascade's core signal tickers) + optional `atlas_patterns` (list, AC-3) + optional `edge_signals` (dict, AC-5) |

**Returns:** `str` — the full prompt string; unchanged in content from before `DE-ADVISOR-CACHE-001`, only its internal construction is now two named pieces instead of one inline builder.

---

### `generate_candidate_overlay(signal_context: dict, *, n_attempts=MAX_GENERATION_ATTEMPTS) -> GenerationResult`

Calls Fable (tool-use, `emit_frontrunner_overlay` tool, forced `tool_choice`) to compose one candidate frontrunner overlay DSL node, enforces AC-4's post-generation hard constraints, and compiles it. Never trusts the model's raw output.

**`DE-ADVISOR-CACHE-001` (2026-07-29):** the SDK-call `messages=` payload is now a 2-block content array instead of a bare string — `[{"type":"text","text":stable_prompt,"cache_control":{"type":"ephemeral"}}, {"type":"text","text":hints_section}]`. `stable_prompt` comes from `_build_stable_instructional_prefix()` directly; `hints_section` is derived via `full_prompt[len(stable_prompt):]` where `full_prompt` is `_build_generation_prompt(signal_context)`'s own return value — a byte slice against an independently-computed prefix, never a hand-duplicated section. Real `count_tokens` measurement: 1,730 tokens on the messages block alone (just under the model's 2,048-token floor); combined with the static `_EMIT_OVERLAY_TOOL` schema (which also gains `cache_control` and renders before `messages` in the same cached prefix), approximately 2,650-2,750 tokens — clears the floor via tools+messages together, not messages alone. See "Prompt Caching" under Internal Mechanics for the full mechanics and the mid-cycle regression this change caught.

**Enforced post-generation (AC-4):**
- **(a) VIX presence:** `_has_vix_ticker_in_fire_branch` — the candidate's `then` (fire) branch, or a nested tier's own `then` branch, must contain >=1 VIX-family ticker (`frontrunner_detector.VIX_FAMILY_TICKERS`, imported not duplicated). A candidate that fails this check is rejected and retried (bounded).
- **(c) Mergeable-rung collapse:** `_collapse_mergeable_rungs` walks nested `if` chains sharing an identical `(fn, comparator, rhs_fixed, window)` signature AND identical fire content, differing only in `lhs_ticker`, and collapses a chain of length >=2 into one `if_compound` node with a `binary_compound` `"any"` condition. A genuine scale-in tier (different threshold or different fire content per level) never matches this signature and is left untouched.
- **(d) Scale-in tiers preserved:** guaranteed by construction — the collapse function only touches structurally-identical chains.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `signal_context` | `dict` | `watched_tickers` (list, the incumbent cascade's core signal tickers) + optional `atlas_patterns` (list, AC-3) |
| `n_attempts` | `int` | Bounded retry count (default `MAX_GENERATION_ATTEMPTS`) |

**Returns:** `GenerationResult` — never raises (D-1).

**Degradation paths:** `"max_tokens: response truncated"` (all attempts truncated) · `"NoToolUseBlock"` · `"InvalidToolUsePayload"` · `"candidate fire branch contains no VIX-family ticker"` (AC-4a rejection, retried) · `type(exc).__name__` on any unexpected exception.

---

### `splice_candidate_into_symphony(incumbent_symphony: dict, incumbent_cascade, candidate: dict) -> dict | None`

Replaces the detected incumbent cascade subtree (identified by `incumbent_cascade.overlay_tree`'s node `id`) with the candidate, inside a full copy of the incumbent symphony (AC-5).

Accepts either a raw build-plan-DSL node (`"kind"` key — compiled here via `plan_tree_compiler.compile_plan`) or an already-compiled Composer node (`"step"` key — used as-is). Re-validates the spliced result via `symphony_schema.validate_tree` before returning.

**Returns:** the full spliced symphony dict, or `None` on any structural failure (node not found, compile failure, validation errors). Never raises.

---

## Proposal Identity & Provenance (`DE-FR-PROPOSAL-IDENTITY-001`, 2026-08-20)

A frontrunner proposal's `candidate_tree` is, by design, a FULL standalone copy of the incumbent symphony with one detected cascade replaced by a generated overlay (`splice_candidate_into_symphony`, above). Before this cycle, every Composer-facing surface named the artifact as if it were a bare frontrunner fragment — the uploaded draft's name was the generic `"Frontrunner Candidate — <hash>"` and its description was a static one-liner, disclosing neither the full-copy nature of the artifact nor what the generated overlay actually does. The operator (2026-08-20, verified against 3 live droplet rows — all `proposal_source='frontrunner_builder'`, same incumbent, ~186KB candidate_tree each) ruled this misleading. These helpers, plus three additive `metrics_json` keys and matching dashboard-card render changes (see [app.md](app.md) and `templates/ai_advisor.html`'s Key-Files row), make every proposal's identity honest end to end — the Composer draft name/description, and the dashboard cards — with zero change to detection, splice, gating, or acceptance math (AC-8).

**Revise 2 (same day, PR #126's `/code-review` gate).** The independent `/code-review` skill returned 11 fix-before-merge findings (F1-F10, F12; F11 accepted-not-blocking, informational) against the original cycle's diff. F1-F8/F10/F11 landed in this module (`916e08d5`); F5/F9 landed self-contained in `app.py`'s render prefetch (`292d543a`); F8/F10's dashboard-side consumer wiring landed in `app.py`/`templates/ai_advisor.html` (`b8ba8085`). F12 (a CLAUDE.md wording correction, not a code change) is tracked in `DECISIONS.md` only. Each finding is documented inline at its fix site below and summarized in `DE-FR-PROPOSAL-IDENTITY-001`'s "Revise 2" section in `DECISIONS.md`.

### `resolve_incumbent_display_name(bot_state: dict, symphony_id: str) -> str`

**New (F8, Revise 2, 2026-08-20).** Pure, no I/O, never raises. The hash→name lookup against `bot_state`: a match returns the live `name` field, an unresolvable `symphony_id` (or a non-`dict` `bot_state`) falls back to `symphony_id` itself — never fabricates, never blanks. Public (no leading underscore) so `app.py` can import it directly. The SINGLE shared source of truth for this lookup — before Revise 2, `approve_frontrunner_proposal` and `app.py`'s `ai_advisor_tab()` prefetch loop each hand-rolled their own copy of this exact logic, an honesty-drift risk between the Composer-upload identity and the dashboard-card identity closed by extracting it here.

### `build_proposal_symphony_name(display_name: str, proposal_id: int, source: str) -> str`

AC-1/AC-7. Pure, no I/O. The Composer-facing symphony `name` for an approved proposal. Two locked formats keyed by `source`:

- `"frontrunner_builder"` (default/else branch): `"{display_name} + FR overlay (full copy, #{proposal_id})"`.
- `"strategy_builder_retrofit"`: `"Strategy Builder candidate for {display_name} (from scratch, #{proposal_id})"`.

Bounded to `MAX_PROPOSAL_NAME_CHARS` (100) via `_truncate_display_name_to_fit` — only `display_name` is ever shortened (with a trailing `…`); the `#{proposal_id})` suffix always survives intact, guaranteeing uniqueness even for two proposals from the same run against the same incumbent (the operator's real 2026-08-03 rows were 2 minutes apart). Does NOT resolve hash→display-name itself — that is the caller's job (`approve_frontrunner_proposal`, below, via `resolve_incumbent_display_name`).

**F2 (Revise 2):** a falsy (empty-string) `display_name` — a genuine production condition, since `strategy_builder_scheduler` keys every observation to `symphony_id=""` — omits the name-slot segment entirely (`"FR overlay (full copy, #{id})"` / `"Strategy Builder candidate (from scratch, #{id})"`) rather than rendering a blank identity slot with a leading/double space.

### `build_proposal_symphony_description(*, display_name, incumbent_hash, proposal_id, created_at, source, replaced_node_id=None, overlay_summary=None) -> str`

AC-2/AC-6/AC-7. Pure, no I/O. The Composer-facing `description`. Always carries `display_name`/`incumbent_hash`/`proposal_id`/`created_at` plus the universal `_SAFETY_SENTENCE` ("Undeployed candidate — review before investing.").

- `source="strategy_builder_retrofit"`: appends "From-scratch Strategy Builder candidate — does not contain the incumbent's logic." — the full-copy sentence never appears (would be false for a from-scratch candidate).
- `source="frontrunner_builder"`: appends either `"Replaces node {replaced_node_id}: {overlay_summary}."` (bounded to `_MAX_OVERLAY_SUMMARY_CHARS_IN_DESCRIPTION=300` chars) when both fields are present, or the shared `OVERLAY_NOT_RECORDED_TEXT` fallback (AC-6, legacy rows) when either is `None` — followed **unconditionally** by "Full standalone copy — the original symphony is not modified." **Deviation from the plan's original AC-2 wording, locked at implementation (`65738df0`):** the plan's Architecture section implied this full-copy sentence should gate on the overlay metrics being present; the shipped behavior makes it unconditional, because the full-copy property is a fact about the SPLICE itself, not about whether AC-3's identity metrics were recorded — a legacy row predating this feature is still a genuine full spliced copy, and gating the sentence on the metrics would incorrectly imply otherwise. A dedicated regression test locks this (`65738df0`, `test_frontrunner_proposal_identity.py`).

**Revise 2 additions:**
- **F4 — first sentence is source-branched.** The retrofit branch's first sentence is now `"Strategy Builder candidate for {display_name}, proposal #{id}, created {created_at}."` — it no longer reuses the `frontrunner_builder` branch's `"Frontrunner Builder candidate for ... (incumbent {hash})..."` opener, which falsely implied an incumbent relationship a from-scratch candidate doesn't have.
- **F2 — empty `display_name` handled** in both branches' first sentence, mirroring the name-builder's fix: the `"for {display_name}"` clause is omitted entirely (never a blank `"for , proposal..."`) when `display_name` is falsy.
- **F7 — bounded independently + safety-sentence-last restructure.** `display_name` is now bounded to `_MAX_DISPLAY_NAME_CHARS_IN_DESCRIPTION=200` chars (with `…`) BEFORE embedding — previously only the name builder bounded it, not this one. The assembly order is inverted: every OTHER part (`body`) is joined and truncated to fit within `MAX_PROPOSAL_DESCRIPTION_CHARS − len(_SAFETY_SENTENCE) − 1`, and `_SAFETY_SENTENCE` is appended LAST — guaranteeing `description.endswith(_SAFETY_SENTENCE)` for every proposal, regardless of how long `display_name`/`overlay_summary` are. The old code appended the safety sentence FIRST in the parts list and then truncated the fully-assembled string from the tail with a bare slice — a sufficiently long `display_name` or `overlay_summary` could chop the safety sentence off entirely, silently dropping the one sentence every description is guaranteed to have.

Bounded to `MAX_PROPOSAL_DESCRIPTION_CHARS` (1000) — post-F7, this bound caps everything EXCEPT the final safety sentence, which always survives.

### `summarize_overlay(overlay_tree: dict | None) -> str`

AC-3. Pure, no I/O, never raises (internal `try/except` degrades to the fallback on any unexpected shape). Builds a short, specific, human-readable one-liner describing what a generated overlay DOES — e.g. `"relative-strength-index(SPY,10) gt 30 rotates into VIXY"` — by reading the overlay's flat `condition` (requires an `"lhs_fn"` key, and — Revise 2, F6 — a non-`None` `"window"` and a non-`None` `rhs["fixed"]`, both now REQUIRED rather than optional-with-a-literal-`"None"`-fallback) plus the first asset ticker found in its `"then"` branch (`_find_first_asset_ticker`, handles `scheme="equal"`'s bare-list-of-assets shape, `scheme="specified"`'s `{"node":..., "pct":...}`-wrapper shape, and — Revise 2, F1 — a nested `if`/`if_compound` node's own `"then"` then `"else"` branches). Any other shape — compound condition, missing/malformed `condition`, missing `window`/`rhs["fixed"]`, non-dict input, `None` — degrades to the exact literal `_OVERLAY_SUMMARY_FALLBACK = "compound condition overlay"`, never a fabricated guess.

**F1 (Revise 2) — nested scale-in overlays now summarized, not degraded.** A 2-tier scale-in overlay — the generation prompt's own flagship worked example (HARD REQUIREMENT #4: a nested `if`-node inside the outer node's `"then"` branch) — nests ANOTHER `{"kind":"if"/"if_compound", "then":[...], "else":[...]}` node inside a `then`/`else` list. That nested node has neither `"children"` nor `"node"`, so before this fix `_find_first_asset_ticker` returned `None` for it and `summarize_overlay` silently fell through to the generic fallback for every real scale-in overlay — the specific-summary path was unreachable for the exact shape the generation prompt is designed to produce. Fixed by explicitly descending into a nested `if`/`if_compound` node's own `"then"` (then `"else"`) when encountered, same "first asset leaf found by walking, however nested" contract.

**F6 (Revise 2) — `window`/`rhs["fixed"]` required, never a literal `"None"`.** Before this fix, a missing `condition["window"]` or `rhs["fixed"]` fell through to `summarize_overlay`'s f-string interpolation, which would render the literal text `"None"` inside an otherwise-plausible-looking summary (e.g. `"relative-strength-index(SPY,None) gt 30 rotates into VIXY"`) instead of degrading to the honest fallback. Both are now required — non-`None` — before the specific-summary path is taken.

**Operates on the raw pre-compile/pre-graft build-plan-DSL overlay node** (`GenerationResult.candidate`'s shape, `kind="if"`/`"if_compound"`) — the same object `_run_build_for_symphony`'s persist site stores as `metrics_json["overlay_tree"]` (see below), never the post-splice `spliced` tree (whose `else` branch embeds the entire incumbent core — persisting or summarizing that would recreate exactly the misleading-identity problem this feature exists to fix).

### Persist-site wiring (`_run_build_for_symphony`)

Additive, zero change to `metrics`'s existing keys. Right before the existing `database.insert_frontrunner_proposal` call, three keys are stamped from values already in scope in the same loop iteration (`cascade`, `result`):

```python
metrics["overlay_tree"] = result.candidate          # pre-compile/pre-graft DSL node — NOT `spliced`
metrics["replaced_node_id"] = (
    cascade.overlay_tree.get("id") if isinstance(cascade.overlay_tree, dict) else None
)
metrics["overlay_summary"] = summarize_overlay(result.candidate)
```

No schema migration — `metrics_json` is already free-form JSON (per the plan's Decisions table: additive-first, zero schema risk). A row persisted before this cycle simply lacks these three keys; every consumer (the approve-path description builder, the dashboard card render) treats their absence as the AC-6 legacy-row case, never a crash and never a fabricated value.

### `approve_frontrunner_proposal` — name/description wiring (updated)

`approve_frontrunner_proposal` (documented in full below) now resolves the incumbent's display name via `resolve_incumbent_display_name(database.load_state() or {}, proposal["symphony_id"])` — `database` is already lazy-imported in this function — and calls `build_proposal_symphony_name`/`build_proposal_symphony_description` (reading `replaced_node_id`/`overlay_summary` off `proposal["metrics_json"]`) to build the `save_symphony(name=..., description=...)` call's arguments. This REPLACES the prior hardcoded `name=f"Frontrunner Candidate — {proposal['symphony_id']}"` / `description="Frontrunner Builder candidate (operator-approved)"` pair verbatim — the bare-hash format no longer occurs on any approval, including legacy proposals. Any failure in this resolution block is caught by the function's own outer `try`/`except` (D-1) — an unresolvable/malformed proposal degrades to the existing `error_message`-recorded failure path, never a crash.

**F8 (Revise 2) — shared display-name resolution.** The hash→name lookup itself now lives ONLY in `resolve_incumbent_display_name` (above); this function's previous inline duplicate of the same logic was deleted in favor of calling the shared helper — the identical function `app.py`'s render prefetch also calls.

**F3 (Revise 2) — hardened `metrics_json` guard.** The `metrics_json` read was `proposal.get("metrics_json") or {}`, which silently does NOT fire on a TRUTHY non-`dict` value (e.g. a stored JSON array) — the subsequent `.get()` calls then raised `AttributeError`, caught by this function's outer `except`, but WITHOUT first persisting an `error_message` the way every other failure branch does (a silent-ish failure, distinguishable from the other documented failure modes only by an empty `error_message` field). Fixed to `proposal_metrics = raw_metrics if isinstance(raw_metrics, dict) else {}`, mirroring `app.py`'s own equivalent AC-6 guard on the render side.

---

### `run_frontrunner_build(symphony_ids: list[str] | None = None) -> None`

**D-1 never-raises entry point.** Called by `strategy_builder_scheduler.run_weekly_build()` (AC-1, weekly) and by the on-demand `POST /ai-advisor/frontrunner-builder/run` route (wave-2, `app.py`, async-dispatched via a dedicated `_FRONTRUNNER_BUILD_EXECUTOR` -- see app.md). Detects → generates → splices → gates → accepts → queues, for each live symphony. Never calls `composer_draft_client.save_symphony`.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `symphony_ids` | `list[str] \| None` | `None` (default): resolves the roster from live `bot_state` at run time via `_resolve_live_symphony_roster`. Supplied: restricts the build to those ids (used by the on-demand route / tests) |

A per-symphony exception is logged and skipped — it never aborts the batch.

---

### `approve_frontrunner_proposal(proposal_id: int) -> ApprovalResult`

**Operator-approved.** The ONLY function in the whole frontrunner surface that may call `composer_draft_client.save_symphony` (AC-9). Invoked exclusively from the operator-driven `POST /ai-advisor/proposal/approve` route (wave-2, `app.py`, shipped 2026-07-11) -- never from the unattended weekly build path. See [app.md §Frontrunner Builder Routes](app.md) for the route-level contract.

Sequence: look up the `frontrunner_proposals` row → idempotent no-op if already `uploaded` → **AC-12 local-count guard** (`database.count_uploaded_frontrunner_proposals()` against `MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW`; fails **closed** — refuses to create — both when the cap is reached and when the count itself can't be determined) → `composer_draft_client.save_symphony(...)` → **AC-9 belt-and-suspenders**: `composer_draft_client.verify_undeployed(symphony_id)` before marking `'uploaded'` → persists an `advisor_observations` audit row on success.

On ANY failure (proposal not found, cap reached, Composer 4xx/5xx, `verify_undeployed` returning `False`), the proposal is left un-marked `'uploaded'` and the failure reason is written to `frontrunner_proposals.error_message` (AC-11: "do NOT mark uploaded, do NOT retry blindly").

**Idempotent:** re-approving an already-`uploaded` proposal is a no-op that echoes the recorded `symphony_id` — no duplicate Composer symphony is created, via `save_symphony`'s own `already_uploaded_symphony_id` seam.

**Returns:** `ApprovalResult`. Never raises.

**Shared by both proposal sources.** `frontrunner_proposals.proposal_source` distinguishes `'frontrunner_builder'` rows (this module's own pipeline) from `'strategy_builder_retrofit'` rows (`advisors/strategy_builder_engine.py::_persist_survivor`, AC-10) -- both flow through this exact same function, so there is one approval→create code path for the whole feature, not two.

## AC-5: Signal-Gated Generation (in-memory, no persistence — frontrunner-signals cycle, 2026-07-16)

**Status (updated post-wiring, post-de-productization):** all five functions below (a sixth, `resolve_signals_unavailable_marker`, was REMOVED — see the note at the end of this section) are implemented, unit-tested (`tests/advisors/test_frontrunner_builder_signal_gating.py`, 9/9 GREEN post-AC-R4 trim, plus `test_frontrunner_builder_signal_wiring.py`, 5/5, for the integration path), and WIRED into `_run_build_for_symphony`. On every build run: the incumbent's `extract_fr_checks` output is classified against live Atlas signal data in memory (`_build_classification_rows_from_fr_checks`, AC-4), candidate generation is gated on positive-edge keys and a Tier-1 remove-key veto, and accepted candidates carry signal provenance in their `metrics_json`. **What does NOT happen:** the classification result is never persisted to a database table and never rendered on a dashboard tab — that layer (built at `bf6f026b`, wired at `95dac72c`) was removed the same day per the operator's de-productization ruling (AC-R2, `6715d654`, `DE-FR-SIGNALS-001`) — it had productized a one-time PM deliverable (the cull analysis) that was never asked for as a product feature.

Candidate frontrunner generation consumes live Atlas signal edge data (`docs/generated/advisors_frontrunner_signals.md`) rather than generating structurally-only. Five functions remain, all in this module per the Architecture doc:

### `filter_positive_edge_signal_keys(classification_rows: list[dict]) -> list[str]`

AC-5(a). Returns only the `fr_key`s classified `"keep"` — the sole positive-edge tier (`cagr>0 and sharpe>0` by `classify_fr_checks`' construction). Candidate generation may only propose from these keys. Never raises — malformed rows are skipped, never fabricated.

### `candidate_contains_tier1_remove_key(candidate: dict, classification_rows: list[dict]) -> bool`

AC-5(b). `True` if the candidate's watched `fr_key` is classified `"remove"` (Tier 1) in the current classification snapshot — the caller vetoes the candidate BEFORE any backtest spend. Pure lookup, no I/O — never calls the backtest seam itself.

### `build_signal_provenance(fr_keys: list[str], classification_rows: list[dict]) -> dict[str, dict]`

AC-5(c). `{fr_key: {cagr, sharpe, classification, ...}}` for the given `fr_keys`, sourced from the current classification snapshot — the provenance record attached to each candidate. Keys absent from `classification_rows` are simply omitted, never fabricated.

### `resolve_signals_unavailable_marker` — REMOVED (AC-R2, `6715d654`)

This function (and its `signals_unavailable`/`reason` marker-persistence caller) was removed by the de-productization rip — its only caller was the removed `persist_classification_run` call site. If a future cycle needs a per-symphony signal-availability check again, re-derive it from `load_frontrunner_signals()`'s own `available`/`reason` return fields rather than resurrecting this function verbatim (its removal was reviewed and approved, not accidental). See `DE-FR-SIGNALS-001` for the full account.

### `format_crossover_fr_key(*, ticker, window, rhs_fn, rhs_val) -> str` / `format_vs_fr_key(*, ticker, window, rhs_ticker) -> str`

PM-ruling display-identity formatters — crossover and ticker-vs-ticker `FRCheck`s (`fr_key=None` in the pure dataclass, see `docs/generated/advisors_frontrunner_detector.md`'s `FRCheck` invariant) are designed to be PERSISTED and RENDERED, never silently dropped: `SPY:10:xover(moving-average-return,31)` / `EYEG:10:vs(KMLM)`. Both formats live in ONE place (containment rule — no ad-hoc format ever appears elsewhere) with a documented non-collision invariant: a genuine Atlas `fr_key`'s third segment is always a plain number; both display forms always contain a letter + parenthesis, a structurally disjoint string space. See `DE-FR-SIGNALS-001` for the full rationale (the Paragons misdiagnosis this rule exists to prevent from recurring) — note that after the discriminator's Stage-2 correction, `format_crossover_fr_key`'s numeric-`rhs_val` case is structurally defined but has NO confirmed live occurrence in the operator's real trees (see the detector doc's discriminator section); `format_vs_fr_key` is the one confirmed-populated non-joinable form. **These formatters ARE called** — from `_build_classification_rows_from_fr_checks`, on every build run, to build the in-memory `classification_rows` list a crossover/`vs()` FRCheck is represented in (never silently dropped). Neither formatter's output is persisted to a database table anymore — the classification-row warehouse tables (`frontrunner_classification_snapshots`/`frontrunner_run_metadata`) were removed by AC-R2 (`6715d654`).

### `build_classification_row_for_crossover(*, ticker, window, rhs_fn, rhs_val, branch_path) -> dict`

Builds a `classification_rows`-shaped dict for a crossover `FRCheck`. Always `classification="no_edge_data"` (nothing to join a crossover key to), every edge stat `None`. Called from `_build_classification_rows_from_fr_checks` on every build run — its result feeds the in-memory `classification_rows` list only, never persisted.

## Internal Mechanics

### Prompt Caching (`DE-ADVISOR-CACHE-001`, 2026-07-29)

**Real `count_tokens` measurement:** the messages block alone is 1,730 tokens — just UNDER the model's 2,048-token minimum-cacheable-prefix floor. Combined with the static `_EMIT_OVERLAY_TOOL` tool schema (Anthropic renders `tools` before `messages` in the same cached prefix, and this cycle also marks the tool with `cache_control`), the combined prefix is approximately 2,650-2,750 tokens — clearing the floor via tools+messages TOGETHER, not messages alone. This is the ground truth that drove the team-lead's scope decision to include this site (alongside `build_plan_generator.py`) among the 2 real breakpoints this cycle, out of the 7 advisor SDK call sites measured — see `DE-ADVISOR-CACHE-001` in `DECISIONS.md` for the full record across all 7.

**The reorder is minimal and content-preserving.** `_build_generation_prompt(signal_context)`'s pre-cycle order was: intro + HARD REQUIREMENTS (stable) → watched/Atlas/edge-signal hints (VOLATILE) → node-shape description (stable) → both worked examples (stable). The volatile hints block sat in the MIDDLE, breaking prefix contiguity for the stable content that surrounds it. The fix relocates ONLY that hints block — to a new trailing `"## LIVE SIGNAL CONTEXT"` section — leaving the hard-requirements text, the node-shape description, and both examples in their original relative order; they become a contiguous cacheable prefix purely as a side effect of the volatile block's removal from the middle. This is a strictly smaller diff than an earlier (rejected) framing that would have also moved the two examples up next to the hard-requirements block — unnecessary, since the node-shape description and examples were already contiguous with each other and needed no independent move.

**Round-trip byte-preservation.** `_build_stable_instructional_prefix() + _build_signal_context_hints_section(signal_context)` reconstructs `_build_generation_prompt`'s return value exactly for any given `signal_context` — pinned by `tests/fixtures/frontrunner_builder/generation_prompt_stable_content_baseline.json`, a fixture capturing the pre-reorder producer's output for round-trip comparison against the reordered two-helper reconstruction.

**Mid-cycle regression, caught and fixed.** The first GREEN draft's `generate_candidate_overlay` called the two new helpers directly, bypassing `_build_generation_prompt` entirely — this broke `test_frontrunner_builder_signal_wiring.py`'s existing spy on `_build_generation_prompt` (the AC-5 integration test suite observes that exact function to verify the signal-gating wiring). Fixed by keeping `generate_candidate_overlay`'s call to `_build_generation_prompt` (so the spied-on seam stays intact) and deriving the `cache_control` split via a byte slice of its return value against the independently-computed `_build_stable_instructional_prefix()` output, rather than bypassing the function. This is the reason `_build_generation_prompt` still exists as a thin two-line wrapper rather than being deleted in favor of the two helpers.

**No-trade-boundary guard.** This is purely a prompt-text reorder inside the generation call — it never touches `composer_draft_client`, `approve_frontrunner_proposal`, or any Composer-write code, and the adversarial `tests/security/test_frontrunner_no_trade_boundary.py` suite stays green, unmodified. The reordered candidate still passes through all four existing downstream gates (BHY/FDR, PBO veto, Calmar acceptance, operator-approval) before an operator ever sees it — see AC-4 in `DE-ADVISOR-CACHE-001`.

**The real proof is a live probe, not tests-green.** Every test here mocks `_build_client`/`messages.create` and can only confirm the request SHAPE. None can prove Anthropic actually cached anything — the PM's live cache-hit probe (a separate, non-mocked API round-trip) targets `build_plan_generator.generate_build_plans`, not this site, per `DE-ADVISOR-CACHE-001`'s AC-9.

### Batch-composition fix (2026-07-11)

The inherited WIP had put BOTH the incumbent and the candidate into the same `evaluate_candidate_batch` call — making them rivals for the single BHY/FDR-winner slot instead of candidate-vs-baseline (both landed `fdr_not_winner`, gate structurally dead). Fixed in `_gate_and_accept_candidate`: the batch (`bt_candidates`) carries **only** the candidate; the incumbent's own fresh backtest supplies `incumbent_oos_alpha` as the scalar `KEEP_INCUMBENT` baseline — the same shape as `strategy_builder_engine.propose_strategies`'s established usage.

### Gate-reachability fix — `_TREE_SPLICE_PANEL_PARAMS_SENTINEL`

`backtest_gate_engine`'s discretionary panel (`_compute_parameter_stability_score`/`_compute_prior_anchor_score`) was designed to compare an Optuna-tuned candidate's parameter vector against the incumbent's. A tree-splice candidate has none — passing **empty** `candidate_params`/`incumbent_params` was structurally disadvantageous, not neutral: the incumbent's own `inc_stability` is hardcoded to `1.0` ("stable against itself") while an empty-input pair falls back to the `0.5` neutral-prior short-circuit, giving the incumbent an unwinnable floor (`0.5` candidate panel score vs `0.75` incumbent, `margin 0.5>=0.75` mathematically impossible regardless of return quality — verified via a direct `evaluate_candidate_batch` probe). The feature would have shipped hollow (gate rejects all candidates in prod), caught pre-ship by `frtest`.

**Fix:** pass an **identical non-empty** dict (`_TREE_SPLICE_PANEL_PARAMS_SENTINEL`) for `candidate_params`/`incumbent_params`/`theory_prior_params` — every parameter-distance sub-score resolves to a genuine 1.0/1.0 N/A-tie, so the panel becomes a neutral pass-through for tree-splice candidates. The real vetoes (BHY/FDR significance, PBO, OOS-alpha-beats-both-baselines) remain fully load-bearing and unaffected. **PM-verified byte-unchanged for shared code:** `git diff f51cffe 8d0b18d -- acceptance_gate.py advisors/backtest_gate_engine.py autotuner.py` was empty — zero impact on the autotuner/strategy_builder call sites. `frreview` independently traced the same code path and confirmed `ADOPT_CANDIDATE` was provably unreachable pre-fix and that the sentinel produces a genuine tie, not a weakening. See "Gate-Reachability Fix" in `DECISIONS.md`.

### Gate#2 like-for-like fold baseline (AC-G2-1..6, 2026-07-16)

`_gate_and_accept_candidate`'s Gate#2 baseline (`incumbent_oos_alpha`) used to be the incumbent's FULL-series return sum (`sum(incumbent_returns_pct)`), compared against the candidate's `oos_alpha` -- a VALIDATION-FOLD-only sum computed internally by `evaluate_candidate_batch` (~20% of days, `backtest_gate_engine.py:551-552`). That fold-vs-full unit mismatch (~5x bias) systematically favored KEEP_INCUMBENT for any profitable incumbent, regardless of how much better the candidate's per-day return genuinely was -- a real defect, LATENT in production because the real 2026-07-16 run's 115 rejects all died earlier at Gate#1/BHY significance.

**Fix (AC-G2-1):** the incumbent baseline is now computed via `backtest_gate_engine._fold_transform_single(incumbent_returns_pct)` -- the SAME 60/20/20 + PURGE_DAYS/EMBARGO_DAYS fold transform the gate applies internally to the candidate -- so both sides of the Gate#2 comparison are like-for-like validation-fold sums. Reuses an existing seam (`logic_change_engine.py` already imports the same function cross-module for an identical H6/RC-1 defect class); zero diff to `backtest_gate_engine.py`/`acceptance_gate.py`/`autotuner.py`/`math_engine.py`.

**AC-G2-6 (a regression the AC-G2-1 fix itself introduced):** `_fold_transform_single`'s thin-series branch (`<FOLD_TRANSFORM_MIN_TOTAL_DAYS`=65 days) returns a hardcoded `oos_alpha=0.0` sentinel, not a real measurement -- naively adopting it as the incumbent baseline would collapse Gate#2 to a "beat zero" bar for any short-history incumbent, a fail-OPEN mode the OLD full-series-sum code never had. Fixed by reading `incumbent_fold.purge_integrity_ok`/`.thin_window` (the same flags the candidate side already hard-vetoes on) and substituting `float("inf")` for the incumbent baseline when either fires -- forcing conservative KEEP_INCUMBENT, mirroring the existing `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA` edge-14 pattern (`backtest_gate_engine.py:196`). The `+inf` sentinel is a local variable only -- never written into a persisted `metrics_json` dict (pinned by a dedicated `json.dumps(..., allow_nan=False)` test).

Two pre-existing tests (`test_a_gate_and_calmar_surviving_candidate_is_queued_with_metrics` and `test_a_weak_candidate_that_clears_the_fdr_veto_is_still_rejected_on_oos_alpha`) had reverse-engineered assertions against the defective fold-vs-full bar; both were reconciled to the corrected fold-vs-fold semantics (one docstring-only, one fixture-premise-only -- see `DE-FR-SIGNALS-001` for the full account). g2-review APPROVE @ `570fd6fa` (AC-G2-1..5) and APPROVE @ `cbacb678` (AC-G2-6). See "Gate#2 fold-vs-full baseline defect" in `DECISIONS.md`.

### DoF-ledger isolation — `evidence_source="OVERLAY_BACKTEST_SELECTION"`

**This is the actual isolation mechanism** (not the `spec_bundle_id` sentinel). An earlier design attempted isolation via `spec_bundle_id` alone; that was proven false — `database.get_researcher_dof_ledger_for_run` excludes only rows matching the *current run's own winning bundle*, so any other `spec_bundle_id` (including a sentinel) still swept into every symphony's real N_effective, silently inflating the autotuner's BHY/Yekutieli overfitting haircut on every weekly frontrunner search. The real, verified isolation mechanism: every consumer that aggregates `researcher_dof_ledger` (`database.count_dof_backtest_selections`, `database.get_researcher_dof_ledger_for_run` — the production N_effective feed at `autotuner.py:2487`) filters on the literal string `evidence_source='BACKTEST_SELECTION'`. Writing frontrunner rows with the distinct value `"OVERLAY_BACKTEST_SELECTION"` excludes them from every such consumer by construction — zero schema/query change. `_DOF_LEDGER_SPEC_BUNDLE_SENTINEL` is kept as belt-and-suspenders audit legibility only. Verified by a real-DB (non-mocked) integration suite; `frreview` independently re-traced the SQL filters at review and confirmed the exclusion. See "DoF-Ledger Isolation" in `DECISIONS.md`.

### Atlas corpus — once-per-run hoist (AC-3/AC-12)

`_gather_atlas_frontrunner_patterns` loads the shared weekly-cached Atlas corpus (`community_strats.load_community_strategies()`), reuses `frontrunner_detector.detect_frontrunner_cascades` structurally against each Atlas candidate's tree, and extracts pattern dicts (`vix_tickers`, `rsi_thresholds`, `watched_tickers`, `basket_node_count`) — **never** `oos_metrics`/`sharpe` (AC-3: "never trusts incoming oos_metrics.sharpe"). Called **once per symphony run**, not once per cascade — an earlier version called it inside the per-cascade loop, which made every unmocked test attempt up to `MAX_CASCADES_PER_SYMPHONY_RUN` live Atlas/Mongo fetches (the "hitting Mongo" failure mode this hoist fixes; also a correct production efficiency win regardless of test exposure).

`watched_tickers=[]` is passed at the current (hoisted) call site — **intentional, not a placeholder**: the function's `watched_tickers` param is currently unused (no ticker-relevance filtering implemented). **Landmine flagged at review (P2-2, landed `07bdc8c`, documented in-source):** because the call is now run-scoped rather than cascade-scoped, if ticker-relevance filtering is ever wired against that param, this call site is the one that must be updated to pass real tickers — otherwise filtering silently no-ops forever with an empty list.

### `_gate_and_accept_candidate` — the AC-6/AC-7 decision function

Independently re-backtests both incumbent and candidate (never trusts the incumbent's stored `oos_metrics`), runs the candidate through `evaluate_candidate_batch`, records the search-breadth DoF row regardless of verdict, and — only for the gate's `ADOPT_CANDIDATE` survivor — applies `frontrunner_acceptance.evaluate_calmar_acceptance`. Returns `(accepted: bool, metrics: dict)`; on any reject path the metrics dict still carries the incumbent-vs-candidate CAGR/MDD/node-count deltas (when available) so AC-11's "rejected item w/ reason+deltas" can be persisted as an audit observation — a pre-gate backtest failure has no valid comparison data and stays a log-only skip.

### Traversal style (P2-1, landed `26c1364`)

`_count_tree_nodes` (this module) is **iterative** (explicit-stack), mirroring `symphony_schema.py`'s established pattern and the equivalent fix in `frontrunner_detector.py` (`_count_nodes`/`_collect_tickers`/`_find_cascade_roots` — see that module's doc). This closed a real gap found at code review, not a hypothetical: `frtest`'s empirical probe (`2df4ca6`) confirmed the pre-fix recursive version raised `RecursionError`, uncaught, on a synthetic 3,000-deep tree — its sole production caller (`_gate_and_accept_candidate`) happened to wrap the whole call in try/except, so the pre-fix blast radius was a logged reject with `reason="RecursionError"`, but the function itself provided no safety net of its own. Behavior-preserving; verified against the full frontrunner test surface with zero regressions.

## Testing

- `tests/advisors/test_frontrunner_builder.py` — 8 tests (generation constraints, splice, run-entrypoint behavior)
- `tests/advisors/test_frontrunner_gate_wiring.py` — 17 tests (gate-reachability fix, cascade-cap, AC-11 rejected-observation, batch composition, plus AC-G2-1..6 the Gate#2 fold-vs-fold baseline + thin-incumbent conservative-withhold, added this cycle)
- `tests/advisors/test_frontrunner_atlas_patterns.py` — 9 tests (AC-3 Atlas wiring, once-per-run hoist)
- `tests/advisors/test_frontrunner_dof_isolation.py` — 4 tests (real-DB, non-mocked: all 4 polluting `researcher_dof_ledger` consumers proven to exclude frontrunner rows / include autotuner rows unchanged)
- `tests/advisors/test_frontrunner_approval.py` — 8 tests (`approve_frontrunner_proposal` orchestration, AC-12 upload-cap guard, idempotency)
- `tests/advisors/test_frontrunner_deep_tree_hardening.py` — 5 tests (P2-1: 3 depth-hardening tests against a 3,000-deep synthetic tree + 2 regression guards on the public D-1 boundary/skip-reason contract; shared with `frontrunner_detector` — see that module's doc)
- `tests/security/test_frontrunner_no_trade_boundary.py` — 10 tests (adversarial source-scan: no invest/deploy symbol or URL fragment anywhere in the frontrunner surface, `run_frontrunner_build` never calls `save_symphony`; stays green, unmodified, through the `DE-ADVISOR-CACHE-001` prompt reorder)
- `tests/test_no_live_mongo_guard.py` — 4 tests (session-autouse guard: any live `pymongo.MongoClient` construction under pytest fails loud, real-money-critical — composes suite-wide with `community_strats`/`atlas_cache`)
- `tests/advisors/test_frontrunner_builder_signal_gating.py` — AC-5 (frontrunner-signals cycle) FUNCTION-LEVEL tests: positive-edge filtering, Tier-1 remove-key veto, provenance building, the crossover/vs display-format functions' non-collision invariant (9 tests post-AC-R4 trim — the 2 `resolve_signals_unavailable_marker` tests were deleted with that function, AC-R2).
- `tests/advisors/test_frontrunner_builder_signal_wiring.py` — AC-5 INTEGRATION tests (5 tests post-AC-R4 trim): extract/classify executes on a real build run, generation prompt carries edge-stat lines, Tier-1-remove veto fires before backtest spend, accepted `metrics_json` carries signal provenance, `signals_unavailable` degrades the run structural-only. The persistence-specific tests (persist-called-and-warehouse-contains-rows, the accessor empty-state negative pin) were deleted with the persistence layer (AC-R2/AC-R4). **This is the exact test file whose `_build_generation_prompt` spy caught the `DE-ADVISOR-CACHE-001` mid-cycle regression — see "Prompt Caching" under Internal Mechanics above.**
- `tests/advisors/test_frontrunner_builder_prompt_caching.py` (new, `DE-ADVISOR-CACHE-001`, 2026-07-29) — pins the `cache_control` breakpoint's presence and placement, the round-trip byte-preservation of the reorder against `tests/fixtures/frontrunner_builder/generation_prompt_stable_content_baseline.json`, that the hints section is verifiably outside the cached block, and that the retry loop resends an identical two-block structure.
- `tests/advisors/test_frontrunner_proposal_identity.py` (new, `DE-FR-PROPOSAL-IDENTITY-001`, 2026-08-20, 43+ tests across the RED/mid-cycle-addition commits) — unit coverage for all three pure helpers (name-builder format/hash-fallback/truncation/uniqueness-via-id/retrofit-wording branch; description-builder provenance fields + the unconditional-full-copy-sentence deviation locked at `65738df0` + AC-6 legacy-row degrade; `summarize_overlay`'s flat-condition/compound-degrade/malformed-input paths using a robust positive/negative ticker-presence pair rather than a fragile whole-tree-percentage heuristic); persist-site stamping (the pre-graft `result.candidate`, never `spliced`, adversarially guarded); approve-path `save_symphony` payload assertions (bare-hash format asserted ABSENT).
- `tests/app/test_frontrunner_proposal_identity_template.py` (new, `DE-FR-PROPOSAL-IDENTITY-001`, 2026-08-20) — `ai_advisor_tab()` context assembly (overlay fields bounded, `metrics_json` non-dict normalization to `{}`, incumbent display-name resolution) and rendered-HTML assertions (identity/explainer/overlay-summary lines escaped, an adversarial `<script>`-bearing display name asserted escaped, legacy-row empty-state, source-branched raw-preview `<summary>` label — "Full spliced symphony (preview)" for `frontrunner_builder` vs "Proposed symphony (preview)" for `strategy_builder_retrofit`, with "spliced" asserted absent from retrofit cards). **Mid-cycle regression caught and fixed (Revise 1, `02f86e51`):** a pre-existing latent crash in the stats table — `is not none` checks on values that become Jinja `Undefined` (not literal `None`) once `metrics_json` degrades to `{}`, which then crash Jinja's `format()` filter — was the actual root cause of the AC-6 non-dict-`metrics_json` 500, not merely the missing normalization; fixed with `is defined and` guards on all 7 stats-table cells (calmar/cagr/mdd × incumbent/candidate, plus `fr-node-count-delta`, initially missed in the first pass and caught by a dedicated sufficiency-review RED test, `ebe4bbb0`).

**PM-authoritative gate run** (quiet window, `-n0`, unique `DB_PATH`, process table checked clear before running) on `26c1364`: **207 passed, 2 skipped, 44.31s** across the 9 frontrunner files + `test_no_live_mongo_guard.py` + `test_community_strats.py` + `test_atlas_cache.py` (+5 vs the prior 202 = the new deep-tree hardening file). The 2 skips are pre-existing/stale (`test_community_strats.py` claiming the module doesn't exist — it does; unrelated to this cycle, not fixed here). Warnings are benign quantstats divide-by-zero on zero-max-drawdown edges. **This cycle's AC-5 addition:** 55/55 own RED tests GREEN at `bf6f026b` at the FUNCTION level (shared commit with the AC-3/AC-4/AC-6 work in `frontrunner_detector.py`/`frontrunner_signals.py`); 110/110 across the full RED batch on the frontrunner-signals branch as of that commit. Cluster-D wiring landed at `95dac72c` (integration into the build path) and its classification-persistence half was subsequently removed by the operator's de-productization ruling (AC-R2, `6715d654`) the same day — see `DE-FR-SIGNALS-001` for the full account. **Final state, fr-doc-verified directly at HEAD `6715d654`:** `python -m pytest tests/advisors -k frontrunner -n0 -q` -> 240 passed, 1030 deselected, 1 xfailed (66.90s) across the whole frontrunner test surface (both slices, G2 fix + de-productization rip).

**`DE-ADVISOR-CACHE-001` verification (2026-07-29):** doc-writer independently re-ran (not just relaying cache-impl's own count) `test_frontrunner_builder_prompt_caching.py` + `test_frontrunner_builder.py` + `test_frontrunner_gate_wiring.py` + `test_frontrunner_builder_signal_wiring.py` + `tests/security/test_frontrunner_no_trade_boundary.py` alongside the sibling `build_plan_generator` test files (shared verification run, `DE-ADVISOR-CACHE-001` covers both sites) — **163 passed, 0 failed, `-n0`**. Both ruff gates clean on `advisors/frontrunner_builder.py`. See `DE-ADVISOR-CACHE-001` in `DECISIONS.md` for the full record, including the note that this is request-SHAPE verification only — the real Anthropic-side cache-hit proof is a separate PM live probe against `build_plan_generator.generate_build_plans`, not yet run as of this doc pass.

**`DE-FR-PROPOSAL-IDENTITY-001` verification (2026-08-20):** doc-writer independently re-ran (not relaying fpi-test-writer's own "138 passed" figure) `python -m pytest tests/advisors -k frontrunner tests/security/test_frontrunner_no_trade_boundary.py tests/app/test_frontrunner_proposal_identity_template.py -n0 -q` at HEAD `02f86e51` — **306 passed, 1 xfailed (pre-existing, `test_real_looking_core_tickers_do_not_leak_into_watched_tickers`, documented in `DE-FR-SIGNALS-001`, unrelated to this cycle), 0 failed**. This is a strict superset of the two new-cycle files plus the full frontrunner test surface — a wider net than the touched-file-only slice, run to also catch any regression this cycle's `metrics_json` shape change (a non-dict-normalizing `{}` default landing on a route every other frontrunner test also exercises) might have introduced elsewhere.

**`DE-FR-PROPOSAL-IDENTITY-001` Revise-2 verification (2026-08-20):** PR #126's independent `/code-review` returned 11 fix-before-merge findings (F1-F10, F12; F11 accepted-not-blocking). Cycle: `fcb7a0a9` (RED, F1-F10 across both identity test files) → `292d543a` (GREEN, F5/F9, self-contained `app.py` render-side fixes) → `916e08d5` (GREEN, F1-F8/F10/F11 in `advisors/frontrunner_builder.py`) → `b8ba8085` (GREEN, F8/F10 consumer wiring in `app.py`/`templates/ai_advisor.html`) → `7b8e6c98` (test fix: a self-contradictory RED test corrected, plus 2 new mutation-based proofs that F8/F10's shared-helper wiring is load-bearing — disabling the import falls back to the pre-fix inline logic and a test goes RED) → `36522e66` (a ruff E501 line-length wrap, no logic change). fpi-test-writer's reported figure: 164 passed / 0 failed across both new-cycle test files plus the 6 pre-existing frontrunner regression suites. **Doc-writer independently re-ran** (not relayed) the same narrower slice from the original-cycle verification above, plus the full `-k frontrunner` superset, at HEAD `36522e66`:
- Narrow slice (`test_frontrunner_proposal_identity.py` + `test_frontrunner_proposal_identity_template.py` + `test_frontrunner_approval.py` + `test_frontrunner_no_trade_boundary.py` + `test_frontrunner_builder.py` + `test_frontrunner_gate_wiring.py`, `-n0`): **117 passed, 0 failed**.
- Full frontrunner surface (`tests/advisors -k frontrunner` + `test_frontrunner_no_trade_boundary.py` + `test_frontrunner_proposal_identity_template.py`, `-n0`): **332 passed, 1 xfailed (the same pre-existing unrelated xfail as the original-cycle verification above), 0 failed** — up from 306 at the original cycle's HEAD, consistent with the ~26 net new RED-then-GREEN Revise-2 tests.

## Internal Dependencies

- `advisors.plan_tree_compiler`, `advisors.symphony_schema` — module-level imports (compile + validate)
- `advisors.frontrunner_detector` — `VIX_FAMILY_TICKERS` (module-level import) + `detect_frontrunner_cascades` (CC-2 lazy import inside `_run_build_for_symphony`)
- `advisors.community_strats`, `advisors.frontrunner_detector` — CC-2 lazy imports inside `_gather_atlas_frontrunner_patterns`
- `advisors.backtest_gate_engine` (`BacktestCandidate`, `evaluate_candidate_batch`), `advisors.composer_backtest_client` (`run_backtest`), `advisors.frontrunner_acceptance` (`evaluate_calmar_acceptance`), `analytics` (`compute_quantstats_metrics`) — CC-2 lazy imports inside `_gate_and_accept_candidate`
- `database` — CC-2 lazy imports throughout (`load_state`, `insert_dof_ledger_row`, `insert_advisor_observation`, `insert_frontrunner_proposal`, `get_frontrunner_proposal`, `count_uploaded_frontrunner_proposals`, `update_frontrunner_proposal_status`)
- `symphony_logic` — CC-2 lazy import inside `_run_build_for_symphony` (`fetch_symphony_score`)
- `advisors.composer_draft_client` — imported at module scope inside `approve_frontrunner_proposal` only (never referenced from the build/run path)
- `advisors.frontrunner_signals` — CC-2 lazy import inside `_run_build_for_symphony` (`load_frontrunner_signals()`, the AC-5 signals hoist) and inside `_build_classification_rows_from_fr_checks` (`classify_fr_checks()`, AC-4) — both called on every build run. `resolve_signals_unavailable_marker`, the earlier per-symphony-availability caller, was REMOVED along with the persistence layer it fed (AC-R2, `6715d654`)
- `anthropic` SDK — lazy-imported inside `_build_client` (factory seam, mirrors `build_plan_generator._build_client`)

**Reverse dependencies (who calls into this module):**
- `advisors/strategy_builder_scheduler.py::run_weekly_build` — calls `run_frontrunner_build()` unconditionally after the four Strategy-Builder objectives complete (AC-1, weekly, isolated in its own try/except so a frontrunner failure never blocks the objective loop above it)
- `advisors/strategy_builder_engine.py::_persist_survivor` — does NOT call anything in this module directly; it writes its own `frontrunner_proposals` row via `database.insert_frontrunner_proposal(proposal_source="strategy_builder_retrofit")`, which later flows through THIS module's `approve_frontrunner_proposal` on operator approval (AC-10)

## Wave-2 UI (built, 2026-07-11)

The Advisor-tab UI and its three POST action routes are built and reviewed as part of this branch (`feature/frontrunner-builder`, wave-2, `eb1b612`):

- `POST /ai-advisor/frontrunner-builder/run` -- on-demand trigger, async 202 dispatch via a dedicated executor (`app.py`; see [app.md §Frontrunner Builder Routes](app.md))
- `POST /ai-advisor/proposal/approve` / `POST /ai-advisor/proposal/reject` -- generic, source-agnostic, shared by both `frontrunner_builder` and `strategy_builder_retrofit` rows (`app.py`)
- The Frontrunner Builder Advisor tab (`templates/ai_advisor.html`, 7th tab panel) -- pending-approval cards with incumbent-vs-candidate Calmar/CAGR/MDD/node-count deltas, Approve/Reject buttons (`static/ai_advisor.js`: `frRunBuild`, `frApprove`, `frReject` -- see [static/ai_advisor.js](static_ai_advisor_js.md))

`run_frontrunner_build` and `approve_frontrunner_proposal` are both now operator-reachable through the dashboard, not just via tests or a Python shell.

**Frontrunner-signals cycle addition, built then removed (2026-07-16):** the same 7th tab panel briefly gained a read-only "Live Signal Classification" subsection (AC-7) rendering persisted `fr_key → live RSI → edge stats → keep/prune/remove/no_edge_data` rows. It was removed the same day (AC-R1, `f563f16c`) per the operator's de-productization ruling — the classification tab productized a one-time PM deliverable (the cull analysis) that was never asked for as a product feature. The rest of this tab (Run build, proposal cards, approval flow, PR #96) is unaffected. See `DE-FR-SIGNALS-001` for the operator's verbatim ruling and the full account.

**Still open -- operator-gated task-zero live test.** One real `save_symphony` create against the operator's Composer account, then immediately `verify_undeployed`, then delete the throwaway symphony (feature-plans/frontrunner-builder.md §Architecture "Build task ZERO"). **The wave-2 UI being built and reviewed does NOT mean the approve→create path has been exercised against the real Composer API** -- `approve_frontrunner_proposal` has to date only been called against mocked Composer responses in tests. This gate must pass before the operator's first real "Approve" click in production.

See `DE-FRONTRUNNER-002` in `DECISIONS.md` for the wave-2 UI decisions (async-202 dispatch rationale, generic source-agnostic route shape, `candidate_tree` preview-bounding, render-security posture).

## CLAUDE.md Key-Files amendments (historical proposal — APPLIED, see status note)

**[STATUS, corrected 2026-08-20]** The three wave-2 amendments below (and the `DE-ADVISOR-CACHE-001` fourth amendment referenced at the end of this section) were, contrary to the "Not applied"/"confirmed still unapplied" claims this section originally carried, in fact applied to `.claude/CLAUDE.md`'s `## Key Files` table — the `app.py`, `templates/ai_advisor.html`, and `static/ai_advisor.js` rows each carry a `"; applied to this table 2026-07-17)"` marker, and the `advisors/frontrunner_{detector,builder,acceptance}.py` row already carries the `DE-ADVISOR-CACHE-001` prompt-caching content. Verified directly against the current project `CLAUDE.md` at this doc pass, not assumed. The bracketed text below is left in place as the historical record of what was proposed (never rewritten, per the append-only convention for this kind of note) — do not re-apply it; it is already present in `.claude/CLAUDE.md`. This cycle (`DE-FR-PROPOSAL-IDENTITY-001`) adds a new, genuinely-pending draft — see the DRAFT sent to the PM/team-lead for approval, referenced from `DE-FR-PROPOSAL-IDENTITY-001` in `DECISIONS.md`.

**`app.py` row** -- append:
> **Frontrunner Builder wave-2 routes (2026-07-11, `eb1b612`):** `GET /ai-advisor/frontrunner-builder` → 302 redirect (no standalone page, mirrors the strategy-builder stub); `POST /ai-advisor/frontrunner-builder/run` -- async 202 dispatch to a dedicated `_FRONTRUNNER_BUILD_EXECUTOR` (single-worker, `atexit`-registered, deliberately separate from `_DISMISS_EXECUTOR`), fail-fast on missing `ANTHROPIC_API_KEY` before submit, submitted work wrapped in a log-and-swallow closure (`_run_frontrunner_build_background`) as defense-in-depth against a D-1 contract violation on an unawaited `Future`; `POST /ai-advisor/proposal/approve` -- generic/source-agnostic (`proposal_id`-keyed), the ONLY route in the app that can reach `composer_draft_client.save_symphony` (exclusively via `advisors.frontrunner_builder.approve_frontrunner_proposal`); `POST /ai-advisor/proposal/reject` -- status-only DB write. `ai_advisor_tab()` additively prefetches `database.get_pending_frontrunner_proposals()`, bounding each row's `candidate_tree` to a 4000-char JSON preview (`candidate_tree_preview`) before template render -- the full spliced tree (potentially 8,000+ nodes) is never passed to Jinja. See `docs/generated/app.md` §"Frontrunner Builder Routes" and `DE-FRONTRUNNER-002` in `DECISIONS.md`.

**`templates/ai_advisor.html` row** -- append:
> **Frontrunner Builder tab (2026-07-11, `eb1b612`):** 7th tab panel (`tab-panel-frontrunner-builder`), following the same in-place-tab pattern as Strategy Builder. Persistent non-dismissible risk banner (this is the one tab on the page where an operator action -- Approve -- creates a real Composer symphony). Pending-approval cards render incumbent-vs-candidate Calmar/CAGR/MDD deltas + node-count delta (columns conditionally shown via `is_fr = p.proposal_source == 'frontrunner_builder'` -- `strategy_builder_retrofit` rows have no incumbent to compare against, so the Incumbent column and node-count-delta strip are structurally omitted, not blanked); a collapsible raw-candidate-preview `<details>` block renders the server-bounded `candidate_tree_preview` string (never raw JSON, no `| safe` anywhere on this panel). Approve/Reject buttons call `frApprove`/`frReject` (JS).

**`static/ai_advisor.js` row** -- append:
> **Frontrunner Builder tab functions (2026-07-11, `eb1b612`):** `frRunBuild()` -- on-demand build trigger, POSTs to `/ai-advisor/frontrunner-builder/run`, does NOT auto-navigate (unlike `sbRunAnalysis`) since the route returns 202 before results exist; shows a "reload later" status message. `frDispatchProposalAction(action, proposalId)` -- shared approve/reject dispatch (internal); disables both card buttons + dims the card during the request (prevents double-submit); on success replaces the card's action row with a confirmation message, on failure restores the card and alerts the error. `frApprove`/`frReject` -- thin `window`-exposed wrappers for Jinja `onclick` handlers.
