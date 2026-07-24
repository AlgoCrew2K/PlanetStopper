/**
 * performance.js — Performance tab client logic.
 *
 * Read-only operator surface:
 *   - Fetches /api/performance and /api/performance/symphonies.
 *   - Renders cumulative-return curves (live vs Planet Stopper-exited) via Chart.js.
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
    // kind 'pp' = fraction-scale delta rendered as percentage points (e.g. +1.70pp).
    // invert (AC-7 / MAPERF-05, optional — defaults falsy when omitted): true for
    // lower-is-better metrics (e.g. volatility), mirroring index.js's Risk Profile
    // panel invertDelta flag (index.js:466-479) — flips which sign of the raw
    // (shadow - live) delta renders as the "good" color. The displayed VALUE and
    // arrow direction are UNCHANGED by invert — only the color decision flips.
    // The trailing entries (upside_capture / downside_capture) are Tier 2 benchmark
    // placeholders: they have no data feed yet, so they render as 'not yet available'
    // (data-unavail) — never a fabricated number.
    var METRIC_LABELS = [
        // --- risk-adjusted: primary (bold) ---
        ['sharpe',              'Sharpe',                    'num',      true],
        ['sortino',             'Sortino',                   'num',      false],
        ['max_drawdown',        'Max drawdown',              'pct_frac', true],
        ['max_drawdown_delta',  'Max DD reduction',          'pp',       false],  // derived
        ['calmar',              'Calmar',                    'num',      false],
        // --- volatility ---
        ['volatility',          'Annualized volatility',     'pct_frac', false, true],  // Tier 1, invert (lower is better)
        ['volatility_delta',    'Volatility reduction',      'pp',       false],  // Tier 1, derived
        // --- return ---
        ['total_return',        'Total return',              'pct_frac', true],
        ['annualized_return',   'Annualized return (CAGR)',  'pct_frac', false],
        ['win_rate',            'Win rate',                  'frac',     false],
        // --- roadmap placeholders (Tier 2, no benchmark feed → 'not yet available') ---
        ['upside_capture',      'Upside capture vs SPY',     'pct',      false],
        ['downside_capture',    'Downside capture vs SPY',   'pct',      false],
    ];

    // Tier 2 benchmark metrics: no SPY/TQQQ data feed exists yet, so these always
    // render as unavailable placeholders (never a fabricated value).
    var TIER2_PLACEHOLDER_KEYS = ['upside_capture', 'downside_capture'];

    function fmt(value, kind) {
        if (value === null || value === undefined) return '—';
        if (typeof value !== 'number' || !isFinite(value)) return '—';
        if (Math.abs(value) > 1000) return '—';
        // pct_frac: quantstats returns fractions; multiply by 100 for display
        if (kind === 'pct_frac') return (value * 100).toFixed(2) + '%';
        if (kind === 'frac') return (value * 100).toFixed(2) + '%';
        // pp: fraction-scale delta shown as percentage points (e.g. 0.017 → +1.70pp)
        if (kind === 'pp') return (value >= 0 ? '+' : '') + (value * 100).toFixed(2) + 'pp';
        // pct: already a percentage value (Tier 2 capture %) — rare, kept for completeness
        if (kind === 'pct') return value.toFixed(1) + '%';
        return value.toFixed(3);
    }

    function fmtDelta(live, shadow, kind) {
        if (live === null || shadow === null || live === undefined || shadow === undefined) return '—';
        if (typeof live !== 'number' || typeof shadow !== 'number') return '—';
        if (!isFinite(live) || !isFinite(shadow)) return '—';
        if (Math.abs(live) > 1000 || Math.abs(shadow) > 1000) return '—';
        var delta = shadow - live;
        var formatted = fmt(delta, kind);
        if (delta > 0) return '↑ +' + formatted;
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

    // Chart.js plugin: draws filled pill (bot) and hollow rect (live) endpoint
    // callout labels at the right edge of the chart (V-15).
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
            var accentColor = cs.getPropertyValue('--studio-accent').trim() || '#1f7a4d';
            var paperColor  = cs.getPropertyValue('--studio-paper').trim()  || '#ffffff';
            var inkDimColor = cs.getPropertyValue('--studio-ink-dim').trim() || '#64748b';
            var ruleColor   = cs.getPropertyValue('--studio-rule').trim()    || '#e2e8f0';

            ctx.save();
            ctx.font = '700 11px Manrope, ui-sans-serif, system-ui, sans-serif';

            var pad = 6;
            var h = 18;

            // Bot — filled pill
            var bw = ctx.measureText(botLabel).width + pad * 2;
            var bx = lastBot.x + 6;
            var by = lastBot.y - h / 2;
            ctx.fillStyle = accentColor;
            ctx.beginPath();
            if (ctx.roundRect) {
                ctx.roundRect(bx, by, bw, h, 3);
            } else {
                ctx.rect(bx, by, bw, h);
            }
            ctx.fill();
            ctx.fillStyle = '#ffffff';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(botLabel, bx + pad, by + h / 2);

            // Live — hollow rect (filled with paper colour + border)
            var lw = ctx.measureText(liveLabel).width + pad * 2;
            var lx = lastLive.x + 6;
            var ly = lastLive.y - h / 2;
            ctx.fillStyle = paperColor;
            ctx.strokeStyle = ruleColor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            if (ctx.roundRect) {
                ctx.roundRect(lx, ly, lw, h, 3);
            } else {
                ctx.rect(lx, ly, lw, h);
            }
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = inkDimColor;
            ctx.fillText(liveLabel, lx + pad, ly + h / 2);

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
        var posColor  = cs.getPropertyValue('--studio-pos').trim() || '#1f7a4d';
        var divFill   = hexToRgba(posColor, 0.14);

        var botSeries  = cumulative(payload.shadow_returns);
        var liveSeries = cumulative(payload.live_returns);

        var data = {
            labels: payload.dates,
            datasets: [
                {
                    label: 'Live (if held)',
                    data: liveSeries,
                    borderColor: liveColor,
                    backgroundColor: 'transparent',
                    fill: false,
                    tension: 0.15,
                    pointRadius: 0,
                    borderWidth: 2,
                    borderDash: [4, 4],
                },
                {
                    label: 'Planet Stopper-Exited (shadow)',
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

        var inkFaint  = cs.getPropertyValue('--studio-ink-faint').trim();
        var ruleColor = cs.getPropertyValue('--studio-rule').trim();

        // Reserve right-side padding so endpoint labels fit inside the canvas
        var options = {
            responsive: true,
            maintainAspectRatio: false,
            layout: { padding: { right: 80 } },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (c) {
                            return c.dataset.label + ': ' + c.parsed.y.toFixed(2) + '%';
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: inkFaint, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
                    grid: { color: ruleColor },
                },
                y: {
                    ticks: {
                        color: inkFaint,
                        callback: function (v) { return v.toFixed(1) + '%'; },
                    },
                    grid: { color: ruleColor },
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

    // Tier 2 placeholder row: the operator sees the roadmap metric, but the value
    // columns render an explicit 'not yet available' dash — never a fabricated number.
    // data-unavail='true' marks the row distinct from live data rows.
    function placeholderRow(label) {
        var unavail = '<span class="metric-unavail" title="Requires SPY/TQQQ benchmark data feed — not yet available">—</span>';
        return (
            '<tr data-testid="metric-row" data-unavail="true">'
            + '<td>' + label + '</td>'
            + '<td style="text-align:right;color:var(--studio-ink-faint);">' + unavail + '</td>'
            + '<td style="text-align:right;color:var(--studio-ink-faint);">' + unavail + '</td>'
            + '<td class="delta" style="text-align:right;"></td>'
            + '</tr>'
        );
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

            // Tier 2 benchmark metrics: no data feed → always a placeholder row.
            if (TIER2_PLACEHOLDER_KEYS.indexOf(key) !== -1) {
                return placeholderRow(label);
            }

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
            var dcDelta = dcHasBoth ? (shadowVal - liveVal) : null;
            var dcGood = dcDelta === null ? null : (invert ? (dcDelta <= 0) : (dcDelta >= 0));
            var dc = dcDelta === null ? 'neutral' : (dcGood ? 'pos' : 'neg');
            var primaryAttr = isPrimary ? ' data-primary="true"' : '';
            var deltaColor = dc === 'pos' ? 'var(--studio-pos)' : dc === 'neg' ? 'var(--studio-neg)' : 'inherit';
            var barFill = key === 'max_drawdown' ? 'var(--studio-neg)' : 'var(--studio-accent)';

            // Only render bar when both values are present and finite
            var hasDelta = typeof liveVal === 'number' && typeof shadowVal === 'number'
                           && isFinite(liveVal) && isFinite(shadowVal);
            var delta = hasDelta ? shadowVal - liveVal : 0;
            var barW = hasDelta ? Math.min(Math.abs(delta) * 10, 100).toFixed(1) : '0';
            var metricBarSvg = '<svg data-testid="metric-bar" class="metric-bar" viewBox="0 0 100 6" '
                + 'preserveAspectRatio="none" width="80" height="6">'
                + '<rect x="0" y="1" width="' + barW + '" height="4" rx="2" fill="' + barFill + '"></rect>'
                + '</svg>';

            return (
                '<tr data-testid="metric-row"' + primaryAttr + '>'
                + '<td>' + label + '</td>'
                + '<td style="text-align:right;color:' + signColor(liveVal, key)   + '">' + fmt(liveVal, kind)   + '</td>'
                + '<td style="text-align:right;color:' + signColor(shadowVal, key) + '">' + fmt(shadowVal, kind) + '</td>'
                + '<td class="delta" style="text-align:right;color:' + deltaColor + '">'
                +   metricBarSvg + fmtDelta(liveVal, shadowVal, kind)
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
    // metric's native scale; suffix is appended verbatim (e.g. '%', 'pp', '').
    // higherIsBetter governs which sign is rendered green.
    function setHeadlineStat(id, value, decimals, suffix, higherIsBetter) {
        var el = document.getElementById(id);
        if (!el) return;
        if (value === null || typeof value !== 'number' || !isFinite(value)) {
            el.textContent = '--';
            el.style.color = 'inherit';
            return;
        }
        el.textContent = (value >= 0 ? '+' : '') + value.toFixed(decimals) + suffix;
        var good = higherIsBetter ? value >= 0 : value <= 0;
        el.style.color = good ? 'var(--studio-pos)' : 'var(--studio-neg)';
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
                ? (guardAlpha >= 0 ? '+' : '') + guardAlpha.toFixed(2) + '%'
                : '--';
            gaEl.style.color = guardAlpha !== null && guardAlpha >= 0
                ? 'var(--studio-pos)' : 'var(--studio-neg)';
        }

        // Phase 2 risk-adjusted headline deltas (ux-design-deliverable.md §Change 1).
        // sharpeDelta / sortinoDelta: bot − held; higher is better.
        // mddReduction: abs(held_mdd) − abs(bot_mdd) in fraction scale → percentage points;
        //   positive means the bot suffered a shallower drawdown (capital preserved).
        var bothSharpe = typeof shadow.sharpe === 'number' && typeof live.sharpe === 'number';
        var sharpeDelta = bothSharpe ? shadow.sharpe - live.sharpe : null;

        var bothSortino = typeof shadow.sortino === 'number' && typeof live.sortino === 'number';
        var sortinoDelta = bothSortino ? shadow.sortino - live.sortino : null;

        var bothMdd = typeof shadow.max_drawdown === 'number' && typeof live.max_drawdown === 'number';
        var mddReduction = bothMdd
            ? (Math.abs(live.max_drawdown) - Math.abs(shadow.max_drawdown)) * 100  // fraction → pp
            : null;

        setHeadlineStat('sharpe-delta-value', sharpeDelta, 2, '', true);
        setHeadlineStat('sortino-delta-value', sortinoDelta, 2, '', true);
        setHeadlineStat('mdd-reduction-value', mddReduction, 2, 'pp', true);
    }

    function renderObsCount(payload) {
        // Observation count moved out of the headline strip into a caption
        // (ux-design-deliverable.md §Change 1). Window days included for context.
        var caption = document.getElementById('obs-caption');
        if (caption) {
            var n = payload.observation_count;
            var win = payload.window_days;
            var txt = String(n) + (n === 1 ? ' observation' : ' observations');
            if (typeof win === 'number') txt += ' · ' + win + 'd window';
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

    // AC-5 (MAPERF-03, cross-plan correction): YTD sends the LITERAL string
    // 'ytd' — not a computed calendar-days-since-Jan-1 count. The six other
    // Performance-tab buttons (30/60/90/125/252/1260) stay deliberate
    // TRADING-day counts by design; only YTD's contract changes. The server
    // (/api/performance) resolves the literal 'ytd' token to a Jan-1 CALENDAR
    // cutoff via analytics._window_cutoff_date — the same helper the dashboard
    // hero-chart/strip picker already uses — instead of the old approximate
    // calendar-count-fed-into-a-trading-day-slice (which over-fetched past
    // Jan 1 whenever the ratio of calendar-to-trading days diverged).
    function resolveDays(raw) {
        if (raw === 'ytd') return 'ytd';
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
            emptyState.style.cssText = 'padding:3rem 1.5rem;text-align:center;color:var(--studio-ink-dim);font-size:0.9375rem;';
            emptyState.innerHTML = '<div style="font-size:2rem;margin-bottom:0.75rem;">&#9835;</div>'
                + '<strong>Pick a symphony</strong>'
                + '<p style="margin-top:0.5rem;font-size:0.8125rem;">Select a symphony from the dropdown above to view per-symphony performance.</p>';
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

    function refresh() {
        var params = currentParams();
        if (params.scope === 'symphony' && !params.symphony_id) {
            showPickSymphonyState();
            return;
        }
        hidePickSymphonyState();
        fetch('/api/performance?' + params.qs)
            .then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function (payload) {
                renderObsCount(payload);
                renderHeadlineStats(payload);
                renderBanner(payload);
                renderChart(payload);
                renderMetrics(payload);
            })
            .catch(function (err) {
                console.error('performance refresh failed', err);
            });
    }

    function loadSymphonies() {
        return fetch('/api/performance/symphonies')
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                var picker = document.getElementById('symphony-picker');
                if (!picker) return;
                var symphonies = (body && body.symphonies) || [];
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
        chip.textContent = verdict ? verdict.replace(/_/g, ' ') : 'INSUFFICIENT DATA';
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

        var nameTd = document.createElement('td');
        nameTd.textContent = symphonyId;
        tr.appendChild(nameTd);

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

    // The route's uniform degraded-row contract means "replay" is always a
    // full object, never bare null -- a cold/missing cache (cache-hit-only
    // per the operator ruling; never fetches to backfill) shows as
    // verdict=INSUFFICIENT_DATA + n_obs=0. Swap in this friendlier copy for
    // that specific case instead of a row of dashes.
    var PRECOND_REPLAY_UNAVAILABLE_MESSAGE =
        'replay sample unavailable — populates after the next autotune run';

    function preconditionUnavailableRowEl(symphonyId, sampleLabel) {
        var tr = document.createElement('tr');
        tr.setAttribute('data-testid', 'guard-alpha-preconditions-row');
        tr.className = 'precond-row-unavailable';

        var nameTd = document.createElement('td');
        nameTd.textContent = symphonyId;
        tr.appendChild(nameTd);

        var sampleTd = document.createElement('td');
        sampleTd.textContent = sampleLabel;
        tr.appendChild(sampleTd);

        var msgTd = document.createElement('td');
        msgTd.colSpan = 5;
        msgTd.textContent = PRECOND_REPLAY_UNAVAILABLE_MESSAGE;
        tr.appendChild(msgTd);

        return tr;
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
        if (!tbody || !emptyState) return;

        var symphonies = (data && data.symphonies) || {};
        var ids = Object.keys(symphonies);
        var anyUsable = ids.some(function (id) { return _preconditionEntryIsUsable(symphonies[id]); });

        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        if (ids.length === 0 || !anyUsable) {
            emptyState.style.display = '';
            return;
        }

        emptyState.style.display = 'none';
        ids.forEach(function (id) {
            var entry = symphonies[id];
            var replay = entry.replay;
            var replayIsColdCache = replay && replay.verdict === 'INSUFFICIENT_DATA' && replay.n_obs === 0;
            if (replayIsColdCache) {
                tbody.appendChild(preconditionUnavailableRowEl(id, 'Replay'));
            } else if (replay) {
                tbody.appendChild(preconditionRowEl(id, 'Replay', replay));
            }
            if (entry.shadow) tbody.appendChild(preconditionRowEl(id, 'Shadow', entry.shadow));
        });
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
        td.style.textAlign = 'right';
        if (!windowStats) {
            td.textContent = '--';
            return td;
        }
        // RULING C: every window cell names its actual retained-history days
        // (coverage_days) beside the count -- never let a nominal 365d column
        // header imply a full year the retention policy can't back.
        td.textContent = windowStats.exit_count + ' (' + windowStats.coverage_days + 'd retained)';
        return td;
    }

    // Builds one <tr> via DOM APIs only (createElement/textContent) -- symphony
    // identifiers are external-origin (Composer); never interpolated into innerHTML.
    function exitTurnoverRowEl(symphonyId, turnover) {
        var tr = document.createElement('tr');
        tr.setAttribute('data-testid', 'exit-turnover-row');

        var nameTd = document.createElement('td');
        nameTd.textContent = symphonyId;
        tr.appendChild(nameTd);

        var windows = (turnover && turnover.windows) || {};
        tr.appendChild(turnoverWindowCell(windows['30']));
        tr.appendChild(turnoverWindowCell(windows['90']));
        tr.appendChild(turnoverWindowCell(windows['365']));

        var dragTd = document.createElement('td');
        dragTd.style.textAlign = 'right';
        var drag = turnover && turnover.est_annual_friction_drag_pct;
        dragTd.textContent = (drag === null || drag === undefined) ? '--' : Number(drag).toFixed(3) + '%';
        tr.appendChild(dragTd);

        return tr;
    }

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

    // AC-8 fetch chain: sources the symphony-id list from
    // /api/performance/symphonies (the same source loadSymphonies() uses for
    // the picker), then fetches /api/exit-turnover per symphony. Explicit
    // 401 handling (never a bare .ok check) so an unauthenticated poll
    // degrades to the panel's empty-state instead of throwing on a non-JSON
    // response body.
    function fetchExitTurnover() {
        fetch('/api/performance/symphonies')
            .then(function (response) {
                if (response.status === 401) return null;
                if (!response.ok) return null;
                return response.json();
            })
            .then(function (body) {
                var symphonies = (body && body.symphonies) || [];
                if (symphonies.length === 0) {
                    renderExitTurnover([]);
                    return;
                }
                return Promise.all(
                    symphonies.map(function (sym) {
                        return fetch('/api/exit-turnover?symphony_id=' + encodeURIComponent(sym.id))
                            .then(function (response) {
                                if (response.status === 401) return null;
                                if (!response.ok) return null;
                                return response.json();
                            })
                            .then(function (turnover) {
                                return { symphony_id: sym.id, turnover: turnover };
                            })
                            .catch(function () {
                                return { symphony_id: sym.id, turnover: null };
                            });
                    })
                ).then(function (entries) {
                    renderExitTurnover(entries.filter(function (e) { return e.turnover; }));
                });
            })
            .catch(function (err) {
                console.error('fetchExitTurnover failed', err);
                renderExitTurnover([]);
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireUI();
        loadSymphonies().then(refresh);
        fetchGuardAlphaPreconditions();
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
