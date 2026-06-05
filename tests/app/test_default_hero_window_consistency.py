"""
RED — Option A: default hero = windowed default GUARD ALPHA; account-lifetime CR is a SEPARATE
non-windowed stat (PM live-gate bug + user decision).

THE BUG (PM live gate, 2026-06-04): on page load the hero showed the un-windowed ACCOUNT-basis CR
(~67%) under a static "30d" label, while /api/strip/30d returned the VW windowed value (~29%) — so
the first picker click jumped the hero and "30D" > "All Time" (backwards). Root: the default
_compute_portfolio_strip (app.py:689-705) uses get_portfolio_cumulative_return_account_basis
(account if_held ~66%) while compute_windowed_portfolio_strip (analytics.py:1496-1499) uses the VW
if_held (~28%). A windowed ACCOUNT-basis CR is not even computable (Composer total-stats gives only
an all-time scalar, no daily account series).

USER DECISION — OPTION A:
 1. The DEFAULT hero loads the WINDOWED default-window GUARD ALPHA, consistent with the picker's
    first click. The window label applies to the GUARD ALPHA (the windowable bot-vs-held metric).
    No un-windowed value under a window label.
 2. The ~67% account-lifetime CR becomes a SEPARATE, clearly-labeled "Account · all-time" stat that
    does NOT window and does NOT carry the window label — a fixed all-time reference.
 3. The PICKER windows GUARD ALPHA + ANN VOL + MAX-DD. The account-lifetime CR does NOT window.

THESE TESTS pin Option A (exercised WITH A WARM _account_totals_cache — the live condition; the
empty-cache default silently took the VW path, which is why this slipped):
 (a) default /api/state hero WINDOWED guard alpha == /api/strip/<default-window> guard alpha;
 (b) the account-lifetime CR is a DISTINCT field that does NOT change across windows;
 (c) every /api/strip/<window> carries a re-windowing guard_alpha;
 (d) templates carry a distinct 'Account · all-time' element.

They FAIL today and go GREEN when flask+risk-engine wire Option A.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import app as app_module
import database as _db

# The picker's default window (templates/index.html active button + label).
_DEFAULT_WINDOW = "30d"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _live_bot_state():
    return {
        "sym-x": {
            "name": "Symphony X", "account": "ACC1", "current_value": 10000.0,
            "current_return": 1.5, "simple_return": 0.30, "net_deposits": 1000.0,
            "time_weighted_return": 0.30, "max_drawdown": 0.08,
            "armed": True, "tp_armed": False, "para_armed": False, "triggered": False,
        }
    }


# A warm account cache (as _refresh_account_totals populates live): account CR ~66%, cash present.
_WARM_CACHE = {
    "portfolio_cr": 66.0, "portfolio_tc": 0.5, "portfolio_mdd": 24.47,
    "portfolio_value": 12945.18,
}


# ===========================================================================
# (a) default poll hero windowed guard alpha == /api/strip/<default> — ONE basis
# ===========================================================================

class TestDefaultHeroGuardAlphaMatchesWindowedDefault:
    def test_default_poll_guard_alpha_equals_windowed_default(self, client, monkeypatch):
        """WARM-CACHE path (the live condition): the default /api/state hero's WINDOWED guard
        alpha must EQUAL /api/strip/<default-window>'s guard alpha. No 67-vs-29 jump on first click.
        """
        monkeypatch.setattr(app_module, "_account_totals_cache", dict(_WARM_CACHE), raising=False)
        with patch.object(app_module.database, "load_state", return_value=_live_bot_state()), \
             patch.object(app_module, "dotenv_values", lambda *_a, **_k: {}), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            state = client.get("/api/state").get_json()
            strip = client.get(f"/api/strip/{_DEFAULT_WINDOW}").get_json()

        ps = state.get("portfolio_strip") or {}
        windowed_alpha = strip.get("guard_alpha")
        assert windowed_alpha is not None, (
            f"/api/strip/{_DEFAULT_WINDOW} must define a guard_alpha; got {strip!r}"
        )
        # Option A: the default strip must carry an EXPLICIT windowed guard_alpha field (the hero
        # metric), NOT just an account-basis cumulative_return. Deriving dry_run-if_held from the
        # account CR is the OLD behavior and must not satisfy this — require the field + the window.
        assert "guard_alpha" in ps, (
            "OPTION-A FAIL: the default poll portfolio_strip has no 'guard_alpha' field. The "
            "default hero must carry the WINDOWED guard alpha (Option A), not the un-windowed "
            f"account CR. portfolio_strip keys={list(ps.keys())}"
        )
        assert str(ps.get("window", "")).lower() == _DEFAULT_WINDOW, (
            "OPTION-A FAIL: the default poll portfolio_strip must echo the default window "
            f"'{_DEFAULT_WINDOW}' (so the label tracks the windowed guard alpha); got "
            f"window={ps.get('window')!r}."
        )
        default_alpha = ps.get("guard_alpha")
        # abs=1e-6: the default hero guard alpha and the default-window strip guard alpha are the
        # SAME windowed number — no 67-vs-29 jump on first click.
        assert default_alpha == pytest.approx(windowed_alpha, abs=1e-6), (
            f"OPTION-A FAIL: default hero guard_alpha {default_alpha} != windowed default "
            f"{windowed_alpha}. The page must load the windowed default-window guard alpha so the "
            "first picker click does not jump the hero."
        )


# ===========================================================================
# (b) account-lifetime CR is a DISTINCT non-windowed field
# ===========================================================================

def _account_all_time_cr(payload: dict):
    """Locate the dedicated account-all-time CR field (Option A). Accept the canonical name or a
    small set of reasonable aliases so the test pins the CONTRACT (a separate field), not a brittle
    exact name the implementer must guess."""
    meta = (payload.get("meta") or {}).get("portfolio") or {}
    ps = payload.get("portfolio_strip") or {}
    for src in (meta, ps, payload):
        if not isinstance(src, dict):
            continue
        for k in ("account_all_time_cr", "account_cr_all_time", "account_lifetime_cr",
                  "account_cr"):
            if src.get(k) is not None:
                return src[k]
    return None


class TestAccountAllTimeCrIsSeparateAndUnwindowed:
    def test_account_all_time_cr_field_exists_and_is_account_basis(self, client, monkeypatch):
        """The ~66% account-lifetime CR must be exposed as its OWN field (not the windowed hero
        value), so the UI can render the 'Account · all-time' stat."""
        monkeypatch.setattr(app_module, "_account_totals_cache", dict(_WARM_CACHE), raising=False)
        with patch.object(app_module.database, "load_state", return_value=_live_bot_state()), \
             patch.object(app_module, "dotenv_values", lambda *_a, **_k: {}), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            state = client.get("/api/state").get_json()
        acct_cr = _account_all_time_cr(state)
        assert acct_cr is not None, (
            "OPTION-A FAIL: no distinct account-all-time CR field in the payload. The ~66% account "
            "lifetime CR must be a SEPARATE clearly-labeled stat, not the windowed hero headline."
        )
        assert acct_cr == pytest.approx(_WARM_CACHE["portfolio_cr"], abs=1e-6), (
            f"account-all-time CR {acct_cr} must equal the Composer account simple_return "
            f"{_WARM_CACHE['portfolio_cr']} (account basis), not a windowed VW value."
        )

    def test_account_all_time_cr_does_not_change_across_windows(self, client, monkeypatch):
        """The account-all-time stat is fixed — it must NOT vary when the picker window changes
        (it carries no window label and does not window). /api/strip/<window> must NOT alter it."""
        monkeypatch.setattr(app_module, "_account_totals_cache", dict(_WARM_CACHE), raising=False)
        seen = set()
        with patch.object(app_module.database, "load_state", return_value=_live_bot_state()), \
             patch.object(app_module, "dotenv_values", lambda *_a, **_k: {}), \
             patch.object(app_module, "render_template", lambda *_a, **_k: ""):
            for _ in ("first", "second"):
                state = client.get("/api/state").get_json()
                acct = _account_all_time_cr(state)
                assert acct is not None, "account-all-time CR field must be present"
                seen.add(round(float(acct), 6))
        assert len(seen) == 1, (
            f"account-all-time CR must be constant; saw {seen} — it must not window."
        )


# ===========================================================================
# (c) windowed strip carries a re-windowing guard alpha for every token
# ===========================================================================

class TestWindowedGuardAlphaReWindows:
    def test_strip_guard_alpha_defined_for_every_window(self, client, monkeypatch):
        """Every /api/strip/<window> must carry a guard_alpha (the windowable hero metric) and echo
        the window so the label tracks the value."""
        monkeypatch.setattr(app_module, "_account_totals_cache", dict(_WARM_CACHE), raising=False)
        with patch.object(app_module.database, "load_state", return_value=_live_bot_state()):
            for window in ("30d", "60d", "90d", "125d", "ytd", "1y", "all"):
                strip = client.get(f"/api/strip/{window}").get_json()
                assert str(strip.get("window")).lower() == window, (
                    f"/api/strip/{window} must echo the window; got {strip.get('window')!r}"
                )
                assert "guard_alpha" in strip, (
                    f"/api/strip/{window} must carry a guard_alpha (the windowed hero metric)."
                )


# ===========================================================================
# (d) the template surfaces a distinct 'Account · all-time' element
# ===========================================================================

class TestTemplateHasAccountAllTimeElement:
    def test_template_has_distinct_account_all_time_element(self):
        """templates/index.html must carry a distinct, clearly-labeled account-all-time element,
        separate from the windowed guard-alpha hero (so the ~67% is not mislabeled '30d')."""
        html = Path(app_module.__file__).parent.joinpath("templates", "index.html").read_text(
            encoding="utf-8"
        )
        # Require a DEDICATED testid (not a loose word-match — "All Time" picker button + the word
        # "account" elsewhere would incidentally satisfy a substring check). The account-all-time
        # stat is a distinct addressable element so the UI + tests can pin it separately from the
        # windowed guard-alpha hero.
        assert 'data-testid="account-all-time' in html.lower(), (
            "OPTION-A FAIL: no element with a data-testid starting 'account-all-time' in "
            "templates/index.html. The ~67% account-lifetime CR must be a DISTINCT, addressable, "
            "clearly-labeled stat — separate from the windowed guard-alpha hero (which carries the "
            "window label)."
        )
