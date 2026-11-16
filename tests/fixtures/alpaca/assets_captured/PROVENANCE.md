# Alpaca asset records, real responses

Host `https://paper-api.alpaca.markets`, read-only `GET /v2/assets/{symbol}`, captured by the PM on the
droplet's credentials. One file per case: `{request, status, content_type, body}`, body verbatim.

| File | Request | Captured (UTC) | Case |
|---|---|---|---|
| asset_SHNY_inactive_not_tradable.json | GET /v2/assets/SHNY | 2026-11-16T18:03:33+00:00 | 200, status inactive, tradable false |
| asset_SPY_active_tradable.json | GET /v2/assets/SPY | 2026-11-16T18:03:33+00:00 | 200, active, tradable |
| asset_UGL_active_tradable.json | GET /v2/assets/UGL | 2026-11-16T18:03:33+00:00 | 200, active, tradable |
| asset_ZZZZQQ_404_not_found.json | GET /v2/assets/ZZZZQQ | 2026-11-16T18:03:33+00:00 | 404, asset not found |
| asset_SPY_401_wrong_key.json | GET /v2/assets/SPY with a deliberately wrong key pair | 2026-11-16T18:03:33+00:00 | 401 |
| asset_AKAN_active_not_tradable.json | GET /v2/assets/AKAN | 2026-11-16T19:24:23+00:00 | 200, active, tradable false (one of 37 such symbols on NASDAQ, NYSE, ARCA, BATS, AMEX in one read of the active us_equity list) |

No credential appears in any body. The 401 case sent the literal key and secret `invalid`.
