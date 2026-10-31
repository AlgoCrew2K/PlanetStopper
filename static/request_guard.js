'use strict';

/**
 * window.PSRequest: the one request helper every page shares.
 *
 * Loaded by templates/_chrome.html before chrome.js, so it is defined before any page script runs.
 *
 * GET side
 *   issue(key) / isLatest(key, n)   the newest request issued under a key is the only one a page draws
 *   getJson(url)                    one GET, answered as {ok:true, status, body, timing} for a 2xx JSON
 *                                   body, else {ok:false, status, kind}; an answer that is not data
 *                                   carries no body, so a page cannot draw it
 *   goLogin()                       a lapsed session goes to the login page
 *
 * POST side
 *   csrfToken()                     this tab's one token, read once when the page loads
 *   refreshCsrf()                   read it again (concurrent callers share one request)
 *   noteDaemonStart(iso)            a changed daemon start stamp means a restart: read a new token
 *   postAction(url, init)           send the caller's request exactly once; a 403 refreshes the token
 *                                   and is reported as refused. Nothing in this module sends a POST a
 *                                   second time: only the operator's next click can.
 *   notSentText(res, clickAgain)    the sentence a control shows for a refused POST
 *
 * State lives in this tab's memory only, so two tabs never share a token or a request counter.
 */
(function () {
  var TOKEN_URL = '/api/csrf-token';
  var LOGIN_URL = '/login';
  var STATUS_FORBIDDEN = 403;
  var STATUS_UNAUTHORIZED = 401;

  // ── newest request wins ────────────────────────────────────────────────────
  var _issued = {};

  function issue(key) {
    _issued[key] = (_issued[key] || 0) + 1;
    return _issued[key];
  }

  function isLatest(key, n) {
    return _issued[key] === n;
  }

  // ── GET ────────────────────────────────────────────────────────────────────
  function getJson(url) {
    var sentAtMono = performance.now();
    return fetch(url).then(function (response) {
      var timing;
      if (typeof window.receiptTiming === 'function') {
        timing = window.receiptTiming(sentAtMono, response);
      } else {
        timing = { sentAtMono: sentAtMono, receivedAtMono: performance.now(), serverNowMs: null };
      }
      if (response.status === STATUS_UNAUTHORIZED) {
        return { ok: false, status: response.status, kind: 'unauthorized' };
      }
      if (!response.ok) {
        return { ok: false, status: response.status, kind: 'http' };
      }
      return response.json().then(
        function (body) { return { ok: true, status: response.status, body: body, timing: timing }; },
        function () { return { ok: false, status: response.status, kind: 'not_json' }; }
      );
    }, function () {
      return { ok: false, status: 0, kind: 'network' };
    });
  }

  function goLogin() {
    location.assign(LOGIN_URL);
  }

  // ── the tab's CSRF token ───────────────────────────────────────────────────
  var _token = null;
  var _refreshing = null;

  function csrfToken() {
    return _token;
  }

  function _readToken() {
    return fetch(TOKEN_URL)
      .then(function (r) { return r.json(); })
      .then(function (b) {
        if (b && typeof b.csrf_token === 'string' && b.csrf_token) {
          _token = b.csrf_token;
          return true;
        }
        return false;
      })
      .catch(function () { return false; });
  }

  function refreshCsrf() {
    if (_refreshing) return _refreshing;
    _refreshing = _readToken().then(function (arrived) {
      _refreshing = null;
      return arrived;
    });
    return _refreshing;
  }

  document.addEventListener('DOMContentLoaded', function () {
    refreshCsrf();
  });

  // ── restart detection ──────────────────────────────────────────────────────
  var _startStamp = null;

  function noteDaemonStart(iso) {
    if (typeof iso !== 'string' || !iso) return;
    if (_startStamp === null) {
      _startStamp = iso;
      return;
    }
    if (iso !== _startStamp) {
      _startStamp = iso;
      refreshCsrf();
    }
  }

  // ── POST ───────────────────────────────────────────────────────────────────
  function postAction(url, init) {
    return fetch(url, init).then(function (response) {
      var status = response.status;
      var ok = response.ok;
      return response.json().then(
        function (body) { return body; },
        function () { return null; }
      ).then(function (body) {
        if (status === STATUS_FORBIDDEN) {
          // Every 403 on a POST is the session check's refusal: read a new token for the
          // operator's next click and report the refusal. The request is never sent again.
          return refreshCsrf().then(function (refreshed) {
            return {
              status: status, ok: false, body: body, refused: 'csrf',
              tokenRefreshed: refreshed, networkError: false
            };
          });
        }
        return {
          status: status, ok: ok, body: body, refused: null,
          tokenRefreshed: false, networkError: false
        };
      });
    }, function () {
      return {
        status: 0, ok: false, body: null, refused: null,
        tokenRefreshed: false, networkError: true
      };
    });
  }

  // What a control says when its POST was refused for the session check (403) and so was not sent:
  // the cause, and that the operator's next click is the one that sends it. `clickAgain` names that
  // click (for example 'Click Save again.').
  function notSentText(res, clickAgain) {
    return 'Not sent: ' + (res.tokenRefreshed
      ? 'the session check refused it.'
      : 'the connection failed, so the session could not be refreshed.') + ' ' + clickAgain;
  }

  window.PSRequest = {
    issue: issue,
    isLatest: isLatest,
    getJson: getJson,
    goLogin: goLogin,
    csrfToken: csrfToken,
    refreshCsrf: refreshCsrf,
    noteDaemonStart: noteDaemonStart,
    postAction: postAction,
    notSentText: notSentText
  };
})();
