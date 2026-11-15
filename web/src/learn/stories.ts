// Stories in the sky (#/learn/stories): guest stars from Chinese histories and SPHEREx at their
// remnants, the old star map around any spot, and the Moon and planets among the lodges in
// 2026–27. Brought back from #214 (春。 and Codex) onto the Learn page, with the remnant gallery
// and the "where the Viewer is now" spot from 春。's later Stories (product/stories.ts). This module,
// its CSS and the /guxing/ scripts load only when the page opens (learn/index.ts imports it).
import "./stories.css";
import { loadObject } from "../api/client";
import type { Det } from "../api/types";
import { formatRaDec } from "../app/astro";
import { parseCoords } from "../app/coords";
import type { PageContext, View } from "../app/modules";
import { readView, TARGETS } from "../app/state";
import {
  EVENT_TERMS,
  blinkAt,
  blinkHref,
  coordKey,
  esc,
  eventLine,
  eventsIn,
  guestChecks,
  loadGuxing,
  measuredPath,
  monthLabel,
  monthsBetween,
  pickMonth,
  remnantCaption,
  resolveSpot,
  skyHref,
  snName,
  sourceHtml,
  spotList,
  yearText,
  zh,
  type GuxingLibs,
  type Spot,
} from "./stories-data";
import type { StoryPart } from "./stories-route";

export interface StoriesOptions {
  signal: AbortSignal;
  explore: PageContext["explore"];
  /** Where the Viewer is now (Around a spot opens there); defaults to the view in the address. */
  here?: () => View | null;
  /** After opening the Viewer (the page passes window.scrollTo). */
  scrollTop?: () => void;
  /** Test seams. */
  load?: () => Promise<GuxingLibs>;
  now?: () => Date;
}

const IMG_BASE = "/guxing/data/nasa/";

function blinkLink(pos: { ra: number; dec: number }, label = "Blink this spot"): string {
  const at = blinkAt(pos);
  return `<a class="btn" href="${esc(blinkHref(at))}" data-blink data-ra="${at.ra}" data-dec="${at.dec}" data-fov="${at.fov}" data-det="${at.det}">${esc(label)}</a>`;
}

const dashes = (s: string) => s.replace(/(\d)-(\d)/g, "$1–$2");

// ---- guest stars ----------------------------------------------------------------------------
export function guestsHtml({ gx, nasa }: GuxingLibs): string {
  const guests = [...nasa.guest_stars.items].sort((a, b) => a.year - b.year);
  const unchecked = guests.every((g) => !g.source_checked);
  const items = guests
    .map((g) => {
      const r = nasa.spherex_remnants.items.find((x) => x.id === g.id);
      if (!r) return "";
      const checks = guestChecks(gx, g);
      return `
      <article class="st-guest" id="st-${esc(g.id)}" aria-labelledby="st-${esc(g.id)}-h">
        <h2 id="st-${esc(g.id)}-h"><span class="st-sn">${esc(snName(g.id))}</span> ${esc(r.remnant)}</h2>
        <figure class="st-pic">
          <img src="${esc(IMG_BASE + r.image)}" width="256" height="256" loading="lazy" decoding="async"
            alt="SPHEREx colour picture of the sky around ${esc(r.remnant)}">
          <figcaption>${esc(remnantCaption(r))}</figcaption>
        </figure>
        <div class="st-text">
          <figure class="st-record">
            <blockquote>
              <p>“${esc(g.record_en)}”</p>
              <p class="st-zh" lang="zh">${esc(g.record_zh)}</p>
            </blockquote>
            <figcaption>${esc(yearText(g.year))}, ${sourceHtml(g.source)}${
              g.source_checked ? ". Checked against the original text." : unchecked ? "" : ". Not checked against the original text."
            }</figcaption>
          </figure>
          <p class="st-seen"><strong>In SPHEREx:</strong> ${esc(dashes(r.seen_in_spherex))}</p>
          <ul class="st-checks" aria-label="Does the record fit the remnant's position?">
            ${checks
              .map(
                (c) =>
                  `<li class="${c.ok === true ? "st-fits" : c.ok === false ? "st-differs" : "st-info"}">${
                    c.ok === true ? `<span class="st-mark">Fits</span>` : c.ok === false ? `<span class="st-mark">Differs</span>` : ""
                  }<span>${c.html}</span></li>`,
              )
              .join("")}
          </ul>
          <p class="st-actions">
            ${blinkLink(r)}
            <a href="#/learn/stories/asterisms/${esc(g.id)}">The old star map here</a>
            <a href="${esc(skyHref(r, r.field_deg))}">Sky map</a>
          </p>
        </div>
      </article>`;
    })
    .join("");
  const gallery = guests
    .map((g) => {
      const r = nasa.spherex_remnants.items.find((x) => x.id === g.id);
      return r
        ? `<li><a href="#/learn/stories" data-jump="st-${esc(g.id)}"><img src="${esc(IMG_BASE + r.image)}" width="256" height="256" loading="lazy" decoding="async" alt=""><span><strong>${esc(
            snName(g.id),
          )}</strong> ${esc(r.remnant)}</span></a></li>`
        : "";
    })
    .join("");
  return `
    <ol class="st-gallery" aria-label="The six guest stars">${gallery}</ol>
    <div class="st-intro">
      <p>Chinese court astronomers logged “guest stars” (${zh("客星")}): new lights that blazed up and faded. These six records are matched today with the remnants of exploded stars.${
        unchecked
          ? " Each record is quoted as the secondary literature gives it (Stephenson &amp; Green 2002; Xu, Pankenier &amp; Jiang 2000); we haven't checked the original texts."
          : ""
      }</p>
      <p>Under each record, the remnant's position is checked against the place the record names, on a star map with Qing-era identifications made long after these records: a mismatch may come from the map rather than the record. A remnant doesn't visibly change between SPHEREx visits six months apart; blinking one shows the photos of that spot and anything that moves through them.</p>
    </div>
    <div class="st-guests">${items}</div>`;
}

// ---- around a spot ------------------------------------------------------------------------------
function spotOptions(list: readonly Spot[], current: Spot): string {
  const opt = (s: Spot) => `<option value="${esc(s.key)}" ${s.key === current.key ? "selected" : ""}>${esc(s.label)}</option>`;
  const extra =
    current.group === "here"
      ? `<option value="${esc(current.key)}" selected>In the Viewer now: ${esc(current.label)}</option>`
      : current.group === "typed"
        ? `<option value="${esc(current.key)}" selected>${esc(current.label)}</option>`
        : "";
  return `${extra}
    <optgroup label="Featured on SkyShift">${list.filter((s) => s.group === "featured").map(opt).join("")}</optgroup>
    <optgroup label="Guest stars">${list.filter((s) => s.group === "guest").map(opt).join("")}</optgroup>`;
}

export function asterismsHtml({ gx }: GuxingLibs, list: readonly Spot[], current: Spot): string {
  return `
    <div class="st-intro">
      <p>Chinese astronomers divided the sky into 28 lodges (${zh("宿")}): slices from pole to pole, each starting at a determinative star. A position inside a lodge is counted in du (${zh("度")}), 365.25 to the full circle. Stars were grouped into asterisms (${zh("星官")}); the map here has ${gx.asterisms.length} of them, with names like ${esc(
        gx.asterisms.find((a) => a.zh === "天关")?.en ?? "Celestial Pass",
      )} and ${esc(gx.asterisms.find((a) => a.zh === "骑官")?.en ?? "Imperial Guards")}.</p>
      <p>Pick a spot to see its lodge, the asterisms around it, and how a court astronomer might have logged a new star there.</p>
    </div>
    <form class="st-pick" novalidate>
      <div class="st-field">
        <label for="st-spot">Spot</label>
        <select id="st-spot" class="input">${spotOptions(list, current)}</select>
      </div>
      <div class="st-field">
        <label for="st-coords">Or paste coordinates</label>
        <span class="st-row"><input id="st-coords" class="input" type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="83.633 22.0145"><button type="submit" class="btn">Show</button></span>
      </div>
    </form>
    <p class="st-msg" role="status"></p>
    <div class="st-card"></div>
    <p class="st-path" hidden></p>
    <p class="st-actions st-spot-actions"></p>`;
}

function cardFor(spot: Spot, nowYear: number): HTMLElement {
  const card = document.createElement("guxing-card");
  const year = spot.year ?? nowYear;
  card.setAttribute("lang", "en");
  card.setAttribute("ra", String(spot.ra));
  card.setAttribute("dec", String(spot.dec));
  card.setAttribute("date", String(year));
  if (spot.year != null) card.setAttribute("epoch", String(spot.year));
  const where =
    spot.group === "guest"
      ? `${spot.label.replace(/^SN (\d+): /, "the remnant of SN $1, ")}, logged as if in ${yearText(year)}`
      : spot.group === "featured"
        ? `${spot.label}, SkyShift's featured field`
        : formatRaDec(spot);
  card.setAttribute("source", `Position: ${where}.`);
  return card;
}

function showSpot(body: HTMLElement, spot: Spot, o: StoriesOptions, ctl: { abort?: AbortController }): void {
  ctl.abort?.abort();
  const mine = new AbortController();
  ctl.abort = mine;
  o.signal.addEventListener("abort", () => mine.abort(), { once: true });
  const nowYear = (o.now?.() ?? new Date()).getUTCFullYear();
  const holder = body.querySelector<HTMLElement>(".st-card")!;
  const card = cardFor(spot, nowYear);
  holder.replaceChildren(card);
  const path = body.querySelector<HTMLElement>(".st-path")!;
  path.hidden = true;
  path.textContent = "";
  body.querySelector<HTMLElement>(".st-spot-actions")!.innerHTML = `${blinkLink(spot)} <a href="${esc(skyHref(spot, 1))}">Sky map</a>`;
  if (spot.slug) {
    void loadObject({ ra: spot.ra, dec: spot.dec, slug: spot.slug }, mine.signal)
      .then((obj) => {
        if (mine.signal.aborted) return;
        const p = measuredPath(obj.measure);
        if (!p) return;
        card.setAttribute("pmra", p.pmra.toFixed(1));
        card.setAttribute("pmdec", p.pmdec.toFixed(1));
        card.setAttribute("years", "1000");
        card.setAttribute("source", `Position: ${spot.label}, SkyShift's featured field. Motion: SkyShift's measurement between SPHEREx visits.`);
        path.hidden = false;
        path.textContent = `The red line is where ${spot.label} drifts in the next 1000 years at the rate SkyShift measured between SPHEREx visits, ${p.rate.toFixed(2)}″ a year. A straight-line extrapolation, not a prediction.`;
      })
      .catch(() => {
        // No measurement to draw: the card still shows the spot.
      });
  }
}

function wireAsterisms(body: HTMLElement, list: Spot[], first: Spot, o: StoriesOptions): void {
  const ctl: { abort?: AbortController } = {};
  const select = body.querySelector<HTMLSelectElement>("#st-spot")!;
  const input = body.querySelector<HTMLInputElement>("#st-coords")!;
  const msg = body.querySelector<HTMLElement>(".st-msg")!;
  const go = (spot: Spot) => {
    history.replaceState(history.state, "", `#/learn/stories/asterisms/${spot.key}`);
    showSpot(body, spot, o, ctl);
  };
  select.addEventListener(
    "change",
    () => {
      const spot = resolveSpot(select.value, list, null);
      if (spot) {
        msg.textContent = "";
        go(spot);
      }
    },
    { signal: o.signal },
  );
  body.querySelector("form")!.addEventListener(
    "submit",
    (ev) => {
      ev.preventDefault();
      const pos = parseCoords(input.value);
      if (!pos) {
        msg.textContent = "Couldn't read that. Paste coordinates in degrees or hours, like 83.633 22.0145 or 05h34m31.9s +22°00′52″.";
        msg.classList.add("is-error");
        return;
      }
      msg.classList.remove("is-error");
      const spot = resolveSpot(coordKey(pos), list, null)!;
      select.querySelector("option.st-typed")?.remove();
      const o2 = document.createElement("option");
      o2.className = "st-typed";
      o2.value = spot.key;
      o2.textContent = spot.label;
      select.prepend(o2);
      select.value = spot.key;
      msg.textContent = `Showing ${spot.label}.`;
      go(spot);
    },
    { signal: o.signal },
  );
  showSpot(body, first, o, ctl);
}

// ---- planets ------------------------------------------------------------------------------------
export function planetsHtml({ nasa }: GuxingLibs, month: string, months: readonly string[]): string {
  const p = nasa.planets;
  return `
    <div class="st-intro">
      <p>Where the Moon and planets meet the lodges and bright stars in 2026 and 2027, in the words court astronomers used for it. Computed from NASA JPL's DE421 ephemeris, as seen from the centre of the Earth: from where you stand, the Moon can look up to a degree away from these positions. Under each event, the same event in the style of a court record, with the year's name in the old sixty-year cycle (2026 is ${zh("丙午")}).</p>
    </div>
    <div class="st-month">
      <label for="st-month">Month</label>
      <span class="st-row">
        <button type="button" class="btn btn-ghost" data-step="-1" aria-label="Earlier month">‹</button>
        <select id="st-month" class="input">${months.map((m) => `<option value="${m}" ${m === month ? "selected" : ""}>${monthLabel(m)}</option>`).join("")}</select>
        <button type="button" class="btn btn-ghost" data-step="1" aria-label="Later month">›</button>
      </span>
    </div>
    <p class="st-count" role="status"></p>
    <ol class="st-events"></ol>
    <dl class="st-terms">
      <div><dt>${zh("犯")} ${EVENT_TERMS["犯"].en}</dt><dd>passing within 1° of a star.</dd></div>
      <div><dt>${zh("掩")} ${EVENT_TERMS["掩"].en}</dt><dd>the Moon covering a star (within 0.26°).</dd></div>
      <div><dt>${zh("入宿")} ${EVENT_TERMS["入宿"].en}</dt><dd>crossing from one of the 28 lodges into its neighbour.</dd></div>
      <div><dt>${zh("留")} ${EVENT_TERMS["留"].en}</dt><dd>a planet's motion in right ascension reverses.</dd></div>
    </dl>
    <p class="st-small">Stars of magnitude ${p.star_limit_mag.toFixed(1)} or brighter; for the Moon, ${p.moon_star_limit_mag.toFixed(1)} or brighter. Dates in UTC.</p>`;
}

function fillMonth(body: HTMLElement, libs: GuxingLibs, month: string): void {
  const list = eventsIn(libs.nasa.planets.events, month);
  const n = list.length;
  body.querySelector(".st-count")!.textContent = n === 0 ? `Nothing in ${monthLabel(month)}.` : `${n} event${n === 1 ? "" : "s"} in ${monthLabel(month)}.`;
  body.querySelector(".st-events")!.innerHTML = list
    .map((e) => {
      const l = eventLine(libs.gx, e);
      const log = e.log_zh ? `<span class="st-log" lang="zh" title="As a court astronomer would have logged it">${esc(e.log_zh)}</span>` : "";
      return `<li><span class="st-date">${esc(l.date)}</span><span class="st-term">${zh(l.term.zh)} ${esc(l.term.en)}</span><span class="st-what">${l.html}${log}</span></li>`;
    })
    .join("");
}

function wirePlanets(body: HTMLElement, libs: GuxingLibs, months: string[], o: StoriesOptions): void {
  const select = body.querySelector<HTMLSelectElement>("#st-month")!;
  const set = (m: string) => {
    select.value = m;
    history.replaceState(history.state, "", `#/learn/stories/planets/${m}`);
    fillMonth(body, libs, m);
    body.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) => {
      const i = months.indexOf(m) + Number(b.dataset.step);
      b.disabled = i < 0 || i >= months.length;
    });
  };
  select.addEventListener("change", () => set(select.value), { signal: o.signal });
  body.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) =>
    b.addEventListener(
      "click",
      () => {
        const i = months.indexOf(select.value) + Number(b.dataset.step);
        if (i >= 0 && i < months.length) set(months[i]);
      },
      { signal: o.signal },
    ),
  );
  fillMonth(body, libs, select.value);
  body.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) => {
    const i = months.indexOf(select.value) + Number(b.dataset.step);
    b.disabled = i < 0 || i >= months.length;
  });
}

// ---- credits ------------------------------------------------------------------------------------
export function creditsHtml(): string {
  return `
    <section class="st-credits" aria-labelledby="st-credits-h">
      <h2 id="st-credits-h">Where this comes from</h2>
      <dl>
        <div><dt>Records</dt><dd>Guest-star wording as quoted by Stephenson &amp; Green (2002) and Xu, Pankenier &amp; Jiang (2000).</dd></div>
        <div><dt>Star map</dt><dd>Asterisms, lodges and star names from Stellarium's “Chinese” sky culture (CC BY-SA): Qing-era identifications, English names after the Hong Kong Space Museum. Star positions from the HYG database v4.1 (CC BY-SA 4.0).</dd></div>
        <div><dt>Moon and planets</dt><dd>NASA JPL DE421 ephemeris (Folkner et al. 2008), geocentric, without light-time or aberration.</dd></div>
        <div><dt>Remnant pictures</dt><dd>NASA SPHEREx DR1 all-sky spectral cube (HiPS, 102 planes from 0.75 to 5.0 µm), from NASA/IPAC IRSA.</dd></div>
        <div><dt>Guxing (${zh("古星")})</dt><dd>The library behind this page, by 春。 and Codex. A cultural interpretation, not a detection result.</dd></div>
      </dl>
    </section>`;
}

// ---- the page -------------------------------------------------------------------------------------
export async function renderStories(body: HTMLElement, part: StoryPart, arg: string | null, o: StoriesOptions): Promise<void> {
  body.innerHTML = `<p class="st-status" role="status">Loading the old star maps…</p>`;
  let libs: GuxingLibs;
  try {
    libs = await (o.load ? o.load() : loadGuxing());
  } catch (e) {
    if (o.signal.aborted) return;
    body.innerHTML = `<div class="st-fail" role="alert"><p>${esc((e as Error).message)}</p><p><button type="button" class="btn st-retry">Try again</button></p></div>`;
    body.querySelector(".st-retry")!.addEventListener("click", () => void renderStories(body, part, arg, o), { once: true });
    return;
  }
  if (o.signal.aborted) return;

  body.addEventListener(
    "click",
    (ev) => {
      const a = (ev.target as Element | null)?.closest<HTMLAnchorElement>("a[data-blink]");
      const me = ev as MouseEvent;
      if (!a || me.button !== 0 || me.metaKey || me.ctrlKey || me.shiftKey || me.altKey) return;
      ev.preventDefault();
      // In place, without reloading the site; the Viewer keeps the scroll it finds, so start at the top.
      o.explore({ ra: Number(a.dataset.ra), dec: Number(a.dataset.dec), clock: "months", fov: Number(a.dataset.fov), det: Number(a.dataset.det) as Det });
      o.scrollTop?.();
    },
    { signal: o.signal },
  );

  if (part === "guests") {
    body.innerHTML = guestsHtml(libs) + creditsHtml();
    // The gallery jumps to each record; a "#st-…" address would leave the Learn route.
    body.querySelectorAll<HTMLAnchorElement>("a[data-jump]").forEach((a) =>
      a.addEventListener(
        "click",
        (ev) => {
          const target = body.querySelector<HTMLElement>(`#${a.dataset.jump}`);
          if (!target) return;
          ev.preventDefault();
          target.scrollIntoView({ block: "start" });
          const h = target.querySelector<HTMLElement>("h2");
          if (h) {
            h.tabIndex = -1;
            h.focus({ preventScroll: true });
          }
        },
        { signal: o.signal },
      ),
    );
  } else if (part === "planets") {
    const months = monthsBetween(libs.nasa.planets.start, libs.nasa.planets.end);
    const month = pickMonth(months, arg, o.now?.() ?? new Date());
    body.innerHTML = planetsHtml(libs, month, months) + creditsHtml();
    wirePlanets(body, libs, months, o);
  } else {
    const list = spotList(TARGETS, libs.nasa.spherex_remnants.items, libs.nasa.guest_stars.items);
    const here = o.here ? o.here() : readView();
    const spot = resolveSpot(arg, list, here) ?? list[0];
    body.innerHTML = asterismsHtml(libs, list, spot) + creditsHtml();
    if (arg && !resolveSpot(arg, list, null)) {
      body.querySelector(".st-msg")!.textContent = `Couldn't read “${arg}” as a spot. Showing ${spot.label} instead.`;
    }
    wireAsterisms(body, list, spot, o);
  }
}
