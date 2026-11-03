# Approval packet: Unpin every Claude model version

## What is being built

Every Claude call PlanetStopper makes runs on the newest model of its family without any versioned model ID in production or deployed source, for the operator using the AI Advisor tabs, the nightly Market Prism council, the 03:00 lens synthesis, the 03:45 retirement explainer and the weekly suggestion schedulers.
- A source scan fails the build on any versioned Claude model ID in production or deployed source (AC-1).
- Each Messages API call site resolves the newest active model of its family from the Models API at run time, re-resolving at most once a day, keeping the last good result, and re-resolving when a resolved model stops being served (AC-2).
- The nightly council launches with the generic tag `opus` (AC-3).
- A family never resolved in the process degrades honestly with a reason code, never a literal ID, and the panels say no candidate was generated rather than that the gate rejected one (AC-4).
- The two operator overrides keep working, and an override the API does not serve fails loudly instead of being replaced (AC-5).
- Model badges and copy, run provenance and the accept and reject audit rows name the model actually resolved or used (AC-6).
- The four tool-emitting generators stop forcing a tool call, send strict tool definitions and handle a reply without a tool call (AC-7).
- Classifier refusals are retried server-side on a fallback model and otherwise degrade honestly (AC-8).
- Requests and response parsing fit models that always think: explicit effort, no rejected parameters, answers read by block type, truncation reported (AC-9).

## Acceptance criteria and scenarios

### AC-1: No versioned model ID can exist in production or deployed source
Rule: If a tracked file outside `tests/`, `docs/` and `feature-plans/` that is Python, under `templates/`, `static/` or `deploy/`, a PowerShell or shell script, a workflow under `.github/workflows/`, an agent file under `.claude/agents/`, or `.env.example` contains a Claude model ID made of `claude-`, a family name (opus, sonnet, haiku, fable, mythos) and a version digit, then the source-scan test shall fail and name each file and line that holds one.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the repository at the GREEN commit | the source-scan test runs | it passes; the files that hold such IDs at the ref (Trigger) hold none |
| S-1.2 | N1 | a Python module outside the exempt trees with the ID inside a string used as code | the scan runs | it fails and names that file and line |
| S-1.3 | N1 | a Python module outside the exempt trees with the ID only in a docstring or a comment | the scan runs | it fails and names that file and line |
| S-1.4 | N1 | a file under `templates/` with the ID in markup | the scan runs | it fails and names that file and line |
| S-1.5 | N1 | a file under `static/` with the ID in JavaScript | the scan runs | it fails and names that file and line |
| S-1.6 | N1 | `.env.example` with the ID in a commented example line | the scan runs | it fails and names that file and line |
| S-1.7 | N1 | one planted ID per shape: one version component (`claude-sonnet-5` shape), two components (`claude-opus-4-8` shape), a dated suffix (`claude-haiku-4-5-20251001` shape), and one per family name (opus, sonnet, haiku, fable, mythos) | the scan runs over each in turn | each one fails the scan |
| S-1.8 | N1 | the same IDs under `tests/`, `tests/fixtures/`, `docs/` (including the Python file under `docs/research/`) and `feature-plans/` | the scan runs | it passes; those trees are exempt |
| S-1.9 | N1 | the generic tag `opus` in production code, the word `Fable` in prose, and `model: opus` or `model: sonnet` in an agent file | the scan runs | it passes; none of these is a versioned ID |
| S-1.10 | N7 | any repository state | the scan runs | it only reads files; no file changes |
| S-1.11 | N1 | the ID planted, one at a time, in a `deploy/` drop-in, in `schedule_prism.ps1`, in `.github/workflows/tests.yml` and in the `model:` line of a council agent file | the scan runs | each one fails and names that file and line |

Negative coverage: N1 S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.11 | N2 n/a: the scan refuses nothing to a user; its failure is the build gate, covered under N1 | N3 n/a: the scan computes no value | N4 n/a: the scan runs in CI and locally with no permission dimension | N5 n/a: one scan run reads a fixed file set | N6 n/a: the scan reads only the local checkout | N7 S-1.10 | N8 n/a: no edit is held open

### AC-2: Each Messages API call runs on its family's newest model
Rule: When an AI Advisor action or scheduled job makes a Claude Messages API call and no override is set for its knob, the call shall name the newest active model of that site's family according to this process's last successful Models API listing, and the process shall attempt a new listing before the call whenever it has none or the last successful one is older than 24 hours.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the Models API lists two active fable-line models with different `created_at` | the operator clicks Run Advisor (POST `/ai-advisor/suggest`) | the Messages API request names the fable-line model with the later `created_at` |
| S-2.2 | B | the Models API lists two active opus-line models | the operator sends a chat message (POST `/ai-advisor/chat/send`) | the request names the later opus-line model |
| S-2.3 | B | as S-2.1 | the operator evaluates an asset swap in objective mode (POST `/ai-advisor/asset-swaps/evaluate` with neither ticker) | the generation request names the later fable-line model |
| S-2.4 | B | as S-2.1 | the operator evaluates a logic change (POST `/ai-advisor/logic-changes/evaluate`) | the generation request names the later fable-line model |
| S-2.5 | B | as S-2.1 | the operator runs Strategy Builder (POST `/ai-advisor/strategy-builder/run`) | the build-plan request names the later fable-line model |
| S-2.6 | B | as S-2.1 | the operator starts a Frontrunner Builder run (POST `/ai-advisor/frontrunner-builder/run`) | each overlay request names the later fable-line model |
| S-2.7 | B | as S-2.1, a retirement recommendation needing a fresh explanation | the retirement worker runs (the 03:45 tick, `app.py:1440`, or POST `/ai-advisor/retirement/run`) | the explainer request names the later fable-line model |
| S-2.8 | B | as S-2.2, at least one lens available, `DISABLE_DAEMON_LENS_PIPELINE` unset | the 03:00 lens pipeline runs (`app.py:1433`) | the synthesis request names the later opus-line model |
| S-2.9 | B | as S-2.1 | `python -m advisors.weekly_suggestions_scheduler` runs in its own process | every Strategy Builder, asset swap, logic change and frontrunner request in the run names the later fable-line model |
| S-2.10 | B | the process's last successful fable listing was at time T; a newer active fable-line model is listed afterwards | a fable call happens before T plus 24 h, then another after T plus 24 h | the first names the earlier model; the second is preceded by one new listing and names the newer model |
| S-2.11 | N1 | the newest model on the list has `line` opus and the newest fable-line model is older | a Fable-family site calls | it names the fable-line model; an Opus-family site never names a fable-line model and the reverse holds |
| S-2.12 | N1 | a listed model whose `id` text contains `fable` but whose `line` is null or another family, while other listed models carry `line` | a Fable-family site calls | that model is not chosen; family membership comes from `line` |
| S-2.13 | N1 | the fable-line model with the latest `created_at` has `lifecycle` deprecated | a Fable-family site calls | the newest active fable-line model is chosen instead |
| S-2.14 | N1 | the newest fable-line model appears only on a later page (`has_more` true on the first page) | the family is resolved | the later page is read and that model is chosen |
| S-2.15 | N1 | a fable-line model whose `created_at` is the epoch value (release date unknown) beside a dated one | the family is resolved | the dated model is chosen |
| S-2.16 | N1 | two active fable-line models with equal `created_at` | the family is resolved | the one the Models API lists first is chosen, the same one on every resolution of that list |
| S-2.17 | N5 | the fable family is unresolved in the daemon process | ten Fable-family calls start at once from request threads, the scheduler thread and the frontrunner run | one Models API listing sequence is issued and all ten calls name its result |
| S-2.18 | N6 | the family resolved earlier in this process; the Models API now times out, returns 5xx, 429, 403 or 401, or the connection fails | a call of that family happens | the call names the last resolved model; a log record at WARNING or above names the failure type only |
| S-2.19 | N6 | the 24 h re-listing is due and fails | calls continue | each names the last resolved model for the rest of the process lifetime, and the next call after a failed attempt attempts the listing again |
| S-2.20 | N7 | a call at any site | the request is sent | prompt text, system text, tool names and cache breakpoints are the same as at the ref, except the instruction to call the tool and the strict schemas of AC-7 and the request settings of AC-8 and AC-9; each site's `max_tokens` and request timeout are AC-9 request settings and may change (D-7) |
| S-2.21 | N8 | a Frontrunner Builder or weekly scheduler run in progress, holding a resolved fable model, when a re-listing in the same process picks a newer one | the run makes its later calls | every call in that run names the model it started with, so the run's provenance names one model |
| S-2.22 | N6 | a resolved, non-override model that the Messages API now answers with 404 not_found (retired, withdrawn or not served to this key) | any site of that family calls | the process discards that resolution, lists again (shared by concurrent callers), and sends the call once more on the newly resolved model if it differs; if the new listing names the same model or fails, the action degrades with `model_unavailable` as in AC-4 |
| S-2.23 | N7 | the no-trade AST scans (`tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`) | a call to `composer_draft_client.save_symphony`, an order call or a `LIVE_EXECUTION` read is planted in the code that resolves models | at least one scan fails; without the plant all pass |
| S-2.25 | N1 | no listed model carries a `line` key; the list holds an ID starting with `claude-fable-` and an ID that contains `fable` but not as that prefix | a Fable-family site calls | the family is taken from the `claude-fable-` ID prefix: the prefixed model is eligible and the other is not |
| S-2.26 | N1 | listed models carry `line` but no `lifecycle` key | a Fable-family site calls | every listed fable-line model counts as active, and the one with the latest `created_at` is chosen |
| S-2.24 | N7 | the Retirement Recommender's LLM-free import ban (`tests/app/test_retirement_producer_explainer_wiring.py:53-58`, from `feature-plans/retirement-visibility.md` AC-9) | an import of the model-resolving code is planted in `advisors/retirement_recommender.py` | the ban fails; without the plant it passes |

Negative coverage: N1 S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.25, S-2.26 | N2 covered by AC-8 | N3 covered by AC-4 | N4 n/a: resolution uses the server's own API key with no user identity, and the routes keep the global auth and CSRF hooks unchanged | N5 S-2.17 | N6 S-2.18, S-2.19, S-2.22 | N7 S-2.20, S-2.23, S-2.24 | N8 S-2.21

### AC-3: The nightly council launches with the generic tag `opus`
Rule: When `prism_scheduler.py` launches the Market Prism council, the `claude` command line shall pass `--model opus` and no versioned model ID.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a council run is due | the scheduler launches the council | the argument after `--model` on the launched command line is exactly `opus`, and no argument matches the AC-1 pattern |
| S-3.2 | N7 | `ANTHROPIC_API_KEY` is set in the scheduler's environment | the council subprocess starts | its environment lacks `ANTHROPIC_API_KEY` (`prism_scheduler.py:271-274`), and `--max-budget-usd 15.0` (`prism_scheduler.py:39`), `--output-format json`, `--dangerously-skip-permissions` and the prompt stay as at the ref |
| S-3.3 | N1 | `ADVISOR_SYNTHESIS_MODEL` holds a model ID | the council launches | the command line still passes `--model opus`; the API-side overrides do not reach the CLI path |
| S-3.4 | N6 | the `claude` CLI exits non-zero (for example it does not recognize the tag) | the attempt ends | the existing redacted diagnostic is printed, up to 3 attempts run (`prism_scheduler.py:33`), the scheduler exits 1 after the last failure, and the scheduler writes no MARKET_PRISM row |
| S-3.5 | N5 | a MARKET_PRISM row already exists for tonight | the scheduler starts again | the existing idempotency guard skips the run; no second council launches |
| S-3.6 | N7 | the six council agent files | the change is applied | each still reads `model: opus` at line 5 |

Negative coverage: N1 S-3.3 | N2 n/a: classifier refusals inside the CLI session are the CLI's own handling, unchanged by this plan | N3 n/a: the CLI resolves its tag itself and the scheduler computes no model value | N4 S-3.2 | N5 S-3.5 | N6 S-3.4 | N7 S-3.2, S-3.6 | N8 n/a: the scheduler holds no edit state

### AC-4: A family never resolved degrades honestly
Rule: If an action needs a family's model, no override is set for its knob, and this process has never resolved that family (the listing failed, or it lists no active model of that family), then the action shall send no Messages API request and shall degrade through its existing degraded channel with the reason code `model_unavailable`, naming no model ID and no exception message.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | N3 | fable unresolved and the listing fails | POST `/ai-advisor/suggest` | the response's error text contains `model_unavailable`; no suggestion is returned |
| S-4.2 | N3 | opus unresolved and the listing fails | POST `/ai-advisor/chat/send` | 200 with an `error` text containing `model_unavailable` |
| S-4.3 | N3 | fable unresolved and the listing fails | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 200; `survivors` 0; `message` contains `model_unavailable`; `provenance` is present with `generation_model` the empty string; no `advisor_observations` row is written |
| S-4.4 | N3 | as S-4.3 | POST `/ai-advisor/logic-changes/evaluate` | the same outcome as S-4.3 on that route |
| S-4.5 | N3 | as S-4.3 | POST `/ai-advisor/strategy-builder/run` | built-new contributes no plans, Atlas candidates are still evaluated, the response's `mode_notice` names `model_unavailable` and no model family name, and `provenance.generation_model` is the empty string |
| S-4.6 | N3 | as S-4.3 | a Frontrunner Builder run | no proposal is queued; a log record at WARNING or above names `model_unavailable` |
| S-4.7 | N3 | as S-4.3 | the retirement worker runs | each recommendation is persisted with no explanation, as at the ref when the explainer returns nothing; a log record at WARNING or above names `model_unavailable` |
| S-4.8 | N3 | opus unresolved and the listing fails | the 03:00 lens pipeline runs | one MARKET_PRISM row is written with sentiment `limited-inputs` and a rationale naming `model_unavailable`, in the shape of `advisors/lens_pipeline.py:316` |
| S-4.9 | N3 | fable unresolved and the listing fails | the weekly suggestions scheduler runs | each generator degrades as in S-4.3, S-4.4, S-4.5 and S-4.6, and the process exits normally |
| S-4.10 | N6 | the listing returns 401, 403, 429, 5xx, times out or cannot connect on first use | any site above | the same outcome as its row above; the shown reason is the code only, never the exception message |
| S-4.11 | N1 | the listing succeeds but holds no active model of the family | any site of that family | the same outcome as its row above |
| S-4.12 | N7 | any site above while unresolved | the action runs | no Messages API request is sent at all; no literal model ID is used in its place |
| S-4.13 | N5 | the family is unresolved after a failed listing | several calls of the family arrive, at once and later | concurrent calls share one listing attempt, and the next call after a failure attempts the listing again |
| S-4.14 | B | an asset swap evaluation degraded with `model_unavailable`, and the Models API has since recovered | the operator evaluates again in objective mode | the listing succeeds, the request names the newest fable-line model and the route returns the gate result as at the ref |
| S-4.15 | N7 | fable unresolved and the listing fails | POST `/ai-advisor/asset-swaps/evaluate` in explicit-pair mode (both tickers) | no model call is needed and none is made; the route returns the gate result as at the ref, with no `model_unavailable` |
| S-4.16 | N3 | an objective-mode asset swap result whose `message` carries any generation reason code (`model_unavailable`, `no_tool_call`, `refusal`, `max_tokens`, `override_model_not_found` or `override_request_rejected`) | the Asset Swaps panel renders it | the empty-state title says no swap was generated this run, not that a swap failed the gate; a zero-survivor result with no reason code keeps the title "No swap cleared the gate this run" (`static/ai_advisor_asset_swaps.js:377`) |

Negative coverage: N1 S-4.11 | N2 S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9 (each action refuses to run its model call) | N3 S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.16 | N4 n/a: no user identity enters resolution | N5 S-4.13 | N6 S-4.10 | N7 S-4.12, S-4.15 | N8 n/a: an action holds no edit open; the page open while a family is unresolved is the AC-6 N3 and N8 case

### AC-5: Operator overrides still choose the model
Rule: When `ADVISOR_SUGGESTION_MODEL` or `ADVISOR_SYNTHESIS_MODEL` holds a non-blank value, the sites of that knob shall send that value as the model, and if the API rejects that model or the request sent to it, the action shall degrade with a message naming the variable and shall not substitute the newest model.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | N1 | `ADVISOR_SUGGESTION_MODEL` absent from the environment | a Fable-family site calls | the newest fable-line model is used (AC-2) |
| S-5.2 | N1 | `ADVISOR_SUGGESTION_MODEL` set to the empty string | a Fable-family site calls | treated as absent: the newest fable-line model is used |
| S-5.3 | N1 | `ADVISOR_SUGGESTION_MODEL` set to spaces only | a Fable-family site calls | treated as absent |
| S-5.4 | B | `ADVISOR_SUGGESTION_MODEL` set to a model ID the API serves | POST `/ai-advisor/suggest` | the request names exactly that ID |
| S-5.5 | N6 | `ADVISOR_SUGGESTION_MODEL` set to an ID the API answers with 404 not_found | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 200; `message` names `ADVISOR_SUGGESTION_MODEL` and the reason code `override_model_not_found`; no retry on the newest model; nothing is persisted |
| S-5.6 | N1 | `ADVISOR_SUGGESTION_MODEL` set to an opus-line ID | a Fable-family site calls | that ID is used verbatim; the override decides across families |
| S-5.7 | N6 | an override is set and the Models API is unreachable | a site of that knob calls | the override is used; no listing is needed for that knob |
| S-5.8 | N7 | only `ADVISOR_SYNTHESIS_MODEL` set | a Fable-family site calls | it uses the newest fable-line model; setting one knob never changes the other knob's sites |
| S-5.9 | N4 | an authenticated dashboard session | POST `/api/settings` with `ADVISOR_SUGGESTION_MODEL` in `globals` | 400, the key is rejected by `_SETTINGS_WRITE_ALLOWLIST` (`app.py:6118-6124`), and `.env` is unchanged |
| S-5.10 | B | `ADVISOR_SYNTHESIS_MODEL` set to a served ID | POST `/ai-advisor/chat/send` | the request names exactly that ID |
| S-5.11 | B | `ADVISOR_SUGGESTION_MODEL` set to a served ID | a Frontrunner Builder run | each overlay request names exactly that ID |
| S-5.12 | B | `ADVISOR_SYNTHESIS_MODEL` set to a served ID | the 03:00 lens pipeline runs | the synthesis request names exactly that ID |
| S-5.13 | B | `ADVISOR_SUGGESTION_MODEL` set to a served ID | the retirement worker runs | the explainer request names exactly that ID |
| S-5.14 | B | as S-5.13 | POST `/ai-advisor/logic-changes/evaluate` | the generation request names exactly that ID |
| S-5.15 | B | as S-5.13 | POST `/ai-advisor/strategy-builder/run` | the build-plan request names exactly that ID |
| S-5.16 | B | as S-5.13 | the weekly suggestions scheduler runs | every generation request names exactly that ID |
| S-5.17 | B | as S-5.13 | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the generation request names exactly that ID |
| S-5.18 | N6 | `ADVISOR_SYNTHESIS_MODEL` names a served model that answers 400 invalid_request to the request's effort, strict tool or fallback setting | POST `/ai-advisor/chat/send` | 200 with an `error` naming `ADVISOR_SYNTHESIS_MODEL` and the reason code `override_request_rejected`; no retry on another model |

Negative coverage: N1 S-5.1, S-5.2, S-5.3, S-5.6 | N2 S-5.5, S-5.18 | N3 covered by AC-4 | N4 S-5.9 | N5 n/a: the override is read per call with no shared state to race | N6 S-5.5, S-5.7, S-5.18 | N7 S-5.8 | N8 n/a: an environment value is not edited through the product

### AC-6: Badges, copy, provenance and audit rows name the model actually resolved or used
Rule: When the AI Advisor page renders, a run reports its model or an accept or reject decision is recorded, the page and the Strategy Builder notice shall show the Models API `display_name` of each family's resolved model (or the raw override), `provenance.generation_model` shall hold the ID from the `model` field of the last reply the run received, or, when the run made no model call, the knob's override value when one is set and otherwise the family's resolved ID or the empty string, and the audit row's `model_id` shall hold the `ADVISOR_SUGGESTION_MODEL` override value when one is set and otherwise the suggestion family's resolved ID or the empty string; text for an unresolved model reads `model unavailable`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the daemon has resolved both families | GET `/ai-advisor` | the Asset Swaps, Logic Changes and Strategy Builder badges and the run-controls note (`templates/ai_advisor.html:1248`, `:1258`, `:1278`, `:2188`) show the fable model's `display_name`; the Chat badge and the Market Prism badge (`:1268`, `:1318`) show the opus model's `display_name` |
| S-6.2 | B | a newer model has been released and the family re-listed | GET `/ai-advisor` | the badges show the new `display_name` with no code change; no ID to name map exists in source (the map at `app.py:6527-6534` is gone) |
| S-6.3 | B | an objective-mode asset swap, a logic change or a Strategy Builder run receives a reply that produces output | the route returns | `provenance.generation_model` equals that reply's `model` field, and the result panel's model line (`static/ai_advisor_asset_swaps.js:336`, `static/ai_advisor_logic_changes.js:342`, `static/ai_advisor.js:893`) shows it |
| S-6.4 | N6 | the requested model refused and a fallback model answered (AC-8) | the run returns | `generation_model` names the fallback model from the reply's `model` field, not the requested model |
| S-6.5 | N3 | a family is unresolved | GET `/ai-advisor` | the page returns 200 and each badge of that family reads `model unavailable` |
| S-6.6 | N3 | the fable family is unresolved | an objective-mode asset swap takes an early-error path before generation (for example no Composer key) | `provenance.generation_model` is the empty string and the panel's model line reads `model unavailable` |
| S-6.7 | N6 | the Models API is slow or unreachable | GET `/ai-advisor` | the render does not wait on the Models API; badges show the last resolved name, or `model unavailable` if none |
| S-6.8 | N1 | an override names an ID the Models API list does not contain | GET `/ai-advisor` | the badge shows the raw override value |
| S-6.9 | N1 | a `display_name` containing HTML special characters | the page or panel renders it | it is escaped by template autoescape or the existing escape helpers and renders as text |
| S-6.10 | N7 | `llm_suggestions` rows written before the change | the change deploys | those rows keep the `model_id` they were written with; no backfill runs |
| S-6.11 | B | the suggestion family is resolved | POST `/ai-advisor/accept` or `/ai-advisor/reject` (`app.py:8705`, `:8738`) | the `llm_suggestions` row's `model_id` holds the resolved suggestion-family ID (at the ref it holds the synthesis-family value), and the accept route's config write and its four gates run as at the ref |
| S-6.18 | N1 | `ADVISOR_SUGGESTION_MODEL` holds a non-blank value | POST `/ai-advisor/accept` or `/ai-advisor/reject` | the row's `model_id` holds that override value verbatim, whether or not the suggestion family was ever resolved, and the accept route's config write and its four gates run as at the ref |
| S-6.12 | N6 | the Models API is unreachable and the suggestion family was never resolved | POST `/ai-advisor/accept` | the route does not wait on the Models API; the config write and gates run as at the ref and the row's `model_id` is the empty string |
| S-6.13 | B | a Strategy Builder run in which built-new produced no plans | the route returns | `mode_notice` names the resolved suggestion-family `display_name` (or `model unavailable`) and the generator's reason code, and never the word Opus for a Fable-family call (at the ref it reads "Opus generation produced 0 plans", `app.py:8091`) |
| S-6.14 | B | the daemon has just restarted, the Models API is reachable and no operator action has happened | GET `/ai-advisor` after startup completes | both families' badges show resolved display names; the page did not need a chat or generation call first |
| S-6.15 | N2 | a refusal with no fallback ends an objective-mode asset swap run | the route returns | `generation_model` names the refusing reply's `model` |
| S-6.16 | N6 | an objective-mode asset swap run ends with `no_tool_call` | the route returns | `generation_model` names the last reply's `model` |
| S-6.17 | N1 | the fable family is resolved | POST `/ai-advisor/asset-swaps/evaluate` in explicit-pair mode | no model call runs; `generation_model` holds the resolved fable-line ID, as the field held the accessor value at the ref |
| S-6.19 | N1 | `ADVISOR_SUGGESTION_MODEL` holds a non-blank value | POST `/ai-advisor/asset-swaps/evaluate` in explicit-pair mode, or an objective-mode run that ends on an early-error path before generation | no model call runs; `generation_model` holds that override value verbatim |

Negative coverage: N1 S-6.8, S-6.9, S-6.17, S-6.18, S-6.19 | N2 S-6.15 | N3 S-6.5, S-6.6 | N4 n/a: the page and the accept and reject routes keep the global auth and CSRF hooks unchanged | N5 n/a: the render and the audit write read the last resolved value and race nothing; concurrent resolution is the AC-2 N5 case | N6 S-6.4, S-6.7, S-6.12, S-6.16 | N7 S-6.10, S-6.11 | N8 S-6.5 (the page is open while the family is still unresolved)

### AC-7: Tool-emitting generators do not force a tool call
Rule: When the asset swap, logic change, build-plan or frontrunner overlay generator calls the model, the request shall carry `tool_choice` of type `auto` and a prompt instruction to call the tool, the asset swap and logic change requests shall also carry a strict tool definition, and if a reply holds no tool call, the generator shall try again up to 3 attempts in total and then degrade with the reason code `no_tool_call`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the model replies with the tool call | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the request's `tool_choice` type is `auto` (never `tool` or `any`) and the swap pairs are parsed as at the ref |
| S-7.2 | B | as S-7.1 | POST `/ai-advisor/logic-changes/evaluate` | as S-7.1 for logic edits |
| S-7.3 | B | as S-7.1 | POST `/ai-advisor/strategy-builder/run` | as S-7.1 for build plans |
| S-7.4 | B | as S-7.1 | a Frontrunner Builder run | as S-7.1 for overlays |
| S-7.5 | N6 | every reply is text only | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 3 requests are sent, then 200 with `survivors` 0, `message` containing `no_tool_call`, provenance present, and nothing persisted |
| S-7.6 | N6 | as S-7.5 | POST `/ai-advisor/logic-changes/evaluate` | as S-7.5 on that route |
| S-7.7 | N6 | as S-7.5 | POST `/ai-advisor/strategy-builder/run` | 3 build-plan requests, then `mode_notice` names `no_tool_call` (at the ref one reply without a tool call ends generation with `NoToolUseBlock`, `advisors/build_plan_generator.py:1143-1144`) |
| S-7.8 | N6 | as S-7.5 | a Frontrunner Builder run | 3 overlay requests as at the ref, then no proposal is queued and a log record at WARNING or above names `no_tool_call` (the reason read `NoToolUseBlock` at the ref) |
| S-7.9 | N1 | the first reply has no tool call and the second has one | any of the four generators | 2 requests; the second reply's tool input is used |
| S-7.10 | N1 | attempt 1 truncated, attempt 2 without a tool call, attempt 3 with one | build plans or overlays | 3 requests in total; the third reply is used; truncation and missing tool calls share the bound at these two sites |
| S-7.11 | N7 | the change is applied | any of the four generators sends a request | the cache breakpoints at `advisors/build_plan_generator.py:1100-1102` and `advisors/frontrunner_builder.py:940-942` are present, build plans and overlays still stop after 3 attempts, and tool input is validated as at the ref (held tickers and tradeable universe at `advisors/asset_swap_engine.py:1194-1205`, the VIX check and tree validation) |
| S-7.12 | N7 | a reply's tool input names an asset the symphony does not hold | asset swap generation | the pair is dropped as at the ref; tool input stays untrusted |
| S-7.13 | B | as S-7.1 | the weekly suggestions scheduler runs | every Strategy Builder, asset swap, logic change and frontrunner request carries `tool_choice` type `auto` |
| S-7.14 | N7 | the asset swap or logic change generator | a request is sent | its tool definition is marked strict and every object node in the tool's input schema sets `additionalProperties` to false (none does at the ref, Known fact 6), and the tool's property names and types are otherwise as at the ref |
| S-7.15 | N7 | the build-plan or frontrunner overlay generator | a request is sent | its tool definition is not marked strict and its open `rhs` and `condition` objects stay open as at the ref; the tool input is still parsed and validated as at the ref, and a reply without a tool call is still the `no_tool_call` outcome |

Negative coverage: N1 S-7.9, S-7.10 | N2 covered by AC-8 | N3 covered by AC-4 | N4 n/a: generation runs with the server's key and no user identity | N5 n/a: each call's attempts are its own, with no state shared between calls | N6 S-7.5, S-7.6, S-7.7, S-7.8 | N7 S-7.11, S-7.12, S-7.14, S-7.15 | N8 n/a: a generation call holds no edit open

### AC-8: Refusals are retried on a fallback model, then reported
Rule: If the model declines a request (`stop_reason` `refusal`), then the call shall be served by Anthropic's server-side fallback where one exists for the refusal category, and otherwise the action shall degrade with the reason code `refusal` and the category when it is not null, keeping no partial output.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.16 | B | the primary model answers without refusing | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the request carries the server-side fallback setting, the primary reply is used as at the ref and provenance names the primary model |
| S-8.1 | N2 | the primary model refuses and a fallback model answers | POST `/ai-advisor/suggest` | suggestions from the fallback reply are returned |
| S-8.2 | N2 | as S-8.1 | POST `/ai-advisor/chat/send` | the fallback reply's text is the answer |
| S-8.3 | N2 | as S-8.1 | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | candidates come from the fallback reply and provenance names the fallback model (S-6.4) |
| S-8.4 | N2 | as S-8.1 | POST `/ai-advisor/logic-changes/evaluate` | as S-8.3 on that route |
| S-8.5 | N2 | as S-8.1 | POST `/ai-advisor/strategy-builder/run` | plans come from the fallback reply and provenance names the fallback model |
| S-8.6 | N2 | as S-8.1 | a Frontrunner Builder run | the overlay comes from the fallback reply |
| S-8.7 | N2 | as S-8.1 | the retirement worker runs | the explanation is the fallback reply's text |
| S-8.8 | N2 | as S-8.1 | the 03:00 lens pipeline runs | the sentiment is parsed from the fallback reply |
| S-8.9 | N2 | a refusal whose `stop_details.category` is `reasoning_extraction`, which server-side fallback does not retry | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 200, `survivors` 0, `message` contains `refusal` and `reasoning_extraction`; nothing persisted |
| S-8.10 | N1 | a refusal whose `stop_details.category` is null | POST `/ai-advisor/chat/send` | the error text contains `refusal` with no category text |
| S-8.11 | N2 | the primary and every fallback model refuse | POST `/ai-advisor/suggest` | the error text contains `refusal` and the last refusal's category |
| S-8.12 | N2 | a refusal arrives after partial text or partial tool input | any site | the partial output is discarded; nothing from it is shown or persisted |
| S-8.13 | N7 | any of the eight sites, including each retry inside the build-plan and overlay loops | each request goes on the wire | its body carries `fallbacks` set to `default` and its headers carry the beta `server-side-fallback-2026-07-01` |
| S-8.14 | N5 | a refusal with no fallback | build-plan or overlay generation | the same request is not sent again to the same model; the generator stops with `refusal` without spending its remaining attempts |
| S-8.15 | N2 | one generator call in a weekly run is refused with no fallback | the weekly suggestions scheduler runs | that symphony or objective degrades with `refusal`, the others continue, and the process exits normally |

Negative coverage: N1 S-8.10 | N2 S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.11, S-8.12, S-8.15 | N3 covered by AC-4 | N4 n/a: refusal handling has no user permission dimension | N5 S-8.14 | N6 S-8.9 (the fallback is not attempted for that category) | N7 S-8.13 | N8 n/a: no edit is held open during a call

### AC-9: Requests and replies fit models that always think
Rule: When any of the eight Messages API sites calls the model, the request shall set `effort` explicitly and send no `temperature`, `top_p`, `top_k`, `budget_tokens`, `thinking` disabled or assistant prefill, the site shall read its answer only from blocks whose type is `text` or `tool_use`, and if a reply stops at `max_tokens` at a site that has no truncation retry at the ref (suggestions, chat, explainer, lens synthesis, asset swaps, logic changes), the action shall degrade at once with the reason code `max_tokens` and show no partial answer.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the reply is a thinking block followed by a text block holding the sentiment JSON | the 03:00 lens pipeline runs | the sentiment and rationale are parsed from the text block (at the ref `advisors/lens_pipeline.py:293` reads `content[0].text`, the thinking block) |
| S-9.2 | B | a reply of thinking then text | POST `/ai-advisor/chat/send` | the answer is the text block only |
| S-9.3 | B | a reply of thinking then text | the retirement worker runs | the explanation is the text block only |
| S-9.4 | B | a structured reply preceded by thinking | POST `/ai-advisor/suggest` | suggestions are parsed |
| S-9.5 | B | a reply of thinking then the tool call | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the tool input is parsed |
| S-9.6 | B | as S-9.5 | POST `/ai-advisor/logic-changes/evaluate` | the tool input is parsed |
| S-9.7 | B | as S-9.5 | POST `/ai-advisor/strategy-builder/run` | the tool input is parsed |
| S-9.8 | B | as S-9.5 | a Frontrunner Builder run | the tool input is parsed |
| S-9.9 | N1 | a reply of a `fallback` block, thinking, then text or the tool call | chat, the explainer and each other site | the answer is read from the text or tool block only; nothing from the `fallback` block appears in the answer, although the SDK builds it as an object with a `text` attribute (Known fact 5) |
| S-9.10 | N7 | each of the eight sites | a request is sent | it carries an explicit `effort` value and none of `temperature`, `top_p`, `top_k`, `budget_tokens`, `thinking` disabled or a trailing assistant message |
| S-9.11 | N1 | a chat reply stops at `max_tokens` after partial text | POST `/ai-advisor/chat/send` | 200 with an `error` containing `max_tokens`; the partial text is not returned (at the ref `advisors/advisor_chat.py:403-419` joins whatever text blocks arrived and returns them as the answer) |
| S-9.12 | N1 | the explainer reply stops at `max_tokens` | the retirement worker runs | no explanation is stored for that recommendation; a log record at WARNING or above names `max_tokens` |
| S-9.13 | N1 | the synthesis reply stops at `max_tokens` | the 03:00 lens pipeline runs | the row is written with `limited-inputs` and a rationale naming `max_tokens` |
| S-9.14 | N1 | the suggestion reply stops at `max_tokens` | POST `/ai-advisor/suggest` | the error text names `max_tokens`; no suggestion is returned |
| S-9.15 | N1 | an asset swap or logic change reply stops at `max_tokens` | POST `/ai-advisor/asset-swaps/evaluate` in objective mode or POST `/ai-advisor/logic-changes/evaluate` | no further request is sent (these sites have no truncation retry at the ref); 200 with `survivors` 0 and `message` naming `max_tokens`; nothing persisted |
| S-9.16 | N7 | build-plan and overlay replies stop at `max_tokens` | the generator runs | it retries up to 3 attempts and ends with its reason text as at the ref (`advisors/build_plan_generator.py:1129-1134`, `advisors/frontrunner_builder.py:955-963`) |
| S-9.17 | N6 | the API answers HTTP 400 invalid_request to a request for a resolved, non-override model | any site | the site degrades through its existing D-1 path with the exception type only |
| S-9.18 | B | the real API on the droplet, one representative input per site at its chosen effort | each of the eight sites runs once | each reply has a `stop_reason` other than `max_tokens` and produces its output; verified in the PM's live merge check on the droplet, not in a development cycle (D-6) |

Negative coverage: N1 S-9.9, S-9.11, S-9.12, S-9.13, S-9.14, S-9.15 | N2 covered by AC-8 | N3 covered by AC-4 | N4 n/a: no user permission dimension | N5 n/a: each request is independent | N6 S-9.17 | N7 S-9.10, S-9.16 | N8 n/a: no edit is held open during a call

## Rule-by-entry-point matrix

Entry points: E1 POST `/ai-advisor/suggest`; E2 POST `/ai-advisor/chat/send`; E3 POST `/ai-advisor/asset-swaps/evaluate` (objective mode unless a cell says otherwise); E4 POST `/ai-advisor/logic-changes/evaluate`; E5 POST `/ai-advisor/strategy-builder/run`; E6 POST `/ai-advisor/frontrunner-builder/run`; E7 the retirement worker (03:45 tick and POST `/ai-advisor/retirement/run`); E8 the 03:00 lens pipeline; E9 `python -m advisors.weekly_suggestions_scheduler` (the weekly `python -m advisors.strategy_builder_scheduler` run is covered by the E9 weekly-run scenarios, with no column of its own, D-8); E10 `prism_scheduler.py`; E11 GET `/ai-advisor`; E12 POST `/ai-advisor/accept`; E13 POST `/ai-advisor/reject`.

| Rule | E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 | E9 | E10 | E11 | E12 | E13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-2 newest family model | S-2.1 | S-2.2 | S-2.3 | S-2.4 | S-2.5 | S-2.6 | S-2.7 | S-2.8 | S-2.9 | n/a: CLI path, covered by the AC-3 row | n/a: the page makes no model call; its badge is the AC-6 case | n/a: no model call | n/a: no model call |
| AC-3 generic tag | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | S-3.1 | n/a: no launch | n/a: no launch | n/a: no launch |
| AC-4 never resolved | S-4.1 | S-4.2 | S-4.3, S-4.15, S-4.16 | S-4.4 | S-4.5 | S-4.6 | S-4.7 | S-4.8 | S-4.9 | n/a: the CLI resolves its tag and calls no Models API | n/a: the page makes no model call; its unresolved badge is the AC-6 case | n/a: no model call; its unresolved audit value is the AC-6 case | n/a: no model call; its unresolved audit value is the AC-6 case |
| AC-5 override | S-5.4 | S-5.10, S-5.18 | S-5.17, S-5.5 | S-5.14 | S-5.15 | S-5.11 | S-5.13 | S-5.12 | S-5.16 | n/a: overrides do not reach the CLI path, per the AC-3 rule | n/a: the page makes no model call; its override badge is the AC-6 case | n/a: no model call | n/a: no model call |
| AC-6 attribution | n/a: the suggest response reports no model | n/a: the chat reply reports no model | S-6.3, S-6.6, S-6.15, S-6.16, S-6.17, S-6.19 | S-6.3 | S-6.3, S-6.13 | n/a: queued proposals report no model | n/a: the explanation reports no model | n/a: the MARKET_PRISM row reports no model | n/a: weekly runs persist no model ID (Known fact 7) | n/a: the council row reports no model | S-6.1, S-6.5, S-6.7, S-6.8, S-6.14 | S-6.11, S-6.12, S-6.18 | S-6.11, S-6.18 |
| AC-7 no forced tool | n/a: structured output, no tool | n/a: plain text | S-7.1, S-7.5, S-7.14 | S-7.2, S-7.6, S-7.14 | S-7.3, S-7.7, S-7.15 | S-7.4, S-7.8, S-7.15 | n/a: plain text | n/a: plain text | S-7.13 | n/a: CLI path | n/a: no model call | n/a: no model call | n/a: no model call |
| AC-8 refusal | S-8.1, S-8.11 | S-8.2, S-8.10 | S-8.16, S-8.3, S-8.9 | S-8.4 | S-8.5 | S-8.6 | S-8.7 | S-8.8 | S-8.15 | n/a: CLI path | n/a: no model call | n/a: no model call | n/a: no model call |
| AC-9 request and reply shape | S-9.4, S-9.14 | S-9.2, S-9.11 | S-9.5, S-9.15 | S-9.6, S-9.15 | S-9.7, S-9.16 | S-9.8, S-9.16 | S-9.3, S-9.12 | S-9.1, S-9.13 | S-9.10 | n/a: CLI path | n/a: no model call | n/a: no model call | n/a: no model call |

## Not Building

Non-goals:
- NB-1: No change to which family a call site uses; the AC-2 family list is the one at the ref (R-30).
- NB-2: No new model feature beyond what the newer models require (no task budgets, no thinking display, no streaming, no Batch API).
- NB-3: No new screen, tab, button or element; only badge text, the Strategy Builder notice, the provenance line, the asset swap empty-state title and degraded message text change (R-11, R-32).
- NB-4: Package version pins in `requirements.txt` (for example `anthropic~=0.85.0` at `requirements.txt:1`) are not unpinned by this plan (excludes part of R-2; approved [PM-ASSUMED]; Q-11). DEP-1 (`tests/security/test_dep1_requirements_pinning.py:55`, `:114-117`) requires a version specifier and accepts a floor such as `>=`, so a floor-only change would satisfy both DEP-1 and G-3; raising the anthropic SDK floor to send the AC-8 settings is a Gate-2 choice the approach raises to the PM (Prerequisites).
- NB-5: removed: the Strategy Builder `mode_notice` family wording is now built by S-6.13.
- NB-6: removed: the accept and reject audit rows now record the suggestion family by S-6.11.
- NB-7: The Market Prism badge keeps reading the synthesis family's resolved model rather than a model recorded by the council run (approved [PM-ASSUMED]; Q-12).

Behavior that stays exactly as it is:
- NB-8: Advisory-only and no-trade boundaries; `tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py` and `tests/security/test_retirement_recommender_no_trade_boundary.py` stay green and cover the new model-resolving code (S-2.23), and no new path reaches `composer_draft_client.save_symphony`, `LIVE_EXECUTION` or an order call.
- NB-9: D-1 never-raises contracts at every site; no exception message reaches a browser-facing string (`tests/security/test_d1_error_disclosure.py` stays green).
- NB-10: Prompt-cache breakpoints (DE-ADVISOR-CACHE-001) at `advisors/build_plan_generator.py:1100-1102` and `advisors/frontrunner_builder.py:940-942`.
- NB-11: Truncation retry bounds: 3 attempts at the build-plan and overlay generators (`advisors/build_plan_generator.py:117`, `advisors/frontrunner_builder.py:94`); no truncation retry is added elsewhere.
- NB-12: Every route's JSON keys and value types; only model values, `mode_notice` text and degraded message text change.
- NB-13: The council's subscription auth, budget cap, retry, idempotency guard and F-4 row verification in `prism_scheduler.py`.
- NB-14: The six council agent files keep `model: opus` (R-25).
- NB-15: Historical rows keep their model IDs; no backfill (R-23).
- NB-16: The two override variables keep their names and stay outside `_SETTINGS_WRITE_ALLOWLIST`.

Deferred:
- NB-17: Prompt audit and effort re-tuning for the newer models (R-31, R-34); this plan sets an effort per site but runs no sweep.
- NB-18: The Playwright and harness version unpin, which lives outside this repository (R-34).
- NB-19: Keeping the droplet's Claude Code CLI current so that `opus` means the newest Opus; the CLI version is droplet operations, not repository code (Known-Unconfirmed 2).

Protected surfaces this consumes but does not change:
- NB-20: `alpha_bot_execution.py` and `math_engine.py` stay byte-unchanged (R-35).
- NB-21: The database schema; no migration (R-35). `llm_suggestions.model_id` is already `TEXT NOT NULL DEFAULT ''` (`migrations/003_llm_suggestions.sql:45`).
- NB-22: The AI Advisor accept route's config write and Gates 1 to 4, POST `/ai-advisor/proposal/approve` (the only route reaching `composer_draft_client.save_symphony`), the retirement approve and reject routes, the panic-sell route and the Managed Sleeves order path.
- NB-23: Repositories other than PlanetStopper (R-27).

## Narrowed mappings

R-2: Stop pinning specific versions of things like this; maps to AC-1, NB-4; left out: covers Claude model versions only; package version pins in `requirements.txt` and the Playwright harness pin are left out (NB-4, NB-18, Q-11)
R-20: The four forced tool calls move to tool_choice auto with the tool marked strict and a prompt instruction to call it; maps to AC-7; left out: all four move to tool_choice auto with the call instruction, but only the asset swap and logic change tools are marked strict; the build-plan and frontrunner overlay tools stay non-strict because their `rhs` and `condition` fields are deliberately open objects (`advisors/build_plan_generator.py:681`, `advisors/frontrunner_builder.py:646-669`) that the compiler reads and validates (`advisors/plan_tree_compiler.py:316-327`) (PM ruling, D-5)
R-29: Spend per call is equal or lower; maps to AC-9; left out: equal or lower spend per call is not guaranteed; Opus sites that ran without thinking on Opus 4.8 now think and thinking is billed as output, so the plan only requires an explicit effort per site (Known-Unconfirmed 7)

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Which family does each call site use? | AC-2 | Each site keeps its current family: Fable for suggestions and generation (suggest, asset swaps, logic changes, build plans, frontrunner overlay, retirement explainer), Opus for chat, lens synthesis and the council | default approved [PM-ASSUMED] |
| Q-2 | What happens when a family was never resolved in the process? | AC-4 | Honest unavailable state with the reason code `model_unavailable`; no literal fallback ID anywhere in production code | default approved [PM-ASSUMED] |
| Q-3 | How often does a process re-resolve? | AC-2 | On first use and whenever the last successful listing is older than 24 h; the last good result is kept for the process lifetime while listings fail | default approved [PM-ASSUMED] |
| Q-4 | The newer models default to lower effort (Opus 5.5 `medium`); what effort does each site send? | AC-9 | Every site sets effort explicitly so quality does not drop silently; the value per site is chosen at Gate 2 | default approved [PM-ASSUMED] |
| Q-5 | Do the operator overrides stay, and what do they cover? | AC-5 | They stay as optional operator choice; unset or blank means newest; `ADVISOR_SUGGESTION_MODEL` also drives the Frontrunner Builder, which at the ref used its own constant (`advisors/frontrunner_builder.py:84`); neither drives the council | default approved [PM-ASSUMED] |
| Q-6 | How is a pin kept from coming back? | AC-1 | A source-scan test fails on any `claude-` family-name version-digit ID in production Python, templates, static JS, `.env.example`, `deploy/`, PowerShell and shell scripts, workflows and agent files; tests, docs, plans and fixtures exempt | default approved [PM-ASSUMED] |
| Q-7 | What text marks an unresolved model, and what does the asset swap panel title say when generation produced nothing? | AC-4, AC-6 | Badges, provenance lines and the Strategy Builder notice read `model unavailable`; ID fields hold the empty string; degraded messages carry the code `model_unavailable`; the asset swap title reads "No swap was generated this run" | default approved [PM-ASSUMED] |
| Q-8 | May a page render wait on the Models API? | AC-6 | No: the render never waits on the Models API, and after a daemon restart both badges show resolved names without any operator action | default approved [PM-ASSUMED] |
| Q-9 | What attempt bound do asset swaps and logic changes get for a reply without a tool call (they make one attempt at the ref)? | AC-7 | 3 attempts, the bound build plans and overlays already use, accepting a worst case of about 90 s per click (Breaking Changes) | default approved [PM-ASSUMED] |
| Q-10 | `DECISIONS.md` DE-PRISM-COUNCIL and the HC-3 test (`tests/ai_advisor/test_prism_scheduling.py:1128-1145`) require the pinned `claude-opus-4-8` and forbid the bare `opus` because an alias can change cost and behavior silently; G-4 and G-5 require `opus`. Which stands? | AC-3 | The operator's later instruction (G-4, G-5) supersedes HC-3; HC-3 is rewritten to assert `opus` | [PM-ASSUMED] The operator's later instruction stands: the council passes `--model opus`, and DE-PRISM-COUNCIL is superseded on this point |
| Q-11 | Does G-3 ("STOP PINNING SPECIFIC FUCKING VERSIONS OF SHIT LIKE THIS") reach package versions in `requirements.txt`? DEP-1 requires a specifier but accepts a floor such as `>=`, so floors would satisfy both | plan | No: this plan covers Claude model versions only; moving package pins to floors is a separate follow-up the operator decides | [PM-ASSUMED] Not decided in this plan: NB-4 is approved as written, package pins stay out of this plan, and the operator decides separately |
| Q-12 | Should the Market Prism badge name the model recorded by the council run instead of the synthesis family's resolved model? | AC-6 | No: the badge names the resolved opus-line model; recording the council's model is a follow-up | default approved [PM-ASSUMED] |
| Q-13 | How is family and eligibility decided if the Models API returns no `line` or no `lifecycle` to this key? | AC-2 | When no listed model carries `line`, a model belongs to a family when its ID starts with `claude-`, the family name and a hyphen; when `lifecycle` is absent, every listed model counts as active (the list omits retired models by default) | default approved [PM-ASSUMED] |
| Q-14 | What happens when a resolved model starts answering 404? | AC-2 | Discard that resolution, list again once (shared by concurrent callers), repeat the call once if the new listing names a different model, otherwise degrade with `model_unavailable` | default approved [PM-ASSUMED] |
| Q-15 | Do the effort, strict tool and fallback settings apply to an override model, which may not accept them? | AC-5 | Yes: every request carries them; an override model that rejects them degrades with `override_request_rejected` naming the variable | default approved [PM-ASSUMED] |
