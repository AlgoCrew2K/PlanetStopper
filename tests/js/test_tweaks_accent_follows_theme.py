"""With no accent picked the theme's own accent applies; a picked accent still applies.

Real call path: the real ``static/tweaks.js`` runs under node against the stand-in page of
tests/js/test_tweaks_storage.py (a root that records the properties written to it, a counting
``localStorage``, the window's event dispatch). The stand-in answers the script's read of the light
accent token as the light value, as a browser does while the stylesheet's light block is the only one
applied, which is when the script computes its default.

Why it matters: the script used to take its default accent from that computed read (always the light
value, because the dark attribute is not on the root yet) and write it inline on the root. An inline
custom property beats the dark block of the stylesheet, so every accent text in the dark theme took
the light accent, which is below the contrast bar on the dark paper.

Contract pinned
* Nothing picked (an empty store, or a store written by an earlier version that persisted the light
  default as if it were a pick): no accent is written inline, in the light and in the dark theme, at
  load, after the document is ready, and after a theme change. An empty-string write counts as none.
* A picked accent is written inline and stays: at load, after the document is ready, in either theme,
  after the theme changes, and after a reload from the store the script itself wrote.
* Picking an accent never changes the theme and the theme never changes a pick.
* A different pick replaces the earlier one.
"""

from __future__ import annotations

import json

import pytest

from tests.js.test_tweaks_storage import _node

LIGHT_DEFAULT = "#1f7a4d"  # what the stand-in page's computed read answers (the light token value)
PICKED = "#0b6cc4"
OTHER_PICK = "#b04420"

# A store as the version before this fix wrote it on first visit: every default, the light accent
# included, persisted as though the operator had chosen them.
LEGACY_SEEDED = {
    "theme": "light",
    "density": "balanced",
    "accent": LIGHT_DEFAULT,
    "typeface": "Manrope",
    "mathOverlays": True,
    "numFormat": "full",
}

INLINE = "style:--studio-accent"

NO_INLINE = (None, "")


def _store(extra: dict | None = None) -> str:
    return json.dumps(json.dumps({**LEGACY_SEEDED, **(extra or {})}))


class TestNothingPicked:
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_an_empty_store_writes_no_inline_accent_at_run_or_when_the_document_is_ready(
        self, theme
    ):
        r = _node(
            r"""
        const page = makePage({}).run();
        if ('__THEME__' === 'dark') page.tools().set('theme', 'dark');
        const atRun = page.attrs['__KEY__'];
        page.domReady();
        return { atRun: atRun === undefined ? null : atRun,
                 afterReady: page.attrs['__KEY__'] === undefined ? null : page.attrs['__KEY__'],
                 theme: page.root.getAttribute('data-theme'), thrown: page.thrown };
        """.replace("__THEME__", theme).replace("__KEY__", INLINE)
        )
        assert r["thrown"] is None
        assert r["theme"] == theme
        assert r["atRun"] in NO_INLINE, f"an accent nobody picked was written inline: {r['atRun']}"
        assert r["afterReady"] in NO_INLINE, (
            f"written once the document was ready: {r['afterReady']}"
        )

    def test_changing_the_theme_without_a_pick_still_leaves_the_accent_to_the_theme(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        page.domReady();
        page.tools().set('theme', 'dark');
        const dark = page.attrs['__KEY__'];
        page.tools().set('theme', 'light');
        const light = page.attrs['__KEY__'];
        page.tools().set('density', 'compact');
        return { dark: dark === undefined ? null : dark, light: light === undefined ? null : light,
                 afterOtherChange: page.attrs['__KEY__'] === undefined ? null : page.attrs['__KEY__'] };
        """.replace("__KEY__", INLINE)
        )
        for name, value in r.items():
            assert value in NO_INLINE, (
                f"{name}: an accent nobody picked was written inline: {value}"
            )

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_a_store_written_by_the_earlier_version_is_not_taken_for_a_pick(self, theme):
        r = _node(
            r"""
        const page = makePage({ studioTweaks: __STORED__ }).run();
        page.domReady();
        return { inline: page.attrs['__KEY__'] === undefined ? null : page.attrs['__KEY__'],
                 theme: page.root.getAttribute('data-theme') };
        """.replace("__STORED__", _store({"theme": theme})).replace("__KEY__", INLINE)
        )
        assert r["theme"] == theme
        assert r["inline"] in NO_INLINE, (
            f"the light default an earlier version persisted still applies inline: {r['inline']}"
        )


class TestAPickedAccentStillApplies:
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_a_pick_is_written_inline_immediately_and_in_either_theme(self, theme):
        r = _node(
            r"""
        const page = makePage({}).run();
        page.domReady();
        page.tools().set('theme', '__THEME__');
        page.tools().set('accent', '__PICK__');
        return { inline: page.attrs['__KEY__'], theme: page.root.getAttribute('data-theme') };
        """.replace("__THEME__", theme)
            .replace("__PICK__", PICKED)
            .replace("__KEY__", INLINE)
        )
        assert r["theme"] == theme
        assert r["inline"] == PICKED

    def test_a_pick_survives_a_reload_from_the_store_the_script_wrote(self):
        r = _node(
            r"""
        const first = makePage({}).run();
        first.domReady();
        first.tools().set('accent', '__PICK__');
        const stored = first.store.get('studioTweaks');
        const second = makePage({ studioTweaks: stored }).run();
        const atRun = second.attrs['__KEY__'];
        second.domReady();
        return { atRun: atRun, afterReady: second.attrs['__KEY__'] };
        """.replace("__PICK__", PICKED).replace("__KEY__", INLINE)
        )
        assert r["atRun"] == PICKED, "the pick is not on the root when the script runs"
        assert r["afterReady"] == PICKED

    def test_a_pick_stays_when_the_theme_changes_and_the_theme_stays_when_a_pick_is_made(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        page.domReady();
        page.tools().set('theme', 'dark');
        page.tools().set('accent', '__PICK__');
        const afterPick = { theme: page.root.getAttribute('data-theme'), inline: page.attrs['__KEY__'] };
        page.tools().set('theme', 'light');
        const afterTheme = { theme: page.root.getAttribute('data-theme'), inline: page.attrs['__KEY__'] };
        return { afterPick: afterPick, afterTheme: afterTheme };
        """.replace("__PICK__", PICKED).replace("__KEY__", INLINE)
        )
        assert r["afterPick"] == {"theme": "dark", "inline": PICKED}
        assert r["afterTheme"] == {"theme": "light", "inline": PICKED}

    def test_a_second_pick_replaces_the_first(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        page.domReady();
        page.tools().set('accent', '__A__');
        page.tools().set('accent', '__B__');
        return page.attrs['__KEY__'];
        """.replace("__A__", PICKED)
            .replace("__B__", OTHER_PICK)
            .replace("__KEY__", INLINE)
        )
        assert r == OTHER_PICK

    def test_a_pick_made_in_another_tab_applies_through_the_storage_event(self):
        stored = json.dumps(json.dumps({**LEGACY_SEEDED, "accent": PICKED}))
        r = _node(
            r"""
        const page = makePage({}).run();
        page.domReady();
        page.store.set('studioTweaks', __STORED__);
        page.storage({ key: 'studioTweaks' });
        return page.attrs['__KEY__'];
        """.replace("__STORED__", stored).replace("__KEY__", INLINE)
        )
        assert r == PICKED


class TestAPickEqualToTheEarlierVersionsSeedIsARealPick:
    """Swatch 1 is the very value the earlier version seeded, so only a marker can tell them apart."""

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_swatch_one_is_applied_at_once_persists_across_reloads_and_reads_as_the_pick(
        self, theme
    ):
        r = _node(
            r"""
        const first = makePage({}).run();
        first.domReady();
        first.tools().set('theme', '__THEME__');
        first.tools().set('accent', '__PICK__');
        const afterPick = first.attrs['__KEY__'] === undefined ? null : first.attrs['__KEY__'];
        const carried = Object.fromEntries(first.store);
        const second = makePage(carried).run();
        const atRun = second.attrs['__KEY__'] === undefined ? null : second.attrs['__KEY__'];
        second.domReady();
        const third = makePage(Object.fromEntries(second.store)).run();
        third.domReady();
        return { afterPick: afterPick, atRun: atRun,
                 afterReady: second.attrs['__KEY__'] === undefined ? null : second.attrs['__KEY__'],
                 picked: second.tools().get('accent'), thirdLoad: third.attrs['__KEY__'] === undefined ? null : third.attrs['__KEY__'],
                 theme: second.root.getAttribute('data-theme') };
        """.replace("__THEME__", theme)
            .replace("__PICK__", LIGHT_DEFAULT)
            .replace("__KEY__", INLINE)
        )
        assert r["afterPick"] == LIGHT_DEFAULT, (
            "a click on swatch 1 removed the inline accent, so the theme's own accent shows"
        )
        assert r["atRun"] == LIGHT_DEFAULT, "the swatch 1 pick is lost on reload"
        assert r["afterReady"] == LIGHT_DEFAULT
        assert r["picked"] == LIGHT_DEFAULT, "no swatch reads active: the stored pick reads as none"
        assert r["thirdLoad"] == LIGHT_DEFAULT, "the pick did not survive a second reload"
        assert r["theme"] == theme

    def test_a_store_from_before_the_marker_is_cleared_once_and_rewritten_on_its_first_read(self):
        r = _node(
            r"""
        const first = makePage({ studioTweaks: __STORED__ }).run();
        const rewritten = JSON.parse(first.store.get('studioTweaks'));
        return { rewrittenAccent: rewritten.accent, inline: first.attrs['__KEY__'] === undefined ? null : first.attrs['__KEY__'],
                 read: first.tools().get('accent') };
        """.replace("__STORED__", _store()).replace("__KEY__", INLINE)
        )
        assert r["inline"] in NO_INLINE
        assert r["read"] in NO_INLINE
        assert r["rewrittenAccent"] in NO_INLINE, (
            "the earlier version's stored accent was not rewritten on its first read, so it would "
            "read as a pick once the marker exists"
        )

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_after_the_one_time_clearing_a_pick_of_swatch_one_persists(self, theme):
        r = _node(
            r"""
        const legacy = makePage({ studioTweaks: __STORED__ }).run();
        legacy.domReady();
        legacy.tools().set('theme', '__THEME__');
        legacy.tools().set('accent', '__PICK__');
        const afterPick = legacy.attrs['__KEY__'] === undefined ? null : legacy.attrs['__KEY__'];
        const reloaded = makePage(Object.fromEntries(legacy.store)).run();
        const atRun = reloaded.attrs['__KEY__'] === undefined ? null : reloaded.attrs['__KEY__'];
        reloaded.domReady();
        return { afterPick: afterPick, atRun: atRun, afterReady: reloaded.attrs['__KEY__'] === undefined ? null : reloaded.attrs['__KEY__'] };
        """.replace("__STORED__", _store())
            .replace("__THEME__", theme)
            .replace("__PICK__", LIGHT_DEFAULT)
            .replace("__KEY__", INLINE)
        )
        assert r["afterPick"] == LIGHT_DEFAULT
        assert r["atRun"] == LIGHT_DEFAULT, (
            "a swatch 1 pick made after the one-time clearing is lost"
        )
        assert r["afterReady"] == LIGHT_DEFAULT

    def test_the_one_time_clearing_leaves_a_stored_pick_of_another_accent_alone(self):
        r = _node(
            r"""
        const page = makePage({ studioTweaks: __STORED__ }).run();
        const stored = JSON.parse(page.store.get('studioTweaks'));
        return { inline: page.attrs['__KEY__'], stored: stored.accent };
        """.replace("__STORED__", _store({"accent": PICKED})).replace("__KEY__", INLINE)
        )
        assert r["inline"] == PICKED
        assert r["stored"] == PICKED
