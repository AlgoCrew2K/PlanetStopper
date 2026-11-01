"""Contrast arithmetic over the design tokens, for tests that pin the text colours of the pages.

TEST-ONLY. Every colour comes from ``static/tokens.css``: the light ``:root`` block and the
``[data-theme="dark"]`` overrides, with ``var()`` aliases resolved. Nothing is typed in a test, so a
future change of a token changes the ratio the test computes.

Method (WCAG 2.x relative luminance): a translucent foreground is composited over its background, the
background over any layers beneath it, and the ratio is (L1 + 0.05) / (L2 + 0.05) with L1 the lighter.
``SURFACES`` are the layers the pages' text sits on: the page background, a card (``paper``), a raised
card (``paper-hi``) and a stat tile (the chip tint over a card, or over the page background).

Also here: the rules of every stylesheet and ``<style>`` block that give a class a text colour or an
opacity, so a test can ask which token an element's class resolves to without a browser.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
STATIC = _REPO / "static"
TEMPLATES = _REPO / "templates"
THEMES = ("light", "dark")
MIN_BODY_TEXT_RATIO = 4.5

# Layers text sits on, bottom first: ("--studio-paper",) is a card; a tile adds the chip tint.
SURFACES: dict[str, tuple[str, ...]] = {
    "page": ("--studio-bg",),
    "card": ("--studio-paper",),
    "raised card": ("--studio-paper-hi",),
    "tile on card": ("--studio-paper", "--studio-chip-bg"),
    "tile on page": ("--studio-bg", "--studio-chip-bg"),
}

_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGBA = re.compile(
    r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$"
)
_VAR = re.compile(r"^var\(\s*(--[\w-]+)\s*(?:,[^)]*)?\)$")
_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)


def _strip_comments(text: str) -> str:
    return _COMMENT.sub("", text)


@lru_cache(maxsize=1)
def _token_blocks() -> tuple[dict[str, str], dict[str, str]]:
    """(light declarations, dark overrides), name -> raw value, from tokens.css."""
    text = _strip_comments((STATIC / "tokens.css").read_text(encoding="utf-8"))
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for selector, body in _BLOCK.findall(text):
        target = dark if "data-theme" in selector and "dark" in selector else light
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
            target[name] = value.strip()
    return light, dark


def raw_token(name: str, theme: str) -> str:
    light, dark = _token_blocks()
    table = {**light, **dark} if theme == "dark" else light
    assert name in table, f"{name} is not declared in static/tokens.css for the {theme} theme"
    return table[name]


def parse_color(value: str, theme: str) -> tuple[float, float, float, float]:
    """(r, g, b, alpha) of a colour value, following ``var()`` aliases through the tokens."""
    value = value.strip()
    alias = _VAR.match(value)
    if alias:
        return parse_color(raw_token(alias.group(1), theme), theme)
    hexed = _HEX.match(value)
    if hexed:
        digits = hexed.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return (
            float(int(digits[0:2], 16)),
            float(int(digits[2:4], 16)),
            float(int(digits[4:6], 16)),
            1.0,
        )
    rgba = _RGBA.match(value)
    if rgba:
        alpha = float(rgba.group(4)) if rgba.group(4) is not None else 1.0
        return (float(rgba.group(1)), float(rgba.group(2)), float(rgba.group(3)), alpha)
    raise AssertionError(f"not a colour the helper reads: {value!r}")


def _over(fg: tuple[float, float, float, float], bg: tuple[float, float, float]):
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))


def surface_color(layers: tuple[str, ...], theme: str) -> tuple[float, float, float]:
    """The opaque colour of stacked token layers (bottom first)."""
    base = parse_color(raw_token(layers[0], theme), theme)
    assert base[3] == 1.0, f"{layers[0]} is not opaque"
    color = base[:3]
    for layer in layers[1:]:
        color = _over(parse_color(raw_token(layer, theme), theme), color)
    return color


def _luminance(rgb) -> float:
    def channel(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _ratio(a, b) -> float:
    la, lb = _luminance(a), _luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def text_ratio(token: str, layers: tuple[str, ...], theme: str, *, opacity: float = 1.0) -> float:
    """Composited contrast of a text token (with an element opacity) over stacked layers."""
    bg = surface_color(layers, theme)
    r, g, b, a = parse_color(f"var({token})", theme)
    fg = _over((r, g, b, a * opacity), bg)
    return _ratio(fg, bg)


def pair_ratio(text_token: str, background_token: str, theme: str) -> float:
    """Contrast of an opaque text token on an opaque token background (a filled control)."""
    bg = surface_color((background_token,), theme)
    r, g, b, a = parse_color(f"var({text_token})", theme)
    return _ratio(_over((r, g, b, a), bg), bg)


def ratios_over_surfaces(token: str, *, opacity: float = 1.0) -> dict[str, float]:
    """``'<theme> / <surface>' -> ratio`` for a text token on every surface in both themes."""
    return {
        f"{theme} / {name}": text_ratio(token, layers, theme, opacity=opacity)
        for theme in THEMES
        for name, layers in SURFACES.items()
    }


def weakest(token: str, *, opacity: float = 1.0) -> tuple[str, float]:
    table = ratios_over_surfaces(token, opacity=opacity)
    where = min(table, key=table.get)
    return where, table[where]


# ---------------------------------------------------------------------------
# Which token does a class (or an inline style) put on text
# ---------------------------------------------------------------------------


def style_properties(style: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            out[name.strip().lower()] = value.strip()
    return out


@lru_cache(maxsize=1)
def stylesheet_texts() -> tuple[tuple[str, str], ...]:
    """(source, css) for every static stylesheet and every template ``<style>`` block."""
    out: list[tuple[str, str]] = []
    for path in sorted(STATIC.glob("*.css")):
        out.append((f"static/{path.name}", _strip_comments(path.read_text(encoding="utf-8"))))
    for path in sorted(TEMPLATES.glob("*.html")):
        for block in _STYLE_BLOCK.findall(path.read_text(encoding="utf-8")):
            out.append((f"templates/{path.name}", _strip_comments(block)))
    return tuple(out)


def class_rules(class_name: str) -> list[tuple[str, str, dict[str, str]]]:
    """(source, selector, properties) of every rule whose subject is ``.class_name``, with the
    ``color`` and ``opacity`` it sets (a rule setting neither is left out). A rule on a state
    (``:hover``, ``:focus``, ``:disabled``) is not the resting text."""
    subject = re.compile(r"\." + re.escape(class_name) + r"(?![\w-])")
    found: list[tuple[str, str, dict[str, str]]] = []
    for source, css in stylesheet_texts():
        for selector_group, body in _BLOCK.findall(css):
            for selector in selector_group.split(","):
                selector = selector.strip()
                if not selector or selector.startswith("@"):
                    continue
                last = re.split(r"[\s>+~]+", selector)[-1]
                if not subject.search(last) or re.search(r":(hover|focus|active|disabled)", last):
                    continue
                props = style_properties(body)
                kept = {k: v for k, v in props.items() if k in ("color", "opacity")}
                if kept:
                    found.append((source, selector, kept))
    return found


def token_of(value: str) -> str | None:
    """``--studio-x`` when the value is ``var(--studio-x)``, else None (a literal colour)."""
    match = _VAR.match(value.strip())
    return match.group(1) if match else None
