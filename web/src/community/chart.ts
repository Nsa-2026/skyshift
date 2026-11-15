// Votes over time: one series of columns (per hour or per day), a hairline grid, the peak
// labelled, a tooltip on hover and on keyboard focus (arrow keys), and a table view.
// Columns ≤ 24 px, 4 px rounded tops, square at the baseline, at least 2 px apart.
import type { Activity, Bin } from "./stats";

const SVG = "http://www.w3.org/2000/svg";
// The plot fills whatever height the row gives it (community.css: the pair stretches, and the
// plot is the activity column's 1fr row), so the chart keeps pace with Most debated beside it
// however long that list's words run. A fixed height only ever matched one wording. This is the
// floor it will not go below, for the single-column layout and a short list.
const MIN_HEIGHT = 300;
const M = { top: 22, right: 8, bottom: 26, left: 34 };

const hourFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const longDayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const nf = new Intl.NumberFormat("en-GB");

export const votesWord = (n: number) => `${nf.format(n)} vote${n === 1 ? "" : "s"}`;

/** "14:00–15:00, Sat 14 Nov" or "Sat 14 Nov". */
export function binLabel(b: Bin, unit: Activity["unit"]): string {
  return unit === "hour" ? `${hourFmt.format(b.start)}–${hourFmt.format(b.end)}, ${longDayFmt.format(b.start)}` : longDayFmt.format(b.start);
}

/** Round tick step (1, 2, 5 × 10ⁿ) giving at most `most` intervals up to max. */
export function tickStep(max: number, most = 4): number {
  if (max <= 0) return 1;
  const raw = max / most;
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return Math.max(1, m * p);
  return Math.max(1, 10 * p);
}

/** A column path: square at the baseline, rounded data end. */
function columnPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function el<K extends keyof SVGElementTagNameMap>(name: K, attrs: Record<string, string | number>, text?: string): SVGElementTagNameMap[K] {
  const n = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text != null) n.textContent = text;
  return n;
}

export interface ChartHandle {
  update(a: Activity): void;
  destroy(): void;
}

export function mountActivityChart(host: HTMLElement): ChartHandle {
  host.innerHTML = "";
  const plot = document.createElement("div");
  plot.className = "cm-plot";
  plot.tabIndex = 0;
  plot.setAttribute("role", "img");
  const tip = document.createElement("div");
  tip.className = "cm-tip";
  tip.hidden = true;
  const tipValue = document.createElement("b");
  const tipWhen = document.createElement("span");
  tip.append(tipValue, tipWhen);
  const live = document.createElement("p");
  live.className = "visually-hidden";
  live.setAttribute("aria-live", "polite");
  const tableBox = document.createElement("details");
  tableBox.className = "cm-tableview";
  host.append(plot, live, tableBox);

  let data: Activity | null = null;
  let active = -1;
  let geom = { band: 0, left: M.left, tops: [] as number[] };

  function draw() {
    plot.querySelector("svg")?.remove();
    if (!data || !data.bins.length) return;
    const bins = data.bins;
    const width = Math.max(240, Math.round(plot.clientWidth || 640));
    const height = Math.max(MIN_HEIGHT, Math.round(plot.clientHeight || MIN_HEIGHT));
    const innerW = width - M.left - M.right;
    const innerH = height - M.top - M.bottom;
    const max = Math.max(1, ...bins.map((b) => b.count));
    const step = tickStep(max);
    const top = Math.ceil(max / step) * step;
    const y = (v: number) => M.top + innerH - (v / top) * innerH;
    const band = innerW / bins.length;
    let bw = Math.min(24, band * 0.72);
    if (band - bw < 2) bw = Math.max(1, band - 2);
    geom = { band, left: M.left, tops: bins.map((b) => y(b.count)) };

    const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true", focusable: "false" });
    // grid and y ticks
    for (let v = 0; v <= top; v += step) {
      svg.append(el("line", { x1: M.left, x2: width - M.right, y1: y(v), y2: y(v), class: v === 0 ? "cm-base" : "cm-grid" }));
      svg.append(el("text", { x: M.left - 8, y: y(v), class: "cm-ytick", "text-anchor": "end", "dominant-baseline": "middle" }, nf.format(v)));
    }
    // x labels: spaced at least ~64 px, on round hours (or every few days)
    const every = Math.max(1, Math.ceil(64 / band));
    const unit = data.unit;
    const roundEvery = unit === "hour" ? [1, 2, 3, 6, 12, 24].find((k) => k >= every) ?? 24 : [1, 2, 5, 7, 14, 30].find((k) => k >= every) ?? 30;
    bins.forEach((b, i) => {
      const d = new Date(b.start);
      const mark = unit === "hour" ? d.getHours() % roundEvery === 0 : i % roundEvery === 0;
      if (!mark) return;
      const cx = M.left + band * i + band / 2;
      const text = unit === "hour" ? (d.getHours() === 0 ? dayFmt.format(b.start) : hourFmt.format(b.start)) : dayFmt.format(b.start);
      svg.append(el("text", { x: cx, y: height - 8, class: "cm-xtick", "text-anchor": "middle" }, text));
    });
    // columns
    let peak = 0;
    bins.forEach((b, i) => {
      if (b.count > bins[peak].count) peak = i;
      if (!b.count) return;
      const x = M.left + band * i + (band - bw) / 2;
      const h = Math.max(1, y(0) - y(b.count));
      svg.append(el("path", { d: columnPath(x, y(0) - h, bw, h), class: `cm-col${i === active ? " is-active" : ""}`, "data-i": i }));
    });
    // the one direct label: the peak
    if (bins[peak].count) {
      const cx = M.left + band * peak + band / 2;
      svg.append(el("text", { x: cx, y: y(bins[peak].count) - 6, class: "cm-peak", "text-anchor": "middle" }, nf.format(bins[peak].count)));
    }
    plot.prepend(svg);
  }

  function showTip(i: number) {
    if (!data || i < 0 || i >= data.bins.length) return hideTip();
    active = i;
    const b = data.bins[i];
    tipValue.textContent = votesWord(b.count);
    tipWhen.textContent = binLabel(b, data.unit);
    tip.hidden = false;
    const cx = geom.left + geom.band * i + geom.band / 2;
    const w = plot.clientWidth || 640;
    tip.style.left = `${Math.min(Math.max(cx, 80), w - 80)}px`;
    tip.style.top = `${(geom.tops[i] ?? M.top) - 22}px`; // clears the peak label
    plot.querySelectorAll(".cm-col").forEach((c) => c.classList.toggle("is-active", c.getAttribute("data-i") === String(i)));
    live.textContent = `${tipValue.textContent}, ${tipWhen.textContent}`;
  }

  function hideTip() {
    active = -1;
    tip.hidden = true;
    plot.querySelectorAll(".cm-col.is-active").forEach((c) => c.classList.remove("is-active"));
  }

  function indexAt(clientX: number): number {
    const r = plot.getBoundingClientRect();
    return Math.floor((clientX - r.left - geom.left) / geom.band);
  }

  const onMove = (ev: PointerEvent) => {
    if (!data) return;
    const i = indexAt(ev.clientX);
    if (i < 0 || i >= data.bins.length) return hideTip();
    showTip(i);
  };
  const onLeave = () => {
    if (document.activeElement !== plot) hideTip();
  };
  const onKey = (ev: KeyboardEvent) => {
    if (!data?.bins.length) return;
    const n = data.bins.length;
    const at = active < 0 ? n - 1 : active;
    const next = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: n - 1 }[ev.key];
    if (next == null) return;
    ev.preventDefault();
    showTip(Math.min(n - 1, Math.max(0, next)));
  };
  const onFocus = () => {
    if (!data?.bins.length) return;
    let last = data.bins.length - 1;
    while (last > 0 && !data.bins[last].count) last--;
    showTip(active >= 0 ? active : last);
  };
  plot.addEventListener("pointermove", onMove);
  plot.addEventListener("pointerleave", onLeave);
  plot.addEventListener("keydown", onKey);
  plot.addEventListener("focus", onFocus);
  plot.addEventListener("blur", hideTip);
  plot.append(tip);

  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => draw()) : null;
  ro?.observe(plot);

  function renderTable(a: Activity) {
    const rows = a.bins
      .filter((b) => b.count > 0)
      .map((b) => `<tr><td>${binLabel(b, a.unit)}</td><td>${nf.format(b.count)}</td></tr>`)
      .join("");
    tableBox.innerHTML = `<summary>Show as a table</summary>
      <table class="cm-mini-table"><thead><tr><th scope="col">${a.unit === "hour" ? "Hour" : "Day"} (your time)</th><th scope="col">Votes</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="2">No votes in this period.</td></tr>`}</tbody></table>`;
  }

  return {
    update(a) {
      data = a;
      active = -1;
      tip.hidden = true;
      const sum = a.bins.reduce((s, b) => s + b.count, 0);
      const peak = a.bins.reduce((p, b) => (b.count > p.count ? b : p), a.bins[0] ?? { start: 0, end: 0, count: 0 });
      plot.setAttribute(
        "aria-label",
        a.bins.length
          ? `Votes per ${a.unit}, ${a.bins.length} ${a.unit}s: ${votesWord(sum)} in all, most in ${binLabel(peak, a.unit)} (${nf.format(peak.count)}). Arrow keys read each ${a.unit}.`
          : "No votes yet.",
      );
      // Nothing to draw yet: say so on the host, so the section takes no room until votes arrive.
      host.dataset.empty = sum === 0 ? "true" : "false";
      draw();
      renderTable(a);
    },
    destroy() {
      ro?.disconnect();
      host.innerHTML = "";
    },
  };
}
