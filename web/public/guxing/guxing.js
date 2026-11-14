/*!
 * Guxing 古星 — pure-JS port of python/guxing/core.py (keep the two in sync; tests/parity.mjs checks it).
 * Works in browsers, app webviews, Node. No dependencies.
 *
 *   <script src="guxing-data.js"></script>          // sets window.GUXING_DATA (256 KB, CC BY-SA)
 *   <script src="guxing.js"></script>
 *   const gx = new Guxing();                          // uses GUXING_DATA
 *   gx.locate(269.452, 4.693, {date: '2026-11-14', teff: 3100, mag: 9.5}).log_zh
 *
 * Node:  const Guxing = require('./guxing.js'); const gx = new Guxing(require('./guxing-data.js'));
 * Positions are J2000/ICRS degrees. Wording is playful and culturally inspired (Qing-era asterisms).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Guxing = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  var DU_PER_DEG = 365.25 / 360.0, FAN_DEG = 1.0, YAN_DEG = 0.02, NEAR_DEG = 3.0, SHOU_DAYS = 20.0;
  var SYMBOLS = [['东方苍龙', 'Azure Dragon of the East'], ['北方玄武', 'Black Tortoise of the North'],
    ['西方白虎', 'White Tiger of the West'], ['南方朱雀', 'Vermilion Bird of the South']];
  var STEMS = '甲乙丙丁戊己庚辛壬癸', BRANCHES = '子丑寅卯辰巳午未申酉戌亥', ZH = '零一二三四五六七八九十';
  var DIR_EN = { '东': 'E', '西': 'W', '南': 'S', '北': 'N' };
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;

  function mod(a, n) { return ((a % n) + n) % n; }
  function r_(x) { return Math.floor(x + 0.5); }                       // same as Python _r
  function fx(x, n) { var f = Math.pow(10, n); return (Math.floor(x * f + 0.5) / f).toFixed(n); }  // = Python _fx
  function rnd(x, n) { var f = Math.pow(10, n); return Math.round(x * f) / f; }
  function zhNum(n) {
    n = Math.trunc(n);
    if (n <= 10) return ZH[n];
    if (n < 20) return '十' + ZH[n - 10];
    if (n < 100) return ZH[Math.floor(n / 10)] + '十' + (n % 10 ? ZH[n % 10] : '');
    return String(n);
  }
  function ganzhi(y) { return STEMS[mod(y - 4, 10)] + BRANCHES[mod(y - 4, 12)]; }
  var DRE = /^(-?\d{1,5})(?:-(\d{1,2})(?:-(\d{1,2}))?)?/;
  function toYear(d) {
    if (d === null || d === undefined) return null;
    if (typeof d === 'number') return d;
    if (d instanceof Date) {
      var y0 = Date.UTC(d.getUTCFullYear(), 0, 1), doy = Math.floor((d - y0) / 864e5) + 1;
      return d.getUTCFullYear() + (doy - 0.5) / 365.25;
    }
    var s = String(d).trim();
    if (/^MJD/i.test(s)) return 2000.0 + (parseFloat(s.slice(4)) - 51544.5) / 365.25;
    var m = DRE.exec(s); if (!m) throw new Error('bad date: ' + d);
    var y = +m[1], mo = m[2] ? +m[2] : 7, da = m[3] ? +m[3] : 1;
    return y + ((mo - 1) * 30.44 + da - 0.5) / 365.25;
  }
  function dateParts(d) {
    if (d === null || d === undefined) return null;
    if (typeof d === 'number') return [Math.floor(d), null, null];
    if (d instanceof Date) return [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()];
    var s = String(d);
    if (/^MJD/i.test(s)) return [Math.floor(toYear(s)), null, null];
    var m = DRE.exec(s);
    return [+m[1], m[2] ? +m[2] : null, m[3] ? +m[3] : null];
  }
  function dateZh(d) {
    var p = dateParts(d); if (!p) return '';
    var y = p[0], s = (y <= 0 ? '公元前' + (1 - y) + '年' : '公元' + y + '年') + '（' + ganzhi(y) + '）';
    if (p[1]) s += zhNum(p[1]) + '月';
    if (p[2]) s += zhNum(p[2]) + '日';
    return s;
  }
  function dateEn(d) {
    var p = dateParts(d); if (!p) return '';
    var y = p[0], ys = y <= 0 ? (1 - y) + ' BC' : 'AD ' + y;
    if (!p[1]) return ys;
    return ys + ', month ' + p[1] + (p[2] ? ', day ' + p[2] : '');
  }
  function sepDeg(ra1, d1, ra2, d2) {
    var a = d1 * D2R, b = d2 * D2R;
    var c = Math.sin(a) * Math.sin(b) + Math.cos(a) * Math.cos(b) * Math.cos((ra1 - ra2) * D2R);
    return Math.acos(Math.max(-1, Math.min(1, c))) * R2D;
  }
  function offsetDeg(ra0, d0, ra, d) {
    var a0 = ra0 * D2R, b0 = d0 * D2R, a = ra * D2R, b = d * D2R;
    var cosc = Math.sin(b0) * Math.sin(b) + Math.cos(b0) * Math.cos(b) * Math.cos(a - a0);
    var x = Math.cos(b) * Math.sin(a - a0) / cosc;
    var y = (Math.cos(b0) * Math.sin(b) - Math.sin(b0) * Math.cos(b) * Math.cos(a - a0)) / cosc;
    return [x * R2D, y * R2D];
  }
  function directionZh(e, n) {
    var ax = Math.abs(e), ay = Math.abs(n);
    if (ax < 1e-9 && ay < 1e-9) return '';
    var ew = e > 0 ? '东' : '西', ns = n > 0 ? '北' : '南';
    if (ax < 0.4 * ay) return ns;
    if (ay < 0.4 * ax) return ew;
    return ew + ns;
  }
  function directionEn(dz) {   // N/S first, like "NW"
    return dz.split('').sort(function (a, b) { return ('东西'.indexOf(a) >= 0) - ('东西'.indexOf(b) >= 0); })
      .map(function (c) { return DIR_EN[c]; }).join('');
  }
  function propagate(ra, dec, pmra, pmdec, dt) {
    if (!dt || (!pmra && !pmdec)) return [ra, dec];
    var dec2 = dec + pmdec * dt / 3.6e6;
    var ra2 = ra + pmra * dt / 3.6e6 / Math.max(Math.cos(dec * D2R), 1e-6);
    return [mod(ra2, 360), Math.max(-90, Math.min(90, dec2))];
  }
  function precess(ra, dec, year) {
    if (year === null || year === undefined || Math.abs(year - 2000) < 1e-6) return [ra, dec];
    var T = (year - 2000) / 100;
    var zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T * T * T) / 3600 * D2R;
    var z = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T * T * T) / 3600 * D2R;
    var th = (2004.3109 * T - 0.42665 * T * T - 0.041833 * T * T * T) / 3600 * D2R;
    var a = ra * D2R, d = dec * D2R;
    var A = Math.cos(d) * Math.sin(a + zeta);
    var B = Math.cos(th) * Math.cos(d) * Math.cos(a + zeta) - Math.sin(th) * Math.sin(d);
    var C = Math.sin(th) * Math.cos(d) * Math.cos(a + zeta) + Math.cos(th) * Math.sin(d);
    return [mod((Math.atan2(A, B) + z) * R2D, 360), Math.asin(Math.max(-1, Math.min(1, C))) * R2D];
  }
  function colourZh(teff) {
    if (!teff) return null;
    if (teff < 3900) return ['赤', 'red'];
    if (teff < 5300) return ['黄', 'yellow'];
    if (teff < 7500) return ['白', 'white'];
    return ['青', 'blue-white'];
  }
  function visibility(mag) {
    if (mag === null || mag === undefined) return null;
    return mag <= 6.0 ? ['目力可见', 'naked-eye'] : ['非目力所及', 'beyond the naked eye'];
  }
  function relation(d) {
    if (d <= YAN_DEG) return { zh: '掩', en: 'occulting', pinyin: 'yǎn' };
    if (d <= FAN_DEG) return { zh: '犯', en: 'trespassing on', pinyin: 'fàn' };
    if (d <= NEAR_DEG) return { zh: '近', en: 'near', pinyin: 'jìn' };
    return { zh: '在', en: 'in the region of', pinyin: 'zài' };
  }
  function fmtDu(x) { return x >= 1 ? zhNum(r_(x)) : x >= 0.1 ? fx(x, 1) : fx(x, 2); }
  function spanZh(y) {
    if (y >= 2) return y < 100 ? zhNum(r_(y)) + '年' : r_(y) + '年';
    var d = y * 365.25; return d < 100 ? zhNum(r_(d)) + '日' : r_(d) + '日';
  }
  function spanEn(y) { return y >= 2 ? (y >= 10 ? r_(y) + ' years' : fx(y, 1) + ' years') : fx((y * 365.25), 1) + ' days'; }

  function Guxing(data) {
    data = data || (typeof GUXING_DATA !== 'undefined' ? GUXING_DATA : null);
    if (!data) throw new Error('Guxing: no data. Load guxing-data.js first or pass the JSON.');
    this.data = data; this.stars = {}; this.names = {}; this.mansions = data.mansions; this.asterisms = [];
    this.hips = Object.keys(data.stars).map(Number).sort(function (a, b) { return a - b; });
    var self = this;
    this.hips.forEach(function (h) { self.stars[h] = data.stars[h]; });
    Object.keys(data.names).forEach(function (h) { self.names[+h] = data.names[h]; });
    data.asterisms.forEach(function (a) {
      var set = {}; a[3].forEach(function (l) { l.forEach(function (h) { if (self.stars[h]) set[h] = 1; }); });
      var hs = Object.keys(set).map(Number).sort(function (x, y) { return x - y; });
      if (hs.length) self.asterisms.push({ zh: a[0], en: a[1], py: a[2], lines: a[3], hs: hs });
    });
    this.guestStarsData = null;
  }
  var P = Guxing.prototype;
  P._detPositions = function (epoch) {
    var self = this;
    return this.mansions.map(function (m, i) {
      var s = self.stars[m[3]], p = [s[0], s[1]];
      if (epoch !== null && epoch !== undefined) { p = propagate(p[0], p[1], m[4], m[5], epoch - 2000); p = precess(p[0], p[1], epoch); }
      return [i, p[0], p[1]];
    });
  };
  P.mansion = function (ra, dec, epoch) {
    var hasE = epoch !== null && epoch !== undefined;
    if (hasE) { var q = precess(ra, dec, epoch); ra = q[0]; dec = q[1]; }
    var best = null;
    this._detPositions(hasE ? epoch : null).forEach(function (d) {
      var dra = mod(ra - d[1], 360); if (best === null || dra < best[1]) best = [d[0], dra];
    });
    var m = this.mansions[best[0]], sym = SYMBOLS[Math.floor(best[0] / 7)];
    return { index: best[0], zh: m[0] + '宿', zh_short: m[0], pinyin: m[1], en: m[2] + ' Lodge', determinative_hip: m[3],
      du: rnd(best[1] * DU_PER_DEG, 2), deg: rnd(best[1], 3), symbol_zh: sym[0], symbol_en: sym[1], epoch: hasE ? epoch : 2000.0 };
  };
  P.mansionWidths = function (epoch) {
    var det = this._detPositions(epoch).sort(function (a, b) { return a[1] - b[1]; }), out = {}, self = this;
    det.forEach(function (d, k) { out[self.mansions[d[0]][0]] = rnd(mod(det[(k + 1) % det.length][1] - d[1], 360), 3); });
    return out;
  };
  P.starName = function (h) { var n = this.names[h]; return n ? [n[0], n[1]] : [null, 'HIP ' + h]; };
  P.nearest = function (ra, dec, k, maxDeg) {
    k = k || 3; var res = [], self = this;
    this.asterisms.forEach(function (a) {
      var best = null;
      a.hs.forEach(function (h) {
        var s = self.stars[h]; if (Math.abs(s[1] - dec) > 30) return;
        var d = sepDeg(ra, dec, s[0], s[1]); if (best === null || d < best[0]) best = [d, h];
      });
      if (best === null || (maxDeg !== undefined && maxDeg !== null && best[0] > maxDeg)) return;
      res.push([best[0], a, best[1]]);
    });
    res.sort(function (x, y) { return x[0] - y[0] || (x[1].zh < y[1].zh ? -1 : x[1].zh > y[1].zh ? 1 : 0); });
    return res.slice(0, k).map(function (r) {
      var s = self.stars[r[2]], o = offsetDeg(s[0], s[1], ra, dec), dz = directionZh(o[0], o[1]), nm = self.starName(r[2]);
      return { asterism_zh: r[1].zh, asterism_en: r[1].en, asterism_pinyin: r[1].py, star_hip: r[2], star_zh: nm[0] || r[1].zh,
        star_en: nm[1], star_mag: s[2], sep_deg: rnd(r[0], 3), sep_du: rnd(r[0] * DU_PER_DEG, 2), direction_zh: dz,
        direction_en: directionEn(dz), relation: relation(r[0]) };
    });
  };
  P.distanceToAsterism = function (ra, dec, nameZh) {
    var self = this;
    for (var i = 0; i < this.asterisms.length; i++) {
      var a = this.asterisms[i]; if (a.zh !== nameZh) continue;
      var best = null;
      a.hs.forEach(function (h) { var s = self.stars[h], d = sepDeg(ra, dec, s[0], s[1]); if (!best || d < best[0]) best = [d, h]; });
      var s = this.stars[best[1]], o = offsetDeg(s[0], s[1], ra, dec);
      return { sep_deg: rnd(best[0], 3), star_hip: best[1], direction_zh: directionZh(o[0], o[1]) };
    }
    return null;
  };
  P.relation = relation;
  P.segments = function (ra, dec, radius) {
    radius = radius === undefined ? 10 : radius; var out = [], self = this;
    this.asterisms.forEach(function (a) {
      var segs = [];
      a.lines.forEach(function (l) {
        for (var i = 0; i + 1 < l.length; i++) {
          var sa = self.stars[l[i]], sb = self.stars[l[i + 1]]; if (!sa || !sb) continue;
          if (Math.min(sepDeg(ra, dec, sa[0], sa[1]), sepDeg(ra, dec, sb[0], sb[1])) <= radius) segs.push([[sa[0], sa[1]], [sb[0], sb[1]]]);
        }
      });
      if (segs.length) out.push({ zh: a.zh, en: a.en, segments: segs });
    });
    return out;
  };
  P.locate = function (ra, dec, o) {
    o = o || {}; ra = mod(+ra, 360); dec = +dec;
    var kind = o.kind || '客星', kindEn = o.kind_en || 'guest star', date = o.date === undefined ? null : o.date;
    var m = this.mansion(ra, dec, o.epoch), near = this.nearest(ra, dec, 3), col = colourZh(o.teff), vis = visibility(o.mag);
    var n0 = near[0] || null;
    var zh = (date !== null ? dateZh(date) + '，' : '') + kind + '见' + m.zh + zhNum(r_(m.du)) + '度';
    var en = (date !== null ? dateEn(date) + ': ' : '') + 'A ' + kindEn + ' appears in the ' + m.en + ' (' + m.pinyin + '), ' + r_(m.du) + ' du';
    if (n0) {
      var rel = n0.relation;
      if (rel.zh === '在') { zh += '，在' + n0.asterism_zh + '之域'; en += ', in the region of ' + n0.asterism_en + ' (' + n0.asterism_zh + ')'; }
      else {
        zh += '，' + rel.zh + n0.star_zh + '（' + n0.direction_zh + fmtDu(n0.sep_du) + '度）';
        en += ', ' + rel.en + ' ' + n0.star_en + ' (' + n0.star_zh + '), ' + fx(n0.sep_deg, 1) + '° ' + n0.direction_en;
      }
    }
    if (col) { zh += '，色' + col[0]; en += ', ' + col[1] + ' in colour'; }
    if (vis) { zh += '，' + vis[0]; en += ', ' + vis[1]; }
    return { ra: ra, dec: dec, mansion: m, nearest: near, colour: col && { zh: col[0], en: col[1] },
      visibility: vis && { zh: vis[0], en: vis[1] }, log_zh: zh + '。', log_en: en + '.',
      note: 'Playful, culturally-inspired description; asterisms follow Qing-era identifications (Stellarium, CC BY-SA).' };
  };
  /* track({points:[[date, ra, dec],...]}) or track({ra, dec, pmra, pmdec, start, end, steps}) — pm in mas/yr */
  P.track = function (o) {
    o = o || {}; var self = this, points = o.points, shouDays = o.shou_days || SHOU_DAYS;
    if (!points) {
      var y0 = toYear(o.start), y1 = toYear(o.end); if (y0 === null || y1 === null) throw new Error('need start and end');
      var steps = Math.max(2, Math.trunc(o.steps || 60)); points = [];
      for (var k = 0; k < steps; k++) {
        var y = y0 + (y1 - y0) * k / (steps - 1), p = propagate(+o.ra, +o.dec, +(o.pmra || 0), +(o.pmdec || 0), y - y0);
        points.push([y, p[0], p[1]]);
      }
    }
    var pts = points.map(function (p) { return [toYear(p[0]), mod(+p[1], 360), +p[2], p[0]]; })
      .sort(function (a, b) { return a[0] - b[0]; });
    if (pts.length < 2) throw new Error('need at least two points');
    var mid = pts[Math.floor(pts.length / 2)], cra = mid[1], cdec = mid[2];
    var span = Math.max.apply(null, pts.map(function (p) { return sepDeg(cra, cdec, p[1], p[2]); }));
    var cand = this.hips.filter(function (h) {
      var s = self.stars[h]; return self.names[h] && Math.abs(s[1] - cdec) <= span + FAN_DEG + 0.5 && sepDeg(cra, cdec, s[0], s[1]) <= span + FAN_DEG + 0.5;
    });
    var lodges = [], prevM = null, close = {}, order = [];
    pts.forEach(function (p) {
      var m = self.mansion(p[1], p[2]);
      if (prevM === null || m.zh !== prevM) {
        lodges.push({ year: rnd(p[0], 4), date: typeof p[3] === 'string' ? p[3] : null, mansion: m.zh, mansion_en: m.en, du: m.du });
        prevM = m.zh;
      }
      cand.forEach(function (h) {
        var s = self.stars[h], sd = sepDeg(p[1], p[2], s[0], s[1]);
        if (sd <= FAN_DEG) { if (!close[h]) { close[h] = []; order.push(h); } close[h].push([p[0], sd]); }
      });
    });
    var events = order.map(function (h) {
      var lst = close[h], mn = lst[0];
      lst.forEach(function (a) { if (a[1] < mn[1]) mn = a; });
      var dur = (lst[lst.length - 1][0] - lst[0][0]) * 365.25, nm = self.starName(h), rel = relation(mn[1]);
      if (dur >= shouDays && lst.length >= 2) rel = { zh: '守', en: 'guarding', pinyin: 'shǒu' };
      return { star_hip: h, star_zh: nm[0], star_en: nm[1], relation: rel, closest_year: rnd(mn[0], 4),
        closest_deg: rnd(mn[1], 3), days_within_1deg: rnd(dur, 1) };
    });
    events = events.map(function (e, i) { return [e, i]; })
      .sort(function (a, b) { return a[0].closest_year - b[0].closest_year || a[1] - b[1]; }).map(function (x) { return x[0]; });
    var legsRaw = [];
    for (var i = 0; i + 1 < pts.length; i++) {
      var a = pts[i], b = pts[i + 1], off = offsetDeg(a[1], a[2], b[1], b[2]), dt = Math.max(b[0] - a[0], 1e-9);
      legsRaw.push([a[0], off[0] / dt, off[1] / dt, Math.hypot(off[0], off[1]) / dt]);
    }
    var speeds = legsRaw.map(function (l) { return l[3]; }).sort(function (x, y) { return x - y; });
    var med = speeds[Math.floor(speeds.length / 2)] || 1e-12;
    var MOT_EN = { '顺行': 'prograde (eastward)', '逆行': 'retrograde (westward)', '留': 'stationary', '北行': 'northward', '南行': 'southward' };
    var legs = [], last = null;
    legsRaw.forEach(function (l) {
      var st = l[3] < 0.15 * med ? '留' : Math.abs(l[1]) >= Math.abs(l[2]) ? (l[1] > 0 ? '顺行' : '逆行') : (l[2] > 0 ? '北行' : '南行');
      if (st !== last) legs.push({ from_year: rnd(l[0], 4), motion_zh: st, motion_en: MOT_EN[st] });
      last = st;
    });
    var first = pts[0], lastp = pts[pts.length - 1], total = sepDeg(first[1], first[2], lastp[1], lastp[2]);
    var loc0 = this.locate(first[1], first[2], { date: first[3], teff: o.teff, mag: o.mag, kind: o.kind, kind_en: o.kind_en });
    var zh = loc0.log_zh.slice(0, -1), en = loc0.log_en.slice(0, -1);
    zh += '；' + legs.map(function (l) { return l.motion_zh; }).join('、');
    en += '; it moves ' + legs.map(function (l) { return l.motion_en; }).join(', then ');
    events.slice(0, 4).forEach(function (ev) { zh += '，' + ev.relation.zh + (ev.star_zh || ev.star_en); en += ', ' + ev.relation.en + ' ' + ev.star_en; });
    if (lodges.length > 1) {
      zh += '，自' + lodges[0].mansion + '入' + lodges.slice(1).map(function (l) { return l.mansion; }).join('、');
      en += ', passing from the ' + lodges[0].mansion_en + ' into ' + lodges.slice(1).map(function (l) { return l.mansion_en; }).join(', ');
    }
    var spanY = lastp[0] - first[0];
    zh += '；' + spanZh(spanY) + '间行' + fmtDu(total * DU_PER_DEG) + '度。';
    en += '; it travels ' + fx(total, 2) + '° in ' + spanEn(spanY) + '.';
    return { start: loc0, lodges: lodges, events: events, motion: legs, total_deg: rnd(total, 6), span_years: rnd(spanY, 6),
      log_zh: zh, log_en: en, points: pts.map(function (p) { return { year: rnd(p[0], 6), ra: rnd(p[1], 6), dec: rnd(p[2], 6) }; }) };
  };
  /* backtest(guestStarsJson) — pass data/guest_stars.json */
  P.backtest = function (gs) {
    var self = this; gs = gs || this.guestStarsData;
    return gs.items.map(function (g) {
      var loc = self.locate(g.ra, g.dec, { date: g.year }), mThen = self.mansion(g.ra, g.dec, g.year);
      var row = { id: g.id, year: g.year, remnant: g.remnant, record_zh: g.record_zh, record_en: g.record_en, source: g.source,
        source_checked: g.source_checked, mansion_j2000: loc.mansion.zh, mansion_then: mThen.zh, du_then: mThen.du,
        nearest: loc.nearest.map(function (n) { return n.asterism_zh; }), log_zh: loc.log_zh, log_en: loc.log_en, checks: [] };
      if (g.expect_mansion) row.checks.push({ what: 'lodge', expected: g.expect_mansion + '宿', got: mThen.zh, ok: mThen.zh_short === g.expect_mansion });
      if (g.expect_asterism) {
        var d = self.distanceToAsterism(g.ra, g.dec, g.expect_asterism);
        row.checks.push({ what: 'asterism', expected: g.expect_asterism, got: d ? fx(d.sep_deg, 1) + '° away' : 'n/a', ok: !!d && d.sep_deg <= NEAR_DEG });
        if (g.expect_direction && d) row.checks.push({ what: 'direction', expected: g.expect_direction, got: d.direction_zh,
          ok: g.expect_direction.split('').every(function (c) { return d.direction_zh.indexOf(c) >= 0; }) });
      }
      return row;
    });
  };
  Guxing.util = { zhNum: zhNum, ganzhi: ganzhi, toYear: toYear, dateZh: dateZh, dateEn: dateEn, sepDeg: sepDeg, offsetDeg: offsetDeg,
    directionZh: directionZh, propagate: propagate, precess: precess, colourZh: colourZh, relation: relation };
  Guxing.constants = { DU_PER_DEG: DU_PER_DEG, FAN_DEG: FAN_DEG, YAN_DEG: YAN_DEG, NEAR_DEG: NEAR_DEG, SHOU_DAYS: SHOU_DAYS };
  Guxing.version = '1.0.0';
  return Guxing;
});
