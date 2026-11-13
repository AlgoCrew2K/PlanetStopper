# Provenance of the page-explainers fixtures

Three records live here. Each is a golden written once by a committed script; none is built to
match a parser or a test.

## copy.json (the expected text of every copy row, Glossary line and Glossary title)

- **Sources**, both read through `git show` and never from the working tree:
  - the plan `feature-plans/aligned-tables-and-quiet-copy.md`, read at commit
    `75243235526c367d6b09b0da245b7794f8ddf63d` (`75243235`, "record the PM's slice-review rulings R1
    for the fix round"). The tables read (Copy table A, Copy table X, the Values table, Table G,
    Table G2 and Table G3) equal the plan as approved at `f1d07c75` (itself byte-equal to `ac4563cb`)
    apart from what the R1 rulings add: Copy table X rows CX-15 to CX-20, the Frontrunner Builder
    Glossary's X11 split into X11a to X11c, History's four exit reason lines and their Table G3
    titles. `extract_copy.py --check 75243235` reports no difference;
  - the reference commit's own fixture, `tests/fixtures/page_explainers/copy.json` at commit
    `c0760e70eb6f8c6620002570b493bb9ca4ec6b1e` (`c0760e70`), and the reference template
    `templates/ai_advisor.html` at the same commit (the guardrail paragraph the three Overview
    guardrail lines come from).
- **Producer:** `extract_copy.py` in this directory. Command, from the repository root:
  `python tests/fixtures/page_explainers/extract_copy.py`.
- **Check against a plan revision:** `python tests/fixtures/page_explainers/extract_copy.py --check <sha>`
  re-extracts from the plan at `<sha>` and prints every row, Glossary host or title that differs from
  `copy.json`. A plan revision after approval is a new dispatch that re-extracts; no test reads the
  plan or runs git.
- **What it holds:** `rows` (Copy table A, 55 rows, and Copy table X, 20 rows, in plan order, each
  with its disposition, its exact new text or none, and a kept row's text at the reference);
  `glossary` (14 hosts, 187 lines in Table G order, each the reference row's text or its Table G2
  text); `titles` (the 16 Table G3 rows with their expanded line keys); `values` (the reference's slot
  rows with the two the plan's Values table changes, the screen threshold in R02 and the tenure
  threshold in R22); `ref_rows` and `ref_values` (the reference fixture's own, unchanged, served by
  the accessors `tests/ui/_page_copy.py` keeps for every test no cycle has re-pinned yet).
- **What is resolved at extraction time:** a Copy table A row reading "(unchanged)" takes the text
  the reference fixture's row of the same key shows; Table G2's O31a, O31b and O31c are the first three
  sentences of the reference's guardrail paragraph, each after its "Term: " prefix without the term's
  own leading name and without a following "is" or "are", every other word kept; a Table G3 cell
  naming lines ("F15a to F15g; F15h") is expanded into `line_keys`. A row the script cannot parse
  stops it with the row's key.
- **Counts at extraction:** 75 rows, 187 Glossary lines in 14 hosts, 16 title rows, 8 slot rows.

## ref_capture.json (what the pages show, request and pin at the reference)

- **Reference commit:** `c0760e70`, the base this change builds on. The capture ran in the shared
  worktree of the change, on a branch whose production paths (`app.py`, `templates/`, `static/`,
  `analytics.py`, `database.py`, `market_calendar.py`, `alpha_bot_execution.py`, `math_engine.py`,
  `reporting.py`, `advisors/tenure_review.py` and `migrations/`) equal `c0760e70` with no uncommitted
  change; the producer refuses otherwise, and it was run before any production file changed.
- **Producer:** `produce_ref_capture.py` in this directory, with the reading code in
  `tests/ui/_page_explainers.py` (the same module the checks import) and the table registry and
  stacked-form reading in `tests/ui/_alignment.py`. Command: `python tests/fixtures/page_explainers/produce_ref_capture.py`.
- **Worlds** (`seed_world`): `empty` (no state), `dashboard_full` (the captured droplet state through
  `database.save_state` plus one CVaR diagnostic through `database.record_cvar_diagnostic`, no
  sleeves), `dashboard_sleeves` (the same plus one sleeve and one rule through `database.create_sleeve`
  and `database.create_sleeve_rule`), and `full` (the same plus the captured post-mortem days, a
  pending Frontrunner proposal over the captured incumbent tree, one weekly-suggestion survivor, two
  autotune runs per symphony, the captured Market Prism row, the captured retirement run summary and
  a stored tenure review record). Every figure the CVaR row carries is synthetic (no captured CVaR
  row exists); every other record is captured droplet output or the real writer's shape. The app clock
  is fixed at 2026-10-21 11:00 ET and the date windows at 2026-07-01 (`tests/ui/_heading_margin_capture.py`),
  and the CSRF token every page embeds is fixed.
- **What it holds:** per world and host, the figure-element texts and the control inventory at 1920,
  1440 and 390 px, and the stacked-row form of every table of the registry at 1000, 390 and 320 px
  (how the table, its rows and its cells are laid out and whether its header shows, never what they say;
  on the Advisor every tab is chosen in turn); per world, the section-heading order of every host, the
  banner markup, and the bodies of the read-only routes (`/api/state`, `/api/performance`,
  `/api/history/30`, `/api/retirement-recommendations`, `/api/guard-alpha-summary`,
  `/api/autotune-runs`, `/api/settings` and `/health`; a top-level key that differs between two reads
  in one process is recorded as volatile and left out); once, the login and logout pages, one Discord
  payload (`reporting.send_eod_discord_post` over the captured day `2026-06-30`, with `requests.post`
  replaced at the network boundary), the SHA-256 (CRLF normalized to LF) of `alpha_bot_execution.py`,
  `math_engine.py`, `database.py` and `advisors/tenure_review.py` at `c0760e70`, the migration listing
  at `c0760e70`, the hashes of `tests/app/test_dashboard_no_order_path.py` and the five
  `tests/security/*no_trade_boundary*.py` files at `c0760e70`, the route table and hooks
  (`tests/_hygiene_surface_facts.py`), the AST digest of `run_scheduler`, the template buttons no script
  handles, and the rendered text of the five kept rows.
- **Not recorded:** document heights (the earlier height bound is not carried); Strategy Builder
  survivor and backtest-failure rows and the five-card Frontrunner case (the cycle that reads them
  writes them at test time through the real writers).
- **Determinism:** two runs of the producer give the same file apart from the capture time. The header
  says "Degraded" once the process has been up two minutes with no scheduler tick, so each reading
  starts that clock afresh, in the capture and in the checks alike.
- **Browser:** Firefox 148.0.2 through Playwright. A different version is a different render: the
  browser checks fail with a recapture message instead of comparing.
- **Size note:** the file is a golden record like `tests/fixtures/dashboard/heading_margin_baseline/baseline.json`,
  not a dataset a test asserts counts over.

## ref_token_inventory.json (the markup tokens the pages carried at the reference)

- **Reference commit:** `c0760e70`, read with `git show c0760e70:<path>` for each page template and
  script (the working tree is never read, and the checks never call git).
- **Producer:** `produce_ref_token_inventory.py` in this directory. Command:
  `python tests/fixtures/page_explainers/produce_ref_token_inventory.py`.
- **What it holds:** per file, the literal `data-testid`, other `data-*`, `id` and `class` tokens, the
  three contract names, and the literal `title` attribute values. The three controls an earlier change
  removed ("View logs", "Go to cash", "Edit variables") are not in the markup at this reference, so
  `removed_control_tokens` is empty.

## Worlds built inside a test module (Performance and History)

No file is committed for these; each is written at test time through a real writer and named here
so a reviewer can see what is captured and what is changed.

- **History with all four exit reasons** (`tests/ui/test_page_copy_performance_history.py`,
  `seed_history_with_all_four_reasons`): `tests/ui/_shell_support.seed_history_exits` writes the
  captured post-mortem days (`tests/fixtures/prod_droplet/post_mortems/`); none of them holds a
  `Trailing Stop` exit (the captured reasons are `Take-Profit`, `VWAP Breakdown` and `VWAP Bleed
  Cut`). The one field `exit_reason` of one captured `Take-Profit` trigger of 2026-06-23 is changed
  to `Trailing Stop`; nothing else in the record changes. The route body the page reads is the real
  `/api/history` answer over those files. A second case edits one exit's `reason` in that body to
  `Manual override` (a reason no map knows) to show an unmapped reason is shown as sent.
- **Performance with 11 precondition rows** (`tests/ui/test_page_explainers_performance_history_browser.py`,
  `_usable_preconditions_body`): the real `/api/guard-alpha-preconditions` answer over the captured
  shadow rows (13 days, below the 40-observation floor, so every verdict is `INSUFFICIENT_DATA` and the
  page draws no row); each symphony's shadow `verdict` is changed to `NOT_MET`, every other field is
  the route's own. Synthetic in that one field, so the panel's populated state can be measured.
