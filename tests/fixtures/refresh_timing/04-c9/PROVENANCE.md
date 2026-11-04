# 04-c9 fixtures: provenance

Plan: refresh-timing 04, cycle C9 (cross-control close).

## Record: `post_requests_at_base.json` (case: the request each operator POST control sends, at the base)

- Source: each control of the census (`tests/ui/_post_controls.py`) clicked once with fixed inputs over the real
  `static/` scripts and templates: the page controls in `tests/ui/_page_js_harness`, the dashboard Cash Now,
  fleet Dismiss and Managed Sleeves buttons in `tests/dashboard/_index_js_harness`. The POST is answered with a
  plain success; the record is the one request the page sent (URL, method, headers with the token value replaced
  by `<token>`, body, `keepalive`, the init object keys). 25 controls.
- Producer call: `python tests/fixtures/refresh_timing/04-c9/capture_post_requests.py` with `DB_PATH` and
  `ATLAS_CACHE_DB_PATH` set to scratch files. The script refuses to run when `static/`, `templates/` or `app.py`
  differ from `HEAD`.
- Base SHA: `7d8db3cd123397c1d013b693d7921c115e00ef07`; captured at `2026-10-31T09:35:32Z`.
- Scenarios served: S-2.11, S-6.5, S-7.4. Edits: none. Sign-off: not needed (no derived record).

The capture harness account `7c2607c5-4583-4c1b-ae55-e5a05bb49f0d` is invented; it is not a real account id.
