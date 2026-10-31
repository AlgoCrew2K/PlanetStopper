# 03-c7 fixtures: provenance

Plan: dashboard-refresh, cycle 7 (the stored theme before first paint and across tabs).

This folder holds no record files.

| Record | Source | Case | Scenarios | PM sign-off |
|---|---|---|---|---|
| a stored dark theme | the value `studioTweaks.set('theme', 'dark')` of the real `static/tweaks.js` writes, read back from a first run under node | a stored theme | S-10.1, S-10.2, S-10.3 | not derived |
| a stored value that is not JSON | the stored dark value with its closing brace removed | S-10.5 | S-10.5 | requested |
| a stored full set | `json.dumps` of the six keys of `TWEAK_DEFAULTS` with other values (density compact, accent `#123456`, typeface Inter, math overlays off, number format compact) | every attribute is applied at run | S-10.1 | requested |
| each page | `GET` of `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` through the Flask client, and through the in-process server in the browser module | head order | S-10.2 | not derived |
| a strip with a large guard alpha | `default` `GET /api/strip/30d` with `guard_alpha` 1234.5 | the formatter reads the attribute at each format | S-10.6 | requested |

The browser module holds `static/tweaks.js` back 400 ms in its request handler and samples the root's `data-theme` at the first animation frame once the body exists. That samples what a frame could draw; whether the theme looks right is judged by the capturer.
