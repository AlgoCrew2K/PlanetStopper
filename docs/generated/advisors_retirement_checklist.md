# advisors/retirement_checklist

> Deterministic, no-LLM wind-down checklist builder for an operator-approved Retirement Recommender candidate: the candidate's id/name/current holdings plus a fixed set of manual steps the operator performs by hand in Composer. The one entirely deterministic module in the Retirement Approval Lifecycle feature (operator ruling, Gate-2b) -- a template, not a generated artifact. No trade, order, liquidation, deploy, or `LIVE_EXECUTION` primitive of any kind.

**Source:** `advisors/retirement_checklist.py`
**Last updated:** 2026-08-27 (module BYTE-UNCHANGED this pass -- verified zero diff against Cycle 2b's shipped state; doc correction only, Phase 2 Cycle 2c epic-completion, AC-3, `DE-RETIRE-APPROVAL-001`/`DE-RETIRE-POLISH-001` -- see the "Consumed by the template" section below, which corrects the FIRST `/code-review` pass's F7 ACCEPT ruling now that `templates/ai_advisor.html` renders `candidate_name`.) Prior: 2026-08-26 (new module, Phase 2 Cycle 2b, `DE-RETIRE-APPROVAL-001`; corrected same-day for the PR #139 review remediation's F5 fix -- see the "Honest off-hours degrade" section below)

## Overview

`advisors/retirement_checklist.py` has exactly one public function, `build_checklist(recommendation: dict, bot_state: dict) -> dict`, which assembles a deterministic, advisory wind-down checklist for a retirement candidate. It never calls a language model of any kind -- the module contains no LLM client import, no generation call, and no reference to `ai_advisor` or the anthropic SDK anywhere in its source (structurally enforced, see "No LLM, no exec path" below).

**Who calls it, and when.** The sole production caller is `app.py`'s `ai_advisor_tab()`, which assembles the checklist AT RENDER TIME for each recommendation card whose live `approval_status` is `"approved"` -- never inside the approve route itself (`_dispatch_retirement_decision` is a status-ONLY write, AC-5) and never at producer/persist time (unlike the explainer, which runs once at 03:45 and is persisted; the checklist is recomputed fresh on every render, since it reflects LIVE current holdings, not a historical snapshot). See [app](app.md)'s "Retirement Approval Lifecycle — explainer output, display names, live-join, and checklist assembly" section for the full call-site wiring, including the `_ret_any_approved` short-circuit that skips `build_checklist` entirely when no card in the batch is approved. **As of Cycle 2c (AC-1),** the `database.load_state()` call this caller passes in as `bot_state` is no longer conditional on any card being approved -- it now runs unconditionally per request (for the AC-1 display-name resolution every card needs, approved or not) and is REUSED for the checklist call, rather than being fetched a second time.

## `build_checklist(recommendation: dict, bot_state: dict) -> dict`

**Args:**
- `recommendation` -- a retirement recommendation dict carrying at least `candidate_id` (a Cycle-2a `raw_response` shape, but any dict with that key works).
- `bot_state` -- the live state dict (as returned by `database.load_state()`), used to resolve the candidate's display name and current holdings.

**Returns** a dict with exactly these 6 keys:

| Key | Type | Meaning |
|-----|------|---------|
| `candidate_id` | `str \| None` | Echoed verbatim from `recommendation.get("candidate_id")`. |
| `candidate_name` | `str \| None` | `bot_state[candidate_id]["name"]` when resolvable; `None` if the candidate isn't present in `bot_state` or that entry isn't a dict. **Rendered by the template as of Cycle 2c, AC-3** — see "Consumed by the template" below. |
| `holdings` | `list[str]` | Sorted ticker keys from `bot_state[candidate_id]["logic_holdings"]`. `[]` when unavailable (see the off-hours degrade below). |
| `holdings_available` | `bool` | `True` iff `logic_holdings` resolved to a non-empty dict. |
| `steps` | `list[str]` | A fresh copy of the fixed `_CHECKLIST_STEPS` tuple (see below) -- always populated, regardless of holdings availability. |
| `unavailable_note` | `str \| None` | `_HOLDINGS_UNAVAILABLE_NOTE` when `holdings_available` is `False`; `None` otherwise. |

**Never raises**, regardless of malformed/missing/`None` input -- every dict access is defended with an `isinstance` check before use (`rec = recommendation if isinstance(recommendation, dict) else {}`, same pattern for `bot_state`/`entry`/`logic_holdings`). A `None` or non-dict `recommendation`/`bot_state` degrades to the same honest-empty shape a real-but-empty input would produce.

## Consumed by the template (Cycle 2c, AC-3) -- corrects the first `/code-review` pass's F7 ACCEPT ruling

`build_checklist` has returned `candidate_name` as one of its 6 documented keys since it was first written (Cycle 2b). Through Cycle 2b and its Revise round, `templates/ai_advisor.html` never read it — the FIRST PR #139 `/code-review` pass's Finding F7 correctly identified this and the PM ruled it ACCEPT ("benign, keep as-is... a real future consumer... is a plausible near-term render enhancement"; see `DE-RETIRE-APPROVAL-001`'s Revise-round section in `DECISIONS.md`). **That future consumer landed in Cycle 2c (AC-1..AC-8's own AC-3, from a SECOND, later `/code-review` pass — not a re-litigation of F7, a fulfillment of the exact enhancement F7's ACCEPT ruling anticipated):** the checklist block now renders `{{ (cl.get('candidate_name') or cl.get('candidate_id', '')) | e }}` as a header line inside the wind-down checklist, with an honest fallback to the raw `candidate_id` when the name is unresolvable — mirroring the SAME fallback shape `build_checklist` itself already uses internally. This module (`retirement_checklist.py`) is BYTE-UNCHANGED for AC-3 — the field it has always returned simply gained a consumer, exactly per the plan's own Architecture note ("`advisors/retirement_checklist.py` -- already returns `candidate_name` -- no change unless the fallback needs hardening (AC-3 is template-side)"). The F7 ACCEPT ruling in `DECISIONS.md`'s Revise-round section is preserved verbatim as a historical record of what was true through Cycle 2b — this section is the forward correction, not a rewrite of that history.

## Honest off-hours degrade (AC-6)

`logic_holdings` is a live, market-hours-only field -- off-hours, weekends, or a flat symphony can leave it empty or absent from `bot_state`. `build_checklist` treats `logic_holdings` as available only when it resolves to a **non-empty dict**:

```python
if isinstance(logic_holdings, dict) and logic_holdings:
    holdings = sorted(logic_holdings.keys())
    holdings_available = True
    unavailable_note = None
else:
    holdings = []
    holdings_available = False
    unavailable_note = _HOLDINGS_UNAVAILABLE_NOTE
```

An empty-dict, `None`, or entirely-missing `logic_holdings` all take the SAME `else` branch -- there is no fabricated ticker anywhere in this path. `_HOLDINGS_UNAVAILABLE_NOTE` = `"current holdings unavailable (off-hours) -- view live positions in Composer"`, a named module-level constant (not a magic string), rendered verbatim by the template as `{{- cl.get('unavailable_note') | e -}}` -- the single source of truth for this text.

**[CORRECTED, PR #139 review remediation, F5, `5655edfd`, 2026-08-26]** `templates/ai_advisor.html` originally ALSO carried a hardcoded `{% else %}current holdings unavailable (off-hours) — view live positions in Composer{% endif %}` fallback branch, duplicating this constant's exact wording as a defense-in-depth belt-and-suspenders for the case where `cl` is present but `unavailable_note` is somehow falsy. This branch was dead code by construction: `build_checklist` (this module) ALWAYS sets `unavailable_note` to the named constant whenever `holdings_available` is `False` (see the function body above -- there is no code path where `holdings_available=False` and `unavailable_note` is simultaneously falsy). A same-cycle PR-level `/code-review` (PR #139) flagged the duplicate literal as a drift risk (a future change to `_HOLDINGS_UNAVAILABLE_NOTE` here would silently NOT update the template's independent copy) and it was removed -- the template now renders `cl.get('unavailable_note')` unconditionally on that branch, with this module's constant as the sole source of the text.

## `logic_holdings` weight-shape defensive extraction

`build_checklist` extracts only the **ticker set** (`sorted(logic_holdings.keys())`) -- it never reads or interprets the per-ticker weight value at all, so the documented weight-shape variance elsewhere in this codebase (a bare float vs. `{"weight": x}`) is a non-issue here by construction: this function has no code path that could be sensitive to that shape difference, since it discards the values entirely and keeps only the keys.

## `_CHECKLIST_STEPS` -- the fixed manual steps (AC-6)

A tuple of 5 fixed advisory-prose strings, named module-level constant (`_CHECKLIST_STEPS`), never inlined as magic strings at the return site:

1. "Open the candidate symphony directly in Composer."
2. "Cross-check the holdings listed below against the live position view in Composer."
3. "Manually wind down (sell or liquidate) each listed position within Composer -- this checklist does not execute any trade itself."
4. "Confirm the symphony's cash balance reflects the completed wind-down."
5. "Pause or archive the symphony in Composer once the wind-down is confirmed."

These describe manual actions the **operator** performs by hand in Composer's own UI -- this module never performs any of them itself, and contains no code path that could.

## No LLM, no exec path (AC-8 safety boundary)

This module is held to a **stronger** guarantee than "no trade primitive" -- the whole module must be entirely LLM-free, not merely off the approve/reject action path. `tests/security/test_retirement_action_no_trade_boundary.py`'s Group B (`TestChecklistModuleNeverReachesLlm`) proves this four ways:
1. Source never contains the substring `"ai_advisor"` (the module the LLM client seam lives in).
2. Source never references `"anthropic"` (case-insensitive) -- the SDK itself.
3. Source never references `_build_client` (the LLM seam attribute name) or `explain_recommendation` (the explainer's entrypoint).
4. **A static AST walk** (`_references_name_or_attr`) confirms no `Name` or `Attribute` node anywhere in the module's parsed AST resolves to `_build_client` -- a structural proof, not just a substring/runtime-mock check, so a future refactor that reintroduces the call under a different code shape (e.g. an aliased import) cannot silently slip past.

Group A's shared parametrized scan (applied to both `retirement_explainer.py` and this module) additionally confirms: no forbidden action-word-shaped public symbol (`invest`/`deploy`/`sell`/`buy`/`order`/`liquidate`/`liquidation`/`trade`/`execute` as a whole underscore-delimited identifier component -- deliberately NOT a ban on those words appearing inside the checklist's own advisory prose, see the note below); no forbidden trade-URL fragment (`/deploy/`, `/invest`, `/sell`, `/liquidate`) in executable code; no import of `alpha_bot_execution` or `composer_draft_client`; no reference to `invest_in_symphony`, `LIVE_EXECUTION`, or `is_advisory_only`; no wildcard imports.

**The deliberate AC-8 nuance -- advisory TEXT is not banned, executable CODE PATHS are.** `_CHECKLIST_STEPS` necessarily contains the words "sell", "liquidate", and "wind down" as **advisory prose describing what the operator does by hand** -- the safety scan's identifier/URL/import checks never inspect string-literal CONTENTS for these words, only identifiers, URL path fragments, and import statements. Banning the bare word "sell" from appearing inside a checklist step's own text would make the checklist's own AC-6-mandated content fail the safety test despite containing zero executable trade code -- see `tests/security/test_retirement_action_no_trade_boundary.py`'s module docstring for the same ruling stated at the test-file level.

## Internal Dependencies

- None -- this module imports nothing beyond `from __future__ import annotations`. It does not import `database`, `ai_advisor`, `alpha_bot_execution`, `composer_draft_client`, or any other module in this codebase.

**Caller:** `app.py`'s `ai_advisor_tab()` -- see [app](app.md)'s "Retirement Approval Lifecycle — explainer output, display names, live-join, and checklist assembly" section for the render-time call site (fetches `bot_state` via `database.load_state()` once per request -- unconditionally, since Cycle 2c's AC-1 needs it for every card -- and assembles the checklist per approved card in its own `try`/`except`, degrading `_rec["_checklist"]` to `None` on any failure -- the template then renders "Checklist unavailable." rather than a 500).

See `DE-RETIRE-APPROVAL-001` in `DECISIONS.md` for the full Gate-2b design record.
