# GDELT API Contract — Planet Stopper Sentiment Lens

**Pinned:** 2026-06-15
**Reconstruction source:** `advisors/lens_gdelt.py` (shipped at PR #33, commit d632de3)
**Contract reference:** this file; see also `feature-plans/lens-data-gdelt-sentiment.md`

---

## §1 — Endpoint: timelinetone (primary tone signal)

Base URL: `https://api.gdeltproject.org/api/v2/doc/doc`

Query parameters (pinned):
- `query=stock+market+finance` — universe-level market sentiment query
- `mode=timelinetone` — returns a time-series of AvgTone values
- `format=json`

Full pinned URL:
```
https://api.gdeltproject.org/api/v2/doc/doc?query=stock+market+finance&mode=timelinetone&format=json
```

No API key required. Free endpoint, rate-limited at ~1 request / 5 seconds per IP.

---

## §2 — timelinetone Response Shape

```json
{
  "timeline": [
    {
      "series": "<series-name-string>",
      "data": [
        {"date": "YYYYMMDDHHMMSS", "value": <float>},
        ...
      ]
    }
  ]
}
```

**Correct extraction path** (CRITICAL — prior bug used the wrong level):
- `timeline[0]["data"]` is the list of `{date, value}` dicts
- Each `value` is the raw AvgTone for that time bucket
- `timeline[0]` itself is `{series, data}` — it does NOT have a top-level `"value"` key
- The prior production bug (`tone=None, available=True`) extracted `entry.get("value")` from the
  series wrapper, which always returned None. The correct path is one level deeper: `p["value"]`
  inside the `data` list.

---

## §3 — Source Citation Shape

From the artlist endpoint (§2b), each article is mapped to:
```json
{"url": "...", "seendate": "YYYYMMDDHHMMSS", "title": "...", "domain": "..."}
```

The `source` field in the return dict is always:
```
GDELT 2.0 DOC API timelinetone — https://api.gdeltproject.org/
```

---

## §4 — Return Dict Contract

```python
{
    "available": bool,          # True only when tone was successfully extracted
    "tone":      float | None,  # Normalized AvgTone in [-1.0, 1.0]; None when unavailable
    "per_ticker": None,         # v1: always None (universe-level signal only)
    "source":    str,           # Always present — human-readable citation string
    "sources":   list | None,   # Artlist citations; None on artlist failure before tone, [] on best-effort artlist failure after tone success
    "reason":    str | None,    # Set only when available=False; None on success
}
```

**Invariant (load-bearing, pinned by AC-9a):** `tone is None` IMPLIES `available is False`.
The reverse (`available=True, tone=None`) is FORBIDDEN — it was the prior production bug.

**Named reason labels** (D-1 contract — never `str(exc)`, only `type(exc).__name__`):
- `"rate_limited"` — HTTP 429 exhausted all retry attempts
- `"gdelt_fetch_failed"` — non-2xx status code
- `"no_tone_data"` — HTTP 200 but timeline list empty or no numeric values
- For all other exceptions: `type(exc).__name__` (e.g. `"ConnectionError"`, `"JSONDecodeError"`)

---

## §5 — Retry / Rate-Limit Policy (Amendment 1)

Only the **timelinetone** (tone) GET is retried. Artlist is best-effort (no retry).

| Constant | Pinned Value | Rationale |
|---|---|---|
| `_GDELT_MAX_ATTEMPTS` | 4 | 1 initial + 3 retries; prevents persistent-429 PC crash |
| `_GDELT_BACKOFF_BASE_S` | 20.0 s | 4× margin above GDELT's 5 s/req rate-limit window |
| `_GDELT_BACKOFF_CAP_S` | 60.0 s | Caps exponential schedule (20s → 40s → 60s) |
| `_GDELT_TIMEOUT_S` | 15.0 s | Per-attempt connect+read timeout; avoids urllib3 None default |
| `_GDELT_INTER_REQUEST_S` | 6.0 s | Spacing between tone GET and artlist GET; clears 5 s rate window |

Backoff schedule: `min(_GDELT_BACKOFF_BASE_S * 2**attempt, _GDELT_BACKOFF_CAP_S)`
- attempt 0 → 20 s
- attempt 1 → 40 s
- attempt 2 → 60 s (capped)

**429 detection:** HTTP status code only. The body is plaintext on 429 — do NOT attempt to parse it as JSON.

**Amendment 2 (2026-07-13, `advisor-suite-fixes.md` AC-4/AC-6 fix cycle):** the tone GET's bounded retry loop now ALSO retries `requests.exceptions.Timeout` and `requests.exceptions.ConnectionError`, using the same per-attempt exponential backoff formula and the same `_GDELT_MAX_ATTEMPTS` ceiling as the 429 path above — mirroring `ai_advisor._fetch_with_backoff` (`ai_advisor.py:406-486`), which already retries these transient network errors alongside 429. Prior to this amendment, a Timeout/ConnectionError on the FIRST attempt propagated immediately past the retry loop (only the non-raising 429 HTTP response ever reached the retry logic), so a single transient network blip produced an unrecoverable `available=False` even though attempts 2-4 would likely have succeeded. Non-network exceptions (e.g. a `JSONDecodeError` from a malformed 2xx body) are still NOT retried — unchanged from the original contract, and consistent with `_fetch_with_backoff`. No new D-1 reason label was added: an exhausted Timeout/ConnectionError retry still returns `type(exc).__name__` (`"Timeout"` / `"ConnectionError"`) per the existing §4 table.

---

## §6 — Scope Limitations (v1)

- Universe-level signal only. `per_ticker` is always `None`. The query is fixed
  (`stock+market+finance`) regardless of which tickers are in the live portfolio.
- Per-ticker GDELT signals are deferred to a future version.

---

## §7 — Normalization

Raw GDELT AvgTone is in `[-100, 100]`. Normalized tone is:
```python
tone = float(max(-1.0, min(1.0, mean_tone / 100.0)))
```
Result is always in `[-1.0, 1.0]`.

---

## §8 — Architecture Constraints

- **D-1:** All error `reason` values use `type(exc).__name__` only — never `str(exc)` or the
  exception message (which can contain PII or credentials).
- **CC-2 / off-execution-path:** `lens_gdelt` is never imported at module level in
  `alpha_bot_execution.py`. It is lazy-imported only from the AI Advisor lens pipeline
  (`advisors/lens_pipeline.py`) which runs off-hours (03:00 nightly), never on the
  1-minute execution cadence.
- **Honest availability:** producers must set `available=False` whenever `tone=None`.
  A producer MUST NOT return `available=True, tone=None`.
