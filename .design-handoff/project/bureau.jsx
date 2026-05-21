// Direction A — Bureau
// Editorial / financial-publication feel. Warm cream paper, Source Serif 4
// display, Geist body, JetBrains Mono numerals. Hairline rules, generous
// whitespace, status as small caps + colored dot. Both light and dark modes.

(function () {
  const { useMemo } = React;

  // ── Palette ────────────────────────────────────────────────────────────────
  function palette(theme, accent) {
    if (theme === 'dark') {
      return {
        bg: '#161310',
        paper: '#1c1814',
        ink: '#ece4d2',
        inkDim: 'rgba(236,228,210,0.62)',
        inkFaint: 'rgba(236,228,210,0.38)',
        rule: 'rgba(236,228,210,0.14)',
        ruleStrong: 'rgba(236,228,210,0.28)',
        chipBg: 'rgba(236,228,210,0.06)',
        accent,
        pos: '#7fbf7f',
        neg: '#e07061',
        warn: '#e3a14a',
        plum: '#c79ad4',
        triggered: '#e07061',
        tp: '#c79ad4',
        para: '#84c6e0',
      };
    }
    return {
      bg: '#f4efe5',
      paper: '#faf6ec',
      ink: '#1a1714',
      inkDim: 'rgba(26,23,20,0.62)',
      inkFaint: 'rgba(26,23,20,0.38)',
      rule: 'rgba(26,23,20,0.12)',
      ruleStrong: 'rgba(26,23,20,0.28)',
      chipBg: 'rgba(26,23,20,0.04)',
      accent,
      pos: '#1f7a4d',
      neg: '#b43a2a',
      warn: '#b07419',
      plum: '#6a3f8a',
      triggered: '#b43a2a',
      tp: '#6a3f8a',
      para: '#1a5d8f',
    };
  }

  function statusColor(sym, p) {
    if (sym.triggered) {
      const r = sym.triggered_reason || '';
      if (r.includes('Take-Profit')) return p.tp;
      if (r.includes('VWAP')) return p.tp;
      if (r.includes('Trailing')) return p.triggered;
      return p.triggered;
    }
    if (sym.para_armed) return p.para;
    if (sym.tp_armed) return p.tp;
    if (sym.armed) return p.warn;
    return p.inkFaint;
  }

  function statusLabel(sym) {
    if (sym.triggered) return (sym.triggered_reason || 'Triggered').toUpperCase();
    if (sym.para_armed) return 'PARA-ARMED';
    if (sym.tp_armed) return 'TP-ARMED';
    if (sym.armed) return 'ARMED';
    return 'STANDBY';
  }

  function fmtPct(v, compact, opts = {}) {
    if (v == null) return '—';
    const digits = compact ? 1 : 2;
    const sign = opts.signed && v > 0 ? '+' : (v < 0 ? '' : (opts.signed ? '+' : ''));
    return `${sign}${v.toFixed(digits)}%`;
  }

  function fmtMoney(v) {
    if (v == null) return '—';
    return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // ── Sparkline ──────────────────────────────────────────────────────────────
  function Sparkline({ data, color, width = 64, height = 18, strokeWidth = 1.25 }) {
    const path = useMemo(() => {
      if (!data || data.length < 2) return '';
      const lo = Math.min(...data), hi = Math.max(...data);
      const span = hi - lo || 1;
      return data.map((v, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((v - lo) / span) * height;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
      }).join(' ');
    }, [data, width, height]);
    return (
      <svg width={width} height={height} style={{ display: 'block' }}>
        <path d={path} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  // ── Top utility bar ────────────────────────────────────────────────────────
  function UtilityBar({ data, p }) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 32px', borderBottom: `1px solid ${p.rule}`,
        fontFamily: 'JetBrains Mono, ui-monospace, monospace',
        fontSize: 10.5, color: p.inkDim, letterSpacing: '0.04em',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <span>ALPHABOT · v3.2.0</span>
          <span style={{ color: p.inkFaint }}>·</span>
          <span>NODE local · Flask 3.0</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <ModeChip mode={data.meta.mode} p={p} />
          <span style={{ color: p.inkFaint }}>·</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{
              width: 7, height: 7, borderRadius: 7,
              background: data.meta.system_online ? p.pos : p.neg,
              boxShadow: data.meta.system_online ? `0 0 0 3px ${p.pos}22` : 'none',
            }} />
            SYSTEM ONLINE
          </span>
          <span style={{ color: p.inkFaint }}>·</span>
          <span>NEXT RUN {data.meta.next_run}</span>
          <span style={{ color: p.inkFaint }}>·</span>
          <span style={{ color: p.ink }}>{data.meta.clock_et}</span>
        </div>
      </div>
    );
  }

  function ModeChip({ mode, p }) {
    const isLive = mode === 'LIVE';
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '3px 8px',
        border: `1px solid ${isLive ? p.neg : p.ruleStrong}`,
        color: isLive ? p.neg : p.ink,
        background: isLive ? `${p.neg}10` : 'transparent',
        fontWeight: 600, letterSpacing: '0.12em',
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: 6, background: isLive ? p.neg : p.inkDim,
        }} />
        {mode}
      </span>
    );
  }

  // ── Masthead ───────────────────────────────────────────────────────────────
  function Masthead({ data, p }) {
    const m = data.meta;
    const totalAlpha = data.symphonies
      .filter(s => s.guard_alpha != null)
      .reduce((a, s) => a + s.guard_alpha, 0);

    return (
      <div style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 60,
        padding: '32px 32px 28px', borderBottom: `1px solid ${p.rule}`,
        alignItems: 'end',
      }}>
        <div>
          <div style={{
            fontFamily: '"Source Serif 4", Georgia, serif',
            fontWeight: 500, fontSize: 64, lineHeight: 0.95,
            letterSpacing: '-0.025em', color: p.ink,
            display: 'flex', alignItems: 'baseline', gap: 14,
          }}>
            <span>AlphaBot</span>
            <span style={{
              fontSize: 18, fontWeight: 400, color: p.accent,
              fontFamily: '"Source Serif 4", serif', fontStyle: 'italic',
              letterSpacing: 0,
            }}>
              guard
            </span>
          </div>
          <div style={{
            marginTop: 10, fontSize: 12, color: p.inkDim,
            letterSpacing: '0.03em', display: 'flex', gap: 14, alignItems: 'center',
          }}>
            <span>Risk engine for Composer symphonies</span>
            <span style={{ color: p.inkFaint }}>·</span>
            <span style={{
              fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
              padding: '2px 8px', border: `1px solid ${p.rule}`, color: p.ink,
            }}>
              {m.account.label}<span style={{ color: p.inkFaint }}> · {m.account.uuid_short}</span>
            </span>
          </div>
        </div>

        {/* KPI row */}
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
          borderLeft: `1px solid ${p.rule}`,
        }}>
          <KPI label="Tracked" value={m.tracked} p={p} />
          <KPI label="Armed" value={m.armed} dotColor={m.armed ? p.warn : null} p={p} />
          <KPI label="Triggered today" value={m.triggered} dotColor={m.triggered ? p.neg : null} p={p} />
          <KPI
            label="Guard alpha"
            value={`${totalAlpha >= 0 ? '+' : ''}${totalAlpha.toFixed(2)}%`}
            valueColor={totalAlpha >= 0 ? p.pos : p.neg}
            footnote="vs if-held"
            p={p}
            big
          />
        </div>
      </div>
    );
  }

  function KPI({ label, value, dotColor, valueColor, footnote, p, big }) {
    return (
      <div style={{
        borderRight: `1px solid ${p.rule}`,
        padding: '6px 24px',
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        minHeight: 92,
      }}>
        <div style={{
          fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase',
          color: p.inkDim, fontWeight: 500, marginBottom: 6,
          display: 'flex', alignItems: 'center', gap: 7,
        }}>
          {dotColor && <span style={{ width: 6, height: 6, borderRadius: 6, background: dotColor }} />}
          {label}
        </div>
        <div style={{
          fontFamily: '"Source Serif 4", Georgia, serif',
          fontWeight: 500, fontSize: big ? 38 : 44, lineHeight: 1,
          color: valueColor || p.ink, letterSpacing: '-0.02em',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {value}
        </div>
        {footnote && (
          <div style={{
            marginTop: 6, fontSize: 10, color: p.inkFaint, letterSpacing: '0.04em',
          }}>
            {footnote}
          </div>
        )}
      </div>
    );
  }

  // ── Status banner + action row ─────────────────────────────────────────────
  function StatusBanner({ data, p }) {
    const m = data.meta;
    const tt = m.triggers_today;
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 32px', borderBottom: `1px solid ${p.rule}`,
        background: p.paper,
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          fontSize: 11, color: p.ink, letterSpacing: '0.06em',
          textTransform: 'uppercase', fontWeight: 500,
        }}>
          <span style={{ width: 7, height: 7, borderRadius: 7, background: p.inkFaint }} />
          {m.market_state_label}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <span style={{
            fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase',
            color: p.inkDim, fontWeight: 500,
          }}>
            Today's triggers
          </span>
          <TriggerChip label="Trailing stop" count={tt.trailing_stop} color={p.neg} p={p} />
          <TriggerChip label="Take-profit" count={tt.take_profit} color={p.plum} p={p} />
        </div>
      </div>
    );
  }

  function TriggerChip({ label, count, color, p }) {
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        padding: '4px 10px 4px 8px',
        border: `1px solid ${color}55`,
        color: p.ink, fontSize: 11, letterSpacing: '0.05em',
      }}>
        <span style={{ width: 6, height: 6, borderRadius: 6, background: color }} />
        {label}
        <span style={{
          fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, color, marginLeft: 2,
        }}>×{count}</span>
      </span>
    );
  }

  function ActionBar({ p }) {
    const items = [
      { label: 'Force run', primary: true },
      { label: 'EOD analysis' },
      { label: 'Push Discord' },
      { label: 'History' },
      { label: 'Edit variables' },
      { label: 'Performance' },
      { label: 'AI advisor' },
      { label: 'Go to cash', danger: true },
    ];
    return (
      <div style={{
        display: 'flex', borderBottom: `1px solid ${p.rule}`,
      }}>
        {items.map((it, i) => (
          <button key={it.label} style={{
            flex: 1, padding: '12px 8px',
            background: 'transparent', border: 'none',
            borderRight: i < items.length - 1 ? `1px solid ${p.rule}` : 'none',
            fontFamily: 'Geist, sans-serif', fontSize: 10.5, fontWeight: 600,
            color: it.danger ? p.neg : (it.primary ? p.accent : p.ink),
            letterSpacing: '0.12em', textTransform: 'uppercase',
            cursor: 'pointer',
          }}>
            {it.label}
          </button>
        ))}
      </div>
    );
  }

  // ── Ledger ─────────────────────────────────────────────────────────────────
  function Ledger({ data, p, t }) {
    const compact = t.numFormat === 'compact';
    const dense = t.density === 'compact';
    const roomy = t.density === 'roomy';
    const rowPad = dense ? '8px 16px' : roomy ? '16px 16px' : '12px 16px';

    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Section header */}
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
          padding: '22px 32px 14px',
        }}>
          <div style={{
            fontFamily: '"Source Serif 4", serif', fontSize: 22, fontWeight: 500,
            letterSpacing: '-0.01em', color: p.ink,
          }}>
            Symphony ledger
            <span style={{
              fontFamily: 'Geist', fontSize: 11, fontWeight: 400, color: p.inkDim,
              marginLeft: 12, letterSpacing: '0.02em',
            }}>
              {data.symphonies.length} symphonies · data as of {data.meta.data_as_of}
            </span>
          </div>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
            color: p.inkFaint, letterSpacing: '0.05em',
          }}>
            last update {data.meta.last_updated}
          </div>
        </div>

        {/* Table */}
        <div style={{ padding: '0 32px', flex: 1, overflow: 'hidden' }}>
          <table style={{
            width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed',
            fontFamily: 'Geist, sans-serif',
          }}>
            <colgroup>
              <col style={{ width: '28%' }} />
              <col style={{ width: '10%' }} />
              {t.mathOverlays && <col style={{ width: '7%' }} />}
              <col style={{ width: '9%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '9%' }} />
              <col style={{ width: '8%' }} />
            </colgroup>
            <thead>
              <tr style={{ borderBottom: `1px solid ${p.ruleStrong}` }}>
                <Th p={p}>Symphony</Th>
                <Th p={p} center>Status</Th>
                {t.mathOverlays && <Th p={p} right>MC%</Th>}
                <Th p={p} right>Stop</Th>
                <Th p={p} right>Return</Th>
                <Th p={p} right>Peak</Th>
                <Th p={p} right>If held · α</Th>
                <Th p={p} right>Today</Th>
                <Th p={p} center>Tape</Th>
              </tr>
            </thead>
            <tbody>
              {data.symphonies.map((s) => {
                const sc = statusColor(s, p);
                const ret = s.triggered ? s.triggered_at_return : s.current_return;
                const retColor = ret >= 0 ? p.pos : p.neg;
                const ihRet = s.current_return;
                const ihColor = ihRet >= 0 ? p.pos : p.neg;
                const alpha = s.guard_alpha;
                const peak = s.shadow_hwm;
                const tcDR = s.tc_dr;
                const tcColor = tcDR != null ? (tcDR >= 0 ? p.pos : p.neg) : p.inkFaint;

                return (
                  <tr key={s.id} style={{ borderBottom: `1px solid ${p.rule}` }}>
                    <td style={{ padding: rowPad, fontSize: 13, color: p.ink }}>
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        <span style={{
                          width: 3, height: 18, background: sc,
                          flexShrink: 0,
                        }} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
                      </div>
                    </td>
                    <td style={{ padding: rowPad, textAlign: 'center' }}>
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', gap: 6,
                        fontSize: 10, fontWeight: 600, letterSpacing: '0.1em',
                        color: sc,
                      }}>
                        <span style={{ width: 6, height: 6, borderRadius: 6, background: sc }} />
                        {statusLabel(s)}
                      </span>
                      {s.triggered && s.triggered_at_time && (
                        <div style={{
                          fontFamily: 'JetBrains Mono, monospace',
                          fontSize: 9.5, color: p.inkFaint, marginTop: 2,
                        }}>
                          ⌟ {s.triggered_at_time} ET
                        </div>
                      )}
                    </td>
                    {t.mathOverlays && (
                      <td style={{
                        padding: rowPad, textAlign: 'right',
                        fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                        color: s.mc_prob < 15 ? p.warn : (s.mc_prob > 80 ? p.inkDim : p.ink),
                        fontVariantNumeric: 'tabular-nums',
                      }}>
                        {s.mc_prob != null ? s.mc_prob.toFixed(1) : '—'}
                      </td>
                    )}
                    <td style={{
                      padding: rowPad, textAlign: 'right',
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                      color: p.inkDim,
                      fontVariantNumeric: 'tabular-nums',
                    }}>
                      {s.breakeven_locked && <span style={{ marginRight: 4, color: p.accent }}>◆</span>}
                      {fmtPct(s.stop_trigger, compact)}
                    </td>
                    <td style={{
                      padding: rowPad, textAlign: 'right',
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 13, fontWeight: 500,
                      color: retColor,
                      fontVariantNumeric: 'tabular-nums',
                    }}>
                      {fmtPct(ret, compact, { signed: true })}
                    </td>
                    <td style={{
                      padding: rowPad, textAlign: 'right',
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                      color: peak > 0 ? p.pos : p.inkDim,
                      fontVariantNumeric: 'tabular-nums',
                    }}>
                      {fmtPct(peak, compact)}
                    </td>
                    <td style={{
                      padding: rowPad, textAlign: 'right',
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                      fontVariantNumeric: 'tabular-nums',
                    }}>
                      <div style={{ color: ihColor }}>
                        {fmtPct(ihRet, compact, { signed: true })}
                      </div>
                      {alpha != null && (
                        <div style={{
                          fontSize: 10, marginTop: 1,
                          color: alpha >= 0 ? p.pos : p.neg,
                        }}>
                          {alpha > 0 ? '+' : ''}{alpha.toFixed(2)}α
                        </div>
                      )}
                    </td>
                    <td style={{
                      padding: rowPad, textAlign: 'right',
                      fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
                      color: tcColor,
                      fontVariantNumeric: 'tabular-nums',
                    }}>
                      {fmtPct(tcDR, compact, { signed: true })}
                    </td>
                    <td style={{ padding: rowPad, textAlign: 'center' }}>
                      <div style={{ display: 'inline-block' }}>
                        <Sparkline
                          data={s.sparkline}
                          color={s.triggered ? p.inkDim : (ret >= 0 ? p.pos : p.neg)}
                          width={64}
                          height={16}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        <div style={{
          padding: '16px 32px', borderTop: `1px solid ${p.rule}`,
          display: 'flex', justifyContent: 'space-between',
          fontFamily: 'JetBrains Mono, monospace', fontSize: 10,
          color: p.inkFaint, letterSpacing: '0.04em',
        }}>
          <span>account value ${fmtMoney(data.meta.portfolio.account_value)} · cash ${fmtMoney(data.meta.portfolio.available_cash)}</span>
          <span>cumulative {data.meta.portfolio.cumulative_return.toFixed(2)}% · sharpe {data.meta.portfolio.sharpe.toFixed(2)} · maxDD {data.meta.portfolio.max_drawdown.toFixed(2)}%</span>
        </div>
      </div>
    );
  }

  function Th({ children, right, center, p }) {
    return (
      <th style={{
        padding: '12px 16px',
        textAlign: right ? 'right' : center ? 'center' : 'left',
        fontWeight: 500, fontSize: 10, letterSpacing: '0.14em',
        textTransform: 'uppercase', color: p.inkDim,
        fontFamily: 'Geist, sans-serif',
      }}>
        {children}
      </th>
    );
  }

  // ── Main ───────────────────────────────────────────────────────────────────
  function Bureau({ data, t }) {
    const p = palette(t.theme, t.accent);
    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'Geist, ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <UtilityBar data={data} p={p} />
        <Masthead data={data} p={p} />
        <StatusBanner data={data} p={p} />
        <ActionBar p={p} />
        <Ledger data={data} p={p} t={t} />
      </div>
    );
  }

  window.Bureau = Bureau;
})();
