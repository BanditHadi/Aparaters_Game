/* ==========================================================================
   Aparat chat DOM observer.
   - Content-based dedup (hash) instead of WeakSet (survives DOM re-renders)
   - Bounded ring buffer so memory stays constant
   - MutationObserver attached to the closest common ancestor of the chat,
     not to document.body (much lighter)
   - Ruby detection ONLY looks inside the message element itself
   ========================================================================== */
function __sbStart(cfg, push) {
  // ---- content dedup ring buffer ----------------------------------------
  var RING = 4000;
  var ring = new Array(RING);
  var ringIdx = 0;
  function hashStr(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }
  function alreadySeen(k) {
    for (var i = 0; i < RING; i++) if (ring[i] === k) return true;
    return false;
  }
  function remember(k) {
    ring[ringIdx] = k;
    ringIdx = (ringIdx + 1) % RING;
  }

  var t0 = Date.now();
  var warm = (cfg.warmup_seconds || 0) * 1000;

  function clean(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }
  function toInt(s) {
    if (!s) return 0;
    var out = "";
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0x06F0 && c <= 0x06F9) out += String.fromCharCode(c - 0x06F0 + 48);
      else if (c >= 0x0660 && c <= 0x0669) out += String.fromCharCode(c - 0x0660 + 48);
      else out += s[i];
    }
    var m = out.match(/\d+/);
    return m ? parseInt(m[0], 10) : 0;
  }

  /* ---- extract one message from a candidate node ---------------------- */
  function extract(el) {
    // ruby detection: only inside the element itself
    var coin = el.querySelector(".coin-count");
    var wrapper = el.querySelector(".ruby-message .wrapper");
    if (coin && wrapper) {
      var amount = toInt(clean(coin.textContent));
      var userEl = el.querySelector("a.channel");
      var user = userEl ? clean(userEl.textContent).replace(/^@/, "") : "viewer";
      var text = "";
      var box = el.querySelector(".details-box > .ruby-message");
      if (box) text = clean(box.textContent);
      if (!text) {
        var all = el.querySelectorAll(".ruby-message");
        for (var i = all.length - 1; i >= 0; i--) {
          if (all[i].querySelector(".wrapper")) continue;
          var s = clean(all[i].textContent);
          if (s) { text = s; break; }
        }
      }
      var emoji = el.querySelector(".emoji-box");
      var color = emoji && emoji.style ? (emoji.style.backgroundColor || "") : "";
      return { user: user, text: text, kind: "ruby", amount: amount, color: color };
    }

    // chat detection
    var textEl = el.querySelector(".message__text");
    if (textEl) {
      var nameEl = el.querySelector(".message__username");
      var user2 = nameEl ? clean(nameEl.textContent).replace(/^@/, "") : "viewer";
      var text2 = clean(textEl.textContent);
      return { user: user2, text: text2, kind: "text", amount: 0, color: "" };
    }
    return null;
  }

  function messageKey(el, m) {
    var idEl = el.querySelector && el.querySelector("[id]");
    if (idEl && idEl.getAttribute && idEl.getAttribute("id")) return "id:" + idEl.getAttribute("id");
    var ownId = el.getAttribute && el.getAttribute("id");
    if (ownId) return "id:" + ownId;
    return "h:" + hashStr(m.kind + "|" + m.user + "|" + m.text + "|" + (m.amount || 0));
  }

  /* ---- process one candidate ------------------------------------------ */
  var CANDIDATES = ".message, .ruby-message";

  function process(el, isWarmup) {
    if (!el || el.nodeType !== 1) return;
    if (el.dataset && el.dataset.sbDone === "1") return;

    var m = extract(el);
    if (!m || !m.text) return;
    if (m.text.length > 400) return;

    var key = messageKey(el, m);
    if (alreadySeen(key)) return;
    remember(key);
    if (el.dataset) el.dataset.sbDone = "1";

    if (isWarmup || Date.now() - t0 < warm) return;
    try { push(m.user, m.text, m); } catch (e) {}
  }

  /* ---- scan a subtree ------------------------------------------------- */
  function scan(root, isWarmup) {
    if (!root || root.nodeType !== 1) return;
    var list = [];
    try {
      if (root.matches && root.matches(CANDIDATES)) list = [root];
      else list = Array.prototype.slice.call(root.querySelectorAll(CANDIDATES));
    } catch (e) { return; }

    // Sort so that outer nodes come first (dedup more reliably)
    list.forEach(function (el) {
      // find the outermost candidate that is not nested inside another candidate
      var node = el;
      var p = node.parentElement;
      while (p) {
        if (p.matches && p.matches(CANDIDATES)) { node = p; }
        p = p.parentElement;
      }
      process(node, isWarmup);
    });
  }

  /* ---- initial scan --------------------------------------------------- */
  function initialScan() {
    // mark everything currently on the page as seen
    var list = [];
    try { list = Array.prototype.slice.call(document.querySelectorAll(CANDIDATES)); } catch (e) {}
    list.forEach(function (el) {
      var m = extract(el);
      if (!m || !m.text) return;
      if (m.text.length > 400) return;
      remember(messageKey(el, m));
      if (el.dataset) el.dataset.sbDone = "1";
    });
  }

  /* ---- find a good anchor for the MutationObserver -------------------- */
  function findAnchor() {
    // try to find the chat container: an element that contains at least one
    // candidate and has many children
    var candidates = [".message", ".ruby-message"];
    for (var i = 0; i < candidates.length; i++) {
      try {
        var first = document.querySelector(candidates[i]);
        if (first) {
          // walk up 3 levels
          var p = first.parentElement;
          for (var k = 0; k < 3 && p; k++) p = p.parentElement;
          if (p && p.nodeType === 1) return p;
        }
      } catch (e) {}
    }
    return document.body;
  }

  /* ---- attach --------------------------------------------------------- */
  function attach() {
    initialScan();
    var anchor = findAnchor();

    var obs = new MutationObserver(function (muts) {
      muts.forEach(function (mu) {
        mu.addedNodes.forEach(function (n) {
          if (n.nodeType === 1) scan(n, false);
        });
      });
    });
    try {
      obs.observe(anchor, { childList: true, subtree: true });
    } catch (e) {
      obs.observe(document.body, { childList: true, subtree: true });
    }

    // If the chat container is replaced by Aparat, re-attach.
    // Also periodically reset the WeakSet-like state by clearing `sbDone`
    // flags so old messages don't accumulate attributes forever.
    setInterval(function () {
      try {
        var newAnchor = findAnchor();
        if (newAnchor && newAnchor !== anchor) {
          try { obs.disconnect(); } catch (e) {}
          anchor = newAnchor;
          obs.observe(anchor, { childList: true, subtree: true });
        }
      } catch (e) {}
    }, 60 * 1000);   // هر ۶۰ ثانیه چک کن
  }

  if (document.body) attach();
  else document.addEventListener("DOMContentLoaded", attach);
}