/**
 * Per-symphony Settings Modal — settings-modal.js
 *
 * Opens via the gear icon on each symphony card (index.html). Fetches state from
 * GET /api/symphony-settings/<sym>, saves via POST /api/symphony-settings/<sym>.
 *
 * Design source of truth:
 *   .design-handoff/persymph-modal/claude-design/alphabotpm/project/settings-modal.jsx
 *
 * Rules enforced here:
 *   - All colors via --studio-* CSS custom properties; NO bare hex values. The modal's look is
 *     one stylesheet (MODAL_CSS, added once to the page head), so hover and focus states exist.
 *   - Autotuner parameters section: read-only spans/divs ONLY — no input elements.
 *   - AI advisor section: display-only cards, no action controls.
 *   - Live toggle OFF→ON requires ConfirmGoLive dialog; a bare click never persists live.
 *   - The CSRF token and the save's answer come from window.PSRequest (static/request_guard.js);
 *     a save the session check refuses is never sent again, the operator's next click sends it.
 *   - A response is applied only while it belongs to the open the modal is on: opening another
 *     symphony, or closing the modal, discards a late answer; Save posts only to the symphony whose
 *     values the modal loaded.
 *
 * Auto-refresh floor: the modal fetches fresh state only when opened, never on a
 * timer shorter than the engine's 1-minute cadence (arch constraint 1).
 */

(function () {
    'use strict';

    // ── Tuning parameter order + units (mirrors real DB schema keys) ──────────
    // These are the SCREAMING_SNAKE_CASE keys that autotuner writes via
    // save_symphony_strategy and DEFAULT_STRATEGY in database.py.
    var TUNING_ORDER = [
        'TRIGGER_THRESHOLD_PCT',
        'TAKE_PROFIT_MC_PCT',
        'MAX_SQUEEZE_FLOOR',
        'VWAP_CROSS_HWM_PCT',
        'PARABOLIC_VELOCITY_THRESHOLD',
        'MAX_PARABOLIC_SQUEEZE',
        'VWAP_BLEED_MULTIPLIER',
        'VWAP_BLEED_TICKS',
    ];

    var TUNING_UNITS = {
        TRIGGER_THRESHOLD_PCT:      '%',
        TAKE_PROFIT_MC_PCT:         '%',
        MAX_SQUEEZE_FLOOR:          '',
        VWAP_CROSS_HWM_PCT:         '%',
        PARABOLIC_VELOCITY_THRESHOLD: '',
        MAX_PARABOLIC_SQUEEZE:      '',
        VWAP_BLEED_MULTIPLIER:      '×',
        VWAP_BLEED_TICKS:           '',
    };

    // ── Modal state ───────────────────────────────────────────────────────────
    var _currentSymphonyId = null;
    // The symphony's display name from the last successful load. Reset on every open so a
    // symphony without one (or a failed load) never shows the previous symphony's name.
    var _currentDisplayName = null;
    var _modalData = null;         // last-fetched API response
    // The symphony _modalData was loaded for: Save posts only when it is the symphony the modal is on.
    var _modalSymphonyId = null;
    // One number per open (and per close): a response that is not for the newest one is discarded.
    var _openSeq = 0;
    var _pendingLockedVars = null; // local edits before Save
    // Track pending toggle state independently from _modalData.live_mode so
    // the toggle click has immediate visual effect without persisting (AC-3).
    var _pendingLiveMode = undefined;

    // ── Modal stylesheet ──────────────────────────────────────────────────────
    // One sheet, scoped under #sym-settings-modal, added to the head the first time the modal is
    // built. Type is 12 / 13 / 14 / 16 px at weight 400 or 500, spacing is on a 4 px grid, and a
    // status is a dot plus one word. Text on a filled control is the paper token: white on the
    // dark theme's lighter green and red is below 4.5:1.
    var MODAL_STYLE_ID = 'sym-settings-modal-style';
    var MODAL_CSS = [
        '#sym-settings-modal .ssm-surface{position:relative;z-index:1;width:560px;max-width:calc(100vw - 32px);',
        'max-height:calc(100vh - 56px);background:var(--studio-paper);border:1px solid var(--studio-rule-hi);',
        'border-radius:16px;box-shadow:0 24px 64px rgba(0,0,0,0.32);display:flex;flex-direction:column;overflow:hidden}',
        '#sym-settings-modal .ssm-head{display:flex;align-items:flex-start;gap:12px;flex-shrink:0;',
        'padding:16px 20px 12px;border-bottom:1px solid var(--studio-rule)}',
        '#sym-settings-modal .ssm-head-main{flex:1;min-width:0}',
        '#sym-settings-modal .ssm-kicker{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px;',
        'font-size:12px;line-height:16px;color:var(--studio-ink-dim);margin-bottom:4px}',
        '#sym-settings-modal .ssm-title{font-size:16px;line-height:24px;font-weight:500;color:var(--studio-ink);',
        'white-space:normal;overflow-wrap:anywhere}',
        '#sym-settings-modal .ssm-close{flex-shrink:0;width:32px;height:32px;background:transparent;',
        'border:1px solid var(--studio-rule-hi);border-radius:6px;color:var(--studio-ink-dim);font-size:16px;',
        'line-height:1;cursor:pointer}',
        '#sym-settings-modal .ssm-close:hover{background:var(--studio-chip-bg);color:var(--studio-ink)}',
        '#sym-settings-modal .ssm-body{flex:1;min-height:0;overflow:auto;padding:16px 20px;display:flex;',
        'flex-direction:column;gap:8px}',
        '#sym-settings-modal .ssm-body>*{flex-shrink:0}',
        '#sym-settings-modal .ssm-foot{flex-shrink:0;border-top:1px solid var(--studio-rule);padding:12px 20px;',
        'display:flex;flex-direction:column;gap:8px;background:var(--studio-paper)}',
        '#sym-settings-modal .ssm-state{padding:32px;text-align:center;font-size:13px;line-height:20px;',
        'color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-state--err{text-align:left;color:var(--studio-neg)}',
        '#sym-settings-modal .ssm-sec{display:flex;align-items:baseline;justify-content:space-between;gap:12px;',
        'margin-top:12px}',
        '#sym-settings-modal .ssm-sec:first-child{margin-top:0}',
        '#sym-settings-modal .ssm-sec-title{font-size:13px;line-height:20px;font-weight:500;color:var(--studio-ink)}',
        '#sym-settings-modal .ssm-sec-cap{font-size:12px;line-height:16px;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-note{flex:none;font-size:12px;line-height:16px;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-dot{display:inline-block;flex:none;width:8px;height:8px;border-radius:50%;',
        'background:currentColor}',
        '#sym-settings-modal .ssm-dot--pos{color:var(--studio-pos)}',
        '#sym-settings-modal .ssm-dot--neg{color:var(--studio-neg)}',
        '#sym-settings-modal .ssm-dot--warn{color:var(--studio-warn)}',
        '#sym-settings-modal .ssm-dot--accent{color:var(--studio-accent)}',
        '#sym-settings-modal .ssm-dot--muted{color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-status{display:inline-flex;align-items:center;gap:6px;font-size:12px;',
        'line-height:16px;font-weight:500;color:var(--studio-ink);white-space:nowrap}',
        '#sym-settings-modal .ssm-banner{display:flex;align-items:flex-start;gap:8px;padding:8px 12px;',
        'border:1px solid var(--studio-rule);border-radius:8px;font-size:12px;line-height:16px;color:var(--studio-ink)}',
        '#sym-settings-modal .ssm-banner .ssm-dot{margin-top:4px}',
        '#sym-settings-modal .ssm-banner strong{font-weight:500}',
        '#sym-settings-modal .ssm-banner--caution{background:var(--studio-warn-tint);border-color:var(--studio-warn-border)}',
        '#sym-settings-modal .ssm-banner--danger{background:var(--studio-neg-tint);border-color:var(--studio-neg-border)}',
        '#sym-settings-modal .ssm-banner--danger strong{color:var(--studio-neg)}',
        '#sym-settings-modal .ssm-exec{display:flex;align-items:center;gap:16px;padding:12px 16px;',
        'background:var(--studio-bg);border:1px solid var(--studio-rule);border-radius:8px}',
        '#sym-settings-modal .ssm-exec--live{background:var(--studio-neg-tint);border-color:var(--studio-neg-border)}',
        '#sym-settings-modal .ssm-exec--prearmed{background:var(--studio-warn-tint);border-color:var(--studio-warn-border)}',
        '#sym-settings-modal .ssm-exec-main{flex:1;min-width:0}',
        '#sym-settings-modal .ssm-exec-label{font-size:14px;line-height:20px;font-weight:500;color:var(--studio-ink)}',
        '#sym-settings-modal .ssm-prearmed{margin-left:8px;font-size:12px;font-weight:500;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-exec-sub{font-size:12px;line-height:16px;color:var(--studio-ink-dim);margin-top:2px}',
        '#sym-settings-modal .ssm-switch{flex-shrink:0;width:52px;height:30px;padding:2px;display:flex;',
        'align-items:center;justify-content:flex-start;background:var(--studio-rule-hi);',
        'border:1px solid var(--studio-rule-hi);border-radius:999px;cursor:pointer;',
        'transition:background-color .15s,border-color .15s}',
        '#sym-settings-modal .ssm-switch--on{justify-content:flex-end;background:var(--studio-neg);border-color:var(--studio-neg)}',
        '#sym-settings-modal .ssm-switch--prearmed{background:var(--studio-warn);border-color:var(--studio-warn)}',
        '#sym-settings-modal .ssm-knob{width:24px;height:24px;border-radius:999px;background:var(--studio-white);',
        'box-shadow:0 1px 2px rgba(0,0,0,0.2)}',
        '#sym-settings-modal .ssm-locks{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:4px}',
        '#sym-settings-modal .sym-settings-locked-var-btn{display:flex;align-items:center;gap:8px;padding:6px 8px;',
        'min-width:0;width:100%;text-align:left;cursor:pointer;background:transparent;',
        'border:1px solid var(--studio-rule);border-radius:6px;font-family:inherit}',
        '#sym-settings-modal .sym-settings-locked-var-btn:hover{background:var(--studio-chip-bg)}',
        '#sym-settings-modal .sym-settings-locked-var-btn[data-locked="1"]{background:var(--studio-accent-tint);',
        'border-color:var(--studio-accent-border)}',
        '#sym-settings-modal .ssm-box{flex:none;width:16px;height:16px;border-radius:4px;',
        'border:1.5px solid var(--studio-rule-hi);display:flex;align-items:center;justify-content:center;',
        'color:var(--studio-paper);font-size:12px;line-height:1}',
        '#sym-settings-modal [data-locked="1"] .ssm-box{background:var(--studio-accent);border-color:var(--studio-accent)}',
        '#sym-settings-modal .ssm-lockname{flex:1;min-width:0;font-size:12px;line-height:16px;font-weight:500;',
        'color:var(--studio-ink);font-variant-numeric:tabular-nums;white-space:normal;overflow-wrap:anywhere}',
        '#sym-settings-modal .ssm-lockedtag{flex:none;font-size:12px;line-height:16px;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-params{background:var(--studio-bg);border:1px solid var(--studio-rule);',
        'border-radius:8px;overflow:hidden}',
        '#sym-settings-modal .ssm-param{display:flex;align-items:center;justify-content:space-between;gap:12px;',
        'min-height:28px;padding:2px 12px;border-top:1px solid var(--studio-rule)}',
        '#sym-settings-modal .ssm-param:first-child{border-top:none}',
        '#sym-settings-modal .ssm-param-key{font-size:12px;line-height:16px;color:var(--studio-ink-dim);',
        'font-variant-numeric:tabular-nums;overflow-wrap:anywhere}',
        '#sym-settings-modal .ssm-param-key .ssm-dot{margin-left:8px}',
        '#sym-settings-modal .ssm-param-val{flex:none;font-size:13px;line-height:20px;font-weight:500;',
        'color:var(--studio-ink);font-variant-numeric:tabular-nums}',
        '#sym-settings-modal .ssm-adv-list{display:flex;flex-direction:column}',
        '#sym-settings-modal .ssm-adv{padding:8px 0;border-top:1px solid var(--studio-rule)}',
        '#sym-settings-modal .ssm-adv-line{display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px}',
        '#sym-settings-modal .ssm-adv-role{font-size:12px;line-height:16px;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-adv-count{margin-left:auto;font-size:12px;line-height:16px;',
        'color:var(--studio-ink-dim);font-variant-numeric:tabular-nums}',
        '#sym-settings-modal .ssm-adv-note{margin-top:2px;font-size:12px;line-height:16px;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-empty{padding:8px 0;font-size:12px;line-height:16px;color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-err{display:flex;align-items:center;gap:8px;font-size:12px;line-height:16px;',
        'font-weight:500;color:var(--studio-neg)}',
        '#sym-settings-modal .ssm-actions{display:flex;justify-content:flex-end;gap:8px}',
        '#sym-settings-modal .ssm-btn{height:32px;padding:0 16px;border:1px solid transparent;border-radius:6px;',
        'font-family:inherit;font-size:13px;font-weight:500;cursor:pointer}',
        '#sym-settings-modal .ssm-btn--ghost{background:transparent;border-color:var(--studio-rule-hi);color:var(--studio-ink-dim)}',
        '#sym-settings-modal .ssm-btn--ghost:hover{background:var(--studio-chip-bg);color:var(--studio-ink)}',
        '#sym-settings-modal .ssm-btn--primary{background:var(--studio-accent);color:var(--studio-paper)}',
        '#sym-settings-modal .ssm-btn--primary:hover:not(:disabled){background:color-mix(in srgb,var(--studio-accent) 86%,var(--studio-ink))}',
        '#sym-settings-modal .ssm-btn--danger{background:var(--studio-neg);color:var(--studio-paper)}',
        '#sym-settings-modal .ssm-btn--danger:hover:not(:disabled){background:color-mix(in srgb,var(--studio-neg) 86%,var(--studio-ink))}',
        '#sym-settings-modal .ssm-btn:disabled{opacity:.6;cursor:default}',
        '#sym-settings-modal .ssm-confirm{position:absolute;inset:0;z-index:5;display:flex;align-items:center;',
        'justify-content:center;padding:24px;background:rgba(0,0,0,0.5)}',
        '#sym-settings-modal .ssm-confirm-card{position:relative;z-index:1;width:420px;max-width:100%;',
        'background:var(--studio-paper);border:1px solid color-mix(in srgb,var(--studio-neg) 40%,transparent);',
        'border-radius:12px;overflow:hidden;box-shadow:0 24px 64px rgba(0,0,0,0.4)}',
        '#sym-settings-modal .ssm-confirm-head{display:flex;align-items:center;gap:8px;padding:12px 16px;',
        'font-size:14px;line-height:20px;font-weight:500;color:var(--studio-neg);background:var(--studio-neg-tint);',
        'border-bottom:1px solid var(--studio-neg-border)}',
        '#sym-settings-modal .ssm-confirm-body{padding:16px;font-size:13px;line-height:20px;color:var(--studio-ink)}',
        '#sym-settings-modal .ssm-confirm-foot{display:flex;justify-content:flex-end;gap:8px;padding:0 16px 16px}',
        '#sym-settings-modal :focus-visible{outline:2px solid var(--studio-accent);outline-offset:2px}',
        '@media (max-width:480px){',
        '#sym-settings-modal .ssm-head,#sym-settings-modal .ssm-foot{padding-left:16px;padding-right:16px}',
        '#sym-settings-modal .ssm-body{padding:12px 16px}',
        '#sym-settings-modal .ssm-state{padding:24px 16px}',
        '}'
    ].join('\n');

    function _ensureStyle() {
        if (document.getElementById(MODAL_STYLE_ID)) { return; }
        var style = document.createElement('style');
        style.id = MODAL_STYLE_ID;
        style.textContent = MODAL_CSS;
        (document.head || document.body).appendChild(style);
    }

    // ── Entry point: called by gear icon onclick ───────────────────────────────
    window.openSymphonySettings = function (evt, symphonyId) {
        if (evt) { evt.stopPropagation(); }
        var seq = ++_openSeq;
        _currentSymphonyId = symphonyId;
        _currentDisplayName = null;
        // The previous symphony's values are gone the moment another is opened, so no Save can
        // post them under the new one.
        _modalData = null;
        _modalSymphonyId = null;
        _pendingLockedVars = null;
        _pendingLiveMode = undefined;
        _renderLoadingModal();
        _showModal();
        fetch('/api/symphony-settings/' + encodeURIComponent(symphonyId))
            .then(function (r) {
                if (!r.ok) { throw new Error('HTTP ' + r.status); }
                return r.json();
            })
            .then(function (data) {
                // A newer open, or a close, makes this answer someone else's: draw nothing.
                if (seq !== _openSeq) { return; }
                _modalData = data;
                _modalSymphonyId = symphonyId;
                _currentDisplayName = (typeof data.display_name === 'string' && data.display_name)
                    ? data.display_name : null;
                _pendingLockedVars = (data.locked_vars || []).slice();
                _pendingLiveMode = undefined;
                _renderModal(false);
            })
            .catch(function () {
                if (seq !== _openSeq) { return; }
                _renderErrorModal('Failed to load settings. Please try again.');
            });
    };

    // ── Modal DOM helpers ──────────────────────────────────────────────────────

    function _getOrCreateModalEl() {
        _ensureStyle();
        var el = document.getElementById('sym-settings-modal');
        if (!el) {
            el = document.createElement('div');
            el.id = 'sym-settings-modal';
            el.setAttribute('role', 'dialog');
            el.setAttribute('aria-modal', 'true');
            el.setAttribute('aria-label', 'Symphony settings');
            // Fixed overlay — shown/hidden via display (same CSS pattern as #fleet-info-modal).
            el.style.cssText = [
                'display:none',
                'position:fixed',
                'inset:0',
                'z-index:9500',
                'align-items:center',
                'justify-content:center',
                'background:rgba(0,0,0,0.45)',
                'font-family:var(--studio-sans,Manrope),sans-serif',
            ].join(';');
            el.addEventListener('click', function (e) {
                if (e.target === el) { _closeModal(); }
            });
            document.body.appendChild(el);
        }
        return el;
    }

    function _showModal() {
        var el = _getOrCreateModalEl();
        el.style.display = 'flex';
        // Focus trap: first focusable element inside the surface on open.
        setTimeout(function () {
            var first = el.querySelector('button,input,[tabindex="0"]');
            if (first) { first.focus(); }
        }, 50);
        document.addEventListener('keydown', _onKeyDown);
    }

    function _closeModal() {
        // Closing ends this open: an answer still in flight for it is discarded when it lands.
        _openSeq += 1;
        var el = document.getElementById('sym-settings-modal');
        if (el) { el.style.display = 'none'; }
        _pendingLiveMode = undefined;
        document.removeEventListener('keydown', _onKeyDown);
    }

    // ESC key: close confirm dialog (id="sym-settings-confirm", aria-label="Confirm live trading")
    // first if open, otherwise close whole modal.
    // (WCAG 2.1 — ESC must close the topmost layer, not skip over dialogs.)
    function _onKeyDown(e) {
        if (e.key === 'Escape') {
            var confirm = document.getElementById('sym-settings-confirm');
            if (confirm) {
                confirm.parentNode.removeChild(confirm);
            } else {
                _closeModal();
            }
        }
        // Focus trap: Tab / Shift+Tab must cycle within the modal (WCAG 2.1.2).
        if (e.key === 'Tab') {
            var modal = document.getElementById('sym-settings-modal');
            if (!modal || modal.style.display === 'none') { return; }
            var focusable = Array.prototype.slice.call(
                modal.querySelectorAll('button:not([disabled]),input:not([disabled]),[tabindex="0"]')
            );
            if (focusable.length === 0) { return; }
            var first = focusable[0];
            var last = focusable[focusable.length - 1];
            if (e.shiftKey) {
                if (document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                }
            } else {
                if (document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
        }
    }

    // ── Loading / error stubs ──────────────────────────────────────────────────

    function _renderLoadingModal() {
        var el = _getOrCreateModalEl();
        el.innerHTML = _surfaceHtml(
            '<div class="ssm-state">Loading…</div>',
            '',
            '',
            false,
            false
        );
        _wireCloseButtons();
    }

    function _renderErrorModal(msg) {
        var el = _getOrCreateModalEl();
        el.innerHTML = _surfaceHtml(
            '<div class="ssm-state ssm-state--err">' + _esc(msg) + '</div>',
            '',
            '',
            false,
            true
        );
        _wireCloseButtons();
    }

    // ── Main render ───────────────────────────────────────────────────────────

    // `saveError`: false for none, true for the locked-state message, or a string to show as is
    // (a save that was not sent).
    function _renderModal(saveError) {
        if (!_modalData) { return; }
        var data = _modalData;
        var globalOn = !!data.global_live;
        // Use _pendingLiveMode if set by a toggle click; otherwise use the persisted value.
        var liveToggle = (_pendingLiveMode !== undefined) ? !!_pendingLiveMode : !!data.live_mode;
        var locked = _pendingLockedVars || (data.locked_vars || []).slice();
        var advisorList = data.advisor_observations || data.advisor || [];
        var parameters = data.parameters || data.params || {};

        // Effective-mode status (AC-2: 3-way truth table): a dot and the mode's words.
        var modeBadge;
        if (!globalOn) {
            modeBadge = _status('warn', 'Dry-run (global off)');
        } else if (liveToggle) {
            modeBadge = _status('neg', 'Live');
        } else {
            modeBadge = _status('muted', 'Dry-run');
        }

        // Banners
        var bannerHtml = '';
        if (!globalOn) {
            bannerHtml = _banner('caution',
                '<strong>Global live execution is off.</strong> ' +
                'This symphony will not trade live regardless of the toggle below — ' +
                'the system master-switch must be enabled first. This toggle pre-arms the symphony ' +
                'so it goes live automatically when the global switch is turned on.');
        } else if (liveToggle) {
            bannerHtml = _banner('danger',
                '<strong>Real money is at stake.</strong> ' +
                'This symphony will execute live sell orders via the connected brokerage account ' +
                '(Composer · Alpaca).');
        }

        // Execution mode row
        var execClass = 'ssm-exec';
        if (liveToggle && globalOn) {
            execClass += ' ssm-exec--live';
        } else if (liveToggle && !globalOn) {
            execClass += ' ssm-exec--prearmed';
        }

        var subtext;
        if (!liveToggle) {
            subtext = 'Dry-run — monitoring only, no real orders.';
        } else if (globalOn) {
            subtext = 'Live — real sell orders will execute on Composer · Alpaca.';
        } else {
            subtext = 'Pre-armed — will go live automatically once the global switch is on.';
        }

        var prearmedLabel = (!globalOn && liveToggle)
            ? '<span class="ssm-prearmed">· pre-armed</span>'
            : '';

        var toggleHtml = _toggleHtml(liveToggle, !globalOn);

        // Locked-vars checklist (AC-6: editable)
        var lockedVarsRows = TUNING_ORDER.map(function (key) {
            var isLocked = locked.indexOf(key) !== -1;
            var lockedTag = isLocked ? '<span class="ssm-lockedtag">locked</span>' : '';
            return '<button type="button" class="sym-settings-locked-var-btn"' +
                ' data-key="' + _esc(key) + '" data-locked="' + (isLocked ? '1' : '0') + '"' +
                ' aria-pressed="' + (isLocked ? 'true' : 'false') + '">' +
                '<span class="ssm-box">' + (isLocked ? '✓' : '') + '</span>' +
                // A name breaks after an underscore when it must wrap; it is never cut.
                '<span class="ssm-lockname">' + _esc(key).replace(/_/g, '_<wbr>') + '</span>' +
                lockedTag +
                '</button>';
        }).join('');

        // Autotuner parameters — READ-ONLY (AC-7: no input elements ever)
        var paramsRows = TUNING_ORDER.map(function (key) {
            var val = parameters[key];
            var formatted = (val !== undefined && val !== null) ? Number(val).toFixed(2) : '—';
            var isLocked = locked.indexOf(key) !== -1;
            var lockedDot = isLocked ? '<span class="ssm-dot ssm-dot--accent"></span>' : '';
            return '<div class="ssm-param">' +
                '<span class="ssm-param-key">' + _esc(key) + lockedDot + '</span>' +
                '<span class="ssm-param-val">' + _esc(formatted) + (TUNING_UNITS[key] || '') + '</span>' +
                '</div>';
        }).join('');

        // AI advisor rows — DISPLAY-ONLY, no action controls (AC-8). Observations that say the
        // same thing (same role, verdict and note) are one row with a count.
        var advisorHtml = _advisorRows(advisorList);
        if (advisorHtml === '') {
            advisorHtml = '<div class="ssm-empty">No advisor suggestions for this symphony yet.</div>';
        }

        // Save-error state (AC-12)
        var saveErrorText = typeof saveError === 'string'
            ? saveError
            : "Couldn't save — symphony state is locked by a running EOD cycle. Retry in a moment.";
        var footerExtra = saveError
            ? '<div role="alert" class="ssm-err">' +
              '<span class="ssm-dot ssm-dot--neg"></span>' +
              '<span>' + _esc(saveErrorText) + '</span>' +
              '</div>'
            : '';
        // A save that was not sent is clicked again as it was; only a failed one is a retry.
        var saveLabel = saveError === true ? 'Retry save' : 'Save settings';
        var saveClass = saveError === true ? 'ssm-btn ssm-btn--danger' : 'ssm-btn ssm-btn--primary';

        var footer = footerExtra +
            '<div class="ssm-actions">' +
            '<button type="button" id="sym-settings-cancel" class="ssm-btn ssm-btn--ghost">Cancel</button>' +
            '<button type="button" id="sym-settings-save" class="' + saveClass + '">' +
            saveLabel + '</button>' +
            '</div>';

        var body = bannerHtml +
            _sectionHeader('Execution mode', "Whether this symphony may send real sell orders; it trades live only when this switch and the server's global live switch are both on.", '', 'M04') +
            '<div class="' + execClass + '">' +
            '<div class="ssm-exec-main">' +
            '<div class="ssm-exec-label">' +
            'Live trading' + prearmedLabel + '</div>' +
            '<div class="ssm-exec-sub">' +
            _esc(subtext) + '</div>' +
            '</div>' +
            toggleHtml +
            '</div>' +

            _sectionHeader('Locked tuning variables', "Checked variables keep their value when the weekly autotune runs; the autotune may change the rest.", '', 'M05') +
            // Columns of at least 13rem: on a phone the controls stack one to a row, each with room
            // for its full name. Every block of the body keeps its own height (the body scrolls),
            // so a block with overflow hidden is never squashed to its first line.
            '<div class="ssm-locks">' +
            lockedVarsRows + '</div>' +

            _sectionHeader('Autotuner parameters', "The values the autotune set for this symphony, read-only here.", '', 'M06') +
            '<div class="ssm-params">' +
            paramsRows + '</div>' +

            _sectionHeader(
                'AI advisor — this symphony',
                "What the advisor said about this symphony; to act on it, lock a variable above or accept a suggestion on the Advisor page.",
                '<span class="ssm-note">Advisory only</span>',
                'M07'
            ) +
            advisorHtml +
            _glossary([
                ['M01', "Dry-run (global off): the server's live switch is off, so nothing trades live. Live: this symphony sends real sell orders. Dry-run: this symphony is watched but sends no orders."],
                ['M08', "Clear: the check passed. Watch: worth a look, not a failure. Breach: the check failed."]
            ]);

        var el = _getOrCreateModalEl();
        // modeBadge injected as 4th arg so _surfaceHtml places it in the header (AC-2).
        el.innerHTML = _surfaceHtml(body, footer, modeBadge, true, true);

        _wireCloseButtons();
        _wireLockedVarButtons();
        document.getElementById('sym-settings-save').addEventListener('click', _onSave);
    }

    // ── ConfirmGoLive dialog ──────────────────────────────────────────────────

    function _showConfirmDialog() {
        // After inserting, focus moves to sym-confirm-cancel so keyboard users
        // can reach the dialog buttons without tabbing through the outer modal.
        // The .focus() call below (post-insert) satisfies WCAG 2.1 focus management.
        var overlay = document.getElementById('sym-settings-modal');
        var existing = document.getElementById('sym-settings-confirm');
        if (existing) { existing.parentNode.removeChild(existing); }

        // The name leads; the Composer hash stays in a title attribute. With no name (or no id)
        // the stored identifier is shown unchanged.
        var symphonyLabel = _currentSymphonyId
            ? '<strong title="' + _esc(_currentSymphonyId) + '">' +
              _esc(_currentDisplayName || _currentSymphonyId) + '</strong>'
            : '<strong>this symphony</strong>';

        var html = '<div id="sym-settings-confirm" role="dialog" aria-modal="true"' +
            ' aria-label="Confirm live trading" class="ssm-confirm">' +
            '<div class="ssm-confirm-card">' +
            '<div class="ssm-confirm-head">' +
            '<span class="ssm-dot ssm-dot--neg"></span><span>Confirm live trading</span></div>' +
            '<div class="ssm-confirm-body">' +
            'You are about to enable real-money live trading for ' +
            symphonyLabel + '. ' +
            'This symphony will execute live sell orders via your connected brokerage account ' +
            'the next time an exit signal fires.</div>' +
            '<div class="ssm-confirm-foot">' +
            '<button type="button" id="sym-confirm-cancel" class="ssm-btn ssm-btn--ghost">Cancel</button>' +
            '<button type="button" id="sym-confirm-go-live" class="ssm-btn ssm-btn--danger">Yes, go live</button>' +
            '</div></div></div>';

        var surface = overlay.querySelector('[data-modal-surface]');
        if (surface) {
            var frag = document.createElement('div');
            frag.innerHTML = html;
            surface.appendChild(frag.firstElementChild);
        }

        // Move focus into the confirm dialog so keyboard users can reach the buttons (WCAG 2.1).
        var cancelBtn = document.getElementById('sym-confirm-cancel');
        if (cancelBtn) { cancelBtn.focus(); }

        document.getElementById('sym-confirm-cancel').addEventListener('click', function () {
            var d = document.getElementById('sym-settings-confirm');
            if (d) { d.parentNode.removeChild(d); }
        });

        document.getElementById('sym-confirm-go-live').addEventListener('click', function () {
            _doSave(true /* confirmed */);
        });
    }

    // ── Save logic ─────────────────────────────────────────────────────────────

    function _onSave() {
        if (!_modalData) { return; }
        var currentLive = !!_modalData.live_mode;
        var newLive = (_pendingLiveMode !== undefined) ? !!_pendingLiveMode : currentLive;

        if (newLive && !currentLive) {
            // OFF→ON transition requires the ConfirmGoLive dialog (AC-3).
            _showConfirmDialog();
        } else {
            _doSave(false);
        }
    }

    function _doSave(confirmed) {
        // Post only the values this modal loaded for the symphony it is open on.
        if (!_currentSymphonyId || !_modalData || _modalSymphonyId !== _currentSymphonyId) { return; }

        // Close confirm dialog if present.
        var d = document.getElementById('sym-settings-confirm');
        if (d) { d.parentNode.removeChild(d); }

        var currentLive = _modalData ? !!_modalData.live_mode : false;
        var newLive = (_pendingLiveMode !== undefined) ? !!_pendingLiveMode : currentLive;

        var payload = {
            live_mode: newLive,
            locked_vars: _pendingLockedVars || (_modalData ? (_modalData.locked_vars || []) : []),
        };
        if (newLive && confirmed) {
            payload.confirm = true;
        }

        var btn = document.getElementById('sym-settings-save');
        if (btn) {
            btn.textContent = 'Saving…';
            btn.disabled = true;
        }

        var seqAtSend = _openSeq;
        PSRequest.postAction('/api/symphony-settings/' + encodeURIComponent(_currentSymphonyId), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': PSRequest.csrfToken() || '',
            },
            body: JSON.stringify(payload),
        }).then(function (res) {
            // The modal was closed or reopened on another symphony while the save was in flight:
            // its answer is not for what is on screen now.
            if (seqAtSend !== _openSeq) { return; }
            if (res.refused === 'csrf') {
                // Nothing was saved and nothing is sent again. The edits stay (including a live flag
                // turned on), and the next Save asks the go-live confirmation again before it sends.
                _renderModal(res.tokenRefreshed
                    ? 'Not sent: the session check refused the save after a restart. Click Save settings again.'
                    : 'Not sent: the connection failed, so the session could not be refreshed. Click Save settings again.');
                return;
            }
            var body = res.body;
            if (res.networkError || !res.ok || body === null || (body && body.status === 'error')) {
                _renderModal(true /* saveError */);
                return;
            }
            // Reflect persisted state and reset pending flags.
            _modalData.live_mode = newLive;
            _pendingLiveMode = undefined;
            _renderModal(false);
        });
    }

    // ── Wire-up helpers ────────────────────────────────────────────────────────

    function _wireCloseButtons() {
        var closeBtn = document.getElementById('sym-settings-close');
        if (closeBtn) { closeBtn.addEventListener('click', _closeModal); }
        var cancelBtn = document.getElementById('sym-settings-cancel');
        if (cancelBtn) { cancelBtn.addEventListener('click', _closeModal); }
    }

    function _wireLockedVarButtons() {
        var btns = document.querySelectorAll('.sym-settings-locked-var-btn');
        btns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var key = btn.getAttribute('data-key');
                if (!key || !_pendingLockedVars) { return; }
                var idx = _pendingLockedVars.indexOf(key);
                if (idx === -1) {
                    _pendingLockedVars.push(key);
                } else {
                    _pendingLockedVars.splice(idx, 1);
                }
                _renderModal(false);
            });
        });

        // Wire the live-trading toggle button.
        var toggleBtn = document.getElementById('sym-settings-live-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function () {
                var currentLive = _modalData ? !!_modalData.live_mode : false;
                var pendingLive = (_pendingLiveMode !== undefined) ? !!_pendingLiveMode : currentLive;
                _pendingLiveMode = !pendingLive;
                // Re-render to reflect the pending toggle state visually (AC-3 badge reactivity).
                _renderModal(false);
            });
        }
    }

    // ── HTML primitives (vanilla, classes from MODAL_CSS) ─────────────────────

    // _surfaceHtml builds the modal shell.  modeBadgeHtml is injected into the header
    // so the effective-mode badge (AC-2) is always visible regardless of body scroll.
    // showIdentity is false while loading, when no identifier may be shown yet.
    function _surfaceHtml(bodyHtml, footerHtml, modeBadgeHtml, hasFooter, showIdentity) {
        var footerSection = hasFooter
            ? '<div class="ssm-foot">' + footerHtml + '</div>'
            : '';

        return '<div data-modal-surface class="ssm-surface">' +

            '<div class="ssm-head">' +
            '<div class="ssm-head-main">' +
            '<div class="ssm-kicker"><span>Settings</span>' +
            '<span id="sym-settings-mode-badge">' +
            (modeBadgeHtml || '') + '</span></div>' +
            '<div class="ssm-title"' +
            (showIdentity
                ? ' title="' + _esc(_currentSymphonyId || '') + '">' +
                  _esc(_currentDisplayName || _currentSymphonyId || '')
                : '>&nbsp;') +
            '</div>' +
            '</div>' +
            '<button type="button" id="sym-settings-close" class="ssm-close" aria-label="Close">×</button>' +
            '</div>' +

            // min-height:0 lets this flex child shrink below its content so it scrolls inside the
            // capped surface and the footer (Cancel, Save) stays on screen.
            '<div class="ssm-body">' +
            bodyHtml + '</div>' +

            footerSection +
            '</div>';
    }

    // `explainerKey` marks the caption as the description of that block.
    function _sectionHeader(title, caption, rightHtml, explainerKey) {
        var captionHtml = caption
            ? '<div class="ssm-sec-cap"' +
              (explainerKey ? ' data-explainer="' + explainerKey + '"' : '') + '>' +
              _esc(caption) + '</div>'
            : '';
        // One balanced element: the header row. The blocks that follow are siblings in the body's
        // column, so the footer (Cancel, Save) sits below the scrolling body, never inside it.
        return '<div class="ssm-sec">' +
            '<div>' +
            '<div class="ssm-sec-title">' + _esc(title) + '</div>' +
            captionHtml + '</div>' +
            (rightHtml || '') + '</div>';
    }

    // The modal's one closed Glossary, the markup templates/_glossary.html writes for the pages:
    // `lines` is a list of [key, text] pairs.
    function _glossary(lines) {
        return '<details class="hp-more" data-terms="glossary" data-testid="glossary" ' +
            'data-glossary-host="settings modal">' +
            '<summary>Glossary</summary>' +
            lines.map(function (t) {
                return '<p data-term="' + t[0] + '">' + _esc(t[1]) + '</p>';
            }).join('') +
            '</details>';
    }

    // A status is a dot and one word. `kind` picks the dot's colour; `labelHtml` is markup the
    // caller has already made safe.
    function _status(kind, labelHtml) {
        return '<span class="ssm-status"><span class="ssm-dot ssm-dot--' + kind + '"></span>' +
            labelHtml + '</span>';
    }

    function _banner(tone, innerHtml) {
        var dot = { caution: 'warn', danger: 'neg' }[tone] || 'muted';
        return '<div class="ssm-banner ssm-banner--' + tone + '">' +
            '<span class="ssm-dot ssm-dot--' + dot + '"></span>' +
            '<div>' + innerHtml + '</div></div>';
    }

    function _toggleHtml(on, prearmed) {
        var cls = 'ssm-switch' + (on ? (prearmed ? ' ssm-switch--on ssm-switch--prearmed' : ' ssm-switch--on') : '');
        return '<button type="button" id="sym-settings-live-toggle" class="' + cls + '"' +
            ' role="switch" aria-checked="' + (on ? 'true' : 'false') + '"' +
            ' aria-label="Live trading toggle">' +
            '<span class="ssm-knob"></span>' +
            '</button>';
    }

    var ADVISOR_VERDICT_DOT = { CLEAR: 'pos', BREACH: 'neg', WATCH: 'warn' };

    // The words of one stored observation row (advisor_role, verdict, raw_response), or null when
    // the row has neither a verdict nor prose and so has nothing to show.
    function _advisorParts(obs) {
        var verdict = typeof obs.verdict === 'string' ? obs.verdict : '';
        var body = obs.raw_response;
        var note = body && typeof body === 'object' && typeof body.note === 'string' ? body.note : '';
        if (!verdict && !note) return null;
        var role = typeof obs.advisor_role === 'string'
            ? obs.advisor_role.toLowerCase().replace(/_/g, ' ') : '';
        return { verdict: verdict, note: note, role: role.charAt(0).toUpperCase() + role.slice(1) };
    }

    // "NOT_APPLICABLE" reads "Not applicable".
    function _verdictWord(verdict) {
        var text = verdict.replace(/_/g, ' ').toLowerCase();
        return text.charAt(0).toUpperCase() + text.slice(1);
    }

    // One row per distinct (role, verdict, note), in the order each first appears, with how many
    // stored observations say it. Display-only: operators act by locking a variable and letting the
    // optimizer converge.
    function _advisorRows(list) {
        var order = [];
        var seen = {};
        (list || []).forEach(function (obs) {
            var parts = _advisorParts(obs);
            if (!parts) return;
            var key = [parts.role, parts.verdict, parts.note].join('\u0000');
            if (!seen[key]) {
                seen[key] = { parts: parts, count: 0 };
                order.push(key);
            }
            seen[key].count += 1;
        });
        if (!order.length) return '';
        return '<div class="ssm-adv-list">' + order.map(function (key) {
            return _advisorCard(seen[key].parts, seen[key].count);
        }).join('') + '</div>';
    }

    function _advisorCard(parts, count) {
        var status = parts.verdict
            ? _status(ADVISOR_VERDICT_DOT[parts.verdict.toUpperCase()] || 'muted', _esc(_verdictWord(parts.verdict)))
            : '';
        var countHtml = count > 1
            ? '<span class="ssm-adv-count" title="The same verdict and note, stored ' + count + ' times">' +
              count + ' observations</span>'
            : '';
        var noteRow = parts.note
            ? '<div class="ssm-adv-note">' + _esc(parts.note) + '</div>'
            : '';
        return '<div data-testid="sym-settings-advisor-card" class="ssm-adv">' +
            '<div class="ssm-adv-line">' + status +
            '<span class="ssm-adv-role">' + _esc(parts.role) + '</span>' + countHtml + '</div>' +
            noteRow +
            '</div>';
    }

    // ── Utility ───────────────────────────────────────────────────────────────

    function _esc(str) {
        return String(str || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

}());
