# sleeves

> Managed Sleeves P1: sleeve infrastructure + the direct Alpaca order layer — the only part of the codebase permitted to place broker orders.

**Source:** `sleeves/__init__.py`, `sleeves/alpaca_orders.py`, `sleeves/reconciliation.py`, `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py`, plus the `sleeve_*` accessors in `database.py` and `migrations/033_sleeves.sql`
**Last updated:** 2026-07-07 (P1 GREEN tip `0c5c4df`, review-approved)

## Overview

A managed sleeve is a bounded slice of the operator's own Alpaca account (paper today; live only once live keys are provisioned) governed by operator-authored rules — the rules themselves are a P2 concern. P1 ships the infrastructure a rule engine will act through: the single Alpaca order client, envelope clamping, risk-based sizing, cash/position ledger accounting, and broker-truth reconciliation, plus the additive DB schema and accessors that persist all of it. No rule engine exists yet — `sleeve_rules` is schema-ready but written and read by no P1 code path, and `sleeve_rule_fires` doesn't exist yet at all (see [Deferred to P2](#deferred-to-p2) below).

Every module here is a pure function library (no I/O beyond `sleeves/alpaca_orders.py`'s HTTP calls) — dataclasses in, dataclasses out, so the risk-critical math (clamping, sizing, conservation) is testable without a database or network. Persistence and orchestration are the caller's job.

## Architecture invariants (binding; enforced by `tests/sleeves/test_containment_invariants.py`)

1. **Single order-capable module.** `sleeves/alpaca_orders.py` is the only file in the repo permitted to reference the `/v2/orders` broker endpoint or define an order-placing function (`submit_bracket_order`, `submit_trailing_stop_order`, `cancel_order`, and the reserved-but-unused-in-P1 names `submit_order`/`place_order`/`close_position`/`liquidate_position`). A whole-repo AST scan asserts this — including explicitly probing `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py`, and `sleeves/reconciliation.py` as the most tempting places for a shortcut to leak in. This scan is additive to, and does not replace, the pre-existing single-file guards `tests/execution/test_m2_no_order_path.py` and `tests/app/test_dashboard_no_order_path.py`, which the containment test suite also pins as present and non-gutted (≥3 test functions each).
2. **Live-host string denylist.** The bare string `api.alpaca.markets` (as distinct from `paper-api.alpaca.markets`) may appear nowhere in the repo outside `sleeves/alpaca_orders.py`'s `resolve_host()` function or its module-level host constants. One pre-existing prose-only mention (`advisors/universe_provider.py`, explaining why paper keys 401 on the live host) is allowlisted, with its own guard test confirming that mention never turns into a real network call.
3. **`resolve_host()` is the single gated host-selection function.** Pure function of two caller-supplied booleans (`live_mode`, `live_keys_present`) — it reads no environment itself. Returns the paper host unless BOTH are `True`, in which case it returns the live host. Computing those two booleans (sleeve status + `SLEEVE_LIVE_EXECUTION` for `live_mode`; `ALPACA_LIVE_KEY`/`ALPACA_LIVE_SECRET` presence for `live_keys_present`) is the P2/P3 runner's responsibility, out of P1 scope.
4. **Envelope clamps are reduce-only.** `sleeves.envelope.clamp_order` never returns a `qty` greater than the requested `qty`. The one exception where a categorical rule (not a magnitude clamp) applies is the ticker allowlist, which gates **entries only** — a sell of a symbol the sleeve currently holds is never refused for being off the allowlist, so narrowing an allowlist (or a delisting) can never trap an existing position with no way out.
5. **Ledger conservation law.** `sleeves.ledger` maintains `cash_usd + reserved_usd + sum(position.cost_basis_usd) == capital_usd + realized_pnl_usd` after every legal operation — no dollar is created or destroyed by the ledger's own bookkeeping.
6. **RESERVED-then-native-status order lifecycle.** A `sleeve_orders` row is written at reserve time (status `'RESERVED'`, `alpaca_order_id` still `NULL`) using a `client_order_id` minted by the caller *before* the broker call — so a crash between reservation and broker ack is recoverable via `get_order_by_client_order_id`. Once the broker acks, the row's status transitions to Alpaca's own native order-status enum verbatim (`new`/`accepted`/`partially_filled`/`filled`/`canceled`/`rejected`/`expired`/...) — no invented `SUBMITTED`/`OPEN` synonyms.

## `sleeves/alpaca_orders.py`

THE single order-capable module (AC-7/8/13/14/15). Raw `requests` calls — no SDK, no `requests.Session` (so tests can `patch.object(alpaca_orders.requests, "post", ...)` directly, matching `advisors/composer_backtest_client.py`'s test convention). No network calls at import time.

**Error contract:** never raises on API or transport errors. Every failure path returns an `OrderResult` with `order=None` and a D-1-redacted `error` string — built only from `type(exc).__name__` for transport failures or `f"HTTP {status_code}"` for non-2xx responses, never raw exception text or response bodies (which could carry account figures or echoed credentials).

**Retry policy:** bounded exponential backoff over `_BACKOFF_INTERVALS = (1.0, 2.0, 4.0, 8.0)` seconds (`MAX_RETRY_WAIT_SECONDS = 15.0`). `max_retries` counts *total* attempts, not retries after a first attempt. Every request carries an explicit `_REQUEST_TIMEOUT_S = 10.0` timeout. A `requests.Timeout` is never retried (the server may still be processing the original request). `429` responses honor the `Retry-After` header when present, else the first backoff interval.

### Types

**`OrderResult`** (frozen dataclass) — `order: dict | list | None` (raw Alpaca response body on success; a `dict` for an Order/Account, a `list` for `get_positions`), `error: str | None`.

### API Reference

#### `resolve_host(*, live_mode: bool, live_keys_present: bool) -> str`
The single gated host-selection function — see [invariant 3](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy) above. Returns `"https://paper-api.alpaca.markets"` unless both arguments are `True`, in which case `"https://api.alpaca.markets"`.

#### `submit_bracket_order(*, symbol, qty, side, take_profit_price, stop_loss_price, client_order_id=None, time_in_force="day", live_mode=False, live_keys_present=False, max_retries=4) -> OrderResult`
Submits a market-entry bracket order (`order_class="bracket"`: entry + take-profit + stop-loss legs). AC-7: every entry defaults to a bracket so no position exists without a broker-side exit; the take-profit/stop-loss legs are held **at the broker** (AC-8) and survive engine downtime by construction. `client_order_id`, when supplied, is forwarded verbatim to Alpaca's own `client_order_id` order field — the caller mints this *before* the call so a lost HTTP response can still be recovered via `get_order_by_client_order_id` (lost-ack recovery).

#### `submit_trailing_stop_order(*, symbol, qty, side, trail_percent=None, trail_price=None, client_order_id=None, time_in_force="day", live_mode=False, live_keys_present=False, max_retries=4) -> OrderResult`
Submits a native Alpaca trailing-stop order (`type="trailing_stop"`), held at the broker (AC-8). Exactly one of `trail_percent`/`trail_price` should be supplied (Alpaca's own mutually-exclusive trail-spec fields). Same `client_order_id` lost-ack-recovery forwarding as `submit_bracket_order`.

#### `cancel_order(*, order_id: str, live_mode=False, live_keys_present=False) -> OrderResult`
Cancels an open order by broker order id (`DELETE /v2/orders/{order_id}`). Never raises.

#### `get_order(*, order_id: str, live_mode=False, live_keys_present=False) -> OrderResult`
Polls a single order's current broker-truth status. AC-8: calling this fresh (no shared in-memory object) is exactly how a restarted engine confirms a protective order is still live.

#### `get_order_by_client_order_id(*, client_order_id: str, live_mode=False, live_keys_present=False) -> OrderResult`
Looks up a broker order by the `client_order_id` minted before submitting (`GET /v2/orders:by_client_order_id`) — the lost-ack recovery path: if a submit's HTTP response is lost (e.g. a connection reset after the broker already processed the request), the caller recovers the broker's actual order state via the same `client_order_id` it generated, independent of whether the process ever saw a response.

#### `get_account(*, live_mode=False, live_keys_present=False) -> OrderResult`
Fetches broker-truth account state (cash, equity, buying power) — `GET /v2/account`.

#### `get_positions(*, live_mode=False, live_keys_present=False) -> OrderResult`
Fetches broker-truth open positions — `GET /v2/positions`. On success `order` holds a `list`, not a `dict` (the raw Alpaca response shape).

## `sleeves/reconciliation.py`

Tolerance-based broker-truth reconciliation (AC-9). Pure functions over plain dicts/floats — no DB access, no network calls, and no import of `sleeves/alpaca_orders.py`. The caller fetches sleeve-ledger state and broker-truth (via `alpaca_orders.get_account`/`get_positions`) and passes plain values in; this separation keeps the module independently unit-testable and keeps the whole-repo order-endpoint containment invariant simple — reconciliation never becomes a second place capable of reaching the broker.

The verdict is always exactly one of two values — never a third, partial, or soft state: `"OK"` or `"PAUSED_RECONCILIATION"`. Any breach pauses. Enforcing "no order while paused" is a P3/runner integration concern, out of P1 scope — this module only computes the verdict.

**Breach vocabulary** (appears verbatim as a substring of a `ReconciliationResult.breaches` entry):

| Breach | Meaning |
|--------|---------|
| `unknown_position:<SYMBOL>` | Broker holds a symbol our ledger has no record of (orphaned bracket leg / manual operator intervention at the broker). |
| `missing_position:<SYMBOL>` | Our ledger believes we hold a symbol the broker has zero (or no) position in. |
| `position_drift:<SYMBOL>` | Both sides show the symbol, but qty differs beyond the relative tolerance. |
| `cash_drift` | Cash differs beyond the absolute tolerance. |

A symbol present on both sides with `qty=0` on both is **not** a breach — a fully-closed position still tracked by the ledger is expected, not drift.

### Types

**`ReconciliationResult`** (frozen dataclass) — `ok: bool` (True iff `breaches` is empty), `verdict: str` (`"OK"` or `"PAUSED_RECONCILIATION"`), `breaches: list[str]`.

### API Reference

#### `reconcile_positions(sleeve_positions: dict[str, float], broker_positions: dict[str, float], tolerance_pct: float) -> ReconciliationResult`
Diffs the ledger's position beliefs against broker-truth. `tolerance_pct` is a relative tolerance (e.g. `0.005` = 0.5%) applied against the larger of the two quantities for a symbol present on both sides.

#### `reconcile_cash(sleeve_cash_usd: float, broker_cash_usd: float, tolerance_usd: float) -> ReconciliationResult`
Diffs the ledger's cash belief against broker-truth cash. `tolerance_usd` is an absolute tolerance (fees/rounding); drift is direction-agnostic — broker cash being higher OR lower than expected is equally a breach.

#### `reconcile_sleeve(*, sleeve_positions, broker_positions, sleeve_cash_usd, broker_cash_usd, position_tolerance_pct, cash_tolerance_usd) -> ReconciliationResult`
Combined pre/post-trade reconciliation verdict (AC-9) — breaches is the union of the position and cash checks; verdict is `PAUSED_RECONCILIATION` if either breaches.

## `sleeves/envelope.py`

The envelope hard box (AC-2, AC-3). `clamp_order` is the **sole enforcement point** for a sleeve's risk limits: ticker allowlist, max single-position % of sleeve equity, per-order dollar cap, max daily turnover, and long-only/no-shorting. Pure function (no I/O, no state) — every clamp/refusal decision is returned as data; persisting it (into a future `sleeve_rule_fires` row, per P2) is the caller's job.

**Reduce-only semantics:** `clamp_order` never returns `qty` greater than the requested qty. If shrinking every applicable limit to its floor would leave `qty <= 0`, the order is refused (`approved=False, qty=0`, reason `REASON_REDUCED_TO_ZERO`) rather than silently sent at a smaller-but-nonzero size the caller never asked for.

**Clamp processing order:**
1. **Allowlist** — a categorical gate, not a magnitude clamp, and **buy-side only** (review finding BLOCK #1, see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)). A sell of a currently-held symbol is never refused for `REASON_NOT_IN_ALLOWLIST` — narrowing the allowlist or a delisting must not trap an existing position with no sanctioned exit. A sell is still subject to the long-only/position-qty cap below, just not the allowlist.
2. **Per-side magnitude caps**, each a ceiling on qty, floored to a whole share, tightest cap wins via sequential min-reduction: sell is capped at `current_position_qty` (long-only, always applies); buy is capped by `max_position_pct`'s remaining room; both sides are capped by `max_order_usd / price` and by the remaining `max_daily_turnover_usd` budget.
3. If magnitude clamping reduces qty to `<= 0`, the order is refused with reason normalized to `REASON_REDUCED_TO_ZERO` regardless of which specific limit hit zero.

### Types

**`ClampResult`** (frozen dataclass) — `approved: bool` (False = refuse outright, qty forced to 0), `qty: float` (final qty, never greater than `original_qty`), `original_qty: float`, `clamped: bool` (True iff `qty != original_qty`), `reason: str | None` (populated whenever `clamped` or not `approved`).

**Reason codes:** `REASON_NOT_IN_ALLOWLIST`, `REASON_MAX_POSITION_PCT`, `REASON_MAX_ORDER_USD`, `REASON_MAX_DAILY_TURNOVER`, `REASON_LONG_ONLY_NO_SHORT`, `REASON_REDUCED_TO_ZERO` (reported instead of a specific limit's reason when the combined clamping would leave `qty <= 0`).

### API Reference

#### `clamp_order(*, symbol, side, qty, price, envelope: dict, sleeve_equity: float, current_position_qty=0.0, turnover_used_usd=0.0) -> ClampResult`
Clamps a proposed order to the sleeve's envelope. `envelope` shape (operator-authored, schema-validated in P2): `{"allowlist": [...], "max_position_pct": float, "max_order_usd": float, "max_daily_turnover_usd": float, "long_only": bool}`. Any cap key that is `None` or absent means "unlimited" for that dimension.

#### `is_envelope_widened(old_envelope: dict, new_envelope: dict) -> bool`
True iff `new_envelope` is less restrictive than `old_envelope` in any dimension — drives the P3 arming route's widen-requires-re-ceremony gate (AC-3). Since `clamp_order` treats an absent/`None` cap as unlimited, **removing a cap entirely** (old value present, new value `None`/absent) is the single most extreme possible widen and is correctly flagged (review finding BLOCK #3, fixed after the initial GREEN required both old and new values present before comparing — see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)). The reverse direction (old absent, new present — going from unlimited to bounded) is a narrowing and is never flagged. Narrowing or an identical envelope returns `False`.

## `sleeves/sizing.py`

Risk-based order sizing (AC-7). Four modes translate an operator-authored rule action into a proposed order qty/notional. Sizing **proposes**; `sleeves.envelope` **disposes** (clamps to the hard box) — this module never enforces envelope limits and never raises (D-1-style never-raises result, matching `advisors/composer_backtest_client.BacktestResult`). `risk_pct` and `pct_of_sleeve` are fractions (`0.01` == 1%), never percentage points — matches `math_engine.py`'s percent/fraction discipline.

**Whole-share flooring:** Alpaca rejects `order_class=bracket`/`oco`/`oto` with a fractional qty, so flooring toward zero is the default (`fractionable=False`, matching AC-7's bracket-by-default). Flooring — never rounding up — is the conservative direction: rounding up a `risk_pct`-sized order would silently exceed the operator's configured risk budget. A result that floors to 0 shares is an explicit error (`"qty_rounds_to_zero"`), never a silent zero-qty order. A negative raw qty (from a negative `risk_pct`/`pct_of_sleeve`/`dollars`/`shares` input) is always an explicit error (`"negative_qty"`) regardless of `fractionable` (review finding FLAG #4 — the initial GREEN let `fractionable=True` bypass this check; see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)).

### Types

**`SizingResult`** (frozen dataclass) — `qty: float`, `notional_usd: float`, `mode: str`, `error: str | None` (`qty`/`notional_usd` are `0.0` on any error, never a partial/garbage size).

### API Reference

#### `size_order(*, mode: str, sleeve_equity: float, price: float, stop_price=None, risk_pct=None, pct_of_sleeve=None, dollars=None, shares=None, fractionable=False) -> SizingResult`
Sizes a proposed order under one of four modes:

| Mode | Formula |
|------|---------|
| `"risk_pct"` | `risk_dollars = risk_pct * sleeve_equity`; `qty = risk_dollars / abs(price - stop_price)` |
| `"pct_of_sleeve"` | `notional = pct_of_sleeve * sleeve_equity`; `qty = notional / price` |
| `"dollars"` | `notional = dollars`; `qty = notional / price` |
| `"shares"` | `qty = shares` directly; `notional = qty * price` |

`dollars`/`shares` modes are **not** capped by `sleeve_equity` or any envelope limit here — `sleeves.envelope.clamp_order` is the sole enforcement point for equity/position/turnover caps. Never raises: an unknown mode, a missing required parameter, a non-positive price, a degenerate stop distance (`abs(price - stop_price) <= 0`), or a qty that floors to zero all return `SizingResult(error=...)`.

## `sleeves/ledger.py`

Sleeve cash/position accounting — the capital conservation invariant. Tracks one sleeve's cash, open reservations, and positions at cost. Every transition function is pure (state in, state out, no input mutation) so the conservation invariant is testable without a database. The engine is a fresh subprocess per tick — persisting a `LedgerState` snapshot across ticks is the caller's responsibility (the P2/P3 runner reconstructs it from `sleeve_orders` + `sleeve_fills` rows via `database.get_sleeve_order_history`); this module has zero I/O of its own.

**Conservation law** (must hold after every legal operation): `cash_usd + reserved_usd + sum(position.cost_basis_usd) == capital_usd + realized_pnl_usd`. No dollar is created or destroyed by the ledger's own bookkeeping.

All dollar/qty parameters are rejected as `ValueError` if NaN/±Inf (mirrors `math_engine.py`'s identical policy for exit-decision math). Non-finite rejection is layered with explicit sign checks added during the P1 review cycle: `reserve()` rejects `notional_usd <= 0`; `apply_fill()` rejects `price <= 0` and negative `qty` (see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)).

### Types

**`Position`** (frozen dataclass) — `qty: float` (shares currently held), `cost_basis_usd: float` (a **total**, not per-share average — divide by `qty` for average cost).

**`LedgerState`** (frozen dataclass) — `capital_usd: float` (fixed at sleeve creation, AC-1, never changes), `cash_usd: float` (spendable cash right now), `reserved_usd: float` (dollars set aside for open BUY orders only — a sell reserves *shares*, not cash, enforced elsewhere), `realized_pnl_usd: float` (cumulative realized gain/loss from sells), `positions: dict[str, Position]` (a fully-sold-out symbol remains present with `qty=0, cost_basis_usd=0`, never deleted, so callers can rely on a stable per-symbol entry once a position has ever been opened).

**`InsufficientCashError`** — raised by `reserve()` when `notional_usd` exceeds available `cash_usd`; AC-1's "never spend beyond allocation" enforced here, not merely advised by the envelope clamp.

**`InsufficientPositionError`** — raised by `apply_fill()` on a sell whose qty exceeds the sleeve's currently held qty for that symbol, **or** when the symbol's position is already fully sold out (`qty <= 0`) even for a zero-qty sell (long-only, no shorting; review finding BLOCK #2 — the initial GREEN divided by zero computing an average cost per share for an already-sold-out position instead of raising this documented exception; see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)).

### API Reference

#### `new_ledger(capital_usd: float) -> LedgerState`
Initializes a sleeve's ledger at creation (AC-1): cash starts equal to the fixed capital allocation; no reservations, positions, or realized P&L yet.

#### `reserve(ledger: LedgerState, notional_usd: float) -> LedgerState`
Moves `notional_usd` from cash into open reservations ahead of submitting an order — a write-ahead step that must be called, and its result durably persisted, **before** the broker call. Raises `InsufficientCashError` if `notional_usd` exceeds available cash. Raises `ValueError` if `notional_usd` is not strictly positive.

#### `release(ledger: LedgerState, notional_usd: float) -> LedgerState`
Returns a reservation to cash — an order was canceled or rejected before (or after a partial) fill. Cancel and reject are modeled identically; there is no distinct "reject" transition.

#### `apply_fill(ledger: LedgerState, *, symbol, side, qty, price, reserved_usd) -> LedgerState`
Applies one fill (or partial fill). **BUY:** the reservation resolves into a position; any difference between `reserved_usd` and the actual fill notional (`qty * price`) returns to (or draws down) cash. **SELL:** reduces the position at its average cost basis and realizes the gain/loss; sells never touch `reserved_usd` (buy-side-only concept); callers pass `reserved_usd=0.0` for sells. Raises `InsufficientPositionError` per the sold-out-position case above. Raises `ValueError` if `price <= 0` or `qty < 0` (`qty == 0` is legal, specifically so a zero-qty sell reaches the `InsufficientPositionError` check rather than an undifferentiated rejection).

## Database layer (`database.py` `sleeve_*` accessors + `migrations/033_sleeves.sql`)

Migration 033 (additive-only, idempotent `CREATE TABLE/INDEX IF NOT EXISTS`) ships five tables. No `PRAGMA foreign_keys=ON` anywhere in this codebase, so every cross-table reference below is a soft FK (documented in comments, not DB-enforced), matching the `spec_facets.bundle_hash` precedent (migration 016).

| Table | Columns | Notes |
|-------|---------|-------|
| `sleeves` | `id, name (UNIQUE), capital_usd, status DEFAULT 'SHADOW', envelope_json DEFAULT '{}', created_at, updated_at` | One row per managed sleeve. |
| `sleeve_rules` | `id, sleeve_id, name, json_doc DEFAULT '{}', mode DEFAULT 'SHADOW', enabled DEFAULT 1, created_at, updated_at` | Schema-ready for the P2 rule engine; not written or read by any P1 code path. |
| `sleeve_orders` | `id, client_order_id (UNIQUE), alpaca_order_id (nullable, UNIQUE where not null), sleeve_id, rule_id (nullable), symbol, side, qty, reserved_price (nullable), order_class DEFAULT 'simple', status DEFAULT 'RESERVED', submitted_at, raw_json DEFAULT '{}', updated_at` | `client_order_id` is the durable pre-broker correlation key (see [invariant 6](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy)); `alpaca_order_id` is `NULL` for the entire pre-ack window. `reserved_price` is the estimated fill price used to size the cash reservation at insert time — distinct from the actual fill price in `sleeve_fills.fill_price`. |
| `sleeve_fills` | `id, order_id (soft FK to sleeve_orders.id), broker_fill_id (nullable, UNIQUE where not null), fill_price, filled_qty, filled_at, created_at` | One row per discrete fill event, including partials. `broker_fill_id` is Alpaca's Account Activities (`activity_type=FILL`) `id`, used to dedup across overlapping poll windows. |
| `sleeve_runtime` | `rule_id, key, value, updated_at` — composite PK `(rule_id, key)`, upserted via `INSERT OR REPLACE` (`port_state` precedent, migration 010) | Durable pacing/latch/bench KV store for the P2 fresh-subprocess-per-minute engine. Schema-ready now; not read or written by any P1 code path. |

### Deferred to P2

`sleeve_rule_fires` — the table originally sketched alongside these five in the plan's Architecture section — is **deferred to a P2 migration (034)**, not part of migration 033. Additive-first migration discipline makes the deferral free (no P1 code path writes fires), and the fires row shape (sensed snapshot, episode semantics) is better designed against the real P2 rule-runner than guessed at during P1. See `DECISIONS.md` `DE-SLEEVES-P1-001` addendum and `feature-plans/managed-sleeves.md`'s Architecture section.

### Accessors

All read paths use `get_ro_connection()`; all writes use `get_connection()`. Every query is parameterized — no f-string SQL interpolation of caller-supplied values anywhere in this section.

| Function | Purpose |
|----------|---------|
| `create_sleeve(name, capital_usd, envelope_json="{}") -> int` | Insert a sleeve (starts `SHADOW`), return its id. |
| `get_sleeve(sleeve_id)` / `get_sleeve_by_name(name)` / `get_all_sleeves()` | Read one or all sleeve rows. |
| `update_sleeve_status(sleeve_id, status)` / `update_sleeve_envelope(sleeve_id, envelope_json)` | Mutate status / replace envelope (widen-vs-narrow ceremony gating is an application-layer decision, AC-3 — this accessor writes unconditionally). |
| `create_sleeve_rule(...)` / `get_sleeve_rule(rule_id)` / `get_sleeve_rules_for_sleeve(sleeve_id)` | `sleeve_rules` CRUD — schema-ready for P2, unused by P1. |
| `insert_sleeve_order(client_order_id, sleeve_id, symbol, side, qty, ...)` | Insert one `sleeve_orders` row, normally at reserve time (`status='RESERVED'`). `client_order_id` is UNIQUE-enforced — re-inserting one raises `sqlite3.IntegrityError` rather than silently duplicating. |
| `attach_alpaca_order_id(client_order_id, alpaca_order_id, status=None, raw_json=None)` | Populate `alpaca_order_id` on an existing `RESERVED` row once the broker acks. Looked up by `client_order_id` (the pre-ack key). No-op if unknown. |
| `update_sleeve_order_status(client_order_id, status, raw_json=None)` | Update status/`raw_json` by `client_order_id` (not `alpaca_order_id`, since that column is `NULL` during the pre-ack window). No-op if unknown. |
| `get_sleeve_order_by_client_id(client_order_id)` / `get_sleeve_order_by_alpaca_id(alpaca_order_id)` | Point lookups from either correlation key — the runner mints `client_order_id`; broker poll/webhook responses key off `alpaca_order_id`. |
| `get_sleeve_orders(sleeve_id=None, rule_id=None, status=None, limit=100)` | Filtered read, newest-submitted first; `limit` server-side clamped to 500. |
| `get_sleeve_order_history(sleeve_id)` | Every order for a sleeve, oldest-first, each augmented with a `"fills"` key. Raw event data only — no cost-basis/P&L arithmetic here; exists so `sleeves.ledger`'s tested pure functions can fold over the full history to reconstruct current `LedgerState`. |
| `get_daily_turnover_usd(sleeve_id, trading_day)` | Executed notional (fills on `trading_day`) plus still-reserved notional for non-terminal orders submitted that day (unfilled remainder only, so a partial fill's executed portion is never double-counted). Terminal-status classification is a denylist (`filled, canceled, expired, replaced, done_for_day, rejected`) that deliberately fails closed — an unrecognized/future status, and Alpaca's `stopped`/`suspended` statuses specifically, are treated as still-reserving (over-counts turnover, the conservative direction for a risk cap) rather than silently excluded. |
| `insert_sleeve_fill(order_id, fill_price, filled_qty, filled_at, broker_fill_id=None)` | Insert one `sleeve_fills` row against an existing `sleeve_orders.id` (the internal id, not `client_order_id`/`alpaca_order_id`). `broker_fill_id` is UNIQUE-enforced when present, for dedup against overlapping Account Activities poll windows. |
| `get_fills_for_order(order_id)` | All fills for one order, ascending. |
| `get_sleeve_runtime(rule_id, key)` / `set_sleeve_runtime(rule_id, key, value)` / `get_all_sleeve_runtime_for_rule(rule_id)` / `delete_sleeve_runtime(rule_id, key)` | `sleeve_runtime` CRUD — schema-ready for P2, unused by P1. |

## Notes from the P1 review cycle

`sleeve-review` found four gaps in the initial risk-core GREEN (commit `2200c66`), encoded as RED (`7fee4d2`, 20 tests, 19 GREEN + 1 skip pending a real-vs-mock decision) and fixed in `13e73b6`:

- **BLOCK #1** (`envelope.py`) — the allowlist gate applied to both sides, so narrowing an allowlist (or a delisting) while the sleeve still held the removed symbol meant the *only* way to exit — a sell — was refused. Fixed: allowlist now gates buy-side entries only.
- **BLOCK #2** (`ledger.py`) — `apply_fill(side="sell", qty=0.0)` against an already-sold-out symbol (`qty == 0`, retained per the never-deleted-positions contract) divided by zero computing an average cost per share that no longer existed. Fixed: the sell-side guard now also treats `existing.qty <= 0` as insufficient position, raising the documented `InsufficientPositionError` instead of crashing with an undocumented `ZeroDivisionError`.
- **BLOCK #3** (`envelope.py`) — `is_envelope_widened` required both the old and new cap value present before comparing magnitudes, so nulling out (or omitting) a cap entirely — the single most extreme possible widen — never tripped the AC-3 re-ceremony gate. Fixed: old-present/new-`None`(or absent) is now always a widen.
- **FLAG #4** (`ledger.py` + `sizing.py`) — non-finite rejection never checked sign, so `reserve()`/`apply_fill()` silently accepted non-positive `notional_usd`/`price`/`qty`, and `sizing.py`'s `fractionable=True` early-return path had no sign check at all. Fixed: `reserve()` rejects `notional_usd <= 0`; `apply_fill()` rejects `price <= 0` and `qty < 0` (`qty == 0` stays legal, reaching BLOCK #2's check instead); `_floor_to_whole_share` rejects any negative `raw_qty` before the `fractionable` branch.

The one initially-skipped test (`test_zero_qty_sell_against_sold_out_position_...`) was later pinned to a hard assertion (`raises_insufficient_position`) once `sleeve-risk-impl` concretely chose the raise-over-no-op resolution for BLOCK #2 — see commit `0c5c4df`. Final state: 220 passed / 0 failed / 0 skipped across the `sleeves` test surface.

## Internal Dependencies

- `sleeves/alpaca_orders.py` — `requests` only (stdlib `os`, `time`, `dataclasses`, `urllib.parse.quote`). No internal AlphaBot imports.
- `sleeves/reconciliation.py`, `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py` — stdlib only (`dataclasses`, `math`). No imports of each other or of `sleeves/alpaca_orders.py` — each is independently unit-testable.
- `database.py` — owns all `sleeve_*` table I/O; no sleeves module imports `database.py` directly (P1 has no orchestrator yet; that wiring is P2/P3 scope).
- No sleeves module is imported by `alpha_bot_execution.py`, `app.py`, or any other production module in P1 — there is no caller yet. `sleeves.rules.runner` (P2) will be the first production import site, per the plan's lazy-import convention (`ai_advisor.py` CC-2 pattern: imported inside `main()`, never module-level).
