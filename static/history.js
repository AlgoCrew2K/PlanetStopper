/**
 * history.js — Guard Alpha History tab client logic.
 *
 * Fetches /api/history/<days> and renders:
 *   - Hero stat strip
 *   - Daily alpha bar chart (SVG)
 *   - By-reason breakdown cards
 *   - Recent triggers table
 *
 * All colors resolved via getComputedStyle(document.documentElement) so
 * theme changes propagate without a reload. No Tailwind class names.
 */
(function () {
    'use strict';

    var cs = null;
    // F-020: the most recent /api/history payload, kept around so the day
    // drill-down (renderDayDrilldown below) can read daily_exits without a
    // second fetch.
    var lastPayload = null;
    // The day the operator drilled into by clicking its bar, or null for "Today's exits". A poll
    // re-renders THIS day from the new payload; only the Today's exits button or a window click clears it.
    var drilledDay = null;
    // The data stamp of the last good load, kept for the connection-lost cue.
    var lastDataStamp = null;

    function color(varName) {
        if (!cs) cs = getComputedStyle(document.documentElement);
        return cs.getPropertyValue(varName).trim();
    }

    function cssVar(name) {
        return 'var(' + name + ')';
    }

    function escHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ---------------------------------------------------------------------------
    // Hero stats
    // ---------------------------------------------------------------------------

    function renderHero(payload) {
        var isEmpty = (payload.total_alpha === 0 && payload.trigger_count === 0) ||
                      (payload.trigger_count === 0);
        var emptyEl = document.getElementById('history-empty-state');
        var heroEl = document.querySelector('[data-testid="history-hero"]');
        var dailyEl = document.querySelector('[data-testid="daily-strip"]');
        var reasonEl = document.querySelector('[data-testid="by-reason-section"]');
        if (emptyEl) emptyEl.style.display = isEmpty ? '' : 'none';
        if (heroEl) heroEl.style.display = isEmpty ? 'none' : '';
        if (dailyEl) dailyEl.style.display = isEmpty ? 'none' : '';
        if (reasonEl) reasonEl.style.display = isEmpty ? 'none' : '';
        if (isEmpty) return;

        var totalAlpha = typeof payload.total_alpha === 'number' ? alphaPointsText(payload.total_alpha) : '--';
        var totalSaved = dollarSavedText(payload.total_saved);
        var triggers = typeof payload.trigger_count === 'number' ? String(payload.trigger_count) : '--';
        var winRate = typeof payload.win_rate === 'number' ? payload.win_rate.toFixed(1) + '%' : '--';

        setId('val-total-alpha', totalAlpha);
        // The tile states its basis: a sum of per-exit points, not a portfolio return.
        setId('total-alpha-descriptor', 'Each exit\'s return minus its if-held return at 15:54 ET, added across '
            + exitCountText(payload.trigger_count) + ' with no size weighting. Not a portfolio return.');
        setId('val-total-saved', totalSaved);
        // DE-GAS-COHERENCE-001: color by SIGN (payload.total_saved can be negative --
        // a losing window must not render green like a winning one), same idiom as
        // the total_alpha coloring below and renderReasonCards' alphaColor.
        var savedEl = document.getElementById('val-total-saved');
        if (savedEl && typeof payload.total_saved === 'number') {
            savedEl.style.color = payload.total_saved >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg');
        }
        setId('val-trigger-count', triggers);
        setId('val-win-rate', winRate);

        var alphaEl = document.getElementById('val-total-alpha');
        if (alphaEl && typeof payload.total_alpha === 'number') {
            alphaEl.style.color = payload.total_alpha >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg');
        }

        var winRateEl = document.getElementById('val-win-rate');
        if (winRateEl && typeof payload.win_rate === 'number') {
            winRateEl.style.color = payload.win_rate >= 50 ? cssVar('--studio-pos') : cssVar('--studio-neg');
        }
    }

    function setId(id, text) {
        var el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    // A sum of per-exit alpha is in points ("pts"), never a percent: signed, two decimals.
    function alphaPointsText(value) {
        return (value >= 0 ? '+' : '') + value.toFixed(2) + ' pts';
    }

    function exitCountText(count) {
        if (typeof count !== 'number') return 'the window\'s exits';
        return count + (count === 1 ? ' exit' : ' exits');
    }

    // The one place a dollar result becomes text (the hero tile and every reason card):
    // "$" + the magnitude with thousands separators + " saved" or " lost" by the sign of
    // the figure. The magnitude rounds with toFixed (ties away from zero), the rule
    // analytics.format_dollar_saved applies to the Discord text, so the two never
    // disagree on a tie. A value that is not a number reads "--" with no word.
    function dollarSavedText(value) {
        if (typeof value !== 'number') return '--';
        var parts = Math.abs(value).toFixed(2).split('.');
        if (/^\d+$/.test(parts[0])) parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return '$' + parts.join('.') + (value < 0 ? ' lost' : ' saved');
    }

    // ---------------------------------------------------------------------------
    // Daily alpha bar chart (SVG)
    // ---------------------------------------------------------------------------

    function renderDailyChart(dailyAlpha, dailyDates) {
        var svg = document.getElementById('daily-chart-svg');
        setId('daily-chart-first', dailyDates && dailyDates.length ? String(dailyDates[0]) : '');
        setId('daily-chart-last', dailyDates && dailyDates.length > 1 ? String(dailyDates[dailyDates.length - 1]) : '');
        if (!svg || !dailyAlpha || dailyAlpha.length === 0) {
            if (svg) svg.innerHTML = '';
            return;
        }

        var posColor = color('--studio-pos');
        var negColor = color('--studio-neg');
        var n = dailyAlpha.length;
        var viewW = 1000;
        var viewH = 100;
        var barW = Math.max(1, (viewW / n) - 1);
        var maxAbs = Math.max.apply(null, dailyAlpha.map(Math.abs)) || 1;
        var midY = viewH / 2;

        svg.setAttribute('viewBox', '0 0 ' + viewW + ' ' + viewH);

        var bars = dailyAlpha.map(function (v, i) {
            var x = i * (viewW / n);
            var barH = Math.abs(v) / maxAbs * midY;
            var y = v >= 0 ? midY - barH : midY;
            var fill = v >= 0 ? posColor : negColor;
            // F-020: per-bar date hook (data-date + a <title> tooltip) so a
            // day is both hoverable and clickable for the drill-down below. The
            // tooltip also carries the day's figure, in the strip's own unit.
            var dateStr = (dailyDates && dailyDates[i]) || '';
            var dateAttr = dateStr ? ' data-date="' + escHtml(dateStr) + '"' : '';
            var titleTag = dateStr ? '<title>' + escHtml(dateStr + ': ' + alphaPointsText(v)) + '</title>' : '';
            return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" ' +
                'width="' + barW.toFixed(1) + '" height="' + barH.toFixed(1) + '" ' +
                'fill="' + escHtml(fill) + '" opacity="0.85"' + dateAttr + '>' + titleTag + '</rect>';
        });

        // baseline: a hairline that stays one pixel wide however the strip is scaled
        bars.push('<line x1="0" y1="' + midY + '" x2="' + viewW + '" y2="' + midY + '" ' +
            'stroke="' + escHtml(color('--studio-rule-hi')) + '" stroke-width="1" ' +
            'vector-effect="non-scaling-stroke"/>');

        svg.innerHTML = bars.join('');

        // F-020: wire per-bar click -> day drill-down. Re-rendering replaces
        // svg.innerHTML wholesale, so the old rects (and their listeners) are
        // discarded each time — no double-binding across renders.
        svg.querySelectorAll('rect[data-date]').forEach(function (rect) {
            rect.addEventListener('click', function () {
                renderDayDrilldown(rect.getAttribute('data-date'));
            });
        });
    }

    // Marks the bar of the day the exits table is showing; no argument clears the mark.
    function markDrilledBar(dateStr) {
        var svg = document.getElementById('daily-chart-svg');
        if (!svg) return;
        svg.querySelectorAll('rect[data-date]').forEach(function (rect) {
            if (dateStr && rect.getAttribute('data-date') === dateStr) rect.classList.add('is-drilled');
            else rect.classList.remove('is-drilled');
        });
    }

    // ---------------------------------------------------------------------------
    // By-reason cards
    // ---------------------------------------------------------------------------

    // Design spec (history.jsx REASON_META) defines exactly 4 canonical exit reasons (V-28).
    // All API reason strings are normalized to one of these 4 canonical keys before render.
    var CANONICAL_REASONS = ['Take-Profit', 'Trailing Stop', 'VWAP Breakdown', 'VWAP Bleed Cut'];

    var REASON_CANONICAL_MAP = {
        'take_profit':     'Take-Profit',
        'take-profit':     'Take-Profit',
        'take profit':     'Take-Profit',
        'trailing_stop':   'Trailing Stop',
        'trailing-stop':   'Trailing Stop',
        'trailing stop':   'Trailing Stop',
        'stop_loss':       'Trailing Stop',
        'stop-loss':       'Trailing Stop',
        'parabolic_stop':  'Trailing Stop',
        'parabolic stop':  'Trailing Stop',
        'vwap_breakdown':  'VWAP Breakdown',
        'vwap-breakdown':  'VWAP Breakdown',
        'vwap breakdown':  'VWAP Breakdown',
        'vwap_bleed_cut':  'VWAP Bleed Cut',
        'vwap bleed cut':  'VWAP Bleed Cut',
        'vwap_bleed':      'VWAP Bleed Cut',
        'vwap-bleed':      'VWAP Bleed Cut',
        'vwap bleed':      'VWAP Bleed Cut',
        'bleed_cut':       'VWAP Bleed Cut',
        'bleed-cut':       'VWAP Bleed Cut',
        'bleed cut':       'VWAP Bleed Cut',
    };

    // The names shown for the canonical reasons; the keys stay as the API and the stored exits spell them.
    var REASON_DISPLAY = {
        'Take-Profit': 'Take-profit',
        'Trailing Stop': 'Trailing stop',
        'VWAP Breakdown': 'VWAP breakdown',
        'VWAP Bleed Cut': 'VWAP bleed',
    };

    function reasonDisplayName(reason) {
        return REASON_DISPLAY[reason] || reason;
    }

    var REASON_STRIP_COLOR = {
        'Take-Profit':    '--studio-plum',
        'Trailing Stop':  '--studio-neg',
        'VWAP Breakdown': '--studio-cyan',
        'VWAP Bleed Cut': '--studio-warn',
    };

    // A reason is a coloured dot and its name; hp-dot--<key> in history.html colours the dot with
    // the same four tokens as REASON_STRIP_COLOR.
    var REASON_DOT_CLASS = {
        'Take-Profit':    'tp',
        'Trailing Stop':  'stop',
        'VWAP Breakdown': 'vwap',
        'VWAP Bleed Cut': 'bleed',
    };

    function canonicalizeByReason(byReason) {
        var merged = {};
        CANONICAL_REASONS.forEach(function (c) {
            merged[c] = { alpha: 0, count: 0, wins: 0, dollars: 0 };
        });
        Object.keys(byReason).forEach(function (rawKey) {
            var normalized = rawKey.toLowerCase().replace(/-/g, '_').replace(/\s+/g, '_');
            var canonical = REASON_CANONICAL_MAP[normalized] || REASON_CANONICAL_MAP[rawKey.toLowerCase()] || null;
            if (!canonical) return;
            var src = byReason[rawKey];
            merged[canonical].alpha   += (src.alpha   || 0);
            merged[canonical].count   += (src.count   || 0);
            merged[canonical].wins    += (src.wins    || 0);
            merged[canonical].dollars += (src.dollars || 0);
        });
        // Drop canonical slots with zero data so empty cards don't render
        var result = {};
        CANONICAL_REASONS.forEach(function (c) {
            if (merged[c].count > 0) result[c] = merged[c];
        });
        return result;
    }

    function reasonStripColor(reason) {
        return REASON_STRIP_COLOR[reason] || '--studio-accent';
    }

    function reasonDotHtml(reason) {
        var key = REASON_DOT_CLASS[reason];
        return '<span class="hp-dot' + (key ? ' hp-dot--' + key : '') + '"></span>';
    }

    function renderReasonCards(byReason) {
        var container = document.getElementById('reason-cards');
        if (!container) return;

        // V-28: canonicalize API keys to the design's 4 exit reasons before render.
        var canonical = canonicalizeByReason(byReason || {});

        if (Object.keys(canonical).length === 0) {
            // The message is one row of the ledger, spanning every column.
            container.innerHTML = '<tr><td class="hp-empty-cell" colspan="7">No exit reason data for this window.</td></tr>';
            return;
        }

        // One row per reason: the dot and name, then the figures under the table's own column
        // headers, so no label repeats on a row. On a phone the row stacks and each figure
        // carries its label (data-label).
        var html = Object.keys(canonical).map(function (reason) {
            var s = canonical[reason];
            var alphaNum = typeof s.alpha === 'number' ? s.alpha : null;
            var alphaStr = alphaNum !== null ? alphaPointsText(alphaNum) : '--';
            var alphaColor = alphaNum !== null
                ? (alphaNum >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg'))
                : cssVar('--studio-ink');
            var stripToken = reasonStripColor(reason);
            var winRateNum = s.count > 0 ? (s.wins / s.count) * 100 : 0;
            var rateColor = winRateNum >= 50 ? cssVar('--studio-pos') : cssVar('--studio-neg');
            var avgAlpha = s.count > 0 && alphaNum !== null ? alphaNum / s.count : null;
            var avgStr = avgAlpha !== null ? (avgAlpha >= 0 ? '+' : '') + avgAlpha.toFixed(3) + ' pts' : '--';

            return (
                '<tr data-testid="reason-card">' +
                '<td>' + reasonDotHtml(reason) + escHtml(reasonDisplayName(reason)) + '</td>' +
                '<td class="num hp-strong" data-label="Sum of exit alpha" style="color:' + alphaColor + ';">' +
                escHtml(alphaStr) + '</td>' +
                '<td class="num" data-label="Dollar result">' + escHtml(dollarSavedText(s.dollars)) + '</td>' +
                '<td class="num" data-label="Exits">' + escHtml(String(s.count)) + '</td>' +
                '<td class="num" data-label="Wins">' + escHtml((s.wins || 0) + '/' + s.count) + '</td>' +
                // B-13: reason-bar SVG whose rect width reflects win-rate for this reason.
                '<td class="num" data-label="Win rate" style="color:' + rateColor + ';"><span class="hp-rate">' +
                '<svg data-testid="reason-bar" viewBox="0 0 100 4" preserveAspectRatio="none" width="48" height="4">' +
                '<rect x="0" y="0" width="' + Math.min(winRateNum, 100).toFixed(1) + '" height="4" fill="' + cssVar(stripToken) + '"></rect>' +
                '</svg>' + escHtml(winRateNum.toFixed(0) + '%') + '</span></td>' +
                '<td class="num" data-label="Avg per exit" data-testid="avg-per-exit">' + escHtml(avgStr) + '</td>' +
                '</tr>'
            );
        }).join('');

        container.innerHTML = html;
    }

    // ---------------------------------------------------------------------------
    // Recent triggers
    // ---------------------------------------------------------------------------

    var MONTH_ABBREVIATIONS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // "Oct 30" from the ISO digits of the session day, so the browser's zone plays no part; a
    // value that is not an ISO date is shown as sent (as text, never markup).
    function sessionDayLabel(isoDay) {
        var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDay));
        if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return String(isoDay);
        return MONTH_ABBREVIATIONS[Number(m[2]) - 1] + ' ' + Number(m[3]);
    }

    // While the market is closed the exits are the closed session's, and the label says which day;
    // while it is open they are today's.
    function exitsLabel(payload) {
        if (payload && payload.todays_exits_market_closed === true && payload.todays_exits_day) {
            return 'Exits on ' + sessionDayLabel(payload.todays_exits_day);
        }
        return 'Today\'s exits';
    }

    function renderTriggers(payload) {
        var tbody = document.getElementById('triggers-tbody');
        if (!tbody) return;

        var todays_exits = payload.todays_exits || [];

        var heading = document.getElementById('todays-exits-heading');
        if (heading) {
            heading.textContent = exitsLabel(payload) + ' (' + todays_exits.length + ')';
        }

        if (todays_exits.length === 0) {
            tbody.innerHTML =
                '<tr><td colspan="4" class="hp-empty-cell">No exit records for this window.</td></tr>';
            return;
        }

        tbody.innerHTML = todays_exits.map(function (rec) {
            var symDisplay = rec.symphony_name || rec.symphony_id || '—';
            var rawReason = String(rec.reason || '—');
            // V-31: the reason's dot is coloured per strategy — resolve to the canonical key for the lookup.
            var normalizedKey = rawReason.toLowerCase().replace(/-/g, '_').replace(/\s+/g, '_');
            var canonicalReason = REASON_CANONICAL_MAP[normalizedKey] || REASON_CANONICAL_MAP[rawReason.toLowerCase()] || null;
            // DE-GAS-COHERENCE-001: the Detail % is a signed guard-alpha figure --
            // color by sign (same convention as renderHero's alpha and
            // renderReasonCards' alphaColor), not the flat --studio-ink-dim a
            // losing exit previously shared with a winning one.
            var detailColor = typeof rec.detail === 'number'
                ? (rec.detail >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg'))
                : cssVar('--studio-ink-dim');
            return (
                '<tr>' +
                '<td class="hp-time">' + escHtml(String(rec.ts || '—')) + '</td>' +
                '<td>' + escHtml(String(symDisplay)) + '</td>' +
                '<td>' + reasonDotHtml(canonicalReason) + escHtml(canonicalReason ? reasonDisplayName(canonicalReason) : rawReason) + '</td>' +
                '<td class="num" style="color:' + detailColor + ';">' + escHtml((function(d) {
                    if (typeof d !== 'number') return String(d || '—');
                    return (d >= 0 ? '+' : '') + d.toFixed(2) + '%';
                })(rec.detail)) + '</td>' +
                '</tr>'
            );
        }).join('');
    }

    // ---------------------------------------------------------------------------
    // Per-day drill-down (F-020)
    // ---------------------------------------------------------------------------

    function setTodaysExitsButtonVisible(visible) {
        var btn = document.querySelector('[data-testid="history-todays-exits-btn"]');
        if (!btn) return;
        if (visible) btn.removeAttribute('hidden'); else btn.setAttribute('hidden', '');
        if (lastPayload) btn.textContent = exitsLabel(lastPayload);
    }

    function clearDrilledDay() {
        drilledDay = null;
        setTodaysExitsButtonVisible(false);
        markDrilledBar(null);
    }

    function renderDayDrilldown(dateStr) {
        drilledDay = dateStr;
        setTodaysExitsButtonVisible(true);
        var exits = (lastPayload && lastPayload.daily_exits && lastPayload.daily_exits[dateStr]) || [];
        // List the day's exits by trigger time (HH:MM, earliest first); an exit with
        // no time goes last. Exits that share a time, and the untimed ones, keep the
        // file's order: the index is the last sort key, so no sort stability is assumed.
        var ordered = exits.map(function (e, index) {
            return { exit: e, ts: e.time_triggered || e.ts || '', index: index };
        }).sort(function (a, b) {
            if (!a.ts !== !b.ts) return a.ts ? -1 : 1;
            if (a.ts !== b.ts) return a.ts < b.ts ? -1 : 1;
            return a.index - b.index;
        });
        // Reuse the existing triggers-tbody surface rather than a parallel
        // render target — renderTriggers sets its own "Today's exits (N)"
        // heading first, overridden with the selected day's label below.
        renderTriggers({
            todays_exits: ordered.map(function (o) {
                return {
                    ts: o.ts,
                    symphony_id: o.exit.symphony_id,
                    // The name the producer wrote; an exit without one says so
                    // rather than showing the identifier as if it were a name.
                    symphony_name: o.exit.symphony_name || 'name not recorded',
                    reason: o.exit.reason,
                    detail: o.exit.detail
                };
            })
        });
        var heading = document.getElementById('todays-exits-heading');
        if (heading) {
            heading.textContent = sessionDayLabel(dateStr) + ' exits (' + exits.length + ')';
        }
        markDrilledBar(dateStr);
    }

    // ---------------------------------------------------------------------------
    // Main fetch + render
    // ---------------------------------------------------------------------------

    function windowDays(val) {
        // YTD travels as the token; the server resolves it to 1 January, the same
        // cutoff the dashboard and the Performance tab use for it.
        if (val === 'ytd') return val;
        return parseInt(val, 10) || 30;
    }

    // The page's data age: the server's own "data as of" stamp for the newest exit it shows, or the
    // connection-lost cue (with the last good stamp) after a poll that did not bring data. Set through
    // textContent, so a stamp is never read as markup.
    function writeDataStamp(connectionLost) {
        var el = document.getElementById('history-data-as-of');
        if (!el) return;
        var asOf = lastDataStamp === null ? null : 'data as of ' + lastDataStamp;
        if (connectionLost) {
            el.textContent = asOf === null ? 'connection lost' : 'connection lost · ' + asOf;
        } else {
            el.textContent = asOf === null ? '' : asOf;
        }
    }

    function isHistoryBody(payload) {
        return !!payload && typeof payload === 'object' && !Array.isArray(payload) &&
            payload.status !== 'error';
    }

    window.loadHistory = function loadHistory(windowVal) {
        cs = null; // reset CSS var cache on each call (theme may have changed)
        var days = windowDays(windowVal);
        // The newest request issued is the only one drawn: a window click is never overwritten by a
        // poll that was already in flight.
        var request = PSRequest.issue('history');
        return PSRequest.getJson('/api/history/' + days).then(function (res) {
            if (!PSRequest.isLatest('history', request)) return;
            if (!res.ok) {
                // A lapsed session goes to the login page; any other answer is not data, so the page
                // keeps what it shows and says the connection was lost.
                if (res.kind === 'unauthorized') { PSRequest.goLogin(); return; }
                writeDataStamp(true);
                return;
            }
            var payload = res.body;
            if (!isHistoryBody(payload)) { writeDataStamp(true); return; }
            try {
                lastPayload = payload; // F-020: source for the day drill-down
                setTodaysExitsButtonVisible(drilledDay !== null);
                renderHero(payload);
                renderDailyChart(payload.daily_alpha || [], payload.daily_dates || []);
                // V-28: canonicalize before rendering to enforce 4-card design spec.
                renderReasonCards(payload.by_reason || {});
                // A drilled day stays drilled across polls and is drawn from the new payload; a day the
                // window no longer holds falls back to today's exits.
                if (drilledDay !== null && (payload.daily_dates || []).indexOf(drilledDay) === -1) {
                    clearDrilledDay();
                }
                if (drilledDay !== null) renderDayDrilldown(drilledDay); else renderTriggers(payload);
                lastDataStamp = typeof payload.data_as_of === 'string' ? payload.data_as_of : '--';
                writeDataStamp(false);
            } catch (err) {
                console.error('history render failed', err);
            }
        });
    };

    var currentWindow = '30';

    window.historySelectWindow = function historySelectWindow(btn, windowVal) {
        var strip = document.getElementById('window-picker');
        if (strip) {
            strip.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
        }
        btn.classList.add('active');
        currentWindow = windowVal;
        clearDrilledDay();
        loadHistory(windowVal);
    };

    // The way back from a drilled day to today's exits.
    window.historyShowTodaysExits = function historyShowTodaysExits() {
        clearDrilledDay();
        if (lastPayload) renderTriggers(lastPayload);
    };

    document.addEventListener('DOMContentLoaded', function () {
        loadHistory(currentWindow);
        function _historyPoll() { loadHistory(currentWindow); }
        setInterval(_historyPoll, 30000);
    });
})();
