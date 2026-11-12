/* window.PSGlossary: titles from Glossary lines. Writes attributes only, never markup. */
(function () {
  var MARK = 'data-glossary-titled';

  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
  }

  function leadTerm(line) {
    var i = line.indexOf(': ');
    return i < 0 ? null : line.slice(0, i);
  }

  function readHost(host) {
    var byTerm = {};
    var byKey = {};
    var paras = host.querySelectorAll('p[data-term]');
    for (var i = 0; i < paras.length; i++) {
      var line = clean(paras[i].textContent);
      var lead = leadTerm(line);
      if (lead !== null && !(lead in byTerm)) { byTerm[lead] = line; }
      var key = paras[i].getAttribute('data-term');
      if (!(key in byKey)) { byKey[key] = line; }
    }
    return { byTerm: byTerm, byKey: byKey };
  }

  function nearestHost(el) {
    for (var a = el.parentElement; a; a = a.parentElement) {
      for (var c = a.firstElementChild; c; c = c.nextElementSibling) {
        if (c !== el && c.hasAttribute('data-glossary-host')) { return c; }
      }
    }
    return null;
  }

  function title(el, line) {
    if (el.hasAttribute('title') && !el.hasAttribute(MARK)) { return; }
    if (el.getAttribute('title') !== line) { el.setAttribute('title', line); }
    if (!el.hasAttribute(MARK)) { el.setAttribute(MARK, ''); }
  }

  function isLabel(el) {
    var tag = el.tagName.toLowerCase();
    return tag === 'th' || el.getAttribute('role') === 'columnheader' || el.childElementCount === 0;
  }

  function apply(root) {
    root = root || document;
    var cache = [];
    var hosts = [];
    function lines(host) {
      var i = hosts.indexOf(host);
      if (i < 0) { hosts.push(host); cache.push(readHost(host)); i = hosts.length - 1; }
      return cache[i];
    }
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.closest('[data-glossary-host]')) { continue; }
      var host = nearestHost(el);
      if (!host) { continue; }
      var key = el.getAttribute('data-glossary');
      if (key !== null) {
        var byKey = lines(host).byKey;
        if (key in byKey) { title(el, byKey[key]); }
        continue;
      }
      if (!isLabel(el)) { continue; }
      var text = clean(el.textContent).replace(/:$/, '').trim();
      var byTerm = lines(host).byTerm;
      if (text !== '' && text in byTerm) { title(el, byTerm[text]); }
    }
  }

  var pending = false;
  function schedule() {
    if (pending) { return; }
    pending = true;
    window.requestAnimationFrame(function () {
      pending = false;
      apply(document);
    });
  }

  function start() {
    apply(document);
    if (document.body && window.MutationObserver) {
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    }
  }

  window.PSGlossary = { apply: apply };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
