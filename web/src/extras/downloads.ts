// "Downloads": the data behind the Blink page. Static first: the five fields with science products
// link their prepared /science-data/<slug>/science.json and build the exposure CSV from it in the
// browser; any spot can save the frames and object the page already loaded. The animated GIF is
// drawn by /api/export (contracts/api.md) where the server has the field. Drafts for the Minor
// Planet Center (ADES PSV) come from SkyShift's own unexplained movers on the Hours clock, built in
// the browser with the candidate page's report builder: always a draft, never sent.
// Reworked from PR #214 (春。 and Codex), web/src/card/science.ts downloads().
import { epochsFor } from "../api/client";
import { hasScience, loadScience } from "../api/science-client";
import type { Det, FramesResponse, MeasuredTrack } from "../api/types";
import { formatMjd } from "../app/astro";
import type { View } from "../app/modules";
import { targetBySlug } from "../app/state";
import { mpcDraft, type MpcDraft } from "../assist/reports";
import type { AssistCandidate } from "../assist/types";
import { type Ctx, esc, isAbort, plural, saveText, sectionShell, umRange } from "./shared";

/** One exposure of science.json `frames[]` (contracts/api.md, GET /api/science). */
export interface ScienceFrame {
  mjd: number;
  det: number;
  um: number | null;
  visit: number;
  quality: string | null;
  flags?: Record<string, number> | null;
}

const cell = (v: unknown): string => {
  if (v == null) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const csv = (head: string[], rows: unknown[][]): string => [head, ...rows].map((r) => r.map(cell).join(",")).join("\n") + "\n";

/** The exposure table of science.json as CSV: the columns of /api/export?format=csv, flags as NAME=count;… */
export function scienceCsv(frames: ScienceFrame[]): string {
  return csv(
    ["mjd", "det", "um", "visit", "quality", "flags"],
    frames.map((f) => [
      f.mjd,
      f.det,
      f.um,
      f.visit,
      f.quality,
      f.flags ? Object.entries(f.flags).map(([k, n]) => `${k}=${n}`).join(";") : "",
    ]),
  );
}

/** The blink's epochs (visits or exposures) as CSV: times in MJD (UTC), wavelengths in µm. */
export function epochsCsv(frames: FramesResponse): string {
  return csv(
    ["id", "kind", "det", "mjd_mid", "mjd_start", "mjd_end", "n_exposures", "um_min", "um_max", "flag_frac", "sat_frac"],
    [...frames.epochs]
      .sort((a, b) => a.det - b.det || a.mjd_mid - b.mjd_mid)
      .map((e) => [e.id, e.kind, e.det, e.mjd_mid, e.mjd_start, e.mjd_end, e.n_exposures, e.um_min, e.um_max, e.flag_frac, e.sat_frac]),
  );
}

/** Tracks a draft can be made for: SkyShift's unexplained movers ("suspect"), each with two or more dots. */
export function draftTracks(frames: FramesResponse | null): MeasuredTrack[] {
  return (frames?.measured?.tracks ?? []).filter((t) => t.class === "suspect" && t.dots.length >= 2);
}

/** An ADES PSV draft for one track, from its dots in these frames (assist/reports.ts mpcDraft). */
export function trackDraft(track: MeasuredTrack, frames: FramesResponse, fallbackDet: Det): MpcDraft {
  const dots = [...track.dots].sort((a, b) => a.mjd - b.mjd);
  const detections = dots.map((d) => {
    const det = frames.epochs.find((e) => e.id === d.epoch_id)?.det ?? fallbackDet;
    const m = frames.measured?.detections.find((x) => x.track_id === track.id && x.epoch_id === d.epoch_id);
    return { ra: d.ra, dec: d.dec, mjd: d.mjd, det, flux_ujy: m?.flux_ujy ?? null, snr: m?.snr ?? null };
  });
  // Only the fields mpcDraft reads: an id for the tracklet name, the first position, the dots.
  const c = {
    id: track.suspect_id ?? track.id,
    ra: dots[0].ra,
    dec: dots[0].dec,
    mjd: dots[0].mjd,
    det: detections[0].det,
    flux_ujy: null,
    snr: null,
    n_dots: dots.length,
    detections,
  } as unknown as AssistCandidate;
  return mpcDraft(c, { dataRelease: frames.data_release || undefined });
}

interface Item {
  label: string;
  about: string;
  href?: string;
  file?: string;
  make?: () => Promise<{ text: string; name: string; type: string }> | { text: string; name: string; type: string };
}

const slugOrSpot = (v: View) => v.slug ?? `${v.ra.toFixed(4)}_${v.dec >= 0 ? "+" : ""}${v.dec.toFixed(4)}`;

export function itemsFor(ctx: Ctx, signal: () => AbortSignal): Item[] {
  const { view, frames, object } = ctx;
  const items: Item[] = [];
  const name = slugOrSpot(view);
  const det = view.det;
  const band = `D${det} (${umRange(det)} µm)`;
  const q = `ra=${view.ra}&dec=${view.dec}`;
  if (hasScience(view.slug)) {
    items.push({
      label: "Analysis, JSON",
      about: "The field's prepared science product: every exposure with its quality, light curves, synthetic W1 and W2, the DR1 reference spectrum and the depth reached.",
      href: `/science-data/${view.slug}/science.json`,
      file: `skyshift-${name}-science.json`,
    });
    items.push({
      label: "Exposure list, CSV",
      about: "One row per SPHEREx exposure: time (MJD, UTC), detector, wavelength (µm), visit, quality and flagged pixels by name.",
      make: async () => {
        const sci = await loadScience<{ frames: ScienceFrame[] }>(view, signal());
        return { text: scienceCsv(sci.frames), name: `skyshift-${name}-exposures.csv`, type: "text/csv" };
      },
    });
    items.push({
      label: `Animated GIF, ${band}`,
      about: `The months blink of ${band}, one frame per visit, drawn by our server.`,
      href: `/api/export?${q}&det=${det}&clock=months&format=gif`,
      file: `skyshift-${name}-d${det}-months.gif`,
    });
    return items;
  }
  if (frames) {
    items.push({
      label: "Frames and object, JSON",
      about: "What this page loaded, without the pixels: every epoch, the Gaia stars and known objects in the field, what our pipeline measured, and the object's spectrum and shift.",
      make: () => ({ text: JSON.stringify({ frames, object }, null, 1), name: `skyshift-${name}-${frames.clock}.json`, type: "application/json" }),
    });
    const kind = frames.clock === "hours" ? "exposure" : "visit";
    items.push({
      label: `${kind === "visit" ? "Visit" : "Exposure"} list, CSV`,
      about: `One row per ${kind} on the plate: detector, times (MJD, UTC), exposures, wavelengths (µm) and the flagged and saturated fractions.`,
      make: () => ({ text: epochsCsv(frames), name: `skyshift-${name}-${frames.clock}.csv`, type: "text/csv" }),
    });
    // A featured target's static twin: the server draws its GIF (contracts/api.md, /api/export).
    if (targetBySlug(view.slug) && epochsFor(frames, det).length > 1)
      items.push({
        label: `Animated GIF, ${band}`,
        about: `The ${frames.clock} blink of ${band}, drawn by our server.`,
        href: `/api/export?${q}&det=${det}&clock=${frames.clock}&format=gif`,
        file: `skyshift-${name}-d${det}-${frames.clock}.gif`,
      });
  }
  return items;
}

export function mountDownloads(host: HTMLElement) {
  host.insertAdjacentHTML(
    "beforeend",
    sectionShell("downloads", "Downloads", "The data behind this page, and a draft report for the Minor Planet Center when SkyShift finds an unexplained mover."),
  );
  const details = host.querySelector<HTMLDetailsElement>('[data-sec="downloads"]')!;
  const body = details.querySelector<HTMLElement>("[data-body]")!;
  let ctx: Ctx | null = null;
  let ctl = new AbortController();

  function render() {
    if (!ctx || !details.open) return;
    const at = ctx;
    const items = itemsFor(at, () => ctl.signal);
    const tracks = draftTracks(at.frames);
    const row = (it: Item, i: number) =>
      `<li>${
        it.href
          ? `<a class="btn btn-ghost ex-dl-btn" href="${esc(it.href)}" download="${esc(it.file ?? "")}">${esc(it.label)}</a>`
          : `<button type="button" class="btn btn-ghost ex-dl-btn" data-make="${i}">${esc(it.label)}</button>`
      }<span class="ex-dl-about">${esc(it.about)}</span></li>`;
    const files = items.length
      ? `<ul class="ex-dl">${items.map(row).join("")}</ul>`
      : `<p class="ex-p ex-status">This fills in once the plate's frames have loaded.</p>`;
    const gifNote =
      !hasScience(at.view.slug) && !targetBySlug(at.view.slug) && at.frames
        ? `<p class="ex-note">The animated GIF is drawn on our server for featured targets only.</p>`
        : "";
    let drafts: string;
    if (tracks.length) {
      const list = tracks
        .map((t, i) => {
          const first = Math.min(...t.dots.map((d) => d.mjd));
          return `<li><button type="button" class="btn btn-ghost ex-dl-btn" data-draft="${i}">ADES draft, mover ${i + 1}</button><span class="ex-dl-about">${plural(t.dots.length, "position")} on ${formatMjd(first)}<span class="pro-only">, ${esc(t.suspect_id ?? t.id)}</span>.</span></li>`;
        })
        .join("");
      drafts = `<p class="ex-p">Drafts, not submitted. Two dots in one session can't show motion by themselves: blink the mover first. Each file lists what to add before sending it to the Minor Planet Center (your name, and SPHEREx's position for every line).</p><ul class="ex-dl">${list}</ul>`;
    } else if (at.frames?.clock === "hours" && !at.frames.measured) {
      // measured null or absent: not measured, which is not the same as "no mover".
      drafts = `<p class="ex-p">No draft: our pipeline hasn't measured these exposures, so it can't say whether anything here moves.</p>`;
    } else if (at.frames?.clock === "hours") {
      drafts = `<p class="ex-p">No draft: SkyShift found no unexplained mover in these exposures.</p>`;
    } else {
      drafts = `<p class="ex-p">Drafts come from unexplained movers SkyShift finds on the Hours clock.</p>`;
    }
    body.innerHTML = `${files}${gifNote}
      <h3 class="ex-h">Report to the Minor Planet Center</h3>${drafts}
      <p class="ex-status" role="status" data-said></p>`;
    const said = body.querySelector<HTMLElement>("[data-said]")!;
    body.querySelectorAll<HTMLButtonElement>("[data-make]").forEach((b) =>
      b.addEventListener("click", async () => {
        const it = items[Number(b.dataset.make)];
        if (!it?.make) return;
        const key = at.key;
        b.disabled = true;
        said.textContent = "";
        try {
          const f = await it.make();
          if (ctx?.key !== key) return;
          saveText(f.text, f.name, f.type);
        } catch (e) {
          if (isAbort(e) || ctx?.key !== key) return;
          said.textContent = "The prepared file couldn't be read. Try again in a minute.";
        } finally {
          b.disabled = false;
        }
      }),
    );
    body.querySelectorAll<HTMLButtonElement>("[data-draft]").forEach((b) =>
      b.addEventListener("click", () => {
        const t = tracks[Number(b.dataset.draft)];
        if (!t || !at.frames) return;
        const d = trackDraft(t, at.frames, at.view.det);
        saveText(d.text, `skyshift-draft-${(t.suspect_id ?? t.id).replace(/[^\w.-]+/g, "_")}.psv`, "text/plain");
      }),
    );
  }

  details.addEventListener("toggle", render);

  return {
    clear() {
      ctx = null;
      ctl.abort();
      ctl = new AbortController();
      body.replaceChildren();
    },
    show(next: Ctx) {
      if (ctx?.key !== next.key) this.clear();
      ctx = next;
      render();
    },
  };
}
