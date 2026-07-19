import glob
import json
import logging
import os
import time
from datetime import datetime

import requests

import database

# Canonical post-mortem directory — anchored to project root regardless of CWD.
_POST_MORTEMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "post_mortems")

# Stage-1 snapshot-basis cutoff (ET time-of-day): Stage 1 fires in the 15:54 ET
# minute (rebalance-blackout schedule), and the $-saved panel declares a
# "snapshot-time basis". The engine keeps ticking past close (~16:04), so an
# off-schedule Stage-1 run (manual regeneration, late daemon) would otherwise
# silently re-base if-held onto EOD rows. One basis everywhere: this value must
# match SNAPSHOT_CUTOFF_ET in scripts/regenerate_post_mortems.py (the historical
# repair tool), which was verified against the 2026-07-09 audit ground truth.
STAGE1_SNAPSHOT_CUTOFF_ET = "15:54:59"


def generate_eod_snapshot(
    bot_state, current_date_str, is_post_rebalance=False, discord_webhook_url=None, live_prices=None
):
    """Generates a two-stage daily post-mortem JSON snapshot and handles Discord alerts."""
    os.makedirs(_POST_MORTEMS_DIR, exist_ok=True)
    report_file = os.path.join(_POST_MORTEMS_DIR, f"post_mortem_{current_date_str}.json")

    if not is_post_rebalance:
        # STAGE 1 (15:54 ET): Freeze Math & Shadow Returns
        if os.path.exists(report_file):
            return

        print(f"  -> Generating Stage 1 Post-Mortem (Locking Math): {report_file}")

        report = {
            "date": current_date_str,
            "summary": {
                "total_monitored": 0,
                "total_triggered": 0,
                "positive_guard_alpha_count": 0,
            },
            "tomorrow_target_holdings": {"STATUS": "Pending Composer Rebalance"},
            "triggers": [],
        }

        for sym_id, sym in bot_state.items():
            if not isinstance(sym, dict):
                continue

            report["summary"]["total_monitored"] += 1

            if sym.get("triggered"):
                report["summary"]["total_triggered"] += 1

                f_ret = sym.get("triggered_at_return", 0.0)

                # Source if-held from the shadow_history TABLE (latest row for this
                # symphony+day at/before the snapshot cutoff) — the data-phase truth
                # from Composer. bot_state's current_return must NOT be trusted
                # here: the action phase clobbers it every cycle with the
                # frozen-basket reconstruction (alpha_bot_execution.py TRUE SHADOW
                # RETURN OVERRIDE), which collapses to ~f_ret on basket misses
                # (booking exactly $0 saved) and fabricates values otherwise — 7 of
                # 11 audited days sign-flipped (VERDICT-droplet 2026-07-09 Finding
                # 2; the #80 comment claimed this sourcing but never queried the
                # table). The cutoff holds the declared snapshot basis on
                # off-schedule runs.
                shadow_row = database.load_latest_shadow_row(
                    sym_id, current_date_str, et_cutoff=STAGE1_SNAPSHOT_CUTOFF_ET
                )
                if_held_source = "shadow_history"
                if shadow_row is None or shadow_row.get("current_return") is None:
                    # All-post-cutoff day (daemon started after the cutoff): the
                    # earliest row of the day is nearest the declared basis —
                    # real off-basis shadow data beats the clobbered bot_state
                    # value. Distinct marker keeps the off-basis booking
                    # auditable.
                    shadow_row = database.load_earliest_shadow_row(sym_id, current_date_str)
                    if_held_source = "shadow_history_post_cutoff"
                if shadow_row is not None and shadow_row.get("current_return") is not None:
                    live_ret = float(shadow_row["current_return"])
                else:
                    # Zero shadow rows for the (symphony, day) — the ONLY case
                    # bot_state may be trusted.
                    live_ret = sym.get("current_return", 0.0)
                    if_held_source = "bot_state_fallback"
                saved_pct = f_ret - live_ret

                sym_val = sym.get("current_value", 0.0)
                saved_dollars = sym_val * (saved_pct / 100.0) if sym_val > 0 else 0.0

                if saved_pct > 0:
                    report["summary"]["positive_guard_alpha_count"] += 1

                if f_ret == sym.get("triggered_at_stop"):
                    exit_reason = "Take-Profit"
                elif sym.get("triggered_reason"):
                    exit_reason = sym.get("triggered_reason")
                else:
                    exit_reason = "Trailing Stop"

                # Fetch strategy parameters for this specific symphony
                raw_name = sym.get("name", "Unknown")
                normalized_name = database.normalize_name(raw_name)
                strat = database.get_symphony_strategy(normalized_name)
                params = strat.get("params", {})

                report["triggers"].append(
                    {
                        "symphony_name": sym.get("name", "Unknown"),
                        "symphony_value": round(sym_val, 2),
                        "account_id": sym.get("account", "Unknown"),
                        "exit_reason": exit_reason,
                        "exit_return": round(f_ret, 2),
                        "attempted_trigger_level": round(sym.get("triggered_at_stop", 0.0), 2),
                        "shadow_return": round(live_ret, 2),
                        "shadow_hwm": round(sym.get("shadow_hwm", 0.0), 2),
                        "saved_pct_guard_alpha": round(saved_pct, 2),
                        "saved_dollars": round(saved_dollars, 2),
                        # Money-math provenance: "shadow_history" (table truth) or
                        # "bot_state_fallback" (row-less degradation) — no silent
                        # source switching (contract: test_postmortem_if_held_
                        # shadow_history_source.py).
                        "if_held_source": if_held_source,
                        "hwm_at_trigger": round(sym.get("triggered_at_hwm", 0.0), 2),
                        "time_triggered": sym.get("triggered_at_time", ""),
                        "symphony_vol": round(sym.get("symphony_vol", 0.0), 2),
                        "strategy_params": params,
                        "next_day_holdings": ["Pending..."],
                    }
                )

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)

    else:
        # STAGE 2 (16:00 ET): Inject Tomorrow's Holdings and Fix Final Math
        if not os.path.exists(report_file):
            print("  -> Warning: Stage 1 snapshot missing. Cannot inject new holdings.")
            return

        with open(report_file, encoding="utf-8") as f:
            report = json.load(f)

        if "STATUS" not in report.get("tomorrow_target_holdings", {}):
            return

        print(
            f"  -> Generating Stage 2 Post-Mortem (Injecting Holdings & Correcting EOD Alpha): {report_file}"  # noqa: E501  # un-wrappable long line
        )

        portfolio_holdings_summary = {}

        for sym_id, sym in bot_state.items():
            if not isinstance(sym, dict):
                continue

            sym_holdings = [h.get("ticker") for h in sym.get("current_holdings", [])]

            for trigger in report.get("triggers", []):
                if trigger.get("symphony_name") == sym.get("name") and trigger.get(
                    "account_id"
                ) == sym.get("account"):
                    trigger["next_day_holdings"] = sym_holdings

            for holding in sym.get("current_holdings", []):
                ticker = holding.get("ticker", "UNKNOWN")
                weight = holding.get("allocation", 0.0)
                if ticker not in portfolio_holdings_summary:
                    portfolio_holdings_summary[ticker] = 0.0
                portfolio_holdings_summary[ticker] += weight

        pos_alpha_count = sum(
            1 for t in report.get("triggers", []) if t.get("saved_pct_guard_alpha", 0) > 0
        )
        report["summary"]["positive_guard_alpha_count"] = pos_alpha_count

        sorted_holdings = dict(
            sorted(portfolio_holdings_summary.items(), key=lambda item: item[1], reverse=True)
        )
        report["tomorrow_target_holdings"] = sorted_holdings

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)

        # We no longer send the Discord push directly from here.
        # It is handled by send_eod_discord_post() after the autotuner completes.


def send_eod_discord_post(current_date_str, report_file, optimization_results, discord_webhook_url):
    """Sends the finalized EOD report to Discord, including a multi-timeframe summary, historical chart, and optimization changes."""  # noqa: E501  # un-wrappable long line
    if not discord_webhook_url:
        return

    print("  -> Pushing EOD Snapshot to Discord...")
    try:
        if not os.path.exists(report_file):
            print(f"  -> Error: Report file {report_file} not found.")
            return

        with open(report_file, encoding="utf-8") as f:
            report = json.load(f)

        total_monitored = report.get("summary", {}).get("total_monitored", 0)
        triggers = report.get("triggers", [])
        total_triggered = len(triggers)

        # 1. Time Series Data Extraction for Chart
        dates_list = []
        alpha_list = []
        saved_list = []
        win_rate_list = []

        all_pm_files = sorted(glob.glob(os.path.join(_POST_MORTEMS_DIR, "post_mortem_*.json")))
        # Limit to last 45 days if the list is getting too long
        chart_files = all_pm_files[-45:] if len(all_pm_files) > 45 else all_pm_files

        for f_path in chart_files:
            try:
                d_str = os.path.basename(f_path).replace("post_mortem_", "").replace(".json", "")
                with open(f_path, encoding="utf-8") as f:
                    day_data = json.load(f)

                day_triggers = day_data.get("triggers", [])
                t_count = len(day_triggers)

                if t_count > 0:
                    d_alpha = (
                        sum(t.get("saved_pct_guard_alpha", 0.0) for t in day_triggers) / t_count
                    )
                    d_saved = sum(t.get("saved_dollars", 0.0) for t in day_triggers)
                    d_wins = sum(1 for t in day_triggers if t.get("saved_pct_guard_alpha", 0.0) > 0)
                    d_win_rate = (d_wins / t_count) * 100.0
                else:
                    d_alpha = 0.0
                    d_saved = 0.0
                    d_win_rate = 0.0

                dates_list.append(d_str)
                alpha_list.append(round(d_alpha, 2))
                saved_list.append(round(d_saved, 2))
                win_rate_list.append(round(d_win_rate, 1))
            except (OSError, json.JSONDecodeError):
                continue

        # 2. QuickChart API POST Request
        chart_url = None
        if dates_list:
            chart_config = {
                "type": "line",
                "data": {
                    "labels": dates_list,
                    "datasets": [
                        {
                            "label": "Avg Guard Alpha (%)",
                            "borderColor": "#10b981",  # emerald
                            "data": alpha_list,
                            "yAxisID": "y",
                            "fill": False,
                        },
                        {
                            "label": "Win Rate (%)",
                            "borderColor": "#3b82f6",  # blue
                            "borderDash": [5, 5],
                            "data": win_rate_list,
                            "yAxisID": "y",
                            "fill": False,
                        },
                        {
                            "label": "Daily Saved ($)",
                            "type": "bar",
                            "backgroundColor": "rgba(245, 158, 11, 0.5)",  # goldenrod/amber
                            "data": saved_list,
                            "yAxisID": "y1",
                        },
                    ],
                },
                "options": {
                    "scales": {
                        "yAxes": [
                            {"id": "y", "position": "left", "ticks": {"fontColor": "#cbd5e1"}},
                            {
                                "id": "y1",
                                "position": "right",
                                "gridLines": {"display": False},
                                "ticks": {"fontColor": "#f59e0b"},
                            },
                        ],
                        "xAxes": [{"ticks": {"fontColor": "#cbd5e1"}}],
                    },
                    "legend": {"labels": {"fontColor": "#cbd5e1"}},
                },
            }

            try:
                resp = requests.post(
                    "https://quickchart.io/chart/create",
                    json={
                        "chart": chart_config,
                        "width": 800,
                        "height": 400,
                        "backgroundColor": "#1e293b",
                    },
                    timeout=10,
                )
                chart_url = resp.json().get("url")
            except (requests.RequestException, ValueError) as e:
                # requests.post raises RequestException; resp.json() raises ValueError on bad JSON
                logging.warning(
                    "send_eod_discord_post: QuickChart request failed, chart will be omitted: %s", e
                )

        # Multi-Timeframe Performance Stats (1d, 7d, 30d)
        windows = [1, 7, 30]

        # Initialize stats for each window
        stats = {
            w: {
                "total_saved": 0.0,
                "total_value": 0.0,
                "total_alpha": 0.0,
                "trigger_count": 0,
                "wins": 0,
                "by_reason": {},
            }
            for w in windows
        }

        all_reasons = set()

        try:
            end_date = datetime.strptime(current_date_str, "%Y-%m-%d")
            files = glob.glob(os.path.join(_POST_MORTEMS_DIR, "post_mortem_*.json"))

            for f_path in files:
                try:
                    date_part = (
                        os.path.basename(f_path).replace("post_mortem_", "").replace(".json", "")
                    )
                    file_date = datetime.strptime(date_part, "%Y-%m-%d")
                    delta_days = (end_date - file_date).days

                    if delta_days < 0:
                        continue  # Skip future files

                    active_windows = [w for w in windows if delta_days <= w]
                    if not active_windows:
                        continue

                    with open(f_path, encoding="utf-8") as f:
                        data = json.load(f)

                    for t in data.get("triggers", []):
                        alpha_pct = t.get("saved_pct_guard_alpha", 0.0)
                        dollars = t.get("saved_dollars", 0.0)
                        sym_val = t.get("symphony_value", 0.0)
                        reason = t.get("exit_reason", "Unknown")
                        all_reasons.add(reason)

                        for w in active_windows:
                            ws = stats[w]
                            ws["total_saved"] += dollars
                            ws["total_value"] += sym_val
                            ws["total_alpha"] += alpha_pct
                            ws["trigger_count"] += 1
                            if alpha_pct > 0:
                                ws["wins"] += 1

                            if reason not in ws["by_reason"]:
                                ws["by_reason"][reason] = {
                                    "saved": 0.0,
                                    "value": 0.0,
                                    "alpha": 0.0,
                                    "count": 0,
                                    "wins": 0,
                                }

                            rs = ws["by_reason"][reason]
                            rs["saved"] += dollars
                            rs["value"] += sym_val
                            rs["alpha"] += alpha_pct
                            rs["count"] += 1
                            if alpha_pct > 0:
                                rs["wins"] += 1
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
        except ValueError as e:
            # datetime.strptime on current_date_str raises ValueError; inner per-file errors caught above  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
            logging.warning(
                "send_eod_discord_post: history stats parse error, stats will be zeroed: %s", e
            )

        # 1. Main Summary Embed
        desc_lines = [
            f"**Total Monitored:** {total_monitored}",
            f"**Total Triggered:** {total_triggered}\n",
        ]

        for w in windows:
            ws = stats[w]
            avg_alpha = (
                (ws["total_alpha"] / ws["trigger_count"]) if ws["trigger_count"] > 0 else 0.0
            )
            win_rate = (
                (ws["wins"] / ws["trigger_count"] * 100.0) if ws["trigger_count"] > 0 else 0.0
            )

            desc_lines.append(f"**📅 {w}-Day Performance Summary**")
            desc_lines.append(f"• **Avg Guard Alpha:** {avg_alpha:+.2f}%")
            desc_lines.append(f"• **Total Saved:** ${ws['total_saved']:+,.2f}")
            desc_lines.append(
                f"• **Win Rate:** {win_rate:.0f}% ({ws['wins']}/{ws['trigger_count']})"
            )

            # Nested Reason Breakdown for this specific window
            breakdown_parts = []
            for reason in sorted(list(all_reasons)):
                rs = ws["by_reason"].get(reason)
                if rs and rs["count"] > 0:
                    r_alpha = (rs["alpha"] / rs["count"]) if rs["count"] > 0 else 0.0
                    # FIXED: Replaced markdown hyphen with literal spaces and unicode hollow circle
                    breakdown_parts.append(
                        f"    ◦ {reason}: {r_alpha:+.2f}% ({rs['wins']}/{rs['count']})"
                    )

            if breakdown_parts:
                desc_lines.append("• **Breakdown by Reason:**")
                desc_lines.extend(breakdown_parts)

            desc_lines.append("")  # Spacer between windows

        main_desc = "\n".join(desc_lines)
        if len(main_desc) > 4096:
            main_desc = main_desc[:4093] + "..."

        embeds = [
            {
                "title": f"📊 Planet Stopper EOD Analysis ({current_date_str})",
                "color": 3447003,
                "description": main_desc,
                "footer": {"text": "End of Day Post-Mortem"},
            }
        ]

        if chart_url:
            embeds[0]["image"] = {"url": chart_url}

        # 2. Symphony Optimization Embeds (Delta-Only)
        # run_autotuner returns a reason-carrying abort marker on a graceful
        # history-shortfall abort — render it as an explicit operator notice
        # rather than iterating it as per-symphony optimization results.
        if isinstance(optimization_results, dict) and optimization_results.get("aborted"):
            embeds.append(
                {
                    "title": "Autotuner Aborted",
                    "description": (
                        "Autotuner aborted — "
                        f"{optimization_results.get('reason', 'history shortfall')}"
                    ),
                    "color": 15158332,  # red — an aborted optimisation run
                }
            )
        elif optimization_results:
            for sym_name, changes in optimization_results.items():
                sym_changes_text = ""
                baseline_text = ""
                selection_stats = None
                if changes:
                    if "_baseline_chosen" in changes:
                        baseline_text = f"**Decision:** {changes['_baseline_chosen']}\n\n"

                    if "_selection_stats" in changes:
                        selection_stats = changes["_selection_stats"]

                    for var, vals in changes.items():
                        if var in ("_baseline_chosen", "_selection_stats"):
                            continue
                        # Shape guard (F-015): only real {old,new} delta dicts belong here.
                        # Other entries -- e.g. the eval_window_days stats block autotuner.py
                        # unconditionally writes into every symphony's changes dict -- are not
                        # deltas and must be skipped, not indexed, so one malformed sibling
                        # entry can't crash the render for every symphony.
                        if not (isinstance(vals, dict) and "old" in vals and "new" in vals):
                            continue
                        # Delta-Only Filter: Only add to string if the value actually changed
                        if vals["old"] != vals["new"]:
                            sym_changes_text += f"- `{var}`: {vals['old']} -> {vals['new']}\n"

                if not sym_changes_text:
                    sym_changes_text = "✅ Optimal parameters retained."

                if selection_stats is not None:

                    def _fmt(v):
                        return f"{v:.4f}" if v is not None else "N/A"

                    # naive = raw Optuna best Sortino; selection t-stat = Harvey & Liu
                    # haircut winner's Sortino·sqrt(T); frozen-eval = held-out Sortino.
                    selection_line = (
                        f"\nSortino (naive / selection t-stat / frozen-eval): "
                        f"{_fmt(selection_stats.get('naive_sharpe'))} / "
                        f"{_fmt(selection_stats.get('selection_tstat'))} / "
                        f"{_fmt(selection_stats.get('frozen_eval_sharpe'))}"
                    )
                    sym_changes_text += selection_line

                embeds.append(
                    {
                        "title": f"⚙️ {sym_name.title()} Optimization",
                        "color": 10181046,
                        "description": baseline_text + sym_changes_text,
                    }
                )
        else:
            embeds.append(
                {
                    "title": "⚙️ Optimization",
                    "color": 10181046,
                    "description": "No optimization changes.",
                }
            )

        # Discord enforces a strict limit of 10 embeds per webhook message.
        # Chunk the embeds into batches of 10 to ensure all data is sent.

        with open(report_file, "rb") as f:
            file_data = f.read()

        # Send the first batch (which includes the EOD JSON file attachment)
        first_batch = embeds[:10]
        payload_data = {"payload_json": json.dumps({"embeds": first_batch})}
        files_payload = {"file": (report_file, file_data, "application/json")}
        requests.post(discord_webhook_url, data=payload_data, files=files_payload, timeout=10)

        # Loop through and send any remaining batches (no file attached)
        for i in range(10, len(embeds), 10):
            time.sleep(1.5)  # Pause briefly to respect Discord's rate limits
            next_batch = embeds[i : i + 10]
            requests.post(discord_webhook_url, json={"embeds": next_batch}, timeout=10)

        print("  -> Discord Push Complete.")

    except (OSError, ValueError, requests.RequestException, TypeError, KeyError) as e:
        # OSError: file I/O; ValueError: json.load/strptime/JSONDecodeError; RequestException: Discord POST; TypeError: json.dumps non-serializable; KeyError: malformed changes-dict entry the shape guard above didn't anticipate  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
        logging.error("send_eod_discord_post: failed to send EOD webhook: %s", e)


def send_discord_alert(
    symphony_name,
    current_return,
    prob_underperforming,
    stop_trigger_level,
    high_water_mark,
    is_live,
    discord_webhook_url,
    exit_reason="Trailing Stop",
    vwap_bleed_arm_pct=None,
    vwap_bleed_ticks=None,
    vwap_diff=None,
    vwap_breakdown_ticks=None,
    tp_threshold=None,
    vwap_bleed_multiplier=None,
    symphony_vol=None,
):
    if not discord_webhook_url:
        return

    if exit_reason == "Take-Profit":
        base_title = "🎯 Smart Take-Profit Locked"
        live_color = 5763719  # Green
    elif exit_reason == "VWAP Breakdown":
        base_title = "📉 VWAP Breakdown Exit"
        live_color = 15548997  # Red/Orange
    elif exit_reason == "VWAP Bleed Cut":
        base_title = "🩸 VWAP Bleed Protection"
        live_color = 15548997  # Red/Orange
    elif current_return > 0:
        base_title = "✅ Profit Locked"
        live_color = 5763719
    elif current_return < 0:
        base_title = "🛑 Bleed Stopped"
        live_color = 15548997
    else:
        base_title = "🛡️ Breakeven Locked"
        live_color = 3447003

    title = f"{base_title}: {exit_reason} Triggered" if is_live else f"⚠️ [DRY RUN] {base_title}"
    color = live_color if is_live else 16766720
    action_text = (
        "Executed 'Sell to Cash' via API. Trade queued for Composer execution window."
        if is_live
        else "Bypassed (Dry Run Mode)"
    )

    # prob_underperforming is None when MC history was insufficient (the
    # out-of-band insufficient sentinel). Render it safely rather than crashing
    # the alert.
    mc_prob_text = f"{prob_underperforming:.1f}%" if prob_underperforming is not None else "N/A"

    fields = [
        {"name": "Symphony", "value": symphony_name, "inline": True},
        {"name": "Exit Return", "value": f"{current_return:.2f}%", "inline": True},
        {"name": "High Water Mark", "value": f"{high_water_mark:.2f}%", "inline": True},
        {"name": "Stop Level", "value": f"{stop_trigger_level:.2f}%", "inline": True},
        {"name": "MC Probability", "value": mc_prob_text, "inline": True},
        {"name": "Action Taken", "value": action_text, "inline": False},
    ]

    if exit_reason == "VWAP Bleed Cut" and vwap_bleed_arm_pct is not None:
        bleed_val = f"Threshold: `{vwap_bleed_arm_pct}%`"
        if vwap_bleed_multiplier is not None and symphony_vol is not None:
            bleed_val += f" (Vol: `{symphony_vol:.2f}` × `{vwap_bleed_multiplier}`)"
        bleed_val += f" | Persistence: `{vwap_bleed_ticks}` ticks"
        fields.append({"name": "Bleed Protection", "value": bleed_val, "inline": False})

    if exit_reason == "VWAP Breakdown" and vwap_diff is not None:
        fields.append(
            {
                "name": "VWAP Breakdown Stats",
                "value": f"VWAP Diff: {vwap_diff * 100:.2f}% | Ticks Below: {vwap_breakdown_ticks}",
                "inline": False,
            }
        )

    if exit_reason == "Take-Profit" and tp_threshold is not None:
        fields.append(
            {
                "name": "Take-Profit Threshold",
                "value": f"MC Prob Reached: {mc_prob_text} >= {tp_threshold}%",
                "inline": False,
            }
        )

    payload = {
        "embeds": [
            {
                "title": title,
                "color": color,
                "fields": fields,
                "footer": {"text": "Alpha Bot • Hybrid Defense Protocol"},
            }
        ]
    }
    requests.post(discord_webhook_url, json=payload, timeout=10)
