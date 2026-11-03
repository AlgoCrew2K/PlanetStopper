"""One reading of the pages' headings and panel children, shared by the producer and the check.

TEST-ONLY. ``tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py``
writes ``baseline.json`` from a tree whose production files equal the base commit, and
``tests/ui/test_heading_margin_shared_browser.py`` reads the head through the same functions and
compares. Both call :func:`capture_group`, so a difference between the two is a difference
between the trees and never between two readers.

What is read, per view (a page, plus each Advisor tab and each Settings section after its
button is pressed), per width (1440 and 375) and per theme (light and dark): every element the
heading rule can reach (``.section-header`` and every child of ``.hero-section``) that the view
shows, in document order, or the whole body for ``/login`` (the rule matches nothing there, so
the page is read whole). Per element: tag, sorted classes, the classes of its parent, whether
the shared rule's own selector matches it, the first 40 characters of its text, the four margin
longhands and the sha256 of its computed style (every property but the box-size-resolved ones
named in :data:`BOX_SIZE_RESOLVED`).

The app clock and the browser clock are both fixed at :data:`CAPTURE_INSTANT`, so the market
state and every clock-derived text are the same on every run. The record holds the browser
version: a different version is a different render, and the check says so instead of comparing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.app._refresh_timing_state_world import frozen_datetime_class
from tests.ui._shell_support import NARROW, WIDE, open_page, open_probe, seed_every_page

ET = ZoneInfo("America/New_York")

# A Wednesday inside the regular session, so the dashboard renders its open-market state.
CAPTURE_INSTANT = datetime(2026, 10, 21, 11, 0, 0, tzinfo=ET)

WIDTHS = {"1440": WIDE, "375": NARROW}
THEMES = ("light", "dark")

PAGE_PATHS = {
    "dashboard": "/",
    "history": "/history",
    "performance": "/performance",
    "login": "/login",
    "ai-advisor": "/ai-advisor",
    "settings": "/settings",
}

# What a page switches between inside one load: the button attribute and the view prefix.
SWITCHERS = {
    "ai-advisor": ("button[data-tab]", "data-tab", "tab"),
    "settings": ("button[data-section]", "data-section", "section"),
}

ELEMENT_SELECTOR = ".section-header, .hero-section > *"
SHARED_RULE_SELECTOR = ".hero-section > .section-header:first-child"
LOGIN_BODY_SELECTOR = "body *"

# Properties whose resolved value follows the box's content or its containing block, not the
# cascade the heading rule can change.
BOX_SIZE_RESOLVED = frozenset(
    {
        "width",
        "height",
        "inline-size",
        "block-size",
        "top",
        "right",
        "bottom",
        "left",
        "transform-origin",
        "perspective-origin",
    }
)
_INSET_PREFIX = "inset"

MARGINS = ("margin-top", "margin-right", "margin-bottom", "margin-left")
TEXT_CHARS = 40
SETTLE_MS = 1500
SWITCH_SETTLE_MS = 500

_READ_ELEMENTS = """
([selector, sharedSelector, margins, visibleOnly]) => {
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    if (visibleOnly && el.getClientRects().length === 0) continue;
    const style = getComputedStyle(el);
    const props = [];
    for (let i = 0; i < style.length; i++) {
      const name = style.item(i);
      props.push([name, style.getPropertyValue(name)]);
    }
    const parent = el.parentElement;
    out.push({
      tag: el.tagName.toLowerCase(),
      classes: [...el.classList].sort(),
      parent_classes: parent ? [...parent.classList].sort() : [],
      shared_rule_matches: el.matches(sharedSelector),
      text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
      margins: Object.fromEntries(margins.map((m) => [m, style.getPropertyValue(m)])),
      props,
    });
  }
  return out;
}
"""


def prepare(tmp_path, setattr_fn) -> None:
    """Seed every page's state and fix the app clock; ``setattr_fn`` is ``monkeypatch.setattr``
    in a test (undone at teardown) or ``setattr`` in the producer."""
    import app as app_module

    seed_every_page(tmp_path, setattr_fn)
    setattr_fn(app_module, "datetime", frozen_datetime_class(CAPTURE_INSTANT))


def _digest(props: list[list[str]]) -> str:
    kept = sorted(
        f"{name}: {value}"
        for name, value in props
        if name not in BOX_SIZE_RESOLVED and not name.startswith(_INSET_PREFIX)
    )
    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


def read_elements(page, selector: str, *, visible_only: bool) -> list[dict]:
    raw = page.evaluate(
        _READ_ELEMENTS, [selector, SHARED_RULE_SELECTOR, list(MARGINS), visible_only]
    )
    return [
        {
            "tag": item["tag"],
            "classes": item["classes"],
            "parent_classes": item["parent_classes"],
            "shared_rule_matches": item["shared_rule_matches"],
            "text": item["text"][:TEXT_CHARS],
            "margins": item["margins"],
            "style_digest": _digest(item["props"]),
        }
        for item in raw
    ]


def _switch_values(page, button_selector: str, attribute: str) -> list[str]:
    return page.evaluate(
        "([sel, attr]) => [...document.querySelectorAll(sel)].map((b) => b.getAttribute(attr))",
        [button_selector, attribute],
    )


def _press(page, button_selector: str, attribute: str, value: str) -> None:
    page.evaluate(
        "([sel, attr, value]) => { const b = [...document.querySelectorAll(sel)]"
        ".find((x) => x.getAttribute(attr) === value); b.click(); }",
        [button_selector, attribute, value],
    )
    page.wait_for_timeout(SWITCH_SETTLE_MS)


def view_key(page_id: str, sub: str, width_key: str, theme: str) -> str:
    return f"{page_id}|{sub}|{width_key}|{theme}"


def capture_group(firefox, server, page_id: str, width_key: str, theme: str) -> dict[str, list]:
    """Every view of ``page_id`` at one width and theme: ``{view key: [element record, ...]}``."""
    width, height = WIDTHS[width_key]
    views: dict[str, list] = {}
    with open_probe(
        firefox, server, width=width, height=height, theme="dark" if theme == "dark" else None
    ) as probe:
        probe.context.clock.install(time=CAPTURE_INSTANT)
        open_page(probe, PAGE_PATHS[page_id])
        probe.page.wait_for_timeout(SETTLE_MS)
        whole = page_id == "login"
        selector = LOGIN_BODY_SELECTOR if whole else ELEMENT_SELECTOR
        views[view_key(page_id, "load", width_key, theme)] = read_elements(
            probe.page, selector, visible_only=not whole
        )
        if page_id in SWITCHERS:
            button_selector, attribute, prefix = SWITCHERS[page_id]
            for value in _switch_values(probe.page, button_selector, attribute):
                _press(probe.page, button_selector, attribute, value)
                views[view_key(page_id, f"{prefix}:{value}", width_key, theme)] = read_elements(
                    probe.page, selector, visible_only=True
                )
    return views


def capture_everything(firefox, server) -> dict:
    """The whole record, in the shape ``baseline.json`` holds."""
    views: dict[str, list] = {}
    for page_id in PAGE_PATHS:
        for width_key in WIDTHS:
            for theme in THEMES:
                views.update(capture_group(firefox, server, page_id, width_key, theme))
    return {"browser_version": firefox.version, "views": views}


def groups() -> list[tuple[str, str, str]]:
    """Every ``(page id, width key, theme)`` the record covers."""
    return [(p, w, t) for p in PAGE_PATHS for w in WIDTHS for t in THEMES]


def views_of_group(record_views: dict, page_id: str, width_key: str, theme: str) -> dict:
    """The record's views for one group (the keys end in ``|<width>|<theme>``)."""
    suffix = f"|{width_key}|{theme}"
    prefix = f"{page_id}|"
    return {k: v for k, v in record_views.items() if k.startswith(prefix) and k.endswith(suffix)}
