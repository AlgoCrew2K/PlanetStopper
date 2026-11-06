# Provenance of the page-explainers fixtures

Two records live here. Both are goldens written once by a committed script; neither is built to
match a parser or a test.

## copy.json (the expected text of every copy row)

- **Source:** the approved plan `feature-plans/page-explainers-and-density.md`, Copy tables A, B and
  C and the Values table, read through `git show` at commit
  `2681e6935d46c6b159d71f040ba92753bc87376a` (`2681e693`, "approve page-explainers-and-density").
- **Producer:** `extract_copy.py` in this directory. Command, from the repository root, in two passes
  (the ref capture needs Copy table C's old texts and the extraction needs the ref capture's kept
  rows):
  1. `python tests/fixtures/page_explainers/extract_copy.py --pass-one`
  2. (the ref capture below)
  3. `python tests/fixtures/page_explainers/extract_copy.py`
- **Check against a plan revision:** `python tests/fixtures/page_explainers/extract_copy.py --check <sha>`
  re-extracts from the plan at `<sha>` and prints every row that differs from `copy.json`. A plan
  revision after approval is a new dispatch that re-extracts; no test reads the plan.
- **What is resolved at extraction time:** a Copy table B definition that names a Copy table A
  description takes that text; Y14b takes the D29a to D29d definitions without their leading name;
  "resolved: ...; unresolved: ..." pairs are split; the per-state sentences of H02 and H08 are split
  into `states`; a criterion reference after a text (`(AC-8)`) is moved to `annotation`; a kept row
  whose ref text the plan gives as a pointer takes the rendered text in `ref_capture.json`
  (`kept_rows`). A row the script cannot parse stops it with the row's key.
- **Counts at extraction:** 276 rows (A, B and C) and 8 slot rows.

## ref_capture.json (what the pages show, request and pin at the reference)

- **Reference commit:** `1988ce5f` ("test(ui): credential-free probe orders imports so a clock stamp
  is never read as set by the environment"). The capture ran in a throwaway worktree at `7e6afd02`,
  whose production paths (`app.py`, `templates/`, `static/`, `analytics.py`, `market_calendar.py`,
  `alpha_bot_execution.py`, `math_engine.py`, `reporting.py`) equal `1988ce5f`; the producer refuses
  otherwise. `database.py` is the one path allowed to differ, because the merged security head adds
  lines to it: it is pinned at the build base `8f8be28d` (S-5.13), as the approach's Items Raised
  records.
- **Producer:** `produce_ref_capture.py` in this directory, with the reading code in
  `tests/ui/_page_explainers.py` (the same module the checks import). Commands: see the producer's
  docstring (create the worktree with `git worktree add --detach <path> 7e6afd02`, copy the producer,
  `copy.json` and `tests/ui/_page_explainers.py` in, run it there, remove the worktree).
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
- **What it holds:** per world and host, the document height and the heights of the elements the copy
  tables reword at 375 px (the AC-7 baseline), the figure-element texts and the control inventory at
  1440 px, the control inventory at 375 px; per world, the section-heading order of every host, the
  banner markup, the bodies of the seven read-only routes (a top-level key that differs between two
  reads in one process is recorded as volatile and left out); once, the login and logout pages, one
  Discord payload (`reporting.send_eod_discord_post` over the captured day `2026-06-30`, with
  `requests.post` replaced at the network boundary), the engine files' hashes at `1988ce5f` and
  `database.py`'s at `8f8be28d` (SHA-256 of the bytes with CRLF normalized to LF), the migration
  listing at `8f8be28d`, the route table and hooks (`tests/_hygiene_surface_facts.py`), the AST digest of `run_scheduler`, the template buttons no
  script handles (S-5.15) and the rendered text of the five kept rows.
- **Not recorded:** Strategy Builder survivor and backtest-failure rows and the five-card Frontrunner
  case; the cycle that reads them writes them at test time through the real writers.
- **Browser:** Firefox 148.0.2 through Playwright. A different version is a different render: the
  browser checks fail with a recapture message instead of comparing.
- **Size note:** the file is a golden record like `tests/fixtures/dashboard/heading_margin_baseline/baseline.json`,
  not a dataset a test asserts counts over.

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
