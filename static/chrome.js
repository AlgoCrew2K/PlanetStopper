'use strict';

/** CSRF token fetched from GET /api/csrf-token on page load (AC-1). */
var _chromeCsrfToken = null;
document.addEventListener('DOMContentLoaded', function () {
  fetch('/api/csrf-token')
    .then(function (r) { return r.json(); })
    .then(function (b) { _chromeCsrfToken = b.csrf_token || null; })
    .catch(function () { /* csrf token unavailable — POSTs will 403 */ });
});

// ── Force-run button (FP-T3-02) ────────────────────────────────────────────
function forceRun(e) {
  var btn = document.querySelector('[data-testid="force-run-btn"]');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.textContent = 'Running…';
  fetch('/api/trigger', { method: 'POST', headers: { 'X-CSRF-Token': _chromeCsrfToken || '' } })
    .then(function (r) {
      btn.textContent = r.ok ? 'Triggered!' : 'Error';
      setTimeout(function () {
        btn.textContent = 'Force run now';
        btn.disabled = false;
      }, 2000);
      if (typeof window.loadState === 'function') window.loadState();
    })
    .catch(function () {
      btn.textContent = 'Error';
      setTimeout(function () {
        btn.textContent = 'Force run now';
        btn.disabled = false;
      }, 2000);
    });
}
document.addEventListener('DOMContentLoaded', function () {
  var forceRunBtn = document.querySelector('[data-testid="force-run-btn"]');
  if (forceRunBtn) forceRunBtn.addEventListener('click', forceRun);
});

// ── Workspace switcher (B-08) ─────────────────────────────────────────────
// Track active account UUID. Exposed on window so index.js loadState() can
// read window.activeAccount when building the /api/state URL.
window.activeAccount = null;

function switchAccount(uuid) {
  window.activeAccount = uuid || null;

  // Update the button label to reflect the selected account.
  var label = document.getElementById('ws-label');
  var uuidEl = document.getElementById('ws-uuid');
  if (label && uuidEl) {
    var dropdown = document.getElementById('ws-dropdown');
    var items = dropdown ? dropdown.querySelectorAll('[data-uuid]') : [];
    items.forEach(function (el) {
      if (el.dataset.uuid === uuid) {
        label.textContent = el.dataset.label || uuid;
        uuidEl.textContent = uuid ? uuid.slice(0, 8) : '';
      }
    });
  }

  closeAccountDropdown();
  if (typeof window.loadState === 'function') window.loadState();
}
window.switchAccount = switchAccount;

function closeAccountDropdown() {
  var dd = document.getElementById('ws-dropdown');
  if (dd) dd.setAttribute('hidden', '');
}

function toggleAccountDropdown(e) {
  e.stopPropagation();
  var dd = document.getElementById('ws-dropdown');
  if (!dd) return;

  if (!dd.hasAttribute('hidden')) {
    closeAccountDropdown();
    return;
  }

  // Populate dropdown from /api/accounts on first open (or repopulate each time).
  fetch('/api/accounts')
    .then(function (r) { return r.json(); })
    .then(function (data) {
      dd.innerHTML = '';
      var accounts = (data && data.accounts) || [];
      if (accounts.length === 0) {
        dd.innerHTML = '<div style="padding:10px 14px;font-size:12px;color:var(--studio-ink-dim);">No accounts configured</div>';
      } else {
        accounts.forEach(function (acc) {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.dataset.uuid = acc.uuid;
          btn.dataset.label = acc.label;
          btn.style.cssText = [
            'display:block', 'width:100%', 'text-align:left',
            'padding:9px 14px', 'border:none', 'border-bottom:1px solid var(--studio-rule)',
            'background:transparent', 'cursor:pointer',
            'font-family:var(--studio-sans,Manrope,sans-serif)',
            'font-size:12px', 'color:var(--studio-ink)'
          ].join(';');
          btn.innerHTML =
            '<span style="font-weight:600;">' + escHtml(acc.label) + '</span>' +
            '<span style="display:block;font-size:10px;color:var(--studio-ink-dim);margin-top:1px;">' +
            escHtml(acc.uuid.slice(0, 8)) + '</span>';
          btn.addEventListener('click', function () { switchAccount(acc.uuid); });
          btn.addEventListener('mouseover', function () {
            this.style.background = 'var(--studio-chip-bg)';
          });
          btn.addEventListener('mouseout', function () {
            this.style.background = 'transparent';
          });
          dd.appendChild(btn);
        });
        // Remove bottom border on last item.
        var last = dd.querySelector('button:last-child');
        if (last) last.style.borderBottom = 'none';
      }
      dd.removeAttribute('hidden');
    })
    .catch(function () {
      dd.innerHTML = '<div style="padding:10px 14px;font-size:12px;color:var(--studio-neg);">Failed to load accounts</div>';
      dd.removeAttribute('hidden');
    });
}
window.toggleAccountDropdown = toggleAccountDropdown;

// Close dropdown when clicking outside.
document.addEventListener('click', function (e) {
  var wrap = document.getElementById('ws-wrap');
  if (wrap && !wrap.contains(e.target)) {
    closeAccountDropdown();
  }
});

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Emergency Liquidate modal ─────────────────────────────────────────────
function closePanicModal() {
  var modal = document.getElementById('panic-modal');
  if (modal) modal.setAttribute('hidden', '');
}
window.closePanicModal = closePanicModal;

function openPanicModal() {
  var modal = document.getElementById('panic-modal');
  if (!modal) return;
  modal.removeAttribute('hidden');
  var phrase = document.getElementById('panic-phrase');
  if (phrase) phrase.value = '';
  var btn = document.getElementById('panic-confirm-btn');
  if (btn) btn.disabled = true;

  // Populate account select from /api/accounts (same source as workspace switcher).
  var sel = document.getElementById('panic-account');
  if (sel) {
    fetch('/api/accounts')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var accounts = (data && data.accounts) || [];
        sel.innerHTML = accounts.length
          ? accounts.map(function (a) {
              return '<option value="' + escHtml(a.uuid) + '">' +
                escHtml(a.label) + ' (' + escHtml(a.uuid.slice(0, 8)) + '…)</option>';
            }).join('')
          : '<option value="">No accounts configured</option>';
      })
      .catch(function () {
        sel.innerHTML = '<option value="">Failed to load accounts</option>';
      });
  }
}
window.openPanicModal = openPanicModal;

function submitPanicLiquidation() {
  var accountEl = document.getElementById('panic-account');
  var phraseEl = document.getElementById('panic-phrase');
  if (!accountEl || !phraseEl) return;
  var accountId = accountEl.value;
  var phrase = phraseEl.value;
  if (phrase !== 'LIQUIDATE') return;
  fetch('/api/sell_account', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _chromeCsrfToken || '' },
    body: JSON.stringify({ account_id: accountId, confirm_account_id: accountId, confirm_phrase: phrase }),
  }).then(function (r) { return r.json(); }).then(function (d) {
    closePanicModal();
    alert(d.message || (d.executed ? 'Liquidation initiated.' : 'Dry-run: no action taken.'));
  });
}
window.submitPanicLiquidation = submitPanicLiquidation;

// Escape key closes the panic modal.
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') closePanicModal();
});

// ── Engine status: Live / Stale / Closed ─────────────────────────────────
// Single three-state indicator — no per-second counters. State changes only
// on the poll cycle so there are no numbers that can look frozen.
function updateEngineStatus(data) {
  var dot   = document.getElementById('engine-status-dot');
  var label = document.getElementById('engine-status-label');
  var wrap  = document.getElementById('engine-status');
  if (!label) return;

  var meta   = (data && data.meta) || {};
  var isOpen = (data && data.market_state === 'open') || !!(meta.system_online);

  if (!isOpen) {
    if (dot)  dot.style.background = 'var(--studio-ink-dim)';
    label.style.color = 'var(--studio-ink-dim)';
    label.textContent = 'Closed';
    if (wrap) wrap.title = '';
    return;
  }

  var cycleAt = (data && data.last_successful_cycle_at) || (meta && meta.last_cycle_at);
  var stale = cycleAt ? (Date.now() - new Date(cycleAt).getTime() > 2 * 60 * 1000) : false;

  if (stale) {
    if (dot)  dot.style.background = 'var(--studio-warn)';
    label.style.color = 'var(--studio-warn)';
    label.textContent = 'Stale';
    if (wrap) wrap.title = 'Positions may not be actively guarded.';
  } else {
    if (dot)  dot.style.background = 'var(--studio-pos)';
    label.style.color = 'var(--studio-pos)';
    label.textContent = 'Live';
    if (wrap) wrap.title = '';
  }
}
window.updateEngineStatus = updateEngineStatus;
// Alias so existing index.js callers of updateChromeTicker keep working.
function updateChromeTicker(data) {
  updateEngineStatus(data);
  resyncNextCountdown(data);
}
window.updateChromeTicker = updateChromeTicker;

// ── NEXT: MM:SS countdown ─────────────────────────────────────────────────
// Ticks down every second; resynced from data.next_run_seconds (top-level
// integer) on each /api/state poll. Falls back to parsing meta.next_run
// "MM:SS" string. Hidden when market is closed.
// NOTE: next_run_seconds can legitimately be 0 — use typeof checks, not
// truthiness, so 0 is not mistaken for "no data".
var _nextCountdownSecs = null;

function _fmtCountdown(secs) {
  if (secs == null || secs < 0) secs = 0;
  var m = Math.floor(secs / 60);
  var s = secs % 60;
  return m + ':' + (s < 10 ? '0' : '') + s;
}

function resyncNextCountdown(data) {
  var el = document.getElementById('next-run-countdown');
  if (!el) return;
  var meta   = (data && data.meta) || {};
  var isOpen = (data && data.market_state === 'open') || !!(meta.system_online);
  if (!isOpen) {
    el.style.display = 'none';
    _nextCountdownSecs = null;
    return;
  }
  el.style.display = '';
  // top-level data.next_run_seconds is the authoritative integer field.
  // Use typeof check so 0 (valid) is not treated as absent.
  if (data && typeof data.next_run_seconds === 'number') {
    _nextCountdownSecs = data.next_run_seconds;
  } else if (meta.next_run && /^\d{1,2}:\d{2}$/.test(meta.next_run)) {
    var parts = meta.next_run.split(':');
    _nextCountdownSecs = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
  }
  if (_nextCountdownSecs != null) {
    el.textContent = 'NEXT: ' + _fmtCountdown(_nextCountdownSecs);
  }
}

function _tickNextCountdown() {
  var el = document.getElementById('next-run-countdown');
  if (!el || el.style.display === 'none' || _nextCountdownSecs == null) return;
  _nextCountdownSecs = Math.max(0, _nextCountdownSecs - 1);
  el.textContent = 'NEXT: ' + _fmtCountdown(_nextCountdownSecs);
}
setInterval(_tickNextCountdown, 1000);

// ── ET clock (FP-T3-06) ───────────────────────────────────────────────────
function updateClock() {
  var el = document.querySelector('[data-testid="et-clock"]');
  if (!el) return;
  try {
    el.textContent = new Date().toLocaleTimeString('en-US', {
      timeZone: 'America/New_York',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch (_) { /* browser lacks timeZone support */ }
}
updateClock();
setInterval(updateClock, 1000);
