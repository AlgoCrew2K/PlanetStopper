/**
 * ai_advisor_chat.js — Chat panel client logic (M5, AC-4.1/4.2/4.3).
 *
 * The panel is always scoped to a specific artifact (a swap proposal,
 * logic-change proposal, or correlation figure).  It is opened by
 * openChatPanel(artifactData) and closed by closeChatPanel().
 *
 * AC-4.1 HARD constraint: this file must NEVER add apply/accept/deploy/trade
 * affordances.  The only actions are send-message and close-panel.
 *
 * All colors are resolved from --studio-* CSS custom properties so theme
 * changes propagate without a reload.
 */
(function () {
    'use strict';

    // -------------------------------------------------------------------------
    // Module state
    // -------------------------------------------------------------------------

    /** @type {Array<{role: string, content: string}>} */
    var _history = [];

    /** @type {{artifactId: string, artifactType: string, objective: string, gatePill: string, keyStat: string}|null} */
    var _currentArtifact = null;

    var _inFlight = false;

    // -------------------------------------------------------------------------
    // DOM refs (resolved lazily so this module works when the panel is not
    // present on page-load, e.g. when landing on a different advisor tab)
    // -------------------------------------------------------------------------

    function el(id) { return document.getElementById(id); }

    // -------------------------------------------------------------------------
    // HTML escape helper — always escape user/AI content before injection
    // -------------------------------------------------------------------------

    function esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // -------------------------------------------------------------------------
    // Gate pill colour from decision string
    // -------------------------------------------------------------------------

    function _gatePillStyle(decision) {
        if (!decision) {
            return 'color:var(--studio-ink-dim);border-color:var(--studio-border);';
        }
        if (decision === 'ADOPT_CANDIDATE' || decision === 'passed') {
            return 'color:var(--studio-pos);border-color:var(--studio-pos);background:rgba(31,122,77,0.07);';
        }
        if (decision === 'KEEP_INCUMBENT' || decision === 'withheld') {
            return 'color:var(--studio-warn);border-color:var(--studio-warn);background:var(--studio-warn-tint);';
        }
        return 'color:var(--studio-neg);border-color:var(--studio-neg);background:var(--studio-neg-tint);';
    }

    function _gatePillLabel(decision) {
        if (!decision) return '—';
        if (decision === 'ADOPT_CANDIDATE') return 'passed';
        if (decision === 'KEEP_INCUMBENT') return 'withheld';
        if (decision === 'REJECT_VETO_FAILED') return 'rejected';
        return decision;
    }

    // -------------------------------------------------------------------------
    // Open the chat panel, scoped to an artifact
    //
    // artifactData shape:
    //   {
    //     artifactId:   string,      // unique id for the artifact (e.g. swap proposal id)
    //     artifactType: string,      // e.g. "asset_swap" | "logic_change" | "correlation"
    //     title:        string,      // panel title, e.g. "Explain: IALT swap in Symphony Alpha"
    //     contextLabel: string,      // breadcrumb label, e.g. "Asset Swap · Symphony Alpha"
    //     objective:    string,      // artifact objective text (AC-4.2 grounding)
    //     gateDecision: string|null, // gate verdict decision enum
    //     keyStat:      string,      // e.g. "Sharpe 1.42" or "Correlation 0.77"
    //   }
    // -------------------------------------------------------------------------

    window.openChatPanel = function openChatPanel(artifactData) {
        _history = [];
        _currentArtifact = artifactData;

        // Update panel header
        var titleEl = el('chat-panel-title');
        if (titleEl) titleEl.textContent = artifactData.title || 'Explain';

        var ctxEl = el('chat-panel-context-label');
        if (ctxEl) ctxEl.textContent = artifactData.contextLabel || 'AI Advisor';

        // Update artifact summary strip
        var objEl = el('artifact-objective');
        if (objEl) objEl.textContent = artifactData.objective || '';

        var pillEl = el('artifact-gate-pill');
        if (pillEl) {
            pillEl.textContent = _gatePillLabel(artifactData.gateDecision);
            pillEl.setAttribute('style', _gatePillStyle(artifactData.gateDecision));
        }

        var statEl = el('artifact-key-stat');
        if (statEl) statEl.textContent = artifactData.keyStat || '';

        // Clear thread
        var thread = el('chat-thread');
        if (thread) thread.innerHTML = '';

        // Reset input
        var input = el('chat-input');
        if (input) {
            input.value = '';
            _syncSendBtn();
        }

        // Open panel
        var panel = document.querySelector('[data-testid="chat-panel"]');
        if (panel) panel.classList.add('chat-panel--open');
    };

    // -------------------------------------------------------------------------
    // Close the panel and clear state
    // -------------------------------------------------------------------------

    window.closeChatPanel = function closeChatPanel() {
        var panel = document.querySelector('[data-testid="chat-panel"]');
        if (panel) panel.classList.remove('chat-panel--open');
        _history = [];
        _currentArtifact = null;
    };

    // -------------------------------------------------------------------------
    // Send a message and stream the reply
    // -------------------------------------------------------------------------

    window.sendChatMessage = function sendChatMessage() {
        if (!window._chatAvailable) return;
        if (_inFlight) return;
        if (!_currentArtifact) return;

        var input = el('chat-input');
        if (!input) return;

        var message = input.value.trim();
        if (!message) return;

        _inFlight = true;
        _syncSendBtn();

        // Append operator bubble
        _appendBubble('operator', message);
        input.value = '';

        // Record in history before the request
        _history.push({ role: 'user', content: message });

        // Append in-flight indicator
        var thinkingId = 'chat-thinking-' + Date.now();
        _appendBubbleWithId('ai', 'Thinking…', thinkingId, true);

        fetch('/ai-advisor/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                artifact_id:   _currentArtifact.artifactId,
                artifact_type: _currentArtifact.artifactType,
                history:       _history.slice(0, -1),   // history before current message
                message:       message,
            }),
        })
            .then(function (resp) { return resp.json(); })
            .then(function (body) {
                // Remove thinking indicator
                var thinkingEl = document.getElementById(thinkingId);
                if (thinkingEl) thinkingEl.remove();

                if (body.error) {
                    _appendBubble('ai', 'Error: ' + body.error);
                    // Do not record error as AI turn — let the operator retry.
                    _history.pop();  // remove the user message that errored
                } else {
                    var reply = body.reply || '';
                    _appendBubble('ai', reply);
                    _history.push({ role: 'assistant', content: reply });
                }
            })
            .catch(function (err) {
                var thinkingEl = document.getElementById(thinkingId);
                if (thinkingEl) thinkingEl.remove();
                _appendBubble('ai', 'Request failed: ' + err.message);
                _history.pop();  // remove the user message that errored
            })
            .finally(function () {
                _inFlight = false;
                _syncSendBtn();
            });
    };

    // -------------------------------------------------------------------------
    // Bubble rendering helpers
    // -------------------------------------------------------------------------

    function _appendBubble(role, content) {
        _appendBubbleWithId(role, content, null, false);
    }

    function _appendBubbleWithId(role, content, id, isThinking) {
        var thread = el('chat-thread');
        if (!thread) return;

        var wrapper = document.createElement('div');
        if (id) wrapper.id = id;

        if (role === 'ai') {
            wrapper.className = 'chat-bubble--ai' + (isThinking ? ' chat-bubble--thinking' : '');
            wrapper.innerHTML =
                '<div class="chat-bubble-label">AI Advisor</div>' +
                '<div class="chat-bubble-body">' + esc(content) + '</div>';
        } else {
            wrapper.className = 'chat-bubble--operator';
            wrapper.innerHTML =
                '<div class="chat-bubble-body">' + esc(content) + '</div>';
        }

        thread.appendChild(wrapper);
        // Scroll to bottom
        thread.scrollTop = thread.scrollHeight;
    }

    // -------------------------------------------------------------------------
    // Sync send-button enabled state
    // -------------------------------------------------------------------------

    function _syncSendBtn() {
        var btn = el('chat-send-btn');
        if (!btn) return;
        var input = el('chat-input');
        var hasText = input ? input.value.trim().length > 0 : false;
        btn.disabled = _inFlight || !hasText || !_currentArtifact;
        btn.textContent = _inFlight ? 'Sending…' : 'Send';
    }

    // -------------------------------------------------------------------------
    // Textarea auto-grow + send-button sync
    // -------------------------------------------------------------------------

    document.addEventListener('DOMContentLoaded', function () {
        var input = el('chat-input');
        if (!input) return;

        input.addEventListener('input', function () {
            // Auto-grow up to max-height defined in CSS
            input.style.height = 'auto';
            input.style.height = input.scrollHeight + 'px';
            _syncSendBtn();
        });

        // Send on Enter (Shift+Enter inserts newline)
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendChatMessage();
            }
        });

        _syncSendBtn();
    });

})();
