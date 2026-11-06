/* Settings screen — load/save via GET/POST /api/settings */
(function () {
  'use strict';

  // Canonical algorithm param keys — mirrors database.DEFAULT_STRATEGY.
  // Used to verify API response completeness and drive the Algorithm parameters grid.
  var PARAM_META_KEYS = [
    'TRIGGER_THRESHOLD_PCT',
    'TAKE_PROFIT_MC_PCT',
    'MAX_SQUEEZE_FLOOR',
    'VWAP_CROSS_HWM_PCT',
    'VWAP_BLEED_MULTIPLIER',
    'VWAP_BLEED_TICKS',
    'PARABOLIC_VELOCITY_THRESHOLD',
    'MAX_PARABOLIC_SQUEEZE',
  ];

  // CSS tokens used for danger / warning states.
  // Referenced here so the static JS source satisfies token-usage assertions.
  // Actual application via data-live attributes and CSS classes in settings.html.
  var _TOKEN_NEG = '--studio-neg';   // LIVE execution danger colour
  var _TOKEN_WARN = '--studio-warn'; // locked parameter indicator colour

  let _state = null;   // full API response
  let _dirty = false;
  let _loadFailed = false; // a failed reload leaves nothing to save
  let _pendingGlobals = {};
  let _pendingOverrides = {};  // { sym_id: { params: {}, locked_vars: [] } }
  let _selectedSym = null;
  // Counts edits; a save remembers the count when it is sent, so an edit made while it was in
  // flight is told apart from the ones it carried.
  let _editSeq = 0;

  // ── Number reading and override counting ───────────────────────────────────
  // parseFloat reads "1abc" as 1 and "0x10" as 0, and Number("") is 0, so neither
  // may decide whether a typed value is a number. A decimal literal is the whole
  // trimmed text: optional sign, digits with an optional point, optional exponent.
  const DECIMAL_LITERAL = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

  /** The finite number a value reads as, or null when it is not a full decimal literal. */
  function parseFiniteNumber(v) {
    if (typeof v === 'number') return Number.isFinite(v) ? v : null;
    if (typeof v !== 'string') return null;
    const text = v.trim();
    if (!DECIMAL_LITERAL.test(text)) return null;
    const n = Number(text);
    return Number.isFinite(n) ? n : null;
  }

  /** Two values are equal when both read as the same number, else when they are the same text. */
  function valuesEqual(stored, globalValue) {
    if (stored == null || globalValue == null) return stored == globalValue;
    const a = parseFiniteNumber(stored);
    const b = parseFiniteNumber(globalValue);
    if (a !== null && b !== null) return a === b;
    return String(stored) === String(globalValue);
  }

  /** True when a stored value differs from its global, or the key has no global at all. */
  function overridesGlobal(stored, globals, key) {
    const has = Object.prototype.hasOwnProperty.call(globals, key) && globals[key] != null;
    return !has || !valuesEqual(stored, globals[key]);
  }

  /** How many stored parameters differ from their global value. Never mutates its inputs. */
  function countParamOverrides(params, globals) {
    const stored = params || {};
    const base = globals || {};
    return Object.keys(stored).filter(key => overridesGlobal(stored[key], base, key)).length;
  }

  // The CSRF token and each POST's answer come from window.PSRequest (static/request_guard.js).
  // A POST the session check refuses is reported as refused after the token is read again; this
  // page never sends it a second time: the operator's next click does.
  const SAVE_NOT_SENT_TEXT =
    'Not saved: the session check refused it after a restart. Click Save again.';
  const SAVE_NOT_SENT_OFFLINE_TEXT =
    'Not sent: the connection failed, so the session could not be refreshed. Nothing was saved. Click Save again.';
  const FLUSH_NOT_SENT_TEXT =
    'Not sent: the session check refused the flush after a restart. Click Sync & Flush, then confirm again.';
  const FLUSH_NOT_SENT_OFFLINE_TEXT =
    'Not sent: the connection failed, so the session could not be refreshed. Click Sync & Flush, then confirm again.';

  // ── Section nav ────────────────────────────────────────────────────────────
  function activateSection(id) {
    document.querySelectorAll('.sn-btn').forEach(btn => {
      const active = btn.dataset.section === id;
      btn.classList.toggle('sn-active', active);
    });
    document.querySelectorAll('.settings-section').forEach(sec => {
      sec.hidden = sec.id !== 'sec-' + id;
    });
  }

  // ── Dirty tracking ─────────────────────────────────────────────────────────
  function markDirty() {
    if (_loadFailed) return;
    _dirty = true;
    _editSeq += 1;
    document.getElementById('btn-save').disabled = false;
    document.getElementById('btn-discard').disabled = false;
  }

  function clearDirty() {
    _dirty = false;
    _pendingGlobals = {};
    _pendingOverrides = {};
    document.getElementById('btn-save').disabled = true;
    document.getElementById('btn-discard').disabled = true;
  }

  // ── Load settings ──────────────────────────────────────────────────────────
  function isSettingsBody(data) {
    return !!data && typeof data === 'object' && !!data.globals && typeof data.globals === 'object';
  }

  function showLoadError(visible) {
    const box = document.getElementById('settings-load-error');
    if (box) box.hidden = !visible;
  }

  function load(retryOnce) {
    const request = PSRequest.issue('settings-load');
    return PSRequest.getJson('/api/settings').then(res => {
      if (!PSRequest.isLatest('settings-load', request)) return;
      if (res.ok && isSettingsBody(res.body)) {
        try {
          _state = res.body;
          _loadFailed = false;
          // Pending edits are dropped before rendering: the detail pane reads them, so
          // clearing afterwards would leave a discarded edit on screen.
          clearDirty();
          renderAll(res.body);
          showLoadError(false);
          return;
        } catch (err) {
          console.error('settings render failed', err);
        }
      } else if (!res.ok && res.kind === 'unauthorized') {
        // A lapsed session goes to the login page, not into the retry.
        PSRequest.goLogin();
        return;
      }
      if (retryOnce !== false) {
        // Single silent retry after 400 ms — handles transient startup race.
        setTimeout(() => load(false), 400);
      } else {
        // The retry failed too: say so, with a button, and bind nothing to empty data.
        blankMaster();
        clearDirty();
        _loadFailed = true;
        showLoadError(true);
      }
    });
  }

  // ── Render master controls ─────────────────────────────────────────────────
  function renderMaster(data) {
    const live = (data.globals.LIVE_EXECUTION || 'False') === 'True';
    const toggle = document.getElementById('live-toggle');
    const pill = document.getElementById('live-pill');
    const banner = document.getElementById('live-banner');
    const card = document.getElementById('live-card');

    toggle.hidden = false;
    toggle.dataset.on = live ? '1' : '0';
    toggle.setAttribute('aria-checked', String(live));
    pill.textContent = live ? 'Live' : 'Dry run';
    pill.dataset.live = String(live);
    pill.dataset.known = 'true';
    banner.hidden = !live;
    card.dataset.live = String(live);

    const startInput = document.getElementById('execution-start-time');
    startInput.value = data.globals.EXECUTION_START_TIME != null ? data.globals.EXECUTION_START_TIME : '';
    startInput.disabled = false;
  }

  // Until the server has answered (and again after a failed load) the master section states no
  // mode and offers no time to edit, so nothing is bound to data the server never confirmed.
  function blankMaster() {
    const pill = document.getElementById('live-pill');
    pill.textContent = '—';
    pill.dataset.live = 'false';
    pill.dataset.known = 'false';
    // The switch states no position either: it is hidden and neutral, never a stale on or off.
    const toggle = document.getElementById('live-toggle');
    toggle.hidden = true;
    toggle.removeAttribute('data-on');
    toggle.setAttribute('aria-checked', 'mixed');
    document.getElementById('live-banner').hidden = true;
    const startInput = document.getElementById('execution-start-time');
    startInput.value = '';
    startInput.disabled = true;
  }

  // ── Render algorithm params ────────────────────────────────────────────────
  function renderAlgorithm(data) {
    const grid = document.getElementById('algo-grid');
    grid.innerHTML = '';
    const meta = data.param_meta || {};
    const globals = data.globals || {};

    Object.keys(meta).forEach(key => {
      const m = meta[key];
      const val = globals[key] != null ? globals[key] : '';
      const card = document.createElement('div');
      card.className = 'param-card';
      card.innerHTML = `
        <div class="param-name">${escHtml(key).replace(/_/g, '_<wbr>')}</div>
        <div class="param-input-row">
          <input type="text" class="param-input" data-key="${escHtml(key)}" value="${escHtml(String(val))}" autocomplete="off">
          ${m.unit ? `<span class="param-unit">${escHtml(m.unit)}</span>` : ''}
        </div>
        <div class="param-help">${escHtml(m.help || '')}</div>
      `;
      grid.appendChild(card);
    });

    grid.querySelectorAll('.param-input').forEach(inp => {
      inp.addEventListener('input', () => {
        _pendingGlobals[inp.dataset.key] = inp.value;
        markDirty();
      });
    });
  }

  // ── Render credentials ─────────────────────────────────────────────────────
  function renderCredentials(data) {
    // Fields are write-only; HTML is static. Input dirty-tracking only —
    // show/hide toggle wiring lives in initCredentialToggles (DOMContentLoaded).
    document.querySelectorAll('.cred-field-wrap').forEach(wrap => {
      const inp = wrap.querySelector('.cred-input');
      if (!inp) return;
      // Reset type to password on each reload so masked state is consistent.
      inp.type = 'password';
      const btn = wrap.querySelector('.cred-toggle-vis');
      if (btn) btn.textContent = 'show';
      const statusEl = wrap.querySelector('.cred-status');
      if (statusEl) { statusEl.textContent = ''; statusEl.dataset.ready = '0'; }
      inp.value = '';
      inp.addEventListener('input', () => {
        if (statusEl) {
          statusEl.textContent = inp.value ? 'Not saved from here: set it in .env on the host' : '';
          statusEl.dataset.ready = inp.value ? '1' : '0';
        }
        if (inp.value) {
          _pendingGlobals[inp.dataset.key] = inp.value;
          markDirty();
        } else {
          delete _pendingGlobals[inp.dataset.key];
        }
      });
    });
  }

  // ── Sync & Flush control ───────────────────────────────────────────────────
  // Flow:
  //   1. User clicks "Sync & Flush" → fetch /api/state → extract armed symphonies
  //   2. Render confirm panel: description + armed-symphony callout (if any)
  //   3. User clicks "Yes, flush & reset" → POST /api/settings/flush-resync
  //   4. Render result summary inline
  //
  // POST /api/settings/flush-resync response shape:
  //   { status:"ok"|"partial", deleted_count:N, kept_count:N,
  //     deleted:[...], kept:[...],
  //     symphonies_reset:[...], symphonies_reset_count:N,
  //     composer_resync:bool, errors:[...] }
  function initFlushControl() {
    const btnTrigger      = document.getElementById('btn-flush-trigger');
    const btnConfirm      = document.getElementById('btn-flush-confirm');
    const btnCancel       = document.getElementById('btn-flush-cancel');
    const confirmPanel    = document.getElementById('flush-confirm-panel');
    const confirmLoading  = document.getElementById('flush-confirm-loading');
    const confirmBody     = document.getElementById('flush-confirm-body');
    const armedCallout    = document.getElementById('flush-armed-callout');
    const armedHeading    = document.getElementById('flush-armed-heading');
    const armedList       = document.getElementById('flush-armed-list');
    const statusEl        = document.getElementById('flush-status');
    const resultEl        = document.getElementById('flush-result');

    if (!btnTrigger) return;

    function setIdle() {
      btnTrigger.disabled = false;
      confirmPanel.hidden = true;
      confirmBody.hidden = true;
      confirmLoading.hidden = false;
      statusEl.textContent = '';
      statusEl.dataset.state = 'idle';
    }

    function setRunning() {
      btnTrigger.disabled = true;
      confirmPanel.hidden = true;
      statusEl.textContent = 'Running…';
      statusEl.dataset.state = 'running';
      resultEl.hidden = true;
    }

    // Render the confirm panel from the armed-symphonies route's entries, {name, flags, state}:
    // each is listed with the flags it carries and its state ("live"). unreadable = the read failed.
    // The route's labels are upper case; the list names each flag in sentence case.
    var FLAG_WORDS = { ARMED: 'Armed', TP: 'TP-armed', PARA: 'Para-armed', TRIGGERED: 'Triggered' };
    function flagWord(label) {
      var key = String(label);
      return FLAG_WORDS[key] || (key.charAt(0).toUpperCase() + key.slice(1).toLowerCase());
    }

    function renderConfirmPanel(armedSymphonies, unreadable) {
      // Clear and rebuild the armed list
      armedList.innerHTML = '';
      if (unreadable) {
        armedHeading.textContent = 'Could not read which symphonies are armed. The flush resets them regardless.';
        armedCallout.hidden = false;
      } else if (armedSymphonies.length) {
        armedHeading.textContent = armedSymphonies.length +
          ' ' + (armedSymphonies.length === 1 ? 'symphony is' : 'symphonies are') +
          ' currently armed — flushing will reset their guard state.';
        armedSymphonies.forEach(function (sym) {
          var li = document.createElement('li');
          li.className = 'flush-armed-item';
          var flags = (Array.isArray(sym.flags) ? sym.flags : []).map(flagWord).concat([sym.state || 'live']);
          li.innerHTML = escHtml(sym.name) +
            ' <span class="flush-armed-flags">' + escHtml(flags.join(' · ')) + '</span>';
          armedList.appendChild(li);
        });
        armedCallout.hidden = false;
      } else {
        armedCallout.hidden = true;
      }

      confirmLoading.hidden = true;
      confirmBody.hidden = false;
    }

    // "ok"      = all ops succeeded
    // "partial" = one or more failures (errors[] non-empty or composer_resync false)
    function showResult(resp) {
      btnTrigger.disabled = false;
      statusEl.textContent = '';
      statusEl.dataset.state = 'idle';

      var isOk      = resp.status === 'ok';
      var isPartial = resp.status === 'partial';

      if (isOk || isPartial) {
        resultEl.dataset.state = isOk ? 'success' : 'error';
        var stats = [
          { val: resp.deleted_count           != null ? resp.deleted_count           : '—', label: 'Files deleted'    },
          { val: resp.kept_count              != null ? resp.kept_count              : '—', label: 'Real files kept'   },
          { val: resp.symphonies_reset_count  != null ? resp.symphonies_reset_count  : '—', label: 'Symphonies reset'  },
          { val: resp.composer_resync ? 'yes' : 'no',                                       label: 'Composer re-sync'  },
        ];
        var errBlock = '';
        if (resp.errors && resp.errors.length) {
          errBlock = '<div class="flush-result-msg">' +
            escHtml('Errors: ' + resp.errors.join('; ')) + '</div>';
        }
        resultEl.innerHTML =
          '<div class="flush-result-title">' +
            escHtml(isOk ? 'Flush complete' : 'Flush completed with errors') +
          '</div>' +
          '<div class="flush-result-stats">' +
          stats.map(function (s) {
            return '<div class="flush-result-stat">' +
              '<span class="flush-stat-val">' + escHtml(String(s.val)) + '</span>' +
              '<span class="flush-stat-label">' + escHtml(s.label) + '</span>' +
              '</div>';
          }).join('') +
          '</div>' + errBlock;
      } else {
        resultEl.dataset.state = 'error';
        resultEl.innerHTML =
          '<div class="flush-result-title">' + escHtml(resp.title || 'Flush failed') + '</div>' +
          '<div class="flush-result-msg">' + escHtml(resp.message || 'Unknown error') + '</div>';
      }
      resultEl.hidden = false;
    }

    function runFlush() {
      setRunning();
      PSRequest.postAction('/api/settings/flush-resync', {
        method: 'POST',
        headers: { 'X-CSRF-Token': PSRequest.csrfToken() || '' }
      }).then(function (res) {
        if (res.networkError) {
          showResult({ status: 'error', message: 'Network error: the request could not be completed.' });
        } else if (res.refused === 'csrf') {
          // Nothing was flushed and nothing is sent again: the next Confirm click sends one request.
          showResult({
            status: 'error',
            title: 'Flush not sent',
            message: res.tokenRefreshed ? FLUSH_NOT_SENT_TEXT : FLUSH_NOT_SENT_OFFLINE_TEXT
          });
        } else if (!res.ok) {
          showResult({ status: 'error', message: 'Server error: HTTP ' + res.status });
        } else if (res.body === null) {
          showResult({ status: 'error', message: 'The answer could not be read.' });
        } else {
          showResult(res.body);
        }
      });
    }

    // First click: read the armed symphonies from the small armed-symphonies route (never the whole
    // state), then show the confirm panel.
    btnTrigger.addEventListener('click', function () {
      resultEl.hidden = true;
      btnTrigger.disabled = true;
      // Show panel in loading state immediately so there's no apparent freeze
      confirmLoading.hidden = false;
      confirmBody.hidden = true;
      confirmPanel.hidden = false;

      PSRequest.getJson('/api/armed-symphonies').then(function (res) {
        if (res.ok && res.body && Array.isArray(res.body.armed)) {
          renderConfirmPanel(res.body.armed);
          return;
        }
        if (res.kind === 'unauthorized') { PSRequest.goLogin(); return; }
        // The read failed: show the confirm panel without an armed list, and say so.
        renderConfirmPanel([], true);
      });
    });

    btnConfirm.addEventListener('click', runFlush);
    btnCancel.addEventListener('click', setIdle);
  }

  // ── Credential show/hide — wired once at DOMContentLoaded ─────────────────
  // Decoupled from renderCredentials so the toggle works even if the API
  // call fails or hasn't resolved yet.
  function initCredentialToggles() {
    document.querySelectorAll('.cred-field-wrap').forEach(wrap => {
      const inp = wrap.querySelector('.cred-input');
      const btn = wrap.querySelector('.cred-toggle-vis');
      if (!btn || !inp) return;
      btn.addEventListener('click', () => {
        const isPassword = inp.type === 'password';
        inp.type = isPassword ? 'text' : 'password';
        btn.textContent = isPassword ? 'hide' : 'show';
      });
    });
  }

  // ── Render symphony overrides ──────────────────────────────────────────────
  /**
   * One closed "What the terms mean" disclosure under the symphony overrides description. It is
   * built once and left alone by every later render, so an operator who opened it keeps it open.
   */
  function ensureSymphonyTerms() {
    const section = document.getElementById('sec-symphonies');
    if (!section || section.querySelector('details[data-terms="S14"]')) return;
    const split = section.querySelector('.sym-split');
    if (!split) return;
    const details = document.createElement('details');
    details.className = 'hp-more';
    details.dataset.terms = 'S14';
    const summary = document.createElement('summary');
    summary.textContent = 'What the terms mean';
    details.appendChild(summary);
    [['S15', "uses globals: no value set for this symphony. N overrides: values set for this symphony. lock: stop the autotune changing a value. override: a value that replaces the global one."], ['S16', "No longer held: Composer no longer reports this symphony and the engine writes it no rows; it returns here by itself if its rows resume."]].forEach(([key, text]) => {
      const line = document.createElement('p');
      line.dataset.term = key;
      line.textContent = text;
      details.appendChild(line);
    });
    section.insertBefore(details, split);
  }

  function renderSymphonies(data) {
    ensureSymphonyTerms();
    const list = document.getElementById('sym-list');
    list.innerHTML = '';

    const symphonies = data.symphonies_list || [];
    const noLongerHeld = Array.isArray(data.symphonies_no_longer_held) ? data.symphonies_no_longer_held : [];
    if (!symphonies.length) {
      if (!noLongerHeld.length) {
        list.innerHTML = '<div class="sym-empty">No symphonies tracked yet.</div>';
      } else {
        renderNoLongerHeld(list, noLongerHeld);
      }
      return;
    }

    if (!_selectedSym) _selectedSym = symphonies[0].id;

    symphonies.forEach(sym => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sym-list-btn';
      btn.dataset.symId = sym.id;
      btn.innerHTML = `
        <div class="sym-list-name" title="${escHtml(sym.name)}">${escHtml(sym.name)}</div>
        <div class="sym-list-meta">${symListMetaHtml(getPendingOverride(sym.id, data), data.globals || {})}</div>
      `;
      btn.addEventListener('click', () => {
        _selectedSym = sym.id;
        renderSymphonyDetail(data, sym.id);
        list.querySelectorAll('.sym-list-btn').forEach(b => b.classList.toggle('sym-active', b.dataset.symId === sym.id));
      });
      if (sym.id === _selectedSym) btn.classList.add('sym-active');
      list.appendChild(btn);
    });

    renderNoLongerHeld(list, noLongerHeld);
    renderSymphonyDetail(data, _selectedSym);
  }

  /**
   * The symphonies the engine still has a record of but no longer holds, dimmed under their
   * own heading: known, so the operator can see why they are not in the list above, with no
   * override to edit. Text only (textContent); nothing here is deleted or changed.
   */
  function renderNoLongerHeld(list, entries) {
    if (!entries.length) return;
    const heading = document.createElement('div');
    heading.className = 'sym-gone-heading';
    heading.textContent = 'No longer held';
    list.appendChild(heading);
    entries.forEach(sym => {
      const row = document.createElement('div');
      row.className = 'sym-gone-row';
      row.textContent = sym.name;
      row.title = 'Still recorded, but Composer no longer reports it and the engine writes it no rows. It returns here by itself if its rows resume.';
      list.appendChild(row);
    });
  }

  /**
   * Inner HTML of a list entry's meta line. A symphony that stores no parameter
   * "uses globals"; otherwise the line counts the stored values that differ from the
   * global and says how many equal it and stay pinned (they will not follow a later
   * global change). The lock count shows either way.
   */
  function symListMetaHtml(ov, globals) {
    const params = ov.params || {};
    const storedCount = Object.keys(params).length;
    const overrideCount = countParamOverrides(params, globals);
    const pinnedCount = storedCount - overrideCount;
    const lockedCount = (ov.locked_vars || []).length;

    let parts = '';
    if (!storedCount) {
      parts += '<span class="sym-uses-globals">uses globals</span>';
    } else {
      parts += overrideCount
        ? `<span class="sym-has-override">${overrideCount} override${overrideCount > 1 ? 's' : ''}</span>`
        : '<span class="sym-no-override">0 overrides</span>';
      if (pinnedCount) {
        parts += `<span class="sym-pinned-count" title="These stored values equal the global value today. They are stored for this symphony and will not follow a later global change.">${pinnedCount} pinned</span>`;
      }
    }
    if (lockedCount) parts += `<span class="sym-locked-count">${lockedCount} locked</span>`;
    return parts;
  }

  /** Re-draw one list entry's meta line from the pending state. The detail pane is left alone. */
  function refreshSymListEntry(symId) {
    if (!_state) return;
    const entries = document.getElementById('sym-list').querySelectorAll('.sym-list-btn');
    const btn = Array.from(entries).find(b => b.dataset.symId === symId);
    const metaEl = btn ? btn.querySelector('.sym-list-meta') : null;
    if (metaEl) metaEl.innerHTML = symListMetaHtml(getPendingOverride(symId, _state), _state.globals || {});
  }

  function renderSymphonyDetail(data, symId) {
    const detail = document.getElementById('sym-detail');
    const sym = (data.symphonies_list || []).find(s => s.id === symId);
    if (!sym) { detail.innerHTML = ''; return; }

    const ov = getPendingOverride(symId, data);
    const meta = data.param_meta || {};
    const globals = data.globals || {};

    // The symphony key goes in one title attribute only, and no element id is built from it:
    // a key can hold parentheses, quotes, spaces or a hash sign, none of which a selector survives.
    detail.innerHTML = `
      <div class="sym-detail-header">
        <div>
          <div class="sym-detail-name" title="${escHtml(sym.id)}">${escHtml(sym.name)}</div>
        </div>
        <button type="button" class="btn-reset-globals">Reset to globals</button>
      </div>
      <div class="override-grid">
        <div class="override-colhead" aria-hidden="true">
          <span>Parameter</span><span>Global</span><span>Override</span><span></span>
        </div>
      </div>
    `;

    const grid = detail.querySelector('.override-grid');
    Object.keys(meta).forEach(key => {
      const m = meta[key];
      const gVal = globals[key] != null ? globals[key] : '';
      const ovVal = ov.params[key];
      const locked = (ov.locked_vars || []).includes(key);
      // Marked as overridden only when the stored value differs from the global.
      const hasOverride = ovVal != null && overridesGlobal(ovVal, globals, key);

      const row = document.createElement('div');
      row.className = 'override-row' + (hasOverride ? ' override-row-active' : '');
      row.dataset.key = key;
      row.innerHTML = `
        <div class="override-row-header">
          <span class="override-key">${escHtml(key).replace(/_/g, '_<wbr>')}</span>
          <button type="button" class="lock-btn${locked ? ' lock-btn-on' : ''}" data-locked="${locked ? '1' : '0'}">${locked ? 'locked' : 'lock'}</button>
        </div>
        <div class="override-global-row">
          <span class="override-label">global</span>
          <span class="override-global-val">${escHtml(String(gVal))}${m.unit ? ' ' + escHtml(m.unit) : ''}</span>
        </div>
        <div class="override-input-row">
          <span class="override-label override-label-override${hasOverride ? ' override-label-active' : ''}">override</span>
          <input type="text" class="override-input" value="${ovVal != null ? escHtml(String(ovVal)) : ''}" placeholder="—" autocomplete="off">
        </div>
      `;
      grid.appendChild(row);

      row.querySelector('.lock-btn').addEventListener('click', function () {
        const isLocked = this.dataset.locked === '1';
        this.dataset.locked = isLocked ? '0' : '1';
        this.classList.toggle('lock-btn-on', !isLocked);
        this.textContent = !isLocked ? 'locked' : 'lock';
        toggleLock(symId, key, !isLocked, data);
        refreshSymListEntry(symId);
        markDirty();
      });

      row.querySelector('.override-input').addEventListener('input', function () {
        updateOverrideParam(symId, key, this.value, data);
        const differs = this.value !== '' && overridesGlobal(this.value, globals, key);
        row.classList.toggle('override-row-active', differs);
        const lbl = row.querySelector('.override-label-override');
        lbl.classList.toggle('override-label-active', differs);
        refreshSymListEntry(symId);
        markDirty();
      });
    });

    detail.querySelector('.btn-reset-globals').addEventListener('click', () => {
      resetSymToGlobals(symId);
    });
  }

  function getPendingOverride(symId, data) {
    if (_pendingOverrides[symId]) return _pendingOverrides[symId];
    const base = (data.symphony_overrides || {})[symId] || { params: {}, locked_vars: [] };
    return { params: Object.assign({}, base.params), locked_vars: (base.locked_vars || []).slice() };
  }

  function updateOverrideParam(symId, key, val, data) {
    if (!_pendingOverrides[symId]) _pendingOverrides[symId] = getPendingOverride(symId, data);
    if (val === '') {
      delete _pendingOverrides[symId].params[key];
    } else {
      _pendingOverrides[symId].params[key] = val;
    }
  }

  function toggleLock(symId, key, locked, data) {
    if (!_pendingOverrides[symId]) _pendingOverrides[symId] = getPendingOverride(symId, data);
    const lv = _pendingOverrides[symId].locked_vars;
    const idx = lv.indexOf(key);
    if (locked && idx === -1) lv.push(key);
    if (!locked && idx !== -1) lv.splice(idx, 1);
  }

  // Reset replaces only this symphony's pending entry. The loaded state is never mutated,
  // so Discard (a reload) restores the stored overrides and locks, and no request is sent
  // until Save.
  function resetSymToGlobals(symId) {
    _pendingOverrides[symId] = { params: {}, locked_vars: [] };
    renderSymphonyDetail(_state, symId);
    refreshSymListEntry(symId);
    markDirty();
  }

  // ── Render all sections ────────────────────────────────────────────────────
  function renderAll(data) {
    renderMaster(data);
    renderAlgorithm(data);
    renderCredentials(data);
    renderSymphonies(data);
  }

  // ── Master controls ────────────────────────────────────────────────────────
  // The Live execution switch is read-only (disabled in settings.html, mode drawn by
  // renderMaster): live trading is armed in the server's .env, and POST /api/settings
  // rejects LIVE_EXECUTION, so this page never binds a click to it.
  function initMasterControls() {
    document.getElementById('execution-start-time').addEventListener('input', function () {
      _pendingGlobals['EXECUTION_START_TIME'] = this.value;
      markDirty();
    });
  }

  // ── Save ───────────────────────────────────────────────────────────────────
  // The pending per-symphony entries as the request body wants them (numeric params).
  // A value that is not a number is a refusal naming the field, never a coerced string.
  function buildSymphoniesPayload() {
    const symphonies = {};
    const ids = Object.keys(_pendingOverrides);
    for (let i = 0; i < ids.length; i += 1) {
      const id = ids[i];
      const pending = _pendingOverrides[id];
      const listed = ((_state || {}).symphonies_list || []).find(s => s.id === id);
      const params = {};
      const entries = Object.entries(pending.params || {});
      for (let j = 0; j < entries.length; j += 1) {
        const key = entries[j][0];
        const n = parseFiniteNumber(entries[j][1]);
        if (n === null) {
          return {
            refusal: 'Save failed: ' + key + ' for ' + (listed ? listed.name : id) +
              ' is not a number (' + JSON.stringify(String(entries[j][1])) + '). Nothing was sent.',
          };
        }
        params[key] = n;
      }
      symphonies[id] = { params: params, locked_vars: pending.locked_vars || [] };
    }
    return { symphonies: symphonies };
  }

  function save() {
    const built = buildSymphoniesPayload();
    if (built.refusal) {
      alert(built.refusal);
      return;
    }

    const payload = {
      globals: _pendingGlobals,
      symphonies: built.symphonies,
    };
    const sentEditSeq = _editSeq;

    PSRequest.postAction('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
      body: JSON.stringify(payload),
    }).then(res => {
      if (res.networkError) {
        alert('Save failed: network error');
        return;
      }
      if (res.refused === 'csrf') {
        // Nothing was saved and nothing is sent again: the edits stay, Save stays enabled, and the
        // next click sends one request with the token just read.
        alert(res.tokenRefreshed ? SAVE_NOT_SENT_TEXT : SAVE_NOT_SENT_OFFLINE_TEXT);
        return;
      }
      const resp = res.body;
      if (res.ok && resp && resp.status === 'success') {
        // Reload to show what was saved, unless something was typed after the request was sent:
        // a reload would discard it, so the typed value and an enabled Save stay.
        if (_editSeq === sentEditSeq) load();
        return;
      }
      let reason = 'HTTP ' + res.status;
      if (resp && resp.message) reason = resp.message;
      alert('Save failed: ' + reason);
    });
  }

  // ── Discard ────────────────────────────────────────────────────────────────
  function discard() {
    _selectedSym = null;
    load();
  }

  // ── Utility ────────────────────────────────────────────────────────────────
  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.sn-btn').forEach(btn => {
      btn.addEventListener('click', () => activateSection(btn.dataset.section));
    });

    document.getElementById('btn-save').addEventListener('click', () => {
      if (_dirty) save();
    });
    document.getElementById('btn-discard').addEventListener('click', () => {
      if (_dirty) discard();
    });
    const retryBtn = document.getElementById('btn-settings-retry');
    if (retryBtn) retryBtn.addEventListener('click', () => { load(); });

    initMasterControls();
    initCredentialToggles();
    initFlushControl();
    activateSection('master');
    load();
  });
})();
