// /performance route — aggregate or per-symphony cumulative returns
// (Live held vs AlphaBot-exited shadow) + QuantStats risk metrics table.
// Matches the /api/performance contract in app.py.

(function () {
  const { useState, useMemo } = React;
  const { TopBar, PageHeader, SegControl } = window;

  const WINDOWS = [
    { value: 30,   label: '30d'  },
    { value: 60,   label: '60d'  },
    { value: 90,   label: '90d'  },
    { value: 125,  label: '125d' },
    { value: 'ytd', label: 'YTD' },
    { value: 252,  label: '1Y'   },
    { value: 1260, label: '5Y'   },
  ];
  const SCOPES = [
    { value: 'aggregate', label: 'Aggregate' },
    { value: 'symphony',  label: 'Per-symphony' },
  ];

  // Realistic-feeling end-states per window. AlphaBot trends ~25% per year of
  // cumulative return with shadow tracking 15-20% behind on long horizons
  // (positive guard alpha).
  const WINDOW_ENDS = {
    30:    { live: 8.4,   bot: 9.6   },
    60:    { live: 14.8,  bot: 18.3  },
    90:    { live: 21.2,  bot: 25.6  },
    125:   { live: 28.4,  bot: 33.1  },
    'ytd': { live: 23.5,  bot: 28.7  },  // ~95 trading days Jan→mid-May
    252:   { live: 38.6,  bot: 47.4  },
    1260:  { live: 142.8, bot: 198.6 },
  };
  // Trading-day counts. YTD resolves to ~95 in May.
  const WINDOW_DAYS = { 30: 30, 60: 60, 90: 90, 125: 125, 'ytd': 95, 252: 252, 1260: 1260 };

  // Deterministic random walk — same seed → same series, so the chart isn't
  // re-rolling on every render.
  function lcg(seed) {
    let s = seed | 0;
    return () => { s = (s * 1664525 + 1013904223) | 0; return ((s >>> 0) / 0xFFFFFFFF); };
  }

  function synthSeries(days, end, seed) {
    const rnd = lcg(seed);
    const out = [0];
    let cum = 0;
    const drift = end / days;
    for (let i = 1; i < days; i++) {
      // daily return = drift + noise. Daily vol scales with sqrt(time).
      const noise = (rnd() - 0.5) * (end > 50 ? 1.4 : 0.9);
      cum += drift + noise * 0.5;
      out.push(cum);
    }
    // Force end exactly at target so labels match
    out[out.length - 1] = end;
    return out;
  }

  function synthDates(days) {
    const out = [];
    const start = new Date(2026, 4, 18); // May 18 2026 = today
    let d = new Date(start.getTime());
    let count = 0;
    while (out.length < days) {
      // Skip weekends
      const dow = d.getDay();
      if (dow !== 0 && dow !== 6) {
        out.unshift(`${d.getMonth()+1}/${d.getDate()}`);
        count++;
      }
      d = new Date(d.getTime() - 86400000);
    }
    return out;
  }

  const METRICS = [
    { key: 'total_return',       label: 'Total return',        suffix: '%', betterHigh: true,  primary: true },
    { key: 'annualized_return',  label: 'Annualized',          suffix: '%', betterHigh: true },
    { key: 'sharpe',             label: 'Sharpe',              suffix: '',  betterHigh: true,  digits: 2, primary: true },
    { key: 'sortino',            label: 'Sortino',             suffix: '',  betterHigh: true,  digits: 2 },
    { key: 'max_drawdown',       label: 'Max drawdown',        suffix: '%', betterHigh: false, primary: true },
    { key: 'calmar',             label: 'Calmar',              suffix: '',  betterHigh: true,  digits: 2 },
    { key: 'win_rate',           label: 'Win rate',            suffix: '%', betterHigh: true,  digits: 1 },
  ];

  function Performance({ data, t, onNavigate, onForceRun }) {
    const p = window.studioPalette(t.theme, t.accent);
    const perf = data.performance;
    const [scope, setScope] = useState(perf.scope);
    const [symId, setSymId] = useState('sym_paragons');
    const [windowDays, setWindowDays] = useState(perf.window_days);

    const series = useMemo(() => {
      const dayCount = WINDOW_DAYS[windowDays] || windowDays;
      const ends = WINDOW_ENDS[windowDays] || { live: 14.8, bot: 18.3 };

      if (scope === 'symphony') {
        // Per-symphony — reuse Paragons detail data when 60d/90d, else synthesize
        // a per-symphony walk seeded by symphony id length so each one is unique.
        if (windowDays === 60 || windowDays === 90) {
          return {
            dates: data.detail.perf.hist_dates,
            live:  data.detail.perf.held,
            bot:   data.detail.perf.bot,
            metrics_live: { total_return: -10.42, annualized_return: -41.2, sharpe: -0.62, sortino: -0.81, max_drawdown: -23.18, calmar: -1.78, win_rate: 38.5 },
            metrics_bot:  { total_return:  -6.01, annualized_return: -24.6, sharpe: -0.38, sortino: -0.52, max_drawdown: -16.55, calmar: -1.49, win_rate: 41.2 },
            obs: 60,
          };
        }
        const symSeed = symId.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
        const liveSym = synthSeries(dayCount, ends.live * 0.4, symSeed);
        const botSym  = synthSeries(dayCount, ends.bot * 0.5, symSeed + 137);
        return {
          dates: synthDates(dayCount),
          live: liveSym,
          bot:  botSym,
          metrics_live: scaleMetrics(perf.live_metrics, dayCount / 60),
          metrics_bot:  scaleMetrics(perf.shadow_metrics, dayCount / 60),
          obs: dayCount,
        };
      }

      // Aggregate — use the 60d hard-coded series for 60d, synth for others
      if (windowDays === 60) {
        return {
          dates: perf.dates,
          live:  perf.live_returns,
          bot:   perf.shadow_returns,
          metrics_live: perf.live_metrics,
          metrics_bot:  perf.shadow_metrics,
          obs: perf.observation_count,
        };
      }
      const dates = synthDates(dayCount);
      return {
        dates,
        live: synthSeries(dayCount, ends.live, 42),
        bot:  synthSeries(dayCount, ends.bot, 271),
        metrics_live: scaleMetrics(perf.live_metrics, dayCount / 60),
        metrics_bot:  scaleMetrics(perf.shadow_metrics, dayCount / 60),
        obs: dayCount,
      };
    }, [scope, symId, windowDays]);

    const alphaCum = series.bot[series.bot.length - 1] - series.live[series.live.length - 1];

    const windowLabel = (WINDOWS.find(w => w.value === windowDays) || {}).label || `${windowDays}d`;

    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'var(--studio-sans, Manrope), ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <TopBar data={data} p={p} route="performance" onNavigate={onNavigate} onForceRun={onForceRun} />
        <PageHeader
          title="Performance"
          subtitle="Live (if held) vs AlphaBot-exited (shadow) · post-mortem snapshots"
          p={p}
          right={
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <SegControl
                value={scope}
                options={SCOPES}
                onChange={setScope}
                p={p}
              />
              {scope === 'symphony' && (
                <window.Select
                  value={symId}
                  options={data.symphonies.map(s => ({ value: s.id, label: s.name.length > 36 ? s.name.slice(0, 36) + '…' : s.name }))}
                  onChange={setSymId}
                  p={p}
                  minWidth={280}
                />
              )}
              <SegControl
                value={windowDays}
                options={WINDOWS}
                onChange={setWindowDays}
                p={p}
              />
            </div>
          }
        />

        <div style={{ flex: 1, overflow: 'auto' }}>
          <Headline series={series} alphaCum={alphaCum} windowLabel={windowLabel} p={p} t={t} />
          <ChartBlock series={series} p={p} t={t} />
          <MetricsTable series={series} p={p} t={t} />
        </div>
      </div>
    );
  }

  // Scale Sharpe/Sortino/MaxDD etc. by sqrt(time) for a roughly realistic look
  // when synthesizing longer windows. Mock only — real /api/performance does
  // this properly via quantstats.
  function scaleMetrics(base, factor) {
    return {
      total_return:       base.total_return * factor,
      annualized_return:  base.annualized_return,
      sharpe:             base.sharpe,
      sortino:            base.sortino,
      max_drawdown:       base.max_drawdown * Math.min(1, Math.sqrt(factor)),
      calmar:             base.calmar,
      win_rate:           base.win_rate,
    };
  }

  // ── Headline strip — big α + observation count ────────────────────────────
  function Headline({ series, alphaCum, windowLabel, p, t }) {
    const digits = t.numFormat === 'compact' ? 1 : 2;
    return (
      <div style={{
        display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr 1fr',
        gap: 1, background: p.rule,
        borderBottom: `1px solid ${p.rule}`,
      }}>
        <Stat
          k="Cumulative guard α"
          big
          value={`${alphaCum >= 0 ? '+' : ''}${alphaCum.toFixed(digits)}%`}
          color={alphaCum >= 0 ? p.pos : p.neg}
          sub={`over the ${windowLabel} window · bot ${alphaCum >= 0 ? 'ahead of' : 'behind'} if-held`}
          p={p}
        />
        <Stat
          k="Bot total return"
          value={`+${series.metrics_bot.total_return.toFixed(digits)}%`}
          color={p.pos}
          sub="exit-on-signal shadow"
          p={p}
        />
        <Stat
          k="If-held total return"
          value={`${series.metrics_live.total_return >= 0 ? '+' : ''}${series.metrics_live.total_return.toFixed(digits)}%`}
          color={series.metrics_live.total_return >= 0 ? p.posDim : p.neg}
          sub="hold-through baseline"
          p={p}
        />
        <Stat
          k="Observations"
          value={series.obs}
          sub="trading days in window"
          p={p}
        />
      </div>
    );
  }

  function Stat({ k, value, color, sub, p, big }) {
    return (
      <div style={{ padding: '20px 24px', background: p.paper }}>
        <div style={{
          fontSize: 10.5, fontWeight: 700, color: p.inkDim,
          letterSpacing: '0.12em', textTransform: 'uppercase',
        }}>
          {k}
        </div>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontWeight: 700, fontSize: big ? 44 : 28,
          color: color || p.ink, letterSpacing: '-0.025em',
          fontVariantNumeric: 'tabular-nums', lineHeight: 1.05,
          marginTop: 6,
        }}>
          {value}
        </div>
        {sub && (
          <div style={{
            marginTop: 5, fontSize: 11.5, color: p.inkFaint, letterSpacing: '0.02em',
          }}>
            {sub}
          </div>
        )}
      </div>
    );
  }

  // ── Chart block ───────────────────────────────────────────────────────────
  function ChartBlock({ series, p, t }) {
    const w = 1280, h = 360;
    const pad = { l: 60, r: 60, t: 24, b: 36 };
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;

    const all = [...series.live, ...series.bot, 0];
    const lo = Math.min(...all) - 1;
    const hi = Math.max(...all) + 1;
    const span = (hi - lo) || 1;
    const n = series.live.length;
    const x = (i) => pad.l + (i / (n - 1)) * innerW;
    const y = (v) => pad.t + innerH - ((v - lo) / span) * innerH;

    const livePath = series.live.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const botPath = series.bot.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const botArea = `${botPath} L${x(n-1)},${y(lo)} L${x(0)},${y(lo)} Z`;
    // Divergence fill between the two lines
    const divArea = `${botPath} ${series.live.map((v, i) => `L${x(n-1-i).toFixed(1)},${y(series.live[n-1-i]).toFixed(1)}`).join(' ')} Z`;

    // Y gridlines at 0, 5, 10, 15, 20…
    const gridStep = hi - lo > 30 ? 10 : hi - lo > 15 ? 5 : hi - lo > 7 ? 2 : 1;
    const gridVals = [];
    for (let g = Math.ceil(lo / gridStep) * gridStep; g <= Math.floor(hi / gridStep) * gridStep; g += gridStep) {
      gridVals.push(g);
    }

    // Date ticks — roughly every 1/6 of the window
    const tickN = Math.min(6, n);
    const tickIdxs = Array.from({ length: tickN }, (_, i) => Math.floor(i * (n - 1) / (tickN - 1)));

    return (
      <div style={{ padding: '22px 28px 14px', background: p.bg }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
          marginBottom: 14,
        }}>
          <div style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: 17, fontWeight: 700, color: p.ink, letterSpacing: '-0.01em',
          }}>
            Cumulative returns
          </div>
          <div style={{
            display: 'flex', gap: 18, fontSize: 11.5,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', color: p.inkDim,
            alignItems: 'center',
          }}>
            <Legend color={p.accent} label="AlphaBot-exited (shadow)" />
            <Legend color={p.inkDim} label="Live (if held)" dashed />
            <span style={{
              padding: '3px 8px',
              background: p.chipBg, border: `1px solid ${p.rule}`,
              borderRadius: 999, fontSize: 10.5,
              color: p.inkDim,
            }}>
              divergence shaded
            </span>
          </div>
        </div>

        <div style={{
          background: p.paper, border: `1px solid ${p.rule}`,
          padding: 0, overflow: 'hidden',
        }}>
          <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" style={{ display: 'block' }}>
            <defs>
              <linearGradient id="perf-bot-grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={p.accent} stopOpacity="0.10" />
                <stop offset="1" stopColor={p.accent} stopOpacity="0" />
              </linearGradient>
              <linearGradient id="perf-div-grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={p.pos} stopOpacity="0.18" />
                <stop offset="1" stopColor={p.pos} stopOpacity="0.05" />
              </linearGradient>
            </defs>

            {/* Grid */}
            {gridVals.map(g => (
              <g key={g}>
                <line x1={pad.l} x2={w - pad.r} y1={y(g)} y2={y(g)} stroke={p.rule} strokeWidth={1} opacity={g === 0 ? 0.8 : 0.4} />
                <text x={pad.l - 8} y={y(g) + 3} fill={p.inkFaint} fontSize="10" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="500" textAnchor="end" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {g > 0 ? '+' : ''}{g}%
                </text>
              </g>
            ))}

            {/* Divergence fill (between bot and live) */}
            <path d={divArea} fill="url(#perf-div-grad)" />

            {/* Live line (dashed) */}
            <path d={livePath} fill="none" stroke={p.inkDim} strokeWidth={1.8} strokeDasharray="4,3" />

            {/* Bot line (primary) */}
            <path d={botPath} fill="none" stroke={p.accent} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" />

            {/* End markers + labels */}
            <circle cx={x(n - 1)} cy={y(series.bot[n - 1])} r={4} fill={p.accent} />
            <circle cx={x(n - 1)} cy={y(series.live[n - 1])} r={3} fill={p.paper} stroke={p.inkDim} strokeWidth={1.5} />

            <rect x={x(n - 1) + 6} y={y(series.bot[n - 1]) - 9} width={62} height={18} rx={2} fill={p.accent} />
            <text x={x(n - 1) + 37} y={y(series.bot[n - 1]) + 4} fontSize="11" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="700" fill="#fff" textAnchor="middle" style={{ fontVariantNumeric: 'tabular-nums' }}>
              +{series.bot[n - 1].toFixed(1)}%
            </text>
            <rect x={x(n - 1) + 6} y={y(series.live[n - 1]) - 9} width={62} height={18} rx={2} fill={p.paper} stroke={p.rule} strokeWidth={1} />
            <text x={x(n - 1) + 37} y={y(series.live[n - 1]) + 4} fontSize="11" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="600" fill={p.inkDim} textAnchor="middle" style={{ fontVariantNumeric: 'tabular-nums' }}>
              +{series.live[n - 1].toFixed(1)}%
            </text>

            {/* Date ticks */}
            {tickIdxs.map((i, k) => (
              <text key={k} x={x(i)} y={h - 12} fill={p.inkFaint} fontSize="10" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="500" textAnchor="middle" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {series.dates[i]}
              </text>
            ))}
          </svg>
        </div>
      </div>
    );
  }

  function Legend({ color, label, dashed }) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
        <svg width={24} height={6}>
          <line x1={0} y1={3} x2={24} y2={3} stroke={color} strokeWidth={2}
            strokeDasharray={dashed ? '4,3' : 'none'} />
        </svg>
        {label}
      </span>
    );
  }

  // ── Metrics table ─────────────────────────────────────────────────────────
  function MetricsTable({ series, p, t }) {
    const digits = t.numFormat === 'compact' ? 1 : 2;
    return (
      <div style={{ padding: '14px 28px 28px', background: p.bg }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 17, fontWeight: 700, color: p.ink, letterSpacing: '-0.01em',
          marginBottom: 14,
        }}>
          Risk metrics
        </div>
        <div style={{
          background: p.paper, border: `1px solid ${p.rule}`, overflow: 'hidden',
        }}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1.4fr 1fr 1fr 1fr',
            padding: '12px 18px',
            background: p.bg,
            borderBottom: `1px solid ${p.rule}`,
            fontSize: 10, fontWeight: 700, color: p.inkDim,
            letterSpacing: '0.12em', textTransform: 'uppercase',
          }}>
            <span>Metric</span>
            <span style={{ textAlign: 'right' }}>Live · if held</span>
            <span style={{ textAlign: 'right' }}>Bot · alphabot-exited</span>
            <span style={{ textAlign: 'right' }}>Delta</span>
          </div>
          {METRICS.map((m, i) => {
            const live = series.metrics_live[m.key];
            const bot = series.metrics_bot[m.key];
            const d = m.digits ?? digits;
            const delta = bot - live;
            const isGood = m.betterHigh ? delta >= 0 : delta <= 0;
            const fmt = v => v == null ? '—' : `${v > 0 && m.suffix === '%' ? '+' : ''}${v.toFixed(d)}${m.suffix}`;
            return (
              <div key={m.key} style={{
                display: 'grid',
                gridTemplateColumns: '1.4fr 1fr 1fr 1fr',
                padding: m.primary ? '16px 18px' : '12px 18px',
                borderBottom: i < METRICS.length - 1 ? `1px solid ${p.rule}` : 'none',
                alignItems: 'baseline',
                background: m.primary ? p.paper : 'transparent',
              }}>
                <span style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: m.primary ? 14 : 13, fontWeight: m.primary ? 700 : 500,
                  color: p.ink,
                }}>
                  {m.label}
                </span>
                <span style={{
                  textAlign: 'right',
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: m.primary ? 16 : 13.5, fontWeight: m.primary ? 600 : 500,
                  color: live != null && live >= 0 ? p.ink : p.neg,
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {fmt(live)}
                </span>
                <span style={{
                  textAlign: 'right',
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: m.primary ? 16 : 13.5, fontWeight: m.primary ? 700 : 600,
                  color: bot != null && bot >= 0 ? p.ink : p.neg,
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {fmt(bot)}
                </span>
                <span style={{
                  textAlign: 'right',
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: m.primary ? 16 : 13.5, fontWeight: m.primary ? 700 : 600,
                  color: isGood ? p.pos : p.neg,
                  fontVariantNumeric: 'tabular-nums',
                  display: 'inline-flex', alignItems: 'baseline', justifyContent: 'flex-end', gap: 4,
                }}>
                  <span style={{ fontSize: m.primary ? 12 : 10, fontWeight: 700 }}>
                    {isGood ? '↑' : '↓'}
                  </span>
                  {delta > 0 ? '+' : ''}{delta.toFixed(d)}{m.suffix}
                </span>
              </div>
            );
          })}
        </div>
        <div style={{
          marginTop: 12, fontSize: 11, color: p.inkFaint, letterSpacing: '0.02em',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
        }}>
          Computed via quantstats over the selected window. Δ↑ green = bot beat held on this metric.
          Less-negative max drawdown counts as a win.
        </div>
      </div>
    );
  }

  window.Performance = Performance;
})();
