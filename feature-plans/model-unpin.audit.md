Spec audit (re-check 1 of 1): feature-plans/model-unpin.md (tier: feature)
Verified against: PlanetStopper @ 37c8e983b64c (`## main...origin/main`; the working tree carries untracked files, so code was read with `git show 37c8e983:<path>`)
Checklist: PlanetStopper `.claude/agents/_refs/spec-review-checklist.md`; tier template `~/.claude/skills/scaffold/templates/feature.md`; source `feature-plans/model-unpin.source.md`, read whole
Result: 4 CONFIRMED, 0 QUESTION (all 25 first-round findings resolved; 4 new findings)

Structure pass: sections are complete and in order. The placeholder grep is empty. `plan-trace check --stage gate-1` reports clean, and `plan-trace self-contained` reports 0 findings. The SDK facts below come from the installed anthropic 0.85.0, which is the version `requirements.txt:1` pins.

First-round findings: all resolved.
- #1 to #7 and #9 to #25 are addressed as the writer listed. Each changed citation was re-read at the ref and holds.
- #8 is resolved as a source check. `model-unpin.source.md` contains the brief's two operator messages and Step-2 answers verbatim (lines 2-3 and 5-26), plus the PM's message to the writer (line 29), which carries the relayed operator text behind G-4 and G-5 and the live read behind G-30 to G-32. Every G line matches its cited source line. The PM still has to confirm that file's provenance; it is recorded as Known-Unconfirmed 11.

A. Wrong or unverifiable claims

1. CONFIRMED - Known fact 5, Prerequisites bullet 3, Known-Unconfirmed 1 - The spec says the pinned SDK sends "`fallbacks` and betas only on its beta namespace", and KU-1 asks "whether the pinned SDK's beta namespace accepts the structured-output call". Both can be settled from the installed SDK, and the first statement is wrong:
   - Neither namespace has a `fallbacks` parameter. `client.beta.messages.create` takes `betas` but not `fallbacks`.
   - Both namespaces take `extra_body` and `extra_headers` on `create` and `parse`. So the pinned SDK can put `fallbacks` and the beta header on the wire from the standard namespace as well.
   - `client.beta.messages.parse` exists and takes `output_format` and `betas`, so the beta-namespace half of KU-1 is answered.

   As written, the Gate-2 choice in Prerequisites (beta namespace or a higher SDK floor) rests on a false premise. KU-1 should keep only the part code cannot settle: whether `output_format` is served on the newer models. - SDK introspection, anthropic 0.85.0:
   - `inspect.signature(anthropic.resources.beta.messages.Messages.create)` contains `betas` and no `fallbacks`;
   - `'fallbacks' in ...beta...Messages.create params` is False;
   - `'extra_body' in anthropic.resources.messages.Messages.parse params` is True;
   - `hasattr(beta.messages.Messages, 'parse')` is True, and its params include `output_format` and `betas`.

B. Criteria missing or contradicting an enforced contract

2. CONFIRMED - AC-2 / Q-13 (default not applied in any scenario) - The AC-2 family rule falls back to Q-13 when the listing has no `line` or no `lifecycle`. Known-Unconfirmed 3 says this is why "the criteria hold either way". No scenario asserts that fallback:
   - none in which no listed model carries `line`, so family comes from the ID prefix `claude-<family>-`;
   - none in which `lifecycle` is absent, so every listed model counts as active.

   S-2.12 covers only a mixed listing, where other models carry `line`. The fixture plan names a "no `line` at all" case, but no criterion states its outcome, so the branch the plan relies on if KU-3 goes the other way is untested. Add one scenario per Q-13 branch, including a model whose ID has the family name but not as the `claude-<family>-` prefix. - Plan `## Acceptance Criteria` AC-2 S-2.11 to S-2.16, Q-13 row, Known-Unconfirmed 3; the SDK model type declares no `line`/`lifecycle` (`ModelInfo.model_fields` = `id, created_at, display_name, type`).

3. CONFIRMED - S-4.16 (an incomplete reason-code family) - The scenario lists the reason codes that switch the Asset Swaps empty-state title to "no swap was generated": `model_unavailable`, `no_tool_call`, `refusal`, `max_tokens`, `override_model_not_found`. It omits `override_request_rejected`. Q-15 and the AC-5 Rule apply that code at every site of the override knob, the asset-swap route included. As written, a test will pass while an override-rejected run shows "No swap cleared the gate this run" (`static/ai_advisor_asset_swaps.js:377`). This is the narrow-assertion family; state the rule as "any generation reason code" or list all six. - Plan S-4.16 vs S-5.18, Q-15 and the AC-5 Rule ("if the API rejects that model or the request sent to it").

4. CONFIRMED - AC-6 Rule (audit row and no-call provenance under an override); Rule-by-Entry matrix E12/E13 - The AC-6 Rule gives badges "the resolved model (or the raw override)". It says the audit row's `model_id` holds "the suggestion family's resolved ID or the empty string", and that a no-call `generation_model` (S-6.17 explicit-pair, S-6.6 early error) holds "the family's resolved ID or the empty string". It never says what these two fields hold when `ADVISOR_SUGGESTION_MODEL` is set and the suggestions were produced by the override model. The matrix marks AC-5 "n/a: no model call" at E12 and E13, yet the value AC-6 records there depends on the override. Add the override case to the Rule and to S-6.11 and S-6.17. - Plan AC-6 Rule; S-6.11; S-6.17; matrix row "AC-5 override", columns E12 and E13. At the ref the audit value comes from the knob, including when it is set: `app.py:8705` "model_id=ai_advisor.resolve_advisor_model()".

C. Template sections absent or empty without reason
none

D. HOW content that belongs at Gate 2
none

E. Closed-scope breaches
none

F. Correct and easily doubted
- S-2.22's 404 path, S-9.15's degrade-at-once rule and NB-11 now agree with the ref. The asset-swap and logic-change generators have no `stop_reason` read (`advisors/asset_swap_engine.py:1160-1176`, `advisors/logic_change_engine.py:879-895`).
- Known fact 7 holds. Persisted `raw_response` carries `run_id`/`evidence_injected` and no model key (`advisors/asset_swap_engine.py:783-799`, `advisors/strategy_builder_engine.py:735-743`).
- The widened AC-1 scope passes S-1.1 at the ref. The tracked `.claude/agents/*.md`, `deploy/`, `*.ps1` and `.github/workflows/` files hold no versioned ID; the agent files read family aliases only (`model: opus` / `model: sonnet`).

Refusal enumeration (changes since round 1 only):
| Input | Write path entry | Refusal (code / status) | Enforced at | Spec covers (AC-N / Fail-Closed / unreachable: reason / MISSING) |
|---|---|---|---|---|
| Messages request, resolved model | 8 SDK sites | 404 not_found | Anthropic API | S-2.22, Fail-Closed row 2 |
| Messages request, override | sites of the knob | 400 invalid_request (effort/strict/fallback) | Anthropic API | S-5.18, Fail-Closed row 4; the asset-swap panel title for this code is missing (finding 3) |
| Messages request | 8 SDK sites | 401 / 403 / 413 / 429 / 500 / 529 / timeout | Anthropic API | existing D-1 type-only handling, now named in the Fail-Closed paragraph |
| Models API listing | resolution | 401 / 403 / 429 / 5xx / timeout / connection | Anthropic API | S-2.18, S-4.10 |
