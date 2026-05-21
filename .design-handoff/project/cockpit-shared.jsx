// Direction C — Cockpit · shared bits (palette, helpers, dial, sparkline)

(function () {
  const { useMemo } = React;

  function palette(theme, accent) {
    if (theme === 'dark') {
      return {
        bg: '#0f1213',
        surface: '#171b1d',
        surfaceHi: '#1d2225',
        rule: 'rgba(245,243,237,0.10)',
        ruleHi: 'rgba(245,243,237,0.20)',
        ink: '#f0ece2',
        inkDim: 'rgba(240,236,226,0.66)',
        inkFaint: 'rgba(240,236,226,0.40)',
        chipBg: 'rgba(240,236,226,0.06)',
        accent, pos: '#5cc28c', neg: '#e76a55',
        warn: '#e3a14a', plum: '#b58cc8', cyan: '#6fb8c8',
      };
    }
    return {
      bg: '#ebe7dc',
      surface: '#fbf8ee',
      surfaceHi: '#ffffff',
      rule: 'rgba(20,18,12,0.10)',
      ruleHi: 'rgba(20,18,12,0.22)',
      ink: '#16140c',
      inkDim: 'rgba(22,20,12,0.66)',
      inkFaint: 'rgba(22,20,12,0.38)',
      chipBg: 'rgba(22,20,12,0.04)',
      accent, pos: '#1f7a4d', neg: '#b4382a',
      warn: '#b07419', plum: '#6a3f8a', cyan: '#1a6e88',
    };
  }
  window.cockpitPalette = palette;

  window.cockpitStatus = function (sym, p) {
    if (sym.triggered) {
      const r = sym.triggered_reason || '';
      if (r.includes('Take-Profit')) return { color: p.plum, label: 'TAKE-PROFIT', kind: 'triggered' };
      if (r.includes('VWAP')) return { color: p.cyan, label: r.toUpperCase(), kind: 'triggered' };
      return { color: p.neg, label: 'TRAILING STOP', kind: 'triggered' };
    }
    if (sym.para_armed) return { color: p.cyan, label: 'PARA-ARMED', kind: 'armed' };
    if (sym.tp_armed) return { color: p.plum, label: 'TP-ARMED', kind: 'armed' };
    if (sym.armed) return { color: p.warn, label: 'ARMED', kind: 'armed' };
    return { color: p.inkFaint, label: 'STANDBY', kind: 'standby' };
  };

  // MC probability dial — small ring, fill rotates
  window.CockpitDial = function ({ value, color, faint, size = 44, stroke = 4 }) {
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const pct = Math.max(0, Math.min(100, value || 0)) / 100;
    return (
      <svg width={size} height={size}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={faint} strokeWidth={stroke} />
        <circle
          cx={size/2} cy={size/2} r={r} fill="none"
          stroke={color} strokeWidth={stroke}
          strokeDasharray={`${c * pct} ${c}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${size/2} ${size/2})`}
        />
      </svg>
    );
  };

  window.CockpitSparkArea = function ({ data, color, width = 220, height = 56, stop = null }) {
    if (!data || data.length < 2) return null;
    const lo = Math.min(...data, stop ?? data[0], 0);
    const hi = Math.max(...data, stop ?? data[0]);
    const span = (hi - lo) || 1;
    const xs = data.map((_, i) => (i / (data.length - 1)) * width);
    const ys = data.map(v => height - ((v - lo) / span) * height);
    const lineD = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
    const areaD = `${lineD} L${width},${height} L0,${height} Z`;
    const stopY = stop != null ? height - ((stop - lo) / span) * height : null;
    const zeroY = height - ((0 - lo) / span) * height;
    const lastY = ys[ys.length - 1];
    // Build a safe gradient id — color may be an rgba() string with parens that
    // would break url(#…) references, so strip to alphanumeric.
    const gradId = `cockpit-g-${color.replace(/[^a-z0-9]/gi, '')}-${Math.round(width)}-${Math.round(height)}`;
    return (
      <svg width={width} height={height} style={{ display: 'block' }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.22" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {zeroY > 0 && zeroY < height && (
          <line x1={0} x2={width} y1={zeroY} y2={zeroY} stroke="currentColor" strokeOpacity="0.15" strokeWidth={1} />
        )}
        {stopY != null && (
          <line x1={0} x2={width} y1={stopY} y2={stopY} stroke={color} strokeOpacity="0.45" strokeWidth={1} strokeDasharray="3,3" />
        )}
        <path d={areaD} fill={`url(#${gradId})`} />
        <path d={lineD} fill="none" stroke={color} strokeWidth={1.6} strokeLinecap="round" />
        <circle cx={width} cy={lastY} r={2.5} fill={color} />
      </svg>
    );
  };

  window.CockpitMicroSpark = function ({ data, color, width = 100, height = 24 }) {
    if (!data || data.length < 2) return null;
    const lo = Math.min(...data), hi = Math.max(...data);
    const span = (hi - lo) || 1;
    const pts = data.map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - lo) / span) * height;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return (
      <svg width={width} height={height} style={{ display: 'block' }}>
        <path d={pts} fill="none" stroke={color} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  };
})();
