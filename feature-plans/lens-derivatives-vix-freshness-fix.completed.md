# Feature: Derivatives Lens — VIX/VXV Freshness Fix
Status: ready
Created: 2026-06-16

## Summary

The shipped derivatives lens (`advisors/lens_options_proxy.py`, a FRED VIXCLS/VXVCLS volatility
proxy from PR #30) serves a **~6-year-stale** VIX value as if it were current, while reporting
`available=True`. Root cause (verified on origin/main `93910b6`): `_fetch_fred_series` requests
`sort_order="asc"`, `limit=100`, `observation_start="2020-01-01"` — i.e. the **oldest** 100
observations (~Jan–May 2020). `_parse_latest_observation` then walks the list in reverse and
returns ~May-2020 as the "latest" observation, with `as_of_date` reading 2020. Honest-availability
(D-1) only covers fetch *failure*, never *staleness*, so a stale-but-successful fetch flows through
as a confident, wrong market read into the nightly Market Prism.

This fix makes the producer fetch the genuinely most-recent observation AND adds a freshness guard
so a stale latest-observation degrades to `available=False` (honest) instead of fabricating a
current read. Behavior-preserving for the fetch-failure and regime-classification paths.

## Acceptance Criteria

- [ ] **AC-1 (recent-window fetch):** `_fetch_fred_series` returns the genuinely most-recent FRED
  observations for the series, not the oldest-from-2020 batch. The observation that
  `_parse_latest_observation` selects MUST be the most recent valid (non-`"."`) datapoint FRED has
  published, on any given run date.
- [ ] **AC-2 (freshness guard — the core fix):** when the latest valid observation's date is older
  than `_OPTIONS_PROXY_MAX_STALENESS_DAYS` calendar days relative to the run date, the producer
  returns `available=False` with a real `reason` (e.g. `"stale_data"`) and NO fabricated
  vix/regime/risk values. A fresh latest observation (within the window) returns `available=True`
  with the real values. The threshold is a NAMED module-level constant with a source comment.
- [ ] **AC-3 (as_of_date truthful):** the `as_of_date` field in the returned dict equals the real
  date of the selected latest observation (so a 2020 date can never appear on a 2026 run that
  reports available).
- [ ] **AC-4 (honest-availability preserved):** existing fetch-failure / 429-exhausted /
  no-valid-observations paths still return `available=False` with `reason=type(exc).__name__`
  (D-1) and never crash `run_pipeline`. Regime classification + risk read unchanged when data is
  fresh and valid.
- [ ] **AC-5 (deterministic testability):** the run-date used by the freshness comparison is
  injectable/monkeypatchable (e.g. a module-level `_today()` helper) so golden-fixture tests
  evaluate staleness deterministically without depending on the wall clock.
- [ ] **AC-6 (bounded + off-path):** bounded retry (finite `_OPTIONS_PROXY_MAX_ATTEMPTS`, backoff
  cap) and off-execution-path / no-blocking-I/O-on-the-engine-path properties are preserved.

## Architecture

Surface: `advisors/lens_options_proxy.py` only (plus its test file). No schema change, no new route,
no engine-path change. Advisory-only, off-execution-path.

Two viable approaches for AC-1 — implementer chooses; the **contract** (most-recent valid
observation is selected) is what matters:

1. **Recent rolling window (preferred — preserves `_parse_latest_observation` semantics):** keep
   `sort_order="asc"` but replace the hardcoded `observation_start="2020-01-01"` with a rolling
   recent start computed at fetch time (`_today()` minus a named lookback, e.g.
   `_OPTIONS_PROXY_LOOKBACK_DAYS` ≈ 90 calendar days — wide enough to always contain several valid
   trading-day observations across holidays). The ascending list's tail is then the newest, and the
   existing reverse-walk in `_parse_latest_observation` still selects the latest. Drop the
   misleading `# only the most recent batch needed` / `# sufficient historical window` comments.
2. **Descending + limit:** `sort_order="desc"`, small `limit`, no `observation_start`; then
   `_parse_latest_observation` must select from the FRONT (walk forward) since the list is
   newest-first. If this approach is taken, the parse helper MUST be updated accordingly and its
   tests adjusted — do not leave the reverse-walk against a desc list (it would pick the oldest).

Freshness guard (AC-2): after `_parse_latest_observation` yields `(value, date_str)`, compare
`date_str` to `_today()`. If the gap exceeds `_OPTIONS_PROXY_MAX_STALENESS_DAYS`, treat as
unavailable (`available=False`, `reason="stale_data"`), short-circuiting before regime/risk
computation. Apply to the VIX series (the spot that drives the read); document whether the 3-month
(VXVCLS) series gets the same guard (recommended: guard the series actually used for the reported
level; if VXV is stale but VIX fresh, decide per the term-structure computation — document the
choice as [PM-ASSUMED] in the producer docstring).

**Data flow (unchanged except the guard):** `run_pipeline` → derivatives lens → `_fetch_options_proxy`
→ `_fetch_fred_series` (recent window) → `_parse_latest_observation` (latest valid) → **freshness
guard** → `_classify_regime` / `_derive_risk_read` → dict.

## Design-System Mapping

N/A — backend producer, no UI surface. (The Overview tab renders whatever the lens reports; an
honest `available=False` stale state surfaces via the existing informative empty-state.)

## Edge Cases

- **Weekend / holiday run:** the latest VIX observation may legitimately be 1–4 calendar days old.
  The staleness threshold (≈10 days [PM-ASSUMED]) MUST NOT flag these as stale — pick a threshold
  comfortably above the longest normal market closure so the nightly 03:00 run stays available on
  ordinary weekends/holidays.
- **All recent observations are `"."` (missing):** `_parse_latest_observation` returns `None` →
  `available=False` (existing path) — keep.
- **FRED returns fewer rows than the window (new series / sparse):** still selects the latest
  available; freshness guard decides availability honestly.
- **Run-date at/just-after a long closure (e.g. year-end):** covered by the threshold margin; if a
  genuine multi-week data gap occurs, honest `available=False` is the correct, intended outcome.
- **Stale VIX but fresh VXV (or vice-versa):** document the chosen rule; never report a term
  structure mixing a fresh and a 6-year-stale leg.

## Security Considerations

- **No new external surface.** Same FRED endpoint, same `FRED_API_KEY` from `os.environ` (never
  hardcoded). The fix narrows/rolls the date window — no new credential, no new host.
- **Input validation:** response values are parsed as floats only; `"."` markers skipped; no eval /
  template of response content. Date parsing is strict (`datetime.date.fromisoformat`) inside a
  try/except that degrades to unavailable on a malformed date rather than crashing.
- **Data exposure:** advisory values only, stored via existing `advisor_observations` path; D-1
  `type(exc).__name__` on errors; no raw response persisted.
- **Advisory-only / off-execution-path:** never touches `LIVE_EXECUTION`; cannot place a trade.

## Testing Strategy

New/updated tests in `tests/ai_advisor/test_lens_options_proxy.py` (or the existing derivatives
test file — implementer/test-writer confirm the canonical path). All HTTP mocked; NO live FRED in
the suite. Fixtures schema-derived from the real FRED `observations` shape with a runtime validator;
assertions derive expected values from the fixture — NEVER hardcoded VIX literals.

- `test_fetch_requests_recent_window` — assert the FRED request params no longer pin
  `observation_start="2020-01-01"` with asc+limit such that the oldest batch is returned; assert the
  selected observation is the most-recent valid one for a multi-date fixture.
- `test_fresh_latest_observation_available_true` — fixture whose latest valid date is within the
  threshold relative to a monkeypatched `_today()`; assert `available=True`, real `vix`/regime, and
  `as_of_date` == the fixture's latest date.
- `test_stale_latest_observation_available_false` — fixture whose latest valid date is ~6 years
  before a monkeypatched `_today()` (reproduces the live bug); assert `available=False`,
  `reason="stale_data"`, and NO fabricated vix/regime/risk values. **This is the RED that pins the
  reported defect.**
- `test_weekend_holiday_within_threshold_available` — latest date 3–4 days before `_today()`;
  assert still `available=True` (guard must not false-positive on normal closures).
- `test_as_of_date_is_latest_observation_date` — assert `as_of_date` equals the selected
  observation's real date.
- `test_named_staleness_constant_has_source_comment` — assert `_OPTIONS_PROXY_MAX_STALENESS_DAYS`
  (and any new lookback constant) is a named module-level constant; reviewer verifies the source
  comment manually (math-layer constant rule).
- Preserve existing fetch-failure / 429 / no-observations tests (AC-4 regression guard).

**Run protocol:** `pytest tests/ai_advisor -p no:xdist -o addopts= -m "not live and not slow and not perf"`
(the `-m` filter is MANDATORY — without it live tests hammer real APIs). `DB_PATH` via
`tests/conftest.py`.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Add a freshness guard, not just fix the window | The window fix alone would silently start serving fresh data, but a future FRED gap/outage would again pass stale data as current. Honest-availability must cover staleness, not only fetch failure — this is the real defect class. |
| Recent rolling window (asc + computed start) preferred | Minimal change; preserves the existing `_parse_latest_observation` reverse-walk semantics and its tests. Desc+limit is allowed but requires changing the parse helper. |
| [PM-ASSUMED] `_OPTIONS_PROXY_MAX_STALENESS_DAYS` ≈ 10 calendar days | Above the longest normal market closure (3-day weekend + adjacent holiday ≈ 4 days) with margin, so ordinary nightly runs stay available; still catches the 6-year bug decisively. Named constant — tune later if false-unavailable is observed. |
| [PM-ASSUMED] `_OPTIONS_PROXY_LOOKBACK_DAYS` ≈ 90 calendar days | Wide enough to always contain several valid trading-day observations across holidays, small enough to keep the response light. |
| Injectable `_today()` | Deterministic staleness tests without wall-clock coupling. |

## Scope Boundaries

- **IN:** `advisors/lens_options_proxy.py` recent-window fetch + freshness guard + truthful
  `as_of_date` + injectable run-date; tests; doc-gen update (module doc + DECISIONS entry noting the
  defect class and the guard). The fix must be WIRED on the live producer path (it already is —
  `_fetch_options_proxy` is called by the pipeline; no new wiring needed, but the live functional
  test must prove the deployed nightly path now reports a recent VIX or an honest stale state).
- **OUT:** the fundamentals portfolio→ticker fan-out bug (separate cycle); any new derivatives data
  source beyond the FRED VIX/VXV proxy; schema/migration changes; UI changes; the regime/risk
  classification math (behavior-preserving — only gated by the new freshness guard).

**Team note:** Toxic-Pair TDD — test-writer (quant-test-writer, LEAD) + implementer
(composer-alpaca-integration — best-fitting external-API/bounded-retry/fixture-first specialist for
a FRED HTTP fetch; candidates checked: risk-engine-specialist [math-core, less fit for HTTP],
sqlite-specialist [N/A], flask-dashboard-specialist [N/A]) + reviewer (quant-code-reviewer) +
doc-writer (doc-gen). PM live-functional gate: real FRED fetch (FRED_API_KEY is set) proving the
deployed producer now returns a recent `as_of_date` (or an honest stale `available=False`), plus a
fixture-driven stale→unavailable / fresh→available toggle.
