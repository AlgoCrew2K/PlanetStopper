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

    function renderSuggestions(suggestions, symphonyId, body) {
        var container = document.getElementById('suggestions-container');
        updateChips(suggestions);

        // AC-3: surface the nightly lens-cache staleness stamp in the suggest panel.
        // Placed before branching so it updates on both empty-state and suggestion paths.
        // Uses textContent (never innerHTML) — the timestamp is server-generated ISO UTC
        // but textContent ensures no XSS risk regardless of future content changes.
        var lensAsOfEl = document.getElementById('advisor-lens-as-of');
        if (lensAsOfEl) {
            var lensAsOf = body && body.lens_data_as_of;
            if (lensAsOf) {
                var staleTag = body.lens_data_stale ? ' (stale)' : '';
                lensAsOfEl.textContent = 'Market context as of ' + lensAsOf + staleTag;
                lensAsOfEl.style.display = '';
            } else {
                lensAsOfEl.textContent = '';
                lensAsOfEl.style.display = 'none';
            }
        }

        if (suggestions.length === 0) {
            // AC1/AC4: render the per-symphony assessment so the empty-state
            // box shows real context (why no edit is suggested) rather than a
            // generic message identical for every symphony.
            // body.assessment carries baseline_decision, oos_alpha,
            // fallback_oos_alpha, default_oos_alpha, and summary.
            var assessment = (typeof body !== 'undefined' && body && body.assessment) ? body.assessment : null;
            var summaryHtml = assessment && assessment.summary
                ? escHtml(assessment.summary)
                : 'No suggestions — the advisor did not find a well-supported edit at this time.';
            var decisionHtml = assessment && assessment.baseline_decision
                ? '<div style="font-size:0.75rem;color:' + cssVar('--studio-ink-dim') + ';margin-top:0.5rem;">' +
                  'Baseline decision: <strong>' + escHtml(String(assessment.baseline_decision)) + '</strong></div>'
                : '';
            var oosHtml = '';
            if (assessment) {
                var oosVal = assessment.oos_alpha !== null && assessment.oos_alpha !== undefined
                    ? fmtSharpe(assessment.oos_alpha)
                    : 'N/A';
                var fallbackVal = assessment.fallback_oos_alpha !== null && assessment.fallback_oos_alpha !== undefined
                    ? fmtSharpe(assessment.fallback_oos_alpha)
                    : 'N/A';
                oosHtml = '<div style="font-size:0.75rem;color:' + cssVar('--studio-ink-dim') + ';margin-top:0.25rem;">' +
                    'OOS alpha (cumulative sum across triggered days): <code>' + escHtml(oosVal) + '</code>' +
                    ' &nbsp;|&nbsp; Fallback OOS: <code>' + escHtml(fallbackVal) + '</code>' +
                    '</div>';
            }
            container.innerHTML =
                '<div style="background:' + cssVar('--studio-surface') + ';' +
                'border:1px solid ' + cssVar('--studio-border') + ';' +
                'border-radius:1rem;padding:1.5rem;' +
                'color:' + cssVar('--studio-ink-dim') + ';font-size:0.875rem;grid-column:1/-1;">' +
                summaryHtml + decisionHtml + oosHtml +
                '</div>';
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
                '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">' +
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
                renderSuggestions(body.suggestions || [], symphonyId, body);
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
        if (!list) return;
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
                    // F-023 / DE-PERFVIEW-ID-MISMATCH correction: the
                    // accept/suggest flow's canonical key is the display
                    // NAME, not the Composer hash -- POST /ai-advisor/accept
                    // reads database.get_symphony_strategy/save_symphony_
                    // strategy directly (normalize_name(display_name)-keyed
                    // only, no hash resolution). The endpoint now returns
                    // {id,name} objects (not bare strings), so this must
                    // read sym.name explicitly -- but it stays the option
                    // value, unlike performance.js's picker (which genuinely
                    // needs sym.id since it feeds a hash-keyed query).
                    opt.value = sym.name;
                    opt.textContent = sym.name;
                    selectEl.appendChild(opt);
                });
                if (prevVal) selectEl.value = prevVal;
            })
            .catch(function () {});
    }

    document.addEventListener('DOMContentLoaded', function () {
        loadRecentRuns();
        loadSymphonies();
        refreshIncubationChips();
        // 15 s floor — faster than the engine's minute cadence makes no sense.
        // Also drives the incubation status-chip refresh (AC-5), folded into
        // this existing interval rather than a new timer.
        setInterval(function () {
            loadRecentRuns();
            refreshIncubationChips();
        }, 15000);

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

        // ----------------------------------------------------------------
        // In-place tab switcher (AC2 — window-selector pattern).
        // Matches the .active-toggle pattern from static/index.js:1348.
        // ----------------------------------------------------------------
        (function initTabSwitcher() {
            var tabs = document.querySelectorAll('[role="tab"][data-tab]');
            var panels = document.querySelectorAll('[role="tabpanel"][data-tab]');

            if (!tabs.length || !panels.length) { return; }

            function activateTab(targetTab) {
                // Update tab button states.
                tabs.forEach(function (btn) {
                    var isActive = btn === targetTab;
                    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
                    if (isActive) {
                        btn.classList.add('active');
                    } else {
                        btn.classList.remove('active');
                    }
                });
                // Show/hide panels — match data-tab to the button's data-tab.
                var targetName = targetTab.getAttribute('data-tab');
                panels.forEach(function (panel) {
                    if (panel.getAttribute('data-tab') === targetName) {
                        panel.classList.add('tab-panel--active');
                    } else {
                        panel.classList.remove('tab-panel--active');
                    }
                });
            }

            // Wire click handlers.
            tabs.forEach(function (btn) {
                btn.addEventListener('click', function () {
                    activateTab(btn);
                });
            });

            // Ensure the initially-active button's panel is shown.
            var initialActive = document.querySelector('[role="tab"][aria-selected="true"]');
            if (initialActive) {
                activateTab(initialActive);
            }
        }());

    });

    // ---------------------------------------------------------------------------
    // Strategy incubation gate — live status chip refresh (AC-5).
    //
    // Chips are server-rendered on page load (app.py's ai_advisor_tab() stamps
    // a live-joined status onto each survivor at that request), but incubation
    // status can change between page loads (e.g. INCUBATING -> PROMOTED days
    // later) — this keeps already-rendered chips in sync without a full page
    // reload. Folded into the existing 15 s poll interval below rather than a
    // new timer. Property assignment only (className/textContent), never
    // innerHTML — status_reason is server-derived text.
    // ---------------------------------------------------------------------------

    function refreshIncubationChips() {
        var chips = document.querySelectorAll('[data-testid="incubation-status-chip"]');
        if (!chips.length) { return; }
        fetch('/api/incubation')
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                var rows = (body && body.incubating) || [];
                var byHash = {};
                rows.forEach(function (r) { byHash[r.candidate_hash] = r; });
                chips.forEach(function (chip) {
                    var hash = chip.dataset.candidateHash;
                    var row = hash ? byHash[hash] : null;
                    if (!row) { return; }
                    chip.className = 'incubation-status-chip incubation-status-chip--' + row.badge_modifier;
                    chip.textContent = row.badge_label;
                });
            })
            .catch(function () { /* leave last-known chip state in place */ });
    }

    // ---------------------------------------------------------------------------
    // Strategy Builder tab functions (moved from inline script in deleted
    // ai_advisor_strategy_builder.html — AC5 of the SPA-port fold-in).
    //
    // These functions are defined inside the IIFE so they share the _csrfToken
    // closure, then exposed on window so Jinja onclick="sbRunAnalysis()" works.
    // ---------------------------------------------------------------------------

    /**
     * Open the Chat tab pre-loaded with a strategy-proposal artifact (Phase 4).
     *
     * Stores the artifact in sessionStorage so the Chat tab picks it up on load,
     * then navigates to /ai-advisor/chat with from_strategy_builder param.
     *
     * This is pure JS navigation — no form submission, no POST.
     * The button calling this must be type="button" (never type="submit").
     */
    function openChatWithArtifact(artifactJson) {
        try {
            var artifact = JSON.parse(artifactJson);
            sessionStorage.setItem('sb_pending_chat_artifact', JSON.stringify(artifact));
        } catch (e) {
            // Ignore parse errors — navigate anyway.
        }
        var sym = document.getElementById('sb-symphony-select')
            ? document.getElementById('sb-symphony-select').value : '';
        var url = '/ai-advisor/chat';
        if (sym) { url += '?from_strategy_builder=1&symphony_id=' + encodeURIComponent(sym); }
        window.location.href = url;
    }

    /**
     * Strategy Builder run trigger.
     *
     * Reads objective/universe/symphony from the controls panel, obtains the
     * CSRF token from the prefetched _csrfToken (or fetches fresh on miss),
     * then POSTs to /ai-advisor/strategy-builder/run with X-CSRF-Token header.
     *
     * On success: renders the response IN-PLACE into #sb-run-results (survivor
     * cards, 0-survivor honest state, rejected-candidates collapsible) — never
     * navigates away, so the operator can tell THIS run's results from prior
     * history (AC-1/AC-2, feature-plans/advisor-suite-fixes.md). No sparkline —
     * the run endpoint returns no equity points; the persisted-history cards
     * (server-rendered) keep the sparkline, these do not (accepted scope gap).
     *
     * On error: the sb-run-error div is shown inline.
     */
    // AC-7 (F6, Gap F; r1-review Checkpoint-3 BLOCK finding): rejection_reason
    // -> distinguishable copy. Mirrors the SB Jinja _REJECTION_COPY map and
    // its Asset-Swaps/Logic-Changes JS siblings exactly (same 4 mapped
    // values, same wording) so the operator sees identical explanations
    // regardless of which surface (live run vs. persisted history vs. the
    // other two evaluate routes) rejected the candidate. Extensible: an
    // unmapped reason (null, or a future untracked class) renders NOTHING —
    // never a fabricated blanket string.
    var SB_LIVE_REJECTION_COPY = {
        pbo_veto: 'This candidate failed the overfitting-robustness (PBO) check.',
        below_spy_alpha: 'This candidate did not beat the SPY benchmark over the same period.',
        oos_inferior_to_incumbent: 'This candidate did not outperform the live incumbent out-of-sample.',
        fdr_not_winner: 'This candidate cleared the FDR-calibrated significance bar but was not the single strongest candidate this run.',
    };

    async function sbRunAnalysis() {
        var btn = document.getElementById('sb-run-btn');
        var errDiv = document.getElementById('sb-run-error');
        var resultsDiv = document.getElementById('sb-run-results');
        if (!btn) { return; }

        btn.disabled = true;
        btn.textContent = 'Running…';
        if (errDiv) { errDiv.style.display = 'none'; errDiv.textContent = ''; }
        if (resultsDiv) {
            resultsDiv.innerHTML = '<div class="sb-loading-state">Running analysis…</div>';
        }

        try {
            // Obtain CSRF token — use cached value or fetch fresh.
            var csrfToken = _csrfToken;
            if (!csrfToken) {
                var tokenResp = await fetch('/api/csrf-token');
                if (!tokenResp.ok) { throw new Error('Could not obtain CSRF token'); }
                var tokenData = await tokenResp.json();
                csrfToken = tokenData.csrf_token;
            }

            // Build payload from the strategy-builder controls.
            var objective = (document.getElementById('sb-objective-select') || {}).value || 'diversify';
            var universeRaw = (document.getElementById('sb-universe-input') || {}).value || '';
            var universe = universeRaw.split(',')
                .map(function (s) { return s.trim().toUpperCase(); })
                .filter(Boolean);
            var symphonyId = (document.getElementById('sb-symphony-select') || {}).value || '';

            // POST to the unchanged action route.
            var resp = await fetch('/ai-advisor/strategy-builder/run', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken,
                },
                body: JSON.stringify({ objective: objective, universe: universe, symphony_id: symphonyId }),
            });

            var data = await resp.json();
            if (data.error) {
                // AC-11: error_category is a richer, still-sanitized cause token
                // (a type(exc).__name__-shaped string) alongside the static
                // "strategy-builder-error" token — non-null-only, never render
                // the literal string "null"/"undefined" in the DOM.
                var errText = data.error + (data.error_category ? ' (' + data.error_category + ')' : '');
                if (errDiv) { errDiv.textContent = errText; errDiv.style.display = 'block'; }
                if (resultsDiv) { resultsDiv.innerHTML = ''; }
            } else {
                var n = data.n_candidates || 0, thr = data.fdr_adjusted_threshold;
                var sv = data.survivors || [], rj = data.rejected || [];
                var html = '<div class="run-controls-note" data-testid="sb-live-summary">Evaluated ' + n +
                    ' candidate' + (n === 1 ? '' : 's') + (thr != null ? ' — threshold α=' + thr.toFixed(4) : '') + '</div>';
                // AC-11 (F5, Gap E): built-new/Atlas provenance rollup — no prior
                // render surface existed for these two fields anywhere in the
                // codebase (checked Jinja + every JS file); minimal factual line,
                // same ' · ' separator convention already used elsewhere in this
                // file.
                if (data.built_new_count != null || data.atlas_count != null) {
                    html += '<div class="run-controls-note" data-testid="sb-live-provenance">Built-new: ' +
                        (data.built_new_count || 0) + ' · Atlas: ' + (data.atlas_count || 0) + '</div>';
                }
                // AC-11: degraded-run notice ("Opus produced 0 plans...") — server-
                // authored prose, rendered verbatim, non-null-only.
                if (data.mode_notice) {
                    html += '<div class="empty-state" data-testid="sb-live-mode-notice">' +
                        escHtml(data.mode_notice) + '</div>';
                }
                // AC-12: honest indicator when the drawdown/Pearson screens did not
                // run this batch (no live-portfolio return series at route time).
                if (data.screens_skipped) {
                    html += '<div class="run-controls-note" data-testid="sb-live-screens-skipped">Screens skipped' +
                        (data.screens_skipped_reason ? ': ' + escHtml(data.screens_skipped_reason) : '') + '</div>';
                }
                // AC-4/AC-5 (DEGRADE-FIX): honest notice when some candidates were
                // compiled but could not be tradeability-checked because Composer's
                // /backtest was unreachable (infra outage, not a genuine gate
                // rejection) -- guarded on the boolean flag (mirrors the
                // screens_skipped/screens_skipped_reason pairing above), server-
                // authored prose rendered verbatim, distinct from the "0 passed
                // the gate" empty-state and from survivor/rejected cards.
                // Non-null-only -- never fabricated (AC-5).
                if (data.backtest_unavailable) {
                    html += '<div class="empty-state" data-testid="sb-live-backtest-unavailable">' +
                        escHtml(data.backtest_unavailable_notice) + '</div>';
                }
                // R2-1 (AC-4/AC-5): run-level generation provenance -- model,
                // injected-evidence manifest, and run-id, read straight off
                // data.provenance (the route's 4-key object; see app.py's
                // ai_advisor_strategy_builder_run()). Distinct from the
                // existing built-new/Atlas TEMPLATE-provenance rollup above
                // (data-testid="sb-live-provenance", AC-11/F5) -- same
                // overloaded English word, different concept (per-candidate
                // origin vs. this run's generation-context provenance),
                // hence the disambiguated testid. Non-null-only: a null
                // provenance (the pre-existing no-key error path, which
                // never populates this field) renders nothing, mirroring
                // the mode_notice/backtest_unavailable idiom above.
                if (data.provenance) {
                    var prov = data.provenance;
                    var evidence = prov.evidence_injected || {};
                    var evidenceParts = [];
                    ['tree', 'stats', 'technicals', 'sentiment', 'derivatives', 'macro', 'fundamentals'].forEach(function (key) {
                        var val = evidence[key];
                        if (val) { evidenceParts.push(key + ': ' + val); }
                    });
                    html += '<div class="run-controls-note" data-testid="sb-live-generation-provenance">' +
                        'Model: ' + escHtml(prov.generation_model || '') +
                        (evidenceParts.length ? ' · Context — ' + escHtml(evidenceParts.join(', ')) : '') +
                        (prov.run_id ? ' · Run: ' + escHtml(prov.run_id) : '') +
                        '</div>';
                }
                function card(c, cls) {
                    var extra = '';
                    var modifierClass = '';
                    if (cls === 'survivor') {
                        // AC-9: low_power drives a CSS modifier only — the caveat
                        // TEXT itself comes from c.caveats (server-appended
                        // _LOW_POWER_CAVEAT when true), never re-derived or
                        // hardcoded here; the numeric MIN_POWER_FOLD_DAYS threshold
                        // never crosses into JS (locked AC-9 contract). Moot for
                        // rejected candidates — didn't clear the gate either way,
                        // mirrors app.py's own survivor-only scoping.
                        if (c.low_power) { modifierClass = ' proposal-card--low-power'; }
                        if (c.caveats && c.caveats.length) {
                            extra += '<div class="caveats-block" data-testid="caveats-block">' +
                                c.caveats.map(function (cv) { return '<p class="caveat-text">' + escHtml(cv) + '</p>'; }).join('') +
                                '</div>';
                        }
                    } else {
                        // AC-7: rejection_reason -> distinguishable copy via
                        // SB_LIVE_REJECTION_COPY (byte-identical wording to the
                        // persisted-history Jinja _REJECTION_COPY and the
                        // Asset-Swaps/Logic-Changes JS REJECTION_COPY maps).
                        // Unmapped/null reason renders nothing — never a
                        // fabricated blanket string.
                        var reasonCopy = c.rejection_reason ? SB_LIVE_REJECTION_COPY[c.rejection_reason] : null;
                        if (reasonCopy) {
                            extra += '<div class="apply-guidance" data-testid="apply-guidance">' +
                                '<strong>Gate withheld:</strong> ' + escHtml(reasonCopy) + '</div>';
                        }
                    }
                    return '<div class="proposal-card proposal-card--' + cls + modifierClass + '"><span class="card-candidate-id">' +
                        escHtml(c.candidate_id || '') + '</span>' + extra + '</div>';
                }
                if (sv.length) {
                    html += '<div class="proposal-cards" data-testid="sb-live-survivor-cards">' +
                        sv.map(function (s) { return card(s, 'survivor'); }).join('') + '</div>';
                } else {
                    html += '<div class="empty-state" data-testid="sb-live-empty-state">Evaluated ' + n +
                        ' candidates — 0 passed the gate</div>';
                }
                if (rj.length) {
                    html += '<details class="rejected-collapsible" data-testid="sb-live-rejected-section">' +
                        '<summary>Candidates that did not clear the gate (' + rj.length + ')</summary>' +
                        '<div class="rejected-cards">' + rj.map(function (r) { return card(r, 'rejected'); }).join('') +
                        '</div></details>';
                }
                if (resultsDiv) { resultsDiv.innerHTML = html; }
            }
        } catch (err) {
            if (errDiv) { errDiv.textContent = 'Request failed: ' + err.message; errDiv.style.display = 'block'; }
            if (resultsDiv) { resultsDiv.innerHTML = ''; }
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Run analysis'; }
        }
    }

    // ---------------------------------------------------------------------------
    // Frontrunner Builder tab functions.
    //
    // Defined inside the IIFE so they share the _csrfToken closure, then
    // exposed on window so Jinja onclick="..." works.
    // ---------------------------------------------------------------------------

    /**
     * Frontrunner Builder run trigger.
     *
     * POSTs to /ai-advisor/frontrunner-builder/run. The route dispatches the
     * (genuinely multi-minute, all-live-symphonies) build to a background
     * executor and returns immediately — there is no synchronous result to
     * render. On success this shows a status message telling the operator to
     * reload the page later; it deliberately does NOT auto-navigate (unlike
     * sbRunAnalysis) since results are not ready yet when the response lands.
     */
    async function frRunBuild() {
        var btn = document.getElementById('fr-run-btn');
        var statusDiv = document.getElementById('fr-run-status');
        var errDiv = document.getElementById('fr-run-error');
        if (!btn) { return; }

        btn.disabled = true;
        btn.textContent = 'Starting…';
        if (statusDiv) { statusDiv.textContent = ''; }
        if (errDiv) { errDiv.style.display = 'none'; errDiv.textContent = ''; }

        try {
            var csrfToken = _csrfToken;
            if (!csrfToken) {
                var tokenResp = await fetch('/api/csrf-token');
                if (!tokenResp.ok) { throw new Error('Could not obtain CSRF token'); }
                var tokenData = await tokenResp.json();
                csrfToken = tokenData.csrf_token;
            }

            var resp = await fetch('/ai-advisor/frontrunner-builder/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                body: JSON.stringify({}),
            });
            var data = await resp.json();

            if (data && data.error) {
                if (errDiv) { errDiv.textContent = data.error; errDiv.style.display = 'block'; }
            } else if (statusDiv) {
                statusDiv.textContent =
                    'Build started in the background — this can take several minutes for ' +
                    'multiple symphonies. Reload this page afterward to see new proposals.';
            }
        } catch (err) {
            if (errDiv) { errDiv.textContent = 'Request failed: ' + err.message; errDiv.style.display = 'block'; }
        } finally {
            btn.disabled = false;
            btn.textContent = 'Run build';
        }
    }

    /**
     * Shared approve/reject dispatch for a frontrunner_proposals row.
     *
     * action: 'approve' | 'reject'. Disables both buttons in the card
     * immediately (prevents double-submit) and replaces the card's action
     * row with a status message on success.
     */
    function frDispatchProposalAction(action, proposalId) {
        var card = document.getElementById('fr-card-' + proposalId);
        var approveBtn = card ? card.querySelector('[data-testid="fr-approve-btn"]') : null;
        var rejectBtn = card ? card.querySelector('[data-testid="fr-reject-btn"]') : null;
        if (approveBtn) { approveBtn.disabled = true; }
        if (rejectBtn) { rejectBtn.disabled = true; }
        if (card) { card.style.opacity = '0.6'; }

        var routePath = action === 'approve' ? '/ai-advisor/proposal/approve' : '/ai-advisor/proposal/reject';

        (async function () {
            try {
                var csrfToken = _csrfToken;
                if (!csrfToken) {
                    var tokenResp = await fetch('/api/csrf-token');
                    if (!tokenResp.ok) { throw new Error('Could not obtain CSRF token'); }
                    var tokenData = await tokenResp.json();
                    csrfToken = tokenData.csrf_token;
                }

                var resp = await fetch(routePath, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                    body: JSON.stringify({ proposal_id: proposalId }),
                });
                var data = await resp.json();

                if (data && (data.success === true)) {
                    if (card) {
                        card.style.opacity = '1';
                        var msg = action === 'approve'
                            ? 'Approved — created in Composer' + (data.symphony_id ? ' (' + escHtml(data.symphony_id) + ')' : '') + '.'
                            : 'Rejected.';
                        var actionsRow = card.querySelector('.proposal-actions');
                        if (actionsRow) {
                            actionsRow.innerHTML =
                                '<p style="color:' + cssVar('--studio-pos') + ';font-size:0.875rem;font-weight:700;">' +
                                msg + '</p>';
                        }
                    }
                } else {
                    if (card) { card.style.opacity = '1'; }
                    if (approveBtn) { approveBtn.disabled = false; }
                    if (rejectBtn) { rejectBtn.disabled = false; }
                    alert((action === 'approve' ? 'Approve' : 'Reject') + ' failed: ' + (data && data.error ? data.error : 'unknown error'));
                }
            } catch (err) {
                if (card) { card.style.opacity = '1'; }
                if (approveBtn) { approveBtn.disabled = false; }
                if (rejectBtn) { rejectBtn.disabled = false; }
                alert('Request failed: ' + err.message);
            }
        }());
    }

    function frApprove(proposalId) {
        frDispatchProposalAction('approve', proposalId);
    }

    function frReject(proposalId) {
        frDispatchProposalAction('reject', proposalId);
    }

    // Expose on window so Jinja onclick="..." handlers can call them.
    window.openChatWithArtifact = openChatWithArtifact;
    window.sbRunAnalysis = sbRunAnalysis;
    window.frRunBuild = frRunBuild;
    window.frApprove = frApprove;
    window.frReject = frReject;

})();
