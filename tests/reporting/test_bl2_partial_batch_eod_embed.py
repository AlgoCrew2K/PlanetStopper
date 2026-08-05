"""RED tests -- BL-2 AC-3: the EOD Discord embed builder must render a
partial autotune batch VISIBLY DIFFERENTLY from both the "aborted" embed and
the healthy "N optimized" embed.

Feature plan: feature-plans/audit-bl2-autotuner-loop-isolation.md
Source: docs/audit/TWO-WEEK-REVIEW-2026-08-04.md Sec4 Finding T2, Sec6 Backlog BL-2

Background
----------
run_autotuner()'s return gains an additive
``optimization_results["_batch_summary"] = {"attempted": N, "completed": M}``
key (BL-2 AC-2, pinned in tests/autotuner/test_bl2_loop_isolation.py).
reporting.send_eod_discord_post's existing per-symphony changes loop
(reporting.py:654-655, ``for sym_name, changes in optimization_results.items()``)
iterates EVERY top-level key as a symphony name -- an additive
``_batch_summary`` key would render as a fake "_Batch_Summary Optimization"
embed unless explicitly excluded (structural gap identified during RED
planning, team-lead approved).

What these tests pin
---------------------
- AC-3: attempted != completed renders a message visibly distinct from both
  "Autotuner Aborted" and the healthy "N symphonies optimized" case.
- Structural guard: the ``_batch_summary`` key must NEVER render as a fake
  per-symphony embed.
- AC-5 regression: attempted == completed renders NO partial-batch notice
  (same happy-path rendering as today).
- Edge case: ALL symphonies failed (completed == 0) still renders an honest,
  non-crashing partial-batch message.
- Backward-compat negative case: a legacy-shaped optimization_results dict
  with NO ``_batch_summary`` key at all (e.g. a hand-built caller, or the
  pre-BL-2 shape) must never crash and must render the pre-BL-2 legacy
  output.

Fixture/harness pattern reused verbatim (house lesson
``feedback_consumer_suite_discovery_before_sufficiency``): the
``_make_capturing_post``/``_rendered_text``/report-file-writing helpers from
``tests/reporting/test_eod_changes_dict_shape_guard.py`` (the F-015
precedent for this exact function), duplicated here per this suite's
established per-file self-containment convention.

Provenance
----------
No producer-computed value is asserted -- these tests drive
reporting.send_eod_discord_post directly with hand-built optimization_results
dicts (never a hardcoded dollar/stat/rate value) and assert structural
outcomes (which embeds appear, substring presence/absence in rendered text).

No live Discord, no live DB, -n0 only.
"""

from __future__ import annotations

import json

import reporting

_SENTINEL_WEBHOOK = "https://discord.example.invalid/webhook/BL2-PARTIAL-BATCH-TEST"


def _write_minimal_report(tmp_path, date_str):
    """Write a minimal valid EOD report file so send_eod_discord_post proceeds
    past its own os.path.exists/json.load guard."""
    report_file = tmp_path / f"post_mortem_{date_str}.json"
    report_file.write_text(
        json.dumps({"summary": {"total_monitored": 0, "total_triggered": 0}, "triggers": []}),
        encoding="utf-8",
    )
    return report_file


def _make_capturing_post():
    """Return (post_fn, captured) -- post_fn records every requests.post call
    (both json= and multipart data=/payload_json forms) into captured, and
    returns a stub response satisfying .json() (QuickChart) and
    .status_code (Discord)."""
    captured = []

    def _post(url, *args, **kwargs):
        payload = kwargs.get("json")
        if payload is None and "data" in kwargs:
            data = kwargs["data"]
            if isinstance(data, dict) and "payload_json" in data:
                try:
                    payload = json.loads(data["payload_json"])
                except (TypeError, ValueError):
                    payload = None
        captured.append(payload)

        class _Resp:
            status_code = 200

            def json(self):
                return {}

        return _Resp()

    return _post, captured


def _rendered_text(captured):
    """Flatten every embed title + description across every captured payload
    into one lowercase-searchable string, and also expose the raw embeds
    list for structural (title-presence) assertions."""
    text = ""
    for payload in captured:
        if not isinstance(payload, dict):
            continue
        for embed in payload.get("embeds", []):
            if isinstance(embed, dict):
                text += " " + str(embed.get("title", ""))
                text += " " + str(embed.get("description", ""))
    return text


def _all_embed_titles(captured):
    titles = []
    for payload in captured:
        if not isinstance(payload, dict):
            continue
        for embed in payload.get("embeds", []):
            if isinstance(embed, dict):
                titles.append(embed.get("title", ""))
    return titles


def _notice_embeds(captured, *, known_symphony_names=()):
    """Return embeds that are none of: the hero ("Planet Stopper EOD
    Analysis" -- whose description is full of incidental digits: dates,
    "30-Day Performance Summary", $0.00 figures, etc; confirmed via a direct
    debug run against the CURRENT unfixed code, which is exactly why a
    whole-blob substring check for attempted/completed digits would be
    vacuous), a REAL per-symphony embed (title ending in
    "<Name.title()> Optimization" for a name this test itself deliberately
    seeded via known_symphony_names), or the Managed Sleeves digest.

    Anything else is a candidate NEW notice embed a partial-batch fix would
    add. Deliberately does NOT exclude a title like "_Batch_Summary
    Optimization" (the CURRENT unfixed code's fake per-symphony rendering of
    the "_batch_summary" key, since "_batch_summary" is never passed via
    known_symphony_names) -- that fake embed's own description carries no
    digits at all (the per-symphony shape guard skips int-valued
    "attempted"/"completed" entries, rendering "Optimal parameters
    retained."), so the calling test's digit-presence assertion still
    correctly fails against it today. A separate test
    (test_batch_summary_key_never_renders_as_a_fake_symphony_embed) already
    pins that this fake embed must disappear entirely post-fix.
    """
    known_titles = {f"{name.title()} Optimization" for name in known_symphony_names}
    notices = []
    for payload in captured:
        if not isinstance(payload, dict):
            continue
        for embed in payload.get("embeds", []):
            if not isinstance(embed, dict):
                continue
            title = str(embed.get("title", ""))
            if "Planet Stopper EOD Analysis" in title:
                continue
            if any(title.endswith(kt) for kt in known_titles):
                continue
            if "Managed Sleeves" in title:
                continue
            notices.append(embed)
    return notices


def _patch_common(tmp_path, monkeypatch, date_str):
    """Shared per-test scaffolding: redirect _POST_MORTEMS_DIR, write the
    report file, capture requests.post, no-op time.sleep."""
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
    report_file = _write_minimal_report(tmp_path, date_str)
    post_fn, captured = _make_capturing_post()
    monkeypatch.setattr(reporting.requests, "post", post_fn)
    monkeypatch.setattr(reporting.time, "sleep", lambda *a, **k: None)
    return report_file, captured


# ===========================================================================
# AC-3: partial batch renders distinct from both full-batch and aborted.
# ===========================================================================


def test_partial_batch_renders_distinct_from_full_batch_and_aborted(tmp_path, monkeypatch):
    """AC-3: attempted=3, completed=2 (one symphony's isolated exception)
    must render a message that (a) is NOT the "Autotuner Aborted" title,
    (b) is NOT the generic "No optimization changes" description, and
    (c) is visibly distinct -- e.g. surfaces both the attempted and
    completed counts somewhere in the rendered text.

    MUST FAIL on current code: reporting.py has no branch reading
    _batch_summary at all today -- a dict containing it renders exactly like
    a normal full batch (or, if it has no real symphony keys, falls through
    to the generic "No optimization changes" description), indistinguishable
    from either other state.
    """
    date_str = "2026-08-05"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {
        "sym_a": {"maxSqueezeFloor": {"old": 0.05, "new": 0.08}},
        "_batch_summary": {"attempted": 3, "completed": 2},
    }

    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured).lower()
    titles = _all_embed_titles(captured)

    assert "autotuner aborted" not in rendered, (
        f"a partial batch (2 of 3 completed) must NOT render as the "
        f"'Autotuner Aborted' state -- rendered: {rendered!r}"
    )
    assert "no optimization changes" not in rendered, (
        f"a partial batch must NOT render as the generic 'No optimization "
        f"changes' message -- indistinguishable from a genuine no-op run. "
        f"rendered: {rendered!r}"
    )

    # Scoped to a genuinely NEW notice embed (never the whole flattened
    # rendered blob -- the hero embed's own description is full of
    # incidental digits: "2026-08-05", "30-Day Performance Summary", $0.00
    # figures, etc, which would make a whole-blob "3" / "2" substring check
    # vacuously pass even on unfixed code).
    notices = _notice_embeds(captured, known_symphony_names=["sym_a"])
    assert notices, (
        f"no new notice embed found distinct from the hero/per-symphony/"
        f"sleeves embeds -- AC-3 requires a dedicated partial-batch notice "
        f"embed. All titles: {titles!r}"
    )
    notice_text = " ".join(
        str(e.get("title", "")) + " " + str(e.get("description", "")) for e in notices
    )
    assert "3" in notice_text and "2" in notice_text, (
        f"the partial-batch notice embed must surface both the attempted "
        f"(3) and completed (2) counts -- notice embed text: {notice_text!r}"
    )


def test_batch_summary_key_never_renders_as_a_fake_symphony_embed(tmp_path, monkeypatch):
    """Structural guard: the additive ``_batch_summary`` top-level key must
    NEVER be iterated by the existing per-symphony changes loop
    (reporting.py:654-655) as if it were a symphony name -- no embed titled
    anything derived from "_batch_summary" (e.g. "_Batch_Summary
    Optimization" via ``sym_name.title()``) may ever appear.

    MUST FAIL on current code: the unguarded ``for sym_name, changes in
    optimization_results.items()`` loop treats every top-level key
    (including "_batch_summary") as a symphony name.
    """
    date_str = "2026-08-05"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {
        "sym_a": {"maxSqueezeFloor": {"old": 1.0, "new": 2.0}},
        "_batch_summary": {"attempted": 2, "completed": 1},
    }

    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    titles = _all_embed_titles(captured)
    fake_titles = [
        t for t in titles if "batch_summary" in t.lower() or "batch summary" in t.lower()
    ]
    # The real partial-batch NOTICE embed is allowed to mention "batch" in
    # its own title/description (e.g. "Partial Batch") -- what must NEVER
    # happen is the per-symphony-loop's OWN title format
    # (f"{sym_name.title()} Optimization") being applied to the literal key
    # "_batch_summary", which Python's str.title() renders as "_Batch_Summary".
    assert not any("_batch_summary" in t.lower().replace(" ", "_") for t in titles), (
        f"the _batch_summary key leaked into the per-symphony embed loop as "
        f"a fake symphony -- got titles: {titles!r}"
    )


# ===========================================================================
# AC-5 regression: full batch (attempted == completed) unaffected.
# ===========================================================================


def test_full_batch_zero_failures_does_not_render_partial_batch_notice(tmp_path, monkeypatch):
    """AC-5: when attempted == completed (zero isolated failures), the
    per-symphony embeds render as today AND no partial-batch notice appears
    -- the new _batch_summary key is purely informational when there is
    nothing to report.
    """
    date_str = "2026-08-05"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {
        "sym_a": {"maxSqueezeFloor": {"old": 0.05, "new": 0.08}},
        "_batch_summary": {"attempted": 1, "completed": 1},
    }

    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured)
    assert "0.05" in rendered and "0.08" in rendered, (
        f"the real symphony delta must still render normally on a clean "
        f"batch -- rendered: {rendered!r}"
    )
    rendered_lower = rendered.lower()
    assert "autotuner aborted" not in rendered_lower, (
        "a clean (attempted==completed) batch must never render the aborted state"
    )
    # A clean batch has nothing partial to report -- no partial-batch/failed
    # notice should appear alongside the normal per-symphony embed.
    assert "failed" not in rendered_lower, (
        f"a clean batch (attempted==completed==1) must not render any "
        f"'failed' language -- rendered: {rendered!r}"
    )


# ===========================================================================
# Edge case: ALL symphonies failed.
# ===========================================================================


def test_all_failed_zero_completed_renders_honest_partial_batch_message(tmp_path, monkeypatch):
    """Edge case: attempted=3, completed=0 (every symphony's processing
    raised) -- no real symphony keys exist in optimization_results at all.
    Must still render an honest partial-batch (0-of-3) message, must not
    crash, and must be distinct from both "Autotuner Aborted" and "No
    optimization changes".
    """
    date_str = "2026-08-05"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {"_batch_summary": {"attempted": 3, "completed": 0}}

    # Must not raise.
    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured).lower()
    titles = _all_embed_titles(captured)
    assert "autotuner aborted" not in rendered, (
        f"a 0-of-3 partial batch is NOT a pre-loop abort -- must not render "
        f"as 'Autotuner Aborted'. rendered: {rendered!r}"
    )
    assert "no optimization changes" not in rendered, (
        f"a 0-of-3 partial batch must not render as the generic no-change "
        f"message -- indistinguishable from a genuine no-op run. rendered: "
        f"{rendered!r}"
    )

    # Scoped to a genuinely NEW notice embed -- never the whole flattened
    # rendered blob, whose hero embed alone is riddled with incidental "0"s
    # ("Total Monitored: 0", "$0.00 saved", "Win Rate: 0%", etc), which would
    # make a whole-blob "0" substring check vacuously pass even on unfixed
    # code. No real symphony key exists in this fixture (every symphony
    # failed), so known_symphony_names is empty.
    notices = _notice_embeds(captured, known_symphony_names=[])
    assert notices, (
        f"no new notice embed found distinct from the hero/sleeves embeds "
        f"-- AC-3 requires a dedicated partial-batch notice embed even when "
        f"zero symphonies completed. All titles: {titles!r}"
    )
    notice_text = " ".join(
        str(e.get("title", "")) + " " + str(e.get("description", "")) for e in notices
    )
    assert "3" in notice_text and "0" in notice_text, (
        f"the partial-batch notice embed must surface both the attempted "
        f"(3) and completed (0) counts -- notice embed text: {notice_text!r}"
    )


# ===========================================================================
# Backward-compat negative case: no _batch_summary key at all.
# ===========================================================================


def test_missing_batch_summary_key_never_crashes_legacy_shape_still_renders(tmp_path, monkeypatch):
    """Negative case: an optimization_results dict with NO ``_batch_summary``
    key at all (the legacy pre-BL-2 shape, or any future caller that omits
    it) must never crash and must render exactly the pre-BL-2 legacy
    per-symphony output -- no partial-batch notice appears when there is no
    batch-summary information to report.
    """
    date_str = "2026-08-05"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {"sym_legacy": {"maxSqueezeFloor": {"old": 3.0, "new": 4.0}}}

    # Must not raise.
    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured)
    assert "3.0" in rendered and "4.0" in rendered, (
        f"the legacy-shaped symphony delta must still render normally when "
        f"_batch_summary is absent entirely -- rendered: {rendered!r}"
    )
    rendered_lower = rendered.lower()
    assert "autotuner aborted" not in rendered_lower
    assert "failed" not in rendered_lower, (
        f"no partial-batch/failed language may appear when _batch_summary is "
        f"absent -- rendered: {rendered!r}"
    )
