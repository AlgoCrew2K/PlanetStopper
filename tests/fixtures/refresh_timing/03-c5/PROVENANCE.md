# 03-c5 fixtures: provenance

Plan: dashboard-refresh, cycle 5 (elements fixed at render follow the poll).

This folder holds no record files. Bodies are producer output generated at test time; a derived body changes one named field of one.

## Producer worlds

| World | Producer calls | Case it covers | Scenarios |
|---|---|---|---|
| `cards-panels` (first) | `open_cards_world`, then the real writers: `database.create_sleeve`, `create_sleeve_rule`, `update_sleeve_status`, `insert_sleeve_rule_fire` (fired 14:30 UTC), `record_cvar_diagnostic`, `save_symphony_strategy`; `GET /`, `GET /api/state` | a page with two sleeves (three rules), one fire today, a diagnostic and one locked variable | S-6.6, S-6.7, S-6.9 to S-6.12 |
| `cards-panels` (later, `world["next"]`) | the same database after one more fire (14:40 UTC), a rule added, a rule deleted by a direct delete on the world's own database file (no accessor deletes a rule), a sleeve added and another deleted with `database.delete_sleeve`, a newer diagnostic (the older row's `ts_utc` set back so the newer wins), two more locked variables; `GET /`, `GET /api/state` | the data after a cycle; the server render of the same data for the cross-render comparison | S-6.6, S-6.7, S-6.11, S-6.12 |
| `app` state worlds | `tests/app/_refresh_timing_state_world.seeded_world` for `open`, `closed_frozen` and `pre_market`, sleeves and diagnostics through the same writers | `dashboard_panels` and each card's `mdd_lifetime` on every branch | S-6.6 to S-6.9, S-6.12 |
| `default`, `cards-open` | the harness worlds | the page the derived bodies below meet | S-6.1 to S-6.5, S-6.8 |

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `_with_meta(state, mode="LIVE")` | `default` poll | `meta.mode` set to `LIVE` | a producer body with `LIVE_EXECUTION` true needs the developer env file the tests must not read; one field of a real body stands in | S-6.1 | requested |
| `_with_strip_legs` | `default` poll | `portfolio_strip.today_change`, `cumulative_return` or `max_drawdown` `dry_run` and `if_held` set per case (greater, smaller, equal, missing, both missing) | the plan's family S-6.5 | S-6.2, S-6.4, S-6.5 | requested |
| `_with_portfolio_meta(state, insufficient_history=...)` | `default` poll | `meta.portfolio.insufficient_history` set to true or false | S-6.4 | S-6.4 | requested |
| `strip_with(strip, vol_bot=..., vol_held=...)` | `default` `GET /api/strip/30d` | the two vol fields set per case | the Ann. Vol rule's family | S-6.3, S-6.5 | requested |
| `sup.with_card_only(state, id, mdd_lifetime=...)` | `default` poll | one card's `mdd_lifetime` set to 12.34, -12.34, 0.0 or null; 5.0 then 9.0 | the card field the poll carries | S-6.8 | requested |
| `_with_panels(state, ...)` | `cards-open` or `cards-panels` poll | `dashboard_panels` replaced by a part list in the shape the plan states (`sleeves`, `cvar` with `ok` and `diagnostic`, `vars_locked_count`); failure shapes (`sleeves` null, `cvar.ok` false, count null) | a read that failed cannot be produced by a healthy database | S-6.7, S-6.9 | requested |
| hostile name | `cards-panels` later poll | the first sleeve's and its first rule's `name` set to `<img src=x onerror=alert(1)>` | markup in a name | S-6.12 | requested |

The rule table in `test_dashboard_render_follow.py` (`_wins`) restates the server render's rules from the template for the derived leg cases (a missing Today, Cumulative or Max DD leg reads as 0, as the template does; Ann. Vol marks no side without both legs).
