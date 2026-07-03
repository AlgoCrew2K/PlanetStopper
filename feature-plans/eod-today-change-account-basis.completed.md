# eod-today-change-account-basis (DE-EOD-BASIS-001)

**Status:** ready

## Summary

The dashboard hero "Today's Change (Held)" — and, to a lesser degree, "Cumulative
Return (Held)" — silently CHANGES BASIS depending on market state. This makes the EOD
number not directly comparable to the intraday number, nor to Composer.

- **Live / intraday path** (`_compute_portfolio_strip`, app.py:1115) is CORRECT: it wraps
  the value-weighted (VW) portfolio today-change through
  `analytics.get_portfolio_today_change_account_basis` (app.py:1202-1216) and the VW
  cumulative return through `analytics.get_portfolio_cumulative_return_account_basis`
  (app.py:1172-1194), yielding a **cash-INCLUSIVE account basis** that matches Composer's
  `todays_percent_change` / `simple_return` denominator.
- **Frozen / EOD path** (`/api/state` closed-branch, route gate app.py:1581; authoritative
  recompute app.py:1758-1834) is BROKEN in two ways:
  1. **today_change** is emitted as **raw VW** (`analytics.get_portfolio_today_change`,
     app.py:1815-1817) with NO account-basis wrap. Denominator = invested symphony-value
     sum (**cash EXCLUDED**). Different basis than the live path and than Composer.
  2. **cumulative_return** is **half-converted** (app.py:1807-1812): `if_held` is swapped to
     the cached account-level CR (`portfolio_cr`) but `dry_run` is left on VW basis — a
     mixed-basis dict, so `guard_alpha = dry_run - if_held` is a scope artefact, not a
     clean guard delta.

Root cause of the basis flip: the frozen branch was never routed through the account-basis
helpers that PR #83 (DE-TODAY-BASIS-001) added and wired into the live path. The engine
writer (`alpha_bot_execution.py:1056-1088`) also writes a VW strip into
`last_market_close_snapshot`, but the app's frozen branch explicitly RECOMPUTES the strip
at read time ("authoritative, never pass-through", app.py:1758) and ignores the engine's
written value — so the engine has no access to `_account_totals_cache` and **the fix
belongs entirely at APP READ TIME**, mirroring the live path. The engine stays untouched.

Aggravating factor (the reported 6/30 incident): `_refresh_account_totals` (app.py:740) was
returning `Read timed out` against Composer at 15:58-15:59 ET. Because `mark_stale()` fires
every minute at :00 (via the trigger `finally`, app.py:685/716) and `_StaleFlagDict.get()`
returns `None` when stale (app.py:491-494) with **no last-good retention**, a single timeout
after a `mark_stale()` nulls ALL account-totals reads → the account-basis wrap silently
falls back to VW-both. So even once the frozen wrap is added, a transient timeout would
re-introduce the very basis flip we are fixing unless the stale-cache policy is hardened.

**The fix:** route the frozen/EOD today-change AND cumulative-return through the SAME
account-basis helpers the live path uses, at APP READ TIME, and harden the stale-cache
policy so a transient Composer timeout never silently degrades the account basis to VW.
Display / aggregation only — the execution path is not touched.

## Acceptance Criteria

- **AC-1 (today-change account basis):** In the frozen/EOD branch (`market_state` in
  `{"closed_frozen", "pre_market"}`, app.py:1581), the recomputed
  `portfolio_strip.today_change` has BOTH `if_held` AND `dry_run` on the cash-inclusive
  account basis via `analytics.get_portfolio_today_change_account_basis(_vw_tc, _cached_tc,
  account_value, symphony_value_sum)` — the identical helper, identical argument
  derivation, and identical basis as the live path (app.py:1207-1212). For an untriggered
  portfolio (VW `dry_run == if_held`), frozen `today_change["dry_run"] ==
  today_change["if_held"]` (zero phantom alpha), matching the live path.

- **AC-2 (cumulative-return account basis — fix the half-converted leg):** In the same
  branch, the recomputed `portfolio_strip.cumulative_return` has BOTH `if_held` AND
  `dry_run` on account basis via `analytics.get_portfolio_cumulative_return_account_basis(
  _vw_cr, _cached_cr, account_value, symphony_value_sum)` — replacing the current
  half-converted override (app.py:1807-1812) that leaves `dry_run` on VW basis. Frozen CR
  behaviour is byte-identical to the live CR path (app.py:1180-1190) for the same inputs.

- **AC-3 (honest stale-cache policy — DECISION REQUIRED, see Architecture §Stale-cache
  policy):** When the live `_account_totals_cache` is stale/unavailable at frozen render
  time (Composer timeout, or the every-minute `mark_stale()` window, or an empty cache
  after a process restart), the account-basis wrap NEVER silently presents a VW value as if
  it were account basis. Specifically:
  - **Tier 1 (last-good retention):** the frozen wrap uses the last SUCCESSFULLY-fetched
    account totals (`portfolio_value`, `portfolio_cr`, `portfolio_tc`) — which survive
    `mark_stale()` and are overwritten only on a successful `_refresh_account_totals` — and
    the strip carries an explicit staleness stamp (`account_basis_as_of` + a boolean
    `account_basis_stale`) so the operator sees data age. A transient Composer timeout
    therefore preserves account-basis consistency instead of collapsing to VW.
  - **Tier 2 (honest floor):** when NO account totals have EVER been fetched this process
    (fresh restart, Composer unreachable) the strip degrades honestly — the today-change /
    CR `if_held` is emitted with an explicit `basis: "value_weighted"` marker (or `None`),
    NEVER as an unlabelled value the UI would misread as account basis. The default is the
    explicit VW-basis marker (the number is still a legitimate cash-excluded figure; the
    sin cured is the silent mislabel), with `None` as the documented alternative.
  - Testable: (a) stale cache + a warm last-good ⇒ frozen wrap equals the account-basis
    result computed from the last-good totals AND `account_basis_stale is True`; (b) no
    last-good at all ⇒ `basis == "value_weighted"` present (or `if_held is None`), and the
    value is NEVER equal to what an account-basis wrap would have produced from absent data.

- **AC-4 (bounded, surfaced refresh timeout):** `_refresh_account_totals` (app.py:740) keeps
  a bounded network timeout expressed as a NAMED constant (currently the literal `10` at
  app.py:769; promote to a named constant, env-tunable if trivial), continues to log the
  failure at WARNING/ERROR (app.py:795-805), AND records a machine-readable
  last-refresh-success timestamp + last-error marker that AC-3's staleness stamp reads from.
  A timeout is NOT silently swallowed into a stale snapshot with no operator-visible signal:
  the failure both (a) leaves the last-good totals intact for AC-3 Tier 1 and (b) surfaces
  via the AC-3 staleness stamp. Testable: on a simulated `requests` timeout, the last-good
  totals are unchanged, the error is logged, and the last-success timestamp is not advanced.

- **AC-5 (HARD SCOPE GUARD — engine/exit math byte-unchanged):** `alpha_bot_execution.py`
  and `math_engine.py` are byte-identical after this cycle. The engine's exit-decision /
  guard logic and the EOD snapshot WRITER (`alpha_bot_execution.py:1056-1088`) are
  unaffected — the engine keeps writing the raw VW strip; the app wraps on read. No change
  touches the minute-cadence execution path. Verified by `git diff` showing zero lines
  changed in `alpha_bot_execution.py` / `math_engine.py`, and by the engine/exit test
  suites staying green.

- **AC-6 (golden-fixture frozen == live parity):** A golden-fixture test proves that for one
  captured/derived set of inputs (VW today-change, VW CR, `account_if_held_tc`,
  `account_cr`, `account_value`, `symphony_value_sum`), the frozen-path
  `portfolio_strip.today_change` and `portfolio_strip.cumulative_return` are EQUAL to the
  live-path (`_compute_portfolio_strip`) values for the same inputs. Fixture inputs are
  captured-from-producer or schema-derived; expected values are DERIVED from the fixture
  (never hardcoded producer-computed literals).

- **AC-7 (existing frozen-path contracts preserved):** The recompute stays authoritative
  and R14-compliant — `tests/dashboard/test_frozen_strip_onfly.py` (AC-OF.1..5:
  recompute-authoritative, `trading_day=snapshot["trading_day"]`, live-branch untouched,
  no-snapshot notice, no-crash), `tests/dashboard/test_frozen_portfolio_strip.py`,
  `tests/dashboard/test_r2_frozen_state.py`, and `tests/app/test_account_totals_cache.py`
  all stay green. The account-basis wrap is applied AFTER the authoritative VW recompute,
  exactly as the live path applies it after its VW compute.

- **AC-8 (no new render-path I/O):** The frozen account-basis wrap adds NO Composer/Alpaca
  network call and NO new DB query to the request/render path — it reads only the in-memory
  `_account_totals_cache` (and the AC-3 last-good store), which are populated by the
  separate every-minute scheduler thread. Testable: the frozen branch issues zero outbound
  HTTP on the render path (existing no-network render tests stay green).

- **AC-9 (pre_market parity):** `market_state == "pre_market"` receives the identical
  account-basis treatment as `closed_frozen` (they share the branch at app.py:1581) — the
  fix is not gated to one of the two frozen sub-states.

## Architecture

### Read-time-wrap approach (mirror the live path)

The engine writes raw VW inputs into `last_market_close_snapshot`; the app wraps them on
read. This keeps the change OFF the execution path and puts frozen basis handling on the
same footing as the live path.

All edits are confined to the `/api/state` frozen recompute block, app.py:1758-1834:

1. **Hoist `symphony_value_sum` + read the account totals once.** Immediately after
   `_snap_symphonies_list` is built (app.py:1761-1781), compute
   `_snap_symphony_value_sum = sum(s.get("value") or 0.0 for s in _snap_symphonies_list)`
   and read `_snap_cached_value` / `_snap_cached_cr` / `_snap_cached_tc` from the
   account-totals source (see §Reaching the account cache). This mirrors app.py:1170 /
   1154-1160 in the live path. Move `_snap_cached_value` (currently read at app.py:1813)
   above the CR block so both the CR and TC wraps can use it.

2. **CR wrap (replace app.py:1807-1812).** Replace the half-converted override:
   ```python
   _snap_cr = analytics.get_portfolio_cumulative_return(_snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day)
   _snap_cached_cr = <account cr>
   if _snap_cached_cr is not None:
       _snap_cr = analytics.get_portfolio_cumulative_return_account_basis(
           _snap_cr, _snap_cached_cr, _snap_account_value, _snap_symphony_value_sum,
       )
   # else: VW-both fallback subject to the AC-3 honest-degradation marker
   ```
   This is byte-for-byte the live path (app.py:1180-1190). Note the CR helper's
   division-guard returns the raw VW dict unchanged (analytics.py:1066/1068) — this is
   INTENTIONALLY left as-is because the live path uses the same helper with the same guard,
   and AC-6 requires frozen == live. Do NOT re-harden the CR helper in this cycle (that
   would make frozen diverge from live); the AC-3 marker covers the honest-labelling need.

3. **TC wrap (replace app.py:1815-1817).** Replace the raw-VW today_change:
   ```python
   _snap_vw_tc = analytics.get_portfolio_today_change(_snap_symphonies_list, _snap_bot_state, trading_day=_snap_trading_day)
   _snap_cached_tc = <account tc>
   if _snap_cached_tc is not None:
       _snap_today_change = analytics.get_portfolio_today_change_account_basis(
           _snap_vw_tc, _snap_cached_tc, _snap_account_value, _snap_symphony_value_sum,
       )
   else:
       _snap_today_change = _snap_vw_tc  # + AC-3 basis marker
   ```
   Byte-for-byte the live path (app.py:1204-1216). The TC helper's division-guard already
   returns account-basis Bot==Held (analytics.py:1135/1139) — no change needed.

4. **Preserve the existing `except Exception` all-None fallback** (app.py:1841-1849) and the
   `account_all_time_cr` surface (app.py:1838-1840).

### Helpers (already exist — reused verbatim, NOT modified)

- `analytics.get_portfolio_today_change_account_basis(vw_tc, account_if_held_tc,
  account_value, symphony_value_sum)` — analytics.py:1083-1157. Division-guard →
  account-basis Bot==Held; None-guards; `invested_frac` clamped to `min(..., 1.0)`.
- `analytics.get_portfolio_cumulative_return_account_basis(vw_cr, account_if_held,
  account_value, symphony_value_sum)` — analytics.py:1024-1080. Division-guard → raw VW
  (unchanged; matches live path).

### Engine writer — confirmed UNCHANGED

`alpha_bot_execution.py:1056-1088` writes the VW strip into
`last_market_close_snapshot["portfolio_strip"]`. The app's frozen branch does an
authoritative recompute (app.py:1758) and never reads that written strip, so the engine
needs no change and MUST NOT change (AC-5). The engine cannot reach `_account_totals_cache`
(separate process, in-memory-only cache) — which is precisely why the wrap lives at app
read time.

### Reaching the account cache at read time + Stale-cache policy (AC-3/AC-4)

The frozen branch already reads `_account_totals_cache.get("portfolio_value")` /
`portfolio_cr` (app.py:1807/1813). The account cache is populated by
`_refresh_account_totals` on the app's every-minute scheduler (app.py:841), 24/7 — so at
EOD render time it is normally warm. The failure modes:

- `mark_stale()` fires every minute at :00 (trigger `finally`, app.py:685/716); a poll that
  lands in the stale window before the refresh thread re-warms reads `None`.
- A Composer timeout (app.py:769) after a `mark_stale()` leaves the flag set → reads `None`
  until the next successful minute.
- A fresh process restart leaves the cache empty until the first successful refresh.

`_StaleFlagDict.get()` returns `None` in all of the above (app.py:491-494) with **no
last-good retention** — the reported 6/30 defect.

**Recommended policy (PM DECISION POINT — the diagnosis explicitly asks to decide + specify):**

- **Tier 1 — last-good retention (primary):** add a plain (non-stale-flagged) `dict`
  `_account_totals_last_good` written INSIDE `_refresh_account_totals` on every successful
  fetch (alongside the existing `_account_totals_cache` writes, under the same lock,
  app.py:776-794) plus a `_account_totals_last_success_at` timestamp. The frozen wrap reads
  `_account_totals_cache.get(...)`; on `None`, it falls back to `_account_totals_last_good`
  and stamps `portfolio_strip["account_basis_stale"] = True` +
  `portfolio_strip["account_basis_as_of"] = <last success ET>`. This makes the account-basis
  wrap robust to the every-minute stale blip AND to transient timeouts — directly fixing
  the 6/30 incident. (The live path MAY adopt the same last-good fallback for symmetry, but
  that is optional and can be scoped to the frozen branch to keep the blast radius small —
  flag as an open item, do not silently expand.)
- **Tier 2 — honest floor:** when `_account_totals_last_good` is also empty (never fetched),
  emit the VW value with an explicit `basis: "value_weighted"` marker on the strip (default)
  — NEVER an unlabelled value the client would render as account basis. The `None`
  alternative is documented but not the default (nulling loses a legitimate cash-excluded
  number on a transient outage).

**Alternative (documented downscope):** honest-degradation-ONLY — skip the last-good store,
and on any stale read emit the VW value with the explicit `basis: "value_weighted"` marker.
Simpler, but leaves the 6/30 transient-timeout case degrading to a labelled-VW number rather
than a stale-but-consistent account-basis number. The Tier-1 store is recommended precisely
because the motivating incident is a transient timeout.

**AC-4 refresh hardening:** promote the `timeout=10` literal (app.py:769) to a named
constant (`_ACCOUNT_TOTALS_HTTP_TIMEOUT_S`, env-tunable if trivial); the existing exception
log stays (app.py:795-805); add the `_account_totals_last_success_at` write on success so
AC-3's staleness stamp has an authoritative source. The refresh already runs in its own
daemon thread (app.py:690) and off the request path — no request-thread blocking is
introduced.

## Edge Cases

- **Cache warm (happy path):** frozen wrap == live wrap; untriggered ⇒ `dry_run == if_held`.
- **Every-minute `mark_stale()` blip:** poll lands mid-window ⇒ Tier-1 last-good keeps
  account basis + `account_basis_stale = True`.
- **Composer timeout (6/30 incident):** stale flag set ⇒ Tier-1 last-good used; no silent VW.
- **Fresh process restart, never fetched:** Tier-2 honest floor ⇒ explicit
  `basis: "value_weighted"` marker (or `None`); never an unlabelled account-basis-looking VW.
- **`account_value <= 0` / non-finite:** TC helper ⇒ account-basis Bot==Held
  (analytics.py:1135); CR helper ⇒ raw VW (analytics.py:1066, matches live).
- **`symphony_value_sum <= 0` (flat/all-cash):** same division-guard behaviour as above.
- **`account_if_held_tc is None`:** TC helper ⇒ `{"if_held": None, "dry_run": None}`
  (analytics.py:1140-1142).
- **VW `dry_run`/`if_held` is None:** helper ⇒ `{"if_held": account_if_held_tc, "dry_run":
  None}` (analytics.py:1143-1146).
- **Deposit/withdrawal day (`invested_frac` shift):** `invested_frac = min(sym_sum /
  account_value, 1.0)` clamps a stale-snapshot `sym_sum > account_value` from amplifying the
  guard delta (analytics.py:1128-1130/1151).
- **`account_value == symphony_value_sum` (zero cash):** `invested_frac = 1.0`; account
  basis reduces to VW-plus-account-held — consistent across both paths.
- **market_state `pre_market` vs `closed_frozen`:** both flow through the same branch and
  get identical treatment (AC-9).
- **Legacy snapshot with no `portfolio_tc` in cache but a warm `portfolio_cr`:** TC falls to
  Tier-2 marker while CR wraps — the two fields are independently guarded (mirrors the live
  path, where each cached key is checked independently).

## Security Considerations

- Read-only display/aggregation path; no new write path, no CSRF surface, no
  `_SETTINGS_WRITE_ALLOWLIST` interaction.
- No secret exposure: account IDs continue to be scrubbed to env labels in the frozen branch
  (app.py:1720-1731); the staleness stamp carries only a time string + boolean, no
  credentials, no raw account identifiers.
- No new external call on the render path (AC-8) — the wrap reads in-memory caches only;
  Composer is only ever contacted by the off-request scheduler thread.
- SQLite access on this path stays read-only (`database.load_state()` reader) — unchanged.

## Testing Strategy

- **Golden-fixture parity (AC-6):** a fixture under
  `tests/fixtures/dashboard/frozen_portfolio_strip/eod_account_basis_parity.json` carrying
  VW today-change, VW CR, `account_if_held_tc`, `account_cr`, `account_value`,
  `symphony_value_sum` (captured-from-producer or schema-derived). Test drives BOTH the
  frozen recompute and `_compute_portfolio_strip` (live) with identical inputs and asserts
  the resulting `today_change` and `cumulative_return` dicts are equal. Expected values are
  DERIVED from the fixture (`invested_frac`, `guard_delta`) — never hardcoded literals
  (fixture-provenance + no-hardcoded-producer-values rules).
- **Frozen account-basis unit tests (AC-1/AC-2):** closed_frozen + warm cache + a VW today
  change that DIFFERS from the cached account `portfolio_tc` (simulating cash dilution) ⇒
  assert frozen `today_change["dry_run"] == today_change["if_held"]` for an untriggered
  portfolio, and assert a triggered portfolio's frozen `dry_run` equals `account_if_held_tc
  + guard_delta_vw * invested_frac`. Same for CR.
- **Regression: VW basis no longer used at EOD (AC-1/AC-2):** with a warm cache, assert the
  frozen strip does NOT equal the raw `analytics.get_portfolio_today_change` /
  `get_portfolio_cumulative_return` VW result when `account_value != symphony_value_sum`
  (i.e. the wrap actually fired). This pins the fix against silent regression to VW.
- **Stale-cache / timeout policy (AC-3/AC-4):**
  - stale cache + warm last-good ⇒ frozen strip uses last-good account-basis values AND
    `account_basis_stale is True` + `account_basis_as_of` present.
  - no last-good (fresh restart) ⇒ `basis == "value_weighted"` marker present (or `if_held
    is None`), and the value is NOT what an account-basis wrap would produce.
  - simulated `requests` timeout in `_refresh_account_totals` (patch `requests.get` to raise
    `Timeout`) ⇒ last-good store unchanged, error logged, last-success timestamp not
    advanced.
- **Existing contracts (AC-7):** run `tests/dashboard/test_frozen_strip_onfly.py`,
  `tests/dashboard/test_frozen_portfolio_strip.py`, `tests/dashboard/test_r2_frozen_state.py`,
  `tests/app/test_account_totals_cache.py`, `tests/analytics/test_account_basis_tc.py` — all
  stay green.
- **No-network render (AC-8):** existing frozen-render tests assert zero outbound HTTP on the
  request path; keep green.
- **Scope guard (AC-5):** `git diff --stat` gate asserting zero changes in
  `alpha_bot_execution.py` / `math_engine.py`; the engine + exit-decision suites stay green.
- All tests `-n0`, bounded, no live API (targeted run through the memory cap per project
  gotchas — never the uncapped/full suite locally).

## Scope Boundaries

- **In scope:** the `/api/state` frozen recompute block (app.py:1758-1834) — CR + TC wraps;
  the AC-3 last-good store + staleness stamp; the AC-4 `_refresh_account_totals` timeout
  constant + last-success marker. Display / aggregation only.
- **Out of scope / MUST NOT change:** `alpha_bot_execution.py` (engine + EOD snapshot
  writer), `math_engine.py`, any exit-decision / guard logic, the minute-cadence execution
  path. No re-hardening of `get_portfolio_cumulative_return_account_basis` (keeping frozen ==
  live). No change to the two guarded write paths or `_SETTINGS_WRITE_ALLOWLIST`.
- **No destructive schema change.** If the AC-3 last-good store needs any persistence it is
  in-memory only (matching `_account_totals_cache`); no migration, no new DB column. Any
  additive strip field (`account_basis_stale`, `account_basis_as_of`, `basis`) is additive
  and null-safe for the template (mirrors the frozen `_FROZEN_SYM_DEFAULTS` null-safety
  pattern).
- **Open item (non-blocking):** whether the LIVE path also adopts the Tier-1 last-good
  fallback for symmetry. Recommended but scoped out of this cycle unless the PM wants it —
  flag, do not silently expand.
