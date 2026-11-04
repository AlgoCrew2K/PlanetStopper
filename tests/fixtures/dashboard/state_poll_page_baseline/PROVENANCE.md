# state_poll_page_baseline: provenance

`baseline.json` holds what the dashboard `/` showed and requested at the reference commit `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`, one record per scenario of `tests/ui/_state_poll_page_capture.py` (page text, detail panel, footer, Risk Math velocity, the `/api/state` requests of one poll, final path, header label). It is producer output: `tests/ui/produce_state_poll_page_baseline.py` runs each scenario through the capture module the checking test also uses, on the app in this process over a per-test database seeded by the real writers from the records of `tests/fixtures/state_poll_no_table/`, with the route clock and the browser clock fixed at the world's instant and outbound requests refused.

- Reference commit: `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`; the producer refuses unless `git diff --quiet` against it over `app.py templates static analytics.py database.py market_calendar.py` exits 0 and `git status --porcelain` on those paths is empty (it ran on the RED tree, whose production paths equal the reference).
- Command: `BASELINE_OUT=<existing directory> python -m pytest -m browser -n0 tests/ui/produce_state_poll_page_baseline.py`
- Captured at: 2026-11-04T00:10:08Z (Firefox 148.0.2 through Playwright, headless). The check compares the browser version and fails naming a mismatch.
- Scenarios: closed_first_poll_narrow_dark, closed_first_poll_wide_light, no_state_first_load, open_detail_panel_poll, open_fallback_poll, open_first_poll_wide_light, open_poll_answered_401, open_poll_answered_500, open_sse_refresh.
- Viewports and themes: 1440x900 light for every scenario except `closed_first_poll_narrow_dark` (375x812, dark).
- Determinism: two runs in separate processes gave equal records (the capture waits until no request has been in flight for 600 ms before it reads, so late answers are written).
- sha256 of `baseline.json`: `42761afa95404d92a2c76cf71f46b6e4ae61fffd50c71f04a3ea8d98b825e99f`
- PM sign-off: not needed (producer output).

To regenerate: `git worktree add --detach <tmp> 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`, copy the producer, `tests/ui/_state_poll_page_capture.py`, `tests/app/_state_poll_worlds.py` and `tests/fixtures/state_poll_no_table/` (records) into the same relative paths, and run the command above there. The producer lives in `tests/ui/` so that the directory conftest applies to it as it does to the test.
