"""
RED tests — Managed Sleeves P3: dashboard Sleeves panel (AC-16).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard, visual
gate s3-ux):

  * templates/index.html gains a Sleeves panel section matching the existing
    light card-UI (Settings/Advisor card markup) — data-testid="sleeves-panel"
    wrapper, data-testid="sleeve-card" per sleeve, data-testid=
    "sleeve-status-badge" carrying the literal status string (one of SHADOW /
    PAPER / LIVE / BENCHED / PAUSED_RECONCILIATION / stale), and
    data-testid="atlas-cache-health-badge" for the Atlas cache-health
    observability element (audit MEDIUM-2).
  * app.py's dashboard() route (GET "/") passes a `sleeves` list (from
    database.get_all_sleeves()) into the render_template("index.html", ...)
    context so the panel is server-rendered on initial page load — mirrors
    the existing vars_locked_count / edit-vars-indicator precedent.
  * Sleeve/rule names are rendered through Jinja's normal autoescaping (no
    |safe filter, no raw string concatenation into HTML) — a sleeve name
    containing HTML/script content must render escaped, never executable.
  * No dark-theme CSS classes are introduced (house incident — "WHAT HAVE YOU
    DONE TO THE FUCKING UI" — never repeat it).

Route-level tests use the REAL dashboard() route, a REAL (per-test isolated)
SQLite DB, and the existing tests/app/conftest.py autouse
_stub_get_api_state_dict fixture (so Composer/network state is stubbed but
the sleeves panel itself is rendered from real database rows) — this is the
"route-level test with real producer modules, mock only DB/network" house
rule (dashboard-truth incident).
"""

from __future__ import annotations

import pathlib

import pytest

import app as app_module
import database

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_INDEX_HTML = _TEMPLATES_DIR / "index.html"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Static template markup (fast, no live rendering — mirrors the guard-alpha
# panel test convention in test_guard_alpha_panel_ui.py)
# ---------------------------------------------------------------------------


class TestSleevesPanelMarkup:
    def test_sleeves_panel_wrapper_element_present(self):
        html = _html()
        assert 'data-testid="sleeves-panel"' in html, (
            'AC-16 FAIL: templates/index.html missing data-testid="sleeves-panel". '
            "The Sleeves panel has not been added to the template yet."
        )

    def test_sleeve_status_badge_element_present(self):
        html = _html()
        assert 'data-testid="sleeve-status-badge"' in html, (
            "AC-16 FAIL: templates/index.html missing a per-sleeve "
            'data-testid="sleeve-status-badge" element.'
        )

    def test_atlas_cache_health_badge_present(self):
        html = _html()
        assert 'data-testid="atlas-cache-health-badge"' in html, (
            "AC-16 FAIL: templates/index.html missing "
            'data-testid="atlas-cache-health-badge" (audit MEDIUM-2 — cache-row '
            "age + last available=False reason)."
        )

    def test_arm_disarm_controls_present(self):
        html = _html()
        assert (
            'data-testid="sleeve-arm-control"' in html
            or 'data-testid="sleeve-disarm-control"' in html
        ), "AC-16 FAIL: the Sleeves panel must expose arm/disarm controls per sleeve"

    def test_panel_uses_light_card_ui_not_dark_theme(self):
        """The panel must reuse the existing light card-UI theme — never
        introduce dark/foreign CSS classes (house incident, standing rule)."""
        html = _html()
        if 'data-testid="sleeves-panel"' not in html:
            pytest.skip("Panel not yet present — prior test covers this.")
        dark_markers = ["bg-dark", "dark-card", "theme-dark"]
        found = [m for m in dark_markers if m in html]
        assert not found, (
            f"AC-16 theme FAIL: dark/foreign CSS class(es) {found} found in "
            "templates/index.html. The Sleeves panel must use the existing "
            "light card-UI CSS. Never introduce dark/foreign theme classes."
        )


# ---------------------------------------------------------------------------
# Route-level render smoke test — real DB, real dashboard() route
# ---------------------------------------------------------------------------


class TestSleevesPanelRendersSeededStatuses:
    _STATUSES = ("SHADOW", "PAPER", "LIVE", "BENCHED", "PAUSED_RECONCILIATION", "stale")

    def test_panel_renders_for_each_seeded_status_without_error(self, client):
        seeded_names = []
        for status in self._STATUSES:
            name = f"panel-render-{status.lower()}-sleeve"
            sleeve_id = database.create_sleeve(name, 1000.0, envelope_json="{}")
            database.update_sleeve_status(sleeve_id, status)
            seeded_names.append((name, status))

        resp = client.get("/")
        assert resp.status_code == 200, (
            f"GET / must render 200 with sleeves seeded in every documented "
            f"status; got {resp.status_code}"
        )
        html = resp.get_data(as_text=True)

        for name, status in seeded_names:
            assert name in html, (
                f"seeded sleeve {name!r} (status={status}) did not appear "
                f"anywhere in the rendered dashboard HTML"
            )

    def test_panel_renders_correct_status_text_for_a_paused_sleeve(self, client):
        name = "panel-paused-reconciliation-sleeve"
        sleeve_id = database.create_sleeve(name, 1000.0, envelope_json="{}")
        database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION")

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        assert name in html
        assert "PAUSED_RECONCILIATION" in html, (
            "a sleeve paused for reconciliation drift must show its actual "
            "status text on the panel — an operator must be able to see this "
            "at a glance, not infer it."
        )


# ---------------------------------------------------------------------------
# XSS: sleeve names render escaped, never as raw executable markup
# ---------------------------------------------------------------------------


class TestSleeveNameXSSEscaping:
    def test_script_tag_in_sleeve_name_renders_escaped(self, client):
        malicious_name = "<script>alert('xss')</script>"
        database.create_sleeve(malicious_name, 1000.0, envelope_json="{}")

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "<script>alert('xss')</script>" not in html, (
            "a sleeve name containing a raw <script> tag must NEVER appear "
            "unescaped in the rendered dashboard HTML — Jinja autoescaping "
            "must be in effect for every sleeve-supplied string."
        )
        assert "&lt;script&gt;" in html, (
            "the sleeve name must appear in its Jinja-escaped entity form "
            "(&lt;script&gt;...) — proof autoescaping actually ran, not just "
            "that the raw tag is absent (e.g. via silent truncation)."
        )


# ---------------------------------------------------------------------------
# Live-credential leakage: dashboard() must never expose ALPACA_LIVE_KEY/
# ALPACA_LIVE_SECRET to the rendered panel (s3-review pre-GREEN finding,
# 2026-07-08 — plan Security Considerations: "keys never in logs/fire
# snapshots/Discord/client responses", D-1 contract)
# ---------------------------------------------------------------------------


class TestNoLiveCredentialLeakage:
    def test_live_key_and_secret_never_appear_in_rendered_dashboard_html(self, client, monkeypatch):
        """Even when live keys ARE configured (operator has provisioned them),
        their literal values must never reach the rendered dashboard HTML —
        the Atlas health badge / sleeve status badges / arm-live affordance
        only need to know keys ARE present, never what they equal."""
        sentinel_live_key = "SENTINEL-LIVE-KEY-VALUE-MUST-NEVER-RENDER"
        sentinel_live_secret = "SENTINEL-LIVE-SECRET-VALUE-MUST-NEVER-RENDER"

        original_dotenv_values = app_module.dotenv_values

        def _fake_dotenv_values(*args, **kwargs):
            env = dict(original_dotenv_values(*args, **kwargs))
            env["ALPACA_LIVE_KEY"] = sentinel_live_key
            env["ALPACA_LIVE_SECRET"] = sentinel_live_secret
            env["SLEEVE_LIVE_EXECUTION"] = "True"
            return env

        monkeypatch.setattr(app_module, "dotenv_values", _fake_dotenv_values)
        database.create_sleeve("credential-leak-test-sleeve", 1000.0, envelope_json="{}")

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert sentinel_live_key not in html, (
            "ALPACA_LIVE_KEY's literal value must NEVER appear anywhere in "
            "the rendered dashboard HTML, even when live keys are configured — "
            "the panel may only reflect presence/absence, never the value "
            "(D-1 contract, plan Security Considerations)."
        )
        assert sentinel_live_secret not in html, (
            "ALPACA_LIVE_SECRET's literal value must NEVER appear anywhere in "
            "the rendered dashboard HTML, for the same reason."
        )

    def test_live_key_and_secret_never_appear_when_absent_either(self, client, monkeypatch):
        """Regression-direction guard: absence must render as absence, not as
        an empty-but-present field that could later be populated with a raw
        value in the same template slot without a test catching it."""
        original_dotenv_values = app_module.dotenv_values

        def _fake_dotenv_values(*args, **kwargs):
            env = dict(original_dotenv_values(*args, **kwargs))
            env.pop("ALPACA_LIVE_KEY", None)
            env.pop("ALPACA_LIVE_SECRET", None)
            return env

        monkeypatch.setattr(app_module, "dotenv_values", _fake_dotenv_values)
        database.create_sleeve("credential-leak-test-sleeve-absent", 1000.0, envelope_json="{}")

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "ALPACA_LIVE_KEY" not in html and "ALPACA_LIVE_SECRET" not in html, (
            "the raw env-var NAMES must never leak into the rendered HTML "
            "either — the panel must render a derived presence/absence "
            "signal, never echo the underlying key names or values."
        )


# ---------------------------------------------------------------------------
# s3-ux live visual-gate findings (task #25/#32, 2026-07-08) — AC-16 FAIL.
# Confirmed by an ACTUAL rendered render (Playwright, screenshots read
# personally), not code inspection alone. Findings #1/#4/#5/#2/#3 encoded as
# RED below; finding #6 (mobile 375px header/card-title flex-wrap misalign)
# is CSS-layout-only and not meaningfully pytest-testable — flagged
# separately to s3-dashboard/team-lead for a fix + s3-ux re-render, not
# encoded here.
# ---------------------------------------------------------------------------


def _extract_status_badge_class(html: str, sleeve_id: int) -> str:
    """Return the class attribute of the sleeve-status-badge span belonging
    to the card for sleeve_id (the badge appears shortly after the card's
    own data-sleeve-id in the template, before the next card's)."""
    import re

    pattern = re.compile(
        r'data-sleeve-id="'
        + str(sleeve_id)
        + r'"[\s\S]*?data-testid="sleeve-status-badge"[\s\S]*?class="([^"]+)"',
    )
    match = pattern.search(html)
    assert match, f"could not locate the sleeve-status-badge for sleeve_id={sleeve_id} in the HTML"
    return match.group(1)


class TestStatusBadgeDistinctness:
    """Finding #1: templates/index.html:1483-1486 maps ONLY LIVE and PAPER to
    their own class ("triggered"/"armed") — SHADOW, PAUSED_RECONCILIATION,
    BENCHED, and "stale" all collapse into the same "standby" class,
    confirmed via render (s3ux-sleeves-panel-closeup.png) to be visually
    IDENTICAL light-gray pills differing only in text. AC-16 requires six
    named, legible/distinct statuses — PAUSED_RECONCILIATION in particular
    is a serious operational state that must never look like a mundane
    fresh SHADOW sleeve."""

    _STATUSES = ("SHADOW", "PAPER", "LIVE", "BENCHED", "PAUSED_RECONCILIATION", "stale")

    def test_every_named_status_maps_to_a_visually_distinct_class(self, client):
        class_by_status: dict[str, str] = {}
        for status in self._STATUSES:
            sleeve_id = database.create_sleeve(
                f"badge-distinct-{status.lower()}", 1000.0, envelope_json="{}"
            )
            database.update_sleeve_status(sleeve_id, status)
            resp = client.get("/")
            html = resp.get_data(as_text=True)
            class_by_status[status] = _extract_status_badge_class(html, sleeve_id)

        distinct_classes = set(class_by_status.values())
        assert len(distinct_classes) == len(self._STATUSES), (
            f"AC-16 FAIL: {len(self._STATUSES)} named statuses must each map "
            f"to a visually distinct class, but only {len(distinct_classes)} "
            f"distinct class value(s) were found: {class_by_status!r}. Every "
            f"status must be legible/distinct from every other — collapsing "
            f"multiple statuses into one shared class (e.g. a catch-all "
            f"'standby') fails this."
        )

    def test_paused_reconciliation_is_never_the_same_class_as_shadow(self, client):
        """The single highest-priority pairing from the live finding:
        PAUSED_RECONCILIATION (a serious, attention-worthy operational
        state) must never be visually indistinguishable from a mundane
        freshly-created SHADOW sleeve."""
        shadow_id = database.create_sleeve("badge-shadow-vs-paused-1", 1000.0, envelope_json="{}")
        paused_id = database.create_sleeve("badge-shadow-vs-paused-2", 1000.0, envelope_json="{}")
        database.update_sleeve_status(paused_id, "PAUSED_RECONCILIATION")

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        shadow_class = _extract_status_badge_class(html, shadow_id)
        paused_class = _extract_status_badge_class(html, paused_id)

        assert shadow_class != paused_class, (
            f"PAUSED_RECONCILIATION (class={paused_class!r}) must never share "
            f"a class with SHADOW (class={shadow_class!r}) — an operator must "
            f"be able to spot a reconciliation pause at a glance."
        )


class TestDisabledButtonStyling:
    """Finding #5: templates/index.html:1516-1523's Disarm button uses the
    exact same inline style whether or not the `disabled` attribute is
    present — confirmed via DOM inspection + getComputedStyle (cursor:
    pointer, opacity: 1 identical in both states). An operator has no visual
    cue the button is inert."""

    def test_disabled_disarm_button_carries_a_distinct_visual_marker(self, client):
        # Deliberately avoid the substring "disabled" in these fixture names
        # -- an earlier draft of this test used names containing it, which
        # got captured by the (too-broad) markup regex and poisoned the
        # "disabled" fixture-sanity checks below with a false positive.
        shadow_id = database.create_sleeve("button-style-inert-sleeve", 1000.0, envelope_json="{}")
        # SHADOW status renders the Disarm button disabled (nothing to disarm yet).
        armed_id = database.create_sleeve("button-style-active-sleeve", 1000.0, envelope_json="{}")
        database.update_sleeve_status(armed_id, "PAPER")

        resp = client.get("/")
        html = resp.get_data(as_text=True)

        import re

        def _disarm_button_markup(sleeve_id: int) -> str:
            # Scoped to the <button ...> OPENING TAG only (never crossing
            # into another tag's content, e.g. the card's own name text) --
            # both data-testid and this exact sleeve's data-sleeve-id must
            # appear as attributes on the SAME <button> element.
            pattern = re.compile(
                r'<button\b[^>]*?data-testid="sleeve-disarm-control"[^>]*?data-sleeve-id="'
                + str(sleeve_id)
                + r'"[^>]*>',
                re.DOTALL,
            )
            match = pattern.search(html)
            assert match, f"could not locate the Disarm button for sleeve_id={sleeve_id}"
            return match.group(0)

        disabled_markup = _disarm_button_markup(shadow_id)
        enabled_markup = _disarm_button_markup(armed_id)

        assert "disabled" in disabled_markup, (
            "fixture sanity: a SHADOW sleeve's Disarm button must carry the disabled attribute"
        )
        assert "disabled" not in enabled_markup, (
            "fixture sanity: a PAPER sleeve's Disarm button must NOT carry the disabled attribute"
        )

        # The disabled state must carry SOME additional visual marker beyond
        # the bare `disabled` attribute — a distinct class, or a visibly
        # different inline style/class token (opacity/cursor), so an
        # operator can tell at a glance the control is inert. Compare the
        # NORMALIZED style/class attribute values specifically (not the raw
        # tag text) — a naive raw-string diff after stripping the word
        # "disabled" is contaminated by whitespace artifacts Jinja leaves
        # behind from the {% if %}...{% endif %} block itself (an earlier
        # draft of this test passed vacuously for exactly that reason).
        def _attr_value(tag_markup: str, attr: str) -> str:
            attr_match = re.search(attr + r'="([^"]*)"', tag_markup)
            return attr_match.group(1).strip() if attr_match else ""

        disabled_style = _attr_value(disabled_markup, "style")
        enabled_style = _attr_value(enabled_markup, "style")
        disabled_class = _attr_value(disabled_markup, "class")
        enabled_class = _attr_value(enabled_markup, "class")

        assert disabled_style != enabled_style or disabled_class != enabled_class, (
            "AC-16 FAIL: the disabled Disarm button must carry a visually "
            "distinct style or class beyond the bare 'disabled' attribute — "
            f"currently both render the IDENTICAL style ({disabled_style!r}) "
            f"and class ({disabled_class!r}) whether disabled or not, so a "
            f"disabled control looks exactly like an enabled one (confirmed "
            f"live: cursor:pointer and opacity:1 in both states)."
        )


class TestDeleteControl:
    """Finding #4: no delete affordance exists anywhere — only Arm Paper and
    Disarm controls are present. The plan's route list includes a delete
    route; Edge Cases says a sleeve with open positions must refuse deletion
    unless flat (delete never liquidates)."""

    def test_delete_control_present_in_template(self):
        html = _html()
        assert 'data-testid="sleeve-delete-control"' in html, (
            "AC-16 FAIL: templates/index.html has no delete control for a "
            "sleeve — only Arm Paper and Disarm exist."
        )

    def test_delete_route_removes_a_flat_sleeve_with_no_orders(self, client):
        sleeve_id = database.create_sleeve("delete-test-flat-sleeve", 1000.0, envelope_json="{}")

        resp = client.post(f"/api/sleeves/{sleeve_id}/delete")

        assert resp.status_code == 200, (
            f"deleting a flat sleeve (zero orders, zero positions) must "
            f"succeed; got {resp.status_code}: {resp.get_data(as_text=True)[:300]!r}"
        )
        assert database.get_sleeve(sleeve_id) is None, (
            "the sleeve row must actually be gone after a successful delete"
        )

    def test_delete_route_refuses_a_sleeve_with_an_open_position(self, client):
        """Edge Cases: 'Sleeve deleted with open positions → refuse unless
        flat... delete never liquidates' — v1 semantics are refuse-unless-flat."""
        sleeve_id = database.create_sleeve("delete-test-with-position", 5000.0, envelope_json="{}")
        client_order_id = f"delete-test-position-{sleeve_id}"
        order_pk = database.insert_sleeve_order(
            client_order_id=client_order_id,
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=10.0,
            status="filled",
            reserved_price=500.0,
        )
        database.insert_sleeve_fill(
            order_id=order_pk, fill_price=500.0, filled_qty=10.0, filled_at="2026-07-08T14:00:00Z"
        )

        resp = client.post(f"/api/sleeves/{sleeve_id}/delete")

        assert resp.status_code in (400, 409), (
            f"deleting a sleeve with an open position must be refused (delete "
            f"never liquidates); got {resp.status_code}"
        )
        assert database.get_sleeve(sleeve_id) is not None, (
            "the sleeve must NOT be deleted when it still holds an open position"
        )

    def test_delete_route_for_unknown_sleeve_returns_404(self, client):
        resp = client.post("/api/sleeves/999999/delete")
        assert resp.status_code == 404


class TestPanelFinancialData:
    """Findings #2/#3: AC-16 requires 'cash ledger vs broker truth' and
    'realized per-rule P&L (attribution from fill records)' — currently
    _build_sleeves_panel_context (app.py) returns only id/name/status/
    capital_usd/rules (fire counts only, no financial figures at all).

    Scope decision (test-writer, flagged to team-lead/docs for visibility):
    per-rule realized P&L is achievable with ZERO new schema by reusing
    sleeve_orders.rule_id (already present) to filter a sleeve's own
    get_sleeve_order_history() before folding it through
    sleeves.ledger.reconstruct_from_history — that LedgerState's
    realized_pnl_usd, computed over just that rule's own orders, IS the
    rule's attributed realized P&L. 'Cash ledger vs broker truth' is scoped
    here to the sleeve's OWN ledger cash_usd (also zero-new-schema, via the
    same reconstruct_from_history call) -- the "vs broker truth" signal is
    the EXISTING sleeve.status column (PAUSED_RECONCILIATION already means
    "known mismatch"); persisting a raw broker-truth dollar figure for
    display would need a schema migration and is intentionally NOT required
    by this file -- flagged as a separate, explicit open point rather than
    silently assumed in scope.
    """

    @staticmethod
    def _extract_sleeve_card_html(html: str, sleeve_id: int) -> str:
        """Scope a check to just ONE sleeve's own card fragment -- the full
        page HTML contains unrelated CSS/JS numeric literals (widths,
        chart configs, etc.) that a bare whole-page substring search could
        collide with, producing a false pass."""
        import re

        pattern = re.compile(
            r'data-testid="sleeve-card"\s+data-sleeve-id="'
            + str(sleeve_id)
            + r'"[\s\S]*?(?=data-testid="sleeve-card"|</section>)'
        )
        match = pattern.search(html)
        assert match, f"could not locate the sleeve-card fragment for sleeve_id={sleeve_id}"
        return match.group(0)

    def test_panel_shows_sleeve_ledger_cash(self, client):
        """Seeds a PARTIAL buy so the sleeve's ledger cash_usd genuinely
        DIFFERS from its static capital_usd -- asserting on the untouched
        capital_usd figure alone would pass vacuously (that field is already
        rendered today via the "Capital: $X" label; this test must prove a
        LEDGER-derived figure, not just re-confirm the pre-existing static
        capital display)."""
        sleeve_id = database.create_sleeve("financial-data-cash-sleeve", 7777.0, envelope_json="{}")
        buy_notional = 2000.0
        order_pk = database.insert_sleeve_order(
            client_order_id=f"ledger-cash-test-{sleeve_id}",
            sleeve_id=sleeve_id,
            symbol="SPY",
            side="buy",
            qty=20.0,
            status="filled",
            reserved_price=buy_notional / 20.0,
        )
        database.insert_sleeve_fill(
            order_id=order_pk,
            fill_price=buy_notional / 20.0,
            filled_qty=20.0,
            filled_at="2026-07-08T14:00:00Z",
        )
        expected_ledger_cash = 7777.0 - buy_notional  # fixture-derived, not hardcoded

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        card_html = self._extract_sleeve_card_html(html, sleeve_id)

        assert (
            f"{expected_ledger_cash:.2f}" in card_html
            or f"{expected_ledger_cash:,.2f}" in card_html
        ), (
            f"AC-16 FAIL: the panel must show the sleeve's own LEDGER cash "
            f"figure (${expected_ledger_cash:.2f}, derived from "
            f"sleeves.ledger.reconstruct_from_history after a ${buy_notional:.2f} "
            f"buy — NOT the static, unchanged capital_usd of $7777.00) within "
            f"sleeve {sleeve_id}'s own card fragment. The pre-existing "
            f"'Capital: $X' label alone is insufficient — that always shows "
            f"the static capital_usd, never the ledger's actual current cash."
        )

    def test_panel_shows_realized_pnl_per_rule_derived_from_fills(self, client):
        """A rule's realized P&L must be a REAL figure derived from its own
        attributed fills (sleeve_orders.rule_id), never a hardcoded/omitted
        placeholder. Seeds a buy-then-sell round trip for one rule so a
        nonzero, fixture-derived realized P&L exists to assert on. The check
        is scoped to this sleeve's own card fragment (not the whole page) so
        an unrelated numeric literal elsewhere on the dashboard can't
        produce a false pass."""
        sleeve_id = database.create_sleeve("financial-data-pnl-sleeve", 10000.0, envelope_json="{}")
        rule_id = database.create_sleeve_rule(
            sleeve_id, "financial-data-pnl-rule", json_doc="{}", mode="SHADOW"
        )

        buy_price = 100.0
        sell_price = 150.0
        qty = 10.0

        buy_order_pk = database.insert_sleeve_order(
            client_order_id=f"pnl-test-buy-{rule_id}",
            sleeve_id=sleeve_id,
            rule_id=rule_id,
            symbol="SPY",
            side="buy",
            qty=qty,
            status="filled",
            reserved_price=buy_price,
        )
        database.insert_sleeve_fill(
            order_id=buy_order_pk,
            fill_price=buy_price,
            filled_qty=qty,
            filled_at="2026-07-08T14:00:00Z",
        )
        sell_order_pk = database.insert_sleeve_order(
            client_order_id=f"pnl-test-sell-{rule_id}",
            sleeve_id=sleeve_id,
            rule_id=rule_id,
            symbol="SPY",
            side="sell",
            qty=qty,
            status="filled",
        )
        database.insert_sleeve_fill(
            order_id=sell_order_pk,
            fill_price=sell_price,
            filled_qty=qty,
            filled_at="2026-07-08T15:00:00Z",
        )

        expected_realized_pnl = (sell_price - buy_price) * qty  # fixture-derived, not hardcoded

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        card_html = self._extract_sleeve_card_html(html, sleeve_id)

        # Accept either sign convention (+500.00 or 500.00) and either 2 or
        # fewer decimal places, but the derived dollar figure itself must
        # appear somewhere within THIS sleeve's own card fragment.
        assert (
            f"{expected_realized_pnl:.2f}" in card_html
            or f"{expected_realized_pnl:.0f}" in card_html
        ), (
            f"AC-16 FAIL: the panel must show this rule's realized P&L "
            f"(${expected_realized_pnl:.2f}, derived from its own attributed "
            f"buy@{buy_price}/sell@{sell_price} round trip via "
            f"sleeve_orders.rule_id + sleeves.ledger.reconstruct_from_history) "
            f"within sleeve {sleeve_id}'s own card fragment — fire counts "
            f"alone are not sufficient for AC-16's 'realized per-rule P&L "
            f"(attribution from fill records)' requirement."
        )
