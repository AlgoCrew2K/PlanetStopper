# Dry-Run vs If-Held Mirroring — Read-Only RCA

**Scope:** portfolio strip + per-symphony CR/TC/MDD cells in `table_partial.html`.
**Verdict:** wiring is intentionally mirrored at the source for CR and MDD; TC is only differentiated post-trigger. Both portfolio and per-symphony cells exhibit the same problem — operator only "noticed" it on the strip because the per-symphony `If Held (Shadow)` column they compared against is **a different code path** (not an M1 helper).

---

## 1. Root cause — file:line evidence

The M1 helpers in `analytics.py` set `dry_run = if_held` by construction for CR and MDD, and conditionally for TC.

**`get_symphony_cumulative_return`** — `analytics.py:418-438`
- Line 437: `if_held = simple_return * 100.0`
- Line 438: `return {"if_held": if_held, "dry_run": if_held}` — **same scalar, both keys.**
- Docstring (425): *"bot_state does not store CR; always equals if_held."*

**`get_symphony_max_drawdown`** — `analytics.py:441-453`
- Line 452: `if_held = float(sym_dict["max_drawdown"])`
- Line 453: `return {"if_held": if_held, "dry_run": if_held}` — **same scalar, both keys.**
- Docstring (446): *"bot_state does not store MDD; always equals if_held."*

**`get_symphony_today_change`** — `analytics.py:398-415`
- Line 406: `if_held = float(sym_dict["last_percent_change"]) * 100.0`
- Lines 407-414: `dry_run = bot_state_entry["current_return"]` **only if `triggered is True`**; otherwise `dry_run = if_held`.

The portfolio aggregator (`_value_weighted_portfolio`, lines 456-505) does an honest two-stream value-weighted average (lines 493-494 sum independently, 503-504 divide independently). It is correct — but with identical per-symphony inputs, identical aggregates are mathematically forced.

The portfolio numbers the operator pasted (CR/MDD/TC identical to ~14 decimals) match: no symphony in the portfolio is currently `triggered`, so even the one TC path that *could* differ collapses to `if_held`.

---

## 2. Was dry_run vs if_held supposed to differ?

**Yes, per the M1 design intent** (engine shadow vs buy-and-hold). The wiring is broken at the **data-source layer**, not the helper layer:

- `bot_state` has no persisted shadow-equity series — only `shadow_hwm` (a high-water scalar) and the running `current_return` Composer reports.
- `current_return` (`alpha_bot_execution.py:426, 446, 501, 566, 780`) is computed from `sym["last_percent_change"]` — the **Composer live percent**, identical to what `if_held` reads. It is NOT a shadow series.
- `simple_return` is also a direct passthrough from Composer (`alpha_bot_execution.py:100`).

So the helpers honor the only contract the data supports: there is no AlphaBot-shadow CR/MDD time-series anywhere in `bot_state`. The "dry_run" branch is a placeholder that intentionally mirrors `if_held` until a shadow-equity series is produced.

TC has a real differentiation path (post-trigger frozen return vs live drift), but it only activates when `triggered=True`. The fix surface is therefore "where does the shadow equity series come from?" — not "rewire the helpers."

---

## 3. Per-symphony cells vs portfolio strip — same bug?

**Same bug.** The per-symphony cells at `table_partial.html:188-190` consume `sym._tc / _cr / _mdd`, attached in `app.py:316-326` via the **same** helpers. CR and MDD cells render `dry_run (if_held)` with **identical numbers in both halves** for every untriggered symphony; TC the same except for currently-triggered rows.

The column the operator compared against — **"If Held (Shadow)"** (header `app.py`/template line 61-62, value at `table_partial.html:187`) — is `shadow_str`, built at lines 122-129 from `sym.current_return` and `sym.triggered_at_return`. It is **NOT** an M1 helper output. It shows different values from "Return" only when `is_triggered=True` (the frozen exit return diverges from the live drift). This is why the operator saw distinct values per-row in that column despite the M1 cells being mirrored — they are reading two unrelated data paths.

The column header "If Held (Shadow)" is also misleadingly named relative to the M1 `if_held` semantic — they are not the same concept.

---

## 4. Fix recommendations (no implementation)

1. **Decide the intended dry_run data source.** Options:
   a. Persist a per-cycle AlphaBot-shadow equity curve in `bot_state` (new field, e.g. `shadow_equity[date] = value`), then derive CR/MDD/TC from it.
   b. Compute dry_run from existing exit-trigger telemetry (`triggered_at_return`, `f_ret`) as a frozen-on-exit cumulative.
   c. Keep mirroring and explicitly mark CR/MDD as "if-held only" in the UI until shadow series ships.
2. **TC dry_run inconsistency** — `analytics.py:412` reads `current_return` only when triggered. If option (a) is chosen, TC should also read from the shadow series for symmetry; otherwise the metric is mixed (engine-shadow for triggered, Composer-live for untriggered).
3. **UI clarity** — until divergence ships, render strip and CR/MDD cells as a single value (no `dry_run (if_held)` parenthetical) to avoid implying differentiation that does not exist.
4. **Rename column** — `table_partial.html:62` "If Held" / "If Held (Shadow)" should be disambiguated from the M1 `if_held` key; they encode different concepts and will confuse future maintainers.
5. **Test gap** — no fixture asserts `dry_run != if_held` under shadow conditions; once the data source lands, golden fixtures must cover both untriggered and triggered states across at least one full session to lock divergence in.

---

## Files cited (absolute paths)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\analytics.py` (lines 398-505)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\app.py` (lines 282-332)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py` (lines 100, 426, 446, 501, 566, 780)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\templates\table_partial.html` (lines 61-62, 122-129, 143-152, 187-190)
