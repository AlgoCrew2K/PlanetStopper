// History route — rollup of post-mortem guard alpha over a window.
// Mirrors /api/history/<days> in app.py: total_alpha, total_saved,
// trigger_count, wins, win_rate, by_reason breakdown.

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

  // Trading-day counts and end-state guard α totals per window.
  const WINDOW_META = {
    30:   { days:   30, totalAlpha: 4.2,  saved:  520.40, count: 28, wins: 17, avg: 0.150 },
    60:   { days:   60, totalAlpha: 9.8,  saved: 1248.10, count: 57, wins: 34, avg: 0.172 },
    90:   { days:   90, totalAlpha: 14.62, saved: 1947.21, count: 84, wins: 51, avg: 0.174 },
    125:  { days:  125, totalAlpha: 19.4, saved: 2616.80, count: 118, wins: 71, avg: 0.164 },
    'ytd':{ days:   95, totalAlpha: 15.2, saved: 2046.30, count: 89, wins: 54, avg: 0.171 },
    252:  { days:  252, totalAlpha: 38.7, saved: 5128.40, count: 240, wins: 144, avg: 0.161 },
    1260: { days: 1260, totalAlpha: 187.4, saved: 25104.80, count: 1180, wins: 712, avg: 0.159 },
  };

  function lcg(seed) { let s = seed | 0; return () => { s = (s * 1664525 + 1013904223) | 0; return ((s >>> 0) / 0xFFFFFFFF); }; }
  function synthDaily(days, target, seed) {
    const rnd = lcg(seed);
    const out = [];
    const drift = target / days;
    for (let i = 0; i < days; i++) {
      out.push(drift + (rnd() - 0.5) * 0.6);
    }
    return out;
  }

  function History({ data, t, onNavigate, onForceRun }) {
    const p = window.studioPalette(t.theme, t.accent);
    const baseHist = data.history;
    const [windowDays, setWindowDays] = useState(baseHist.window_days);
    const digits = t.numFormat === 'compact' ? 1 : 2;
    const meta = WINDOW_META[windowDays] || WINDOW_META[90];
    const windowLabel = (WINDOWS.find(w => w.value === windowDays) || {}).label || `${windowDays}d`;

    // Build a window-scoped view of history. By-reason proportions are kept
    // stable and scaled with total_alpha so the breakdown stays plausible.
    const h = useMemo(() => {
      const factor = meta.totalAlpha / baseHist.total_alpha;
      const dailyLen = meta.days;
      const daily = dailyLen === 90 ? baseHist.daily_alpha : synthDaily(dailyLen, meta.totalAlpha, dailyLen * 7);
      const byReason = {};
      for (const [k, v] of Object.entries(baseHist.by_reason)) {
        byReason[k] = {
          alpha:   v.alpha * factor,
          count:   Math.round(v.count * (meta.count / baseHist.trigger_count)),
          wins:    Math.round(v.wins * (meta.wins / baseHist.wins)),
          dollars: v.dollars * factor,
        };
      }
      return {
        window_days: dailyLen,
        total_alpha: meta.totalAlpha,
        total_saved: meta.saved,
        trigger_count: meta.count,
        wins: meta.wins,
        win_rate: (meta.wins / meta.count) * 100,
        avg_guard_alpha: meta.avg,
        by_reason: byReason,
        daily_alpha: daily,
      };
    }, [windowDays]);

    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'var(--studio-sans, Manrope), ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <TopBar data={data} p={p} route="history" onNavigate={onNavigate} onForceRun={onForceRun} />
        <PageHeader
          title="Guard alpha history"
          subtitle="Rollup of post-mortem snapshots · how the bot's exits actually scored vs the if-held series"
          p={p}
          right={<SegControl value={windowDays} options={WINDOWS} onChange={setWindowDays} p={p} />}
        />

        <div style={{ flex: 1, overflow: 'auto' }}>
          <Hero h={h} digits={digits} p={p} windowLabel={windowLabel} />
          <DailyStrip h={h} p={p} windowLabel={windowLabel} />
          <ByReason h={h} digits={digits} p={p} />
          <RecentTriggers data={data} p={p} digits={digits} />
        </div>
      </div>
    );
  }

  function Hero({ h, digits, p, windowLabel }) {
    const sign = (v) => v > 0 ? '+' : '';
    return (
      <div style={{
        display: 'grid', gridTemplateColumns: '1.5fr 1fr 1fr 1fr',
        gap: 1, background: p.rule,
        borderBottom: `1px solid ${p.rule}`,
      }}>
        <Stat
          k="Total guard α"
          big
          value={`${sign(h.total_alpha)}${h.total_alpha.toFixed(digits)}%`}
          color={h.total_alpha >= 0 ? p.pos : p.neg}
          sub={`over ${windowLabel} window · avg ${sign(h.avg_guard_alpha)}${h.avg_guard_alpha.toFixed(3)}% per exit`}
          p={p}
        />
        <Stat
          k="$ saved"
          value={`$${h.total_saved.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          color={p.pos}
          sub="dollar value of α"
          p={p}
        />
        <Stat
          k="Triggers"
          value={h.trigger_count}
          sub={`${h.wins} wins · ${h.trigger_count - h.wins} losses`}
          p={p}
        />
        <Stat
          k="Win rate"
          value={`${h.win_rate.toFixed(1)}%`}
          color={h.win_rate >= 50 ? p.pos : p.neg}
          sub="exits that beat hold"
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
          <div style={{ marginTop: 5, fontSize: 11.5, color: p.inkFaint }}>
            {sub}
          </div>
        )}
      </div>
    );
  }

  // ── Daily strip chart ─────────────────────────────────────────────────────
  function DailyStrip({ h, p, windowLabel }) {
    const w = 1280, ht = 140, pad = { l: 60, r: 60, t: 18, b: 26 };
    const innerW = w - pad.l - pad.r;
    const innerH = ht - pad.t - pad.b;
    const series = h.daily_alpha;
    const n = series.length;
    const lo = Math.min(...series, 0);
    const hi = Math.max(...series, 0);
    const span = (hi - lo) || 1;
    const x = (i) => pad.l + (i / n) * innerW;
    const y = (v) => pad.t + innerH - ((v - lo) / span) * innerH;
    const zeroY = y(0);
    const barW = (innerW / n) - 1;

    return (
      <div style={{ padding: '22px 28px 14px' }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
          marginBottom: 12,
        }}>
          <div style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: 16, fontWeight: 700, color: p.ink, letterSpacing: '-0.01em',
          }}>
            Daily α
          </div>
          <div style={{ display: 'flex', gap: 14, fontSize: 11.5, color: p.inkDim }}>
            <Legend color={p.pos} label="positive α" p={p} />
            <Legend color={p.neg} label="negative α" p={p} />
          </div>
        </div>
        <div style={{ background: p.paper, border: `1px solid ${p.rule}` }}>
          <svg viewBox={`0 0 ${w} ${ht}`} width="100%" height={ht} preserveAspectRatio="none" style={{ display: 'block' }}>
            <line x1={pad.l} x2={w - pad.r} y1={zeroY} y2={zeroY} stroke={p.ruleHi} strokeWidth={1} />
            {series.map((v, i) => {
              const bx = x(i);
              const by = v >= 0 ? y(v) : zeroY;
              const bh = Math.abs(y(v) - zeroY);
              return (
                <rect
                  key={i}
                  x={bx} y={by}
                  width={Math.max(barW, 1)} height={Math.max(bh, 0.5)}
                  fill={v >= 0 ? p.pos : p.neg}
                  fillOpacity={v >= 0 ? 0.85 : 0.85}
                />
              );
            })}
            {/* Y-axis labels */}
            {[hi, 0, lo].filter(v => Math.abs(v) > 0.01 || v === 0).map((v, k) => (
              <text key={k} x={pad.l - 8} y={y(v) + 3} fill={p.inkFaint} fontSize="10" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="500" textAnchor="end" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {v > 0 ? '+' : ''}{v.toFixed(2)}%
              </text>
            ))}
            <text x={pad.l} y={ht - 10} fill={p.inkFaint} fontSize="10" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="500" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {windowLabel} ago
            </text>
            <text x={w - pad.r} y={ht - 10} fill={p.inkFaint} fontSize="10" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="500" textAnchor="end" style={{ fontVariantNumeric: 'tabular-nums' }}>
              today
            </text>
          </svg>
        </div>
      </div>
    );
  }

  function Legend({ color, label, p }) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ width: 8, height: 8, background: color, display: 'inline-block' }} />
        {label}
      </span>
    );
  }

  // ── By-reason breakdown ──────────────────────────────────────────────────
  function ByReason({ h, digits, p }) {
    const REASON_META = {
      'Take-Profit':    { color: p.plum,  short: 'TP',    desc: 'Monte Carlo collapse — mean reversion expected.' },
      'Trailing Stop':  { color: p.neg,   short: 'STOP',  desc: 'Vol-scaled trailing stop hit after peak.' },
      'VWAP Breakdown': { color: p.cyan,  short: 'VWAP',  desc: 'Price dropped below VWAP after HWM — System A.' },
      'VWAP Bleed Cut': { color: p.warn,  short: 'BLEED', desc: 'Slow bleed below vol-scaled VWAP floor — System B.' },
    };

    return (
      <div style={{ padding: '14px 28px 14px' }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 16, fontWeight: 700, color: p.ink, letterSpacing: '-0.01em',
          marginBottom: 12,
        }}>
          By exit reason
        </div>
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12,
        }}>
          {Object.entries(h.by_reason).map(([reason, stats]) => {
            const meta = REASON_META[reason] || { color: p.inkDim, short: '—', desc: '' };
            const winRate = stats.count > 0 ? (stats.wins / stats.count) * 100 : 0;
            const avgAlpha = stats.count > 0 ? stats.alpha / stats.count : 0;
            return (
              <div key={reason} style={{
                background: p.paper, border: `1px solid ${p.rule}`,
                padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8,
                position: 'relative', overflow: 'hidden',
              }}>
                <span style={{
                  position: 'absolute', top: 0, left: 0, right: 0, height: 2,
                  background: meta.color,
                }} />
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{
                    fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                    fontSize: 13, fontWeight: 700, color: p.ink,
                  }}>
                    {reason}
                  </span>
                  <span style={{
                    padding: '2px 7px', background: meta.color + '14',
                    border: `1px solid ${meta.color}55`,
                    borderRadius: 999, fontSize: 10, fontWeight: 700, color: meta.color,
                    letterSpacing: '0.08em',
                  }}>
                    {meta.short}
                  </span>
                </div>

                <div style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontWeight: 700, fontSize: 28,
                  color: stats.alpha >= 0 ? p.pos : p.neg,
                  letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums',
                  lineHeight: 1,
                }}>
                  {stats.alpha >= 0 ? '+' : ''}{stats.alpha.toFixed(digits)}%
                </div>
                <div style={{
                  fontSize: 10.5, color: p.inkFaint,
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  marginTop: -4,
                }}>
                  cumulative α · ${stats.dollars.toFixed(2)} saved
                </div>

                <div style={{
                  display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
                  gap: 6, paddingTop: 8, borderTop: `1px solid ${p.rule}`,
                }}>
                  <MiniStat k="Triggers" v={stats.count} p={p} />
                  <MiniStat k="Wins" v={`${stats.wins}/${stats.count}`} color={winRate >= 50 ? p.pos : p.neg} p={p} />
                  <MiniStat k="Win rate" v={`${winRate.toFixed(0)}%`} color={winRate >= 50 ? p.pos : p.neg} p={p} />
                </div>

                <div style={{
                  fontSize: 10.5, color: p.inkDim,
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  letterSpacing: '0.02em',
                }}>
                  {meta.desc}
                </div>
                <div style={{
                  fontSize: 10, color: p.inkFaint,
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  letterSpacing: '0.04em',
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  avg per exit: {avgAlpha >= 0 ? '+' : ''}{avgAlpha.toFixed(3)}%
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function MiniStat({ k, v, color, p }) {
    return (
      <div>
        <div style={{
          fontSize: 9, color: p.inkDim, letterSpacing: '0.1em',
          textTransform: 'uppercase', fontWeight: 700,
        }}>
          {k}
        </div>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 13, fontWeight: 600, color: color || p.ink,
          fontVariantNumeric: 'tabular-nums', marginTop: 1,
        }}>
          {v}
        </div>
      </div>
    );
  }

  // ── Recent triggers list — reuse events array, filter to triggers ────────
  function RecentTriggers({ data, p, digits }) {
    const triggers = data.events.filter(e => e.kind === 'TRIGGER');
    return (
      <div style={{ padding: '14px 28px 28px' }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 16, fontWeight: 700, color: p.ink, letterSpacing: '-0.01em',
          marginBottom: 12,
        }}>
          Today's exits ({triggers.length})
        </div>
        <div style={{
          background: p.paper, border: `1px solid ${p.rule}`,
        }}>
          <div style={{
            display: 'grid', gridTemplateColumns: '110px 1fr 160px 1fr',
            padding: '10px 18px',
            background: p.bg, borderBottom: `1px solid ${p.rule}`,
            fontSize: 10, fontWeight: 700, color: p.inkDim,
            letterSpacing: '0.12em', textTransform: 'uppercase',
          }}>
            <span>Time</span>
            <span>Symphony</span>
            <span>Reason</span>
            <span>Detail</span>
          </div>
          {triggers.map((e, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '110px 1fr 160px 1fr',
              padding: '11px 18px',
              borderBottom: i < triggers.length - 1 ? `1px solid ${p.rule}` : 'none',
              alignItems: 'center',
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              fontSize: 12,
            }}>
              <span style={{
                color: p.inkDim, fontWeight: 500,
                fontVariantNumeric: 'tabular-nums',
              }}>
                {e.ts}
              </span>
              <span style={{ color: p.ink, fontWeight: 600 }}>
                {e.sym}
              </span>
              <span style={{
                color: e.reason === 'Take-Profit' ? p.plum : e.reason === 'Trailing Stop' ? p.neg : p.cyan,
                fontWeight: 700, letterSpacing: '0.04em',
              }}>
                {e.reason}
              </span>
              <span style={{ color: p.inkDim }}>{e.detail}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  window.History = History;
})();
