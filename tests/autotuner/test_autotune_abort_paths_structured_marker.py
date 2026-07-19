"""RED tests -- F-004: run_autotuner has THREE graceful-abort return sites but
only ONE returns the structured {aborted: True, reason: ...} marker
(confidence-audit finding, fix-autotune-reporting cycle, AC-4/AC-5).

Background
----------
autotuner.run_autotuner has three points where it aborts gracefully rather
than completing a walk-forward optimization run:

  1. autotuner.py:2358-2364 -- generate_synthetic_history raises the named
     history-shortfall exception. This path ALREADY returns the structured
     marker (fixed in a prior cycle -- see
     tests/autotuner/test_history_shortfall_graceful_abort.py, which pins
     it and its end-to-end render via reporting.send_eod_discord_post).
  2. autotuner.py:2365-2367 -- generate_synthetic_history returns a falsy
     history_125d (no exception, just nothing usable). Returns a bare
     ``None`` today.
  3. autotuner.py:2375-2378 -- fewer than 2 trading days survive the 60/20/20
     WFA split. Returns a bare ``None`` today.

reporting.py:456-466 ALREADY branches on ``optimization_results.get("aborted")``
to render a distinct "Autotuner Aborted" embed (from the same prior cycle
that fixed path 1) -- so once paths 2 and 3 also return the structured
marker, the existing render logic picks them up with NO reporting.py change
required. The gap this file targets is narrower than "abort doesn't render":
it is specifically that paths 2 and 3 still return bare None, which
reporting.py's ``elif optimization_results:`` treats as falsy and renders as
"No optimization changes" -- indistinguishable from a genuine no-op run.

What these tests pin
---------------------
- Paths 2 and 3 return the structured {aborted: True, reason: <str>} marker,
  not bare None (AC-4).
- All THREE paths (including the already-fixed path 1) share the identical
  marker shape -- a contract lock so a future change to any one path can't
  silently drift back to bare None without breaking this test (AC-4).
- End-to-end: feeding paths 2 and 3's return into
  reporting.send_eod_discord_post renders "Autotuner Aborted" + the reason,
  and is visibly distinct from a genuine no-change run's "No optimization
  changes" (AC-5).

Provenance
----------
No producer-computed value is asserted. These tests drive run_autotuner with
generate_synthetic_history monkeypatched to reproduce each abort trigger
condition and assert structural outcomes (marker shape, reason is a
non-empty string, rendered text contains specific literal tokens) -- never a
hardcoded dollar/stat/rate value. pytest.approx is not relevant; no floats
are compared.

No live Discord, no live DB (isolated per tests/conftest.py), -n0 only.
"""

from __future__ import annotations

import json

import pytest

import autotuner
import reporting


def _minimal_bot_state() -> dict:
    """A minimal bot_state with one symphony -- enough for run_autotuner to
    reach the generate_synthetic_history call. Same shape as
    tests/autotuner/test_history_shortfall_graceful_abort.py's
    _minimal_bot_state."""
    return {
        "sym-A": {
            "current_holdings": [{"ticker": "SPY", "allocation": 1.0}],
        }
    }


def _spec_bundle_kwarg() -> dict:
    """Return {spec_bundle_id: <id>} if run_autotuner accepts that parameter
    -- mirrors the identical helper in test_history_shortfall_graceful_abort.py
    so both files satisfy the NN1 spec-freeze guard the same way."""
    import inspect

    sig = inspect.signature(autotuner.run_autotuner)
    if "spec_bundle_id" not in sig.parameters:
        return {}
    from tests.autotuner.conftest import make_phase1_theory_bundle

    return {"spec_bundle_id": make_phase1_theory_bundle()}


def _assert_structured_abort_marker(result, *, case_label: str) -> None:
    """Shared shape assertion: the abort return must be a dict carrying
    aborted=True and a non-empty string reason -- never a bare None."""
    assert result is not None, (
        f"[{case_label}] run_autotuner returned a bare None. AC-4: every "
        f"graceful-abort path must return the structured "
        f"{{'aborted': True, 'reason': <str>}} marker, matching the one "
        f"path (the history-shortfall exception) that already does -- a "
        f"bare None is indistinguishable from a healthy 'ran, no changes' "
        f"result on the reporting.py render side."
    )
    assert isinstance(result, dict), (
        f"[{case_label}] run_autotuner's abort return ({result!r}) is not a "
        f"dict -- AC-4 requires the structured marker shape."
    )
    assert result.get("aborted") is True, (
        f"[{case_label}] run_autotuner's abort return ({result!r}) does not "
        f"carry aborted=True."
    )
    reason = result.get("reason")
    assert isinstance(reason, str) and reason, (
        f"[{case_label}] run_autotuner's abort return ({result!r}) does not "
        f"carry a non-empty string 'reason' -- the operator must be told "
        f"WHY the autotuner aborted."
    )


def test_run_autotuner_structured_marker_when_synthetic_history_is_empty(monkeypatch) -> None:
    """autotuner.py:2365-2367: generate_synthetic_history returning a falsy
    history_125d (no exception raised) must ALSO return the structured
    abort marker.

    MUST FAIL on current code: this path is a bare ``return`` (implicit
    None) today.
    """

    def _empty_history(*args, **kwargs):
        return {}

    monkeypatch.setattr(autotuner.synthetic_history, "generate_synthetic_history", _empty_history)

    result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"], **_spec_bundle_kwarg()
    )

    _assert_structured_abort_marker(result, case_label="empty_history_125d")


def test_run_autotuner_structured_marker_when_insufficient_days_for_wfa(monkeypatch) -> None:
    """autotuner.py:2375-2378: fewer than 2 trading days surviving the
    60/20/20 WFA split must ALSO return the structured abort marker.

    MUST FAIL on current code: this path is a bare ``return`` (implicit
    None) today.
    """

    def _one_day_history(*args, **kwargs):
        # history_125d is truthy (passes the "not history_125d" check at
        # :2365) but total_days collapses to 1 -- below the WFA floor of 2.
        return {"sym-A": {"2026-05-08": {"c": 100.0, "o": 100.0, "h": 100.0, "l": 100.0}}}

    monkeypatch.setattr(
        autotuner.synthetic_history, "generate_synthetic_history", _one_day_history
    )

    result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"], **_spec_bundle_kwarg()
    )

    _assert_structured_abort_marker(result, case_label="insufficient_days_for_wfa")


# ---------------------------------------------------------------------------
# AC-4 contract lock: all three abort trigger conditions share one shape.
# ---------------------------------------------------------------------------


def _configure_exception_abort(monkeypatch) -> None:
    """Path 1 (already fixed): generate_synthetic_history raises the named
    history-shortfall exception."""
    exc_type = autotuner.synthetic_history.HistoryShortfallError

    def _raise_shortfall(*args, **kwargs):
        raise exc_type("synthetic_history: persistent history shortfall")

    monkeypatch.setattr(
        autotuner.synthetic_history, "generate_synthetic_history", _raise_shortfall
    )


def _configure_empty_history_abort(monkeypatch) -> None:
    """Path 2: generate_synthetic_history returns a falsy history_125d."""

    def _empty_history(*args, **kwargs):
        return {}

    monkeypatch.setattr(autotuner.synthetic_history, "generate_synthetic_history", _empty_history)


def _configure_short_days_abort(monkeypatch) -> None:
    """Path 3: history_125d is truthy but total_days < 2."""

    def _one_day_history(*args, **kwargs):
        return {"sym-A": {"2026-05-08": {"c": 100.0, "o": 100.0, "h": 100.0, "l": 100.0}}}

    monkeypatch.setattr(
        autotuner.synthetic_history, "generate_synthetic_history", _one_day_history
    )


_ABORT_CASES = [
    pytest.param(_configure_exception_abort, "history_shortfall_exception", id="exception_path"),
    pytest.param(_configure_empty_history_abort, "empty_history_125d", id="empty_history_path"),
    pytest.param(_configure_short_days_abort, "insufficient_days_for_wfa", id="short_days_path"),
]


@pytest.mark.parametrize("configure_fn,case_label", _ABORT_CASES)
def test_all_three_abort_paths_share_the_same_marker_shape(
    configure_fn, case_label, monkeypatch
) -> None:
    """Contract lock: ALL THREE graceful-abort trigger conditions in
    run_autotuner must return the identical marker shape
    ({aborted: True, reason: <non-empty str>}) -- not just the one path that
    already does. This pins the contract so a future change to any single
    path cannot silently drift back to a bare None without breaking this
    test.
    """
    configure_fn(monkeypatch)

    result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"], **_spec_bundle_kwarg()
    )

    _assert_structured_abort_marker(result, case_label=case_label)


# ---------------------------------------------------------------------------
# AC-5 end-to-end: aborted vs genuine no-change render distinctly.
# ---------------------------------------------------------------------------


def _make_capturing_post():
    """Return (post_fn, captured) -- records every requests.post payload.
    Same pattern as test_history_shortfall_graceful_abort.py's local
    _capture_post closures."""
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
            status_code = 204

            def raise_for_status(self):
                return None

            def json(self):
                return {}

        return _Resp()

    return _post, captured


def _rendered_text(captured) -> str:
    text = ""
    for payload in captured:
        if not isinstance(payload, dict):
            continue
        for embed in payload.get("embeds", []):
            if isinstance(embed, dict):
                text += " " + str(embed.get("title", ""))
                text += " " + str(embed.get("description", ""))
    return text


@pytest.mark.parametrize("configure_fn,case_label", _ABORT_CASES)
def test_aborted_run_renders_distinctly_from_a_genuine_no_change_run(
    configure_fn, case_label, monkeypatch, tmp_path
) -> None:
    """AC-5 end-to-end: feed each abort path's run_autotuner return into
    reporting.send_eod_discord_post and assert it renders an explicit
    "Autotuner Aborted" notice carrying the reason -- distinct from a
    genuine no-change run's "No optimization changes".

    MUST FAIL on current code for the empty_history_path and
    short_days_path cases (their bare-None return renders as
    "No optimization changes", indistinguishable from a real no-op).
    """
    configure_fn(monkeypatch)

    abort_result = autotuner.run_autotuner(
        _minimal_bot_state(), "2026-05-10", ["acc-1"], **_spec_bundle_kwarg()
    )

    date_str = "2026-05-10"
    report_file = tmp_path / f"post_mortem_{date_str}.json"
    report_file.write_text(
        json.dumps({"summary": {"total_monitored": 0}, "triggers": []}), encoding="utf-8"
    )

    post_fn, captured = _make_capturing_post()
    monkeypatch.setattr(reporting.requests, "post", post_fn)

    reporting.send_eod_discord_post(
        date_str, str(report_file), abort_result, "https://discord.example/webhook/abort-e2e"
    )

    aborted_rendered = _rendered_text(captured).lower()
    assert "abort" in aborted_rendered, (
        f"[{case_label}] send_eod_discord_post did not render an explicit "
        f"'aborted' notice for run_autotuner's return ({abort_result!r}). "
        f"Rendered: {aborted_rendered!r}"
    )
    assert "no optimization changes" not in aborted_rendered, (
        f"[{case_label}] the abort rendered as the generic 'No optimization "
        f"changes' message -- indistinguishable from a genuine no-change "
        f"run. AC-5 requires a visibly distinct message. Rendered: "
        f"{aborted_rendered!r}"
    )

    # Companion: a genuine no-change run (optimization_results={}) must
    # render the ORIGINAL "No optimization changes" message and must NOT
    # say "Autotuner Aborted" -- pins the two states stay visibly distinct.
    post_fn_2, captured_2 = _make_capturing_post()
    monkeypatch.setattr(reporting.requests, "post", post_fn_2)
    reporting.send_eod_discord_post(
        date_str, str(report_file), {}, "https://discord.example/webhook/no-change-e2e"
    )
    no_change_rendered = _rendered_text(captured_2).lower()
    assert "no optimization changes" in no_change_rendered, (
        f"[{case_label}] genuine no-change run ({{}}) did not render the "
        f"expected 'No optimization changes' message. Rendered: "
        f"{no_change_rendered!r}"
    )
    assert "abort" not in no_change_rendered, (
        f"[{case_label}] genuine no-change run ({{}}) incorrectly rendered "
        f"an 'abort' notice. Rendered: {no_change_rendered!r}"
    )
