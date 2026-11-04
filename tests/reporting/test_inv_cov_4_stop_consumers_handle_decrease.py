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
# 2 — static/index.js: pin via source-grep that no JS comparator
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
