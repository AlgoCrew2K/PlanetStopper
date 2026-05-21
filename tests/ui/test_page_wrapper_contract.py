"""
RED tests for the shared page-layout contract (Studio v3 CSS consolidation).

THE BUG
-------
The Dashboard (route "/", templates/index.html) renders with a correct
full-width layout. Performance ("/performance"), History ("/history"),
AI Advisor ("/ai-advisor") and Settings ("/settings") render shifted right
with a large empty left margin.

THE ROOT CAUSE (not a band-aid -- the real one)
-----------------------------------------------
Studio v3 page templates are standalone HTML documents with no Jinja base
template. Each template redefines its OWN page-layout skeleton -- the `body`
page padding and the `.page-wrap` / `.settings-layout` outer wrapper rules --
inside its own inline `<style>` block. Five copies of the same skeleton drifted
apart:

    index.html        body: no padding   .page-wrap: max-width:100%; margin:0 auto; padding:24px 28px 48px
    performance.html  body: padding clamp .page-wrap: width:100%; box-sizing:border-box
    ai_advisor.html   body: padding 2rem  .page-wrap: width:100%
    history.html      body: padding 2rem  .page-wrap: width:100%; max-width:2400px; margin:0 auto
    settings.html     body: no padding    .settings-layout: flex shell (different primitive)

That duplication IS the tech debt that produced the right-shift bug. The fix
is to consolidate: ONE shared definition of the page-layout skeleton, in a
shared CSS file, consumed by all five pages.

THE CONTRACT (team-lead refined direction)
------------------------------------------
  AC-LC.1  A shared stylesheet `static/layout.css` exists.
  AC-LC.2  layout.css defines the page-wrapper skeleton ONCE -- the core
           positioning rule(s) for `.page-wrap` (box-sizing, centering,
           max-width, horizontal inset).
  AC-LC.3  layout.css drives per-page variation through named CSS custom
           properties (e.g. `--studio-page-max-width`, `--studio-page-inset`)
           so intentional differences are explicit overrides, not forks.
  AC-LC.4  Every one of the five page templates links `layout.css` in <head>.
  AC-LC.5  No page template's inline <style> still defines the page-layout
           skeleton -- specifically no `body` rule with page-level padding,
           and no `.page-wrap` / `.settings-layout` rule that redefines
           `max-width`, `margin`, `padding`, or `box-sizing`. The skeleton
           lives ONLY in layout.css.
  AC-LC.6  Intentional per-page variation (history's ultrawide cap,
           performance's fluid inset, settings' full-bleed) is expressed
           ONLY as named CSS-variable overrides -- never as a duplicated
           skeleton rule block. (Enforced by AC-LC.5: a duplicated skeleton
           rule is forbidden; a `--studio-page-*: ...` override is allowed.)
  AC-LC.7  settings.html consumes the shared skeleton like the other four.
           Its sidebar / save-discard footer / scroll rules are INNER
           structure and remain in settings.html -- they are not skeleton
           and are not flagged.
  AC-LC.8  history.html's intentional ultrawide cap survives the refactor as
           a named CSS-variable override. history.html must set the page
           max-width custom property to its 2400px ultrawide value (V-32) --
           an AFFIRMATIVE contract so a future "cleanup" cannot silently
           delete the intentional variation along with the drift.
  AC-LC.9  layout.css resets the body box model: it declares a `body` rule
           with both margin:0 and padding:0. Without this the non-Dashboard
           pages inherit the UA-default 8px body margin and render inset
           relative to the Dashboard -- the residual right-shift the Chrome
           visual pass caught.
  AC-LC.10 No page template's inline <style> declares a `body` rule with its
           own margin or padding -- the body box-model reset lives ONLY in
           layout.css.
  AC-LC.11 layout.css contains the shared universal reset -- a rule whose
           selector covers `*` declaring box-sizing:border-box. Three of the
           five pages currently lack any universal box-sizing reset and
           render content-box; the reset must be shared in layout.css.
  AC-LC.12 No page template's inline <style> contains its own universal `*`
           box-sizing reset -- the universal reset lives ONLY in layout.css
           so all five pages share one base reset.

These assert the design-system contract -- single source of truth, no
per-template skeleton duplication, variation via named overrides -- NOT
computed pixel values and NOT specific literal max-width/padding numbers
(those are sty-ux's canonical-spec call). Pure static parsing of template
source + the shared CSS file. tests/ui/.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "templates"
_STATIC = _REPO_ROOT / "static"
_LAYOUT_CSS = _STATIC / "layout.css"

# The five page templates. ALL FIVE -- including settings.html -- must consume
# the shared skeleton (team-lead direction supersedes the earlier Option A
# carve-out).
PAGE_TEMPLATES = (
    "index.html",
    "performance.html",
    "ai_advisor.html",
    "history.html",
    "settings.html",
)

# Skeleton selectors -- the outer page-layout wrappers. A page template must
# not redefine these with skeleton properties in its own <style>.
SKELETON_WRAPPER_SELECTORS = (".page-wrap", ".settings-layout")

# Properties that constitute the page-layout "skeleton". A `.page-wrap` /
# `.settings-layout` rule declaring any of these in a template is duplication.
SKELETON_PROPERTIES = ("max-width", "margin", "padding", "box-sizing")

# Horizontal-padding properties for the body-padding check.
_HORIZONTAL_PADDING_PROPS = (
    "padding",
    "padding-left",
    "padding-right",
    "padding-inline",
    "padding-inline-start",
    "padding-inline-end",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_template(name: str) -> str:
    path = _TEMPLATES / name
    assert path.is_file(), f"Expected page template {name!r} at {path}."
    return path.read_text(encoding="utf-8")


def _inline_style_css(html: str) -> str:
    """Return the concatenated, comment-stripped contents of inline <style>."""
    blocks = re.findall(
        r"<style\b[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL
    )
    css = "\n".join(blocks)
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _iter_css_rules(css: str):
    """Yield (selector_list, {prop: value}) for every rule in a CSS string.

    `selector_list` is the list of comma-separated selector strings. @media and
    other at-rules are descended into so nested rules are also yielded.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for rule_match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        head = rule_match.group(1).strip()
        body = rule_match.group(2)
        if head.startswith("@"):
            # @media / @supports wrapper -- inner rules are matched separately
            # by the same regex on subsequent iterations, so skip the wrapper.
            continue
        selectors = [s.strip() for s in head.split(",") if s.strip()]
        decls: dict[str, str] = {}
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            prop, _, value = decl.partition(":")
            decls[prop.strip().lower()] = " ".join(value.split())
        yield selectors, decls


def _head_section(html: str) -> str:
    match = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _links_layout_css(html: str) -> bool:
    """True if the template's <head> links static/layout.css."""
    head = _head_section(html)
    for link_match in re.finditer(r"<link\b[^>]*>", head, re.IGNORECASE):
        tag = link_match.group(0)
        if "stylesheet" not in tag.lower():
            continue
        href_match = re.search(r'href\s*=\s*"([^"]*)"', tag, re.IGNORECASE)
        if href_match and "layout.css" in href_match.group(1):
            return True
    return False


def _has_nonzero_horizontal_padding(decls: dict[str, str]) -> bool:
    for prop in _HORIZONTAL_PADDING_PROPS:
        value = decls.get(prop)
        if value is None:
            continue
        if value.strip() not in ("0", "0px", "0 0", "0 0 0 0"):
            return True
    return False


# ===========================================================================
# AC-LC.1 -- shared layout.css exists
# ===========================================================================

class TestSharedLayoutStylesheetExists:
    """The consolidation hinges on a single shared layout stylesheet."""

    def test_layout_css_file_exists(self):
        """AC-LC.1: static/layout.css exists."""
        assert _LAYOUT_CSS.is_file(), (
            f"static/layout.css must exist -- it is the single shared source "
            f"of truth for the page-layout skeleton. Expected at {_LAYOUT_CSS}."
        )


# ===========================================================================
# AC-LC.2 -- layout.css defines the .page-wrap skeleton once
# ===========================================================================

class TestLayoutCssDefinesSkeleton:
    """layout.css must contain the canonical .page-wrap skeleton rule."""

    def test_layout_css_defines_page_wrap_skeleton(self):
        """AC-LC.2: layout.css declares .page-wrap with skeleton properties."""
        assert _LAYOUT_CSS.is_file(), "static/layout.css must exist (AC-LC.1)."
        css = _LAYOUT_CSS.read_text(encoding="utf-8")

        page_wrap_props: dict[str, str] = {}
        for selectors, decls in _iter_css_rules(css):
            if ".page-wrap" in selectors:
                page_wrap_props.update(decls)

        assert page_wrap_props, (
            "static/layout.css must define a `.page-wrap` rule -- the shared "
            "page-wrapper skeleton."
        )
        declared_skeleton = [p for p in SKELETON_PROPERTIES if p in page_wrap_props]
        assert declared_skeleton, (
            f"static/layout.css `.page-wrap` rule must declare the skeleton "
            f"positioning properties (one or more of {list(SKELETON_PROPERTIES)}). "
            f"Found only: {sorted(page_wrap_props)}."
        )


# ===========================================================================
# AC-LC.3 -- layout.css drives variation via named CSS variables
# ===========================================================================

class TestLayoutCssUsesCssVariables:
    """
    Per-page variation must be a knob, not a fork. The shared skeleton must
    reference at least one named `--studio-page-*` custom property so pages can
    override (e.g. history's ultrawide cap) without duplicating the rule.
    """

    def test_layout_css_skeleton_references_page_css_variable(self):
        """AC-LC.3: layout.css .page-wrap skeleton uses a --studio-page-* var."""
        assert _LAYOUT_CSS.is_file(), "static/layout.css must exist (AC-LC.1)."
        css = _LAYOUT_CSS.read_text(encoding="utf-8")

        page_wrap_values: list[str] = []
        for selectors, decls in _iter_css_rules(css):
            if ".page-wrap" in selectors:
                page_wrap_values.extend(decls.values())

        assert page_wrap_values, (
            "static/layout.css must define a `.page-wrap` rule (AC-LC.2)."
        )
        uses_var = any(
            re.search(r"var\(\s*--studio-page-[\w-]+", v) for v in page_wrap_values
        )
        assert uses_var, (
            "static/layout.css `.page-wrap` skeleton must drive per-page "
            "variation through named CSS custom properties "
            "(e.g. var(--studio-page-max-width), var(--studio-page-inset)). "
            "Intentional per-page differences (history's ultrawide cap, "
            "performance's fluid inset) become named overrides, not forked "
            f"rule blocks. .page-wrap values found: {page_wrap_values!r}"
        )


# ===========================================================================
# AC-LC.4 -- every page template links layout.css
# ===========================================================================

class TestEveryPageLinksLayoutCss:
    """All five page templates must consume the shared stylesheet."""

    @pytest.mark.parametrize("template_name", PAGE_TEMPLATES)
    def test_page_links_layout_css(self, template_name):
        """AC-LC.4: page <head> links static/layout.css."""
        html = _read_template(template_name)
        assert _links_layout_css(html), (
            f"{template_name} must link static/layout.css in its <head> "
            f'(<link rel="stylesheet" href="{{{{ url_for(\'static\', '
            f"filename='layout.css') }}}}\">). Every page consumes the shared "
            f"page-layout skeleton."
        )


# ===========================================================================
# AC-LC.5 + AC-LC.6 -- no per-template skeleton duplication
# ===========================================================================

class TestNoPerTemplateSkeletonDuplication:
    """
    The skeleton lives ONLY in layout.css. A page template's inline <style>
    must not redefine it. Two forbidden forms:
      (a) a `body` rule with page-level horizontal padding;
      (b) a `.page-wrap` / `.settings-layout` rule declaring a skeleton
          property (max-width / margin / padding / box-sizing).
    Named CSS-variable overrides are NOT skeleton rules -- they are allowed.
    """

    @pytest.mark.parametrize("template_name", PAGE_TEMPLATES)
    def test_template_body_has_no_page_level_padding(self, template_name):
        """AC-LC.5: no page template inline <style> sets body page padding."""
        css = _inline_style_css(_read_template(template_name))
        for selectors, decls in _iter_css_rules(css):
            if "body" in selectors and _has_nonzero_horizontal_padding(decls):
                pytest.fail(
                    f"{template_name} inline <style> defines a `body` rule "
                    f"with page-level horizontal padding ({decls!r}). The "
                    f"page-layout skeleton -- including page inset -- must "
                    f"live ONLY in static/layout.css. Remove the per-template "
                    f"body padding."
                )

    @pytest.mark.parametrize("template_name", PAGE_TEMPLATES)
    def test_template_does_not_redefine_wrapper_skeleton(self, template_name):
        """AC-LC.5/6: no inline <style> redefines .page-wrap/.settings-layout skeleton."""
        css = _inline_style_css(_read_template(template_name))
        for selectors, decls in _iter_css_rules(css):
            for selector in SKELETON_WRAPPER_SELECTORS:
                if selector not in selectors:
                    continue
                duplicated = [p for p in SKELETON_PROPERTIES if p in decls]
                assert not duplicated, (
                    f"{template_name} inline <style> redefines the page "
                    f"skeleton: `{selector}` rule declares "
                    f"{sorted(duplicated)} ({decls!r}). The skeleton lives "
                    f"ONLY in static/layout.css. Intentional per-page "
                    f"variation must be a named CSS-variable override "
                    f"(e.g. `:root {{ --studio-page-max-width: 2400px; }}`), "
                    f"not a duplicated `{selector}` rule block."
                )


# ===========================================================================
# AC-LC.8 -- history.html's intentional ultrawide cap survives as an override
# ===========================================================================

class TestHistoryUltrawideCapPreservedAsOverride:
    """
    history.html intentionally caps its width at 2400px on ultrawide screens
    (the V-32 ultrawide cap). The consolidation refactor must PRESERVE that
    intentional variation -- re-expressed as a named CSS-variable override, not
    deleted along with the drift.

    This is an AFFIRMATIVE contract: history.html must set the page-max-width
    custom property to its 2400px value. Without this test a future "cleanup"
    could silently drop the intentional cap and nothing would catch it.
    """

    def test_history_sets_max_width_override_variable(self):
        """AC-LC.8: history.html sets a --studio-page-*max-width* override to 2400px."""
        css = _inline_style_css(_read_template("history.html"))

        # Find any --studio-page-* custom-property declaration whose name
        # concerns max-width and whose value is the 2400px ultrawide cap.
        override_decls: dict[str, str] = {}
        for _selectors, decls in _iter_css_rules(css):
            for prop, value in decls.items():
                if prop.startswith("--studio-page-") and "max-width" in prop:
                    override_decls[prop] = value

        assert override_decls, (
            "history.html must preserve its intentional 2400px ultrawide cap "
            "(V-32) as a named CSS-variable override -- e.g. "
            "`:root { --studio-page-max-width: 2400px; }` in its inline "
            "<style>. The cap is intentional design, not drift; the refactor "
            "must re-express it as an override, not delete it. No "
            "--studio-page-*max-width* custom property found in history.html."
        )
        assert any("2400px" in v for v in override_decls.values()), (
            f"history.html's page-max-width override must keep the 2400px "
            f"ultrawide value (V-32). Found override declarations: "
            f"{override_decls!r}."
        )


# ===========================================================================
# AC-LC.9 -- layout.css resets the body box model
# ===========================================================================

def _is_zero(value: str) -> bool:
    """True if a CSS length value is an effective zero (0 / 0px / 0 0 ...)."""
    return value.strip() in ("0", "0px", "0 0", "0 0 0 0", "0px 0px")


class TestLayoutCssResetsBodyBoxModel:
    """
    layout.css must zero the body margin AND padding. Without this reset the
    non-Dashboard pages inherit the UA-default 8px body margin and render
    inset relative to the Dashboard -- the residual right-shift the Chrome
    visual pass caught after the .page-wrap consolidation.
    """

    def test_layout_css_body_rule_zeroes_margin_and_padding(self):
        """AC-LC.9: layout.css `body` rule sets margin:0 and padding:0."""
        assert _LAYOUT_CSS.is_file(), "static/layout.css must exist (AC-LC.1)."
        css = _LAYOUT_CSS.read_text(encoding="utf-8")

        body_props: dict[str, str] = {}
        for selectors, decls in _iter_css_rules(css):
            if "body" in selectors:
                body_props.update(decls)

        assert body_props, (
            "static/layout.css must define a `body` rule that resets the body "
            "box model. Without it the non-Dashboard pages inherit the "
            "UA-default 8px body margin and render inset vs the Dashboard."
        )
        assert "margin" in body_props and _is_zero(body_props["margin"]), (
            f"static/layout.css `body` rule must declare `margin: 0`. "
            f"Found margin={body_props.get('margin')!r}. The UA-default 8px "
            f"body margin is exactly the residual right-shift bug."
        )
        assert "padding" in body_props and _is_zero(body_props["padding"]), (
            f"static/layout.css `body` rule must declare `padding: 0`. "
            f"Found padding={body_props.get('padding')!r}."
        )


# ===========================================================================
# AC-LC.10 -- no per-template body margin/padding
# ===========================================================================

class TestNoPerTemplateBodyBoxModel:
    """
    The body box-model reset lives ONLY in layout.css. A page template's inline
    <style> must not declare its own `body { margin }` / `body { padding }` --
    that is skeleton duplication and is exactly how the 5 pages drifted out of
    pixel-consistency. A universal `*` element reset is a separate concern (not
    a `body` selector) and is intentionally not flagged.
    """

    @pytest.mark.parametrize("template_name", PAGE_TEMPLATES)
    def test_template_body_rule_has_no_margin_or_padding(self, template_name):
        """AC-LC.10: no inline <style> `body` rule declares margin or padding."""
        css = _inline_style_css(_read_template(template_name))
        for selectors, decls in _iter_css_rules(css):
            if "body" not in selectors:
                continue
            box_model = [p for p in ("margin", "padding") if p in decls]
            assert not box_model, (
                f"{template_name} inline <style> `body` rule declares "
                f"{sorted(box_model)} ({decls!r}). The body box-model reset "
                f"must live ONLY in static/layout.css -- a per-template "
                f"`body` margin/padding is skeleton duplication and is how "
                f"the pages drifted out of pixel-consistency. Remove it; "
                f"layout.css owns `body {{ margin:0; padding:0 }}`."
            )


# ===========================================================================
# AC-LC.11 + AC-LC.12 -- shared universal box-sizing reset
# ===========================================================================

def _is_universal_selector(selector: str) -> bool:
    """True if a CSS selector is the universal selector `*` or `*::pseudo`.

    Matches `*`, `*::before`, `*::after`, `*:focus` etc. -- a bare universal
    selector, optionally with a pseudo-element/class. Does NOT match `.foo *`
    (descendant) or `*.bar` (qualified) -- those are scoped, not a global reset.
    """
    s = selector.strip()
    return bool(re.fullmatch(r"\*(?:::?[\w-]+)?", s))


def _universal_box_sizing_rules(css: str) -> list[dict[str, str]]:
    """Return declaration blocks of universal-selector rules that set box-sizing."""
    found: list[dict[str, str]] = []
    for selectors, decls in _iter_css_rules(css):
        if "box-sizing" not in decls:
            continue
        if any(_is_universal_selector(s) for s in selectors):
            found.append(decls)
    return found


class TestLayoutCssOwnsUniversalReset:
    """
    layout.css must contain the shared universal box-sizing reset. Three of the
    five pages currently have no universal reset and render content-box -- a
    real box-model divergence. The reset belongs in the shared stylesheet.
    """

    def test_layout_css_has_universal_border_box_reset(self):
        """AC-LC.11: layout.css universal `*` rule sets box-sizing:border-box."""
        assert _LAYOUT_CSS.is_file(), "static/layout.css must exist (AC-LC.1)."
        css = _LAYOUT_CSS.read_text(encoding="utf-8")

        universal_rules = _universal_box_sizing_rules(css)
        assert universal_rules, (
            "static/layout.css must contain a universal reset -- a rule whose "
            "selector covers `*` (e.g. `*, *::before, *::after`) declaring "
            "box-sizing. Three of the five pages currently lack any universal "
            "box-sizing reset and render content-box."
        )
        assert any(
            "border-box" in decls.get("box-sizing", "")
            for decls in universal_rules
        ), (
            f"static/layout.css universal `*` reset must declare "
            f"box-sizing: border-box. Found: {universal_rules!r}."
        )


class TestNoPerTemplateUniversalReset:
    """
    The universal box-sizing reset lives ONLY in layout.css so all five pages
    share one base reset. A page template must not carry its own `*` reset.
    """

    @pytest.mark.parametrize("template_name", PAGE_TEMPLATES)
    def test_template_has_no_universal_box_sizing_reset(self, template_name):
        """AC-LC.12: no inline <style> declares its own universal `*` box-sizing reset."""
        css = _inline_style_css(_read_template(template_name))
        offenders = _universal_box_sizing_rules(css)
        assert not offenders, (
            f"{template_name} inline <style> declares its own universal `*` "
            f"box-sizing reset ({offenders!r}). The universal reset must live "
            f"ONLY in static/layout.css so all five pages share one base "
            f"reset -- a per-template universal reset is how the box model "
            f"diverged (3 of 5 pages had no reset at all). Remove it."
        )
