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

                # Source if-held from shadow_history.current_return (the engine's live
                # trajectory), recorded accurately post-trigger by alpha_bot_execution.py.
                # The basket reconstruction (triggered_basket_snapshot + live_prices) collapsed
                # to ~f_ret when basket prices were frozen at exit level, producing ~$0 saved
                # despite large actual divergence. Diagnosis: a7601fb / guard-alpha-saved-diagnosis.md.
                live_ret = sym.get("current_return", 0.0)
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


def build_sleeves_digest_section(sleeve_summaries: list[dict]) -> str:
    """Pure, never-raising EOD digest formatter for Managed Sleeves (AC-17).

    sleeve_summaries: [{"name": str, "status": str, "rules": [{"name": str,
    "today_fires": int, "lifetime_fires": int, "realized_pnl_usd": float,
    "benched": bool}, ...]}, ...]. Returns "" (falsy) for an empty list -- no
    phantom section when no sleeves exist. Every optional key is read
    defensively so a minimal/incomplete summary dict degrades gracefully
    rather than raising -- this is an EOD report, not a critical path.
    """
    if not sleeve_summaries:
        return ""

    lines = ["**Managed Sleeves**"]
    for summary in sleeve_summaries:
        if not isinstance(summary, dict):
            continue
        name = summary.get("name") or "unnamed sleeve"
        status = summary.get("status") or "?"
        lines.append(f"• **{name}** ({status})")
        for rule in summary.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            rule_name = rule.get("name") or "unnamed rule"
            today_fires = rule.get("today_fires", 0) or 0
            lifetime_fires = rule.get("lifetime_fires", 0) or 0
            # A non-numeric realized value (None = the attribution fold
            # failed upstream) renders as an explicit "n/a" -- never a dollar
            # figure fabricated from a placeholder (audit finding #8: "$+0.00"
            # is a value claim).
            realized_pnl = rule.get("realized_pnl_usd", 0.0)
            realized_txt = (
                f"${realized_pnl:+,.2f}"
                if isinstance(realized_pnl, (int, float)) and not isinstance(realized_pnl, bool)
                else "n/a"
            )
            benched_note = " — BENCHED" if rule.get("benched") else ""
            lines.append(
                f"    ◦ {rule_name}: today {today_fires} · lifetime {lifetime_fires} "
                f"· realized {realized_txt}{benched_note}"
            )
    return "\n".join(lines)


def _build_sleeve_digest_summaries(current_date_str: str) -> list[dict]:
    """Assemble sleeve_summaries for build_sleeves_digest_section from live DB
    rows (AC-17). Never raises -- a per-sleeve/per-rule read failure omits
    that sleeve/rule rather than crashing the EOD scheduler thread.

    realized_pnl_usd is derived from the SAME fold as the dashboard panel
    (sleeves.ledger.attribute_realized_fills, buy-side attribution -- audit
    finding #8 pinned panel/digest agreement), so the two operator surfaces
    can never disagree on the same quantity. On fold failure the value is
    None and the formatter renders "n/a" -- never a fabricated $0.00. benched
    stays an honest placeholder (False) until the churn-brake machinery
    (AC-11) lands.
    """
    from sleeves import ledger as sleeve_ledger  # noqa: PLC0415

    summaries: list[dict] = []
    try:
        sleeve_rows = database.get_all_sleeves()
    except Exception:
        return summaries

    for sleeve in sleeve_rows or []:
        sleeve_id = sleeve.get("id")
        try:
            rule_rows = (
                database.get_sleeve_rules_for_sleeve(sleeve_id) if sleeve_id is not None else []
            )
        except Exception:
            rule_rows = []

        try:
            order_history = (
                database.get_sleeve_order_history(sleeve_id) if sleeve_id is not None else []
            )
            realized_by_rule = {}
            for record in sleeve_ledger.attribute_realized_fills(order_history):
                realized_by_rule[record.opening_rule_id] = (
                    realized_by_rule.get(record.opening_rule_id, 0.0) + record.realized_delta_usd
                )
        except Exception:
            # None (not {}): the fold FAILED -- every rule renders "n/a"; an
            # empty dict would render $0.00 value claims (audit finding #8).
            realized_by_rule = None

        rule_summaries = []
        for rule in rule_rows:
            rule_id = rule.get("id")
            # Same COUNT accessors as the panel (audit #14: len(limited rows)
            # capped lifetime at 100; audit #15: ET-prefix vs UTC-stored
            # fired_at misattributed evening fires -- the day accessor
            # converts current_date_str's ET day to its exact UTC window).
            try:
                lifetime_fires = (
                    database.get_sleeve_rule_fire_count(rule_id) if rule_id is not None else 0
                )
                today_fires = (
                    database.get_fire_count_for_rule_on_day(rule_id, current_date_str)
                    if rule_id is not None
                    else 0
                )
            except Exception:
                lifetime_fires = 0
                today_fires = 0
            rule_summaries.append(
                {
                    "name": rule.get("name", ""),
                    "today_fires": today_fires,
                    "lifetime_fires": lifetime_fires,
                    "realized_pnl_usd": (
                        realized_by_rule.get(rule_id, 0.0) if realized_by_rule is not None else None
                    ),
                    "benched": False,
                }
            )

        summaries.append(
            {
                "name": sleeve.get("name", ""),
                "status": sleeve.get("status", ""),
                "rules": rule_summaries,
            }
        )
    return summaries


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

        # 3. Managed Sleeves digest extension (AC-17): per-rule fires + benched
        # rules. Silent when zero sleeves exist -- no phantom section.
        sleeves_section = build_sleeves_digest_section(
            _build_sleeve_digest_summaries(current_date_str)
        )
        if sleeves_section:
            embeds.append(
                {
                    "title": "🗂️ Managed Sleeves",
                    "color": 3447003,
                    "description": sleeves_section[:4096],
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

    except (OSError, ValueError, requests.RequestException, TypeError) as e:
        # OSError: file I/O; ValueError: json.load/strptime/JSONDecodeError; RequestException: Discord POST; TypeError: json.dumps non-serializable  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
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
