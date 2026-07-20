# Feature: Regen Stamps Verified-Unchanged Entries (F-008 completion)
Status: ready
Created: 2026-07-20

## Summary
`scripts/regenerate_post_mortems.py` stamps `if_held_source = "shadow_history"` ONLY on entries whose recomputed values differ from the stored values (`if old != new:` block, ~line 190). An in-window entry that the script RESOLVES to a shadow_history row and CONFIRMS byte-equal is left unstamped — and the F-008 read-time guard (`analytics.is_valid_post_mortem_entry`) then EXCLUDES that verified-correct entry from every live consumer. Production impact (2026-07-20 repair run on the droplet): `post_mortem_2026-06-22.json` has 10/11 entries stamped and 1 verified-correct entry unstamped — "(INVEST) LQD + EYEG 5 ways Full Market", **$+28.72** — silently missing from the live "$X saved" headline. Fix: a resolved entry whose values are confirmed-equal but whose stamp is missing/unrecognized gets stamped `"shadow_history"`, that stamp-addition counts as a change (file rewritten, reported distinctly), and everything stays idempotent. The guard itself is UNTOUCHED.

## Acceptance Criteria
- [ ] AC-1: an in-window trigger entry resolved to a shadow_history row whose recomputed values EQUAL the stored values but whose `if_held_source` is missing (or an unrecognized string) is stamped `"shadow_history"`; the file counts as changed (rewritten on `--apply`, `regenerated` marker present) and the report prints a distinct stamp-only line (e.g. "stamp-only: <symphony>").
- [ ] AC-2: an entry already carrying a TRUSTED stamp (`shadow_history`, `shadow_history_post_cutoff`, `bot_state_fallback`) with equal values is NOT modified — no re-stamp, no rewrite churn from such entries alone.
- [ ] AC-3: value-changed entries behave exactly as today (values updated + stamped + reported in old→new form) — regression-guarded.
- [ ] AC-4: idempotency — a second run immediately after a stamp-only `--apply` reports zero changes and rewrites nothing.
- [ ] AC-5: the all-or-nothing refusal (`--apply` refuses if ANY in-window entry cannot be resolved) is byte-unchanged in behavior.
- [ ] AC-6: dry-run prints pending stamp-only changes without writing anything (dry-run default preserved).
- [ ] AC-7 (blast radius): `analytics.is_valid_post_mortem_entry` / `_TRUSTED_IF_HELD_SOURCES` and all live consumers (app.py, analytics.py) are byte-UNCHANGED.

## Architecture
- `scripts/regenerate_post_mortems.py` (~lines 180-200): today `if old != new:` gates BOTH the value update AND the stamp AND the `changes.append`. Restructure minimally: value-change branch unchanged; add an `elif` for resolved-and-equal entries whose existing `if_held_source` is not in the trusted set → stamp + append a stamp-only change record. The trusted-set membership check must mirror `analytics._TRUSTED_IF_HELD_SOURCES` semantics (import it or mirror the frozenset with a source comment — implementer's call, but no drift: prefer importing `analytics.is_valid_post_mortem_entry` if import cost is acceptable for a script that already imports repo modules; else a named constant with a pointer comment).
- Report path: `changes` non-empty → file rewritten + `regenerated` marker — existing machinery reused; stamp-only records flow through it.
- NO changes to analytics.py / app.py / reporting.py.

## Edge Cases
- Mixed file: some value-changed, some stamp-only, some trusted-unchanged → all three behaviors coexist; only the first two produce report lines.
- Unrecognized stamp string (e.g. `"bogus"`) on a confirmed-equal entry → treated as untrusted → re-stamped `"shadow_history"` (the value was just verified against shadow_history truth).
- Entry with a trusted NON-shadow_history stamp (`bot_state_fallback`) and equal values → left alone (AC-2; do not churn provenance that is already trusted).
- Out-of-window files → untouched (existing behavior).

## Security Considerations
- No new inputs; the script remains operator-gated, dry-run-default, all-or-nothing. No secrets. Parameterized reads only.

## Testing Strategy
- Extend `tests/scripts/test_regenerate_post_mortems_window.py` (existing module from #104): fixture post-mortem JSONs + a seeded temp shadow_history DB per the module's existing pattern; `-n0`.
- RED: (1) unstamped-but-correct entry → stamped + file rewritten + stamp-only report line (AC-1, RED today); (2) trusted-stamp unchanged entry → file NOT rewritten when it's the only candidate (AC-2); (3) value-change regression (AC-3, stays green); (4) second-run idempotency after stamp-only apply (AC-4, RED today via the rewrite gate); (5) dry-run prints stamp-only without writing (AC-6); (6) all-or-nothing refusal regression (AC-5, stays green).
- Blast-radius grep: no other caller of the stamping block; analytics/app byte-unchanged (AC-7) asserted by the reviewer, not tests.
- Both ruff gates. LF line endings (repo norm — cycle-4 CI lesson).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Fix the SCRIPT, not the guard | The guard's per-entry trust model is correct and live; the script under-asserts provenance it has just verified. Smallest blast radius: repair tooling only. |
| Stamp-only counts as a change | Otherwise the rewrite gate skips the file and the stamp never lands on disk (the exact production gap observed). |
| Leave trusted non-shadow_history stamps alone | Verified-equal doesn't justify rewriting already-trusted provenance; avoids churn and keeps the diff honest. |

## Scope Boundaries
- IN: `scripts/regenerate_post_mortems.py` stamping/report/rewrite-gate behavior + its tests.
- OUT: `analytics.py` guard, `app.py`, `reporting.py`, any live consumer; the 07-09 exit_triggers 4x-duplicate (separate gated repair); all other findings.
