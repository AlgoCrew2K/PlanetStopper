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

        var totalAlpha = typeof payload.total_alpha === 'number' ? payload.total_alpha.toFixed(2) + '%' : '--';
        var totalSaved = typeof payload.total_saved === 'number'
            ? '$' + payload.total_saved.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : '--';
        var triggers = typeof payload.trigger_count === 'number' ? String(payload.trigger_count) : '--';
        var winRate = typeof payload.win_rate === 'number' ? payload.win_rate.toFixed(1) + '%' : '--';

        setId('val-total-alpha', totalAlpha);
        setId('val-total-saved', totalSaved);
        setId('val-trigger-count', triggers);
        setId('val-win-rate', winRate);

        var alphaEl = document.getElementById('val-total-alpha');
        if (alphaEl && typeof payload.total_alpha === 'number') {
            alphaEl.style.color = payload.total_alpha >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg');
        }

        var savedEl = document.getElementById('val-total-saved');
        if (savedEl) {
            savedEl.style.color = cssVar('--studio-pos');
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

    // ---------------------------------------------------------------------------
    // Daily alpha bar chart (SVG)
    // ---------------------------------------------------------------------------

    function renderDailyChart(dailyAlpha) {
        var svg = document.getElementById('daily-chart-svg');
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
            return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" ' +
                'width="' + barW.toFixed(1) + '" height="' + barH.toFixed(1) + '" ' +
                'fill="' + escHtml(fill) + '" opacity="0.85"/>';
        });

        // baseline
        bars.push('<line x1="0" y1="' + midY + '" x2="' + viewW + '" y2="' + midY + '" ' +
            'stroke="' + escHtml(color('--studio-rule')) + '" stroke-width="0.5"/>');

        svg.innerHTML = bars.join('');
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

    var REASON_DESCRIPTIONS = {
        'Take-Profit':    'Monte Carlo collapse — mean reversion expected.',
        'Trailing Stop':  'Vol-scaled trailing stop hit after peak.',
        'VWAP Breakdown': 'Price dropped below VWAP after HWM — System A.',
        'VWAP Bleed Cut': 'Slow bleed below vol-scaled VWAP floor — System B.',
    };

    var REASON_STRIP_COLOR = {
        'Take-Profit':    '--studio-plum',
        'Trailing Stop':  '--studio-neg',
        'VWAP Breakdown': '--studio-cyan',
        'VWAP Bleed Cut': '--studio-warn',
    };

    var REASON_BADGE = {
        'Take-Profit':    'TP',
        'Trailing Stop':  'STOP',
        'VWAP Breakdown': 'VWAP',
        'VWAP Bleed Cut': 'BLEED',
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

    function reasonBadge(reason) {
        return REASON_BADGE[reason] || reason.slice(0, 4).toUpperCase();
    }

    function renderReasonCards(byReason) {
        var container = document.getElementById('reason-cards');
        if (!container) return;

        // V-28: canonicalize API keys to the design's 4 exit reasons before render.
        var canonical = canonicalizeByReason(byReason || {});

        if (Object.keys(canonical).length === 0) {
            container.innerHTML = '<div style="padding:2rem;text-align:center;color:' +
                cssVar('--studio-ink-dim') + ';font-size:0.875rem;">No exit reason data for this window.</div>';
            return;
        }

        var html = Object.keys(canonical).map(function (reason) {
            var s = canonical[reason];
            var alphaNum = typeof s.alpha === 'number' ? s.alpha : null;
            var alphaStr = alphaNum !== null ? (alphaNum >= 0 ? '+' : '') + alphaNum.toFixed(2) + '%' : '--';
            var alphaColor = alphaNum !== null
                ? (alphaNum >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg'))
                : cssVar('--studio-ink');
            var dollars = typeof s.dollars === 'number'
                ? '$' + s.dollars.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                : '--';
            var stripToken = reasonStripColor(reason);
            var badge = reasonBadge(reason);
            var winRateNum = s.count > 0 ? (s.wins / s.count) * 100 : 0;
            var descText = REASON_DESCRIPTIONS[reason] || reason;
            var avgAlpha = s.count > 0 && alphaNum !== null ? alphaNum / s.count : null;
            var avgStr = avgAlpha !== null ? (avgAlpha >= 0 ? '+' : '') + avgAlpha.toFixed(3) + '%' : '--';

            return (
                // V-33: use --studio-surface (paper token) so cards are dark in dark theme.
                // V-29: 4px colored top border per design spec (position:absolute span, top 0).
                '<div data-testid="reason-card" style="' +
                'background:' + cssVar('--studio-surface') + ';' +
                'border:1px solid ' + cssVar('--studio-border') + ';' +
                'border-radius:0.75rem;overflow:hidden;position:relative;">' +
                '<span style="position:absolute;top:0;left:0;right:0;height:4px;background:' + cssVar(stripToken) + ';display:block;"></span>' +
                // B-13: reason-bar SVG whose rect width reflects win-rate for this reason.
                '<svg data-testid="reason-bar" viewBox="0 0 100 4" preserveAspectRatio="none" width="100%" height="4" style="display:block;">' +
                '<rect x="0" y="0" width="' + Math.min(winRateNum, 100).toFixed(1) + '" height="4" fill="' + cssVar(stripToken) + '" opacity="0.35"></rect>' +
                '</svg>' +
                '<div style="padding:1rem;padding-top:0.75rem;">' +
                '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:0.5rem;">' +
                '<span style="font-size:0.8125rem;font-weight:700;color:' + cssVar('--studio-ink') + ';">' + escHtml(reason) + '</span>' +
                '<span style="padding:2px 7px;background:color-mix(in srgb,' + cssVar(stripToken) + ' 13%,transparent);border:1px solid color-mix(in srgb,' + cssVar(stripToken) + ' 33%,transparent);' +
                'border-radius:999px;font-size:0.625rem;font-weight:700;color:' + cssVar(stripToken) + ';letter-spacing:0.08em;">' + escHtml(badge) + '</span>' +
                '</div>' +
                '<div style="font-size:1.75rem;font-weight:800;color:' + alphaColor + ';line-height:1;letter-spacing:-0.02em;font-variant-numeric:tabular-nums;">' +
                escHtml(alphaStr) + '</div>' +
                '<div style="font-size:0.6875rem;color:' + cssVar('--studio-ink-dim') + ';margin-top:0.25rem;margin-bottom:0.75rem;">' +
                'cumulative α · ' + escHtml(dollars) + ' saved' +
                '</div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0.375rem;padding-top:0.75rem;border-top:1px solid ' + cssVar('--studio-border') + ';">' +
                '<div><div style="font-size:0.5625rem;color:' + cssVar('--studio-ink-dim') + ';text-transform:uppercase;letter-spacing:0.1em;font-weight:700;">Triggers</div>' +
                '<div style="font-size:0.8125rem;font-weight:600;color:' + cssVar('--studio-ink') + ';margin-top:1px;">' + escHtml(String(s.count)) + '</div></div>' +
                '<div><div style="font-size:0.5625rem;color:' + cssVar('--studio-ink-dim') + ';text-transform:uppercase;letter-spacing:0.1em;font-weight:700;">Wins</div>' +
                '<div style="font-size:0.8125rem;font-weight:600;color:' + (winRateNum >= 50 ? cssVar('--studio-pos') : cssVar('--studio-neg')) + ';margin-top:1px;">' + escHtml((s.wins || 0) + '/' + s.count) + '</div></div>' +
                '<div data-testid="avg-per-exit"><div style="font-size:0.5625rem;color:' + cssVar('--studio-ink-dim') + ';text-transform:uppercase;letter-spacing:0.1em;font-weight:700;">Win rate</div>' +
                '<div style="font-size:0.8125rem;font-weight:600;color:' + (winRateNum >= 50 ? cssVar('--studio-pos') : cssVar('--studio-neg')) + ';margin-top:1px;">' + escHtml(winRateNum.toFixed(0) + '%') + '</div></div>' +
                '</div>' +
                '<div style="font-size:0.6875rem;color:' + cssVar('--studio-ink-dim') + ';margin-top:0.625rem;">' + escHtml(descText) + '</div>' +
                '<div style="font-size:0.625rem;color:' + cssVar('--studio-ink-dim') + ';margin-top:0.25rem;font-variant-numeric:tabular-nums;">' +
                'avg per exit: ' + escHtml(avgStr) + '</div>' +
                '</div>' +
                '</div>'
            );
        }).join('');

        container.innerHTML = html;
    }

    // ---------------------------------------------------------------------------
    // Recent triggers
    // ---------------------------------------------------------------------------

    function renderTriggers(payload) {
        var tbody = document.getElementById('triggers-tbody');
        if (!tbody) return;

        var todays_exits = payload.todays_exits || [];

        var heading = document.getElementById('todays-exits-heading');
        if (heading) {
            heading.textContent = 'Today\'s exits (' + todays_exits.length + ')';
        }

        if (todays_exits.length === 0) {
            tbody.innerHTML =
                '<tr><td colspan="4" style="text-align:center;padding:1rem;color:' +
                cssVar('--studio-ink-dim') + ';">No exit records for this window.</td></tr>';
            return;
        }

        tbody.innerHTML = todays_exits.map(function (rec) {
            var symDisplay = rec.symphony_name || rec.symphony_id || '—';
            var rawReason = String(rec.reason || '—');
            // V-31: color REASON cell per strategy — resolve to canonical key for color lookup.
            var normalizedKey = rawReason.toLowerCase().replace(/-/g, '_').replace(/\s+/g, '_');
            var canonicalReason = REASON_CANONICAL_MAP[normalizedKey] || REASON_CANONICAL_MAP[rawReason.toLowerCase()] || null;
            var reasonColor = canonicalReason
                ? cssVar(REASON_STRIP_COLOR[canonicalReason] || '--studio-ink')
                : cssVar('--studio-ink');
            return (
                '<tr style="border-bottom:1px solid ' + cssVar('--studio-border') + ';">' +
                '<td style="padding:0.375rem 0.75rem;color:' + cssVar('--studio-ink-dim') + ';">' + escHtml(String(rec.ts || '—')) + '</td>' +
                '<td style="padding:0.375rem 0.75rem;color:' + cssVar('--studio-ink-dim') + ';">' + escHtml(String(symDisplay)) + '</td>' +
                '<td style="padding:0.375rem 0.75rem;font-weight:700;letter-spacing:0.04em;color:' + reasonColor + ';">' + escHtml(rawReason) + '</td>' +
                '<td style="padding:0.375rem 0.75rem;color:' + cssVar('--studio-ink-dim') + ';">' + escHtml((function(d) {
                    if (typeof d !== 'number') return String(d || '—');
                    return (d >= 0 ? '+' : '') + d.toFixed(2) + '%';
                })(rec.detail)) + '</td>' +
                '</tr>'
            );
        }).join('');
    }

    // ---------------------------------------------------------------------------
    // Main fetch + render
    // ---------------------------------------------------------------------------

    function windowDays(val) {
        if (val === 'ytd') {
            var now = new Date();
            var startOfYear = new Date(now.getFullYear(), 0, 1);
            return Math.ceil((now - startOfYear) / 86400000);
        }
        return parseInt(val, 10) || 90;
    }

    window.loadHistory = function loadHistory(windowVal) {
        cs = null; // reset CSS var cache on each call (theme may have changed)
        var days = windowDays(windowVal);
        fetch('/api/history/' + days)
            .then(function (resp) { return resp.json(); })
            .then(function (payload) {
                renderHero(payload);
                renderDailyChart(payload.daily_alpha || []);
                // V-28: canonicalize before rendering to enforce 4-card design spec.
                renderReasonCards(payload.by_reason || {});
                renderTriggers(payload);
            })
            .catch(function (err) {
                console.error('history load failed', err);
            });
    };

    var currentWindow = '90';

    window.historySelectWindow = function historySelectWindow(btn, windowVal) {
        var strip = document.getElementById('window-picker');
        if (strip) {
            strip.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
        }
        btn.classList.add('active');
        currentWindow = windowVal;
        loadHistory(windowVal);
    };

    document.addEventListener('DOMContentLoaded', function () {
        loadHistory(currentWindow);
        function _historyPoll() { loadHistory(currentWindow); }
        setInterval(_historyPoll, 30000);
    });
})();
