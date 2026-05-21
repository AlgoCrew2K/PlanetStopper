// Settings route — Edit Variables panel.
// Sections: Master controls / Algorithm parameters / Credentials (masked) /
// Per-symphony overrides. Mirrors GET/POST /api/settings in app.py.

(function () {
  const { useState } = React;
  const { TopBar, PageHeader, FieldLabel } = window;

  // Algorithm parameter taxonomy — copied from the README + math_engine.py
  // intent so the help text actually means something.
  const PARAM_META = {
    TRIGGER_THRESHOLD_PCT: {
      help: 'Monte Carlo threshold for arming the trailing stop. Lower = bot waits longer before defending.',
      unit: '%', kind: 'pct',
    },
    TAKE_PROFIT_MC_PCT: {
      help: 'Aggressive take-profit threshold once MC probability collapses.',
      unit: '%', kind: 'pct',
    },
    MAX_SQUEEZE_FLOOR: {
      help: 'Tightest the stop distance can shrink under the log-time squeeze.',
      unit: '×', kind: 'mult',
    },
    VWAP_CROSS_HWM_PCT: {
      help: 'Return needed to activate the VWAP Breakdown defense (System A).',
      unit: '%', kind: 'pct',
    },
    VWAP_BLEED_MULTIPLIER: {
      help: 'Multiplier on 20d vol used to set the VWAP Bleed Cut threshold.',
      unit: '×', kind: 'mult',
    },
    VWAP_BLEED_TICKS: {
      help: 'Consecutive ticks required below bleed threshold to trigger.',
      unit: 'ticks', kind: 'int',
    },
    PARABOLIC_VELOCITY_THRESHOLD: {
      help: 'Tick-velocity threshold that arms the parabolic squeeze ratchet.',
      unit: '', kind: 'decimal',
    },
    MAX_PARABOLIC_SQUEEZE: {
      help: 'Stop tightening once parabolic squeeze is armed or breakeven locked.',
      unit: '×', kind: 'mult',
    },
  };

  function Settings({ data, t, onNavigate, onForceRun }) {
    const p = window.studioPalette(t.theme, t.accent);
    const s = data.settings;
    const [section, setSection] = useState('master');
    const [dirty, setDirty] = useState(false);

    const sections = [
      { id: 'master',     label: 'Master controls' },
      { id: 'algorithm',  label: 'Algorithm parameters' },
      { id: 'credentials',label: 'Credentials' },
      { id: 'symphonies', label: 'Symphony overrides' },
    ];

    return (
      <div style={{
        width: '100%', height: '100%',
        background: p.bg, color: p.ink,
        fontFamily: 'var(--studio-sans, Manrope), ui-sans-serif, system-ui, sans-serif',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <TopBar data={data} p={p} route="settings" onNavigate={onNavigate} onForceRun={onForceRun} />
        <PageHeader
          title="Settings"
          subtitle=".env globals + SQLite-isolated symphony strategies · live edits, no restart"
          p={p}
          right={
            <div style={{ display: 'flex', gap: 8 }}>
              <button disabled={!dirty} style={{
                padding: '7px 13px',
                background: 'transparent', border: `1px solid ${p.rule}`,
                borderRadius: 4, cursor: dirty ? 'pointer' : 'default',
                color: dirty ? p.inkDim : p.inkFaint,
                fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5, fontWeight: 600,
              }}>
                Discard changes
              </button>
              <button disabled={!dirty} style={{
                padding: '7px 14px',
                background: dirty ? p.accent : p.chipBg,
                border: 'none', borderRadius: 4,
                color: dirty ? '#fff' : p.inkFaint,
                fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontSize: 11.5, fontWeight: 700,
                letterSpacing: '0.02em', cursor: dirty ? 'pointer' : 'default',
              }}>
                Save changes
              </button>
            </div>
          }
        />

        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          {/* Side nav */}
          <aside style={{
            width: 220, flexShrink: 0,
            borderRight: `1px solid ${p.rule}`,
            background: p.paper,
            padding: '20px 0',
          }}>
            {sections.map(sec => {
              const active = sec.id === section;
              return (
                <button key={sec.id} onClick={() => setSection(sec.id)} style={{
                  display: 'block', width: '100%',
                  padding: '10px 22px',
                  background: 'transparent',
                  border: 'none', borderLeft: `2px solid ${active ? p.accent : 'transparent'}`,
                  textAlign: 'left', cursor: 'pointer',
                  color: active ? p.ink : p.inkDim,
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: 13, fontWeight: active ? 700 : 500,
                  letterSpacing: '-0.005em',
                }}>
                  {sec.label}
                </button>
              );
            })}

            <div style={{
              margin: '20px 22px 0', paddingTop: 18,
              borderTop: `1px solid ${p.rule}`,
              fontSize: 11, color: p.inkFaint,
              lineHeight: 1.5, fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            }}>
              All changes are written to .env or alphabot_state.db immediately
              on save. The minute scheduler picks them up on the next tick.
            </div>
          </aside>

          {/* Content */}
          <main style={{ flex: 1, overflow: 'auto', padding: '28px 32px' }}>
            {section === 'master' && <MasterControls s={s} p={p} setDirty={setDirty} />}
            {section === 'algorithm' && <AlgorithmParams s={s} p={p} setDirty={setDirty} />}
            {section === 'credentials' && <Credentials s={s} p={p} setDirty={setDirty} />}
            {section === 'symphonies' && <SymphonyOverrides s={s} data={data} p={p} setDirty={setDirty} />}
          </main>
        </div>
      </div>
    );
  }

  // ── Master controls — LIVE_EXECUTION (kill switch) + start time ──────────
  function MasterControls({ s, p, setDirty }) {
    const [live, setLive] = useState(s.globals.LIVE_EXECUTION === 'True');
    return (
      <div style={{ maxWidth: 760 }}>
        <SectionHeader title="Master controls" subtitle="The two switches that govern real-money execution. Treat both with respect." p={p} />

        <div style={{
          background: live ? `${p.neg}10` : p.paper,
          border: `1px solid ${live ? p.neg + '55' : p.rule}`,
          padding: 22, marginBottom: 16,
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 24 }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: 16, fontWeight: 700, color: p.ink,
                }}>
                  Live execution
                </span>
                <span style={{
                  padding: '2px 8px',
                  background: live ? p.neg : p.chipBg,
                  border: `1px solid ${live ? p.neg : p.rule}`,
                  color: live ? '#fff' : p.inkDim,
                  borderRadius: 4, fontSize: 10, fontWeight: 700, letterSpacing: '0.1em',
                }}>
                  {live ? 'LIVE' : 'DRY RUN'}
                </span>
              </div>
              <div style={{
                marginTop: 6, fontSize: 13, color: p.inkDim, lineHeight: 1.55,
              }}>
                When <strong style={{ color: p.ink }}>LIVE</strong>, the bot fires real{' '}
                <code style={{ fontSize: 12, padding: '1px 5px', background: p.chipBg, borderRadius: 2 }}>
                  POST /go-to-cash
                </code>{' '}
                requests to Composer the moment a stop is hit. <strong style={{ color: p.ink }}>DRY RUN</strong>{' '}
                tracks identical math but never sends an order — used to validate parameters before flipping the switch.
              </div>
              {live && (
                <div style={{
                  marginTop: 12, padding: '10px 14px',
                  background: p.neg + '14', border: `1px solid ${p.neg}55`,
                  color: p.neg, fontSize: 12, fontWeight: 600, lineHeight: 1.4,
                }}>
                  ⚠ Real-money mode. Every trigger will send a liquidation to Composer.
                  Discord receives an audit alert on every panic-stop call regardless of live state.
                </div>
              )}
            </div>
            <Toggle on={live} onChange={(v) => { setLive(v); setDirty(true); }} p={p} />
          </div>
        </div>

        <Row p={p}>
          <FieldLabel p={p}>Execution start time (ET)</FieldLabel>
          <TextInput value={s.globals.EXECUTION_START_TIME} suffix="ET" p={p} onChange={() => setDirty(true)} />
          <Hint p={p}>The minute the scheduler begins monitoring stops. Market opens 09:30; earlier values stage state pre-open.</Hint>
        </Row>
      </div>
    );
  }

  // ── Algorithm parameters ─────────────────────────────────────────────────
  function AlgorithmParams({ s, p, setDirty }) {
    return (
      <div style={{ maxWidth: 880 }}>
        <SectionHeader
          title="Algorithm parameters"
          subtitle="Global defaults applied to every symphony. Per-symphony overrides in the next section take precedence."
          p={p}
        />

        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12,
        }}>
          {Object.entries(s.globals)
            .filter(([k]) => PARAM_META[k])
            .map(([k, v]) => (
              <ParamRow key={k} name={k} value={v} meta={PARAM_META[k]} p={p} onChange={() => setDirty(true)} />
            ))}
        </div>
      </div>
    );
  }

  function ParamRow({ name, value, meta, p, onChange }) {
    return (
      <div style={{
        background: p.paper, border: `1px solid ${p.rule}`,
        padding: '14px 16px',
      }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 11.5, color: p.ink, fontWeight: 700,
          letterSpacing: '0.04em', fontVariantNumeric: 'tabular-nums',
          marginBottom: 8,
        }}>
          {name}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          <input
            defaultValue={value}
            onChange={onChange}
            style={{
              flex: 1, padding: '7px 10px',
              background: p.bg, border: `1px solid ${p.rule}`, borderRadius: 4,
              color: p.ink, fontSize: 13,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              fontWeight: 600, fontVariantNumeric: 'tabular-nums',
              outline: 'none',
            }}
          />
          {meta.unit && (
            <span style={{
              marginLeft: 8, fontSize: 12, color: p.inkDim, fontWeight: 600,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              letterSpacing: '0.02em',
            }}>
              {meta.unit}
            </span>
          )}
        </div>
        <div style={{
          marginTop: 8, fontSize: 11.5, color: p.inkDim, lineHeight: 1.45,
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
        }}>
          {meta.help}
        </div>
      </div>
    );
  }

  // ── Credentials (masked) ─────────────────────────────────────────────────
  function Credentials({ s, p, setDirty }) {
    return (
      <div style={{ maxWidth: 760 }}>
        <SectionHeader
          title="Credentials"
          subtitle="Keys + account UUIDs + webhook URLs. Values are write-only — the server returns masked placeholders so secrets never round-trip through the browser."
          p={p}
        />

        <div style={{
          background: p.warn + '0e', border: `1px solid ${p.warn}55`,
          padding: '10px 14px',
          color: p.ink, fontSize: 12, lineHeight: 1.5, marginBottom: 18,
        }}>
          <strong style={{ color: p.warn }}>Heads up.</strong>{' '}
          Saving any of these fields writes the new value to <code style={{ fontSize: 11, padding: '1px 4px', background: p.chipBg, borderRadius: 2 }}>.env</code>{' '}
          immediately. Blank fields are ignored (existing value preserved).
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <CredGroup title="Composer" p={p}>
            <CredField name="COMPOSER_KEY_ID"  current={s.secrets.COMPOSER_KEY_ID}  p={p} onChange={setDirty} />
            <CredField name="COMPOSER_SECRET"  current={s.secrets.COMPOSER_SECRET}  p={p} onChange={setDirty} />
            <CredField name="ACCOUNT_INDIVIDUAL" current={s.secrets.ACCOUNT_INDIVIDUAL} p={p} onChange={setDirty} />
            <CredField name="ACCOUNT_ROTH"      current={s.secrets.ACCOUNT_ROTH}     p={p} onChange={setDirty} />
            <CredField name="ACCOUNT_TRAD"      current={s.secrets.ACCOUNT_TRAD}     p={p} onChange={setDirty} />
          </CredGroup>
          <CredGroup title="Alpaca · price data" p={p}>
            <CredField name="ALPACA_KEY"     current={s.secrets.ALPACA_KEY}    p={p} onChange={setDirty} />
            <CredField name="ALPACA_SECRET"  current={s.secrets.ALPACA_SECRET} p={p} onChange={setDirty} />
          </CredGroup>
          <CredGroup title="Discord + Anthropic" p={p}>
            <CredField name="DISCORD_WEBHOOK_URL" current={s.secrets.DISCORD_WEBHOOK_URL} p={p} onChange={setDirty} />
            <CredField name="ANTHROPIC_API_KEY"   current={s.secrets.ANTHROPIC_API_KEY}   p={p} onChange={setDirty} />
          </CredGroup>
        </div>
      </div>
    );
  }

  function CredGroup({ title, children, p }) {
    return (
      <div style={{ background: p.paper, border: `1px solid ${p.rule}` }}>
        <div style={{
          padding: '10px 16px', borderBottom: `1px solid ${p.rule}`,
          fontSize: 10, fontWeight: 700, color: p.inkDim,
          letterSpacing: '0.12em', textTransform: 'uppercase',
        }}>
          {title}
        </div>
        <div style={{ padding: '4px 0' }}>{children}</div>
      </div>
    );
  }

  function CredField({ name, current, p, onChange }) {
    const [reveal, setReveal] = useState(false);
    const [val, setVal] = useState('');
    return (
      <div style={{
        padding: '10px 16px',
        display: 'grid', gridTemplateColumns: '180px 1fr 64px 80px', gap: 10,
        alignItems: 'center',
        borderBottom: `1px solid ${p.rule}`,
      }}>
        <span style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 11.5, color: p.inkDim, fontWeight: 600,
          letterSpacing: '0.04em', fontVariantNumeric: 'tabular-nums',
        }}>
          {name}
        </span>
        <input
          type={reveal ? 'text' : 'password'}
          placeholder={current}
          value={val}
          onChange={(e) => { setVal(e.target.value); onChange?.(); }}
          style={{
            padding: '7px 10px',
            background: p.bg, border: `1px solid ${p.rule}`, borderRadius: 4,
            color: p.ink, fontSize: 12.5,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontWeight: 500, fontVariantNumeric: 'tabular-nums',
            outline: 'none', letterSpacing: '0.04em',
          }}
        />
        <button onClick={() => setReveal(r => !r)} style={{
          padding: '5px 9px', background: 'transparent', border: `1px solid ${p.rule}`,
          borderRadius: 4, cursor: 'pointer',
          color: p.inkDim, fontSize: 10.5, fontWeight: 600,
          letterSpacing: '0.06em',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
        }}>
          {reveal ? 'hide' : 'show'}
        </button>
        <span style={{
          fontSize: 10.5, color: val ? p.pos : p.inkFaint, fontWeight: 600,
          letterSpacing: '0.04em', textAlign: 'right',
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
        }}>
          {val ? '↑ ready to save' : 'unchanged'}
        </span>
      </div>
    );
  }

  // ── Symphony overrides ───────────────────────────────────────────────────
  function SymphonyOverrides({ s, data, p, setDirty }) {
    const [selectedId, setSelectedId] = useState(data.symphonies[0].id);
    const selected = data.symphonies.find(x => x.id === selectedId);
    const override = s.symphony_overrides[selectedId] || { params: {}, locked_vars: [] };

    return (
      <div style={{ maxWidth: 1120 }}>
        <SectionHeader
          title="Per-symphony overrides"
          subtitle="Override globals or lock parameters against autotuner changes. Stored in alphabot_state.db, keyed on normalized symphony name."
          p={p}
        />

        <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16 }}>
          {/* Symphony list */}
          <div style={{
            background: p.paper, border: `1px solid ${p.rule}`,
            maxHeight: 540, overflow: 'auto',
          }}>
            {data.symphonies.map(sym => {
              const ov = s.symphony_overrides[sym.id];
              const active = sym.id === selectedId;
              const hasOverride = ov && Object.keys(ov.params || {}).length > 0;
              return (
                <button
                  key={sym.id}
                  onClick={() => setSelectedId(sym.id)}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    padding: '11px 14px',
                    background: active ? p.bg : 'transparent',
                    border: 'none',
                    borderLeft: `2px solid ${active ? p.accent : 'transparent'}`,
                    borderBottom: `1px solid ${p.rule}`,
                    cursor: 'pointer',
                    fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  }}
                >
                  <div style={{
                    fontSize: 12, fontWeight: active ? 700 : 600, color: p.ink,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }} title={sym.name}>
                    {sym.name}
                  </div>
                  <div style={{
                    marginTop: 2, fontSize: 10, color: p.inkFaint, fontWeight: 500,
                    letterSpacing: '0.04em',
                    display: 'flex', gap: 6,
                  }}>
                    {hasOverride
                      ? <span style={{ color: p.accent, fontWeight: 700 }}>◆ {Object.keys(ov.params).length} override{Object.keys(ov.params).length > 1 ? 's' : ''}</span>
                      : <span>uses globals</span>
                    }
                    {ov && ov.locked_vars && ov.locked_vars.length > 0 && (
                      <span style={{ color: p.warn }}>· {ov.locked_vars.length} locked</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Override editor */}
          <div style={{
            background: p.paper, border: `1px solid ${p.rule}`,
            padding: '18px 20px',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 14 }}>
              <div>
                <div style={{
                  fontFamily: 'var(--studio-sans, Manrope), sans-serif',
                  fontSize: 16, fontWeight: 700, color: p.ink, letterSpacing: '-0.01em',
                }}>
                  {selected.name}
                </div>
                <div style={{
                  fontSize: 10.5, color: p.inkFaint, marginTop: 2,
                  letterSpacing: '0.04em', fontVariantNumeric: 'tabular-nums',
                }}>
                  {selected.id} · normalized: {selected.id.replace('sym_', '')}
                </div>
              </div>
              <button style={{
                padding: '6px 11px', background: 'transparent',
                border: `1px solid ${p.rule}`, borderRadius: 4,
                color: p.inkDim, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              }}>
                Reset to globals
              </button>
            </div>

            <div style={{
              marginTop: 16, paddingTop: 16, borderTop: `1px solid ${p.rule}`,
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10,
            }}>
              {Object.entries(s.globals)
                .filter(([k]) => PARAM_META[k])
                .map(([k, v]) => {
                  const ovVal = override.params[k];
                  const locked = override.locked_vars && override.locked_vars.includes(k);
                  return (
                    <OverrideRow
                      key={k}
                      name={k}
                      global={v}
                      override={ovVal}
                      locked={locked}
                      meta={PARAM_META[k]}
                      p={p}
                      onChange={() => setDirty(true)}
                    />
                  );
                })}
            </div>
          </div>
        </div>
      </div>
    );
  }

  function OverrideRow({ name, global: gVal, override, locked, meta, p, onChange }) {
    const hasOverride = override != null;
    return (
      <div style={{
        padding: '11px 14px',
        background: hasOverride ? p.accent + '0d' : p.bg,
        border: `1px solid ${hasOverride ? p.accent + '40' : p.rule}`,
      }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
          marginBottom: 7,
        }}>
          <span style={{
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontSize: 11, color: p.ink, fontWeight: 700,
            letterSpacing: '0.04em', fontVariantNumeric: 'tabular-nums',
          }}>
            {name}
          </span>
          <button onClick={onChange} style={{
            padding: '1px 7px', background: locked ? p.warn + '20' : 'transparent',
            border: `1px solid ${locked ? p.warn + '55' : p.rule}`,
            borderRadius: 4, cursor: 'pointer',
            color: locked ? p.warn : p.inkDim, fontSize: 9.5, fontWeight: 700,
            letterSpacing: '0.08em', textTransform: 'uppercase',
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          }}>
            {locked ? '◆ locked' : 'lock'}
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 10, color: p.inkFaint, letterSpacing: '0.04em', minWidth: 50,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif', fontWeight: 600,
          }}>
            global
          </span>
          <span style={{
            flex: 1, padding: '4px 8px',
            background: p.chipBg, border: `1px solid ${p.rule}`,
            borderRadius: 3, fontSize: 11.5, color: p.inkDim,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontVariantNumeric: 'tabular-nums',
          }}>
            {gVal}{meta.unit && ' ' + meta.unit}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 5 }}>
          <span style={{
            fontSize: 10, color: hasOverride ? p.accent : p.inkFaint,
            letterSpacing: '0.04em', minWidth: 50, fontWeight: 700,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          }}>
            override
          </span>
          <input
            defaultValue={hasOverride ? override : ''}
            placeholder="—"
            onChange={onChange}
            style={{
              flex: 1, padding: '4px 8px',
              background: p.paper, border: `1px solid ${hasOverride ? p.accent + '60' : p.rule}`,
              borderRadius: 3, fontSize: 11.5, color: p.ink, fontWeight: 600,
              fontFamily: 'var(--studio-sans, Manrope), sans-serif',
              fontVariantNumeric: 'tabular-nums',
              outline: 'none',
            }}
          />
        </div>
      </div>
    );
  }

  // ── Bits ─────────────────────────────────────────────────────────────────
  function SectionHeader({ title, subtitle, p }) {
    return (
      <div style={{ marginBottom: 20 }}>
        <div style={{
          fontFamily: 'var(--studio-sans, Manrope), sans-serif',
          fontSize: 22, fontWeight: 700, color: p.ink,
          letterSpacing: '-0.02em', lineHeight: 1.1,
        }}>
          {title}
        </div>
        {subtitle && (
          <div style={{
            marginTop: 6, fontSize: 12.5, color: p.inkDim, lineHeight: 1.5,
            maxWidth: 680,
          }}>
            {subtitle}
          </div>
        )}
      </div>
    );
  }

  function Row({ children, p }) {
    return (
      <div style={{
        background: p.paper, border: `1px solid ${p.rule}`,
        padding: '16px 18px', marginBottom: 12,
      }}>
        {children}
      </div>
    );
  }

  function TextInput({ value, suffix, p, onChange }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <input
          defaultValue={value}
          onChange={onChange}
          style={{
            width: 120, padding: '7px 10px',
            background: p.bg, border: `1px solid ${p.rule}`, borderRadius: 4,
            color: p.ink, fontSize: 13, fontWeight: 600,
            fontFamily: 'var(--studio-sans, Manrope), sans-serif',
            fontVariantNumeric: 'tabular-nums', outline: 'none',
          }}
        />
        {suffix && (
          <span style={{ fontSize: 12, color: p.inkDim, fontWeight: 600 }}>{suffix}</span>
        )}
      </div>
    );
  }

  function Hint({ children, p }) {
    return (
      <div style={{
        marginTop: 8, fontSize: 11.5, color: p.inkDim, lineHeight: 1.5,
        fontFamily: 'var(--studio-sans, Manrope), sans-serif',
      }}>
        {children}
      </div>
    );
  }

  function Toggle({ on, onChange, p }) {
    return (
      <button onClick={() => onChange(!on)} style={{
        width: 56, height: 30, padding: 2,
        background: on ? p.neg : p.chipBg,
        border: `1px solid ${on ? p.neg : p.ruleHi}`,
        borderRadius: 999, cursor: 'pointer',
        display: 'flex', alignItems: 'center',
        justifyContent: on ? 'flex-end' : 'flex-start',
        transition: 'background .15s',
      }}>
        <span style={{
          width: 24, height: 24, borderRadius: 999,
          background: '#fff', boxShadow: '0 1px 2px rgba(0,0,0,0.18)',
          transition: 'all .15s',
        }} />
      </button>
    );
  }

  window.Settings = Settings;
})();
