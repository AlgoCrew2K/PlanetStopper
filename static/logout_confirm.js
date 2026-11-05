/* Sign-out confirm page: one header-token POST to /logout, never re-sent on its own. */
(function () {
  'use strict';

  var form = document.getElementById('logout-form');
  var button = document.querySelector('[data-testid="logout-btn"]');
  var message = document.querySelector('[data-testid="logout-message"]');
  if (!form || !button || !message) return;

  // The process token rendered with the page, used when the fetched token has not arrived yet.
  var pageToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  function show(text) {
    message.textContent = text;
    message.hidden = false;
  }

  function signOut() {
    if (button.disabled) return;
    button.disabled = true;
    message.hidden = true;
    message.textContent = '';

    PSRequest.postAction('/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': PSRequest.csrfToken() || pageToken }
    }).then(function (res) {
      if (res.ok) {
        location.assign('/login');
        return;
      }
      if (res.refused === 'csrf' && res.tokenRefreshed) {
        show('This page expired after a restart; choose Sign out again');
      } else if (res.networkError || res.refused === 'csrf') {
        show(PSRequest.notSentText(res, 'Choose Sign out again.'));
      } else {
        show('Not signed out: the server answered HTTP ' + res.status + '. Choose Sign out again.');
      }
      button.disabled = false;
    });
  }

  button.addEventListener('click', function (event) {
    event.preventDefault();
    signOut();
  });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    signOut();
  });
})();
