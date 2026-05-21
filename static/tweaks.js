(function () {
  var STORAGE_KEY = 'studioTweaks';

  // Valid density values: 'compact' | 'balanced' | 'roomy'
  var TWEAK_DEFAULTS = {
    theme: 'light',
    density: 'balanced',
    // Derive from CSS token so the default tracks tokens.css --studio-accent; hex is a fallback only.
    accent: (typeof getComputedStyle !== 'undefined'
      ? getComputedStyle(document.documentElement).getPropertyValue('--studio-accent').trim()
      : '') || '#1f7a4d',
    typeface: 'Manrope',
    mathOverlays: true,
    numFormat: 'full',
  };

  function load() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function save(values) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
    } catch (e) {}
  }

  function get(key) {
    var stored = load();
    if (stored && Object.prototype.hasOwnProperty.call(stored, key)) {
      return stored[key];
    }
    return TWEAK_DEFAULTS[key];
  }

  function set(key, value) {
    var stored = load() || Object.assign({}, TWEAK_DEFAULTS);
    stored[key] = value;
    save(stored);
    window.dispatchEvent(new CustomEvent('tweakchange', { detail: { key: key, value: value } }));
  }

  function getAll() {
    var stored = load();
    return Object.assign({}, TWEAK_DEFAULTS, stored || {});
  }

  function seed() {
    if (!load()) {
      save(Object.assign({}, TWEAK_DEFAULTS));
    }
  }

  function applyAll(values) {
    var root = document.documentElement;
    root.setAttribute('data-theme', values.theme);
    root.setAttribute('data-density', values.density);
    root.style.setProperty('--studio-accent', values.accent);
    root.style.setProperty('--studio-sans', values.typeface + ', system-ui, sans-serif');
    root.setAttribute('data-math-overlays', values.mathOverlays ? 'true' : 'false');
    root.setAttribute('data-num-format', values.numFormat);
  }

  // Sync every control widget in the tweaks panel to match stored values.
  // Called after applyAll so the DOM attributes are already correct.
  function syncControls(values) {
    var panel = document.getElementById('studio-tweaks-panel');
    if (!panel) return;

    // Theme select
    var themeSelect = panel.querySelector('select[onchange*="theme"]');
    if (themeSelect) themeSelect.value = values.theme;

    // Density select
    var densitySelect = panel.querySelector('select[onchange*="density"]');
    if (densitySelect) densitySelect.value = values.density;

    // Typeface select
    var typefaceSelect = panel.querySelector('[data-testid="typeface-select"]');
    if (typefaceSelect) typefaceSelect.value = values.typeface;

    // Number-format select
    var numFormatSelect = panel.querySelector('select[onchange*="numFormat"]');
    if (numFormatSelect) numFormatSelect.value = values.numFormat;

    // Math-overlays toggle
    var mathToggle = panel.querySelector('.twk-toggle');
    if (mathToggle) {
      var on = values.mathOverlays ? '1' : '0';
      mathToggle.dataset.on = on;
      mathToggle.setAttribute('aria-checked', values.mathOverlays ? 'true' : 'false');
    }

    // Accent swatches — find which swatch-var resolves to the saved accent value,
    // mark it checked; clear all others.
    var swatches = panel.querySelectorAll('[data-testid="accent-swatch"]');
    swatches.forEach(function (btn) {
      var swatchColor = getComputedStyle(document.documentElement)
        .getPropertyValue(btn.dataset.swatchVar).trim();
      var isActive = swatchColor === values.accent;
      btn.setAttribute('aria-checked', String(isActive));
      btn.style.border = isActive ? '2px solid var(--studio-ink)' : '2px solid transparent';
    });
  }

  seed();

  window.studioTweaks = { get: get, set: set, getAll: getAll, DEFAULTS: TWEAK_DEFAULTS };

  document.addEventListener('DOMContentLoaded', function () {
    var values = getAll();
    applyAll(values);
    syncControls(values);
  });
  window.addEventListener('tweakchange', function () {
    var values = getAll();
    applyAll(values);
    syncControls(values);
  });
})();
