/* ==========================================================================
   Demo mode - a tiny in-browser "fake server".
   Open  http://localhost:8080/?demo=1  (or index.html?demo=1) to preview the
   overlay without Python, a YouTube stream or an internet voice.
   It produces the very same messages the Python server sends over the
   WebSocket, so it is also a handy reference for the protocol.
   ========================================================================== */
(function () {
  'use strict';

  // starting scores copied from the reference screenshot
  var SEED = {
    IR: 31900, ID: 14900, IN: 14880, KH: 13700, AU: 5417, MY: 5348, BD: 4869, TH: 3918, AZ: 3544,
    JP: 3460, PK: 3421, MK: 2116, MM: 1925, DJ: 1902, BR: 1640, FI: 1517, PH: 1458, MX: 1264,
    CD: 1200, MZ: 1200, VU: 1200, GB: 1171, US: 1165, BG: 1160, PL: 1086, VN: 1085, CA: 1078,
    ES: 1015, AT: 1012, IQ: 886, DZ: 884, HN: 873, OM: 851, KZ: 851, NZ: 826, PT: 806, NL: 802,
    GT: 801, BO: 800, NI: 800, BZ: 800, BJ: 800, KM: 800, LU: 800, TR: 784, CL: 692, NP: 637,
    JO: 537, KE: 485, TD: 485, BH: 483, ZM: 451, UY: 449, VE: 448, UZ: 413, MV: 406, MN: 405,
    VA: 404, FR: 403, IE: 403, PS: 402, GE: 401
  };
  var THRESHOLDS = [0, 5, 15, 35, 70, 120, 200, 320, 500, 800];
  var NAMES = ['Ali_Gamer', '\u0639\u0644\u06CC \u0631\u0636\u0627\u06CC\u06CC', 'Sara', '\u0633\u0627\u0631\u0629', 'Mohammad', 'Carlos', 'Putri', 'Aditya', 'Emma', 'Kenji',
    'Fatima', 'Dmitri', 'Zeynep', 'Hassan', 'Lucas', 'Nguyen', 'AlbCro22', 'User R', 'Maria', 'Omar',
    '\u0631\u0636\u0627', 'Parisa', 'Tariq', '\u0644\u064A\u0644\u0649', 'John', 'Priya', 'Sven', 'Chloe', 'Ahmed', 'Yuki', '\u0646\u06AF\u0627\u0631', 'Bilal'];
  var HEAVY = ['IR', 'ID', 'IN', 'KH', 'AU', 'MY', 'BD', 'TH', 'AZ', 'JP', 'PK', 'BR', 'TR', 'US', 'GB', 'DE', 'MX', 'EG', 'SA', 'IQ'];
  var ALIASES = {
    'usa': 'US', 'america': 'US', 'uk': 'GB', 'england': 'GB', 'britain': 'GB', 'uae': 'AE', 'emirates': 'AE',
    'korea': 'KR', 'holland': 'NL', 'persia': 'IR', 'turkiye': 'TR',
    '\u0627\u06CC\u0631\u0627\u0646': 'IR', '\u062A\u0631\u06A9\u06CC\u0647': 'TR', '\u0639\u0631\u0628\u0633\u062A\u0627\u0646': 'SA', '\u0645\u0635\u0631': 'EG',
    '\u0639\u0631\u0627\u0642': 'IQ', '\u0627\u0644\u0639\u0631\u0627\u0642': 'IQ', '\u0622\u0645\u0631\u06CC\u06A9\u0627': 'US', '\u0627\u0645\u0627\u0631\u0627\u062A': 'AE',
    '\u0627\u0641\u063A\u0627\u0646\u0633\u062A\u0627\u0646': 'AF', '\u067E\u0627\u06A9\u0633\u062A\u0627\u0646': 'PK', '\u0647\u0646\u062F': 'IN', '\u0627\u0644\u0633\u0639\u0648\u062F\u064A\u0629': 'SA'
  };
  var MILESTONES = [100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];

  function ordinal(n) {
    var o = { 1: 'first', 2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth', 6: 'sixth', 7: 'seventh', 8: 'eighth', 9: 'ninth', 10: 'tenth' };
    return o[n] || n + 'th';
  }
  function spoken(n) {
    function t(x) { var s = x.toFixed(1); return s.replace(/\.0$/, ''); }
    return n >= 1e6 ? t(n / 1e6) + ' million' : n >= 1000 ? t(n / 1000) + ' thousand' : String(n);
  }
  function rnd(a) { return a[(Math.random() * a.length) | 0]; }

  window.startDemo = function (app) {
    function begin(rows) {
      var list = rows.map(function (r) { return [r[0], r[2]]; });
      var codes = list.map(function (r) { return r[0]; });
      var nameOf = {}, a3 = {}, nameIndex = [];
      rows.forEach(function (r) { nameOf[r[0]] = r[2]; a3[r[2].toLowerCase()] = r[0]; nameIndex.push([r[2].toLowerCase(), r[0]]); });
      var byA3 = {}; rows.forEach(function (r) { byA3[r[1]] = r[0]; });

      var scores = {}, since = {}, seq = 0;
      codes.forEach(function (c) { scores[c] = SEED[c] || 0; });
      codes.slice().sort(function (a, b) { return (scores[b] - scores[a]) || (a < b ? -1 : 1); })
        .forEach(function (c, i) { since[c] = i; seq = i + 1; });
      function ranking() {
        return codes.slice().sort(function (a, b) { return (scores[b] - scores[a]) || (since[a] - since[b]); });
      }

      var users = {};
      function user(name, country) {
        if (!users[name]) {
          var chats = (Math.random() * 90) | 0;
          users[name] = { name: name, chats: chats, level: levelFor(chats), points: 0, country: country || rnd(HEAVY) };
        }
        return users[name];
      }
      function levelFor(c) { var l = 1; THRESHOLDS.forEach(function (n, i) { if (c >= n) l = i + 1; }); return l; }
      [['Hossein', 599, 9510, 'IR', 8], ['H', 558, 8810, 'IR', 8], ['PolarBear', 335, 5130, 'IN', 7],
       ['Sara', 210, 3400, 'ID', 6], ['\u0639\u0644\u06CC', 190, 2900, 'IR', 6]].forEach(function (u) {
        users[u[0]] = { name: u[0], chats: u[1], points: u[2], country: u[3], level: u[4] };
      });
      function chatters() {
        return Object.keys(users).map(function (k) { return users[k]; })
          .sort(function (a, b) { return b.points - a.points; }).slice(0, 5)
          .map(function (u) { return { id: u.name, name: u.name, avatar: '', chats: u.chats, points: u.points, country: u.country, level: u.level }; });
      }
      var chattersDirty = false;

      var lastSpeak = {}, GAP = { overtake: 7000, leader: 3000, fire: 9000, milestone: 5000, chatter: 15000 };
      function speak(text, kind) {
        var now = Date.now();
        if (now - (lastSpeak[kind] || 0) < (GAP[kind] || 0)) return;
        lastSpeak[kind] = now;
        app.handle({ type: 'speak', text: text, kind: kind, url: null, browser_tts: true, duration: Math.max(2, text.length / 13 + 0.8) });
      }

      var recent = {}, lastFire = {}, topUser = 'Hossein';
      function award(code, pts, u, kind, amount) {
        var prev = ranking(), prevRank = prev.indexOf(code) + 1, prevScore = scores[code];
        var levelUsed = u.level, levelUp = false;
        scores[code] += pts; seq++; since[code] = seq;
        var rank = ranking().indexOf(code) + 1;
        u.country = code; u.points += pts;
        if (kind === 'chat') { u.chats++; var nl = levelFor(u.chats); levelUp = nl > u.level; u.level = nl; }
        chattersDirty = true;
        app.handle({
          type: 'vote', kind: kind, code: code, name: nameOf[code], points: pts, total: scores[code], rank: rank,
          prev_rank: prevRank, seq: seq, amount: amount || '', level_up: levelUp,
          user: { id: u.name, name: u.name, avatar: '', level: levelUsed }
        });
        var cn = nameOf[code], who = /[A-Za-z]/.test(u.name) ? u.name.replace(/[_\d]+/g, ' ') : 'a viewer';
        if (kind === 'superchat') speak(who + ' sent a ' + amount + ' Super Chat! ' + cn + ' receives ' + spoken(pts) + ' points!', 'superchat');
        if (kind === 'member') speak(who + ' just became a member! ' + cn + ' receives ' + spoken(pts) + ' points!', 'member');
        if (rank === 1 && prevRank !== 1 && scores[prev[0]] > 0) {
          app.handle({ type: 'event', kind: 'leader', code: code, other: prev[0], text: cn + ' takes the lead!' });
          speak(cn + ' takes the lead! ' + nameOf[prev[0]] + ' drops to second place.', 'leader');
        } else if (rank < prevRank && rank <= 10) {
          var passed = prev[rank - 1];
          if (scores[passed] > 0) {
            app.handle({ type: 'event', kind: 'overtake', code: code, other: passed, rank: rank, text: cn + ' passes ' + nameOf[passed] });
            speak(cn + ' passes ' + nameOf[passed] + ' and moves into ' + ordinal(rank) + ' place!', 'overtake');
          }
        }
        var crossed = MILESTONES.filter(function (m) { return prevScore < m && m <= scores[code]; });
        if (crossed.length) {
          var m = crossed[crossed.length - 1];
          app.handle({ type: 'event', kind: 'milestone', code: code, value: m, text: cn + ' reached ' + m });
          speak(cn + ' has reached ' + spoken(m) + ' points!', 'milestone');
        }
        var now = Date.now(), dq = (recent[code] = (recent[code] || []).filter(function (x) { return now - x[0] < 10000; }));
        dq.push([now, pts]);
        var total = dq.reduce(function (s, x) { return s + x[1]; }, 0);
        if (kind === 'chat' && total >= 25 && now - (lastFire[code] || 0) > 60000) {
          lastFire[code] = now;
          app.handle({ type: 'event', kind: 'fire', code: code, value: total, text: cn + ' is on fire!' });
          speak(cn + ' is on fire! ' + total + ' points in just 10 seconds!', 'fire');
        }
        if (u.name !== topUser) {
          var cur = users[topUser];
          if (!cur || u.points > cur.points) {
            topUser = u.name;
            app.handle({ type: 'event', kind: 'chatter', text: u.name + ' is the new top chatter!' });
            speak((/[A-Za-z]/.test(u.name) ? u.name : 'A viewer') + ' is now the number one top chatter with ' + spoken(u.points) + ' points!', 'chatter');
          }
        }
      }

      // very small version of the Python country matcher (names, ISO codes, a few aliases)
      function stripAccents(s) {
        return s.replace(/[\u00C0-\u024F]/g, function (ch) { return ch.normalize('NFD').replace(/[\u0300-\u036f]/g, ''); });
      }
      function detect(raw) {
        var text = stripAccents(raw);
        var flag = text.match(/[\u{1F1E6}-\u{1F1FF}]{2}/u);
        if (flag) {
          var cc = String.fromCharCode(flag[0].codePointAt(0) - 0x1F1E6 + 65, flag[0].codePointAt(2) - 0x1F1E6 + 65);
          if (nameOf[cc]) return cc;
        }
        var low = text.toLowerCase();
        var tokens = text.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
        for (var i = 0; i < tokens.length; i++) {
          var t = tokens[i];
          if (ALIASES[t.toLowerCase()]) return ALIASES[t.toLowerCase()];
        }
        var best = null;
        nameIndex.forEach(function (n) {
          var re = new RegExp('(^|[^a-z])' + n[0].replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '($|[^a-z])');
          if (re.test(low) && (!best || n[0].length > best[0].length)) best = n;
        });
        if (best) return best[1];
        for (var j = 0; j < tokens.length; j++) {
          var k = tokens[j];
          if (k.length === 2 && k === k.toUpperCase() && nameOf[k]) return k;
          if (k.length === 3 && k === k.toUpperCase() && byA3[k]) return byA3[k];
          if (k.length === 2 && tokens.length === 1 && nameOf[k.toUpperCase()]) return k.toUpperCase();
        }
        return null;
      }

      // ------------------------------------------------------------ boot
      app.handle({
        type: 'init',
        config: { title: 'BOOST YOUR COUNTRY', superchat_per_usd: 100, member_points: 400, max_level: 10, flag_base: 'https://flagcdn.com/w160/' },
        countries: list,
        board: ranking().map(function (c) { return [c, scores[c], since[c]]; }),
        chatters: chatters()
      });
      setTimeout(function () { speak('Welcome to Boost Your Country! Type your country name in the chat to give it points!', 'welcome'); }, 1800);

      // ------------------------------------------------- random traffic
      function pickCountry() {
        return Math.random() < 0.5 ? rnd(HEAVY) : rnd(codes);
      }
      setInterval(function () {
        var n = Math.random() < 0.5 ? 1 : 2;
        for (var i = 0; i < n; i++) {
          var u = user(rnd(NAMES));
          var code = Math.random() < 0.8 ? u.country : pickCountry();
          award(code, u.level, u, 'chat');
        }
      }, 520);

      function burst(code, count) {
        var left = count;
        var iv = setInterval(function () {
          var u = user(rnd(NAMES)); u.level = Math.max(u.level, 3);
          award(code, u.level, u, 'chat');
          if (--left <= 0) clearInterval(iv);
        }, 170);
      }
      setInterval(function () {
        var order = ranking();
        burst(order[3 + ((Math.random() * 14) | 0)], 24);
      }, 22000);

      var superTimer = setInterval(function () {
        var usd = rnd([2, 5, 10, 20, 50]);
        award(rnd(HEAVY.concat(codes)), usd * 100, user(rnd(NAMES)), 'superchat', '$' + usd + '.00');
      }, 38000);
      setInterval(function () { award(rnd(HEAVY), 400, user(rnd(NAMES)), 'member', ''); }, 70000);
      setInterval(function () {
        if (!chattersDirty) return;
        chattersDirty = false;
        app.handle({ type: 'chatters', list: chatters() });
      }, 800);
      void superTimer;

      // ---------------------------- remote control (used by the web page preview)
      window.addEventListener('message', function (ev) {
        var d = ev.data;
        if (!d || d.__boost !== true) return;
        var code, u;
        if (d.action === 'chat') {
          u = user(d.user || 'You');
          code = detect(String(d.text || ''));
          if (code) { u.last = Date.now(); award(code, u.level, u, 'chat'); }
          else app.handle({ type: 'event', kind: 'notice', text: 'No country found in your message' });
          if (ev.source) ev.source.postMessage({ __boostReply: true, ok: !!code, country: code ? nameOf[code] : null }, '*');
        } else if (d.action === 'superchat') {
          u = user(d.user || 'You'); code = d.code && nameOf[d.code] ? d.code : (detect(String(d.text || '')) || rnd(HEAVY));
          var usd = Number(d.usd) || 10;
          award(code, usd * 100, u, 'superchat', '$' + usd.toFixed(2));
        } else if (d.action === 'member') {
          u = user(d.user || 'You'); code = d.code && nameOf[d.code] ? d.code : (detect(String(d.text || '')) || rnd(HEAVY));
          award(code, 400, u, 'member', '');
        } else if (d.action === 'burst') {
          code = d.code && nameOf[d.code] ? d.code : (detect(String(d.text || '')) || rnd(HEAVY));
          burst(code, 40);
        }
      });
    }

    if (Array.isArray(window.DEMO_COUNTRIES)) begin(window.DEMO_COUNTRIES);
    else fetch('/data/countries.json').then(function (r) { return r.json(); }).then(begin)
      .catch(function (e) { console.error('Demo needs data/countries.json', e); });
  };
})();
