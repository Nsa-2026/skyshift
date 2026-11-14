/*!
 * <guxing-card> — drop-in web component. Needs guxing-data.js + guxing.js loaded first (fully offline).
 *
 *   <guxing-card ra="269.452" dec="4.693" date="2026-11-14" teff="3100" mag="9.5"
 *                pmra="-1010" pmdec="10230" years="1000"
 *                source="NASA SPHEREx · proper motion measured from 358 frames"></guxing-card>
 *
 * Attributes
 *   ra, dec        J2000 degrees (required)
 *   date           '2026-11-14', a year number, or 'MJD:61000'
 *   teff, mag      optional: colour word (赤/黄/白/青) and naked-eye flag
 *   pmra, pmdec    optional proper motion in mas/yr (pmra includes cos dec) -> draws the path, uses track()
 *   years          length of the drawn path (default 1000)
 *   epoch          use the lodge boundaries of that year (e.g. 1054)
 *   kind, kind-en  default 客星 / guest star (e.g. 荧惑 / Mars)
 *   radius         chart radius in degrees (default 7)
 *   source         text naming the NASA measurement behind the numbers (shown on the card; please always set it)
 *   source-url     optional link for the source line
 *   lang           zh | en | both (default both)
 * Events: dispatches 'guxing:result' with the JSON result in event.detail.
 * Theming: set CSS custom properties on the element: --gx-bg, --gx-fg, --gx-muted, --gx-accent, --gx-line, --gx-star.
 */
(function () {
  if (typeof window === 'undefined' || !window.customElements || customElements.get('guxing-card')) return;
  var D2R = Math.PI / 180;
  var CSS = [
    ':host{display:block;font-family:"Noto Serif SC","Songti SC",serif;color:var(--gx-fg,#1f2328);',
    '--_bg:var(--gx-bg,#fbf8f1);--_muted:var(--gx-muted,#6b6f76);--_accent:var(--gx-accent,#b4322a);',
    '--_line:var(--gx-line,#8a7a5c);--_star:var(--gx-star,#2b2b2b);max-width:560px}',
    '@media (prefers-color-scheme:dark){:host{color:var(--gx-fg,#e8e4da);--_bg:var(--gx-bg,#14161b);',
    '--_muted:var(--gx-muted,#9aa0a8);--_accent:var(--gx-accent,#e0685e);--_line:var(--gx-line,#b9a77f);--_star:var(--gx-star,#f1ede4)}}',
    '.card{background:var(--_bg);border:1px solid color-mix(in srgb,var(--_line) 45%,transparent);border-radius:12px;padding:14px 16px;',
    'display:grid;grid-template-columns:minmax(0,1fr);gap:10px}',
    '.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}',
    '.xiu{font-size:28px;font-weight:700;color:var(--_accent);letter-spacing:.05em}',
    '.sym{font-size:13px;color:var(--_muted)}.du{font-size:15px}',
    '.zh{font-size:17px;line-height:1.7}.en{font-size:13px;line-height:1.5;color:var(--_muted);font-family:system-ui,sans-serif}',
    'svg{width:100%;height:auto;display:block;border-radius:8px;background:color-mix(in srgb,var(--_line) 8%,transparent)}',
    '.src{font:12px/1.4 system-ui,sans-serif;color:var(--_muted);border-top:1px dashed color-mix(in srgb,var(--_line) 50%,transparent);padding-top:8px}',
    '.src b{color:inherit;font-weight:600}.src a{color:inherit}',
    '.err{color:var(--_accent);font:13px system-ui}'
  ].join('');

  function GuxingCard() { return Reflect.construct(HTMLElement, [], GuxingCard); }
  GuxingCard.prototype = Object.create(HTMLElement.prototype);
  GuxingCard.prototype.constructor = GuxingCard;
  Object.setPrototypeOf(GuxingCard, HTMLElement);
  GuxingCard.observedAttributes = ['ra', 'dec', 'date', 'teff', 'mag', 'pmra', 'pmdec', 'years', 'epoch', 'kind', 'kind-en',
    'radius', 'source', 'source-url', 'lang'];
  GuxingCard.prototype.connectedCallback = function () {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    this.render();
  };
  GuxingCard.prototype.attributeChangedCallback = function () { if (this.shadowRoot) this.render(); };

  function num(el, k) { var v = el.getAttribute(k); return v === null || v === '' ? undefined : +v; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function englishOnly(s) { return s.replace(/\s*\([^()]*\p{Script=Han}[^()]*\)/gu, ''); }

  GuxingCard.prototype.render = function () {
    var root = this.shadowRoot;
    try {
      if (!window.Guxing) throw new Error('guxing.js not loaded');
      var gx = window.__guxing || (window.__guxing = new window.Guxing());
      var ra = num(this, 'ra'), dec = num(this, 'dec');
      if (ra === undefined || dec === undefined) { root.innerHTML = '<style>' + CSS + '</style>'; return; }
      var date = this.getAttribute('date'); if (date !== null && /^-?\d+(\.\d+)?$/.test(date)) date = +date;
      var opts = { date: date === null ? undefined : date, teff: num(this, 'teff'), mag: num(this, 'mag'), epoch: num(this, 'epoch'),
        kind: this.getAttribute('kind') || undefined, kind_en: this.getAttribute('kind-en') || undefined };
      var pmra = num(this, 'pmra'), pmdec = num(this, 'pmdec'), res, path = null;
      if (pmra !== undefined || pmdec !== undefined) {
        var y0 = Guxing.util.toYear(opts.date === undefined ? 2026 : opts.date), yrs = num(this, 'years') || 1000;
        res = gx.track({ ra: ra, dec: dec, pmra: pmra || 0, pmdec: pmdec || 0, start: y0, end: y0 + yrs, steps: 120,
          teff: opts.teff, mag: opts.mag, kind: opts.kind, kind_en: opts.kind_en });
        path = res.points; res.locate = res.start;
      } else {
        res = gx.locate(ra, dec, opts); res.locate = res;
      }
      var m = res.locate.mansion, lang = this.getAttribute('lang') || 'both';
      var english = lang === 'en';
      var src = this.getAttribute('source'), srcUrl = this.getAttribute('source-url');
      var html = '<style>' + CSS + '</style><div class="card" part="card">' +
        '<div class="top"><span class="xiu">' + esc(english ? m.en : m.zh) + '</span><span class="du">' + m.du.toFixed(1) + (english ? ' du' : ' 度') + '</span>' +
        '<span class="sym">' + esc(english ? m.symbol_en : m.symbol_zh) + (english ? '' : ' · ' + esc(m.en)) + '</span></div>' +
        chart(gx, ra, dec, num(this, 'radius') || 7, res.locate, path, english) +
        (lang !== 'en' ? '<div class="zh" part="zh">' + esc(res.log_zh) + '</div>' : '') +
        (lang !== 'zh' ? '<div class="en" part="en">' + esc(english ? englishOnly(res.log_en) : res.log_en) + '</div>' : '') +
        '<div class="src">' + (src ? '<b>' + (english ? 'Data:' : '数据 Data:') + '</b> ' + (srcUrl ? '<a href="' + esc(srcUrl) + '" target="_blank" rel="noopener">' + esc(src) + '</a>' : esc(src)) + '<br>' : '') +
        (english ? 'Asterisms: Stellarium "chinese" sky culture (CC BY-SA; Qing-era identifications). A cultural interpretation, not a detection result.' :
          '星官 Asterisms: Stellarium "chinese" sky culture (CC BY-SA, 清代认证). 趣味文化解读，非检测结果。') + '</div></div>';
      root.innerHTML = html;
      this.dispatchEvent(new CustomEvent('guxing:result', { detail: res, bubbles: true }));
    } catch (e) {
      root.innerHTML = '<style>' + CSS + '</style><div class="card"><div class="err">Guxing: ' + esc(e.message) + '</div></div>';
    }
  };

  // Gnomonic star chart centred on the target, north up, east LEFT (as on the sky).
  function chart(gx, ra0, dec0, R, loc, path, english) {
    var W = 360, H = 300, S = Math.min(W, H) / 2 / Math.tan(R * D2R);
    function P(ra, dec) {
      var o = Guxing.util.offsetDeg(ra0, dec0, ra, dec);
      var x = Math.tan(o[0] * D2R), y = Math.tan(o[1] * D2R);
      return [W / 2 - x * S, H / 2 - y * S];
    }
    function inside(p) { return p[0] > -20 && p[0] < W + 20 && p[1] > -20 && p[1] < H + 20; }
    var out = ['<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Chinese asterisms around the position">'];
    // lodge boundaries: hour circles through determinative stars
    gx.mansions.forEach(function (mm) {
      var s = gx.stars[mm[3]]; var pts = [];
      for (var d = dec0 - R * 1.6; d <= dec0 + R * 1.6; d += R / 20) {
        if (d < -89.9 || d > 89.9) continue;
        var p = P(s[0], d); if (Guxing.util.sepDeg(ra0, dec0, s[0], d) < 80) pts.push(p);
      }
      var vis = pts.filter(inside);
      if (vis.length > 1) {
        out.push('<polyline points="' + vis.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ') +
          '" fill="none" stroke="var(--_accent)" stroke-opacity=".45" stroke-dasharray="4 4"/>');
        var lp = vis.reduce(function (a, b) { return Math.abs(b[1] - H + 14) < Math.abs(a[1] - H + 14) ? b : a; });  // label near the bottom edge
        out.push('<text x="' + (lp[0] + 3).toFixed(1) + '" y="' + Math.min(H - 6, lp[1]).toFixed(1) + '" font-size="11" fill="var(--_accent)" fill-opacity=".85">' + esc(english ? mm[2] : mm[0] + '宿') + '</text>');
      }
    });
    // asterism lines + labels
    var segs = gx.segments(ra0, dec0, R * 1.5);
    segs.forEach(function (a) {
      var sx = 0, sy = 0, n = 0;
      a.segments.forEach(function (sg) {
        var p1 = P(sg[0][0], sg[0][1]), p2 = P(sg[1][0], sg[1][1]);
        if (!inside(p1) && !inside(p2)) return;
        out.push('<line x1="' + p1[0].toFixed(1) + '" y1="' + p1[1].toFixed(1) + '" x2="' + p2[0].toFixed(1) + '" y2="' + p2[1].toFixed(1) +
          '" stroke="var(--_line)" stroke-width="1.1" stroke-opacity=".8"/>');
        [p1, p2].forEach(function (p) { if (inside(p)) { sx += p[0]; sy += p[1]; n++; } });
      });
      var nz = loc.nearest && loc.nearest[0] && loc.nearest[0].asterism_zh === a.zh && n <= 2;  // the ring label already names it
      if (n && !nz) out.push('<text x="' + (sx / n).toFixed(1) + '" y="' + (sy / n - 6).toFixed(1) + '" font-size="12" text-anchor="middle" fill="var(--_line)">' + esc(english ? a.en : a.zh) + '</text>');
    });
    // stars
    gx.hips.forEach(function (h) {
      var s = gx.stars[h]; if (Math.abs(s[1] - dec0) > R * 1.6) return;
      if (Guxing.util.sepDeg(ra0, dec0, s[0], s[1]) > R * 1.5) return;
      var p = P(s[0], s[1]); if (!inside(p)) return;
      var r = Math.max(0.8, 3.6 - 0.5 * s[2]);
      out.push('<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="' + r.toFixed(2) + '" fill="var(--_star)"/>');
    });
    // nearest star ring
    var n0 = loc.nearest && loc.nearest[0];
    if (n0 && n0.sep_deg < R) {
      var ns = gx.stars[n0.star_hip], pn = P(ns[0], ns[1]);
      out.push('<circle cx="' + pn[0].toFixed(1) + '" cy="' + pn[1].toFixed(1) + '" r="7" fill="none" stroke="var(--_accent)" stroke-width="1"/>');
      out.push('<text x="' + (pn[0] + 9).toFixed(1) + '" y="' + (pn[1] + 4).toFixed(1) + '" font-size="12" fill="var(--_accent)">' + esc(english ? n0.star_en : n0.star_zh) + '</text>');
    }
    // path of a moving object
    if (path && path.length > 1) {
      var pp = path.map(function (q) { return P(q.ra, q.dec); }).filter(inside);
      if (pp.length > 1) out.push('<polyline points="' + pp.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ') +
        '" fill="none" stroke="var(--_accent)" stroke-width="2" stroke-linecap="round"/>');
    }
    // target
    out.push('<g stroke="var(--_accent)" stroke-width="2"><line x1="' + (W / 2 - 12) + '" y1="' + H / 2 + '" x2="' + (W / 2 - 4) + '" y2="' + H / 2 + '"/>' +
      '<line x1="' + (W / 2 + 4) + '" y1="' + H / 2 + '" x2="' + (W / 2 + 12) + '" y2="' + H / 2 + '"/>' +
      '<line x1="' + W / 2 + '" y1="' + (H / 2 - 12) + '" x2="' + W / 2 + '" y2="' + (H / 2 - 4) + '"/>' +
      '<line x1="' + W / 2 + '" y1="' + (H / 2 + 4) + '" x2="' + W / 2 + '" y2="' + (H / 2 + 12) + '"/></g>');
    out.push('<text x="8" y="16" font-size="11" fill="var(--_muted)">' + (english ? 'N ↑  E ←  Radius ' : '北 N ↑　东 E ←　半径 ') + R + '°</text>');
    out.push('</svg>');
    return out.join('');
  }

  customElements.define('guxing-card', GuxingCard);
})();
