// Shared mock data — realistic AlphaBot state pulled from the user's screenshot.
// All three design directions render from this same source so they're comparable.

window.ALPHABOT_DATA = {
  meta: {
    account: { label: 'Roth IRA', uuid_short: '880be47e' },
    mode: 'DRY RUN', // 'DRY RUN' | 'LIVE'
    market_state: 'closed', // 'open' | 'closed' | 'pre' | 'post'
    market_state_label: 'Market closed · frozen at 16:00:01 ET',
    clock_et: '06:17:07 PM ET',
    next_run: '00:00',
    system_online: true,
    tracked: 11,
    armed: 1,
    triggered: 4,
    triggers_today: { trailing_stop: 1, take_profit: 3 },
    portfolio: {
      tc: 0.38, // today's change (dry-run / alphabot)
      tc_if_held: 0.45, // today's change as-if held (slightly ahead — bot exited some winners early)
      cr: 78.23, cr_if_held: 76.50, // cumulative — bot ahead by 1.73%
      mdd: -18.88, mdd_if_held: -24.47, // max drawdown — bot saved 5.59%
      account_actual_return: 0.38,
      cumulative_return: 78.23,
      annualized_return: 36.87,
      sharpe: 1.12,
      sharpe_if_held: 0.94,
      max_drawdown: 18.88,
      stddev: 32.89,
      account_value: 13172.46,
      available_cash: 20.97,
      buying_power: 20.97,
      // 60-day cumulative return series for the hero chart (Bot vs If-Held)
      hist_dates: ['Mar 18', 'Apr 1', 'Apr 15', 'May 1', 'May 8', 'May 15', 'May 18'],
      hist_bot: [0, 4.2, 7.8, 12.4, 15.1, 16.8, 18.3],
      hist_held: [0, 3.8, 6.5, 10.1, 11.2, 13.5, 14.8],
    },
    data_as_of: '16:00 ET',
    last_updated: '4:17:07 PM',
  },
  symphonies: [
    {
      id: 'sym_lqd_eyeg', name: '(INVEST) LQD + EYEG 5 ways Full Market',
      mc_prob: 43.7, status: 'TAKE-PROFIT', triggered: true, triggered_reason: 'Take-Profit',
      triggered_at_time: '10:40', triggered_at_return: 1.68, triggered_at_hwm: 2.93, triggered_at_stop: 1.68,
      current_return: 1.80, shadow_hwm: 2.93, stop_trigger: 1.68,
      tc_dr: 1.68, tc_ih: 1.80, cr_dr: 7.92, cr_ih: 5.74, mdd_dr: -9.4, mdd_ih: -14.82,
      guard_alpha: -0.12,
      sparkline: [0, 0.4, 0.9, 1.5, 2.1, 2.6, 2.9, 2.8, 2.5, 2.0, 1.85, 1.7, 1.68, 1.72, 1.76, 1.78, 1.80],
    },
    {
      id: 'sym_hunted', name: '(INVEST) Planet of Hunted Cascades',
      mc_prob: 30.7, status: 'STANDBY', triggered: false,
      current_return: 0.52, shadow_hwm: 1.41, stop_trigger: 0.24,
      tc_dr: 0.52, tc_ih: 0.52, cr_dr: 79.65, cr_ih: 81.31, mdd_dr: -22.1, mdd_ih: -32.26,
      sparkline: [0, 0.2, 0.5, 0.8, 1.2, 1.41, 1.3, 1.1, 0.9, 0.7, 0.6, 0.55, 0.52],
    },
    {
      id: 'sym_inflation', name: '(INVEST) Planet of Projected Inflation: Corporate Chaos 2060',
      mc_prob: 53.6, status: 'TAKE-PROFIT', triggered: true, triggered_reason: 'Take-Profit',
      triggered_at_time: '12:32', triggered_at_return: 0.61, triggered_at_hwm: 1.04, triggered_at_stop: 0.61,
      current_return: 0.80, shadow_hwm: 1.04, stop_trigger: 0.61,
      tc_dr: 0.61, tc_ih: 0.80, cr_dr: 71.42, cr_ih: 68.27, mdd_dr: -10.21, mdd_ih: -14.95,
      guard_alpha: -0.19,
      sparkline: [0, 0.3, 0.6, 0.9, 1.0, 1.04, 0.95, 0.85, 0.75, 0.7, 0.65, 0.63, 0.61, 0.66, 0.73, 0.77, 0.80],
    },
    {
      id: 'sym_crypto', name: '(INVEST:CRYPTO) We do a Little Trolling Planet\'s Mix v1.4',
      mc_prob: 83.0, status: 'STANDBY', triggered: false,
      current_return: -0.52, shadow_hwm: 0.33, stop_trigger: -0.24,
      tc_dr: -0.52, tc_ih: -0.52, cr_dr: 285.4, cr_ih: 319.86, mdd_dr: -17.85, mdd_ih: -25.52,
      sparkline: [0, 0.2, 0.3, 0.33, 0.2, 0.05, -0.1, -0.25, -0.35, -0.4, -0.45, -0.5, -0.52],
    },
    {
      id: 'sym_chaos', name: 'Corporate Chaos 5 ways',
      mc_prob: 0.0, status: 'TP-ARMED', tp_armed: true,
      current_return: 1.74, shadow_hwm: 2.33, stop_trigger: 2.01,
      breakeven_locked: true,
      tc_dr: null, tc_ih: 1.74, cr_dr: 4.18, cr_ih: -2.96, mdd_dr: -7.92, mdd_ih: -12.51,
      sparkline: [0, 0.5, 1.0, 1.4, 1.8, 2.1, 2.33, 2.2, 2.05, 1.9, 1.8, 1.75, 1.74],
    },
    {
      id: 'sym_feaver', name: 'Land of Feaver\'d Allocations: Intelligent Novas',
      mc_prob: 30.7, status: 'STANDBY', triggered: false,
      current_return: 0.54, shadow_hwm: 1.41, stop_trigger: 0.24,
      tc_dr: 0.54, tc_ih: 0.54, cr_dr: 18.34, cr_ih: 14.61, mdd_dr: -8.1, mdd_ih: -11.87,
      sparkline: [0, 0.2, 0.5, 0.8, 1.2, 1.41, 1.3, 1.0, 0.85, 0.7, 0.62, 0.57, 0.54],
    },
    {
      id: 'sym_planet_lqd', name: 'Planet LQD: Run of the Feaver\'d WaltAnansi USDs 2/3 Mixup',
      mc_prob: 85.5, status: 'STANDBY', triggered: false,
      current_return: -0.71, shadow_hwm: 1.09, stop_trigger: -0.12,
      tc_dr: -0.71, tc_ih: -0.71, cr_dr: 45.82, cr_ih: 40.26, mdd_dr: -16.4, mdd_ih: -23.36,
      sparkline: [0, 0.4, 0.8, 1.0, 1.09, 0.9, 0.6, 0.3, 0.0, -0.3, -0.5, -0.65, -0.71],
    },
    {
      id: 'sym_erased', name: 'Planet of Erased History: Nova\'s Feaver\'d Golden Age of EYEG',
      mc_prob: 45.8, status: 'TRAILING STOP', triggered: true, triggered_reason: 'Trailing Stop',
      triggered_at_time: '14:57', triggered_at_return: 0.65, triggered_at_hwm: 1.0, triggered_at_stop: 0.92,
      current_return: 0.79, shadow_hwm: 1.0, stop_trigger: 0.92,
      tc_dr: 0.65, tc_ih: 0.79, cr_dr: 3.05, cr_ih: 2.19, mdd_dr: -10.18, mdd_ih: -14.23,
      guard_alpha: -0.14,
      sparkline: [0, 0.3, 0.6, 0.8, 0.95, 1.0, 0.95, 0.88, 0.82, 0.78, 0.72, 0.68, 0.65, 0.69, 0.74, 0.77, 0.79],
    },
    {
      id: 'sym_golden', name: 'Planet of the Golden Age (Buy Copy)',
      mc_prob: 57.3, status: 'STANDBY', triggered: false,
      current_return: 0.23, shadow_hwm: 0.87, stop_trigger: 0.11,
      tc_dr: 0.23, tc_ih: 0.23, cr_dr: 24.91, cr_ih: 21.45, mdd_dr: -4.62, mdd_ih: -6.57,
      sparkline: [0, 0.2, 0.4, 0.6, 0.75, 0.87, 0.7, 0.55, 0.45, 0.35, 0.28, 0.25, 0.23],
    },
    {
      id: 'sym_paragons', name: 'Planet of the Paragons: EYEG of the LQD',
      mc_prob: 43.1, status: 'TAKE-PROFIT', triggered: true, triggered_reason: 'Take-Profit',
      triggered_at_time: '12:33', triggered_at_return: 2.47, triggered_at_hwm: 3.08, triggered_at_stop: 2.47,
      current_return: 1.95, shadow_hwm: 3.08, stop_trigger: 2.47,
      tc_dr: 2.47, tc_ih: 1.95, cr_dr: -2.84, cr_ih: -6.01, mdd_dr: -16.55, mdd_ih: -23.18,
      guard_alpha: 0.52,
      sparkline: [0, 0.5, 1.2, 1.8, 2.4, 2.8, 3.08, 2.9, 2.7, 2.4, 2.1, 2.0, 1.95],
    },
    {
      id: 'sym_reason', name: 'Planet of the Reasonabilists: Era of the Erased Golden Age',
      mc_prob: 95.7, status: 'STANDBY', triggered: false,
      current_return: -1.22, shadow_hwm: 1.05, stop_trigger: -0.10,
      tc_dr: -1.22, tc_ih: -1.22, cr_dr: 81.2, cr_ih: 76.60, mdd_dr: -17.1, mdd_ih: -23.44,
      sparkline: [0, 0.4, 0.7, 0.9, 1.05, 0.8, 0.5, 0.1, -0.3, -0.6, -0.85, -1.05, -1.22],
    },
  ],
  events: [
    { ts: '14:57:12', sym: 'Planet of Erased History', kind: 'TRIGGER', reason: 'Trailing Stop', detail: 'Stop hit at +0.65% (peak +1.00%)' },
    { ts: '12:33:08', sym: 'Planet of the Paragons', kind: 'TRIGGER', reason: 'Take-Profit', detail: 'TP at +2.47% (α +0.52%)' },
    { ts: '12:32:51', sym: 'Planet of Projected Inflation', kind: 'TRIGGER', reason: 'Take-Profit', detail: 'TP at +0.61%' },
    { ts: '10:40:33', sym: 'LQD + EYEG 5 ways', kind: 'TRIGGER', reason: 'Take-Profit', detail: 'TP at +1.68%' },
    { ts: '10:38:01', sym: 'LQD + EYEG 5 ways', kind: 'ARM', reason: 'TP-ARMED', detail: 'MC prob fell to 4.2%' },
    { ts: '10:12:44', sym: 'Corporate Chaos 5 ways', kind: 'LOCK', reason: 'Breakeven Lock', detail: 'Floor raised to 0.00%' },
    { ts: '09:42:19', sym: 'Planet of the Paragons', kind: 'ARM', reason: 'PARA-ARMED', detail: 'Velocity exceeded threshold' },
    { ts: '09:30:00', sym: 'System', kind: 'INFO', reason: 'Boot', detail: 'AlphaBot scheduler started · tracking 11 symphonies' },
  ],

  // Showcase detail for the per-symphony slide-over.
  // Bound to sym_paragons (Take-Profit triggered at 12:33, guard alpha +0.52%).
  detail: {
    sym_id: 'sym_paragons',
    intraday: {
      // 1-minute-ish timeline 09:30 → 16:00. Indices align across series.
      times: ['09:30','09:45','10:00','10:15','10:30','10:45','11:00','11:15','11:30','11:45','12:00','12:15','12:30','12:33','12:45','13:00','13:15','13:30','13:45','14:00','14:15','14:30','14:45','15:00','15:15','15:30','15:45','16:00'],
      // Live (if-held) cumulative return %
      live: [0, 0.18, 0.42, 0.71, 0.95, 1.18, 1.42, 1.65, 1.81, 1.98, 2.14, 2.31, 2.46, 2.47, 2.62, 2.78, 2.95, 3.04, 3.08, 3.02, 2.91, 2.74, 2.52, 2.34, 2.18, 2.05, 1.98, 1.95],
      // Shadow (what AlphaBot booked — frozen at trigger)
      shadow: [0, 0.18, 0.42, 0.71, 0.95, 1.18, 1.42, 1.65, 1.81, 1.98, 2.14, 2.31, 2.46, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47, 2.47],
      // Vol-scaled trailing stop level over time (decays via log-time squeeze)
      stop: [-0.5, -0.42, -0.30, -0.18, 0.04, 0.30, 0.58, 0.85, 1.05, 1.32, 1.58, 1.84, 2.10, 2.13, null, null, null, null, null, null, null, null, null, null, null, null, null, null],
      // Breakeven lock (activated mid-morning)
      breakeven: [null, null, null, null, null, null, 0, 0, 0, 0, 0, 0, 0, 0, null, null, null, null, null, null, null, null, null, null, null, null, null, null],
      // VWAP overlay
      vwap: [0, 0.12, 0.28, 0.46, 0.63, 0.81, 1.02, 1.22, 1.39, 1.55, 1.71, 1.87, 2.02, 2.04, 2.18, 2.30, 2.42, 2.52, 2.58, 2.59, 2.55, 2.48, 2.39, 2.28, 2.19, 2.12, 2.07, 2.04],
      // MC probability over time (falls as run-up extends → triggers TP-ARMED then TRIGGER)
      mc: [62, 58, 51, 44, 38, 32, 28, 24, 20, 17, 14, 10, 6, 4.2, null, null, null, null, null, null, null, null, null, null, null, null, null, null],
    },
    events: [
      { ts: '12:33:08', kind: 'TRIGGER', reason: 'Take-Profit', detail: 'Cash conversion sent · α +0.52% locked', ok: true },
      { ts: '12:32:05', kind: 'CONFIRM', reason: 'TP confirm 2/2', detail: 'Above TP threshold for 2nd consecutive tick' },
      { ts: '12:30:18', kind: 'CONFIRM', reason: 'TP confirm 1/2', detail: 'MC prob 4.2% < TAKE_PROFIT_MC_PCT 5.0' },
      { ts: '11:52:30', kind: 'PARA', reason: 'Parabolic squeeze active', detail: 'Velocity 0.184 > threshold 0.150' },
      { ts: '11:04:11', kind: 'LOCK', reason: 'Breakeven Lock', detail: 'Floor raised to 0.00% (5 confirm ticks at +1.2%)' },
      { ts: '09:42:19', kind: 'ARM', reason: 'PARA-ARMED', detail: 'Run-up velocity flagged' },
      { ts: '09:30:00', kind: 'BOOT', reason: 'Tracking started', detail: 'Vol(20d) = 16.8% · stop dist = 0.84%' },
    ],
    risk_params: {
      // From .env / SQLite override
      TRIGGER_THRESHOLD_PCT: 15.0,
      TAKE_PROFIT_MC_PCT: 5.0,
      MAX_SQUEEZE_FLOOR: 0.10,
      VWAP_CROSS_HWM_PCT: 1.5,
      VWAP_BLEED_MULTIPLIER: 1.2,
      VWAP_BLEED_TICKS: 3,
      PARABOLIC_VELOCITY_THRESHOLD: 0.15,
      MAX_PARABOLIC_SQUEEZE: 0.65,
    },
    locked_vars: ['VWAP_BLEED_MULTIPLIER', 'MAX_PARABOLIC_SQUEEZE'],
    perf: {
      // 60-day per-symphony Bot vs If-Held cumulative
      hist_dates: ['Mar 18', 'Apr 1', 'Apr 15', 'May 1', 'May 8', 'May 15', 'May 18'],
      bot: [0, -1.2, -3.4, -5.1, -4.8, -5.6, -6.01],
      held: [0, -2.1, -4.8, -7.3, -7.9, -9.4, -10.42],
    },
  },

  // ────────────────────────────────────────────────────────────────────────
  // /performance route data — matches the /api/performance contract in app.py
  // (scope, dates, live_returns, shadow_returns, live_metrics, shadow_metrics,
  // observation_count, insufficient_history).
  // ────────────────────────────────────────────────────────────────────────
  performance: {
    scope: 'aggregate',
    window_days: 60,
    observation_count: 58,
    insufficient_history: false,
    // 60 trading days of daily cumulative returns
    dates: (() => {
      const out = [];
      const start = new Date('2026-02-21');
      for (let i = 0; i < 58; i++) {
        const d = new Date(start.getTime() + i * 86400000);
        out.push(`${d.getMonth()+1}/${d.getDate()}`);
      }
      return out;
    })(),
    shadow_returns: [0, 0.4, 0.9, 1.4, 1.1, 1.8, 2.6, 3.1, 2.7, 3.4, 4.2, 4.5, 4.1, 3.6, 4.0, 4.8, 5.2, 5.6, 5.1, 6.0, 6.4, 6.0, 6.5, 7.1, 7.5, 8.0, 7.6, 8.2, 8.5, 8.1, 8.6, 9.2, 9.7, 9.4, 10.0, 10.4, 10.8, 11.2, 10.7, 11.4, 11.9, 12.4, 12.8, 12.3, 12.7, 13.4, 13.9, 14.4, 14.0, 14.7, 15.1, 14.6, 15.0, 15.6, 16.1, 15.8, 16.2, 18.3],
    live_returns: [0, 0.4, 0.85, 1.3, 1.0, 1.7, 2.4, 2.9, 2.5, 3.1, 3.9, 4.2, 3.9, 3.4, 3.7, 4.5, 4.8, 5.2, 4.7, 5.5, 5.8, 5.5, 5.9, 6.4, 6.7, 7.1, 6.7, 7.2, 7.4, 7.0, 7.4, 7.9, 8.3, 8.0, 8.5, 8.8, 9.1, 9.4, 9.0, 9.6, 9.9, 10.3, 10.6, 10.2, 10.5, 11.0, 11.4, 11.8, 11.4, 11.9, 12.2, 11.8, 12.1, 12.6, 13.0, 12.8, 13.1, 14.8],
    // QuantStats metrics — keys match _PERFORMANCE_METRIC_KEYS in app.py
    shadow_metrics: {
      total_return: 18.30, annualized_return: 36.87, sharpe: 1.12,
      sortino: 1.68, max_drawdown: -18.88, calmar: 1.95, win_rate: 58.6,
    },
    live_metrics: {
      total_return: 14.80, annualized_return: 29.85, sharpe: 0.94,
      sortino: 1.42, max_drawdown: -24.47, calmar: 1.22, win_rate: 56.9,
    },
  },

  // ────────────────────────────────────────────────────────────────────────
  // /ai-advisor route — sample suggestions + recent autotune runs
  // ────────────────────────────────────────────────────────────────────────
  advisor: {
    last_run_at: '2026-05-18T15:53:00-04:00',
    target_symphony: 'sym_paragons',
    suggestions: [
      {
        config_key: 'TAKE_PROFIT_MC_PCT',
        current_value: 5.0,
        suggested_value: 3.5,
        rationale: 'Last 30 days show 6 take-profit exits triggered at MC ≤5%, of which 4 gave back ≥0.3% to the held series within 60 minutes. Tighter threshold delays exit until conviction is stronger.',
        risk_direction: 'looser',
        confidence: 'high',
        data_sufficiency: 'sufficient',
        oos_status: 'passed',
        impact: { metric: 'sortino', delta: 0.18 },
      },
      {
        config_key: 'PARABOLIC_VELOCITY_THRESHOLD',
        current_value: 0.15,
        suggested_value: 0.13,
        rationale: 'Symphony hit parabolic-armed state on 11 of last 60 sessions but only 3 generated a real run-up. Lower threshold ratchets earlier on the false-positive tail.',
        risk_direction: 'tighter',
        confidence: 'medium',
        data_sufficiency: 'sufficient',
        oos_status: 'passed',
        impact: { metric: 'max_drawdown', delta: 1.42 },
      },
      {
        config_key: 'VWAP_BLEED_MULTIPLIER',
        current_value: 1.2,
        suggested_value: 1.5,
        rationale: 'Recent bleed exits clustered around the −0.50% safety clamp — multiplier is too tight given asset 20d vol of 16.8%.',
        risk_direction: 'looser',
        confidence: 'medium',
        data_sufficiency: 'sufficient',
        oos_status: 'rejected',
        oos_reason: 'OOS sharpe regressed from 1.12 → 0.97 on holdout (threshold 1.05).',
        impact: { metric: 'sharpe', delta: -0.15 },
      },
      {
        config_key: 'MAX_PARABOLIC_SQUEEZE',
        current_value: 0.65,
        suggested_value: 0.55,
        rationale: 'Once parabolic-armed, current squeeze leaves 35% of peak-to-stop room. Tighter squeeze locks more of the upside before mean-reversion.',
        risk_direction: 'tighter',
        confidence: 'low',
        data_sufficiency: 'limited',
        oos_status: 'pending',
        impact: { metric: 'guard_alpha', delta: 0.21 },
      },
    ],
    autotune_runs: [
      { symphony: 'sym_paragons',    ts: '2026-05-17 16:14 ET', decision: 'apply',  naive_sharpe: 1.18, dsr: 0.82, frozen_eval: 'passed' },
      { symphony: 'sym_lqd_eyeg',    ts: '2026-05-17 16:14 ET', decision: 'apply',  naive_sharpe: 1.04, dsr: 0.71, frozen_eval: 'passed' },
      { symphony: 'sym_hunted',      ts: '2026-05-17 16:14 ET', decision: 'reject', naive_sharpe: 0.92, dsr: 0.41, frozen_eval: 'failed' },
      { symphony: 'sym_inflation',   ts: '2026-05-16 16:14 ET', decision: 'apply',  naive_sharpe: 1.21, dsr: 0.79, frozen_eval: 'passed' },
      { symphony: 'sym_crypto',      ts: '2026-05-16 16:14 ET', decision: 'reject', naive_sharpe: 0.65, dsr: 0.22, frozen_eval: 'failed' },
      { symphony: 'sym_paragons',    ts: '2026-05-15 16:14 ET', decision: 'apply',  naive_sharpe: 1.14, dsr: 0.74, frozen_eval: 'passed' },
      { symphony: 'sym_feaver',      ts: '2026-05-15 16:14 ET', decision: 'fallback', naive_sharpe: 0.81, dsr: 0.35, frozen_eval: 'reverted' },
    ],
  },

  // ────────────────────────────────────────────────────────────────────────
  // /api/history rollup — by_reason breakdown of post-mortems
  // ────────────────────────────────────────────────────────────────────────
  history: {
    window_days: 90,
    total_alpha: 14.62,
    total_saved: 1947.21,
    trigger_count: 84,
    wins: 51,
    win_rate: 60.7,
    avg_guard_alpha: 0.174,
    by_reason: {
      'Take-Profit':     { alpha: 9.21,  count: 38, wins: 28, dollars: 1188.40 },
      'Trailing Stop':   { alpha: 4.84,  count: 31, wins: 18, dollars:  642.10 },
      'VWAP Breakdown':  { alpha: 0.95,  count:  9, wins:  4, dollars:   95.40 },
      'VWAP Bleed Cut':  { alpha: -0.38, count:  6, wins:  1, dollars:   21.31 },
    },
    // 90-day daily Guard Alpha series for the strip chart
    daily_alpha: [
      0.08, -0.02, 0.15, 0.31, 0.12, -0.05, 0.21, 0.18, 0.09, 0.04,
      -0.11, 0.27, 0.42, 0.18, 0.05, 0.13, -0.08, 0.22, 0.31, 0.15,
      0.06, -0.04, 0.18, 0.11, 0.24, 0.08, -0.12, 0.36, 0.19, 0.07,
      0.13, 0.21, -0.03, 0.28, 0.15, 0.09, 0.04, -0.06, 0.32, 0.18,
      0.11, 0.07, 0.24, 0.14, -0.09, 0.19, 0.27, 0.12, 0.05, 0.21,
      -0.02, 0.16, 0.30, 0.18, 0.09, 0.13, -0.04, 0.22, 0.31, 0.17,
      0.06, 0.11, 0.25, 0.14, -0.08, 0.19, 0.28, 0.12, 0.04, 0.21,
      0.16, -0.03, 0.27, 0.19, 0.08, 0.13, 0.22, -0.06, 0.31, 0.18,
      0.09, 0.14, 0.27, 0.16, -0.04, 0.21, 0.30, 0.17, 0.12, 0.52,
    ],
  },

  // ────────────────────────────────────────────────────────────────────────
  // Settings / Edit Variables — globals + per-symphony strategy overrides
  // ────────────────────────────────────────────────────────────────────────
  settings: {
    globals: {
      LIVE_EXECUTION: 'False',
      EXECUTION_START_TIME: '09:30',
      TRIGGER_THRESHOLD_PCT: '15.0',
      TAKE_PROFIT_MC_PCT: '5.0',
      MAX_SQUEEZE_FLOOR: '0.10',
      VWAP_CROSS_HWM_PCT: '1.5',
      VWAP_BLEED_MULTIPLIER: '1.2',
      VWAP_BLEED_TICKS: '3',
      PARABOLIC_VELOCITY_THRESHOLD: '0.15',
      MAX_PARABOLIC_SQUEEZE: '0.65',
    },
    // Masked secrets — never echoed from server
    secrets: {
      COMPOSER_KEY_ID: '••••••••',
      COMPOSER_SECRET: '••••••••',
      ALPACA_KEY: '••••••••',
      ALPACA_SECRET: '••••••••',
      DISCORD_WEBHOOK_URL: '••••••••',
      ACCOUNT_INDIVIDUAL: '••••••••',
      ACCOUNT_ROTH: '••••••••',
      ACCOUNT_TRAD: '••••••••',
      ANTHROPIC_API_KEY: '••••••••',
    },
    symphony_overrides: {
      sym_paragons: {
        params: {
          TAKE_PROFIT_MC_PCT: 3.5,
          PARABOLIC_VELOCITY_THRESHOLD: 0.13,
        },
        locked_vars: ['VWAP_BLEED_MULTIPLIER', 'MAX_PARABOLIC_SQUEEZE'],
      },
      sym_paragons_count: 2,
      sym_lqd_eyeg: {
        params: { TAKE_PROFIT_MC_PCT: 4.0 },
        locked_vars: [],
      },
    },
  },
};
