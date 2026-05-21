/**
 * performance.js — DV2 Performance tab client logic.
 *
 * Read-only operator surface:
 *   - Fetches /api/performance and /api/performance/symphonies.
 *   - Renders cumulative-return curves (live vs AlphaBot-exited) on a
 *     Chart.js canvas.
 *   - Renders a 7-metric quantstats table side-by-side with a delta column.
 *
 * Auto-refresh floor is intentionally 60s (well above the 15s floor mandated
 * for live-cycle dashboards) because post-mortem snapshots only land once a
 * day; polling faster would just burn CPU re-parsing the same JSON.
 */
(function () {
    'use strict';

    var chartInstance = null;

    var METRIC_LABELS = [
        ['total_return', 'Total Return', 'pct'],
        ['annualized_return', 'Annualized Return (CAGR)', 'pct'],
        ['sharpe', 'Sharpe', 'num'],
        ['sortino', 'Sortino', 'num'],
        ['max_drawdown', 'Max Drawdown', 'pct'],
        ['calmar', 'Calmar', 'num'],
        ['win_rate', 'Win Rate', 'pct'],
    ];

    function fmt(value, kind) {
        if (value === null || value === undefined) return '—';
        if (typeof value !== 'number' || !isFinite(value)) return '—';
        if (kind === 'pct') return (value * 100).toFixed(2) + '%';
        return value.toFixed(3);
    }

    function fmtDelta(live, shadow, kind) {
        if (live === null || shadow === null || live === undefined || shadow === undefined) return '—';
        if (typeof live !== 'number' || typeof shadow !== 'number') return '—';
        if (!isFinite(live) || !isFinite(shadow)) return '—';
        var delta = shadow - live;
        var formatted = fmt(delta, kind);
        if (delta > 0) return '+' + formatted;
        return formatted;
    }

    function deltaClass(live, shadow) {
        if (live === null || shadow === null || live === undefined || shadow === undefined) return 'text-slate-500';
        if (typeof live !== 'number' || typeof shadow !== 'number') return 'text-slate-500';
        if (!isFinite(live) || !isFinite(shadow)) return 'text-slate-500';
        var delta = shadow - live;
        if (delta > 0) return 'text-emerald-400';
        if (delta < 0) return 'text-rose-400';
        return 'text-slate-300';
    }

    function cumulative(returns) {
        // Compounding cumulative return series for the chart.
        var out = [];
        var acc = 1.0;
        for (var i = 0; i < returns.length; i++) {
            acc = acc * (1.0 + returns[i]);
            out.push((acc - 1.0) * 100.0); // percent
        }
        return out;
    }

    function renderChart(payload) {
        var canvas = document.getElementById('returns-chart');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');

        var data = {
            labels: payload.dates,
            datasets: [
                {
                    label: 'Live (held to close)',
                    data: cumulative(payload.live_returns),
                    borderColor: '#60a5fa',
                    backgroundColor: 'rgba(96, 165, 250, 0.1)',
                    fill: false,
                    tension: 0.15,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: 'AlphaBot-Exited (shadow)',
                    data: cumulative(payload.shadow_returns),
                    borderColor: '#34d399',
                    backgroundColor: 'rgba(52, 211, 153, 0.1)',
                    fill: false,
                    tension: 0.15,
                    pointRadius: 0,
                    borderWidth: 2,
                },
            ],
        };

        var options = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: { color: '#cbd5e1' },
                },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + '%';
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#94a3b8', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
                    grid: { color: 'rgba(148, 163, 184, 0.1)' },
                },
                y: {
                    ticks: {
                        color: '#94a3b8',
                        callback: function (v) { return v.toFixed(1) + '%'; },
                    },
                    grid: { color: 'rgba(148, 163, 184, 0.1)' },
                },
            },
        };

        if (chartInstance) {
            chartInstance.data = data;
            chartInstance.options = options;
            chartInstance.update();
        } else {
            chartInstance = new Chart(ctx, { type: 'line', data: data, options: options });
        }
    }

    function renderMetrics(payload) {
        var tbody = document.getElementById('metrics-tbody');
        if (!tbody) return;
        var live = payload.live_metrics || {};
        var shadow = payload.shadow_metrics || {};
        var rows = METRIC_LABELS.map(function (spec) {
            var key = spec[0], label = spec[1], kind = spec[2];
            var liveVal = live[key];
            var shadowVal = shadow[key];
            var dc = deltaClass(liveVal, shadowVal);
            return (
                '<tr class="border-b border-slate-700/50">' +
                '<td class="py-2 px-3 text-slate-400 font-sans">' + label + '</td>' +
                '<td class="py-2 px-3 text-right">' + fmt(liveVal, kind) + '</td>' +
                '<td class="py-2 px-3 text-right">' + fmt(shadowVal, kind) + '</td>' +
                '<td class="py-2 px-3 text-right ' + dc + '">' + fmtDelta(liveVal, shadowVal, kind) + '</td>' +
                '</tr>'
            );
        });
        tbody.innerHTML = rows.join('');
    }

    function renderBanner(payload) {
        var banner = document.getElementById('insufficient-banner');
        if (!banner) return;
        if (payload.insufficient_history) {
            banner.classList.remove('hidden');
        } else {
            banner.classList.add('hidden');
        }
    }

    function renderObsCount(payload) {
        var el = document.getElementById('observation-count');
        if (!el) return;
        el.textContent = String(payload.observation_count);
    }

    function currentParams() {
        var scope = document.getElementById('scope-toggle').value;
        var days = document.getElementById('days-picker').value;
        var symId = document.getElementById('symphony-picker').value;
        var qs = 'scope=' + encodeURIComponent(scope) + '&days=' + encodeURIComponent(days);
        if (scope === 'symphony' && symId) {
            qs += '&symphony_id=' + encodeURIComponent(symId);
        }
        return { scope: scope, qs: qs, symphony_id: symId };
    }

    function refresh() {
        var params = currentParams();
        if (params.scope === 'symphony' && !params.symphony_id) {
            // Wait until a symphony is picked.
            return;
        }
        fetch('/api/performance?' + params.qs)
            .then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function (payload) {
                renderObsCount(payload);
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
                        opt.value = sym;
                        opt.textContent = sym;
                        if (idx === 0) opt.selected = true;
                        picker.appendChild(opt);
                    });
                }
            })
            .catch(function (err) {
                console.error('failed to load symphonies', err);
            });
    }

    function wireUI() {
        var scopeToggle = document.getElementById('scope-toggle');
        var symphonyWrapper = document.getElementById('symphony-picker-wrapper');
        var symphonyPicker = document.getElementById('symphony-picker');
        var daysPicker = document.getElementById('days-picker');

        function syncSymphonyVisibility() {
            if (scopeToggle.value === 'symphony') {
                symphonyWrapper.style.display = '';
            } else {
                symphonyWrapper.style.display = 'none';
            }
        }

        scopeToggle.addEventListener('change', function () {
            syncSymphonyVisibility();
            refresh();
        });
        symphonyPicker.addEventListener('change', refresh);
        daysPicker.addEventListener('change', refresh);

        syncSymphonyVisibility();
    }

    document.addEventListener('DOMContentLoaded', function () {
        wireUI();
        loadSymphonies().then(refresh);

        // Post-mortems land once a day; 60s polling is well above the
        // documented 15s floor and avoids redundant JSON re-parsing.
        setInterval(refresh, 60000);
    });
})();
