# Approval packet: Dashboard refresh: every element of `/` follows every refresh, in every market state, and recovers by itself

## What is being built

The operator keeps `/` open through a whole trading day and every number, label, card, panel and status on it follows the newest data, keeps the window and symphony he picked, never draws an error as data, never goes quiet after a restart or a sleep, and says how old its data is.
- The headline and Ann. Vol keep the picked window (AC-1).
- An error, refused or timed-out poll keeps the last values with a cue; a 401 goes to login (AC-2).
- The event stream re-opens after it closes, and the $-saved panel refreshes without it (AC-3).
- An age-based stale cue, a catch-up on return, wake and network return, and a true countdown (AC-4).
- After the close and before the open, cards, verdicts and badges match the poll (AC-5).
- Render-only elements follow the poll: mode pill, hero legend, bar classes, card labels, Sleeves and CVaR panels (AC-6).
- Cash Now is disabled on a card that exits (AC-7).
- The detail panel follows polls and shows only its own symphony (AC-8).
- Sparklines and dials show only the current session's readings (AC-9).
- The stored theme applies before first paint and across tabs (AC-10).

## Acceptance criteria and scenarios

### AC-1: The headline and Ann. Vol keep the picked window
Rule: When any refresh lands while a hero window is selected (30d when none was picked), the hero shall keep its previous series with a visible no-data-yet cue whenever that window has no data, and the guard-alpha headline, its window label and the Ann. Vol row shall show that window's values from `/api/strip/{window}`, never the poll's default-window values, and a response for an earlier selection that lands after a later one shall be discarded.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | 1Y is picked and the headline shows `/api/strip/1y`'s guard alpha G1 | a 30 s poll carrying `portfolio_strip.guard_alpha` G30, different from G1, lands | `#guard-alpha-headline` reads G1 formatted as at the ref, or the fresh 1Y value; `#guard-alpha-window-label` reads "1Y" |
| S-1.2 | B | as S-1.1 | a cycle event's refresh lands | as S-1.1 |
| S-1.3 | N3 | 1Y is picked | a poll lands whose `portfolio_strip.guard_alpha` is null | the request sent is `/api/strip/1y`, never `/api/strip/30d`; the headline keeps its 1Y value until it answers |
| S-1.4 | N7 | no window has been picked (30d default) | the page renders and three polls land with no new data | the headline reads the same value on the render and after each poll |
| S-1.5 | B | 1Y is picked and the vol row shows `/api/strip/1y`'s `vol_bot` and `vol_held` | a poll lands | the row still shows the 1Y values |
| S-1.6 | N3 | 30d is picked and `/api/strip/30d` carries `vol_bot` null | the click and three polls land | the row reads the dashes each time, never a lifetime value under 30d |
| S-1.7 | N5 | the operator clicks 1Y then 30d before either strip response lands; 1Y's lands last | both land | the headline, label and vol row show 30d |
| S-1.8 | N6 | 1Y is picked | the refresh's `/api/strip/1y` request fails | the headline, label and vol row keep their last 1Y values |
| S-1.9 | N7 | any window | a refresh lands | the hero chart (ux-overhaul 05 AC-4), the Today, Cumulative and Max DD rows and the $-saved panel's window are as before this plan |
| S-1.10 | N1 | All Time is picked | a poll lands | the headline reads `/api/strip/all`'s value |
| S-1.11 | B | 1Y is picked | a refresh lands whose data differs from the last one (a new data stamp) | exactly one `/api/strip/1y` request is sent |
| S-1.12 | N5 | 1Y is picked | a refresh lands whose data stamp equals the last one's | no `/api/strip/1y` request is sent |
| S-1.13 | B | no window was picked | the page renders and the first poll lands | the vol row shows `/api/strip/30d`'s `vol_bot` and `vol_held`, or the dashes when either is null, never the poll's lifetime `vol_bot` beside a null `vol_held` |
| S-1.14 | N3 | the poll's series has no row inside the picked window (a refused `/api/hero-chart` meeting a stale poll series, or YTD on 1 January) | the window is picked or a refresh lands | the hero keeps its previous series, the label reads the picked window, and a visible cue under it reads "No data yet for YTD" (the picked window's label); the cue clears when data for the window arrives |

Negative coverage: N1 S-1.10 | N2 n/a: the strip route refuses only unknown tokens, which the picker never sends | N3 S-1.3, S-1.6, S-1.14 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-1.7, S-1.12 | N6 S-1.8 | N7 S-1.4, S-1.9 | N8 n/a: a window pick holds no edit state

### AC-2: An error, refused or timed-out poll keeps the last values with a cue; a 401 goes to login
Rule: If an `/api/state` response is not a 2xx JSON data body (a 401, 403, 500 or 503, an HTML body, a JSON body without the data fields, a network failure or no answer within 20 s), then `/` shall keep every value it shows, show the connection-lost cue with the last data stamp, retry at the next poll, and on a 401 navigate to `/login`; and the page's refresh counter (`body[data-refresh-count]`) shall count each newest request's handled outcome, a rendered body or a handled failure, and never a superseded request.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | N6 | a healthy poll was rendered | a poll answers 500 `{"status": "error", "message": "An internal error occurred"}` | the tracked, armed and triggered counts, both section badges, the hero points, the vol row and every card keep their values; the header's engine label reads "Connection Lost" (overview SD-13) and `#hero-data-as-of` reads "connection lost | data as of" followed by the last good stamp |
| S-2.2 | N4 | a healthy poll was rendered | a poll answers 401 `{"error": "unauthenticated"}` | the page navigates to `/login` |
| S-2.3 | N6 | as S-2.1 | a poll answers 503 `{"error": "misconfigured"}` | as S-2.1 |
| S-2.4 | N2 | as S-2.1 | a poll answers 403 with an HTML body | as S-2.1 |
| S-2.5 | N6 | as S-2.1 | the request fails at the network | as S-2.1 |
| S-2.6 | N6 | as S-2.1 | no answer arrives within 20 s | the request is abandoned and the page shows the S-2.1 state; a late answer to it is discarded |
| S-2.7 | N1 | as S-2.1 | a 200 answers with a body that is not JSON | as S-2.1 |
| S-2.8 | B | the page shows the S-2.1 state | the next poll answers with data | every value updates and the cue clears |
| S-2.9 | N3 | a fresh deploy with no state | a poll answers the waiting body with its `notice` | it renders as at the ref; it is data, not an error |
| S-2.10 | N5 | a newer poll succeeded | an older poll's error lands after it | the error is ignored and no cue shows |
| S-2.11 | B | the counter reads N | the newest poll answers with data and renders | the counter reads N+1 |
| S-2.12 | N6 | the counter reads N | the newest poll fails and the failure is handled (the S-2.1 state) | the counter reads N+1 |
| S-2.13 | N5 | the counter reads N and a newer request has been issued | the older request's body or failure lands | the counter still reads N |
| S-2.14 | N6 | the page is open | the operator clicks Force run now and its state refresh answers 500 | the page shows the S-2.1 state |
| S-2.15 | N6 | the server render has drawn the page | the first page-load state request fails | the server-rendered values stay with the S-2.1 cue |

Negative coverage: N1 S-2.7 | N2 S-2.4 | N3 S-2.9 | N4 S-2.2 | N5 S-2.10, S-2.13 | N6 S-2.1, S-2.3, S-2.5, S-2.6, S-2.12, S-2.14, S-2.15 | N7 n/a: the healthy path is unchanged and covered by S-2.8 | N8 n/a: no edit state

### AC-3: The event stream re-opens after it closes, and the $-saved panel refreshes without it
Rule: When the event stream closes or reports an error, `/` shall open a new stream within 30 s and keep retrying, at most 60 s apart, while it fails, with at most one stream open; while no stream is open each 30 s poll shall also refresh the $-saved panel for the picked window; and while a stream is open the panel shall refresh on each event whose `changed` is true.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the page's stream is open | a reconnect is answered 502, so the stream is closed | within 30 s the page opens a new `EventSource` to `/api/events` |
| S-3.2 | N6 | every reconnect fails for 5 minutes | the attempts run | the gap between attempts grows and never exceeds 60 s, and at most one stream is ever open |
| S-3.3 | B | no stream is open | a 30 s poll lands | one `GET /api/guard-alpha-summary?window=` with the picked window is sent |
| S-3.4 | B | a stream is open | an event with `changed` true arrives, then one with `changed` false | the panel refreshes once, for the first |
| S-3.5 | N4 | the reconnect is answered 401 | the stream closes | the page navigates to `/login` |
| S-3.6 | B | the daemon restarts with the page open | the new daemon serves | events resume within 90 s of it serving, and the header and values recover with no reload |
| S-3.7 | N5 | two tabs on `/` | one tab's stream closes | only that tab re-opens a stream; the other's is untouched |
| S-3.8 | N6 | a $-saved request fails | the failure lands | the panel keeps its last values |
| S-3.9 | N5 | the operator picks 30d while a 1Y $-saved request is in flight | both land, 1Y last | the panel shows 30d |
| S-3.10 | N7 | the stream is open and healthy | an hour passes | the page opens no second stream, and the 30 s poll interval stays |

Negative coverage: N1 n/a: the stream takes no input | N2 S-3.5 | N3 n/a: an empty panel response is the existing honest empty state | N4 S-3.5 | N5 S-3.7, S-3.9 | N6 S-3.2, S-3.8 | N7 S-3.10 | N8 n/a: no edit state

### AC-4: An age-based stale cue, a catch-up refresh on return, and a true countdown
Rule: When more than 90 s have passed since `/` last received data, it shall show the not-updating cue; when the tab becomes visible, the page is restored from the back-forward cache or the browser reports it is online again, it shall send exactly one `/api/state` request at once; the NEXT countdown shall show the true seconds to the next `:00` within 1 s, in the server render's format; the header's "Stale" check shall judge the last cycle's age as the server reckons it, never by the browser clock; and the header label shall follow overview SD-13's precedence.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | the last data arrived 95 s ago | the cue check runs | the header label reads "Not updating" with the tooltip "No data received for 1 min."; `#hero-data-as-of` keeps its stamp and adds " | not updated for 1 min" |
| S-4.2 | B | the tab was hidden for 10 minutes | it becomes visible | exactly one `/api/state` request is sent at once, and the stream is re-opened if it closed (AC-3) |
| S-4.3 | B | the laptop slept for 5 minutes | it wakes and the browser fires `online` or the tab's visibility returns | exactly one request at once |
| S-4.4 | B | the page is restored from the back-forward cache | `pageshow` fires with `persisted` | exactly one request at once |
| S-4.5 | N5 | `visibilitychange` and `online` fire within 1 s | both fire | one request is sent |
| S-4.6 | N7 | a visible tab with data 20 s old | 30 s pass | requests are only the poll's and the events', as before |
| S-4.7 | B | `/api/state` took 4 s | the countdown resyncs | it equals the seconds to the next `:00` within 1 s |
| S-4.8 | B | the server render shows "NEXT 00:42" | the first poll resyncs | the text keeps that form, for example "NEXT 00:38", never "NEXT: 0:38" |
| S-4.9 | N1 | the countdown reaches `:00` | the second passes | it continues from the next minute's count and never sits at "NEXT 00:00" for more than 2 s |
| S-4.10 | N1 | the browser clock is 3 minutes fast | the cue check and the countdown run | the cue appears only when no data arrived for 90 s by the page's own receipt times, and the countdown stays true |
| S-4.11 | N7 | the market is closed | the header renders | the countdown hides as at the ref |
| S-4.12 | N1 | the browser clock is 3 minutes fast and the last cycle completed 30 s ago by the server's clock | a poll lands | the header reads "Live", never "Stale" |
| S-4.13 | N6 | the last data arrived 95 s ago and the current poll fails | the failure lands | the header reads "Connection Lost" (overview SD-13) |
| S-4.14 | B | 15:56 ET in the blackout, and no data for 95 s | the cue check runs | the header reads "Not updating", which outranks "Rebalance pause" (overview SD-13) |
| S-4.15 | N6 | the market is open, data is fresh, and `/api/state`'s `degraded_reasons` reads ["scheduler_stalled"] | a poll lands | the header reads "Degraded" and its tooltip reads "Scheduler stalled: no engine spawn for 2 minutes or more." (the suite approach RC-1's fixed sentence), never the raw key; an unknown reason reads "The daemon reports a degraded state."; with `degraded_reasons` empty the label follows the lower ranks of overview SD-13 |

Negative coverage: N1 S-4.9, S-4.10, S-4.12 | N2 n/a: nothing is refused | N3 n/a: the cue shows exactly when no data arrived | N4 n/a: client-only over authenticated data | N5 S-4.5 | N6 S-4.13, S-4.15 | N7 S-4.6, S-4.11 | N8 n/a: no edit state

### AC-5: After the close and before the open, cards, verdicts and badges match the poll
Rule: When a poll lands in `closed_frozen` or `pre_market`, `/` shall update every card's status pill, values, section and verdict from the response's `symphonies` list and `guard_alpha` (plan 01 AC-6), and each section badge shall equal the number of cards in its section; a `guard_alpha` that is absent, null, not finite, or 1e21 or more in magnitude (the server render's bound) shall show the dash.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the page is open across 16:00 with 6 triggered cards | the first frozen poll lands | each triggered card's verdict reads the verdict for its frozen `guard_alpha`, never the dash |
| S-5.2 | B | the page is loaded after the close | three polls land | each triggered card's verdict text is identical on the render and after each poll |
| S-5.3 | B | the page is loaded after the close | the first poll lands | the Active and Standby badges equal the numbers of cards in those sections |
| S-5.4 | B | 08:00 ET | the first poll lands | each card's status pill and Today, Cumulative and Max DD values equal the poll's `symphonies` entry |
| S-5.5 | N3 | a triggered entry with no `guard_alpha` | a poll lands | its verdict reads the dash |
| S-5.6 | N7 | an account is picked after hours | polls land | other accounts' cards stay hidden (ux-overhaul 05 S-6.8) |
| S-5.7 | N5 | a card's status changes on a poll | it lands | the card moves into its new section at the place the server render's sort order gives it |
| S-5.8 | N6 | a frozen poll fails | it lands | the cards keep their values (AC-2) |
| S-5.9 | N1 | a triggered entry whose `guard_alpha` is 1e21, -1e21 or 1e30 | a poll lands | its verdict reads the dash, as the server render does for the same value |
| S-5.10 | N3 | a triggered entry whose `guard_alpha` key is absent, or null | a poll lands | its verdict reads the dash (this reverses ux-overhaul 05 C2's write-nothing, see Earlier criteria) |

Negative coverage: N1 S-5.9 | N2 n/a: display only | N3 S-5.5, S-5.10 | N4 n/a: `/` is behind the global auth hook, unchanged | N5 S-5.7 | N6 S-5.8 | N7 S-5.6 | N8 n/a: no edit state

### AC-6: Elements fixed at render follow the poll
Rule: When a poll lands, `/` shall update the mode pill and the hero legend's live or dry-run word from `meta.mode`, each comparison row's winner and loser classes by the rule the server render applies to that row, each card's lifetime Max DD, the insufficient-history labels and the edit-vars count, and the Sleeves and CVaR panels, from data the poll fetches.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | two tabs on `/` showing DRY RUN | `LIVE_EXECUTION` becomes true and a poll lands in the other tab | that tab's mode pill reads LIVE with its live state and the legend reads "Planet Stopper (live)" |
| S-6.2 | B | the Today row rendered with Bot ahead | a poll makes Held ahead | `winner` is on the held bar and label and `loser` on the bot bar |
| S-6.3 | B | the Ann. Vol row | a poll makes bot's vol lower than held's | bot's bar carries `winner`, as the render's inverted rule gives |
| S-6.4 | N3 | the Max DD row with insufficient history | a poll lands | neither bar carries `winner` or `loser` |
| S-6.5 | N1 | bot equals held on the Today row | a poll lands | the classes equal the server render's for equal values (`templates/index.html:923`) |
| S-6.6 | B | a sleeve rule fires while `/` is open | the next poll lands | that rule's "today" fire count has increased |
| S-6.7 | B | the CVaR diagnostic changes in a cycle | the next poll lands | the CVaR panel shows the new values |
| S-6.8 | B | a card's lifetime Max DD changes | the next poll lands | `[data-field="mdd-lifetime"]` shows it |
| S-6.9 | N6 | the sleeves read fails | a poll lands | the panel keeps its last values |
| S-6.10 | N7 | the Sleeves panel's arm and disarm buttons | the operator uses them | they behave and reload as at the ref |
| S-6.11 | N5 | the Sleeves and CVaR data for two polls land out of order | both land | the older is discarded (overview SD-6) |
| S-6.12 | B | a sleeve rule is added and another removed while `/` is open | the next poll lands | the added rule's row appears and the removed rule's row disappears, without a reload |

Negative coverage: N1 S-6.5 | N2 n/a: display only | N3 S-6.4 | N4 n/a: client-only over authenticated data | N5 S-6.11 | N6 S-6.9 | N7 S-6.10 | N8 n/a: no edit state

### AC-7: Cash Now is disabled on a card that exits
Rule: When a poll reports a symphony triggered, its Cash Now button shall be disabled, and when a poll reports it untriggered, enabled, except while a Cash Now request for it is in flight, and except after one has succeeded in this page load, when the button stays disabled reading "In cash" until a reload; the request itself stays as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | an untriggered card with Cash Now enabled | a poll reports it triggered | its Cash Now button is disabled |
| S-7.2 | B | a triggered card | the next session's first poll reports it untriggered | its Cash Now button is enabled and reads "Cash Now" |
| S-7.3 | N8 | a Cash Now request for the card is in flight | a poll lands | the button stays disabled and reads "Selling..." until the request answers |
| S-7.4 | N7 | any click | the request is sent | its URL, method, headers and body equal the ref's (`static/index.js:409-413`) |
| S-7.5 | N5 | the operator double-clicks | both clicks land | one request is sent, as at the ref |
| S-7.6 | N5 | a Cash Now request for the card succeeded and the button reads "In cash" | a poll reports the symphony untriggered | the button stays disabled and reads "In cash", so no second sell is one click away |

Negative coverage: N1 n/a: no input | N2 n/a: refusal display is plan 04 AC-2 | N3 n/a: a card always has a triggered state | N4 n/a: the route's auth and CSRF hooks are unchanged | N5 S-7.5, S-7.6 | N6 n/a: a failed poll leaves the button as it was (AC-2) | N7 S-7.4 | N8 S-7.3

### AC-8: The detail panel follows polls and shows only its own symphony
Rule: While the detail panel is open on symphony S, each poll shall rewrite its live return, stop, peak, alpha and risk-math figures from S's newest state, and the panel shall show only S's chart, logs, variables and risk profile, discarding any response for another symphony or older than the newest request for S.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | the panel is open on an untriggered symphony showing -0.70% | a poll carries `current_return` 5.55 | `#dp-live-ret` shows +5.55% within that poll |
| S-8.2 | B | the panel is open | a poll changes the stop and the MC probability | the risk-math rows show the new values |
| S-8.3 | N3 | the panel was open on A | the operator opens B, whose `/api/chart` returns no points | the panel shows B's empty state, never A's chart or exit marker |
| S-8.4 | N5 | the operator opens A then B | A's responses land after B's | the panel shows B's chart, logs, variables and profile |
| S-8.5 | N8 | the panel's chart overlays are toggled | a poll lands | the chart is not rebuilt and the toggles stay (ux-overhaul 05 S-3.8) |
| S-8.6 | N3 | the panel is open on S | a poll no longer carries S (the account pick changed) | the panel keeps its last figures and its sub-label reads "not in the current view" |
| S-8.7 | N3 | the operator opens a card before the first poll has landed | the first poll lands | the panel fills from it |
| S-8.8 | N7 | any open | the panel opens | the title is the display name (ux-overhaul 05 AC-8) |
| S-8.9 | N6 | a poll fails | it lands | the panel keeps its figures |

Negative coverage: N1 n/a: the panel takes a symphony id from a card click only | N2 n/a: display only | N3 S-8.3, S-8.6, S-8.7 | N4 n/a: client-only over authenticated data | N5 S-8.4 | N6 S-8.9 | N7 S-8.8 | N8 S-8.5

### AC-9: Sparklines and dials show only the current session's readings
Rule: When `/` draws a card's sparkline or MC dial, `/api/chart/{symphony_id}` shall key its held overlay to the ET trading date, the sparkline shall carry its session's date whenever that session is not today's, and the dial shall show the poll's current `mc_prob`, or the dash when it is null, never an older tape reading; a refresh whose data stamp is unchanged shall send no `/api/chart` request.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | 20:30 EDT on the UTC host, a symphony with shadow rows for today's ET date | `GET /api/chart/{symphony_id}` | its points carry `held` values |
| S-9.2 | B | 01:00 ET, a tape from the previous session | the sparkline draws | it carries the caption with that session's date, for example "2026-10-21 session" |
| S-9.3 | N3 | an untriggered symphony whose state `mc_prob` is null | the poll and the chart fetch land | the dial reads the dash after both |
| S-9.4 | B | state `mc_prob` 63 and the tape's last non-null reading 58 | both land | the dial reads 63% |
| S-9.5 | N7 | a triggered symphony | the dial renders | it reads the dash (`feature-plans/math-f7.md` AC-2) |
| S-9.6 | N5 | chart responses for two polls land out of order | both land | the older is discarded |
| S-9.7 | N3 | `/api/chart` returns no points | the card renders | "No intraday data yet" as in ux-overhaul 05 S-1.4 |
| S-9.8 | N1 | 09:31 ET, the day's first cycle has written the new tape | the poll lands | the sparkline shows today's tape with no caption |
| S-9.9 | B | 12 cards, and a refresh whose data stamp is new | it lands | one `/api/chart` request per card is sent, and none for a non-symphony key |
| S-9.10 | N5 | a refresh whose data stamp equals the last one's, including the first poll after page load, whose stamp equals the one the page loaded with | it lands | no `/api/chart` request is sent, so the load's chart requests are not repeated |

Negative coverage: N1 S-9.8 | N2 n/a: the chart route refuses nothing | N3 S-9.3, S-9.7 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-9.6, S-9.10 | N6 n/a: a failed chart fetch is ux-overhaul 05 S-1.5 | N7 S-9.5 | N8 n/a: no edit state

### AC-10: The stored theme applies before first paint and across tabs
Rule: When a page that loads `static/tweaks.js` loads with a stored theme, the root element shall carry that theme before the body's first paint, and when the theme changes in one tab, every other open tab of the app shall apply it without a reload.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | a stored dark theme | `/` loads | the root element has `data-theme` "dark" before the body's first paint |
| S-10.2 | B | as S-10.1 | `/history`, `/performance`, `/ai-advisor` and `/settings` load | each root has `data-theme` "dark" before first paint |
| S-10.3 | B | two open tabs | the theme changes to dark in one | the other applies dark without a reload, and its charts recolour (ux-overhaul 05 AC-5) |
| S-10.4 | N3 | no stored theme | a page loads | the default theme applies, as at the ref |
| S-10.5 | N1 | a stored value that is not valid JSON | a page loads | the default theme applies and no error stops the page |
| S-10.6 | N7 | the number-format setting changes in one tab | the other tab's next poll lands | its numbers use the new format |

Negative coverage: N1 S-10.5 | N2 n/a: nothing is refused | N3 S-10.4 | N4 n/a: client-only | N5 n/a: the last change wins, as ux-overhaul 05 S-5.4 pins | N6 n/a: no request | N7 S-10.6 | N8 n/a: the tweaks panel holds no unsaved state

## Rule-by-entry-point matrix

| Rule | 30 s poll | Cycle event | Window picker click | Account pick | Tab return or `online` | Stream re-open |
|---|---|---|---|---|---|---|
| AC-1 picked window kept | S-1.1, S-1.5 | S-1.2 | S-1.7 | n/a: the pick refreshes cards only (ux-overhaul 05 AC-6), and its poll is the 30 s poll's case | n/a: the catch-up request is a poll, covered by S-1.1 | n/a: re-opening fetches no strip |
| AC-2 errors never drawn | S-2.1 | n/a: the event carries no data; its refetch is a poll | n/a: the click fetches no `/api/state` | n/a: its request is a poll | n/a: its request is a poll | n/a: the stream carries no state; its 401 is AC-3's S-3.5 |
| AC-3 panel refresh | S-3.3 | S-3.4 | S-3.9 | n/a: the panel is portfolio-wide | n/a: the catch-up is a poll, covered by S-3.3 | S-3.1 |
| AC-5 frozen cards match | S-5.1 | S-5.1 | n/a: the click changes no card | S-5.6 | n/a: a poll | n/a: no card data |
| AC-8 panel follows polls | S-8.1 | S-8.1 | n/a: the click changes no panel | S-8.6 | n/a: a poll | n/a: no panel data |

| Rule | First page-load state request | Force run button | Detail panel open |
|---|---|---|---|
| AC-1 picked window kept | S-1.4, S-1.13 | n/a: its refresh is a poll, covered by S-1.1 | n/a: opening the panel changes no window |
| AC-2 errors never drawn | S-2.15 | S-2.14 | n/a: the panel reads `/api/chart`, `/api/logs` and `/api/settings`, not `/api/state`; its failures are AC-8's S-8.9 |
| AC-5 frozen cards match | S-5.2 | n/a: its refresh is a poll, covered by S-5.1 | n/a: opening the panel changes no card |
| AC-8 panel follows polls | S-8.7 | n/a: its refresh is a poll, covered by S-8.1 | S-8.3, S-8.4 |

## Not Building

Non-goals:
- NB-1: A new visual design for any cue; every cue uses existing text, the header label and the `.legend-as-of` style (overview NB-2).
- NB-4: Changing the 30 s poll interval (approved by the PM, [PM-ASSUMED]).
- NB-6: Fixing Cash Now's request so it can succeed: it posts `{symphony_id}` to a route that requires `confirm_account_id` (`static/index.js:409-413`, `app.py:5455-5456`); the request may not change in this suite (overview NB-6), so it is a sensitive follow-up plan; plan 04 AC-2 shows the refusal (approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-2: Every trade and liquidation request, Cash Now's included (overview NB-6).
- NB-5: The hero chart's window, the sparklines' in-place updates, the first-paint verdicts and dials, the banners, the account filter and the detail title, as ux-overhaul 05 delivers them.

Deferred:
- NB-3: The newest-request guard on `loadState` (TA-11) stays ux-overhaul 05 C3 round 2's; the other covered findings stay with their plans (overview NB-12).

Protected surfaces consumed, not changed:
- NB-7: `POST /api/sell_account`, the Managed Sleeves arming routes and `LIVE_EXECUTION` (overview NB-14).

## Narrowed mappings

R-1: Every timing issue the refresh-timing audit found is resolved; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10; left out: this plan's part is the findings G-65 assigns it
R-2: No shown value changes, reverts or flickers when no new data arrived; maps to AC-1, AC-5, AC-6; left out: the client writers on `/`
R-3: Every datum the poll carries reaches the screen without a reload; maps to AC-5, AC-6, AC-8; left out: `/`
R-4: Every number, chart, label and status on every page shows the newest data the engine has produced; maps to AC-5, AC-6, AC-8, AC-9; left out: `/`
R-5: Stale or wrong-day data is shown only with a dated, honest cue; maps to AC-4, AC-9; left out: the client's age cue and the sparkline's session date; the server's stamps are plan 01's
R-6: Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss; maps to AC-3, AC-4; left out: `/`
R-7: The suite covers `/` and the GET routes it polls; maps to AC-1, AC-9; left out: `/`, `/api/strip/{window}`, `/api/guard-alpha-summary` and `/api/chart/{symphony_id}`
R-9: A whole trading day on `/` shows correct, current values; maps to AC-4, AC-5, AC-9; left out: the client side
R-10: The operator's selections persist through every refresh, and Cash Now keeps working through the day; maps to AC-1, AC-7, AC-8; left out: the hero window, the open detail panel and Cash Now's enabled state; the account pick is ux-overhaul 05 AC-6, and Force run and Emergency Liquidate are ux-overhaul 04's and plan 04's
R-11: Tabs kept open across a deploy, a restart or a sleep, switched away and back, stay current; maps to AC-3, AC-4; left out: `/`
R-12: Values change within one engine cycle of new data; maps to AC-1, AC-3; left out: `/`
R-13: Every freshness stamp carries a date and its true age; maps to AC-4; left out: the age cue on `/`; the date is plan 01's
R-14: After a restart, live updates, actions and the header recover without a reload; maps to AC-3; left out: the event stream on `/`; the token is plan 04's
R-16: An error, refused, unavailable or timed-out poll response is never drawn as data; the last good values stay with a cue and a retry; a 401 goes to login; maps to AC-2; left out: `/`'s `/api/state` poll; the other pages are plan 04's
R-23: The newest issued request wins on every page, not only `loadState`, and two tabs are independent; maps to AC-1, AC-3, AC-8, AC-9; left out: `/`; `loadState` itself is ux-overhaul 05 C3 round 2's (NB-3)
R-27: An error body is distinguishable from data; maps to AC-2; left out: `/api/state` on `/`
R-37: Server-sent events stay, fixed and not replaced; maps to AC-3; left out: the client's half
R-63: The LIVE / DRY RUN mode pill and the hero legend follow the live flag without a reload; maps to AC-6; left out: on `/`; the other four pages' pill is plan 04 AC-1 (overview SD-9)

## User tasks

Workflow (Dashboard `/`): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Keep `/` open through a trading day
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/` at 08:00 ET | the prior session's snapshot | Cards, badges and rows agree; the as-of names the prior session's date | S-5.4, S-5.3 |
| 2 | Pick 1Y | none | The headline, its label and the Ann. Vol row read 1Y and keep it through every poll and event | S-1.1, S-1.2, S-1.5 |
| 3 | Watch a symphony exit at 11:00 | a trigger | Its card turns triggered and its Cash Now button disables | S-7.1 |
| 4 | Stay past 16:00 | the close | Each triggered card keeps its verdict; badges equal the cards | S-5.1, S-5.3 |

### T-2: Survive a restart, a sleep and a tab switch
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Leave `/` open while the daemon restarts | a 502 window | The event stream re-opens; the $-saved panel keeps refreshing | S-3.1, S-3.3 |
| 2 | Switch tabs for 10 minutes and come back | none | One refresh fires at once; the stale cue shows while data is old | S-4.2, S-4.1 |
| 3 | Sleep the laptop for 5 minutes | none | On wake one refresh fires at once | S-4.3 |

### T-3: Read a symphony's detail
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open a card's detail panel and leave it open | a moving symphony | Its numbers update on each poll | S-8.1 |
| 2 | Open another card quickly | two symphonies | Only the second symphony's chart, logs and profile show | S-8.3, S-8.4 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | How long may `/api/state` take before the page counts it as failed? | AC-2 | 20 s, over three times the slowest measured response (6.11 s) | default approved by the PM, [PM-ASSUMED] |
| Q-2 | What do the not-updating cues read? | AC-4 | The header label "Not updating" with the tooltip "No data received for N min."; the as-of adds " | not updated for N min", N in whole minutes | default approved by the PM, [PM-ASSUMED] |
| Q-3 | What does the detail panel do when its symphony leaves the current view? | AC-8 | It keeps its last figures and its sub-label reads "not in the current view" | default approved by the PM, [PM-ASSUMED] |
| Q-4 | What does a card's sparkline show between midnight and the day's first cycle? | AC-9 | The previous session's tape with a caption naming its date | default approved by the PM, [PM-ASSUMED] |
| Q-5 | No `.claude/workflows/` file exists for `/` (plans 01, 02 and 03). Accept "no workflow file" and the User Tasks written here? | AC-1 | Yes, until the operator confirms a workflow file | default approved by the PM, [PM-ASSUMED] |

Sensitive: money (AC-7). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
