"""
RED tests — Managed Sleeves P3: EOD Discord digest extension (AC-17).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard):

    reporting.build_sleeves_digest_section(sleeve_summaries: list[dict]) -> str
        Pure, never-raising formatter. Each sleeve_summary dict carries at
        least {"name": str, "status": str, "rules": [{"name": str,
        "today_fires": int, "lifetime_fires": int, "realized_pnl_usd": float,
        "benched": bool}, ...]}. Returns "" (falsy) for an empty list — no
        phantom section when no sleeves exist. Otherwise returns a
        human-readable block naming every sleeve and, for any rule with
        benched=True, explicitly calling out that it is benched (AC-17: "EOD
        digest extended with a sleeve section (per-rule P&L, round trips,
        benched rules)").

    reporting.send_eod_discord_post(...) internally builds sleeve summaries
    (via database.get_all_sleeves / database.get_sleeve_rule_fires) and, when
    non-empty, includes reporting.build_sleeves_digest_section's output
    somewhere in the outgoing Discord payload. When there are zero sleeves,
    the digest is silent — no phantom sleeves section is appended.

This file scopes the exact Discord embed/field layout to the implementer's
discretion; it pins the pure formatter's I/O contract and that its output
actually reaches the outgoing webhook payload when sleeves exist.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import reporting

pytestmark = pytest.mark.skipif(
    not hasattr(reporting, "build_sleeves_digest_section"),
    reason="RED phase — reporting.build_sleeves_digest_section not implemented yet",
)

_SENTINEL_RULE_NAME = "digest-test-benched-rule"
_SENTINEL_SLEEVE_NAME = "digest-test-sleeve"


def _sample_summary(*, benched: bool = False) -> list[dict]:
    return [
        {
            "name": _SENTINEL_SLEEVE_NAME,
            "status": "PAPER",
            "rules": [
                {
                    "name": _SENTINEL_RULE_NAME,
                    "today_fires": 2,
                    "lifetime_fires": 14,
                    "realized_pnl_usd": -37.5,
                    "benched": benched,
                }
            ],
        }
    ]


class TestBuildSleevesDigestSection:
    def test_empty_summary_list_returns_falsy(self):
        result = reporting.build_sleeves_digest_section([])
        assert not result, (
            "an empty sleeve-summary list must produce a falsy (empty string) "
            "result — no phantom sleeves section when no sleeves exist."
        )

    def test_non_empty_summary_names_the_sleeve(self):
        result = reporting.build_sleeves_digest_section(_sample_summary())
        assert _SENTINEL_SLEEVE_NAME in result, (
            f"the digest section must name every sleeve; sleeve name "
            f"{_SENTINEL_SLEEVE_NAME!r} not found in output"
        )

    def test_benched_rule_is_explicitly_called_out(self):
        result = reporting.build_sleeves_digest_section(_sample_summary(benched=True))
        assert "benched" in result.lower(), (
            f"a benched rule ({_SENTINEL_RULE_NAME!r}) must be explicitly "
            f"called out as benched in the digest section, not silently listed "
            f"alongside active rules"
        )

    def test_never_raises_on_a_missing_optional_key(self):
        """A summary dict missing an optional key (e.g. realized_pnl_usd)
        must never crash the digest — it's an EOD report, not a critical
        path; a formatting gap must degrade gracefully, never 500 the
        scheduler thread."""
        minimal_summary = [{"name": "minimal-sleeve", "status": "SHADOW", "rules": []}]
        try:
            result = reporting.build_sleeves_digest_section(minimal_summary)
        except Exception as exc:  # pragma: no cover — only fires on regression
            pytest.fail(
                f"build_sleeves_digest_section must never raise on a minimal/"
                f"incomplete summary dict; got {type(exc).__name__}: {exc}"
            )
        assert isinstance(result, str)


class TestSendEodDiscordPostIncludesSleevesSection:
    def test_sleeves_section_reaches_the_outgoing_webhook_payload(self, tmp_path, monkeypatch):
        report_file = tmp_path / "post_mortem_2026-07-08.json"
        report_file.write_text(
            json.dumps({"summary": {"total_monitored": 0}, "triggers": []}), encoding="utf-8"
        )

        sentinel_section = f"SENTINEL-SECTION::{_SENTINEL_SLEEVE_NAME}"
        monkeypatch.setattr(
            reporting, "build_sleeves_digest_section", lambda _summaries: sentinel_section
        )

        import database

        monkeypatch.setattr(
            database,
            "get_all_sleeves",
            lambda: [{"id": 1, "name": _SENTINEL_SLEEVE_NAME, "status": "PAPER"}],
        )

        captured_posts = []

        def _fake_post(url, **kwargs):
            captured_posts.append({"url": url, "kwargs": kwargs})
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {"url": "https://quickchart.test/chart.png"}
            return m

        with patch.object(reporting.requests, "post", side_effect=_fake_post):
            reporting.send_eod_discord_post(
                "2026-07-08",
                str(report_file),
                {},
                "https://discord.test/webhook",
            )

        combined = json.dumps([str(p["kwargs"]) for p in captured_posts])
        assert sentinel_section in combined, (
            "when sleeves exist, build_sleeves_digest_section's output must "
            "reach the outgoing Discord webhook payload somewhere (embed "
            "description/field, or top-level content) — got no trace of the "
            f"sentinel section in any captured POST: {combined[:500]!r}"
        )

    def test_no_sleeves_section_when_zero_sleeves_exist(self, tmp_path, monkeypatch):
        report_file = tmp_path / "post_mortem_2026-07-08.json"
        report_file.write_text(
            json.dumps({"summary": {"total_monitored": 0}, "triggers": []}), encoding="utf-8"
        )

        build_section_mock = MagicMock(return_value="")
        monkeypatch.setattr(reporting, "build_sleeves_digest_section", build_section_mock)

        import database

        monkeypatch.setattr(database, "get_all_sleeves", lambda: [])

        with patch.object(reporting.requests, "post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"url": "https://quickchart.test/chart.png"}
            reporting.send_eod_discord_post(
                "2026-07-08",
                str(report_file),
                {},
                "https://discord.test/webhook",
            )

        combined = json.dumps([str(c.kwargs) for c in mock_post.call_args_list if c.kwargs])
        assert _SENTINEL_SLEEVE_NAME not in combined, (
            "with zero sleeves, no sleeves-section content must appear in the "
            "outgoing Discord payload"
        )
