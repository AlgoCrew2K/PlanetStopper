# advisors/ticker_health

> The pure half of the nightly dead-ticker check: the tree walk, the Alpaca verdicts, the carry-forward of stored warnings, and the plain text the dashboard and Discord show.

**Source:** `advisors/ticker_health.py`
**Last updated:** 2026-11-17

## Overview

The module does no I/O. `advisors/ticker_health_runner.py` reads Composer and Alpaca and stores the records this module judges and renders. Nothing here reaches an order, a trade, a Composer write or `LIVE_EXECUTION` (a no-trade scan in `tests/security/test_ticker_health_no_trade_boundary.py` pins this). A warning means "this live symphony's current tree names a ticker Alpaca says cannot be traded"; fixing the tree stays the operator's action in Composer.

Identifier key spaces: `symphony_id` is the Composer hash id (the key a warning, a carry-forward and an announcement use); `display_name` is only the label shown. The two never join, so a rename does not make a new warning.

Stored times are ISO 8601 text with a zone; every time shown is rendered by `analytics.format_et_stamp` (`YYYY-MM-DD HH:MM ET`).

## API Reference

### Constants
| Name | Meaning |
|------|---------|
| `RUN_ROLE = "TICKER_HEALTH_RUN"` | `advisor_observations` role of one stored run row |
| `ANNOUNCEMENT_ROLE = "TICKER_HEALTH_ANNOUNCEMENT"` | role of the once-only Discord announcement rows |
| `LEAD_SENTENCE` | the warning's lead sentence, also the first line of every Discord message |
| `STALE_AFTER` | 36 hours: a completed run older than this makes the stale notice show. The one authority for the limit; the notice's hour count is derived from it |
| `DISCORD_CONTENT_LIMIT` | 2000 characters, Discord's documented limit on a message's `content` |

### `tree_ticker_uses(tree) → dict[str, TickerUse]`
Walks a Composer `/score` tree iteratively and returns, per ticker, `TickerUse(asset_nodes, conditions)`: the asset nodes that buy it and the condition nodes that compare it. A condition-only ticker has no asset node, so the two counts stay separate. The placeholder asset ticker `%` is skipped. Condition operands come from the nested `condition` block through `symphony_schema.extract_tickers`, and from the legacy `lhs-val` and `rhs-val` operands (a `rhs-val` string counts unless `rhs-fixed-value?` is true) that `extract_tickers` does not read. An else condition contributes nothing. Its results for `extract_tickers`'s other callers are unchanged.

### `is_plain_ticker(text) → bool`
True for a string of the form `A-Z` then `A-Z0-9`, with one optional `.` segment. A ticker of any other form is reported not checked ("ticker form not checked") and is never put in an Alpaca request path.

### `asset_class_covered(tree) → bool`
False only when the tree carries a top-level `asset_class` other than `EQUITIES`. An absent key is covered.

### `classify_asset_answer(status, body) → TickerVerdict` / `classify_asset_failure() → TickerVerdict`
`TickerVerdict(state, reason)`, state one of `healthy`, `dead`, `not_checked`. The HTTP status decides first, the body only on a 200.

| Alpaca answer | Verdict |
|---------------|---------|
| 200, `status` `active`, `tradable` true | `healthy` |
| 200, `inactive` and not tradable | `dead`, "inactive and not tradable at Alpaca" |
| 200, `active` but not tradable | `dead`, "not tradable at Alpaca" |
| 200, `inactive` but tradable | `dead`, "inactive at Alpaca" |
| 200 with an unreadable body (missing field, `status` not `active`/`inactive`, `tradable` not a bool) | `not_checked`, "Alpaca response unreadable" |
| 404 | `dead`, "not listed at Alpaca" |
| 401, 403 | `not_checked`, "Alpaca refused the credentials" |
| 429 | `not_checked`, "Alpaca rate limit" |
| 5xx | `not_checked`, "Alpaca unavailable" |
| any other status | `not_checked`, "Alpaca rejected the request" |
| transport error (`classify_asset_failure`) | `not_checked`, "Alpaca unreachable" |

A not-checked ticker is never read as healthy and never clears a stored warning.

### `display_name(entry) → str` and `held_state(holdings_fields, ticker) → str`
`display_name` is the roster entry's stripped name, else its id. `held_state` reads the roster's `holdings` field of every account that lists the symphony: `held` on any match of `ticker` or `working_ticker`, `unknown` when any field or entry cannot be read, else `not_held`. "Held now" comes from the roster read of the same run, never from the engine's stored holdings, which freeze while a symphony is triggered.

### `carry_warnings(previous_warnings, judged, unjudged_symphonies, listed_ids, every_roster_read, ended_at) → list[dict]`
Builds the warnings of the run being stored. A pair judged dead is fresh: `found_at` is this run's `ended_at`, `announced` carries over from the previous record. A pair judged healthy is dropped (the warning clears). A pair not judged this run (symphony or ticker not checked) is carried unchanged with its earlier `found_at`. A symphony missing from every roster is dropped only when every configured account's roster was read. Warning fields: `symphony_id`, `display_name`, `ticker`, `reason`, `asset_nodes`, `conditions`, `held`, `found_at`, `announced`.

### `is_completed(rosters_read, symphonies_listed, symphonies_reached, tickers_asked, tickers_answered) → bool`
A run completes when at least one roster was read, every listed symphony was reached (judged or reported not checked with its cause), and, if any ticker needed Alpaca, at least one answer came back. A run with no ticker to ask completes. Checked times and staleness count completed runs only.

### `page_view(latest, latest_completed, now_utc) → dict`
Returns `{"warning": None | {"checked", "lines"}, "notices": [str]}`; the template adds `warning["lead"]`. Each check time is printed at most once across the warning and the notices:

- The warning lead ends "Checked {L}.", L the newest completed check's time. A line carries `from_check` (shown as "(from the check at {t})") only when its own `found_at` time differs from L. Lines carried from the same earlier check each keep their own suffix, so t may repeat across those suffixes only.
- Latest run unavailable or skipped: one notice "Ticker check unavailable: {reason}."; failed: "Ticker check failed on {F}.". Either ends with " Last completed check {L}." only when no warning is shown, or " No completed check yet." when no check has completed.
- Latest run completed: one "Not checked: ..." notice per account, symphony and ticker the run could not judge (several unanswered tickers of one symphony and reason are one counted line).
- Stale notice, only beside a completed latest run older than `STALE_AFTER`: "No ticker check has completed since {L}." with no warning shown, "No ticker check has completed in the last {H} hours." with one shown. Never shown beside an unavailable, skipped or failed-run notice.
- No stored run at all: the single notice "No ticker check has completed yet."

All text is plain; the template escapes it.

### `discord_literal(text) → str` and `discord_messages(pairs) → list[str]`
`discord_literal` puts one backslash before each Discord markdown, link or mention character. `discord_messages` returns one or more messages, each the lead sentence then one line per pair in the order given, each message within `DISCORD_CONTENT_LIMIT`. A line reads `{name} ({symphony_id}): {ticker} is {reason}. Used by N asset nodes and N conditions. {Held now.|Not held now.|Holding unknown.}`. Every Unicode line boundary in a name becomes one space, so a name cannot add a line, and an over-long name is cut (before escaping) and ends in `...`.

## Types
- `TickerUse(asset_nodes: int, conditions: int)`, frozen dataclass.
- `TickerVerdict(state: str, reason: str | None = None)`, frozen dataclass.

## Internal Dependencies
- `advisors.symphony_schema` (`extract_tickers`)
- `analytics` (`format_et_stamp`)
