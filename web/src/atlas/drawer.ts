// The preview drawer (right on desktop, bottom sheet on phone): what the signal is, why it is on
// the list, its key number with units, a live mini blink, votes so far (read only), and three
// actions: Review it, Blink here, Next nearby. Also the small pin for a click on empty sky.
import { GRADE_TEXT, failedGates, gradeCSentence, gradeLabel, gradeOf, isGate, votable } from "../api/grades";
import type { Candidate, CandidateKind, FramesResponse } from "../api/types";
import { DEFAULT_FOV, candidateFramesUrl, loadFrames } from "../api/client";
import { formatRaDec } from "../app/astro";
import type { Target } from "../app/state";
import { ANSWERS, ANSWER_TEXT, consensus, total, type Counts } from "../vote/model";
import { getVoteStore } from "../vote/store";
import { KIND_TEXT, detectorText, flagsSentence, isCaution, keyNumber, labelText, reasonText, seenText, zoneName } from "./kinds";
import { mountMiniBlink, type MiniBlinkHandle } from "./miniblink";

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

/** The kind's pencil mark as a small inline SVG (for chips, the key, the drawer and the list). */
export function kindIcon(kind: CandidateKind | "target", size = 18): string {
  const s = size;
  const m = s / 2;
  const r = s * 0.26;
  let d = "";
  let cls = "atl-ico-red";
  if (kind === "mover") {
    const t = r + 1;
    d = `<circle cx="${m - 1.5}" cy="${m + 1.5}" r="${r}"/><path d="M${m - 1.5 + t * 0.7} ${m + 1.5 - t * 0.7}l${s * 0.2} ${-s * 0.2}"/>`;
  } else if (kind === "high_pm") {
    d = `<circle cx="${m - 3}" cy="${m}" r="${r * 0.85}"/><path d="M${m - 3 + r} ${m}H${s - 2}M${s - 5.5} ${m - 3}L${s - 2} ${m}L${s - 5.5} ${m + 3}"/>`;
  } else if (kind === "spectral") {
    cls = "atl-ico-violet";
    d = `<path d="M${m} ${m - r - 1.5}L${m + r + 1.5} ${m}L${m} ${m + r + 1.5}L${m - r - 1.5} ${m}Z"/>`;
  } else {
    cls = "atl-ico-ink";
    d = `<path d="M${m + 2.5} ${m}h4M${m - 2.5} ${m}h-4M${m} ${m + 2.5}v4M${m} ${m - 2.5}v-4"/>`;
  }
  return `<svg class="atl-ico ${cls}" width="${s}" height="${s}" viewBox="0 0 ${s} ${s}" aria-hidden="true" focusable="false">${d}</svg>`;
}

/** Votes so far, in one or two plain sentences. */
export function votesText(c: Counts): string {
  const n = total(c);
  if (n === 0) return "No votes yet. Review it to cast the first.";
  const parts = ANSWERS.filter((a) => c[a] > 0)
    .sort((a, b) => c[b] - c[a])
    .map((a) => `${c[a]} ${ANSWER_TEXT[a].label.toLowerCase()}`);
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  const k = consensus(c);
  const head = `${n} vote${n === 1 ? "" : "s"}: ${list}.`;
  return k
    ? `${head} People agree: ${ANSWER_TEXT[k.answer].label.toLowerCase()} (${Math.round(k.share * 100)} %).`
    : `${head} Consensus needs 5 votes with 70 % agreeing.`;
}

/** The vetting grade in one or two sentences, or nothing for a row without one (as before the vetting). */
export function gradeLine(c: Candidate): string {
  const g = gradeOf(c);
  if (!g) return "";
  const t = GRADE_TEXT[g];
  const gates = g === "C" ? failedGates(c) : [];
  const text =
    g === "C"
      ? `${gates.length ? gradeCSentence(gates) : "Grade C: it fails a pipeline check."} Most likely an artefact or a catalogued source, so it isn't put to a vote; you can still blink it.`
      : `${gradeLabel(g)}. ${t.plain}`;
  return `<p class="atl-dr-grade" data-grade="${g}">${esc(text)}</p>`;
}

function nearestKnownText(c: Candidate): string {
  if (c.nearest_known_arcsec == null || !Number.isFinite(c.nearest_known_arcsec)) return "Not checked";
  const a = c.nearest_known_arcsec;
  if (a >= 3600) return `None within 1°`;
  if (a >= 120) return `Nearest ${(a / 60).toFixed(1)}′ away`;
  return `Nearest ${a.toFixed(a >= 10 ? 0 : 1)}″ away`;
}

function cataloguesText(c: Candidate): string {
  if (c.gaia_match && c.catwise_match) return "In Gaia and CatWISE";
  if (c.gaia_match) return "In Gaia";
  if (c.catwise_match) return "In CatWISE";
  return "In neither Gaia nor CatWISE";
}

export interface DrawerHandlers {
  review(c: Candidate): void;
  blink(c: Candidate): void;
  next(c: Candidate): void;
  close(): void;
}

export interface DrawerHandle {
  destroy(): void;
  pause(): void;
}

function copyText(text: string, btn: HTMLButtonElement) {
  const done = (ok: boolean) => {
    const was = btn.textContent;
    btn.textContent = ok ? "Copied" : "Select and copy";
    setTimeout(() => (btn.textContent = was), 1400);
  };
  try {
    navigator.clipboard.writeText(text).then(
      () => done(true),
      () => done(false),
    );
  } catch {
    done(false);
  }
}

/** Fill the drawer with one candidate. */
export function renderCandidate(
  el: HTMLElement,
  c: Candidate,
  o: { handlers: DrawerHandlers; reducedMotion: boolean; red: string; hasNext: boolean },
): DrawerHandle {
  const k = KIND_TEXT[c.kind];
  const key = keyNumber(c);
  const caution = flagsSentence(c.flags);
  const grade = gradeOf(c);
  const open = votable(c);
  // A grade C's failed checks are in its grade line: don't repeat them as chips.
  const reasons = grade === "C" ? c.reasons.filter((r) => !isGate(r)) : c.reasons;
  // Evidence and labels first, then the contract's "check first" codes, marked as cautions.
  const chips = [
    ...c.labels.map((l) => ({ text: labelText(l), caution: false })),
    ...reasons.filter((r) => !isCaution(r)).map((r) => ({ text: reasonText(r), caution: false })),
    ...reasons.filter(isCaution).map((r) => ({ text: reasonText(r), caution: true })),
  ];
  const pos = formatRaDec(c);
  const scoreText = c.score != null && Number.isFinite(c.score) ? `, pipeline score ${c.score.toFixed(2)}` : "";
  el.innerHTML = `
    <header class="atl-dr-head">
      ${kindIcon(c.kind, 22)}
      <h2 class="atl-dr-title" id="atl-dr-title" tabindex="-1">${esc(k.name)}</h2>
      <button class="atl-dr-x" type="button" data-close aria-label="Close the preview">×</button>
    </header>
    <p class="atl-dr-meaning">${esc(k.meaning)}</p>
    ${c.why ? `<p class="atl-dr-why">${esc(c.why)}</p>` : ""}
    ${gradeLine(c)}
    ${
      key
        ? `<p class="atl-dr-key"><span class="atl-dr-num">${esc(key.value)}</span><span class="atl-dr-unit">${esc(key.unit)}</span></p>
           ${key.detail ? `<p class="atl-dr-keydetail">${esc(key.detail)}</p>` : ""}`
        : ""
    }
    ${
      chips.length
        ? `<ul class="atl-dr-chips">${chips
            .map((t) => (t.caution ? `<li class="is-caution"><span class="visually-hidden">Check first: </span>${esc(t.text)}</li>` : `<li>${esc(t.text)}</li>`))
            .join("")}</ul>`
        : ""
    }
    ${caution ? `<p class="atl-dr-caution">${esc(caution)}</p>` : ""}
    <div class="atl-dr-mini" data-mini></div>
    <dl class="atl-dr-facts">
      <div><dt>Position</dt><dd><span>${esc(pos)}</span> <button class="atl-dr-copy" type="button" data-copy>Copy</button></dd></div>
      <div><dt>Seen</dt><dd>${esc(seenText(c))}</dd></div>
      <div><dt>Detector</dt><dd>${esc(detectorText(c.det))}</dd></div>
      <div><dt>Known asteroid</dt><dd>${esc(nearestKnownText(c))}</dd></div>
      <div><dt>Catalogues</dt><dd>${esc(cataloguesText(c))}</dd></div>
      <div><dt>Zone</dt><dd>${esc(zoneName(c.zone))}${esc(scoreText)}</dd></div>
    </dl>
    <p class="atl-dr-votes" data-votes aria-live="polite">Counting votes…</p>
    <div class="atl-dr-actions">
      <a class="btn btn-primary" href="#/c/${encodeURIComponent(c.id)}" data-review${open ? "" : ` title="Its page: every check it failed. Grade C isn't put to a vote."`}>${open ? "Review it" : "See why"}</a>
      <button class="btn" type="button" data-blink title="${
        c.frames_url ? "Blink its prepared frames, full size" : "Build frames of this spot live from NASA's archive (up to 15 s)"
      }">Blink here</button>
      <button class="btn btn-ghost" type="button" data-next ${o.hasNext ? "" : "disabled"}>Next nearby</button>
    </div>
    <p class="atl-dr-id">${esc(c.id)}</p>
  `;
  const q = <T extends Element>(s: string) => el.querySelector(s) as T;
  const mini = q<HTMLDivElement>("[data-mini]");
  let blink: MiniBlinkHandle | null = null;
  let ctl: AbortController | null = null;
  let gone = false;

  q<HTMLButtonElement>("[data-close]").addEventListener("click", () => o.handlers.close());
  q<HTMLButtonElement>("[data-copy]").addEventListener("click", (ev) => copyText(`${c.ra.toFixed(6)} ${c.dec.toFixed(6)}`, ev.currentTarget as HTMLButtonElement));
  q<HTMLAnchorElement>("[data-review]").addEventListener("click", (ev) => {
    ev.preventDefault();
    o.handlers.review(c);
  });
  q<HTMLButtonElement>("[data-blink]").addEventListener("click", () => o.handlers.blink(c));
  q<HTMLButtonElement>("[data-next]").addEventListener("click", () => o.handlers.next(c));

  // Votes: read only. The vote store never rejects; counts may come from Firestore or this browser.
  getVoteStore()
    .then((s) => s.countsFor([c.id]))
    .then((m) => {
      if (gone) return;
      const counts = m.get(c.id);
      q<HTMLParagraphElement>("[data-votes]").textContent = !open && (!counts || total(counts) === 0)
        ? "Not put to a vote: grade C."
        : counts
          ? votesText(counts)
          : "Votes couldn't be counted.";
    })
    .catch(() => {
      if (!gone) q<HTMLParagraphElement>("[data-votes]").textContent = "Votes couldn't be counted right now.";
    });

  async function play(frames: FramesResponse, jsonUrl: string) {
    blink?.destroy();
    blink = await mountMiniBlink(mini, { candidate: c, frames, jsonUrl, paused: o.reducedMotion, red: o.red, signal: ctl?.signal });
  }

  function say(text: string, withButton: boolean) {
    mini.innerHTML = `<div class="atl-dr-mini-empty"><p>${esc(text)}</p>${
      withButton ? `<button class="btn" type="button" data-load>Load the blink</button>` : ""
    }</div>`;
    mini.querySelector<HTMLButtonElement>("[data-load]")?.addEventListener("click", loadLive);
  }

  async function loadStatic(url: string) {
    ctl = new AbortController();
    mini.innerHTML = `<div class="atl-dr-mini-empty"><p>Loading the frames…</p></div>`;
    try {
      const jsonUrl = candidateFramesUrl(url);
      const res = await fetch(jsonUrl, { signal: ctl.signal });
      if (!res.ok) throw new Error(String(res.status));
      const frames = (await res.json()) as FramesResponse;
      if (gone) return;
      await play(frames, jsonUrl);
    } catch (e) {
      if (gone || (e as Error).name === "AbortError") return;
      say("The prepared frames didn't load. Load them live from SPHEREx instead.", true);
    }
  }

  async function loadLive() {
    ctl?.abort();
    ctl = new AbortController();
    mini.innerHTML = `<div class="atl-dr-mini-empty"><p>Building frames from SPHEREx. This takes up to 15 s.</p></div>`;
    try {
      const loaded = await loadFrames({ ra: c.ra, dec: c.dec, clock: c.clock, fov: DEFAULT_FOV[c.clock], det: c.det }, ctl.signal);
      if (gone) return;
      await play(loaded.frames, loaded.jsonUrl);
    } catch (e) {
      if (gone || (e as Error).name === "AbortError") return;
      const msg = e instanceof Error && /only one usable frame/.test(e.message) ? e.message : "The live service didn't answer. Blink here opens the full blink page.";
      say(msg, true);
    }
  }

  if (c.frames_url) void loadStatic(c.frames_url);
  else say(`See this spot ${c.clock === "hours" ? "hours" : "months"} apart. The frames come live from NASA's archive.`, true);

  return {
    pause: () => blink?.pause(),
    destroy() {
      gone = true;
      ctl?.abort();
      blink?.destroy();
      el.innerHTML = "";
    },
  };
}

/** Fill the drawer with a featured target. */
export function renderTarget(el: HTMLElement, t: Target, o: { open(t: Target): void; close(): void }): DrawerHandle {
  el.innerHTML = `
    <header class="atl-dr-head">
      ${kindIcon("target", 22)}
      <h2 class="atl-dr-title" id="atl-dr-title" tabindex="-1">${esc(t.name)}</h2>
      <button class="atl-dr-x" type="button" data-close aria-label="Close the preview">×</button>
    </header>
    <p class="atl-dr-meaning">A featured target: a ${esc(t.kind)} with frames ready to blink.</p>
    <p class="atl-dr-why">${esc(t.look_for)}</p>
    <p class="atl-dr-keydetail">${esc(t.expected)}</p>
    <dl class="atl-dr-facts">
      <div><dt>Position</dt><dd>${esc(formatRaDec(t))}</dd></div>
      <div><dt>Clock</dt><dd>${t.clock === "hours" ? "Hours: exposures within one visit" : "Months: visits six months apart"}</dd></div>
      <div><dt>Detector</dt><dd>${esc(detectorText(t.det))}</dd></div>
    </dl>
    <div class="atl-dr-actions">
      <button class="btn btn-primary" type="button" data-open>Blink it</button>
    </div>
  `;
  el.querySelector<HTMLButtonElement>("[data-close]")!.addEventListener("click", () => o.close());
  el.querySelector<HTMLButtonElement>("[data-open]")!.addEventListener("click", () => o.open(t));
  return {
    pause() {},
    destroy() {
      el.innerHTML = "";
    },
  };
}
