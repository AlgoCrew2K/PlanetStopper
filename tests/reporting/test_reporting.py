"""
Regression-pinning suite for ``reporting.py``.

Covers the six high-value surfaces identified in cycle C3a:
  1. generate_eod_snapshot Guard Alpha calculation (Stage 1)
  2. Shadow return uses frozen triggered_at_* fields when triggered=True
  3. send_discord_alert exit-reason title/color branching
  4. Conditional field injection (VWAP Bleed, VWAP Breakdown, Take-Profit)
  5. Discord 10-embed chunking (15 embeds → 2 POST calls)
  6. QuickChart POST payload is well-formed JSON with chart config

Mocking strategy
----------------
- ``requests.post`` is patched on the ``reporting`` module namespace via
  ``patch.object(reporting.requests, "post")`` — same pattern as
  ``test_execute_sell_to_cash.py``.
- ``time.sleep`` is patched to no-op throughout (prevents real sleeps on chunked
  send paths).
- ``database.normalize_name`` and ``database.get_symphony_strategy`` are patched
  to isolate reporting from SQLite entirely.
- Filesystem I/O in ``generate_eod_snapshot`` is patched via ``tmp_path`` (pytest
  built-in) combined with ``os.chdir``, OR via ``unittest.mock.patch`` on
  ``builtins.open`` / ``os.path.exists`` / ``json.dump`` as needed so tests
  never touch real disk outside tmp.

No live network calls are made. No ``@pytest.mark.live`` tests in this file.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import reporting

# ---------------------------------------------------------------------------
# Sentinel webhook / chart URLs — sentinel strings that must never reach a
# real HTTP stack. Tests assert the mock WAS called; they do not assert the
# URL value (that belongs to reporting.py's configuration, not to tests).
# ---------------------------------------------------------------------------
_SENTINEL_WEBHOOK = "https://discord.example.invalid/webhook/SENTINEL"
_SENTINEL_CHART_URL = "https://quickchart.example.invalid/chart/SENTINEL"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _db_stubs():
    """Return a pair of patches that stub database calls out of SQLite."""
    patch_normalize = patch(
        "reporting.database.normalize_name", side_effect=lambda n: n.strip().lower()
    )
    patch_get_strat = patch(
        "reporting.database.get_symphony_strategy",
        return_value={"params": {}, "locked_vars": {}},
    )
    return patch_normalize, patch_get_strat


def _make_post_mock(status_code=200, json_return=None):
    """Build a mock requests.Response-like object for requests.post return."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_return or {}
    return resp


# ===========================================================================
# 1. generate_eod_snapshot — Guard Alpha calculation
#    saved_pct = f_ret - live_ret
#    Three parametrized cases: gain (live < f_ret), loss (live > f_ret), tie.
# ===========================================================================

# Guard Alpha cases: (f_ret_pct, basket_current_pct, expected_sign_str)
# f_ret is the return locked at trigger time (triggered_at_return).
# When triggered_basket is empty and live_prices is None, live_ret falls back
# to sym["current_return"].  saved_pct = f_ret - live_ret.
_GUARD_ALPHA_CASES = [
    pytest.param(
        -2.0,  # f_ret  — frozen exit return (negative, bleed stopped)
        -4.5,  # live_ret — where price ended up without guard (worse)
        "positive",  # saved_pct = -2.0 - (-4.5) = +2.5  → gain case
        id="gain_live_worse_than_exit",
    ),
    pytest.param(
        -2.0,  # f_ret
        -0.5,  # live_ret — market recovered; guard alpha is negative
        "negative",  # saved_pct = -2.0 - (-0.5) = -1.5  → loss case
        id="loss_market_recovered_after_exit",
    ),
    pytest.param(
        -2.0,  # f_ret
        -2.0,  # live_ret — identical → tie
        "zero",  # saved_pct = 0.0
        id="tie_exact_equal",
    ),
]


@pytest.mark.parametrize("f_ret,live_ret,expected_sign", _GUARD_ALPHA_CASES)
def test_guard_alpha_calculation_sign_matches_scenario(
    f_ret, live_ret, expected_sign, tmp_path, monkeypatch
):
    """Stage 1: saved_pct = f_ret - live_ret is computed and written to the
    snapshot.  We assert the sign (positive/negative/zero) and that the key
    'saved_pct_guard_alpha' is present in the trigger record.

    Tolerance comment: float subtraction of two values that are themselves
    two-decimal fixture inputs; pytest.approx(rel=1e-6) is sufficient because
    there is no intermediate rounding in the path under test — the only rounding
    is the final ``round(saved_pct, 2)`` which is idempotent for these inputs.
    """
    sym_id = "SYM_ALPHA_TEST"
    bot_state = {
        sym_id: {
            "triggered": True,
            "triggered_at_return": f_ret,
            "triggered_at_stop": f_ret - 1.0,  # != f_ret so not Take-Profit
            "triggered_reason": "Trailing Stop",
            "current_return": live_ret,  # fallback used when no basket
            "current_value": 10_000.0,
            "triggered_basket_snapshot": [],  # empty → uses current_return
            "name": "Test Symphony",
            "account": "acct-001",
            "shadow_hwm": 3.5,
            "triggered_at_hwm": 3.5,
            "triggered_at_time": "15:32:00",
            "symphony_vol": 0.012,
        }
    }

    date_str = "2026-01-15"
    report_file = tmp_path / f"post_mortem_{date_str}.json"

    patch_norm, patch_strat = _db_stubs()

    # Point reporting at tmp_path so the file is isolated from the project root.
    # reporting.py now uses _POST_MORTEMS_DIR (absolute path), not CWD.
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

    with patch_norm, patch_strat:
        reporting.generate_eod_snapshot(
            bot_state,
            date_str,
            is_post_rebalance=False,
            discord_webhook_url=None,
            live_prices=None,
        )

    assert report_file.exists(), "Stage 1 snapshot file was not created"
    with open(report_file, encoding="utf-8") as fh:
        snapshot = json.load(fh)

    assert len(snapshot["triggers"]) == 1, "Expected exactly one trigger record"
    trigger = snapshot["triggers"][0]

    assert "saved_pct_guard_alpha" in trigger, (
        "Key 'saved_pct_guard_alpha' missing from trigger record"
    )

    actual_saved = trigger["saved_pct_guard_alpha"]
    expected_saved = round(f_ret - live_ret, 2)

    assert actual_saved == pytest.approx(expected_saved, rel=1e-6), (
        # rel=1e-6 because the values are already rounded to 2dp in the fixture;
        # floating-point subtraction error on two-decimal inputs is well below 1e-4.
        f"Guard Alpha mismatch: expected {expected_saved}, got {actual_saved}"
    )

    if expected_sign == "positive":
        assert actual_saved > 0, f"Expected positive guard alpha, got {actual_saved}"
    elif expected_sign == "negative":
        assert actual_saved < 0, f"Expected negative guard alpha, got {actual_saved}"
    else:  # tie
        assert actual_saved == pytest.approx(0.0, abs=1e-9), (
            f"Expected zero guard alpha for tie case, got {actual_saved}"
        )


# ===========================================================================
# 2. Shadow return uses frozen triggered_at_* fields when triggered=True
#    When triggered_basket_snapshot is non-empty and live_prices is provided,
#    the basket post-trigger move is computed; f_ret (triggered_at_return) is
#    the FROZEN value and must appear as exit_return in the snapshot.
# ===========================================================================


def test_shadow_return_uses_frozen_triggered_at_return_not_current(tmp_path, monkeypatch):
    """When a symphony has triggered=True and live_prices are provided, the
    Stage 1 snapshot must use triggered_at_return as the exit_return — the
    value frozen at the moment of trigger — NOT current_return.

    This pins the contract that post-trigger moves cannot retroactively change
    the recorded exit return.
    """
    frozen_exit_return = -3.0  # locked at trigger time
    current_return_drifted = -1.0  # current state — should NOT appear as exit_return

    ticker = "SPY"
    trigger_price = 400.0
    live_price = 380.0  # price moved down 5% after trigger

    # Basket with one holding; allocation 1.0 (100%)
    triggered_basket = [{"ticker": ticker, "allocation": 1.0, "price": trigger_price}]
    live_prices = {ticker: {"last_price": live_price}}

    bot_state = {
        "SYM_FROZEN": {
            "triggered": True,
            "triggered_at_return": frozen_exit_return,
            "triggered_at_stop": frozen_exit_return - 1.0,
            "triggered_reason": "Trailing Stop",
            "current_return": current_return_drifted,
            "current_value": 50_000.0,
            "triggered_basket_snapshot": triggered_basket,
            "name": "Frozen Symphony",
            "account": "acct-002",
            "shadow_hwm": 2.0,
            "triggered_at_hwm": 2.0,
            "triggered_at_time": "14:45:00",
            "symphony_vol": 0.008,
        }
    }

    date_str = "2026-01-16"
    report_file = tmp_path / f"post_mortem_{date_str}.json"

    patch_norm, patch_strat = _db_stubs()

    # Point reporting at tmp_path so the file is isolated from the project root.
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

    with patch_norm, patch_strat:
        reporting.generate_eod_snapshot(
            bot_state,
            date_str,
            is_post_rebalance=False,
            discord_webhook_url=None,
            live_prices=live_prices,
        )

    assert report_file.exists()
    with open(report_file, encoding="utf-8") as fh:
        snapshot = json.load(fh)

    assert len(snapshot["triggers"]) == 1
    trigger = snapshot["triggers"][0]

    # exit_return must equal the FROZEN triggered_at_return value
    assert trigger["exit_return"] == pytest.approx(frozen_exit_return, rel=1e-6), (
        # rel=1e-6: the only transformation is round(..., 2); fixture value is
        # already 2dp so no rounding error is possible.
        f"exit_return should be frozen value {frozen_exit_return}; "
        f"got {trigger['exit_return']}.  current_return ({current_return_drifted}) "
        "must not leak into exit_return."
    )

    # Sanity: the drifted current_return must NOT be the exit_return
    assert trigger["exit_return"] != pytest.approx(current_return_drifted, rel=1e-6), (
        "exit_return incorrectly uses current_return instead of triggered_at_return"
    )


# ===========================================================================
# 3. send_discord_alert — exit-reason title/color branching
# ===========================================================================

# Parametrize: (exit_reason, expected_title_fragment, expected_live_color)
# Colors are read from the production code path, not hardcoded aspirationally.
# We assert that the embed color for is_live=True matches the branch-specific
# constant in the function body.
_EXIT_REASON_CASES = [
    pytest.param(
        "Trailing Stop",
        # current_return > 0 → "Profit Locked" branch (no special exit_reason match)
        "Profit Locked",
        5763719,  # Green — current_return > 0 path
        {"current_return": 1.5},
        id="trailing_stop_profit",
    ),
    pytest.param(
        "Trailing Stop",
        "Bleed Stopped",
        15548997,  # Red — current_return < 0 path
        {"current_return": -2.0},
        id="trailing_stop_bleed",
    ),
    pytest.param(
        "VWAP Bleed Cut",
        "VWAP Bleed Protection",
        15548997,  # Red/Orange
        {"current_return": -1.0},
        id="vwap_bleed_cut",
    ),
    pytest.param(
        "VWAP Breakdown",
        "VWAP Breakdown Exit",
        15548997,  # Red/Orange
        {"current_return": -1.5},
        id="vwap_breakdown",
    ),
    pytest.param(
        "Take-Profit",
        "Smart Take-Profit Locked",
        5763719,  # Green
        {"current_return": 4.0},
        id="take_profit",
    ),
]


@pytest.mark.parametrize(
    "exit_reason,expected_title_fragment,expected_color,extra_kwargs",
    _EXIT_REASON_CASES,
)
def test_send_discord_alert_title_and_color_match_exit_reason(
    exit_reason, expected_title_fragment, expected_color, extra_kwargs
):
    """Pins that the embed title and color in the POST payload match the
    exit-reason branch in send_discord_alert.

    is_live=True is used so the live_color (not the dry-run override) fires.
    We assert title contains the expected fragment (not exact equality) so
    minor string decoration changes don't break the test; we assert exact
    integer color because color is a branch-selection outcome.
    """
    current_return = extra_kwargs.get("current_return", 0.0)

    with (
        patch.object(reporting.requests, "post") as mock_post,
        patch.object(reporting.time, "sleep"),
    ):
        mock_post.return_value = _make_post_mock()

        reporting.send_discord_alert(
            symphony_name="Test Symphony",
            current_return=current_return,
            prob_underperforming=55.0,
            stop_trigger_level=-5.0,
            high_water_mark=3.0,
            is_live=True,
            discord_webhook_url=_SENTINEL_WEBHOOK,
            exit_reason=exit_reason,
        )

    assert mock_post.called, "requests.post was not called — webhook not sent"

    call_kwargs = mock_post.call_args
    # The function calls requests.post(url, json=payload, timeout=...)
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert payload is not None, "POST payload json argument is missing"

    embeds = payload.get("embeds")
    assert embeds is not None and len(embeds) == 1, (
        f"Expected exactly 1 embed in payload; got {embeds}"
    )

    embed = embeds[0]
    title = embed.get("title", "")
    color = embed.get("color")

    assert expected_title_fragment in title, (
        f"Expected title fragment '{expected_title_fragment}' in '{title}'"
    )
    assert color == expected_color, (
        f"Expected color {expected_color} for exit_reason='{exit_reason}'; got {color}"
    )


# ===========================================================================
# 4. Conditional field injection
#    VWAP Bleed → "Bleed Protection" field present
#    VWAP Breakdown → "VWAP Breakdown Stats" field present
#    Take-Profit → "Take-Profit Threshold" field present
# ===========================================================================


@pytest.mark.parametrize(
    "exit_reason,extra_kwargs,expected_field_name",
    [
        pytest.param(
            "VWAP Bleed Cut",
            {
                "vwap_bleed_arm_pct": 0.5,
                "vwap_bleed_ticks": 3,
                "current_return": -1.0,
            },
            "Bleed Protection",
            id="vwap_bleed_active_injects_bleed_protection_field",
        ),
        pytest.param(
            "VWAP Breakdown",
            {
                "vwap_diff": -0.003,
                "vwap_breakdown_ticks": 4,
                "current_return": -2.0,
            },
            "VWAP Breakdown Stats",
            id="vwap_breakdown_injects_stats_field",
        ),
        pytest.param(
            "Take-Profit",
            {
                "tp_threshold": 70.0,
                "prob_underperforming": 75.0,
                "current_return": 3.5,
            },
            "Take-Profit Threshold",
            id="take_profit_injects_threshold_field",
        ),
    ],
)
def test_conditional_field_injected_when_feature_flag_active(
    exit_reason, extra_kwargs, expected_field_name
):
    """Pins that each optional feature flag injects its corresponding embed
    field.  Tests are negative-capable: if the branch condition is removed
    from production code the field will be absent and the assertion will fail.
    """
    prob_underperforming = extra_kwargs.pop("prob_underperforming", 55.0)
    current_return = extra_kwargs.pop("current_return", 0.0)

    with (
        patch.object(reporting.requests, "post") as mock_post,
        patch.object(reporting.time, "sleep"),
    ):
        mock_post.return_value = _make_post_mock()

        reporting.send_discord_alert(
            symphony_name="Test Symphony",
            current_return=current_return,
            prob_underperforming=prob_underperforming,
            stop_trigger_level=-5.0,
            high_water_mark=3.0,
            is_live=True,
            discord_webhook_url=_SENTINEL_WEBHOOK,
            exit_reason=exit_reason,
            **extra_kwargs,
        )

    assert mock_post.called

    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert payload is not None

    fields = payload["embeds"][0].get("fields", [])
    field_names = [f["name"] for f in fields]

    assert expected_field_name in field_names, (
        f"Expected field '{expected_field_name}' to be injected for "
        f"exit_reason='{exit_reason}'; found fields: {field_names}"
    )


@pytest.mark.parametrize(
    "exit_reason,minimal_kwargs,absent_field_name",
    [
        pytest.param(
            "Trailing Stop",
            {"current_return": -1.0},
            "Bleed Protection",
            id="no_bleed_flag_no_bleed_protection_field",
        ),
        pytest.param(
            "Trailing Stop",
            {"current_return": -1.0},
            "VWAP Breakdown Stats",
            id="no_breakdown_flag_no_breakdown_stats_field",
        ),
        pytest.param(
            "Trailing Stop",
            {"current_return": -1.0},
            "Take-Profit Threshold",
            id="no_tp_flag_no_tp_threshold_field",
        ),
    ],
)
def test_conditional_field_absent_when_feature_flag_not_set(
    exit_reason, minimal_kwargs, absent_field_name
):
    """Negative counterpart: flags not passed → corresponding field absent.
    This ensures the injection is genuinely conditional, not always-on.
    """
    current_return = minimal_kwargs.get("current_return", 0.0)

    with (
        patch.object(reporting.requests, "post") as mock_post,
        patch.object(reporting.time, "sleep"),
    ):
        mock_post.return_value = _make_post_mock()

        reporting.send_discord_alert(
            symphony_name="Test Symphony",
            current_return=current_return,
            prob_underperforming=55.0,
            stop_trigger_level=-5.0,
            high_water_mark=3.0,
            is_live=True,
            discord_webhook_url=_SENTINEL_WEBHOOK,
            exit_reason=exit_reason,
        )

    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    fields = payload["embeds"][0].get("fields", [])
    field_names = [f["name"] for f in fields]

    assert absent_field_name not in field_names, (
        f"Field '{absent_field_name}' should NOT be present when its feature "
        f"flag is not set, but was found in: {field_names}"
    )


# ===========================================================================
# 5. Discord 10-embed chunking
#    15 embeds → first batch of 10 (with file), then second batch of 5.
#    requests.post must be called exactly 2 times.
# ===========================================================================


def _make_optimization_results(n_symphonies):
    """Build a minimal optimization_results dict producing exactly n_symphonies
    optimization embeds.  Combined with the 1 fixed summary embed + the 1 fixed
    'no changes' embed that fires for each symphony with no actual changes,
    this lets us control total embed count precisely.

    To hit 15 total embeds:
      - 1 main summary embed (always present)
      - n_symphonies optimization embeds
    So n_symphonies = 14 → 15 total.
    """
    return {
        f"Symphony_{i}": {"_baseline_chosen": "baseline", f"param_{i}": {"old": 1, "new": 2}}
        for i in range(n_symphonies)
    }


def test_discord_chunking_15_embeds_calls_post_twice(tmp_path, monkeypatch):
    """When send_eod_discord_post produces 15 embeds, it must chunk into
    [10, 5] and call requests.post exactly twice.

    First call carries the file attachment (multipart/data), second is JSON.
    We assert call count (2) and embed counts per batch ([10, 5]).

    The 15 embeds are:
      - 1 main summary embed
      - 14 optimization embeds (one per symphony key in optimization_results)
    """
    date_str = "2026-01-17"
    report_file = tmp_path / f"post_mortem_{date_str}.json"

    # Redirect _POST_MORTEMS_DIR to tmp_path so the historical glob finds
    # our report file (not the real post_mortems/ directory).  This makes
    # the test self-contained and independent of leaked test artifacts.
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

    # Write a minimal valid report file so the function can open it
    minimal_report = {
        "summary": {"total_monitored": 2, "total_triggered": 1},
        "triggers": [],
        "tomorrow_target_holdings": {},
    }
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(minimal_report, fh)

    # 14 optimization symphonies → 1 summary + 14 optim = 15 embeds total
    optimization_results = _make_optimization_results(14)

    # QuickChart POST: first mock returns the chart URL; remaining calls are Discord
    quickchart_response = MagicMock()
    quickchart_response.json.return_value = {"url": _SENTINEL_CHART_URL}

    discord_response = MagicMock()
    discord_response.status_code = 200

    # requests.post is called: once for QuickChart (if dates_list is non-empty)
    # then once per Discord batch.
    # Since tmp_path has no post_mortem_*.json files beyond our one report file
    # (which has no triggers), dates_list will be populated with one entry of
    # zeros but will still be non-empty → QuickChart POST fires.
    # Call order: [quickchart, discord_batch_1, discord_batch_2]
    # We need to handle that the report file itself gets picked up by glob.

    with (
        patch.object(reporting.requests, "post") as mock_post,
        patch.object(reporting.time, "sleep"),
    ):
        mock_post.side_effect = [
            quickchart_response,  # QuickChart POST
            discord_response,  # Discord batch 1 (10 embeds)
            discord_response,  # Discord batch 2 (5 embeds)
        ]

        reporting.send_eod_discord_post(
            current_date_str=date_str,
            report_file=str(report_file),
            optimization_results=optimization_results,
            discord_webhook_url=_SENTINEL_WEBHOOK,
        )

    # Total POST calls: 1 QuickChart + 2 Discord batches = 3
    assert mock_post.call_count == 3, (
        f"Expected 3 POST calls (1 QuickChart + 2 Discord batches); got {mock_post.call_count}"
    )

    # Extract Discord calls (calls 1 and 2, 0-indexed)
    discord_call_1 = mock_post.call_args_list[1]
    discord_call_2 = mock_post.call_args_list[2]

    # First Discord call uses multipart (data= + files=); extract embed count
    # from the payload_json string
    call1_data = discord_call_1.kwargs.get("data") or discord_call_1[1].get("data")
    assert call1_data is not None, (
        "First Discord call should use multipart data= (with file attachment)"
    )
    batch1_embeds = json.loads(call1_data["payload_json"])["embeds"]
    assert len(batch1_embeds) == 10, (
        f"First Discord batch should have 10 embeds; got {len(batch1_embeds)}"
    )

    # Second Discord call uses json=
    call2_json = discord_call_2.kwargs.get("json") or discord_call_2[1].get("json")
    assert call2_json is not None, "Second Discord call should use json= (no file attachment)"
    batch2_embeds = call2_json["embeds"]
    assert len(batch2_embeds) == 5, (
        f"Second Discord batch should have 5 embeds; got {len(batch2_embeds)}"
    )


def test_discord_no_chunking_when_10_or_fewer_embeds(tmp_path, monkeypatch):
    """Boundary: exactly 1 optimization embed → 2 total embeds → single batch.
    requests.post is called exactly once for Discord (plus possibly one for
    QuickChart).  The Discord call must use the multipart form (first batch path).
    """
    date_str = "2026-01-18"
    report_file = tmp_path / f"post_mortem_{date_str}.json"

    # Redirect _POST_MORTEMS_DIR to tmp_path so the historical glob finds
    # our report file (not the real post_mortems/ directory).  This makes
    # the test self-contained and independent of leaked test artifacts.
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

    minimal_report = {
        "summary": {"total_monitored": 1, "total_triggered": 0},
        "triggers": [],
        "tomorrow_target_holdings": {},
    }
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(minimal_report, fh)

    optimization_results = _make_optimization_results(1)  # 1 summary + 1 optim = 2

    quickchart_response = MagicMock()
    quickchart_response.json.return_value = {"url": _SENTINEL_CHART_URL}
    discord_response = MagicMock()

    with (
        patch.object(reporting.requests, "post") as mock_post,
        patch.object(reporting.time, "sleep"),
    ):
        mock_post.side_effect = [
            quickchart_response,
            discord_response,
        ]

        reporting.send_eod_discord_post(
            current_date_str=date_str,
            report_file=str(report_file),
            optimization_results=optimization_results,
            discord_webhook_url=_SENTINEL_WEBHOOK,
        )

    # 1 QuickChart + 1 Discord = 2 calls total (no second Discord batch)
    assert mock_post.call_count == 2, (
        f"Expected 2 total POST calls (1 QuickChart + 1 Discord); got {mock_post.call_count}"
    )

    discord_call = mock_post.call_args_list[1]
    call_data = discord_call.kwargs.get("data") or discord_call[1].get("data")
    assert call_data is not None, (
        "Single Discord batch should still use multipart (first-batch path)"
    )
    batch_embeds = json.loads(call_data["payload_json"])["embeds"]
    assert len(batch_embeds) <= 10, (
        f"Single batch must never exceed 10 embeds; got {len(batch_embeds)}"
    )


# ===========================================================================
# 6. QuickChart POST payload is well-formed JSON with chart config
#    send_eod_discord_post POSTs to QuickChart with a json= body containing
#    a 'chart' key whose value has 'type', 'data', and 'options'.
# ===========================================================================


def test_quickchart_post_payload_is_well_formed(tmp_path, monkeypatch):
    """Pins that the QuickChart POST contains the required chart config
    structure: top-level 'chart' key with nested 'type', 'data' (containing
    'labels' and 'datasets'), and 'options'.  Also asserts 'width' and
    'height' are positive integers (shape assertion, not value assertion).

    We trigger this by placing a post_mortem file in tmp_path with at least
    one trigger so dates_list is non-empty and the QuickChart branch fires.
    """
    date_str = "2026-01-19"
    report_file = tmp_path / f"post_mortem_{date_str}.json"

    # Redirect _POST_MORTEMS_DIR to tmp_path so the historical glob picks up
    # the trigger-containing report file we create here.  Without this, the
    # scan uses the project-root post_mortems/ directory whose contents are
    # not guaranteed (especially on a fresh checkout), making the QuickChart
    # branch non-deterministically skipped.
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

    # Report with one trigger so the historical aggregation finds data
    report_with_trigger = {
        "summary": {"total_monitored": 1, "total_triggered": 1},
        "triggers": [
            {
                "symphony_name": "Test",
                "saved_pct_guard_alpha": 1.5,
                "saved_dollars": 150.0,
                "exit_reason": "Trailing Stop",
                "symphony_value": 10000.0,
            }
        ],
        "tomorrow_target_holdings": {},
    }
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(report_with_trigger, fh)

    quickchart_response = MagicMock()
    quickchart_response.json.return_value = {"url": _SENTINEL_CHART_URL}
    discord_response = MagicMock()

    captured_quickchart_kwargs = {}

    def _capture_post(url, **kwargs):
        if "quickchart" in url:
            captured_quickchart_kwargs.update(kwargs)
            return quickchart_response
        return discord_response

    with (
        patch.object(reporting.requests, "post", side_effect=_capture_post),
        patch.object(reporting.time, "sleep"),
    ):
        reporting.send_eod_discord_post(
            current_date_str=date_str,
            report_file=str(report_file),
            optimization_results=None,
            discord_webhook_url=_SENTINEL_WEBHOOK,
        )

    assert captured_quickchart_kwargs, (
        "QuickChart POST was not captured — either it was not called or URL "
        "matching failed.  Check that production code still POSTs to a URL "
        "containing 'quickchart'."
    )

    qc_json = captured_quickchart_kwargs.get("json")
    assert qc_json is not None, "QuickChart POST must use json= kwarg (not data= or params=)"

    # Top-level structure
    assert "chart" in qc_json, "QuickChart payload missing top-level 'chart' key"
    assert "width" in qc_json, "QuickChart payload missing 'width'"
    assert "height" in qc_json, "QuickChart payload missing 'height'"

    assert isinstance(qc_json["width"], int) and qc_json["width"] > 0, (
        "QuickChart 'width' must be a positive integer"
    )
    assert isinstance(qc_json["height"], int) and qc_json["height"] > 0, (
        "QuickChart 'height' must be a positive integer"
    )

    chart = qc_json["chart"]
    assert "type" in chart, "chart config missing 'type'"
    assert "data" in chart, "chart config missing 'data'"
    assert "options" in chart, "chart config missing 'options'"

    data = chart["data"]
    assert "labels" in data, "chart.data missing 'labels'"
    assert "datasets" in data, "chart.data missing 'datasets'"
    assert isinstance(data["datasets"], list) and len(data["datasets"]) > 0, (
        "chart.data.datasets must be a non-empty list"
    )


# ===========================================================================
# Edge cases / guard clauses
# ===========================================================================


def test_send_discord_alert_no_op_when_webhook_url_is_none():
    """send_discord_alert must return immediately without calling requests.post
    when discord_webhook_url is None.  Prevents crashes during dry-run cycles
    where no webhook is configured.
    """
    with patch.object(reporting.requests, "post") as mock_post:
        reporting.send_discord_alert(
            symphony_name="Test",
            current_return=1.0,
            prob_underperforming=60.0,
            stop_trigger_level=-5.0,
            high_water_mark=3.0,
            is_live=False,
            discord_webhook_url=None,
        )

    mock_post.assert_not_called()


def test_send_eod_discord_post_no_op_when_webhook_url_is_none(tmp_path):
    """send_eod_discord_post must return immediately when discord_webhook_url
    is None.
    """
    date_str = "2026-01-20"
    report_file = tmp_path / f"post_mortem_{date_str}.json"
    with open(report_file, "w") as fh:
        json.dump({"summary": {}, "triggers": []}, fh)

    with patch.object(reporting.requests, "post") as mock_post:
        reporting.send_eod_discord_post(
            current_date_str=date_str,
            report_file=str(report_file),
            optimization_results=None,
            discord_webhook_url=None,
        )

    mock_post.assert_not_called()


def test_generate_eod_snapshot_stage1_idempotent(tmp_path, monkeypatch):
    """Stage 1 must not overwrite an existing snapshot (the guard
    ``if os.path.exists(report_file): return`` protects production state).
    """
    # Redirect _POST_MORTEMS_DIR to tmp_path so generate_eod_snapshot reads
    # and writes within the test's temp directory.  Without this, the
    # idempotency guard checks the real project post_mortems/ directory and
    # the test accidentally leaks files to disk.
    monkeypatch.setattr(reporting, "_POST_MORTEMS_DIR", str(tmp_path))

    date_str = "2026-01-21"
    report_file = tmp_path / f"post_mortem_{date_str}.json"

    sentinel_content = {"sentinel": True, "triggers": []}
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(sentinel_content, fh)

    bot_state = {
        "SYM": {
            "triggered": False,
            "name": "Test",
            "account": "acct-001",
        }
    }

    patch_norm, patch_strat = _db_stubs()

    with patch_norm, patch_strat:
        reporting.generate_eod_snapshot(
            bot_state,
            date_str,
            is_post_rebalance=False,
            discord_webhook_url=None,
            live_prices=None,
        )

    with open(report_file, encoding="utf-8") as fh:
        content = json.load(fh)

    assert content.get("sentinel") is True, (
        "Stage 1 overwrote an existing snapshot — idempotency guard is broken"
    )
