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
                // F-025: the y-axis (magnitude) must stay visible so this chart's
                // day-by-day cumulative series (~-3% scale) can't be visually
                // conflated with the adjacent VW-lifetime scalar headline (~+34%,
                // opposite sign, ~12x magnitude). X stays hidden -- the
                // hero-data-as-of legend span already covers time.
                scales: { x: { display: false }, y: { display: true } }
            }
        });
        applyHeroWindow(_heroWindow);
    }

    // ---------------------------------------------------------------------------
    // Guard alpha headline
    // ---------------------------------------------------------------------------

    function renderGuardAlpha(data, _fromStrip) {
        var ps = (data || {}).portfolio_strip || {};
        // Read the server-computed windowed VW guard alpha directly.  The server
        // sets portfolio_strip.guard_alpha in _compute_portfolio_strip (app.py:846-847)
        // and /api/strip/<window> returns it from compute_windowed_portfolio_strip.
        // Do NOT derive guard_alpha from cumulative_return.dry_run - cumulative_return.if_held:
        // those fields may be on different bases (windowed VW bot vs account-basis held),
        // which produces a fabricated -36.18% instead of the correct ~+0.90%.
        var guard_alpha = typeof ps.guard_alpha === 'number' ? ps.guard_alpha : null;

        // Frozen-path fallback: the closed_frozen /api/state portfolio_strip is built
        // inline (app.py ~line 1262) and does not call _compute_portfolio_strip, so
        // guard_alpha and windowed_cumulative_return are absent.  /api/strip/<window>
        // works in all market states.  Fetching it here populates both the headline
        // (via the renderGuardAlpha call inside fetchWindowedStrip) and the cumulative
        // row (via updateComparisonRows) with correct windowed VW values.
        // _fromStrip guard: compute_windowed_portfolio_strip can return guard_alpha:null
        // when weight_sum==0 (no symphonies). Without the guard that would loop forever.
        if (guard_alpha === null) {
            if (!_fromStrip) fetchWindowedStrip('30d');
            return;
        }

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
                .then(function (data) {
                    renderSparkline(sym.id, data, sym);
                    // Thread the triggered/exited flag onto chartData so renderMcDial
                    // can actively render the honest exited state instead of scanning
                    // history for a (possibly stale, resurrected) reading.
                    data.triggered = sym.triggered;
                    renderMcDial(sym.id, data);
                })
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

        // mc_prob: for a TRIGGERED (exited) symphony, never scan history for a
        // stale pre-trigger reading -- once post-trigger rows carry the honest
        // None sentinel (AC-1), a backward null-scan cannot tell "no data yet"
        // from "exited" and would resurrect the last real pre-trigger value as
        // if it were current. Short-circuit on trigger STATE before the scan.
        var mcProb = null;
        if (!sym.triggered) {
            // mc_prob: scan data array backwards (top-level is null); skip sentinels
            for (var i = data.length - 1; i >= 0; i--) {
                var candidate = sentinelToNull(data[i].mc_prob != null ? data[i].mc_prob : null);
                if (candidate != null) { mcProb = candidate; break; }
            }
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
        if (mcBar) {
            if (mcProb != null) {
                var pct = Math.max(0, Math.min(100, mcProb));
                mcBar.style.width = pct + '%';
                mcBar.style.background = mcProb < 15 ? cs('--studio-warn') : (mcProb > 80 ? cs('--studio-ink-dim') : cs('--studio-accent'));
            } else {
                // Neutral reset -- never leave the bar showing a stale
                // width/color from a previous (possibly pre-trigger) render.
                mcBar.style.width = '0%';
                mcBar.style.background = cs('--studio-ink-faint');
            }
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
        var dials = document.querySelectorAll('[data-testid="mc-dial"][data-sym-id="' + symId + '"]');

        // Exited/triggered: actively render an honest "-" state. Never scan
        // history for a stale pre-trigger reading (that would resurrect it as
        // "current"), and never silently skip the render (that freezes the
        // dial at whatever was last drawn) -- both are the F7 defects.
        if (chartData && chartData.triggered) {
            dials.forEach(function (svg) {
                var arc = svg.querySelector('.mc-arc');
                var text = svg.querySelector('.mc-text');
                if (arc) {
                    arc.setAttribute('stroke-dashoffset', MC_CIRCUMFERENCE.toFixed(2));
                    arc.style.stroke = cs('--studio-ink-faint');
                }
                if (text) text.textContent = '—';
            });
            return;
        }

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
        // AC-4a: each row names its alpha (.vs-delta) testid explicitly so the poll
        // can address + refresh the displayed alpha (comp-today-delta /
        // comp-cumulative-delta / comp-mdd-delta) — not just bot/held text.
        var rows = [
            { id: 'today',      deltaTestid: 'comp-today-delta',      values: ps.today_change      || {}, higherIsBetter: true },
            // F-014: this row's SSR label reads "Cumulative · lifetime"
            // (templates/index.html:920) -- it must source the lifetime
            // cumulative_return only. Do NOT prefer a windowed value here; that
            // clobbers the lifetime-labeled figure with a windowed one on every poll.
            { id: 'cumulative', deltaTestid: 'comp-cumulative-delta', values: ps.cumulative_return || {}, higherIsBetter: true },
            { id: 'mdd',        deltaTestid: 'comp-mdd-delta',        values: ps.max_drawdown      || {}, higherIsBetter: false }
        ];
        rows.forEach(function (row) {
            // F-016: sentinelToNull's null result must survive to fmtPct (which has
            // its own honest '--' branch for null) -- do NOT coerce to 0 here, that
            // fabricates a false "no change" reading for a genuinely missing value.
            var bot  = sentinelToNull(typeof row.values.dry_run === 'number' ? row.values.dry_run : (Number(row.values.dry_run) || null));
            var held = sentinelToNull(typeof row.values.if_held  === 'number' ? row.values.if_held  : (Number(row.values.if_held)  || null));
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
                // F-016: guard setPosNeg -- its `value >= 0` check is TRUE for null in
                // JS (null coerces to 0), which would mis-classify a missing value as
                // positive/green if called unguarded.
                if (bot !== null) setPosNeg(botText, row.higherIsBetter ? bot : -bot);
                else botText.classList.remove('pos', 'neg');
            }
            if (heldText) {
                heldText.textContent = 'Held ' + fmtPct(held);
                if (held !== null) setPosNeg(heldText, row.higherIsBetter ? held : -held);
                else heldText.classList.remove('pos', 'neg');
            }

            // AC-4a: recompute + write the alpha (.vs-delta) span every poll so the
            // displayed alpha == displayed (Bot - Held) by construction. Without this
            // the delta stays frozen at its server-side-render value while Bot/Held
            // tick live (the 2.17-vs-1.72 staleness symptom). For Max DD, alpha is the
            // drawdown the guard SAVED: abs(held) - abs(bot) (less DD = better),
            // matching the template's mdd_alpha and the per-card convention.
            var deltaEl = document.querySelector('[data-testid="' + row.deltaTestid + '"]');
            if (deltaEl) {
                if (bot !== null && held !== null) {
                    var deltaAlpha = row.higherIsBetter
                        ? (bot - held)
                        : (Math.abs(held) - Math.abs(bot));
                    deltaEl.textContent = 'α ' + fmtPct(deltaAlpha);
                    setPosNeg(deltaEl, deltaAlpha);
                } else {
                    deltaEl.textContent = 'α ' + fmtPct(null);
                    deltaEl.classList.remove('pos', 'neg');
                }
            }
        });

        // Ann Vol row — data lives in meta.portfolio.vol_bot / vol_held (fraction scale).
        // vol is inverted: lower is better for this capital-preservation system.
        var portfolio = ((data && data.meta) || {}).portfolio || {};
        var rawVolBot  = portfolio.vol_bot  != null ? portfolio.vol_bot  : null;
        var rawVolHeld = portfolio.vol_held != null ? portfolio.vol_held : null;
        var hasVol = rawVolBot != null && rawVolHeld != null;

        var volBotText  = document.querySelector('[data-testid="comp-vol-bot-text"]');
        var volHeldText = document.querySelector('[data-testid="comp-vol-held-text"]');
        var volDelta    = document.querySelector('[data-testid="comp-vol-delta"]');
        var volBotBars  = document.querySelectorAll('[data-testid="comp-bar-vol-bot"]');
        var volHeldBars = document.querySelectorAll('[data-testid="comp-bar-vol-held"]');

        if (hasVol) {
            var volBotPct  = rawVolBot  * 100;
            var volHeldPct = rawVolHeld * 100;
            var volAlphaPp = (rawVolHeld - rawVolBot) * 100;
            var volBotWins = rawVolBot <= rawVolHeld;
            var volMax     = Math.max(rawVolBot, rawVolHeld, 0.0001);
            var botW  = volBotWins  ? 100 : Math.min(rawVolHeld / rawVolBot  * 100, 100);
            var heldW = !volBotWins ? 100 : Math.min(rawVolBot  / rawVolHeld * 100, 100);

            if (volBotText) {
                volBotText.textContent = 'Bot ' + volBotPct.toFixed(1) + '%';
                volBotText.className = volBotText.className.replace(/\bpos\b|\bneg\b/g, '').trim();
            }
            if (volHeldText) {
                volHeldText.textContent = 'Held ' + volHeldPct.toFixed(1) + '%';
                volHeldText.className = volHeldText.className.replace(/\bpos\b|\bneg\b/g, '').trim();
            }
            if (volDelta) {
                volDelta.textContent = 'α ' + (volAlphaPp >= 0 ? '+' : '') + volAlphaPp.toFixed(1) + 'pp';
                volDelta.className = volDelta.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                volDelta.classList.add(volAlphaPp >= 0 ? 'pos' : 'neg');
            }
            volBotBars.forEach(function (el) { el.style.width = botW.toFixed(1) + '%'; });
            volHeldBars.forEach(function (el) { el.style.width = heldW.toFixed(1) + '%'; });
        } else {
            if (volBotText)  volBotText.textContent  = 'Bot —';
            if (volHeldText) volHeldText.textContent = 'Held —';
            if (volDelta)    volDelta.textContent    = 'α —';
            volBotBars.forEach(function (el)  { el.style.width = '0%'; });
            volHeldBars.forEach(function (el) { el.style.width = '0%'; });
        }
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
            // Always call (never skip on null) -- an exited symphony's mc_prob is
            // honestly null (AC-1), and renderMcDial must actively render that
            // state rather than freeze at whatever was last drawn.
            renderMcDial(id, { triggered: sym.triggered, mc_prob: sym.mc_prob, data: [] });

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

                // Dual-value-headline + footer — querySelectorAll so BOTH tc-bot spans update
                // (headline dv-value and footer cfg-val share the same data-field attribute).
                card.querySelectorAll('[data-field="tc-bot"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.tc_bot); _setPosNegClass(el, sym.tc_bot); });
                card.querySelectorAll('[data-field="tc-held"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.tc_held); _setPosNegClass(el, sym.tc_held); });

                // Footer grid — querySelectorAll so every matching span updates.
                card.querySelectorAll('[data-field="cr-bot"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.cr_bot); _setPosNegClass(el, sym.cr_bot); });
                card.querySelectorAll('[data-field="cr-held"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.cr_held); _setPosNegClass(el, sym.cr_held); });
                card.querySelectorAll('[data-field="mdd-bot"]').forEach(function (el) { el.textContent = _fmtAbsPct(sym.mdd_bot); });
                card.querySelectorAll('[data-field="mdd-held"]').forEach(function (el) { el.textContent = _fmtAbsPct(sym.mdd_held); });

                // Show/hide + update the triggered-verdict outcome banner.
                // The template always renders this element; display:none hides it until exit.
                // On each poll: show when sym.triggered, hide otherwise; set text from
                // sym.guard_alpha (carried in symphonies payload from this cycle).
                var verdict = card.querySelector('[data-testid="triggered-verdict"]');
                if (verdict != null) {
                    if (sym.triggered) {
                        verdict.style.display = '';
                        if (sym.guard_alpha != null) {
                            var ga = sym.guard_alpha;
                            verdict.textContent = ga > 0
                                ? 'Good call · saved +' + ga.toFixed(1) + '%α'
                                : 'Early exit · gave up ' + Math.abs(ga).toFixed(1) + '%α';
                        }
                    } else {
                        verdict.style.display = 'none';
                    }
                }

                // Update "Bot · frozen" / "Bot" dv-label when triggered state changes.
                // data-field="dv-label-bot" targets the label span in .dual-value-headline.
                var dvLabelBot = card.querySelector('[data-field="dv-label-bot"]');
                if (dvLabelBot != null) {
                    dvLabelBot.textContent = sym.triggered ? 'Bot · frozen' : 'Bot';
                }

                // Update footer alpha-badge labels (α delta on Today/Cum/Max DD headings).
                // data-field="tc-alpha-badge" / "cr-alpha-badge" / "mdd-alpha-badge"
                var tcAlpha  = (sym.tc_bot  != null && sym.tc_held  != null) ? sym.tc_bot  - sym.tc_held  : null;
                var crAlpha  = (sym.cr_bot  != null && sym.cr_held  != null) ? sym.cr_bot  - sym.cr_held  : null;
                // mdd alpha: held_abs - bot_abs (positive = bot had smaller drawdown = better)
                var mddAlpha = (sym.mdd_bot != null && sym.mdd_held != null)
                    ? Math.abs(sym.mdd_held) - Math.abs(sym.mdd_bot)
                    : null;

                var tcBadge  = card.querySelector('[data-field="tc-alpha-badge"]');
                var crBadge  = card.querySelector('[data-field="cr-alpha-badge"]');
                var mddBadge = card.querySelector('[data-field="mdd-alpha-badge"]');

                if (tcBadge != null) {
                    tcBadge.textContent = tcAlpha != null
                        ? 'α ' + (tcAlpha >= 0 ? '+' : '') + tcAlpha.toFixed(1)
                        : 'α --';
                    tcBadge.className = tcBadge.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                    if (tcAlpha != null) tcBadge.classList.add(tcAlpha >= 0 ? 'pos' : 'neg');
                }
                if (crBadge != null) {
                    crBadge.textContent = crAlpha != null
                        ? 'α ' + (crAlpha >= 0 ? '+' : '') + crAlpha.toFixed(1)
                        : 'α --';
                    crBadge.className = crBadge.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                    if (crAlpha != null) crBadge.classList.add(crAlpha >= 0 ? 'pos' : 'neg');
                }
                if (mddBadge != null) {
                    mddBadge.textContent = mddAlpha != null
                        ? 'α ' + (mddAlpha >= 0 ? '+' : '') + mddAlpha.toFixed(1)
                        : 'α --';
                    mddBadge.className = mddBadge.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                    if (mddAlpha != null) mddBadge.classList.add(mddAlpha >= 0 ? 'pos' : 'neg');
                }

                // Section auto-partition: move the card to the correct section when status
                // changes.  Grouping rule matches app.py:474 exactly — a card is active if
                // ANY of the four flags is true; standby otherwise.  appendChild on a live
                // DOM node moves it (no recreation, sparkline canvas preserved).
                var isActive = sym.armed || sym.tp_armed || sym.para_armed || sym.triggered;
                var activeSection  = document.querySelector('[data-testid="active-section"]');
                var standbySection = document.querySelector('[data-testid="standby-section"]');
                if (activeSection && standbySection) {
                    var targetSection = isActive ? activeSection : standbySection;
                    // Prefer the .cards-grid child if present (matches template structure);
                    // fall back to the section container itself.
                    var targetGrid = targetSection.querySelector('.cards-grid') || targetSection;
                    if (card.parentNode !== targetGrid) {
                        targetGrid.appendChild(card);
                    }
                }
            });
        });
    }

    // Update section count badges and "data as of" time from poll response.
    // Section counts derive from data.symphonies list (active = armed/triggered/tp_armed/para_armed).
    // The "data as of" legend span refreshes from meta.portfolio.data_as_of each tick.
    function updateSectionMeta(data) {
        // F-011: data.symphonies genuinely does not exist on the closed/frozen
        // /api/state branch (app.py emits state/bot_state only there) -- read the
        // real field, present on BOTH branches, and filter to symphony entries the
        // same way the server does (isinstance(v, dict) and "name" in v) since
        // state/bot_state also carries flat non-symphony metadata keys.
        var stateObj = (data && (data.bot_state || data.state)) || {};
        var syms = Object.keys(stateObj)
            .map(function (k) { return stateObj[k]; })
            .filter(function (v) { return v && typeof v === 'object' && 'name' in v; });
        var activeCount  = syms.filter(function (s) {
            return s.armed || s.tp_armed || s.para_armed || s.triggered;
        }).length;
        var standbyCount = syms.filter(function (s) {
            return !s.armed && !s.tp_armed && !s.para_armed && !s.triggered;
        }).length;

        var activeBadge  = document.querySelector('[data-testid="active-section-count"]');
        var standbyBadge = document.querySelector('[data-testid="standby-section-count"]');
        if (activeBadge)  activeBadge.textContent  = activeCount;
        if (standbyBadge) standbyBadge.textContent = standbyCount;

        // Update "data as of <time>" in hero chart legend — id="hero-data-as-of".
        var portfolio = ((data.meta) || {}).portfolio || {};
        var dataAsOf = portfolio.data_as_of || data.data_as_of || null;
        var asOfEl = document.getElementById('hero-data-as-of');
        if (asOfEl && dataAsOf) asOfEl.textContent = 'data as of ' + dataAsOf;
    }

    // ---------------------------------------------------------------------------
    // Account · all-time CR — fixed stat, never windowed
    // ---------------------------------------------------------------------------
    // Reads meta.portfolio.account_all_time_cr (authoritative, from /api/state _build_meta)
    // with portfolio_strip.account_all_time_cr as fallback.  The SSR renders the element
    // only when the account cache is warm; if the cache was cold at page-load the element
    // is absent.  This renderer creates it when missing so cold-start shows the stat without
    // requiring a manual page reload.  NOT wired to fetchWindowedStrip — this stat carries
    // no window label and must not change when the window picker is clicked.
    function updateAccountAllTime(data) {
        var portfolio = ((data.meta) || {}).portfolio || {};
        var ps = (data.portfolio_strip) || {};
        var value = portfolio.account_all_time_cr != null
            ? portfolio.account_all_time_cr
            : (ps.account_all_time_cr != null ? ps.account_all_time_cr : null);
        if (value === null) return;

        var el = document.querySelector('[data-testid="account-all-time-cr"]');
        if (!el) {
            // Cold-start: SSR omitted the block because the account cache was cold.
            // Create the container + span and insert it after the guard-alpha section
            // (matching templates/index.html:810-813 markup).
            var container = document.querySelector('.account-all-time-stat');
            if (!container) {
                var guardSection = document.getElementById('guard-alpha-headline');
                var parent = guardSection ? guardSection.parentNode : null;
                if (!parent) return;
                container = document.createElement('div');
                container.className = 'account-all-time-stat';
                container.style.cssText = 'margin-top:6px;font-size:11px;color:var(--studio-ink-dim);';
                container.innerHTML =
                    '<span class="account-all-time-label">Account &middot; all-time</span>'
                    + ' <span data-testid="account-all-time-cr" class="account-all-time-value"></span>';
                parent.insertAdjacentHTML('beforeend', container.outerHTML);
            }
            el = document.querySelector('[data-testid="account-all-time-cr"]');
            if (!el) return;
        }

        el.textContent = fmtPct(value);
        el.className = 'account-all-time-value ' + (value >= 0 ? 'pos' : 'neg');
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
            // Update section count badges and hero "data as of" time.
            function () { updateSectionMeta(data); },
            // Account · all-time CR: fixed stat, not windowed — must fire on every poll
            // so cold-start (SSR omitted the element) resolves without a manual reload.
            function () { updateAccountAllTime(data); },
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
    // Poll loop + staleness tracking
    // ---------------------------------------------------------------------------

    // AC-8: track the last time a successful /api/state response arrived so the badge
    // can reflect a connection-lost state independently of the poll cadence.
    var lastSuccessfulPollAt = 0;

    // AC-8: surface a visible "connection lost" state when the poll/SSE fails.
    // Flips the engine badge and the data-as-of element so the operator knows
    // the displayed numbers are frozen — not silently stale.
    function showConnectionLost() {
        // Target real ids from _chrome.html:51-53 and index.html:846.
        var dot = document.getElementById('engine-status-dot');
        var label = document.getElementById('engine-status-label');
        if (dot) { dot.style.background = 'var(--studio-neg, #e33)'; }
        if (label) { label.textContent = 'Connection Lost'; label.style.color = 'var(--studio-neg, #e33)'; }
        var dataAsOf = document.getElementById('hero-data-as-of');
        if (dataAsOf) { dataAsOf.textContent = 'connection lost'; }
    }

    function loadState() {
        fetch('/api/state')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                lastSuccessfulPollAt = Date.now();
                updateDashboard(data);
            })
            .catch(function (err) {
                showConnectionLost();
                console.error('state load failed', err);
            });
    }

    // Re-window the hero headline VALUE + the three vs-rows for a window token.
    // The windowed strip dict shares portfolio_strip's shape, so we reuse
    // renderGuardAlpha + updateComparisonRows by wrapping it as a poll-style payload.
    // Defined at IIFE scope (not inside DOMContentLoaded) so renderGuardAlpha can call
    // it as a frozen-path fallback — renderGuardAlpha is also at IIFE scope and the
    // DOMContentLoaded callback would be invisible from there.
    function fetchWindowedStrip(token) {
        fetch('/api/strip/' + token)
            .then(function (r) { return r.json(); })
            .then(function (strip) {
                if (!strip || strip.error) return;
                var wrapped = {
                    portfolio_strip: strip,
                    meta: { portfolio: { vol_bot: strip.vol_bot, vol_held: strip.vol_held } }
                };
                renderGuardAlpha(wrapped, true); // _fromStrip=true — prevents re-entry when strip guard_alpha is null
                updateComparisonRows(wrapped);
            })
            .catch(function (err) { console.error('windowed strip load failed', err); });
    }

    // AC-1: fetch /api/guard-alpha-summary and populate the dollar-saved panel.
    // Called on page load AND on each SSE cycle-complete event (Finding 8) — no
    // continuous polling; the panel updates when the engine actually cycles.
    // Uses dollar-saved-headline — NOT guard-alpha-headline (that carries the windowed
    // % guard alpha from /api/strip/<window> and must not be clobbered).
    function fetchGuardAlphaSummary() {
        fetch('/api/guard-alpha-summary')
            .then(function (response) {
                if (!response.ok) return;
                return response.json();
            })
            .then(function (data) {
                if (!data) return;
                var headlineEl = document.getElementById('dollar-saved-headline');
                var countEl = document.getElementById('guard-event-count');
                var labelEl = document.getElementById('dollar-saved-basis-label');
                if (data.guard_event_count === 0) {
                    if (headlineEl) headlineEl.textContent = 'No guard events yet';
                    if (countEl) countEl.textContent = '0';
                    if (labelEl) labelEl.textContent = data.basis_label || '';
                } else {
                    // Finding 9: sign drives format ("-$N.NN") and color — same idiom
                    // as the guard-alpha-headline renderer. Never hardcoded green.
                    var saved = data.cumulative_saved_dollars;
                    if (headlineEl) {
                        headlineEl.textContent = (saved < 0 ? '-$' : '$') + Math.abs(saved).toFixed(2);
                        headlineEl.style.color = saved >= 0 ? cs('--studio-pos') : cs('--studio-neg');
                    }
                    if (countEl) countEl.textContent = data.guard_event_count;
                    if (labelEl) labelEl.textContent = data.basis_label || '';
                }
            })
            .catch(function (err) { console.error('guard-alpha-summary load failed', err); });
    }

    document.addEventListener('DOMContentLoaded', function () {
        // Poll floor is 15 s — matches the engine's minute cadence (see POLL_INTERVAL_MS).
        loadState();
        setInterval(loadState, POLL_INTERVAL_MS);

        // AC-3: SSE event-driven update — primary path; poll (above) is the resilience fallback.
        if (typeof EventSource !== 'undefined') {
            var _es = new EventSource('/api/events');
            // Finding 8: the $-saved panel rides the same event — a page left open
            // across the EOD post-mortem write (or a new guard event) updates live.
            _es.addEventListener('cycle-complete', function () { loadState(); fetchGuardAlphaSummary(); });
            _es.onerror = function () { /* silent — poll fallback handles reconnect */ };
        }

        fetchGuardAlphaSummary();

        // AC-3: each picker button maps to a lowercase URL window token. The SAME
        // token drives BOTH /api/strip/<token> (re-windows the hero VALUE + vs-rows
        // + label) and /api/hero-chart/<token> (re-windows the two chart lines).
        // "window-all" is the NEW All-Time option (token "all" = lifetime cross-epoch).
        var windowTokenMap = {
            'window-30d':  '30d',
            'window-60d':  '60d',
            'window-90d':  '90d',
            'window-125d': '125d',
            'window-ytd':  'ytd',
            'window-1y':   '1y',
            'window-all':  'all'
        };
        var winLabelMap = {
            'window-30d': '30d', 'window-60d': '60d', 'window-90d': '90d',
            'window-125d': '125d', 'window-ytd': 'YTD', 'window-1y': '1Y',
            'window-all': 'All Time'
        };

        Object.keys(windowTokenMap).forEach(function (testid) {
            var btn = document.querySelector('[data-testid="' + testid + '"]');
            if (!btn) return;
            btn.addEventListener('click', function () {
                document.querySelectorAll('[data-testid="window-selector"] button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                var token = windowTokenMap[testid];
                _heroWindow = token;
                var winLabel = document.getElementById('guard-alpha-window-label');
                if (winLabel) winLabel.textContent = winLabelMap[testid] || btn.textContent;
                // The picker drives EVERY metric: re-window the chart lines (fetch +
                // _cumChart update) AND the headline VALUE + vs-rows (windowed strip).
                fetch('/api/hero-chart/' + token)
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
                // Re-window the headline guard-alpha VALUE + the three vs-rows so the
                // label always matches the actual window (kills the F1 mislabel).
                fetchWindowedStrip(token);
            });
        });
    });

    // ---------------------------------------------------------------------------
    // Managed Sleeves panel (AC-16) — arm-to-paper / disarm controls.
    // Reuses the module's _csrfToken (fetched once on DOMContentLoaded above).
    // Arm-live and envelope-widen have their own multi-gate ceremonies (AC-3/
    // AC-14) and are deliberately NOT wired to a one-click control here — they
    // need a dedicated confirm-phrase modal, a follow-up UI pass, not a bare
    // button that could fat-finger a live-trading arm.
    // ---------------------------------------------------------------------------

    function armSleeveRuleToPaper(sleeveId, ruleId, btn) {
        if (!window.confirm('Arm this rule to PAPER? It will start placing real paper-account orders.')) return;
        btn.disabled = true;
        fetch('/api/sleeves/' + sleeveId + '/rules/' + ruleId + '/arm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
            body: JSON.stringify({ target_mode: 'PAPER' })
        })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok) {
                window.alert('Arm rejected: ' + ((res.body && res.body.message) || 'unknown error'));
                btn.disabled = false;
                return;
            }
            location.reload();
        })
        .catch(function (err) {
            console.error('armSleeveRuleToPaper failed', err);
            btn.disabled = false;
        });
    }
    window.armSleeveRuleToPaper = armSleeveRuleToPaper;

    function disarmSleeve(sleeveId, btn) {
        if (!window.confirm('Disarm this sleeve? All autonomy stops immediately; re-arming requires the ceremony again.')) return;
        btn.disabled = true;
        fetch('/api/sleeves/' + sleeveId + '/disarm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' }
        })
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            location.reload();
        })
        .catch(function (err) {
            console.error('disarmSleeve failed', err);
            btn.disabled = false;
        });
    }
    window.disarmSleeve = disarmSleeve;

    function deleteSleeve(sleeveId, btn) {
        if (!window.confirm('Delete this sleeve? Refused unless it is flat (no open position) — delete never liquidates.')) return;
        btn.disabled = true;
        fetch('/api/sleeves/' + sleeveId + '/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' }
        })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok) {
                window.alert('Delete rejected: ' + ((res.body && res.body.message) || 'unknown error'));
                btn.disabled = false;
                return;
            }
            location.reload();
        })
        .catch(function (err) {
            console.error('deleteSleeve failed', err);
            btn.disabled = false;
        });
    }
    window.deleteSleeve = deleteSleeve;
})();
