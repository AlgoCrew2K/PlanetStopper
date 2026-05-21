// Direction C — Cockpit · main view
// Card-grid dashboard where each symphony is an "instrument" tile.
// Armed/triggered cards span wider and surface a sparkline+stop overlay
// and an MC dial. Standby cards stay compact.

(function () {
  const { cockpitPalette, cockpitStatus, CockpitDial, CockpitSparkArea, CockpitMicroSpark } = window;

  // ── Top bar ────────────────────────────────────────────────────────────────
  function TopBar({ data, p }) {
    const m = data.meta;
    const isLive = m.mode === 'LIVE';
    return (
      <div style={{
        display: 'flex', alignItems: 'center',
        padding: '14px 28px',
        background: p.surface,
        borderBottom: `1px solid ${p.rule}`,
        gap: 18,
      }}>
        {/* Wordmark */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 8,
            background: p.accent,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: p.surfaceHi, fontWeight: 700, fontSize: 13,
            fontFamily: 'Geist, sans-serif', letterSpacing: '-0.02em',
          }}>
            α
          </div>
          <div style={{
            fontFamily: 'Geist, sans-serif', fontWeight: 600, fontSize: 17,
            color: p.ink, letterSpacing: '-0.02em',
          }}>
            AlphaBot
            <span style={{ color: p.inkFaint, fontWeight: 400, marginLeft: 4 }}>guard</span>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ display: 'flex', gap: 2, marginLeft: 16 }}>
          {['Dashboard', 'Performance', 'AI advisor', 'History', 'Settings'].map((n, i) => (
            <button key={n} style={{
              padding: '7px 12px', border: 'none',
              background: i === 0 ? p.chipBg : 'transparent',
              borderRadius: 7,
              color: i === 0 ? p.ink : p.inkDim,
              fontFamily: 'Geist, sans-serif', fontSize: 12.5,
              fontWeight: i === 0 ? 600 : 500,
              cursor: 'pointer',
            }}>{n}</button>
          ))}
        </nav>

        <div style={{ flex: 1 }} />

        {/* Account chip */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 10px 6px 12px',
          background: p.chipBg, borderRadius: 8,
          fontFamily: 'Geist, sans-serif', fontSize: 12, color: p.ink,
        }}>
          <span style={{ width: 6, height: 6, borderRadius: 6, background: p.pos }} />
          {m.account.label}
          <span style={{ color: p.inkFaint, fontFamily: 'JetBrains Mono, monospace', fontSize: 10.5 }}>
            {m.account.uuid_short}
          </span>
          <span style={{ color: p.inkFaint }}>⌄</span>
        </div>

        {/* Mode pill */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 7,
          padding: '6px 11px', borderRadius: 8,
          background: isLive ? `${p.neg}18` : p.chipBg,
          border: `1px solid ${isLive ? p.neg + '55' : p.rule}`,
          fontFamily: 'Geist, sans-serif', fontSize: 11.5, fontWeight: 600,
          letterSpacing: '0.08em', color: isLive ? p.neg : p.ink,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: 6,
            background: isLive ? p.neg : p.inkDim,
          }} />
          {m.mode}
        </div>

        {/* Force run */}
        <button style={{
          padding: '7px 14px', borderRadius: 8,
          background: p.accent, color: p.surfaceHi,
          border: 'none', cursor: 'pointer',
          fontFamily: 'Geist, sans-serif', fontSize: 12, fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{
            width: 0, height: 0,
            borderLeft: '6px solid currentColor',
            borderTop: '4px solid transparent',
            borderBottom: '4px solid transparent',
          }} />
          Force run
        </button>
      </div>
    );
  }

  // ── Hero strip — portfolio summary ─────────────────────────────────────────
  function Hero({ data, p }) {
    const m = data.meta;
    const port = m.portfolio;
    const totalAlpha = data.symphonies
      .filter(s => s.guard_alpha != null)
      .reduce((a, s) => a + s.guard_alpha, 0);

    const tcSpark = data.symphonies.map(s => s.current_return).slice(0, 12);
    // Build a synthetic cumulative-ish chart for the hero
    const cum = [0, 8, 14, 12, 22, 30, 25, 38, 45, 52, 48, 58, 64, 72, 78];

    return (
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1.4fr 1fr 1fr 1fr',
        gap: 14, padding: '18px 28px',
      }}>
        {/* Hero portfolio card */}
        <div style={{
          background: p.surface, borderRadius: 14,
          border: `1px solid ${p.rule}`,
          padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <div>
              <div style={{
                fontFamily: 'Geist, sans-serif', fontSize: 11,
                color: p.inkDim, letterSpacing: '0.08em', textTransform: 'uppercase',
                fontWeight: 600,
              }}>
                Portfolio value
              </div>
              <div style={{
                fontFamily: 'Geist, sans-serif', fontWeight: 600, fontSize: 30,
                letterSpacing: '-0.025em', color: p.ink, marginTop: 2,
                fontVariantNumeric: 'tabular-nums',
              }}>
                ${port.account_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4 }}>
                <span style={{
                  fontFamily: 'Geist, sans-serif', fontSize: 13, fontWeight: 600,
                  color: port.tc >= 0 ? p.pos : p.neg,
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {port.tc >= 0 ? '+' : ''}{port.tc.toFixed(2)}%
                </span>
                <span style={{ fontSize: 11, color: p.inkFaint }}>today</span>
              </div>
            </div>
            <div style={{ flex: 1, marginLeft: 24, height: 56 }}>
              <CockpitSparkArea data={cum} color={p.pos} width={260} height={56} />
            </div>
          </div>
        </div>

        <HeroKpi label="Tracked" value={m.tracked} sub={`${data.symphonies.length} symphonies`} p={p} />
        <HeroKpi
          label="Active alerts"
          value={m.armed + m.triggered}
          sub={
            <span>
              <span style={{ color: p.warn }}>{m.armed} armed</span> · <span style={{ color: p.neg }}>{m.triggered} triggered</span>
            </span>
          }
          dotColor={p.warn} p={p}
        />
        <HeroKpi
          label="Guard alpha today"
          value={`${totalAlpha >= 0 ? '+' : ''}${totalAlpha.toFixed(2)}%`}
          valueColor={totalAlpha >= 0 ? p.pos : p.neg}
          sub="vs if-held"
          p={p}
        />
      </div>
    );
  }

  function HeroKpi({ label, value, sub, valueColor, dotColor, p }) {
    return (
      <div style={{
        background: p.surface, borderRadius: 14,
        border: `1px solid ${p.rule}`, padding: '18px 20px',
      }}>
        <div style={{
          fontFamily: 'Geist, sans-serif', fontSize: 11,
          color: p.inkDim, letterSpacing: '0.08em', textTransform: 'uppercase',
          fontWeight: 600, display: 'flex', alignItems: 'center', gap: 7,
        }}>
          {dotColor && <span style={{ width: 6, height: 6, borderRadius: 6, background: dotColor }} />}
          {label}
        </div>
        <div style={{
          fontFamily: 'Geist, sans-serif', fontWeight: 600, fontSize: 32,
          letterSpacing: '-0.025em', color: valueColor || p.ink, marginTop: 2,
          fontVariantNumeric: 'tabular-nums', lineHeight: 1.1,
        }}>
          {value}
        </div>
        <div style={{ fontSize: 11.5, color: p.inkFaint, marginTop: 4 }}>{sub}</div>
      </div>
    );
  }

  // ── Symphony tiles ────────────────────────────────────────────────────────
  function SymphonyCard({ sym, p, t, big }) {
    const si = cockpitStatus(sym, p);
    const ret = sym.triggered ? sym.triggered_at_return : sym.current_return;
    const retColor = ret >= 0 ? p.pos : p.neg;
    const ihRet = sym.current_return;
    const alpha = sym.guard_alpha;
    const compact = t.numFormat === 'compact';
    const digits = compact ? 1 : 2;
    const showMath = t.mathOverlays;

    return (
      <div style={{
        background: p.surface,
        border: `1px solid ${si.kind !== 'standby' ? si.color + '40' : p.rule}`,
        borderRadius: 14, padding: big ? 18 : 14,
        display: 'flex', flexDirection: 'column', gap: big ? 12 : 8,
        position: 'relative', overflow: 'hidden',
      }}>
        {/* Top: state pill + status accent line */}
        {si.kind !== 'standby' && (
          <div style={{
            position: 'absolute', top: 0, left: 0, right: 0, height: 2,
            background: si.color,
          }} />
        )}

        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{
              fontFamily: 'Geist, sans-serif', fontSize: big ? 13.5 : 12.5,
              fontWeight: 600, color: p.ink, letterSpacing: '-0.005em',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }} title={sym.name}>
              {sym.name}
            </div>
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              marginTop: 5,
              fontFamily: 'Geist, sans-serif', fontSize: 9.5, fontWeight: 700,
              letterSpacing: '0.1em', color: si.color,
            }}>
              <span style={{
                width: 6, height: 6, borderRadius: 6, background: si.color,
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
          </div>
          {showMath && big && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <div style={{ position: 'relative', width: 44, height: 44 }}>
                <CockpitDial
                  value={sym.mc_prob}
                  color={sym.mc_prob < 15 ? p.warn : (sym.mc_prob > 80 ? p.inkDim : p.accent)}
                  faint={p.rule}
                  size={44} stroke={4}
                />
                <div style={{
                  position: 'absolute', inset: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'Geist, sans-serif', fontSize: 11, fontWeight: 600,
                  color: p.ink, fontVariantNumeric: 'tabular-nums',
                }}>
                  {sym.mc_prob != null ? sym.mc_prob.toFixed(0) : '—'}
                </div>
              </div>
              <div style={{
                fontSize: 8.5, color: p.inkFaint, letterSpacing: '0.08em', textTransform: 'uppercase',
                fontFamily: 'Geist, sans-serif', fontWeight: 600,
              }}>MC</div>
            </div>
          )}
        </div>

        {/* Big number */}
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
          <div style={{
            fontFamily: 'Geist, sans-serif', fontWeight: 600,
            fontSize: big ? 28 : 22, color: retColor,
            letterSpacing: '-0.025em', fontVariantNumeric: 'tabular-nums',
            lineHeight: 1,
          }}>
            {ret >= 0 ? '+' : ''}{ret.toFixed(digits)}%
          </div>
          {!big && showMath && (
            <div style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
              color: p.inkFaint, letterSpacing: '0.04em',
            }}>
              MC {sym.mc_prob != null ? sym.mc_prob.toFixed(1) : '—'}
            </div>
          )}
        </div>

        {/* Sparkline w/ stop overlay (big cards only) */}
        {big ? (
          <div style={{ color: p.ink, marginTop: 2 }}>
            <CockpitSparkArea
              data={sym.sparkline}
              color={sym.triggered ? p.inkDim : retColor}
              width={big ? 380 : 200} height={56}
              stop={showMath ? sym.stop_trigger : null}
            />
          </div>
        ) : (
          <div style={{ color: p.ink, marginTop: 2 }}>
            <CockpitMicroSpark
              data={sym.sparkline}
              color={sym.triggered ? p.inkDim : retColor}
              width={200} height={28}
            />
          </div>
        )}

        {/* Stats row */}
        <div style={{
          display: 'grid', gridTemplateColumns: big ? 'repeat(4, 1fr)' : 'repeat(3, 1fr)',
          gap: 8, paddingTop: 6,
          borderTop: `1px solid ${p.rule}`,
        }}>
          <Stat label="Stop" value={`${sym.stop_trigger >= 0 ? '+' : ''}${sym.stop_trigger.toFixed(digits)}%`} p={p} />
          <Stat label="Peak" value={`+${sym.shadow_hwm.toFixed(digits)}%`} color={sym.shadow_hwm > 0 ? p.pos : p.ink} p={p} />
          <Stat
            label="If held"
            value={`${ihRet >= 0 ? '+' : ''}${ihRet.toFixed(digits)}%`}
            color={ihRet >= 0 ? p.pos : p.neg} p={p}
          />
          {big && (
            <Stat
              label="α"
              value={alpha != null ? `${alpha >= 0 ? '+' : ''}${alpha.toFixed(digits)}%` : '—'}
              color={alpha != null ? (alpha >= 0 ? p.pos : p.neg) : p.inkFaint}
              p={p}
            />
          )}
        </div>
      </div>
    );
  }

  function Stat({ label, value, color, p }) {
    return (
      <div>
        <div style={{
          fontFamily: 'Geist, sans-serif', fontSize: 9, color: p.inkDim,
          letterSpacing: '0.1em', textTransform: 'uppercase', fontWeight: 600,
        }}>
          {label}
        </div>
        <div style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 11.5,
          color: color || p.ink, fontVariantNumeric: 'tabular-nums', marginTop: 1,
        }}>
          {value}
        </div>
      </div>
    );
  }

  // ── Grid ───────────────────────────────────────────────────────────────────
  function Grid({ data, p, t }) {
    // Active (armed/triggered) get big cards; standby get small
    const active = data.symphonies.filter(s => s.triggered || s.armed || s.tp_armed || s.para_armed);
    const standby = data.symphonies.filter(s => !(s.triggered || s.armed || s.tp_armed || s.para_armed));

    return (
      <div style={{ padding: '6px 28px 28px', flex: 1, overflow: 'auto' }}>
        {active.length > 0 && (
          <React.Fragment>
            <SectionLabel label="Active · armed & triggered" count={active.length} p={p} accent={p.warn} />
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14, marginBottom: 22,
            }}>
              {active.map(s => <SymphonyCard key={s.id} sym={s} p={p} t={t} big />)}
            </div>
          </React.Fragment>
        )}

        <SectionLabel label="Standby · watching" count={standby.length} p={p} accent={p.inkFaint} />
        <div style={{
          display: 'grid',
          gridTemplateColumns: t.density === 'compact' ? 'repeat(4, 1fr)' : 'repeat(3, 1fr)',
          gap: 14,
        }}>
          {standby.map(s => <SymphonyCard key={s.id} sym={s} p={p} t={t} />)}
        </div>
      </div>
    );
  }

  function SectionLabel({ label, count, p, accent }) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, margin: '14px 0 12px',
      }}>
        <span style={{ width: 6, height: 6, borderRadius: 6, background: accent }} />
        <span style={{
          fontFamily: 'Geist, sans-serif', fontSize: 12, fontWeight: 600,
          color: p.ink, letterSpacing: '-0.005em',
        }}>{label}</span>
        <span style={{
          fontFamily: 'JetBrains Mono, monospace', fontSize: 10.5,
          color: p.inkFaint,
        }}>{count}</span>
        <div style={{ flex: 1, height: 1, background: p.rule, marginLeft: 4 }} />
      </div>
    );
  }

  // ── Main ──────────────────────────────────────────────────────────────────
  function Cockpit({ data, t }) {
    const p = cockpitPalette(t.theme, t.accent);
    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'Geist, ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <TopBar data={data} p={p} />
        <Hero data={data} p={p} />
        <Grid data={data} p={p} t={t} />
      </div>
    );
  }

  window.Cockpit = Cockpit;
})();
