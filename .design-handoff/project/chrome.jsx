// Shared chrome — TopBar + nav + page shell used by every Studio route.
// Each route renders <PageShell route="performance"> ... </PageShell>.
// The TopBar reads tab state from the `route` prop and lets you click another
// nav item; the host wires that to navigate(...) (canvas artboards just ignore).

(function () {
  const { useState } = React;

  const ROUTES = [
    { id: 'dashboard',   label: 'Dashboard'   },
    { id: 'performance', label: 'Performance' },
    { id: 'advisor',     label: 'Advisor'     },
    { id: 'history',     label: 'History'     },
    { id: 'settings',    label: 'Settings'    },
  ];

  function TopBar({ data, p, route, onNavigate, onForceRun }) {
    const m = data.meta;
    const isLive = m.mode === 'LIVE';
    return (
      <header style={{
        display: 'flex', alignItems: 'center', gap: 20,
        padding: '14px 28px',
        borderBottom: `1px solid ${p.rule}`,
        background: p.paper,
        flexShrink: 0,
      }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em',
          color: p.ink, display: 'flex', alignItems: 'baseline', gap: 7,
        }}>
          AlphaBot
          <span style={{ color: p.accent, fontWeight: 500, fontSize: 13 }}>guard</span>
        </div>

        <nav style={{ display: 'flex', gap: 2, marginLeft: 12 }}>
          {ROUTES.map((r) => {
            const active = r.id === route;
            return (
              <button key={r.id} onClick={() => onNavigate?.(r.id)} style={{
                padding: '6px 11px', border: 'none',
                background: 'transparent',
                color: active ? p.ink : p.inkDim,
                fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                fontSize: 12.5,
                fontWeight: active ? 700 : 500, letterSpacing: '-0.005em',
                cursor: 'pointer', position: 'relative',
              }}>
                {r.label}
                {active && (
                  <span style={{
                    position: 'absolute', left: 8, right: 8, bottom: -15, height: 2,
                    background: p.accent,
                  }} />
                )}
              </button>
            );
          })}
        </nav>

        <div style={{ flex: 1 }} />

        <div style={{
          display: 'flex', alignItems: 'center', gap: 7,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5,
          color: p.inkDim, letterSpacing: '0.04em',
          fontVariantNumeric: 'tabular-nums', fontWeight: 500,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: 6,
            background: m.system_online ? p.pos : p.neg,
            boxShadow: m.system_online ? `0 0 0 3px ${p.pos}22` : 'none',
          }} />
          ONLINE · NEXT {m.next_run}
        </div>

        <button style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 8px 6px 10px',
          background: p.chipBg, border: `1px solid ${p.rule}`,
          borderRadius: 4, cursor: 'pointer',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12, color: p.ink,
          fontWeight: 600,
        }}>
          {m.account.label}
          <span style={{
            color: p.inkFaint, fontSize: 10, fontWeight: 500,
            fontVariantNumeric: 'tabular-nums',
          }}>
            {m.account.uuid_short}
          </span>
          <span style={{ color: p.inkFaint, fontSize: 10 }}>▾</span>
        </button>

        <div style={{
          display: 'flex', alignItems: 'center', gap: 7,
          padding: '6px 10px',
          background: isLive ? `${p.neg}1a` : 'transparent',
          border: `1px solid ${isLive ? p.neg : p.ruleHi}`,
          borderRadius: 4,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11, fontWeight: 700,
          letterSpacing: '0.1em', color: isLive ? p.neg : p.ink,
        }}>
          <span style={{
            width: 5, height: 5, borderRadius: 5,
            background: isLive ? p.neg : p.inkDim,
          }} />
          {m.mode}
        </div>

        <button onClick={onForceRun} style={{
          padding: '7px 14px',
          background: p.accent, color: '#fff',
          border: 'none', borderRadius: 4, cursor: 'pointer',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12, fontWeight: 700,
          letterSpacing: '0.02em',
        }}>
          Force run now
        </button>
      </header>
    );
  }

  // Compact section header used across content routes (Performance, Advisor,
  // History, Settings). Title + optional subtitle, with optional right-side slot.
  function PageHeader({ title, subtitle, right, p, dense }) {
    return (
      <div style={{
        padding: dense ? '18px 28px 14px' : '28px 28px 18px',
        borderBottom: `1px solid ${p.rule}`,
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 24,
        background: p.paper,
      }}>
        <div>
          <h1 style={{
            margin: 0,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: dense ? 24 : 32, fontWeight: 700,
            letterSpacing: '-0.025em', color: p.ink, lineHeight: 1.05,
          }}>
            {title}
          </h1>
          {subtitle && (
            <div style={{
              marginTop: 6, fontSize: 12, color: p.inkDim,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              letterSpacing: '0.02em',
            }}>
              {subtitle}
            </div>
          )}
        </div>
        {right}
      </div>
    );
  }

  // Pill control used in toolbars (Window 30/60/90/125 etc).
  function SegControl({ value, options, onChange, p }) {
    return (
      <div style={{
        display: 'inline-flex', padding: 2,
        background: p.chipBg, border: `1px solid ${p.rule}`, borderRadius: 4,
      }}>
        {options.map(o => {
          const active = o.value === value;
          return (
            <button key={o.value} onClick={() => onChange?.(o.value)} style={{
              padding: '5px 11px',
              background: active ? p.paper : 'transparent',
              border: active ? `1px solid ${p.rule}` : '1px solid transparent',
              boxShadow: active ? `0 1px 0 ${p.ruleHi}` : 'none',
              borderRadius: 3, cursor: 'pointer',
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              fontSize: 11, fontWeight: active ? 700 : 500,
              color: active ? p.ink : p.inkDim,
              letterSpacing: '0.02em',
              fontVariantNumeric: 'tabular-nums',
            }}>
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }

  function FieldLabel({ children, p }) {
    return (
      <div style={{
        fontSize: 9.5, fontWeight: 700, color: p.inkDim,
        letterSpacing: '0.12em', textTransform: 'uppercase',
        marginBottom: 5,
        fontFamily: 'var(--studio-sans, Manrope), sans-serif',
      }}>
        {children}
      </div>
    );
  }

  function Select({ value, options, onChange, p, minWidth }) {
    return (
      <select
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        style={{
          padding: '6px 28px 6px 10px',
          background: p.chipBg, border: `1px solid ${p.rule}`, borderRadius: 4,
          color: p.ink,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 12, fontWeight: 500,
          appearance: 'none',
          backgroundImage: `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'><path d='M2 4l3 3 3-3' fill='none' stroke='${encodeURIComponent(p.inkDim)}' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'/></svg>")`,
          backgroundRepeat: 'no-repeat',
          backgroundPosition: 'right 8px center',
          minWidth: minWidth || 'auto',
          cursor: 'pointer',
        }}
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  }

  Object.assign(window, { TopBar, PageHeader, SegControl, FieldLabel, Select, ROUTES });

  // ── Shared time-range windows + synthesizer ─────────────────────────────
  // Used by Performance, History, Dashboard hero and Detail panel perf chart so
  // every time-range selector has the same option set (30d → 5Y including YTD).
  const TIME_WINDOWS = [
    { value: 30,    label: '30d'  },
    { value: 60,    label: '60d'  },
    { value: 90,    label: '90d'  },
    { value: 125,   label: '125d' },
    { value: 'ytd', label: 'YTD'  },
    { value: 252,   label: '1Y'   },
    { value: 1260,  label: '5Y'   },
  ];
  const TIME_WINDOW_DAYS = { 30: 30, 60: 60, 90: 90, 125: 125, 'ytd': 95, 252: 252, 1260: 1260 };

  function lcg(seed) { let s = seed | 0; return () => { s = (s * 1664525 + 1013904223) | 0; return ((s >>> 0) / 0xFFFFFFFF); }; }
  function synthSeries(days, end, seed) {
    const rnd = lcg(seed);
    const out = [0];
    const drift = end / days;
    const vol = end > 50 ? 1.4 : 0.9;
    let cum = 0;
    for (let i = 1; i < days; i++) {
      cum += drift + (rnd() - 0.5) * vol * 0.5;
      out.push(cum);
    }
    out[out.length - 1] = end;
    return out;
  }
  function synthDates(days) {
    const out = [];
    let d = new Date(2026, 4, 18);
    while (out.length < days) {
      const dow = d.getDay();
      if (dow !== 0 && dow !== 6) out.unshift(`${d.getMonth()+1}/${d.getDate()}`);
      d = new Date(d.getTime() - 86400000);
    }
    return out;
  }
  Object.assign(window, { TIME_WINDOWS, TIME_WINDOW_DAYS, synthSeries, synthDates });
})();
