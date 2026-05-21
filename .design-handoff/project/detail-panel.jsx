// Per-symphony detail slide-over.
// Opens from the right when a Studio symphony card is clicked.
// Shows the full risk story for one symphony: intraday chart with live +
// shadow + stop + breakeven + VWAP, event timeline, bot-vs-if-held perf,
// math panel, and variables.

(function () {
  const { useState, useEffect, useMemo } = React;

  // ── Main panel ────────────────────────────────────────────────────────────
  function DetailPanel({ sym, data, t, p, onClose }) {
    const [show, setShow] = useState(false);
    const [overlays, setOverlays] = useState({
      stop: true, breakeven: true, vwap: t.mathOverlays, mc: t.mathOverlays,
    });

    useEffect(() => {
      if (sym) requestAnimationFrame(() => setShow(true));
      else setShow(false);
    }, [sym]);

    if (!sym) return null;

    const detail = data.detail; // showcase data is bound to sym_paragons; we drive UI from it regardless of which card was clicked
    const compact = t.numFormat === 'compact';
    const digits = compact ? 1 : 2;

    return (
      <React.Fragment>
        {/* Scrim */}
        <div
          onClick={() => { setShow(false); setTimeout(onClose, 220); }}
          style={{
            position: 'absolute', inset: 0,
            background: 'rgba(0,0,0,0.35)',
            opacity: show ? 1 : 0,
            transition: 'opacity .22s ease-out',
            zIndex: 10,
          }}
        />

        {/* Panel */}
        <aside style={{
          position: 'absolute', right: 0, top: 0, bottom: 0,
          width: 760, background: p.paper,
          boxShadow: '-8px 0 32px rgba(0,0,0,0.18)',
          borderLeft: `1px solid ${p.rule}`,
          transform: show ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform .26s cubic-bezier(.2,.7,.3,1)',
          display: 'flex', flexDirection: 'column',
          zIndex: 11, color: p.ink,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', overflow: 'hidden',
        }}>
          <PanelHeader sym={sym} p={p} onClose={() => { setShow(false); setTimeout(onClose, 220); }} />
          <div style={{ flex: 1, overflow: 'auto' }}>
            <StatRow sym={sym} p={p} digits={digits} />
            <ChartBlock detail={detail} sym={sym} p={p} overlays={overlays} setOverlays={setOverlays} />
            <SplitBlock detail={detail} sym={sym} p={p} digits={digits} />
            <PerfBlock detail={detail} sym={sym} p={p} digits={digits} />
            <VarsBlock detail={detail} p={p} />
          </div>
          <PanelFooter sym={sym} p={p} />
        </aside>
      </React.Fragment>
    );
  }

  // ── Header ────────────────────────────────────────────────────────────────
  function PanelHeader({ sym, p, onClose }) {
    const si = window.studioStatusInfo(sym, p);
    return (
      <div style={{
        padding: '16px 24px 16px',
        borderBottom: `1px solid ${p.rule}`,
        display: 'flex', alignItems: 'flex-start', gap: 14,
      }}>
        <button onClick={onClose} style={{
          width: 30, height: 30, padding: 0, marginTop: 2,
          background: 'transparent', border: `1px solid ${p.rule}`, borderRadius: 4,
          color: p.inkDim, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} title="Close (Esc)">
          <span style={{ fontSize: 14, fontFamily: 'var(--studio-sans, Manrope), sans-serif' }}>×</span>
        </button>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 7,
            fontSize: 9.5, fontWeight: 700, letterSpacing: '0.12em',
            color: si.color,
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: 6, background: si.color,
              boxShadow: si.kind !== 'standby' ? `0 0 0 3px ${si.color}22` : 'none',
            }} />
            {si.label}
            {sym.triggered && sym.triggered_at_time && (
              <span style={{ color: p.inkFaint, fontWeight: 500, letterSpacing: '0.04em' }}>
                · executed {sym.triggered_at_time} ET
              </span>
            )}
          </div>
          <h2 style={{
            margin: '4px 0 0', fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: 22, fontWeight: 500, letterSpacing: '-0.015em',
            color: p.ink, lineHeight: 1.2,
          }}>
            {sym.name}
          </h2>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button style={{
            padding: '6px 11px', background: 'transparent',
            border: `1px solid ${p.rule}`, borderRadius: 4,
            color: p.ink, fontSize: 11, fontWeight: 500, cursor: 'pointer',
          }}>View logs</button>
          <button style={{
            padding: '6px 11px', background: 'transparent',
            border: `1px solid ${p.neg}55`, borderRadius: 4,
            color: p.neg, fontSize: 11, fontWeight: 600, cursor: 'pointer',
            letterSpacing: '0.04em',
          }}>Go to cash →</button>
        </div>
      </div>
    );
  }

  // ── Stat row ──────────────────────────────────────────────────────────────
  function StatRow({ sym, p, digits }) {
    const ret = sym.triggered ? sym.triggered_at_return : sym.current_return;
    const ihRet = sym.current_return;
    const alpha = sym.guard_alpha;
    const stats = [
      {
        k: 'Live return',
        v: `${ret >= 0 ? '+' : ''}${ret.toFixed(digits)}%`,
        c: ret >= 0 ? p.pos : p.neg,
        sub: sym.triggered ? 'frozen at exit' : 'real-time',
      },
      {
        k: 'Stop level',
        v: `${sym.stop_trigger >= 0 ? '+' : ''}${sym.stop_trigger.toFixed(digits)}%`,
        c: p.ink,
        sub: sym.breakeven_locked ? '◆ breakeven lock' : 'vol-scaled',
      },
      {
        k: 'Shadow peak',
        v: `+${sym.shadow_hwm.toFixed(digits)}%`,
        c: p.pos,
        sub: 'HWM today',
      },
      {
        k: 'Guard α',
        v: alpha != null ? `${alpha >= 0 ? '+' : ''}${alpha.toFixed(digits)}%` : '—',
        c: alpha != null ? (alpha >= 0 ? p.pos : p.neg) : p.inkFaint,
        sub: alpha != null ? (alpha >= 0 ? 'saved vs hold' : 'gave up vs hold') : 'not exited',
      },
    ];
    return (
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
        borderBottom: `1px solid ${p.rule}`,
      }}>
        {stats.map((s, i) => (
          <div key={s.k} style={{
            padding: '16px 18px',
            borderRight: i < 3 ? `1px solid ${p.rule}` : 'none',
          }}>
            <div style={{
              fontSize: 9.5, color: p.inkDim, letterSpacing: '0.1em',
              textTransform: 'uppercase', fontWeight: 600,
            }}>{s.k}</div>
            <div style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 26, fontWeight: 500,
              color: s.c, letterSpacing: '-0.02em', marginTop: 2,
              fontVariantNumeric: 'tabular-nums', lineHeight: 1.05,
            }}>{s.v}</div>
            <div style={{ fontSize: 10.5, color: p.inkFaint, marginTop: 3, letterSpacing: '0.02em' }}>
              {s.sub}
            </div>
          </div>
        ))}
      </div>
    );
  }

  // ── Chart block ───────────────────────────────────────────────────────────
  function ChartBlock({ detail, sym, p, overlays, setOverlays }) {
    const id = detail.intraday;
    return (
      <div style={{ padding: '18px 24px 24px', borderBottom: `1px solid ${p.rule}` }}>
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          marginBottom: 12,
        }}>
          <div>
            <div style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 16, fontWeight: 500,
              color: p.ink, letterSpacing: '-0.005em',
            }}>
              Intraday tape
            </div>
            <div style={{ fontSize: 10.5, color: p.inkDim, marginTop: 1 }}>
              09:30 ET → 16:00 ET · 1-minute resolution
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <Toggle label="Stop" color={p.neg} active={overlays.stop}
              onClick={() => setOverlays(o => ({ ...o, stop: !o.stop }))} p={p} />
            <Toggle label="Breakeven" color={p.accent} active={overlays.breakeven}
              onClick={() => setOverlays(o => ({ ...o, breakeven: !o.breakeven }))} p={p} />
            <Toggle label="VWAP" color={p.cyan} active={overlays.vwap}
              onClick={() => setOverlays(o => ({ ...o, vwap: !o.vwap }))} p={p} />
            <Toggle label="MC %" color={p.warn} active={overlays.mc}
              onClick={() => setOverlays(o => ({ ...o, mc: !o.mc }))} p={p} />
          </div>
        </div>

        <Chart id={id} p={p} overlays={overlays} sym={sym} />

        <div style={{
          display: 'flex', gap: 16, marginTop: 12, fontSize: 11,
          color: p.inkDim, fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          flexWrap: 'wrap',
        }}>
          <LegendItem color={p.pos} label="Live (if held)" />
          <LegendItem color={p.inkDim} label="Shadow (booked)" dashed />
          {overlays.stop && <LegendItem color={p.neg} label="Trailing stop" dashed />}
          {overlays.breakeven && <LegendItem color={p.accent} label="Breakeven lock" />}
          {overlays.vwap && <LegendItem color={p.cyan} label="VWAP" />}
          {overlays.mc && <LegendItem color={p.warn} label="MC probability (right axis)" />}
        </div>
      </div>
    );
  }

  function Toggle({ label, color, active, onClick, p }) {
    return (
      <button onClick={onClick} style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '4px 9px',
        background: active ? color + '14' : 'transparent',
        border: `1px solid ${active ? color + '55' : p.rule}`,
        borderRadius: 4,
        color: active ? p.ink : p.inkDim,
        fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5, fontWeight: 500,
        cursor: 'pointer', letterSpacing: '0.02em',
      }}>
        <span style={{
          width: 5, height: 5, borderRadius: 5,
          background: active ? color : p.inkFaint,
        }} />
        {label}
      </button>
    );
  }

  function LegendItem({ color, label, dashed }) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <svg width={20} height={6}>
          <line x1={0} y1={3} x2={20} y2={3} stroke={color} strokeWidth={2}
            strokeDasharray={dashed ? '3,3' : 'none'} />
        </svg>
        {label}
      </span>
    );
  }

  function Chart({ id, p, overlays, sym }) {
    const w = 700, h = 220, pad = { l: 36, r: 36, t: 12, b: 26 };
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;

    // Y axis range — % returns. Include stops + breakeven.
    const allY = [
      ...id.live.filter(v => v != null),
      ...id.shadow.filter(v => v != null),
      ...id.stop.filter(v => v != null),
      ...(overlays.vwap ? id.vwap.filter(v => v != null) : []),
      ...id.breakeven.filter(v => v != null),
      0,
    ];
    const lo = Math.min(...allY) - 0.2;
    const hi = Math.max(...allY) + 0.2;
    const span = (hi - lo) || 1;

    const n = id.times.length;
    const x = (i) => pad.l + (i / (n - 1)) * innerW;
    const y = (v) => pad.t + innerH - ((v - lo) / span) * innerH;

    // MC % uses right axis 0..100
    const yMc = (v) => pad.t + innerH - (Math.max(0, Math.min(100, v)) / 100) * innerH;

    function buildPath(series) {
      let d = '', open = false;
      series.forEach((v, i) => {
        if (v == null) { open = false; return; }
        d += `${!open ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
        open = true;
      });
      return d.trim();
    }

    function buildMcPath(series) {
      let d = '', open = false;
      series.forEach((v, i) => {
        if (v == null) { open = false; return; }
        d += `${!open ? 'M' : 'L'}${x(i).toFixed(1)},${yMc(v).toFixed(1)} `;
        open = true;
      });
      return d.trim();
    }

    const livePath = buildPath(id.live);
    const shadowPath = buildPath(id.shadow);
    const stopPath = buildPath(id.stop);
    const breakevenPath = buildPath(id.breakeven);
    const vwapPath = buildPath(id.vwap);
    const mcPath = buildMcPath(id.mc);

    // Gridlines at 0% and 1% increments
    const gridVals = [];
    for (let g = Math.ceil(lo); g <= Math.floor(hi); g++) gridVals.push(g);

    // Trigger marker
    const trigIdx = id.times.indexOf('12:33');
    const trigX = trigIdx >= 0 ? x(trigIdx) : null;
    const trigY = trigIdx >= 0 ? y(id.live[trigIdx]) : null;

    // Time ticks every ~hour
    const tickIdxs = [0, 4, 8, 12, 16, 20, 24, 27];

    return (
      <div style={{ background: p.bg === '#13100c' ? '#1c1814' : '#fdfaf0', borderRadius: 4, padding: 0, border: `1px solid ${p.rule}` }}>
        <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} style={{ display: 'block' }}>
          {/* gridlines */}
          {gridVals.map(g => (
            <g key={g}>
              <line x1={pad.l} x2={w - pad.r} y1={y(g)} y2={y(g)} stroke={p.rule} strokeWidth={1} strokeDasharray={g === 0 ? 'none' : '0'} opacity={g === 0 ? 0.6 : 0.35} />
              <text x={pad.l - 6} y={y(g) + 3} fill={p.inkFaint} fontSize="9" fontFamily="var(--studio-sans, Manrope), sans-serif" textAnchor="end">
                {g > 0 ? '+' : ''}{g}%
              </text>
            </g>
          ))}

          {/* right-axis MC label if shown */}
          {overlays.mc && (
            <React.Fragment>
              {[0, 50, 100].map(v => (
                <text key={v} x={w - pad.r + 6} y={yMc(v) + 3} fill={p.warn} fontSize="9" fontFamily="var(--studio-sans, Manrope), sans-serif" textAnchor="start" opacity="0.8">
                  {v}
                </text>
              ))}
            </React.Fragment>
          )}

          {/* live area */}
          <defs>
            <linearGradient id="detail-live-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={p.pos} stopOpacity="0.16" />
              <stop offset="1" stopColor={p.pos} stopOpacity="0" />
            </linearGradient>
          </defs>
          {livePath && (
            <path
              d={`${livePath} L${x(n-1)},${y(lo)} L${x(0)},${y(lo)} Z`}
              fill="url(#detail-live-grad)"
            />
          )}

          {/* breakeven horizontal segment */}
          {overlays.breakeven && breakevenPath && (
            <path d={breakevenPath} fill="none" stroke={p.accent} strokeWidth={1.5} opacity="0.9" />
          )}

          {/* stop line */}
          {overlays.stop && stopPath && (
            <path d={stopPath} fill="none" stroke={p.neg} strokeWidth={1.5} strokeDasharray="4,3" opacity="0.85" />
          )}

          {/* VWAP */}
          {overlays.vwap && vwapPath && (
            <path d={vwapPath} fill="none" stroke={p.cyan} strokeWidth={1.4} opacity="0.65" />
          )}

          {/* Shadow (after trigger — dashed continuation) */}
          {shadowPath && (
            <path d={shadowPath} fill="none" stroke={p.inkDim} strokeWidth={1.6} strokeDasharray="2,3" />
          )}

          {/* Live */}
          {livePath && (
            <path d={livePath} fill="none" stroke={p.pos} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
          )}

          {/* MC overlay */}
          {overlays.mc && mcPath && (
            <path d={mcPath} fill="none" stroke={p.warn} strokeWidth={1.4} strokeDasharray="1,2" opacity="0.8" />
          )}

          {/* Trigger marker */}
          {trigX != null && (
            <g>
              <line x1={trigX} x2={trigX} y1={pad.t} y2={h - pad.b} stroke={p.plum} strokeWidth={1} opacity="0.6" />
              <circle cx={trigX} cy={trigY} r={5} fill={p.paper} stroke={p.plum} strokeWidth={2} />
              <circle cx={trigX} cy={trigY} r={2} fill={p.plum} />
              <rect
                x={trigX + 8} y={pad.t + 4}
                width={70} height={20} rx={2}
                fill={p.plum} opacity="0.95"
              />
              <text x={trigX + 43} y={pad.t + 17} fontSize="9.5" fontFamily="var(--studio-sans, Manrope), sans-serif" fontWeight="600" fill="#fff" textAnchor="middle" letterSpacing="0.06em">
                TP · 12:33
              </text>
            </g>
          )}

          {/* X tick labels */}
          {tickIdxs.map(i => (
            <text key={i} x={x(i)} y={h - 8} fill={p.inkFaint} fontSize="9" fontFamily="var(--studio-sans, Manrope), sans-serif" textAnchor="middle">
              {id.times[i]}
            </text>
          ))}
        </svg>
      </div>
    );
  }

  // ── Events + Math split block ─────────────────────────────────────────────
  function SplitBlock({ detail, sym, p, digits }) {
    return (
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        borderBottom: `1px solid ${p.rule}`,
      }}>
        <Events events={detail.events} p={p} />
        <RiskMath detail={detail} sym={sym} p={p} digits={digits} />
      </div>
    );
  }

  function Events({ events, p }) {
    const colors = {
      TRIGGER: p.plum, ARM: p.warn, PARA: p.cyan, LOCK: p.accent, CONFIRM: p.inkDim, BOOT: p.inkFaint,
    };
    return (
      <div style={{ padding: '18px 20px 20px', borderRight: `1px solid ${p.rule}` }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 16, fontWeight: 500,
          color: p.ink, letterSpacing: '-0.005em', marginBottom: 14,
        }}>
          Today's events
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {events.map((e, i) => {
            const c = colors[e.kind] || p.inkDim;
            return (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: '64px 1fr',
                gap: 12, paddingBottom: 12, position: 'relative',
                paddingLeft: 0,
              }}>
                <span style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10,
                  color: p.inkFaint, letterSpacing: '0.04em', paddingTop: 2,
                }}>
                  {e.ts}
                </span>
                <div style={{
                  borderLeft: `1px solid ${p.rule}`,
                  paddingLeft: 14, position: 'relative', paddingBottom: 2,
                }}>
                  <span style={{
                    position: 'absolute', left: -4, top: 4,
                    width: 7, height: 7, borderRadius: 7,
                    background: c, border: `2px solid ${p.paper}`,
                  }} />
                  <div style={{
                    fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11, fontWeight: 600,
                    color: c, letterSpacing: '0.06em',
                  }}>
                    {e.kind} · {e.reason}
                  </div>
                  <div style={{ fontSize: 11.5, color: p.inkDim, marginTop: 2 }}>
                    {e.detail}
                    {e.ok && <span style={{ color: p.pos, marginLeft: 6 }}>✓</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function RiskMath({ detail, sym, p, digits }) {
    const ip = detail.intraday;
    const lastMc = sym.mc_prob;
    const stopDist = sym.shadow_hwm - sym.stop_trigger;
    return (
      <div style={{ padding: '18px 20px 20px' }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 16, fontWeight: 500,
          color: p.ink, letterSpacing: '-0.005em', marginBottom: 14,
        }}>
          Risk math
        </div>
        <MathRow label="MC probability" value={`${lastMc != null ? lastMc.toFixed(1) : '—'}%`} bar={lastMc} hint="lower → more risk-off" p={p} />
        <MathRow label="Stop distance" value={`${stopDist.toFixed(2)}%`} hint="from peak to stop" p={p} />
        <MathRow label="Vol (20d)" value="16.8%" hint="annualised" p={p} />
        <MathRow label="Parabolic velocity" value="0.184 ⤴" hint="threshold 0.150" p={p} alert />
        <MathRow label="Breakeven lock" value={sym.breakeven_locked ? 'Active · floor 0.00%' : 'Off'} hint="5 confirm ticks @ +1.20%" p={p} accent={sym.breakeven_locked} />
        <MathRow label="VWAP defense" value="Armed · System B" hint={`bleed mult ${detail.risk_params.VWAP_BLEED_MULTIPLIER}× vol`} p={p} />
      </div>
    );
  }

  function MathRow({ label, value, hint, bar, p, alert, accent }) {
    return (
      <div style={{
        paddingBottom: 10, marginBottom: 10,
        borderBottom: `1px solid ${p.rule}`,
      }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
          marginBottom: 2,
        }}>
          <span style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11, color: p.inkDim,
            fontWeight: 500, letterSpacing: '0.02em',
          }}>
            {label}
          </span>
          <span style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12,
            color: accent ? p.accent : alert ? p.warn : p.ink, fontWeight: 500,
            fontVariantNumeric: 'tabular-nums',
          }}>
            {value}
          </span>
        </div>
        {bar != null && (
          <div style={{
            height: 3, background: p.chipBg, marginBottom: 4, overflow: 'hidden',
          }}>
            <div style={{
              height: '100%', width: `${Math.max(0, Math.min(100, bar))}%`,
              background: bar < 15 ? p.warn : (bar > 80 ? p.inkDim : p.accent),
            }} />
          </div>
        )}
        {hint && (
          <div style={{ fontSize: 10, color: p.inkFaint, letterSpacing: '0.02em' }}>
            {hint}
          </div>
        )}
      </div>
    );
  }

  // ── Perf block · symphony Bot vs Held across windows ─────────────────────
  function PerfBlock({ detail, sym, p, digits }) {
    const [windowDays, setWindowDays] = useState(60);
    const dayCount = (window.TIME_WINDOW_DAYS && window.TIME_WINDOW_DAYS[windowDays]) || 60;
    const windowLabel = (window.TIME_WINDOWS.find(w => w.value === windowDays) || {}).label || '60d';

    // Re-synth bot/held trajectories per window, seeded by symphony id so the
    // shape is deterministic per symphony. End values come from sym's _cr/_mdd.
    const symSeed = sym.id.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
    const heldEnd = sym.cr_ih != null ? sym.cr_ih : -10;
    const botEnd  = sym.cr_dr != null ? sym.cr_dr : heldEnd + 3;
    // Scale to window
    const factor = dayCount / 60;
    const heldSeries = window.synthSeries(Math.max(7, dayCount), heldEnd * factor, symSeed);
    const botSeries  = window.synthSeries(Math.max(7, dayCount), botEnd * factor, symSeed + 17);

    // X tick labels — pick a sane density
    const tickN = 6;
    const fullDates = window.synthDates(Math.max(7, dayCount));
    const tickIdxs = Array.from({ length: tickN }, (_, i) => Math.floor(i * (botSeries.length - 1) / (tickN - 1)));

    const w = 680, h = 110, pad = { l: 24, r: 24, t: 8, b: 18 };
    const innerW = w - pad.l - pad.r;
    const innerH = h - pad.t - pad.b;
    const all = [...botSeries, ...heldSeries, 0];
    const lo = Math.min(...all) - 1;
    const hi = Math.max(...all) + 1;
    const span = (hi - lo) || 1;
    const x = (i) => pad.l + (i / (botSeries.length - 1)) * innerW;
    const y = (v) => pad.t + innerH - ((v - lo) / span) * innerH;
    const botPath = botSeries.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const heldPath = heldSeries.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');

    const alpha = botSeries[botSeries.length-1] - heldSeries[heldSeries.length-1];

    return (
      <div style={{ padding: '18px 24px 22px', borderBottom: `1px solid ${p.rule}` }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
          marginBottom: 12, gap: 10, flexWrap: 'wrap',
        }}>
          <div>
            <div style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 16, fontWeight: 700,
              color: p.ink, letterSpacing: '-0.005em',
            }}>
              {windowLabel} performance · this symphony
            </div>
            <div style={{
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11,
              color: alpha >= 0 ? p.pos : p.neg, fontWeight: 700,
              fontVariantNumeric: 'tabular-nums', letterSpacing: '0.04em',
              marginTop: 4,
            }}>
              {alpha >= 0 ? '+' : ''}{alpha.toFixed(digits)}% guard α over window
            </div>
          </div>
          <window.SegControl
            value={windowDays}
            options={window.TIME_WINDOWS}
            onChange={setWindowDays}
            p={p}
          />
        </div>

        <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" style={{ display: 'block', marginBottom: 8 }}>
          <line x1={pad.l} x2={w-pad.r} y1={y(0)} y2={y(0)} stroke={p.rule} />
          <path d={heldPath} fill="none" stroke={p.inkFaint} strokeWidth={1.5} strokeDasharray="3,3" />
          <path d={botPath} fill="none" stroke={p.accent} strokeWidth={2} strokeLinecap="round" />
          {tickIdxs.map((i, k) => (
            <text key={k} x={x(i)} y={h - 4} fill={p.inkFaint} fontSize="9" fontFamily="var(--studio-sans, Manrope), sans-serif" textAnchor="middle">
              {fullDates[i]}
            </text>
          ))}
        </svg>

        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 12, paddingTop: 6,
        }}>
          <PerfCell label="Today (TC)" bot={sym.tc_dr} held={sym.tc_ih} p={p} digits={digits} />
          <PerfCell label="Cumulative (CR)" bot={sym.cr_dr} held={sym.cr_ih} p={p} digits={digits} />
          <PerfCell label="Max DD" bot={sym.mdd_dr} held={sym.mdd_ih} p={p} digits={digits} invert />
        </div>
      </div>
    );
  }

  function PerfCell({ label, bot, held, p, digits, invert, note }) {
    const diff = (bot != null && held != null) ? (invert ? held - bot : bot - held) : null;
    const fmt = v => v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`;
    return (
      <div>
        <div style={{
          fontSize: 9.5, color: p.inkDim, letterSpacing: '0.1em',
          textTransform: 'uppercase', fontWeight: 600,
        }}>
          {label}
        </div>
        <div style={{
          display: 'flex', gap: 12, marginTop: 5,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12,
        }}>
          <div>
            <div style={{ fontSize: 9, color: p.inkFaint, letterSpacing: '0.06em' }}>BOT</div>
            <div style={{
              color: bot != null ? (bot >= 0 ? p.pos : p.neg) : p.inkFaint,
              fontWeight: 600,
            }}>{fmt(bot)}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: p.inkFaint, letterSpacing: '0.06em' }}>HELD</div>
            <div style={{ color: held != null ? (held >= 0 ? p.pos : p.neg) : p.inkFaint }}>
              {fmt(held)}
            </div>
          </div>
          <div style={{ marginLeft: 'auto' }}>
            <div style={{ fontSize: 9, color: p.inkFaint, letterSpacing: '0.06em', textAlign: 'right' }}>α</div>
            <div style={{
              color: diff != null ? (diff >= 0 ? p.pos : p.neg) : p.inkFaint,
              fontWeight: 600, textAlign: 'right',
            }}>{diff != null ? `${diff >= 0 ? '+' : ''}${diff.toFixed(digits)}` : (note || '—')}</div>
          </div>
        </div>
      </div>
    );
  }

  // ── Variables block ───────────────────────────────────────────────────────
  function VarsBlock({ detail, p }) {
    const params = detail.risk_params;
    const locked = new Set(detail.locked_vars);
    return (
      <div style={{ padding: '18px 24px 22px' }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
          marginBottom: 12,
        }}>
          <div style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 16, fontWeight: 500,
            color: p.ink, letterSpacing: '-0.005em',
          }}>
            Symphony variables
          </div>
          <button style={{
            background: 'transparent', border: `1px solid ${p.rule}`, borderRadius: 4,
            padding: '5px 10px', cursor: 'pointer',
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11, color: p.ink, fontWeight: 500,
          }}>
            Edit variables
          </button>
        </div>
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)',
          gap: 0, border: `1px solid ${p.rule}`,
        }}>
          {Object.entries(params).map(([k, v], i) => (
            <div key={k} style={{
              padding: '9px 12px',
              borderBottom: i < Object.keys(params).length - 2 ? `1px solid ${p.rule}` : 'none',
              borderRight: i % 2 === 0 ? `1px solid ${p.rule}` : 'none',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              gap: 8,
            }}>
              <span style={{
                fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10.5,
                color: p.inkDim, letterSpacing: '0.02em',
              }}>
                {k}
              </span>
              <span style={{
                fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5,
                color: p.ink, fontWeight: 500,
                fontVariantNumeric: 'tabular-nums',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}>
                {v}
                {locked.has(k) && (
                  <span style={{ color: p.accent, fontSize: 9 }} title="Locked from autotuner">◆</span>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // ── Footer ────────────────────────────────────────────────────────────────
  function PanelFooter({ sym, p }) {
    return (
      <div style={{
        padding: '12px 24px', borderTop: `1px solid ${p.rule}`,
        background: p.paper,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 10,
        color: p.inkFaint, letterSpacing: '0.04em',
      }}>
        <span>id: {sym.id} · normalized: {sym.id.replace('sym_', '')}</span>
        <span>shadow_hwm · stop · mc_prob updated every 1m</span>
      </div>
    );
  }

  window.DetailPanel = DetailPanel;
})();
