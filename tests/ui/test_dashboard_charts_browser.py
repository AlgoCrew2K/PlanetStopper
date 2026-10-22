"""Live render of the dashboard ``/``: charts drawn from first paint, readiness,
entry animation kept, dial arc and chart colours following the theme.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_dashboard_charts_browser.py``).
Firefox headless through Playwright, imported inside a fixture so collection
works on a host without Playwright. Never skips: a missing launcher URL or a
missing Playwright fails with a message naming it.

The server is the PM's Mode A launcher (a production-database copy, outbound
network refused, scheduler off), named by ``UX_LAUNCHER_URL``. Chart.js loads
in the browser from its CDN. Readiness is ``body[data-dashboard-ready="1"]``,
never network idle and never a server-rendered selector.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.browser

READY_SELECTOR = 'body[data-dashboard-ready="1"]'
READY_TIMEOUT_MS = 60_000

_PROBE_CHARTS = """
() => {
  const heroCanvas = document.getElementById('cum-chart');
  const hero = Chart.getChart(heroCanvas);
  const sparks = Array.from(document.querySelectorAll('[data-testid="card-spark"][data-sym-id]')).map((c) => {
    const chart = Chart.getChart(c);
    const wrapper = c.parentElement;
    return {
      id: c.getAttribute('data-sym-id'),
      hasChart: !!chart,
      points: chart ? chart.data.labels.length : 0,
      status: ((wrapper && wrapper.textContent) || '').trim(),
    };
  });
  return {
    chartVersion: typeof Chart === 'undefined' ? null : Chart.version,
    hero: hero ? { points: hero.data.labels.length, lines: hero.data.datasets.map((d) => d.data.length) } : null,
    heroEmptyShown: !!document.getElementById('hero-chart-empty') && !document.getElementById('hero-chart-empty').hidden,
    sparks: sparks,
    ready: document.body.getAttribute('data-dashboard-ready'),
    refreshCount: document.body.getAttribute('data-refresh-count'),
  };
}
"""


@pytest.fixture(scope="module")
def launcher_url() -> str:
    url = os.environ.get("UX_LAUNCHER_URL", "").strip()
    if not url:
        pytest.fail(
            "UX_LAUNCHER_URL is not set: the PM sets it to the Mode A launcher "
            "(production-database copy) serving this head"
        )
    return url.rstrip("/")


@pytest.fixture(scope="module")
def firefox():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"Playwright is not installed on this host: {exc}")
    with sync_playwright() as playwright:
        try:
            browser = playwright.firefox.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 - name what is missing, never skip
            pytest.fail(f"Playwright's Firefox could not be launched: {exc}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(firefox):
    context = firefox.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def _assert_first_paint(probe: dict) -> None:
    assert probe["chartVersion"] is not None, (
        "Chart is undefined in the browser: BLOCKED, not judged"
    )
    assert probe["hero"] is not None or probe["heroEmptyShown"], (
        "the hero shows neither a chart nor its empty text"
    )
    if probe["hero"] is not None:
        assert probe["hero"]["points"] > 0
        assert all(n == probe["hero"]["points"] for n in probe["hero"]["lines"]), probe["hero"]
    assert probe["sparks"], "the page holds no sparkline canvases"
    for spark in probe["sparks"]:
        assert spark["hasChart"] or spark["status"], (
            f"card {spark['id']} shows neither a chart nor a status text"
        )


def test_hero_and_sparklines_are_drawn_while_the_state_request_is_held(page, launcher_url):
    held = []
    page.route("**/api/state", lambda route: held.append(route))  # never fulfilled: stays pending
    page.goto(launcher_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
    probe = page.evaluate(_PROBE_CHARTS)
    _assert_first_paint(probe)
    assert held, "the page never requested /api/state"
    assert probe["refreshCount"] is None, "no refresh may have settled while /api/state is held"


def test_a_second_quick_navigation_draws_everything(page, launcher_url):
    try:
        page.goto(launcher_url + "/", wait_until="commit")
    except Exception:  # noqa: BLE001 - the first navigation is superseded on purpose
        pass
    page.goto(launcher_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
    _assert_first_paint(page.evaluate(_PROBE_CHARTS))


def test_every_chart_keeps_its_entry_animation_and_readiness_follows_the_last_one(
    page, launcher_url
):
    page.goto(launcher_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
    result = page.evaluate(
        """
        () => {
          const charts = Object.values(Chart.instances);
          return {
            count: charts.length,
            animationDisabled: charts.filter((c) => c.options.animation === false).map((c) => c.canvas.id || c.canvas.getAttribute('data-sym-id')),
            stillAnimating: charts.filter((c) => Chart.animator.running(c)).length,
          };
        }
        """
    )
    assert result["count"] > 0
    assert result["animationDisabled"] == [], "a chart was built with its animation disabled"
    assert result["stillAnimating"] == 0, (
        "readiness was signalled while an entry animation was still running"
    )


def test_refresh_count_grows_after_each_refresh_and_charts_keep_their_instances(page, launcher_url):
    page.goto(launcher_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
    page.wait_for_selector('body[data-refresh-count="1"]', timeout=READY_TIMEOUT_MS)
    page.evaluate("() => { window.__chartIds = Object.values(Chart.instances).map((c) => c.id); }")
    # the 30 s poll is the refresh (window.loadState belongs to a later cycle)
    page.wait_for_selector('body[data-refresh-count="2"]', timeout=READY_TIMEOUT_MS)
    same = page.evaluate(
        "() => JSON.stringify(Object.values(Chart.instances).map((c) => c.id)) === JSON.stringify(window.__chartIds)"
    )
    assert same, "a refresh rebuilt or replaced a chart"


def test_theme_change_recolours_the_charts_and_the_dial_arc(page, launcher_url):
    page.goto(launcher_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
    page.wait_for_selector('body[data-refresh-count="1"]', timeout=READY_TIMEOUT_MS)
    page.evaluate("() => window.studioTweaks.set('theme', 'dark')")
    result = page.evaluate(
        """
        () => {
          const css = getComputedStyle(document.documentElement);
          const token = (n) => css.getPropertyValue(n).trim();
          const hero = Chart.getChart(document.getElementById('cum-chart'));
          const probe = document.createElement('span');
          document.body.appendChild(probe);
          const arcs = Array.from(document.querySelectorAll('[data-testid="mc-dial"] .mc-arc')).map((arc) => {
            probe.style.color = arc.style.stroke;
            return { reference: arc.style.stroke, computed: getComputedStyle(arc).stroke, expected: getComputedStyle(probe).color };
          });
          probe.remove();
          return {
            theme: document.documentElement.getAttribute('data-theme'),
            heroBot: hero ? hero.data.datasets[0].borderColor : null,
            heroHeld: hero ? hero.data.datasets[1].borderColor : null,
            accent: token('--studio-accent'),
            dim: token('--studio-ink-dim'),
            arcs: arcs,
          };
        }
        """
    )
    assert result["theme"] == "dark"
    assert result["heroBot"] == result["accent"], "the hero bot line did not take the dark accent"
    assert result["heroHeld"] == result["dim"], "the hero held line did not take the dark ink-dim"
    assert result["arcs"], "the page holds no MC dial arcs"
    for arc in result["arcs"]:
        assert arc["reference"].startswith("var(--studio-"), (
            f"arc stroke is not a token reference: {arc['reference']}"
        )
        assert arc["computed"] == arc["expected"], (
            "the dial arc's computed stroke is not the dark token's colour"
        )
