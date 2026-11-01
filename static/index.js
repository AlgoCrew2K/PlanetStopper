(function () {
    'use strict';

    var _cumChart = null;
    // One source of line colour tokens per chart kind, read at build and on tweakchange.
    var CHART_LINE_TOKENS = {
        hero: ['--studio-accent', '--studio-ink-dim'],
        spark: ['--studio-accent', '--studio-ink-dim'],
        detail: ['--studio-accent', '--studio-ink-faint', '--studio-neg',
                 '--studio-accent', '--studio-cyan', '--studio-warn']
    };
    function lineToken(kind, i) { return cs(CHART_LINE_TOKENS[kind][i]); }
    var _pollCap = null;
    var _heroWindow = '30d';
    var _pollSeries = null;   // the latest poll's dated hero series (fallback source)
    var _windowAnchor = null; // the ET trading day the latest poll's figures belong to (meta.portfolio.window_anchor)
    var _pollTail = null;     // what the last poll's series ended with (change detection)
    var _heroDrawnWindow = '30d';  // window the hero holds; the server render is the 30d series
    var _heroRequestSeq = 0;  // only the latest hero-window request may draw
    var _sparkMarkers = {};   // symphony id -> exit-marker fields, read at draw time
    var _refreshCount = 0;
    var _dashboardReady = false;
    var _botState = {};
    var _symIds = [];
    var _intradayChart = null;
    // The engine writes once a minute and a cycle event refreshes the page at once; the poll
    // is the fallback, so its interval stays well above the 15 s floor.
    var POLL_INTERVAL_MS = 30000;
    // A /api/state request with no answer for this long is abandoned and counts as a failed
    // refresh (over three times the slowest response measured, 6.11 s).
    var STATE_REQUEST_TIMEOUT_MS = 20000;
    // A stream that closed is opened again after this long, doubling while attempts fail up to
    // the cap and starting over after an open. The page never gives up: the daemon may be down
    // all night.
    var EVENT_STREAM_RETRY_FIRST_MS = 5000;
    var EVENT_STREAM_RETRY_MAX_MS = 60000;
    var EVENT_STREAM_PROBE_TIMEOUT_MS = 10000;
    // readyState values of an EventSource (HTML specification), named so the stream code reads
    // the same under any stub.
    var STREAM_OPEN = 1;
    var STREAM_CLOSED = 2;
    // Wake signals (the tab shown again, a page restored from the back-forward cache, the network
    // back) arrive in bursts: one catch-up refresh serves every signal inside this span.
    var CATCH_UP_COALESCE_MS = 1000;
    // Where the age cue on the as-of text is redrawn; it sends no request.
    var FRESHNESS_CHECK_MS = 1000;
    // No data for this long means the page slept or was throttled: a stream that still reads
    // OPEN cannot be trusted to be connected, because a socket dropped while asleep keeps
    // reading OPEN.
    var STREAM_TRUST_AFTER_SILENCE_MS = 90000;
    var WINDOW_LABELS = {
        '30d': '30d', '60d': '60d', '90d': '90d', '125d': '125d',
        'ytd': 'YTD', '1y': '1Y', 'all': 'All Time'
    };

    // The CSRF token and every POST's answer come from window.PSRequest (static/request_guard.js,
    // loaded before this file by the shared chrome). A POST the session check refuses (403) is
    // reported as refused after the token is read again; no control here ever sends it a second time.
    function cs(varName) {
        return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    }

    // The page's monotonic clock, in ms. Every age on this page is measured on it, never on the
    // wall clock, which can be minutes off. A browser always has performance; the fallback is
    // for a page environment without one.
    function monoNow() {
        return typeof performance === 'object' && performance !== null ? performance.now() : Date.now();
    }

    // The chart library is a CDN script. When that request fails `Chart` is undefined
    // and the page still has its numbers to show. Every use of the library goes
    // through chartLibrary() / chartOn(), so a missing library reads as "no chart",
    // never as a thrown ReferenceError that fails the refresh that carried the data.
    function chartLibrary() {
        return typeof Chart === 'function' ? Chart : null;
    }

    // The chart a canvas holds, or null (none drawn, or no chart library).
    function chartOn(canvas) {
        var lib = chartLibrary();
        return (lib && typeof lib.getChart === 'function' && lib.getChart(canvas)) || null;
    }

    // Builds a chart with its entry animation kept; null when the chart library is
    // missing. The chart is "drawn" once its entry animation completes, or a render
    // pass ends with no animation running (a render that ran none). chart.__drawn is a
    // promise of that moment; whenDrawn() runs a callback synchronously at it.
    function createChart(canvas, config) {
        var lib = chartLibrary();
        if (!lib) return null;
        var state = { done: false, callbacks: [] };
        var resolveDrawn;
        var drawn = new Promise(function (resolve) { resolveDrawn = resolve; });
        function fire() {
            if (state.done) return;
            state.done = true;
            resolveDrawn();
            state.callbacks.forEach(function (cb) { cb(); });
        }
        config.options = config.options || {};
        config.options.animation = { onComplete: fire };
        config.plugins = (config.plugins || []).concat([{
            id: 'drawnSignal',
            afterRender: function (chart) {
                if (!(lib.animator && lib.animator.running(chart))) fire();
            }
        }]);
        var chart = new lib(canvas, config);
        chart.__drawn = drawn;
        chart.__drawnState = state;
        return chart;
    }

    function whenDrawn(chart, callback) {
        if (chart.__drawnState.done) callback();
        else chart.__drawnState.callbacks.push(callback);
    }

    function markDashboardReady() {
        if (_dashboardReady || !document.body) return;
        _dashboardReady = true;
        document.body.dataset.dashboardReady = '1';
    }

    // Engine uses ±999 as "not-set" sentinel. Treat any abs(v) >= 900 as unset.
    function isSentinel(v) {
        return typeof v === 'number' && Math.abs(v) >= 900;
    }

    function sentinelToNull(v) {
        return (typeof v === 'number' && !isSentinel(v)) ? v : null;
    }

    function fmtPct(value, decimals) {
        if (value == null || isNaN(value)) return '--';
        decimals = decimals != null ? decimals : 2;
        var numFormat = document.documentElement.dataset.numFormat || 'full';
        if (numFormat === 'compact' && Math.abs(value) >= 1000) {
            return (value / 1000).toFixed(1) + 'k%';
        }
        return (value >= 0 ? '+' : '') + value.toFixed(decimals) + '%';
    }

    // A difference of two returns, in percentage points: signed, two decimals, then "pp". The
    // compact number format applies as it does to every other figure (1234.5 reads "1.2k pp").
    function fmtPp(value) {
        if (value == null || isNaN(value)) return '--';
        var numFormat = document.documentElement.dataset.numFormat || 'full';
        if (numFormat === 'compact' && Math.abs(value) >= 1000) {
            return (value / 1000).toFixed(1) + 'k pp';
        }
        return (value >= 0 ? '+' : '') + value.toFixed(2) + ' pp';
    }

    // ---------------------------------------------------------------------------
    // Hero chart (bot vs held cumulative returns)
    // ---------------------------------------------------------------------------

    var _WINDOW_TRAILING_DAYS = { '30d': 30, '60d': 60, '90d': 90, '125d': 125, '1y': 365 };

    // Mirrors analytics.window_cutoff: the inclusive lower bound (ISO date) of a window counted back
    // from the day the poll's figures belong to (meta.portfolio.window_anchor: the session that closed
    // while the market is closed, today's session while it is open); null = lifetime. The browser's
    // own clock never decides a cutoff: a laptop that woke days later, or a page left open into the
    // next year, draws the window the server would. A body that states no anchor falls back to the
    // newest day of its own series.
    function windowCutoffIso(token) {
        var anchor = _windowAnchor;
        if (!anchor && _pollSeries && _pollSeries.dates.length) {
            anchor = _pollSeries.dates[_pollSeries.dates.length - 1];
        }
        if (!anchor) return null;
        if (token === 'ytd') return anchor.slice(0, 4) + '-01-01';
        var days = _WINDOW_TRAILING_DAYS[token];
        if (!days) return null;
        return new Date(Date.parse(anchor + 'T00:00:00Z') - days * 86400000).toISOString().slice(0, 10);
    }

    function showHeroEmpty() {
        var el = document.getElementById('hero-chart-empty');
        if (el) el.hidden = false;
    }

    // The picked window has no data yet: the chart keeps the series it already drew and this
    // cue, named for the picked window, says so. Any series drawn afterwards hides it.
    function showWindowNoData(token) {
        var el = document.getElementById('hero-window-no-data');
        if (!el) return;
        el.textContent = 'No data yet for ' + (WINDOW_LABELS[token] || token);
        el.hidden = false;
    }

    function hideWindowNoData() {
        var el = document.getElementById('hero-window-no-data');
        if (el) el.hidden = true;
    }

    // Draws a /api/hero-chart-shaped series: in place when the chart exists. A body
    // with no points never replaces a drawn series. Returns the chart, or null when nothing was drawn
    // (no points, no canvas, or no chart library: the empty text shows then).
    function setHeroSeries(series) {
        var dates = series && series.hist_dates;
        if (!Array.isArray(dates) || !dates.length) return null;
        var bot = series.hist_bot || [];
        var held = series.hist_held || [];
        var canvas = document.getElementById('cum-chart');
        if (!canvas) return null;
        var emptyEl = document.getElementById('hero-chart-empty');
        if (emptyEl) emptyEl.hidden = true;
        hideWindowNoData();
        if (_cumChart) {
            _cumChart.data.labels = dates;
            _cumChart.data.datasets[0].data = bot;
            _cumChart.data.datasets[1].data = held;
            _cumChart.update('none');
            return _cumChart;
        }
        _cumChart = createChart(canvas, {
            type: 'line',
            data: {
                labels: dates,
                datasets: [
                    {
                        label: 'Planet Stopper',
                        data: bot,
                        borderColor: lineToken('hero', 0),
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.3,
                        fill: false
                    },
                    {
                        label: 'If held',
                        data: held,
                        borderColor: lineToken('hero', 1),
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        tension: 0.3,
                        fill: false,
                        borderDash: [4, 4]
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                // The tooltip follows the pointer along the x axis, like the Performance tab's
                // chart: it appears with the pointer anywhere over a day, not only on a point.
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (c) {
                                return c.dataset.label + ': ' + c.parsed.y.toFixed(2) + '%';
                            }
                        }
                    }
                },
                // F-025: the y-axis (magnitude) must stay visible so this chart's
                // day-by-day cumulative series (~-3% scale) can't be visually
                // conflated with the adjacent VW-lifetime scalar headline (~+34%,
                // opposite sign, ~12x magnitude). X stays hidden -- the
                // hero-data-as-of legend span already covers time.
                scales: { x: { display: false }, y: { display: true } }
            }
        });
        if (!_cumChart) { showHeroEmpty(); return null; }
        return _cumChart;
    }

    // Window fallback when /api/hero-chart fails: the poll's own dated series cut by
    // the calendar cutoff and rebased so each curve compounds from 1.0 at the window start.
    function applyHeroFallback(token) {
        if (!_pollSeries) return;
        var cutoff = windowCutoffIso(token);
        var dates = _pollSeries.dates;
        var k = 0;
        if (cutoff) { while (k < dates.length && dates[k] < cutoff) k++; }
        if (k >= dates.length) {
            // The poll holds no row inside the window: keep what is drawn and say so.
            showWindowNoData(token);
            return;
        }
        // The caption describes the series drawn: it falls short of the window when the
        // poll series starts after the cutoff, or (all time) fills the poll's own cap.
        var naEl = document.getElementById('hero-chart-na');
        if (naEl) {
            var short = cutoff ? dates[0] > cutoff
                : (_pollCap !== null && dates.length >= _pollCap);
            naEl.style.display = short ? 'block' : 'none';
        }
        function rebase(cumulative) {
            var base = k === 0 ? 0 : cumulative[k - 1];
            return cumulative.slice(k).map(function (c) {
                return ((1 + c / 100) / (1 + base / 100) - 1) * 100;
            });
        }
        setHeroSeries({
            hist_dates: dates.slice(k),
            hist_bot: rebase(_pollSeries.bot),
            hist_held: rebase(_pollSeries.held)
        });
        _heroDrawnWindow = token;
    }

    // Draws the answer to a /api/hero-chart request; only the latest request may draw.
    function drawHeroWindow(token, request) {
        var seq = ++_heroRequestSeq;
        request
            .then(function (r) {
                if (!r.ok) throw new Error('hero-chart');  // a non-2xx body is never drawn
                return r.json();
            })
            .then(function (d) {
                if (seq !== _heroRequestSeq) return;
                _heroDrawnWindow = token;
                var naEl = document.getElementById('hero-chart-na');
                // Always plot whatever data is available; caption when history is shorter than the window.
                if (naEl) naEl.style.display = d.insufficient_history ? 'block' : 'none';
                setHeroSeries(d);
                // An answer with no points never replaces a drawn series: say the window is empty.
                if (!Array.isArray(d.hist_dates) || !d.hist_dates.length) showWindowNoData(token);
            })
            .catch(function () {
                if (seq === _heroRequestSeq) applyHeroFallback(token);
            });
    }

    function requestHeroWindow(token) {
        drawHeroWindow(token, fetch('/api/hero-chart/' + token));
    }

    // A poll never redraws the hero from its own series. It only notes the series and,
    // when that series changed since the last poll, asks for the selected window again.
    function renderHeroChart(meta) {
        var portfolio = (meta || {}).portfolio;
        if (!portfolio || typeof portfolio !== 'object') return;
        _windowAnchor = typeof portfolio.window_anchor === 'string' ? portfolio.window_anchor : null;
        var dates = portfolio.hist_dates;
        var bot = portfolio.hist_bot;
        var held = portfolio.hist_held;
        if (!Array.isArray(dates) || !dates.length || !Array.isArray(bot) || !Array.isArray(held)) return;
        _pollSeries = { dates: dates, bot: bot, held: held };
        var tail = {
            length: dates.length,
            last_date: dates[dates.length - 1],
            last_bot: bot[bot.length - 1],
            last_held: held[held.length - 1]
        };
        var changed = !_pollTail || ['length', 'last_date', 'last_bot', 'last_held'].some(function (k) {
            return _pollTail[k] !== tail[k];
        });
        _pollTail = tail;
        if (changed) requestHeroWindow(_heroWindow);
        // A pick made before the first poll whose request failed: show it from the poll series.
        else if (_heroDrawnWindow !== _heroWindow) applyHeroFallback(_heroWindow);
    }

    // ---------------------------------------------------------------------------
    // Guard effect headline, window label, Max DD row and Ann. Vol row: the picked window's strip
    // ---------------------------------------------------------------------------
    // These are written by one path, from one /api/strip/<window> answer:
    // #guard-alpha-headline, #guard-alpha-window-label, the Max DD row and the Ann. Vol row.
    // A poll never writes them (its own figures are the default window's and lifetime's), so a
    // refresh cannot put another window's value under the picked window's label. The label is
    // written with the value it describes. Do NOT derive guard_alpha from
    // cumulative_return.dry_run - cumulative_return.if_held: those fields can be on
    // different bases (the cumulative row leaves out a symphony with no Composer cost basis).

    var _stripRequestSeq = 0;      // only the newest strip request may apply
    var _stripAnsweredToken = null; // the window whose strip request was last answered; null before the first
    var _stripFiguresBlank = false; // a pick wrote dashes and no strip has answered for the picked window yet

    // Ann. Vol row. vol is inverted: lower is better for this capital-preservation system.
    // vol_bot/vol_held are fractions; a null on either side reads as dashes, never as a
    // value from another window.
    function updateVolRow(strip) {
        var rawVolBot  = strip.vol_bot  != null ? strip.vol_bot  : null;
        var rawVolHeld = strip.vol_held != null ? strip.vol_held : null;
        var hasVol = rawVolBot != null && rawVolHeld != null;

        var volBotText  = document.querySelector('[data-testid="comp-vol-bot-text"]');
        var volHeldText = document.querySelector('[data-testid="comp-vol-held-text"]');
        var volDelta    = document.querySelector('[data-testid="comp-vol-delta"]');
        var volBotBars  = document.querySelectorAll('[data-testid="comp-bar-vol-bot"]');
        var volHeldBars = document.querySelectorAll('[data-testid="comp-bar-vol-held"]');
        // A window with no volatility figure hides the row: a row of dashes says nothing.
        var volRow = volBotText && typeof volBotText.closest === 'function'
            ? volBotText.closest('[data-testid="vs-row"]') : null;
        if (volRow) volRow.hidden = !hasVol;

        if (hasVol) {
            var volBotPct  = rawVolBot  * 100;
            var volHeldPct = rawVolHeld * 100;
            var volAlphaPp = (rawVolHeld - rawVolBot) * 100;
            var volBotWins = rawVolBot <= rawVolHeld;
            var botW  = volBotWins  ? 100 : Math.min(rawVolHeld / rawVolBot  * 100, 100);
            var heldW = !volBotWins ? 100 : Math.min(rawVolBot  / rawVolHeld * 100, 100);

            if (volBotText) {
                volBotText.textContent = 'Bot ' + volBotPct.toFixed(1) + '%';
                volBotText.className = volBotText.className.replace(/\bpos\b|\bneg\b/g, '').trim();
            }
            if (volHeldText) {
                volHeldText.textContent = 'Held ' + volHeldPct.toFixed(1) + '%';
                volHeldText.className = volHeldText.className.replace(/\bpos\b|\bneg\b/g, '').trim();
            }
            if (volDelta) {
                volDelta.textContent = 'α ' + (volAlphaPp >= 0 ? '+' : '') + volAlphaPp.toFixed(1) + 'pp';
                volDelta.className = volDelta.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                volDelta.classList.add(volAlphaPp >= 0 ? 'pos' : 'neg');
            }
            volBotBars.forEach(function (el) { el.style.width = botW.toFixed(1) + '%'; });
            volHeldBars.forEach(function (el) { el.style.width = heldW.toFixed(1) + '%'; });
            applyBarClasses(volBotBars, volHeldBars, volBotWins);
        } else {
            if (volBotText)  volBotText.textContent  = 'Bot —';
            if (volHeldText) volHeldText.textContent = 'Held —';
            if (volDelta)    volDelta.textContent    = 'α —';
            volBotBars.forEach(function (el)  { el.style.width = '0%'; });
            volHeldBars.forEach(function (el) { el.style.width = '0%'; });
            // Without both legs no side leads.
            applyBarClasses(volBotBars, volHeldBars, null);
        }
    }

    // Max DD row: the picked window's drawdown, Bot and Held, printed as magnitudes (a drawdown
    // carries no plus sign). A leg the window cannot supply shows no digits, the row's own
    // "insufficient history" label shows, and no side leads; the poll's lifetime figure is never
    // written here or anywhere else on this row. A strip with no max_drawdown object changes nothing.
    function updateMddRow(strip) {
        var legs = strip && strip.max_drawdown;
        if (!legs || typeof legs !== 'object') return;
        var bot = typeof legs.dry_run === 'number' && isFinite(legs.dry_run) ? Math.abs(legs.dry_run) : null;
        var held = typeof legs.if_held === 'number' && isFinite(legs.if_held) ? Math.abs(legs.if_held) : null;
        var supplied = bot !== null && held !== null && strip.insufficient_history !== true;
        var botText = document.querySelector('[data-testid="comp-mdd-bot-text"]');
        var heldText = document.querySelector('[data-testid="comp-mdd-held-text"]');
        // A dash carries no sign colour: an unknown drawdown must not read as a bad one.
        if (botText) {
            botText.textContent = bot !== null ? 'Bot ' + bot.toFixed(2) + '%' : '\u2014';
            if (bot !== null) botText.classList.add('neg'); else botText.classList.remove('neg');
        }
        if (heldText) {
            heldText.textContent = held !== null ? 'Held ' + held.toFixed(2) + '%' : '\u2014';
            if (held !== null) heldText.classList.add('neg'); else heldText.classList.remove('neg');
        }
        var maxAbs = Math.max(bot === null ? 0 : bot, held === null ? 0 : held, 0.01);
        var botBars = document.querySelectorAll('[data-testid="comp-bar-bot"][data-row="mdd"]');
        var heldBars = document.querySelectorAll('[data-testid="comp-bar-held"][data-row="mdd"]');
        botBars.forEach(function (el) {
            el.style.width = Math.min((bot === null ? 0 : bot) / maxAbs * 100, 100).toFixed(1) + '%';
        });
        heldBars.forEach(function (el) {
            el.style.width = Math.min((held === null ? 0 : held) / maxAbs * 100, 100).toFixed(1) + '%';
        });
        // The smaller drawdown leads; with no figure for the window no side does.
        applyBarClasses(botBars, heldBars, supplied ? bot <= held : null);
        var deltaEl = document.querySelector('[data-testid="comp-mdd-delta"]');
        if (deltaEl) {
            deltaEl.hidden = !supplied;
            if (supplied) {
                // Held minus Bot, in percentage points (the unit of the headline beside it).
                var saved = held - bot;
                deltaEl.textContent = '\u03b1 ' + fmtPp(saved);
                setPosNeg(deltaEl, saved);
            }
        }
        var notice = document.querySelector('[data-testid="mdd-insufficient-label"]');
        if (notice) notice.hidden = supplied;
    }

    // The window's figures are not known: the headline reads '--', the Max DD row and the Ann.
    // Vol row show dashes (no digit, no bar, no leader) and no claim about the window's history
    // is made. Written the moment a window is picked, so nothing of the previous window stays
    // under the new label, and left in place when the strip cannot be loaded.
    function blankWindowedFigures() {
        var el = document.getElementById('guard-alpha-headline');
        if (el) {
            el.textContent = fmtPp(null);
            el.style.color = cs('--studio-ink-dim');
        }
        var botText = document.querySelector('[data-testid="comp-mdd-bot-text"]');
        var heldText = document.querySelector('[data-testid="comp-mdd-held-text"]');
        // A dash carries no sign colour: an unknown drawdown must not read as a bad one.
        if (botText) { botText.textContent = '—'; botText.classList.remove('neg'); }
        if (heldText) { heldText.textContent = '—'; heldText.classList.remove('neg'); }
        var mddBotBars = document.querySelectorAll('[data-testid="comp-bar-bot"][data-row="mdd"]');
        var mddHeldBars = document.querySelectorAll('[data-testid="comp-bar-held"][data-row="mdd"]');
        mddBotBars.forEach(function (bar) { bar.style.width = '0%'; });
        mddHeldBars.forEach(function (bar) { bar.style.width = '0%'; });
        applyBarClasses(mddBotBars, mddHeldBars, null);
        var mddDelta = document.querySelector('[data-testid="comp-mdd-delta"]');
        if (mddDelta) mddDelta.hidden = true;
        var mddNotice = document.querySelector('[data-testid="mdd-insufficient-label"]');
        if (mddNotice) mddNotice.hidden = true;
        // A pending Ann. Vol row is shown with dashes; only an answer with no volatility hides it.
        var volBotText = document.querySelector('[data-testid="comp-vol-bot-text"]');
        var volHeldText = document.querySelector('[data-testid="comp-vol-held-text"]');
        var volDelta = document.querySelector('[data-testid="comp-vol-delta"]');
        if (volBotText) volBotText.textContent = 'Bot —';
        if (volHeldText) volHeldText.textContent = 'Held —';
        if (volDelta) volDelta.textContent = 'α —';
        var volBotBars = document.querySelectorAll('[data-testid="comp-bar-vol-bot"]');
        var volHeldBars = document.querySelectorAll('[data-testid="comp-bar-vol-held"]');
        volBotBars.forEach(function (bar) { bar.style.width = '0%'; });
        volHeldBars.forEach(function (bar) { bar.style.width = '0%'; });
        applyBarClasses(volBotBars, volHeldBars, null);
    }

    // The visible notice for a window whose strip could not be loaded; it names the window.
    // Cleared by the next pick and by the next strip that answers.
    function showStripNotice(token) {
        var el = document.getElementById('guard-alpha-window-notice');
        if (!el) return;
        el.textContent = 'Could not load the ' + (WINDOW_LABELS[token] || token) + ' window';
        el.hidden = false;
    }

    function clearStripNotice() {
        var el = document.getElementById('guard-alpha-window-notice');
        if (!el) return;
        el.textContent = '';
        el.hidden = true;
    }

    // The one writer of the headline value, its window label, the Max DD row and the Ann. Vol
    // row. A null guard_alpha (fewer than two days in the window) reads '--' under the window's
    // label, so the previous window's value never stays under the new label.
    function applyWindowedStrip(token, strip) {
        var guard_alpha = typeof strip.guard_alpha === 'number' ? strip.guard_alpha : null;
        var el = document.getElementById('guard-alpha-headline');
        if (el) {
            el.textContent = fmtPp(guard_alpha);
            el.style.color = guard_alpha === null ? cs('--studio-ink-dim')
                : (guard_alpha >= 0 ? cs('--studio-pos') : cs('--studio-neg'));
        }
        var labelEl = document.getElementById('guard-alpha-window-label');
        if (labelEl) labelEl.textContent = WINDOW_LABELS[token] || token;
        updateMddRow(strip);
        updateVolRow(strip);
    }

    // Asks for the picked window's strip. Newest request wins, and an answer or a failure applies
    // only while its window is still the picked one. A refused, failed or malformed answer writes
    // no figure: after a pick the figures stay the dashes the pick wrote and a notice names the
    // window; a refresh of a window that already shows its own figures keeps them (they are that
    // window's, one refresh old). A failed request leaves the window unanswered, so the next
    // refresh asks again, and the next good answer clears the notice.
    function requestWindowedStrip(token) {
        var seq = ++_stripRequestSeq;
        return fetch('/api/strip/' + token)
            .then(function (r) {
                if (!r.ok) throw new Error('strip HTTP ' + r.status);
                return r.json();
            })
            .then(function (strip) {
                if (seq !== _stripRequestSeq || token !== _heroWindow) return;
                if (!strip || typeof strip !== 'object' || strip.error || !('guard_alpha' in strip)) {
                    throw new Error('strip answer is not a strip');
                }
                _stripAnsweredToken = token;
                _stripFiguresBlank = false;
                clearStripNotice();
                applyWindowedStrip(token, strip);
            })
            .catch(function (err) {
                console.error('windowed strip load failed', err);
                if (seq !== _stripRequestSeq || token !== _heroWindow) return;
                if (_stripFiguresBlank) showStripNotice(token);
            });
    }

    // Per applied body: the strip is asked for when the body carries newer data than the last
    // one, or when the picked window has no answered strip yet (the first poll, or a request
    // that failed).
    function refreshWindowedStrip(stampChanged) {
        if (stampChanged || _stripAnsweredToken !== _heroWindow) requestWindowedStrip(_heroWindow);
    }

    // The data moment a body describes: the same value #hero-data-as-of shows.
    function dataStamp(data) {
        var portfolio = ((data && data.meta) || {}).portfolio || {};
        return portfolio.data_as_of || (data && data.data_as_of) || null;
    }

    // ---------------------------------------------------------------------------
    // Exit-event line plugin (vertical breakline + badge for TP/Stop/VWAP exits)
    // ---------------------------------------------------------------------------

    function exitReasonLabel(reason) {
        if (!reason) return 'Exit';
        var r = reason.toLowerCase();
        if (r.indexOf('take') !== -1 || r.indexOf('tp') !== -1) return 'TP';
        if (r.indexOf('vwap') !== -1) return 'VWAP';
        return 'Stop';
    }

    function exitReasonColor(reason) {
        if (!reason) return cs('--studio-neg');
        var r = reason.toLowerCase();
        if (r.indexOf('take') !== -1 || r.indexOf('tp') !== -1) return cs('--studio-pos');
        if (r.indexOf('vwap') !== -1) return cs('--studio-plum');
        return cs('--studio-neg');
    }

    // Returns a Chart.js v4 inline plugin that draws a vertical hairline + badge.
    // exitIdx: index into the labels array; label: badge text; color: hex/rgb string
    // isMini: true for sparkline (smaller badge), false for detail chart
    // exitIdx, label and color may be functions, resolved at draw (a poll moves the
    // labels and a theme change moves the colour).
    function makeExitLinePlugin(exitIdxArg, labelArg, colorArg, isMini) {
        return {
            id: 'exitLine',
            afterDraw: function (chart) {
                var exitIdx = typeof exitIdxArg === 'function' ? exitIdxArg(chart) : exitIdxArg;
                if (exitIdx < 0 || exitIdx >= chart.data.labels.length) return;
                var label = typeof labelArg === 'function' ? labelArg() : labelArg;
                var color = typeof colorArg === 'function' ? colorArg() : colorArg;
                var ctx = chart.ctx;
                var xScale = chart.scales.x;
                var yScale = chart.scales.y;
                if (!xScale || !yScale) return;
                var x = xScale.getPixelForValue(exitIdx);
                var top = yScale.top;
                var bottom = yScale.bottom;

                ctx.save();
                ctx.strokeStyle = color;
                ctx.lineWidth = isMini ? 1 : 1.5;
                ctx.setLineDash([3, 3]);
                ctx.beginPath();
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();

                var pad = 4;
                var fontSize = isMini ? 8 : 10;
                ctx.font = '700 ' + fontSize + 'px system-ui, sans-serif';
                var tw = ctx.measureText(label).width;
                var bw = tw + pad * 2;
                var bh = fontSize + pad * 2;
                // Badge sits above the chart (top + 1px gap)
                var bx = Math.min(x - bw / 2, chart.chartArea.right - bw - 2);
                bx = Math.max(bx, chart.chartArea.left + 2);
                var by = top + 1;
                var r = 2;
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.moveTo(bx + r, by);
                ctx.lineTo(bx + bw - r, by);
                ctx.arcTo(bx + bw, by, bx + bw, by + r, r);
                ctx.lineTo(bx + bw, by + bh - r);
                ctx.arcTo(bx + bw, by + bh, bx + bw - r, by + bh, r);
                ctx.lineTo(bx + r, by + bh);
                ctx.arcTo(bx, by + bh, bx, by + bh - r, r);
                ctx.lineTo(bx, by + r);
                ctx.arcTo(bx, by, bx + r, by, r);
                ctx.closePath();
                ctx.fill();
                ctx.fillStyle = '#ffffff';
                ctx.textBaseline = 'middle';
                ctx.fillText(label, bx + pad, by + bh / 2);
                // Tick line from badge down to hairline
                ctx.strokeStyle = color;
                ctx.lineWidth = 1;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(x, by + bh);
                ctx.lineTo(x, by + bh + 4);
                ctx.stroke();
                ctx.restore();
            }
        };
    }

    // ---------------------------------------------------------------------------
    // Per-symphony sparklines
    // ---------------------------------------------------------------------------

    var SPARK_NO_DATA_TEXT = 'No intraday data yet';
    var SPARK_UNAVAILABLE_TEXT = 'Chart unavailable';

    function exitIndexFor(labels, sym) {
        if (!sym || !sym.triggered || !sym.triggered_at_time) return -1;
        var idx = labels.indexOf(sym.triggered_at_time);
        if (idx === -1) {
            // Partial match: find nearest label that starts with the exit time prefix
            var prefix = sym.triggered_at_time.slice(0, 5);
            idx = labels.findIndex(function (l) { return l && l.slice(0, 5) === prefix; });
        }
        return idx;
    }

    function sparkMarkerPlugin(symId) {
        return makeExitLinePlugin(
            function (chart) { return exitIndexFor(chart.data.labels, _sparkMarkers[symId]); },
            function () { return exitReasonLabel((_sparkMarkers[symId] || {}).triggered_reason); },
            function () { return exitReasonColor((_sparkMarkers[symId] || {}).triggered_reason); },
            true
        );
    }

    // Text shown where a card holds no chart; cleared once the canvas holds one.
    function setSparkStatus(canvas, text) {
        var parent = canvas.parentNode;
        if (!parent) return;
        var el = parent.querySelector('.spark-status');
        if (!el) {
            if (!text) return;
            parent.style.position = 'relative';
            el = document.createElement('span');
            el.className = 'spark-status legend-as-of';
            el.style.position = 'absolute';
            el.style.top = '50%';
            el.style.left = '50%';
            el.style.transform = 'translate(-50%, -50%)';
            parent.appendChild(el);
        }
        el.textContent = text;
    }

    // Names the session of a tape that is not today's: one span beside the canvas, text
    // "<session_date> session", present only while the tape's session differs from the ET
    // day. A body naming either date less (a store that records no date) names no session.
    // The date is written as text; the span is cleared by the next tape that is today's.
    function setSparkSessionCaption(canvas, chartBody) {
        var parent = canvas.parentNode;
        if (!parent) return;
        var session = chartBody && typeof chartBody.session_date === 'string' ? chartBody.session_date : '';
        var today = chartBody && typeof chartBody.et_date === 'string' ? chartBody.et_date : '';
        var text = session && today && session !== today ? session + ' session' : '';
        var el = parent.querySelector('.spark-session');
        if (!el) {
            if (!text) return;
            // On its own line above the tape, not laid over it: the tape's line never runs
            // through the text.
            el = document.createElement('span');
            el.className = 'spark-session legend-as-of';
            el.style.display = 'block';
            el.style.textAlign = 'left';
            el.style.marginLeft = '2px';
            parent.insertBefore(el, canvas);
        }
        el.textContent = text;
        el.hidden = !text;
    }

    function sparkHeldDataset(data) {
        return {
            data: data.map(function (d) { return d.held != null ? d.held : null; }),
            borderColor: lineToken('spark', 1),
            borderWidth: 1,
            pointRadius: 0,
            tension: 0.3,
            fill: false,
            borderDash: [3, 3],
            spanGaps: false
        };
    }

    // Explicit y range: Chart.js default can clip lines when return values exceed [0,1].
    function sparkYRange(returns) {
        var vals = returns.filter(function (v) { return v != null; });
        var dataMin = vals.length ? Math.min.apply(null, vals) : 0;
        var dataMax = vals.length ? Math.max.apply(null, vals) : 1;
        var dataPad = Math.max(Math.abs(dataMax - dataMin) * 0.08, 0.001);
        return { min: dataMin - dataPad, max: dataMax + dataPad };
    }

    // chartData null = the request failed. Returns the charts the card holds.
    function renderSparkline(symId, chartData, sym) {
        if (sym) _sparkMarkers[symId] = sym;
        var data = chartData && Array.isArray(chartData.data) ? chartData.data : null;
        var held = [];
        var canvases = document.querySelectorAll('[data-testid="card-spark"][data-sym-id="' + symId + '"]');
        canvases.forEach(function (canvas) {
            var chart = chartOn(canvas);
            if (!data || !data.length) {
                // A drawn chart stays as it is; only a canvas with none gets the status text.
                if (chart) { held.push(chart); return; }
                setSparkStatus(canvas, data ? SPARK_NO_DATA_TEXT : SPARK_UNAVAILABLE_TEXT);
                return;
            }
            var ctx = canvas.getContext('2d');
            if (!ctx) return;
            setSparkSessionCaption(canvas, chartData);
            var labels = data.map(function (d) { return d.time || ''; });
            var returns = data.map(function (d) { return d['return'] !== undefined ? d['return'] : 0; });
            var hasHeld = data.some(function (d) { return d.held != null; });
            var range = sparkYRange(returns);
            var padTop = exitIndexFor(labels, _sparkMarkers[symId]) >= 0 ? 18 : 2;
            if (chart) {
                chart.data.labels = labels;
                chart.data.datasets[0].data = returns;
                if (chart.data.datasets[1]) {
                    chart.data.datasets[1].data = sparkHeldDataset(data).data;
                } else if (hasHeld) {
                    chart.data.datasets.push(sparkHeldDataset(data));
                }
                chart.options.scales.y.min = range.min;
                chart.options.scales.y.max = range.max;
                chart.options.layout.padding.top = padTop;
                chart.update('none');
                held.push(chart);
                return;
            }
            var datasets = [{
                data: returns,
                borderColor: lineToken('spark', 0),
                borderWidth: 1.5,
                pointRadius: 0,
                tension: 0.3,
                fill: false
            }];
            if (hasHeld) datasets.push(sparkHeldDataset(data));
            chart = createChart(canvas, {
                type: 'line',
                data: { labels: labels, datasets: datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    // Top padding carves space above the chart area for the exit badge.
                    layout: { padding: { top: padTop, bottom: 2, left: 2, right: 2 } },
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false },
                        y: { display: false, min: range.min, max: range.max }
                    }
                },
                plugins: [sparkMarkerPlugin(symId)]
            });
            if (!chart) {  // no chart library: the card shows its status text instead
                setSparkStatus(canvas, SPARK_UNAVAILABLE_TEXT);
                return;
            }
            setSparkStatus(canvas, '');
            held.push(chart);
        });
        return held;
    }

    function drawnPromise(charts) {
        return Promise.all(charts.map(function (c) { return c.__drawn; }));
    }

    // The state map holds symphony entries and flat metadata keys; a symphony entry is an
    // object carrying a name (the server's own rule for telling them apart).
    function isSymphonyEntry(v) {
        return !!v && typeof v === 'object' && !Array.isArray(v) && 'name' in v;
    }

    // Newest request wins, per symphony: a chart answer applies only while no later request for
    // the same symphony has been sent, so a slow older answer never replaces a newer tape.
    var _chartRequestSeq = {};

    // Fetches one card's intraday series and draws it. Never rejects; resolves once the
    // request has settled and the card's chart has drawn (a card showing status text
    // holds none). onCharts, when given, receives the card's charts as soon as the
    // request has settled (an empty list for an answer a newer request has replaced).
    // The card's MC dial is not written here: it follows the poll's reading (updateCards).
    function loadSpark(sym, onCharts) {
        var symId = sym.id;
        var seq = (_chartRequestSeq[symId] || 0) + 1;
        _chartRequestSeq[symId] = seq;
        function replaced() { return seq !== _chartRequestSeq[symId]; }
        return fetch('/api/chart/' + symId)
            .then(function (r) {
                if (!r.ok) throw new Error('chart request failed');
                return r.json();
            })
            .then(function (data) {
                if (!data || typeof data !== 'object') throw new Error('chart body unreadable');
                if (replaced()) { if (onCharts) onCharts([]); return; }
                var charts = renderSparkline(symId, data, sym);
                if (onCharts) onCharts(charts);
                return drawnPromise(charts);
            })
            .catch(function () {
                if (replaced()) { if (onCharts) onCharts([]); return; }
                var charts = renderSparkline(symId, null, sym);
                if (onCharts) onCharts(charts);
                return drawnPromise(charts);
            });
    }

    // The tapes change only when the engine writes a new cycle: a refresh whose data stamp is
    // the one already applied (the first poll after load included, which is compared with the
    // stamp the page was rendered with) asks for none. A refresh with newer data asks once per
    // symphony entry.
    function loadCharts(botState, stampChanged) {
        if (!stampChanged) return Promise.resolve([]);
        var settled = [];
        Object.keys(botState || {}).forEach(function (key) {
            var sym = botState[key];
            if (!isSymphonyEntry(sym) || !sym.id) return;
            settled.push(loadSpark(sym));
        });
        return Promise.all(settled);
    }

    // The stamp the server render printed beside the hero chart: the data block's data_stamp,
    // else the text the render put in #hero-data-as-of (AS_OF_UNKNOWN when the render had none).
    var AS_OF_PREFIX = 'data as of ';
    var AS_OF_UNKNOWN = '--';
    function seedAsOfStamp(block) {
        var stamp = block && typeof block.data_stamp === 'string' ? block.data_stamp : null;
        _lastRefreshStamp = stamp;
        if (stamp === null) {
            var el = document.getElementById('hero-data-as-of');
            var shown = el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
            if (shown.indexOf(AS_OF_PREFIX) === 0) stamp = shown.slice(AS_OF_PREFIX.length);
        }
        _asOfStamp = stamp;
    }

    // Draws the hero and the card sparklines from the server render, before any
    // /api/state answer, then marks the page ready once everything has drawn.
    function paintFirstFrame() {
        var block = null;
        var node = document.getElementById('dashboard-initial-data');
        if (node) {
            try { block = JSON.parse(node.textContent); } catch (e) { block = null; }
        }
        // The render's data stamp is the baseline the first poll is compared with, and the one
        // the as-of text keeps beside a connection-lost cue until a poll names a newer one.
        seedAsOfStamp(block);
        // Ready is set synchronously at the moment the last held item is released.
        var holds = 0;
        function hold() {
            var released = false;
            holds += 1;
            return function () {
                if (released) return;
                released = true;
                holds -= 1;
                if (holds === 0) markDashboardReady();
            };
        }
        var setupDone = hold();

        var hero = block ? setHeroSeries(block.hero) : null;
        if (hero) whenDrawn(hero, hold());
        else showHeroEmpty();
        _pollTail = (block && block.hero_poll_tail) || null;
        _pollCap = (block && typeof block.hero_poll_cap === 'number') ? block.hero_poll_cap : null;
        var sparks = (block && block.sparks) || {};
        var seen = {};
        document.querySelectorAll('[data-testid="card-spark"][data-sym-id]').forEach(function (canvas) {
            var id = canvas.getAttribute('data-sym-id');
            if (!id || seen[id]) return;
            seen[id] = true;
            var release = hold();
            loadSpark(Object.assign({ id: id }, sparks[id]), function (charts) {
                var left = charts.length;
                if (!left) release();
                charts.forEach(function (c) {
                    whenDrawn(c, function () { left -= 1; if (left === 0) release(); });
                });
            });
        });
        setupDone();
    }

    // ---------------------------------------------------------------------------
    // Card verdict (guard_alpha from bot_state)
    // ---------------------------------------------------------------------------

    // Verdict wording. The server renders the same text at first paint
    // (analytics.js_to_fixed mirrors toFixed), so a poll never changes it.
    function verdictText(ga) {
        return ga > 0
            ? 'Good call · saved +' + ga.toFixed(1) + '%α'
            : 'Early exit · gave up ' + Math.abs(ga).toFixed(1) + '%α';
    }

    // The figure is the exit return minus the card's current return: it moves with the card
    // while the market is open and is fixed at the capture after the close.
    function verdictBasisLabel(marketState) {
        return marketState === 'open' ? 'vs held now' : 'at close';
    }

    // From this magnitude toFixed prints exponent text. The server render prints the dash for
    // such a figure (analytics.JS_TO_FIXED_EXPONENT_FROM), so a poll must not print the text.
    var JS_TO_FIXED_EXPONENT_FROM = 1e21;
    var VERDICT_NO_VALUE_TEXT = '—';

    // A guard alpha the verdict can print: a finite number below the bound. Anything else (absent,
    // null, a string, NaN, a figure too large to print) has no verdict figure.
    function usableGuardAlpha(ga) {
        return typeof ga === 'number' && isFinite(ga) && Math.abs(ga) < JS_TO_FIXED_EXPONENT_FROM;
    }

    // Writes the text span and the basis label beside it. A usable guard alpha writes its
    // wording and shows the label; any other value writes the dash and hides the label (a label
    // is never shown beside no value), the server render's rule for the same entry.
    function writeVerdict(verdict, ga, marketState) {
        if (verdict == null) return;
        var usable = usableGuardAlpha(ga);
        var textEl = verdict.querySelector('[data-field="verdict-text"]');
        if (textEl) textEl.textContent = usable ? verdictText(ga) : VERDICT_NO_VALUE_TEXT;
        var qualifier = verdict.querySelector('[data-testid="guard-alpha-basis"]');
        if (qualifier) {
            if (usable) {
                qualifier.textContent = verdictBasisLabel(marketState);
                qualifier.removeAttribute('hidden');
            } else {
                qualifier.setAttribute('hidden', '');
            }
        }
    }

    function updateVerdicts(botState, marketState) {
        var cards = document.querySelectorAll('[data-testid="triggered-verdict"]');
        cards.forEach(function (el) {
            var card = el.closest('[data-testid="sym-card"]');
            if (!card) return;
            var canvas = card.querySelector('[data-testid="card-spark"]');
            var symId = canvas ? canvas.getAttribute('data-sym-id') : null;
            if (!symId) return;
            var sym = (botState || {})[symId];
            if (!sym) return;
            writeVerdict(el, sym.guard_alpha, marketState);
        });
    }

    // ---------------------------------------------------------------------------
    // Cash Now
    // ---------------------------------------------------------------------------

    function cashNow(e, symId) {
        e.stopPropagation();
        var btn = e.currentTarget;
        var symName = symId;
        var card = btn.closest('[data-testid="sym-card"]');
        if (card) {
            var nameEl = card.querySelector('.card-name');
            if (nameEl) symName = nameEl.textContent.trim();
        }
        if (!window.confirm('Sell "' + symName + '" to cash?\nThis will execute a live sell order.')) return;
        btn.disabled = true;
        btn.textContent = 'Selling…';
        // The poll leaves a button in this state alone (updateCards): it is enabled again only by
        // a refusal below, never by a card that reads as not exited.
        btn.setAttribute('data-cash-state', 'selling');
        // The note beside the button says why a click did not sell; the next click clears it.
        var note = btn.parentNode ? btn.parentNode.querySelector('[data-testid="cash-now-note"]') : null;
        if (note) note.textContent = '';
        function refuse(text) {
            btn.disabled = false;
            btn.textContent = 'Cash Now';
            btn.removeAttribute('data-cash-state');
            if (!btn.parentNode) return;
            if (!note) {
                note = document.createElement('span');
                note.setAttribute('data-testid', 'cash-now-note');
                note.setAttribute('role', 'status');
                btn.parentNode.appendChild(note);
            }
            note.textContent = text;
        }
        PSRequest.postAction('/api/sell_account', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ symphony_id: symId })
        }).then(function (res) {
            if (res.ok) {
                btn.textContent = 'In cash';
                // Sold in this page load: no later poll turns the button back on.
                btn.setAttribute('data-cash-state', 'sold');
                return;
            }
            if (res.networkError) {
                refuse('Not sold: the request failed. Check the connection, then click Cash Now again.');
            } else if (res.refused === 'csrf') {
                refuse(PSRequest.notSentText(res, 'Click Cash Now again.'));
            } else if (res.body && typeof res.body.message === 'string' && res.body.message) {
                // The route's own refusal (a 400 names the missing or mismatched confirmation).
                refuse('Not sold: ' + res.body.message);
            } else {
                refuse('Not sold: the server refused the request (HTTP ' + res.status + ').');
            }
        });
    }

    window.cashNow = cashNow;

    // ---------------------------------------------------------------------------
    // Fleet-alert dismissal. The banner hides only after the server accepts the
    // dismissal; a refusal keeps it shown and says so. The dismissed alert's
    // tripped_at_et is remembered so a stale poll cannot bring it back.
    // ---------------------------------------------------------------------------

    var FLEET_DISMISS_FAILED_TEXT = 'Dismissal not saved';

    function dismissFleetAlert(btn) {
        var banner = document.getElementById('fleet-correlation-banner');
        var notice = document.getElementById('fleet-dismiss-notice');
        var alertAtClick = window._lastFleetAlert;
        if (btn) btn.disabled = true;
        function refused(text) {
            if (notice) {
                notice.textContent = text;
                notice.removeAttribute('hidden');
            }
            if (btn) btn.disabled = false;
        }
        PSRequest.postAction('/api/fleet-alert/dismiss', {
            method: 'POST',
            headers: { 'X-CSRF-Token': PSRequest.csrfToken() || '' }
        }).then(function (res) {
            if (res.refused === 'csrf') {
                // The session check refused it: the banner stays and the next click sends one request.
                refused(res.tokenRefreshed
                    ? 'Not dismissed: click Dismiss again'
                    : 'Not sent: connection failed, click Dismiss again');
                return;
            }
            if (!res.ok) { refused(FLEET_DISMISS_FAILED_TEXT); return; }
            window._dismissedFleetTrippedAt =
                (alertAtClick && alertAtClick.tripped_at_et) || null;
            if (notice) {
                notice.textContent = FLEET_DISMISS_FAILED_TEXT;
                notice.setAttribute('hidden', '');
            }
            if (banner) banner.setAttribute('hidden', '');
            if (btn) btn.disabled = false;
        });
    }
    window.dismissFleetAlert = dismissFleetAlert;

    // ---------------------------------------------------------------------------
    // Detail panel
    // ---------------------------------------------------------------------------

    // The symphony the open panel shows, and the newest open: a request the panel makes applies
    // only while it is still the newest one for the symphony the panel is open on.
    var _detailSymId = null;
    var _detailRequestSeq = 0;
    var _detailNeedsFetch = false;  // opened before any poll named the symphony: ask once it does
    var DETAIL_UNKNOWN_TITLE = 'Symphony Detail';
    var DETAIL_NOT_IN_VIEW_TEXT = 'not in the current view';

    function detailPanelIsOpen() {
        var panel = document.getElementById('detail-panel');
        return !!panel && panel.classList.contains('open');
    }

    // The stats row and its sub-labels, from a state entry. Written at open and by every poll.
    function writeDetailFigures(sym) {
        // Live return: use triggered_at_return if triggered, else current_return
        // A triggered entry's figure is its frozen exit return; the live return is a different
        // quantity and never stands in for it. An unknown figure (absent, null, not finite, or the
        // engine's not-set sentinel) is a dash, never +0.00%: 0.0 is a known value, "unknown" is not.
        var rawLive = sym.triggered ? sym.triggered_at_return : sym.current_return;
        var liveRet = sentinelToNull(typeof rawLive === 'number' && isFinite(rawLive) ? rawLive : null);
        // sentinelToNull covers both -999 and +999 variants
        var stopLevel = sentinelToNull(typeof sym.stop_trigger === 'number' ? sym.stop_trigger : null);
        var shadowPeak = sentinelToNull(typeof sym.shadow_hwm === 'number' ? sym.shadow_hwm : null);
        // guard_alpha may be present even for triggered symphonies
        var ga = typeof sym.guard_alpha === 'number' ? sym.guard_alpha
               : (sym.guard_alpha != null ? parseFloat(sym.guard_alpha) : null);
        if (isNaN(ga) || isSentinel(ga)) ga = null;

        var elLive = document.getElementById('dp-live-ret');
        if (elLive) {
            elLive.textContent = liveRet === null ? '—' : fmtPct(liveRet);
            elLive.style.color = liveRet === null ? cs('--studio-ink-dim')
                : (liveRet >= 0 ? cs('--studio-pos') : cs('--studio-neg'));
        }
        var elLiveSub = document.getElementById('dp-live-ret-sub');
        if (elLiveSub) elLiveSub.textContent = sym.triggered ? 'frozen at exit' : 'real-time';

        var elStop = document.getElementById('dp-stop-level');
        if (elStop) elStop.textContent = stopLevel != null ? fmtPct(stopLevel) : '--';
        var elStopSub = document.getElementById('dp-stop-sub');
        if (elStopSub) elStopSub.textContent = sym.breakeven_locked ? '◆ breakeven lock' : 'vol-scaled';

        var elPeak = document.getElementById('dp-shadow-peak');
        if (elPeak) {
            elPeak.textContent = shadowPeak != null ? fmtPct(shadowPeak) : '--';
            if (shadowPeak != null) elPeak.style.color = cs('--studio-pos');
        }

        var elAlpha = document.getElementById('dp-alpha');
        if (elAlpha) {
            elAlpha.textContent = ga != null ? fmtPct(ga) : '--';
            if (ga != null) elAlpha.style.color = ga >= 0 ? cs('--studio-pos') : cs('--studio-neg');
        }
        var elAlphaSub = document.getElementById('dp-alpha-sub');
        if (elAlphaSub) elAlphaSub.textContent = ga != null ? (ga >= 0 ? 'saved vs hold' : 'gave up vs hold') : 'not exited';

        return { stopLevel: stopLevel, shadowPeak: shadowPeak };
    }

    // The name the card carries, for a panel opened before any state named its symphony.
    function cardNameFor(symId) {
        if (!symId) return null;
        var safeId = String(symId).replace(/["\\]/g, '');
        var nameEl = document.querySelector('.sym-card[data-sym-id="' + safeId + '"] .card-name');
        var text = nameEl ? nameEl.textContent.trim() : '';
        return text || null;
    }

    function writeDetailTitle(sym, symId) {
        var titleEl = document.getElementById('detail-panel-title');
        if (!titleEl) return;
        // An entry that names no usable symphony keeps the default title; only a panel opened
        // before any state named its symphony reads the name the card carries.
        titleEl.textContent = sym
            ? (sym.name || DETAIL_UNKNOWN_TITLE)
            : (cardNameFor(symId) || DETAIL_UNKNOWN_TITLE);
    }

    // The panel says it is waiting from the moment it opens until the first state names its
    // symphony (or says it is not in the view): never a panel of dashes with no explanation.
    function setDetailLoading(waiting) {
        var loadingEl = document.getElementById('detail-loading');
        if (loadingEl) loadingEl.hidden = !waiting;
    }

    // The panel's own requests: the tape, the events, the variables and the risk profile. Each
    // answer applies only while this is still the newest open, on a panel that is still open.
    // The Risk Profile's window: the trailing rows (trading days) the request asks for, also named
    // on the panel so its figures are not read against the card's different window.
    var RISK_PROFILE_DAYS = 60;

    function startDetailRequests(symId, sym, seq) {
        fetchDetailChart(sym.id || symId, sym, seq);

        // Phase 2b (ux-design-deliverable.md §Change 5): Risk Profile section —
        // per-symphony Sharpe / Sortino / Max DD / Ann. volatility, bot vs if-held.
        // Fetched from /api/performance?scope=symphony so the panel shows the same
        // risk-adjusted metrics as the Performance page. Read-only; no engine rerun.
        var rpEl = document.getElementById('detail-risk-profile');
        if (!rpEl) return;
        function current() { return seq === _detailRequestSeq && detailPanelIsOpen(); }
        fetch('/api/performance?scope=symphony&symphony_id=' + encodeURIComponent(symId) + '&days=' + RISK_PROFILE_DAYS)
            .then(function (r) { return r.json(); })
            .then(function (p) {
                if (!current()) return;
                var bot = p.shadow_metrics || {};
                var held = p.live_metrics || {};
                // [label, botVal, heldVal, kind, invertDelta]
                // kind 'num' => raw value (Sharpe/Sortino); 'pct_frac' => fraction scaled to %.
                // invertDelta true => lower is better (Max DD, Ann. volatility).
                var rows = [
                    ['Sharpe',          bot.sharpe,       held.sharpe,       'num',      false],
                    ['Sortino',         bot.sortino,      held.sortino,      'num',      false],
                    ['Max drawdown',    bot.max_drawdown, held.max_drawdown, 'pct_frac', true],
                    ['Ann. volatility', bot.volatility,   held.volatility,   'pct_frac', true]
                ];
                var html = rows.map(function (row) {
                    var label = row[0], bv = row[1], hv = row[2], kind = row[3], invert = row[4];
                    var hasBoth = (bv != null && hv != null);
                    var delta = hasBoth ? (bv - hv) : null;
                    var deltaGood = invert ? (delta <= 0) : (delta >= 0);
                    var dcol = (delta == null) ? 'inherit'
                             : deltaGood ? cs('--studio-pos') : cs('--studio-neg');
                    function fmt(v) {
                        if (v == null) return '—';
                        return (kind === 'num') ? v.toFixed(2) : (v * 100).toFixed(1) + '%';
                    }
                    var deltaStr = (delta == null) ? '—'
                        : (kind === 'num'
                            ? (delta >= 0 ? '+' : '') + delta.toFixed(2)
                            : (delta >= 0 ? '+' : '') + (delta * 100).toFixed(1) + 'pp');
                    // Match the existing .math-row pattern (index.html:1223-1258):
                    // label + value share a flex row; hint sits below on its own line.
                    return '<div class="math-row">'
                        + '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:2px;">'
                        + '<span class="math-row-label">' + label + '</span>'
                        + '<span class="math-row-value" style="color:' + dcol + ';">' + deltaStr + '</span>'
                        + '</div>'
                        + '<div class="math-row-hint">Bot ' + fmt(bv) + ' · Held ' + fmt(hv) + '</div>'
                        + '</div>';
                }).join('');
                rpEl.innerHTML = '<div class="math-row-hint">Last ' + RISK_PROFILE_DAYS + ' trading days</div>' + html;
            })
            .catch(function () {
                // Graceful: leave the section empty on fetch failure — no JS error surfaced.
                if (current()) rpEl.innerHTML = '';
            });
    }

    function openDetailPanel(symIdOrIdx) {
        document.getElementById('detail-panel').classList.add('open');
        document.getElementById('detail-scrim').classList.add('open');

        var symId = (typeof symIdOrIdx === 'string') ? symIdOrIdx : _symIds[symIdOrIdx];
        var sym = symId ? _botState[symId] : null;

        // Opening on another symphony takes the previous one's tape, events and variables off the
        // panel at once: nothing of it may show under the new title while the new answers arrive.
        var switched = _detailSymId !== null && symId !== _detailSymId;
        _detailSymId = symId || null;
        var seq = ++_detailRequestSeq;
        _detailChartReading = null;
        if (switched) clearDetailPanelBody();
        var rpEl = document.getElementById('detail-risk-profile');
        if (rpEl) rpEl.innerHTML = '';

        writeDetailTitle(sym, symId);
        // No poll has named this symphony yet: the first one fills the panel and asks for the rest.
        _detailNeedsFetch = !sym;
        setDetailLoading(!sym);
        if (!sym) return;

        var kept = writeDetailFigures(sym);
        // Populate Risk Math from bot state eagerly (B-04); chart fetch will overwrite with live data.
        populateRiskMathFromState(sym);
        startDetailRequests(symId, sym, seq);
    }

    // Takes the previous symphony's tape, events and variables off the panel.
    function clearDetailPanelBody() {
        if (_intradayChart) { _intradayChart.destroy(); _intradayChart = null; }
        var canvas = document.getElementById('intraday-canvas');
        if (canvas) setSparkStatus(canvas, '');
        disableOverlayToggles();
        var events = document.getElementById('events-timeline');
        if (events) events.textContent = '';
        var vars = document.getElementById('vars-section');
        if (vars) vars.textContent = '';
    }

    // The open panel follows each poll: the figures and the state-derived risk rows take the
    // symphony's newest entry, and the tape (with the chart's toggles) is left as it is. An entry
    // the body no longer carries leaves everything shown and says so; it reverts when the entry
    // returns. A panel opened before any poll named its symphony fills from the first one that does.
    function refreshOpenDetailPanel(botState) {
        if (!_detailSymId || !detailPanelIsOpen()) return;
        var sym = botState ? botState[_detailSymId] : null;
        setDetailLoading(false);
        if (!isSymphonyEntry(sym)) {
            var sub = document.getElementById('dp-live-ret-sub');
            if (sub) sub.textContent = DETAIL_NOT_IN_VIEW_TEXT;
            return;
        }
        writeDetailTitle(sym, _detailSymId);
        var kept = writeDetailFigures(sym);
        populateRiskMathFromState(sym, true);
        if (_detailNeedsFetch) {
            _detailNeedsFetch = false;
            startDetailRequests(_detailSymId, sym, _detailRequestSeq);
        }
    }

    // The chart's answer for the open symphony, kept so a poll's rows read the same as the chart's.
    // Taken off on every open: nothing of another symphony's tape may carry across.
    var _detailChartReading = null;

    // The Risk Math rows and hints, one rule for every writer. Vol and MC are the state's value when
    // it is a usable number, else the chart's reading; MC is no reading for a triggered symphony; the
    // stop level is the state's, else the chart's last stop, against the state's peak.
    function riskMathRows(sym) {
        var chart = _detailChartReading || {};
        var usable = function (v) { return sentinelToNull(typeof v === 'number' ? v : null); };
        var pick = function (own, fallback) { var v = usable(own); return v != null ? v : fallback; };
        var mc = sym.triggered ? null : pick(sym.mc_prob, chart.mc != null ? chart.mc : null);
        var vol = pick(sym.symphony_vol, chart.vol != null ? chart.vol : null);
        var stopLevel = pick(sym.stop_trigger, chart.stop != null ? chart.stop : null);
        var peak = usable(sym.shadow_hwm);
        var para = usable(sym.para_velocity);
        return {
            mc: mc,
            stop: (peak != null && stopLevel != null) ? (peak - stopLevel).toFixed(2) + '%' : '--',
            vol: vol != null ? vol.toFixed(3) : '--',
            para: para != null ? para.toFixed(2) + '%' : '--',
            paraHint: para != null ? 'active ratchet velocity' : 'threshold —',
            be: sym.breakeven_locked ? 'Active · floor 0.00%' : 'Off',
            beHint: sym.breakeven_locked ? '5 confirm ticks @ lock threshold' : 'not yet activated'
        };
    }

    // Writes the rows a state entry (with the chart's reading, once it has answered) can fill. The
    // VWAP row is the tape's alone, so a poll passes keepVwap and leaves what the chart gave it.
    function populateRiskMathFromState(sym, keepVwap) {
        var rows = riskMathRows(sym);
        var mcVal = rows.mc != null ? rows.mc.toFixed(1) + '%' : '--';
        var fields = [
            { id: 'dp-rm-mc',   val: mcVal },
            { id: 'dp-rm-stop', val: rows.stop },
            { id: 'dp-rm-vol',  val: rows.vol },
            { id: 'dp-rm-para', val: rows.para },
            { id: 'dp-rm-para-hint', val: rows.paraHint },
            { id: 'dp-rm-be',   val: rows.be },
            { id: 'dp-rm-be-hint', val: rows.beHint }
        ];
        // vwap_pct does not exist on bot_state; the chart path fills this in via
        // last.vwap_diff in renderRiskMathPanel. Eager render shows '--' until chart loads.
        if (!keepVwap) {
            fields.push({ id: 'dp-rm-vwap', val: '--' });
            fields.push({ id: 'dp-rm-vwap-hint', val: '—' });
        }
        fields.forEach(function (f) {
            var el = document.getElementById(f.id);
            if (el) el.textContent = f.val;
        });
        // The reading's bar follows its text.
        var mcBar = document.getElementById('dp-rm-mc-bar');
        if (mcBar) {
            if (rows.mc != null) {
                mcBar.style.width = Math.max(0, Math.min(100, rows.mc)) + '%';
                mcBar.style.background = rows.mc < 15 ? cs('--studio-warn') : (rows.mc > 80 ? cs('--studio-ink-dim') : cs('--studio-accent'));
            } else {
                mcBar.style.width = '0%';
                mcBar.style.background = cs('--studio-ink-faint');
            }
        }
    }

    function fetchDetailChart(symId, sym, seq) {
        if (!symId) return;
        Promise.all([
            fetch('/api/chart/' + symId).then(function (r) { return r.json(); }).catch(function () { return null; }),
            fetch('/api/logs/' + symId).then(function (r) { return r.json(); }).catch(function () { return []; }),
            fetch('/api/settings').then(function (r) { return r.json(); }).catch(function () { return {}; })
        ]).then(function (results) {
            // Only the newest open's answers apply: the tape, the events and the variables of
            // an earlier symphony that land late are dropped.
            if (seq !== _detailRequestSeq || !detailPanelIsOpen()) return;
            // Pass settingsData (results[2]) to renderIntradayChart so the chart path
            // can read VWAP_BLEED_MULTIPLIER for the hint text (FIX 3 / Option A).
            renderIntradayChart(results[0], sym, results[2]);
            renderDetailLogs(results[1]);
            renderDetailVars(results[2], sym);
        });
    }

    function renderIntradayChart(chartData, sym, settingsData) {
        var canvas = document.getElementById('intraday-canvas');
        if (!canvas) return;
        // null = the request failed: a chart already drawn stays, an empty canvas says so.
        if (chartData === null || typeof chartData !== 'object') {
            if (!_intradayChart) setSparkStatus(canvas, SPARK_UNAVAILABLE_TEXT);
            return;
        }
        var data = chartData.data || [];
        if (!data.length) {
            // This symphony has no tape: the previous symphony's chart must not stay on the panel.
            if (_intradayChart) { _intradayChart.destroy(); _intradayChart = null; }
            disableOverlayToggles();
            setSparkStatus(canvas, SPARK_NO_DATA_TEXT);
            return;
        }
        setSparkStatus(canvas, '');

        if (_intradayChart) { _intradayChart.destroy(); _intradayChart = null; }

        var labels = data.map(function (d) { return d.time || ''; });
        var botData = data.map(function (d) { return sentinelToNull(d['return'] != null ? d['return'] : null); });
        var stopData = data.map(function (d) { return sentinelToNull(d.stop != null ? d.stop : null); });
        var breakevenData = data.map(function (d) { return sentinelToNull(d.breakeven != null ? d.breakeven : null); });
        var vwapData = data.map(function (d) { return sentinelToNull(d.vwap != null ? d.vwap : null); });
        var heldData = data.map(function (d) { return sentinelToNull(d.held != null ? d.held : null); });
        // mc_prob in data points is already 0-100 scale
        var mcData = data.map(function (d) { return sentinelToNull(d.mc_prob != null ? d.mc_prob : null); });

        var intradayPlugins = [];
        if (sym && sym.triggered && sym.triggered_at_time) {
            var iExitIdx = labels.indexOf(sym.triggered_at_time);
            if (iExitIdx === -1) {
                var iPrefix = sym.triggered_at_time.slice(0, 5);
                iExitIdx = labels.findIndex(function (l) { return l && l.slice(0, 5) === iPrefix; });
            }
            if (iExitIdx >= 0) {
                intradayPlugins.push(makeExitLinePlugin(
                    iExitIdx,
                    exitReasonLabel(sym.triggered_reason),
                    function () { return exitReasonColor(sym.triggered_reason); },
                    false
                ));
            }
        }

        // No chart library: the detail panel keeps its numbers (risk math below) without the chart.
        var ChartLib = chartLibrary();
        _intradayChart = !ChartLib ? null : new ChartLib(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    { label: 'Bot', data: botData, borderColor: lineToken('detail', 0), borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false, yAxisID: 'y' },
                    { label: 'Held', data: heldData, borderColor: lineToken('detail', 1), borderWidth: 1.5, pointRadius: 0, tension: 0.2, fill: false, borderDash: [4,4], yAxisID: 'y' },
                    { label: 'Stop', data: stopData, borderColor: lineToken('detail', 2), borderWidth: 1.5, pointRadius: 0, tension: 0, fill: false, borderDash: [4,3], yAxisID: 'y', hidden: false },
                    { label: 'Breakeven', data: breakevenData, borderColor: lineToken('detail', 3), borderWidth: 1.5, pointRadius: 0, tension: 0, fill: false, yAxisID: 'y', hidden: false },
                    { label: 'VWAP', data: vwapData, borderColor: lineToken('detail', 4), borderWidth: 1.4, pointRadius: 0, tension: 0.2, fill: false, yAxisID: 'y', hidden: true },
                    { label: 'MC %', data: mcData, borderColor: lineToken('detail', 5), borderWidth: 1.4, pointRadius: 0, tension: 0.3, fill: false, borderDash: [1,2], yAxisID: 'mc', hidden: true }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { display: false },
                    y: { position: 'left', ticks: { font: { size: 10 } } },
                    mc: { position: 'right', min: 0, max: 100, ticks: { font: { size: 10 } }, grid: { drawOnChartArea: false } }
                }
            },
            plugins: intradayPlugins
        });

        wireOverlayToggles();
        renderRiskMathPanel(chartData, sym, settingsData);
    }

    // With no chart the overlay toggles have nothing to switch.
    function disableOverlayToggles() {
        ['toggle-stop', 'toggle-breakeven', 'toggle-vwap', 'toggle-mc'].forEach(function (id) {
            var btn = document.getElementById(id);
            if (btn) btn.disabled = true;
        });
    }

    function wireOverlayToggles() {
        // Stop (idx 2) and Breakeven (idx 3) default ON; VWAP (4) and MC (5) default OFF.
        var toggleMap = {
            'toggle-stop':      { idx: 2, legendId: 'legend-stop' },
            'toggle-breakeven': { idx: 3, legendId: 'legend-breakeven' },
            'toggle-vwap':      { idx: 4, legendId: 'legend-vwap' },
            'toggle-mc':        { idx: 5, legendId: 'legend-mc' }
        };
        Object.keys(toggleMap).forEach(function (id) {
            var cfg = toggleMap[id];
            var btn = document.getElementById(id);
            if (!btn || !_intradayChart) return;

            var dsIdx = cfg.idx;
            var ds = _intradayChart.data.datasets[dsIdx];
            var hasData = ds && ds.data && ds.data.some(function (v) { return v != null; });
            btn.disabled = !hasData;
            btn.title = hasData ? '' : 'No data available';

            // Sync initial active state from dataset hidden flag
            var initiallyVisible = hasData && ds.hidden === false;
            btn.classList.toggle('active', initiallyVisible);
            var legendEl = cfg.legendId ? document.getElementById(cfg.legendId) : null;
            if (legendEl) legendEl.style.display = initiallyVisible ? 'inline-flex' : 'none';

            btn.onclick = function () {
                if (!hasData || !_intradayChart) return;
                var meta = _intradayChart.getDatasetMeta(dsIdx);
                var nowHidden = ds.hidden === true || meta.hidden === true;
                ds.hidden = !nowHidden;
                meta.hidden = !nowHidden;
                _intradayChart.update();
                var visible = !ds.hidden;
                btn.classList.toggle('active', visible);
                if (legendEl) legendEl.style.display = visible ? 'inline-flex' : 'none';
            };
        });
    }

    function renderRiskMathPanel(chartData, sym, settingsData) {
        var data = chartData.data || [];
        var last = data.slice(-1)[0] || {};
        sym = sym || {};

        // The chart's reading: the last row's vol and stop, and the newest MC reading found scanning
        // backwards (the top level is null; sentinels skipped). Whether it applies to a triggered
        // symphony is riskMathRows' rule, so a stale pre-trigger reading is never resurrected.
        var chartMc = null;
        for (var i = data.length - 1; i >= 0; i--) {
            var candidate = sentinelToNull(data[i].mc_prob != null ? data[i].mc_prob : null);
            if (candidate != null) { chartMc = candidate; break; }
        }
        _detailChartReading = {
            mc: chartMc,
            vol: sentinelToNull(last.vol != null ? last.vol : null),
            stop: sentinelToNull(last.stop != null ? last.stop : null)
        };
        populateRiskMathFromState(sym, true);

        // vwap_diff from last chart point as a proxy for VWAP state
        var rawVwapDiff = sentinelToNull(last.vwap_diff != null ? last.vwap_diff : null);
        var vwapVal = rawVwapDiff != null ? (rawVwapDiff >= 0 ? 'Above' : 'Below') + ' (' + rawVwapDiff.toFixed(2) + '%)' : '--';

        function setEl(id, text) { var e = document.getElementById(id); if (e) e.textContent = text; }

        // VWAP state with hint.
        // bleed mult lives in /api/settings response (settingsData.symphonies[name].params.VWAP_BLEED_MULTIPLIER);
        // sym.acc_VWAP_BLEED_MULTIPLIER does not exist on bot_state (FIX 3 / Option A).
        setEl('dp-rm-vwap', vwapVal);
        if (rawVwapDiff != null) {
            var bleedMult = null;
            if (settingsData) {
                var symName = sym.normalized_name || sym.name || '';
                var symId   = sym.id || '';
                var allSyms = Object.assign({}, settingsData.symphonies || {}, settingsData.symphony_overrides || {});
                var symStrat = allSyms[symName] || allSyms[symId];
                if (!symStrat) {
                    var allKeys = Object.keys(allSyms);
                    for (var ki = 0; ki < allKeys.length; ki++) {
                        var k = allKeys[ki];
                        if ((symName && (symName.indexOf(k) >= 0 || k.indexOf(symName) >= 0)) ||
                            (symId && (symId.indexOf(k) >= 0 || k.indexOf(symId) >= 0))) {
                            symStrat = allSyms[k];
                            break;
                        }
                    }
                }
                var bmRaw = symStrat && symStrat.params ? symStrat.params.VWAP_BLEED_MULTIPLIER : null;
                bleedMult = (bmRaw != null && typeof bmRaw === 'number') ? bmRaw : null;
            }
            setEl('dp-rm-vwap-hint', bleedMult != null ? 'bleed mult ' + bleedMult.toFixed(1) + '× vol' : 'VWAP defense active');
        } else {
            setEl('dp-rm-vwap-hint', '—');
        }

        // Panel footer
        var footerEl = document.getElementById('dp-footer-id');
        if (footerEl && sym && sym.id) {
            var normName = sym.normalized_name || sym.id.replace(/^sym_/, '');
            footerEl.textContent = 'id: ' + sym.id + ' · normalized: ' + normName;
        }
    }

    function renderDetailLogs(logs) {
        var container = document.getElementById('events-timeline');
        if (!container) return;
        var items = Array.isArray(logs) ? logs : (logs.events || []);
        if (!items.length) {
            var empty = document.createElement('div');
            empty.setAttribute('data-field', 'events-empty');
            empty.setAttribute('style', 'font-size:11px;color:var(--studio-ink-faint);');
            empty.textContent = eventsEmptyText();
            container.textContent = '';
            container.appendChild(empty);
            return;
        }
        var kindColorMap = {
            'trigger':  'var(--studio-plum)',
            'triggered':'var(--studio-plum)',
            'arm':      'var(--studio-warn)',
            'armed':    'var(--studio-warn)',
            'para':     'var(--studio-cyan)',
            'lock':     'var(--studio-accent)',
            'confirm':  'var(--studio-ink-dim)',
            'boot':     'var(--studio-ink-faint)',
            'info':     'var(--studio-ink-dim)',
            'error':    'var(--studio-neg)'
        };
        container.innerHTML = items.slice(0, 20).map(function (ev) {
            var ts = ev.timestamp || ev.time || '';
            var hhmm = ts.length >= 16 ? ts.slice(11, 16) : ts;
            var kind = (ev.event_type || ev.kind || 'info').toLowerCase();
            var msg = String(ev.message || ev.msg || '');
            var color = kindColorMap[kind] || 'var(--studio-ink-dim)';
            var kindLabel = kind.toUpperCase();
            // Extract reason from message if present
            var reasonMatch = msg.match(/^([A-Z_]+(?:\s[A-Z_]+)*):\s*/);
            var reason = reasonMatch ? msg.slice(reasonMatch[0].length) : msg;
            var kindDisplay = kindLabel + (reasonMatch ? ' · ' + reasonMatch[1] : '');
            // The message carries a symphony name (the engine logs "<name> ARMED (trail)") and a
            // name is authored outside this app, so every field of the row is written as text.
            return '<div class="event-row">' +
                '<span class="event-ts">' + escHtml(hhmm) + '</span>' +
                '<div class="event-body">' +
                  '<span class="event-dot" style="background:' + color + ';"></span>' +
                  '<div class="event-kind" style="color:' + color + ';">' + escHtml(kindDisplay) + '</div>' +
                  '<div class="event-detail">' + escHtml(reason) + '</div>' +
                '</div>' +
                '</div>';
        }).join('');
    }

    function renderDetailVars(settingsData, sym) {
        var container = document.getElementById('vars-section');
        if (!container || !sym) return;
        var symName = sym.normalized_name || sym.name || '';
        var symId   = sym.id || '';
        var allSyms = Object.assign({}, settingsData.symphonies || {}, settingsData.symphony_overrides || {});
        // Try exact match by normalized_name, then by id, then by partial match
        var symStrat = allSyms[symName] || allSyms[symId];
        if (!symStrat) {
            var allKeys = Object.keys(allSyms);
            for (var i = 0; i < allKeys.length; i++) {
                var k = allKeys[i];
                if ((symName && (symName.indexOf(k) >= 0 || k.indexOf(symName) >= 0)) ||
                    (symId && (symId.indexOf(k) >= 0 || k.indexOf(symId) >= 0))) {
                    symStrat = allSyms[k];
                    break;
                }
            }
        }
        var params = symStrat ? (symStrat.params || {}) : null;
        var locked = symStrat ? new Set(symStrat.locked_vars || []) : new Set();
        if (!params || Object.keys(params).length === 0) {
            container.innerHTML = '<div class="var-row"><span class="var-key">No variables configured</span></div>';
            return;
        }
        container.innerHTML = Object.entries(params).map(function (pair) {
            var k = pair[0]; var v = pair[1];
            var lockBadge = locked.has(k) ? ' <span style="color:var(--studio-accent);font-size:9px;" title="Locked from autotuner">&#9670;</span>' : '';
            // The name and a string value are written as text; the lock badge is this file's own markup.
            return '<div class="var-row">' +
                '<span class="var-key">' + escHtml(k) + lockBadge + '</span>' +
                '<span class="var-val">' + (typeof v === 'number' ? v.toFixed(v % 1 === 0 ? 0 : 3) : escHtml(v)) + '</span>' +
                '</div>';
        }).join('');
    }

    window.openDetailPanel = openDetailPanel;

    // ---------------------------------------------------------------------------
    // MC dial rendering
    // ---------------------------------------------------------------------------

    var MC_CIRCUMFERENCE = 94.25; // 2 * π * 15

    function renderMcDial(symId, chartData) {
        var dials = document.querySelectorAll('[data-testid="mc-dial"][data-sym-id="' + symId + '"]');

        // Exited/triggered: actively render an honest "-" state. Never scan
        // history for a stale pre-trigger reading (that would resurrect it as
        // "current"), and never silently skip the render (that freezes the
        // dial at whatever was last drawn) -- both are the F7 defects.
        // The arc colour is a token reference, never a computed colour, so a theme
        // change recolours it with no redraw.
        function showNoReading() {
            dials.forEach(function (svg) {
                var arc = svg.querySelector('.mc-arc');
                var text = svg.querySelector('.mc-text');
                if (arc) {
                    arc.setAttribute('stroke-dashoffset', MC_CIRCUMFERENCE.toFixed(2));
                    arc.style.stroke = 'var(--studio-ink-faint)';
                }
                if (text) text.textContent = '—';
            });
        }
        if (chartData && chartData.triggered) {
            showNoReading();
            return;
        }

        // mc_prob in chart data points is 0-100 scale; top-level chartData.mc_prob may be null.
        // Use last non-null mc_prob from data array, falling back to top-level fields.
        var mcProb = null;
        var dataPoints = chartData.data || [];
        for (var i = dataPoints.length - 1; i >= 0; i--) {
            if (dataPoints[i].mc_prob != null) { mcProb = dataPoints[i].mc_prob; break; }
        }
        if (mcProb === null) mcProb = chartData.mc_prob != null ? chartData.mc_prob : (chartData.mc_pct != null ? chartData.mc_pct : null);
        // No reading (the engine's +/-999, or none at all) reads the dash, the same as a first
        // paint: an older reading never stands in for a missing one.
        if (mcProb === null || typeof mcProb !== 'number' || !isFinite(mcProb) || isSentinel(mcProb)) {
            showNoReading();
            return;
        }

        // Normalise: values arrive as 0-100 percentage
        var pct = Math.max(0, Math.min(100, mcProb)) / 100;
        var offset = MC_CIRCUMFERENCE * (1 - pct);
        var label = (pct * 100).toFixed(0) + '%';
        // Threshold color per design Dial: <15 → warn (amber), >80 → inkDim, else accent
        var arcColor = mcProb < 15 ? 'var(--studio-warn)'
                     : mcProb > 80 ? 'var(--studio-ink-dim)'
                     : 'var(--studio-accent)';
        dials.forEach(function (svg) {
            var arc = svg.querySelector('.mc-arc');
            var text = svg.querySelector('.mc-text');
            if (arc) {
                arc.setAttribute('stroke-dashoffset', offset.toFixed(2));
                arc.style.stroke = arcColor;
            }
            if (text) text.textContent = label;
        });
    }

    // ---------------------------------------------------------------------------
    // Comparison rows (Today / Cumulative / Max DD) — live update from portfolio_strip
    // ---------------------------------------------------------------------------

    function setPosNeg(el, value) {
        if (!el) return;
        el.classList.remove('pos', 'neg');
        if (value >= 0) el.classList.add('pos');
        else el.classList.add('neg');
    }

    // Marks the leader of a comparison row as the server render does: the leader's bar fill
    // carries `winner` and the other's `loser`, and the leader's label (the `.vs-bar-label` of the
    // same row, bot first) carries `winner`; a label never carries `loser`. botWins null means no
    // side leads, so no element carries a class. Classes other than these two are left alone.
    function applyBarClasses(botFills, heldFills, botWins) {
        function mark(els, cls) {
            Array.prototype.forEach.call(els, function (el) {
                el.classList.remove('winner', 'loser');
                if (cls) el.classList.add(cls);
            });
        }
        mark(botFills, botWins === null ? '' : (botWins ? 'winner' : 'loser'));
        mark(heldFills, botWins === null ? '' : (botWins ? 'loser' : 'winner'));
        var anchor = botFills.length ? botFills[0] : (heldFills.length ? heldFills[0] : null);
        var row = anchor && typeof anchor.closest === 'function' ? anchor.closest('.vs-row') : null;
        var labels = row ? row.querySelectorAll('.vs-bar-label') : [];
        if (labels.length >= 2) {
            labels[0].classList.remove('winner');
            labels[1].classList.remove('winner');
            if (botWins !== null) labels[botWins ? 0 : 1].classList.add('winner');
        }
    }

    // BL-4 (account-basis honesty render, DE-AUDIT-BL4-001): the backend
    // already computes portfolio_strip["basis"] (Tier-2 value-weighted
    // floor) and portfolio_strip["account_basis_stale"]/["account_basis_as_of"]
    // (Tier-1 stale-last-good stamp) -- see app.py -- but nothing on the
    // dashboard surfaced them, so an operator saw a stale/floor account
    // figure with no visible indicator. Renders a small disclosure chip on
    // the Today/Cumulative comparison rows only (never Max DD, which is not
    // account-basis-derived). Priority rule (approved team-lead ruling):
    // when both flags are true simultaneously, STALE wins -- it is the more
    // actionable disclosure and the only way to guarantee the two labels
    // never render simultaneously on one chip. Called every poll cycle, so
    // each branch explicitly resets the chip (a real stale-to-healthy
    // transition must un-show a chip left over from a prior poll).
    function renderAccountBasisChip(testid, ps) {
        var chip = document.querySelector('[data-testid="' + testid + '"]');
        if (!chip) return;
        if (ps.account_basis_stale) {
            chip.textContent = 'Stale account data (as of ' + ps.account_basis_as_of + ')';
            chip.hidden = false;
        } else if (ps.basis === 'value_weighted') {
            chip.textContent = 'Value-weighted floor — degraded account fetch';
            chip.hidden = false;
        } else {
            chip.textContent = '';
            chip.hidden = true;
        }
    }

    // Dedicated account-fetch freshness stamp -- distinct from the hero
    // chart legend's own data-as-of element (engine-cycle contract,
    // untouched). Only the STALE tier has an honest timestamp to disclose;
    // the value-weighted-floor tier has no account_basis_as_of at all, and
    // none may be fabricated.
    function renderAccountBasisFreshness(ps) {
        var el = document.getElementById('account-basis-as-of');
        if (!el) return;
        if (ps.account_basis_stale && ps.account_basis_as_of) {
            el.textContent = 'account data as of ' + ps.account_basis_as_of;
            el.hidden = false;
        } else {
            el.textContent = '';
            el.hidden = true;
        }
    }

    // Max DD labels on the cards: each card renders both the "insufficient history" label and the
    // alpha element, one hidden. They follow meta.portfolio.insufficient_history; a body that does
    // not say leaves them as they are. The hero row is not here: it follows the picked window's
    // strip (updateMddRow), so a poll never decides what it says.
    function applyMddLabels(data) {
        var portfolio = (data && data.meta && data.meta.portfolio) || {};
        var insufficient = portfolio.insufficient_history;
        if (typeof insufficient !== 'boolean') return;
        [['[data-testid="card-mdd-insufficient-label"]', '[data-field="mdd-alpha-badge"]']
        ].forEach(function (pair) {
            document.querySelectorAll(pair[0]).forEach(function (el) { el.hidden = !insufficient; });
            document.querySelectorAll(pair[1]).forEach(function (el) { el.hidden = insufficient; });
        });
    }

    function updateComparisonRows(data) {
        var ps = (data && data.portfolio_strip) || {};
        applyMddLabels(data);
        // The account-basis helpers run only on a genuine state-poll payload.
        // 'data_as_of' is unconditionally set by _compute_portfolio_strip on every
        // /api/state poll (app.py), so it discriminates a poll from any other payload
        // shape (the windowed strip never carries it, nor the basis keys: a healthy
        // state poll lacks those too, so their absence alone proves nothing).
        if ('data_as_of' in ps) {
            renderAccountBasisChip('comp-today-basis-chip', ps);
            renderAccountBasisChip('comp-cumulative-basis-chip', ps);
            renderAccountBasisFreshness(ps);
        }
        // AC-4a: each row names its alpha (.vs-delta) testid explicitly so the poll
        // can address + refresh the displayed alpha (comp-today-delta /
        // comp-cumulative-delta / comp-mdd-delta) — not just bot/held text.
        // Both rows are built only from a genuine /api/state poll payload (which always
        // carries data_as_of): a payload without it is on a different basis and scope (Today
        // can be account-basis on a real poll vs always-VW on a windowed strip), so leaving
        // `rows` empty leaves each row's last-correct DOM value alone instead of bouncing it.
        // The picked window never changes these two rows. The Max DD and Ann. Vol rows are
        // the strip's, not the poll's (see updateMddRow and updateVolRow): the poll's own Max DD
        // is a different window's figure and is never written under the row's label.
        var rows = [];
        if ('data_as_of' in ps) {
            rows = [
                { id: 'today',      deltaTestid: 'comp-today-delta',      values: ps.today_change      || {}, higherIsBetter: true },
                { id: 'cumulative', deltaTestid: 'comp-cumulative-delta', values: ps.cumulative_return || {}, higherIsBetter: true }
            ];
        }
        rows.forEach(function (row) {
            // F-016: sentinelToNull's null result must survive to fmtPct (which has
            // its own honest '--' branch for null) -- do NOT coerce to 0 here, that
            // fabricates a false "no change" reading for a genuinely missing value.
            var bot  = sentinelToNull(typeof row.values.dry_run === 'number' ? row.values.dry_run : (Number(row.values.dry_run) || null));
            var held = sentinelToNull(typeof row.values.if_held  === 'number' ? row.values.if_held  : (Number(row.values.if_held)  || null));
            var maxAbs = Math.max(Math.abs(bot), Math.abs(held), 1);

            var botBars  = document.querySelectorAll('[data-testid="comp-bar-bot"][data-row="'  + row.id + '"]');
            var heldBars = document.querySelectorAll('[data-testid="comp-bar-held"][data-row="' + row.id + '"]');

            botBars.forEach(function (el) {
                el.style.width = Math.min(Math.abs(bot) / maxAbs * 100, 100).toFixed(1) + '%';
            });
            heldBars.forEach(function (el) {
                el.style.width = Math.min(Math.abs(held) / maxAbs * 100, 100).toFixed(1) + '%';
            });
            // The leader, by the rule the server render applies to the row (a missing leg reads
            // as 0 there): the higher return leads.
            var botWins = (bot === null ? 0 : bot) >= (held === null ? 0 : held);
            applyBarClasses(botBars, heldBars, botWins);

            var botText  = document.querySelector('[data-testid="comp-' + row.id + '-bot-text"]');
            var heldText = document.querySelector('[data-testid="comp-' + row.id + '-held-text"]');
            if (botText) {
                botText.textContent = 'Bot ' + fmtPct(bot);
                // F-016: guard setPosNeg -- its `value >= 0` check is TRUE for null in
                // JS (null coerces to 0), which would mis-classify a missing value as
                // positive/green if called unguarded.
                if (bot !== null) setPosNeg(botText, bot);
                else botText.classList.remove('pos', 'neg');
            }
            if (heldText) {
                heldText.textContent = 'Held ' + fmtPct(held);
                if (held !== null) setPosNeg(heldText, held);
                else heldText.classList.remove('pos', 'neg');
            }

            // AC-4a: recompute + write the alpha (.vs-delta) span every poll so the
            // displayed alpha == displayed (Bot - Held) by construction. Without this
            // the delta stays frozen at its server-side-render value while Bot/Held
            // tick live (the 2.17-vs-1.72 staleness symptom).
            var deltaEl = document.querySelector('[data-testid="' + row.deltaTestid + '"]');
            if (deltaEl) {
                if (bot !== null && held !== null) {
                    // Bot minus Held, in percentage points (the unit of the headline above).
                    var deltaAlpha = bot - held;
                    deltaEl.textContent = 'α ' + fmtPp(deltaAlpha);
                    setPosNeg(deltaEl, deltaAlpha);
                } else {
                    deltaEl.textContent = 'α ' + fmtPp(null);
                    deltaEl.classList.remove('pos', 'neg');
                }
            }
        });
    }

    // ---------------------------------------------------------------------------
    // updateCards — refresh per-symphony card values from /api/state 'symphonies'
    //
    // Updates only the targeted data-field spans inside each [data-sym-id] card.
    // Does NOT touch card structure, layout, classes, or the sparkline <canvas>.
    // Cards go stale without this because the 30s poll (POLL_INTERVAL_MS) updates
    // hero widgets but the cards are server-rendered only on hard-refresh.
    // ---------------------------------------------------------------------------

    function _fmtSignedPct(val, decimals) {
        // A missing figure reads as the server render prints it: an em dash.
        if (val == null || isNaN(val)) return '\u2014';
        decimals = decimals != null ? decimals : 1;
        return (val >= 0 ? '+' : '') + val.toFixed(decimals) + '%';
    }

    function _fmtAbsPct(val, decimals) {
        if (val == null || isNaN(val)) return '--';
        decimals = decimals != null ? decimals : 1;
        return Math.abs(val).toFixed(decimals) + '%';
    }

    function _setPosNegClass(el, val) {
        if (el == null) return;
        if (val == null || isNaN(val)) return;
        el.classList.remove('pos', 'neg');
        el.classList.add(val >= 0 ? 'pos' : 'neg');
    }

    // Derive status-pill label + CSS class from per-symphony flags.
    // Mirrors the Jinja logic in index.html so both stay in sync.
    function _symStatusPill(sym) {
        if (sym.triggered) {
            var tr = sym.triggered_reason || '';
            if (tr.indexOf('Take-Profit') !== -1 || tr.indexOf('take_profit') !== -1) {
                return { text: 'TAKE-PROFIT', cls: 'tp' };
            }
            if (tr.indexOf('VWAP Bleed') !== -1 || tr.indexOf('vwap_bleed') !== -1) {
                return { text: 'VWAP BLEED', cls: 'bleed' };
            }
            if (tr.indexOf('VWAP') !== -1 || tr.indexOf('vwap') !== -1) {
                return { text: 'VWAP', cls: 'vwap' };
            }
            return { text: 'TRAILING STOP', cls: 'stop' };
        }
        if (sym.para_armed) { return { text: 'Para-Armed', cls: 'para-armed' }; }
        if (sym.tp_armed)   { return { text: 'TP-Armed',   cls: 'tp-armed'   }; }
        if (sym.armed)      { return { text: 'Armed',      cls: 'armed'      }; }
        return { text: 'Standby', cls: 'standby' };
    }

    function updateCards(data) {
        var syms = data.symphonies;
        if (!Array.isArray(syms)) return;

        syms.forEach(function (sym) {
            var id = sym.id;
            if (!id) return;

            // Update MC dial from sym.mc_prob on every poll. renderMcDial accepts a
            // chartData object; pass a minimal stub so the arc updates without a
            // full chart fetch. The mc-dial querySelector is inside renderMcDial.
            // Always call (never skip on null) -- an exited symphony's mc_prob is
            // honestly null (AC-1), and renderMcDial must actively render that
            // state rather than freeze at whatever was last drawn.
            renderMcDial(id, { triggered: sym.triggered, mc_prob: sym.mc_prob, data: [] });

            // Find the card element by data-sym-id attribute; there may be one in
            // active section and one in standby — update whichever exists.
            var cards = document.querySelectorAll('[data-sym-id="' + id + '"].sym-card');
            cards.forEach(function (card) {
                // Update status-pill text + class from sym.triggered/armed/tp_armed/para_armed.
                // Without this the pill stays at its server-rendered value (e.g. "Armed")
                // even after an exit fires on the next engine cycle.
                var pill = card.querySelector('[data-testid="status-pill"]');
                if (pill != null) {
                    var pillState = _symStatusPill(sym);
                    pill.className = pill.className.replace(
                        /\b(tp|bleed|vwap|stop|para-armed|tp-armed|armed|standby)\b/g, ''
                    ).replace(/\s+/g, ' ').trim();
                    pill.classList.add('status-pill', pillState.cls);
                    pill.textContent = pillState.text;
                }

                // Dual-value-headline + footer — querySelectorAll so BOTH tc-bot spans update
                // (headline dv-value and footer cfg-val share the same data-field attribute).
                card.querySelectorAll('[data-field="tc-bot"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.tc_bot); _setPosNegClass(el, sym.tc_bot); });
                card.querySelectorAll('[data-field="tc-held"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.tc_held); _setPosNegClass(el, sym.tc_held); });

                // Footer grid — querySelectorAll so every matching span updates.
                card.querySelectorAll('[data-field="cr-bot"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.cr_bot); _setPosNegClass(el, sym.cr_bot); });
                card.querySelectorAll('[data-field="cr-held"]').forEach(function (el) { el.textContent = _fmtSignedPct(sym.cr_held); _setPosNegClass(el, sym.cr_held); });
                card.querySelectorAll('[data-field="mdd-bot"]').forEach(function (el) { el.textContent = _fmtAbsPct(sym.mdd_bot); });
                card.querySelectorAll('[data-field="mdd-held"]').forEach(function (el) { el.textContent = _fmtAbsPct(sym.mdd_held); });
                // Composer's lifetime drawdown scalar, printed apart from the comparison above. A
                // body from before the key existed leaves the printed figure alone.
                if ('mdd_lifetime' in sym) {
                    card.querySelectorAll('[data-field="mdd-lifetime"]').forEach(function (el) {
                        el.textContent = _fmtAbsPct(sym.mdd_lifetime);
                    });
                }

                // Show/hide + update the triggered-verdict outcome banner.
                // The template always renders this element; display:none hides it until exit.
                // On each poll: show when sym.triggered, hide otherwise; set text from
                // sym.guard_alpha (carried in symphonies payload from this cycle).
                var verdict = card.querySelector('[data-testid="triggered-verdict"]');
                if (verdict != null) {
                    if (sym.triggered) {
                        verdict.style.display = '';
                        writeVerdict(verdict, sym.guard_alpha, data.market_state);
                    } else {
                        verdict.style.display = 'none';
                    }
                }

                // Cash Now is for a card that has not exited: disabled while the card reads as
                // triggered, enabled once it does not (a new session). A button whose sell is in
                // flight or done keeps that state (data-cash-state) whatever the card reads.
                var cashBtn = card.querySelector('[data-testid="cash-now-btn"]');
                if (cashBtn != null) {
                    var cashState = cashBtn.getAttribute('data-cash-state');
                    if (cashState !== 'selling' && cashState !== 'sold') {
                        var exited = !!sym.triggered;
                        if (cashBtn.disabled !== exited) cashBtn.disabled = exited;
                        if (cashBtn.textContent !== 'Cash Now') cashBtn.textContent = 'Cash Now';
                    }
                }

                // Update "Bot · frozen" / "Bot" dv-label when triggered state changes.
                // data-field="dv-label-bot" targets the label span in .dual-value-headline.
                var dvLabelBot = card.querySelector('[data-field="dv-label-bot"]');
                if (dvLabelBot != null) {
                    dvLabelBot.textContent = sym.triggered ? 'Bot · frozen' : 'Bot';
                }

                // Update footer alpha-badge labels (α delta on Today/Cum/Max DD headings).
                // data-field="tc-alpha-badge" / "cr-alpha-badge" / "mdd-alpha-badge"
                var tcAlpha  = (sym.tc_bot  != null && sym.tc_held  != null) ? sym.tc_bot  - sym.tc_held  : null;
                var crAlpha  = (sym.cr_bot  != null && sym.cr_held  != null) ? sym.cr_bot  - sym.cr_held  : null;
                // mdd alpha: held_abs - bot_abs (positive = bot had smaller drawdown = better)
                var mddAlpha = (sym.mdd_bot != null && sym.mdd_held != null)
                    ? Math.abs(sym.mdd_held) - Math.abs(sym.mdd_bot)
                    : null;

                var tcBadge  = card.querySelector('[data-field="tc-alpha-badge"]');
                var crBadge  = card.querySelector('[data-field="cr-alpha-badge"]');
                var mddBadge = card.querySelector('[data-field="mdd-alpha-badge"]');

                if (tcBadge != null) {
                    tcBadge.textContent = tcAlpha != null
                        ? 'α ' + (tcAlpha >= 0 ? '+' : '') + tcAlpha.toFixed(1)
                        : 'α --';
                    tcBadge.className = tcBadge.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                    if (tcAlpha != null) tcBadge.classList.add(tcAlpha >= 0 ? 'pos' : 'neg');
                }
                if (crBadge != null) {
                    crBadge.textContent = crAlpha != null
                        ? 'α ' + (crAlpha >= 0 ? '+' : '') + crAlpha.toFixed(1)
                        : 'α --';
                    crBadge.className = crBadge.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                    if (crAlpha != null) crBadge.classList.add(crAlpha >= 0 ? 'pos' : 'neg');
                }
                if (mddBadge != null) {
                    mddBadge.textContent = mddAlpha != null
                        ? 'α ' + (mddAlpha >= 0 ? '+' : '') + mddAlpha.toFixed(1)
                        : 'α --';
                    mddBadge.className = mddBadge.className.replace(/\bpos\b|\bneg\b/g, '').trim();
                    if (mddAlpha != null) mddBadge.classList.add(mddAlpha >= 0 ? 'pos' : 'neg');
                }

                // Section auto-partition: move the card to the correct section when status
                // changes.  Grouping rule matches app.py:474 exactly — a card is active if
                // ANY of the four flags is true; standby otherwise.  appendChild on a live
                // DOM node moves it (no recreation, sparkline canvas preserved).
                var isActive = sym.armed || sym.tp_armed || sym.para_armed || sym.triggered;
                var activeSection  = document.querySelector('[data-testid="active-section"]');
                var standbySection = document.querySelector('[data-testid="standby-section"]');
                if (activeSection && standbySection) {
                    var targetSection = isActive ? activeSection : standbySection;
                    // Prefer the .cards-grid child if present (matches template structure);
                    // fall back to the section container itself.
                    var targetGrid = targetSection.querySelector('.cards-grid') || targetSection;
                    if (card.parentNode !== targetGrid) {
                        targetGrid.appendChild(card);
                    }
                }
            });
        });
    }

    // Update section count badges and "data as of" time from poll response.
    // Section counts derive from data.symphonies list (active = armed/triggered/tp_armed/para_armed).
    // The "data as of" legend span refreshes from meta.portfolio.data_as_of each tick.
    // Entries of the state that belong to the picked account (all when none is picked).
    function _inPickedAccount(entry) {
        return !window.activeAccount || (entry && entry.account === window.activeAccount);
    }

    // Hide the cards of every other account while one is picked, and show the
    // all-accounts captions on the hero and strip, which always cover every account.
    function applyAccountFilter(data) {
        var stateObj = (data && (data.bot_state || data.state)) || {};
        var cards = document.querySelectorAll('.sym-card[data-sym-id]');
        Array.prototype.forEach.call(cards, function (card) {
            var show = _inPickedAccount(stateObj[card.getAttribute('data-sym-id')]);
            card.hidden = !show;
            card.style.display = show ? '' : 'none';
        });
        ['hero-scope-caption', 'strip-scope-caption'].forEach(function (id) {
            var cap = document.getElementById(id);
            if (cap) cap.hidden = !window.activeAccount;
        });
    }

    // The badges count the cards on the page: the cards of each section that are shown. The
    // card list places the cards (updateCards), the account filter hides the other accounts'
    // (applyAccountFilter), so this runs after both and reads what they left.
    function updateSectionMeta() {
        [['active-section', 'active-section-count'], ['standby-section', 'standby-section-count']]
            .forEach(function (pair) {
                var section = document.querySelector('[data-testid="' + pair[0] + '"]');
                var badge = document.querySelector('[data-testid="' + pair[1] + '"]');
                if (!section || !badge) return;
                var shown = 0;
                section.querySelectorAll('.sym-card').forEach(function (card) {
                    if (!card.hidden && card.style.display !== 'none') shown += 1;
                });
                badge.textContent = shown;
            });
    }

    // Each section lists its cards in the order of the response's card list (the order the page
    // was rendered in). A card is moved only when the order differs, and moving a node keeps its
    // canvas, so no chart is rebuilt.
    function orderSectionCards(data) {
        var syms = data && data.symphonies;
        if (!Array.isArray(syms)) return;
        var rank = {};
        syms.forEach(function (sym, i) {
            if (sym && sym.id && !(sym.id in rank)) rank[sym.id] = i;
        });
        function rankOf(card) {
            var id = card.getAttribute('data-sym-id');
            return id in rank ? rank[id] : syms.length;
        }
        ['active-section', 'standby-section'].forEach(function (testid) {
            var section = document.querySelector('[data-testid="' + testid + '"]');
            if (!section) return;
            var grid = section.querySelector('.cards-grid') || section;
            var cards = Array.prototype.filter.call(grid.children, function (c) { return c.classList.contains('sym-card'); });
            var wanted = cards.slice().sort(function (a, b) { return rankOf(a) - rankOf(b); });
            var differs = wanted.some(function (card, i) { return card !== cards[i]; });
            if (!differs) return;
            wanted.forEach(function (card) { grid.appendChild(card); });
        });
    }

    // ---------------------------------------------------------------------------
    // Account · all-time CR — fixed stat, never windowed
    // ---------------------------------------------------------------------------
    // Reads meta.portfolio.account_all_time_cr (authoritative, from /api/state _build_meta)
    // with portfolio_strip.account_all_time_cr as fallback.  The SSR renders the element
    // only when the account cache is warm; if the cache was cold at page-load the element
    // is absent.  This renderer creates it when missing so cold-start shows the stat without
    // requiring a manual page reload.  NOT wired to the windowed strip — this stat carries
    // no window label and must not change when the window picker is clicked.
    function updateAccountAllTime(data) {
        var portfolio = ((data.meta) || {}).portfolio || {};
        var ps = (data.portfolio_strip) || {};
        var value = portfolio.account_all_time_cr != null
            ? portfolio.account_all_time_cr
            : (ps.account_all_time_cr != null ? ps.account_all_time_cr : null);
        if (value === null) return;

        var el = document.querySelector('[data-testid="account-all-time-cr"]');
        if (!el) {
            // Cold-start: SSR omitted the block because the account cache was cold.
            // Create the container + span and insert it after the guard-alpha section
            // (matching templates/index.html:810-813 markup).
            var container = document.querySelector('.account-all-time-stat');
            if (!container) {
                var guardSection = document.getElementById('guard-alpha-headline');
                var parent = guardSection ? guardSection.parentNode : null;
                if (!parent) return;
                container = document.createElement('div');
                container.className = 'account-all-time-stat';
                container.style.cssText = 'margin-top:6px;font-size:11px;color:var(--studio-ink-dim);';
                container.innerHTML =
                    '<span class="account-all-time-label">Account &middot; all-time</span>'
                    + ' <span data-testid="account-all-time-cr" class="account-all-time-value"></span>';
                parent.insertAdjacentHTML('beforeend', container.outerHTML);
            }
            el = document.querySelector('[data-testid="account-all-time-cr"]');
            if (!el) return;
        }

        el.textContent = fmtPct(value);
        el.className = 'account-all-time-value ' + (value >= 0 ? 'pos' : 'neg');
    }

    // ---------------------------------------------------------------------------
    // Panels the page render fixes and the poll now carries (data.dashboard_panels):
    // the Managed Sleeves panel, the CVaR diagnostic panel and the Edit Vars badge.
    // Each part is applied only when it is present and sound; a part whose read failed
    // (null, or ok false), or a body with no dashboard_panels, leaves the panel as it is.
    // Existing elements are written by textContent; a card or row the page lacks is cloned
    // from the matching <template> and filled the same way. Nothing here writes innerHTML.
    // ---------------------------------------------------------------------------

    function setTextIfChanged(el, text) {
        if (el && el.textContent !== text) el.textContent = text;
    }

    function fieldOf(root, name) {
        return root.querySelector('[data-field="' + name + '"]');
    }

    // $12.34 as the server render prints a dollar figure (toFixed rounding); `missing` stands
    // in for a figure that is absent.
    function dollarText(value, missing) {
        return typeof value === 'number' && isFinite(value) ? '$' + value.toFixed(2) : missing;
    }

    // The element with data-<attr>="<id>" among a container's element children, or null.
    function childWithId(container, attr, id) {
        var kids = container.children;
        for (var i = 0; i < kids.length; i++) {
            if (kids[i].getAttribute(attr) === id) return kids[i];
        }
        return null;
    }

    // A copy of a <template>'s first element, owned by this document; null when the page has none.
    function cloneTemplateRoot(templateId) {
        var template = document.getElementById(templateId);
        var first = template && template.content ? template.content.firstElementChild : null;
        return first ? document.importNode(first, true) : null;
    }

    // Puts `wanted` (elements already children of `container`) in that order, moving only an
    // element that is out of place.
    function placeInOrder(container, wanted) {
        var cursor = container.firstElementChild;
        wanted.forEach(function (el) {
            if (cursor === el) {
                cursor = el.nextElementSibling;
            } else {
                container.insertBefore(el, cursor);
            }
        });
    }

    function wireButton(button, handlerName, argsFn) {
        button.addEventListener('click', function () {
            var handler = window[handlerName];
            if (typeof handler === 'function') handler.apply(window, argsFn().concat([button]));
        });
    }

    function syncArmControl(rowEl, sleeveId, rule) {
        var existing = rowEl.querySelector('[data-testid="sleeve-arm-control"]');
        var wanted = rule.mode === 'SHADOW';
        if (wanted && !existing) {
            var template = cloneTemplateRoot('sleeve-rule-row-template');
            var fresh = template ? template.querySelector('[data-testid="sleeve-arm-control"]') : null;
            var actions = fieldOf(rowEl, 'rule-actions');
            if (!fresh || !actions) return;
            fresh.setAttribute('data-sleeve-id', String(sleeveId));
            fresh.setAttribute('data-rule-id', String(rule.id));
            wireButton(fresh, 'armSleeveRuleToPaper', function () { return [sleeveId, rule.id]; });
            actions.appendChild(fresh);
        } else if (!wanted && existing) {
            existing.parentNode.removeChild(existing);
        }
    }

    var RULE_LABEL_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // "Oct 30" from the ISO digits of the session day (no Date, so the browser zone plays no
    // part) while the market is closed; "today" while open or when the day is unknown.
    function ruleTodayLabel(meta) {
        var anchor = meta && meta.portfolio && meta.portfolio.window_anchor;
        var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(anchor));
        if (!meta || meta.system_online !== false || !m || Number(m[2]) < 1 || Number(m[2]) > 12) {
            return 'today';
        }
        return RULE_LABEL_MONTHS[Number(m[2]) - 1] + ' ' + Number(m[3]);
    }
    var _ruleTodayLabel = 'today';

    // While the market is not open the figures are the last session's: the labels that said
    // today or live name the session day ("Oct 30") instead, as the server render does, so a
    // label never changes between the first paint and the first poll. null while open.
    var _sessionDay = null;
    function eventsEmptyText() {
        return _sessionDay ? 'No events on ' + _sessionDay + '.' : 'No events yet today.';
    }
    function applySessionWording(meta) {
        var label = ruleTodayLabel(meta);
        _sessionDay = label === 'today' ? null : label;
        document.querySelectorAll('[data-field="day-word"]').forEach(function (el) {
            setTextIfChanged(el, _sessionDay || 'Today');
        });
        setTextIfChanged(document.querySelector('[data-field="live-return-label"]'),
            _sessionDay ? 'Return' : 'Live Return');
        setTextIfChanged(document.querySelector('[data-field="hwm-label"]'),
            _sessionDay ? 'HWM ' + _sessionDay : 'HWM today');
        setTextIfChanged(document.querySelector('[data-field="events-heading"]'),
            _sessionDay ? _sessionDay + ' events' : 'Today’s events');
        setTextIfChanged(document.querySelector('[data-field="events-empty"]'), eventsEmptyText());
        if (_sessionDay) {
            document.querySelectorAll('.dv-label').forEach(function (el) {
                if (/^If held · live$/.test(el.textContent.replace(/\s+/g, ' ').trim())) {
                    setTextIfChanged(el, 'If held');
                }
            });
        }
    }

    function syncRuleRow(rowEl, sleeveId, rule) {
        setTextIfChanged(fieldOf(rowEl, 'rule-today-label'), _ruleTodayLabel);
        setTextIfChanged(fieldOf(rowEl, 'rule-name'), String(rule.name));
        setTextIfChanged(fieldOf(rowEl, 'rule-mode'), '(' + rule.mode + ')');
        setTextIfChanged(fieldOf(rowEl, 'rule-today'), String(rule.today_fires));
        setTextIfChanged(fieldOf(rowEl, 'rule-lifetime'), String(rule.lifetime_fires));
        setTextIfChanged(fieldOf(rowEl, 'rule-realized'), dollarText(rule.realized_pnl_usd, 'n/a'));
        syncArmControl(rowEl, sleeveId, rule);
    }

    function newRuleRow(sleeveId, rule) {
        var rowEl = cloneTemplateRoot('sleeve-rule-row-template');
        if (!rowEl) return null;
        rowEl.setAttribute('data-rule-id', String(rule.id));
        var arm = rowEl.querySelector('[data-testid="sleeve-arm-control"]');
        if (arm) arm.parentNode.removeChild(arm);  // syncArmControl adds it for a SHADOW rule
        return rowEl;
    }

    function syncSleeveCard(card, sleeve) {
        setTextIfChanged(fieldOf(card, 'sleeve-name'), String(sleeve.name));
        var badge = card.querySelector('[data-testid="sleeve-status-badge"]');
        if (badge) {
            setTextIfChanged(badge, String(sleeve.status));
            var badgeClass = 'status-pill ' + sleeve.status_badge_class;
            if (badge.className !== badgeClass) badge.className = badgeClass;
        }
        setTextIfChanged(fieldOf(card, 'sleeve-capital'), dollarText(sleeve.capital_usd, '\u2014'));
        setTextIfChanged(fieldOf(card, 'sleeve-ledger-cash'), dollarText(sleeve.ledger_cash_usd, '\u2014'));
        setTextIfChanged(fieldOf(card, 'sleeve-realized'), dollarText(sleeve.realized_pnl_usd, 'n/a'));

        var rules = Array.isArray(sleeve.rules) ? sleeve.rules : [];
        var rulesBox = fieldOf(card, 'sleeve-rules');
        if (rulesBox) {
            var wantedIds = {};
            var rows = [];
            rules.forEach(function (rule) {
                if (!rule || rule.id === null || rule.id === undefined) return;
                var id = String(rule.id);
                wantedIds[id] = true;
                var rowEl = childWithId(rulesBox, 'data-rule-id', id);
                if (!rowEl) {
                    rowEl = newRuleRow(sleeve.id, rule);
                    if (!rowEl) return;
                    rulesBox.appendChild(rowEl);
                }
                syncRuleRow(rowEl, sleeve.id, rule);
                rows.push(rowEl);
            });
            Array.prototype.slice.call(rulesBox.children).forEach(function (rowEl) {
                if (!wantedIds[rowEl.getAttribute('data-rule-id')]) rulesBox.removeChild(rowEl);
            });
            placeInOrder(rulesBox, rows);
            rulesBox.hidden = rows.length === 0;
        }

        // A SHADOW sleeve has nothing to disarm: the button is inert, as the server renders it.
        var inert = sleeve.status === 'SHADOW';
        var disarm = card.querySelector('[data-testid="sleeve-disarm-control"]');
        if (disarm) {
            if (disarm.disabled !== inert) disarm.disabled = inert;
            if (disarm.classList.contains('control-disabled') !== inert) {
                disarm.classList.toggle('control-disabled', inert);
            }
        }
    }

    function newSleeveCard(sleeve) {
        var card = cloneTemplateRoot('sleeve-card-template');
        if (!card) return null;
        var id = String(sleeve.id);
        card.setAttribute('data-sleeve-id', id);
        var disarm = card.querySelector('[data-testid="sleeve-disarm-control"]');
        var remove = card.querySelector('[data-testid="sleeve-delete-control"]');
        if (disarm) {
            disarm.setAttribute('data-sleeve-id', id);
            wireButton(disarm, 'disarmSleeve', function () { return [sleeve.id]; });
        }
        if (remove) {
            remove.setAttribute('data-sleeve-id', id);
            wireButton(remove, 'deleteSleeve', function () { return [sleeve.id]; });
        }
        return card;
    }

    function updateSleevesPanel(sleeves) {
        var list = document.querySelector('[data-testid="sleeves-panel"] [data-field="sleeves-list"]');
        if (!list) return;
        var wantedIds = {};
        var cards = [];
        sleeves.forEach(function (sleeve) {
            if (!sleeve || sleeve.id === null || sleeve.id === undefined) return;
            var id = String(sleeve.id);
            wantedIds[id] = true;
            var card = childWithId(list, 'data-sleeve-id', id);
            if (!card) {
                card = newSleeveCard(sleeve);
                if (!card) return;
                list.appendChild(card);
            }
            syncSleeveCard(card, sleeve);
            cards.push(card);
        });
        Array.prototype.slice.call(list.children).forEach(function (card) {
            if (!wantedIds[card.getAttribute('data-sleeve-id')]) list.removeChild(card);
        });
        placeInOrder(list, cards);
        var empty = document.querySelector('[data-testid="sleeves-empty"]');
        if (empty) empty.hidden = cards.length > 0;
    }

    function finiteOrNull(v) {
        return typeof v === 'number' && isFinite(v) ? v : null;
    }

    // The CVaR panel shows the latest diagnostic and is hidden while there is none. The words and
    // figures follow the server render's: four places, a dash for a missing figure.
    function renderCvarPanel(diagnostic) {
        var panel = document.getElementById('cvar-diagnostic-panel');
        if (!panel) return;
        var has = !!diagnostic && typeof diagnostic === 'object';
        var five = has ? finiteOrNull(diagnostic.cvar_5pct) : null;
        var stderr = has ? finiteOrNull(diagnostic.cvar_5pct_stderr) : null;
        var nTail = has ? finiteOrNull(diagnostic.cvar_n_tail) : null;
        setTextIfChanged(fieldOf(panel, 'cvar-5pct'), five !== null ? five.toFixed(4) : '\u2014');
        var cell = fieldOf(panel, 'cvar-stderr-cell');
        if (cell) cell.hidden = five === null;
        var stderrEl = fieldOf(panel, 'cvar-stderr');
        if (stderrEl) {
            setTextIfChanged(stderrEl, '\u00b1' + (stderr !== null ? stderr.toFixed(4) : '\u2014') + ' stderr');
            stderrEl.setAttribute('data-cvar-stderr', stderr !== null ? String(stderr) : '');
        }
        var nEl = fieldOf(panel, 'cvar-n-tail');
        if (nEl) {
            setTextIfChanged(nEl, 'n=' + (nTail !== null ? String(nTail) : '\u2014'));
            nEl.setAttribute('data-cvar-n-tail', nTail !== null ? String(nTail) : '');
        }
        panel.hidden = !has;
    }

    // The Edit Vars badge shows how many variables are locked, and is hidden and empty at zero.
    function renderEditVarsBadge(count) {
        var badge = document.querySelector('[data-testid="edit-vars-indicator"]');
        if (!badge) return;
        var shown = count > 0;
        setTextIfChanged(badge, shown ? count + ' locked' : '');
        badge.hidden = !shown;
        badge.style.display = shown ? 'inline-flex' : 'none';
    }

    // The headline's descriptor says "Simulated" while the engine's last cycle ran simulated. The
    // live poll's bot_state carries the engine's flag (last_execution_mode); a body that does not
    // (the after-close snapshot's state) leaves the server-rendered text as it is.
    function renderExecutionDescriptor(data) {
        var state = data && (data.bot_state || data.state);
        if (!state || typeof state.last_execution_mode !== 'boolean') return;
        setTextIfChanged(document.getElementById('guard-alpha-simulated'),
            state.last_execution_mode === false ? 'Simulated ' : '');
    }

    function renderDashboardPanels(data) {
        var panels = data && data.dashboard_panels;
        if (!panels || typeof panels !== 'object') return;
        _ruleTodayLabel = ruleTodayLabel(data.meta);
        if (Array.isArray(panels.sleeves)) updateSleevesPanel(panels.sleeves);
        if (panels.cvar && typeof panels.cvar === 'object' && panels.cvar.ok === true) {
            renderCvarPanel(panels.cvar.diagnostic);
        }
        if (typeof panels.vars_locked_count === 'number' && isFinite(panels.vars_locked_count)) {
            renderEditVarsBadge(panels.vars_locked_count);
        }
    }

    // ---------------------------------------------------------------------------
    // Main updateDashboard — called on each fetch('/api/state') response
    // ---------------------------------------------------------------------------

    // The data stamp beside the hero chart (#hero-data-as-of), the stamp of the last applied
    // body, and what the last refresh did. renderAsOf is the only writer of that element.
    var _asOfStamp = null;          // the stamp shown; null until the render or a body names one
    var _lastRefreshStamp = null;   // the stamp of the last applied body; seeded from the render
    var _refreshFailed = false;     // the newest /api/state request ended in a handled failure
    var _lastDataMono = null;       // performance.now() when the last good body arrived

    // Says what the page knows about its data: that the last refresh failed, the stamp of the
    // data on screen, and (once no data has arrived for a while) for how long. With nothing to
    // say, the server render stands.
    function renderAsOf() {
        var el = document.getElementById('hero-data-as-of');
        if (!el) return;
        var parts = [];
        if (_refreshFailed) parts.push('connection lost');
        // The dashes the server prints for a render with no stamp are not a stamp to keep
        // beside the cue.
        var stampKnown = _asOfStamp !== null && _asOfStamp !== AS_OF_UNKNOWN;
        if (stampKnown || (_asOfStamp !== null && !_refreshFailed)) parts.push(AS_OF_PREFIX + _asOfStamp);
        var minutes = typeof window.notUpdatedForMinutes === 'function' ? window.notUpdatedForMinutes() : null;
        if (minutes !== null) parts.push('not updated for ' + minutes + ' min');
        if (!parts.length) return;
        var text = parts.join(' · ');
        if (el.textContent !== text) el.textContent = text;
    }

    // timing is the receipt record of the response that carried the body (null when the body
    // did not come from a request).
    function updateDashboard(data, timing) {
        var meta = data.meta || {};
        var botState = data.bot_state || data.state || {};

        _botState = botState;
        _symIds = Object.keys(botState);
        var chartsSettled = Promise.resolve();

        // One reading per applied body: does it carry newer data than the last one?
        var stamp = dataStamp(data);
        var stampChanged = stamp === null || stamp !== _lastRefreshStamp;
        _lastRefreshStamp = stamp;
        if (stamp !== null) _asOfStamp = stamp;

        // C-10: per-renderer try/catch so one failure doesn't kill subsequent renderers
        [
            function () { renderHeroChart(meta); },
            // The headline, its window label and the Ann. Vol row follow the picked window.
            function () { refreshWindowedStrip(stampChanged); },
            function () { updateComparisonRows(data); },
            function () { updateVerdicts(botState, data.market_state); },
            function () { if (typeof window.updateBanners === 'function') window.updateBanners(data); },
            function () { updateMiniStats(meta); },
            function () { chartsSettled = loadCharts(botState, stampChanged); },
            function () { if (typeof window.updateChromeTicker === 'function') window.updateChromeTicker(data, timing); },
            // cards-live: refresh per-symphony card value spans from data.symphonies.
            // Reuses POLL_INTERVAL_MS — no new timer. Cards stay live between hard-refreshes.
            function () { updateCards(data); },
            function () { orderSectionCards(data); },
            // Account · all-time CR: fixed stat, not windowed — must fire on every poll
            // so cold-start (SSR omitted the element) resolves without a manual reload.
            function () { updateAccountAllTime(data); },
            function () { applyAccountFilter(data); },
            function () { updateSectionMeta(); },
            // The header's mode pill and the hero legend's mode word follow the body.
            function () { if (typeof window.updateModeIndicators === 'function') window.updateModeIndicators(meta); },
            // The open detail panel follows the body.
            function () { refreshOpenDetailPanel(botState); },
            // The Sleeves and CVaR panels and the Edit Vars badge.
            function () { applySessionWording(meta); },
            function () { renderDashboardPanels(data); },
            // The headline descriptor's "Simulated" word.
            function () { renderExecutionDescriptor(data); },
            // Without a stream the poll is the only thing that moves the $-saved panel.
            function () { refreshPanelWithoutStream(); },
            function () { renderAsOf(); }
        ].forEach(function (fn) {
            try { fn(); } catch (e) { console.error('updateDashboard renderer error:', e); }
        });
        return chartsSettled;
    }

    function updateMiniStats(meta) {
        var elTracked = document.getElementById('hero-tracked');
        if (elTracked) elTracked.textContent = meta.tracked != null ? meta.tracked : 0;
        var elArmed = document.getElementById('hero-armed');
        if (elArmed) elArmed.textContent = meta.armed != null ? meta.armed : 0;
        var elTriggered = document.getElementById('hero-triggered');
        if (elTriggered) elTriggered.textContent = meta.triggered != null ? meta.triggered : 0;
    }

    // ---------------------------------------------------------------------------
    // Poll loop, failure handling and the event stream
    // ---------------------------------------------------------------------------

    // The page's one way out when the session is gone: every request this page makes goes
    // through the auth gate, which answers 401 for an expired session.
    function goToLogin() {
        try {
            window.location.assign('/login');
        } catch (e) {
            console.error('redirect to login failed', e);
        }
    }

    // A /api/state data body: an object carrying an object `meta` and a string `market_state`
    // (the live, frozen and waiting bodies all do; the error bodies carry neither).
    function isStateBody(d) {
        return !!d && typeof d === 'object'
            && !!d.meta && typeof d.meta === 'object'
            && typeof d.market_state === 'string';
    }

    // The newest request ended in a handled failure: every value on the page stays, the header
    // label and the as-of text say the data is not current, and the next poll is the retry.
    function showConnectionLost(err) {
        _refreshFailed = true;
        if (typeof window.noteRefreshFailed === 'function') window.noteRefreshFailed();
        renderAsOf();
        console.error('state load failed', err);
    }

    // A good body arrived (not yet rendered).
    function noteStateReceived(timing) {
        _refreshFailed = false;
        _lastDataMono = timing && typeof timing.receivedAtMono === 'number' ? timing.receivedAtMono : monoNow();
        if (typeof window.noteDataReceived === 'function') window.noteDataReceived(timing);
    }

    // Newest-request-wins: every call takes a sequence number and only the
    // response (or failure) of the most recently issued call is applied, so a
    // slow earlier response cannot overwrite a newer snapshot or account pick. The check
    // runs before the answer is classified, so an older error is ignored too.
    // The newest call is counted once it has settled: its renderers ran (or its failure
    // was handled), every card's chart request has answered and every chart it built
    // has drawn. A superseded call is never counted.
    // A request with no answer within STATE_REQUEST_TIMEOUT_MS is abandoned (a failure).
    var _stateRequestSeq = 0;
    function loadState() {
        var seq = ++_stateRequestSeq;
        var query = window.activeAccount
            ? '?account=' + encodeURIComponent(window.activeAccount)
            : '';
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timeout = controller
            ? setTimeout(function () { controller.abort(); }, STATE_REQUEST_TIMEOUT_MS)
            : null;
        var sentAtMono = monoNow();
        return fetch('/api/state' + query, controller ? { signal: controller.signal } : undefined)
            .then(function (r) {
                if (seq !== _stateRequestSeq) return { superseded: true };
                if (r.status === 401) return { login: true };
                if (!r.ok) throw new Error('state request answered HTTP ' + r.status);
                var timing = typeof window.receiptTiming === 'function'
                    ? window.receiptTiming(sentAtMono, r)
                    : { sentAtMono: sentAtMono, receivedAtMono: monoNow(), serverNowMs: null };
                return r.json().then(function (data) {
                    if (seq !== _stateRequestSeq) return { superseded: true };
                    if (!isStateBody(data)) throw new Error('state response is not a data body');
                    return { data: data, timing: timing };
                });
            })
            .then(function (outcome) {
                if (outcome.superseded) return;
                if (outcome.login) { goToLogin(); return; }
                noteStateReceived(outcome.timing);
                // A changed start stamp means the daemon restarted: read a new CSRF token now, so the
                // operator's next click on a control carries a token the new process accepts.
                if (window.PSRequest) window.PSRequest.noteDaemonStart(outcome.data.daemon_started_at);
                return updateDashboard(outcome.data, outcome.timing);
            })
            .catch(function (err) {
                if (seq !== _stateRequestSeq) return;
                showConnectionLost(err);
            })
            .then(function () {
                if (timeout !== null) clearTimeout(timeout);
                if (seq !== _stateRequestSeq) return;
                _refreshCount += 1;
                if (document.body) document.body.dataset.refreshCount = String(_refreshCount);
            });
    }

    // Event stream. The cycle-complete event is the primary refresh path; the poll is the
    // fallback. A closed stream (a reconnect answered 502, a refused connection) is opened
    // again; a stream the browser is reconnecting by itself (CONNECTING) is left alone.
    var _eventStream = null;
    var _eventRetryTimer = null;
    var _eventRetryDelayMs = EVENT_STREAM_RETRY_FIRST_MS;

    function streamIsOpen() {
        return !!_eventStream && _eventStream.readyState === STREAM_OPEN;
    }

    function cancelEventRetry() {
        if (_eventRetryTimer !== null) {
            clearTimeout(_eventRetryTimer);
            _eventRetryTimer = null;
        }
    }

    // Closes the current stream (if any) before opening the next, so at most one is ever open
    // or connecting.
    function openEventStream() {
        cancelEventRetry();
        if (_eventStream) {
            try { _eventStream.close(); } catch (e) { /* already closed */ }
        }
        var stream = new EventSource('/api/events');
        _eventStream = stream;
        // Finding 8: the $-saved panel rides the same event — a page left open
        // across the EOD post-mortem write (or a new guard event) updates live.
        stream.addEventListener('cycle-complete', handleCycleCompleteEvent);
        stream.onopen = function () {
            if (stream === _eventStream) _eventRetryDelayMs = EVENT_STREAM_RETRY_FIRST_MS;
        };
        stream.onerror = function () { onEventStreamError(stream); };
    }

    function ensureEventStream() {
        if (typeof EventSource === 'undefined') return;
        if (_eventStream && _eventStream.readyState !== STREAM_CLOSED) return;
        openEventStream();
    }

    // One reopen is pending at a time; its delay doubles while attempts fail.
    function scheduleEventReopen() {
        cancelEventRetry();
        var delay = _eventRetryDelayMs;
        _eventRetryDelayMs = Math.min(_eventRetryDelayMs * 2, EVENT_STREAM_RETRY_MAX_MS);
        _eventRetryTimer = setTimeout(function () {
            _eventRetryTimer = null;
            ensureEventStream();
        }, delay);
    }

    // An EventSource cannot read the status of a refused reconnect. HEAD on the endpoint
    // answers the auth gate's status without registering a stream client: a 401 means the
    // session is gone and the page goes to the login page instead of retrying.
    function probeEventStream() {
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timeout = controller
            ? setTimeout(function () { controller.abort(); }, EVENT_STREAM_PROBE_TIMEOUT_MS)
            : null;
        var options = controller ? { method: 'HEAD', signal: controller.signal } : { method: 'HEAD' };
        fetch('/api/events', options)
            .then(function (r) {
                if (r.status === 401) { cancelEventRetry(); goToLogin(); }
            })
            .catch(function () { /* unreachable: the pending reopen carries on */ })
            .then(function () { if (timeout !== null) clearTimeout(timeout); });
    }

    function onEventStreamError(stream) {
        if (stream !== _eventStream) return;
        if (stream.readyState !== STREAM_CLOSED) return;
        scheduleEventReopen();
        probeEventStream();
    }

    // One refresh at once when the page is shown again, restored from the back-forward cache
    // or the network is back, whether or not the stream is up. A stream that still reads OPEN
    // after a long silence is replaced: a laptop that slept keeps a dead socket reading OPEN.
    var _lastCatchUpMono = null;
    function catchUp() {
        var now = monoNow();
        if (_lastCatchUpMono !== null && now - _lastCatchUpMono < CATCH_UP_COALESCE_MS) return;
        _lastCatchUpMono = now;
        var silent = _lastDataMono === null || now - _lastDataMono > STREAM_TRUST_AFTER_SILENCE_MS;
        if (streamIsOpen() && silent) {
            cancelEventRetry();
            _eventStream.close();
        }
        ensureEventStream();
        loadState();
    }

    // AC-1: fetch /api/guard-alpha-summary and populate the dollar-saved panel.
    // Called on page load, on each SSE cycle-complete event whose data changed (Finding 8),
    // and on each poll while no stream is open (refreshPanelWithoutStream): the panel updates
    // when the engine actually cycles, or at the poll's pace when the stream is down.
    // Uses dollar-saved-headline — NOT guard-alpha-headline (that carries the windowed
    // % guard alpha from /api/strip/<window> and must not be clobbered).
    // Newest request wins, and an answer applies only while its window is still the picked
    // one; a refused, failed or malformed answer leaves the panel as it was.
    var _summaryRequestSeq = 0;
    var _summaryPendingSeq = 0;      // the request in flight, 0 when the newest has settled
    var _summaryPendingToken = null; // the window that request is for
    // Another window's figures never stay under a pick: dash() (below, inside the function)
    // dashes them until the picked window's own answer lands; a failed answer says so in the panel.
    var _summaryAnsweredToken = null; // the window whose summary last answered well; null before the first

    function fetchGuardAlphaSummary(windowToken) {
        var seq = ++_summaryRequestSeq;
        _summaryPendingSeq = seq;
        _summaryPendingToken = windowToken;
        if (windowToken !== _summaryAnsweredToken) dash();
        var url = '/api/guard-alpha-summary' + (windowToken ? '?window=' + encodeURIComponent(windowToken) : '');
        fetch(url)
            .then(function (response) {
                if (!response.ok) return;
                return response.json();
            })
            .then(function (data) {
                if (seq === _summaryPendingSeq) _summaryPendingSeq = 0;
                if (seq !== _summaryRequestSeq) return;
                if (windowToken && windowToken !== _heroWindow) return;
                if (!data || typeof data.guard_event_count !== 'number') return fail();
                _summaryAnsweredToken = windowToken; dash(1);
                var headlineEl = document.getElementById('dollar-saved-headline');
                var verbEl = document.getElementById('dollar-saved-verb');
                var countEl = document.getElementById('guard-event-count');
                var labelEl = document.getElementById('dollar-saved-basis-label');
                // BL-3: net elements gated on the SAME guard_event_count branch
                // as the gross ones -- an empty state must never show $0.00.
                var netHeadlineEl = document.getElementById('dollar-saved-net-of-friction-headline');
                var netVerbEl = document.getElementById('dollar-saved-net-of-friction-verb');
                if (data.guard_event_count === 0) {
                    if (headlineEl) headlineEl.textContent = 'No guard events yet';
                    if (countEl) countEl.textContent = '0';
                    if (labelEl) labelEl.textContent = data.basis_label || '';
                    if (netHeadlineEl) netHeadlineEl.textContent = 'No guard events yet';
                    if (netVerbEl) netVerbEl.textContent = '';
                } else {
                    // ABS magnitude, no sign char -- verb carries saved/lost (DE-GAS-COHERENCE-001).
                    var saved = data.cumulative_saved_dollars;
                    if (headlineEl) {
                        headlineEl.textContent = '$' + Math.abs(saved).toFixed(2);
                        headlineEl.style.color = saved >= 0 ? cs('--studio-pos') : cs('--studio-neg');
                    }
                    if (verbEl) verbEl.textContent = saved >= 0 ? 'saved' : 'lost';
                    if (countEl) countEl.textContent = data.guard_event_count;
                    if (labelEl) labelEl.textContent = data.basis_label || '';

                    // BL-3 net-of-friction snapshot line, own elements/sign.
                    var netSaved = data.cumulative_saved_dollars_net_of_friction;
                    if (netHeadlineEl) {
                        netHeadlineEl.textContent = '$' + Math.abs(netSaved).toFixed(2);
                        netHeadlineEl.style.color = netSaved >= 0 ? cs('--studio-pos') : cs('--studio-neg');
                    }
                    if (netVerbEl) netVerbEl.textContent = netSaved >= 0 ? 'saved' : 'lost';
                }

                // Realized-basis (EOD-marks) headline -- additive sibling.
                var realizedHeadlineEl = document.getElementById('dollar-saved-realized-headline');
                var realizedVerbEl = document.getElementById('dollar-saved-realized-verb');
                var realizedCoverageEl = document.getElementById('dollar-saved-realized-coverage');
                // BL-3: realized-net elements, gated on the SAME with_data branch.
                var realizedNetHeadlineEl = document.getElementById('dollar-saved-realized-net-of-friction-headline');
                var realizedNetVerbEl = document.getElementById('dollar-saved-realized-net-of-friction-verb');
                var coverage = data.realized_coverage || { with_data: 0, total: 0 };
                if (coverage.with_data === 0) {
                    // Never render a bare zero for no realized coverage (DE-GAS-COHERENCE-001).
                    if (realizedHeadlineEl) realizedHeadlineEl.textContent = 'no realized data yet';
                    if (realizedCoverageEl) realizedCoverageEl.textContent = '0 of ' + coverage.total;
                    if (realizedNetHeadlineEl) realizedNetHeadlineEl.textContent = 'no realized data yet';
                    if (realizedNetVerbEl) realizedNetVerbEl.textContent = '';
                } else {
                    var realizedSaved = data.saved_dollars_realized;
                    if (realizedHeadlineEl) {
                        realizedHeadlineEl.textContent = '$' + Math.abs(realizedSaved).toFixed(2);
                        realizedHeadlineEl.style.color = realizedSaved >= 0 ? cs('--studio-pos') : cs('--studio-neg');
                    }
                    if (realizedVerbEl) realizedVerbEl.textContent = realizedSaved >= 0 ? 'saved' : 'lost';
                    if (realizedCoverageEl) realizedCoverageEl.textContent = coverage.with_data + ' of ' + coverage.total;

                    // BL-3 net-of-friction realized line, own elements/sign.
                    var realizedNetSaved = data.saved_dollars_realized_net_of_friction;
                    if (realizedNetHeadlineEl) {
                        realizedNetHeadlineEl.textContent = '$' + Math.abs(realizedNetSaved).toFixed(2);
                        realizedNetHeadlineEl.style.color = realizedNetSaved >= 0 ? cs('--studio-pos') : cs('--studio-neg');
                    }
                    if (realizedNetVerbEl) realizedNetVerbEl.textContent = realizedNetSaved >= 0 ? 'saved' : 'lost';
                }
            })
            .catch(function (err) {
                if (seq === _summaryPendingSeq) _summaryPendingSeq = 0;
                fail();
                console.error('guard-alpha-summary load failed', err);
            });

        // A failed or malformed answer for the picked window: its figures stay dashes and the
        // panel says so; the next poll asks again (the window is still unanswered).
        function fail() {
            if (seq !== _summaryRequestSeq) return;
            if (windowToken && windowToken !== _heroWindow) return;
            if (windowToken === _summaryAnsweredToken) return; // own figures, one refresh old
            dash(0, 'Could not load the ' + (windowToken || 'all') + ' figures; trying again.');
        }

        // Dashes (no digit) for every figure and the notice text; noticeOnly writes the notice
        // alone (an answer that landed clears it).
        function dash(noticeOnly, notice) {
            if (!noticeOnly) {
                _summaryAnsweredToken = null;
                ['dollar-saved-headline', 'guard-event-count', 'dollar-saved-net-of-friction-headline',
                 'dollar-saved-realized-headline', 'dollar-saved-realized-coverage',
                 'dollar-saved-realized-net-of-friction-headline'].forEach(function (id) {
                    var el = document.getElementById(id);
                    if (el) { el.textContent = '—'; el.style.color = cs('--studio-ink-dim'); }
                });
                ['dollar-saved-verb', 'dollar-saved-basis-label', 'dollar-saved-net-of-friction-verb',
                 'dollar-saved-realized-verb', 'dollar-saved-realized-net-of-friction-verb'].forEach(function (id) {
                    var el = document.getElementById(id);
                    if (el) el.textContent = '';
                });
            }
            var note = document.getElementById('dollar-saved-window-notice');
            if (note) {
                note.textContent = notice || '';
                note.hidden = !notice;
            }
        }
    }

    window.loadState = loadState;

    // While no stream is open the poll also refreshes the $-saved panel for the picked window
    // (with a stream open, events do). A request for that window already in flight is not
    // sent twice: the page's first poll meets the one the load sent.
    function refreshPanelWithoutStream() {
        // With a stream open, events move the panel; a window still unanswered is asked again.
        if (streamIsOpen() && _summaryAnsweredToken === _heroWindow) return;
        if (_summaryPendingSeq !== 0 && _summaryPendingToken === _heroWindow) return;
        fetchGuardAlphaSummary(_heroWindow);
    }

    // Refetch on every cycle-complete event except one that states changed === false.
    // Anything unparseable or missing the key refetches (never skip on doubt).
    function handleCycleCompleteEvent(evt) {
        var unchanged = false;
        try {
            var parsed = JSON.parse(evt.data);
            unchanged = parsed !== null && typeof parsed === 'object' && parsed.changed === false;
        } catch (e) { unchanged = false; }
        if (unchanged) { return; }
        loadState();
        fetchGuardAlphaSummary(_heroWindow);
    }

    document.addEventListener('DOMContentLoaded', function () {
        try { paintFirstFrame(); } catch (e) { console.error('first paint error:', e); }

        // Poll every 30 s (POLL_INTERVAL_MS); the engine writes once a minute. The poll keeps
        // running in every market state and after hours: the server answers the same body
        // then, so it changes nothing, and a page left open overnight picks up the open itself.
        loadState();
        setInterval(loadState, POLL_INTERVAL_MS);

        // AC-3: SSE event-driven update — primary path; poll (above) is the resilience fallback.
        // A closed stream is opened again (ensureEventStream), however long the daemon is away.
        ensureEventStream();

        fetchGuardAlphaSummary(_heroWindow);

        // The as-of text carries an age cue once no data has arrived for a while: redrawn on a
        // timer that sends no request.
        setInterval(renderAsOf, FRESHNESS_CHECK_MS);

        // Catch-up: one refresh at once when the tab is shown again, the page is restored from
        // the back-forward cache or the network returns (coalesced inside CATCH_UP_COALESCE_MS).
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') catchUp();
        });
        window.addEventListener('pageshow', function (evt) {
            if (evt && evt.persisted) catchUp();
        });
        window.addEventListener('online', catchUp);

        // AC-3: each picker button maps to a lowercase URL window token. The SAME
        // token drives BOTH /api/strip/<token> (re-windows the headline VALUE, its label
        // and the Ann. Vol row) and /api/hero-chart/<token> (re-windows the two chart lines).
        // "window-all" is the NEW All-Time option (token "all" = lifetime cross-epoch).
        var windowTokenMap = {
            'window-30d':  '30d',
            'window-60d':  '60d',
            'window-90d':  '90d',
            'window-125d': '125d',
            'window-ytd':  'ytd',
            'window-1y':   '1y',
            'window-all':  'all'
        };

        Object.keys(windowTokenMap).forEach(function (testid) {
            var btn = document.querySelector('[data-testid="' + testid + '"]');
            if (!btn) return;
            btn.addEventListener('click', function () {
                document.querySelectorAll('[data-testid="window-selector"] button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                var token = windowTokenMap[testid];
                _heroWindow = token;
                // The label names the picked window at once; the strip's answer writes it
                // again together with the value it describes (F1).
                var winLabel = document.getElementById('guard-alpha-window-label');
                if (winLabel) winLabel.textContent = WINDOW_LABELS[token] || btn.textContent;
                // The figures beside that label are the previous window's until the strip answers,
                // so they read as dashes until then (and stay dashed if the strip cannot be loaded).
                _stripAnsweredToken = null;
                _stripFiguresBlank = true;
                blankWindowedFigures();
                clearStripNotice();
                // The picker drives the chart lines (latest click wins) and the
                // headline value, its window label and the Ann. Vol row (windowed
                // strip) -- NOT the Today/Cumulative/Max DD rows, which stay on genuine
                // /api/state polls only (DE-CLOSED-BOUNCE-001).
                requestHeroWindow(token);
                requestWindowedStrip(token);
                // AC-2: the $-saved panel re-windows in lockstep with the rest of
                // the hero instead of always showing the all-time sum.
                fetchGuardAlphaSummary(token);
            });
        });
    });

    // A theme or accent change recolours the drawn charts in place (no rebuild, no animation).
    function recolourCharts() {
        if (_cumChart) {
            _cumChart.data.datasets[0].borderColor = lineToken('hero', 0);
            _cumChart.data.datasets[1].borderColor = lineToken('hero', 1);
            _cumChart.update('none');
        }
        document.querySelectorAll('[data-testid="card-spark"]').forEach(function (canvas) {
            var chart = chartOn(canvas);
            if (!chart) return;
            chart.data.datasets[0].borderColor = lineToken('spark', 0);
            if (chart.data.datasets[1]) chart.data.datasets[1].borderColor = lineToken('spark', 1);
            chart.update('none');
        });
        if (_intradayChart) {
            _intradayChart.data.datasets.forEach(function (ds, i) {
                if (CHART_LINE_TOKENS.detail[i]) ds.borderColor = lineToken('detail', i);
            });
            _intradayChart.update('none');
        }
    }
    window.addEventListener('tweakchange', recolourCharts);

    // ---------------------------------------------------------------------------
    // Managed Sleeves panel (AC-16) — arm-to-paper / disarm controls.
    // Sends through PSRequest.postAction, which reads the token again after a refusal.
    // Arm-live and envelope-widen have their own multi-gate ceremonies (AC-3/
    // AC-14) and are deliberately NOT wired to a one-click control here — they
    // need a dedicated confirm-phrase modal, a follow-up UI pass, not a bare
    // button that could fat-finger a live-trading arm.
    // ---------------------------------------------------------------------------

    function armSleeveRuleToPaper(sleeveId, ruleId, btn) {
        if (!window.confirm('Arm this rule to PAPER? It will start placing real paper-account orders.')) return;
        btn.disabled = true;
        PSRequest.postAction('/api/sleeves/' + sleeveId + '/rules/' + ruleId + '/arm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' },
            body: JSON.stringify({ target_mode: 'PAPER' })
        }).then(function (res) {
            if (res.networkError) {
                console.error('armSleeveRuleToPaper failed: request not completed');
                btn.disabled = false;
                return;
            }
            if (res.refused === 'csrf') {
                window.alert(PSRequest.notSentText(res, 'Click Arm again.'));
                btn.disabled = false;
                return;
            }
            if (!res.ok) {
                window.alert('Arm rejected: ' + ((res.body && res.body.message) || 'unknown error'));
                btn.disabled = false;
                return;
            }
            location.reload();
        });
    }
    window.armSleeveRuleToPaper = armSleeveRuleToPaper;

    function disarmSleeve(sleeveId, btn) {
        if (!window.confirm('Disarm this sleeve? All autonomy stops immediately; re-arming requires the ceremony again.')) return;
        btn.disabled = true;
        PSRequest.postAction('/api/sleeves/' + sleeveId + '/disarm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' }
        }).then(function (res) {
            if (res.refused === 'csrf') {
                window.alert(PSRequest.notSentText(res, 'Click Disarm again.'));
                btn.disabled = false;
                return;
            }
            if (!res.ok) {
                console.error('disarmSleeve failed: HTTP ' + res.status);
                btn.disabled = false;
                return;
            }
            location.reload();
        });
    }
    window.disarmSleeve = disarmSleeve;

    function deleteSleeve(sleeveId, btn) {
        if (!window.confirm('Delete this sleeve? Refused unless it is flat (no open position) — delete never liquidates.')) return;
        btn.disabled = true;
        PSRequest.postAction('/api/sleeves/' + sleeveId + '/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken() || '' }
        }).then(function (res) {
            if (res.networkError) {
                console.error('deleteSleeve failed: request not completed');
                btn.disabled = false;
                return;
            }
            if (res.refused === 'csrf') {
                window.alert(PSRequest.notSentText(res, 'Click Delete again.'));
                btn.disabled = false;
                return;
            }
            if (!res.ok) {
                window.alert('Delete rejected: ' + ((res.body && res.body.message) || 'unknown error'));
                btn.disabled = false;
                return;
            }
            location.reload();
        });
    }
    window.deleteSleeve = deleteSleeve;
})();
