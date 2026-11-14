# static/glossary.js

> Gives a label the text of its Glossary line as its title, on every page that carries a Glossary.

**Source:** `static/glossary.js`
**Last updated:** 2026-11-13

## Overview

Each page, Advisor tab, detail panel and settings modal ends in one closed Glossary written by the `glossary(host)` macro in `templates/_glossary.html`: a `<details data-glossary-host="<host>">` holding one `<p data-term="<key>">` per term. `glossary.js` reads those lines and writes them into the `title` attribute of the labels that name the terms. It writes attributes only, never markup, reads no network and holds no state beyond one `requestAnimationFrame` flag.

## API Reference

### `window.PSGlossary.apply(root = document)`
Titles every matching element under `root`. Called once at start and again, through `schedule()`, after each DOM change batch (a `MutationObserver` on `document.body`, one animation frame per burst), so labels the page scripts write later get titles too.

Matching, per element outside any Glossary host:
- The element's Glossary is the nearest sibling-of-an-ancestor element that has `data-glossary-host` (`nearestHost`). An element with no such host is left alone.
- An element with `data-glossary="<key>"` takes the line whose `data-term` equals the key; a key with no line leaves the element untouched.
- Otherwise an element counts as a label when it is a `th`, has `role="columnheader"` or has no child elements. Its text, whitespace collapsed and a trailing ":" removed, is matched against each line's lead term (the text before the line's first ": ").
- An element that already carries its own `title` that this script did not write keeps it (`data-glossary-titled` marks the script's own titles so they can be updated when the Glossary text changes).

## Types

None exported. Markup contract: `data-glossary-host`, `data-term`, `data-glossary`, `data-glossary-titled`.

## Internal Dependencies

- `templates/_glossary.html`: the Glossary markup. Loaded by `templates/index.html`, `history.html`, `performance.html`, `settings.html` and `ai_advisor.html`; `static/settings-modal.js` writes the modal's Glossary with the same attributes.
- [page_explainers.md](page_explainers.md): the copy and the Glossary convention.

## Not confirmed from code

The script rescans the whole document on each DOM change; its cost on the dashboard and Overview polls has not been measured (a tracked backlog item).
