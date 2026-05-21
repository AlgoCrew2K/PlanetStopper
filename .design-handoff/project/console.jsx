// Direction B — Console
// Dense terminal-inspired ledger. Mono throughout, hairline rules, sharp
// corners. Three-pane layout: status rail / ledger / event stream.
// Refined dark + light "paper-terminal" mode.

(function () {
  const { useMemo } = React;

  function palette(theme, accent) {
    if (theme === 'dark') {
      return {
        bg: '#0c0d0a',
        rail: '#101110',
        rule: 'rgba(225,225,210,0.10)',
        ruleStrong: 'rgba(225,225,210,0.22)',
        ink: '#e8e6d6',
        inkDim: 'rgba(232,230,214,0.62)',
        inkFaint: 'rgba(232,230,214,0.34)',
        chipBg: 'rgba(232,230,214,0.04)',
        accent,
        // semantic
        pos: '#7ad17a',
        posDim: 'rgba(122,209,122,0.6)',
        neg: '#e85a4f',
        warn: '#dcb85a',
        plum: '#c98fda',
        cyan: '#6cc6da',
        magenta: '#d56fb8',
      };
    }
    return {
      bg: '#f5f3eb',
      rail: '#ebe8de',
      rule: 'rgba(20,20,15,0.14)',
      ruleStrong: 'rgba(20,20,15,0.32)',
      ink: '#15140f',
      inkDim: 'rgba(21,20,15,0.62)',
      inkFaint: 'rgba(21,20,15,0.36)',
      chipBg: 'rgba(20,20,15,0.04)',
      accent,
      pos: '#1f7a3d',
      posDim: 'rgba(31,122,61,0.65)',
      neg: '#b4382a',
      warn: '#9a7a1a',
      plum: '#6a3f8a',
      cyan: '#15637a',
      magenta: '#9a3a78',
    };
  }

  function statusInfo(sym, p) {
    if (sym.triggered) {
      const r = sym.triggered_reason || '';
      if (r.includes('Take-Profit')) return { color: p.magenta, label: 'TAKE-PROFIT' };
      if (r.includes('VWAP')) return { color: p.cyan, label: r.toUpperCase() };
      return { color: p.neg, label: 'TRAILING STOP' };
    }
    if (sym.para_armed) return { color: p.cyan, label: 'PARA-ARMED' };
    if (sym.tp_armed) return { color: p.magenta, label: 'TP-ARMED' };
    if (sym.armed) return { color: p.warn, label: 'ARMED' };
    return { color: p.inkFaint, label: 'STANDBY' };
  }

  function eventColor(kind, p) {
    if (kind === 'TRIGGER') return p.neg;
    if (kind === 'ARM') return p.warn;
    if (kind === 'LOCK') return p.accent;
    return p.inkDim;
  }

  // Sharp-pixel sparkline w/ optional stop line
  function MicroChart({ data, color, width = 70, height = 22, stop = null }) {
    if (!data || data.length < 2) return null;
    const lo = Math.min(...data, stop ?? data[0]);
    const hi = Math.max(...data, stop ?? data[0]);
    const span = (hi - lo) || 1;
    const pts = data.map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - lo) / span) * height;
      return [x, y];
    });
    const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    const stopY = stop != null ? height - ((stop - lo) / span) * height : null;
    return (
      <svg width={width} height={height} style={{ display: 'block' }}>
        {stopY != null && (
          <line x1={0} x2={width} y1={stopY} y2={stopY} stroke={color} strokeWidth={1} strokeDasharray="2,2" opacity="0.45" />
        )}
        <path d={d} fill="none" stroke={color} strokeWidth={1.25} shapeRendering="geometricPrecision" />
      </svg>
    );
  }

  // ── Top status bar (terminal breadcrumb) ───────────────────────────────────
  function StatusBar({ data, p }) {
    const m = data.meta;
    const isLive = m.mode === 'LIVE';
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 18px',
        borderBottom: `1px solid ${p.rule}`,
        fontFamily: 'Geist Mono, JetBrains Mono, monospace',
        fontSize: 11, color: p.inkDim, letterSpacing: '0.02em',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          <span style={{
            color: p.accent, fontWeight: 600, marginRight: 12,
            letterSpacing: '0.05em',
          }}>
            ALPHABOT/3.2
          </span>
          <span style={{ color: p.inkFaint }}>::</span>
          <span style={{ margin: '0 8px' }}>accounts</span>
          <span style={{ color: p.inkFaint }}>/</span>
          <span style={{ margin: '0 8px', color: p.ink }}>{m.account.label.toLowerCase().replace(' ', '_')}</span>
          <span style={{ color: p.inkFaint }}>·</span>
          <span style={{ marginLeft: 8 }}>uuid={m.account.uuid_short}…</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{
            padding: '2px 8px',
            background: isLive ? `${p.neg}28` : p.chipBg,
            border: `1px solid ${isLive ? p.neg : p.ruleStrong}`,
            color: isLive ? p.neg : p.ink, fontWeight: 600,
            letterSpacing: '0.1em',
          }}>
            [{m.mode}]
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Blink color={m.system_online ? p.pos : p.neg} />
            <span style={{ color: p.ink }}>online</span>
          </span>
          <span style={{ color: p.inkFaint }}>·</span>
          <span>next_run={m.next_run}</span>
          <span style={{ color: p.inkFaint }}>·</span>
          <span style={{ color: p.ink }}>{m.clock_et.replace(' ET', 'ET').replace(' ', '')}</span>
        </div>
      </div>
    );
  }

  function Blink({ color }) {
    return (
      <span style={{
        width: 7, height: 7, background: color, display: 'inline-block',
        animation: 'alphabot-blink 1.6s steps(2) infinite',
      }} />
    );
  }

  // ── Left rail ──────────────────────────────────────────────────────────────
  function LeftRail({ data, p, t }) {
    const m = data.meta;
    const port = m.portfolio;
    const totalAlpha = data.symphonies
      .filter(s => s.guard_alpha != null)
      .reduce((a, s) => a + s.guard_alpha, 0);
    return (
      <aside style={{
        width: 268, flexShrink: 0,
        background: p.rail,
        borderRight: `1px solid ${p.rule}`,
        display: 'flex', flexDirection: 'column',
        fontFamily: 'Geist Mono, JetBrains Mono, monospace',
        fontSize: 11,
      }}>
        <Section title="// portfolio" p={p}>
          <Row label="account" value={m.account.label} p={p} />
          <Row label="value" value={`$${port.account_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} p={p} />
          <Row label="cash" value={`$${port.available_cash.toFixed(2)}`} p={p} />
          <Row label="today" value={`${port.tc >= 0 ? '+' : ''}${port.tc.toFixed(2)}%`} valueColor={port.tc >= 0 ? p.pos : p.neg} p={p} />
          <Row label="cum.ret" value={`${port.cumulative_return.toFixed(2)}%`} valueColor={p.pos} p={p} />
          <Row label="sharpe" value={port.sharpe.toFixed(2)} p={p} />
          <Row label="max_dd" value={`-${port.max_drawdown.toFixed(2)}%`} valueColor={p.neg} p={p} />
        </Section>

        <Section title="// guard" p={p}>
          <BigStat
            label="alpha vs held"
            value={`${totalAlpha >= 0 ? '+' : ''}${totalAlpha.toFixed(2)}%`}
            color={totalAlpha >= 0 ? p.pos : p.neg}
            p={p}
          />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderTop: `1px solid ${p.rule}` }}>
            <Tile k="tracked" v={m.tracked} p={p} />
            <Tile k="armed" v={m.armed} color={m.armed ? p.warn : null} p={p} />
            <Tile k="trig" v={m.triggered} color={m.triggered ? p.neg : null} p={p} last />
          </div>
        </Section>

        <Section title="// today's exits" p={p}>
          <Row
            label="trailing_stop"
            value={`× ${m.triggers_today.trailing_stop}`}
            valueColor={p.neg}
            p={p}
          />
          <Row
            label="take_profit"
            value={`× ${m.triggers_today.take_profit}`}
            valueColor={p.magenta}
            p={p}
          />
          <Row
            label="vwap_breakdown"
            value="× 0"
            valueColor={p.inkFaint}
            p={p}
          />
          <Row
            label="vwap_bleed"
            value="× 0"
            valueColor={p.inkFaint}
            p={p}
          />
        </Section>

        <Section title="// quick commands" p={p}>
          {[
            ['F', 'force_run'],
            ['E', 'eod_analysis'],
            ['D', 'push_discord'],
            ['H', 'history'],
            ['V', 'edit_vars'],
            ['Q', 'go_to_cash', p.neg],
          ].map(([k, cmd, color]) => (
            <button key={k} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              width: '100%', padding: '6px 14px',
              background: 'transparent', border: 'none',
              borderTop: `1px solid ${p.rule}`,
              fontFamily: 'inherit', fontSize: 11, color: color || p.ink,
              cursor: 'pointer', textAlign: 'left',
            }}>
              <span>{cmd}</span>
              <kbd style={{
                fontFamily: 'inherit', fontSize: 9, padding: '1px 5px',
                background: p.chipBg, border: `1px solid ${p.rule}`, color: p.inkDim,
              }}>{k}</kbd>
            </button>
          ))}
        </Section>

        <div style={{ flex: 1 }} />

        <div style={{
          padding: '10px 14px', borderTop: `1px solid ${p.rule}`,
          fontSize: 10, color: p.inkFaint, letterSpacing: '0.04em',
        }}>
          {m.market_state_label.toLowerCase()}
        </div>
      </aside>
    );
  }

  function Section({ title, p, children }) {
    return (
      <div style={{ borderBottom: `1px solid ${p.rule}` }}>
        <div style={{
          padding: '10px 14px 4px',
          fontSize: 9.5, color: p.inkDim, letterSpacing: '0.08em',
        }}>
          {title}
        </div>
        <div style={{ paddingBottom: 8 }}>{children}</div>
      </div>
    );
  }

  function Row({ label, value, valueColor, p }) {
    return (
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        padding: '3px 14px',
      }}>
        <span style={{ color: p.inkDim }}>{label}</span>
        <span style={{
          color: valueColor || p.ink,
          fontVariantNumeric: 'tabular-nums',
          maxWidth: '60%', overflow: 'hidden', textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>{value}</span>
      </div>
    );
  }

  function BigStat({ label, value, color, p }) {
    return (
      <div style={{ padding: '6px 14px 10px' }}>
        <div style={{ fontSize: 9.5, color: p.inkDim, letterSpacing: '0.06em', marginBottom: 2 }}>{label}</div>
        <div style={{
          fontSize: 22, fontWeight: 500, color, letterSpacing: '-0.02em',
          fontFamily: 'Geist Mono, monospace', fontVariantNumeric: 'tabular-nums',
        }}>
          {value}
        </div>
      </div>
    );
  }

  function Tile({ k, v, color, p, last }) {
    return (
      <div style={{
        padding: '6px 12px',
        borderRight: last ? 'none' : `1px solid ${p.rule}`,
      }}>
        <div style={{ fontSize: 9, color: p.inkDim, letterSpacing: '0.06em' }}>{k}</div>
        <div style={{
          fontSize: 18, fontWeight: 500,
          color: color || p.ink,
          fontFamily: 'Geist Mono, monospace', fontVariantNumeric: 'tabular-nums',
        }}>
          {v}
        </div>
      </div>
    );
  }

  // ── Center ledger ──────────────────────────────────────────────────────────
  function Ledger({ data, p, t }) {
    const dense = t.density === 'compact';
    const roomy = t.density === 'roomy';
    const rowH = dense ? 28 : roomy ? 40 : 34;
    const rowPad = dense ? '4px 12px' : roomy ? '10px 12px' : '7px 12px';
    const showMath = t.mathOverlays;

    return (
      <main style={{
        flex: 1, minWidth: 0,
        display: 'flex', flexDirection: 'column',
        background: p.bg, color: p.ink,
        fontFamily: 'Geist Mono, JetBrains Mono, monospace',
      }}>
        {/* Ledger header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
          padding: '14px 20px 10px',
          borderBottom: `1px solid ${p.rule}`,
        }}>
          <div style={{
            fontSize: 13, fontWeight: 600, letterSpacing: '0.04em',
          }}>
            <span style={{ color: p.accent }}>$</span> SYMPHONY_LEDGER
            <span style={{ color: p.inkFaint, marginLeft: 12, fontWeight: 400 }}>
              -- {data.symphonies.length} rows · sort=mc_prob asc · scope={data.meta.account.label.toLowerCase()}
            </span>
          </div>
          <div style={{ fontSize: 10.5, color: p.inkFaint }}>
            t-1m sync · pid=1421
          </div>
        </div>

        {/* Column headers */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: showMath
            ? '4ch 1fr 12ch 5ch 8ch 8ch 8ch 12ch 11ch'
            : '4ch 1fr 12ch 8ch 8ch 8ch 12ch 11ch',
          padding: '8px 20px', gap: 8,
          fontSize: 9.5, color: p.inkDim, letterSpacing: '0.08em',
          borderBottom: `1px solid ${p.rule}`,
        }}>
          <span>#</span>
          <span>symphony</span>
          <span>status</span>
          {showMath && <span style={{ textAlign: 'right' }}>mc%</span>}
          <span style={{ textAlign: 'right' }}>stop</span>
          <span style={{ textAlign: 'right' }}>return</span>
          <span style={{ textAlign: 'right' }}>peak</span>
          <span style={{ textAlign: 'right' }}>if_held · α</span>
          <span style={{ textAlign: 'center' }}>tape</span>
        </div>

        <div style={{ flex: 1, overflow: 'auto' }}>
          {data.symphonies.map((s, i) => {
            const si = statusInfo(s, p);
            const ret = s.triggered ? s.triggered_at_return : s.current_return;
            const retColor = ret >= 0 ? p.pos : p.neg;
            const ih = s.current_return;
            const ihColor = ih >= 0 ? p.pos : p.neg;
            const alpha = s.guard_alpha;
            const lit = s.triggered || s.armed || s.tp_armed || s.para_armed;
            return (
              <div key={s.id} style={{
                display: 'grid',
                gridTemplateColumns: showMath
                  ? '4ch 1fr 12ch 5ch 8ch 8ch 8ch 12ch 11ch'
                  : '4ch 1fr 12ch 8ch 8ch 8ch 12ch 11ch',
                padding: rowPad, gap: 8,
                fontSize: 12,
                borderBottom: `1px solid ${p.rule}`,
                background: lit ? `${si.color}08` : 'transparent',
                alignItems: 'center', minHeight: rowH,
                fontVariantNumeric: 'tabular-nums',
              }}>
                <span style={{ color: p.inkFaint, fontSize: 10 }}>
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span style={{
                  color: p.ink, whiteSpace: 'nowrap',
                  overflow: 'hidden', textOverflow: 'ellipsis',
                }} title={s.name}>
                  {s.name}
                </span>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  color: si.color, fontSize: 10.5, fontWeight: 500,
                  letterSpacing: '0.05em',
                }}>
                  <span style={{
                    width: 6, height: 6, background: si.color,
                    boxShadow: lit ? `0 0 4px ${si.color}` : 'none',
                  }} />
                  {si.label}
                  {s.triggered && s.triggered_at_time && (
                    <span style={{ color: p.inkFaint, fontSize: 9.5, marginLeft: 4 }}>
                      @{s.triggered_at_time}
                    </span>
                  )}
                </span>
                {showMath && (
                  <span style={{
                    textAlign: 'right',
                    color: s.mc_prob < 15 ? p.warn : s.mc_prob > 80 ? p.inkDim : p.ink,
                  }}>
                    {s.mc_prob != null ? s.mc_prob.toFixed(1) : '—'}
                  </span>
                )}
                <span style={{ textAlign: 'right', color: p.inkDim }}>
                  {s.breakeven_locked && <span style={{ color: p.accent, marginRight: 2 }}>◆</span>}
                  {s.stop_trigger != null ? `${s.stop_trigger >= 0 ? '+' : ''}${s.stop_trigger.toFixed(2)}` : '—'}
                </span>
                <span style={{ textAlign: 'right', color: retColor, fontWeight: 500 }}>
                  {ret != null ? `${ret >= 0 ? '+' : ''}${ret.toFixed(2)}` : '—'}
                </span>
                <span style={{ textAlign: 'right', color: s.shadow_hwm > 0 ? p.posDim : p.inkDim }}>
                  {s.shadow_hwm != null ? `+${s.shadow_hwm.toFixed(2)}` : '—'}
                </span>
                <span style={{ textAlign: 'right' }}>
                  <span style={{ color: ihColor }}>
                    {`${ih >= 0 ? '+' : ''}${ih.toFixed(2)}`}
                  </span>
                  {alpha != null && (
                    <span style={{ color: alpha >= 0 ? p.pos : p.neg, marginLeft: 6 }}>
                      {alpha >= 0 ? '+' : ''}{alpha.toFixed(2)}α
                    </span>
                  )}
                </span>
                <span style={{ display: 'flex', justifyContent: 'center' }}>
                  <MicroChart
                    data={s.sparkline}
                    color={s.triggered ? p.inkDim : (ret >= 0 ? p.pos : p.neg)}
                    width={70}
                    height={18}
                    stop={s.stop_trigger}
                  />
                </span>
              </div>
            );
          })}
        </div>

        {/* Bottom command hint */}
        <div style={{
          padding: '8px 20px', borderTop: `1px solid ${p.rule}`,
          display: 'flex', gap: 16,
          fontSize: 10, color: p.inkFaint,
        }}>
          <span><kbd style={kbd(p)}>/</kbd> filter</span>
          <span><kbd style={kbd(p)}>s</kbd> sort</span>
          <span><kbd style={kbd(p)}>↵</kbd> drill in</span>
          <span><kbd style={kbd(p)}>g g</kbd> top</span>
          <span style={{ marginLeft: 'auto', color: p.inkDim }}>
            data_as_of={data.meta.data_as_of}
          </span>
        </div>
      </main>
    );
  }

  function kbd(p) {
    return {
      fontFamily: 'inherit', fontSize: 9, padding: '1px 5px',
      background: p.chipBg, border: `1px solid ${p.rule}`, color: p.ink,
      marginRight: 4,
    };
  }

  // ── Right rail · event stream ─────────────────────────────────────────────
  function RightRail({ data, p }) {
    return (
      <aside style={{
        width: 320, flexShrink: 0,
        background: p.rail,
        borderLeft: `1px solid ${p.rule}`,
        display: 'flex', flexDirection: 'column',
        fontFamily: 'Geist Mono, JetBrains Mono, monospace',
      }}>
        <div style={{
          padding: '10px 14px',
          borderBottom: `1px solid ${p.rule}`,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}>
            <span style={{ color: p.accent }}>&gt;</span> EVENT_STREAM
          </div>
          <div style={{
            fontSize: 9, color: p.inkFaint, display: 'flex', alignItems: 'center', gap: 5,
          }}>
            <Blink color={p.pos} /> live
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '8px 0' }}>
          {data.events.map((e, i) => {
            const c = eventColor(e.kind, p);
            return (
              <div key={i} style={{
                padding: '8px 14px',
                borderBottom: `1px solid ${p.rule}`,
                fontSize: 10.5,
              }}>
                <div style={{
                  display: 'flex', justifyContent: 'space-between', marginBottom: 3,
                  fontSize: 9.5, letterSpacing: '0.04em',
                }}>
                  <span style={{ color: p.inkFaint }}>{e.ts}</span>
                  <span style={{
                    color: c, fontWeight: 600, letterSpacing: '0.06em',
                  }}>
                    [{e.kind}]
                  </span>
                </div>
                <div style={{
                  color: p.ink, fontWeight: 500, marginBottom: 1,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  {e.sym}
                </div>
                <div style={{
                  color: p.inkDim, fontSize: 10,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  <span style={{ color: c }}>{e.reason}</span>
                  <span style={{ color: p.inkFaint }}> · </span>
                  {e.detail}
                </div>
              </div>
            );
          })}
        </div>

        <div style={{
          padding: '8px 14px', borderTop: `1px solid ${p.rule}`,
          fontSize: 9.5, color: p.inkFaint, display: 'flex', justifyContent: 'space-between',
        }}>
          <span>buffered=64</span>
          <span>discord <span style={{ color: p.pos }}>●</span> connected</span>
        </div>
      </aside>
    );
  }

  // ── Main ───────────────────────────────────────────────────────────────────
  function ConsoleView({ data, t }) {
    const p = palette(t.theme, t.accent);
    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'Geist Mono, JetBrains Mono, monospace',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <style>{`
          @keyframes alphabot-blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0.3; } }
        `}</style>
        <StatusBar data={data} p={p} />
        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          <LeftRail data={data} p={p} t={t} />
          <Ledger data={data} p={p} t={t} />
          <RightRail data={data} p={p} />
        </div>
      </div>
    );
  }

  window.ConsoleView = ConsoleView;
})();
