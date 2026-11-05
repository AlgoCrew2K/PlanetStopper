/**
 * ai_advisor.js — Studio AI Advisor client logic.
 *
 * All colors use CSS custom properties (--studio-*) resolved at runtime so
 * theme/accent changes propagate without a reload.
 * No Tailwind class names in this file.
 */
(function () {
    'use strict';

    // The CSRF token and every POST's answer come from window.PSRequest (static/request_guard.js).
    // A POST the session check refuses (403) is reported as refused after the token is read again;
    // no control here sends it a second time: the operator's next click does.

    /** A notice inside a card that keeps its buttons: the card says what was not sent. */
    function showCardNotice(card, text) {
        if (!card) return;
        var notice = card.querySelector('[data-testid="card-not-sent"]');
        if (!notice) {
            notice = document.createElement('p');
            notice.setAttribute('data-testid', 'card-not-sent');
            notice.setAttribute('role', 'status');
            card.appendChild(notice);
        }
        notice.textContent = text;
    }

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

    // ---------------------------------------------------------------------------
    // One ET time formatter: the page-script half of analytics.format_et_stamp
    // (the Jinja filter `et_stamp` is the template half). Same contract and the
    // same accepted text, so a time reads identically wherever it is shown:
    // 'YYYY-MM-DD HH:MM ET' in America/New_York, minutes truncated; a time with
    // no zone is read as UTC unless opts.naiveIsUtc is false (then it prints its
    // date only, unconverted); opts.dateOnly prints the ET date; a calendar date
    // alone is returned unchanged; anything absent or unreadable is the dash.
    // Parsed with regular expressions, never Date.parse (its reading of zone-less
    // and fractional text differs between browsers); never throws.
    // ---------------------------------------------------------------------------

    var ET_STAMP_MISSING = '\u2014';
    var ET_STAMP_ISO = /^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})?)?$/;
    var ET_STAMP_COMPACT = /^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$/;
    var ET_STAMP_TRIM = /^[ \t\r\n]+|[ \t\r\n]+$/g;
    var _etStampFormatter = null;

    function etDaysInMonth(year, month) {
        if (month === 2) {
            return (year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)) ? 29 : 28;
        }
        return (month === 4 || month === 6 || month === 9 || month === 11) ? 30 : 31;
    }

    function etPad(n, width) {
        var s = String(n);
        while (s.length < width) { s = '0' + s; }
        return s;
    }

    function fmtEtStamp(value, opts) {
        try {
            var naiveIsUtc = !(opts && opts.naiveIsUtc === false);
            var dateOnly = !!(opts && opts.dateOnly);
            if (typeof value !== 'string') { return ET_STAMP_MISSING; }
            var text = value.replace(ET_STAMP_TRIM, '');
            if (!text) { return ET_STAMP_MISSING; }

            var f;          // [year, month, day, hour, minute, second]
            var zoneText = null;
            var zoned = false;
            var m = ET_STAMP_COMPACT.exec(text);
            if (m) {
                f = m.slice(1, 7).map(Number);
                zoned = true;
            } else {
                m = ET_STAMP_ISO.exec(text);
                if (!m) { return ET_STAMP_MISSING; }
                f = [Number(m[1]), Number(m[2]), Number(m[3])];
                if (m[4] === undefined) {
                    // A calendar date alone: valid or not, never converted.
                    if (f[0] < 1 || f[1] < 1 || f[1] > 12 || f[2] < 1 || f[2] > etDaysInMonth(f[0], f[1])) {
                        return ET_STAMP_MISSING;
                    }
                    return text;
                }
                f.push(Number(m[4]), Number(m[5]), Number(m[6]));
                zoneText = m[7] === undefined ? null : m[7];
                zoned = zoneText !== null;
            }
            if (f[0] < 1 || f[1] < 1 || f[1] > 12 || f[2] < 1 || f[2] > etDaysInMonth(f[0], f[1]) ||
                f[3] > 23 || f[4] > 59 || f[5] > 59) {
                return ET_STAMP_MISSING;
            }
            var offsetMs = 0;
            if (zoneText !== null && zoneText !== 'Z') {
                var offsetHours = Number(zoneText.slice(1, 3));
                var offsetMinutes = Number(zoneText.slice(4, 6));
                if (offsetHours > 23 || offsetMinutes > 59) { return ET_STAMP_MISSING; }
                offsetMs = (zoneText.charAt(0) === '-' ? -1 : 1) * (offsetHours * 60 + offsetMinutes) * 60000;
            }
            if (!zoned && !naiveIsUtc) {
                return etPad(f[0], 4) + '-' + etPad(f[1], 2) + '-' + etPad(f[2], 2);
            }
            var local = new Date(0);
            local.setUTCFullYear(f[0], f[1] - 1, f[2]);
            local.setUTCHours(f[3], f[4], f[5], 0);
            var instantMs = local.getTime() - offsetMs;
            if (isNaN(instantMs)) { return ET_STAMP_MISSING; }

            if (!_etStampFormatter) {
                _etStampFormatter = new Intl.DateTimeFormat('en-US', {
                    timeZone: 'America/New_York',
                    hourCycle: 'h23',
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit'
                });
            }
            var parts = {};
            _etStampFormatter.formatToParts(new Date(instantMs)).forEach(function (p) {
                parts[p.type] = p.value;
            });
            var dayText = etPad(parts.year, 4) + '-' + parts.month + '-' + parts.day;
            if (dateOnly) { return dayText; }
            var hour = parts.hour === '24' ? '00' : parts.hour;
            return dayText + ' ' + hour + ':' + parts.minute + ' ET';
        } catch (err) {
            return ET_STAMP_MISSING;
        }
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
        // The strip renders hidden: a count of zero before any run would read as a
        // result. A returned run (even one with no suggestions) shows its own counts.
        var strip = document.getElementById('advisor-summary-strip');
        if (strip) strip.removeAttribute('hidden');
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
        if (confidence === 'high') return 'color:' + cssVar('--studio-pos') + ';font-weight:500;';
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
                lensAsOfEl.textContent = 'Market context as of ' + fmtEtStamp(lensAsOf) + staleTag;
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
            // A status dot and one word, like the gate badges: the word stays in primary ink (the
            // warn colour is too light to carry 11-12px text), the dot carries the meaning.
            var suffBadge = (s.data_sufficiency && s.data_sufficiency !== 'sufficient')
                ? '<span class="gate-dot gate-dot--warn" data-testid="data-sufficiency-badge">' +
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
                    ? '<span style="font-size:0.6875rem;color:' + cssVar('--studio-ink-dim') + ';">' +
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
                return '<span class="gate-dot" data-testid="gate-badge" style="--dot:' + gc + ';">' +
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

                // The text column asks for 12rem before it shrinks: on a phone the actions wrap under it
                // instead of sitting on top of the config key. min-width:0 lets it shrink if it must.
                '<div style="flex:1 1 12rem;min-width:0;">' +
                '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">' +
                confidenceRing +
                '<span style="font-size:0.75rem;font-weight:500;' +
                'color:' + cssVar('--studio-accent') + ';">' +
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
                      'font-size:0.75rem;font-weight:500;cursor:not-allowed;">' +
                      'Blocked by OOS gate</button>'
                    : '<button class="suggestion-apply-btn" data-card-index="' + i + '" ' +
                      'style="padding:0.375rem 1rem;background:' + cssVar('--studio-pos') + ';' +
                      'color:' + cssVar('--studio-paper-hi') + ';border:none;border-radius:0.5rem;' +
                      'font-size:0.75rem;font-weight:500;cursor:pointer;">' +
                      'Apply suggestion</button>'
                ) +
                '<button class="suggestion-dismiss-btn" data-card-index="' + i + '" ' +
                'style="padding:0.375rem 1rem;background:' + cssVar('--studio-surface-raised') + ';' +
                'color:' + cssVar('--studio-ink-dim') + ';border:1px solid ' + cssVar('--studio-border') + ';' +
                'border-radius:0.5rem;font-size:0.75rem;font-weight:500;cursor:pointer;">' +
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

        // The buttons carry only the card's index; the symphony's name is read here from the
        // container when a button is pressed. A name is authored outside this app and can hold an
        // apostrophe, a quote or code, so it is never written into an inline handler string.
        container.querySelectorAll('.suggestion-apply-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                window.acceptSuggestion(Number(btn.getAttribute('data-card-index')), container._symphonyId);
            });
        });
        container.querySelectorAll('.suggestion-dismiss-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                window.rejectSuggestion(Number(btn.getAttribute('data-card-index')), container._symphonyId);
            });
        });
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

        PSRequest.postAction('/ai-advisor/suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ symphony_id: symphonyId }),
        })
            .then(function (res) {
                function showError(text) {
                    container.innerHTML = '';
                    errorEl.style.display = 'block';
                    errorEl.textContent = text;
                }
                if (res.networkError) { showError('Request failed: network error'); return; }
                if (res.refused === 'csrf') {
                    showError(PSRequest.notSentText(res, 'Click Run Claude advisor again.'));
                    return;
                }
                var body = res.body;
                if (!body) { showError('Request failed: the answer could not be read (HTTP ' + res.status + ')'); return; }
                if (body.error) {
                    showError('Advisor unavailable: ' + body.error);
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
        PSRequest.postAction('/ai-advisor/accept', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ symphony_id: symphonyId, suggestion: suggestion }),
        })
            .then(function (res) {
                if (res.networkError) {
                    card.style.opacity = '1';
                    alert('Request failed: network error');
                    return;
                }
                if (res.refused === 'csrf') {
                    // Nothing was accepted and nothing is sent again: the card keeps its buttons.
                    card.style.opacity = '1';
                    showCardNotice(card, PSRequest.notSentText(res, 'Click Accept again.'));
                    return;
                }
                var body = res.body || {};
                if (body.status === 'accepted') {
                    card.innerHTML =
                        '<p style="color:' + cssVar('--studio-pos') + ';font-size:0.875rem;font-weight:500;">' +
                        'Accepted — config updated.</p>';
                } else {
                    card.style.opacity = '1';
                    alert('Rejected by C2 gate: ' + (body.error || body.status));
                }
            });
    };

    window.rejectSuggestion = function rejectSuggestion(index, symphonyId) {
        var container = document.getElementById('suggestions-container');
        var suggestion = container._suggestions[index];
        var card = document.getElementById('card-' + index);

        card.style.opacity = '0.5';
        PSRequest.postAction('/ai-advisor/reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ symphony_id: symphonyId, suggestion: suggestion }),
        })
            .then(function (res) {
                if (res.networkError) {
                    card.style.opacity = '1';
                    alert('Request failed: network error');
                    return;
                }
                if (res.refused === 'csrf') {
                    card.style.opacity = '1';
                    showCardNotice(card, PSRequest.notSentText(res, 'Click Reject again.'));
                    return;
                }
                card.innerHTML =
                    '<p style="color:' + cssVar('--studio-ink-dim') + ';font-size:0.875rem;">Rejected.</p>';
            });
    };

    // ---------------------------------------------------------------------------
    // Autotune sparkline
    // ---------------------------------------------------------------------------

    var _sparkChart = null;

    // The canvas carries an inline `display:block` in the template, which beats the
    // `hidden` attribute, so it is hidden and shown through style.display. Shown again
    // means back to the value the template writes.
    var SPARK_CANVAS_DISPLAY = 'block';

    function setSparklineVisible(canvas, visible) {
        canvas.style.display = visible ? SPARK_CANVAS_DISPLAY : 'none';
    }

    // Destroy the chart the canvas holds, whoever created it: Chart.js refuses a second
    // chart on a canvas that still holds one, and our own handle is null when another
    // owner drew on the canvas first.
    function releaseSparklineCanvas(canvas) {
        var held = null;
        if (typeof Chart !== 'undefined') {
            held = typeof Chart.getChart === 'function' ? Chart.getChart(canvas) : _sparkChart;
        }
        if (held) held.destroy();
        _sparkChart = null;
    }

    // Draws every returned run that has a score, oldest first. Hidden (no space taken)
    // when the chart library is missing or no run has a score; a chart that fails to
    // build hides the canvas too, and never reaches the caller's failure handler.
    function renderAutotuneSparkline(rows) {
        var canvas = document.getElementById('autotune-sparkline-canvas');
        if (!canvas) return;

        var sharpeValues = rows
            .slice()
            .reverse()
            .map(function (r) { return r.naive_sharpe !== null && r.naive_sharpe !== undefined ? Number(r.naive_sharpe) : null; })
            .filter(function (v) { return v !== null; });

        if (typeof Chart === 'undefined' || !sharpeValues.length) {
            releaseSparklineCanvas(canvas);
            setSparklineVisible(canvas, false);
            return;
        }

        try {
            var cs = getComputedStyle(document.documentElement);
            // C-15: fall back to neutral token values — no bare hex.
            var accentColor = cs.getPropertyValue('--studio-accent').trim() || cs.getPropertyValue('--studio-swatch-1').trim();
            var borderColor = cs.getPropertyValue('--studio-rule').trim() || cs.getPropertyValue('--studio-border').trim();

            releaseSparklineCanvas(canvas);
            // Shown before the chart is built: a responsive chart sizes itself from the
            // canvas box, which a hidden canvas does not have.
            setSparklineVisible(canvas, true);

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
        } catch (err) {
            _sparkChart = null;
            setSparklineVisible(canvas, false);
        }
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

    // The status dot's colour, as a modifier class on the word (pos, neg or warn).
    function decisionTone(decision) {
        if (decision === 'apply') return 'pos';
        if (decision === 'reject') return 'neg';
        // V-25: fallback/hold/pending/unknown all use the warn token
        // so saturation stays consistent in light + dark themes.
        return 'warn';
    }

    function frozenEvalTone(verdict) {
        if (verdict === 'passed') return 'pos';
        if (verdict === 'failed') return 'neg';
        return 'warn';
    }

    // The run row's `naive_sharpe` is the optimizer's best trial score under the
    // run's own objective (not a Sharpe ratio), so the card names the objective
    // the route reports in `row.objective` (read from the run's spec bundle).
    // Only the two objectives the route can name appear here; null, absent or any
    // other value takes the generic label, which names both possible objectives
    // in its tooltip, so a stored value is never printed as if it were a name.
    var AUTOTUNE_SCORE_TIP_SUFFIX =
        ' It is the optimizer’s best trial score, before the selection haircut.';
    var AUTOTUNE_SCORE_LABELS = {
        crra_eu: {
            label: 'Best CRRA-EU score',
            tip: 'Scored by the CRRA expected-utility objective.' + AUTOTUNE_SCORE_TIP_SUFFIX,
        },
        sortino_loss_aversion: {
            label: 'Best Sortino score',
            tip: 'Scored by the Sortino-ratio objective.' + AUTOTUNE_SCORE_TIP_SUFFIX,
        },
    };
    var AUTOTUNE_SCORE_GENERIC = {
        label: 'Best objective score',
        tip: 'The objective is CRRA-EU or Sortino, set by the run’s spec bundle, ' +
            'which could not be read for this run.' + AUTOTUNE_SCORE_TIP_SUFFIX,
    };

    function autotuneScoreInfo(objective) {
        return Object.prototype.hasOwnProperty.call(AUTOTUNE_SCORE_LABELS, objective)
            ? AUTOTUNE_SCORE_LABELS[objective]
            : AUTOTUNE_SCORE_GENERIC;
    }

    // One row per symphony, at most this many; every other returned run is an older run.
    var AUTOTUNE_CARD_LIMIT = 12;
    // The number of runs GET /api/autotune-runs returns (get_all_autotune_runs(limit=50)),
    // so the older-runs summary can say how far back the list reaches.
    var AUTOTUNE_ROUTE_ROW_LIMIT = 50;

    // rows arrive newest first. A symphony's row shows its first (newest) run; the first
    // AUTOTUNE_CARD_LIMIT symphonies, in the order their newest runs arrive, get rows;
    // every other row, in the route's order, is an older run.
    function splitAutotuneRuns(rows) {
        var seen = Object.create(null);
        var cards = [];
        rows.forEach(function (r) {
            var key = String(r.symphony_id);
            if (seen[key]) return;
            seen[key] = true;
            if (cards.length < AUTOTUNE_CARD_LIMIT) cards.push(r);
        });
        var older = rows.filter(function (r) { return cards.indexOf(r) === -1; });
        return { cards: cards, older: older };
    }

    // The markup of one run: one table row, used for the main list and for the older-runs list. The words
    // a header names for the whole column stay in the row as visually hidden text (and show on a phone,
    // where there is no header, and in a table whose header cannot name the score's objective), so a row
    // read alone says what each figure is.
    function autotuneRunRowHtml(r) {
        var decision = r.baseline_decision || '';
        // B-13: human-readable label instead of raw enum.
        var pillLabel = decisionLabel(decision);
        var frozenVerdict = r.frozen_eval_verdict || '';
        // The display name when the live state holds the symphony, else the
        // stored key; the key itself stays in the title.
        var symLabel = escHtml(r.symphony_name || r.symphony_id || '');
        var symKey = escHtml(r.symphony_id || '');
        var scoreInfo = autotuneScoreInfo(r.objective);
        // A run that never reached the frozen evaluation has no verdict:
        // say so in dim ink instead of rendering an empty word. It stays on screen:
        // it is the reason there is no verdict, not a word a header already says.
        var frozenHtml = frozenVerdict
            ? '<span class="autotune-run-frozen">Frozen-eval ' +
              '<span class="frozen-eval-pill frozen-eval-pill--' + frozenEvalTone(frozenVerdict) + '">' +
              escHtml(frozenVerdict) + '</span></span>'
            : '<span class="autotune-run-frozen">Frozen-eval not evaluated</span>';
        // Selection t-stat is the Harvey & Liu haircut winner's t-statistic.
        var selTstat = r.selection_tstat;
        // BL-8 (DE-AUDIT-BL8-001): the "silent-never-tuned" streak signal.
        // status == 'streak' with streak_weeks >= 2 (a genuine multi-run pattern, mirrors
        // analytics._NEVER_ADOPTED_MIN_ROWS) or status == 'insufficient_history'
        // (AC-11's informative degrade); streak_weeks < 2 with status 'streak'
        // renders nothing: a single non-adopted run isn't a pattern worth flagging.
        // The column header says "Runs without adopting a tuned config", so the cell is the
        // figure; the rest of the sentence stays in the cell as hidden text and shows on a phone.
        var streak = r.never_adopted_streak;
        var streakHtml = '';
        if (streak && streak.status === 'streak' && streak.streak_weeks >= 2) {
            streakHtml = '<span class="autotune-streak-figure">' + escHtml(streak.streak_weeks) + '</span> ' +
                '<span class="adv-vh autotune-streak-tail">runs without adopting a tuned config</span>';
        } else if (streak && streak.status === 'insufficient_history') {
            streakHtml = 'adoption streak: insufficient history (&lt;2 runs)';
        }
        return (
            '<div class="autotune-row" role="row" data-testid="autotune-run-row">' +
            '<span class="autotune-run-name" role="cell" title="' + symKey + '">' + symLabel + '</span>' +
            // The New York stamp (the shared formatter), not the stored string.
            '<span class="autotune-run-time" role="cell">' + escHtml(fmtEtStamp(r.run_timestamp)) + '</span>' +
            '<span class="autotune-run-outcome" role="cell">' +
            '<span class="decision-pill decision-pill--' + decisionTone(decision) + '">' + escHtml(pillLabel) + '</span>' +
            frozenHtml +
            '</span>' +
            '<span class="autotune-run-streak" role="cell">' + streakHtml + '</span>' +
            '<span class="autotune-run-score" role="cell" title="' + escHtml(scoreInfo.tip) + '">' +
            '<span class="adv-vh autotune-score-label">' + escHtml(scoreInfo.label) + ' </span>' +
            '<span class="mono-bold">' + escHtml(fmtSharpe(r.naive_sharpe)) + '</span></span>' +
            '<span class="autotune-run-tstat" role="cell">' +
            '<span class="adv-vh">Sel t-stat </span><span class="mono-bold">' + escHtml(fmtSharpe(selTstat)) + '</span></span>' +
            '</div>'
        );
    }

    // The runs as one table with its own header row (the older-runs list is a separate table, so a bare
    // figure there still has its column name above it). The score column's header names the objective
    // when every row in the table shares one (the usual case); when they differ it says "Best score" and
    // each row names its own, so the objective is always on screen.
    function autotuneTableHtml(rows) {
        var labels = [];
        var scoreTip = '';
        rows.forEach(function (r) {
            var info = autotuneScoreInfo(r.objective);
            if (labels.indexOf(info.label) === -1) {
                labels.push(info.label);
                scoreTip = info.tip;
            }
        });
        var sharedObjective = labels.length === 1;
        var scoreHead = sharedObjective ? labels[0] : 'Best score';
        var scoreHeadTitle = sharedObjective ? ' title="' + escHtml(scoreTip) + '"' : '';
        var head = '<div class="autotune-row autotune-row--head" role="row">' +
            '<span role="columnheader">Symphony</span>' +
            '<span role="columnheader">Run time (ET)</span>' +
            '<span role="columnheader">Outcome</span>' +
            '<span role="columnheader" class="autotune-run-streak">Runs without adopting a tuned config</span>' +
            '<span role="columnheader" class="autotune-run-score"' + scoreHeadTitle + '>' + escHtml(scoreHead) + '</span>' +
            '<span role="columnheader" class="autotune-run-tstat">Sel t-stat</span>' +
            '</div>';
        return '<div class="autotune-table' + (sharedObjective ? '' : ' autotune-table--row-objectives') +
            '" role="table" aria-label="Autotune runs">' + head +
            rows.map(function (r) { return autotuneRunRowHtml(r); }).join('') + '</div>';
    }

    // The older-runs <details> is static page markup: this writes only its rows container,
    // its summary text and its `hidden` property. It never recreates the element and never
    // touches `open`, so a 15 s poll leaves a details the operator opened open.
    function renderOlderAutotuneRuns(older) {
        var details = document.getElementById('autotune-older-runs');
        var summary = document.getElementById('autotune-older-summary');
        var olderList = document.getElementById('autotune-older-list');
        if (olderList) olderList.innerHTML = older.length ? autotuneTableHtml(older) : '';
        if (summary) {
            summary.textContent = older.length
                ? older.length + (older.length === 1 ? ' older run' : ' older runs') +
                  ' (covers the ' + AUTOTUNE_ROUTE_ROW_LIMIT + ' most recent runs)'
                : '';
        }
        if (details) details.hidden = older.length === 0;
    }

    // A poll that does not bring a list never empties the cards: it adds this notice beside the
    // list, and the next good poll removes it.
    var RUNS_NOT_REFRESHED_TEXT = 'Could not refresh runs';

    function setRunsNotice(list, text) {
        var notice = document.querySelector('[data-testid="autotune-runs-notice"]');
        if (!text) {
            if (notice) notice.textContent = '';
            return;
        }
        if (!notice) {
            notice = document.createElement('p');
            notice.setAttribute('data-testid', 'autotune-runs-notice');
            notice.setAttribute('role', 'status');
            notice.style.color = cssVar('--studio-neg');
            if (list.parentNode) list.parentNode.insertBefore(notice, list);
        }
        notice.textContent = text;
    }

    function loadRecentRuns() {
        var list = document.getElementById('autotune-runs-list');
        if (!list) return;
        var request = PSRequest.issue('autotune-runs');
        return PSRequest.getJson('/api/autotune-runs').then(function (res) {
            if (!PSRequest.isLatest('autotune-runs', request)) return;
            if (!res.ok) {
                // A lapsed session goes to the login page; any other answer is not a list of runs.
                if (res.kind === 'unauthorized') { PSRequest.goLogin(); return; }
                setRunsNotice(list, RUNS_NOT_REFRESHED_TEXT);
                return;
            }
            var rows = res.body;
            if (!Array.isArray(rows)) {
                // An error object is not an empty list: keep the cards and say so.
                setRunsNotice(list, RUNS_NOT_REFRESHED_TEXT);
                return;
            }
            try {
                setRunsNotice(list, '');
                if (!rows.length) {
                    list.innerHTML = '<div class="autotune-empty">No tuning runs recorded yet.</div>';
                    renderOlderAutotuneRuns([]);
                    renderAutotuneSparkline([]);
                    return;
                }
                // C-16: render the table with all per-run fields per advisor.jsx.
                var parts = splitAutotuneRuns(rows);
                list.innerHTML = autotuneTableHtml(parts.cards);
                renderOlderAutotuneRuns(parts.older);
                // Last, and on its own: the cards are on screen whatever the chart does.
                renderAutotuneSparkline(rows);
            } catch (err) {
                console.error('autotune runs render failed', err);
                setRunsNotice(list, RUNS_NOT_REFRESHED_TEXT);
            }
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

        initTabSwitcher();
        renderHeatmapDepth();
        refreshPrismLensClamp();
        watchPrismLensClamp();
    });

    // ---------------------------------------------------------------------------
    // Market Prism lens summaries. The stylesheet clamps each summary to a few lines once the lens row
    // carries data-clamp="on"; a lens whose text is cut gets a More control that opens it in place, and
    // one whose text fits gets none. Measured while the Overview is on screen (a hidden panel has no
    // height), so it runs again when that tab opens and when the width changes. Text only.
    // ---------------------------------------------------------------------------

    function refreshPrismLensClamp() {
        var grid = document.querySelector('[data-testid="prism-lenses"]');
        if (!grid) { return; }
        grid.setAttribute('data-clamp', 'on');
        grid.querySelectorAll('.prism-lens').forEach(function (lens) {
            var text = lens.querySelector('.prism-lens-text');
            var btn = lens.querySelector('.prism-lens-more');
            if (!text || lens.classList.contains('prism-lens--open')) { return; }
            if (text.clientHeight === 0) { return; }
            var cut = text.scrollHeight > text.clientHeight + 1;
            if (cut && !btn) {
                btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'prism-lens-more';
                btn.setAttribute('aria-expanded', 'false');
                btn.textContent = 'More';
                btn.addEventListener('click', function () {
                    var open = lens.classList.toggle('prism-lens--open');
                    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
                    btn.textContent = open ? 'Less' : 'More';
                });
                lens.appendChild(btn);
            } else if (!cut && btn) {
                btn.remove();
            }
        });
    }

    // A change of width changes how many lines a summary takes, so the controls are measured again.
    var _prismResizeTimer = null;
    function watchPrismLensClamp() {
        if (typeof window.addEventListener !== 'function') { return; }
        window.addEventListener('resize', function () {
            clearTimeout(_prismResizeTimer);
            _prismResizeTimer = setTimeout(refreshPrismLensClamp, 150);
        });
    }

    // ---------------------------------------------------------------------------
    // Heatmap depth. Each correlation cell carries the aligned days its number rests on
    // (data-n-obs, from the stored run). They print under the number, so the depth is read
    // without a hover. Text only: the value is read from an attribute and set as textContent.
    // ---------------------------------------------------------------------------

    function renderHeatmapDepth() {
        document.querySelectorAll('td[data-testid="corr-cell"][data-n-obs]').forEach(function (cell) {
            if (cell.querySelector('.corr-n')) { return; }
            var days = cell.getAttribute('data-n-obs');
            if (days === null || days === '') { return; }
            var depth = document.createElement('span');
            depth.className = 'corr-n';
            depth.textContent = 'n=' + days;
            cell.appendChild(depth);
        });
    }

    // ---------------------------------------------------------------------------
    // In-place tab switcher (AC2 — window-selector pattern) and the #<tab>
    // fragment. A click selects a tab and writes its name to the URL fragment
    // (replaceState: no history entry), so a reload or a shared link opens the
    // same tab; a hashchange (a link to #retirement, back/forward) selects the
    // tab it names. The fragment is untrusted input: it is only ever compared
    // with the data-tab names the page renders, never used to build a selector.
    // ---------------------------------------------------------------------------

    function renderedTabNames() {
        var names = [];
        document.querySelectorAll('[role="tab"][data-tab]').forEach(function (btn) {
            names.push(btn.getAttribute('data-tab'));
        });
        return names;
    }

    /** The tab a URL fragment names: only a '#' + a rendered name, else 'overview'. */
    function tabNameFromHash(hash, knownNames) {
        if (typeof hash !== 'string' || hash.charAt(0) !== '#' || !Array.isArray(knownNames)) {
            return 'overview';
        }
        var name = hash.slice(1);
        return knownNames.indexOf(name) >= 0 ? name : 'overview';
    }

    function activateTabByName(name) {
        document.querySelectorAll('[role="tab"][data-tab]').forEach(function (btn) {
            var isActive = btn.getAttribute('data-tab') === name;
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
            if (isActive) {
                btn.classList.add('active');
                // The row scrolls sideways on a narrow screen; 'nearest' moves it only as far as
                // needed and never scrolls the page itself.
                if (typeof btn.scrollIntoView === 'function') {
                    btn.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                }
            } else {
                btn.classList.remove('active');
            }
        });
        document.querySelectorAll('[role="tabpanel"][data-tab]').forEach(function (panel) {
            if (panel.getAttribute('data-tab') === name) {
                panel.classList.add('tab-panel--active');
            } else {
                panel.classList.remove('tab-panel--active');
            }
        });
        if (name === 'overview') { refreshPrismLensClamp(); }
    }

    /** Put a rendered tab's name in the URL fragment without adding a history entry. */
    function writeTabFragment(name) {
        try {
            history.replaceState(null, '', '#' + name);
        } catch (err) {
            // Some embedded contexts refuse replaceState; the tab still switches.
        }
    }

    function initTabSwitcher() {
        var tabs = document.querySelectorAll('[role="tab"][data-tab]');
        var panels = document.querySelectorAll('[role="tabpanel"][data-tab]');
        if (!tabs.length || !panels.length) { return; }

        tabs.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var name = btn.getAttribute('data-tab');
                activateTabByName(name);
                writeTabFragment(name);
            });
        });
        window.addEventListener('hashchange', function () {
            activateTabByName(tabNameFromHash(window.location.hash, renderedTabNames()));
        });
        // The first paint follows the URL: no fragment, or one naming no tab, is the Overview.
        activateTabByName(tabNameFromHash(window.location.hash, renderedTabNames()));
    }

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
    // These functions are defined inside the IIFE, then exposed on window so
    // Jinja onclick="sbRunAnalysis()" works.
    // ---------------------------------------------------------------------------

    /**
     * The chat panel's own fields for a Strategy Builder card's artifact.
     *
     * The artifact the card carries (the M6 strategy_proposal dict) is what the explain endpoint
     * receives as the artifact context; the panel's title, breadcrumb, objective line, gate pill
     * and key figure are different things, so they are made here from it. Nothing is invented: a
     * figure the artifact lacks is left out of the key line.
     */
    var GATE_OBJECTIVE_LINES = {
        ADOPT_CANDIDATE: 'Why this candidate cleared the statistical gate',
        KEEP_INCUMBENT: 'Why the gate withheld this candidate against the incumbent',
        REJECT_VETO_FAILED: 'Why the gate rejected this candidate'
    };

    function chatPanelDataFromArtifact(artifact) {
        var source = artifact.template_id === 'atlas-suggested' ? 'Atlas-suggested'
            : (artifact.template_id === 'built-new' ? 'Built from scratch' : 'Strategy proposal');
        var figures = [];
        if (typeof artifact.sharpe === 'number') { figures.push('Sharpe ' + artifact.sharpe.toFixed(2)); }
        if (typeof artifact.cagr === 'number') { figures.push('CAGR ' + (artifact.cagr * 100).toFixed(1) + '%'); }
        if (typeof artifact.calmar === 'number') { figures.push('Calmar ' + artifact.calmar.toFixed(2)); }
        var tickers = Array.isArray(artifact.tickers) ? artifact.tickers.slice(0, 6).join(', ') : '';
        return {
            artifactId: artifact.artifact_id,
            artifactType: artifact.artifact_type,
            title: 'Discuss: ' + source + ' candidate',
            contextLabel: 'Strategy Builder' + (tickers ? ' \u00b7 ' + tickers : ''),
            objective: GATE_OBJECTIVE_LINES[artifact.gate_verdict] || 'How the gate judged this candidate',
            gateDecision: artifact.gate_verdict,
            keyStat: figures.join(' \u00b7 '),
            artifactContext: artifact
        };
    }

    /**
     * Open the Chat tab on this page with a strategy-proposal artifact (Phase 4).
     *
     * The page does not navigate: the Chat tab is selected in place and the chat panel opens
     * on the card's own context (title, objective, gate verdict and key figures), so the
     * conversation is in view at once. No form submission, no POST. The button calling this
     * must be type="button" (never type="submit").
     */
    function openChatWithArtifact(artifactJson) {
        var artifact = null;
        try {
            artifact = JSON.parse(artifactJson);
        } catch (e) {
            artifact = null;
        }
        activateTabByName('chat');
        writeTabFragment('chat');
        if (artifact && typeof window.openChatPanel === 'function') {
            window.openChatPanel(chatPanelDataFromArtifact(artifact));
        }
    }

    /**
     * Strategy Builder run trigger.
     *
     * Reads objective/universe/symphony from the controls panel and POSTs to
     * /ai-advisor/strategy-builder/run through PSRequest.postAction (the CSRF token
     * is the tab's own; a refused run says it was not sent and is never repeated).
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

    // The objective / source words a Strategy Builder card shows. The server
    // renders the ONE mapping (app.py _SB_OBJECTIVE_LABELS / _SB_SOURCE_LABELS)
    // into the panel's data-sb-labels attribute, so a live run's cards use the
    // same words as the stored cards above. A missing attribute degrades to
    // "not recorded" on every card rather than inventing labels here.
    function sbCardLabels() {
        var fallback = { objectives: {}, sources: {}, not_recorded: 'not recorded' };
        var panel = document.getElementById('tab-panel-strategy-builder');
        var raw = panel && panel.getAttribute('data-sb-labels');
        if (!raw) { return fallback; }
        try {
            var parsed = JSON.parse(raw);
            return {
                objectives: parsed.objectives || {},
                sources: parsed.sources || {},
                not_recorded: parsed.not_recorded || fallback.not_recorded,
            };
        } catch (e) {
            return fallback;
        }
    }

    // A stored value's label, or null when the value is absent, not a string, or
    // not a key of the mapping (never the raw value).
    function sbLabelFor(mapping, stored) {
        return typeof stored === 'string' && Object.prototype.hasOwnProperty.call(mapping, stored)
            ? mapping[stored]
            : null;
    }

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
            // Build payload from the strategy-builder controls.
            var objective = (document.getElementById('sb-objective-select') || {}).value || 'diversify';
            var universeRaw = (document.getElementById('sb-universe-input') || {}).value || '';
            var universe = universeRaw.split(',')
                .map(function (s) { return s.trim().toUpperCase(); })
                .filter(Boolean);
            var symphonyId = (document.getElementById('sb-symphony-select') || {}).value || '';

            // POST to the unchanged action route.
            var res = await PSRequest.postAction('/ai-advisor/strategy-builder/run', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': PSRequest.csrfToken() || '',
                },
                body: JSON.stringify({ objective: objective, universe: universe, symphony_id: symphonyId }),
            });
            if (res.networkError) { throw new Error('network error'); }
            if (res.refused === 'csrf') {
                if (errDiv) {
                    errDiv.textContent = PSRequest.notSentText(res, 'Click Run analysis again.');
                    errDiv.style.display = 'block';
                }
                if (resultsDiv) { resultsDiv.innerHTML = ''; }
                return;
            }
            var data = res.body;
            if (!data) { throw new Error('the answer could not be read (HTTP ' + res.status + ')'); }
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
                        'Model: ' + escHtml(prov.generation_model || 'model unavailable') +
                        (evidenceParts.length ? ' · Context — ' + escHtml(evidenceParts.join(', ')) : '') +
                        (prov.run_id ? ' · Run: ' + escHtml(prov.run_id) : '') +
                        '</div>';
                }
                var cardLabels = sbCardLabels();
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
                    // AC-7: objective and source in words, and a title that leads with
                    // the objective when it is known (the candidate id stays as the
                    // secondary label); an unrecorded objective leaves the id alone.
                    var objectiveLabel = sbLabelFor(cardLabels.objectives, c.params && c.params.objective);
                    var sourceLabel = sbLabelFor(cardLabels.sources, c.template_id);
                    var candidateId = escHtml(c.candidate_id || '');
                    var titleHtml = objectiveLabel
                        ? escHtml(objectiveLabel) + ' <span class="stat-nil">' + candidateId + '</span>'
                        : candidateId;
                    var stripHtml = '<div class="objective-strip" data-testid="objective-strip">' +
                        'Objective: ' + escHtml(objectiveLabel || cardLabels.not_recorded) +
                        ' &nbsp;&middot;&nbsp; ' +
                        'Source: ' + escHtml(sourceLabel || cardLabels.not_recorded) + '</div>';
                    return '<div class="sym-card adv-card proposal-card proposal-card--' + cls + modifierClass + '"><span class="card-name adv-card-name">' +
                        titleHtml + '</span>' + stripHtml + extra + '</div>';
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
    // Defined inside the IIFE, then exposed on window so Jinja onclick="..." works.
    // ---------------------------------------------------------------------------

    // After a build is started the tab asks for its outcome every FR_BUILD_POLL_INTERVAL_MS and gives
    // up after FR_BUILD_POLL_TIMEOUT_MS. The poll is bounded, begins with the operator's click, and reads
    // one small read-only route; the 15 s floor for unattended refresh is kept.
    var FR_BUILD_POLL_INTERVAL_MS = 15000;
    var FR_BUILD_POLL_TIMEOUT_MS = 1800000;
    var FR_BUILD_LOST_TEXT = 'Build still running or lost; reload to check';
    // True from the click that starts a build until the page shows its outcome (or gives up): the
    // button stays disabled meanwhile and a second start from this tab sends nothing.
    var _frBuildActive = false;

    /**
     * Frontrunner Builder run trigger.
     *
     * POSTs to /ai-advisor/frontrunner-builder/run. The route dispatches the
     * (genuinely multi-minute, all-live-symphonies) build to a background
     * executor and answers 202 with a run id. The button then stays disabled and
     * the tab polls GET /api/frontrunner-build/last-run until the summary of THAT
     * run appears: "Build finished: N new proposals", or the failure with its code.
     * After 30 minutes with no outcome (a build lost to a restart included) it
     * enables the button again and says so. It never navigates away.
     */
    async function frRunBuild() {
        var btn = document.getElementById('fr-run-btn');
        var statusDiv = document.getElementById('fr-run-status');
        var errDiv = document.getElementById('fr-run-error');
        if (!btn || btn.disabled || _frBuildActive) { return; }

        _frBuildActive = true;
        btn.disabled = true;
        btn.textContent = 'Starting…';
        if (statusDiv) { statusDiv.textContent = ''; }
        if (errDiv) { errDiv.style.display = 'none'; errDiv.textContent = ''; }

        function showError(text) {
            if (errDiv) { errDiv.textContent = text; errDiv.style.display = 'block'; }
        }
        function finish() {
            _frBuildActive = false;
            btn.disabled = false;
            btn.textContent = 'Run build';
        }

        var res = await PSRequest.postAction('/ai-advisor/frontrunner-builder/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({}),
        });
        var data = res.body;
        if (res.networkError) {
            showError('Request failed: network error');
            finish();
            return;
        }
        if (res.refused === 'csrf') {
            showError(PSRequest.notSentText(res, 'Click Run build again.'));
            finish();
            return;
        }
        if (data && data.error) {
            // The route's own refusal (for example a missing API key), in its own words.
            showError(data.error);
            finish();
            return;
        }
        if (!(res.status === 202 && data && typeof data.run_id === 'string' && data.run_id)) {
            showError('The build did not start (HTTP ' + res.status + ').');
            finish();
            return;
        }

        // Started: wait for this run's summary, with the button disabled.
        btn.textContent = 'Build running…';
        if (statusDiv) {
            statusDiv.textContent = 'Build started in the background — this can take several minutes ' +
                'for multiple symphonies. This page shows the result here when it finishes.';
        }
        var runId = data.run_id;
        var startedAt = Date.now();

        function showOutcome(summary) {
            if (summary.status === 'completed') {
                var n = typeof summary.proposals === 'number' ? summary.proposals : 0;
                if (statusDiv) {
                    statusDiv.textContent = 'Build finished: ' + n + ' new ' +
                        (n === 1 ? 'proposal' : 'proposals');
                }
            } else {
                if (statusDiv) { statusDiv.textContent = ''; }
                showError('Build failed' + (summary.error_code ? ' (' + String(summary.error_code) + ')' : ''));
            }
            finish();
        }

        async function pollOnce() {
            if (Date.now() - startedAt >= FR_BUILD_POLL_TIMEOUT_MS) {
                if (statusDiv) { statusDiv.textContent = FR_BUILD_LOST_TEXT; }
                finish();
                return;
            }
            var read = await PSRequest.getJson('/api/frontrunner-build/last-run');
            if (read.ok && read.body && read.body.last_run && read.body.last_run.run_id === runId) {
                showOutcome(read.body.last_run);
                return;
            }
            if (!read.ok && read.kind === 'unauthorized') {
                PSRequest.goLogin();
                return;
            }
            setTimeout(pollOnce, FR_BUILD_POLL_INTERVAL_MS);
        }
        setTimeout(pollOnce, FR_BUILD_POLL_INTERVAL_MS);
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

        function restore() {
            if (card) { card.style.opacity = '1'; }
            if (approveBtn) { approveBtn.disabled = false; }
            if (rejectBtn) { rejectBtn.disabled = false; }
        }

        PSRequest.postAction(routePath, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ proposal_id: proposalId }),
        }).then(function (res) {
            var verb = action === 'approve' ? 'Approve' : 'Reject';
            if (res.networkError) {
                restore();
                alert('Request failed: network error');
                return;
            }
            if (res.refused === 'csrf') {
                // Nothing was approved or rejected and nothing is sent again: the buttons come back.
                restore();
                alert(PSRequest.notSentText(res, 'Click ' + verb + ' again.'));
                return;
            }
            var data = res.body;
            if (data && (data.success === true)) {
                if (card) {
                    card.style.opacity = '1';
                    var msg = action === 'approve'
                        ? 'Approved — created in Composer' + (data.symphony_id ? ' (' + escHtml(data.symphony_id) + ')' : '') + '.'
                        : 'Rejected.';
                    var actionsRow = card.querySelector('.proposal-actions');
                    if (actionsRow) {
                        actionsRow.innerHTML =
                            '<p style="color:' + cssVar('--studio-pos') + ';font-size:0.875rem;font-weight:500;">' +
                            msg + '</p>';
                    }
                }
            } else {
                restore();
                alert(verb + ' failed: ' + (data && data.error ? data.error : 'unknown error'));
            }
        });
    }

    function frApprove(proposalId) {
        frDispatchProposalAction('approve', proposalId);
    }

    function frReject(proposalId) {
        frDispatchProposalAction('reject', proposalId);
    }

    // The pill class per decision state; templates/ai_advisor.html maps the same three pairs.
    var RETIREMENT_PILL_CLASS = { pending: 'standby', approved: 'armed', rejected: 'triggered' };

    /**
     * Shared approve/reject dispatch for a retirement recommendation card
     * (Cycle 2b, AC-5/AC-7; reworked Cycle 2c, AC-7). Mirrors
     * frDispatchProposalAction's direct-element shape: CSRF header from
     * PSRequest, POST to /ai-advisor/retirement/{approve,reject} with
     * {candidate_id}, disable-on-submit, status-message swap on success.
     *
     * btnEl is the CLICKED button element itself (the Jinja onclick handler
     * passes `this`) -- candidateId and the ancestor card are both derived
     * directly from it (.dataset.candidateId / .closest(...)) rather than
     * scanning every [data-testid="retirement-recommendation-card"] on the
     * page and comparing a dataset attribute to find "the" one. This is the
     * direct-element analog of frDispatchProposalAction's unique-id lookup:
     * the clicked button's own ancestor card is always correct regardless
     * of any duplicate/matching-candidate-id edge case.
     *
     * Deliberately no auto page-reload on success: the wind-down checklist
     * is assembled server-side at render time only
     * (advisors.retirement_checklist.build_checklist, called from
     * ai_advisor_tab()) -- a JS-side re-render would be a second,
     * drift-prone implementation of the same deterministic checklist. A
     * manual reload picks it up.
     */
    function retDispatchDecision(action, btnEl) {
        var candidateId = btnEl ? btnEl.dataset.candidateId : null;
        var card = btnEl ? btnEl.closest('[data-testid="retirement-recommendation-card"]') : null;
        var approveBtn = card ? card.querySelector('[data-testid="retirement-approve-btn"]') : null;
        var rejectBtn = card ? card.querySelector('[data-testid="retirement-reject-btn"]') : null;
        if (approveBtn) { approveBtn.disabled = true; }
        if (rejectBtn) { rejectBtn.disabled = true; }
        if (card) { card.style.opacity = '0.6'; }

        var routePath = action === 'approve' ? '/ai-advisor/retirement/approve' : '/ai-advisor/retirement/reject';

        function restore() {
            if (card) { card.style.opacity = '1'; }
            if (approveBtn) { approveBtn.disabled = false; }
            if (rejectBtn) { rejectBtn.disabled = false; }
        }

        PSRequest.postAction(routePath, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ candidate_id: candidateId }),
        }).then(function (res) {
            var verb = action === 'approve' ? 'Approve' : 'Reject';
            if (res.networkError) {
                restore();
                alert('Request failed: network error');
                return;
            }
            if (res.refused === 'csrf') {
                // Nothing was decided and nothing is sent again: the buttons come back.
                restore();
                alert(PSRequest.notSentText(res, 'Click ' + verb + ' again.'));
                return;
            }
            var data = res.body;
            if (data && data.success === true) {
                if (card) {
                    card.style.opacity = '1';
                    // F3: color the confirmation by the actual action taken
                    // -- a reject confirmation must never render in the
                    // same success-green used for approve.
                    var confirmColor = action === 'reject' ? cssVar('--studio-neg') : cssVar('--studio-pos');
                    var msg = action === 'approve'
                        ? 'Approved — reload this page to view the wind-down checklist.'
                        : 'Rejected.';
                    var actionsRow = card.querySelector('.retirement-rec-actions');
                    if (actionsRow) {
                        actionsRow.innerHTML =
                            '<p style="color:' + confirmColor + ';font-size:0.875rem;font-weight:500;">' +
                            msg + '</p>';
                    }
                    // F4: the status pill must reflect the new decision
                    // immediately, not just the actions row -- otherwise a
                    // stale "Pending" pill sits next to the confirmation
                    // message until the page is reloaded.
                    var newStatus = action === 'reject' ? 'rejected' : 'approved';
                    var statusChip = card.querySelector('[data-testid="retirement-rec-status"]');
                    if (statusChip) {
                        statusChip.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
                        statusChip.className = 'status-pill adv-status ' + RETIREMENT_PILL_CLASS[newStatus];
                    }
                }
            } else {
                restore();
                alert(verb + ' failed: ' + (data && data.error ? data.error : 'unknown error'));
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Retirement "Run recommender" (Cycle 2e, AC-7).
    //
    // Poll cadence. This is NOT a background auto-refresh: it only ever runs
    // after an explicit operator click, is bounded to RETIREMENT_POLL_TIMEOUT_MS
    // (3 minutes), and reads one cheap read-only DB endpoint
    // (GET /api/retirement-recommendations) that never touches the engine's
    // minute-by-minute loop. The dashboard's 15 s floor applies to unattended
    // auto-refresh; a bounded, click-initiated wait for a ~1-2 minute job
    // warrants the tighter 5 s cadence so the page reloads promptly when the
    // run lands.
    // ---------------------------------------------------------------------------
    var RETIREMENT_POLL_INTERVAL_MS = 5000;
    var RETIREMENT_POLL_TIMEOUT_MS = 180000;

    /**
     * Start a retirement-recommender run on demand and reload the page when it
     * lands.
     *
     * POSTs to /ai-advisor/retirement/run (CSRF header from PSRequest),
     * which hands the multi-minute worker to a background thread and answers
     * 202 immediately -- or 409 {"status": "market_open"} while the market is
     * open (Run now is refused: the Composer API is shared with the live
     * engine; nothing starts and nothing is polled), or 409
     * {"status": "already_running"} when a run
     * (this one, or the scheduled 03:45 tick) is already in flight, in which
     * case we simply wait for THAT run. Then polls GET
     * /api/retirement-recommendations every RETIREMENT_POLL_INTERVAL_MS, for
     * at most RETIREMENT_POLL_TIMEOUT_MS, until last_run.run_id differs from
     * the run id on file before the POST, and calls location.reload() so the
     * server-rendered panel shows the new run. On timeout it says the run is
     * still going. Every status string is written with textContent (never
     * innerHTML).
     */
    async function retirementRunNow(btnEl) {
        var statusEl = document.querySelector('[data-testid="retirement-run-now-status"]');
        function setStatus(text) {
            if (statusEl) { statusEl.textContent = text; }
        }
        function sleep(ms) {
            return new Promise(function (resolve) { setTimeout(resolve, ms); });
        }
        // Run id of the latest recorded run (null when no run exists yet).
        async function readLastRunId() {
            var r = await fetch('/api/retirement-recommendations', {
                headers: { 'Accept': 'application/json' },
            });
            if (!r.ok) { throw new Error('HTTP ' + r.status); }
            var body = await r.json();
            return (body && body.last_run && body.last_run.run_id) ? body.last_run.run_id : null;
        }

        if (btnEl) { btnEl.disabled = true; }
        setStatus('Starting…');

        try {
            // The run id on file BEFORE we start: the poll below is done when it changes.
            var priorRunId = await readLastRunId();

            var resp = await PSRequest.postAction('/ai-advisor/retirement/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            });
            if (resp.networkError) { throw new Error('network error'); }
            if (resp.refused === 'csrf') {
                // Nothing started and nothing is sent again: the next click sends one request.
                setStatus(PSRequest.notSentText(resp, 'Click Run recommender again.'));
                if (btnEl) { btnEl.disabled = false; }
                return;
            }
            var data = resp.body;

            // The route answers 409 for BOTH market_open and already_running, so
            // this check must come first or the generic 409 branch swallows it.
            if (data && data.status === 'market_open') {
                setStatus('Run recommender is available outside market hours: the Composer API is shared with the live engine.');
                if (btnEl) { btnEl.disabled = false; }
                return;
            }
            if (resp.status === 409 || (data && data.status === 'already_running')) {
                setStatus('A run is already in progress — waiting for it to finish…');
            } else if (resp.status === 202 || (data && data.status === 'started')) {
                setStatus('Run started — this takes a minute or two…');
            } else {
                setStatus('Could not start the run' + (data && data.error ? ' (' + data.error + ')' : '') + '.');
                if (btnEl) { btnEl.disabled = false; }
                return;
            }

            var deadline = Date.now() + RETIREMENT_POLL_TIMEOUT_MS;
            while (Date.now() < deadline) {
                await sleep(RETIREMENT_POLL_INTERVAL_MS);
                try {
                    var currentRunId = await readLastRunId();
                    if (currentRunId && currentRunId !== priorRunId) {
                        setStatus('Run complete — reloading…');
                        // The reload opens the tab the operator was waiting on.
                        writeTabFragment('retirement');
                        location.reload();
                        return;
                    }
                } catch (pollErr) {
                    // Transient read failure: keep polling until the deadline.
                }
            }
            setStatus('The run is still going — reload this page in a few minutes to see the result.');
        } catch (err) {
            setStatus('Request failed: ' + err.message);
        }
        if (btnEl) { btnEl.disabled = false; }
    }

    // Expose on window so Jinja onclick="..." handlers can call them.
    window.openChatWithArtifact = openChatWithArtifact;
    window.sbRunAnalysis = sbRunAnalysis;
    window.frRunBuild = frRunBuild;
    window.frApprove = frApprove;
    window.frReject = frReject;
    window.retDispatchDecision = retDispatchDecision;
    window.retirementRunNow = retirementRunNow;

    // "Run review now": one bodyless POST. Statuses 202, 409, 401 and any other
    // non-200 show fixed text; a 403 (the session check) is said as not sent and
    // is never repeated; a 200 carrying an error key shows the server's error
    // text (an exception type name). Every message is set with textContent,
    // never a thrown error's message. A refusal gets the negative-token class;
    // the next attempt clears it. A 409 with status market_open says the market-hours refusal.
    function retRunReview(btnEl) {
        if (!btnEl || btnEl.disabled) { return; }
        btnEl.disabled = true;
        var messageEl = document.querySelector('[data-testid="tenure-review-run-message"]');
        function show(text, refused) {
            if (!messageEl) { return; }
            messageEl.textContent = text;
            messageEl.classList.toggle('tenure-review-run-message--refused', !!refused);
        }
        PSRequest.postAction('/ai-advisor/retirement/review/run', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
        }).then(function (res) {
            if (res.networkError) {
                show('Request failed: network error', true);
            } else if (res.refused === 'csrf') {
                // Nothing started and nothing is sent again: the next click sends one request.
                show(PSRequest.notSentText(res, 'Click Run review now again.'), true);
            } else if (res.status === 202) {
                show('Review started: reload this page when it finishes', false);
            } else if (res.status === 409) {
                if (res.body && res.body.status === 'market_open') {
                    show('Run tenure review is available outside market hours: the Composer API is shared with the live engine.', true);
                } else {
                    show('a review is already running', false);
                }
            } else if (res.status === 401) {
                show('Session expired: sign in again', true);
            } else if (res.status === 200) {
                var refusal = '';
                var body = res.body;
                if (body && typeof body.error === 'string') { refusal = body.error.trim(); }
                show(refusal || 'Request failed: HTTP 200', true);
            } else {
                show('Request failed: HTTP ' + res.status, true);
            }
            btnEl.disabled = false;
        });
    }
    window.retRunReview = retRunReview;

})();
