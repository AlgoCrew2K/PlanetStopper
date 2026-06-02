/**
 * Per-symphony Settings Modal — settings-modal.js
 *
 * Opens via the gear icon on each symphony card (index.html) or table row
 * (table_partial.html).  Fetches state from GET /api/symphony-settings/<sym>,
 * saves via POST /api/symphony-settings/<sym>.
 *
 * Design source of truth:
 *   .design-handoff/persymph-modal/claude-design/alphabotpm/project/settings-modal.jsx
 *
 * Rules enforced here:
 *   - All colors via --studio-* CSS custom properties; NO bare hex values.
 *   - Autotuner parameters section: read-only spans/divs ONLY — no input elements.
 *   - AI advisor section: display-only cards, no action controls.
 *   - Live toggle OFF→ON requires ConfirmGoLive dialog; a bare click never persists live.
 *   - CSRF token fetched from /api/csrf-token on load (same pattern as ai_advisor.js).
 *
 * Auto-refresh floor: the modal fetches fresh state only when opened, never on a
 * timer shorter than the engine's 1-minute cadence (arch constraint 1).
 */

(function () {
    'use strict';

    // ── CSRF token (same fetch pattern as static/ai_advisor.js) ───────────────
    var _csrfToken = null;
    document.addEventListener('DOMContentLoaded', function () {
        fetch('/api/csrf-token')
            .then(function (r) { return r.json(); })
            .then(function (b) { _csrfToken = b.csrf_token || null; })
            .catch(function () { /* csrf token unavailable — POSTs will 403 */ });
    });

    // ── Ordered tuning parameters (mirrors settings-modal.jsx TUNING_ORDER) ───
    var TUNING_ORDER = [
        'trail_pct',
        'tp_target',
        'vwap_bleed_mult',
        'vol_scale',
        'mc_prob_floor',
        'para_ratchet_acc',
    ];

    var TUNING_UNITS = {
        trail_pct:        '%',
        tp_target:        '%',
        vwap_bleed_mult:  '×',
        vol_scale:        '×',
        mc_prob_floor:    '',
        para_ratchet_acc: '',
    };

    // ── Modal state ───────────────────────────────────────────────────────────
    var _currentSymphonyId = null;
    var _modalData = null;         // last-fetched API response
    var _pendingLockedVars = null; // local edits before Save
    var _confirmPending = false;   // ConfirmGoLive dialog visible

    // ── Entry point: called by gear icon onclick ───────────────────────────────
    window.openSymphonySettings = function (evt, symphonyId) {
        if (evt) { evt.stopPropagation(); }
        _currentSymphonyId = symphonyId;
        _pendingLockedVars = null;
        _confirmPending = false;
        _renderLoadingModal();
        _showModal();
        fetch('/api/symphony-settings/' + encodeURIComponent(symphonyId))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _modalData = data;
                _pendingLockedVars = (data.locked_vars || []).slice();
                _renderModal(false);
            })
            .catch(function () {
                _renderErrorModal('Failed to load settings. Please try again.');
            });
    };

    // ── Modal DOM helpers ──────────────────────────────────────────────────────

    function _getOrCreateModalEl() {
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
        // Focus trap: first focusable element inside the surface.
        setTimeout(function () {
            var first = el.querySelector('button,input,[tabindex]');
            if (first) { first.focus(); }
        }, 50);
        document.addEventListener('keydown', _onKeyDown);
    }

    function _closeModal() {
        var el = document.getElementById('sym-settings-modal');
        if (el) { el.style.display = 'none'; }
        _confirmPending = false;
        document.removeEventListener('keydown', _onKeyDown);
    }

    function _onKeyDown(e) {
        if (e.key === 'Escape') { _closeModal(); }
    }

    // ── Loading / error stubs ──────────────────────────────────────────────────

    function _renderLoadingModal() {
        var el = _getOrCreateModalEl();
        el.innerHTML = _surfaceHtml('<div style="padding:32px;text-align:center;color:var(--studio-ink-dim);font-size:13px;">Loading…</div>', '', false);
        _wireCloseButtons();
    }

    function _renderErrorModal(msg) {
        var el = _getOrCreateModalEl();
        el.innerHTML = _surfaceHtml('<div style="padding:32px;color:var(--studio-neg);font-size:13px;">' + _esc(msg) + '</div>', '', false);
        _wireCloseButtons();
    }

    // ── Main render ───────────────────────────────────────────────────────────

    function _renderModal(saveError) {
        if (!_modalData) { return; }
        var data = _modalData;
        var globalOn = !!data.global_live;
        var liveToggle = !!data.live_mode;
        var locked = _pendingLockedVars || (data.locked_vars || []).slice();
        var advisorList = data.advisor_observations || data.advisor || [];
        var parameters = data.parameters || data.params || {};
        var symphonyName = _currentSymphonyId || '';

        // Effective-mode badge (AC-2: 3-way truth table)
        var modeBadge;
        if (!globalOn) {
            modeBadge = _badge('caution', 'Dry-run (global off)');
        } else if (liveToggle) {
            modeBadge = _badge('live', '&#9679; Live');
        } else {
            modeBadge = _badge('dry', 'Dry-run');
        }

        // Banners
        var bannerHtml = '';
        if (!globalOn) {
            bannerHtml = _banner('caution',
                '<strong style="color:var(--studio-warn);">Global live execution is OFF.</strong> ' +
                'This symphony will not trade live regardless of the toggle below — ' +
                'the system master-switch must be enabled first. This toggle pre-arms the symphony ' +
                'so it goes live automatically when the global switch is turned on.');
        } else if (liveToggle) {
            bannerHtml = _banner('danger',
                '<strong style="color:var(--studio-neg);">Real money is at stake.</strong> ' +
                'This symphony will execute live sell orders via the connected brokerage account ' +
                '(Composer · Alpaca).');
        }

        // Execution mode row
        var execRowStyle = [
            'display:flex',
            'align-items:center',
            'gap:14px',
            'padding:13px 15px',
            liveToggle && globalOn
                ? 'background:color-mix(in srgb,var(--studio-neg) 6%,transparent);border:1px solid color-mix(in srgb,var(--studio-neg) 27%,transparent)'
                : (liveToggle && !globalOn
                    ? 'background:color-mix(in srgb,var(--studio-warn) 6%,transparent);border:1px solid color-mix(in srgb,var(--studio-warn) 27%,transparent)'
                    : 'background:var(--studio-bg);border:1px solid var(--studio-rule)'),
            'border-radius:10px',
        ].join(';');

        var subtext;
        if (!liveToggle) {
            subtext = 'Dry-run — monitoring only, no real orders.';
        } else if (globalOn) {
            subtext = 'Live — real sell orders will execute on Composer · Alpaca.';
        } else {
            subtext = 'Pre-armed — will go live automatically once the global switch is on.';
        }

        var prearmedLabel = (!globalOn && liveToggle)
            ? '<span style="margin-left:8px;font-size:9.5px;font-weight:700;color:var(--studio-warn);letter-spacing:0.08em;text-transform:uppercase;">· pre-armed</span>'
            : '';

        var toggleHtml = _toggleHtml(liveToggle, !globalOn);

        // Locked-vars checklist (AC-6: editable)
        var lockedVarsRows = TUNING_ORDER.map(function (key) {
            var isLocked = locked.indexOf(key) !== -1;
            return [
                '<button type="button"',
                '  class="sym-settings-locked-var-btn"',
                '  data-key="' + _esc(key) + '"',
                '  style="display:flex;align-items:center;gap:9px;padding:8px 10px;cursor:pointer;',
                '    text-align:left;width:100%;',
                '    background:' + (isLocked ? 'color-mix(in srgb,var(--studio-accent) 8%,transparent)' : 'transparent') + ';',
                '    border:1px solid ' + (isLocked ? 'color-mix(in srgb,var(--studio-accent) 25%,transparent)' : 'var(--studio-rule)') + ';',
                '    border-radius:8px;font-family:var(--studio-sans,Manrope),sans-serif;">',
                '  <span style="width:16px;height:16px;border-radius:4px;flex-shrink:0;',
                '    display:flex;align-items:center;justify-content:center;',
                '    background:' + (isLocked ? 'var(--studio-accent)' : 'transparent') + ';',
                '    border:1.5px solid ' + (isLocked ? 'var(--studio-accent)' : 'var(--studio-rule-hi)') + ';',
                '    color:#fff;font-size:11px;font-weight:800;">',
                isLocked ? '✓' : '',
                '  </span>',
                '  <span style="flex:1;font-size:12px;font-weight:500;color:var(--studio-ink);',
                '    font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">',
                _esc(key),
                '  </span>',
                isLocked ? '<span style="font-size:8.5px;font-weight:700;color:var(--studio-accent);letter-spacing:0.1em;padding:1px 5px;border:1px solid color-mix(in srgb,var(--studio-accent) 33%,transparent);border-radius:4px;">LOCKED</span>' : '',
                '</button>',
            ].join('');
        }).join('');

        // Autotuner parameters — READ-ONLY (AC-7: no input elements)
        var paramsRows = TUNING_ORDER.map(function (key, i) {
            var val = parameters[key];
            var formatted = (val !== undefined && val !== null) ? Number(val).toFixed(2) : '—';
            var isLocked = locked.indexOf(key) !== -1;
            var border = i < TUNING_ORDER.length - 1 ? 'border-bottom:1px solid var(--studio-rule);' : '';
            return [
                '<div style="display:flex;align-items:center;justify-content:space-between;',
                '  padding:9px 14px;' + border + '">',
                '  <span style="font-size:12px;color:var(--studio-ink-dim);font-variant-numeric:tabular-nums;">',
                _esc(key),
                isLocked ? '<span style="color:var(--studio-accent);margin-left:6px;font-size:10px;">◆</span>' : '',
                '  </span>',
                '  <span style="font-size:13px;font-weight:600;color:var(--studio-ink);font-variant-numeric:tabular-nums;">',
                _esc(formatted) + (TUNING_UNITS[key] || ''),
                '  </span>',
                '</div>',
            ].join('');
        }).join('');

        // AI advisor cards — DISPLAY-ONLY, no action controls (AC-8)
        var advisorHtml;
        if (!advisorList || advisorList.length === 0) {
            advisorHtml = [
                '<div style="padding:22px 16px;text-align:center;',
                '  background:var(--studio-bg);border:1px dashed var(--studio-rule-hi);border-radius:10px;',
                '  font-size:12.5px;color:var(--studio-ink-faint);">',
                'No advisor suggestions for this symphony yet.',
                '</div>',
            ].join('');
        } else {
            advisorHtml = advisorList.map(function (obs) {
                return _advisorCard(obs);
            }).join('');
        }

        // Save-error state footer (AC-12)
        var footerExtra = saveError
            ? '<div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--studio-neg);font-weight:600;margin-bottom:6px;">' +
              '<span style="font-size:13px;">✕</span>' +
              "Couldn’t save — symphony state is locked by a running EOD cycle. Retry in a moment." +
              '</div>'
            : '';
        var saveLabel = saveError ? 'Retry save' : 'Save settings';
        var saveBg = saveError ? 'var(--studio-neg)' : 'var(--studio-accent)';

        var body = [
            bannerHtml,

            // Section: Execution mode
            _sectionHeader('Execution mode', ''),
            '<div style="' + execRowStyle + '">',
            '  <div style="flex:1;">',
            '    <div style="font-size:13.5px;font-weight:700;color:var(--studio-ink);">',
            'Live trading' + prearmedLabel,
            '    </div>',
            '    <div style="font-size:11.5px;color:var(--studio-ink-dim);margin-top:3px;line-height:1.45;">',
            _esc(subtext),
            '    </div>',
            '  </div>',
            toggleHtml,
            '</div>',

            // Section: Locked tuning variables
            _sectionHeader('Locked tuning variables', 'Checked vars the autotuner may NOT change.'),
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;',
            '  background:var(--studio-bg);border:1px solid var(--studio-rule);border-radius:10px;padding:12px;">',
            lockedVarsRows,
            '</div>',

            // Section: Autotuner parameters (read-only)
            _sectionHeader('Autotuner parameters', 'Read-only, autotuner-owned.'),
            '<div style="background:var(--studio-bg);border:1px solid var(--studio-rule);border-radius:10px;overflow:hidden;">',
            paramsRows,
            '</div>',

            // Section: AI advisor — this symphony (display-only, AC-8)
            _sectionHeader('AI advisor — this symphony',
                'Display-only. Act by locking a variable and letting the optimizer converge.',
                _badge('neutral', 'Advisory only')),
            advisorHtml,
        ].join('');

        var footer = [
            footerExtra,
            '<div style="display:flex;justify-content:flex-end;gap:10px;">',
            '  <button type="button" id="sym-settings-cancel"',
            '    style="padding:9px 16px;background:transparent;',
            '    border:1px solid var(--studio-rule);border-radius:8px;cursor:pointer;',
            '    font-family:var(--studio-sans,Manrope),sans-serif;',
            '    font-size:12.5px;font-weight:600;color:var(--studio-ink-dim);">',
            '    Cancel',
            '  </button>',
            '  <button type="button" id="sym-settings-save"',
            '    style="padding:9px 18px;background:' + saveBg + ';color:#fff;',
            '    border:none;border-radius:8px;cursor:pointer;',
            '    font-family:var(--studio-sans,Manrope),sans-serif;',
            '    font-size:12.5px;font-weight:700;letter-spacing:0.02em;">',
            saveLabel,
            '  </button>',
            '</div>',
        ].join('');

        var el = _getOrCreateModalEl();
        el.innerHTML = _surfaceHtml(body, footer, true);

        // Wire up header close + cancel + save + locked-var toggles
        _wireCloseButtons();
        _wireLockedVarButtons();

        document.getElementById('sym-settings-save').addEventListener('click', _onSave);
    }

    // ── ConfirmGoLive dialog ──────────────────────────────────────────────────

    function _showConfirmDialog() {
        var overlay = document.getElementById('sym-settings-modal');
        var existing = document.getElementById('sym-settings-confirm');
        if (existing) { existing.parentNode.removeChild(existing); }

        var symphonyLabel = _currentSymphonyId || 'this symphony';

        var html = [
            '<div id="sym-settings-confirm" role="dialog" aria-modal="true" aria-label="Confirm live trading"',
            '  style="position:absolute;inset:0;z-index:5;display:flex;align-items:center;',
            '  justify-content:center;padding:28px;background:rgba(0,0,0,0.5);">',
            '  <div style="position:relative;z-index:1;width:420px;max-width:100%;',
            '    background:var(--studio-paper);border:1px solid color-mix(in srgb,var(--studio-neg) 40%,transparent);',
            '    border-radius:16px;overflow:hidden;',
            '    box-shadow:0 24px 64px rgba(0,0,0,0.4);">',
            '    <div style="padding:16px 18px 14px;',
            '      background:color-mix(in srgb,var(--studio-neg) 6%,transparent);',
            '      border-bottom:1px solid color-mix(in srgb,var(--studio-neg) 20%,transparent);">',
            '      <div style="font-size:15px;font-weight:700;color:var(--studio-neg);',
            '        display:flex;align-items:center;gap:8px;">',
            '        <span>⚠</span> Confirm live trading',
            '      </div>',
            '    </div>',
            '    <div style="padding:16px 18px;font-size:13px;color:var(--studio-ink);line-height:1.55;">',
            'You are about to enable real-money live trading for ',
            '      <strong>' + _esc(symphonyLabel) + '</strong>. ',
            'This symphony will execute live sell orders via your connected brokerage account ',
            'the next time an exit signal fires.',
            '    </div>',
            '    <div style="padding:12px 18px 16px;display:flex;justify-content:flex-end;gap:10px;">',
            '      <button type="button" id="sym-confirm-cancel"',
            '        style="padding:9px 16px;background:transparent;',
            '        border:1px solid var(--studio-rule);border-radius:8px;cursor:pointer;',
            '        font-family:var(--studio-sans,Manrope),sans-serif;',
            '        font-size:12.5px;font-weight:600;color:var(--studio-ink-dim);">',
            '        Cancel',
            '      </button>',
            '      <button type="button" id="sym-confirm-go-live"',
            '        style="padding:9px 18px;background:var(--studio-neg);color:#fff;',
            '        border:none;border-radius:8px;cursor:pointer;',
            '        font-family:var(--studio-sans,Manrope),sans-serif;',
            '        font-size:12.5px;font-weight:700;letter-spacing:0.02em;">',
            '        Yes, go live',
            '      </button>',
            '    </div>',
            '  </div>',
            '</div>',
        ].join('');

        var surface = overlay.querySelector('[data-modal-surface]');
        if (surface) {
            var frag = document.createElement('div');
            frag.innerHTML = html;
            surface.appendChild(frag.firstElementChild);
        }

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
        // If toggling to live, show ConfirmGoLive dialog first (AC-3).
        var currentLive = !!_modalData.live_mode;
        // _pendingLiveMode is set by the toggle click handler.
        var newLive = (_pendingLiveMode !== undefined) ? _pendingLiveMode : currentLive;

        if (newLive && !currentLive) {
            // OFF→ON: requires confirmation.
            _showConfirmDialog();
        } else {
            _doSave(false);
        }
    }

    // Track the pending toggle state (set by toggle click, before Save).
    var _pendingLiveMode;

    function _doSave(confirmed) {
        if (!_currentSymphonyId) { return; }

        // Close confirm dialog if open.
        var d = document.getElementById('sym-settings-confirm');
        if (d) { d.parentNode.removeChild(d); }

        var currentLive = _modalData ? !!_modalData.live_mode : false;
        var newLive = (_pendingLiveMode !== undefined) ? _pendingLiveMode : currentLive;

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

        fetch('/api/symphony-settings/' + encodeURIComponent(_currentSymphonyId), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': _csrfToken || '',
            },
            body: JSON.stringify(payload),
        })
            .then(function (r) {
                if (!r.ok) { throw new Error('HTTP ' + r.status); }
                return r.json();
            })
            .then(function (body) {
                if (body && body.status === 'error') {
                    throw new Error(body.message || 'Save failed');
                }
                // Refresh modal state after successful save.
                _modalData.live_mode = newLive;
                _pendingLiveMode = undefined;
                _renderModal(false);
            })
            .catch(function () {
                _renderModal(true /* saveError */);
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
                if (!key) { return; }
                var idx = _pendingLockedVars.indexOf(key);
                if (idx === -1) {
                    _pendingLockedVars.push(key);
                } else {
                    _pendingLockedVars.splice(idx, 1);
                }
                // Re-render with updated locked state.
                _renderModal(false);
            });
        });

        // Wire the toggle button (live trading).
        var toggleBtn = document.getElementById('sym-settings-live-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function () {
                var currentLive = _modalData ? !!_modalData.live_mode : false;
                var pendingLive = (_pendingLiveMode !== undefined) ? _pendingLiveMode : currentLive;
                _pendingLiveMode = !pendingLive;
                // Reflect toggle in UI without persisting.
                _modalData._pendingLiveToggle = _pendingLiveMode;
                _renderModal(false);
            });
        }
    }

    // ── HTML primitives (vanilla, --studio-* tokens only) ─────────────────────

    function _surfaceHtml(bodyHtml, footerHtml, hasFooter) {
        return [
            // Scrim (click closes) — handled by overlay listener.
            '<div data-modal-surface style="',
            '  position:relative;z-index:1;',
            '  width:560px;max-width:calc(100vw - 32px);',
            '  max-height:calc(100vh - 56px);',
            '  background:var(--studio-paper);',
            '  border:1px solid var(--studio-rule-hi);',
            '  border-radius:16px;',
            '  box-shadow:0 24px 64px rgba(0,0,0,0.32);',
            '  display:flex;flex-direction:column;overflow:hidden;">',

            // Header
            '  <div style="padding:16px 18px;border-bottom:1px solid var(--studio-rule);',
            '    display:flex;align-items:flex-start;gap:12px;flex-shrink:0;">',
            '    <div style="flex:1;min-width:0;">',
            '      <div style="font-size:9.5px;font-weight:700;color:var(--studio-ink-faint);',
            '        letter-spacing:0.14em;text-transform:uppercase;margin-bottom:4px;">',
            '        Settings',
            '      </div>',
            '      <div style="font-size:16px;font-weight:700;color:var(--studio-ink);',
            '        letter-spacing:-0.01em;line-height:1.25;',
            '        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"',
            '        title="' + _esc(_currentSymphonyId || '') + '">',
            _esc(_currentSymphonyId || ''),
            '      </div>',
            '      <div style="margin-top:7px;" id="sym-settings-mode-badge">',
            // Mode badge will be inserted inside body — skip here for loading state.
            '      </div>',
            '    </div>',
            '    <button type="button" id="sym-settings-close" aria-label="Close"',
            '      style="width:30px;height:30px;flex-shrink:0;',
            '      background:transparent;border:1px solid var(--studio-rule);border-radius:7px;',
            '      color:var(--studio-ink-dim);cursor:pointer;font-size:15px;">',
            '      ×',
            '    </button>',
            '  </div>',

            // Body
            '  <div style="flex:1;overflow:auto;padding:18px;display:flex;flex-direction:column;gap:20px;">',
            bodyHtml,
            '  </div>',

            // Footer
            hasFooter
                ? '<div style="flex-shrink:0;border-top:1px solid var(--studio-rule);padding:14px 18px;',
                  '  display:flex;flex-direction:column;gap:10px;background:var(--studio-paper);">',
                  footerHtml,
                  '</div>'
                : '',

            '</div>',
        ].join('');
    }

    function _sectionHeader(title, caption, rightHtml) {
        return [
            '<div style="display:flex;flex-direction:column;gap:10px;">',
            '  <div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;">',
            '    <div>',
            '      <div style="font-size:11px;font-weight:700;color:var(--studio-ink);',
            '        letter-spacing:0.1em;text-transform:uppercase;">',
            _esc(title),
            '      </div>',
            caption
                ? '<div style="font-size:11px;color:var(--studio-ink-faint);margin-top:3px;">' + _esc(caption) + '</div>'
                : '',
            '    </div>',
            rightHtml || '',
            '  </div>',
        ].join('');
    }

    function _badge(kind, label) {
        var styles = {
            live:    'background:var(--studio-neg);border-color:var(--studio-neg);color:#fff;',
            caution: 'background:color-mix(in srgb,var(--studio-warn) 8%,transparent);border-color:color-mix(in srgb,var(--studio-warn) 53%,transparent);color:var(--studio-warn);',
            dry:     'background:var(--studio-chip-bg);border-color:var(--studio-rule);color:var(--studio-ink-dim);',
            neutral: 'background:transparent;border-color:var(--studio-rule);color:var(--studio-ink-dim);',
            pass:    'background:color-mix(in srgb,var(--studio-pos) 8%,transparent);border-color:color-mix(in srgb,var(--studio-pos) 53%,transparent);color:var(--studio-pos);',
            fail:    'background:color-mix(in srgb,var(--studio-neg) 8%,transparent);border-color:color-mix(in srgb,var(--studio-neg) 53%,transparent);color:var(--studio-neg);',
        }[kind] || 'background:transparent;border-color:var(--studio-rule);color:var(--studio-ink-dim);';

        return [
            '<span style="display:inline-flex;align-items:center;gap:6px;',
            '  padding:3px 9px;border-radius:999px;',
            styles,
            '  border:1px solid;',
            '  font-size:9.5px;font-weight:700;letter-spacing:0.08em;',
            '  text-transform:uppercase;white-space:nowrap;">',
            label,
            '</span>',
        ].join('');
    }

    function _banner(tone, innerHtml) {
        var colorVar = { caution: 'var(--studio-warn)', danger: 'var(--studio-neg)', info: 'var(--studio-cyan)' }[tone] || 'var(--studio-ink-dim)';
        var glyph = (tone === 'danger' || tone === 'caution') ? '⚠' : 'ⓘ';
        return [
            '<div style="display:flex;gap:11px;align-items:flex-start;',
            '  padding:11px 14px 11px 12px;',
            '  background:color-mix(in srgb,' + colorVar + ' 7%,transparent);',
            '  border:1px solid color-mix(in srgb,' + colorVar + ' 33%,transparent);',
            '  border-left:3px solid ' + colorVar + ';',
            '  border-radius:8px;">',
            '  <span style="color:' + colorVar + ';font-size:14px;font-weight:800;line-height:1.3;">' + glyph + '</span>',
            '  <div style="font-size:12px;color:var(--studio-ink);line-height:1.5;">',
            innerHtml,
            '  </div>',
            '</div>',
        ].join('');
    }

    function _toggleHtml(on, prearmed) {
        // Track color: prearmed=warn, live=neg, off=chip-bg.
        var trackColor = prearmed ? 'var(--studio-warn)' : 'var(--studio-neg)';
        var bgColor = on ? trackColor : 'var(--studio-chip-bg)';
        var borderColor = on ? trackColor : 'var(--studio-rule-hi)';
        var justify = on ? 'flex-end' : 'flex-start';
        var stripes = (prearmed && on)
            ? 'background-image:repeating-linear-gradient(45deg,transparent,transparent 4px,rgba(255,255,255,0.18) 4px,rgba(255,255,255,0.18) 8px);'
            : '';
        return [
            '<button type="button" id="sym-settings-live-toggle"',
            '  role="switch" aria-checked="' + (on ? 'true' : 'false') + '"',
            '  aria-label="Live trading toggle"',
            '  style="width:52px;height:30px;padding:2px;flex-shrink:0;',
            '  background:' + bgColor + ';',
            '  border:1px solid ' + borderColor + ';',
            '  border-radius:999px;cursor:pointer;',
            '  display:flex;align-items:center;justify-content:' + justify + ';',
            '  transition:background .15s,border-color .15s;',
            stripes,
            '">',
            '  <span style="width:24px;height:24px;border-radius:999px;',
            '    background:#fff;box-shadow:0 1px 2px rgba(0,0,0,0.2);">',
            '  </span>',
            '</button>',
        ].join('');
    }

    function _advisorCard(obs) {
        // Display-only — operators act by locking a variable and letting the optimizer converge (AC-8).
        var variable = obs.variable || '';
        var current = obs.current_value;
        var suggested = obs.suggested;
        var rationale = obs.rationale || '';
        var confidence = obs.confidence || '';
        var oos = obs.oos_status || obs.oos || '';
        var gates = obs.gate_results || obs.gates || [];

        var oosPass = typeof oos === 'string' && oos.toLowerCase().indexOf('pass') !== -1;
        var oosColor = oosPass ? 'var(--studio-pos)' : 'var(--studio-neg)';

        var confColor = { high: 'var(--studio-pos)', medium: 'var(--studio-warn)', low: 'var(--studio-ink-faint)' }[confidence] || 'var(--studio-ink-dim)';

        var gateBadges = Array.isArray(gates) ? gates.map(function (g) {
            return _badge(g.pass ? 'pass' : 'fail', _esc(g.label) + ' ' + (g.pass ? '✓' : '✕'));
        }).join('') : '';

        var currentToSuggested = '';
        if (current !== undefined && current !== null && suggested !== undefined && suggested !== null) {
            currentToSuggested = [
                '<span style="font-variant-numeric:tabular-nums;color:var(--studio-ink-dim);">',
                Number(current).toFixed(2),
                '</span>',
                '<span style="color:' + confColor + ';margin:0 6px;font-weight:700;">→</span>',
                '<strong style="color:var(--studio-ink);">' + Number(suggested).toFixed(2) + '</strong>',
            ].join('');
        } else if (suggested !== undefined && suggested !== null) {
            currentToSuggested = '<strong style="color:var(--studio-ink);">→ ' + Number(suggested).toFixed(2) + '</strong>';
        }

        return [
            '<div style="background:var(--studio-bg);border:1px solid var(--studio-rule);',
            '  border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px;">',
            '  <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">',
            '    <span style="font-size:13px;font-weight:700;color:var(--studio-ink);font-variant-numeric:tabular-nums;">',
            _esc(variable),
            '    </span>',
            '    <div style="display:flex;gap:5px;flex-wrap:wrap;">' + gateBadges + '</div>',
            '  </div>',
            confidence
                ? '<div style="display:flex;align-items:center;gap:12px;">' +
                  '<span style="font-size:9px;font-weight:700;color:var(--studio-ink-dim);letter-spacing:0.1em;text-transform:uppercase;">' +
                  _esc(confidence) + ' confidence</span>' +
                  (currentToSuggested ? '<span style="width:1px;height:12px;background:var(--studio-rule);"></span>' +
                  '<span style="font-size:13px;font-variant-numeric:tabular-nums;">' + currentToSuggested + '</span>' : '') +
                  '</div>'
                : (currentToSuggested ? '<div style="font-size:13px;font-variant-numeric:tabular-nums;">' + currentToSuggested + '</div>' : ''),
            rationale
                ? '<div style="font-size:12px;color:var(--studio-ink-dim);line-height:1.5;">' + _esc(rationale) + '</div>'
                : '',
            oos
                ? '<div style="display:flex;align-items:center;gap:7px;font-size:11px;color:' + oosColor + ';font-weight:600;">' +
                  '<span style="width:6px;height:6px;border-radius:6px;background:' + oosColor + ';"></span>' +
                  _esc(oos) + '</div>'
                : '',
            '</div>',
        ].join('');
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
