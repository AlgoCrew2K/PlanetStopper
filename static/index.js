(function () {
    'use strict';

    var _cumChart = null;
    var _sparks = {};
    var _heroFull = { dates: [], bot: [], held: [] };
    var _heroWindow = 30;
    var _botState = {};
    var _symIds = [];
    var _intradayChart = null;
    var POLL_INTERVAL_MS = 30000;

    /** CSRF token fetched from GET /api/csrf-token on page load (AC-1). */
    var _csrfToken = null;
    document.addEventListener('DOMContentLoaded', function () {
        fetch('/api/csrf-token')
            .then(function (r) { return r.json(); })
            .then(function (b) { _csrfToken = b.csrf_token || null; })
            .catch(function () { /* csrf token unavailable — POSTs will 403 */ });
    });

    function cs(varName) {
        return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    }

    // Engine uses ±999 as "not-set" sentinel. Treat any abs(v) >= 900 as unset.
    function isSentinel(v) {
        return typeof v === 'number' && Math.abs(v) >= 900;
    }

    function sentinelToNull(v) {
        return (typeof v === 'number' && !isSentinel(v)) ? v : null;
    }

    function fmtPct(value, decimals) {
        if (value == null || isNaN(value)) return '--';
        decimals = decimals != null ? decimals : 2;
        var numFormat = document.documentElement.dataset.numFormat || 'full';
        if (numFormat === 'compact' && Math.abs(value) >= 1000) {
            return (value / 1000).toFixed(1) + 'k%';
        }
        return (value >= 0 ? '+' : '') + value.toFixed(decimals) + '%';
    }

    // ---------------------------------------------------------------------------
    // Hero chart (bot vs held cumulative returns)
    // ---------------------------------------------------------------------------

    function applyHeroWindow(days) {
        if (!_cumChart) return;
        var dates = _heroFull.dates;
        var bot = _heroFull.bot;
        var held = _heroFull.held;
        var sliced;
        if (days === 'ytd') {
            var jan1 = new Date(new Date().getFullYear(), 0, 1).toISOString().slice(0, 10);
            var idx = 0;
            while (idx < dates.length && dates[idx] < jan1) idx++;
            sliced = { dates: dates.slice(idx), bot: bot.slice(idx), held: held.slice(idx) };
        } else {
            sliced = { dates: dates.slice(-days), bot: bot.slice(-days), held: held.slice(-days) };
        }
        _cumChart.data.labels = sliced.dates;
        _cumChart.data.datasets[0].data = sliced.bot;
        _cumChart.data.datasets[1].data = sliced.held;
        _cumChart.update('none');
    }

    function renderHeroChart(meta) {
        var portfolio = (meta || {}).portfolio || {};
        var hist_dates = portfolio.hist_dates || [];
        var hist_bot = portfolio.hist_bot || [];
        var hist_held = portfolio.hist_held || [];

        _heroFull.dates = hist_dates;
        _heroFull.bot = hist_bot;
        _heroFull.held = hist_held;

        var canvas = document.getElementById('cum-chart');
        if (!canvas) return;

        if (_cumChart) {
            applyHeroWindow(_heroWindow);
            return;
        }

        if (!hist_dates.length) return;

        _cumChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: hist_dates,
                datasets: [
                    {
                        label: 'Planet Stopper',
                        data: hist_bot,
                        borderColor: cs('--studio-accent'),
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.3,
                        fill: false
                    },
                    {
                        label: 'If held',
                        data: hist_held,
                        borderColor: cs('--studio-ink-dim'),
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        tension: 0.3,
                        fill: false,
                        borderDash: [4, 4]
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { x: { display: false }, y: { display: false } }
            }
        });
        applyHeroWindow(_heroWindow);
    }

    // ---------------------------------------------------------------------------
    // Guard alpha headline
    // ---------------------------------------------------------------------------

    function renderGuardAlpha(data) {
        var ps = (data || {}).portfolio_strip || {};
        var cr_obj = ps.cumulative_return || {};
        var cr = typeof cr_obj.dry_run === 'number' ? cr_obj.dry_run : 0;
        var crHeld = typeof cr_obj.if_held === 'number' ? cr_obj.if_held : 0;
        var guard_alpha = cr - crHeld;

        var el = document.getElementById('guard-alpha-headline');
        if (el) {
            el.textContent = fmtPct(guard_alpha);
            el.style.color = guard_alpha >= 0 ? cs('--studio-pos') : cs('--studio-neg');
        }
    }

    // ---------------------------------------------------------------------------
    // ---------------------------------------------------------------------------
    // Exit-event line plugin (vertical breakline + badge for TP/Stop/VWAP exits)
    // ---------------------------------------------------------------------------

    function exitReasonLabel(reason) {
        if (!reason) return 'Exit';
        var r = reason.toLowerCase();
        if (r.indexOf('take') !== -1 || r.indexOf('tp') !== -1) return 'TP';
        if (r.indexOf('vwap') !== -1) return 'VWAP';
        return 'Stop';
    }

    function exitReasonColor(reason) {
        if (!reason) return cs('--studio-neg');
        var r = reason.toLowerCase();
        if (r.indexOf('take') !== -1 || r.indexOf('tp') !== -1) return cs('--studio-pos');
        if (r.indexOf('vwap') !== -1) return cs('--studio-plum');
        return cs('--studio-neg');
    }

    // Returns a Chart.js v4 inline plugin that draws a vertical hairline + badge.
    // exitIdx: index into the labels array; label: badge text; color: hex/rgb string
    // isMini: true for sparkline (smaller badge), false for detail chart
    function makeExitLinePlugin(exitIdx, label, color, isMini) {
        return {
            id: 'exitLine',
            afterDraw: function (chart) {
                if (exitIdx < 0 || exitIdx >= chart.data.labels.length) return;
                var ctx = chart.ctx;
                var xScale = chart.scales.x;
                var yScale = chart.scales.y;
                if (!xScale || !yScale) return;
                var x = xScale.getPixelForValue(exitIdx);
                var top = yScale.top;
                var bottom = yScale.bottom;

                ctx.save();
                ctx.strokeStyle = color;
                ctx.lineWidth = isMini ? 1 : 1.5;
                ctx.setLineDash([3, 3]);
                ctx.beginPath();
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();

                var pad = 4;
                var fontSize = isMini ? 8 : 10;
                ctx.font = '700 ' + fontSize + 'px system-ui, sans-serif';
                var tw = ctx.measureText(label).width;
                var bw = tw + pad * 2;
                var bh = fontSize + pad * 2;
                // Badge sits above the chart (top + 1px gap)
                var bx = Math.min(x - bw / 2, chart.chartArea.right - bw - 2);
                bx = Math.max(bx, chart.chartArea.left + 2);
                var by = top + 1;
                var r = 2;
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.moveTo(bx + r, by);
                ctx.lineTo(bx + bw - r, by);
                ctx.arcTo(bx + bw, by, bx + bw, by + r, r);
                ctx.lineTo(bx + bw, by + bh - r);
                ctx.arcTo(bx + bw, by + bh, bx + bw - r, by + bh, r);
                ctx.lineTo(bx + r, by + bh);
                ctx.arcTo(bx, by + bh, bx, by + bh - r, r);
                ctx.lineTo(bx, by + r);
                ctx.arcTo(bx, by, bx + r, by, r);
                ctx.closePath();
                ctx.fill();
                ctx.fillStyle = '#ffffff';
                ctx.textBaseline = 'middle';
                ctx.fillText(label, bx + pad, by + bh / 2);
                // Tick line from badge down to hairline
                ctx.strokeStyle = color;
                ctx.lineWidth = 1;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(x, by + bh);
                ctx.lineTo(x, by + bh + 4);
                ctx.stroke();
                ctx.restore();
            }
        };
    }

    // ---------------------------------------------------------------------------
    // Per-symphony sparklines
    // ---------------------------------------------------------------------------

    function renderSparkline(symId, chartData, sym) {
        var canvases = document.querySelectorAll('[data-testid="card-spark"][data-sym-id="' + symId + '"]');
        canvases.forEach(function (canvas) {
            var data = chartData.data || [];
            if (!data.length) return;
            var ctx = canvas.getContext('2d');
            if (!ctx) return;
            if (_sparks[symId]) { _sparks[symId].destroy(); }
            var hasHeld = data.some(function (d) { return d.held != null; });
            var labels = data.map(function (d) { return d.time || ''; });
            var datasets = [{
                data: data.map(function (d) { return d['return'] !== undefined ? d['return'] : 0; }),
                borderColor: cs('--studio-accent'),
                borderWidth: 1.5,
                pointRadius: 0,
                tension: 0.3,
                fill: false
            }];
            if (hasHeld) {
                datasets.push({
                    data: data.map(function (d) { return d.held != null ? d.held : null; }),
                    borderColor: cs('--studio-ink-dim'),
                    borderWidth: 1,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: false,
                    borderDash: [3, 3],
                    spanGaps: false
                });
            }

            var sparkPlugins = [];
            if (sym && sym.triggered && sym.triggered_at_time) {
                var exitIdx = labels.indexOf(sym.triggered_at_time);
                if (exitIdx === -1) {
                    // Partial match: find nearest label that starts with the exit time prefix
                    var prefix = sym.triggered_at_time.slice(0, 5);
                    exitIdx = labels.findIndex(function (l) { return l && l.slice(0, 5) === prefix; });
                }
                if (exitIdx >= 0) {
                    var exitColor = exitReasonColor(sym.triggered_reason);
                    var exitLabel = exitReasonLabel(sym.triggered_reason);
                    sparkPlugins.push(makeExitLinePlugin(exitIdx, exitLabel, exitColor, true));
                }
            }

            // Compute data range for explicit y auto-scaling; Chart.js default can
            // clip lines when return values exceed [0,1] if a previous Chart
            // instance left stale scale state on the canvas.
            var returnVals = datasets[0].data.filter(function (v) { return v != null; });
            var dataMin = returnVals.length ? Math.min.apply(null, returnVals) : 0;
            var dataMax = returnVals.length ? Math.max.apply(null, returnVals) : 1;
            var dataPad = Math.max(Math.abs(dataMax - dataMin) * 0.08, 0.001);

            _sparks[symId] = new Chart(canvas, {
                type: 'line',
                data: { labels: labels, datasets: datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    // Top padding carves space above the chart area for the exit badge.
                    layout: { padding: { top: sparkPlugins.length ? 18 : 2, bottom: 2, left: 2, right: 2 } },
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false },
                        y: { display: false, min: dataMin - dataPad, max: dataMax + dataPad }
                    }
                },
                plugins: sparkPlugins
            });
        });
    }

    function loadCharts(botState) {
        Object.values(botState || {}).forEach(function (sym) {
            if (!sym || !sym.id) return;
            fetch('/api/chart/' + sym.id)
                .then(function (r) { return r.json(); })
                .then(function (data) { renderSparkline(sym.id, data, sym); renderMcDial(sym.id, data); })
                .catch(function () {});
        });
    }

    // ---------------------------------------------------------------------------
    // Card verdict (guard_alpha from bot_state)
    // ---------------------------------------------------------------------------

    function updateVerdicts(botState) {
        var cards = document.querySelectorAll('[data-testid="triggered-verdict"]');
        cards.forEach(function (el) {
            var card = el.closest('[data-testid="sym-card"]');
            if (!card) return;
            var canvas = card.querySelector('[data-testid="card-spark"]');
            var symId = canvas ? canvas.getAttribute('data-sym-id') : null;
            if (!symId) return;
            var sym = (botState || {})[symId];
            if (!sym) return;
            if (sym.guard_alpha == null) {
                el.textContent = '—';
                return;
            }
            var ga = sym.guard_alpha;
            if (ga > 0) {
                el.textContent = 'Good call · saved +' + ga.toFixed(1) + '%α';
            } else {
                el.textContent = 'Early exit · gave up ' + Math.abs(ga).toFixed(1) + '%α';
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Cash Now
    // ---------------------------------------------------------------------------

    function cashNow(e, symId) {
        e.stopPropagation();
        var btn = e.currentTarget;
        var symName = symId;
        var card = btn.closest('[data-testid="sym-card"]');
        if (card) {
            var nameEl = card.querySelector('.card-name');
            if (nameEl) symName = nameEl.textContent.trim();
        }
        if (!window.confirm('Sell "' + symName + '" to cash?\nThis will execute a live sell order.')) return;
        btn.disabled = true;
        btn.textContent = 'Selling…';
        fetch('/api/sell_account', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
            body: JSON.stringify({ symphony_id: symId })
        })
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            btn.textContent = 'In cash';
        })
        .catch(function (err) {
            btn.disabled = false;
            btn.textContent = 'Cash Now';
            console.error('cashNow failed', err);
        });
    }

    window.cashNow = cashNow;

    // ---------------------------------------------------------------------------
    // Detail panel
    // ---------------------------------------------------------------------------

    function openDetailPanel(symIdOrIdx) {
        document.getElementById('detail-panel').classList.add('open');
        document.getElementById('detail-scrim').classList.add('open');

        var symId = (typeof symIdOrIdx === 'string') ? symIdOrIdx : _symIds[symIdOrIdx];
        var sym = symId ? _botState[symId] : null;
        if (!sym) return;

        var titleEl = document.getElementById('detail-panel-title');
        if (titleEl) titleEl.textContent = sym.normalized_name || sym.name || 'Symphony Detail';

        // Live return: use triggered_at_return if triggered, else current_return
        var rawLive = sym.triggered ? sym.triggered_at_return : sym.current_return;
        var liveRet = sentinelToNull(typeof rawLive === 'number' ? rawLive : null);
        if (liveRet == null) liveRet = 0;
        // sentinelToNull covers both -999 and +999 variants
        var stopLevel = sentinelToNull(typeof sym.stop_trigger === 'number' ? sym.stop_trigger : null);
        var shadowPeak = sentinelToNull(typeof sym.shadow_hwm === 'number' ? sym.shadow_hwm : null);
        // guard_alpha may be present even for triggered symphonies
        var ga = typeof sym.guard_alpha === 'number' ? sym.guard_alpha
               : (sym.guard_alpha != null ? parseFloat(sym.guard_alpha) : null);
        if (isNaN(ga) || isSentinel(ga)) ga = null;

        var elLive = document.getElementById('dp-live-ret');
        if (elLive) {
            elLive.textContent = fmtPct(liveRet);
            elLive.style.color = liveRet >= 0 ? cs('--studio-pos') : cs('--studio-neg');
        }
        var elLiveSub = document.getElementById('dp-live-ret-sub');
        if (elLiveSub) elLiveSub.textContent = sym.triggered ? 'frozen at exit' : 'real-time';

        var elStop = document.getElementById('dp-stop-level');
        if (elStop) elStop.textContent = stopLevel != null ? fmtPct(stopLevel) : '--';
        var elStopSub = document.getElementById('dp-stop-sub');
        if (elStopSub) elStopSub.textContent = sym.breakeven_locked ? '◆ breakeven lock' : 'vol-scaled';

        var elPeak = document.getElementById('dp-shadow-peak');
        if (elPeak) {
            elPeak.textContent = shadowPeak != null ? fmtPct(shadowPeak) : '--';
            if (shadowPeak != null) elPeak.style.color = cs('--studio-pos');
        }

        var elAlpha = document.getElementById('dp-alpha');
        if (elAlpha) {
            elAlpha.textContent = ga != null ? fmtPct(ga) : '--';
            if (ga != null) elAlpha.style.color = ga >= 0 ? cs('--studio-pos') : cs('--studio-neg');
        }
        var elAlphaSub = document.getElementById('dp-alpha-sub');
        if (elAlphaSub) elAlphaSub.textContent = ga != null ? (ga >= 0 ? 'saved vs hold' : 'gave up vs hold') : 'not exited';

        // Populate Risk Math from bot state eagerly (B-04); chart fetch will overwrite with live data.
        populateRiskMathFromState(sym, stopLevel, shadowPeak);

        fetchDetailChart(sym.id || symId, sym);

        // Phase 2b (ux-design-deliverable.md §Change 5): Risk Profile section —
        // per-symphony Sharpe / Sortino / Max DD / Ann. volatility, bot vs if-held.
        // Fetched from /api/performance?scope=symphony so the panel shows the same
        // risk-adjusted metrics as the Performance page. Read-only; no engine rerun.
        var rpEl = document.getElementById('detail-risk-profile');
        if (rpEl) {
            rpEl.innerHTML = '';
            fetch('/api/performance?scope=symphony&symphony_id=' + encodeURIComponent(symId) + '&days=60')
                .then(function (r) { return r.json(); })
                .then(function (p) {
                    var bot = p.shadow_metrics || {};
                    var held = p.live_metrics || {};
                    // [label, botVal, heldVal, kind, invertDelta]
                    // kind 'num' => raw value (Sharpe/Sortino); 'pct_frac' => fraction scaled to %.
                    // invertDelta true => lower is better (Max DD, Ann. volatility).
                    var rows = [
                        ['Sharpe',          bot.sharpe,       held.sharpe,       'num',      false],
                        ['Sortino',         bot.sortino,      held.sortino,      'num',      false],
                        ['Max drawdown',    bot.max_drawdown, held.max_drawdown, 'pct_frac', true],
                        ['Ann. volatility', bot.volatility,   held.volatility,   'pct_frac', true]
                    ];
                    var html = rows.map(function (row) {
                        var label = row[0], bv = row[1], hv = row[2], kind = row[3], invert = row[4];
                        var hasBoth = (bv != null && hv != null);
                        var delta = hasBoth ? (bv - hv) : null;
                        var deltaGood = invert ? (delta <= 0) : (delta >= 0);
                        var dcol = (delta == null) ? 'inherit'
                                 : deltaGood ? cs('--studio-pos') : cs('--studio-neg');
                        function fmt(v) {
                            if (v == null) return '—';
                            return (kind === 'num') ? v.toFixed(2) : (v * 100).toFixed(1) + '%';
                        }
                        var deltaStr = (delta == null) ? '—'
                            : (kind === 'num'
                                ? (delta >= 0 ? '+' : '') + delta.toFixed(2)
                                : (delta >= 0 ? '+' : '') + (delta * 100).toFixed(1) + 'pp');
                        // Match the existing .math-row pattern (index.html:1223-1258):
                        // label + value share a flex row; hint sits below on its own line.
                        return '<div class="math-row">'
                            + '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:2px;">'
                            + '<span class="math-row-label">' + label + '</span>'
                            + '<span class="math-row-value" style="color:' + dcol + ';">' + deltaStr + '</span>'
                            + '</div>'
                            + '<div class="math-row-hint">Bot ' + fmt(bv) + ' · Held ' + fmt(hv) + '</div>'
                            + '</div>';
                    }).join('');
                    rpEl.innerHTML = html;
                })
                .catch(function () {
                    // Graceful: leave the section empty on fetch failure — no JS error surfaced.
                    rpEl.innerHTML = '';
                });
        }
    }

    function populateRiskMathFromState(sym, stopLevel, shadowPeak) {
        // stopLevel and shadowPeak are already sentinel-guarded by openDetailPanel
        var stopDist = (shadowPeak != null && stopLevel != null)
            ? ((shadowPeak - stopLevel).toFixed(2) + '%') : '--';
        var beVal = sym.breakeven_locked ? 'Active' : 'Off';
        var rawMc = sentinelToNull(typeof sym.mc_prob === 'number' ? sym.mc_prob : null);
        var mcVal = rawMc != null ? rawMc.toFixed(1) + '%' : '--';
        var rawVol = sentinelToNull(typeof sym.symphony_vol === 'number' ? sym.symphony_vol : null);
        var volVal = rawVol != null ? rawVol.toFixed(3) : '--';
        var rawPara = sentinelToNull(typeof sym.para_velocity === 'number' ? sym.para_velocity : null);
        var paraVal = rawPara != null ? rawPara.toFixed(2) + '%' : '--';
        // vwap_pct does not exist on bot_state; the chart path fills this in via
        // last.vwap_diff in renderRiskMathPanel. Eager render shows '--' until chart loads.
        var rawVwap = null;
        var vwapVal = '--';
        var fields = [
            { id: 'dp-rm-mc',   val: mcVal },
            { id: 'dp-rm-stop', val: stopDist },
            { id: 'dp-rm-vol',  val: volVal },
            { id: 'dp-rm-para', val: paraVal },
            { id: 'dp-rm-be',   val: beVal },
            { id: 'dp-rm-vwap', val: vwapVal },
        ];
        fields.forEach(function (f) {
            var el = document.getElementById(f.id);
            if (el) el.textContent = f.val;
        });
    }

    function fetchDetailChart(symId, sym) {
        if (!symId) return;
        Promise.all([
            fetch('/api/chart/' + symId).then(function (r) { return r.json(); }).catch(function () { return {}; }),
            fetch('/api/logs/' + symId).then(function (r) { return r.json(); }).catch(function () { return []; }),
            fetch('/api/settings').then(function (r) { return r.json(); }).catch(function () { return {}; })
        ]).then(function (results) {
            // Pass settingsData (results[2]) to renderIntradayChart so the chart path
            // can read VWAP_BLEED_MULTIPLIER for the hint text (FIX 3 / Option A).
            renderIntradayChart(results[0], sym, results[2]);
            renderDetailLogs(results[1]);
            renderDetailVars(results[2], sym);
        });
    }

    function renderIntradayChart(chartData, sym, settingsData) {
        var canvas = document.getElementById('intraday-canvas');
        if (!canvas) return;
        var data = chartData.data || [];
        if (!data.length) return;

        if (_intradayChart) { _intradayChart.destroy(); _intradayChart = null; }

        var labels = data.map(function (d) { return d.time || ''; });
        var botData = data.map(function (d) { return sentinelToNull(d['return'] != null ? d['return'] : null); });
        var stopData = data.map(function (d) { return sentinelToNull(d.stop != null ? d.stop : null); });
        var breakevenData = data.map(function (d) { return sentinelToNull(d.breakeven != null ? d.breakeven : null); });
        var vwapData = data.map(function (d) { return sentinelToNull(d.vwap != null ? d.vwap : null); });
        var heldData = data.map(function (d) { return sentinelToNull(d.held != null ? d.held : null); });
        // mc_prob in data points is already 0-100 scale
        var mcData = data.map(function (d) { return sentinelToNull(d.mc_prob != null ? d.mc_prob : null); });

        var intradayPlugins = [];
        if (sym && sym.triggered && sym.triggered_at_time) {
            var iExitIdx = labels.indexOf(sym.triggered_at_time);
            if (iExitIdx === -1) {
                var iPrefix = sym.triggered_at_time.slice(0, 5);
                iExitIdx = labels.findIndex(function (l) { return l && l.slice(0, 5) === iPrefix; });
            }
            if (iExitIdx >= 0) {
                intradayPlugins.push(makeExitLinePlugin(
                    iExitIdx,
                    exitReasonLabel(sym.triggered_reason),
                    exitReasonColor(sym.triggered_reason),
                    false
                ));
            }
        }

        _intradayChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    { label: 'Bot', data: botData, borderColor: cs('--studio-accent'), borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false, yAxisID: 'y' },
                    { label: 'Held', data: heldData, borderColor: cs('--studio-ink-faint'), borderWidth: 1.5, pointRadius: 0, tension: 0.2, fill: false, borderDash: [4,4], yAxisID: 'y' },
                    { label: 'Stop', data: stopData, borderColor: cs('--studio-neg'), borderWidth: 1.5, pointRadius: 0, tension: 0, fill: false, borderDash: [4,3], yAxisID: 'y', hidden: false },
                    { label: 'Breakeven', data: breakevenData, borderColor: cs('--studio-accent'), borderWidth: 1.5, pointRadius: 0, tension: 0, fill: false, yAxisID: 'y', hidden: false },
                    { label: 'VWAP', data: vwapData, borderColor: cs('--studio-cyan'), borderWidth: 1.4, pointRadius: 0, tension: 0.2, fill: false, yAxisID: 'y', hidden: true },
                    { label: 'MC %', data: mcData, borderColor: cs('--studio-warn'), borderWidth: 1.4, pointRadius: 0, tension: 0.3, fill: false, borderDash: [1,2], yAxisID: 'mc', hidden: true }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { display: false },
                    y: { position: 'left', ticks: { font: { size: 10 } } },
                    mc: { position: 'right', min: 0, max: 100, ticks: { font: { size: 10 } }, grid: { drawOnChartArea: false } }
                }
            },
            plugins: intradayPlugins
        });

        wireOverlayToggles();
        renderRiskMathPanel(chartData, sym, settingsData);
    }

    function wireOverlayToggles() {
        // Stop (idx 2) and Breakeven (idx 3) default ON; VWAP (4) and MC (5) default OFF.
        var toggleMap = {
            'toggle-stop':      { idx: 2, legendId: 'legend-stop' },
            'toggle-breakeven': { idx: 3, legendId: 'legend-breakeven' },
            'toggle-vwap':      { idx: 4, legendId: 'legend-vwap' },
            'toggle-mc':        { idx: 5, legendId: 'legend-mc' }
        };
        Object.keys(toggleMap).forEach(function (id) {
            var cfg = toggleMap[id];
            var btn = document.getElementById(id);
            if (!btn || !_intradayChart) return;

            var dsIdx = cfg.idx;
            var ds = _intradayChart.data.datasets[dsIdx];
            var hasData = ds && ds.data && ds.data.some(function (v) { return v != null; });
            btn.disabled = !hasData;
            btn.title = hasData ? '' : 'No data available';

            // Sync initial active state from dataset hidden flag
            var initiallyVisible = hasData && ds.hidden === false;
            btn.classList.toggle('active', initiallyVisible);
            var legendEl = cfg.legendId ? document.getElementById(cfg.legendId) : null;
            if (legendEl) legendEl.style.display = initiallyVisible ? 'inline-flex' : 'none';

            btn.onclick = function () {
                if (!hasData) return;
                var meta = _intradayChart.getDatasetMeta(dsIdx);
                var nowHidden = ds.hidden === true || meta.hidden === true;
                ds.hidden = !nowHidden;
                meta.hidden = !nowHidden;
                _intradayChart.update();
                var visible = !ds.hidden;
                btn.classList.toggle('active', visible);
                if (legendEl) legendEl.style.display = visible ? 'inline-flex' : 'none';
            };
        });
    }

    function renderRiskMathPanel(chartData, sym, settingsData) {
        var data = chartData.data || [];
        var last = data.slice(-1)[0] || {};
        sym = sym || {};

        // mc_prob: scan data array backwards (top-level is null); skip sentinels
        var mcProb = null;
        for (var i = data.length - 1; i >= 0; i--) {
            var candidate = sentinelToNull(data[i].mc_prob != null ? data[i].mc_prob : null);
            if (candidate != null) { mcProb = candidate; break; }
        }

        // stop distance = shadow_hwm - current stop level (sentinel-guard both)
        var shadowHwm = sentinelToNull(typeof sym.shadow_hwm === 'number' ? sym.shadow_hwm : null);
        var rawStopChart = last.stop != null ? last.stop : null;
        var stopLevel = sentinelToNull(typeof sym.stop_trigger === 'number' ? sym.stop_trigger : rawStopChart);
        var stopDist = (shadowHwm != null && stopLevel != null) ? (shadowHwm - stopLevel).toFixed(2) + '%' : '--';

        // vol from last chart point
        var rawVol = sentinelToNull(last.vol != null ? last.vol : null);
        var volVal = rawVol != null ? rawVol.toFixed(3) : '--';

        // breakeven locked from sym state
        var beVal = sym.breakeven_locked ? 'Active' : 'Off';

        // vwap_diff from last chart point as a proxy for VWAP state
        var rawVwapDiff = sentinelToNull(last.vwap_diff != null ? last.vwap_diff : null);
        var vwapVal = rawVwapDiff != null ? (rawVwapDiff >= 0 ? 'Above' : 'Below') + ' (' + rawVwapDiff.toFixed(2) + '%)' : '--';

        function setEl(id, text) { var e = document.getElementById(id); if (e) e.textContent = text; }

        // MC probability + bar
        var mcText = mcProb != null ? mcProb.toFixed(1) + '%' : '—';
        setEl('dp-rm-mc', mcText);
        var mcBar = document.getElementById('dp-rm-mc-bar');
        if (mcBar && mcProb != null) {
            var pct = Math.max(0, Math.min(100, mcProb));
            mcBar.style.width = pct + '%';
            mcBar.style.background = mcProb < 15 ? cs('--studio-warn') : (mcProb > 80 ? cs('--studio-ink-dim') : cs('--studio-accent'));
        }

        setEl('dp-rm-stop', stopDist);
        setEl('dp-rm-vol', volVal);
        var paraVel = (sym.para_velocity != null) ? sym.para_velocity.toFixed(2) + '%' : '--';
        setEl('dp-rm-para', paraVel);
        setEl('dp-rm-para-hint', sym.para_velocity != null ? 'active ratchet velocity' : 'threshold —');

        // Breakeven lock with hint
        var beText = sym.breakeven_locked ? 'Active · floor 0.00%' : 'Off';
        setEl('dp-rm-be', beText);
        setEl('dp-rm-be-hint', sym.breakeven_locked ? '5 confirm ticks @ lock threshold' : 'not yet activated');

        // VWAP state with hint.
        // bleed mult lives in /api/settings response (settingsData.symphonies[name].params.VWAP_BLEED_MULTIPLIER);
        // sym.acc_VWAP_BLEED_MULTIPLIER does not exist on bot_state (FIX 3 / Option A).
        setEl('dp-rm-vwap', vwapVal);
        if (rawVwapDiff != null) {
            var bleedMult = null;
            if (settingsData) {
                var symName = sym.normalized_name || sym.name || '';
                var symId   = sym.id || '';
                var allSyms = Object.assign({}, settingsData.symphonies || {}, settingsData.symphony_overrides || {});
                var symStrat = allSyms[symName] || allSyms[symId];
                if (!symStrat) {
                    var allKeys = Object.keys(allSyms);
                    for (var ki = 0; ki < allKeys.length; ki++) {
                        var k = allKeys[ki];
                        if ((symName && (symName.indexOf(k) >= 0 || k.indexOf(symName) >= 0)) ||
                            (symId && (symId.indexOf(k) >= 0 || k.indexOf(symId) >= 0))) {
                            symStrat = allSyms[k];
                            break;
                        }
                    }
                }
                var bmRaw = symStrat && symStrat.params ? symStrat.params.VWAP_BLEED_MULTIPLIER : null;
                bleedMult = (bmRaw != null && typeof bmRaw === 'number') ? bmRaw : null;
            }
            setEl('dp-rm-vwap-hint', bleedMult != null ? 'bleed mult ' + bleedMult.toFixed(1) + '× vol' : 'VWAP defense active');
        } else {
            setEl('dp-rm-vwap-hint', '—');
        }

        // Panel footer
        var footerEl = document.getElementById('dp-footer-id');
        if (footerEl && sym && sym.id) {
            var normName = sym.normalized_name || sym.id.replace(/^sym_/, '');
            footerEl.textContent = 'id: ' + sym.id + ' · normalized: ' + normName;
        }
    }

    function renderDetailLogs(logs) {
        var container = document.getElementById('events-timeline');
        if (!container) return;
        var items = Array.isArray(logs) ? logs : (logs.events || []);
        if (!items.length) {
            container.innerHTML = '<div style="font-size:11px;color:var(--studio-ink-faint);">No events yet today.</div>';
            return;
        }
        var kindColorMap = {
            'trigger':  'var(--studio-plum)',
            'triggered':'var(--studio-plum)',
            'arm':      'var(--studio-warn)',
            'armed':    'var(--studio-warn)',
            'para':     'var(--studio-cyan)',
            'lock':     'var(--studio-accent)',
            'confirm':  'var(--studio-ink-dim)',
            'boot':     'var(--studio-ink-faint)',
            'info':     'var(--studio-ink-dim)',
            'error':    'var(--studio-neg)'
        };
        container.innerHTML = items.slice(0, 20).map(function (ev) {
            var ts = ev.timestamp || ev.time || '';
            var hhmm = ts.length >= 16 ? ts.slice(11, 16) : ts;
            var kind = (ev.event_type || ev.kind || 'info').toLowerCase();
            var msg = ev.message || ev.msg || '';
            var color = kindColorMap[kind] || 'var(--studio-ink-dim)';
            var kindLabel = kind.toUpperCase();
            // Extract reason from message if present
            var reasonMatch = msg.match(/^([A-Z_]+(?:\s[A-Z_]+)*):\s*/);
            var reason = reasonMatch ? msg.slice(reasonMatch[0].length) : msg;
            var kindDisplay = kindLabel + (reasonMatch ? ' · ' + reasonMatch[1] : '');
            return '<div class="event-row">' +
                '<span class="event-ts">' + hhmm + '</span>' +
                '<div class="event-body">' +
                  '<span class="event-dot" style="background:' + color + ';"></span>' +
                  '<div class="event-kind" style="color:' + color + ';">' + kindDisplay + '</div>' +
                  '<div class="event-detail">' + reason + '</div>' +
                '</div>' +
                '</div>';
        }).join('');
    }

    function renderDetailVars(settingsData, sym) {
        var container = document.getElementById('vars-section');
        if (!container || !sym) return;
        var symName = sym.normalized_name || sym.name || '';
        var symId   = sym.id || '';
        var allSyms = Object.assign({}, settingsData.symphonies || {}, settingsData.symphony_overrides || {});
        // Try exact match by normalized_name, then by id, then by partial match
        var symStrat = allSyms[symName] || allSyms[symId];
        if (!symStrat) {
            var allKeys = Object.keys(allSyms);
            for (var i = 0; i < allKeys.length; i++) {
                var k = allKeys[i];
                if ((symName && (symName.indexOf(k) >= 0 || k.indexOf(symName) >= 0)) ||
                    (symId && (symId.indexOf(k) >= 0 || k.indexOf(symId) >= 0))) {
                    symStrat = allSyms[k];
                    break;
                }
            }
        }
        var params = symStrat ? (symStrat.params || {}) : null;
        var locked = symStrat ? new Set(symStrat.locked_vars || []) : new Set();
        if (!params || Object.keys(params).length === 0) {
            container.innerHTML = '<div class="var-row"><span class="var-key">No variables configured</span></div>';
            return;
        }
        container.innerHTML = Object.entries(params).map(function (pair) {
            var k = pair[0]; var v = pair[1];
            var lockBadge = locked.has(k) ? ' <span style="color:var(--studio-accent);font-size:9px;" title="Locked from autotuner">&#9670;</span>' : '';
            return '<div class="var-row">' +
                '<span class="var-key">' + k + lockBadge + '</span>' +
                '<span class="var-val">' + (typeof v === 'number' ? v.toFixed(v % 1 === 0 ? 0 : 3) : v) + '</span>' +
                '</div>';
        }).join('');
    }

    window.openDetailPanel = openDetailPanel;

    // ---------------------------------------------------------------------------
    // MC dial rendering
    // ---------------------------------------------------------------------------

    var MC_CIRCUMFERENCE = 94.25; // 2 * π * 15

    function renderMcDial(symId, chartData) {
        // mc_prob in chart data points is 0-100 scale; top-level chartData.mc_prob may be null.
        // Use last non-null mc_prob from data array, falling back to top-level fields.
        var mcProb = null;
        var dataPoints = chartData.data || [];
        for (var i = dataPoints.length - 1; i >= 0; i--) {
            if (dataPoints[i].mc_prob != null) { mcProb = dataPoints[i].mc_prob; break; }
        }
        if (mcProb === null) mcProb = chartData.mc_prob != null ? chartData.mc_prob : (chartData.mc_pct != null ? chartData.mc_pct : null);
        if (mcProb === null) return;

        // Normalise: values arrive as 0-100 percentage
        var pct = Math.max(0, Math.min(100, mcProb)) / 100;
        var offset = MC_CIRCUMFERENCE * (1 - pct);
        var label = (pct * 100).toFixed(0) + '%';
        // Threshold color per design Dial: <15 → warn (amber), >80 → inkDim, else accent
        var arcColor = mcProb < 15 ? cs('--studio-warn')
                     : mcProb > 80 ? cs('--studio-ink-dim')
                     : cs('--studio-accent');
        var dials = document.querySelectorAll('[data-testid="mc-dial"][data-sym-id="' + symId + '"]');
        dials.forEach(function (svg) {
            var arc = svg.querySelector('.mc-arc');
            var text = svg.querySelector('.mc-text');
            if (arc) {
                arc.setAttribute('stroke-dashoffset', offset.toFixed(2));
                arc.style.stroke = arcColor;
            }
            if (text) text.textContent = label;
        });
    }

    // ---------------------------------------------------------------------------
    // Comparison rows (Today / Cumulative / Max DD) — live update from portfolio_strip
    // ---------------------------------------------------------------------------

    function setPosNeg(el, value) {
        if (!el) return;
        el.classList.remove('pos', 'neg');
        if (value >= 0) el.classList.add('pos');
        else el.classList.add('neg');
    }

    function updateComparisonRows(data) {
        var ps = (data && data.portfolio_strip) || {};
        var rows = [
            { id: 'today',      values: ps.today_change      || {}, higherIsBetter: true },
            { id: 'cumulative', values: ps.cumulative_return || {}, higherIsBetter: true },
            { id: 'mdd',        values: ps.max_drawdown      || {}, higherIsBetter: false }
        ];
        rows.forEach(function (row) {
            var bot  = sentinelToNull(typeof row.values.dry_run === 'number' ? row.values.dry_run : (Number(row.values.dry_run) || null)) || 0;
            var held = sentinelToNull(typeof row.values.if_held  === 'number' ? row.values.if_held  : (Number(row.values.if_held)  || null)) || 0;
            var maxAbs = Math.max(Math.abs(bot), Math.abs(held), 1);

            var botBars  = document.querySelectorAll('[data-testid="comp-bar-bot"][data-row="'  + row.id + '"]');
            var heldBars = document.querySelectorAll('[data-testid="comp-bar-held"][data-row="' + row.id + '"]');

            botBars.forEach(function (el) {
                el.style.width = Math.min(Math.abs(bot) / maxAbs * 100, 100).toFixed(1) + '%';
            });
            heldBars.forEach(function (el) {
                el.style.width = Math.min(Math.abs(held) / maxAbs * 100, 100).toFixed(1) + '%';
            });

            var botText  = document.querySelector('[data-testid="comp-' + row.id + '-bot-text"]');
            var heldText = document.querySelector('[data-testid="comp-' + row.id + '-held-text"]');
            if (botText) {
                botText.textContent = 'Bot ' + fmtPct(bot);
                setPosNeg(botText, row.higherIsBetter ? bot : -bot);
            }
            if (heldText) {
                heldText.textContent = 'Held ' + fmtPct(held);
                setPosNeg(heldText, row.higherIsBetter ? held : -held);
            }
        });
    }

    // ---------------------------------------------------------------------------
    // updateCards — refresh per-symphony card values from /api/state 'symphonies'
    //
    // Updates only the targeted data-field spans inside each [data-sym-id] card.
    // Does NOT touch card structure, layout, classes, or the sparkline <canvas>.
    // Cards go stale without this because the 30s poll (POLL_INTERVAL_MS) updates
    // hero widgets but the cards are server-rendered only on hard-refresh.
    // ---------------------------------------------------------------------------

    function _fmtSignedPct(val, decimals) {
        if (val == null || isNaN(val)) return '--';
        decimals = decimals != null ? decimals : 1;
        return (val >= 0 ? '+' : '') + val.toFixed(decimals) + '%';
    }

    function _fmtAbsPct(val, decimals) {
        if (val == null || isNaN(val)) return '--';
        decimals = decimals != null ? decimals : 1;
        return Math.abs(val).toFixed(decimals) + '%';
    }

    function _setPosNegClass(el, val) {
        if (el == null) return;
        if (val == null || isNaN(val)) return;
        el.classList.remove('pos', 'neg');
        el.classList.add(val >= 0 ? 'pos' : 'neg');
    }

    // Derive status-pill label + CSS class from per-symphony flags.
    // Mirrors the Jinja logic in index.html so both stay in sync.
    function _symStatusPill(sym) {
        if (sym.triggered) {
            var tr = sym.triggered_reason || '';
            if (tr.indexOf('Take-Profit') !== -1 || tr.indexOf('take_profit') !== -1) {
                return { text: 'TAKE-PROFIT', cls: 'tp' };
            }
            if (tr.indexOf('VWAP Bleed') !== -1 || tr.indexOf('vwap_bleed') !== -1) {
                return { text: 'VWAP BLEED', cls: 'bleed' };
            }
            if (tr.indexOf('VWAP') !== -1 || tr.indexOf('vwap') !== -1) {
                return { text: 'VWAP', cls: 'vwap' };
            }
            return { text: 'TRAILING STOP', cls: 'stop' };
        }
        if (sym.para_armed) { return { text: 'Para-Armed', cls: 'para-armed' }; }
        if (sym.tp_armed)   { return { text: 'TP-Armed',   cls: 'tp-armed'   }; }
        if (sym.armed)      { return { text: 'Armed',      cls: 'armed'      }; }
        return { text: 'Standby', cls: 'standby' };
    }

    function updateCards(data) {
        var syms = data.symphonies;
        if (!Array.isArray(syms)) return;

        syms.forEach(function (sym) {
            var id = sym.id;
            if (!id) return;

            // Update MC dial from sym.mc_prob on every poll. renderMcDial accepts a
            // chartData object; pass a minimal stub so the arc updates without a
            // full chart fetch. The mc-dial querySelector is inside renderMcDial.
            if (sym.mc_prob != null) {
                renderMcDial(id, { mc_prob: sym.mc_prob, data: [] });
            }

            // Find the card element by data-sym-id attribute; there may be one in
            // active section and one in standby — update whichever exists.
            var cards = document.querySelectorAll('[data-sym-id="' + id + '"].sym-card');
            cards.forEach(function (card) {
                // Update status-pill text + class from sym.triggered/armed/tp_armed/para_armed.
                // Without this the pill stays at its server-rendered value (e.g. "Armed")
                // even after an exit fires on the next engine cycle.
                var pill = card.querySelector('[data-testid="status-pill"]');
                if (pill != null) {
                    var pillState = _symStatusPill(sym);
                    pill.className = pill.className.replace(
                        /\b(tp|bleed|vwap|stop|para-armed|tp-armed|armed|standby)\b/g, ''
                    ).replace(/\s+/g, ' ').trim();
                    pill.classList.add('status-pill', pillState.cls);
                    pill.textContent = pillState.text;
                }

                // Update dual-value-headline (tc-bot / tc-held)
                var tcBot  = card.querySelector('[data-field="tc-bot"]');
                var tcHeld = card.querySelector('[data-field="tc-held"]');
                if (tcBot  != null) { tcBot.textContent  = _fmtSignedPct(sym.tc_bot);  _setPosNegClass(tcBot,  sym.tc_bot);  }
                if (tcHeld != null) { tcHeld.textContent = _fmtSignedPct(sym.tc_held); _setPosNegClass(tcHeld, sym.tc_held); }

                // Update footer grid (cr-bot / cr-held / mdd-bot / mdd-held)
                var crBot   = card.querySelector('[data-field="cr-bot"]');
                var crHeld  = card.querySelector('[data-field="cr-held"]');
                var mddBot  = card.querySelector('[data-field="mdd-bot"]');
                var mddHeld = card.querySelector('[data-field="mdd-held"]');
                if (crBot  != null) { crBot.textContent  = _fmtSignedPct(sym.cr_bot);    _setPosNegClass(crBot,  sym.cr_bot);  }
                if (crHeld != null) { crHeld.textContent = _fmtSignedPct(sym.cr_held);   _setPosNegClass(crHeld, sym.cr_held); }
                if (mddBot  != null) { mddBot.textContent  = _fmtAbsPct(sym.mdd_bot); }
                if (mddHeld != null) { mddHeld.textContent = _fmtAbsPct(sym.mdd_held); }
            });
        });
    }

    // ---------------------------------------------------------------------------
    // Main updateDashboard — called on each fetch('/api/state') response
    // ---------------------------------------------------------------------------

    function updateDashboard(data) {
        var meta = data.meta || {};
        var botState = data.bot_state || data.state || {};

        _botState = botState;
        _symIds = Object.keys(botState);

        // C-10: per-renderer try/catch so one failure doesn't kill subsequent renderers
        [
            function () { renderHeroChart(meta); },
            function () { renderGuardAlpha(data); },
            function () { updateComparisonRows(data); },
            function () { updateVerdicts(botState); },
            function () { updateMiniStats(meta); },
            function () { updateStatusStrip(meta); },
            function () { loadCharts(botState); },
            function () {
                var elTicker = document.getElementById('hero-ticker');
                if (elTicker && data.ticker_price != null) elTicker.textContent = data.ticker_price;
            },
            function () { updateMarketDot(data); },
            function () { if (typeof window.updateChromeTicker === 'function') window.updateChromeTicker(data); },
            // cards-live: refresh per-symphony card value spans from data.symphonies.
            // Reuses POLL_INTERVAL_MS — no new timer. Cards stay live between hard-refreshes.
            function () { updateCards(data); },
        ].forEach(function (fn) {
            try { fn(); } catch (e) { console.error('updateDashboard renderer error:', e); }
        });
    }

    function updateMiniStats(meta) {
        var elTracked = document.getElementById('hero-tracked');
        if (elTracked) elTracked.textContent = meta.tracked != null ? meta.tracked : 0;
        var elArmed = document.getElementById('hero-armed');
        if (elArmed) elArmed.textContent = meta.armed != null ? meta.armed : 0;
        var elTriggered = document.getElementById('hero-triggered');
        if (elTriggered) elTriggered.textContent = meta.triggered != null ? meta.triggered : 0;
    }

    function updateMarketDot(data) {
        var meta = data.meta || {};
        var dot = document.querySelector('.status-dot');
        if (!dot) return;
        if (meta.market_state === 'open') {
            dot.classList.remove('closed');
        } else {
            dot.classList.add('closed');
        }
    }

    function updateStatusStrip(meta) {
        var label = document.getElementById('market-label');
        if (label && meta.market_state_label) label.textContent = meta.market_state_label;

        var triggers = (meta.triggers_today) || {};
        var chipTs = document.getElementById('strip-chip-trailing');
        var chipTp = document.getElementById('strip-chip-tp');
        var chipVwap = document.getElementById('strip-chip-vwap');
        if (chipTs) chipTs.textContent = 'Trailing stop: ' + (triggers.trailing_stop || 0);
        if (chipTp) chipTp.textContent = 'Take-profit: ' + (triggers.take_profit || 0);
        if (chipVwap) chipVwap.textContent = 'VWAP: ' + (triggers.vwap || 0);
    }

    // ---------------------------------------------------------------------------
    // Poll loop
    // ---------------------------------------------------------------------------

    function loadState() {
        fetch('/api/state')
            .then(function (r) { return r.json(); })
            .then(function (data) { updateDashboard(data); })
            .catch(function (err) { console.error('state load failed', err); });
    }

    document.addEventListener('DOMContentLoaded', function () {
        loadState();
        setInterval(loadState, POLL_INTERVAL_MS);

        var windowMap = {
            'window-30d':  30,
            'window-60d':  60,
            'window-90d':  90,
            'window-125d': 125,
            'window-ytd':  'ytd',
            'window-1y':   252
        };
        var winLabelMap = { 'window-30d': '30d', 'window-60d': '60d', 'window-90d': '90d', 'window-125d': '125d', 'window-ytd': 'YTD', 'window-1y': '1Y' };
        Object.keys(windowMap).forEach(function (testid) {
            var btn = document.querySelector('[data-testid="' + testid + '"]');
            if (!btn) return;
            btn.addEventListener('click', function () {
                document.querySelectorAll('[data-testid="window-selector"] button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                var winLabel = document.getElementById('guard-alpha-window-label');
                if (winLabel) winLabel.textContent = winLabelMap[testid] || btn.textContent;
                var winKey = windowMap[testid];
                _heroWindow = winKey;
                // Fetch window-specific data so each range renders a distinct curve.
                var winParam = (winKey === 252) ? '1y' : (winKey === 'ytd' ? 'ytd' : winKey + 'd');
                fetch('/api/hero-chart/' + winParam)
                    .then(function (r) { return r.json(); })
                    .then(function (d) {
                        var naEl = document.getElementById('hero-chart-na');
                        // Always plot whatever data is available; caption when history is shorter than the window.
                        if (naEl) naEl.style.display = d.insufficient_history ? 'block' : 'none';
                        if (!_cumChart) return;
                        if (d.hist_dates && d.hist_dates.length) {
                            _cumChart.data.labels = d.hist_dates;
                            _cumChart.data.datasets[0].data = d.hist_bot;
                            _cumChart.data.datasets[1].data = d.hist_held;
                            _cumChart.update('none');
                        }
                    })
                    .catch(function () {
                        applyHeroWindow(_heroWindow);
                    });
            });
        });
    });
})();
