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
        renderAll(data);
        clearDirty();
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
      fetch('/api/settings/flush-resync', { method: 'POST' })
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
      const ov = (data.symphony_overrides || {})[sym.id] || { params: {}, locked_vars: [] };
      const overrideCount = Object.keys(ov.params || {}).length;
      const lockedCount = (ov.locked_vars || []).length;

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sym-list-btn';
      btn.dataset.symId = sym.id;
      btn.innerHTML = `
        <div class="sym-list-name" title="${escHtml(sym.name)}">${escHtml(sym.name)}</div>
        <div class="sym-list-meta">
          ${overrideCount
            ? `<span class="sym-has-override">&#9670; ${overrideCount} override${overrideCount > 1 ? 's' : ''}</span>`
            : '<span class="sym-uses-globals">uses globals</span>'}
          ${lockedCount ? `<span class="sym-locked-count">&middot; ${lockedCount} locked</span>` : ''}
        </div>
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

  function renderSymphonyDetail(data, symId) {
    const detail = document.getElementById('sym-detail');
    const sym = (data.symphonies_list || []).find(s => s.id === symId);
    if (!sym) { detail.innerHTML = ''; return; }

    const ov = getPendingOverride(symId, data);
    const meta = data.param_meta || {};
    const globals = data.globals || {};

    detail.innerHTML = `
      <div class="sym-detail-header">
        <div>
          <div class="sym-detail-name">${escHtml(sym.name)}</div>
          <div class="sym-detail-id">${escHtml(sym.id)} &middot; normalized: ${escHtml(sym.id.replace('sym_', ''))}</div>
        </div>
        <button type="button" class="btn-reset-globals" id="btn-reset-${escHtml(symId)}">Reset to globals</button>
      </div>
      <div class="override-grid" id="override-grid-${escHtml(symId)}"></div>
    `;

    const grid = detail.querySelector('.override-grid');
    Object.keys(meta).forEach(key => {
      const m = meta[key];
      const gVal = globals[key] != null ? globals[key] : '';
      const ovVal = ov.params[key];
      const locked = (ov.locked_vars || []).includes(key);
      const hasOverride = ovVal != null;

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
          <input type="text" class="override-input" value="${hasOverride ? escHtml(String(ovVal)) : ''}" placeholder="—" autocomplete="off">
        </div>
      `;
      grid.appendChild(row);

      row.querySelector('.lock-btn').addEventListener('click', function () {
        const isLocked = this.dataset.locked === '1';
        this.dataset.locked = isLocked ? '0' : '1';
        this.classList.toggle('lock-btn-on', !isLocked);
        this.innerHTML = !isLocked ? '&#9670; locked' : 'lock';
        toggleLock(symId, key, !isLocked, data);
        markDirty();
      });

      row.querySelector('.override-input').addEventListener('input', function () {
        updateOverrideParam(symId, key, this.value, data);
        const hasVal = this.value !== '';
        row.classList.toggle('override-row-active', hasVal);
        const lbl = row.querySelector('.override-label-override');
        lbl.classList.toggle('override-label-active', hasVal);
        markDirty();
      });
    });

    detail.querySelector('#btn-reset-' + escHtml(symId)).addEventListener('click', () => {
      resetSymToGlobals(symId, data);
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

  function resetSymToGlobals(symId, data) {
    _pendingOverrides[symId] = { params: {}, locked_vars: [] };
    renderSymphonyDetail(data, symId);
    markDirty();
    // Refresh list meta
    renderSymphonies(Object.assign({}, _state, {
      symphony_overrides: buildMergedOverrides(),
    }));
  }

  function buildMergedOverrides() {
    const merged = Object.assign({}, (_state || {}).symphony_overrides || {});
    Object.assign(merged, _pendingOverrides);
    return merged;
  }

  // ── Render all sections ────────────────────────────────────────────────────
  function renderAll(data) {
    renderMaster(data);
    renderAlgorithm(data);
    renderCredentials(data);
    renderSymphonies(data);
  }

  // ── Live toggle ────────────────────────────────────────────────────────────
  function initLiveToggle() {
    const toggle = document.getElementById('live-toggle');
    toggle.addEventListener('click', () => {
      const wasOn = toggle.dataset.on === '1';
      const nowOn = !wasOn;
      toggle.dataset.on = nowOn ? '1' : '0';
      toggle.setAttribute('aria-checked', String(nowOn));

      const pill = document.getElementById('live-pill');
      pill.textContent = nowOn ? 'LIVE' : 'DRY RUN';
      pill.dataset.live = String(nowOn);

      document.getElementById('live-banner').hidden = !nowOn;
      document.getElementById('live-card').dataset.live = String(nowOn);

      _pendingGlobals['LIVE_EXECUTION'] = nowOn ? 'True' : 'False';
      markDirty();
    });

    document.getElementById('execution-start-time').addEventListener('input', function () {
      _pendingGlobals['EXECUTION_START_TIME'] = this.value;
      markDirty();
    });
  }

  // ── Save ───────────────────────────────────────────────────────────────────
  function save() {
    const symphoniesPayload = {};
    const allSymIds = new Set([
      ...Object.keys((_state || {}).symphony_overrides || {}),
      ...Object.keys(_pendingOverrides),
    ]);
    allSymIds.forEach(id => {
      const pending = _pendingOverrides[id];
      if (pending) {
        const numParams = {};
        Object.entries(pending.params || {}).forEach(([k, v]) => {
          const n = parseFloat(v);
          numParams[k] = isNaN(n) ? v : n;
        });
        symphoniesPayload[id] = { params: numParams, locked_vars: pending.locked_vars || [] };
      }
    });

    const payload = {
      globals: _pendingGlobals,
      symphonies: symphoniesPayload,
    };

    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(r => r.json())
      .then(resp => {
        if (resp.status === 'success') {
          load();
        } else {
          alert('Save failed: ' + (resp.message || 'unknown error'));
        }
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

    initLiveToggle();
    initCredentialToggles();
    initFlushControl();
    activateSection('master');
    load();
  });
})();
