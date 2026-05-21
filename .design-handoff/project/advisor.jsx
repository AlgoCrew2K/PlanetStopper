// /ai-advisor route — Claude-driven config suggestions for a chosen symphony.
// Operator must accept each one; the gates (allowlist, risk direction, OOS
// revalidation, locked-var guard) live in app.py.

(function () {
  const { useState, useMemo } = React;
  const { TopBar, PageHeader, Select } = window;

  function Advisor({ data, t, onNavigate, onForceRun }) {
    const p = window.studioPalette(t.theme, t.accent);
    const adv = data.advisor;
    const [symId, setSymId] = useState(adv.target_symphony);

    const symName = data.symphonies.find(s => s.id === symId)?.name || symId;

    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'var(--studio-sans, Manrope), ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <TopBar data={data} p={p} route="advisor" onNavigate={onNavigate} onForceRun={onForceRun} />
        <PageHeader
          title="AI advisor"
          subtitle="Claude-driven parameter tuning · every change requires operator approval and passes through four safety gates"
          p={p}
          right={
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10 }}>
              <div>
                <window.FieldLabel p={p}>Symphony</window.FieldLabel>
                <Select
                  value={symId}
                  options={data.symphonies.map(s => ({
                    value: s.id, label: s.name.length > 38 ? s.name.slice(0, 38) + '…' : s.name,
                  }))}
                  onChange={setSymId}
                  p={p}
                  minWidth={320}
                />
              </div>
              <button style={{
                padding: '7px 14px',
                background: p.accent, color: '#fff',
                border: 'none', borderRadius: 4, cursor: 'pointer',
                fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 12, fontWeight: 700,
                letterSpacing: '0.02em',
              }}>
                Run Claude advisor
              </button>
            </div>
          }
        />

        <div style={{ flex: 1, overflow: 'auto' }}>
          <div style={{
            padding: '20px 28px 14px',
            display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
          }}>
            <span style={{
              fontSize: 11.5, color: p.inkDim,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            }}>
              Last run · {new Date(adv.last_run_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET
            </span>
            <span style={{
              padding: '3px 10px', background: p.chipBg, border: `1px solid ${p.rule}`,
              borderRadius: 999, fontSize: 11, color: p.ink, fontWeight: 600,
            }}>
              {adv.suggestions.length} suggestions
            </span>
            <span style={{
              padding: '3px 10px', background: `${p.pos}14`, border: `1px solid ${p.pos}55`,
              borderRadius: 999, fontSize: 11, color: p.pos, fontWeight: 600,
            }}>
              {adv.suggestions.filter(s => s.oos_status === 'passed').length} OOS-passed
            </span>
            <span style={{
              padding: '3px 10px', background: `${p.neg}14`, border: `1px solid ${p.neg}55`,
              borderRadius: 999, fontSize: 11, color: p.neg, fontWeight: 600,
            }}>
              {adv.suggestions.filter(s => s.oos_status === 'rejected').length} OOS-rejected
            </span>
            <span style={{ flex: 1 }} />
            <span style={{
              fontSize: 10.5, color: p.inkFaint,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif', letterSpacing: '0.04em',
              fontVariantNumeric: 'tabular-nums',
            }}>
              symphony: {symName.length > 60 ? symName.slice(0, 60) + '…' : symName}
            </span>
          </div>

          <div style={{
            display: 'grid', gridTemplateColumns: '1.6fr 1fr',
            gap: 14, padding: '4px 28px 28px',
          }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {adv.suggestions.map(s => (
                <SuggestionCard key={s.config_key} sug={s} p={p} t={t} />
              ))}
            </div>
            <AutotuneRuns runs={adv.autotune_runs} symphonies={data.symphonies} p={p} />
          </div>
        </div>
      </div>
    );
  }

  function SuggestionCard({ sug, p, t }) {
    const direction = sug.suggested_value > sug.current_value ? 'up' : 'down';
    const oosBadge = {
      passed:   { color: p.pos,  label: 'OOS · passed' },
      rejected: { color: p.neg,  label: 'OOS · rejected' },
      pending:  { color: p.warn, label: 'OOS · pending' },
    }[sug.oos_status];
    const confColor = {
      high: p.pos, medium: p.warn, low: p.inkFaint,
    }[sug.confidence];
    const isRejected = sug.oos_status === 'rejected';
    const digits = sug.config_key.includes('VELOCITY') || sug.config_key.includes('SQUEEZE') || sug.config_key.includes('FLOOR') ? 2 : 1;

    return (
      <div style={{
        background: p.paper,
        border: `1px solid ${isRejected ? p.neg + '40' : p.rule}`,
        padding: '16px 18px',
        display: 'flex', flexDirection: 'column', gap: 12,
        position: 'relative', overflow: 'hidden',
        opacity: isRejected ? 0.7 : 1,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap',
            }}>
              <span style={{
                fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                fontSize: 14, fontWeight: 700, color: p.ink,
                letterSpacing: '-0.005em',
                fontVariantNumeric: 'tabular-nums',
              }}>
                {sug.config_key}
              </span>
              <Badge color={confColor} label={`${sug.confidence} confidence`} p={p} />
              <Badge color={oosBadge.color} label={oosBadge.label} p={p} solid={sug.oos_status !== 'pending'} />
              {sug.data_sufficiency !== 'sufficient' && (
                <Badge color={p.warn} label={`${sug.data_sufficiency} data`} p={p} />
              )}
            </div>

            <div style={{
              marginTop: 12,
              display: 'flex', alignItems: 'center', gap: 14,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            }}>
              <ValueBlock label="Current" value={sug.current_value.toFixed(digits)} p={p} />
              <span style={{
                fontSize: 22, color: direction === 'up' ? p.pos : p.warn, fontWeight: 700,
              }}>
                →
              </span>
              <ValueBlock label="Suggested" value={sug.suggested_value.toFixed(digits)} highlighted color={direction === 'up' ? p.pos : p.warn} p={p} />
              <div style={{ flex: 1 }} />
              <div style={{ textAlign: 'right' }}>
                <div style={{
                  fontSize: 9.5, fontWeight: 700, color: p.inkDim,
                  letterSpacing: '0.12em', textTransform: 'uppercase',
                }}>
                  Projected {sug.impact.metric.replace('_', ' ')}
                </div>
                <div style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: 18, fontWeight: 700,
                  color: sug.impact.delta >= 0 ? p.pos : p.neg,
                  fontVariantNumeric: 'tabular-nums', marginTop: 2,
                }}>
                  {sug.impact.delta >= 0 ? '+' : ''}{sug.impact.delta.toFixed(2)}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 12.5, lineHeight: 1.55, color: p.inkDim,
          paddingTop: 12, borderTop: `1px solid ${p.rule}`,
        }}>
          <span style={{ color: p.ink, fontWeight: 600 }}>Rationale.</span>{' '}
          {sug.rationale}
          {sug.oos_reason && (
            <div style={{ marginTop: 8, color: p.neg, fontWeight: 500 }}>
              ✕ {sug.oos_reason}
            </div>
          )}
        </div>

        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          paddingTop: 4,
        }}>
          <span style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: 10.5, color: p.inkFaint, letterSpacing: '0.04em',
            fontWeight: 500,
          }}>
            risk direction · <span style={{ color: p.ink, fontWeight: 600 }}>{sug.risk_direction}</span>
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button style={{
              padding: '6px 12px',
              background: 'transparent', border: `1px solid ${p.rule}`, borderRadius: 4,
              color: p.inkDim, fontSize: 11.5, fontWeight: 600,
              cursor: 'pointer',
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            }}>
              Dismiss
            </button>
            <button disabled={isRejected} style={{
              padding: '6px 14px',
              background: isRejected ? p.chipBg : p.accent,
              border: 'none', borderRadius: 4,
              color: isRejected ? p.inkFaint : '#fff',
              fontSize: 11.5, fontWeight: 700,
              cursor: isRejected ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              letterSpacing: '0.02em',
            }}>
              {isRejected ? 'Blocked by OOS gate' : 'Apply suggestion'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  function ValueBlock({ label, value, highlighted, color, p }) {
    return (
      <div>
        <div style={{
          fontSize: 9, fontWeight: 700, color: p.inkDim,
          letterSpacing: '0.12em', textTransform: 'uppercase',
        }}>
          {label}
        </div>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 24, fontWeight: 700,
          color: highlighted ? color : p.ink, letterSpacing: '-0.02em',
          fontVariantNumeric: 'tabular-nums', marginTop: 2,
        }}>
          {value}
        </div>
      </div>
    );
  }

  function Badge({ color, label, p, solid }) {
    return (
      <span style={{
        padding: '2px 8px',
        background: solid ? color + '14' : 'transparent',
        border: `1px solid ${color}55`,
        borderRadius: 4,
        fontSize: 10, fontWeight: 700,
        color, letterSpacing: '0.08em', textTransform: 'uppercase',
        fontFamily: 'var(--studio-sans, Manrope), sans-serif',
      }}>
        {label}
      </span>
    );
  }

  function AutotuneRuns({ runs, symphonies, p }) {
    const nameByID = useMemo(() => Object.fromEntries(symphonies.map(s => [s.id, s.name])), [symphonies]);
    const decisionColor = (d) => d === 'apply' ? p.pos : d === 'reject' ? p.neg : p.warn;
    return (
      <div style={{
        background: p.paper, border: `1px solid ${p.rule}`,
        display: 'flex', flexDirection: 'column',
      }}>
        <div style={{
          padding: '14px 16px', borderBottom: `1px solid ${p.rule}`,
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        }}>
          <div style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: 13, fontWeight: 700, color: p.ink, letterSpacing: '-0.005em',
          }}>
            Recent autotune runs
          </div>
          <span style={{
            fontSize: 10, color: p.inkFaint, fontWeight: 500,
            letterSpacing: '0.04em', fontVariantNumeric: 'tabular-nums',
          }}>
            optuna · 500 trials/run
          </span>
        </div>
        <div style={{ overflow: 'auto', flex: 1 }}>
          {runs.map((r, i) => {
            const name = nameByID[r.symphony] || r.symphony;
            const short = name.length > 28 ? name.slice(0, 28) + '…' : name;
            return (
              <div key={i} style={{
                padding: '11px 16px',
                borderBottom: i < runs.length - 1 ? `1px solid ${p.rule}` : 'none',
                display: 'flex', flexDirection: 'column', gap: 4,
              }}>
                <div style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                }}>
                  <span style={{
                    fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                    fontSize: 12, fontWeight: 600, color: p.ink,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }} title={name}>
                    {short}
                  </span>
                  <Badge color={decisionColor(r.decision)} label={r.decision} p={p} solid />
                </div>
                <div style={{
                  display: 'flex', justifyContent: 'space-between',
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: 10.5, color: p.inkDim,
                  fontVariantNumeric: 'tabular-nums', letterSpacing: '0.02em',
                }}>
                  <span>{r.ts}</span>
                  <span>
                    sharpe <span style={{ color: p.ink, fontWeight: 600 }}>{r.naive_sharpe.toFixed(2)}</span>
                    {' · '}
                    DSR <span style={{ color: p.ink, fontWeight: 600 }}>{r.dsr.toFixed(2)}</span>
                  </span>
                </div>
                <div style={{
                  fontSize: 10, color: p.inkFaint, letterSpacing: '0.04em', textTransform: 'uppercase',
                  fontWeight: 600,
                }}>
                  Frozen-eval <span style={{
                    color: r.frozen_eval === 'passed' ? p.pos : r.frozen_eval === 'failed' ? p.neg : p.warn,
                  }}>{r.frozen_eval}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  window.Advisor = Advisor;
})();
