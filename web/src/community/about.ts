// The About page (#/about): what SkyShift is, the challenge and where each objective lives,
// the two clocks and the pipeline, honest limits, questions, data and credits, the team and AI.
// Acknowledgement texts are copied word for word from the README.
import { loadValidation } from "../api/client";
import type { ValidationResponse } from "../api/types";
import type { PageContext, PageHandle } from "../app/modules";
import { checkInfo } from "../score/checks";
import { CONSENSUS, CONSENSUS_PCT } from "./stats";

const html = String.raw;
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const SECTIONS = [
  ["ab-what", "What it is"],
  ["ab-challenge", "The challenge"],
  ["ab-how", "How it works"],
  ["ab-limits", "Honest limits"],
  ["ab-faq", "Questions"],
  ["ab-credits", "Data and credits"],
  ["ab-team", "Team and AI"],
] as const;

/** The challenge's objectives and considerations, and where each one is answered. */
export const CHALLENGE_ROWS: { asks: string; answer: string; where: [string, string][] }[] = [
  {
    asks: "Enter the coordinates of a patch of sky",
    answer: "The box at the top reads the challenge's own format, 08h55m10.831s −7d14m42.53s, and decimal degrees.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Show the images like a little video",
    answer: "Blink plays the frames in time order on two clocks, Months and Hours. Hold C to flip between two frames.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Test on Barnard's Star, Epsilon Indi B, WISE 0855, RW Aurigae and R Draconis",
    answer: "Each is one click under the box, with comet 3I/ATLAS and two asteroids. All five were found where predicted.",
    where: [["#/explore", "Blink"], ["#/results", "Results"]],
  },
  {
    asks: "Add or subtract images taken at different times",
    answer: "Difference shows each frame minus a reference; Stack adds frames together for a deeper view.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Let people choose the field of view",
    answer: "5′ or 15′ on the Months clock, 20′ or 60′ on the Hours clock. A larger field catches faster movers but moves more data.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Overlay other telescopes, such as Gaia or WISE",
    answer: "Gaia DR3 stars moved to each frame's date, predicted asteroid and comet positions, 2MASS and AllWISE sky layers, and CatWISE matches on every object to check.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Put coordinates in the URL, so the tool can be embedded",
    answer: "Every view lives in the address: ra, dec, clock, fov and det. Add embed=1 to drop the page header inside another site.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Show metadata such as contamination flags",
    answer: "Each frame lists its share of flagged and saturated pixels; objects near ghosts, halos or crosstalk carry a caution.",
    where: [["#/explore", "Blink"], ["#/candidates", "To check"]],
  },
  {
    asks: "Generate spectra on the fly",
    answer: "The object card measures the object in every cutout, 0.75–5 µm, coloured by visit, with water, methane and CO₂ bands marked.",
    where: [["#/explore", "Blink"]],
  },
  {
    asks: "Help as many people as possible pitch in",
    answer: `The whole sky with every object to check on it, short lessons, no account, a practice round, and votes that add up: at ${CONSENSUS.minVotes} votes and ${CONSENSUS_PCT}% agreement an object joins the list for astronomers as a candidate.`,
    where: [["#/", "Sky"], ["#/learn", "Learn"], ["#/review", "Review"], ["#/community", "Community"]],
  },
  {
    asks: "Stay easy to update as new data arrives",
    answer: "Frames for any coordinates come from IRSA's live archive search, so new SPHEREx releases show up without code changes (DR1 and QR3 today).",
    where: [["#/explore", "Blink"], ["#/tools", "Tools"]],
  },
];

/** Session positions inside one visit (days from its start) and exposures per session: a drawing, not data. */
const SESSIONS: [number, number][] = [
  [0.2, 3], [1.1, 2], [1.9, 4], [3.2, 1], [4.0, 3], [5.1, 5], [6.3, 2], [7.0, 3], [8.2, 4], [9.1, 2], [10.4, 3],
  [11.2, 1], [12.5, 4], [13.3, 3], [14.6, 2], [15.8, 5], [16.9, 3], [18.1, 2], [19.4, 4], [20.6, 3], [21.7, 2],
];
const VISIT_DAYS = 22;

function hoursTrack(): string {
  return SESSIONS.map(([d, k]) => {
    const ticks = Array.from({ length: k }, () => "<i></i>").join("");
    // 96% of the width, so the last session's ticks stay inside the track on a phone
    return `<span class="ab-session" style="left:${((d / VISIT_DAYS) * 96).toFixed(2)}%">${ticks}</span>`;
  }).join("");
}

function validationSummary(v: ValidationResponse | null): string {
  if (!v?.checks.length) {
    return html`<p>Every measurement is checked against independent catalogues, with the pass mark set before the run.
      <a href="#/results">See every check on Results</a>.</p>`;
  }
  const count = (s: string) => v.checks.filter((c) => c.status === s).length;
  const pass = count("pass");
  const partial = count("partial");
  const pending = count("pending");
  const fail = count("fail");
  const parts = [`${pass} of ${v.checks.length} checks pass`];
  if (partial) parts.push(`${partial} ${partial === 1 ? "is" : "are"} partial`);
  if (fail) parts.push(`${fail} failed`);
  if (pending) parts.push(`${pending} ${pending === 1 ? "is" : "are"} pending`);
  const last = parts.pop()!;
  const sentence = parts.length ? `${parts.join(", ")} and ${last}` : last;
  const pick = ["V1", "V2", "V4", "V5"]
    .map((id) => v.checks.find((c) => c.id === id))
    .filter((c): c is NonNullable<typeof c> => !!c && c.status !== "pending");
  // A check's scope (V5: which asteroids the recall covers) goes with its number.
  const scope = (id: string) => checkInfo(id)?.scope ?? "";
  const items = pick
    .map((c) => html`<div><dt>${esc(c.claim)}</dt><dd>${esc(c.headline)}${scope(c.id) ? ` ${esc(scope(c.id))}` : ""}</dd></div>`)
    .join("");
  return html`<p>Measured on real SPHEREx data (${esc(v.data_release)}): ${esc(sentence)}. Failing and partial
      results stay on the page with their numbers.</p>
    ${items ? html`<dl class="ab-checks">${items}</dl>` : ""}
    <p><a href="#/results">See every check on Results</a>.</p>`;
}

export function aboutHtml(): string {
  const rows = CHALLENGE_ROWS.map(
    (r) => html`<tr>
      <th scope="row" data-label="The challenge asks">${esc(r.asks)}</th>
      <td data-label="In SkyShift">${esc(r.answer)}</td>
      <td data-label="Where" class="ab-where">${r.where.map(([href, name]) => html`<a href="${href}">${name}</a>`).join(" ")}</td>
    </tr>`,
  ).join("");
  const nav = SECTIONS.map(([id, name]) => html`<li><a href="#/about" data-jump="${id}">${name}</a></li>`).join("");

  return html`<div class="ab">
    <nav class="ab-nav" aria-label="On this page"><ol>${nav}</ol></nav>
    <article class="ab-body">
      <header class="ab-head" id="ab-what" tabindex="-1">
        <h1 class="ab-title">About SkyShift</h1>
        <p class="ab-lede">SkyShift is a public tool for finding what moves and changes in NASA's SPHEREx infrared sky.
          SPHEREx photographs the whole sky every six months in 102 colours. SkyShift plays any spot of it as a short
          blink, the way Pluto was found on two photographic plates, and measures what moved against ESA's Gaia and
          known asteroid orbits. A pipeline reads SPHEREx photos and lists objects to check, most of them detector
          artefacts; people blink them and vote; the ones the community agrees on go to astronomers as candidates. Four people built it in one day for NASA Space
          Apps 2026, challenge "Planet X and SPHEREx".</p>
      </header>

      <section class="ab-sec" aria-labelledby="ab-challenge-h">
        <h2 id="ab-challenge-h"><span id="ab-challenge" tabindex="-1">The challenge</span></h2>
        <p>NASA asked for a public web tool that shows SPHEREx images of any patch of sky so that anyone can see how they
          change over time, and that helps as many people as possible pitch in to make discoveries. Here is where each
          objective and suggestion is answered.</p>
        <div class="panel ab-table-wrap">
          <table class="ab-table">
            <thead><tr><th scope="col">The challenge asks</th><th scope="col">In SkyShift</th><th scope="col">Where</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>

      <section class="ab-sec" aria-labelledby="ab-how-h">
        <h2 id="ab-how-h"><span id="ab-how" tabindex="-1">How it works</span></h2>
        <h3>Two clocks</h3>
        <p>SPHEREx sees each spot of sky in bursts: a visit of 2–4 weeks every six months. Slow objects need frames
          months apart; fast ones need frames hours apart. One blink can't do both, so SkyShift has two clocks.</p>
        <figure class="ab-clocks">
          <div class="ab-drawing" aria-hidden="true">
            <p class="ab-clock-name ab-name-months"><b>Months clock</b><span>visits six months apart</span></p>
            <div class="ab-lane ab-lane-months">
              <ol class="ab-scale ab-scale-3"><li>Visit 1</li><li>Visit 2</li><li>Visit 3</li></ol>
              <div class="ab-track">
                <span class="ab-visit" style="left:0%"></span>
                <span class="ab-visit" style="left:47.5%"></span>
                <span class="ab-visit" style="left:95%"></span>
              </div>
            </div>
            <svg class="ab-zoom" viewBox="0 0 100 20" preserveAspectRatio="none" focusable="false">
              <path d="M47.5 0 L0 20 M52.5 0 L100 20" />
            </svg>
            <p class="ab-clock-name ab-name-hours"><b>Hours clock</b><span>exposures inside one visit</span></p>
            <div class="ab-lane ab-lane-hours">
              <div class="ab-track">${hoursTrack()}</div>
              <ol class="ab-scale ab-scale-2"><li>Day 0</li><li>Day 14–28</li></ol>
            </div>
          </div>
          <figcaption class="ab-clock-texts">
            <p><b>Months.</b> One stacked frame per visit and detector, six months apart. Nearby stars and brown dwarfs
              step across the field; the stack wipes out asteroids and cosmic rays, which sit in one place for only a
              moment.</p>
            <p><b>Hours.</b> Single 113-second exposures in time order inside one visit: about 21 sessions of 1–5
              exposures, 2.1 minutes apart. Asteroids and comets cross the field. A real mover shows up in two exposures
              of one session, or in two detectors exposed at the same moment.</p>
          </figcaption>
        </figure>

        <h3>From photos to objects to check</h3>
        <ol class="ab-steps">
          <li><b class="ab-step-n">990</b><span class="ab-step-name">photos</span>
            <span class="ab-step-text">Each read once from NASA's public copy, about 56 MB, and never stored.</span></li>
          <li><b class="ab-step-n">21 million</b><span class="ab-step-name">dots</span>
            <span class="ab-step-text">Every point of light measured: position, brightness, shape and quality flags.</span></li>
          <li><b class="ab-step-n">873</b><span class="ab-step-name">movers</span>
            <span class="ab-step-text">Dots that change place between two exposures of a session, or between two detectors at once.</span></li>
          <li><b class="ab-step-n">27</b><span class="ab-step-name">suspects</span>
            <span class="ab-step-text">Movers left after removing known asteroids, flagged artefacts, and stars Gaia and CatWISE know.</span></li>
        </ol>
        <p class="ab-fine">Numbers from the 12 deg² pilot, 14 November 2026. On the Months clock, motions between visits
          add moving stars (167 in the pilot). Suspects, moving stars and odd spectra together make the list of objects
          to check on <a href="#/candidates">To check</a>. In the hunt zone that list holds 228 fast movers,
          1,008 moving stars and 97 odd spectra. Most are not real: see Honest limits.</p>

        <h3>How we know it works</h3>
        <div class="ab-validation">${validationSummary(null)}</div>
      </section>

      <section class="ab-sec" aria-labelledby="ab-limits-h">
        <h2 id="ab-limits-h"><span id="ab-limits" tabindex="-1">Honest limits</span></h2>
        <dl class="ab-limits">
          <div><dt>No SPHEREx parallax</dt><dd>Between two visits, parallax moves even Barnard's Star by about 1.1″ at
            most, close to our errors. The object card shows Gaia's distance instead.</dd></div>
          <div><dt>Bright stars saturate</dt><dd>SPHEREx saturates near AB magnitude 9–11, depending on the detector.
            Barnard's Star is saturated in 100 of its 101 spectrum points. Its shift is measured on the star's wings,
            and saturated cores are marked.</dd></div>
          <div><dt>Pixels are 6″ wide</dt><dd>One pixel is 6.15″ and a star's image is 7.5″ across, so close pairs blend:
            Epsilon Indi Ba and Bb show as one dot, and crowded fields near the Milky Way confuse matches.</dd></div>
          <div><dt>Two visits so far</dt><dd>Most of the sky has been seen twice, so motions are good to about 0.15–0.2″
            per year. Slower stars need a third visit.</dd></div>
          <div><dt>Most objects to check are artefacts</dt><dd>A check of the hunt zone's 228 fast movers found 211
            resting on just two dots in one session, which can't show motion by itself. About 41% sit on five recurring
            bad detector pixels, 31% at a detector edge and 10% look like cosmic rays; most of the rest have a source in
            deeper catalogues. About 4 are unexplained, and none of them shows motion. The 8 brightest by S/N are all
            artefacts.</dd></div>
          <div><dt>Asteroid recall covers fast movers only</dt><dd>In the hunt zone, 90% of sightings (413 of 457
            sessions) of 30 distinct known asteroids moving 10–90″/h, brighter than V 16, were re-found. Slower movers
            haven't been checked against real objects.</dd></div>
          <div><dt>Candidates are leads, not discoveries</dt><dd>The pipeline and the votes point at something worth a
            look. Astronomers confirm it with other data before anything is called new.</dd></div>
          <div><dt>Most brown dwarfs move too little in a year</dt><dd>Of the 24 known brown dwarfs faster than 1″ a year
            that we measured, 14 move only 0.5–1.6″ between visits, against an error of 0.4–0.9″: too little to show a direction. Of the
            other 10, 8 went the way Gaia predicts. A star as bright or brighter within 12″ can pull a faint dot's
            position.</dd></div>
        </dl>
      </section>

      <section class="ab-sec" aria-labelledby="ab-faq-h">
        <h2 id="ab-faq-h"><span id="ab-faq" tabindex="-1">Questions</span></h2>
        <div class="ab-faq">
          <h3>Do I need an account?</h3>
          <p>No. Everything works without signing in. The first time you vote, your browser makes a random id, so each
            object gets one vote per browser.</p>
          <h3>What does "mover" mean?</h3>
          <p>A dot that changes place between frames. On the Hours clock a mover is usually an asteroid or a comet; on
            the Months clock, a nearby star or brown dwarf. "Changing" means it stays put while its brightness changes,
            and "junk" is an artefact: a cosmic ray, a ghost, an image edge. <a href="#/learn">Learn</a> has short lessons
            with real examples, and a glossary.</p>
          <h3>Why are the images negative?</h3>
          <p>Frames are shown as plate negatives, dark stars on light glass, the way astronomers blinked photographic
            plates. Faint dots are easier to see that way. Positive switches to bright stars on a dark sky.</p>
          <h3>How are votes used?</h3>
          <p>They are counted per object, and every vote weighs the same. At ${CONSENSUS.minVotes} votes with
            ${CONSENSUS_PCT}% agreeing, an object reaches consensus; movers and changing sources join the list
            for astronomers, which anyone can download from <a href="#/community">Community</a>.</p>
          <h3>Is my data stored?</h3>
          <p>Each vote stores four things: the object's id, your answer, the server's time and your browser's random id.
            No name, email or account. A nickname, if you set one, stays in your browser. Nothing is edited or
            deleted: changing or resetting your answer adds a new vote, and only your latest one counts.</p>
        </div>
      </section>

      <section class="ab-sec" aria-labelledby="ab-credits-h">
        <h2 id="ab-credits-h"><span id="ab-credits" tabindex="-1">Data and credits</span></h2>
        <ul class="ab-credits">
          <li><b>NASA SPHEREx</b> Level 2 spectral images, DR1 and QR3, from NASA/IPAC IRSA: image search, cutouts,
            and the public copy at s3://nasa-irsa-spherex. Also the IRSA SPHEREx all-sky cube (HiPS),
            IRSA's Moving Object Search Tool (MOST) and IRSA's SPHEREx table for comet 3I/ATLAS.</li>
          <li><b>2MASS</b> and <b>AllWISE</b> sky layers (HiPS) and the 2MASS and CatWISE2020 catalogues, served by IRSA.</li>
          <li><b>ESA Gaia DR3</b> through the ESA Gaia Archive (partner data).</li>
          <li><b>Minor Planet Center</b> orbits for known asteroids and comets, propagated with <b>kete</b> (BSD-3-Clause, first written at Caltech/IPAC).</li>
          <li><b>SDSS DR17</b> spectroscopic redshifts and the <b>EAZY</b> templates (galaxy distances, V10);
            the NEATM thermal model (Harris 1998) for asteroid sizes (V11).</li>
          <li><b>Aladin Lite 3.8.2</b> (CDS, GPL-3.0), loaded from jsDelivr at run time and not bundled, showing IRSA HiPS layers.</li>
          <li><b>Google Cloud</b> (Cloud Run, Cloud Storage, Compute Engine) and <b>Firebase</b> (Hosting, Firestore).</li>
          <li>Libraries: astropy, astroquery, pyvo, sep, reproject, numpy, scipy, pandas, pyarrow, FastAPI, uvicorn, Vite, TypeScript, Vitest.</li>
          <li>Modelled on WiseView and the Backyard Worlds: Planet 9 project.</li>
        </ul>

        <p>Downloads, the API and a citation to copy are on <a href="#/tools">Tools and data</a>.</p>

        <h3>Acknowledgements</h3>
        <div class="ab-ack">
          <p>This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.</p>
          <p>This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space Administration and operated by the California Institute of Technology.</p>
          <p>This work has made use of data from the European Space Agency (ESA) mission Gaia (<a href="https://www.cosmos.esa.int/gaia" target="_blank" rel="noopener">https://www.cosmos.esa.int/gaia</a>), processed by the Gaia Data Processing and Analysis Consortium (DPAC, <a href="https://www.cosmos.esa.int/web/gaia/dpac/consortium" target="_blank" rel="noopener">https://www.cosmos.esa.int/web/gaia/dpac/consortium</a>). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement.</p>
        </div>
        <dl class="ab-refs">
          <div><dt>SPHEREx data sets (IRSA)</dt><dd>
            DR1 Spectral Images, <a href="https://doi.org/10.26131/IRSA678" target="_blank" rel="noopener">doi:10.26131/IRSA678</a>;
            QR3 Spectral Images, <a href="https://doi.org/10.26131/IRSA662" target="_blank" rel="noopener">doi:10.26131/IRSA662</a>;
            Spectral All-Sky Cube, <a href="https://doi.org/10.26131/IRSA679" target="_blank" rel="noopener">doi:10.26131/IRSA679</a>.</dd></div>
          <div><dt>SPHEREx canonical papers</dt><dd>As listed on <a href="https://irsa.ipac.caltech.edu/Missions/spherex.html" target="_blank" rel="noopener">IRSA's SPHEREx mission page</a>:
            <ul>
              <li>Mission: <a href="https://ui.adsabs.harvard.edu/abs/2026ApJ...999..139B/abstract" target="_blank" rel="noopener">Bock et al. 2026, ApJ 999, 139</a></li>
              <li>Pipeline: <a href="https://ui.adsabs.harvard.edu/abs/2026ApJS..285...67A/abstract" target="_blank" rel="noopener">Akeson et al. 2026, ApJS 285, 67</a></li>
              <li>Instrument and calibration: <a href="https://ui.adsabs.harvard.edu/abs/2026arXiv260329835K/abstract" target="_blank" rel="noopener">Korngut et al. 2026, arXiv:2603.29835</a></li>
              <li>Sky simulator: <a href="https://ui.adsabs.harvard.edu/abs/2025ApJS..281...10C/abstract" target="_blank" rel="noopener">Crill et al. 2025, ApJS 281, 10</a></li>
            </ul></dd></div>
          <div><dt>Gaia</dt><dd>Gaia Collaboration, Prusti et al. 2016, A&amp;A 595, A1 (the mission); Gaia Collaboration,
            Vallenari et al. 2023, A&amp;A 674, A1 (Data Release 3).</dd></div>
        </dl>
      </section>

      <section class="ab-sec" aria-labelledby="ab-team-h">
        <h2 id="ab-team-h"><span id="ab-team" tabindex="-1">Team and AI</span></h2>
        <p>Four people at one local event. Roles, not names:</p>
        <dl class="ab-team">
          <div><dt>P1</dt><dd>Lead, science and pipeline: shared science code, the pilot, validation (V1–V11).</dd></div>
          <div><dt>P2</dt><dd>Server and deploys: image search, cutouts, stacks, the live API, static featured targets, the 3I/ATLAS movie.</dd></div>
          <div><dt>P3</dt><dd>Sky view and cards: coordinate box, Aladin sky view, object card, spectrum, scoreboard.</dd></div>
          <div><dt>P4</dt><dd>Blink and votes: the blink player and both clocks, flag and vote, CSV, the pitch.</dd></div>
        </dl>
        <h3>Use of AI</h3>
        <p>We used Claude (Anthropic) through Claude Code as a coding and research assistant. It wrote most of the code
          from our instructions, helped us read NASA's documentation, and drafted text, including this page. Commits made
          with it carry a Co-Authored-By line.</p>
        <p>The idea is ours: a WiseView-style blink for SPHEREx, with a spectrum and a public flag-and-vote step. We chose
          the two clocks, the five test objects, the scope cuts and the gates. We set the pass mark for every check before
          running it, read the results, and kept the failing and partial ones on the scoreboard. We reviewed and merged
          every change, ran the tests, and compared the numbers on the site with Gaia, IRSA MOST and the catalogues.</p>
        <p>Before 09:00 on Saturday 14 November 2026 we prepared only a cloud account, planning notes and repository
          configuration. All product code was written after 09:00 on Saturday. Every screenshot and video we publish is a
          capture of the running site on real SPHEREx data; none is AI-generated, and none uses NASA logos.</p>
        <p class="ab-fine">Code: <a href="https://github.com/cucuradu/skyshift" target="_blank" rel="noopener">github.com/cucuradu/skyshift</a>,
          Apache-2.0. Outside libraries and data keep their own licences.</p>
      </section>
    </article>
  </div>`;
}

export function mountAboutWith(el: HTMLElement, _ctx: PageContext, opts: { validation?: () => Promise<ValidationResponse | null> } = {}): PageHandle & { ready(): Promise<void> } {
  el.innerHTML = aboutHtml();
  const box = el.querySelector<HTMLDivElement>(".ab-validation")!;
  const load = opts.validation ?? (() => loadValidation());
  const ready = load()
    .then((v) => {
      box.innerHTML = validationSummary(v);
    })
    .catch(() => {});

  const reduce = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  function onClick(ev: MouseEvent) {
    const a = (ev.target as Element).closest<HTMLAnchorElement>("a[data-jump]");
    if (!a || !el.contains(a)) return;
    ev.preventDefault();
    const target = el.querySelector<HTMLElement>(`#${a.dataset.jump}`);
    if (!target) return;
    target.scrollIntoView({ behavior: reduce() ? "auto" : "smooth", block: "start" });
    target.focus({ preventScroll: true });
  }
  el.addEventListener("click", onClick);

  return {
    show() {},
    destroy() {
      el.removeEventListener("click", onClick);
      el.innerHTML = "";
    },
    ready: () => ready,
  };
}
