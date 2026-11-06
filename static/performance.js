/**
 * performance.js — Performance tab client logic.
 *
 * Read-only operator surface:
 *   - Fetches /api/performance and /api/performance/symphonies.
 *   - Renders cumulative-return curves (if held vs bot (shadow)) via Chart.js.
 *   - Renders a 7-metric quantstats table with delta column.
 *
 * Auto-refresh floor is 60s (the aggregate series derives from shadow_history's
 * daily portfolio returns — the day's point moves at most once per engine cycle;
 * polling faster burns CPU re-parsing near-identical JSON — well above the 15s
 * live-cycle floor).
 */
(function () {
    'use strict';

    var chartInstance = null;

    // Captured once from the Jinja-rendered "Insufficient history" markup so
    // renderBanner() can restore it after overwriting the banner with the
    // AC-4 unrecognized-symphony-id message (F-023 / DE-PERFVIEW-ID-MISMATCH).
    var _defaultBannerHtml = null;

    function hexToRgba(hex, alpha) {
        hex = hex.replace(/^#/, '');
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        var r = parseInt(hex.slice(0, 2), 16);
        var g = parseInt(hex.slice(2, 4), 16);
        var b = parseInt(hex.slice(4, 6), 16);
        if (isNaN(r) || isNaN(g) || isNaN(b)) return 'rgba(31,122,77,' + alpha + ')';
        return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
    }

    // Series values arrive as percentage points (e.g. -0.56 = -0.56%).
    // total_return, annualized_return, max_drawdown, volatility come from quantstats
    // (fraction scale, e.g. -0.17 = -17%) — convert to pct for display.
    // win_rate is always a fraction [0,1] from .mean().
    //
    // Tuple shape (Phase 2): [key, label, kind, isPrimary, invert].
    // Order defines rendering order; risk-adjusted metrics lead per the design hierarchy
    // (ux-design-deliverable.md §2.1 — capital preservation north star ranks risk above return).
    // kind 'pp' = fraction-scale delta of two returns, rendered as a gap in % (e.g. +1.70%).
    // kind 'pct_mag' = a fraction shown as a positive magnitude (the Max DD legs: a
    // drawdown of -0.132 reads 13.20%, as the dashboard shows it); its delta cell is
    // still the signed fraction difference, so the sign says which leg drew down less.
    // invert (AC-7 / MAPERF-05, optional — defaults falsy when omitted): true for
    // lower-is-better metrics (e.g. volatility), mirroring index.js's Risk Profile
    // panel invertDelta flag (index.js:466-479) — flips which sign of the raw
    // (shadow - live) delta renders as the "good" color. The displayed VALUE and
    // arrow direction are UNCHANGED by invert — only the color decision flips.
    var METRIC_LABELS = [
        // --- risk-adjusted: primary (bold) ---
        ['sharpe',              'Sharpe',                    'num',      true],
        ['sortino',             'Sortino',                   'num',      false],
        ['max_drawdown',        'Max DD',                    'pct_mag',  true, true],  // lower is better
        ['max_drawdown_delta',  'Max DD reduction',          'pp',       false],  // derived
        ['calmar',              'Calmar',                    'num',      false],
        // --- volatility ---
        ['volatility',          'Annualized volatility',     'pct_frac', false, true],  // Tier 1, invert (lower is better)
        ['volatility_delta',    'Volatility reduction',      'pp',       false],  // Tier 1, derived
        // --- return ---
        ['total_return',        'Total return',              'pct_frac', true],
        ['annualized_return',   'Annualized return (CAGR)',  'pct_frac', false],
        ['win_rate',            'Up days',                   'frac',     false],
    ];

    // toFixed that never prints a minus sign for a value that rounds to zero ("-0.00"); the same
    // rule as the Dashboard's fixedNoNegZero (static/index.js).
    function fixedNoNegZero(value, digits) {
        var text = value.toFixed(digits);
        return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text;
    }

    // True when the value prints as zero at the given decimals, whichever side of zero it is on.
    function roundsToZero(value, digits) {
        return /^-?0(\.0+)?$/.test(value.toFixed(digits));
    }

    // A value that rounds to zero at the shown decimals reads as plain zero: no minus sign (and no
    // plus sign either, which would claim a direction the figure does not have).
    function signedFixed(value, digits) {
        return (roundsToZero(value, digits) || value < 0 ? '' : '+') + fixedNoNegZero(value, digits);
    }

    function fmt(value, kind) {
        if (value === null || value === undefined) return '—';
        if (typeof value !== 'number' || !isFinite(value)) return '—';
        if (Math.abs(value) > 1000) return '—';
        // pct_frac: quantstats returns fractions; multiply by 100 for display
        if (kind === 'pct_frac') return fixedNoNegZero(value * 100, 2) + '%';
        if (kind === 'pct_mag') return (Math.abs(value) * 100).toFixed(2) + '%';
        if (kind === 'frac') return fixedNoNegZero(value * 100, 2) + '%';
        // pp: fraction-scale delta of two returns, shown as a gap in % (e.g. 0.017 → +1.70%)
        if (kind === 'pp') return signedFixed(value * 100, 2) + '%';
        return fixedNoNegZero(value, 3);
    }

    function fmtDelta(live, shadow, kind, lowerIsBetter) {
        if (live === null || shadow === null || live === undefined || shadow === undefined) return '—';
        if (typeof live !== 'number' || typeof shadow !== 'number') return '—';
        if (!isFinite(live) || !isFinite(shadow)) return '—';
        if (Math.abs(live) > 1000 || Math.abs(shadow) > 1000) return '—';
        // The lower-is-better rows (the drawdown and volatility legs) share one convention and
        // one unit, stated in the column header: bot minus baseline, a gap in %, taken over
        // the magnitudes the legs show. A negative delta is the bot drawing down or swinging less.
        var tallied = kind === 'pct_mag' || lowerIsBetter;
        var delta = tallied
            ? (kind === 'pct_mag' ? Math.abs(shadow) - Math.abs(live) : shadow - live)
            : shadow - live;
        var formatted = fmt(delta, tallied ? 'pp' : kind);
        if (formatted === '—') return formatted;
        // A delta that prints as zero has no direction: no arrow, as for an exactly zero delta.
        if (/^[+-]?0(\.0+)?%?$/.test(formatted)) return formatted;
        // One sign per delta: the 'pp' kind already carries its own '+', the others do not.
        if (delta > 0) return '↑ ' + (formatted.charAt(0) === '+' ? '' : '+') + formatted;
        if (delta < 0) return '↓ ' + formatted;
        return formatted;
    }

    // Delta color decision lives inline in renderMetrics (AC-7 / MAPERF-05) —
    // mirrors index.js's Risk Profile panel, which also inlines its
    // invert-aware deltaGood check at the render call site rather than a
    // shared helper (index.js:479).

    function cumulative(returns) {
        // Series values are percentage points (e.g. -0.56 = -0.56%).
        // Convert to fraction for compounding, then back to percentage for display.
        var out = [];
        var acc = 1.0;
        for (var i = 0; i < returns.length; i++) {
            acc = acc * (1.0 + returns[i] / 100.0);
            out.push((acc - 1.0) * 100.0);
        }
        return out;
    }

    var MONTH_ABBREVIATIONS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // "Aug 31" from an ISO day, when the whole series sits in one calendar year; a series that
    // crosses a year keeps its ISO days (the year is part of what the tick tells), and a label that
    // is not an ISO day is shown as sent.
    function tickDayLabel(label, spansYears) {
        var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(label));
        if (!m || spansYears || Number(m[2]) < 1 || Number(m[2]) > 12) return String(label);
        return MONTH_ABBREVIATIONS[Number(m[2]) - 1] + ' ' + Number(m[3]);
    }

    function seriesSpansYears(dates) {
        if (!dates || dates.length < 2) return false;
        return String(dates[0]).slice(0, 4) !== String(dates[dates.length - 1]).slice(0, 4);
    }

    // Chart.js plugin: writes each series' final figure at the right edge of the chart, in the
    // series' own ink (the bot's in the accent, the held line's in the dim ink), on a paper ground
    // so a gridline never runs through it. Two labels that would overlap are moved apart (V-15).
    var endpointLabelPlugin = {
        id: 'endpointLabels',
        afterDraw: function (chart) {
            var datasets = chart.data.datasets;
            if (!datasets || datasets.length < 2) return;
            var ctx = chart.ctx;
            var xAxis = chart.scales.x;
            var yAxis = chart.scales.y;
            if (!xAxis || !yAxis) return;

            var liveDs = chart.getDatasetMeta(0);
            var botDs  = chart.getDatasetMeta(1);
            var livePoints = liveDs.data;
            var botPoints  = botDs.data;
            if (!livePoints.length || !botPoints.length) return;

            var lastLive = livePoints[livePoints.length - 1];
            var lastBot  = botPoints[botPoints.length - 1];
            var liveVal  = chart.data.datasets[0].data[chart.data.datasets[0].data.length - 1];
            var botVal   = chart.data.datasets[1].data[chart.data.datasets[1].data.length - 1];

            var liveLabel = (liveVal >= 0 ? '+' : '') + liveVal.toFixed(1) + '%';
            var botLabel  = (botVal  >= 0 ? '+' : '') + botVal.toFixed(1)  + '%';

            var cs = getComputedStyle(document.documentElement);
            var accentColor = cs.getPropertyValue('--studio-accent').trim();
            var paperColor  = cs.getPropertyValue('--studio-paper').trim();
            var inkDimColor = cs.getPropertyValue('--studio-ink-dim').trim();
            var fontFamily  = getComputedStyle(document.body).fontFamily;

            var lineHeight = 16;
            var labels = [
                { text: botLabel,  x: lastBot.x,  y: lastBot.y,  ink: accentColor },
                { text: liveLabel, x: lastLive.x, y: lastLive.y, ink: inkDimColor },
            ];
            // Upper label first; when the two sit closer than a line, each moves half the shortfall.
            labels.sort(function (p, q) { return p.y - q.y; });
            var gap = labels[1].y - labels[0].y;
            if (gap < lineHeight) {
                var shift = (lineHeight - gap) / 2;
                labels[0].y -= shift;
                labels[1].y += shift;
            }

            ctx.save();
            ctx.font = '500 11px ' + fontFamily;
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            labels.forEach(function (label) {
                var width = ctx.measureText(label.text).width + 8;
                ctx.fillStyle = paperColor;
                ctx.fillRect(label.x + 4, label.y - lineHeight / 2, width, lineHeight);
                ctx.fillStyle = label.ink;
                ctx.fillText(label.text, label.x + 8, label.y);
            });
            ctx.restore();
        },
    };

    function renderChart(payload) {
        var canvas = document.getElementById('returns-chart');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var cs = getComputedStyle(document.documentElement);
        var botColor  = cs.getPropertyValue('--studio-accent').trim();
        var liveColor = cs.getPropertyValue('--studio-ink-dim').trim();
        var posColor  = cs.getPropertyValue('--studio-pos').trim();
        var divFill   = hexToRgba(posColor, 0.14);

        var botSeries  = cumulative(payload.shadow_returns);
        var liveSeries = cumulative(payload.live_returns);

        var data = {
            labels: payload.dates,
            datasets: [
                {
                    label: 'If held',
                    data: liveSeries,
                    borderColor: liveColor,
                    backgroundColor: 'transparent',
                    fill: false,
                    tension: 0.15,
                    pointRadius: 0,
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                },
                {
                    label: 'Bot (shadow)',
                    data: botSeries,
                    borderColor: botColor,
                    backgroundColor: divFill,
                    fill: 0,
                    tension: 0.15,
                    pointRadius: 0,
                    borderWidth: 2,
                },
            ],
        };

        var inkColor    = cs.getPropertyValue('--studio-ink').trim();
        var ruleColor   = cs.getPropertyValue('--studio-rule').trim();
        var ruleHiColor = cs.getPropertyValue('--studio-rule-hi').trim();
        var paperHi     = cs.getPropertyValue('--studio-paper-hi').trim();
        var fontFamily  = getComputedStyle(document.body).fontFamily;
        var tickFont    = { family: fontFamily, size: 11 };
        var spansYears  = seriesSpansYears(payload.dates);

        // Reserve right-side padding so the endpoint labels fit inside the canvas
        var options = {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 300 },
            layout: { padding: { right: 64 } },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: paperHi,
                    titleColor: liveColor,
                    bodyColor: inkColor,
                    borderColor: ruleHiColor,
                    borderWidth: 1,
                    padding: 8,
                    cornerRadius: 8,
                    titleFont: tickFont,
                    bodyFont: tickFont,
                    callbacks: {
                        label: function (c) {
                            return c.dataset.label + ': ' + c.parsed.y.toFixed(2) + '%';
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: {
                        color: liveColor,
                        font: tickFont,
                        maxRotation: 0,
                        autoSkip: true,
                        maxTicksLimit: 12,
                        callback: function (value) {
                            return tickDayLabel(this.getLabelForValue(value), spansYears);
                        },
                    },
                    grid: { display: false },
                    border: { color: ruleHiColor },
                },
                y: {
                    ticks: {
                        color: liveColor,
                        font: tickFont,
                        callback: function (v) { return v.toFixed(1) + '%'; },
                    },
                    // The zero line is the one that matters: the held and the bot series both start there.
                    grid: {
                        color: function (c) { return c.tick && c.tick.value === 0 ? ruleHiColor : ruleColor; },
                    },
                    border: { display: false },
                },
            },
        };

        if (chartInstance) {
            chartInstance.data = data;
            chartInstance.options = options;
            chartInstance.update();
        } else {
            Chart.register(endpointLabelPlugin);
            chartInstance = new Chart(ctx, { type: 'line', data: data, options: options });
        }
    }

    function signColor(value, key) {
        if (typeof value !== 'number' || !isFinite(value)) return 'inherit';
        // max_drawdown is always negative — use neg colour; don't flip on sign
        if (key === 'max_drawdown') return value >= 0 ? 'inherit' : 'var(--studio-neg)';
        if (value > 0) return 'var(--studio-pos)';
        if (value < 0) return 'var(--studio-neg)';
        return 'inherit';
    }

    function renderMetrics(payload) {
        var tbody = document.getElementById('metrics-tbody');
        if (!tbody) return;
        var live   = payload.live_metrics   || {};
        var shadow = payload.shadow_metrics || {};

        // Derived Tier 1 fields — computed from the metric dicts before rendering.
        // Sign conventions (ux-design-deliverable.md §Change 2; fixture risk_adjusted_derived_fields.json):
        //   max_drawdown_delta = abs(held_mdd) - abs(bot_mdd)  (positive = bot had less drawdown)
        //   volatility_delta   = held_vol - bot_vol            (positive = bot is calmer)
        // The derived row shows the reduction on the bot side against a zero baseline,
        // so the delta column reflects the improvement directly.
        if (typeof live.max_drawdown === 'number' && typeof shadow.max_drawdown === 'number'
            && isFinite(live.max_drawdown) && isFinite(shadow.max_drawdown)) {
            shadow.max_drawdown_delta = Math.abs(live.max_drawdown) - Math.abs(shadow.max_drawdown);
            live.max_drawdown_delta = 0;  // baseline
        }
        if (typeof live.volatility === 'number' && typeof shadow.volatility === 'number'
            && isFinite(live.volatility) && isFinite(shadow.volatility)) {
            shadow.volatility_delta = live.volatility - shadow.volatility;
            live.volatility_delta = 0;  // baseline
        }

        var rows = METRIC_LABELS.map(function (spec) {
            var key = spec[0], label = spec[1], kind = spec[2], isPrimary = spec[3], invert = spec[4];

            var liveVal   = live[key];
            var shadowVal = shadow[key];
            // AC-7 (MAPERF-05): invert-polarity metrics (volatility — lower is
            // better) flip which sign of the raw (shadow - live) delta counts as
            // an improvement, mirroring index.js's Risk Profile panel (index.js:479
            // deltaGood = invert ? (delta <= 0) : (delta >= 0)). The displayed
            // VALUE + arrow (fmtDelta below) are unchanged by invert — only the
            // color decision flips.
            var dcHasBoth = typeof liveVal === 'number' && typeof shadowVal === 'number'
                            && isFinite(liveVal) && isFinite(shadowVal);
            // The drawdown legs are magnitudes: the delta is the gap between the magnitudes.
            var dcDelta = dcHasBoth
                ? (kind === 'pct_mag' ? Math.abs(shadowVal) - Math.abs(liveVal) : shadowVal - liveVal)
                : null;
            var dcGood = dcDelta === null ? null : (invert ? (dcDelta <= 0) : (dcDelta >= 0));
            var dc = dcDelta === null ? 'neutral' : (dcGood ? 'pos' : 'neg');
            var primaryAttr = isPrimary ? ' data-primary="true"' : '';
            // A reduction is measured against nothing: the if-held cell of a derived row holds no
            // value (a "0.00%" there is a baseline that was never measured).
            var derivedRow = /_delta$/.test(key);
            var deltaColor = dc === 'pos' ? 'var(--studio-pos)' : dc === 'neg' ? 'var(--studio-neg)' : 'inherit';

            // The delta cell is the figure alone: its sign, arrow and colour say which leg did better.
            return (
                '<tr data-testid="metric-row"' + primaryAttr + '>'
                + '<td>' + label + '</td>'
                + '<td style="text-align:right;color:' + signColor(liveVal, key)   + '">' + (derivedRow ? '—' : fmt(liveVal, kind)) + '</td>'
                + '<td style="text-align:right;color:' + signColor(shadowVal, key) + '">' + fmt(shadowVal, kind) + '</td>'
                + '<td class="delta" style="text-align:right;color:' + deltaColor + '">'
                +   fmtDelta(liveVal, shadowVal, kind, invert)
                + '</td>'
                + '</tr>'
            );
        });
        tbody.innerHTML = rows.join('');
    }

    function renderBanner(payload) {
        var banner = document.getElementById('insufficient-banner');
        if (!banner) return;
        if (_defaultBannerHtml === null) {
            _defaultBannerHtml = banner.innerHTML;
        }
        // AC-4 (F-023): a totally unrecognized symphony_id (stale/typo'd
        // picker value) must surface a DISTINCT message, never masquerade as
        // the generic "Insufficient history" empty state — both cases yield
        // observation_count 0, but only one means the id itself is wrong.
        var unrecognized = payload.symphony_id_recognized === false;
        banner.innerHTML = unrecognized
            ? '<strong>Strategy not recognized.</strong> This symphony ID isn\'t known — the picker may be showing a stale value.'
            : _defaultBannerHtml;
        banner.style.display = (unrecognized || payload.insufficient_history) ? '' : 'none';
    }

    // Set a headline stat value element's text + sign color. delta is in the
    // metric's native scale; suffix is appended verbatim (e.g. '%', '').
    // higherIsBetter governs which sign is rendered green. A value that rounds to zero at the
    // shown decimals prints without a sign and in neither the good nor the bad colour.
    function setHeadlineStat(id, value, decimals, suffix, higherIsBetter) {
        var el = document.getElementById(id);
        if (!el) return;
        if (value === null || typeof value !== 'number' || !isFinite(value)) {
            el.textContent = '--';
            el.style.color = 'inherit';
            return;
        }
        if (roundsToZero(value, decimals)) {
            el.textContent = fixedNoNegZero(value, decimals) + suffix;
            el.style.color = 'inherit';
            return;
        }
        el.textContent = (value >= 0 ? '+' : '') + value.toFixed(decimals) + suffix;
        var good = higherIsBetter ? value >= 0 : value <= 0;
        el.style.color = good ? 'var(--studio-pos)' : 'var(--studio-neg)';
    }

    // The tile names what its number is: the guard effect (Bot return minus If-held return,
    // each compounded over the window) for the portfolio, or for the picked symphony, over
    // the picked window; the two returns it is the gap between are stated beside it.
    // The windows the Dashboard's picker has a button for: the figure is the Dashboard's only there.
    var DASHBOARD_WINDOW_TOKENS = ['30d', '60d', '90d', '125d', 'ytd', '1y'];

    function renderGuardEffectCaption(liveTR, shadowTR) {
        var scope = segValue('scope-toggle');
        var windowToken = segValue('days-picker');
        var windowName = /^\d+d$/.test(windowToken) ? windowToken : String(windowToken || '').toUpperCase();
        var labelEl = document.getElementById('guard-alpha-label');
        if (labelEl) {
            labelEl.textContent = (scope === 'symphony' ? 'Guard effect on symphony' : 'Guard effect on portfolio')
                + (windowName ? ' · ' + windowName : '');
        }
        var basisEl = document.getElementById('guard-alpha-basis');
        if (basisEl) {
            basisEl.textContent = liveTR !== null && shadowTR !== null
                ? shadowTR.toFixed(2) + '% Bot vs ' + liveTR.toFixed(2) + '% If held, compounded.'
                    + (scope !== 'symphony' && DASHBOARD_WINDOW_TOKENS.indexOf(windowToken) !== -1
                        ? ' Same figure as the Dashboard at ' + windowName + '.' : '')
                : '';
        }
    }

    function renderHeadlineStats(payload) {
        var live   = payload.live_metrics   || {};
        var shadow = payload.shadow_metrics || {};
        // total_return from quantstats is fractional — multiply by 100 for pct display.
        var liveTR   = typeof live.total_return   === 'number' ? live.total_return   * 100 : null;
        var shadowTR = typeof shadow.total_return === 'number' ? shadow.total_return * 100 : null;
        var guardAlpha = (liveTR !== null && shadowTR !== null) ? (shadowTR - liveTR) : null;

        var gaEl = document.getElementById('guard-alpha-value');
        if (gaEl) {
            gaEl.textContent = guardAlpha !== null
                ? signedFixed(guardAlpha, 2) + '%'
                : '--';
            // A figure that rounds to zero is neither a gain nor a loss: no good/bad colour.
            gaEl.style.color = guardAlpha !== null && roundsToZero(guardAlpha, 2)
                ? 'inherit'
                : (guardAlpha !== null && guardAlpha >= 0 ? 'var(--studio-pos)' : 'var(--studio-neg)');
        }
        renderGuardEffectCaption(liveTR, shadowTR);

        // Phase 2 risk-adjusted headline deltas (ux-design-deliverable.md §Change 1).
        // sharpeDelta / sortinoDelta: bot − held; higher is better.
        // mddReduction: abs(held_mdd) − abs(bot_mdd) in fraction scale → a gap in %;
        //   positive means the bot suffered a shallower drawdown (capital preserved).
        var bothSharpe = typeof shadow.sharpe === 'number' && typeof live.sharpe === 'number';
        var sharpeDelta = bothSharpe ? shadow.sharpe - live.sharpe : null;

        var bothSortino = typeof shadow.sortino === 'number' && typeof live.sortino === 'number';
        var sortinoDelta = bothSortino ? shadow.sortino - live.sortino : null;

        var bothMdd = typeof shadow.max_drawdown === 'number' && typeof live.max_drawdown === 'number';
        var mddReduction = bothMdd
            ? (Math.abs(live.max_drawdown) - Math.abs(shadow.max_drawdown)) * 100  // fraction → %
            : null;

        setHeadlineStat('sharpe-delta-value', sharpeDelta, 2, '', true);
        setHeadlineStat('sortino-delta-value', sortinoDelta, 2, '', true);
        setHeadlineStat('mdd-reduction-value', mddReduction, 2, '%', true);
    }

    function renderObsCount(payload) {
        // Observation count moved out of the headline strip into a caption
        // (ux-design-deliverable.md §Change 1). Window days included for context.
        //
        // AC-4/AC-6 (DE-PERF-WINDOW-TRUTH-001): two fixes to this caption.
        //   AC-6 -- the old `if (typeof win === 'number')` gate was the SOLE
        //   condition for appending window context at all, so the YTD button's
        //   string token ("ytd") silently dropped it entirely (E-8 in the
        //   audit). The `else if (win != null)` branch below covers any
        //   non-number token generically -- not a hardcoded 'ytd' special-case,
        //   which would regress for the next string token added.
        //   AC-4 -- a rendered field with no render consumer does not satisfy
        //   "accurate and informational of restrictions to data length" (see
        //   DE-AUDIT-BL4-001). actualDays reads the AC-5 coverage_days/
        //   actual_days fields the route now emits and, when it falls short of
        //   the requested window, the caption states the honest shortfall
        //   instead of silently implying full coverage (E-9 in the audit).
        var caption = document.getElementById('obs-caption');
        if (caption) {
            var n = payload.observation_count;
            var win = payload.window_days;
            var actualDays = typeof payload.coverage_days === 'number' ? payload.coverage_days
                : (typeof payload.actual_days === 'number' ? payload.actual_days : null);
            var txt = String(n) + (n === 1 ? ' observation' : ' observations');
            if (typeof win === 'number') {
                if (actualDays !== null && actualDays < win) {
                    txt += ' · ' + win + 'd window requested · only ' + actualDays +
                        ' trading day' + (actualDays === 1 ? '' : 's') + ' available';
                    if (payload.date_range && payload.date_range.start) {
                        txt += ' (history begins ' + payload.date_range.start + ')';
                    }
                } else {
                    txt += ' · ' + win + 'd window';
                }
            } else if (win != null) {
                // A window token reads as its button does (60d, YTD, 1Y, 5Y), then the
                // first day actually held: the span of the observations counted above.
                var winLabel = typeof win === 'string'
                    ? (/^\d+d$/.test(win) ? win : win.toUpperCase())
                    : String(win);
                txt += ' · ' + winLabel + ' window';
                if (payload.date_range && payload.date_range.start) {
                    txt += ' · from ' + payload.date_range.start;
                }
            }
            caption.textContent = txt;
        }
        // Legacy element kept harmless if present in any cached template.
        var el = document.getElementById('observation-count');
        if (el) el.textContent = String(payload.observation_count);
    }

    function segValue(id) {
        var el = document.getElementById(id);
        if (!el) return '';
        var active = el.querySelector('button.active');
        return active ? active.getAttribute('data-value') : '';
    }

    // Each window button carries the token the route understands (30d 60d 90d 125d
    // ytd 1y 5y) and it is sent as it is: /api/performance resolves it to a calendar
    // cutoff with analytics._window_cutoff_date, the helper the dashboard's picker
    // uses, so a window covers the same span on every page.
    function resolveDays(raw) {
        return raw;
    }

    function currentParams() {
        var scope  = segValue('scope-toggle');
        var rawDays = segValue('days-picker');
        var days   = resolveDays(rawDays);
        var symId  = (document.getElementById('symphony-picker') || {}).value || '';
        var qs = 'scope=' + encodeURIComponent(scope) + '&days=' + encodeURIComponent(days);
        if (scope === 'symphony' && symId) {
            qs += '&symphony_id=' + encodeURIComponent(symId);
        }
        return { scope: scope, qs: qs, symphony_id: symId };
    }

    function showPickSymphonyState() {
        var chartBlock    = document.querySelector('[data-testid="perf-chart-block"]');
        var metricsSection = document.querySelector('[data-testid="metrics-table"]');
        var headlineStrip = document.querySelector('[data-testid="headline-strip"]');
        var emptyState    = document.getElementById('pick-symphony-state');
        if (!emptyState) {
            emptyState = document.createElement('div');
            emptyState.id = 'pick-symphony-state';
            // A panel like every other block on the page; layout.css owns its box.
            emptyState.className = 'hero-section hp-panel hp-empty';
            emptyState.innerHTML = '<div class="hp-empty-title">Pick a symphony</div>'
                + '<div class="hp-empty-body">Select a symphony from the dropdown above to view per-symphony performance.</div>';
            if (chartBlock) chartBlock.parentNode.insertBefore(emptyState, chartBlock);
        }
        emptyState.style.display = '';
        if (chartBlock)     chartBlock.style.display    = 'none';
        if (metricsSection) metricsSection.style.display = 'none';
        if (headlineStrip)  headlineStrip.style.display  = 'none';
    }

    function hidePickSymphonyState() {
        var emptyState    = document.getElementById('pick-symphony-state');
        var chartBlock    = document.querySelector('[data-testid="perf-chart-block"]');
        var metricsSection = document.querySelector('[data-testid="metrics-table"]');
        var headlineStrip = document.querySelector('[data-testid="headline-strip"]');
        if (emptyState)     emptyState.style.display     = 'none';
        if (chartBlock)     chartBlock.style.display     = '';
        if (metricsSection) metricsSection.style.display = '';
        if (headlineStrip)  headlineStrip.style.display  = '';
    }

    // The page's data age: the server's own "data as of" for the newest observation the response
    // used. After a refresh that brought no data, the cue says so and the last good stamp stays.
    // Written through textContent, so a stamp is never read as markup.
    var _lastDataStamp = null;

    function writeDataStamp(connectionLost) {
        var el = document.getElementById('performance-data-as-of');
        if (!el) return;
        var asOf = _lastDataStamp === null ? null : 'data as of ' + _lastDataStamp;
        if (connectionLost) {
            el.textContent = asOf === null ? 'connection lost' : 'connection lost \u00b7 ' + asOf;
        } else {
            el.textContent = asOf === null ? '' : asOf;
        }
    }

    // One panel's drawing never blocks another's: a renderer that throws (the chart library did not
    // load, a series the chart cannot draw) is reported in the console and the rest still render.
    function drawPanel(name, render, payload) {
        try {
            render(payload);
        } catch (err) {
            console.error('performance panel "' + name + '" could not draw', err);
        }
    }

    function refresh() {
        var params = currentParams();
        if (params.scope === 'symphony' && !params.symphony_id) {
            showPickSymphonyState();
            return;
        }
        hidePickSymphonyState();
        // The newest refresh issued is the only one drawn (a picker change is never overwritten by
        // a tick that was already in flight).
        var request = PSRequest.issue('performance');
        PSRequest.getJson('/api/performance?' + params.qs).then(function (res) {
            if (!PSRequest.isLatest('performance', request)) return;
            if (!res.ok) {
                // A lapsed session goes to the login page; any other answer is not data, so every
                // panel keeps what it shows and the stamp says the connection was lost.
                if (res.kind === 'unauthorized') { PSRequest.goLogin(); return; }
                console.error('performance refresh failed: ' + res.kind);
                writeDataStamp(true);
                return;
            }
            var payload = res.body;
            if (!payload || typeof payload !== 'object' || payload.status === 'error') {
                writeDataStamp(true);
                return;
            }
            drawPanel('observation count', renderObsCount, payload);
            drawPanel('headline stats', renderHeadlineStats, payload);
            drawPanel('banner', renderBanner, payload);
            drawPanel('chart', renderChart, payload);
            drawPanel('metrics', renderMetrics, payload);
            _lastDataStamp = typeof payload.data_as_of === 'string' ? payload.data_as_of : '--';
            writeDataStamp(false);
        });
    }

    // Display names by Composer id, from /api/performance/symphonies. The panels below
    // get the id from their routes and show the name; an id with no known name (the
    // list failed) shows as itself.
    var _symphonyNameById = {};

    function rememberSymphonyNames(symphonies) {
        symphonies.forEach(function (sym) { _symphonyNameById[sym.id] = sym.name; });
    }

    // First cell of a panel row: the display name, with the id in a title attribute.
    function symphonyLabelCell(symphonyId) {
        var td = document.createElement('td');
        var name = _symphonyNameById[symphonyId];
        td.textContent = name || symphonyId;
        if (name) td.setAttribute('title', symphonyId);
        return td;
    }

    function loadSymphonies() {
        return fetch('/api/performance/symphonies')
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                var symphonies = (body && body.symphonies) || [];
                rememberSymphonyNames(symphonies);
                var picker = document.getElementById('symphony-picker');
                if (!picker) return;
                picker.innerHTML = '';
                if (symphonies.length === 0) {
                    var opt = document.createElement('option');
                    opt.value = '';
                    opt.textContent = 'No symphonies in history yet';
                    opt.disabled = true;
                    opt.selected = true;
                    picker.appendChild(opt);
                } else {
                    symphonies.forEach(function (sym, idx) {
                        var opt = document.createElement('option');
                        // F-023 / DE-PERFVIEW-ID-MISMATCH: value must be the
                        // bot_state hash (sym.id), never the display name —
                        // the hash is what shadow_history's symphony_id
                        // column actually stores.
                        opt.value = sym.id;
                        opt.textContent = sym.name;
                        if (idx === 0) opt.selected = true;
                        picker.appendChild(opt);
                    });
                }
            })
            .catch(function (err) {
                console.error('failed to load symphonies', err);
            });
    }

    // Wire a segmented control to a callback.
    // stopPropagation prevents clicks bubbling to any ancestor nav link (B-11).
    function wireSegControl(id, onChange) {
        var el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('click', function (e) {
            e.stopPropagation();
            var btn = e.target.closest('button[data-value]');
            if (!btn) return;
            e.preventDefault();
            el.querySelectorAll('button').forEach(function (b) {
                b.classList.remove('active');
                b.setAttribute('aria-pressed', 'false');
            });
            btn.classList.add('active');
            btn.setAttribute('aria-pressed', 'true');
            if (onChange) onChange(btn.getAttribute('data-value'));
        });
    }

    function wireUI() {
        var symphonyWrapper = document.getElementById('symphony-picker-wrapper');
        var symphonyPicker  = document.getElementById('symphony-picker');

        function syncSymphonyVisibility() {
            if (!symphonyWrapper) return;
            symphonyWrapper.style.display = segValue('scope-toggle') === 'symphony' ? '' : 'none';
        }

        wireSegControl('scope-toggle', function (val) {
            syncSymphonyVisibility();
            if (val !== 'symphony') hidePickSymphonyState();
            refresh();
        });

        // B-12: window buttons must re-fetch on each click
        wireSegControl('days-picker', function () { refresh(); });

        if (symphonyPicker) {
            symphonyPicker.addEventListener('change', refresh);
        }

        syncSymphonyVisibility();
    }

    // --- Guard-Alpha stop-justification preconditions panel (Kaminski & Lo 2014) ---

    // Verdict -> chip modifier class (mirrors ai_advisor.js's sentiment-chip map).
    var PRECOND_VERDICT_CHIP_CLASS = {
        'SUFFICIENT_MET':    'precond-verdict-chip--sufficient-met',
        'SUFFICIENT_LIKELY': 'precond-verdict-chip--sufficient-likely',
        'NOT_MET':            'precond-verdict-chip--not-met',
        'NEGATIVE_EDGE':      'precond-verdict-chip--negative-edge',
        'INSUFFICIENT_DATA':  'precond-verdict-chip--insufficient-data',
    };

    function preconditionChipEl(verdict) {
        var chip = document.createElement('span');
        var cls = PRECOND_VERDICT_CHIP_CLASS[verdict] || 'precond-verdict-chip--insufficient-data';
        chip.className = 'precond-verdict-chip ' + cls;
        // A status dot (drawn by the class) and one word or two in sentence case: "Negative edge".
        var words = (verdict ? verdict.replace(/_/g, ' ') : 'INSUFFICIENT DATA').toLowerCase();
        chip.textContent = words.charAt(0).toUpperCase() + words.slice(1);
        return chip;
    }

    function preconditionNumCell(value, digits, prefix) {
        var td = document.createElement('td');
        td.style.textAlign = 'right';
        td.textContent = (value === null || value === undefined)
            ? '--'
            : (prefix || '') + Number(value).toFixed(digits);
        return td;
    }

    // Builds one <tr> via DOM APIs only (createElement/textContent) -- symphony
    // identifiers and sample data are external-origin (Composer / computed from
    // live series); never interpolated into innerHTML.
    function preconditionRowEl(symphonyId, sampleLabel, row) {
        var tr = document.createElement('tr');
        tr.setAttribute('data-testid', 'guard-alpha-preconditions-row');

        tr.appendChild(symphonyLabelCell(symphonyId));

        var sampleTd = document.createElement('td');
        sampleTd.textContent = sampleLabel;
        tr.appendChild(sampleTd);

        tr.appendChild(preconditionNumCell(row.rho, 3));
        tr.appendChild(preconditionNumCell(row.rho_ci, 3, '±'));
        tr.appendChild(preconditionNumCell(row.sharpe_daily, 3));

        var nTd = document.createElement('td');
        nTd.style.textAlign = 'right';
        nTd.textContent = (row.n_obs === null || row.n_obs === undefined) ? '--' : String(row.n_obs);
        tr.appendChild(nTd);

        var verdictTd = document.createElement('td');
        verdictTd.appendChild(preconditionChipEl(row.verdict));
        tr.appendChild(verdictTd);

        return tr;
    }

    // The route's uniform degraded-row contract means "replay" is always a full
    // object, never bare null: a cold or missing cache (cache-hit-only per the
    // operator ruling; never fetches to backfill) reads verdict=INSUFFICIENT_DATA with
    // n_obs=0. Its row renders as every other row does, with dashes; the panel note
    // says once why the replay samples are missing.
    function _replayIsCold(entry) {
        var replay = entry && entry.replay;
        return !!replay && replay.verdict === 'INSUFFICIENT_DATA' && replay.n_obs === 0;
    }

    // Panel-level empty state (AC-7 edge case: "all symphonies insufficient ->
    // panel-level empty state") -- usable means at least one sample for this
    // symphony cleared INSUFFICIENT_DATA.
    function _preconditionEntryIsUsable(entry) {
        if (!entry) return false;
        var replayOk = entry.replay && entry.replay.verdict && entry.replay.verdict !== 'INSUFFICIENT_DATA';
        var shadowOk = entry.shadow && entry.shadow.verdict && entry.shadow.verdict !== 'INSUFFICIENT_DATA';
        return !!(replayOk || shadowOk);
    }

    function renderGuardAlphaPreconditions(data) {
        var tbody = document.getElementById('guard-alpha-preconditions-tbody');
        var emptyState = document.getElementById('guard-alpha-preconditions-empty-state');
        var note = document.getElementById('guard-alpha-preconditions-note');
        if (!tbody || !emptyState) return;

        var symphonies = (data && data.symphonies) || {};
        var ids = Object.keys(symphonies);
        var anyUsable = ids.some(function (id) { return _preconditionEntryIsUsable(symphonies[id]); });

        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
        if (note) note.hidden = true;

        if (ids.length === 0 || !anyUsable) {
            emptyState.style.display = '';
            return;
        }

        emptyState.style.display = 'none';
        ids.forEach(function (id) {
            var entry = symphonies[id];
            // A symphony without a replay sample has no Replay row; the panel note below says
            // once how many lack one.
            if (entry.replay && !_replayIsCold(entry)) {
                tbody.appendChild(preconditionRowEl(id, 'Replay', entry.replay));
            }
            if (entry.shadow) tbody.appendChild(preconditionRowEl(id, 'Shadow', entry.shadow));
        });

        // One note for the panel, not a message per row: some replay samples are cold.
        var cold = ids.filter(function (id) { return _replayIsCold(symphonies[id]); }).length;
        if (note && cold > 0) {
            note.textContent = cold === ids.length
                ? 'Replay samples are unavailable until the next autotune run.'
                : 'Replay samples are unavailable for ' + cold + (cold === 1 ? ' symphony' : ' symphonies')
                    + ' until the next autotune run.';
            note.hidden = false;
        }
    }

    function fetchGuardAlphaPreconditions() {
        fetch('/api/guard-alpha-preconditions')
            .then(function (response) {
                if (!response.ok) return;
                return response.json();
            })
            .then(function (data) {
                if (!data) return;
                renderGuardAlphaPreconditions(data);
            })
            .catch(function () { /* silent -- panel keeps its last-known state */ });
    }

    // --- Exit-turnover panel (AC-8, exit-friction-realized-savings) ---

    function turnoverWindowCell(windowStats) {
        var td = document.createElement('td');
        if (!windowStats) {
            td.textContent = '--';
            return td;
        }
        // RULING C: every window cell names its actual retained-history days
        // (coverage_days) beside the count -- never let a nominal 365d column
        // header imply a full year the retention policy can't back.
        // The count reads first; the days it rests on sit beside it in the quieter ink (the cell's
        // text is still "<count> (<days>d retained)").
        td.appendChild(document.createTextNode(String(windowStats.exit_count)));
        // The phone layout reads the retained days from here ("15 in 30d") and keeps the longer
        // words in the cell for screen readers only.
        td.setAttribute('data-days', String(windowStats.coverage_days));
        var retained = document.createElement('span');
        retained.className = 'hp-muted';
        retained.textContent = ' (' + windowStats.coverage_days + 'd retained)';
        td.appendChild(retained);
        return td;
    }

    // Builds one <tr> via DOM APIs only (createElement/textContent) -- symphony
    // identifiers are external-origin (Composer); never interpolated into innerHTML.
    function exitTurnoverRowEl(symphonyId, turnover) {
        var tr = document.createElement('tr');
        tr.setAttribute('data-testid', 'exit-turnover-row');

        tr.appendChild(symphonyLabelCell(symphonyId));

        var windows = (turnover && turnover.windows) || {};
        tr.appendChild(turnoverWindowCell(windows['30']));
        tr.appendChild(turnoverWindowCell(windows['90']));
        tr.appendChild(turnoverWindowCell(windows['365']));

        var dragTd = document.createElement('td');
        var drag = turnover && turnover.est_annual_friction_drag_pct;
        dragTd.textContent = (drag === null || drag === undefined) ? '--' : Number(drag).toFixed(2) + '%';
        tr.appendChild(dragTd);

        return tr;
    }

    var EXIT_TURNOVER_LOST_TEXT = 'connection lost: showing the last rows received';
    var EXIT_TURNOVER_SERVER_ERROR_TEXT = 'The server could not provide exit turnover; rows without a value show dashes.';

    function renderExitTurnover(entries) {
        var tbody = document.getElementById('exit-turnover-tbody');
        var emptyState = document.getElementById('exit-turnover-empty-state');
        if (!tbody || !emptyState) return;

        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        if (entries.length === 0) {
            emptyState.style.display = '';
            return;
        }

        emptyState.style.display = 'none';
        entries.forEach(function (entry) {
            tbody.appendChild(exitTurnoverRowEl(entry.symphony_id, entry.turnover));
        });
    }

    // The last good turnover body of each symphony, kept so a request that fails keeps its row, and
    // the order the symphonies list gave them.
    var _lastGoodTurnover = {};
    var _turnoverOrder = [];

    function setExitTurnoverNote(text) {
        var panel = document.querySelector('[data-testid="exit-turnover-panel"]');
        if (!panel) return;
        var note = document.getElementById('exit-turnover-note');
        if (!note) {
            note = document.createElement('p');
            note.id = 'exit-turnover-note';
            note.setAttribute('data-testid', 'exit-turnover-note');
            note.setAttribute('role', 'status');
            note.className = 'metrics-caption';
            panel.appendChild(note);
        }
        note.textContent = text;
    }

    function drawExitTurnoverFromLastGood() {
        // A symphony whose read failed and has no earlier good row gets a row of dashes.
        renderExitTurnover(_turnoverOrder.map(function (id) {
            return {
                symphony_id: id,
                turnover: Object.prototype.hasOwnProperty.call(_lastGoodTurnover, id) ? _lastGoodTurnover[id] : null
            };
        }));
    }

    // AC-8 fetch chain: sources the symphony-id list from
    // /api/performance/symphonies (the same source loadSymphonies() uses for
    // the picker), then fetches /api/exit-turnover per symphony. A request
    // that does not bring data never empties the panel: that symphony keeps
    // its last good row, a session that lapsed goes to the login page, and
    // when nothing arrived the panel says the connection was lost.
    function fetchExitTurnover() {
        var request = PSRequest.issue('exit-turnover');
        return PSRequest.getJson('/api/performance/symphonies').then(function (listing) {
            if (!PSRequest.isLatest('exit-turnover', request)) return;
            if (!listing.ok) {
                if (listing.kind === 'unauthorized') { PSRequest.goLogin(); return; }
                setExitTurnoverNote(listing.kind === 'network'
                    ? EXIT_TURNOVER_LOST_TEXT : EXIT_TURNOVER_SERVER_ERROR_TEXT);
                return;
            }
            var symphonies = (listing.body && listing.body.symphonies) || [];
            rememberSymphonyNames(symphonies);
            _turnoverOrder = symphonies.map(function (sym) { return sym.id; });
            if (symphonies.length === 0) {
                _lastGoodTurnover = {};
                renderExitTurnover([]);
                setExitTurnoverNote('');
                return;
            }
            return Promise.all(symphonies.map(function (sym) {
                return PSRequest.getJson('/api/exit-turnover?symphony_id=' + encodeURIComponent(sym.id))
                    .then(function (res) {
                        if (res.ok && res.body && typeof res.body === 'object') {
                            _lastGoodTurnover[sym.id] = res.body;
                            return 'ok';
                        }
                        return res.kind === 'unauthorized' || res.kind === 'network' ? res.kind : 'failed';
                    });
            })).then(function (outcomes) {
                if (!PSRequest.isLatest('exit-turnover', request)) return;
                if (outcomes.indexOf('unauthorized') !== -1) { PSRequest.goLogin(); return; }
                drawExitTurnoverFromLastGood();
                var failedIds = [];
                outcomes.forEach(function (o, i) { if (o !== 'ok') failedIds.push(symphonies[i].id); });
                var unanswered = outcomes.every(function (o) { return o === 'ok' || o === 'network'; });
                var keepsEarlier = failedIds.every(function (id) {
                    return Object.prototype.hasOwnProperty.call(_lastGoodTurnover, id);
                });
                if (failedIds.length === 0) setExitTurnoverNote('');
                else if (failedIds.length === outcomes.length) {
                    setExitTurnoverNote(unanswered ? EXIT_TURNOVER_LOST_TEXT : EXIT_TURNOVER_SERVER_ERROR_TEXT);
                } else {
                    setExitTurnoverNote('Could not refresh ' + failedIds.length + ' of ' + outcomes.length +
                        (keepsEarlier ? ' rows; they keep their earlier values.'
                            : ' rows; rows without a value show dashes.'));
                }
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireUI();
        // The preconditions panel labels its rows with the names loadSymphonies()
        // fetches, so it asks for its rows once that has settled (success or failure).
        loadSymphonies().then(function () {
            refresh();
            fetchGuardAlphaPreconditions();
        });
        fetchExitTurnover();

        // Post-mortems land once a day; the preconditions and exit-turnover
        // panels change at most once per autotune/replay run and once per
        // engine cycle -- folding both into the existing 60s poll (no new
        // timer) stays above the 15s floor.
        setInterval(function () {
            refresh();
            fetchGuardAlphaPreconditions();
            fetchExitTurnover();
        }, 60000);
    });
})();
