"""RED tests -- F-015: EOD Discord push crash on a non-{old,new}-shaped changes
entry (confidence-audit finding, fix-autotune-reporting cycle, AC-1/AC-2/AC-3/AC-6).

Background
----------
reporting.send_eod_discord_post iterates each symphony's ``changes`` dict
(reporting.py:479) and unconditionally indexes ``vals["old"]`` after
skip-listing only ``_baseline_chosen``/``_selection_stats`` (reporting.py:480).
autotuner.py:2855 unconditionally writes an ``eval_window_days`` entry into
that same dict whose value is a plain stats dict (raw_validation_days /
usable_validation_days / raw_frozen_eval_days / purge_days / embargo_days) --
NOT a ``{old, new}`` delta. ``vals["old"]`` KeyErrors on that entry, and
``KeyError`` is absent from the surrounding
``except (OSError, ValueError, requests.RequestException, TypeError)`` at
reporting.py:540 -- so the exception propagates out of send_eod_discord_post
uncaught, killing the entire EOD push on every autotune day
(PRODUCTION-CONFIRMED symptom: "Execution failed: exit status 1", no summary
delivered, no autotune results, no error detail).

What these tests pin
---------------------
- A changes dict containing the real eval_window_days shape (or any other
  non-{old,new} shape) must not crash the push -- the real {old,new} deltas
  in the same dict must still be delivered (AC-1, AC-2, AC-3).
- Multiple malformed entries in one dict must not crash on the first (edge
  case named in the feature plan).
- A normal, all-{old,new} day renders byte-for-byte as before the fix
  (AC-6 golden regression guard).

These tests are deliberately BEHAVIORAL, not AST-based: they do not
prescribe whether the implementer skips a malformed entry, renders it
safely, or widens the except tuple at reporting.py:540 -- only that the push
cannot crash and the real deltas must survive it. See
feature-plans/fix-autotune-reporting.md.

Provenance
----------
The eval_window_days entry shape is copied VERBATIM from the keys
autotuner.py:2855-2861 actually writes (raw_validation_days,
usable_validation_days, raw_frozen_eval_days, purge_days, embargo_days) --
not invented. The AC-6 golden expected string is built from the SAME
literals this file defines as the test's own {old,new} input (not a
hand-copied production value), using the identical f-string reporting.py
itself applies -- a direct passthrough with no float arithmetic along this
path, so a plain string equality is the correct assertion (pytest.approx
would be inapplicable here -- nothing is computed).

No live Discord, no live DB, -n0 only.
"""

import json

import pytest

import reporting

_SENTINEL_WEBHOOK = "https://discord.example.invalid/webhook/SHAPE-GUARD-TEST"

# Verbatim shape autotuner.py:2855-2861 unconditionally writes into every
# symphony's changes dict -- the entry that is NOT a {old,new} delta.
_EVAL_WINDOW_DAYS_ENTRY = {
    "raw_validation_days": 50,
    "usable_validation_days": 29,
    "raw_frozen_eval_days": 50,
    "purge_days": 20,
    "embargo_days": 1,
}


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
    .status_code (Discord). Same pattern as
    tests/autotuner/test_history_shortfall_graceful_abort.py's _capture_post.
    """
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
                # QuickChart response is read via resp.json().get("url");
                # an empty dict simply omits the chart image.
                return {}

        return _Resp()

    return _post, captured


def _rendered_text(captured):
    """Flatten every embed title + description across every captured payload
    into one lowercase-searchable string."""
    text = ""
    for payload in captured:
        if not isinstance(payload, dict):
            continue
        for embed in payload.get("embeds", []):
            if isinstance(embed, dict):
                text += " " + str(embed.get("title", ""))
                text += " " + str(embed.get("description", ""))
    return text


def _patch_common(tmp_path, monkeypatch, date_str):
    """Shared per-test scaffolding: redirect _POST_MORTEMS_DIR, write the
    report file, capture requests.post, no-op time.sleep."""
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))
    report_file = _write_minimal_report(tmp_path, date_str)
    post_fn, captured = _make_capturing_post()
    monkeypatch.setattr(reporting.requests, "post", post_fn)
    monkeypatch.setattr(reporting.time, "sleep", lambda *a, **k: None)
    return report_file, captured


def test_eod_post_survives_a_non_delta_changes_entry_eval_window_days_shaped(tmp_path, monkeypatch):
    """F-015 repro: a changes dict with a real {old,new} delta PLUS the
    eval_window_days-shaped entry autotuner.py:2855 unconditionally writes
    must not crash send_eod_discord_post, and the real delta must still be
    delivered.

    MUST FAIL on current code: vals["old"] on the eval_window_days dict
    raises KeyError, which is not in the except tuple at reporting.py:540,
    so the exception propagates out of this call uncaught.
    """
    date_str = "2026-07-19"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {
        "Sym_A": {
            "maxSqueezeFloor": {"old": 0.05, "new": 0.08},
            "eval_window_days": _EVAL_WINDOW_DAYS_ENTRY,
        }
    }

    # Must not raise.
    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured)
    assert "0.05" in rendered and "0.08" in rendered, (
        f"the real {{old,new}} delta did not survive the malformed "
        f"eval_window_days sibling entry -- rendered embeds: {rendered!r}"
    )


_MALFORMED_SHAPES = [
    pytest.param({"foo": 1, "bar": 2}, id="dict_without_old_new_keys"),
    pytest.param({"old": 0.1}, id="dict_with_only_old_key"),
    pytest.param(42, id="scalar_int"),
    pytest.param([1, 2, 3], id="list_value"),
    pytest.param(None, id="none_value"),
    pytest.param("unexpected-string", id="string_value"),
]


@pytest.mark.parametrize("malformed_value", _MALFORMED_SHAPES)
def test_eod_post_handles_various_malformed_change_entry_shapes(
    malformed_value, tmp_path, monkeypatch
):
    """AC-2: the changes-iteration must handle ANY entry that isn't a
    {old,new} delta dict -- not just the eval_window_days case -- whether by
    skipping it or rendering it safely. Only the real sibling delta's
    survival and the absence of a crash are pinned here; HOW the malformed
    entry itself is handled is the implementer's choice.
    """
    date_str = "2026-07-19"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {
        "Sym_B": {
            "maxSqueezeFloor": {"old": 1.0, "new": 2.0},
            "malformed_entry": malformed_value,
        }
    }

    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured)
    assert "1.0" in rendered and "2.0" in rendered, (
        f"the real delta did not survive malformed entry {malformed_value!r} "
        f"-- rendered embeds: {rendered!r}"
    )


def test_eod_post_multiple_malformed_entries_does_not_crash_on_first(tmp_path, monkeypatch):
    """Edge case named in the feature plan: MULTIPLE malformed entries in one
    changes dict must not crash processing on the first one encountered --
    the loop must get through all entries (dict iteration is insertion-order)
    and still deliver the real delta that comes after them.
    """
    date_str = "2026-07-19"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    optimization_results = {
        "Sym_C": {
            # Two malformed entries FIRST (insertion order), real delta LAST --
            # a crash on the first malformed entry would never reach the delta.
            "eval_window_days": _EVAL_WINDOW_DAYS_ENTRY,
            "another_malformed_entry": [1, 2, 3],
            "maxSqueezeFloor": {"old": 3.0, "new": 4.0},
        }
    }

    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    rendered = _rendered_text(captured)
    assert "3.0" in rendered and "4.0" in rendered, (
        f"the real delta was lost when TWO malformed entries preceded it in "
        f"iteration order -- rendered embeds: {rendered!r}"
    )


def test_eod_post_golden_normal_delta_only_day_renders_unchanged(tmp_path, monkeypatch):
    """AC-6 regression guard: a normal day where every changes entry is a
    real {old,new} delta (the happy path, unchanged by the fix) renders
    identically to the pre-fix behaviour -- same baseline-decision line,
    same delta lines, same selection-stats line.

    The expected description is built from the SAME literals this test
    defines as input (not a hand-copied production value), reusing the
    exact f-string reporting.py itself applies at reporting.py:474-502. This
    is a direct passthrough with no float arithmetic, so a plain string
    equality is the correct assertion -- pytest.approx is inapplicable
    since nothing is computed along this path.
    """
    date_str = "2026-07-19"
    report_file, captured = _patch_common(tmp_path, monkeypatch, date_str)

    sym_name = "sym_d"
    baseline = "current_params"
    deltas = {
        "maxSqueezeFloor": {"old": 0.05, "new": 0.08},
        "volLookback": {"old": 20, "new": 25},
    }
    selection_stats = {
        "naive_sharpe": 1.2345,
        "selection_tstat": 0.987,
        "frozen_eval_sharpe": 1.001,
    }
    changes = {"_baseline_chosen": baseline, "_selection_stats": selection_stats, **deltas}
    optimization_results = {sym_name: changes}

    reporting.send_eod_discord_post(
        date_str, str(report_file), optimization_results, _SENTINEL_WEBHOOK
    )

    # Reconstruct the expected embed using reporting.py's own format rules
    # (reporting.py:474, :484, :496-501), driven by this test's own literals.
    expected_title = f"⚙️ {sym_name.title()} Optimization"
    expected_desc = f"**Decision:** {baseline}\n\n"
    for var, vals in deltas.items():
        if vals["old"] != vals["new"]:
            expected_desc += f"- `{var}`: {vals['old']} -> {vals['new']}\n"
    expected_desc += (
        f"\nSortino (naive / selection t-stat / frozen-eval): "
        f"{selection_stats['naive_sharpe']:.4f} / "
        f"{selection_stats['selection_tstat']:.4f} / "
        f"{selection_stats['frozen_eval_sharpe']:.4f}"
    )

    opt_embed = None
    for payload in captured:
        if not isinstance(payload, dict):
            continue
        for embed in payload.get("embeds", []):
            if isinstance(embed, dict) and embed.get("title") == expected_title:
                opt_embed = embed
                break

    assert opt_embed is not None, (
        f"no {expected_title!r} embed found in the captured payloads -- captured: {captured!r}"
    )
    assert opt_embed["description"] == expected_desc, (
        f"the happy-path optimization embed description changed.\n"
        f"expected: {expected_desc!r}\n"
        f"actual:   {opt_embed['description']!r}"
    )
