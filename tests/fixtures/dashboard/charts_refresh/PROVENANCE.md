# Dashboard charts fixtures: provenance

No captured body is stored in this directory. `tests/dashboard/_index_js_harness.py`
builds each "world" at test time: it writes a throwaway SQLite database through the
production writers and records what the production producers return for it, through
the Flask test client. A parser bug therefore cannot pass its own test: no fixture
was shaped to match `static/index.js`.

## Records and where each comes from

| Record | Case it covers | Source |
|---|---|---|
| three `bot_state` entries (`lW4ZzWuqR8tEO2DhXbil`, `qF5ZU7ALjrlhxrGEwsyJ`, `iaSOOUsmnCJHiZvbrWfs`) | a Take-Profit exit at 09:43, a VWAP exit at 10:11, an untriggered symphony | verbatim records of `tests/fixtures/prod_droplet/bot_state_positions.json` (captured from the droplet by `capture_from_droplet.py`), written with `database.save_state` |
| `GET /` HTML, `GET /api/hero-chart/<token>` (seven tokens), `GET /api/chart/<id>`, `GET /api/state` | the page, each window, each card's intraday tape, the poll | producer output of the real routes over the seeded database (`get_state` with the market state forced `open`, the env file and credentials unset) |
| daily and intraday `shadow_history` rows | N daily points before today (160 in the default world), today's tape 09:30 to 10:20 | `database.record_shadow_observation`; returns are a deterministic sine formula, synthetic and labelled so; the triggered symphonies' shadow (bot) return freezes from 70 percent of the history on, so bot and held diverge |
| today's chart history | the engine's per-cycle point shape | `database.save_chart_history` with points carrying the keys `alpha_bot_execution.py` appends (`time`, `return`, `stop`, `event`, `mc_prob`, `vol`, `vwap_diff`, `base_atr_pct`, `dynamic_multiplier`, `breakeven`, `vwap`) |
| world `default` | full history, all cards with intraday data, plus a "next" capture (one more day and one more tape point written through the same writers) | producer output |
| world `mixed` | one card with no intraday rows (empty `/api/chart` data) | producer output |
| world `empty` | fewer than two trading days: no portfolio history | producer output |
| world `onepoint` | two trading days 100 days apart, so the 30d window holds exactly one point | producer output |
| world `ytd` | rows 29, 30, 31 December and 2 January; the hero-chart routes are called with `datetime.datetime.now` frozen at 2027-01-02 15:00 UTC | producer output under a frozen clock |
| world `hostile` | a stored exit reason that closes a script element | the VWAP record with one named field edited (`triggered_reason`) |
| colour tokens | light and dark values | parsed from `static/tokens.css` at test time, never typed |

## Derived records (each changes only the named field of a real record)

PM sign-off: given in the PM's message to this cycle's RED author (2026-10-22), which
approved the derived records the approach lists; recorded here as the sign-off.

| Derivation | Test | Edit |
|---|---|---|
| `/api/state` without a hero series | S-3.1, S-3.3 | `meta.portfolio.hist_dates`, `hist_bot`, `hist_held` set to `[]`, removed, or `null`; and `meta.portfolio` removed |
| `/api/chart/<untriggered id>` and its `/api/state` entry with a chosen reading | S-5.6 dial arc | the last data point's `mc_prob` and the `symphonies` entry's `mc_prob` set to 0, 14.99, 15, 50, 80, 80.5 and 100 |
| an empty hero-chart body | S-3.1 family | the body `get_hero_chart` returns on its own exception path (keys `hist_dates`, `hist_bot`, `hist_held` empty, `insufficient_history` true, `available_days` 0), as written in `app.py` |
| a failed sparkline response | S-1.5, S-3.2 family | status 500 with the route's own error envelope `{"status": "error", "message": "An internal error occurred"}`; a refused request; an unreadable body |

The `current_return` variants of the card verdict (cycle C2) are not used here.
