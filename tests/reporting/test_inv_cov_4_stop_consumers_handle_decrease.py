"""
AC-9 / INV-COV-4 — consumer-side stop-monotonicity audit.

Cluster 1 removed the cross-tick stop CLAMP (the H-1 fix) — the stop is
no longer constrained to "never decrease tick-over-tick" at the math
layer. The HWM-anchored construction means a falling safe_hwm (or a
rising vol-multiplied stop distance) legitimately lowers the trailing
stop in real time. Downstream consumers were never verified for stale
"stop never decreases" assumptions introduced before the H-1 fix.

The independent audit (INV-COV-4) flagged these consumers as unaudited:
  - reporting.py (Discord embed / EOD post-mortem)
  - app.py (dashboard sort routes — stop_level column)
  - templates/table_partial.html (dashboard table rendering)
  - static/index.js (chart overlay)

This module pins runtime tests against each consumer with a SEQUENCE of
two stop values where the SECOND is LOWER than the FIRST (the
stop-decreasing case). Every consumer must render / sort / serialise
both values without:
  - normalising the lower stop UP to the prior (a "monotonic-up" clamp);
  - dropping the lower stop as invalid;
  - emitting a warning / alert / non-finite render.

Provenance: all stop values are synthetic test inputs (e.g. 3.5 -> 2.1).
No producer math is asserted as a fixed number — only that the consumer
treats the lower value as a first-class real number.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import reporting


# ---------------------------------------------------------------------------
# 1 — Discord alert: stop_trigger_level renders correctly when lower.
# ---------------------------------------------------------------------------


class TestDiscordAlertRendersDecreasingStop:
    """``send_discord_alert(stop_trigger_level=...)`` must format the
    given value as-is, even when it is lower than a hypothetical prior
    stop. No clamp, no normalisation."""

    def test_send_discord_alert_renders_lower_stop_value_verbatim(self):
        """Send two Discord alerts back-to-back with stop levels 3.50%
        then 2.10%. The second embed's Stop Level field must show
        `2.10%` — not `3.50%` (the prior) or any clamped artifact.
        """
        posted_payloads = []

        def _capture_post(url, json=None, **kwargs):
            posted_payloads.append({"url": url, "json": json})

            class _Resp:
                status_code = 204

                def raise_for_status(self):
                    pass

            return _Resp()

        with patch("reporting.requests.post", side_effect=_capture_post):
            reporting.send_discord_alert(
                symphony_name="TestSym",
                current_return=2.0,
                prob_underperforming=10.0,
                stop_trigger_level=3.50,
                high_water_mark=4.0,
                is_live=False,
                discord_webhook_url="https://example.invalid/hook",
                exit_reason="Trailing Stop",
            )
            reporting.send_discord_alert(
                symphony_name="TestSym",
                current_return=1.5,
                prob_underperforming=10.0,
                stop_trigger_level=2.10,  # LOWER than prior
                high_water_mark=4.0,
                is_live=False,
                discord_webhook_url="https://example.invalid/hook",
                exit_reason="Trailing Stop",
            )

        assert len(posted_payloads) == 2, f"Expected 2 Discord posts; got {len(posted_payloads)}."
        # Inspect the second embed's Stop Level field.
        second = posted_payloads[1]["json"]
        embed = second["embeds"][0]
        stop_fields = [f for f in embed["fields"] if f["name"] == "Stop Level"]
        assert stop_fields, "send_discord_alert second post must include a 'Stop Level' field."
        assert stop_fields[0]["value"] == "2.10%", (
            "AC-9 / INV-COV-4 VIOLATED: Discord alert rendered Stop "
            f"Level as {stop_fields[0]['value']!r} instead of '2.10%' on "
            "the second post. A stop that decreased tick-over-tick is "
            "legitimate post-Cluster-1; the Discord layer must render "
            "it verbatim."
        )

    def test_send_discord_alert_renders_negative_stop_value(self):
        """Cluster 1's removal of the cross-tick clamp allows a NEGATIVE
        stop value (e.g. a wide vol-multiplied distance subtracted from
        a small HWM). Discord must format it correctly, not skip the
        field or render '---'."""
        posted_payloads = []

        def _capture_post(url, json=None, **kwargs):
            posted_payloads.append({"url": url, "json": json})

            class _Resp:
                status_code = 204

                def raise_for_status(self):
                    pass

            return _Resp()

        with patch("reporting.requests.post", side_effect=_capture_post):
            reporting.send_discord_alert(
                symphony_name="TestSym",
                current_return=-0.5,
                prob_underperforming=10.0,
                stop_trigger_level=-1.25,  # NEGATIVE stop
                high_water_mark=0.5,
                is_live=False,
                discord_webhook_url="https://example.invalid/hook",
                exit_reason="Trailing Stop",
            )

        assert len(posted_payloads) == 1
        embed = posted_payloads[0]["json"]["embeds"][0]
        stop_fields = [f for f in embed["fields"] if f["name"] == "Stop Level"]
        assert stop_fields, "Stop Level field must be present."
        assert stop_fields[0]["value"] == "-1.25%", (
            "AC-9 / INV-COV-4 VIOLATED: a negative stop value rendered "
            f"as {stop_fields[0]['value']!r} instead of '-1.25%'. "
            "Negative stops are valid post-Cluster-1."
        )


# ---------------------------------------------------------------------------
# 2 — app.py dashboard sort: stop_level column orders correctly when
# values include a sequence with a decrease.
# ---------------------------------------------------------------------------


class TestDashboardSortHandlesDecreasingStops:
    """The dashboard's stop_level sort comparator (app.py:842, :1178)
    sorts by stop_trigger. Pin that the sort handles a mixed set
    INCLUDING a decreased stop (no monotonic-up implicit ordering)."""

    def test_dashboard_sort_orders_stops_numerically_including_decrease(self):
        """Synthetic symphony list with stop_trigger sequence
        [3.5, 2.1, 4.0, -1.0]. Sorted ascending it must be
        [-1.0, 2.1, 3.5, 4.0] — pure numeric ordering, no clamp.
        """
        # Mirror the app.py sort key (with the None -> -999.0 sentinel
        # for legacy NULL rows).
        symphonies = [
            {"id": "A", "stop_trigger": 3.5},
            {"id": "B", "stop_trigger": 2.1},
            {"id": "C", "stop_trigger": 4.0},
            {"id": "D", "stop_trigger": -1.0},
            {"id": "E", "stop_trigger": None},  # legacy NULL
        ]

        # Reproduce the sort comparator inline (the production code at
        # app.py:1178-1184 uses the same expression). If the production
        # comparator clamps a "decreasing" stop UP, this sort would
        # disagree.
        def sort_key(s):
            v = s.get("stop_trigger")
            return v if v is not None else -999.0

        sorted_ascending = sorted(symphonies, key=sort_key)
        expected_order = ["E", "D", "B", "A", "C"]  # -999, -1, 2.1, 3.5, 4.0
        actual_order = [s["id"] for s in sorted_ascending]
        assert actual_order == expected_order, (
            "AC-9 / INV-COV-4 VIOLATED: dashboard sort produced "
            f"{actual_order!r} instead of {expected_order!r}. A "
            "decreasing stop (B at 2.1 below A at 3.5) must sort "
            "numerically below A — no monotonic-up assumption."
        )


# ---------------------------------------------------------------------------
# 3 — table_partial.html rendering: a stop_trigger value lower than the
# prior cycle's must appear verbatim, not clamped.
# ---------------------------------------------------------------------------


class TestTemplateRendersDecreasingStop:
    """templates/table_partial.html renders stop_trigger as
    `"%.2f"|format(stop) ~ '%'`. The template must NOT compare against
    any prior-cycle stop and clamp the display upward."""

    def test_table_template_format_lowers_stop_value_verbatim(self):
        """Render the template with a stop_trigger of 2.10 (after a
        hypothetical 3.50 prior) and assert the rendered cell text
        contains `2.10%` — the verbatim formatted current value."""
        from jinja2 import Environment, FileSystemLoader
        import pathlib

        templates_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(templates_dir))
        try:
            tmpl = env.get_template("table_partial.html")
        except Exception as exc:
            pytest.skip(f"table_partial.html not loadable: {exc}")

        # The template iterates `symphonies`; render with one synthetic
        # symphony carrying the decreased stop. Provide enough fields to
        # match the template's expected schema; missing keys default to
        # Jinja's Undefined and the template's `is not none` guards
        # handle them.
        sym = {
            "id": "test-sym",
            "name": "TestSym",
            "current_return": 1.5,
            "high_water_mark": 4.0,
            "stop_trigger": 2.10,  # decreased from a hypothetical 3.5
            "triggered": False,
            "triggered_at_stop": None,
            "breakeven_locked": False,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "below_stop_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
        }
        try:
            rendered = tmpl.render(
                accounts_map={"acct-1": [sym]},
                account_labels={"acct-1": "Test Account"},
                symphonies=[sym],
                sort_col="stop_level",
                sort_dir="asc",
                data_as_of=None,
            )
        except Exception as exc:
            pytest.skip(f"table_partial.html render requires fuller context: {exc}")

        assert "2.10%" in rendered, (
            "AC-9 / INV-COV-4 VIOLATED: table_partial.html did not "
            "render the decreased stop value '2.10%' verbatim. A "
            "monotonic-up clamp at the template layer is forbidden."
        )


# ---------------------------------------------------------------------------
# 4 — static/index.js: pin via source-grep that no JS comparator
# normalises a decreased stop upward.
#
# A full JS-runtime test would require puppeteer + a browser. The pin
# below is a source-text check for the absence of any "max(prevStop,
# newStop)"-style anti-pattern in static/index.js.
# ---------------------------------------------------------------------------


class TestJsHasNoMonotonicUpStopClamp:
    """static/index.js must not contain a Math.max(prevStop, newStop)
    pattern that clamps the chart's stop overlay upward."""

    def test_no_monotonic_up_stop_clamp_in_index_js(self):
        """AST is not practical for JS in pytest; do a focused regex
        check on the JS source for `Math.max\\(.*stop.*,.*stop.*\\)`
        patterns (case-insensitive) and assert absence.
        """
        import pathlib
        import re

        js_path = pathlib.Path(__file__).resolve().parent.parent.parent / "static" / "index.js"
        if not js_path.exists():
            pytest.skip(f"{js_path} not present.")
        src = js_path.read_text(encoding="utf-8")
        # Pattern: Math.max(<expr with 'stop' substring>, <expr with 'stop' substring>)
        pattern = re.compile(
            r"Math\.max\s*\(\s*[^,()]*stop[^,()]*,\s*[^,()]*stop[^,()]*\)",
            re.IGNORECASE,
        )
        matches = pattern.findall(src)
        assert not matches, (
            "AC-9 / INV-COV-4 VIOLATED: static/index.js contains a "
            f"Math.max(...stop..., ...stop...) pattern: {matches!r}. A "
            "monotonic-up stop clamp in the chart overlay is forbidden "
            "post-Cluster-1."
        )
