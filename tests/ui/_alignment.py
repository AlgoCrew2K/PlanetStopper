"""Alignment and spacing measures for the pages, and the registry of what they measure.

TEST-ONLY. Three things live here, each used by more than one cycle:

* **Widths**: :data:`WIDTHS` (one width inside each layout band from 1001 px up, plus 2560 px) and
  :data:`NARROW` (1000, 390 and 320 px, where the stacked forms stay).
* **Registries** of every repeated row group on the in-scope hosts: :data:`TABLE_GROUPS` (a table
  or a group of repeated fields), :data:`TOOLBAR_ROWS` (rows that may hold controls) and
  :data:`FIGURE_LINES` (figure and status lines). An entry names its host, how the group is
  reached, its kind, the selectors of its rows and cells, and which columns hold numbers. The
  plan's row ids appear in the round's handoff file, never here; an entry is keyed by what the
  group is.
* **Measures**, as JavaScript strings run with ``page.evaluate``, each taking one spec (an entry
  or a hand-built dict) and returning what it measured and the violations it found:
  :data:`COLUMN_EDGES_JS` (header above its column, one header row, column edges), :data:`FIELD_OFFSETS_JS`
  (each named field at one offset on every row or card), :data:`COLUMN_GAPS_JS` (space between the
  widest text of one column and the nearest text of the next), :data:`LINE_GAPS_JS` (equal gaps along
  a figure line, none wrapping), :data:`ROW_START_JS` (a non-control element starts within 24 px of
  the element before it, or at the row's left edge) and :data:`TABLE_FORM_JS` (the stacked-row form
  of a table, compared with the form it has at the reference).

A cell's edge is the bounding box of the text or control inside it, never the cell's own box: the
cell padding is excluded. Text is measured with a ``Range`` over each text node, the technique the
earlier harness used, and a text box one pixel or less wide or tall (visually hidden text) is
not content. A closed ``<details>`` holds nothing visible besides its summary.

Registry entry fields: ``host`` (a page path), ``open`` (how the group is reached: ``{}`` for the
page itself, ``{"tab": name}`` for an Advisor tab, ``{"details": [selector, ...]}`` for disclosures
to open first, ``{"action": name}`` for a group a user action produces, which the cycle that
changes it opens), ``kind`` (``table``: one header row and the column rule; ``fields``: repeated
rows or cards whose named fields share an offset), ``group``, ``row`` and ``cells`` or ``fields``
(selectors), ``numeric`` (column or field names compared by right edge), ``status`` (``ref`` when
the selectors were read from the reference markup and a test shows them live in the seeded world,
``action`` when a user action makes the group, ``script`` when only the page script writes it and a
later cycle pins the selectors) and ``owner`` (the cycle that re-pins the selectors if it renames
them).
"""

from __future__ import annotations

# One width inside each layout band from 1001 px up (a band is where a media rule of the
# stylesheets changes the layout), the boundary itself, and 2560 px.
WIDTHS = (1001, 1120, 1290, 1350, 1400, 1420, 1440, 1920, 2560)
# Below 1001 px the stacked forms stay: the width just under the boundary, a phone and the narrowest
# phone.
NARROW = (1000, 390, 320)
VIEWPORT_HEIGHT = 1000
NARROW_VIEWPORT_HEIGHT = {1000: 800, 390: 844, 320: 640}

# The Advisor's tab names, in the order of its tab bar.
ADVISOR_TABS = (
    "overview",
    "correlations",
    "retirement",
    "asset-swaps",
    "logic-changes",
    "chat",
    "strategy-builder",
    "frontrunner-builder",
)

TOLERANCE_PX = 2
MAX_COLUMN_GAP_PX = 48
MAX_NON_CONTROL_GAP_PX = 24
CONTROL_SELECTOR = 'button, select, input, [role="radiogroup"], [role="tablist"]'


def viewport(width: int) -> dict:
    """The browser viewport for ``width``: the narrow widths have their own heights."""
    return {"width": width, "height": NARROW_VIEWPORT_HEIGHT.get(width, VIEWPORT_HEIGHT)}


# ---------------------------------------------------------------------------
# Shared in-page helpers (prepended to every measure)
# ---------------------------------------------------------------------------

_HELPERS_JS = r"""
const TOL = 2;
const CONTROL = 'button, select, input, textarea, [role="radiogroup"], [role="tablist"]';
const MEDIA = 'svg, canvas, img';
// Whatever a closed <details> holds besides its summary is not visible, whatever boxes it keeps.
const inClosedDetails = (node) => {
  for (let el = node.nodeType === 1 ? node : node.parentElement; el; el = el.parentElement) {
    const holder = el.parentElement;
    if (holder && holder.tagName === 'DETAILS' && !holder.open && el.tagName !== 'SUMMARY') return true;
  }
  return false;
};
const shown = (el) => !!el && el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden' && !inClosedDetails(el);
const union = (a, g) => a
  ? { left: Math.min(a.left, g.left), right: Math.max(a.right, g.right),
      top: Math.min(a.top, g.top), bottom: Math.max(a.bottom, g.bottom) }
  : { left: g.left, right: g.right, top: g.top, bottom: g.bottom };
// The box of the text or control inside an element: padding and the element's own box excluded.
// Text inside a box of one pixel or less that clips its overflow is visually hidden text.
const clippedAway = (node, root) => {
  for (let e = node.parentElement; e && e !== root.parentElement; e = e.parentElement) {
    const r = e.getBoundingClientRect();
    if ((r.width <= 1 || r.height <= 1) && getComputedStyle(e).overflow !== 'visible') return true;
  }
  return false;
};
const DOT_MAX = 16;
const isPaintedDot = (cs) => {
  const bg = cs.backgroundColor;
  return !!bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)' && cs.borderTopLeftRadius !== '0px';
};
const isDot = (g, cs) => g.width >= 1 && g.width <= DOT_MAX && g.height >= 1 && g.height <= DOT_MAX &&
    cs.display !== 'inline' && isPaintedDot(cs);
const contentBox = (el) => {
  let box = null;
  const add = (g) => { if (g.width > 1 && g.height > 1) box = union(box, g); };
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.nodeValue.trim() || !shown(n.parentElement) || clippedAway(n, el)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const g of range.getClientRects()) add(g);
  }
  const sel = CONTROL + ', ' + MEDIA;
  for (const c of el.querySelectorAll(sel)) if (shown(c)) add(c.getBoundingClientRect());
  if (el.matches(sel) && shown(el)) add(el.getBoundingClientRect());
  // A status dot is part of the cell: a small painted mark with no text, either an empty element
  // or the ::before of an element, so the column's edge is the left of the dot when a cell starts
  // with one.
  for (const d of [el, ...el.querySelectorAll('*')]) {
    if (!shown(d)) continue;
    const g = d.getBoundingClientRect();
    if (!d.children.length && !(d.textContent || '').trim() && isDot(g, getComputedStyle(d))) add(g);
    const pseudo = getComputedStyle(d, '::before');
    if (pseudo.content !== 'none' && pseudo.content !== 'normal') {
      const size = parseFloat(pseudo.width);
      if (size >= 1 && size <= DOT_MAX && isPaintedDot(pseudo)) {
        const cs = getComputedStyle(d);
        const left = g.left + (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.paddingLeft) || 0);
        add({ left, right: left + size, top: g.top, bottom: g.bottom, width: size, height: g.height });
      }
    }
  }
  return box;
};
const words = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
const fix = (v) => Math.round(v * 10) / 10;
const cellsOf = (row) => [...row.children].filter(shown);
// The rows of a table group: a <tr> or an element with role=row, shown, outside a nested table.
const rowsOf = (group, spec) => [...group.querySelectorAll(spec.row_selector || 'tr, [role="row"]')]
    .filter((r) => shown(r) && r.closest('table, [role="table"]') === group);
const isHeaderRow = (row) => {
  const cells = cellsOf(row);
  return cells.length > 0 && cells.every((c) => c.matches('th, [role="columnheader"]'));
};
"""


def _script(body: str) -> str:
    return "(spec) => {\n" + _HELPERS_JS + body + "\n}"


# ---------------------------------------------------------------------------
# COLUMN_EDGES_JS: one header row above its column, and the edges of every column
# ---------------------------------------------------------------------------

_COLUMN_CORE_JS = r"""
// One group instance -> { rows: [...], header_rows, columns: [name], boxes: [[box per row]] }.
const readTable = (group) => {
  const all = rowsOf(group, spec);
  const head = all.filter(isHeaderRow);
  const body = all.filter((r) => !isHeaderRow(r));
  const headCells = head.length ? cellsOf(head[0]) : [];
  const names = headCells.map(words);
  return { group, head, body, names };
};
"""

COLUMN_EDGES_JS = _script(
    _COLUMN_CORE_JS
    + r"""
const tol = spec.tolerance || TOL;
const numeric = new Set(spec.numeric || []);
const out = { name: spec.name, found: 0, instances: [] };
const groups = [...document.querySelectorAll(spec.group)].filter(shown);
out.found = groups.length;
groups.forEach((group, index) => {
  const t = readTable(group);
  const v = [];
  const bodyRects = t.body.map((r) => r.getBoundingClientRect());
  if (t.body.length && t.head.length === 0) v.push('no header row above ' + t.body.length + ' body rows');
  if (t.head.length > 1) v.push(t.head.length + ' header rows, not one');
  if (t.head.length) {
    const h = t.head[0].getBoundingClientRect();
    bodyRects.forEach((r, i) => {
      if (r.top < h.bottom - tol) v.push('body row ' + (i + 1) + ' starts above the end of the header row');
    });
  }
  for (let i = 1; i < bodyRects.length; i++) {
    if (bodyRects[i].top < bodyRects[i - 1].bottom - tol) {
      v.push('body rows ' + i + ' and ' + (i + 1) + ' overlap vertically or sit side by side (tops '
             + fix(bodyRects[i - 1].top) + ' and ' + fix(bodyRects[i].top) + ')');
    }
  }
  const headCells = t.head.length ? cellsOf(t.head[0]) : [];
  const edges = t.names.map(() => []);
  const record = (row, label) => {
    const cells = cellsOf(row);
    const used = cells.reduce((n, cell) => n + (cell.colSpan || 1), 0);
    if (t.names.length && used !== t.names.length && !spec.ragged) {
      v.push(label + ' has ' + cells.length + ' cells under ' + t.names.length + ' headers');
    }
    let at = 0;
    cells.forEach((cell) => {
      // A cell that spans columns answers for the last of them when that column is numeric (its
      // text ends at the table's right side), and for the first otherwise.
      const span = cell.colSpan || 1;
      const last = at + span - 1;
      const c = span > 1 && numeric.has(t.names[last]) ? last : at;
      at += span;
      if (c >= edges.length) return;
      const box = contentBox(cell);
      if (box) edges[c].push({ label, box });
    });
  };
  if (t.head.length) record(t.head[0], 'header');
  t.body.forEach((row, i) => record(row, 'row ' + (i + 1)));
  const columns = t.names.map((name, c) => {
    const isNum = numeric.has(name);
    const values = edges[c].map((e) => (isNum ? e.box.right : e.box.left));
    const spread = values.length ? Math.max(...values) - Math.min(...values) : 0;
    if (spread > tol) {
      const lo = edges[c][values.indexOf(Math.min(...values))].label;
      const hi = edges[c][values.indexOf(Math.max(...values))].label;
      v.push('column "' + name + '": ' + (isNum ? 'right' : 'left') + ' edges differ by '
             + fix(spread) + ' px (' + lo + ' at ' + fix(Math.min(...values)) + ', ' + hi + ' at '
             + fix(Math.max(...values)) + ')');
    }
    return { name, numeric: isNum, edge: isNum ? 'right' : 'left', spread: fix(spread), cells: values.length };
  });
  out.instances.push({ index, header_rows: t.head.length, body_rows: t.body.length, columns, violations: v });
});
out.violations = out.instances.flatMap((i) => i.violations.map((m) => 'group ' + i.index + ': ' + m));
return out;
"""
)

# ---------------------------------------------------------------------------
# COLUMN_GAPS_JS: the space between adjacent columns' text
# ---------------------------------------------------------------------------

COLUMN_GAPS_JS = _script(
    _COLUMN_CORE_JS
    + r"""
const maxGap = spec.max_gap === undefined ? 48 : spec.max_gap;
const out = { name: spec.name, found: 0, instances: [] };
const columnsOf = (group) => {
  if (spec.kind === 'fields') {
    const rows = [...document.querySelectorAll(spec.rows)].filter(shown)
      .filter((r) => group === document || group.contains(r));
    return spec.fields.map((f) => ({
      name: f.name,
      boxes: rows.map((r) => { const el = [...r.querySelectorAll(f.selector)].find(shown);
                               return el ? contentBox(el) : null; }).filter(Boolean),
    }));
  }
  const t = readTable(group);
  const rows = [...t.head.slice(0, 1), ...t.body];
  return t.names.map((name, c) => ({
    name,
    boxes: rows.map((row) => { const cell = cellsOf(row)[c]; return cell ? contentBox(cell) : null; })
                .filter(Boolean),
  }));
};
const groups = spec.kind === 'fields' ? [document] : [...document.querySelectorAll(spec.group)].filter(shown);
out.found = groups.length;
groups.forEach((group, index) => {
  const columns = columnsOf(group);
  const v = [];
  const gaps = [];
  for (let c = 0; c + 1 < columns.length; c++) {
    const a = columns[c].boxes, b = columns[c + 1].boxes;
    if (!a.length || !b.length) continue;
    const gap = Math.min(...b.map((x) => x.left)) - Math.max(...a.map((x) => x.right));
    gaps.push({ between: [columns[c].name, columns[c + 1].name], gap: fix(gap) });
    if (gap > maxGap) {
      v.push('gap between "' + columns[c].name + '" and "' + columns[c + 1].name + '" is '
             + fix(gap) + ' px, more than ' + maxGap);
    }
  }
  out.instances.push({ index, gaps, violations: v });
});
out.violations = out.instances.flatMap((i) => i.violations.map((m) => 'group ' + i.index + ': ' + m));
return out;
"""
)

# ---------------------------------------------------------------------------
# FIELD_OFFSETS_JS: each named field at one offset on every row or card
# ---------------------------------------------------------------------------

FIELD_OFFSETS_JS = _script(
    r"""
const tol = spec.tolerance || TOL;
const rows = [...document.querySelectorAll(spec.rows)].filter(shown);
const out = { name: spec.name, found: rows.length, fields: {}, violations: [] };
for (const field of spec.fields) {
  const offsets = [];
  rows.forEach((row, i) => {
    const el = [...row.querySelectorAll(field.selector)].find(shown);
    const box = el ? contentBox(el) : null;
    if (!box) {
      if (!field.optional) out.violations.push('field "' + field.name + '" is missing or empty on row ' + (i + 1));
      return;
    }
    const left = row.getBoundingClientRect().left;
    offsets.push({ row: i + 1, offset: (field.numeric ? box.right : box.left) - left });
  });
  const values = offsets.map((o) => o.offset);
  const spread = values.length ? Math.max(...values) - Math.min(...values) : 0;
  out.fields[field.name] = { edge: field.numeric ? 'right' : 'left', rows: offsets.length, spread: fix(spread) };
  if (spread > tol) {
    const lo = offsets[values.indexOf(Math.min(...values))], hi = offsets[values.indexOf(Math.max(...values))];
    out.violations.push('field "' + field.name + '": ' + (field.numeric ? 'right' : 'left') + ' offset differs by '
      + fix(spread) + ' px (row ' + lo.row + ' at ' + fix(lo.offset) + ', row ' + hi.row + ' at ' + fix(hi.offset) + ')');
  }
}
return out;
"""
)

# ---------------------------------------------------------------------------
# FIGURE_COLUMNS_JS: the label and value rows under each column label of a result card
# ---------------------------------------------------------------------------

FIGURE_COLUMNS_JS = _script(
    r"""
const tol = spec.tolerance || TOL;
const maxGap = spec.max_gap === undefined ? 48 : spec.max_gap;
const cards = [...document.querySelectorAll(spec.cards)].filter(shown);
const out = { name: spec.name, found: cards.length, columns: [], violations: [] };
const boxOf = (row, selector) => {
  const el = [...row.querySelectorAll(selector)].find(shown);
  return el ? contentBox(el) : null;
};
cards.forEach((card, ci) => {
  const heads = [...card.querySelectorAll(spec.column_label)].filter(shown);
  // A row laid out with display: contents has no box of its own: it is shown when its label is.
  const rows = [...card.querySelectorAll(spec.row)]
    .filter((r) => [...r.querySelectorAll(spec.label)].some(shown));
  // A row belongs to the last column label before it in document order.
  const cols = heads.map((h) => ({ name: words(h), rows: [] }));
  rows.forEach((row) => {
    let owner = -1;
    heads.forEach((h, i) => {
      if (h.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING) owner = i;
    });
    if (owner >= 0) cols[owner].rows.push(row);
  });
  cols.forEach((col) => {
    const where = 'card ' + (ci + 1) + ', column "' + col.name + '"';
    const labels = col.rows.map((r) => boxOf(r, spec.label)).filter(Boolean);
    const values = col.rows.map((r) => boxOf(r, spec.value)).filter(Boolean);
    if (!labels.length || !values.length) {
      out.violations.push(where + ' holds no figure row');
      return;
    }
    const spread = (xs) => Math.max(...xs) - Math.min(...xs);
    const labelSpread = spread(labels.map((b) => b.left));
    const valueSpread = spread(values.map((b) => b.right));
    const gap = Math.min(...values.map((b) => b.left)) - Math.max(...labels.map((b) => b.right));
    out.columns.push({ card: ci + 1, column: col.name, rows: col.rows.length,
                       label_spread: fix(labelSpread), value_spread: fix(valueSpread), gap: fix(gap) });
    if (labelSpread > tol) out.violations.push(where + ': labels start ' + fix(labelSpread) + ' px apart');
    if (valueSpread > tol) out.violations.push(where + ': values end ' + fix(valueSpread) + ' px apart');
    if (gap > maxGap) {
      out.violations.push(where + ': the widest label and the nearest value are ' + fix(gap)
                          + ' px apart, more than ' + maxGap);
    }
  });
});
return out;
"""
)

# ---------------------------------------------------------------------------
# LINE_GAPS_JS: the items of one figure line sit with equal gaps
# ---------------------------------------------------------------------------

LINE_GAPS_JS = _script(
    r"""
const tol = spec.tolerance || TOL;
const maxGap = spec.max_gap === undefined ? 48 : spec.max_gap;
const lines = [...document.querySelectorAll(spec.line)].filter(shown);
const out = { name: spec.name, found: lines.length, lines: [], violations: [] };
lines.forEach((line, index) => {
  const items = [...line.querySelectorAll(spec.items || ':scope > *')].filter(shown)
    .map((el) => ({ el, box: contentBox(el) })).filter((x) => x.box)
    .sort((a, b) => a.box.left - b.box.left);
  const v = [];
  const gaps = [];
  items.forEach((x, i) => {
    if (i > 0) gaps.push(x.box.left - items[i - 1].box.right);
  });
  // Items on one line start at tops within half the smaller item's height of each other.
  const tops = items.map((x) => x.box.top);
  const height = items.length ? Math.min(...items.map((x) => x.box.bottom - x.box.top)) : 0;
  if (items.length > 1 && Math.max(...tops) - Math.min(...tops) > Math.max(tol, height / 2)) {
    v.push('line ' + (index + 1) + ': the items are not on one line (tops ' + fix(Math.min(...tops)) + ' to ' + fix(Math.max(...tops)) + ')');
  } else if (gaps.length) {
    if (gaps.some((g) => g > maxGap)) v.push('line ' + (index + 1) + ': a gap of ' + fix(Math.max(...gaps)) + ' px, more than ' + maxGap);
    if (Math.max(...gaps) - Math.min(...gaps) > tol) {
      v.push('line ' + (index + 1) + ': gaps are not equal (' + gaps.map(fix).join(', ') + ')');
    }
  }
  if (spec.no_wrap) {
    for (const x of items) {
      const walker = document.createTreeWalker(x.el, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!n.nodeValue.trim() || !shown(n.parentElement)) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        const lineTops = new Set([...range.getClientRects()].filter((g) => g.width > 1).map((g) => Math.round(g.top)));
        if (lineTops.size > 1) v.push('line ' + (index + 1) + ': the text "' + words(n.parentElement).slice(0, 40) + '" breaks across lines');
      }
    }
  }
  out.lines.push({ index, items: items.length, gaps: gaps.map(fix) });
  out.violations.push(...v);
});
return out;
"""
)

# ---------------------------------------------------------------------------
# ROW_START_JS: the 24 px rule of a toolbar row
# ---------------------------------------------------------------------------

ROW_START_JS = _script(
    r"""
const tol = spec.tolerance || TOL;
const maxGap = spec.max_gap === undefined ? 24 : spec.max_gap;
const controlSelector = spec.controls || CONTROL;
const isControl = (el) => {
  if (el.matches(controlSelector)) return true;
  // A wrapper is a control when everything it shows is a control: a picker group, a labelled select.
  const inside = [...el.querySelectorAll(controlSelector)].filter(shown);
  if (!inside.length) return false;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.nodeValue.trim() || !shown(n.parentElement)) continue;
    if (!n.parentElement.closest(controlSelector) && !inside.some((c) => c.contains(n))) {
      if (!el.matches('label') && !n.parentElement.closest('label')) return false;
    }
  }
  return true;
};
const rows = [...document.querySelectorAll(spec.row)].filter(shown);
const out = { name: spec.name, found: rows.length, rows: [], violations: [] };
rows.forEach((row, index) => {
  const rowBox = row.getBoundingClientRect();
  const cs = getComputedStyle(row);
  const rowLeft = rowBox.left + (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.paddingLeft) || 0);
  const items = [...row.children].filter(shown)
    .map((el) => ({ el, box: contentBox(el), control: false }))
    .filter((x) => x.box);
  items.forEach((x) => { x.control = isControl(x.el); });
  // A line is the run of items whose tops are within half an item's height of the line's first.
  const sorted = items.slice().sort((a, b) => a.box.top - b.box.top || a.box.left - b.box.left);
  const lines = [];
  for (const x of sorted) {
    const last = lines[lines.length - 1];
    if (last && Math.abs(x.box.top - last[0].box.top) <= Math.max(tol, (x.box.bottom - x.box.top) / 2)) last.push(x);
    else lines.push([x]);
  }
  const v = [];
  lines.forEach((line) => {
    line.sort((a, b) => a.box.left - b.box.left);
    line.forEach((x, i) => {
      if (x.control) return;
      const label = '"' + words(x.el).slice(0, 40) + '"';
      if (i === 0) {
        if (Math.abs(x.box.left - rowLeft) > tol) {
          v.push('row ' + (index + 1) + ': ' + label + ' begins a line ' + fix(x.box.left - rowLeft) + ' px from the row\'s left edge');
        }
      } else {
        const gap = x.box.left - line[i - 1].box.right;
        if (gap > maxGap) v.push('row ' + (index + 1) + ': ' + label + ' starts ' + fix(gap) + ' px after the element before it, more than ' + maxGap);
      }
    });
  });
  out.rows.push({ index, items: items.length, lines: lines.length });
  out.violations.push(...v);
});
return out;
"""
)

# ---------------------------------------------------------------------------
# TABLE_FORM_JS: the stacked-row form of a table at a narrow width
# ---------------------------------------------------------------------------

# spec: {"groups": [{"name": ..., "group": css}, ...]}. The form of a table is how its parts are
# laid out, not what they say, so a column the page no longer shows leaves the form as it was.
TABLE_FORM_JS = _script(
    r"""
const opened = [];
const out = {};
for (const g of spec.groups) {
  // A group inside a closed disclosure is measured open, then the disclosure is closed again.
  const found = [...document.querySelectorAll(g.group)];
  for (const el of found) {
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (p.tagName === 'DETAILS' && !p.open) { p.open = true; opened.push(p); }
    }
  }
  const shapes = new Set();
  let count = 0;
  for (const group of found.filter(shown)) {
    count += 1;
    const all = rowsOf(group, {});
    const head = all.filter(isHeaderRow);
    const body = all.filter((r) => !isHeaderRow(r));
    const first = body[0];
    const firstCell = first ? cellsOf(first)[0] : null;
    let stacked = null;
    for (const row of body) {
      const cells = cellsOf(row);
      if (cells.length < 2) continue;
      const tops = cells.map((c) => Math.round(c.getBoundingClientRect().top));
      const rowStacked = new Set(tops).size === tops.length;
      stacked = stacked === null ? rowStacked : stacked && rowStacked;
    }
    shapes.add(JSON.stringify({
      group: getComputedStyle(group).display,
      row: first ? getComputedStyle(first).display : null,
      cell: firstCell ? getComputedStyle(firstCell).display : null,
      header_shown: head.length > 0,
      stacked,
    }));
  }
  out[g.name] = { count, shapes: [...shapes].sort().map((s) => JSON.parse(s)) };
}
for (const p of opened.reverse()) p.open = false;
return out;
"""
)


# ---------------------------------------------------------------------------
# Python wrappers
# ---------------------------------------------------------------------------


def measure(page, script: str, spec: dict) -> dict:
    """Run one measure over the page and return its record (``violations`` lists the faults)."""
    return page.evaluate(script, spec)


def spec_of(entry: dict, **extra) -> dict:
    """A measure spec from a registry entry (the entry's own keys, ``extra`` overriding)."""
    return {**entry, **extra}


def stacked_form_groups() -> list[dict]:
    """The groups whose stacked-row form the reference capture records: every ``table`` entry."""
    return [
        {"name": name, "group": entry["group"]}
        for name, entry in TABLE_GROUPS.items()
        if entry["kind"] == "table"
    ]


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_TABLE = "table"
_FIELDS = "fields"

# Every repeated row group on the in-scope hosts: a ``table`` has one header row and the column
# rule; a ``fields`` group is repeated rows or cards whose named fields share an offset. A group
# that is neither (one badge per check, alternating chat messages, a prose column per lens) has no
# rule and is not listed. ``status`` says how far the selectors are proven: ``ref`` (read from the
# reference markup and shown live in the seeded world by the registry test, with the named ``seed``
# written first), ``action`` (a user action makes the group; the cycle that changes it opens it and
# writes the selectors) or ``unpinned`` (only the page script writes it, or the seeded world does
# not hold it; ``group`` may be None and the owning cycle writes the selectors when it first
# measures the group, naming the columns or fields listed here).
TABLE_GROUPS: dict[str, dict] = {
    # --- the dashboard
    "comparison_rows": {
        "host": "/",
        "open": {},
        "kind": _FIELDS,
        "group": "#portfolio-strip",
        "rows": "#portfolio-strip > .vs-row",
        "fields": [
            {"name": "label", "selector": ".vs-row-label"},
            {"name": "bot", "selector": '[data-testid$="-bot-text"]', "numeric": True},
            {"name": "held", "selector": '[data-testid$="-held-text"]', "numeric": True},
            # The Max DD row shows "needs 30 trading days" in place of its alpha while the history
            # is short, so the alpha cell is absent on that row.
            {
                "name": "alpha",
                "selector": '[data-testid$="-delta"]',
                "numeric": True,
                "optional": True,
            },
        ],
        "status": "ref",
        "owner": "C5",
    },
    "symphony_cards": {
        "host": "/",
        "open": {},
        "kind": _FIELDS,
        "group": ".sym-list",
        "rows": '[data-testid="sym-card"]',
        "fields": [
            {"name": "name block", "selector": ".card-id"},
            {"name": "session column", "selector": ".card-footer-grid > .dual-value-headline"},
            {"name": "cum column", "selector": ".card-footer-grid > .cfg-cell:nth-child(2)"},
            {"name": "max dd column", "selector": ".card-footer-grid > .cfg-cell:nth-child(3)"},
            {"name": "session figure", "selector": '[data-field="tc-bot"]', "numeric": True},
            {"name": "cum figure", "selector": '[data-field="cr-bot"]', "numeric": True},
            {"name": "max dd figure", "selector": '[data-field="mdd-bot"]', "numeric": True},
            {"name": "ring", "selector": '[data-testid="mc-dial"]'},
            {"name": "sparkline", "selector": ".card-spark-box"},
            {"name": "settings button", "selector": '[data-testid="sym-settings-btn"]'},
        ],
        "status": "ref",
        "owner": "C5",
    },
    # A sleeve with three rules whose names differ in length is seeded by the measuring test: with
    # one rule row every field is trivially at one offset.
    "sleeve_rule_rows": {
        "host": "/",
        "open": {"action": "seed_sleeve_rules"},
        "kind": _FIELDS,
        "group": '[data-testid="sleeve-card"]',
        "rows": '[data-testid="sleeve-rule-row"]',
        "fields": [
            {"name": "rule name", "selector": '[data-field="rule-name"]'},
            {"name": "mode", "selector": '[data-field="rule-mode"]'},
            {"name": "actions", "selector": '[data-field="rule-actions"]'},
        ],
        "status": "action",
        "owner": "C5",
    },
    "risk_math_rows": {
        "host": "/",
        "open": {"action": "open_detail_panel"},
        "kind": _FIELDS,
        "group": '[data-testid="detail-panel"]',
        # The Risk math list: the MC probability row and the rows after it. The Risk profile
        # beside it reuses the .math-row class for a different figure per row.
        "rows": "#dp-rm-mc-row, #dp-rm-mc-row ~ .math-row",
        "fields": [
            {"name": "label", "selector": ".math-row-label"},
            {"name": "value", "selector": ".math-row-value", "numeric": True},
            # Only the MC probability row draws a bar.
            {"name": "bar", "selector": ".math-row-bar-wrap", "optional": True},
        ],
        "status": "action",
        "owner": "C5",
    },
    "event_rows": {
        "host": "/",
        "open": {"action": "open_detail_panel"},
        "kind": _FIELDS,
        "group": "#events-timeline",
        "rows": "#events-timeline .event-row",
        "fields": [
            {"name": "time", "selector": ".event-ts"},
            {"name": "text", "selector": ".event-body"},
        ],
        "status": "action",
        "owner": "C5",
    },
    "symphony_variable_rows": {
        "host": "/",
        "open": {"action": "open_detail_panel"},
        "kind": _FIELDS,
        "group": "#vars-section",
        "rows": "#vars-section .var-row",
        "fields": [
            {"name": "name", "selector": ".var-key"},
            {"name": "value", "selector": ".var-val", "numeric": True},
        ],
        "status": "action",
        "owner": "C5",
    },
    # --- Performance and History
    "risk_metrics": {
        "host": "/performance",
        "open": {},
        "kind": _TABLE,
        "group": "table:has(#metrics-tbody)",
        "numeric": ["If held", "Bot (shadow)", "Delta (DD and vol in %)"],
        "status": "ref",
        "owner": "C5",
    },
    "preconditions": {
        "host": "/performance",
        "open": {},
        "kind": _TABLE,
        "group": '[data-testid="guard-alpha-preconditions-table"]',
        "numeric": ["ρ", "95% CI", "Daily Sharpe", "N"],
        "status": "ref",
        "owner": "C5",
    },
    "exit_turnover": {
        "host": "/performance",
        "open": {},
        "kind": _TABLE,
        "group": '[data-testid="exit-turnover-table"]',
        "numeric": ["30d", "90d", "365d", "Est. annual trading-cost drag"],
        "status": "ref",
        "owner": "C5",
    },
    "exit_reasons": {
        "host": "/history",
        "open": {},
        "kind": _TABLE,
        "group": "table:has(#reason-cards)",
        "numeric": [
            "Sum of exit alpha",
            "Dollar result",
            "Exits",
            "Wins",
            "Win rate",
            "Avg per exit",
        ],
        "status": "ref",
        "owner": "C5",
    },
    "exits": {
        "host": "/history",
        "open": {},
        "kind": _TABLE,
        "group": "table:has(#triggers-tbody)",
        "numeric": ["Detail"],
        "status": "ref",
        "owner": "C5",
    },
    # --- Settings and the settings modal
    "algorithm_parameter_cards": {
        "host": "/settings",
        "open": {"action": "choose_section"},
        "kind": _FIELDS,
        "group": "#algo-grid",
        "rows": "#algo-grid .param-card",
        "fields": [
            {"name": "name", "selector": ".param-name"},
            {"name": "input row", "selector": ".param-input-row"},
            {"name": "help", "selector": ".param-help"},
        ],
        "status": "action",
        "owner": "C5",
    },
    "symphony_override_rows": {
        "host": "/settings",
        "open": {"action": "open_symphony_detail"},
        "kind": _FIELDS,
        "group": ".override-grid",
        "rows": ".override-grid .override-row",
        "fields": [
            {"name": "header", "selector": ".override-key"},
            {"name": "global line", "selector": ".override-global-row"},
            {"name": "input row", "selector": ".override-input-row"},
        ],
        "status": "action",
        "owner": "C5",
    },
    "modal_parameter_rows": {
        "host": "/",
        "open": {"action": "open_settings_modal"},
        "kind": _FIELDS,
        "group": "#sym-settings-modal .ssm-params",
        "rows": "#sym-settings-modal .ssm-param",
        "fields": [
            {"name": "key", "selector": ".ssm-param-key"},
            {"name": "value", "selector": ".ssm-param-val", "numeric": True},
        ],
        "status": "action",
        "owner": "C5",
    },
    "modal_locked_variable_entries": {
        "host": "/",
        "open": {"action": "open_settings_modal"},
        "kind": _FIELDS,
        "group": "#sym-settings-modal .ssm-locks",
        "rows": "#sym-settings-modal .sym-settings-locked-var-btn",
        # The checkbox draws a border and holds a tick only while locked, so it has no text to
        # measure; the key and the "locked" tag (locked entries only) are the text fields.
        "fields": [
            {"name": "key", "selector": ".ssm-lockname"},
            {"name": "locked tag", "selector": ".ssm-lockedtag", "optional": True},
        ],
        "status": "action",
        "owner": "C5",
    },
    # --- the Advisor Overview
    "suggestion_cards": {
        "host": "/ai-advisor",
        "open": {"tab": "overview", "action": "run_claude_advisor"},
        "kind": _FIELDS,
        "group": None,
        "fields": [
            {"name": "title"},
            {"name": "gate dots"},
            {"name": "figures"},
            {"name": "buttons"},
        ],
        "status": "action",
        "owner": "C2",
    },
    "autotune_runs": {
        "host": "/ai-advisor",
        "open": {"tab": "overview"},
        "kind": _TABLE,
        "group": '[data-testid="autotune-runs-list"] [role="table"]',
        "numeric": [
            "Runs without adopting a tuned config",
            "Best score",
            "Best CRRA-EU score",
            "Best Sortino score",
            "Best objective score",
            "Sel t-stat",
        ],
        "status": "ref",
        "owner": "C2",
    },
    "autotune_older_runs": {
        "host": "/ai-advisor",
        "open": {"tab": "overview", "details": ['[data-testid="autotune-older-runs"]']},
        "kind": _TABLE,
        "group": '[data-testid="autotune-older-list"] [role="table"]',
        "numeric": [
            "Runs without adopting a tuned config",
            "Best score",
            "Best CRRA-EU score",
            "Best Sortino score",
            "Best objective score",
            "Sel t-stat",
        ],
        "status": "ref",
        "owner": "C2",
    },
    "observation_lines": {
        "host": "/ai-advisor",
        "open": {"tab": "overview"},
        "kind": _FIELDS,
        "group": '[data-testid="tab-panel-overview"]',
        "rows": ".obs-producer",
        "fields": [
            {"name": "label", "selector": ".obs-producer-label"},
            {"name": "verdict", "selector": ".obs-producer-verdict"},
            # The Retirement row's time and count cells are empty, so they have no box to measure.
            {"name": "time", "selector": ".obs-producer-time", "optional": True},
            {
                "name": "count",
                "selector": ".obs-producer-total",
                "numeric": True,
                "optional": True,
            },
            # The last cell: a producer's History toggle, the Retirement row's Open tab link.
            {"name": "last", "selector": '.obs-history-toggle, [data-testid="retirement-pointer"]'},
        ],
        "status": "ref",
        "owner": "C2",
    },
    "observation_histories": {
        "host": "/ai-advisor",
        "open": {"tab": "overview", "details": ['[data-testid="obs-producer-history"]']},
        "kind": _TABLE,
        "group": "table.obs-table",
        "status": "ref",
        "owner": "C2",
    },
    # --- the other Advisor tabs
    "correlation_matrix": {
        "host": "/ai-advisor",
        "open": {"tab": "correlations"},
        "kind": _TABLE,
        "group": '[data-testid="correlation-matrix-table"]',
        "status": "unpinned",
        "owner": "C4",
    },
    "recommendation_cards": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "kind": _FIELDS,
        "group": '[data-testid="tab-panel-retirement"]',
        "rows": '[data-testid="retirement-recommendation-card"]',
        "fields": [],
        "status": "unpinned",
        "owner": "C3",
    },
    "tenure_cards": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "kind": _FIELDS,
        "group": '[data-testid="tab-panel-retirement"]',
        "rows": '[data-testid="tenure-review-entry"]',
        "fields": [
            {
                "name": "performance",
                "selector": '[data-testid="tenure-review-evidence-performance"]',
            },
            {
                "name": "composite score",
                "selector": '[data-testid="tenure-review-evidence-composite"]',
            },
            {"name": "backtest", "selector": '[data-testid="tenure-review-evidence-backtest"]'},
            {
                "name": "marginal contribution",
                "selector": '[data-testid="tenure-review-evidence-marginal"]',
            },
            {
                "name": "long vix hedge",
                "selector": '[data-testid="tenure-review-evidence-long_vix"]',
            },
            {"name": "redundancy", "selector": '[data-testid="tenure-review-evidence-redundancy"]'},
        ],
        "status": "ref",
        "owner": "C3",
    },
    # The figures of a result card are written by the page script after Evaluate: ``figure_columns``
    # names what ``FIGURE_COLUMNS_JS`` reads (a Baseline and a Variant column, each a label and a
    # value per row); ``fields`` are the card's own fields (TT-31, TT-32), at one offset on every card.
    "swap_result_figures": {
        "host": "/ai-advisor",
        "open": {"tab": "asset-swaps", "action": "evaluate_swap"},
        "kind": _FIELDS,
        "group": "#swap-results-area",
        "rows": "#swap-results-area .sym-card",
        "fields": [
            {"name": "objective", "selector": ".swap-card-objective"},
            {"name": "heading", "selector": ".swap-card-heading"},
            {"name": "figures", "selector": ".stats-grid"},
            {"name": "verdict", "selector": ".gate-verdict-row"},
        ],
        "figure_columns": {
            "cards": "#swap-results-area .sym-card",
            "column_label": ".stats-col-label",
            "row": ".stat-row",
            "label": ".stat-label",
            "value": ".stat-value",
        },
        "status": "action",
        "owner": "C4",
    },
    "logic_change_result_figures": {
        "host": "/ai-advisor",
        "open": {"tab": "logic-changes", "action": "evaluate_logic_change"},
        "kind": _FIELDS,
        "group": "#logic-results-area",
        "rows": "#logic-results-area .sym-card",
        "fields": [
            {"name": "objective", "selector": ".proposal-card-objective"},
            {"name": "heading", "selector": ".proposal-card-heading", "optional": True},
            {"name": "figures", "selector": ".stats-grid"},
            {"name": "verdict", "selector": ".gate-verdict-row"},
        ],
        "figure_columns": {
            "cards": "#logic-results-area .sym-card",
            "column_label": ".stats-col-label",
            "row": ".stat-row",
            "label": ".stat-label",
            "value": ".stat-value",
        },
        "status": "action",
        "owner": "C4",
    },
    # A card's figures table: Metric first (text), then one or two figure columns, headers included
    # in the right-edge rule. A node count change row spans the figure columns.
    "stats_tables": {
        "host": "/ai-advisor",
        "open": {"tab": "frontrunner-builder"},
        "kind": _TABLE,
        "group": '[data-testid="stats-table"]',
        "numeric": ["Incumbent", "Candidate", "Live Baseline"],
        "status": "ref",
        "seed": "frontrunner_proposals",
        "owner": "C4",
    },
    "survivor_cards": {
        "host": "/ai-advisor",
        "open": {"tab": "strategy-builder"},
        "kind": _FIELDS,
        "group": '[data-testid="survivor-cards"]',
        "rows": '[data-testid="survivor-cards"] > .proposal-card',
        "fields": [
            {"name": "title", "selector": ".card-name"},
            {"name": "chips", "selector": ".advisory-only-badge"},
            {"name": "figures table", "selector": '[data-testid="stats-table"]'},
            {"name": "notes", "selector": ".caveats-block"},
        ],
        "status": "ref",
        "seed": "strategy_builder",
        "owner": "C4",
    },
    "pending_proposal_cards": {
        "host": "/ai-advisor",
        "open": {"tab": "frontrunner-builder"},
        "kind": _FIELDS,
        "group": '[data-testid="frontrunner-proposal-cards"]',
        "rows": '[data-testid="frontrunner-proposal-cards"] > .proposal-card',
        "fields": [
            {"name": "title", "selector": ".card-name"},
            {"name": "figures table", "selector": '[data-testid="stats-table"]'},
            {"name": "notes", "selector": ".fr-card-flow"},
            {"name": "actions", "selector": ".proposal-actions"},
        ],
        "status": "ref",
        "seed": "frontrunner_proposals",
        "owner": "C4",
    },
    "live_run_cards": {
        "host": "/ai-advisor",
        "open": {"tab": "strategy-builder", "action": "run_analysis"},
        "kind": _FIELDS,
        "group": None,
        "fields": [{"name": "title"}, {"name": "strip"}, {"name": "guidance"}],
        "status": "action",
        "owner": "C4",
    },
}

# Rows that may hold controls: a non-control element starts within 24 px of the element before it,
# or begins a line at the row's left edge; a control may sit at the row's end.
TOOLBAR_ROWS: dict[str, dict] = {
    "overview_toolbar": {
        "host": "/ai-advisor",
        "open": {"tab": "overview"},
        "row": '[data-testid="advisor-grid"]',
        "status": "ref",
        "owner": "C2",
    },
    "retirement_run_row": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "row": ".retirement-run-now-row",
        "status": "ref",
        "owner": "C3",
    },
    "tenure_header": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "row": ".tenure-review-header",
        "status": "ref",
        "owner": "C3",
    },
    "performance_toolbars": {
        "host": "/performance",
        "open": {},
        "row": ".hp-toolbar",
        "status": "ref",
        "owner": "C5",
    },
    "history_toolbar": {
        "host": "/history",
        "open": {},
        "row": ".hp-toolbar",
        "status": "ref",
        "owner": "C5",
    },
    "strategy_builder_run_controls": {
        "host": "/ai-advisor",
        "open": {"tab": "strategy-builder"},
        "row": ".run-controls-row",
        "status": "ref",
        "owner": "C4",
    },
    "swap_form": {
        "host": "/ai-advisor",
        "open": {"tab": "asset-swaps"},
        "row": '[data-testid="try-swap-form"]',
        "status": "ref",
        "owner": "C4",
    },
    "hero_top": {
        "host": "/",
        "open": {},
        "row": ".hero-top",
        "status": "ref",
        "owner": "C5",
    },
    "settings_actions": {
        "host": "/settings",
        "open": {},
        "row": '[data-testid="settings-actions"]',
        "status": "ref",
        "owner": "C5",
    },
    "market_prism_header": {
        "host": "/ai-advisor",
        "open": {"tab": "overview"},
        "row": ".prism-block-header",
        "status": "ref",
        "owner": "C2",
    },
}

# Figure and status lines: the gaps between adjacent items are equal and at most 48 px, and no
# figure's label or value breaks across lines at 1440 and wider.
FIGURE_LINES: dict[str, dict] = {
    "tracked_armed_triggered": {
        "host": "/",
        "open": {},
        "line": '[data-testid="mini-stats"]',
        "items": '[data-testid="mini-stat"]',
        "status": "ref",
        "owner": "C5",
    },
    "dollar_saved_line": {
        "host": "/",
        "open": {},
        "line": ".dollar-lead",
        "items": ":scope > *",
        "status": "ref",
        "owner": "C5",
    },
    "performance_headline_strip": {
        "host": "/performance",
        "open": {},
        "line": '[data-testid="headline-strip"]',
        "items": ".hp-fig",
        "status": "ref",
        "owner": "C5",
    },
    "history_hero_figures": {
        "host": "/history",
        "open": {},
        "line": '[data-testid="history-hero"] .hp-figs',
        "items": ".hp-fig",
        "status": "ref",
        "owner": "C5",
    },
    # At the reference the tiles' container is laid out with ``display: contents``, so it has no box
    # of its own to measure; the cycle that puts the tiles on one row pins the selectors.
    "retirement_last_run_tiles": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "line": None,
        "items": ".mini-stat",
        "status": "unpinned",
        "owner": "C3",
    },
    "tenure_run_line": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "line": ".tenure-review-run",
        "items": ":scope > span",
        "status": "ref",
        "owner": "C3",
    },
    "tenure_card_head": {
        "host": "/ai-advisor",
        "open": {"tab": "retirement"},
        "line": ".tenure-review-entry-head",
        "items": ":scope > *",
        "status": "ref",
        "owner": "C3",
    },
    "sleeve_card_line": {
        "host": "/",
        "open": {},
        "line": '[data-testid="sleeve-card"] > :first-child',
        "items": ":scope > *",
        "status": "ref",
        "owner": "C5",
    },
}


def open_group(probe, entry: dict) -> None:
    """Load the entry's host and reach the group the way ``entry['open']`` says.

    ``{}`` is the page itself; ``{"tab": name}`` chooses an Advisor tab; ``{"details": [...]}`` opens
    each named disclosure. A group a user action produces (``{"action": name}``) is opened by the
    test that changes it, not here: asking for it raises, so a registry entry is never measured
    over a page that does not hold the group.
    """
    # Imported here: the shell support module imports the browser harness.
    from tests.ui._shell_support import open_page

    wanted = entry.get("open", {})
    if "action" in wanted:
        raise LookupError(
            f"the group is made by the action {wanted['action']!r}; open it in the test"
        )
    open_page(probe, entry["host"])
    if "tab" in wanted:
        probe.page.click(f'[data-tab="{wanted["tab"]}"]:not([data-testid^="tab-panel"])')
        probe.page.wait_for_selector(
            f'[data-testid="tab-panel-{wanted["tab"]}"]', state="visible", timeout=10_000
        )
    for selector in wanted.get("details", []):
        probe.page.evaluate(
            "(s) => document.querySelectorAll(s).forEach((d) => { d.open = true; })", selector
        )
