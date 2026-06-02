/**
 * ai_advisor.js — Studio AI Advisor client logic.
 *
 * All colors use CSS custom properties (--studio-*) resolved at runtime so
 * theme/accent changes propagate without a reload.
 * No Tailwind class names in this file.
 */
(function () {
    'use strict';

    /** CSRF token fetched from GET /api/csrf-token on page load (AC-1). */
    var _csrfToken = null;
    document.addEventListener('DOMContentLoaded', function () {
        fetch('/api/csrf-token')
            .then(function (r) { return r.json(); })
            .then(function (b) { _csrfToken = b.csrf_token || null; })
            .catch(function () { /* csrf token unavailable — POSTs will 403 */ });
    });

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    function escHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function cssVar(name) {
        return 'var(' + name + ')';
    }

    function fmtSharpe(val) {
        if (val === null || val === undefined) return 'N/A';
        return Number(val).toFixed(4);
    }

    function fmtDelta(delta) {
        if (delta === null || delta === undefined || isNaN(delta)) return '';
        var sign = delta >= 0 ? '+' : '';
        return sign + Number(delta).toFixed(3);
    }

    // Module-level suggestion state — survives any poll cycle.
    var _activeSuggestions = null;
    var _activeSymphonyId = null;

    // ---------------------------------------------------------------------------
    // Summary chips
    // ---------------------------------------------------------------------------

    function updateChips(suggestions) {
        var total = suggestions.length;
        var passed = suggestions.filter(function (s) { return s.oos_status === 'passed'; }).length;
        var rejected = suggestions.filter(function (s) { return s.oos_status === 'rejected'; }).length;
        var chipTotal = document.getElementById('chip-total');
        var chipPassed = document.getElementById('chip-passed');
        var chipRejected = document.getElementById('chip-rejected');
        if (chipTotal) chipTotal.textContent = total + ' suggestion' + (total !== 1 ? 's' : '');
        if (chipPassed) chipPassed.textContent = passed + ' OOS passed';
        if (chipRejected) chipRejected.textContent = rejected + ' OOS rejected';
    }

    // ---------------------------------------------------------------------------
    // Render suggestion cards
    // ---------------------------------------------------------------------------

    function oosStatusColor(status) {
        if (status === 'passed') return cssVar('--studio-pos');
        if (status === 'rejected') return cssVar('--studio-neg');
        return cssVar('--studio-ink-dim');
    }

    function confidenceBadgeStyle(confidence) {
        if (confidence === 'high') return 'color:' + cssVar('--studio-pos') + ';font-weight:700;';
        if (confidence === 'low') return 'color:' + cssVar('--studio-ink-dim') + ';';
        return 'color:' + cssVar('--studio-ink') + ';';
    }

    function renderSuggestions(suggestions, symphonyId) {
        var container = document.getElementById('suggestions-container');
        updateChips(suggestions);

        if (suggestions.length === 0) {
            container.innerHTML =
                '<div style="background:' + cssVar('--studio-surface') + ';' +
                'border:1px solid ' + cssVar('--studio-border') + ';' +
                'border-radius:1rem;padding:1.5rem;' +
                'color:' + cssVar('--studio-ink-dim') + ';font-size:0.875rem;grid-column:1/-1;">' +
                'No suggestions — the current config looks sound.</div>';
            return;
        }

        container.innerHTML = suggestions.map(function (s, i) {
            var impactBefore = s.impact && typeof s.impact.before === 'number' ? s.impact.before : null;
            var impactAfter = s.impact && typeof s.impact.after === 'number' ? s.impact.after : null;
            var impactDelta = s.impact && typeof s.impact.delta === 'number'
                ? s.impact.delta
                : (impactBefore !== null && impactAfter !== null ? impactAfter - impactBefore : null);
            var impactMetric = s.impact && s.impact.metric ? s.impact.metric : 'sharpe';
            var impactText = impactDelta !== null
                ? impactMetric + ' ' + fmtDelta(impactDelta)
                : '';
            var isOosRejected = s.oos_status === 'rejected';
            var oosColor = oosStatusColor(s.oos_status || 'pending');
            var oosLabel = escHtml(s.oos_status || 'pending');
            if (s.oos_reason) oosLabel += ' — ' + escHtml(s.oos_reason);
            var suffBadge = (s.data_sufficiency && s.data_sufficiency !== 'sufficient')
                ? '<span style="font-size:0.6875rem;color:' + cssVar('--studio-warn') + ';margin-left:0.25rem;">' +
                  escHtml(s.data_sufficiency) + ' data</span>'
                : '';

            // Confidence ring SVG arc (circumference ~56.5 for r=9)
            var CIRC = 56.5;
            var confPct = s.confidence === 'high' ? 1.0 : s.confidence === 'medium' ? 0.6 : 0.3;
            var dashLen = (confPct * CIRC).toFixed(1);
            var ringColor = s.confidence === 'high' ? cssVar('--studio-pos')
                : s.confidence === 'low' ? cssVar('--studio-neg') : cssVar('--studio-accent');
            var confidenceRing =
                '<svg data-testid="confidence-ring" viewBox="0 0 20 20" width="36" height="36" style="flex-shrink:0;">' +
                '<circle cx="10" cy="10" r="9" fill="none" stroke="' + cssVar('--studio-border') + '" stroke-width="2"></circle>' +
                '<circle cx="10" cy="10" r="9" fill="none" stroke="' + ringColor + '" stroke-width="2"' +
                ' stroke-dasharray="' + dashLen + ' ' + CIRC + '"' +
                ' stroke-dashoffset="' + (CIRC * 0.25).toFixed(1) + '"' +
                ' transform="rotate(-90 10 10)"></circle>' +
                '</svg>';

            // Projected-impact bar
            var impactBarW = impactDelta !== null ? Math.min(Math.abs(impactDelta) * 20, 100).toFixed(1) : '0';
            var impactFill = impactDelta !== null && impactDelta >= 0 ? cssVar('--studio-pos') : cssVar('--studio-neg');
            var projectedImpactBar =
                '<div data-testid="projected-impact-bar" style="margin-bottom:0.5rem;">' +
                (impactBefore !== null
                    ? '<span style="font-size:0.625rem;color:' + cssVar('--studio-ink-dim') + ';">' +
                      escHtml(impactMetric) + ': ' + impactBefore.toFixed(3) + ' → ' +
                      (impactAfter !== null ? impactAfter.toFixed(3) : '?') + '</span><br>'
                    : '') +
                '<svg viewBox="0 0 100 6" preserveAspectRatio="none" width="120" height="6">' +
                '<rect x="0" y="1" width="' + impactBarW + '" height="4" rx="2" fill="' + impactFill + '"></rect>' +
                '</svg>' +
                '</div>';

            // Four-gates verdict badges
            var gates = s.four_gates_verdict || {};
            var GATE_LABELS = ['allowlist', 'risk_direction', 'oos_frozen_eval', 'locked_vars'];
            var gateBadges = GATE_LABELS.map(function (gk) {
                var raw = gates[gk];
                var gc = raw === true || raw === 'pass' ? cssVar('--studio-pos')
                    : raw === false || raw === 'fail' ? cssVar('--studio-neg') : cssVar('--studio-warn');
                var label = raw === true ? 'pass' : raw === false ? 'fail' : (raw != null ? String(raw) : 'unknown');
                return '<span data-testid="gate-badge" style="font-size:0.5625rem;font-weight:700;' +
                    'text-transform:uppercase;letter-spacing:0.06em;padding:0.1rem 0.3rem;' +
                    'border-radius:0.25rem;border:1px solid ' + gc + ';color:' + gc + ';white-space:nowrap;">' +
                    escHtml(gk.replace(/_/g, ' ')) + ': ' + escHtml(label) + '</span>';
            }).join(' ');
            var gatesRow = '<div style="display:flex;flex-wrap:wrap;gap:0.25rem;margin-bottom:0.5rem;">' + gateBadges + '</div>';

            return (
                '<div id="card-' + i + '" style="' +
                'background:' + cssVar('--studio-surface') + ';' +
                'border:1px solid ' + (isOosRejected ? cssVar('--studio-neg') : cssVar('--studio-border')) + ';' +
                'border-radius:1rem;padding:1.25rem 1.5rem;' +
                (isOosRejected ? 'opacity:0.7;' : '') + '">' +

                '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;">' +

                '<div style="flex:1;min-width:0;">' +
                '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">' +
                confidenceRing +
                '<span style="font-size:0.625rem;font-weight:700;text-transform:uppercase;' +
                'letter-spacing:0.1em;color:' + cssVar('--studio-accent') + ';">' +
                escHtml(s.config_key) + '</span>' +
                '<span style="font-size:0.6875rem;color:' + cssVar('--studio-ink-dim') + ';">' +
                escHtml(s.risk_direction) + '</span>' +
                '<span style="font-size:0.6875rem;' + confidenceBadgeStyle(s.confidence) + '">' +
                escHtml(s.confidence) + ' confidence</span>' +
                suffBadge +
                '</div>' +

                gatesRow +
                projectedImpactBar +

                '<div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.625rem;">' +
                '<span style="font-size:0.875rem;color:' + cssVar('--studio-ink-dim') + ';">Current: ' +
                '<code style="color:' + cssVar('--studio-ink') + ';">' + escHtml(String(s.current_value)) + '</code></span>' +
                '<span style="color:' + cssVar('--studio-ink-muted') + ';">&rarr;</span>' +
                '<span style="font-size:0.875rem;color:' + cssVar('--studio-ink-dim') + ';">Suggested: ' +
                '<code style="color:' + cssVar('--studio-pos') + ';">' + escHtml(String(s.suggested_value)) + '</code></span>' +
                (impactText ? '<span style="font-size:0.6875rem;color:' + cssVar('--studio-ink-dim') + ';margin-left:0.5rem;">' + escHtml(impactText) + '</span>' : '') +
                '</div>' +

                '<p style="font-size:0.6875rem;color:' + cssVar('--studio-ink-dim') + ';margin-bottom:0.375rem;">' +
                escHtml(s.rationale) + '</p>' +

                '<span style="font-size:0.6875rem;color:' + oosColor + ';">OOS: ' + oosLabel + '</span>' +
                '</div>' +

                '<div style="display:flex;flex-direction:column;gap:0.5rem;flex-shrink:0;">' +
                (isOosRejected
                    ? '<button disabled ' +
                      'style="padding:0.375rem 1rem;background:' + cssVar('--studio-chip-bg') + ';' +
                      'color:' + cssVar('--studio-ink-dim') + ';border:none;border-radius:0.5rem;' +
                      'font-size:0.6875rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;cursor:not-allowed;">' +
                      'Blocked by OOS gate</button>'
                    : '<button onclick="acceptSuggestion(' + i + ',\'' + escHtml(symphonyId) + '\')" ' +
                      'style="padding:0.375rem 1rem;background:' + cssVar('--studio-pos') + ';' +
                      'color:' + cssVar('--studio-white') + ';border:none;border-radius:0.5rem;' +
                      'font-size:0.6875rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;">' +
                      'Apply suggestion</button>'
                ) +
                '<button onclick="rejectSuggestion(' + i + ',\'' + escHtml(symphonyId) + '\')" ' +
                'style="padding:0.375rem 1rem;background:' + cssVar('--studio-surface-raised') + ';' +
                'color:' + cssVar('--studio-ink-dim') + ';border:1px solid ' + cssVar('--studio-border') + ';' +
                'border-radius:0.5rem;font-size:0.6875rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;">' +
                'Dismiss</button>' +
                '</div>' +

                '</div>' +

                (function () {
                    // "Chat about this" button — mirrors the pattern in
                    // ai_advisor_asset_swaps.js lines 246-260.
                    // Artifact JSON scoped to this config suggestion.
                    var cfgArtifact = {
                        artifactId:      'config-suggestion-' + i,
                        artifactType:    'config_suggestion',
                        title:           'Explain: ' + (s.config_key || ''),
                        contextLabel:    'Config Suggestion',
                        objective:       s.rationale || '',
                        gateDecision:    s.oos_status || 'pending',
                        keyStat:         s.confidence || '',
                        artifactContext: {
                            config_key:      s.config_key,
                            current_value:   s.current_value,
                            suggested_value: s.suggested_value,
                            rationale:       s.rationale,
                            oos_status:      s.oos_status,
                            four_gates_verdict: s.four_gates_verdict,
                        },
                    };
                    var cfgArtifactJson = JSON.stringify(cfgArtifact);
                    return (
                        '<div class="card-actions" style="margin-top:0.75rem;padding-top:0.625rem;' +
                        'border-top:1px solid var(--studio-border);display:flex;flex-wrap:wrap;gap:0.5rem;">' +
                        '<button class="chat-about-btn" data-testid="chat-about-this-btn"' +
                        ' data-artifact-json=\'' + escHtml(cfgArtifactJson) + '\'' +
                        ' style="padding:0.375rem 0.875rem;background:var(--studio-accent);color:var(--studio-white);' +
                        'border:none;border-radius:0.5rem;font-size:0.8125rem;font-weight:600;cursor:pointer;' +
                        'white-space:nowrap;"' +
                        ' onclick="(function(e){var d=e.currentTarget.dataset.artifactJson;' +
                        'try{if(typeof openChatPanel===\'function\'){openChatPanel(JSON.parse(d));}' +
                        'else{window.location.href=\'/ai-advisor/chat\';}}' +
                        'catch(ex){window.location.href=\'/ai-advisor/chat\';}})(event)">' +
                        'Chat about this' +
                        '</button>' +
                        '</div>'
                    );
                }()) +

                '</div>'
            );
        }).join('');

        container._suggestions = suggestions;
        container._symphonyId = symphonyId;
        _activeSuggestions = suggestions;
        _activeSymphonyId = symphonyId;
    }

    // ---------------------------------------------------------------------------
    // getSuggestions
    // ---------------------------------------------------------------------------

    window.getSuggestions = function getSuggestions() {
        var selectEl = document.getElementById('symphony-id-input');
        var symphonyId = selectEl ? selectEl.value.trim() : '';
        var errorEl = document.getElementById('advisor-error');
        var container = document.getElementById('suggestions-container');
        var btn = document.getElementById('get-suggestions-btn');

        errorEl.style.display = 'none';
        errorEl.textContent = '';
        container.innerHTML =
            '<p style="color:' + cssVar('--studio-ink-dim') + ';font-size:0.875rem;grid-column:1/-1;">Loading suggestions…</p>';
        btn.disabled = true;
        btn.textContent = 'Loading…';

        fetch('/ai-advisor/suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
            body: JSON.stringify({ symphony_id: symphonyId }),
        })
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                if (body.error) {
                    container.innerHTML = '';
                    errorEl.style.display = 'block';
                    errorEl.textContent = 'Advisor unavailable: ' + body.error;
                    return;
                }
                renderSuggestions(body.suggestions || [], symphonyId);
            })
            .catch(function (err) {
                container.innerHTML = '';
                errorEl.style.display = 'block';
                errorEl.textContent = 'Request failed: ' + err.message;
            })
            .finally(function () {
                // B-14: only re-enable if a symphony is still selected.
                var sel = document.getElementById('symphony-id-input');
                btn.disabled = !(sel && sel.value);
                btn.textContent = 'Run Claude advisor';
            });
    };

    // ---------------------------------------------------------------------------
    // Accept / Reject
    // ---------------------------------------------------------------------------

    window.acceptSuggestion = function acceptSuggestion(index, symphonyId) {
        var container = document.getElementById('suggestions-container');
        var suggestion = container._suggestions[index];
        var card = document.getElementById('card-' + index);

        card.style.opacity = '0.5';
        fetch('/ai-advisor/accept', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
            body: JSON.stringify({ symphony_id: symphonyId, suggestion: suggestion }),
        })
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                if (body.status === 'accepted') {
                    card.innerHTML =
                        '<p style="color:' + cssVar('--studio-pos') + ';font-size:0.875rem;font-weight:700;">' +
                        'Accepted — config updated.</p>';
                } else {
                    card.style.opacity = '1';
                    alert('Rejected by C2 gate: ' + (body.error || body.status));
                }
            })
            .catch(function (err) {
                card.style.opacity = '1';
                alert('Request failed: ' + err.message);
            });
    };

    window.rejectSuggestion = function rejectSuggestion(index, symphonyId) {
        var container = document.getElementById('suggestions-container');
        var suggestion = container._suggestions[index];
        var card = document.getElementById('card-' + index);

        card.style.opacity = '0.5';
        fetch('/ai-advisor/reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
            body: JSON.stringify({ symphony_id: symphonyId, suggestion: suggestion }),
        })
            .then(function () {
                card.innerHTML =
                    '<p style="color:' + cssVar('--studio-ink-dim') + ';font-size:0.875rem;">Rejected.</p>';
            })
            .catch(function (err) {
                card.style.opacity = '1';
                alert('Request failed: ' + err.message);
            });
    };

    // ---------------------------------------------------------------------------
    // Autotune sparkline
    // ---------------------------------------------------------------------------

    var _sparkChart = null;

    function renderAutotuneSparkline(rows) {
        var canvas = document.getElementById('autotune-sparkline-canvas');
        if (!canvas || typeof Chart === 'undefined') return;

        var sharpeValues = rows
            .slice()
            .reverse()
            .map(function (r) { return r.naive_sharpe !== null && r.naive_sharpe !== undefined ? Number(r.naive_sharpe) : null; })
            .filter(function (v) { return v !== null; });

        if (!sharpeValues.length) return;

        var cs = getComputedStyle(document.documentElement);
        // C-15: fall back to neutral token values — no bare hex.
        var accentColor = cs.getPropertyValue('--studio-accent').trim() || cs.getPropertyValue('--studio-swatch-1').trim();
        var borderColor = cs.getPropertyValue('--studio-rule').trim() || cs.getPropertyValue('--studio-border').trim();

        if (_sparkChart) {
            _sparkChart.destroy();
            _sparkChart = null;
        }

        _sparkChart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: sharpeValues.map(function (_, i) { return i + 1; }),
                datasets: [{
                    data: sharpeValues,
                    borderColor: accentColor,
                    borderWidth: 1.5,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: false,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: {
                    x: { display: false },
                    y: {
                        display: true,
                        grid: { color: borderColor, lineWidth: 0.5 },
                        ticks: { font: { size: 8 }, color: borderColor, maxTicksLimit: 3 },
                        border: { display: false },
                    },
                },
            },
        });
    }

    // ---------------------------------------------------------------------------
    // Autotune runs
    // ---------------------------------------------------------------------------

    // B-13: map raw enum values to human-readable labels.
    var DECISION_LABELS = {
        'apply':    'Apply',
        'reject':   'Reject',
        'fallback': 'Fallback',
        'hold':     'Hold',
        'skip':     'Skip',
        'pending':  'Pending',
    };

    function decisionLabel(decision) {
        return DECISION_LABELS[decision] || decision;
    }

    function decisionPillColor(decision) {
        if (decision === 'apply') return cssVar('--studio-pos');
        if (decision === 'reject') return cssVar('--studio-neg');
        // V-25: fallback/hold/pending/unknown all use --studio-warn token
        // so saturation stays consistent in light + dark themes.
        return cssVar('--studio-warn');
    }

    function frozenEvalColor(verdict) {
        if (verdict === 'passed') return cssVar('--studio-pos');
        if (verdict === 'failed') return cssVar('--studio-neg');
        return cssVar('--studio-warn');
    }

    function loadRecentRuns() {
        var list = document.getElementById('autotune-runs-list');
        fetch('/api/autotune-runs')
            .then(function (resp) { return resp.json(); })
            .then(function (rows) {
                if (!rows.length) {
                    list.innerHTML =
                        '<div style="text-align:center;padding:1rem;' +
                        'color:' + cssVar('--studio-ink-dim') + ';font-size:0.875rem;">No tuning runs recorded yet.</div>';
                    return;
                }
                renderAutotuneSparkline(rows);
                // C-16: render card list with all per-run fields per advisor.jsx.
                list.innerHTML = rows.map(function (r) {
                    var decision = r.baseline_decision || '';
                    var pillColor = decisionPillColor(decision);
                    // B-13: human-readable label instead of raw enum.
                    var pillLabel = decisionLabel(decision);
                    var frozenVerdict = r.frozen_eval_verdict || '';
                    var frozenColor = frozenEvalColor(frozenVerdict);
                    var symLabel = escHtml(r.symphony_name || r.symphony_id || '');
                    // V-23: timestamp muted, Sortino / selection t-stat bold mono.
                    // selection_tstat is the Harvey & Liu haircut winner's t-statistic.
                    var selTstat = r.selection_tstat;
                    return (
                        '<div class="autotune-run-card" data-testid="autotune-run-row">' +
                        '<div class="autotune-run-top">' +
                        '<span class="autotune-run-name" title="' + symLabel + '">' + symLabel + '</span>' +
                        '<span class="decision-pill" style="color:' + pillColor + ';border-color:' + pillColor + ';background:' + pillColor + '14;">' +
                        escHtml(pillLabel) + '</span>' +
                        '</div>' +
                        '<div class="autotune-run-meta">' +
                        '<span style="color:' + cssVar('--studio-ink-dim') + ';">' + escHtml(r.run_timestamp || '') + '</span>' +
                        '<span>Sortino <span class="mono-bold">' + escHtml(fmtSharpe(r.naive_sharpe)) + '</span>' +
                        ' · Sel t-stat <span class="mono-bold">' + escHtml(fmtSharpe(selTstat)) + '</span>' +
                        '</span>' +
                        '</div>' +
                        // V-23: FROZEN-EVAL as colored pill.
                        '<div class="autotune-run-verdict">Frozen-eval ' +
                        '<span class="frozen-eval-pill" style="color:' + frozenColor + ';border-color:' + frozenColor + ';background:' + frozenColor + '14;">' +
                        escHtml(frozenVerdict) + '</span></div>' +
                        '</div>'
                    );
                }).join('');
            })
            .catch(function (err) {
                list.innerHTML =
                    '<div style="text-align:center;padding:1rem;' +
                    'color:' + cssVar('--studio-neg') + ';">Failed to load runs: ' + escHtml(err.message) + '</div>';
            });
    }

    // ---------------------------------------------------------------------------
    // Symphony select population
    // ---------------------------------------------------------------------------

    function loadSymphonies() {
        var selectEl = document.getElementById('symphony-id-input');
        if (!selectEl) return;
        fetch('/api/performance/symphonies')
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                var syms = (body && body.symphonies) || [];
                var prevVal = selectEl.value;
                selectEl.innerHTML = '<option value="">Select symphony…</option>';
                syms.forEach(function (sym) {
                    var opt = document.createElement('option');
                    opt.value = sym;
                    opt.textContent = sym;
                    selectEl.appendChild(opt);
                });
                if (prevVal) selectEl.value = prevVal;
            })
            .catch(function () {});
    }

    document.addEventListener('DOMContentLoaded', function () {
        loadRecentRuns();
        loadSymphonies();
        // 15 s floor — faster than the engine's minute cadence makes no sense.
        setInterval(loadRecentRuns, 15000);

        var selectEl = document.getElementById('symphony-id-input');
        var runBtn = document.getElementById('get-suggestions-btn');
        if (selectEl && runBtn) {
            // B-14: enable Run button only when a symphony is selected.
            function syncRunBtn() {
                runBtn.disabled = !selectEl.value;
            }
            // C-11: wire change event so suggestions auto-fetch on pick.
            selectEl.addEventListener('change', function () {
                syncRunBtn();
                if (selectEl.value) {
                    getSuggestions();
                }
            });
            syncRunBtn();
        }
    });
})();
