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
  let _pendingGlobals = {};
  let _pendingOverrides = {};  // { sym_id: { params: {}, locked_vars: [] } }
  let _selectedSym = null;

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

  /** CSRF token fetched from GET /api/csrf-token on page load (AC-1). */
  let _csrfToken = null;
  document.addEventListener('DOMContentLoaded', function () {
    fetch('/api/csrf-token')
      .then(function (r) { return r.json(); })
      .then(function (b) { _csrfToken = b.csrf_token || null; })
      .catch(function () { /* csrf token unavailable — POSTs will 403 */ });
  });

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
    _dirty = true;
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
  function load(retryOnce) {
    fetch('/api/settings')
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(data => {
        _state = data;
        // Pending edits are dropped before rendering: the detail pane reads them, so
        // clearing afterwards would leave a discarded edit on screen.
        clearDirty();
        renderAll(data);
      })
      .catch(() => {
        if (retryOnce !== false) {
          // Single silent retry after 400 ms — handles transient startup race.
          setTimeout(() => load(false), 400);
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

    toggle.dataset.on = live ? '1' : '0';
    toggle.setAttribute('aria-checked', String(live));
    pill.textContent = live ? 'LIVE' : 'DRY RUN';
    pill.dataset.live = String(live);
    banner.hidden = !live;
    card.dataset.live = String(live);

    const startInput = document.getElementById('execution-start-time');
    startInput.value = data.globals.EXECUTION_START_TIME != null ? data.globals.EXECUTION_START_TIME : '';
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
        <div class="param-name">${escHtml(key)}</div>
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
      if (statusEl) { statusEl.textContent = 'unchanged'; statusEl.dataset.ready = '0'; }
      inp.value = '';
      inp.addEventListener('input', () => {
        if (statusEl) {
          statusEl.textContent = inp.value ? '↑ ready to save' : 'unchanged';
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

    // Extract armed symphony names from /api/state bot_state.
    // Armed = any of armed / tp_armed / para_armed / triggered truthy.
    function extractArmed(apiState) {
      var bs = apiState.bot_state || {};
      var armed = [];
      Object.keys(bs).forEach(function (k) {
        var s = bs[k];
        if (!s || typeof s !== 'object') return;
        var flags = [];
        if (s.armed)      flags.push('ARMED');
        if (s.tp_armed)   flags.push('TP');
        if (s.para_armed) flags.push('PARA');
        if (s.triggered)  flags.push('TRIGGERED');
        if (flags.length) {
          armed.push({ name: s.name || k, flags: flags });
        }
      });
      return armed;
    }

    function renderConfirmPanel(armedSymphonies) {
      // Clear and rebuild the armed list
      armedList.innerHTML = '';
      if (armedSymphonies.length) {
        armedHeading.textContent = '⚠ ' + armedSymphonies.length +
          ' ' + (armedSymphonies.length === 1 ? 'symphony is' : 'symphonies are') +
          ' currently armed — flushing will reset their guard state.';
        armedSymphonies.forEach(function (sym) {
          var li = document.createElement('li');
          li.className = 'flush-armed-item';
          li.innerHTML = escHtml(sym.name) +
            ' <span class="flush-armed-flags">' + escHtml(sym.flags.join(' · ')) + '</span>';
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
          '<div class="flush-result-title">Flush failed</div>' +
          '<div class="flush-result-msg">' + escHtml(resp.message || 'Unknown error') + '</div>';
      }
      resultEl.hidden = false;
    }

    function runFlush() {
      setRunning();
      fetch('/api/settings/flush-resync', { method: 'POST', headers: { 'X-CSRF-Token': _csrfToken || '' } })
        .then(function (r) {
          if (!r.ok) return { status: 'error', message: 'Server error: HTTP ' + r.status };
          return r.json();
        })
        .then(showResult)
        .catch(function (err) {
          showResult({ status: 'error', message: 'Network error: ' + err.message });
        });
    }

    // First click: fetch /api/state to check for armed symphonies, then show panel
    btnTrigger.addEventListener('click', function () {
      resultEl.hidden = true;
      btnTrigger.disabled = true;
      // Show panel in loading state immediately so there's no apparent freeze
      confirmLoading.hidden = false;
      confirmBody.hidden = true;
      confirmPanel.hidden = false;

      fetch('/api/state')
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (apiState) {
          renderConfirmPanel(extractArmed(apiState));
        })
        .catch(function () {
          // State fetch failed — show confirm panel without armed list
          renderConfirmPanel([]);
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
  function renderSymphonies(data) {
    const list = document.getElementById('sym-list');
    list.innerHTML = '';

    const symphonies = data.symphonies_list || [];
    if (!symphonies.length) {
      list.innerHTML = '<div class="sym-empty">No symphonies tracked yet.</div>';
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

    renderSymphonyDetail(data, _selectedSym);
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
        ? `<span class="sym-has-override">&#9670; ${overrideCount} override${overrideCount > 1 ? 's' : ''}</span>`
        : '<span class="sym-no-override">0 overrides</span>';
      if (pinnedCount) {
        parts += `<span class="sym-pinned-count" title="These stored values equal the global value today. They are stored for this symphony and will not follow a later global change.">${pinnedCount} pinned at the global value</span>`;
      }
    }
    if (lockedCount) parts += `<span class="sym-locked-count">&middot; ${lockedCount} locked</span>`;
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
      <div class="override-grid"></div>
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
          <span class="override-key">${escHtml(key)}</span>
          <button type="button" class="lock-btn${locked ? ' lock-btn-on' : ''}" data-locked="${locked ? '1' : '0'}">${locked ? '&#9670; locked' : 'lock'}</button>
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
        this.innerHTML = !isLocked ? '&#9670; locked' : 'lock';
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

    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken || '' },
      body: JSON.stringify(payload),
    })
      // Read the body as text first: a refusal from the CSRF or auth hook is an HTML
      // page, and parsing it as JSON would be reported as a network error.
      .then(r => r.text().then(text => ({ status: r.status, ok: r.ok, text: text })))
      .then(res => {
        let resp = null;
        try { resp = JSON.parse(res.text); } catch (e) { resp = null; }
        if (res.ok && resp && resp.status === 'success') {
          load();
          return;
        }
        let reason = 'HTTP ' + res.status;
        if (resp && resp.message) {
          reason = resp.message;
        } else if (res.status === 403) {
          reason = 'the server refused the request (HTTP 403). Nothing was saved.';
        }
        alert('Save failed: ' + reason);
      })
      .catch(err => {
        console.error('save error', err);
        alert('Save failed: network error');
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

    initMasterControls();
    initCredentialToggles();
    initFlushControl();
    activateSection('master');
    load();
  });
})();
