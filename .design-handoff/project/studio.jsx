// Direction D — Studio
// Cockpit's instrument-grid layout × Bureau's editorial typography & restraint.
// Headline = Guard Alpha. Each row of the hero spells out Bot vs If-Held.
// Symphony cards are clickable; opening one triggers props.onOpenDetail.

(function () {
  const { useMemo, useState } = React;

  // ── Palette ────────────────────────────────────────────────────────────────
  function palette(theme, accent) {
    if (theme === 'dark') {
      return {
        bg: '#13100c',
        paper: '#19150f',
        paperHi: '#1f1a13',
        rule: 'rgba(238,228,206,0.10)',
        ruleHi: 'rgba(238,228,206,0.22)',
        ink: '#eee4ce',
        inkDim: 'rgba(238,228,206,0.66)',
        inkFaint: 'rgba(238,228,206,0.40)',
        chipBg: 'rgba(238,228,206,0.05)',
        accent,
        pos: '#74c98a', posDim: 'rgba(116,201,138,0.55)',
        neg: '#e07061', negDim: 'rgba(224,112,97,0.55)',
        warn: '#e3a64a', plum: '#b58cc8', cyan: '#6fb6c8',
      };
    }
    return {
      bg: '#f4efe2',
      paper: '#faf6ea',
      paperHi: '#ffffff',
      rule: 'rgba(20,18,12,0.10)',
      ruleHi: 'rgba(20,18,12,0.22)',
      ink: '#15120c',
      inkDim: 'rgba(21,18,12,0.62)',
      inkFaint: 'rgba(21,18,12,0.36)',
      chipBg: 'rgba(21,18,12,0.04)',
      accent,
      pos: '#1f7a4d', posDim: 'rgba(31,122,77,0.55)',
      neg: '#b43a2a', negDim: 'rgba(180,58,42,0.55)',
      warn: '#b07419', plum: '#6a3f8a', cyan: '#1a6e88',
    };
  }
  window.studioPalette = palette;

  function statusInfo(sym, p) {
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
  }
  window.studioStatusInfo = statusInfo;

  // ── Top bar ────────────────────────────────────────────────────────────────
  function TopBar({ data, p }) {
    const m = data.meta;
    const isLive = m.mode === 'LIVE';
    return (
      <header style={{
        display: 'flex', alignItems: 'center', gap: 20,
        padding: '14px 28px',
        borderBottom: `1px solid ${p.rule}`,
        background: p.paper,
      }}>
        {/* Wordmark — editorial serif */}
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontWeight: 500, fontSize: 22, letterSpacing: '-0.02em',
          color: p.ink, display: 'flex', alignItems: 'baseline', gap: 8,
        }}>
          AlphaBot
          <span style={{ color: p.accent, fontStyle: 'italic', fontSize: 14 }}>guard</span>
        </div>

        <nav style={{ display: 'flex', gap: 2, marginLeft: 12 }}>
          {[
            ['Dashboard', true],
            ['Performance', false],
            ['Advisor', false],
            ['History', false],
            ['Settings', false],
          ].map(([n, active]) => (
            <button key={n} style={{
              padding: '6px 11px', border: 'none',
              background: 'transparent',
              color: active ? p.ink : p.inkDim,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12.5,
              fontWeight: active ? 600 : 500, letterSpacing: '-0.005em',
              cursor: 'pointer', position: 'relative',
            }}>
              {n}
              {active && (
                <span style={{
                  position: 'absolute', left: 8, right: 8, bottom: -15, height: 2,
                  background: p.accent,
                }} />
              )}
            </button>
          ))}
        </nav>

        <div style={{ flex: 1 }} />

        {/* System dot + next run */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 7,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5,
          color: p.inkDim, letterSpacing: '0.04em',
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: 6,
            background: m.system_online ? p.pos : p.neg,
            boxShadow: m.system_online ? `0 0 0 3px ${p.pos}22` : 'none',
          }} />
          ONLINE · NEXT {m.next_run}
        </div>

        {/* Account chip */}
        <button style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 8px 6px 10px',
          background: p.chipBg, border: `1px solid ${p.rule}`,
          borderRadius: 4, cursor: 'pointer',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12, color: p.ink,
        }}>
          {m.account.label}
          <span style={{
            color: p.inkFaint, fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10,
          }}>
            {m.account.uuid_short}
          </span>
          <span style={{ color: p.inkFaint, fontSize: 10 }}>▾</span>
        </button>

        {/* Mode pill */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 7,
          padding: '6px 10px',
          background: isLive ? `${p.neg}1a` : 'transparent',
          border: `1px solid ${isLive ? p.neg : p.ruleHi}`,
          borderRadius: 4,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11, fontWeight: 600,
          letterSpacing: '0.1em', color: isLive ? p.neg : p.ink,
        }}>
          <span style={{
            width: 5, height: 5, borderRadius: 5,
            background: isLive ? p.neg : p.inkDim,
          }} />
          {m.mode}
        </div>

        <button style={{
          padding: '7px 14px',
          background: p.accent, color: '#fff',
          border: 'none', borderRadius: 4, cursor: 'pointer',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12, fontWeight: 600,
          letterSpacing: '0.02em',
        }}>
          Force run now
        </button>
      </header>
    );
  }

  // ── Hero · portfolio with Bot-vs-Held ─────────────────────────────────────
  function Hero({ data, p, t }) {
    const m = data.meta;
    const port = m.portfolio;
    const totalAlpha = data.symphonies
      .filter(s => s.guard_alpha != null)
      .reduce((a, s) => a + s.guard_alpha, 0);

    const [windowDays, setWindowDays] = useState(60);
    const compact = t.numFormat === 'compact';

    // Realistic-feeling cumulative end values per window (bot ahead of held).
    const ENDS = {
      30:   { live: 8.4,   bot: 9.6   },
      60:   { live: 14.8,  bot: 18.3  },
      90:   { live: 21.2,  bot: 25.6  },
      125:  { live: 28.4,  bot: 33.1  },
      'ytd':{ live: 23.5,  bot: 28.7  },
      252:  { live: 38.6,  bot: 47.4  },
      1260: { live: 142.8, bot: 198.6 },
    };
    const ends = ENDS[windowDays] || ENDS[60];
    const dayCount = (window.TIME_WINDOW_DAYS && window.TIME_WINDOW_DAYS[windowDays]) || 60;

    let heroDates, heroBot, heroHeld;
    if (windowDays === 60) {
      heroDates = port.hist_dates;
      heroBot = port.hist_bot;
      heroHeld = port.hist_held;
    } else {
      heroBot  = window.synthSeries(dayCount, ends.bot, 271);
      heroHeld = window.synthSeries(dayCount, ends.live, 42);
      const fullDates = window.synthDates(dayCount);
      const tickCount = 7;
      heroDates = Array.from({ length: tickCount }, (_, i) => fullDates[Math.floor(i * (dayCount - 1) / (tickCount - 1))]);
    }
    const crAlpha = heroBot[heroBot.length - 1] - heroHeld[heroHeld.length - 1];
    const tcAlpha = (port.tc ?? 0) - (port.tc_if_held ?? 0);
    const mddAlpha = (port.mdd_if_held ?? 0) - (port.mdd ?? 0);

    const windowLabel = (window.TIME_WINDOWS.find(w => w.value === windowDays) || {}).label || '60d';

    return (
      <section style={{
        display: 'grid', gridTemplateColumns: '1.15fr 1fr',
        gap: 1, background: p.rule, // hairline gap
        borderBottom: `1px solid ${p.rule}`,
      }}>
        {/* Left half — Guard Alpha headline + cum return chart */}
        <div style={{
          padding: '22px 28px 24px', background: p.paper,
          display: 'flex', flexDirection: 'column', gap: 14,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <div style={{
                fontSize: 10.5, color: p.inkDim, letterSpacing: '0.14em',
                textTransform: 'uppercase', fontWeight: 600,
                fontFamily: 'var(--studio-sans, Manrope), sans-serif', marginBottom: 6,
              }}>
                Guard alpha · {windowLabel}
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
                <span style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontWeight: 700, fontSize: 56, lineHeight: 0.95,
                  color: crAlpha >= 0 ? p.pos : p.neg, letterSpacing: '-0.025em',
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {crAlpha >= 0 ? '+' : ''}{crAlpha.toFixed(2)}%
                </span>
                <span style={{
                  fontSize: 12, color: p.inkDim, lineHeight: 1.3,
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                }}>
                  AlphaBot over <br />if-held baseline
                </span>
              </div>
            </div>
            <window.SegControl
              value={windowDays}
              options={window.TIME_WINDOWS}
              onChange={setWindowDays}
              p={p}
            />
          </div>

          {/* Cumulative chart — Bot vs Held */}
          <CumChart
            dates={heroDates}
            bot={heroBot}
            held={heroHeld}
            p={p}
          />

          <div style={{
            display: 'flex', gap: 14, fontSize: 11.5,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', color: p.inkDim,
            paddingTop: 6, borderTop: `1px solid ${p.rule}`,
          }}>
            <Legend color={p.accent} label="AlphaBot (dry-run)" />
            <Legend color={p.inkFaint} label="If held" dashed />
            <span style={{ marginLeft: 'auto', fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5 }}>
              data as of {m.data_as_of}
            </span>
          </div>
        </div>

        {/* Right half — 3 vs-bars */}
        <div style={{
          padding: '22px 28px 24px', background: p.paper,
          display: 'flex', flexDirection: 'column', gap: 14,
        }}>
          <div style={{
            fontSize: 10.5, color: p.inkDim, letterSpacing: '0.14em',
            textTransform: 'uppercase', fontWeight: 600,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          }}>
            Bot vs if-held
          </div>
          <VsRow
            label="Today" bot={port.tc} held={port.tc_if_held}
            alpha={tcAlpha} unit="%" p={p} compact={compact}
          />
          <VsRow
            label="Cumulative" bot={port.cr} held={port.cr_if_held}
            alpha={crAlpha} unit="%" p={p} compact={compact}
          />
          <VsRow
            label="Max drawdown" bot={port.mdd} held={port.mdd_if_held}
            alpha={mddAlpha} unit="%" p={p} compact={compact} invert
          />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            paddingTop: 14, marginTop: 4, borderTop: `1px solid ${p.rule}`,
            gap: 12,
          }}>
            <MiniStat label="Tracked" value={m.tracked} p={p} />
            <MiniStat label="Armed today" value={m.armed} color={m.armed ? p.warn : null} p={p} />
            <MiniStat label="Triggered" value={m.triggered} color={m.triggered ? p.neg : null} p={p} />
          </div>
        </div>
      </section>
    );
  }

  function Legend({ color, label, dashed }) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <svg width={22} height={6}>
          <line
            x1={0} y1={3} x2={22} y2={3}
            stroke={color} strokeWidth={2}
            strokeDasharray={dashed ? '4,3' : 'none'}
          />
        </svg>
        {label}
      </span>
    );
  }

  function VsRow({ label, bot, held, alpha, unit, p, compact, invert }) {
    const digits = compact ? 1 : 2;
    const max = Math.max(Math.abs(bot || 0), Math.abs(held || 0)) || 1;
    const botFrac = Math.abs(bot || 0) / max;
    const heldFrac = Math.abs(held || 0) / max;
    const alphaGood = invert ? alpha >= 0 : alpha >= 0;
    return (
      <div>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
          marginBottom: 6,
        }}>
          <span style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12, color: p.ink, fontWeight: 500,
          }}>{label}</span>
          <span style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5,
            color: alphaGood ? p.pos : p.neg, fontVariantNumeric: 'tabular-nums',
            fontWeight: 600,
          }}>
            {alpha >= 0 ? '+' : ''}{alpha.toFixed(digits)}{unit} α
          </span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <VsBar
            sub="Bot"
            value={bot} unit={unit} digits={digits}
            frac={botFrac} color={p.accent} p={p}
          />
          <VsBar
            sub="If held"
            value={held} unit={unit} digits={digits}
            frac={heldFrac} color={p.inkFaint} p={p}
          />
        </div>
      </div>
    );
  }

  function VsBar({ sub, value, unit, digits, frac, color, p }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 46, fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 10, color: p.inkFaint, letterSpacing: '0.06em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>{sub}</span>
        <div style={{ flex: 1, height: 5, background: p.chipBg, borderRadius: 0, overflow: 'hidden' }}>
          <div style={{
            height: '100%', width: `${frac * 100}%`,
            background: color,
            transition: 'width .3s ease-out',
          }} />
        </div>
        <span style={{
          width: 64, textAlign: 'right',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12,
          color: p.ink, fontVariantNumeric: 'tabular-nums',
        }}>
          {value != null ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}${unit}` : '—'}
        </span>
      </div>
    );
  }

  function MiniStat({ label, value, color, p }) {
    return (
      <div>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 9.5, color: p.inkDim,
          letterSpacing: '0.1em', textTransform: 'uppercase', fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          {color && <span style={{ width: 5, height: 5, borderRadius: 5, background: color }} />}
          {label}
        </div>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontWeight: 500, fontSize: 28,
          color: color || p.ink, letterSpacing: '-0.02em', marginTop: 1,
          fontVariantNumeric: 'tabular-nums', lineHeight: 1,
        }}>
          {value}
        </div>
      </div>
    );
  }

  // ── Cumulative chart ───────────────────────────────────────────────────────
  function CumChart({ dates, bot, held, p }) {
    const width = 600, height = 120;
    const lo = Math.min(...bot, ...held, 0);
    const hi = Math.max(...bot, ...held);
    const span = (hi - lo) || 1;
    const x = (i) => (i / (bot.length - 1)) * width;
    const y = (v) => height - ((v - lo) / span) * height;

    const botPath = bot.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const heldPath = held.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const botArea = `${botPath} L${width},${height} L0,${height} Z`;
    const lastBotY = y(bot[bot.length - 1]);
    const lastHeldY = y(held[held.length - 1]);

    return (
      <div>
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}>
          <defs>
            <linearGradient id="studio-hero-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={p.accent} stopOpacity="0.18" />
              <stop offset="1" stopColor={p.accent} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={botArea} fill="url(#studio-hero-grad)" />
          <path d={heldPath} fill="none" stroke={p.inkFaint} strokeWidth={1.5} strokeDasharray="4,3" />
          <path d={botPath} fill="none" stroke={p.accent} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
          <circle cx={width} cy={lastBotY} r={3} fill={p.accent} />
          <circle cx={width} cy={lastHeldY} r={2.5} fill={p.inkDim} />
        </svg>
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 9.5,
          color: p.inkFaint, letterSpacing: '0.04em', marginTop: 4,
        }}>
          {dates.map((d, i) => <span key={i}>{d}</span>)}
        </div>
      </div>
    );
  }

  // ── Status strip + actions ────────────────────────────────────────────────
  function StatusStrip({ data, p }) {
    const m = data.meta;
    const tt = m.triggers_today;
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 18,
        padding: '10px 28px',
        background: p.bg,
        borderBottom: `1px solid ${p.rule}`,
        fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5,
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          color: p.inkDim, letterSpacing: '0.04em',
        }}>
          <span style={{ width: 6, height: 6, borderRadius: 6, background: p.inkFaint }} />
          {m.market_state_label}
        </div>
        <div style={{ flex: 1 }} />
        <span style={{
          fontSize: 9.5, color: p.inkFaint, letterSpacing: '0.14em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>
          Today's exits
        </span>
        <Chip label="Trailing stop" count={tt.trailing_stop} color={p.neg} p={p} />
        <Chip label="Take-profit" count={tt.take_profit} color={p.plum} p={p} />
        <Chip label="VWAP" count={0} color={p.cyan} p={p} muted />
      </div>
    );
  }

  function Chip({ label, count, color, p, muted }) {
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 7,
        padding: '4px 10px',
        border: `1px solid ${muted ? p.rule : color + '55'}`,
        background: muted ? 'transparent' : color + '0d',
        borderRadius: 999,
        fontSize: 11, color: muted ? p.inkDim : p.ink,
      }}>
        <span style={{
          width: 5, height: 5, borderRadius: 5,
          background: muted ? p.inkFaint : color,
        }} />
        {label}
        <span style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontWeight: 600,
          color: muted ? p.inkFaint : color, marginLeft: 1,
        }}>×{count}</span>
      </span>
    );
  }

  // ── Symphony card ─────────────────────────────────────────────────────────
  function SymCard({ sym, p, t, big, onOpen, onCash }) {
    const [hover, setHover] = useState(false);
    const si = statusInfo(sym, p);
    const ret = sym.triggered ? sym.triggered_at_return : sym.current_return;
    const retColor = ret >= 0 ? p.pos : p.neg;
    const ihRet = sym.current_return;
    const ihColor = ihRet >= 0 ? p.pos : p.neg;
    const alpha = sym.guard_alpha;
    const compact = t.numFormat === 'compact';
    const digits = compact ? 1 : 2;
    const showMath = t.mathOverlays;

    // Was the bot's exit a good call? Only meaningful for triggered symphonies.
    const verdict = sym.triggered && alpha != null
      ? (alpha >= 0
          ? { color: p.pos, glyph: '✓', label: 'Good call', detail: `saved ${alpha >= 0 ? '+' : ''}${alpha.toFixed(digits)}%α` }
          : { color: p.neg, glyph: '⤓', label: 'Early exit', detail: `gave up ${alpha.toFixed(digits)}%α` })
      : null;

    return (
      <div
        role="button"
        tabIndex={0}
        onClick={() => onOpen?.(sym)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen?.(sym); } }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          textAlign: 'left', cursor: 'pointer',
          background: p.paper,
          border: `1px solid ${si.kind !== 'standby' ? si.color + '40' : p.rule}`,
          padding: big ? 18 : 14,
          display: 'flex', flexDirection: 'column', gap: big ? 10 : 8,
          position: 'relative', overflow: 'hidden',
          boxShadow: hover ? `0 1px 0 ${p.ruleHi}` : 'none',
          transform: hover ? 'translateY(-1px)' : 'none',
          transition: 'transform .12s, box-shadow .12s, border-color .12s',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
        }}
      >
        {si.kind !== 'standby' && (
          <span style={{
            position: 'absolute', top: 0, left: 0, bottom: 0, width: 2,
            background: si.color,
          }} />
        )}

        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{
              fontSize: big ? 13.5 : 12.5, fontWeight: 600, color: p.ink,
              letterSpacing: '-0.005em',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              paddingRight: 6,
            }} title={sym.name}>
              {sym.name}
            </div>
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              marginTop: 5,
              fontSize: 9.5, fontWeight: 700,
              letterSpacing: '0.1em', color: si.color,
            }}>
              <span style={{
                width: 5, height: 5, borderRadius: 5, background: si.color,
                boxShadow: si.kind !== 'standby' ? `0 0 0 3px ${si.color}22` : 'none',
              }} />
              {si.label}
              {sym.triggered && sym.triggered_at_time && (
                <span style={{ color: p.inkFaint, fontWeight: 500, letterSpacing: '0.04em', marginLeft: 2 }}>
                  · {sym.triggered_at_time} ET
                </span>
              )}
              {sym.breakeven_locked && (
                <span style={{ color: p.accent, marginLeft: 2 }} title="Breakeven Lock">◆</span>
              )}
            </div>
            {verdict && (
              <div style={{
                marginTop: 6,
                fontSize: 11, color: verdict.color,
                fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                display: 'inline-flex', alignItems: 'center', gap: 6,
                fontWeight: 500,
              }}>
                <span style={{ fontWeight: 700 }}>{verdict.glyph}</span>
                <span style={{ fontWeight: 600 }}>{verdict.label}</span>
                <span style={{ color: p.inkDim }}>·</span>
                <span style={{ fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5 }}>
                  {verdict.detail}
                </span>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8, flexShrink: 0 }}>
            <CashButton
              p={p} sym={sym}
              big={big}
              onClick={(e) => { e.stopPropagation(); onCash?.(sym); }}
            />
            {showMath && big && (
              <Dial value={sym.mc_prob} p={p} />
            )}
          </div>
        </div>

        {/* BOT vs HELD return — always shown so the comparison is the dominant visual */}
        <div style={{
          display: 'flex', alignItems: 'flex-end', gap: big ? 18 : 14,
          flexWrap: 'wrap',
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 9, fontWeight: 700,
              color: p.inkDim, letterSpacing: '0.12em', textTransform: 'uppercase',
            }}>
              Bot{sym.triggered ? ' · frozen' : ''}
            </span>
            <span style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontWeight: 500,
              fontSize: big ? 32 : 24, color: retColor,
              letterSpacing: '-0.025em', fontVariantNumeric: 'tabular-nums', lineHeight: 1,
            }}>
              {ret >= 0 ? '+' : ''}{ret.toFixed(digits)}%
            </span>
          </div>
          <div style={{
            display: 'flex', flexDirection: 'column', gap: 2,
            paddingLeft: big ? 18 : 14, borderLeft: `1px solid ${p.rule}`,
          }}>
            <span style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 9, fontWeight: 700,
              color: p.inkDim, letterSpacing: '0.12em', textTransform: 'uppercase',
            }}>
              If held{sym.triggered ? ' · live' : ''}
            </span>
            <span style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontWeight: 500,
              fontSize: big ? 22 : 18,
              color: ihColor, opacity: sym.triggered ? 1 : 0.55,
              letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', lineHeight: 1,
            }}>
              {ihRet >= 0 ? '+' : ''}{ihRet.toFixed(digits)}%
            </span>
          </div>
          {!big && showMath && (
            <span style={{
              marginLeft: 'auto',
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5,
              color: p.inkFaint, letterSpacing: '0.04em',
            }}>
              MC {sym.mc_prob != null ? sym.mc_prob.toFixed(1) : '—'}
            </span>
          )}
        </div>

        {/* Tape — bot line solid, held line dashed when they diverge */}
        <div style={{ marginTop: 2, color: p.ink }}>
          {(() => {
            const { bot, held, trigIdx } = botTape(sym);
            if (big) {
              return (
                <SparkArea
                  data={bot}
                  dataHeld={held}
                  color={retColor}
                  heldColor={p.inkDim}
                  width={420} height={54}
                  stop={showMath ? sym.stop_trigger : null}
                  trigIdx={sym.triggered ? trigIdx : null}
                  trigLabel={sym.triggered ? trigShortLabel(sym) : null}
                  p={p}
                />
              );
            }
            return (
              <MicroSpark
                data={bot}
                dataHeld={held}
                color={retColor}
                heldColor={p.inkFaint}
                width={220} height={28}
              />
            );
          })()}
        </div>

        <div style={{
          display: 'grid',
          gridTemplateColumns: big ? 'repeat(4, 1fr)' : 'repeat(3, 1fr)',
          gap: big ? 14 : 10, paddingTop: 10,
          borderTop: `1px solid ${p.rule}`,
        }}>
          <DualStat label="Today" bot={sym.tc_dr} held={sym.tc_ih} p={p} digits={digits} />
          <DualStat label="Cum. return" bot={sym.cr_dr} held={sym.cr_ih} p={p} digits={digits} />
          <DualStat label="Max DD" bot={sym.mdd_dr} held={sym.mdd_ih} p={p} digits={digits} invert />
          {big && (
            <StopPeakCell sym={sym} p={p} digits={digits} />
          )}
        </div>
      </div>
    );
  }

  // Per-metric cell with bot + held + α delta. `invert` flag for metrics where
  // less-negative is better (Max DD): bot beats held when its drawdown is shallower.
  function DualStat({ label, bot, held, p, digits, invert }) {
    const fmt = v => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;
    const delta = (bot != null && held != null)
      ? (invert ? held - bot : bot - held)  // both expressed so positive = bot wins
      : null;
    const deltaColor = delta == null ? p.inkFaint : (delta >= 0 ? p.pos : p.neg);
    return (
      <div style={{ minWidth: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 6,
          marginBottom: 4,
        }}>
          <span style={{
            fontSize: 9, color: p.inkDim, letterSpacing: '0.1em',
            textTransform: 'uppercase', fontWeight: 700,
          }}>
            {label}
          </span>
          {delta != null && (
            <span style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 9.5,
              color: deltaColor, fontWeight: 600,
              fontVariantNumeric: 'tabular-nums',
            }}>
              α {delta >= 0 ? '+' : ''}{delta.toFixed(digits)}
            </span>
          )}
        </div>
        <DualRow tag="bot" value={fmt(bot)} valueColor={bot != null ? (bot >= 0 ? p.pos : p.neg) : p.inkFaint} p={p} bold />
        <DualRow tag="held" value={fmt(held)} valueColor={held != null ? (held >= 0 ? p.pos : p.neg) : p.inkFaint} p={p} />
      </div>
    );
  }

  function DualRow({ tag, value, valueColor, p, bold }) {
    return (
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        marginTop: 1,
      }}>
        <span style={{
          fontSize: 8.5, color: p.inkFaint, letterSpacing: '0.08em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>
          {tag}
        </span>
        <span style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5,
          color: valueColor, fontWeight: bold ? 600 : 500,
          fontVariantNumeric: 'tabular-nums',
        }}>
          {value}
        </span>
      </div>
    );
  }

  // Stop (bot) + Peak (held) — these have no cross-counterpart, so render them
  // as a paired single-line metric instead of stacked dual values.
  function StopPeakCell({ sym, p, digits }) {
    const stopColor = sym.breakeven_locked ? p.accent : p.ink;
    return (
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: 9, color: p.inkDim, letterSpacing: '0.1em',
          textTransform: 'uppercase', fontWeight: 700, marginBottom: 4,
        }}>
          Levels
        </div>
        <DualRow
          tag="stop"
          value={`${sym.breakeven_locked ? '◆ ' : ''}${sym.stop_trigger >= 0 ? '+' : ''}${sym.stop_trigger.toFixed(digits)}%`}
          valueColor={stopColor}
          p={p} bold
        />
        <DualRow
          tag="peak"
          value={`+${sym.shadow_hwm.toFixed(digits)}%`}
          valueColor={sym.shadow_hwm > 0 ? p.pos : p.ink}
          p={p}
        />
      </div>
    );
  }

  // Emergency exit button — always visible on every card, 0 clicks away.
  // Subtle by default, fills danger-red on hover. Stops propagation so clicking
  // it never opens the detail slide-over.
  function CashButton({ p, sym, big, onClick }) {
    const [hover, setHover] = useState(false);
    const disabled = !!sym.triggered;
    return (
      <button
        onClick={onClick}
        disabled={disabled}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        title={disabled ? 'Already exited to cash' : 'Liquidate this symphony to cash now'}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: big ? '5px 10px 5px 9px' : '4px 8px 4px 7px',
          background: disabled ? 'transparent' : (hover ? p.neg : 'transparent'),
          border: `1px solid ${disabled ? p.rule : (hover ? p.neg : p.neg + '55')}`,
          borderRadius: 4,
          color: disabled ? p.inkFaint : (hover ? '#fff' : p.neg),
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: big ? 10.5 : 10,
          fontWeight: 600, letterSpacing: '0.06em',
          textTransform: 'uppercase',
          cursor: disabled ? 'default' : 'pointer',
          transition: 'background .12s, color .12s, border-color .12s',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{
          fontSize: big ? 11 : 10, lineHeight: 1,
          opacity: disabled ? 0.5 : 1,
        }}>{disabled ? '✓' : '↓'}</span>
        {disabled ? 'in cash' : 'Cash now'}
      </button>
    );
  }

  function Stat({ label, value, color, p }) {
    return (
      <div>
        <div style={{
          fontSize: 9, color: p.inkDim, letterSpacing: '0.1em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>
          {label}
        </div>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5,
          color: color || p.ink, fontVariantNumeric: 'tabular-nums', marginTop: 1,
        }}>
          {value}
        </div>
      </div>
    );
  }

  // Dial / Spark helpers
  function Dial({ value, p }) {
    const size = 44, stroke = 4;
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const pct = Math.max(0, Math.min(100, value || 0)) / 100;
    const col = value < 15 ? p.warn : value > 80 ? p.inkDim : p.accent;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
        <div style={{ position: 'relative', width: size, height: size }}>
          <svg width={size} height={size}>
            <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={p.rule} strokeWidth={stroke} />
            <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={col} strokeWidth={stroke}
              strokeDasharray={`${c * pct} ${c}`} strokeLinecap="round"
              transform={`rotate(-90 ${size/2} ${size/2})`} />
          </svg>
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, fontWeight: 600, color: p.ink,
            fontVariantNumeric: 'tabular-nums',
          }}>
            {value != null ? value.toFixed(0) : '—'}
          </div>
        </div>
        <div style={{
          fontSize: 8.5, color: p.inkFaint, letterSpacing: '0.08em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>
          MC
        </div>
      </div>
    );
  }

  function SparkArea({ data, dataHeld, color, heldColor, width, height, stop, p, trigIdx, trigLabel }) {
    if (!data || data.length < 2) return null;
    const heldArr = dataHeld || [];
    const all = [...data, ...heldArr, stop ?? data[0], 0];
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    const span = (hi - lo) || 1;
    const x = (i) => (i / (data.length - 1)) * width;
    const y = (v) => height - ((v - lo) / span) * height;

    const linePath = data.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const areaPath = `${linePath} L${width},${height} L0,${height} Z`;

    // Held divergence is only meaningful AFTER the trigger — that's where bot
    // and held diverge. Drawing it the whole way just overlays the bot line.
    let heldPath = null, heldEndX = null, heldEndY = null;
    const hasMarker = heldArr.length > 0 && trigIdx != null && trigIdx >= 0;
    if (hasMarker) {
      const seg = [];
      for (let i = trigIdx; i < heldArr.length; i++) {
        if (heldArr[i] == null) continue;
        seg.push(`${seg.length === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(heldArr[i]).toFixed(1)}`);
      }
      heldPath = seg.join(' ');
      heldEndX = x(heldArr.length - 1);
      heldEndY = y(heldArr[heldArr.length - 1]);
    }

    const stopY = stop != null ? y(stop) : null;
    const zeroY = y(0);
    const id = `studio-spark-${color.replace(/[^a-z0-9]/gi, '')}-${Math.round(width)}-${Math.round(height)}`;

    // Trigger marker geometry — computed up front, no IIFE.
    const tx = hasMarker ? x(trigIdx) : 0;
    const ty = hasMarker ? y(data[trigIdx]) : 0;
    const labelText = trigLabel || '';
    const labelW = labelText.length * 6.4 + 12;
    const labelLeft = Math.min(tx + 5, width - labelW - 2);

    return (
      <svg width="100%" viewBox={`0 0 ${width} ${height + 18}`} preserveAspectRatio="none" height={height + 18} style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.20" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* shift entire plot down 18px to leave room for the trigger badge label */}
        <g transform="translate(0, 18)">
          {zeroY > 0 && zeroY < height && (
            <line x1={0} x2={width} y1={zeroY} y2={zeroY} stroke={p.rule} strokeWidth={1} />
          )}
          {stopY != null && (
            <line x1={0} x2={width} y1={stopY} y2={stopY} stroke={color} strokeOpacity="0.40" strokeWidth={1} strokeDasharray="4,3" />
          )}
          <path d={areaPath} fill={`url(#${id})`} />

          {/* Trigger breakline — drawn under the lines */}
          {hasMarker && (
            <line x1={tx} x2={tx} y1={0} y2={height} stroke={p.plum} strokeOpacity="0.7" strokeWidth={1.3} strokeDasharray="3,3" />
          )}

          {/* Held divergence (post-trigger only) */}
          {heldPath && (
            <path d={heldPath} fill="none" stroke={heldColor || p.inkDim} strokeWidth={1.8} strokeDasharray="3,3" />
          )}

          {/* Bot line */}
          <path d={linePath} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" />

          {/* Trigger point circle (over the line) */}
          {hasMarker && (
            <circle cx={tx} cy={ty} r={4} fill={p.paper} stroke={p.plum} strokeWidth={2} />
          )}

          {/* End markers */}
          <circle cx={x(data.length - 1)} cy={y(data[data.length - 1])} r={2.8} fill={color} />
          {heldEndX != null && (
            <circle cx={heldEndX} cy={heldEndY} r={2.6} fill={heldColor || p.inkDim} stroke={p.paper} strokeWidth={1} />
          )}
        </g>

        {/* Trigger label sits ABOVE the chart so it never overlaps the data */}
        {hasMarker && labelText && (
          <g>
            <line x1={tx} x2={tx} y1={14} y2={18} stroke={p.plum} strokeOpacity="0.7" strokeWidth={1.3} />
            <rect x={labelLeft} y={1} width={labelW} height={14} rx={2} fill={p.plum} />
            <text
              x={labelLeft + labelW / 2} y={11}
              fontSize="9" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="700"
              fill="#fff" textAnchor="middle" letterSpacing="0.1em"
            >
              {labelText}
            </text>
          </g>
        )}
      </svg>
    );
  }

  function MicroSpark({ data, dataHeld, color, heldColor, width, height }) {
    if (!data || data.length < 2) return null;
    const all = [...data, ...(dataHeld || [])];
    const lo = Math.min(...all), hi = Math.max(...all);
    const span = (hi - lo) || 1;
    const x = (i) => (i / (data.length - 1)) * width;
    const y = (v) => height - ((v - lo) / span) * height;
    const d = data.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const dh = dataHeld
      ? dataHeld.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
      : null;
    return (
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" height={height} style={{ display: 'block' }}>
        {dh && <path d={dh} fill="none" stroke={heldColor} strokeWidth={1} strokeDasharray="2,2" />}
        <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  // Derive the bot's tape from a triggered symphony: same shape until the
  // post-peak crossing of the trigger price, then frozen flat at exit.
  function botTape(sym) {
    if (!sym.triggered) return { bot: sym.sparkline, held: null, trigIdx: -1 };
    const held = sym.sparkline;
    const trig = sym.triggered_at_return;
    let peakIdx = 0;
    for (let i = 1; i < held.length; i++) {
      if (held[i] >= held[peakIdx]) peakIdx = i;
    }
    const bot = [...held];
    let frozen = false, trigIdx = -1;
    for (let i = peakIdx; i < bot.length; i++) {
      if (!frozen && bot[i] <= trig) { frozen = true; trigIdx = i; }
      if (frozen) bot[i] = trig;
    }
    if (trigIdx < 0) trigIdx = peakIdx;
    return { bot, held, trigIdx };
  }
  window.studioBotTape = botTape;

  // Short label for the trigger marker on a card's tape.
  function trigShortLabel(sym) {
    const r = sym.triggered_reason || '';
    if (r.includes('Take-Profit')) return 'TP';
    if (r.includes('VWAP Breakdown')) return 'VWAP';
    if (r.includes('VWAP Bleed')) return 'BLEED';
    if (r.includes('Trailing')) return 'STOP';
    return 'EXIT';
  }

  // ── Grid ──────────────────────────────────────────────────────────────────
  function Grid({ data, p, t, onOpen }) {
    const active = data.symphonies.filter(s => s.triggered || s.armed || s.tp_armed || s.para_armed);
    const standby = data.symphonies.filter(s => !(s.triggered || s.armed || s.tp_armed || s.para_armed));
    const standbyCols = t.density === 'compact' ? 'repeat(4, 1fr)' : t.density === 'roomy' ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)';

    return (
      <div style={{ padding: '6px 28px 26px', flex: 1, overflow: 'auto' }}>
        {active.length > 0 && (
          <React.Fragment>
            <SectionRule label="Active" sub="armed & triggered today" count={active.length} accent={p.warn} p={p} />
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14,
              marginBottom: 22,
            }}>
              {active.map(s => <SymCard key={s.id} sym={s} p={p} t={t} big onOpen={onOpen} />)}
            </div>
          </React.Fragment>
        )}
        <SectionRule label="Standby" sub="watching" count={standby.length} accent={p.inkFaint} p={p} />
        <div style={{
          display: 'grid', gridTemplateColumns: standbyCols, gap: 14,
        }}>
          {standby.map(s => <SymCard key={s.id} sym={s} p={p} t={t} onOpen={onOpen} />)}
        </div>
      </div>
    );
  }

  function SectionRule({ label, sub, count, accent, p }) {
    return (
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 10, margin: '16px 0 12px',
      }}>
        <span style={{ width: 5, height: 5, borderRadius: 5, background: accent, alignSelf: 'center' }} />
        <span style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 16, fontWeight: 500,
          color: p.ink, letterSpacing: '-0.01em',
        }}>{label}</span>
        <span style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11, color: p.inkDim,
        }}>{sub}</span>
        <span style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5,
          color: p.inkFaint,
        }}>{String(count).padStart(2, '0')}</span>
        <div style={{ flex: 1, height: 1, background: p.rule, marginLeft: 4, alignSelf: 'center' }} />
      </div>
    );
  }

  // ── Main ──────────────────────────────────────────────────────────────────
  function Studio({ data, t, onOpenDetail }) {
    const p = palette(t.theme, t.accent);
    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'var(--studio-sans, Manrope), ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <TopBar data={data} p={p} />
        <Hero data={data} p={p} t={t} />
        <StatusStrip data={data} p={p} />
        <Grid data={data} p={p} t={t} onOpen={onOpenDetail} />
      </div>
    );
  }

  window.Studio = Studio;
})();
