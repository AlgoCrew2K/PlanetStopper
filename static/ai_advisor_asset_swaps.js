/**
 * ai_advisor_asset_swaps.js — Asset Swaps tab logic.
 *
 * Handles operator-initiated swap evaluation form (AC-2.1).
 * Renders proposal cards with objective, stats, gate verdict, caveats,
 * and advise-only apply guidance (AC-2.3).  No apply/deploy/trade button
 * ever appears — this is an advise-only surface (AC-X1).
 *
 * All colors use CSS custom properties (--studio-*) so theme/accent changes
 * propagate without a reload.
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

    function fmtStat(val) {
        if (val === null || val === undefined || isNaN(Number(val))) return '—';
        return Number(val).toFixed(4);
    }

    function fmtPct(val) {
        if (val === null || val === undefined || isNaN(Number(val))) return '—';
        return (Number(val) >= 0 ? '+' : '') + Number(val).toFixed(2) + '%';
    }

    // ---------------------------------------------------------------------------
    // Form wire-up: enable Evaluate button only when all three fields are filled
    // ---------------------------------------------------------------------------

    var symphonySelect = document.getElementById('swap-symphony-select');
    var fromInput = document.getElementById('swap-from-ticker');
    var toInput = document.getElementById('swap-to-ticker');
    var evalBtn = document.getElementById('swap-evaluate-btn');
    var errorEl = document.getElementById('try-swap-error');
    var resultsArea = document.getElementById('swap-results-area');

    function syncBtn() {
        if (!evalBtn) return;
        var ready = (
            symphonySelect && symphonySelect.value &&
            fromInput && fromInput.value.trim() &&
            toInput && toInput.value.trim()
        );
        evalBtn.disabled = !ready;
    }

    if (symphonySelect) symphonySelect.addEventListener('change', syncBtn);
    if (fromInput) fromInput.addEventListener('input', syncBtn);
    if (toInput) toInput.addEventListener('input', syncBtn);

    syncBtn();

    // ---------------------------------------------------------------------------
    // Gate pill CSS class
    // ---------------------------------------------------------------------------

    function gatePillClass(decision) {
        if (decision === 'ADOPT_CANDIDATE') return 'gate-pill--passed';
        if (decision === 'KEEP_INCUMBENT') return 'gate-pill--withheld';
        return 'gate-pill--rejected';
    }

    function gatePillLabel(decision) {
        if (decision === 'ADOPT_CANDIDATE') return 'passed';
        if (decision === 'KEEP_INCUMBENT') return 'withheld';
        if (decision === 'REJECT_VETO_FAILED') return 'rejected';
        return decision || 'unknown';
    }

    // ---------------------------------------------------------------------------
    // Render a single swap proposal card
    // ---------------------------------------------------------------------------

    function renderSwapCard(result) {
        var isSurvivor = result.gate_decision === 'ADOPT_CANDIDATE';
        var isError = !!result.backtest_error;
        var cardClass = 'swap-card' +
            (isSurvivor ? ' swap-card--survivor' : '') +
            (isError ? ' swap-card--error' : '') +
            (!isSurvivor && !isError ? ' swap-card--rejected' : '');

        var testId = isSurvivor ? 'swap-card-survivor' : (isError ? 'swap-card-error' : 'swap-card-rejected');

        // Objective line
        var objectiveLine =
            '<div class="swap-card-objective" data-testid="swap-card-objective">' +
            escHtml(result.objective || '') +
            '</div>';

        // From → To heading
        var heading =
            '<div class="swap-card-heading">' +
            '<span class="swap-ticker">' + escHtml(result.from_ticker || '') + '</span>' +
            '<span class="swap-arrow">&rarr;</span>' +
            '<span class="swap-ticker swap-ticker--new">' + escHtml(result.to_ticker || '') + '</span>' +
            '</div>';

        // Backtest error short-circuit (AC-X5)
        if (isError) {
            return (
                '<div class="' + cardClass + '" data-testid="' + testId + '">' +
                objectiveLine +
                heading +
                '<div class="backtest-error" data-testid="backtest-error">' +
                escHtml(result.backtest_error) +
                '</div>' +
                '</div>'
            );
        }

        // Baseline vs variant stats
        var baseStats = result.baseline_stats || {};
        var varStats = result.variant_stats || {};

        var statsGrid =
            '<div class="stats-grid" data-testid="stats-grid">' +

            '<div>' +
            '<div class="stats-col-label">Baseline</div>' +
            '<div class="stat-row">' +
            '<span class="stat-label">Sharpe</span>' +
            '<span class="stat-value">' + escHtml(fmtStat(baseStats.sharpe_ratio)) + '</span>' +
            '</div>' +
            '<div class="stat-row">' +
            '<span class="stat-label">Sortino</span>' +
            '<span class="stat-value">' + escHtml(fmtStat(baseStats.sortino_ratio)) + '</span>' +
            '</div>' +
            '<div class="stat-row">' +
            '<span class="stat-label">Max DD</span>' +
            '<span class="stat-value">' + escHtml(fmtStat(baseStats.max_drawdown)) + '</span>' +
            '</div>' +
            '</div>' +

            '<div>' +
            '<div class="stats-col-label">Variant</div>' +
            '<div class="stat-row">' +
            '<span class="stat-label">Sharpe</span>' +
            '<span class="stat-value">' + escHtml(fmtStat(varStats.sharpe_ratio)) + '</span>' +
            '</div>' +
            '<div class="stat-row">' +
            '<span class="stat-label">Sortino</span>' +
            '<span class="stat-value">' + escHtml(fmtStat(varStats.sortino_ratio)) + '</span>' +
            '</div>' +
            '<div class="stat-row">' +
            '<span class="stat-label">Max DD</span>' +
            '<span class="stat-value">' + escHtml(fmtStat(varStats.max_drawdown)) + '</span>' +
            '</div>' +
            '</div>' +

            '</div>';

        // Gate verdict row
        var pillClass = gatePillClass(result.gate_decision);
        var pillLabel = gatePillLabel(result.gate_decision);
        var gateRow =
            '<div class="gate-verdict-row" data-testid="gate-verdict-row">' +
            '<span class="gate-pill ' + pillClass + '" data-testid="gate-pill">' +
            escHtml(pillLabel) +
            '</span>' +
            '<span class="gate-reason" data-testid="gate-reason">' +
            escHtml(result.gate_reason || '') +
            '</span>' +
            (result.validation_days
                ? '<span class="validation-days" data-testid="validation-days">n=' + result.validation_days + ' days</span>'
                : '') +
            '</div>';

        // Caveats (mandatory for survivors — AC-3.3)
        var caveatsHtml = '';
        if (result.caveats && result.caveats.length) {
            caveatsHtml =
                '<div class="caveats-block" data-testid="caveats-block">' +
                result.caveats.map(function (c) {
                    return '<div class="caveat-text">' + escHtml(c) + '</div>';
                }).join('') +
                '</div>';
        }

        // Advise-only apply guidance — always plain text, never a button (AC-X1)
        var applyGuidance = '';
        if (result.apply_guidance) {
            applyGuidance =
                '<div class="apply-guidance" data-testid="apply-guidance">' +
                '<strong>To apply:</strong> ' +
                escHtml(result.apply_guidance.replace(/^To apply:\s*/i, '')) +
                '</div>';
        }

        // Data warnings
        var dataWarnings = '';
        if (result.data_warnings && result.data_warnings.length) {
            dataWarnings =
                '<div class="data-warnings" data-testid="data-warnings">' +
                'Data warnings: ' + escHtml(JSON.stringify(result.data_warnings)) +
                '</div>';
        }

        // "Discuss this" affordance — subtle text link, intentionally de-emphasised (AC-4.1).
        // Clicking opens the chat panel scoped to this artifact.
        var artifactId = result.candidate_id || (result.from_ticker + '_' + result.to_ticker);
        var artifactObjective = result.objective || '';
        var artifactGate = result.gate_decision || '';
        var artifactKeyStat = result.baseline_stats && result.baseline_stats.sharpe_ratio != null
            ? 'Sharpe (baseline) ' + fmtStat(result.baseline_stats.sharpe_ratio) : '';
        var artifactTitle = 'Explain: ' + (result.from_ticker || '') + ' → ' + (result.to_ticker || '');
        var artifactCtx = 'Asset Swap';
        var swArtifactJson = JSON.stringify({
            artifactId:      artifactId,
            artifactType:    'asset_swap_proposal',
            title:           artifactTitle,
            contextLabel:    artifactCtx,
            objective:       artifactObjective,
            gateDecision:    artifactGate,
            keyStat:         artifactKeyStat,
            // artifactContext carries the full result dict for grounding (AC-4.2)
            artifactContext: result,
        });
        // "Chat about this" — prominent action button replacing the old de-emphasised
        // "Discuss this" link (AC-4). card-actions container uses flex-wrap:wrap so
        // buttons stay within card bounds at narrow viewports (AC-2).
        var chatBtn =
            '<div class="card-actions" style="margin-top:0.75rem;padding-top:0.625rem;' +
            'border-top:1px solid var(--studio-border);display:flex;flex-wrap:wrap;gap:0.5rem;">' +
            '<button class="chat-about-btn" data-testid="chat-about-this-btn"' +
            ' data-artifact-json=\'' + escHtml(swArtifactJson) + '\'' +
            ' style="padding:0.375rem 0.875rem;background:var(--studio-accent);color:var(--studio-white);' +
            'border:none;border-radius:0.5rem;font-size:0.8125rem;font-weight:600;cursor:pointer;' +
            'white-space:nowrap;"' +
            ' onclick="(function(e){var d=e.currentTarget.dataset.artifactJson;' +
            'try{if(typeof openChatPanel===\'function\'){openChatPanel(JSON.parse(d));}' +
            'else{sessionStorage.setItem("pendingChatArtifact",d);window.location.href="/ai-advisor/chat";}}' +
            'catch(ex){sessionStorage.setItem("pendingChatArtifact",d);window.location.href="/ai-advisor/chat";}})(event)">' +
            'Chat about this' +
            '</button>' +
            '</div>';

        return (
            '<div class="' + cardClass + '" data-testid="' + testId + '">' +
            objectiveLine +
            heading +
            statsGrid +
            gateRow +
            caveatsHtml +
            applyGuidance +
            dataWarnings +
            chatBtn +
            '</div>'
        );
    }

    // ---------------------------------------------------------------------------
    // Render the results area
    // ---------------------------------------------------------------------------

    function renderResults(result) {
        if (!resultsArea) return;

        // Single result from operator-initiated evaluation.
        var isSurvivor = result.gate_decision === 'ADOPT_CANDIDATE';

        var html = '';

        if (isSurvivor) {
            html +=
                '<div class="section-header" data-testid="survivors-header">' +
                'Swap Survivors (passed gate)' +
                '</div>' +
                '<div class="swap-cards">' +
                renderSwapCard(result) +
                '</div>';
        } else {
            // AC-2.5: zero survivors is a valid non-error outcome.
            html +=
                '<div class="no-survivors-state" data-testid="no-survivors-state">' +
                '<div class="no-survivors-title">No swap cleared the gate this run</div>' +
                '<div class="no-survivors-body">' +
                'The swap did not pass the acceptance gate. The result is shown below.' +
                '</div>' +
                '</div>';

            html +=
                '<div class="section-header" data-testid="rejected-header" ' +
                'style="margin-top:1.25rem;">' +
                'Rejected candidates' +
                '</div>' +
                '<div class="swap-cards">' +
                renderSwapCard(result) +
                '</div>';
        }

        resultsArea.innerHTML = html;
    }

    function renderError(msg) {
        if (!resultsArea) return;
        resultsArea.innerHTML =
            '<div class="no-survivors-state" style="border-color:' + cssVar('--studio-neg') + ';">' +
            '<div class="no-survivors-title" style="color:' + cssVar('--studio-neg') + ';">Evaluation failed</div>' +
            '<div class="no-survivors-body">' + escHtml(msg) + '</div>' +
            '</div>';
    }

    // ---------------------------------------------------------------------------
    // Evaluate button handler
    // ---------------------------------------------------------------------------

    function evaluateSwap() {
        if (!evalBtn || evalBtn.disabled) return;

        var symphonyId = symphonySelect ? symphonySelect.value : '';
        var fromTicker = fromInput ? fromInput.value.trim().toUpperCase() : '';
        var toTicker = toInput ? toInput.value.trim().toUpperCase() : '';

        if (errorEl) {
            errorEl.style.display = 'none';
            errorEl.textContent = '';
        }

        if (!symphonyId || !fromTicker || !toTicker) return;

        if (resultsArea) {
            resultsArea.innerHTML =
                '<div class="loading-state" data-testid="swap-loading-state">' +
                'Evaluating swap… this may take up to 30 seconds while the backtest runs.' +
                '</div>';
        }

        evalBtn.disabled = true;
        evalBtn.textContent = 'Evaluating…';

        fetch('/ai-advisor/asset-swaps/evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
            body: JSON.stringify({
                symphony_id: symphonyId,
                from_ticker: fromTicker,
                to_ticker: toTicker,
            }),
        })
            .then(function (resp) {
                // AC-9a: guard before JSON parse — a non-JSON error (e.g. 413 HTML
                // from nginx) must surface a clean message, not throw SyntaxError.
                if (!resp.ok) {
                    return resp.text().then(function (body) {
                        throw new Error("backtest unavailable (HTTP " + resp.status + ")");
                    });
                }
                return resp.json();
            })
            .then(function (body) {
                if (body.error) {
                    renderError(body.error);
                    return;
                }
                renderResults(body);
            })
            .catch(function (err) {
                renderError('Request failed: ' + err.message);
            })
            .finally(function () {
                evalBtn.disabled = false;
                evalBtn.textContent = 'Evaluate swap';
                syncBtn();
            });
    }

    if (evalBtn) {
        evalBtn.addEventListener('click', evaluateSwap);
    }

    // Allow Enter key in the ticker inputs to trigger evaluation.
    [fromInput, toInput].forEach(function (el) {
        if (el) {
            el.addEventListener('keydown', function (evt) {
                if (evt.key === 'Enter') evaluateSwap();
            });
        }
    });

})();
