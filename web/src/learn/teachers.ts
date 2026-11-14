// Teacher corner: a 45-minute class activity on Barnard's Star, printable. The answer key is
// filled from the same static data the Blink page shows, so the numbers always match the screen.
import { epochsFor, loadObject } from "../api/client";
import { formatMjd } from "../app/astro";
import { frameSet, target } from "./data";
import { exploreHref } from "./lessons";
import { term as t } from "./glossary";

/** Gaia DR3 proper motion of Barnard's Star, ″/yr (targets.json "expected"). */
export const BARNARD_PM = 10.39;

export function teachersHtml(): string {
  return `
  <header class="ln-head">
    <h1 id="ln-h" tabindex="-1">For teachers: Barnard's Star in 45 minutes</h1>
  </header>
  <div class="ln-teach-intro">
    <p class="ln-lede-p">Students measure how far the fastest star in our sky moved between SPHEREx visits and check the answer against ESA's Gaia. Ages 13 and up; one computer, tablet or phone per pair. No account, nothing to install.</p>
    <p class="ln-noprint"><button type="button" class="btn btn-primary ln-print">Print this plan</button> <a class="btn btn-ghost" href="#/tools">Tools and data</a></p>
  </div>

  <section class="ln-teach-sec" aria-labelledby="ln-t-plan">
    <h2 id="ln-t-plan">The plan</h2>
    <ol class="ln-plan">
      <li><span class="ln-time">0–5 min</span><div><strong>Hook.</strong> Tell the Pluto story (lesson 2): in 1930 Clyde Tombaugh found Pluto by ${t("blink", "blinking")} two photos taken six days apart. Ask: how could a star move?</div></li>
      <li><span class="ln-time">5–15 min</span><div><strong>Blink.</strong> Open <a href="${exploreHref("barnard")}">Barnard's Star</a> on the Blink page (Months clock, D2). Pairs describe what moves and which way. North is up, east is left. Expected answer: one bright star creeps up, north; nothing else moves.</div></li>
      <li><span class="ln-time">15–30 min</span><div><strong>Measure.</strong> Pause on visit 1 and on the last visit and take a screenshot or print of each. Mark the centre of the star's hatched hole on both and measure the distance with a ruler, using the plate's scale bar to turn millimetres into ${t("arcsecond", "arcseconds")}. Write down the two dates printed on the plate.</div></li>
      <li><span class="ln-time">30–40 min</span><div><strong>Compare with Gaia.</strong> Gaia measured Barnard's Star moving ${BARNARD_PM}″ a year (its ${t("proper motion")}). Work out the years between the two dates and Gaia's prediction: ${BARNARD_PM}″ × years. How close did you get? Compare with SkyShift's own measurement on the Blink page.</div></li>
      <li><span class="ln-time">40–45 min</span><div><strong>Wrap up.</strong> Why does this star move so much? (It is only 1.83 pc away, about 6 light years.) Why can't we see most stars move? Exit question: what would an asteroid do in the same blink?</div></li>
    </ol>
  </section>

  <section class="ln-teach-sec" aria-labelledby="ln-t-sheet">
    <h2 id="ln-t-sheet">Worksheet</h2>
    <table class="ln-sheet">
      <caption class="visually-hidden">Worksheet: dates, time between visits, measured and predicted shift</caption>
      <thead><tr><th scope="col">What</th><th scope="col">Your value</th><th scope="col">Unit</th></tr></thead>
      <tbody>
        <tr><th scope="row">Date of visit 1</th><td></td><td>day</td></tr>
        <tr><th scope="row">Date of the last visit</th><td></td><td>day</td></tr>
        <tr><th scope="row">Time between them</th><td></td><td>years</td></tr>
        <tr><th scope="row">Shift you measured</th><td></td><td>″</td></tr>
        <tr><th scope="row">Gaia's prediction (${BARNARD_PM}″ × years)</th><td></td><td>″</td></tr>
        <tr><th scope="row">Difference</th><td></td><td>″</td></tr>
      </tbody>
    </table>
    <p class="ln-small">1′ = 60″. One SPHEREx pixel is 6.15″; the Blink page's 5′ frames are resampled to 2.05″ per pixel.</p>
  </section>

  <section class="ln-teach-sec" aria-labelledby="ln-t-key">
    <h2 id="ln-t-key">Answer key</h2>
    <div class="ln-key" aria-live="polite"><p class="muted">Loading the numbers from the SPHEREx data…</p></div>
  </section>

  <section class="ln-teach-sec ln-noprint" aria-labelledby="ln-t-more">
    <h2 id="ln-t-more">Going further</h2>
    <ul class="ln-links">
      <li><a href="#/learn/1">The seven lessons</a>: ten minutes of reading before class.</li>
      <li><a href="#/learn/glossary">The glossary</a>: every word in one sentence.</li>
      <li><a href="#/tools">Tools and data</a>: downloads, batch coordinates and the API for older students.</li>
      <li><a href="#/review">The Review queue</a>: a class can vote on real objects together.</li>
    </ul>
  </section>`;
}

export interface KeyNumbers {
  first: number;
  last: number;
  years: number;
  predicted: number;
  measured: { shift: number; sigma: number; dtYr: number; gaia: number | null } | null;
}

/** Years between two MJDs and Gaia's proper-motion prediction for them. Pure, for tests. */
export function keyNumbers(first: number, last: number, measure: KeyNumbers["measured"]): KeyNumbers {
  const years = (last - first) / 365.25;
  return { first, last, years, predicted: BARNARD_PM * years, measured: measure };
}

export async function fillAnswerKey(root: HTMLElement, signal: AbortSignal): Promise<void> {
  const box = root.querySelector<HTMLElement>(".ln-key");
  if (!box) return;
  try {
    const tg = target("barnard")!;
    const [set, obj] = await Promise.all([
      frameSet("barnard", "months", 5, 2, signal),
      loadObject({ ra: tg.ra, dec: tg.dec, slug: tg.slug }, signal).catch(() => null),
    ]);
    if (signal.aborted) return;
    const eps = epochsFor(set.frames, 2);
    const m = obj?.measure;
    const k = keyNumbers(
      eps[0].mjd_mid,
      eps[eps.length - 1].mjd_mid,
      m ? { shift: m.shift_arcsec, sigma: m.sigma_arcsec, dtYr: m.dt_yr, gaia: m.predicted_arcsec } : null,
    );
    box.innerHTML = `
      <p>The Blink page prints visit 1 (D2) as ${formatMjd(k.first)} and the last visit as ${formatMjd(k.last)}: ${(k.last - k.first).toFixed(0)} days,
      or ${k.years.toFixed(2)} years. Gaia predicts ${BARNARD_PM} × ${k.years.toFixed(2)} ≈ <strong>${k.predicted.toFixed(1)}″</strong>, about
      ${(k.predicted / 6.15).toFixed(1)} SPHEREx pixels. A ruler measurement within 2–3″ of that is good work.</p>
      ${
        k.measured
          ? `<p>SkyShift's own measurement: <strong>${k.measured.shift.toFixed(2)}″ ± ${k.measured.sigma.toFixed(2)}″</strong> over ${k.measured.dtYr.toFixed(
              3,
            )} years${
              k.measured.gaia != null ? `, against Gaia's ${k.measured.gaia.toFixed(2)}″` : ""
            }. Its time span is shorter than the printed dates because it uses the middle of the photos actually stacked in each visit, and Gaia's number includes parallax: Earth's own motion shifts this star by up to about 1.1″.</p>`
          : ""
      }`;
  } catch {
    if (!signal.aborted)
      box.innerHTML = `<p>Couldn't load the numbers. Work them out from the dates on the Blink page: years = days ÷ 365.25, prediction = ${BARNARD_PM}″ × years.</p>`;
  }
}
