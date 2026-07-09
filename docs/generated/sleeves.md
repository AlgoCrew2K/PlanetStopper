# sleeves

> Managed Sleeves: sleeve infrastructure + the direct Alpaca order layer (P1), the rule engine that senses/evaluates/dispatches through it (P2), and the engine/route/dashboard wiring that makes a rule reachable in production (P3) — the only part of the codebase permitted to place broker orders.

> **AUDIT STATUS (2026-07-09):** an independent branch-accuracy audit (`VERDICT-branch.md`, `DECISIONS.md` `DE-SLEEVES-FIX-001`) found the P3 wiring layer below was **not actually reachable in production** despite the "resolved" language this file previously used throughout — the rule engine was 100% inert via the real create-rule route (silent `KeyError` on every fireable tick), a paper-armed rule's orders were destroyed every tick by a cleanup bug (live-observed destroying a real filled order), and per-rule P&L rendered `$0.00` whenever a defensive rule closed an entry rule's position. **Do not treat this document's pre-2026-07-09 "done"/"resolved" claims about production reachability as current — they describe what P1/P2/P3 were believed to deliver before the audit, not what the audit proved.** Sections below are being corrected in place as each audit finding's fix lands GREEN; a correction reads **"Corrected post-audit (2026-07-09)"** and cites the finding number and fix commit. See `DE-SLEEVES-FIX-001` for the fix cycle's live status.

**Source:** `sleeves/__init__.py`, `sleeves/alpaca_orders.py`, `sleeves/reconciliation.py`, `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py` (P1); `sleeves/rules/{schema,senses,conditions,limits,actions,runner}.py` (P2); `sleeves/tick_orchestrator.py` + its `alpha_bot_execution.main()` hook + the `/api/sleeves*` routes (`app.py`) + the Sleeves dashboard panel (`templates/index.html`, `static/index.js`) + the EOD digest extension (`reporting.py`) (P3); plus the `sleeve_*` accessors in `database.py` and `migrations/033_sleeves.sql` + `migrations/034_sleeve_rule_fires.sql`
**Last updated:** 2026-07-09 (audit fix cycle `DE-SLEEVES-FIX-001` in progress — finding #3 fixed at `4abfaf6`; findings #1/#2/#4/#5/#6/#7/#8/#9-#15/AC-11 RED-landed, GREEN in progress; see the AUDIT STATUS banner above). **Prior state (2026-07-08):** P1 GREEN tip `0c5c4df`, review-approved; P2 GREEN tip `ef34848`, review-approved; P3 GREEN tip `dfbaa4a` — includes s3-review's BLOCK 1+2 fix (`9d8e46c`), the shared-account reconciliation redesign (BLOCK 3, `c178ee2`), the AC-16 panel-rendering fixes (`821b385`, `cd9a970`), the delete route's non-terminal-order-refusal fix (`f8ca848`), and three fixes the PM's earlier hand-seeded paper-account smoke test drove in — the epic's done-bar blocker (live daily-bar/FRED feed wiring, task #33, `d62348a`/`a80d2c3`), the empty-allowlist deny-all bug (task #34, `2c8fcb5`), and sub-penny bracket-leg rejections (task #35, `dfbaa4a`) — ruff clean throughout. See [Notes from the P3 review cycle](#notes-from-the-p3-review-cycle) below. **Superseded by the 2026-07-09 audit** (see banner above): that smoke test used hand-seeded, non-route-shaped engine state and did not establish production reachability — see `DECISIONS.md` `DE-SLEEVES-FIX-001`. The `arm_sleeve_live` prior-PAPER/track-record open point (`DE-SLEEVES-P3-001` "Operator questions for PR review") is unaffected by the audit and remains open.

## Overview

A managed sleeve is a bounded slice of the operator's own Alpaca account (paper today; live only once live keys are provisioned) governed by operator-authored rules. P1 shipped the infrastructure a rule engine acts through: the single Alpaca order client, envelope clamping, risk-based sizing, cash/position ledger accounting, and broker-truth reconciliation. P2 builds the rule engine itself (`sleeves/rules/`): JSON rule-doc validation, a closed sense registry, fail-safe condition-tree evaluation, a DB-backed pacing/episode-latch state machine, action dispatch, and the tick orchestrator. Every rule is born in SHADOW (record-only fires, executes nothing); the runner/actions layer also structurally supports a non-SHADOW (armed) path that reaches the P1 order client only through `envelope.clamp`. **P3 built** the `/api/sleeves*` route surface, both arming ceremonies (SHADOW→PAPER's shadow-fire gate and PAPER→LIVE's panic-flow ceremony), the dashboard panel, and the `alpha_bot_execution.main()` engine hook. **Corrected post-audit (2026-07-09, `DE-SLEEVES-FIX-001` finding #1):** the claim that "a rule really can reach PAPER mode and place a real paper-account order" was true only for hand-seeded, non-route-shaped engine state — via the actual `POST /api/sleeves/<id>/rules` create route, every rule crashed with a silent `KeyError` on its first fireable tick, forever, so no route-created rule could ever record a fire, let alone place an order. As of finding #3's fix (`4abfaf6`), arming DOES correctly promote the owning sleeve's status to PAPER (see [Managed Sleeves P3: engine wiring and surfaces](#managed-sleeves-p3-engine-wiring-and-surfaces) below); the create-route reachability gap (findings #1/#2) is still RED-only as of this writing, tracked in `DE-SLEEVES-FIX-001` and updated here once its GREEN lands. See [Managed Sleeves P2: the rule engine](#managed-sleeves-p2-the-rule-engine) and [Managed Sleeves P3: engine wiring and surfaces](#managed-sleeves-p3-engine-wiring-and-surfaces) below.

Every module here is a pure function library (no I/O beyond `sleeves/alpaca_orders.py`'s HTTP calls) — dataclasses in, dataclasses out, so the risk-critical math (clamping, sizing, conservation) is testable without a database or network. Persistence and orchestration are the caller's job.

## Architecture invariants (binding; enforced by `tests/sleeves/test_containment_invariants.py`)

1. **Single order-capable module.** `sleeves/alpaca_orders.py` is the only file in the repo permitted to reference the `/v2/orders` broker endpoint or define an order-placing function (`submit_bracket_order`, `submit_trailing_stop_order`, `cancel_order`, and the reserved-but-unused-in-P1 names `submit_order`/`place_order`/`close_position`/`liquidate_position`). A whole-repo AST scan asserts this — including explicitly probing `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py`, and `sleeves/reconciliation.py` as the most tempting places for a shortcut to leak in. This scan is additive to, and does not replace, the pre-existing single-file guards `tests/execution/test_m2_no_order_path.py` and `tests/app/test_dashboard_no_order_path.py`, which the containment test suite also pins as present and non-gutted (≥3 test functions each).
2. **Live-host string denylist.** The bare string `api.alpaca.markets` (as distinct from `paper-api.alpaca.markets`) may appear nowhere in the repo outside `sleeves/alpaca_orders.py`'s `resolve_host()` function or its module-level host constants. One pre-existing prose-only mention (`advisors/universe_provider.py`, explaining why paper keys 401 on the live host) is allowlisted, with its own guard test confirming that mention never turns into a real network call.
3. **`resolve_host()` is the single gated host-selection function.** Pure function of two caller-supplied booleans (`live_mode`, `live_keys_present`) — it reads no environment itself. Returns the paper host unless BOTH are `True`, in which case it returns the live host. Computing those two booleans (sleeve status + `SLEEVE_LIVE_EXECUTION` for `live_mode`; `ALPACA_LIVE_KEY`/`ALPACA_LIVE_SECRET` presence for `live_keys_present`) is the P2/P3 runner's responsibility, out of P1 scope.
4. **Envelope clamps are reduce-only.** `sleeves.envelope.clamp_order` never returns a `qty` greater than the requested `qty`. The one exception where a categorical rule (not a magnitude clamp) applies is the ticker allowlist, which gates **entries only** — a sell of a symbol the sleeve currently holds is never refused for being off the allowlist, so narrowing an allowlist (or a delisting) can never trap an existing position with no way out.
5. **Ledger conservation law.** `sleeves.ledger` maintains `cash_usd + reserved_usd + sum(position.cost_basis_usd) == capital_usd + realized_pnl_usd` after every legal operation — no dollar is created or destroyed by the ledger's own bookkeeping.
6. **RESERVED-then-native-status order lifecycle.** A `sleeve_orders` row is written at reserve time (status `'RESERVED'`, `alpaca_order_id` still `NULL`) using a `client_order_id` minted by the caller *before* the broker call — so a crash between reservation and broker ack is recoverable via `get_order_by_client_order_id`. Once the broker acks, the row's status transitions to Alpaca's own native order-status enum verbatim (`new`/`accepted`/`partially_filled`/`filled`/`canceled`/`rejected`/`expired`/...) — no invented `SUBMITTED`/`OPEN` synonyms. **Known behavior, not a defect (confirmed by the PM's real paper-account smoke):** the transition to the broker's native status doesn't happen synchronously with the broker ack itself — `_place_order_with_reservation` attaches only `alpaca_order_id` at ack time; the local row stays literally `'RESERVED'` until `sleeves/tick_orchestrator.py`'s `poll_and_apply_fills` next polls that order and calls `database.update_sleeve_order_status`. Since the engine tick runs once a minute, this is an expected, bounded eventual-consistency window (~60s) between "broker has genuinely accepted the order" and "our own `sleeve_orders.status` reflects that" — not a bug, and not something a caller should assume is instantaneous.
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

**Bracket leg prices are rounded to Alpaca's equity tick before submission (task #35 fix, PM's direct-Alpaca repro: `dfbaa4a`).** `take_profit_price`/`stop_loss_price` arrive from `sleeves/rules/actions.py`'s risk-sizing math unrounded (e.g. `14.839`, `12.8155`); Alpaca rejects these with `HTTP 422` (`sub-penny increment does not fulfill minimum pricing criteria`) — every real bracket entry was refused at the broker before this fix, even though the sizing/envelope/reservation logic upstream had all approved it. `_round_to_equity_tick(price, *, rounding)` quantizes via `decimal.Decimal` (never raw float floor/round — `495.00 / 0.01 == 49499.999999999993` in IEEE-754, which would silently floor an already-tick-valid price down a full cent) to Alpaca's documented equity tick size: 2 decimals at/above `$1.00` (`_EQUITY_TICK_HIGH_PRICE_THRESHOLD`), 4 decimals below. `stop_loss_price` rounds `ROUND_FLOOR` (a long's protective stop must never tighten closer to entry than sizing intended); `take_profit_price` rounds `ROUND_HALF_UP` to the nearest tick (not a protective boundary in the same tightening sense, so either direction is correct). Rounding happens ONLY at this module's boundary — `sleeves/rules/actions.py`'s own sizing/price math stays pure and unrounded.

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
1. **Allowlist** — a categorical gate, not a magnitude clamp, and **buy-side only** (review finding BLOCK #1, see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)). A sell of a currently-held symbol is never refused for `REASON_NOT_IN_ALLOWLIST` — narrowing the allowlist or a delisting must not trap an existing position with no sanctioned exit. A sell is still subject to the long-only/position-qty cap below, just not the allowlist. **An empty or absent allowlist means NO ticker confinement** (task #34 fix, PM ruling on a real paper-account smoke finding: `2c8fcb5`) — the gate is skipped entirely rather than refusing every buy. The original behavior refused every buy for a sleeve whose operator never populated an allowlist (an empty list read as "confined to nothing," not "unconfined"), making the rule's own `when.symbol` unreachable and contradicting AC-2's intent; the real money-safety bounds for an unconfined sleeve are the dollar/position/turnover caps below, which still apply unconditionally regardless of the allowlist. A genuinely non-empty allowlist continues to confine exactly as before — only the vacuous-list case changed.
2. **Per-side magnitude caps**, each a ceiling on qty, floored to a whole share, tightest cap wins via sequential min-reduction: sell is capped at `current_position_qty` (long-only, always applies); buy is capped by `max_position_pct`'s remaining room; both sides are capped by `max_order_usd / price` and by the remaining `max_daily_turnover_usd` budget.
3. If magnitude clamping reduces qty to `<= 0`, the order is refused with reason normalized to `REASON_REDUCED_TO_ZERO` regardless of which specific limit hit zero.

### Types

**`ClampResult`** (frozen dataclass) — `approved: bool` (False = refuse outright, qty forced to 0), `qty: float` (final qty, never greater than `original_qty`), `original_qty: float`, `clamped: bool` (True iff `qty != original_qty`), `reason: str | None` (populated whenever `clamped` or not `approved`).

**Reason codes:** `REASON_NOT_IN_ALLOWLIST`, `REASON_MAX_POSITION_PCT`, `REASON_MAX_ORDER_USD`, `REASON_MAX_DAILY_TURNOVER`, `REASON_LONG_ONLY_NO_SHORT`, `REASON_REDUCED_TO_ZERO` (reported instead of a specific limit's reason when the combined clamping would leave `qty <= 0`).

### API Reference

#### `clamp_order(*, symbol, side, qty, price, envelope: dict, sleeve_equity: float, current_position_qty=0.0, turnover_used_usd=0.0) -> ClampResult`
Clamps a proposed order to the sleeve's envelope. `envelope` shape (operator-authored, schema-validated in P2): `{"allowlist": [...], "max_position_pct": float, "max_order_usd": float, "max_daily_turnover_usd": float, "long_only": bool}`. Any cap key that is `None` or absent means "unlimited" for that dimension.

#### `is_envelope_widened(old_envelope: dict, new_envelope: dict) -> bool`
True iff `new_envelope` is less restrictive than `old_envelope` in any dimension — drives the P3 arming route's widen-requires-re-ceremony gate (AC-3). Since `clamp_order` treats an absent/`None` cap as unlimited, **removing a cap entirely** (old value present, new value `None`/absent) is the single most extreme possible widen and is correctly flagged (review finding BLOCK #3, fixed after the initial GREEN required both old and new values present before comparing — see [Notes from the P1 review cycle](#notes-from-the-p1-review-cycle)). The reverse direction (old absent, new present — going from unlimited to bounded) is a narrowing and is never flagged. Narrowing or an identical envelope returns `False`. **The allowlist follows the same unlimited-means-empty/absent pattern (task #34 fix, `2c8fcb5`):** clearing a genuinely populated allowlist down to empty/absent is the single most extreme possible allowlist widen (confined → the whole universe) and IS flagged; the reverse (empty/absent → populated) is a narrowing and is NOT flagged. Only when both sides already confine to a real, non-empty set does the subset comparison apply. The rewrite was necessary because the prior subset-only check assumed the old "empty = deny-all" semantics and would have mis-flagged `2c8fcb5`'s own narrowing direction as a widen while failing to flag the real extreme case at all.

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

**Resolved in P3** (tracked as a known limitation during P2; s2-review, `DECISIONS.md` `DE-SLEEVES-P2-001`): P2 never advanced an acked order's status past `'RESERVED'` and never recorded a fill (fill-polling was AC-9/P3 scope), so an acked order's reservation never naturally released via this function during P2. `sleeves/tick_orchestrator.py`'s `poll_and_apply_fills` (commit `d7c8bb5`) closes this: every engine tick polls broker-truth status for each non-terminal, post-ack `sleeve_orders` row, records any newly-filled quantity as a real `sleeve_fills` row, and advances the order's status to the broker's own status string verbatim — so this function's next fold sees the real fills and correctly releases any true unfilled remainder once the order reaches a terminal status. The safe-direction property held throughout the gap (over-reserves, never under-reserves). See [`sleeves/tick_orchestrator.py`](#sleevestick_orchestratorpy) below and `DECISIONS.md` `DE-SLEEVES-P3-001`.

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

**Entries require a declared exit (resolved `083526f`, AC-7, PM decision 2026-07-08):** a `buy` action must declare `stop_loss_pct` or `trailing_stop_pct` (`schema.py`'s `_validate_buy_exit_fields` — a schema validation error otherwise; each, when present, must satisfy `0 < pct < 1`) — there is no default/fallback stop, and the earlier `_DEFAULT_BRACKET_STOP_LOSS_PCT`/`_DEFAULT_BRACKET_TAKE_PROFIT_PCT` constants have been removed entirely (a module-source-inspection test enforces their absence). `_dispatch_buy` derives `stop_loss_price = price * (1 - declared_pct)` from whichever of the two fields is declared — note that a declared `trailing_stop_pct` on an entry still produces a **fixed** bracket `stop_loss_price` at that distance, not an Alpaca-native trailing-stop leg on the entry order itself; a real trailing-stop order is `set_stop`'s domain, for protecting an *existing* position. `take_profit_pct` stays optional; when absent, the take-profit distance is derived as a fixed `_TAKE_PROFIT_REWARD_RISK_RATIO = 2.0` multiple of the rule's own declared stop distance — never an independent, disconnected percentage — so it scales with whatever risk the rule declares rather than acting as a second hidden default (RED-pinned by a ratio-consistency test across two different stop distances, never asserting the ratio's value itself). **Operator question, flagged for PR review (not resolved here):** the field name `trailing_stop_pct` on an entry may read as promising a live adjusting trailing-stop leg; whether that's the intended entry-time semantics or a naming point worth revisiting is recorded in `DECISIONS.md` `DE-SLEEVES-P3-001`'s "Operator questions for PR review" list (item 1).

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
5. Else dispatches **every** action in the rule's `then` list via `actions.dispatch_action` (`shadow = rule["mode"] == "SHADOW"`; `ActionContext` is built with `sleeve_row["capital_usd"]` and `rule["id"]` per invariant 9, added `ef34848`), and for each action — fired or refused — writes one `sleeve_rule_fires` row via `database.insert_sleeve_rule_fire`: `rule_class`/`mode_at_fire` snapshotted at this tick (so a later rule edit or mode change never rewrites history), the sensed snapshot and an `outcome_json` (would-have qty/notional, `executed`, `refused_reason`, the raw order/order_error), `clamped`/`clamp_reason` from the `ClampResult`, `episode_id` from the pacing result, and (as of `ef34848`) `ActionResult.order_id` — populated for any armed, successfully-reserved order-placing action, `NULL` for SHADOW fires, refused actions, and `notify`. Since P2 had no production caller yet (no route/ceremony wired a rule to `PAPER`/`LIVE` mode), every fire actually produced during P2 was still `SHADOW` and still got `NULL` in practice — but the runner/actions code itself was fully wired end-to-end for AC-19's trade-to-fire-to-order trace once P3 permitted arming. **Resolved in P3:** the `/api/sleeves*` arm route and `alpha_bot_execution.main()`'s engine hook are the production caller now — see [Managed Sleeves P3: engine wiring and surfaces](#managed-sleeves-p3-engine-wiring-and-surfaces) below.

## Managed Sleeves P3: engine wiring and surfaces

`sleeves/tick_orchestrator.py` plus the `/api/sleeves*` route surface (`app.py`), the Sleeves dashboard panel (`templates/index.html`/`static/index.js`), and the EOD Discord digest extension (`reporting.py`) close the "P2 has no production caller" gap described above — a rule really can reach PAPER mode and place a real paper-account order now, and the sleeve's status/cash/fires are visible on the dashboard.

### `sleeves/tick_orchestrator.py`

The P3 engine-tick entry point (AC-9, AC-10, AC-12). `run_sleeve_tick_for_all_sleeves(*, now_utc, discord_webhook_url=None) -> list` is the single function `alpha_bot_execution.main()` calls. Per sleeve, in order:

0. **SHADOW-status sleeves additionally get `cancel_open_orders_for_shadow_sleeve`** — AC-12's disarm support, since the disarm route can never itself reach the broker (see `disarm_sleeve` below). This is an ADDITIONAL step, not a replacement for steps 1-2.
1. **`poll_and_apply_fills`** — runs for EVERY sleeve regardless of status. Polls broker-truth status for every non-terminal, post-ack `sleeve_orders` row (a still-`RESERVED` pre-ack row, `alpaca_order_id` still `NULL`, is skipped — nothing to poll yet). When the broker reports a filled quantity beyond what `sleeve_fills` already records for that order, the delta is inserted as a new fill row (`broker_fill_id` deterministically keyed on `f"{alpaca_order_id}:{broker_filled_qty}"` for defense-in-depth dedup on top of the delta math, which is the load-bearing idempotency mechanism) and the order's status advances to the broker's own status string verbatim — never an invented synonym. A broker error here is logged at `WARNING` (this is the only place in the stack that can surface it, since `sleeves/alpaca_orders.py` never logs internally by its own never-raises contract) and the order is retried next tick.
2. **AGGREGATE reconciliation** (`_run_aggregate_reconciliation`) — runs ONCE PER TICK per resolved-broker-host group (never per sleeve), across every sleeve in that group that is neither already `PAUSED_RECONCILIATION` coming in nor failed step 1's fill-poll. `alpaca_orders.get_account`/`get_positions` are each called EXACTLY ONCE for the whole group. A broker API failure on either call is treated as a breach for every sleeve in the group (fail-closed — "the call returned nothing" is never read as "the broker confirms zero drift"). Otherwise:
   - **Cash** (`sleeves.reconciliation.reconcile_aggregate_cash`): one-sided — breaches iff `Σ(sleeve.cash_usd + sleeve.reserved_usd)` across every checked sleeve exceeds `broker_cash_usd + cash_tolerance_usd`. The reverse (broker cash exceeding the combined claim) is explicitly NOT a breach — unallocated float, or the operator's own money sharing the account, is normal. A cash breach pauses EVERY sleeve in the group (blame is unattributable across virtual slices of one real account). **Including `reserved_usd` in the claim is a deliberate, now test-pinned choice** (`s3-review`'s full re-verification of `c178ee2` flagged it as a genuinely open, non-blocking point; verified correct by reading `sleeves.ledger.reserve()` directly — it moves `notional_usd` from `cash_usd` into `reserved_usd`, so their sum is invariant across a reservation with no fill, matching real broker semantics where an accepted-but-unfilled order doesn't move settled cash either): `test_open_unfilled_order_does_not_cause_a_false_aggregate_cash_breach` (commit `6eec056`) confirms a sleeve with a real accepted-but-unfilled order never false-breaches against a clean broker-truth account.
   - **Positions** (`sleeves.reconciliation.reconcile_aggregate_position`): stays per-symbol, but the sleeve side is aggregated — for every symbol any checked sleeve holds a nonzero qty in, breaches iff the sleeves' combined qty exceeds `broker_qty * (1 + position_tolerance_pct)`. A broker position in a symbol no sleeve has ever touched is ignored entirely (never even inspected). A breach pauses only the sleeves holding that symbol.

   Either kind of breach persists `PAUSED_RECONCILIATION` onto the affected sleeve row(s) and best-effort posts a Discord alert, and skips rule evaluation for them this tick. A SHADOW sleeve participates in aggregate reconciliation exactly like any other status — SHADOW is not a drift-detection exemption.

   **Resolved BLOCK (task #31, s3-ux live finding + PM ruling, fixed at `c178ee2`):** the earlier per-sleeve `reconcile_sleeve_or_pause` function — RETIRED, no longer exists in this module — compared ONE sleeve's own ledger cash against the WHOLE Alpaca account's cash figure (`alpaca_orders.get_account`'s `"cash"` field is account-level, not per-sleeve). This was correct only by coincidence with exactly one sleeve; a live 7-sleeve seeded-DB render reproduced the bug exactly (every sleeve paused within one tick, since each sleeve's own necessarily-smaller capital differed from the full account total by far more than any tolerance). The aggregate mechanism described above is the fix. Verified: `tests/sleeves/test_reconciliation.py` 28/28 passed, `tests/sleeves/test_tick_orchestrator.py` 21/21 passed; full regression across `tests/sleeves` + engine-wiring + all 5 containment canaries + `test_main_pipeline.py` + routes/arming/disarm/replay/digest tests — 547 passed / 0 failed in this file's scope (8 unrelated pre-existing failures in `tests/app/test_sleeves_panel_render.py` are task #32's panel-rendering BLOCK, untouched by this fix), ruff clean.
3. **Otherwise**, assembles the sleeve's sense context (equity from `_book_equity_usd` — the ledger's own conservation-law figure, cash + reserved + cost-basis, deliberately NOT a broker mark-to-market; sizing off it is conservatively LOW when the sleeve sits on unrealized gains, never conservatively high) and dispatches through `sleeves.rules.runner.evaluate_rules` (the P2 engine) — this still includes SHADOW-status sleeves (AC-6: a SHADOW rule senses/evaluates/records fires; only `PAUSED_RECONCILIATION` skips it).

A single sleeve's exception anywhere in this sequence is caught and logged; processing continues for the remaining sleeves — mirrors the fail-safe containment contract `alpha_bot_execution.main()` applies to the whole module (a sleeve bug must never cost a symphony its exit; here, one sleeve's bug must never cost another sleeve its tick).

**`cancel_open_orders_for_shadow_sleeve`** cancels every non-terminal broker order for a SHADOW-status sleeve — never touches positions or broker-side stops (no `close_position`/`liquidate_position` call), so disarm stays non-destructive by design. A broker error on `cancel_order` is logged at `WARNING` AND best-effort posts a Discord alert specifically on this path — safety-critical, since an operator who clicked disarm must never be left believing an order is cancelled when the broker actually rejected the cancel request.

**Resolved (task #33, the epic's done-bar blocker — PM's real paper-account smoke found this):** `closes_by_symbol`/`fred_cache` were passed as hardcoded empty-dict literals to `evaluate_rules`, so `runner.py`'s own `price = closes[-1] if closes else 0.0` always yielded `0.0`, and `sizing.size_order` refused every entry with `error="invalid_price"` — an armed PAPER/LIVE sleeve could never genuinely place a trade. Safe (fails closed), but non-functional: this made the whole direct-trade epic a no-op in practice, not a money-safety gap.

Fixed at `d62348a`/`a80d2c3` in the rule-evaluation phase (step 3 above): the tick now collects every symbol referenced by an ENABLED rule across every evaluable sleeve, then makes exactly two fetches ONCE PER TICK (never per sleeve, never per symbol individually):

- **`_fetch_closes_for_symbols(symbols, *, now_et)`** — delegates to `synthetic_history.fetch_bars`, the SAME daily-bar path P1/P2 already use for history (never a second HTTP client), over a `_BARS_HISTORY_DAYS = 270`-day window (matching `advisors/lens_technicals.py`'s own history depth). Never raises: a network failure or a symbol the response omits both degrade to an empty closes list for that symbol — the pre-existing `sleeves.rules.senses`/`conditions` fail-safe contract (unavailable sense → not fireable, no fire recorded) handles the rest, unmodified.
- **`_build_fred_cache()`** — cache-only (D-1: no live FRED call from the engine tick, matching `ai_advisor.py`'s own cache-serve precedent). Reads `database.get_latest_market_lens_cache()`'s nightly `"macro"` lens block (`ai_advisor.py`'s `_build_macro_section`) and wraps each series' single latest cached observation into the one-element-list shape `sleeves.rules.senses.sense_fred_series` expects. **Excludes FRED's own `"."` missing-observation sentinel** (a non-blocking `s3-review` follow-up, `a80d2c3`): FRED's API returns the literal string `"."` for a data gap, not `null` and not an omitted key, which would otherwise reach `conditions.py`'s numeric comparator as a bare string and raise `TypeError` instead of degrading through the intended fail-safe path — every value is coerced through `float()` before admission, excluding `"."` (and any other non-numeric value) the same way a missing observation already would.

Both fetches are additionally wrapped at the call site for defense-in-depth against an unexpected internal error (degrading to empty dicts rather than aborting rule evaluation for every sleeve that tick), and are skipped entirely — no network/DB call attempted — when no evaluable sleeve has any enabled rule this tick. A DEFENSIVE/ENTRY rule that only senses `sleeve_status`/`sleeve_equity_usd`/`position_qty`/time-of-day was never affected by the original gap (those senses don't need bars/FRED) and is unaffected by the fix.

A third, non-blocking point from the P3 review pass — `arm_sleeve_live` has no check of a sleeve's prior PAPER arming/track record before allowing a jump straight to LIVE — was NOT encoded as RED (review didn't block on it) and is tracked instead as an open point in `DECISIONS.md` `DE-SLEEVES-P3-001` for the operator/PM to rule on. See `arm_sleeve_live` below.

### Engine hook (`alpha_bot_execution.py`)

`main()` lazy-imports `sleeves.tick_orchestrator` inside the function body (CC-2 convention, matching the `ai_advisor.py` precedent — never a module-level import, so the port-removal guard tests are unaffected) and calls `tick_orchestrator.run_sleeve_tick_for_all_sleeves(now_utc=current_et, discord_webhook_url=DISCORD_WEBHOOK_URL)` strictly AFTER the exit machine's own per-symphony execution queue and BEFORE `database.save_state` — the plan's non-negotiable ordering ("a sleeve can never delay/preempt symphony protection"). The whole call is wrapped in a bare `try/except Exception` that logs and continues — sleeve code can never break symphony trading, mirroring the fleet-correlation block's identical containment pattern immediately above it in the same function.

### Route surface (`app.py`)

All ten routes are CSRF-protected via the existing global `before_request` hook and session-auth via the existing global auth gate — no per-route code needed for either, matching the rest of `app.py`'s route conventions.

| Route | Function | Purpose |
|---|---|---|
| `GET /api/sleeves` | `list_sleeves` | List every sleeve row (panel data source). |
| `POST /api/sleeves` | `create_sleeve_route` | Create a sleeve (AC-1) — always born `SHADOW`; `capital_usd` fixed at creation. |
| `GET /api/sleeves/<id>/rules` | `list_sleeve_rules` | List every rule for one sleeve. |
| `POST /api/sleeves/<id>/rules` | `create_sleeve_rule_route` | Create a rule (AC-4/AC-6) — validated via `sleeves.rules.schema.validate_rule_doc`; always born `SHADOW` regardless of any `mode` the client sends. |
| `POST /api/sleeves/<id>/rules/<rule_id>/arm` | `arm_sleeve_rule` | SHADOW→PAPER (AC-13) — rejected without ≥1 recorded `mode_at_fire == "SHADOW"` fire for that rule (no arm-on-faith); never touches `SLEEVE_LIVE_EXECUTION`/`ALPACA_LIVE_*`. **Corrected post-audit (2026-07-09, `DE-SLEEVES-FIX-001` finding #3, fixed `4abfaf6`):** a successful arm also promotes the owning sleeve's own `status` SHADOW→PAPER (only from SHADOW; never demotes LIVE), and the route now refuses with `409` while the sleeve is `PAUSED_RECONCILIATION`. Before this fix, sleeve status never left SHADOW on arming, so a paper-armed rule's resting orders were canceled every tick by the SHADOW-cleanup step (live-observed destroying a real filled bracket order). |
| `POST /api/sleeves/<id>/arm-live` | `arm_sleeve_live` | PAPER→LIVE panic-flow ceremony (AC-14, see below). |
| `POST /api/sleeves/<id>/disarm` | `disarm_sleeve` | One-click kill switch (AC-12, see below). |
| `POST /api/sleeves/<id>/envelope` | `update_sleeve_envelope_route` | Widen/narrow the envelope (AC-3) via `sleeves.envelope.is_envelope_widened` — never a route-local heuristic; narrowing applies immediately, widening requires the same confirm-id + confirm-phrase ceremony shape as arm-live (phrase: `"WIDEN ENVELOPE"`). |
| `GET /api/sleeves/<id>/rules/<rule_id>/replay?days=N` | `replay_sleeve_rule` | Condition-replay diagnostic (AC-18, see below). |
| `POST /api/sleeves/<id>/delete` | `delete_sleeve_route` | Delete a sleeve (AC-16 finding #4, see below) — refuses unless flat. |

#### `arm_sleeve_live` — `POST /api/sleeves/<id>/arm-live`

Modeled directly on `sell_account`'s 6-gate chain: gates 1-4 are confirmation validation (`confirm_sleeve_id` must be supplied and match the URL's `sleeve_id`; `confirm_phrase` must be supplied and match `"ARM LIVE TRADING"` exactly via `secrets.compare_digest`) — checked BEFORE any environment/credential check. Every genuine ceremony attempt that clears gates 1-4 is audited via a best-effort Discord post and an `ERROR`-level log entry regardless of whether gates 5/6 subsequently block the arm (mirrors `sell_account`'s "audit always fires" contract). Gates 5-6: `ALPACA_LIVE_KEY`/`ALPACA_LIVE_SECRET` must both be present (read via `dotenv_values`, not bare `os.getenv`, matching the rest of `app.py`'s env-reload convention) and `SLEEVE_LIVE_EXECUTION` must be truthy — LIVE arming is impossible by construction without both.

**Open point (non-blocking, s3-review, routed to `DECISIONS.md` rather than invented here):** this route does not check whether the sleeve — or any of its rules — has ever actually been armed to PAPER, or has any paper track record at all. A sleeve can go straight from freshly-created SHADOW to `arm-live` as long as the 6 ceremony gates pass; AC-14's literal text doesn't require a prior-PAPER check and AC-21's culling-verdict gate is explicit P4 scope, but the plan's own P3 Architecture line says "PAPER→LIVE gate: culling verdict... **+ paper track-record minimum** + ceremony" — and no concrete track-record check exists yet. See `DECISIONS.md` `DE-SLEEVES-P3-001` for the full open point, recorded for the operator/PM to rule on rather than resolved here.

#### `disarm_sleeve` — `POST /api/sleeves/<id>/disarm`

AC-12's one-click kill switch — SYNCHRONOUS and DB-only: reverts the sleeve's own `status` and every one of its rules' `mode` back to `SHADOW` immediately via `database.update_sleeve_status`/`update_sleeve_rule_mode`. **Deliberately never calls `sleeves.alpaca_orders.cancel_order`** (or any order-capable function) — doing so from `app.py` would trip the pre-existing whole-repo containment invariant (`tests/app/test_dashboard_no_order_path.py`, which denylists `cancel_order` in every route except `sell_account`). Actual broker cancellation of a disarmed sleeve's lingering open orders happens on the engine's very next tick instead, via `sleeves/tick_orchestrator.py`'s `cancel_open_orders_for_shadow_sleeve` — positions and broker-side stops are never touched either way (disarm is non-destructive by design, matching the plan).

*Design correction mid-cycle:* the disarm route was initially written to call `cancel_order` directly, which would have broken `test_dashboard_no_order_path.py`. Caught pre-emptively by `s3-dashboard`, resolved by `s3-test-writer` at commit `9acc53e` before any GREEN landed — moved to the DB-only/synchronous contract described above, with cancellation relocated to the engine tick.

#### `replay_sleeve_rule` — `GET /api/sleeves/<id>/rules/<rule_id>/replay?days=N`

AC-18's condition-replay diagnostic — `days` must satisfy `1 <= days <= 60`. Runs the rule's own `if` condition tree through the real `sleeves.rules.conditions`/`senses` modules (the same engine code a live tick uses, via `sleeves.rules.runner._collect_sense_keys` to gather referenced sense keys) — never a route-local reimplementation. **P3 ships no historical daily-bar source of its own** (out of this route's committed scope per the plan): every call passes `closes=[]`/empty `fred_cache`, so any indicator or FRED sense correctly reports `insufficient_history`/fails safe, and the response is an honest empty `would_have_fired` list rather than a fabricated fire. The response is labeled `"condition_replay_diagnostic"` and never carries a P&L-shaped key — never an arming input (the plan's explicit decision).

`database.py` gained one new accessor backing the arm/disarm mode transitions: `update_sleeve_rule_mode(rule_id, mode) -> None` — updates one `sleeve_rules` row's `mode` column and stamps `updated_at`; mirrors `update_sleeve_status`'s contract (writes unconditionally — ceremony gating is the caller's job).

#### `delete_sleeve_route` — `POST /api/sleeves/<id>/delete`

AC-16's delete control (finding #4). Refuses (`409`) unless the sleeve is flat: reconstructs the sleeve's `LedgerState` via `sleeves.ledger.reconstruct_from_history` (zero new schema, same mechanism `_build_sleeves_panel_context` uses below) and checks two conditions — every position's qty is zero, AND every one of the sleeve's orders has a status inside the terminal-status set (`filled`/`canceled`/`expired`/`replaced`/`done_for_day`/`rejected` — the same tuple `database.get_daily_turnover_usd` and `sleeves/tick_orchestrator.py` already use). Both checks fail closed (refuse) on any read/reconstruction exception. `404` for an unknown `sleeve_id`. Delete never liquidates (matches the plan's Edge Cases — "refuse unless flat"); soft-FK history rows in `sleeve_rules`/`sleeve_orders`/`sleeve_fills`/`sleeve_runtime`/`sleeve_rule_fires` are left in place as orphaned history (no `PRAGMA foreign_keys=ON` anywhere in this schema).

**Resolved BLOCK (s3-review, against the initial `821b385` GREEN):** the first version only checked open positions — but a position is only ever created by `ledger.apply_fill()`, so an order the broker has accepted but not yet filled (or a still-`RESERVED` pre-ack row) produces zero positions and read as falsely flat, even though it's a live, unrecoverable broker exposure: once the sleeve row is gone, `database.get_all_sleeves()` never includes it again, so nothing would ever poll/cancel/reconcile that order again for the rest of the process's life — worse than the disarm route's already-handled TOCTOU gap, since a disarmed sleeve's row survives and is revisited every tick. Fixed at `f8ca848` by adding the non-terminal-order check above; `"RESERVED"` (the pre-ack default) is correctly absent from the terminal-status tuple, so the one check covers both the accepted-but-unfilled and still-`RESERVED` cases with no separate branch. RED at `a7e57b4`, GREEN at `f8ca848` — 21/21 passed in `tests/app/test_sleeves_panel_render.py`; full regression 1191 passed / 0 failed, ruff clean.

`database.py` also gained `delete_sleeve(sleeve_id) -> None` — deletes one `sleeves` row unconditionally (the refuse-unless-flat gating above is an application-layer decision, mirrors `update_sleeve_status`'s "accessor writes unconditionally" contract).

### Dashboard panel and Atlas cache-health badge (AC-16)

`app.py`'s `dashboard()` route (`GET /`) gained two new context helpers, both read-only and never-raising (a read failure degrades that row rather than 500ing the whole dashboard — the project's dashboard-truth convention):

- **`_build_sleeves_panel_context() -> list[dict]`** — per-sleeve `{id, name, status, status_badge_class, capital_usd, ledger_cash_usd, realized_pnl_usd, rules: [{id, name, mode, today_fires, lifetime_fires, realized_pnl_usd}]}` (sleeve-level `realized_pnl_usd` added alongside the finding #7 fix below). **Corrected post-audit, RESOLVED (2026-07-09, `DE-SLEEVES-FIX-001` finding #14/#15, fixed `e18fe11`):** `today_fires`/`lifetime_fires` previously derived from `len(get_sleeve_rule_fires(...))`, whose default `limit=100` silently capped LIFETIME fires at 100 forever (both panel and digest), and `today_fires` compared an ET date-string prefix against UTC-stored `fired_at`, misattributing evening fires to the next day. Both surfaces now share `database.get_sleeve_rule_fire_count` (ground-truth lifetime `COUNT(*)`, uncapped) and a DST-correct `get_fire_count_for_rule_on_day` (Eastern calendar-day window via `zoneinfo`, not a UTC string compare). Verified: `tests/sleeves/test_meds_hardening.py`'s `TestLifetimeFireCountIsUncapped`/`TestTodayFireCountUsesEasternDates` passed; 72 keep-green, ruff clean.
  - `status_badge_class` (task #32 finding #1) comes from `_sleeve_status_badge_class`, a lookup against `_SLEEVE_STATUS_BADGE_CLASSES` mapping all 6 named statuses to their OWN visually-distinct pill class (`SHADOW`→`standby`, `PAPER`→`armed`, `LIVE`→`triggered`, `BENCHED`→`sleeve-benched`, `PAUSED_RECONCILIATION`→`sleeve-paused`, `stale`→`sleeve-stale`) — replacing an earlier 3-way `if`/`elif` that collapsed 4 of the 6 statuses into a shared "standby" look (`PAUSED_RECONCILIATION` in particular must never look like a mundane fresh `SHADOW` sleeve). An unrecognized status fails safe to `standby`.
  - `ledger_cash_usd` and each rule's `realized_pnl_usd` are derived with ZERO new schema from `sleeve_orders`/`sleeve_fills` history. **Corrected post-audit, RESOLVED (2026-07-09, `DE-SLEEVES-FIX-001` finding #7/#8, fixed `37fefbe`):** the original task #32 implementation folded each rule's own filtered order history through `sleeves.ledger.reconstruct_from_history`, which rendered `$0.00` for EVERY rule on the sleeve whenever a sell was attributed to a different rule than the buy — not an edge case, since a DEFENSIVE rule selling an ENTRY rule's position is the intended design. Fixed by `sleeves.ledger.attribute_realized_fills` (new pure function): ONE sleeve-wide fill-level fold attributing each sell fill's realized delta back to the ENTRY rule whose lots it closes (buy-side attribution; per-rule values sum to ledger truth by construction). On fold failure both the per-rule and sleeve-level figures are `None`, rendered as `"n/a"` — never a fabricated `$0.00`. The EOD digest (`reporting.py`'s `_build_sleeve_digest_summaries`) now derives from the SAME fold (the prior hardcoded `0.0`/"no fill-attribution wired yet" docstring, audit finding #8, is corrected). Verified: `tests/sleeves/test_per_rule_pnl_truth.py` 4/4 passed; 109 keep-green across sleeve routes/arming/disarm/panel/digest/ledger suites, ruff clean. **The "vs broker truth" signal is still the existing `sleeve.status` column** (`PAUSED_RECONCILIATION` already means "known mismatch") — persisting a raw broker-truth cash figure for a direct side-by-side comparison would need a schema migration and stays a separate, explicit open point (operator-questions item #3 in `DECISIONS.md` `DE-SLEEVES-P3-001`), not silently assumed in scope here.
- **`_atlas_cache_health() -> dict`** — audit MEDIUM-2's cache-health badge. Opens a read-only (`file:...?mode=ro`) SQLite connection directly against the Atlas/Front Runner cache DB (`advisors/atlas_cache.py`'s schema) and reports `{available, age_days, reason}` — never calls the live Atlas/Front Runner loader from the request thread (the weekly-cache refresh is triggered elsewhere, never from a Flask route). A missing DB/table/row or an unparseable timestamp each degrade to a distinct structural `reason` string rather than a raised exception.

`templates/index.html` renders a standalone `data-testid="sleeves-panel"` section (existing light-theme card styling, `var(--studio-*)` tokens only — no new CSS framework, no dark-theme classes) with one `data-testid="sleeve-card"` per sleeve, a `data-testid="sleeve-status-badge"` using the 6-way-distinct `status_badge_class` above, the `data-testid="atlas-cache-health-badge"`, a per-rule `data-testid="sleeve-arm-control"` ("Arm Paper" button, shown only when `rule.mode == 'SHADOW'`), a per-sleeve `data-testid="sleeve-disarm-control"`, and a per-sleeve `data-testid="sleeve-delete-control"` (task #32 finding #4 — see `delete_sleeve_route` above). Sleeve/rule names render through plain Jinja autoescaping (no `|safe`) — pinned by an XSS RED test.

Three new CSS status-pill classes back the badge mapping: `.sleeve-benched` (amber), `.sleeve-paused` (bordered, bold, red — visually distinct from `LIVE`'s plain-red `triggered` pill), and `.sleeve-stale` (dashed border, muted ink/cyan). A new `.control-disabled` class (`opacity: 0.4`, `cursor: not-allowed !important`) applies to the Disarm button whenever the sleeve is `SHADOW`-status (task #32 finding #5) — the `!important` is load-bearing: the button's own inline style sets `cursor: pointer` unconditionally, and a class selector alone can never out-specificity an inline style. **Cursor-specificity residual fixed at `cd9a970`:** the first pass (`821b385`) correctly dropped the disabled button's opacity to `0.4` but left its cursor still `pointer` (no `!important`), a gap `s3-ux`'s re-render caught and confirmed fixed with no test/behavior change beyond the CSS rule itself.

Every flex row in the panel (section header, card header, rule row, button row) also gained `flex-wrap`+`gap` with `align-items: center` (task #32 finding #6, mobile 375px, CSS-only) so a wrapped long name never misaligns its sibling badge/buttons.

**`arm-live` and envelope-widen are deliberately NOT wired to a one-click control** — those ceremonies need a dedicated confirm-phrase modal (a follow-up UI pass); SHADOW→PAPER arm, disarm, and delete get real buttons in P3. No sleeve-creation UI exists yet either (no committed RED requires it). The panel is server-rendered on each full page load like the rest of the dashboard — no new client-side polling interval.

`static/index.js` gains `armSleeveRuleToPaper(sleeveId, ruleId, btn)`, `disarmSleeve(sleeveId, btn)`, and `deleteSleeve(sleeveId, btn)` (task #32), all appended inside the existing IIFE so they reuse the module's already-fetched `_csrfToken` rather than duplicating the CSRF-token fetch — each confirms via `window.confirm`, POSTs to the corresponding route, and does a full `location.reload()` on success (no partial DOM patch).

**Verified (task #32):** targeted `-n0`, 19/19 passed in `test_sleeves_panel_render.py` at `821b385` (82/82 across all sleeves-touching RED files); 1189 passed / 0 failed in the full `tests/app`+`tests/sleeves` regression; the cursor fix at `cd9a970` changed no test outcomes (19/19 still passing); ruff check/format and `node --check` on `static/index.js` all clean.

### Discord EOD digest extension (AC-17)

`reporting.py` gains `build_sleeves_digest_section(sleeve_summaries: list[dict]) -> str` — a pure, never-raising formatter; returns `""` (falsy) for an empty list so no phantom section appears when zero sleeves exist, and every optional key is read defensively so an incomplete summary dict degrades gracefully. `_build_sleeve_digest_summaries(current_date_str) -> list[dict]` assembles the summaries from live DB rows the same never-raising way as the panel helper above. Both `realized_pnl_usd` (`0.0`) and `benched` (`False`) are **honest placeholders** — the same fill-attribution/churn-brake gap noted in the panel section — never fabricated. Wired into `send_eod_discord_post()` as a new embed, appended only when the formatter returns a non-empty string.

## Notes from the P3 review cycle

`s3-review`'s post-GREEN pass over the engine wiring (`d7c8bb5`) found two BLOCK-level gaps and one non-blocking open point; a third BLOCK-level gap (shared-account reconciliation semantics) surfaced later from `s3-ux`'s live visual-gate testing and has since been fixed:

- **BLOCK 1 — SHADOW-sleeve reconciliation gap.** `run_sleeve_tick_for_all_sleeves` originally routed SHADOW-status sleeves only through `cancel_open_orders_for_shadow_sleeve`, never `poll_and_apply_fills`/`reconcile_sleeve_or_pause` — a disarmed sleeve stayed SHADOW permanently until re-armed and could hold real broker-side exposure (a TOCTOU fill between the disarm click and the tick's cancel attempt, or a residual position) with nothing ever catching the drift again. Fixed: both functions now run for every sleeve regardless of status, gated only on "already `PAUSED_RECONCILIATION` coming into this tick" (`cancel_open_orders_for_shadow_sleeve` remains an additional step for SHADOW specifically, not a replacement).
- **BLOCK 2 — silent broker-error swallow.** `poll_and_apply_fills` and `cancel_open_orders_for_shadow_sleeve` both silently `continue`d on any `OrderResult.error` with zero logging anywhere in the stack (`sleeves/alpaca_orders.py` never logs internally by its own never-raises contract). Fixed: both now log at `WARNING` on a broker error; `cancel_open_orders_for_shadow_sleeve` additionally gained a `discord_webhook_url` parameter and best-effort posts a Discord alert on a cancel failure specifically — an operator who clicked disarm must never be left believing an order is cancelled when the broker actually rejected it.
- **Non-blocking open point — `arm_sleeve_live` has no prior-PAPER/track-record check.** Not encoded as RED (review didn't block on it); routed to `DECISIONS.md` `DE-SLEEVES-P3-001` for the operator/PM to rule on. See `arm_sleeve_live` above. This point remains OPEN as of this doc update.

The original two BLOCK findings were encoded as RED at commit `f4e496a` (7 new tests in `tests/sleeves/test_tick_orchestrator.py`, confirmed genuinely failing against the prior GREEN, not vacuous) and fixed at commit `9d8e46c` (`tests/sleeves/test_tick_orchestrator.py` 19/19 passed; full regression — `tests/sleeves/` (P1+P2+P3) + `test_p3_sleeves_engine_wiring.py` + all 5 containment canaries + `test_main_pipeline.py` — 477 passed / 0 failed, ruff clean).

- **BLOCK 3 (resolved) — shared-account reconciliation semantics.** `s3-ux`'s live 7-sleeve visual-gate testing (task #25) found `reconcile_sleeve_or_pause` comparing one sleeve's own cash against the whole Alpaca account's cash — every sleeve paused within one tick under real multi-sleeve use. PM ruling: a one-sided aggregate cash invariant (`Σ sleeve cash ≤ account cash + tolerance`, aggregate breach pauses ALL sleeves) plus per-symbol position aggregation across sleeves (an unattributed broker position is ignored, not flagged). RED at `c5a78bc` (`s3-test-writer`), fixed at `c178ee2` (`s3-engine`) — `reconcile_sleeve_or_pause` retired entirely, replaced by `_run_aggregate_reconciliation` plus two new pure functions in `sleeves/reconciliation.py` (`reconcile_aggregate_cash`, `reconcile_aggregate_position`). Full detail under [`sleeves/tick_orchestrator.py`](#sleevestick_orchestratorpy) above.

A further AC-16 panel-rendering BLOCK (task #32 — status-badge color-collapse, disabled-button styling, a missing delete control, missing cash-ledger/per-rule-P&L display) is in flight from the same `s3-ux` visual-gate pass, RED committed at `2d5dcae` and dispatched to `s3-dashboard`. Not yet GREEN as of this doc update — will be documented once it lands.

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
| `update_sleeve_rule_mode(rule_id, mode)` (P3) | Mutate one `sleeve_rules` row's `mode` (arm/disarm transitions) — mirrors `update_sleeve_status`'s contract; writes unconditionally, ceremony gating (AC-13/AC-14/AC-12) is the caller's job. |
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

## Notes from the P2 review cycle

`s2-review` found two BLOCK-level gaps in the initial P2 rule-engine GREEN (`083526f`), encoded as RED (`07c07ca`, fixture fix `df47c84`) and fixed in `ef34848`:

- **BLOCK** (`actions.py`) — the armed path placed real broker orders with zero cash-safety enforcement and no durable reservation row: a `buy` never checked available cash before sizing/clamping an order, and no order-placing action recorded the P1 invariant #6 reservation sequence. Fixed: `sleeves.ledger.reconstruct_from_history` + `ledger.reserve()` now gate every `buy` in both shadow and armed mode (see [architecture invariant 9](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy)); `_place_order_with_reservation` centralizes the `RESERVED`-row-before-broker-call sequence for every order-placing action.
- **BLOCK** (`schema.py`) — `market_hours_only=False` was an unrestricted override, letting an order-placing rule bypass market-hours gating entirely (drift from the plan's v1 market-hours-only-evaluation posture). Fixed: the override is now schema-rejected unless the rule's entire `then` action set is pure-`notify`.

s2-review's re-verdict at `ef34848` was **APPROVE** (both findings closed), independently confirmed: 449 passed / 0 failed / 0 skipped across `tests/sleeves/` (363) plus all 5 P1 containment canary files (`test_m2_no_order_path.py`, `test_dashboard_no_order_path.py`, `test_orphan_port_modules_removed.py`, `test_port_dispatch_removal.py`, `test_port_engine_module_removal.py`), ruff clean.

One non-blocking, tracked follow-up came with that approval: fill-polling wasn't wired in P2 (AC-9/P3 scope), so a successfully-placed order's cash reservation wouldn't naturally release until P3 landed — safe direction throughout (over-reserves, never under-reserves). **Resolved in P3**: `sleeves/tick_orchestrator.py`'s `poll_and_apply_fills` (commit `d7c8bb5`) now polls and records real fills every tick. See `DECISIONS.md` `DE-SLEEVES-P3-001`, [`reconstruct_from_history`](#reconstruct_from_historycapital_usd-float-order_history-listdict---ledgerstate) above, and [Managed Sleeves P3: engine wiring and surfaces](#managed-sleeves-p3-engine-wiring-and-surfaces) below for the full mechanism.

## Internal Dependencies

- `sleeves/alpaca_orders.py` — `requests` only (stdlib `os`, `time`, `dataclasses`, `urllib.parse.quote`). No internal AlphaBot imports.
- `sleeves/reconciliation.py`, `sleeves/envelope.py`, `sleeves/sizing.py`, `sleeves/ledger.py` — stdlib only (`dataclasses`, `math`). No imports of each other or of `sleeves/alpaca_orders.py` — each is independently unit-testable.
- `database.py` — owns all `sleeve_*` table I/O. Not imported by any P1 module directly; P2's `sleeves/rules/limits.py` and `sleeves/rules/runner.py` both import it (the P1 accessors plus the new P2 `sleeve_rule_fires` accessors).
- `sleeves/rules/schema.py`, `sleeves/rules/senses.py` — stdlib only (`json`/`re`/`dataclasses`, `math`/`re`/`dataclasses`/`datetime` respectively). No internal AlphaBot imports; `senses.py` never imports `requests` (AST-verified, [invariant](#architecture-invariants-binding-enforced-by-testssleevestest_containment_invariantspy) for FRED cache-only reads).
- `sleeves/rules/conditions.py` — imports `sleeves.rules.senses` (for the `SenseResult` type only; does not call its functions).
- `sleeves/rules/limits.py` — imports `database` (P1's `get_sleeve_runtime`/`set_sleeve_runtime`) plus stdlib (`uuid`, `datetime`, `zoneinfo`).
- `sleeves/rules/actions.py` — imports `sleeves.alpaca_orders`, `sleeves.envelope`, `sleeves.sizing` (the P1 modules it structurally routes every order through) plus `requests` directly (for the `notify` action's Discord webhook POST — distinct from `sleeves/alpaca_orders.py`'s own `requests` usage, and not a broker call).
- `sleeves/rules/runner.py` — the P2 orchestrator: imports `database`, `market_calendar`, and all five sibling `sleeves.rules` modules (`actions`, `conditions`, `limits`, `schema`, `senses`). The first module in the package to tie the whole rule engine together.
- **Resolved in P3:** both `alpha_bot_execution.py` and `app.py` now import sleeves modules in production. `alpha_bot_execution.py` lazy-imports `sleeves.tick_orchestrator` inside `main()` (CC-2 convention, matching `ai_advisor.py`'s precedent), invoked strictly after the exit machine's own execution queue and before `database.save_state`. `app.py`'s `/api/sleeves*` routes lazily import `sleeves.rules.schema`, `sleeves.rules.conditions`/`senses`/`runner` (via `_collect_sense_keys`), and `sleeves.envelope` inside their own route functions. `sleeves.rules.runner` is no longer reachable only from the test suite — see [Managed Sleeves P3: engine wiring and surfaces](#managed-sleeves-p3-engine-wiring-and-surfaces) above.
