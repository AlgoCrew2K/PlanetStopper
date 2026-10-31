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

// ── Engine status: one precedence, one writer ─────────────────────────────
// renderEngineLabel() is the only function that writes #engine-status-label,
// #engine-status-dot, the #engine-status title and the countdown's visibility. It
// reads module state only, so every caller (a refresh outcome, the one-second tick)
// gets the same answer. The order, highest first; the first rank that holds is shown:
//   Connection Lost  the newest refresh ended in a handled failure
//   Not updating     no data has arrived for more than NOT_UPDATING_AFTER_MS
//   Degraded         the last good body lists degraded reasons
//   Closed           the market is closed
//   Rebalance pause  the engine's scheduled pause before the close
//   Opening          the engine's scheduled session start
//   Stale            the last cycle is older than STALE_AFTER_MS
//   Live             otherwise
// Every age is measured on performance.now() from the page's own receipt times, or on
// the server's clock (the response's Date header) for the cycle's age; the browser's
// wall clock never decides a label, because it can be minutes off.
var NOT_UPDATING_AFTER_MS = 90000;
var STALE_AFTER_MS = 2 * 60 * 1000;
var MS_PER_MINUTE = 60000;
var NEXT_RUN_PERIOD_S = 60;
var SECONDS_PER_MINUTE = 60;
var DEGRADED_REASON_TEXT = {
  scheduler_stalled: 'Scheduler stalled: no engine spawn for 2 minutes or more.',
  engine_overrun: 'An engine run has lasted over 3 minutes; minutes are being skipped.',
  engine_failing: 'The last 5 engine runs failed.'
};
var DEGRADED_UNKNOWN_TEXT = 'The daemon reports a degraded state.';
var CONNECTION_LOST_TITLE = 'The last refresh failed. The values shown are from the last data received.';
var BLACKOUT_TITLE = 'Scheduled pause before the close; stops resume at the close.';
var OPENING_TITLE = 'Scheduled session start; the first cycle is running.';
var STALE_TITLE = 'Positions may not be actively guarded.';

var _engineFailed = false;          // the newest refresh ended in a handled failure
var _engineLastReceivedMono = null; // performance.now() when the last good body arrived
var _engineBody = null;             // what the last good body said about the engine
var _cycleAgeAtReceiptMs = null;    // the last cycle's age on the server's clock at receipt
var _cycleAgeAnchorMono = null;     // performance.now() at that receipt
var _countdownAnchor = null;        // { secs, atMono }: next_run_seconds and when it was true

/** The receipt record of one response (suite contract RC-3), one plain object:
 *  sentAtMono is performance.now() read just before the request was sent,
 *  receivedAtMono is read when the response headers arrive, and serverNowMs is the
 *  server's clock from the HTTP Date header (null when absent or unparseable). */
function receiptTiming(sentAtMono, response) {
  var serverNowMs = null;
  try {
    var header = response && response.headers && response.headers.get && response.headers.get('Date');
    var parsed = header ? Date.parse(header) : NaN;
    if (isFinite(parsed)) serverNowMs = parsed;
  } catch (e) { serverNowMs = null; }
  return { sentAtMono: sentAtMono, receivedAtMono: performance.now(), serverNowMs: serverNowMs };
}
window.receiptTiming = receiptTiming;

/** Whole minutes without data once the page has gone NOT_UPDATING_AFTER_MS with none, else null. */
function notUpdatedForMinutes() {
  if (_engineLastReceivedMono === null) return null;
  var silentMs = performance.now() - _engineLastReceivedMono;
  return silentMs > NOT_UPDATING_AFTER_MS ? Math.floor(silentMs / MS_PER_MINUTE) : null;
}
window.notUpdatedForMinutes = notUpdatedForMinutes;

function _cycleIsStale() {
  if (_cycleAgeAtReceiptMs === null || _cycleAgeAnchorMono === null) return false;
  return _cycleAgeAtReceiptMs + (performance.now() - _cycleAgeAnchorMono) > STALE_AFTER_MS;
}

function _degradedTitle(reasons) {
  return reasons.map(function (reason) {
    return Object.prototype.hasOwnProperty.call(DEGRADED_REASON_TEXT, reason)
      ? DEGRADED_REASON_TEXT[reason] : DEGRADED_UNKNOWN_TEXT;
  }).join(' ');
}

// The label, colour token, tooltip and countdown visibility of the first rank that holds,
// or null while the page knows nothing yet (the server render stands then).
function _engineLabelState() {
  if (_engineFailed) {
    return { text: 'Connection Lost', token: 'neg', title: CONNECTION_LOST_TITLE, countdown: false };
  }
  var minutes = notUpdatedForMinutes();
  if (minutes !== null) {
    return { text: 'Not updating', token: 'warn', title: 'No data received for ' + minutes + ' min.', countdown: true };
  }
  var body = _engineBody;
  if (body === null) return null;
  if (body.degradedReasons.length) {
    return { text: 'Degraded', token: 'neg', title: _degradedTitle(body.degradedReasons), countdown: true };
  }
  if (!body.isOpen) {
    return { text: 'Closed', token: 'ink-dim', title: '', countdown: false };
  }
  if (body.phase === 'blackout') {
    return { text: 'Rebalance pause', token: 'ink-dim', title: BLACKOUT_TITLE, countdown: true };
  }
  if (body.phase === 'opening') {
    return { text: 'Opening', token: 'ink-dim', title: OPENING_TITLE, countdown: true };
  }
  if (_cycleIsStale()) {
    return { text: 'Stale', token: 'warn', title: STALE_TITLE, countdown: true };
  }
  return { text: 'Live', token: 'pos', title: '', countdown: true };
}

function renderEngineLabel() {
  var label = document.getElementById('engine-status-label');
  if (!label) return;
  var state = _engineLabelState();
  if (state === null) return;
  var dot = document.getElementById('engine-status-dot');
  var wrap = document.getElementById('engine-status');
  var countdown = document.getElementById('next-run-countdown');
  var colour = 'var(--studio-' + state.token + ')';
  if (dot) dot.style.background = colour;
  label.style.color = colour;
  label.textContent = state.text;
  if (wrap) wrap.title = state.title;
  if (countdown) countdown.style.display = state.countdown ? '' : 'none';
}
window.renderEngineLabel = renderEngineLabel;

/** The newest refresh ended in a handled failure. */
function noteRefreshFailed() {
  _engineFailed = true;
  renderEngineLabel();
}
window.noteRefreshFailed = noteRefreshFailed;

/** A good body arrived: end the failed state and restart the silence clock. */
function noteDataReceived(timing) {
  _engineFailed = false;
  if (timing && typeof timing.receivedAtMono === 'number') _engineLastReceivedMono = timing.receivedAtMono;
  renderEngineLabel();
}
window.noteDataReceived = noteDataReceived;

/** Stores what a good body says about the engine, re-anchors the countdown and redraws the label.
 *  A timing with no server time keeps the previous Stale judgement: the browser's clock
 *  must never decide it. */
function updateChromeTicker(data, timing) {
  var meta = (data && data.meta) || {};
  var cycleAt = (data && data.last_successful_cycle_at) || (meta && meta.last_cycle_at);
  var reasons = data && Array.isArray(data.degraded_reasons) ? data.degraded_reasons : [];
  _engineBody = {
    isOpen: (data && data.market_state === 'open') || !!meta.system_online,
    phase: data && data.engine_phase,
    degradedReasons: reasons.slice()
  };
  if (timing && typeof timing.serverNowMs === 'number') {
    var cycleMs = cycleAt ? Date.parse(cycleAt) : NaN;
    _cycleAgeAtReceiptMs = isFinite(cycleMs) ? timing.serverNowMs - cycleMs : null;
    _cycleAgeAnchorMono = typeof timing.receivedAtMono === 'number' ? timing.receivedAtMono : performance.now();
  }
  resyncNextCountdown(data, timing);
  renderEngineLabel();
}
window.updateChromeTicker = updateChromeTicker;

// Kept as the entry point existing callers use: a body with no receipt record.
function updateEngineStatus(data) {
  updateChromeTicker(data, null);
}
window.updateEngineStatus = updateEngineStatus;

// ── NEXT MM:SS countdown ──────────────────────────────────────────────────
// Anchored at the moment the request was SENT with the seconds the server reported
// (taken before its per-symphony work), and counted down on performance.now(), so a
// slow response does not shift it. It wraps at the minute instead of stopping at zero.
// Same text form as the server render (templates/_chrome.html). Its visibility is
// written by renderEngineLabel. next_run_seconds can legitimately be 0: use typeof
// checks, not truthiness.
function _pad2(n) { return (n < 10 ? '0' : '') + n; }

function renderCountdown() {
  var el = document.getElementById('next-run-countdown');
  if (!el || _countdownAnchor === null) return;
  var remaining = _countdownAnchor.secs - (performance.now() - _countdownAnchor.atMono) / 1000;
  var secs = ((Math.floor(remaining) % NEXT_RUN_PERIOD_S) + NEXT_RUN_PERIOD_S) % NEXT_RUN_PERIOD_S;
  el.textContent = 'NEXT ' + _pad2(Math.floor(secs / SECONDS_PER_MINUTE)) + ':' + _pad2(secs % SECONDS_PER_MINUTE);
}

function resyncNextCountdown(data, timing) {
  var meta = (data && data.meta) || {};
  var secs = null;
  if (data && typeof data.next_run_seconds === 'number') {
    secs = data.next_run_seconds;
  } else if (meta.next_run && /^\d{1,2}:\d{2}$/.test(meta.next_run)) {
    var parts = meta.next_run.split(':');
    secs = parseInt(parts[0], 10) * SECONDS_PER_MINUTE + parseInt(parts[1], 10);
  }
  if (secs === null) return;
  var sentAt = timing && typeof timing.sentAtMono === 'number' ? timing.sentAtMono : performance.now();
  _countdownAnchor = { secs: secs, atMono: sentAt };
  renderCountdown();
}

// One tick a second: ages the label (Not updating, Stale) and the countdown. It sends no request.
setInterval(function () {
  renderEngineLabel();
  renderCountdown();
}, 1000);

// ── Candidate alert: header badge for new weekly-suggestion survivors ─────
// (feature-plans/candidate-alert.md AC-1..AC-6). chrome.js — not index.js —
// because chrome.js is the only JS asset shared by all 4 screens (loaded via
// _chrome.html's <script src="/static/chrome.js"> on every page).
// 30s poll — matches index.js's POLL_INTERVAL_MS, well above the 15s floor.
var CANDIDATE_ALERT_POLL_INTERVAL_MS = 30000;

function fetchCandidateAlert() {
  fetch('/api/candidate-alert')
    .then(function (response) {
      if (!response.ok) return null;
      return response.json();
    })
    .then(function (data) {
      var badge = document.getElementById('candidate-alert-badge');
      var wrap = document.getElementById('candidate-alert-indicator');
      if (!data) return;

      var count = data.new_valid_count || 0;
      if (badge) {
        if (count > 0) {
          badge.textContent = String(count);
          badge.style.display = 'inline-block';
        } else {
          badge.textContent = '';
          badge.style.display = 'none';
        }
      }

      if (wrap) {
        var lastRun = data.last_run;
        wrap.title = lastRun
          ? 'Weekly run ' + (lastRun.ran_at || '') + ': ' + lastRun.evaluated +
            ' evaluated, ' + lastRun.survivors + ' passed the gate'
          : 'No weekly run yet';
      }
    })
    .catch(function () { /* silent — badge stays at its last-known state (AC-6) */ });
}
window.fetchCandidateAlert = fetchCandidateAlert;

// Fires on indicator click, alongside the native <a href> navigation to
// /ai-advisor — keepalive lets the request complete even though the browser
// is about to navigate away. Best-effort: a failure here must never block
// or interfere with the navigation itself (AC-4 works without JS at all).
function markCandidateAlertViewed() {
  fetch('/api/candidate-alert/mark-viewed', {
    method: 'POST',
    headers: { 'X-CSRF-Token': _chromeCsrfToken || '' },
    keepalive: true,
  }).catch(function () { /* best-effort — navigation proceeds regardless */ });
}
window.markCandidateAlertViewed = markCandidateAlertViewed;

document.addEventListener('DOMContentLoaded', function () {
  fetchCandidateAlert();
  setInterval(fetchCandidateAlert, CANDIDATE_ALERT_POLL_INTERVAL_MS);
  var indicator = document.getElementById('candidate-alert-indicator');
  if (indicator) indicator.addEventListener('click', markCandidateAlertViewed);
});

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
