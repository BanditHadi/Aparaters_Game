/* ==========================================================================
   Streamer Boost Live - overlay client (Persian UI)
   - Top chatters: 10 cards in a 5-slot frame, auto-scroll
   - Reuse DOM nodes (no full rebuild) so scroll offset is preserved
   - Booster: robust update from init / board / vote / periodic message
   - Sound: AudioContext always resumed before playing each tone
   ========================================================================== */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var DEMO = window.DEMO === true || params.get('demo') === '1';
  var COLS = Math.max(4, Math.min(14, parseInt(params.get('cols') || '9', 10) || 9));
  var SFX_ON = params.get('sfx') !== '0';
  var VOICE_ON = params.get('voice') !== '0';
  var SFX_VOL = parseFloat(params.get('volume') || '0.7');
  if (params.get('bg') === 'transparent') document.body.classList.add('transparent');

  var CHATTERS_TOTAL = 10;
  var CHATTERS_VISIBLE = 5;

  var $ = function (s, r) { return (r || document).querySelector(s); };
  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var stage = $('#stage'), grid = $('#grid'), board = $('#board'), ticker = $('#ticker');
  var boosterFill = $('#booster-fill'), boosterText = $('#booster-text'),
      boosterGlow = $('#booster-glow'), boosterWrap = $('#booster');
  var chatterRow = $('#chatter-row');
  var CROWN = '<svg viewBox="0 0 64 44" aria-hidden="true"><path d="M4 38 L8 10 L22 24 L32 4 L42 24 L56 10 L60 38 Z" fill="#ffc933" stroke="#a86a00" stroke-width="3" stroke-linejoin="round"/><rect x="6" y="34" width="52" height="6" rx="2" fill="#f0a800" stroke="#a86a00" stroke-width="2"/><circle cx="8" cy="9" r="4.5" fill="#ff5a8a"/><circle cx="32" cy="4.5" r="5" fill="#6be7ff"/><circle cx="56" cy="9" r="4.5" fill="#ff5a8a"/></svg>';
  var RUBY_ICON = '<svg viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="rg1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4fa8ff"/><stop offset="1" stop-color="#0052b3"/></linearGradient></defs><polygon points="32,58 4,24 14,8 50,8 60,24" fill="url(#rg1)" stroke="#fff" stroke-width="2.5" stroke-linejoin="round"/><polyline points="4,24 60,24 32,58 14,8 24,24 32,8 40,24 50,8" fill="none" stroke="rgba(255,255,255,.75)" stroke-width="2" stroke-linejoin="round"/></svg>';

  var state = {
    config: {}, streamers: new Map(), scores: new Map(), since: new Map(),
    rank: new Map(), order: [], tiles: new Map(), cards: new Map(),
    lastVote: Date.now(), floats: 0, chattersLast: [],
    booster: { progress: '0', target: '50000', active: false, left: 0,
               duration: 60, multiplier: 2 }
  };

  function fit() {
    var s = Math.min(window.innerWidth / 608, window.innerHeight / 1080);
    stage.style.transform = 'translate(-50%, -50%) scale(' + s + ')';
    sizeFx();
  }

  // ---------------------------------------------------------------- BigInt helpers
  function toBig(v) {
    if (v == null) return 0n;
    if (typeof v === 'bigint') return v;
    if (typeof v === 'number') {
      if (!isFinite(v)) return 0n;
      return BigInt(Math.round(v));
    }
    try {
      var s = String(v).trim();
      if (!s) return 0n;
      var neg = s[0] === '-';
      if (neg) s = s.slice(1);
      s = s.replace(/[^\d]/g, '');
      if (!s) return 0n;
      var b = BigInt(s);
      return neg ? -b : b;
    } catch (e) { return 0n; }
  }

  function bigWithCommas(b) {
    if (b == null) return '∞';
    var neg = b < 0n;
    if (neg) b = -b;
    var s = b.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return neg ? '-' + s : s;
  }

  var _SCALES = [
    [10n ** 33n, null],
    [10n ** 30n, ' No'],
    [10n ** 27n, ' Oc'],
    [10n ** 24n, ' Sp'],
    [10n ** 21n, ' Sx'],
    [10n ** 18n, ' Qi'],
    [10n ** 15n, ' Qa'],
    [10n ** 12n, 'T'],
    [10n ** 9n,  'B'],
    [10n ** 6n,  'M'],
    [10n ** 3n,  'K'],
  ];

  function bigFmt(b, decimals) {
    if (b == null) return '∞';
    if (decimals == null) decimals = 1;
    var neg = b < 0n;
    var a = neg ? -b : b;
    var sign = neg ? '-' : '';
    for (var i = 0; i < _SCALES.length; i++) {
      var div = _SCALES[i][0], suffix = _SCALES[i][1];
      if (a >= div) {
        if (suffix === null) {
          var s = a.toString();
          var exp = s.length - 1;
          var mant = s[0];
          if (s.length > 1) mant += '.' + s.slice(1, 1 + decimals + 1);
          mant = mant.replace(/\.?0+$/, '');
          return sign + mant + 'e+' + exp;
        }
        var headLen = Math.min(a.toString().length, 18);
        var head = a.toString().slice(0, headLen);
        var headBig = BigInt(head);
        var tailExp = a.toString().length - headLen;
        var scaled = Number(headBig) / Number(div / (10n ** BigInt(tailExp)));
        var out = scaled.toFixed(decimals).replace(/\.?0+$/, '');
        return sign + out + suffix;
      }
    }
    return sign + bigWithCommas(a);
  }

  function bigFmtSigned(b, decimals) {
    if (b == null) return '+∞';
    var neg = b < 0n;
    var a = neg ? -b : b;
    var prefix = neg ? '-' : '+';
    return prefix + bigFmt(a, decimals);
  }

  function cname(code) {
    var st = state.streamers.get(code);
    return st ? (st.display || st.username) : code;
  }
  function picUrl(username) {
    var st = state.streamers.get(username);
    if (!st) return '';
    return st.pic_src || '';
  }
  function picImg(username, cls) {
    var img = new Image();
    if (cls) img.className = cls;
    img.alt = ''; img.decoding = 'async'; img.draggable = false;
    var src = picUrl(username);
    if (src) img.src = src;
    else img.style.display = 'none';
    img.onerror = function () { img.style.visibility = 'hidden'; };
    return img;
  }
  function retrigger(el, cls, ms) {
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
    clearTimeout(el['_t' + cls]);
    el['_t' + cls] = setTimeout(function () { el.classList.remove(cls); }, ms);
  }
  function hash(s) { var x = 0; s = String(s); for (var i = 0; i < s.length; i++) x = (x * 31 + s.charCodeAt(i)) >>> 0; return x; }
  function setAvatar(el, name, url) {
    var key = url || ('L:' + name);
    if (el.dataset.key === key) return;
    el.dataset.key = key; el.textContent = ''; el.style.background = '';
    var letter = (Array.from((name || '?').trim())[0] || '?').toUpperCase();
    function fallback() { el.textContent = letter; el.style.background = 'hsl(' + (hash(name) % 360) + ' 55% 42%)'; }
    if (url) {
      var img = new Image(); img.referrerPolicy = 'no-referrer';
      img.onerror = function () { img.remove(); fallback(); };
      img.src = url; el.appendChild(img);
    } else fallback();
  }

  function makeStars() {
    var box = $('#stars');
    for (var i = 0; i < 46; i++) {
      var s = h('i', 'star');
      s.style.left = Math.random() * 100 + '%'; s.style.top = Math.random() * 100 + '%';
      s.style.setProperty('--d', (3 + Math.random() * 4) + 's');
      s.style.setProperty('--delay', (Math.random() * 5) + 's');
      box.appendChild(s);
    }
  }

  // ------------------------------------------------------------ grid geometry
  var GRID_W = 588;
  var cellW = GRID_W / COLS;
  var fw = Math.floor(cellW - 9);
  var fh = fw;
  var cellH = fh + 7 + 5 + 17 + 20;
  grid.style.width = GRID_W + 'px';
  grid.style.setProperty('--cw', cellW + 'px');
  grid.style.setProperty('--ch', cellH + 'px');
  grid.style.setProperty('--fw', fw + 'px');
  grid.style.setProperty('--fh', fh + 'px');

  function makeTile(code) {
    var el = h('div', 'tile no-anim zero');
    el.dataset.code = code;
    var medal = h('div', 'medal');
    var crown = h('div', 'crown'); crown.innerHTML = CROWN;
    var wrap = h('div', 'flagwrap');
    var src = picUrl(code);
    if (src) {
      var img = picImg(code, 'flag');
      img.title = cname(code);
      wrap.appendChild(img);
    } else {
      var st = state.streamers.get(code);
      var name = st ? (st.display || st.username) : code;
      var letter = (Array.from(name)[0] || '?').toUpperCase();
      var fb = h('div', 'flag-fallback', letter);
      fb.style.background = 'hsl(' + (hash(name) % 360) + ' 55% 42%)';
      wrap.appendChild(fb);
    }
    wrap.appendChild(h('span', 'gloss'));
    var score = h('div', 'score', '۰');
    el.append(medal, crown, wrap, score, h('span', 'ripple'));
    grid.appendChild(el);
    var t = { code: code, el: el, medal: medal, scoreEl: score, shown: 0n, target: 0n, raf: 0, x: null, y: null, rank: 0, rc: '' };
    state.tiles.set(code, t);
    return t;
  }
  function tile(code) { return state.tiles.get(code) || makeTile(code); }

  function refreshTilePic(code) {
    var t = state.tiles.get(code);
    if (!t) return;
    var wrap = t.el.querySelector('.flagwrap');
    if (!wrap) return;
    var src = picUrl(code);
    var existing = wrap.querySelector('.flag');
    var fallback = wrap.querySelector('.flag-fallback');
    if (src) {
      if (existing && existing.src.endsWith(src.split('/').pop())) return;
      if (existing) existing.remove();
      if (fallback) fallback.remove();
      var img = picImg(code, 'flag');
      img.title = cname(code);
      wrap.insertBefore(img, wrap.firstChild);
    }
  }

  function computeOrder() {
    var codes = Array.from(state.streamers.keys());
    codes.sort(function (a, b) {
      var sa = toBig(state.scores.get(a) || 0);
      var sb = toBig(state.scores.get(b) || 0);
      if (sa !== sb) return sa > sb ? -1 : 1;
      var ia = state.since.get(a) || 0, ib = state.since.get(b) || 0;
      if (ia !== ib) return ia - ib;
      return a < b ? -1 : 1;
    });
    return codes;
  }

  function relayout() {
    var order = computeOrder();
    var rank = new Map();
    order.forEach(function (code, i) {
      var t = tile(code), r = i + 1;
      var score = toBig(state.scores.get(code) || 0);
      rank.set(code, r);
      var x = (i % COLS) * cellW, y = Math.floor(i / COLS) * cellH + 26;
      if (t.x !== x || t.y !== y) { t.x = x; t.y = y; t.el.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)'; }
      var rc = (r <= 3 && score > 0n) ? 'r' + r : '';
      if (rc !== t.rc) {
        if (t.rc) t.el.classList.remove(t.rc);
        if (rc) { t.el.classList.add(rc); t.medal.textContent = r; }
        t.rc = rc;
      }
      t.el.classList.toggle('zero', score <= 0n);
    });
    state.rank = rank; state.order = order;
  }

  function buildGrid() {
    var rows = Math.ceil(state.streamers.size / COLS);
    grid.style.height = (rows * cellH + 44) + 'px';
    state.streamers.forEach(function (_c, code) { tile(code); });
  }

  function setScore(t, value, animate) {
    var b = toBig(value);
    t.target = b;
    if (!animate) { t.shown = b; t.scoreEl.textContent = bigFmt(b, 1); return; }
    if (t.raf) return;
    if (b > 10000000n) { t.shown = b; t.scoreEl.textContent = bigFmt(b, 1); return; }
    var step = function () {
      var diff = t.target - t.shown;
      if (diff < 1n && diff > -1n) { t.shown = t.target; t.scoreEl.textContent = bigFmt(t.shown, 1); t.raf = 0; return; }
      t.shown = t.shown + diff / 5n + (diff > 0n ? 1n : -1n);
      if ((diff > 0n && t.shown > t.target) || (diff < 0n && t.shown < t.target)) t.shown = t.target;
      t.scoreEl.textContent = bigFmt(t.shown, 1);
      t.raf = requestAnimationFrame(step);
    };
    t.raf = requestAnimationFrame(step);
  }

  function floatText(t, text, cls) {
    if (state.floats > 36) return;
    state.floats++;
    var f = h('div', 'float ' + cls, text);
    t.el.appendChild(f);
    setTimeout(function () { f.remove(); state.floats--; }, 1400);
  }
  function trend(t, dir) {
    var el = h('div', 'trend ' + dir, dir === 'up' ? '\u25B2' : '\u25BC');
    t.el.appendChild(el); setTimeout(function () { el.remove(); }, 1800);
  }

  function jumpTop() { board.scrollTop = 0; }

  // ------------------------------------------------------------------ ticker
  var tickPending = null, tickTimer = 0, lastHint = 0, hintIdx = 0;
  function showTicker(v) {
    tickPending = v;
    if (tickTimer) return;
    tickTimer = setTimeout(function () { tickTimer = 0; renderTicker(tickPending); }, 90);
  }
  function swapTicker(line) {
    Array.from(ticker.children).forEach(function (o) {
      o.classList.remove('tick-in'); o.classList.add('tick-out');
      setTimeout(function () { o.remove(); }, 330);
    });
    ticker.appendChild(line);
  }
  function renderTicker(v) {
    var line = h('div', 'tick-line tick-in ' + v.kind);
    var lead;
    if (v.kind === 'ruby') {
      lead = 'روبی' + (v.ruby_count ? ' ×' + v.ruby_count : '');
    } else if (v.kind === 'member') {
      lead = 'عضو جدید';
    } else {
      lead = 'لِوِل ' + ((v.user && v.user.level) || 1);
    }
    line.appendChild(h('span', 'lv', lead));
    var who = h('span', 'who'); who.append('@');
    var b = document.createElement('bdi'); b.textContent = (v.user && v.user.name) || 'بیننده'; who.appendChild(b);
    line.appendChild(who);
    var mini = picImg(v.code, 'mini');
    var src = picUrl(v.code);
    if (!src) {
      var st = state.streamers.get(v.code);
      var name = st ? (st.display || st.username) : v.code;
      var letter = (Array.from(name)[0] || '?').toUpperCase();
      var fb = h('span', 'mini fallback', letter);
      fb.style.background = 'hsl(' + (hash(name) % 360) + ' 55% 42%)';
      line.appendChild(fb);
    } else {
      line.appendChild(mini);
    }
    line.appendChild(h('span', 'cn', cname(v.code)));
    if (v.multiplier && v.multiplier > 1) {
      line.appendChild(h('span', 'mult', '×' + v.multiplier));
    }
    line.appendChild(h('span', 'plus', bigFmtSigned(toBig(v.points), 2)));
    if (v.level_up) line.appendChild(h('span', 'lvup', 'ارتقای لِوِل!'));
    swapTicker(line);
  }
  function idleHint() {
    var now = Date.now();
    if (now - state.lastVote < 8000 || now - lastHint < 6500) return;
    lastHint = now;
    var cfg = state.config || {};
    var hints = [
      'اسم استریمرت رو تو چت بنویس تا امتیاز بگیره!',
      'روبی: <b>هر ۱ 💎 = +' + (cfg.ruby_points || 2) + '</b> امتیاز برای استریمرت',
      'عضو جدید = <b>+' + (cfg.member_points || 400) + '</b> امتیاز برای استریمرت'
    ];
    var line = h('div', 'tick-line hint tick-in');
    line.innerHTML = hints[hintIdx++ % hints.length];
    swapTicker(line);
  }

  // ============================================================
  //  BOOSTER
  // ============================================================
  function renderBooster() {
    if (!boosterWrap || !boosterFill || !boosterText) return;
    var b = state.booster || {};
    var progress = toBig(b.progress || 0);
    var target = toBig(b.target || 50000);
    var active = !!b.active;
    boosterWrap.classList.toggle('active', active);
    if (active) {
      var left = Math.max(0, Number(b.left) || 0);
      var dur = Number(b.duration) || 60;
      var ratio = dur > 0 ? left / dur : 0;
      boosterFill.style.width = (ratio * 100).toFixed(2) + '%';
      boosterText.innerHTML = '🔥 بوستر فعال = امتیاز ×' + (b.multiplier || 2) +
        ' برای ' + Math.ceil(left) + ' ثانیه';
      if (!stage.classList.contains('booster-active')) {
        stage.classList.add('booster-active');
      }
    } else {
      stage.classList.remove('booster-active');
      var ratio2 = target > 0n ? Number(progress * 10000n / target) / 100 : 0;
      if (ratio2 > 100) ratio2 = 100;
      if (ratio2 < 0) ratio2 = 0;
      boosterFill.style.width = ratio2.toFixed(2) + '%';
      boosterText.innerHTML = bigFmt(progress, 1) + ' / ' + bigFmt(target, 1) +
        ' &nbsp; (' + Math.floor(ratio2) + '٪)';
    }
  }
  setInterval(renderBooster, 200);
  (function tickBooster() {
    requestAnimationFrame(tickBooster);
    if (state.booster && state.booster.active) renderBooster();
  })();

  function activateBoosterEffect() {
    stage.classList.remove('booster-effect');
    void stage.offsetWidth;
    stage.classList.add('booster-effect');
    setTimeout(function () { stage.classList.remove('booster-effect'); }, 3000);
    confetti(180, 1.2, ['#ffd23f', '#ffe14a', '#ffb300', '#fff3a0']);
  }

  // ============================================================
  //  TOP CHATTERS - reuse DOM nodes, only update
  // ============================================================
  var voterPics = new Map();
  var voterPicsPending = new Set();

  function ensureVoterPic(name, avatarUrl) {
    if (avatarUrl) return avatarUrl;
    if (voterPics.has(name)) return voterPics.get(name);
    if (voterPicsPending.has(name)) return "";
    voterPicsPending.add(name);
    fetch('/api/voter_pic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: name })
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        voterPics.set(name, (j && j.ok && j.src) ? j.src : "");
        voterPicsPending.delete(name);
        renderChatters(state.chattersLast);
      })
      .catch(function () {
        voterPics.set(name, "");
        voterPicsPending.delete(name);
      });
    return "";
  }

  var scrollState = { offset: 0, dir: 1, lastTs: 0, pausedUntil: 0 };

  function layoutChatters() {
    var gap = 6;
    var W = chatterRow.clientWidth || 548;
    var cw = (W - gap * (CHATTERS_VISIBLE - 1)) / CHATTERS_VISIBLE;
    chatterRow.style.setProperty('--ccw', cw + 'px');
    return { cw: cw, gap: gap, W: W, vis: CHATTERS_VISIBLE, n: CHATTERS_TOTAL };
  }

  function applyChattersScroll() {
    var m = layoutChatters();
    var maxIndex = Math.max(0, m.n - m.vis);
    var maxOffset = maxIndex * (m.cw + m.gap);
    var off = Math.min(maxOffset, Math.max(0, scrollState.offset));
    if (!isFinite(off)) off = 0;
    // update every card's left based on its CURRENT _index (from _lastOrder)
    for (var i = 0; i < _lastOrder.length; i++) {
      var uid = _lastOrder[i];
      var card = state.cards.get(uid);
      if (!card) continue;
      var leftPx = i * (m.cw + m.gap) - off;
      if (!isFinite(leftPx)) leftPx = 0;
      card.style.left = leftPx + 'px';
    }
  }

  function chattersScrollLoop(ts) {
    var now = (typeof ts === 'number') ? ts : performance.now();
    var last = scrollState.lastTs || now;
    var dt = Math.min(0.05, (now - last) / 1000);
    scrollState.lastTs = now;

    if (now >= scrollState.pausedUntil) {
      var m = layoutChatters();
      var maxOffset = Math.max(0, m.n - m.vis) * (m.cw + m.gap);
      var speed = 26;
      scrollState.offset += scrollState.dir * speed * dt;
      if (scrollState.offset >= maxOffset) {
        scrollState.offset = maxOffset;
        scrollState.dir = -1;
        scrollState.pausedUntil = now + 2500;
      } else if (scrollState.offset <= 0) {
        scrollState.offset = 0;
        scrollState.dir = 1;
        scrollState.pausedUntil = now + 2500;
      }
      applyChattersScroll();
    }
    requestAnimationFrame(chattersScrollLoop);
  }

  function pauseChattersScroll(ms) {
    scrollState.pausedUntil = performance.now() + (ms || 1500);
  }

  // لیست رتبه‌بندی فعلی کارت‌ها (به ترتیب از 0 تا n-1)
  var _lastOrder = [];

  function makeChatterCard() {
    var card = h('div', 'cc');
    card.innerHTML =
      '<div class="av-wrap">' +
        '<div class="av"></div>' +
        '<div class="crown-c">' + CROWN + '</div>' +
        '<div class="av-flag"></div>' +
        '<div class="rk-no"></div>' +
      '</div>' +
      '<div class="cname"><bdi></bdi></div>' +
      '<div class="cchats"><b></b> چت</div>' +
      '<div class="cpts"></div>';
    return card;
  }

  function renderChatters(list) {
    state.chattersLast = list || [];
    $('#ch-empty').style.opacity = list.length ? 0 : 1;

    var items = list.slice(0, CHATTERS_TOTAL);
    var m = layoutChatters();

    // 1) به‌روزرسانی/ساخت کارت‌ها (reuse)
    var liveIds = new Set();
    items.forEach(function (u, i) {
      liveIds.add(u.id);
      var card = state.cards.get(u.id);
      if (!card) {
        card = makeChatterCard();
        state.cards.set(u.id, card);
        // کارت جدید را در ابتدا با یک انیمیشن ظاهر کن
        card.classList.add('cc-new');
        setTimeout(function () { card.classList.remove('cc-new'); }, 600);
      }
      // مطمئن شو در DOM هست
      if (card.parentNode !== chatterRow) {
        chatterRow.appendChild(card);
      }

      // رتبه/کلاس‌ها
      card.classList.remove('rk1', 'rk2', 'rk3');
      if (i < 3) card.classList.add('rk' + (i + 1));
      $('.rk-no', card).textContent = i + 1;

      // آواتار
      var av = $('.av', card);
      var avatarUrl = u.avatar || ensureVoterPic(u.name, u.avatar);
      setAvatar(av, u.name, avatarUrl);

      // متن‌ها
      $('bdi', card).textContent = u.name;
      $('.cchats b', card).textContent = String(u.chats);
      $('.cpts', card).textContent = bigFmtSigned(toBig(u.points), 2) + ' امتیاز';

      // پرچم استریمر
      var fl = $('.av-flag', card);
      if (u.streamer) {
        var src = picUrl(u.streamer);
        if (src) {
          // اگر پرچم عوض نشده، دوباره نساز
          if (fl.dataset.c !== u.streamer) {
            fl.dataset.c = u.streamer;
            fl.textContent = '';
            fl.appendChild(picImg(u.streamer));
          }
          fl.style.display = '';
        } else {
          fl.style.display = 'none';
        }
      } else {
        fl.style.display = 'none';
      }
    });

    // 2) حذف کارت‌هایی که در لیست جدید نیستند
    state.cards.forEach(function (card, id) {
      if (!liveIds.has(id)) {
        card.classList.add('leaving');
        state.cards.delete(id);
        setTimeout(function () { card.remove(); }, 600);
      }
    });

    // 3) ترتیب نهایی را ذخیره کن و موقعیت‌ها را اعمال کن
    _lastOrder = items.map(function (u) { return u.id; });
    applyChattersScroll();
  }

  // ------------------------------------------------------------------ banners
  var bannerEl = $('#banner');
  function banner(kind, tag, codes, text) {
    bannerEl.className = 'banner ' + kind;
    bannerEl.textContent = '';
    if (tag) bannerEl.appendChild(h('span', 'tag', tag));
    (codes || []).forEach(function (c) {
      var src = picUrl(c);
      if (src) bannerEl.appendChild(picImg(c));
    });
    bannerEl.appendChild(h('span', 'txt', text));
    void bannerEl.offsetWidth;
    bannerEl.classList.add('show');
  }

  // --------------------------------------------------------- Ruby splash
  var splashEl = $('#splash'), splashQ = [], splashBusy = false;
  function enqueueSplash(v) { if (splashQ.length < 4) splashQ.push(v); pumpSplash(); }
  function pumpSplash() {
    if (splashBusy || !splashQ.length) return;
    splashBusy = true;
    var v = splashQ.shift();
    var isRuby = v.kind === 'ruby';
    var isMember = v.kind === 'member';
    splashEl.textContent = '';
    splashEl.appendChild(h('div', 'sp-rays'));
    var card = h('div', 'sp-card' + (isRuby ? ' ruby' : ''));
    var kind = isRuby ? 'هدیه‌ی روبی' : isMember ? 'عضو جدید' : 'سوپرچت';
    card.appendChild(h('div', 'sp-kind' + (isRuby ? ' ruby' : isMember ? ' member' : ''),
                       kind + (isRuby && v.ruby_count ? ' × ' + v.ruby_count : '')));
    if (isRuby) {
      var ic = h('div', 'sp-ruby-icon');
      ic.innerHTML = RUBY_ICON;
      card.appendChild(ic);
    }
    var src = picUrl(v.code);
    var fl = h('div', 'sp-flag');
    if (src) {
      fl.appendChild(picImg(v.code));
    } else {
      var st = state.streamers.get(v.code);
      var name = st ? (st.display || st.username) : v.code;
      var letter = (Array.from(name)[0] || '?').toUpperCase();
      var fb = h('div', 'sp-flag-fallback', letter);
      fb.style.background = 'hsl(' + (hash(name) % 360) + ' 55% 42%)';
      fl.appendChild(fb);
    }
    card.appendChild(fl);
    card.appendChild(h('div', 'sp-country', cname(v.code)));
    var pts = h('div', 'sp-points');
    pts.append(bigFmtSigned(toBig(v.points), 0) + ' ');
    pts.appendChild(h('small', '', 'امتیاز'));
    card.appendChild(pts);
    var who = h('div', 'sp-user');
    var b = document.createElement('bdi'); b.textContent = v.user.name;
    who.append('@'); who.appendChild(b);
    if (isRuby && v.ruby_count) who.append(' · ' + v.ruby_count + ' 💎');
    else if (v.amount) who.append(' · ' + v.amount);
    card.appendChild(who);
    splashEl.appendChild(card);
    splashEl.classList.add('on');
    splashEl.classList.toggle('ruby-mode', isRuby);
    stage.classList.remove('shake'); void stage.offsetWidth; stage.classList.add('shake');
    if (isRuby) sfx.ruby(); else sfx.super();
    confetti(isRuby ? 120 : 150, 1);
    setTimeout(function () { confetti(90, 0.8); }, 700);
    jumpTop();
    setTimeout(function () { card.classList.add('out'); splashEl.classList.remove('on'); }, 4300);
    setTimeout(function () { splashBusy = false; pumpSplash(); }, 4800);
  }

  // ---------------------------------------------------------------- confetti
  var fx = $('#fx'), fctx = fx.getContext('2d'), parts = [], fxRun = false;
  function sizeFx() { fx.width = window.innerWidth; fx.height = window.innerHeight; }
  var COLORS = ['#ff6bd6', '#ffd23f', '#6be7ff', '#4dff8a', '#ff7a1a', '#b78cff', '#ffffff'];
  function confetti(n, power, colors) {
    colors = colors || COLORS;
    var r = stage.getBoundingClientRect(), sc = r.width / 608;
    var ox = r.left + r.width / 2, oy = r.top + r.height * 0.42;
    for (var i = 0; i < n; i++) {
      var a = Math.random() * Math.PI * 2, sp = (200 + Math.random() * 700) * sc * (power || 1);
      parts.push({ x: ox, y: oy, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - 350 * sc, rot: Math.random() * 6, vr: (Math.random() - 0.5) * 14,
        w: (6 + Math.random() * 7) * sc, h: (9 + Math.random() * 9) * sc, c: colors[(Math.random() * colors.length) | 0], life: 2.6 + Math.random() * 1.4, g: 900 * sc });
    }
    if (!fxRun) { fxRun = true; lastFx = performance.now(); requestAnimationFrame(fxLoop); }
  }
  var lastFx = 0;
  function fxLoop(ts) {
    var dt = Math.min(0.05, (ts - lastFx) / 1000); lastFx = ts;
    fctx.clearRect(0, 0, fx.width, fx.height);
    parts = parts.filter(function (p) { return p.life > 0 && p.y < fx.height + 40; });
    parts.forEach(function (p) {
      p.vy += p.g * dt; p.vx *= 0.992; p.x += p.vx * dt; p.y += p.vy * dt; p.rot += p.vr * dt; p.life -= dt;
      fctx.save(); fctx.translate(p.x, p.y); fctx.rotate(p.rot); fctx.globalAlpha = Math.min(1, p.life);
      fctx.fillStyle = p.c; fctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); fctx.restore();
    });
    if (parts.length) requestAnimationFrame(fxLoop); else { fxRun = false; fctx.clearRect(0, 0, fx.width, fx.height); }
  }

  // ============================================================
  //  SOUND EFFECTS
  // ============================================================
  var actx = null, lastTick = 0;

  function audioCtx() {
    if (!actx) {
      try { actx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { actx = null; }
    }
    return actx;
  }

  function tryResumeAudio() {
    var a = audioCtx();
    if (!a) return Promise.resolve(false);
    if (a.state === 'running') return Promise.resolve(true);
    return a.resume().then(function () { return a.state === 'running'; }).catch(function () { return false; });
  }

  function playTone(a, freq, dur, type, vol, delay, slideTo) {
    var t0 = a.currentTime + (delay || 0);
    var o = a.createOscillator(), g = a.createGain();
    o.type = type || 'sine';
    o.frequency.setValueAtTime(freq, t0);
    if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, (vol || 0.08) * SFX_VOL), t0 + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(a.destination);
    o.start(t0); o.stop(t0 + dur + 0.03);
  }

  function tone(freq, dur, type, vol, delay, slideTo) {
    if (!SFX_ON) return;
    var a = audioCtx();
    if (!a) return;
    if (a.state === 'running') {
      playTone(a, freq, dur, type, vol, delay, slideTo);
      return;
    }
    a.resume().then(function () {
      if (a.state === 'running') playTone(a, freq, dur, type, vol, delay, slideTo);
    }).catch(function () {});
  }

  var sfx = {
    tick: function (p) {
      var n = performance.now();
      if (n - lastTick < 70) return;
      lastTick = n;
      tone(620 + Math.min(p, 10) * 70, 0.09, 'triangle', 0.05);
    },
    whoosh: function () { tone(180, 0.45, 'sawtooth', 0.035, 0, 1100); },
    lead: function () { [523, 659, 784, 1046].forEach(function (f, i) { tone(f, 0.28, 'triangle', 0.08, i * 0.09); }); },
    level: function () { tone(880, 0.12, 'sine', 0.07); tone(1320, 0.2, 'sine', 0.07, 0.1); },
    fire: function () { tone(300, 0.5, 'sawtooth', 0.04, 0, 1500); },
    super: function () { [523, 659, 784, 1046, 1318, 1568].forEach(function (f, i) { tone(f, 0.4, 'square', 0.045, i * 0.08); }); tone(110, 0.9, 'sine', 0.12, 0); },
    ruby: function () {
      [784, 988, 1175, 1568, 1976].forEach(function (f, i) { tone(f, 0.35, 'sine', 0.055, i * 0.07); });
      tone(196, 0.8, 'triangle', 0.1, 0);
      tone(392, 0.6, 'triangle', 0.07, 0.05);
    },
    booster: function () {
      [523, 659, 784, 1046, 1318, 1568, 2093].forEach(function (f, i) {
        tone(f, 0.5, 'triangle', 0.07, i * 0.09);
      });
      tone(131, 1.4, 'sine', 0.14, 0);
      tone(262, 1.2, 'sine', 0.09, 0.05);
      tone(196, 1.0, 'triangle', 0.08, 0.1);
    }
  };

  // ------------------------------------------------------- announcer
  var speechQ = [], speaking = false, soundBtn = $('#sound-btn');
  function showCaption(text) { $('#caption-text').textContent = text; $('#caption').classList.add('show'); }
  function hideCaption() { $('#caption').classList.remove('show'); }
  function enqueueSpeech(m) { if (speechQ.length > 6) speechQ.shift(); speechQ.push(m); pumpSpeech(); }
  function pumpSpeech() {
    if (speaking || !speechQ.length) return;
    var m = speechQ.shift();
    speaking = true; showCaption(m.text);
    var done = false;
    function finish() { if (done) return; done = true; hideCaption(); speaking = false; setTimeout(pumpSpeech, 200); }
    var guard = (m.duration || 5) * 1000 + 4000;
    if (m.url && VOICE_ON) {
      var a = new Audio(m.url); a.volume = 1;
      a.onended = finish; a.onerror = finish;
      var p = a.play();
      if (p && p.catch) p.catch(function (err) {
        if (err && err.name === 'NotAllowedError') {
          done = true; speaking = false; hideCaption(); speechQ.unshift(m); soundBtn.classList.add('show');
        } else finish();
      });
      setTimeout(finish, guard);
    } else if (m.browser_tts && VOICE_ON && 'speechSynthesis' in window) {
      var u = new SpeechSynthesisUtterance(m.text);
      u.lang = 'fa-IR';
      u.rate = 1.05;
      u.onend = finish; u.onerror = finish;
      try { window.speechSynthesis.speak(u); } catch (e) { finish(); }
      setTimeout(finish, Math.max(guard, 9000));
    } else {
      setTimeout(finish, Math.max(2500, (m.duration || 3) * 1000));
    }
  }

  function unlockSound() {
    tryResumeAudio();
    soundBtn.classList.remove('show');
    pumpSpeech();
  }
  soundBtn.addEventListener('click', unlockSound);
  document.addEventListener('pointerdown', function () {
    tryResumeAudio();
    soundBtn.classList.remove('show');
  }, { passive: true });
  function checkSound() {
    var a = audioCtx();
    if (a && a.state !== 'running' && (SFX_ON || VOICE_ON)) soundBtn.classList.add('show');
  }

  // ------------------------------------------------------------- header cards
  function setupHeader() {
    var items = Array.from(document.querySelectorAll('#bonus-flip .flip-item')), i = 0;
    setInterval(function () {
      var cur = items[i]; cur.classList.remove('show'); cur.classList.add('out');
      setTimeout(function () { cur.classList.remove('out'); }, 520);
      i = (i + 1) % items.length; items[i].classList.add('show');
    }, 3400);
    var lv = 2;
    setInterval(function () {
      var max = (state.config && state.config.max_level) || 5;
      lv = lv >= Math.min(max, 10) ? 2 : lv + 1;
      $('#lv-max').textContent = String(lv).replace(/\d/g, function (d) { return '۰۱۲۳۴۵۶۷۸۹'[+d]; });
      $('#lv-pts').textContent = String(lv).replace(/\d/g, function (d) { return '۰۱۲۳۴۵۶۷۸۹'[+d]; });
    }, 2600);
    setInterval(idleHint, 1000);
  }
  function applyConfig() {
    var c = state.config || {};
    if (c.title) $('#title-text').textContent = c.title;
    $('#bonus-ruby').textContent = 'هر ۱ 💎 = +' + (c.ruby_points || 2) + ' امتیاز';
    $('#bonus-mem').textContent = '+' + (c.member_points || 400) + ' امتیاز';
  }
  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme.accent) root.style.setProperty('--accent', theme.accent);
    if (theme.title_color) root.style.setProperty('--title-color', theme.title_color);
    if (theme.bg_top) root.style.setProperty('--bg-top', theme.bg_top);
    if (theme.bg_bottom) root.style.setProperty('--bg-bottom', theme.bg_bottom);
    if (theme.card_radius) root.style.setProperty('--card-radius', theme.card_radius);
    if (theme.score_size) root.style.setProperty('--score-size', theme.score_size);
    if (theme.ruby_color) root.style.setProperty('--ruby-color', theme.ruby_color);
    if (theme.bg_top || theme.bg_bottom) {
      var top = theme.bg_top || '#34196e';
      var bot = theme.bg_bottom || '#05040f';
      stage.style.background =
        'radial-gradient(rgba(255,255,255,.07) 1px, transparent 1.6px) 0 0 / 22px 22px, ' +
        'radial-gradient(120% 46% at 50% 0%, ' + top + ' 0%, rgba(52,25,110,0) 70%), ' +
        'linear-gradient(180deg, ' + top + ' 0%, ' + bot + ' 100%)';
    }
    if (theme.title_color) {
      var tt = document.querySelector('.title-text');
      if (tt) tt.style.color = theme.title_color;
    }
  }

  // ---------------------------------------------------------- message handlers
  function onInit(msg) {
    state.config = msg.config || {};
    applyConfig();
    applyTheme((msg.config && msg.config.theme) || {});
    state.streamers.clear();
    (msg.streamers || []).forEach(function (s) {
      state.streamers.set(s.username, s);
      if (!state.scores.has(s.username)) {
        state.scores.set(s.username, '0');
        state.since.set(s.username, 0);
      }
    });
    if (msg.booster) state.booster = msg.booster;
    buildGrid();
    applyBoard(msg.board || [], false, msg.booster);
    renderChatters(msg.chatters || []);
    renderBooster();
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      state.tiles.forEach(function (t) { t.el.classList.remove('no-anim'); });
    }); });
  }
  function onStreamers(list) {
    var newSet = new Set();
    (list || []).forEach(function (s) {
      state.streamers.set(s.username, s);
      newSet.add(s.username);
      if (!state.scores.has(s.username)) {
        state.scores.set(s.username, '0');
        state.since.set(s.username, 0);
        tile(s.username);
      } else {
        refreshTilePic(s.username);
      }
    });
    Array.from(state.streamers.keys()).forEach(function (u) {
      if (!newSet.has(u)) {
        state.streamers.delete(u);
        state.scores.delete(u);
        state.since.delete(u);
        var t = state.tiles.get(u);
        if (t) { t.el.remove(); state.tiles.delete(u); }
      }
    });
    buildGrid();
    relayout();
  }
  function onBooster(b) {
    if (!b) return;
    var wasActive = state.booster && state.booster.active;
    state.booster = b;
    renderBooster();
    if (!wasActive && state.booster && state.booster.active) {
      activateBoosterEffect();
      sfx.booster();
    }
  }
  function applyBoard(list, animate, booster) {
    list.forEach(function (row) {
      var code = row[0];
      if (!state.streamers.has(code)) return;
      var changed = String(state.scores.get(code)) !== String(row[1]);
      state.scores.set(code, row[1]); state.since.set(code, row[2]);
      var t = tile(code);
      if (changed || !animate) setScore(t, row[1], animate);
    });
    if (booster) onBooster(booster);
    relayout();
  }
  function onVote(v) {
    var t = tile(v.code);
    state.scores.set(v.code, v.total); state.since.set(v.code, v.seq);
    relayout();
    setScore(t, v.total, true);
    retrigger(t.el, 'bump', 800); retrigger(t.el, 'hot', 1500);
    var ptsBig = toBig(v.points);
    var cls = v.kind === 'chat' ? (ptsBig >= 5n ? 'big' : '') : (v.kind === 'ruby' ? 'ruby' : v.kind);
    if (v.multiplier && v.multiplier > 1) cls += ' boosted';
    floatText(t, bigFmtSigned(ptsBig, 2), cls);
    if (v.prev_rank > v.rank) trend(t, 'up');
    state.lastVote = Date.now();
    if (v.booster) onBooster(v.booster);
    showTicker(v);
    var ptsNum = Number(ptsBig > 1000000n ? 10n : ptsBig) || 0;
    if (v.kind === 'chat') {
      sfx.tick(ptsNum);
    } else {
      enqueueSplash(v);
    }
    if (v.level_up) sfx.level();
    pauseChattersScroll(1500);
  }
  function onEvent(e) {
    var t = e.code ? tile(e.code) : null;
    switch (e.kind) {
      case 'leader':
        banner('leader', 'صدرنشین جدید', [e.code], cname(e.code) + ' صدرنشین شد!');
        sfx.lead(); confetti(70, 0.8); jumpTop(); if (t) retrigger(t.el, 'bump', 800);
        break;
      case 'overtake':
        banner('overtake', 'سبقت', [e.code, e.other], cname(e.code) + ' از ' + cname(e.other) + ' جلو زد');
        sfx.whoosh();
        break;
      case 'fire':
        banner('fire', 'آتیش گرفت', [e.code], cname(e.code) + ' · ' + bigFmt(toBig(e.value), 1) + ' امتیاز در ۱۰ ثانیه');
        sfx.fire();
        if (t) { t.el.classList.add('fire'); setTimeout(function () { t.el.classList.remove('fire'); }, 9000); }
        break;
      case 'milestone':
        banner('milestone', 'دستاورد', [e.code], cname(e.code) + ' به ' + bigFmt(toBig(e.value), 0) + ' امتیاز رسید');
        confetti(45, 0.7); sfx.level();
        break;
      case 'chatter':
        banner('chatter', 'نفر اول چت', [], e.text); sfx.level();
        break;
      case 'achievement':
        banner('milestone', 'دستاورد', [e.code], e.text || 'دستاورد جدید باز شد!');
        sfx.level(); confetti(60, 0.8);
        break;
      case 'booster':
        banner('booster', 'بوستر', [], e.text || 'بوستر فعال شد!');
        activateBoosterEffect();
        sfx.booster();
        break;
      case 'notice':
        banner('notice', '', [], e.text);
        break;
    }
  }
  function onReset() {
    state.scores.forEach(function (_v, k) { state.scores.set(k, '0'); state.since.set(k, 0); });
    state.tiles.forEach(function (t) { setScore(t, '0', false); });
    state.booster = { progress: '0', target: state.booster.target, active: false, left: 0,
                      duration: state.booster.duration, multiplier: state.booster.multiplier };
    renderBooster();
    relayout(); renderChatters([]);
  }
  function handle(msg) {
    switch (msg.type) {
      case 'init': onInit(msg); break;
      case 'board': applyBoard(msg.board || [], true, msg.booster); break;
      case 'vote': onVote(msg); break;
      case 'chatters': renderChatters(msg.list || []); break;
      case 'event': onEvent(msg); break;
      case 'speak': enqueueSpeech(msg); break;
      case 'reset': onReset(); break;
      case 'theme': applyTheme(msg.theme || {}); break;
      case 'streamers': onStreamers(msg.streamers || []); break;
      case 'booster': onBooster(msg.booster); break;
    }
  }

  function connect() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var ws = new WebSocket(proto + '//' + location.host + '/ws');
    ws.onmessage = function (ev) { try { handle(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
    ws.onclose = function () { setTimeout(connect, 2000); };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  }

  window.BoostApp = { handle: handle, state: state, fmt: bigFmt, tryResumeAudio: tryResumeAudio };

  function boot() {
    fit(); window.addEventListener('resize', fit);
    makeStars(); setupHeader();

    audioCtx();
    tryResumeAudio();
    setTimeout(tryResumeAudio, 500);
    setTimeout(tryResumeAudio, 1500);
    setTimeout(tryResumeAudio, 3000);

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/sw.js').catch(function (e) {
        console.warn('SW registration failed:', e);
      });
    }

    requestAnimationFrame(chattersScrollLoop);

    if (DEMO && typeof window.startDemo === 'function') window.startDemo(window.BoostApp);
    else connect();
    setTimeout(checkSound, 900);

    setInterval(tryResumeAudio, 2000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();