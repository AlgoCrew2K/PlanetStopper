/**
 * ai_advisor_logic_changes.js
 *
 * Operator-initiated logic-change evaluation for the M4 Logic Changes tab.
 *
 * Wire-up:
 *   - Enable the "Evaluate logic change" button when symphony + description
 *     are both filled.
 *   - POST to /ai-advisor/logic-changes/evaluate, display loading / results
 *     / no-survivors / error states in #logic-results-area.
 *   - Read-only surface only: no Composer write calls, no auto-apply (AC-X1).
 */

(function () {
    "use strict";

    /** CSRF token fetched from GET /api/csrf-token on page load (AC-1). */
    var _csrfToken = null;
    document.addEventListener("DOMContentLoaded", function () {
        fetch("/api/csrf-token")
            .then(function (r) { return r.json(); })
            .then(function (b) { _csrfToken = b.csrf_token || null; })
            .catch(function () { /* csrf token unavailable — POSTs will 403 */ });
    });

    // ---------------------------------------------------------------------------
    // DOM references
    // ---------------------------------------------------------------------------

    const symphonySelect = document.getElementById("tweak-symphony-select");
    const objectiveSelect = document.getElementById("tweak-objective-select");
    const descriptionTextarea = document.getElementById("tweak-description");
    const evaluateBtn = document.getElementById("tweak-evaluate-btn");
    const errorDiv = document.getElementById("try-tweak-error");
    const resultsArea = document.getElementById("logic-results-area");

    // Guard: if we're on the no-api-key render path, none of these exist.
    if (!evaluateBtn || !resultsArea) {
        return;
    }

    // ---------------------------------------------------------------------------
    // Enable the evaluate button only when symphony + description are populated.
    // ---------------------------------------------------------------------------

    function _updateButtonState() {
        const sym = symphonySelect ? symphonySelect.value.trim() : "";
        const desc = descriptionTextarea ? descriptionTextarea.value.trim() : "";
        evaluateBtn.disabled = !(sym && desc);
    }

    if (symphonySelect) {
        symphonySelect.addEventListener("change", _updateButtonState);
    }
    if (descriptionTextarea) {
        descriptionTextarea.addEventListener("input", _updateButtonState);
    }
    _updateButtonState();

    // ---------------------------------------------------------------------------
    // Helpers: render HTML fragments
    // ---------------------------------------------------------------------------

    function _escapeHtml(s) {
        if (s == null) return "";
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    /**
     * Render one logic-change proposal card.
     *
     * Surfaces: objective, param tweak heading, baseline vs variant stats,
     * gate verdict + FDR metadata (AC-3.2 operator audit trail), caveats
     * (mandatory for survivors, AC-3.3), and advise-only apply guidance
     * (plain text, never a button, AC-X1 / AC-3.4).
     */
    function _renderProposalCard(proposal, isSurvivor) {
        const cardClass = isSurvivor
            ? "proposal-card proposal-card--survivor"
            : proposal.backtest_error
                ? "proposal-card proposal-card--error"
                : "proposal-card proposal-card--rejected";

        // Tweak heading: param_key old → new
        const paramKey = _escapeHtml(proposal.tweak_param_key || "");
        const oldVal = _escapeHtml(proposal.tweak_old_value != null ? String(proposal.tweak_old_value) : "");
        const newVal = _escapeHtml(proposal.tweak_new_value != null ? String(proposal.tweak_new_value) : "");

        const heading = paramKey
            ? `<div class="proposal-card-heading">
                 <span class="proposal-param-name">${paramKey}</span>
                 <span class="proposal-value--old">${oldVal}</span>
                 <span class="proposal-change-arrow">&#x2192;</span>
                 <span class="proposal-value--new">${newVal}</span>
               </div>`
            : "";

        // Objective
        const objType = _escapeHtml(proposal.objective_type || "");
        const objLine = objType
            ? `<div class="proposal-card-objective">${objType.replace(/_/g, " ")}</div>`
            : "";

        // Baseline vs variant stats (AC-3.3)
        let statsHtml = "";
        if (proposal.baseline_stats || proposal.variant_stats) {
            const b = proposal.baseline_stats || {};
            const v = proposal.variant_stats || {};

            function _statVal(val, pos_good) {
                if (val == null) return '<span class="stat-value">—</span>';
                const n = parseFloat(val);
                let cls = "stat-value";
                if (!isNaN(n)) {
                    if (pos_good) cls += n >= 0 ? " stat-value--pos" : " stat-value--neg";
                    else cls += n <= 0 ? " stat-value--pos" : " stat-value--neg";
                }
                return `<span class="${cls}">${_escapeHtml(String(val))}</span>`;
            }

            statsHtml = `
            <div class="stats-grid">
              <div>
                <div class="stats-col-label">Baseline</div>
                <div class="stat-row">
                  <span class="stat-label">Sharpe</span>
                  ${_statVal(b.sharpe_ratio, true)}
                </div>
                <div class="stat-row">
                  <span class="stat-label">Sortino</span>
                  ${_statVal(b.sortino_ratio, true)}
                </div>
                <div class="stat-row">
                  <span class="stat-label">Max DD</span>
                  ${_statVal(b.max_drawdown, false)}
                </div>
                <div class="stat-row">
                  <span class="stat-label">Ann. return</span>
                  ${_statVal(b.annual_return, true)}
                </div>
              </div>
              <div>
                <div class="stats-col-label">Variant</div>
                <div class="stat-row">
                  <span class="stat-label">Sharpe</span>
                  ${_statVal(v.sharpe_ratio, true)}
                </div>
                <div class="stat-row">
                  <span class="stat-label">Sortino</span>
                  ${_statVal(v.sortino_ratio, true)}
                </div>
                <div class="stat-row">
                  <span class="stat-label">Max DD</span>
                  ${_statVal(v.max_drawdown, false)}
                </div>
                <div class="stat-row">
                  <span class="stat-label">Ann. return</span>
                  ${_statVal(v.annual_return, true)}
                </div>
              </div>
            </div>`;
        }

        // Gate verdict row + FDR metadata (AC-3.2 audit trail)
        let gateHtml = "";
        if (proposal.gate_decision) {
            const decision = proposal.gate_decision;
            let pillClass = "gate-pill";
            let pillText = decision;
            if (decision === "ADOPT_CANDIDATE") {
                pillClass += " gate-pill--passed";
                pillText = "PASSED";
            } else if (decision === "REJECT_VETO_FAILED" || decision === "REJECT_FDR_FAILED") {
                pillClass += " gate-pill--rejected";
                pillText = "REJECTED";
            } else {
                pillClass += " gate-pill--withheld";
                pillText = "WITHHELD";
            }

            // FDR metadata for operator audit trail (AC-3.2):
            // Shows N candidates tested, adjusted threshold α, and this candidate's p.
            let fdrMetaHtml = "";
            if (proposal.n_candidates != null || proposal.fdr_q != null || proposal.winner_p_adj != null) {
                const nTested = proposal.n_candidates != null ? `N=${_escapeHtml(String(proposal.n_candidates))} tested` : "";
                const alpha = proposal.fdr_adjusted_threshold != null
                    ? `adjusted threshold &alpha;=${_escapeHtml(Number(proposal.fdr_adjusted_threshold).toFixed(4))}`
                    : "";
                const pThis = proposal.winner_p_adj != null
                    ? `this p=${_escapeHtml(Number(proposal.winner_p_adj).toFixed(4))} ${decision === "ADOPT_CANDIDATE" ? "PASSED" : "FAILED"}`
                    : "";
                const parts = [nTested, alpha, pThis].filter(Boolean);
                if (parts.length) {
                    fdrMetaHtml = `<span class="fdr-metadata">${parts.join(" &middot; ")}</span>`;
                }
            }

            const gateReason = _escapeHtml(proposal.gate_reason || "");
            gateHtml = `
            <div class="gate-verdict-row" data-testid="gate-verdict-row">
              <span class="${pillClass}" data-testid="gate-pill">${pillText}</span>
              ${gateReason ? `<span class="gate-reason">${gateReason}</span>` : ""}
              ${fdrMetaHtml}
            </div>`;
        }

        // Caveats (mandatory for survivors — AC-3.3)
        let caveatsHtml = "";
        if (proposal.caveats && proposal.caveats.length) {
            const caveatItems = proposal.caveats
                .map(c => `<p class="caveat-text">${_escapeHtml(c)}</p>`)
                .join("");
            caveatsHtml = `<div class="caveats-block" data-testid="caveats-block">${caveatItems}</div>`;
        }

        // Apply guidance — plain text, never a button (AC-X1 / AC-3.4)
        let guidanceHtml = "";
        if (proposal.apply_guidance) {
            guidanceHtml = `
            <div class="apply-guidance" data-testid="apply-guidance">
                <strong>To apply:</strong> ${_escapeHtml(proposal.apply_guidance)}
            </div>`;
        }

        // Backtest error (AC-X5)
        let errorHtml = "";
        if (proposal.backtest_error) {
            errorHtml = `<p class="backtest-error" data-testid="backtest-error">${_escapeHtml(proposal.backtest_error)}</p>`;
        }

        // Data warnings
        let warningsHtml = "";
        if (proposal.data_warnings && proposal.data_warnings.length) {
            warningsHtml = `<p class="data-warnings">${_escapeHtml(proposal.data_warnings.join("; "))}</p>`;
        }

        // "Discuss this" affordance — subtle text link, intentionally de-emphasised (AC-4.1).
        // Clicking opens the chat panel scoped to this artifact.
        const lcArtifactId = proposal.candidate_id || ("lc_" + (proposal.tweak_param_key || "unknown"));
        const lcObjective = proposal.objective_rationale || proposal.objective_type || "";
        const lcGateDecision = proposal.gate_decision || "";
        const lcKeyStat = proposal.baseline_stats && proposal.baseline_stats.sharpe_ratio != null
            ? `Sharpe (baseline) ${Number(proposal.baseline_stats.sharpe_ratio).toFixed(4)}`
            : "";
        const lcTitle = `Explain: logic change${proposal.tweak_param_key ? " (" + proposal.tweak_param_key + ")" : ""}`;
        const lcCtx = `Logic Change${proposal.symphony_id ? " · " + proposal.symphony_id : ""}`;
        const lcArtifactJson = JSON.stringify({
            artifactId:      lcArtifactId,
            artifactType:    "logic_change_proposal",
            title:           lcTitle,
            contextLabel:    lcCtx,
            objective:       lcObjective,
            gateDecision:    lcGateDecision,
            keyStat:         lcKeyStat,
            // artifactContext carries the full proposal dict for grounding (AC-4.2)
            artifactContext: proposal,
        });
        // "Chat about this" — prominent action button replacing the old de-emphasised
        // "Discuss this" link (AC-4). card-actions container uses flex-wrap:wrap so
        // buttons stay within card bounds at narrow viewports (AC-2).
        const chatBtn = `
        <div class="card-actions" style="margin-top:0.75rem;padding-top:0.625rem;border-top:1px solid var(--studio-border);display:flex;flex-wrap:wrap;gap:0.5rem;">
            <button class="chat-about-btn" data-testid="chat-about-this-btn"
               data-artifact-json='${_escapeHtml(lcArtifactJson)}'
               style="padding:0.375rem 0.875rem;background:var(--studio-accent);color:var(--studio-white);border:none;border-radius:0.5rem;font-size:0.8125rem;font-weight:600;cursor:pointer;white-space:nowrap;"
               onclick="(function(e){var d=e.currentTarget.dataset.artifactJson;try{if(typeof openChatPanel==='function'){openChatPanel(JSON.parse(d));}else{sessionStorage.setItem('pendingChatArtifact',d);window.location.href='/ai-advisor/chat';}}catch(ex){sessionStorage.setItem('pendingChatArtifact',d);window.location.href='/ai-advisor/chat';}})(event)">
                Chat about this
            </button>
        </div>`;

        return `
        <div class="${cardClass}" data-testid="proposal-card">
            ${objLine}
            ${heading}
            ${statsHtml}
            ${gateHtml}
            ${caveatsHtml}
            ${guidanceHtml}
            ${errorHtml}
            ${warningsHtml}
            ${chatBtn}
        </div>`;
    }

    /**
     * Render the full results area from the API JSON response.
     *
     * States:
     *   - no-survivors (valid, not an error) — AC-3.1
     *   - one or more survivors
     *   - rejected candidates section (de-emphasised)
     *   - backtest-failed / error
     */
    function _renderResults(data) {
        if (data.error) {
            return `<div class="no-survivors-state" data-testid="error-state">
                <div class="no-survivors-title">Evaluation error</div>
                <div class="no-survivors-body">${_escapeHtml(data.error)}</div>
            </div>`;
        }

        const survivors = data.survivors_detail || [];
        const rejected = data.rejected_detail || [];
        const noSurvivors = survivors.length === 0;

        let html = "";

        if (noSurvivors) {
            // AC-3.1: zero survivors is a valid non-error outcome.
            html += `<div class="no-survivors-state" data-testid="no-survivors-state">
                <div class="no-survivors-title">No survivors this run</div>
                <div class="no-survivors-body">${_escapeHtml(data.message || "no logic change cleared the gate this run")}</div>
            </div>`;
        } else {
            html += `<div class="section-header">Proposals — ${survivors.length} survivor${survivors.length !== 1 ? "s" : ""}</div>`;
            html += `<div class="proposal-cards" data-testid="proposal-cards">`;
            survivors.forEach(function (p) {
                html += _renderProposalCard(p, true);
            });
            html += `</div>`;
        }

        // Rejected candidates — always shown in a de-emphasised section (AC-3.1)
        if (rejected.length) {
            html += `<div class="rejected-section" data-testid="rejected-section">
                <div class="section-header">Rejected candidates</div>
                <div class="proposal-cards">`;
            rejected.forEach(function (p) {
                html += _renderProposalCard(p, false);
            });
            html += `</div></div>`;
        }

        return html;
    }

    // ---------------------------------------------------------------------------
    // Evaluate button: POST to /ai-advisor/logic-changes/evaluate
    // ---------------------------------------------------------------------------

    evaluateBtn.addEventListener("click", function () {
        if (evaluateBtn.disabled) return;

        const symphonyId = symphonySelect ? symphonySelect.value.trim() : "";
        const objectiveType = objectiveSelect ? objectiveSelect.value.trim() : "reduce_drawdown";
        const changeDescription = descriptionTextarea ? descriptionTextarea.value.trim() : "";

        if (!symphonyId || !changeDescription) return;

        // Clear previous error and show loading state.
        errorDiv.style.display = "none";
        errorDiv.textContent = "";
        resultsArea.innerHTML = '<div class="loading-state" data-testid="loading-state">Evaluating logic change&hellip; (this may take a moment)</div>';
        evaluateBtn.disabled = true;

        fetch("/ai-advisor/logic-changes/evaluate", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": _csrfToken || "" },
            body: JSON.stringify({
                symphony_id: symphonyId,
                objective_type: objectiveType,
                change_description: changeDescription,
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
            .then(function (data) {
                resultsArea.innerHTML = _renderResults(data);
            })
            .catch(function (err) {
                resultsArea.innerHTML = "";
                errorDiv.textContent = "Request failed: " + String(err);
                errorDiv.style.display = "";
            })
            .finally(function () {
                _updateButtonState();
            });
    });

})();
