# sleeves

> Managed Sleeves: sleeve infrastructure + the direct Alpaca order layer (P1), and the rule engine that senses/evaluates/dispatches through it (P2) — the only part of the codebase permitted to place broker orders.

**Source:** `sleeves/__init__.py`, `sleeves/alpaca_orders.py`, `sleeves/reconciliation.py`, `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py` (P1); `sleeves/rules/{schema,senses,conditions,limits,actions,runner}.py` (P2); plus the `sleeve_*` accessors in `database.py` and `migrations/033_sleeves.sql` + `migrations/034_sleeve_rule_fires.sql`
**Last updated:** 2026-07-08 (P1 GREEN tip `0c5c4df`, review-approved; P2 GREEN tip `7e0efe1`, 314 passed / 0 failed / 0 skipped across `tests/sleeves/` — s2-review verdict pending)

## Overview

A managed sleeve is a bounded slice of the operator's own Alpaca account (paper today; live only once live keys are provisioned) governed by operator-authored rules. P1 shipped the infrastructure a rule engine acts through: the single Alpaca order client, envelope clamping, risk-based sizing, cash/position ledger accounting, and broker-truth reconciliation. P2 builds the rule engine itself (`sleeves/rules/`): JSON rule-doc validation, a closed sense registry, fail-safe condition-tree evaluation, a DB-backed pacing/episode-latch state machine, action dispatch, and the tick orchestrator. Every rule is born in SHADOW (record-only fires, executes nothing); the runner/actions layer also structurally supports a non-SHADOW (armed) path that reaches the P1 order client only through `envelope.clamp` — but nothing in the codebase can yet create, arm, or invoke a non-SHADOW rule in production (no route, ceremony, or `alpha_bot_execution.main()` wiring exists — that's P3). See [Managed Sleeves P2: the rule engine](#managed-sleeves-p2-the-rule-engine) below.

Every module here is a pure function library (no I/O beyond `sleeves/alpaca_orders.py`'s HTTP calls) — dataclasses in, dataclasses out, so the risk-critical math (clamping, sizing, conservation) is testable without a database or network. Persistence and orchestration are the caller's job.

## Architecture invariants (binding; enforced by `tests/sleeves/test_containment_invariants.py`)

1. **Single order-capable module.** `sleeves/alpaca_orders.py` is the only file in the repo permitted to reference the `/v2/orders` broker endpoint or define an order-placing function (`submit_bracket_order`, `submit_trailing_stop_order`, `cancel_order`, and the reserved-but-unused-in-P1 names `submit_order`/`place_order`/`close_position`/`liquidate_position`). A whole-repo AST scan asserts this — including explicitly probing `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py`, and `sleeves/reconciliation.py` as the most tempting places for a shortcut to leak in. This scan is additive to, and does not replace, the pre-existing single-file guards `tests/execution/test_m2_no_order_path.py` and `tests/app/test_dashboard_no_order_path.py`, which the containment test suite also pins as present and non-gutted (≥3 test functions each).
2. **Live-host string denylist.** The bare string `api.alpaca.markets` (as distinct from `paper-api.alpaca.markets`) may appear nowhere in the repo outside `sleeves/alpaca_orders.py`'s `resolve_host()` function or its module-level host constants. One pre-existing prose-only mention (`advisors/universe_provider.py`, explaining why paper keys 401 on the live host) is allowlisted, with its own guard test confirming that mention never turns into a real network call.
3. **`resolve_host()` is the single gated host-selection function.** Pure function of two caller-supplied booleans (`live_mode`, `live_keys_present`) — it reads no environment itself. Returns the paper host unless BOTH are `True`, in which case it returns the live host. Computing those two booleans (sleeve status + `SLEEVE_LIVE_EXECUTION` for `live_mode`; `ALPACA_LIVE_KEY`/`ALPACA_LIVE_SECRET` presence for `live_keys_present`) is the P2/P3 runner's responsibility, out of P1 scope.
4. **Envelope clamps are reduce-only.** `sleeves.envelope.clamp_order` never returns a `qty` greater than the requested `qty`. The one exception where a categorical rule (not a magnitude clamp) applies is the ticker allowlist, which gates **entries only** — a sell of a symbol the sleeve currently holds is never refused for being off the allowlist, so narrowing an allowlist (or a delisting) can never trap an existing position with no way out.
5. **Ledger conservation law.** `sleeves.ledger` maintains `cash_usd + reserved_usd + sum(position.cost_basis_usd) == capital_usd + realized_pnl_usd` after every legal operation — no dollar is created or destroyed by the ledger's own bookkeeping.
6. **RESERVED-then-native-status order lifecycle.** A `sleeve_orders` row is written at reserve time (status `'RESERVED'`, `alpaca_order_id` still `NULL`) using a `client_order_id` minted by the caller *before* the broker call — so a crash between reservation and broker ack is recoverable via `get_order_by_client_order_id`. Once the broker acks, the row's status transitions to Alpaca's own native order-status enum verbatim (`new`/`accepted`/`partially_filled`/`filled`/`canceled`/`rejected`/`expired`/...) — no invented `SUBMITTED`/`OPEN` synonyms.
7. **Envelope clamp is the sole gate from the rule engine to the broker (P2).** `sleeves/rules/actions.py`'s `dispatch_action` never calls `sleeves/alpaca_orders.py` directly for a `buy`/`sell`/`go_to_cash` action — it always calls `sleeves.sizing.size_order` first, then `sleeves.envelope.clamp_order` on the result, and only reaches the order client (when `shadow=False`) with the clamp's returned qty, never the raw sizing qty, and never when the clamp refused. This extends invariant 4 through the runner rather than replacing it.
8. **Condition fail-safe never short-circuits past missing data.** `sleeves/rules/conditions.py`'s fail-safe scan visits every leaf in a condition tree regardless of its boolean shape — an available-and-TRUE `OR` branch never masks an unavailable sibling. Any unavailable sense anywhere in the tree forces `fireable=False` for the whole evaluation.
9. **Every order-placing dispatch reserves before it calls the broker, and a `buy` cash-checks in every mode (added `ef34848`).** `sleeves/rules/actions.py`'s `_place_order_with_reservation` inserts a `RESERVED` `sleeve_orders` row (P1 invariant 6) before any of `buy`/`sell`/`go_to_cash`/`set_stop` reaches `sleeves/alpaca_orders.py`. A `buy` additionally calls `sleeves.ledger.reconstruct_from_history` + `ledger.reserve()` **before** checking `shadow` at all, so a SHADOW fire's recorded outcome reflects real cash availability, never an optimistic one.

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

The envelope hard box (AC-2, AC-3). `clamp_order` is the **sole enforcement point** for a sleeve's risk limits: ticker allowlist, max single-position % of sleeve equity, per-order dollar cap, max daily turnover, and long-only/no-shorting. Pure function (no I/O, no state) — every clamp/refusal decision is returned as data; persisting it (into a `sleeve_rule_fires` row, via P2's `sleeves.rules.runner`) is the caller's job.

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

Sleeve cash/position accounting — the capital conservation invariant. Tracks one sleeve's cash, open reservations, and positions at cost. Every transition function is pure (state in, state out, no input mutation) so the conservation invariant is testable without a database. The engine is a fresh subprocess per tick — persisting a `LedgerState` snapshot across ticks is the caller's responsibility: `reconstruct_from_history` folds `database.get_sleeve_order_history`'s output (every `sleeve_orders` row + its `sleeve_fills`) back into the current `LedgerState`, and P2's `sleeves/rules/actions.py` calls it before every armed *and* shadow `buy` dispatch (AC-1 cash-safety, wired `ef34848`); this module has zero I/O of its own.

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

#### `reconstruct_from_history(capital_usd: float, order_history: list[dict]) -> LedgerState`
Folds a sleeve's full order+fill history (as returned by `database.get_sleeve_order_history`, oldest-submitted-first, each order's fills oldest-filled-first) into its **current** `LedgerState` — zero I/O of its own, matching the module's invariant. Per buy order (identified by a non-`None` `reserved_price`, since a sell's `reserved_price` is always `NULL`): `reserve()`s the full `qty * reserved_price` at fold-start, `apply_fill()`s each fill in order with `reserved_usd` proportional to that fill's share of the original reservation, then — only if the order's current status is in a terminal-status set and some qty remains unfilled — `release()`s the unfilled remainder (reject/cancel/expire are released identically). The terminal-status set reuses `database.get_daily_turnover_usd`'s exact denylist (`filled`/`canceled`/`expired`/`replaced`/`done_for_day`/`rejected`), so an unrecognized or future order status fails closed — stays reserved, the conservative direction — rather than silently releasing. A sell order never reserves; each of its fills applies directly with `reserved_usd=0.0`.

## Managed Sleeves P2: the rule engine

`sleeves/rules/` is the rule engine that senses, evaluates, paces, and dispatches through the P1 infrastructure above. Every function here is deterministic given its inputs (no wall-clock reads except the caller-supplied `now_utc`, no in-process caching) so the whole engine is testable without a live tick.

### `sleeves/rules/schema.py`

JSON rule-doc validation (AC-4, AC-20). No field is ever `eval`'d/`exec`'d — every operator/sense/action-type token is checked against a closed enum; string values (e.g. a rule name) are inert data.

**Constants:** `RULE_CLASS_DEFENSIVE = "DEFENSIVE"`, `RULE_CLASS_ENTRY = "ENTRY"`, `MAX_RULE_DOC_BYTES = 32768`, `MAX_CONDITION_DEPTH = 8`.

**Sense-key grammar** (closed, matches `senses.py`'s registry): exact-match keys `time_of_day`, `day_of_week`, `sleeve_status`, `sleeve_cash_usd`, `sleeve_equity_usd`, `position_qty`; the indicator family `<indicator>_<window>` for `sma`/`ema`/`rsi`/`momentum`/`realized_vol`/`drawdown_from_high` (e.g. `sma_20`, `rsi_14`); cached FRED series `fred_<SERIES_ID>` (e.g. `fred_VIXCLS`).

**Action types:** `buy`, `sell`, `go_to_cash`, `set_stop`, `notify`. Sizing modes (for `buy`/`sell`): `risk_pct`, `pct_of_sleeve`, `dollars`, `shares`. `set_stop` requires exactly one of `trail_percent`/`trail_price`. `notify`'s `fields` object is checked against a whitelist (`symbol`, `action`, `qty`, `price`, `reason`, `rule_name`, `sleeve_name`).

**Entry exit fields (resolved `083526f`, PM decision, AC-7):** a `buy` action must declare `stop_loss_pct` or `trailing_stop_pct` (at least one; each, when present, bounded `0 < pct < 1` via `_is_pct_in_unit_interval`) — `_validate_buy_exit_fields` rejects a `buy` declaring neither. `take_profit_pct` is optional and, when present, must be `> 0`; it never substitutes for the stop requirement. These checks apply only inside the `buy` branch — `sell`/`go_to_cash`/`set_stop`/`notify` are unaffected.

#### Types

**`FieldError`** (frozen dataclass) — `field: str`, `message: str`.

**`ValidationResult`** (frozen dataclass) — `valid: bool`, `errors: tuple[FieldError, ...]`, `rule_class: str | None` (populated only when `valid`).

#### API Reference

##### `derive_rule_class(action_types: list[str]) -> str | None`
Structurally derives a rule's class from its `then` action-type list (AC-20). **DEFENSIVE**: every action is reduce-only (subset of `sell`/`go_to_cash`/`set_stop`/`notify`). **ENTRY**: the set contains `buy` and every other action (if any) is `notify` — the one action type compatible with either class, since it's a passive side-effect rather than a position-changing action. Any other mix (e.g. `buy`+`sell`, `buy`+`go_to_cash`) or an empty action list derives no single valid class (`None`) — "a rule cannot claim DEFENSIVE while holding entry actions."

##### `validate_rule_doc(doc: dict) -> ValidationResult`
Validates size (32KB cap on the serialized JSON), the `if` condition tree (structural validity + depth cap 8, via a recursive descent that returns `None` depth on any structural error so a malformed subtree can't be miscounted as shallow), and every `then` action (per action-type field checks above). Derives the rule's class from the validated action types; if the doc also declares a `class` field, it must name a known class and must match the derived class exactly — a declared/derived mismatch is a validation error (AC-20's schema-level enforcement).

Also validates `limits.market_hours_only` (resolved `ef34848`, PM ruling): `market_hours_only=False` is a validation error unless the rule's **entire** `then` action set is pure-`notify` — any order-placing action (entry or defensive) anywhere in `then` forbids the override regardless of the flag. Bypassing the market-hours gate is only ever safe for a rule that places no order at all.

### `sleeves/rules/senses.py`

The closed sense registry (AC-4). Every `sense_*` function is pure and never-raising: missing or insufficient input is a fail-safe `SenseResult(available=False, reason=...)`, never an exception. FRED sensing is cache-read-only by construction — this module never imports `requests` (a live FRED call would defeat the "cached values only, never live in the tick" plan decision), structurally verified by an AST test.

#### Types

**`SenseResult`** (frozen dataclass) — `value: float | str | int | None`, `available: bool`, `reason: str | None`.

**`SenseContext`** (frozen dataclass) — `now_et: datetime`, `sleeve_row: dict`, `closes: list[float]`, `fred_cache: dict[str, list[dict]]`, `as_of: date`.

#### API Reference

##### `resolve_sense(sense_key: str, *, ctx: SenseContext) -> SenseResult`
Dispatches a schema-validated sense-key string to the right `sense_*` function per the grammar above. An unrecognized key returns `unavailable` with reason `unknown_sense_key:<key>` (defense-in-depth beyond `schema.py`'s authoring-time check).

##### `sense_time_of_day(now_et) -> SenseResult` / `sense_day_of_week(now_et) -> SenseResult`
Fail-safe on a naive or non-`America/New_York` `now_et` (reason `naive_or_wrong_tz_datetime`) — both require a genuine ET wall-clock `datetime`, never a caller-computed UTC offset.

##### `sense_sleeve_state(*, sleeve_row, field) -> SenseResult`
Reads one of `sleeve_status`/`sleeve_cash_usd`/`sleeve_equity_usd`/`position_qty` from the caller-assembled sleeve-state dict; fail-safe (`field_missing`) if the field is `None`/absent.

##### `sense_indicator(*, indicator, closes, window) -> SenseResult`
Six daily-bar formulas, fail-safe (`insufficient_history`) when `closes` is shorter than the indicator needs (`window` for `sma`/`ema`/`drawdown_from_high`; `window + 1` for `rsi`/`momentum`/`realized_vol`, since those need one extra bar to form a return series):

| Indicator | Formula |
|---|---|
| `sma` | mean of the last `window` closes |
| `ema` | seeded at the `sma` of the first `window` closes, then recursively updated with smoothing `alpha = 2 / (window + 1)` over the remaining closes |
| `rsi` | classic average-gain/average-loss over the last `window` deltas; `avg_loss == 0` returns `100.0` (never divides by zero — zero losses means maximally overbought), `avg_gain == 0` (with losses present) returns `0.0` |
| `momentum` | `(closes[-1] - closes[-(window+1)]) / closes[-(window+1)]` |
| `realized_vol` | sample standard deviation (`ddof=1`) of simple returns over the last `window + 1` closes |
| `drawdown_from_high` | `(last_close - running_high) / running_high`, `running_high` = max of the last `window` closes |

##### `sense_fred_series(*, series_id, cached_observations, as_of, max_age_days=10) -> SenseResult`
Reads the latest cached observation (by date) for `series_id`; fail-safe on no cached observations (`no_cached_observations`) or on the latest cached date being more than `max_age_days` older than `as_of` (`stale`). Never fetches live — `cached_observations` is caller-supplied from the P1-era Atlas/FRED cache, never a `requests` call from this module.

### `sleeves/rules/conditions.py`

Condition-tree evaluation (AC-4) over a plain nested-dict structure: `{"op": "compare", "sense": ..., "comparator": ..., "value": ...}` for leaves, `{"op": "AND"|"OR", "children": [...]}` or `{"op": "NOT", "child": ...}` for internal nodes (comparators: `>`, `>=`, `<`, `<=`, `==`, `!=`).

**The load-bearing fail-safe rule:** evaluation is two-pass. A depth-first scan first visits **every** leaf regardless of the tree's boolean shape, looking for the first sense whose `SenseResult.available` is `False`. If any leaf is unavailable, the whole evaluation is not-fireable with reason `sense_missing:<key>` — even under an `OR` whose other branch is available-and-`True`. This is deliberately more conservative than Python's own short-circuiting `or`/`and`: a leaf must never be allowed to mask a sibling's missing data. Only once every leaf is confirmed available does the second pass compute the actual boolean result.

#### Types

**`EvalResult`** (frozen dataclass) — `fireable: bool`, `reason: str | None` (populated only when not fireable due to missing data; a fireable=False from a plain false condition carries `reason=None`).

#### API Reference

##### `evaluate_condition(node: dict, sensed: dict[str, senses.SenseResult]) -> EvalResult`
Runs the fail-safe scan first; if it returns a reason, `EvalResult(fireable=False, reason=...)` without ever computing the boolean shape. Otherwise returns `EvalResult(fireable=<boolean result>, reason=None)`.

### `sleeves/rules/limits.py`

The pacing/episode-latch state machine (AC-5). All state lives in the P1 `sleeve_runtime` table, read/written through the existing `database.get_sleeve_runtime`/`set_sleeve_runtime` accessors, keyed per `rule_id` — zero in-process cache, since the engine is a fresh subprocess per minute. Five string-valued keys: `last_fire_ts`, `fires_today_date`, `fires_today_count`, `episode_latched`, `consecutive_false_count`. This module never reads or writes `sleeve_rule_fires` — that table is the runner's durable *audit* log, not this function's pacing source.

**`DEFAULT_REARM_TICKS = 3`** — the default number of consecutive condition-false ticks required to clear an episode latch.

#### Types

**`PacingResult`** (frozen dataclass) — `fireable: bool`, `reason: str | None`, `episode_id: str | None` (a freshly minted `uuid4().hex` on a permitted fire, `None` otherwise).

#### API Reference

##### `check_and_advance_pacing(*, rule_id, now_utc, market_open, condition_true, cooldown_sec=None, max_fires_per_day=None, rearm_ticks=DEFAULT_REARM_TICKS) -> PacingResult`
- **Market closed:** immediately not-fireable (reason `market_closed`) — reads and writes **no** state at all, so a closed-market tick can never perturb the rearm counter or anything else.
- **`condition_true=False`:** increments `consecutive_false_count`; if the episode is latched and the incremented count has reached `rearm_ticks`, clears the latch and resets the counter. Returns not-fireable with `reason=None` (a false condition isn't itself a pacing rejection).
- **`condition_true=True`:** always resets `consecutive_false_count` to `0` first (a true tick breaks the false streak whether or not latched). If still latched, not-fireable (`episode_latched`). Else checks `cooldown_sec` against elapsed time since `last_fire_ts` (`cooldown_active` if too soon), then `max_fires_per_day` against the count for the current ET trading day (`_trading_day` derives the boundary from the caller-supplied `now_utc`, never naive UTC/local "today" — matches `get_daily_turnover_usd`'s established caller-supplied-trading_day convention) (`max_fires_per_day_reached` if reached). If all gates pass: mints a new `episode_id`, persists `last_fire_ts`/`fires_today_date`/`fires_today_count`/`episode_latched`, and returns `fireable=True`.

### `sleeves/rules/actions.py`

Action dispatch (AC-6, AC-7). `dispatch_action` is the **one** place a rule's `then` action turns into either a would-have-ordered shadow record or a real broker call. The load-bearing security property: for `buy`/`sell`/`go_to_cash`, this module always calls `sleeves.sizing.size_order` first, then `sleeves.envelope.clamp_order` on the result, and may only reach `sleeves.alpaca_orders` (when `shadow=False`) using the clamp's returned qty — never the raw sizing qty, and never when the clamp refused. See [invariant 7](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy).

**Cash-safety and durable reservation (s2-review BLOCK finding at `083526f`, PM ruling, resolved `ef34848`, AC-1/AC-19):** every order-placing action — `buy`/`sell`/`go_to_cash`/`set_stop` — now also gets the P1 invariant #6 reservation sequence: a `RESERVED` `sleeve_orders` row (with a freshly minted `client_order_id`) is inserted **before** the broker call, then attached to the broker's own order id on ack or marked terminal on rejection. A `buy` additionally reconstructs the sleeve's *real* current `LedgerState` via `sleeves.ledger.reconstruct_from_history` and attempts `ledger.reserve()` on the clamped notional **in both shadow and armed mode** — so a SHADOW fire's recorded would-have-been outcome reflects true cash availability, not an optimistic one — refusing with `_REASON_INSUFFICIENT_CASH` (zero broker calls, zero DB writes) on `InsufficientCashError`.

**Entries require a declared exit (resolved `083526f`, AC-7, PM decision 2026-07-08):** a `buy` action must declare `stop_loss_pct` or `trailing_stop_pct` (`schema.py`'s `_validate_buy_exit_fields` — a schema validation error otherwise; each, when present, must satisfy `0 < pct < 1`) — there is no default/fallback stop, and the earlier `_DEFAULT_BRACKET_STOP_LOSS_PCT`/`_DEFAULT_BRACKET_TAKE_PROFIT_PCT` constants have been removed entirely (a module-source-inspection test enforces their absence). `_dispatch_buy` derives `stop_loss_price = price * (1 - declared_pct)` from whichever of the two fields is declared — note that a declared `trailing_stop_pct` on an entry still produces a **fixed** bracket `stop_loss_price` at that distance, not an Alpaca-native trailing-stop leg on the entry order itself; a real trailing-stop order is `set_stop`'s domain, for protecting an *existing* position. `take_profit_pct` stays optional; when absent, the take-profit distance is derived as a fixed `_TAKE_PROFIT_REWARD_RISK_RATIO = 2.0` multiple of the rule's own declared stop distance — never an independent, disconnected percentage — so it scales with whatever risk the rule declares rather than acting as a second hidden default (RED-pinned by a ratio-consistency test across two different stop distances, never asserting the ratio's value itself).

**Design note (deliberate, tunable default — not the only option):** the 2:1 reward:risk default is safe — the protective floor is intact, it scales with the rule author's own declared risk, and it's test-pinned — but it does embed an upside opinion: capping every entry's take-profit at exactly `2x` the declared stop distance silently limits how far a winning position can run. The recorded alternative is a stop-only entry (Alpaca's `OTO` order class — one-triggers-other, no take-profit leg at all — letting the position run uncapped with only the protective stop). That alternative is not built; it's a candidate P3+/operator decision, surfaced here rather than buried.

#### Types

**`ActionContext`** (frozen dataclass) — `sleeve_id`, `rule_id: int` (added `ef34848` — threaded to the reservation row and the fire log), `symbol`, `price`, `sleeve_equity_usd`, `capital_usd: float` (added `ef34848` — the sleeve's fixed capital allocation, AC-1, fed to `ledger.reconstruct_from_history`), `current_position_qty`, `turnover_used_usd`, `envelope: dict`, `live_mode: bool = False`, `live_keys_present: bool = False`, `discord_webhook_url: str | None = None`.

**`ActionResult`** (frozen dataclass) — `action_type: str`, `would_have_qty: float | None`, `would_have_notional_usd: float | None`, `executed: bool`, `order_result: alpaca_orders.OrderResult | None`, `clamp: envelope.ClampResult | None`, `refused_reason: str | None`, `order_id: int | None = None` (added `ef34848` — the internal `sleeve_orders.id` for AC-19's fire-to-order trace; `None` for a shadow, refused, or no-order [`notify`] outcome).

#### API Reference

##### `_place_order_with_reservation(*, ctx, symbol, side, qty, order_class, reserved_price, submit_fn) -> tuple[int, alpaca_orders.OrderResult]` (internal helper, added `ef34848`)
Centralizes the P1 invariant #6 sequence for every order-placing action: mints a `client_order_id` and inserts a `RESERVED` `sleeve_orders` row **before** calling `submit_fn` (which receives that `client_order_id` and must return an `OrderResult`); on success, attaches the broker's own order id via `database.attach_alpaca_order_id`; on failure, marks the row `"rejected"` via `database.update_sleeve_order_status` — reject and cancel are modeled identically, and this terminal status **is** the release mechanism (there's no separate in-memory reservation to release, since `LedgerState` is never itself persisted, only reconstructed). Returns the internal `sleeve_orders.id` alongside the `OrderResult`.

##### `dispatch_action(action: dict, *, ctx: ActionContext, shadow: bool) -> ActionResult`
Routes by `action["type"]`:
- **`buy`:** sizes + clamps (side `"buy"`); a sizing error or an unapproved clamp returns `executed=False` with no order attempt. **Then, in both shadow and armed mode:** reconstructs the sleeve's real `LedgerState` (via `ledger.reconstruct_from_history`, fed by `database.get_sleeve_order_history`) and attempts `ledger.reserve()` on the clamped notional — an `InsufficientCashError` refuses with `_REASON_INSUFFICIENT_CASH`, zero broker calls, zero DB writes, in either mode. In shadow mode (past that check), records the would-have-ordered qty/notional and stops. Armed (`shadow=False`): submits via `_place_order_with_reservation`/`alpaca_orders.submit_bracket_order` — **always** the bracket path, never the plain `submit_order` path, per a PM-flagged "no-naked-entry" RED test added mid-cycle (AC-7: no position without a broker-side exit) — and returns the reservation's `order_id`.
- **`sell`:** sizes + clamps (side `"sell"`); armed path submits via `_place_order_with_reservation`/`alpaca_orders.submit_order` (plain, non-bracket) — no cash reservation attempt (sells don't spend cash), but the *same* order-row reservation sequence as `buy` (P1 invariant #6 is not entry-only).
- **`go_to_cash`:** always sizes to the **full** `current_position_qty` — bypasses `sizing.size_order` entirely and ignores a present-but-irrelevant `sizing` field on the action — then clamps and dispatches identically to `sell` (including the order-row reservation sequence).
- **`set_stop`:** no sizing/clamp at all (protects an *existing* position, not a new entry); qty is the current position qty. Armed path submits via `_place_order_with_reservation`/`alpaca_orders.submit_trailing_stop_order` using the action's `trail_percent`/`trail_price` — same reservation-row sequence as the other order-placing actions.
- **`notify`:** never touches `sleeves.alpaca_orders`, shadow or armed, and gets **no** reservation row (it places no order). Filters the action's `fields` against the whitelist again (defense-in-depth beyond `schema.py`'s authoring-time check) and POSTs to `ctx.discord_webhook_url` if set, with a 10-second timeout and `requests.RequestException` suppressed — a Discord delivery failure must never break the tick.

### `sleeves/rules/runner.py`

The P2 tick orchestrator (AC-6, AC-10, AC-20) — the single entry point a fresh-subprocess-per-minute tick calls.

#### Types

**`FireOutcome`** (frozen dataclass) — `rule_id`, `rule_class`, `fired: bool`, `reason: str | None`, `sensed_snapshot: dict`, `action_results: tuple[ActionResult, ...]`, `fire_ids: tuple[int, ...]`.

#### API Reference

##### `evaluate_rules(*, rules, sleeve_row, sleeve_equity_usd, now_utc, closes_by_symbol, positions, fred_cache, envelope, live_mode=False, live_keys_present=False, discord_webhook_url=None, turnover_used_by_symbol=None) -> list[FireOutcome]`
1. Derives `now_et` and `market_open` **internally** via `market_calendar.get_market_state` (XNYS, holiday-aware) — `market_open` is never accepted as a caller-supplied parameter, so a caller cannot substitute a wrong or naive weekday-only check.
2. Derives each rule's class via `schema.derive_rule_class` from its `then` action types, then sorts so every DEFENSIVE-class rule is fully evaluated and dispatched before any ENTRY-class (or unclassifiable) rule, ascending `rule_id` within each group — same-tick precedence per AC-10/AC-20.
3. Per rule: collects every sense key referenced anywhere in its `if` tree, resolves each via `senses.resolve_sense`, and evaluates via `conditions.evaluate_condition`. An unavailable-sense result records `FireOutcome(fired=False, reason=<sense_missing:...>)` and leaves pacing state **entirely untouched** (mirrors the market-closed contract) — no `sleeve_rule_fires` row is written.
4. Else calls `limits.check_and_advance_pacing` (the rule's own `limits` doc controls `market_hours_only` (default `True`), `cooldown_sec`, `max_fires_per_day`, `rearm_ticks`). Not fireable -> `FireOutcome(fired=False, reason=<pacing reason>)`, again no fire row.
5. Else dispatches **every** action in the rule's `then` list via `actions.dispatch_action` (`shadow = rule["mode"] == "SHADOW"`; `ActionContext` is built with `sleeve_row["capital_usd"]` and `rule["id"]` per invariant 9, added `ef34848`), and for each action — fired or refused — writes one `sleeve_rule_fires` row via `database.insert_sleeve_rule_fire`: `rule_class`/`mode_at_fire` snapshotted at this tick (so a later rule edit or mode change never rewrites history), the sensed snapshot and an `outcome_json` (would-have qty/notional, `executed`, `refused_reason`, the raw order/order_error), `clamped`/`clamp_reason` from the `ClampResult`, `episode_id` from the pacing result, and (as of `ef34848`) `ActionResult.order_id` — populated for any armed, successfully-reserved order-placing action, `NULL` for SHADOW fires, refused actions, and `notify`. Since P2 has no production caller yet (no route/ceremony wires a rule to `PAPER`/`LIVE` mode), every fire actually produced in this cycle is still `SHADOW` and still gets `NULL` in practice — but the runner/actions code itself is fully wired end-to-end for AC-19's trade-to-fire-to-order trace once P3 permits arming.

## Database layer (`database.py` `sleeve_*` accessors + `migrations/033_sleeves.sql` + `migrations/034_sleeve_rule_fires.sql`)

Migration 033 (additive-only, idempotent `CREATE TABLE/INDEX IF NOT EXISTS`) ships five tables; migration 034 (same conventions) adds a sixth, `sleeve_rule_fires`, for P2. No `PRAGMA foreign_keys=ON` anywhere in this codebase, so every cross-table reference below is a soft FK (documented in comments, not DB-enforced), matching the `spec_facets.bundle_hash` precedent (migration 016).

| Table | Columns | Notes |
|-------|---------|-------|
| `sleeves` | `id, name (UNIQUE), capital_usd, status DEFAULT 'SHADOW', envelope_json DEFAULT '{}', created_at, updated_at` | One row per managed sleeve. |
| `sleeve_rules` | `id, sleeve_id, name, json_doc DEFAULT '{}', mode DEFAULT 'SHADOW', enabled DEFAULT 1, created_at, updated_at` | `json_doc` holds a rule doc validated by `sleeves.rules.schema.validate_rule_doc`. P2's `runner.evaluate_rules` takes already-assembled rule dicts as a parameter — it does not call these accessors itself; wiring `sleeve_rules` reads into the runner is P3 (route/ceremony) scope. |
| `sleeve_orders` | `id, client_order_id (UNIQUE), alpaca_order_id (nullable, UNIQUE where not null), sleeve_id, rule_id (nullable), symbol, side, qty, reserved_price (nullable), order_class DEFAULT 'simple', status DEFAULT 'RESERVED', submitted_at, raw_json DEFAULT '{}', updated_at` | `client_order_id` is the durable pre-broker correlation key (see [invariant 6](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy)); `alpaca_order_id` is `NULL` for the entire pre-ack window. `reserved_price` is the estimated fill price used to size the cash reservation at insert time — distinct from the actual fill price in `sleeve_fills.fill_price`. |
| `sleeve_fills` | `id, order_id (soft FK to sleeve_orders.id), broker_fill_id (nullable, UNIQUE where not null), fill_price, filled_qty, filled_at, created_at` | One row per discrete fill event, including partials. `broker_fill_id` is Alpaca's Account Activities (`activity_type=FILL`) `id`, used to dedup across overlapping poll windows. |
| `sleeve_runtime` | `rule_id, key, value, updated_at` — composite PK `(rule_id, key)`, upserted via `INSERT OR REPLACE` (`port_state` precedent, migration 010) | Durable pacing/latch/bench KV store for the fresh-subprocess-per-minute engine. Read/written by P2's `sleeves.rules.limits.check_and_advance_pacing` via 5 keys (`last_fire_ts`/`fires_today_date`/`fires_today_count`/`episode_latched`/`consecutive_false_count`); unused by any P1 code path. |
| `sleeve_rule_fires` | `id, rule_id, sleeve_id, fired_at DEFAULT now, action, rule_class, mode_at_fire, sensed_snapshot_json DEFAULT '{}', outcome_json DEFAULT '{}', clamped DEFAULT 0, clamp_reason (nullable), episode_id (nullable), order_id (nullable, soft FK to sleeve_orders.id), created_at` + 4 indexes (`rule_id`+`fired_at`, `sleeve_id`, `order_id` partial, `episode_id` partial) | One row per rule-engine tick evaluation that fired (`when`/`if` matched and `then` was attempted). `rule_class`/`mode_at_fire` are immutable per-fire audit snapshots (AC-20), never re-derived from the rule's current state. `order_id` is populated for an armed, successfully-reserved order-placing action (as of `ef34848`); `NULL` for SHADOW fires, refused actions, and `notify`. Since P2 has no production caller (no route/ceremony arms a rule to `PAPER`/`LIVE`), every fire produced in practice during P2 is still `NULL` — but the column is fully wired end-to-end. Shipped in migration 034 (`c1047c1`), resolving the P1 deferral — see [Resolved: the P1 sleeve_rule_fires deferral](#resolved-the-p1-sleeve_rule_fires-deferral) below. |

### Resolved: the P1 `sleeve_rule_fires` deferral

`sleeve_rule_fires` — the table originally sketched alongside the other five in the plan's Architecture section — was deferred from migration 033 (P1) to a P2 migration, on the reasoning that its row shape (sensed snapshot, episode semantics) was better designed against the real P2 rule-runner than guessed at during P1. **Resolved:** migration 034 (`c1047c1`) shipped it (see the table row above), with the row shape reconciled across independent s2-rules-impl/s2-test-writer proposals before commit. See `DECISIONS.md` `DE-SLEEVES-P1-001`'s addendum and `DE-SLEEVES-P2-001`'s addendum, and `feature-plans/managed-sleeves.md`'s Architecture section (now annotated "resolved in P2").

### Accessors

All read paths use `get_ro_connection()`; all writes use `get_connection()`. Every query is parameterized — no f-string SQL interpolation of caller-supplied values anywhere in this section.

| Function | Purpose |
|----------|---------|
| `create_sleeve(name, capital_usd, envelope_json="{}") -> int` | Insert a sleeve (starts `SHADOW`), return its id. |
| `get_sleeve(sleeve_id)` / `get_sleeve_by_name(name)` / `get_all_sleeves()` | Read one or all sleeve rows. |
| `update_sleeve_status(sleeve_id, status)` / `update_sleeve_envelope(sleeve_id, envelope_json)` | Mutate status / replace envelope (widen-vs-narrow ceremony gating is an application-layer decision, AC-3 — this accessor writes unconditionally). |
| `create_sleeve_rule(...)` / `get_sleeve_rule(rule_id)` / `get_sleeve_rules_for_sleeve(sleeve_id)` | `sleeve_rules` CRUD — not called directly by the P2 runner (it takes pre-assembled rule dicts); wiring these reads into a production caller is P3 scope. |
| `insert_sleeve_order(client_order_id, sleeve_id, symbol, side, qty, ...)` | Insert one `sleeve_orders` row, normally at reserve time (`status='RESERVED'`). `client_order_id` is UNIQUE-enforced — re-inserting one raises `sqlite3.IntegrityError` rather than silently duplicating. |
| `attach_alpaca_order_id(client_order_id, alpaca_order_id, status=None, raw_json=None)` | Populate `alpaca_order_id` on an existing `RESERVED` row once the broker acks. Looked up by `client_order_id` (the pre-ack key). No-op if unknown. |
| `update_sleeve_order_status(client_order_id, status, raw_json=None)` | Update status/`raw_json` by `client_order_id` (not `alpaca_order_id`, since that column is `NULL` during the pre-ack window). No-op if unknown. |
| `get_sleeve_order_by_client_id(client_order_id)` / `get_sleeve_order_by_alpaca_id(alpaca_order_id)` | Point lookups from either correlation key — the runner mints `client_order_id`; broker poll/webhook responses key off `alpaca_order_id`. |
| `get_sleeve_orders(sleeve_id=None, rule_id=None, status=None, limit=100)` | Filtered read, newest-submitted first; `limit` server-side clamped to 500. |
| `get_sleeve_order_history(sleeve_id)` | Every order for a sleeve, oldest-first, each augmented with a `"fills"` key. Raw event data only — no cost-basis/P&L arithmetic here; exists so `sleeves.ledger`'s tested pure functions can fold over the full history to reconstruct current `LedgerState`. |
| `get_daily_turnover_usd(sleeve_id, trading_day)` | Executed notional (fills on `trading_day`) plus still-reserved notional for non-terminal orders submitted that day (unfilled remainder only, so a partial fill's executed portion is never double-counted). Terminal-status classification is a denylist (`filled, canceled, expired, replaced, done_for_day, rejected`) that deliberately fails closed — an unrecognized/future status, and Alpaca's `stopped`/`suspended` statuses specifically, are treated as still-reserving (over-counts turnover, the conservative direction for a risk cap) rather than silently excluded. |
| `insert_sleeve_fill(order_id, fill_price, filled_qty, filled_at, broker_fill_id=None)` | Insert one `sleeve_fills` row against an existing `sleeve_orders.id` (the internal id, not `client_order_id`/`alpaca_order_id`). `broker_fill_id` is UNIQUE-enforced when present, for dedup against overlapping Account Activities poll windows. |
| `get_fills_for_order(order_id)` | All fills for one order, ascending. |
| `get_sleeve_runtime(rule_id, key)` / `set_sleeve_runtime(rule_id, key, value)` / `get_all_sleeve_runtime_for_rule(rule_id)` / `delete_sleeve_runtime(rule_id, key)` | `sleeve_runtime` CRUD — unused by P1; live pacing/latch store for P2's `sleeves.rules.limits.check_and_advance_pacing`. |
| `insert_sleeve_rule_fire(rule_id, sleeve_id, action, rule_class, mode_at_fire, sensed_snapshot_json="{}", outcome_json="{}", clamped=False, clamp_reason=None, episode_id=None, order_id=None, fired_at=None) -> int` | Insert one `sleeve_rule_fires` row, return its id. `rule_class`/`mode_at_fire` are snapshots the caller passes explicitly for this tick — never re-derived later, so a subsequent rule edit or SHADOW→PAPER→LIVE change never rewrites history. `order_id` is the INTERNAL `sleeve_orders.id` (matching `sleeve_fills.order_id`'s precedent), left `None` for SHADOW fires and for actions that placed no order. `fired_at` defaults to SQL insert-time when omitted; pass it explicitly for the tick's own logical timestamp. |
| `get_sleeve_rule_fire(fire_id)` | One `sleeve_rule_fires` row by internal id, or `None`. |
| `get_sleeve_rule_fires(rule_id=None, sleeve_id=None, limit=100)` | Filtered read, newest-fired first; `limit` server-side clamped to 500 (mirrors `get_sleeve_orders`). |
| `get_fire_count_for_rule_on_day(rule_id, trading_day)` | Ground-truth fire count for one rule on a caller-supplied `trading_day` (`'YYYY-MM-DD'`, never derived from naive UTC/local "today" — matches `get_daily_turnover_usd`'s contract) — a cross-check for the AC-16 dashboard panel, independent of `limits.py`'s own `max_fires_per_day` pacing counter (which lives entirely in `sleeve_runtime`). Counts every fire regardless of action/outcome; callers needing an entries-only or exits-only count filter on `action` at the application layer. |

## Notes from the P1 review cycle

`sleeve-review` found four gaps in the initial risk-core GREEN (commit `2200c66`), encoded as RED (`7fee4d2`, 20 tests, 19 GREEN + 1 skip pending a real-vs-mock decision) and fixed in `13e73b6`:

- **BLOCK #1** (`envelope.py`) — the allowlist gate applied to both sides, so narrowing an allowlist (or a delisting) while the sleeve still held the removed symbol meant the *only* way to exit — a sell — was refused. Fixed: allowlist now gates buy-side entries only.
- **BLOCK #2** (`ledger.py`) — `apply_fill(side="sell", qty=0.0)` against an already-sold-out symbol (`qty == 0`, retained per the never-deleted-positions contract) divided by zero computing an average cost per share that no longer existed. Fixed: the sell-side guard now also treats `existing.qty <= 0` as insufficient position, raising the documented `InsufficientPositionError` instead of crashing with an undocumented `ZeroDivisionError`.
- **BLOCK #3** (`envelope.py`) — `is_envelope_widened` required both the old and new cap value present before comparing magnitudes, so nulling out (or omitting) a cap entirely — the single most extreme possible widen — never tripped the AC-3 re-ceremony gate. Fixed: old-present/new-`None`(or absent) is now always a widen.
- **FLAG #4** (`ledger.py` + `sizing.py`) — non-finite rejection never checked sign, so `reserve()`/`apply_fill()` silently accepted non-positive `notional_usd`/`price`/`qty`, and `sizing.py`'s `fractionable=True` early-return path had no sign check at all. Fixed: `reserve()` rejects `notional_usd <= 0`; `apply_fill()` rejects `price <= 0` and `qty < 0` (`qty == 0` stays legal, reaching BLOCK #2's check instead); `_floor_to_whole_share` rejects any negative `raw_qty` before the `fractionable` branch.

The one initially-skipped test (`test_zero_qty_sell_against_sold_out_position_...`) was later pinned to a hard assertion (`raises_insufficient_position`) once `sleeve-risk-impl` concretely chose the raise-over-no-op resolution for BLOCK #2 — see commit `0c5c4df`. Final state: 220 passed / 0 failed / 0 skipped across the full P1 verification scope — `tests/sleeves/` (179) + `tests/database/test_033_sleeves_client_order_id.py` (24) + the two no-order-path canaries `test_m2_no_order_path.py` + `test_dashboard_no_order_path.py` (17) = 220.

## Internal Dependencies

- `sleeves/alpaca_orders.py` — `requests` only (stdlib `os`, `time`, `dataclasses`, `urllib.parse.quote`). No internal AlphaBot imports.
- `sleeves/reconciliation.py`, `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py` — stdlib only (`dataclasses`, `math`). No imports of each other or of `sleeves/alpaca_orders.py` — each is independently unit-testable.
- `database.py` — owns all `sleeve_*` table I/O. Not imported by any P1 module directly; P2's `sleeves/rules/limits.py` and `sleeves/rules/runner.py` both import it (the P1 accessors plus the new P2 `sleeve_rule_fires` accessors).
- `sleeves/rules/schema.py`, `sleeves/rules/senses.py` — stdlib only (`json`/`re`/`dataclasses`, `math`/`re`/`dataclasses`/`datetime` respectively). No internal AlphaBot imports; `senses.py` never imports `requests` (AST-verified, [invariant](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy) for FRED cache-only reads).
- `sleeves/rules/conditions.py` — imports `sleeves.rules.senses` (for the `SenseResult` type only; does not call its functions).
- `sleeves/rules/limits.py` — imports `database` (P1's `get_sleeve_runtime`/`set_sleeve_runtime`) plus stdlib (`uuid`, `datetime`, `zoneinfo`).
- `sleeves/rules/actions.py` — imports `sleeves.alpaca_orders`, `sleeves.envelope`, `sleeves.sizing` (the P1 modules it structurally routes every order through) plus `requests` directly (for the `notify` action's Discord webhook POST — distinct from `sleeves/alpaca_orders.py`'s own `requests` usage, and not a broker call).
- `sleeves/rules/runner.py` — the P2 orchestrator: imports `database`, `market_calendar`, and all five sibling `sleeves.rules` modules (`actions`, `conditions`, `limits`, `schema`, `senses`). The first module in the package to tie the whole rule engine together.
- No sleeves module is imported by `alpha_bot_execution.py`, `app.py`, or any other production module — there is still no production caller as of `7e0efe1`. `sleeves.rules.runner` will be the first production import site once wired, per the plan's lazy-import convention (`ai_advisor.py` CC-2 pattern: imported inside `main()`, never module-level) — P3 scope.
