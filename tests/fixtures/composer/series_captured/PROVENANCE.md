# Composer per-symphony daily series: real captures

Captured 2026-10-22T20:50:58.274063+00:00 (after the US market close) on the production droplet with the app's own Composer credentials,
read-only, one `GET /api/v0.1/portfolio/accounts/{account}/symphonies/{symphony_id}` per live symphony, spaced 1.2 s.
Each `<symphony_id>.json` is the raw response body exactly as returned (keys `epoch_ms`, `series`, `deposit_adjusted_series`); nothing was edited.
No account id or credential is stored; `_index.json` names the endpoint by template only.

Facts these captures establish:
- Every `epoch_ms` point in every series is stamped 00:00 UTC (the stamp is Composer's calendar date in UTC;
  read in America/New_York it falls on the previous day).
- `deposit_adjusted_series` is positive and starts at 1000.0; it differs from `series` only around capital flows.
- The latest point at capture time is 2026-10-21 (UTC date), the last completed session before the capture.
- One live symphony returns empty arrays (0 points).

| Symphony id | Display name | Points | First (UTC) | Last (UTC) |
|---|---|---|---|---|
| `5XjzXjdGnjh99MIsdM97` | Land of Feaver'd Allocations: Intelligent Novas | 150 | 2026-03-19 | 2026-10-21 |
| `8FAXAnQmYi1INDubazeC` | Planet LQD: Run of the Feaver'd WaltAnansi USDs 2/3 Mixup | 235 | 2025-11-13 | 2026-10-21 |
| `Gpaw3IhZghQPRE6AdEKx` | Planet of the Reasonabilists: Era of the Erased Golden Age | 284 | 2025-09-05 | 2026-10-21 |
| `INfCn3eKsu6i4oTTqdUp` | (INVEST) Planet of Projected Inflation: Corporate Chaos 2060 | 0 | - | - |
| `MoAkUHnavSYw3oONiUxe` | Planet of Erased History: Nova's Feaver'd Golden Age of EYEG | 148 | 2026-03-23 | 2026-10-21 |
| `hvPiGP1O7AHfutHE3Fjy` | (INVEST) Planet of Hunted Cascades - Land of Intelligent Allocations | 389 | 2025-04-04 | 2026-10-21 |
| `iaSOOUsmnCJHiZvbrWfs` | (INVEST) LQD + EYEG 5 ways Full Market | 158 | 2026-03-09 | 2026-10-21 |
| `lW4ZzWuqR8tEO2DhXbil` | Planet of the Paragons: EYEG of the LQD | 150 | 2026-03-19 | 2026-10-21 |
| `n2ooAZTvBRN6ZzpMmWmU` | (INVEST:CRYPTO) We do a Little Trolling Planet's Mix v1.4 / 10.19.2022 | 572 | 2024-07-12 | 2026-10-21 |
| `nOyb55RMGVCKPiYXv7TI` | Corporate Chaos 5 ways  | 150 | 2026-03-19 | 2026-10-21 |
| `q7WQHwLzt49cMgFBzHsX` | Planet of the Golden Age: Arrival of the NewRunners | 30 | 2026-09-10 | 2026-10-21 |
| `qF5ZU7ALjrlhxrGEwsyJ` | Planet of the Golden Age (Buy Copy) | 141 | 2026-04-01 | 2026-10-21 |
