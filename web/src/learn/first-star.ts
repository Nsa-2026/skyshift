import "./first-star.css";
import { formatMjd } from "../app/astro";
import { clueRegion, type Point } from "../play/hunt-model";
import { cropAround } from "./data";
import { esc } from "./glossary";
import {
  ASTROMETRY_ARTICLE, HST_FIELD, SPHEREX_WISE_VIEW, WISE_DISCOVERY, WISE_NAME, firstStarPictures,
  loadFirstStar, mastImage, type FirstStarData, type StarTool,
} from "./first-star-data";
import { drawFirstStarPatch, mountFirstStarField } from "./first-star-field";
import { FIRST_STAR_STEPS, FIRST_STAR_TITLE } from "./first-star-steps";

const STEP_COUNT = FIRST_STAR_STEPS.length + 1;
const MATCH_RADIUS_PIXELS = 2.25;

export function mountFirstStar(host: HTMLElement, signal: AbortSignal) {
  host.innerHTML = `<article class="first-star" aria-labelledby="ln-h">
    <a class="first-star-back" href="#/learn">← Learn</a>
    <h1 id="ln-h" tabindex="-1">${FIRST_STAR_TITLE}</h1>
    <p class="first-star-intro">Follow a faint point across three real Hubble images. Find out what you tracked at the end.</p>
    <div class="first-star-body"><p role="status">Loading three Hubble visits…</p></div>
  </article>`;
  const body = host.querySelector<HTMLElement>(".first-star-body")!;

  async function load() {
    try {
      const data = await loadFirstStar(signal);
      if (!signal.aborted) runGuide(body, data, signal);
    } catch (error) {
      if (signal.aborted) return;
      console.warn("Learn: first star observations failed to load", error);
      body.innerHTML = '<p role="alert">Couldn’t load the Hubble observations.</p><button type="button" class="btn btn-primary">Try again</button>';
      body.querySelector("button")!.onclick = () => {
        body.innerHTML = '<p role="status">Loading three Hubble visits…</p>';
        void load();
      };
    }
  }

  void load();
}

function runGuide(body: HTMLElement, data: FirstStarData, signal: AbortSignal) {
  const { width, height, observations, arcsecPerPixel, program } = HST_FIELD;
  const size: [number, number] = [width, height];
  const world = { width, height };
  const fieldArcsec = (width * arcsecPerPixel).toFixed(1);
  const observedShifts = data.positions.slice(1).map((position, i) => {
    const back = data.positions[i];
    return (Math.hypot(position.x - back.x, position.y - back.y) * arcsecPerPixel).toFixed(1);
  });
  const visitMidpoint = (i: number) => (observations[i].startMjd + observations[i].endMjd) / 2;
  const visitDate = (i: number) => formatMjd(visitMidpoint(i));
  const visitCutout = (i: number) => `<figure>
    <canvas width="144" height="144" role="img" aria-label="Hubble detail from Visit ${i + 1}"></canvas>
    <figcaption><strong>Visit ${i + 1}</strong><br>${visitDate(i)}</figcaption>
  </figure>`;
  body.innerHTML = `
    <section class="first-star-guide">
      <header class="first-star-instruction">
        <p class="first-star-progress"></p>
        <h2 class="first-star-prompt" tabindex="-1"></h2>
        <div class="first-star-discovery" hidden>
          <h3>Meet ${esc(WISE_NAME)}.</h3>
          <p>You tracked a cold brown dwarf across three Hubble visits. In these images it moved about ${observedShifts[0]}″, then another ${observedShifts[1]}″, passing close to a brighter point.</p>
          <p><a href="${WISE_DISCOVERY}">See NASA’s discovery animation</a></p>
          <p><a href="${SPHEREX_WISE_VIEW}">Find it at longer wavelengths in the SPHEREx Viewer</a></p>
        </div>
      </header>
      <section class="first-star-comparison" aria-labelledby="first-star-comparison-heading" hidden>
        <h3 id="first-star-comparison-heading">Same patch, two visits</h3>
        <div class="first-star-cutout-pair">${visitCutout(0)}${visitCutout(1)}</div>
        <div class="first-star-difference-explanation" hidden>
          <p>At each matching spot, SkyShift subtracts the infrared light measured in Visit 1 from the light measured in Visit 2.</p>
          <div class="first-star-difference-equation">
            ${visitCutout(1)}
            <span aria-hidden="true">−</span>
            ${visitCutout(0)}
            <span aria-hidden="true">=</span>
            <figure>
              <canvas width="144" height="144" role="img" aria-label="Difference cutout showing Visit 2 minus Visit 1"></canvas>
              <figcaption><strong>Difference</strong></figcaption>
            </figure>
          </div>
          <ul class="first-star-difference-key" aria-label="Difference colour key">
            <li><span class="first-star-swatch is-positive" aria-hidden="true">+</span><strong>Red</strong> · More light in Visit 2</li>
            <li><span class="first-star-swatch is-negative" aria-hidden="true">−</span><strong>Blue</strong> · Less light in Visit 2</li>
            <li><span class="first-star-swatch is-neutral" aria-hidden="true">≈</span><strong>Gray</strong> · Little or no clear change</li>
          </ul>
          <p class="first-star-difference-note">Bright stars can leave coloured streaks when the telescope turns. Use the two marks and the original images to check the shift.</p>
        </div>
      </section>
      <figure class="first-star-observation" aria-labelledby="first-star-caption">
        <div class="first-star-frame">
          <canvas tabindex="0" aria-label="NASA/ESA Hubble infrared star field. Use arrow keys to pan, plus or minus to zoom, and Enter to mark the crosshair."></canvas>
          <span class="first-star-active-visit" aria-hidden="true"></span>
          <span class="first-star-compass" aria-hidden="true">N ↑ · E ←</span>
          <div class="first-star-zoom" role="group" aria-label="Image zoom">
            <button class="btn" type="button" data-control="zoom-in" aria-label="Zoom in">+</button>
            <button class="btn" type="button" data-control="zoom-out" aria-label="Zoom out">−</button>
            <button class="btn" type="button" data-control="reset-view">Full field</button>
          </div>
        </div>
        <div class="first-star-toolbar">
          <div class="first-star-visits" role="group" aria-label="Observation visits">
            ${data.originals.map((_, i) => `<button type="button" class="btn" data-visit="${i}" aria-pressed="false">Visit ${i + 1}</button>`).join("")}
          </div>
          <div class="first-star-filters" role="group" aria-label="Viewer filters">
            <span>Add filters</span>
            <button type="button" class="btn" data-tool="original" aria-pressed="true">Original</button>
            <button type="button" class="btn" data-tool="contrast" aria-pressed="false">High contrast</button>
            <button type="button" class="btn" data-tool="difference" aria-pressed="false">Difference</button>
          </div>
        </div>
        <figcaption id="first-star-caption"></figcaption>
        <details class="first-star-provenance">
          <summary>Observation data and processing</summary>
          <p>NASA/ESA Hubble · WFC3/IR · F110W (near 1.15 µm) · MAST programme ${program}. The ${fieldArcsec}″ cutouts share a north-up grid of ${arcsecPerPixel}″ per pixel. SkyShift used the calibrated drizzle products, aligned stationary field sources and subtracted each cutout’s median sky background.</p>
          <p id="first-star-processing"></p>
          <div class="first-star-archive" hidden>
            <p>The green trail connects the positions measured in those three visits, using published coordinates as references.</p>
            <p><a href="${ASTROMETRY_ARTICLE}">Published astrometry and Hubble observations</a></p>
            <ul>${observations.map((o, i) => `<li><a href="${mastImage(o.filename)}">Visit ${i + 1} · ${esc(o.filename)}</a> · ${visitDate(i)} UTC</li>`).join("")}</ul>
          </div>
        </details>
      </figure>
      <div class="first-star-actions">
        <p class="first-star-feedback" role="status"></p>
        <button type="button" class="btn btn-primary" data-control="continue"></button>
        <div class="first-star-secondary">
          <button type="button" class="btn btn-ghost" data-control="undo" hidden>Undo landmark</button>
          <button type="button" class="btn btn-ghost" data-control="focus" hidden>Zoom to my marks</button>
          <button type="button" class="btn btn-ghost" data-control="hint" hidden>Show a hint</button>
        </div>
      </div>
    </section>`;
  const $ = <T extends HTMLElement = HTMLElement>(selector: string) => body.querySelector<T>(selector)!;
  const buttons = (selector: string) => [...body.querySelectorAll<HTMLButtonElement>(selector)];
  const control = (name: string) => $<HTMLButtonElement>(`[data-control="${name}"]`);
  const canvas = $<HTMLCanvasElement>(".first-star-frame canvas");
  const guide = $(".first-star-guide");
  const comparison = $(".first-star-comparison");
  const comparisonHeading = $("#first-star-comparison-heading");
  const originalComparison = $(".first-star-cutout-pair");
  const differenceExplanation = $(".first-star-difference-explanation");
  const originalCutouts = [...originalComparison.querySelectorAll("canvas")];
  const differenceCutouts = [...differenceExplanation.querySelectorAll("canvas")];
  const pictures = firstStarPictures(data, canvas);
  const prompt = $(".first-star-prompt");
  const feedback = $(".first-star-feedback");
  const next = control("continue");
  const toolButtons = buttons("[data-tool]");
  const visitButtons = buttons("[data-visit]");
  let step = 0;
  let visit = 0;
  let tool: StarTool = "original";
  let landmarks: Point[] = [];
  let marks: (Point | undefined)[] = [];
  let retryVisit: number | null = null;
  let shownContrast = false;
  let seenVisitTwo = false;
  let hints = 0;
  let revealed = false;
  let revealTimer: ReturnType<typeof setTimeout> | undefined;

  const stage = () => step === FIRST_STAR_STEPS.length ? "found" : FIRST_STAR_STEPS[step].id;
  const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
  const landmarkPatch = () => cropAround(landmarks, size, 9, 48);
  const candidatePatch = () => cropAround(marks.slice(0, 2).filter((mark): mark is Point => !!mark), size, 14, 56);
  const field = mountFirstStarField(canvas, world, () => {
    const current = stage();
    const landmarkMarks = landmarks.map((at, i) => ({ at, kind: "landmark" as const, label: String(i + 1) }));
    const showLandmarks = current === "landmarks" || current === "contrast" || current === "next-visit" ||
      current === "compare" || current === "verify";
    const showPatch = current === "mark" || current === "close-look" || current === "difference" || current === "originals";
    const clue = hints && current !== "found" ? clueRegion(data.positions, world, hints) : undefined;
    const visibleMarks = tool === "difference" ? marks.slice(0, 2) : [marks[visit]];
    return {
      image: pictures[tool][visit],
      marks: revealed
        ? data.positions.map((at, i) => ({ at, kind: "archive" as const, label: `Visit ${i + 1}` }))
        : [
          ...landmarkMarks,
          ...visibleMarks.flatMap((at, i) => at ? [{
            at, kind: "candidate" as const, label: tool === "difference" ? `Visit ${i + 1} mark` : "Your mark",
          }] : []),
        ],
      trail: revealed ? data.positions : undefined,
      guides: [
        ...(showLandmarks && landmarks.length ? [{
          patch: landmarkPatch(), label: "Your landmarks", kind: "landmarks" as const,
        }] : []),
        ...(showPatch && marks.some(Boolean) ? [{
          patch: candidatePatch(), label: "Compare this patch", kind: "compare" as const,
        }] : []),
        ...(clue ? [{
          patch: { x: clue.x, y: clue.y, w: clue.width, h: clue.height },
          label: hints === 1 ? "Search here" : "Look closer",
          kind: "hint" as const,
        }] : []),
      ],
    };
  }, selectPoint);

  signal.addEventListener("abort", () => { field.destroy(); clearTimeout(revealTimer); }, { once: true });

  function ready() {
    switch (stage()) {
      case "landmarks": return landmarks.length === 3;
      case "contrast": return shownContrast;
      case "next-visit": return seenVisitTwo;
      case "mark": return !!marks[visit];
      case "close-look": return !!marks[0] && !!marks[1];
      case "difference": return tool === "difference";
      case "originals": return tool !== "difference";
      case "verify": return visit === 2 && !!marks[2];
      case "found": return revealed;
      default: return true;
    }
  }

  function render() {
    const current = stage();
    const found = current === "found";
    const maxVisit = step < 3 ? 0 : current === "verify" || found ? 2 : 1;
    $(".first-star-progress").textContent = `Step ${step + 1} of ${STEP_COUNT}`;
    const text = found ? "Congratulations! You found your first star." : FIRST_STAR_STEPS[step].prompt;
    if (prompt.textContent !== text) prompt.textContent = text;
    next.textContent = found ? "Start again" : FIRST_STAR_STEPS[step].action;
    const canContinue = ready();
    next.disabled = !canContinue;
    next.hidden = found && !revealed;
    $(".first-star-actions").classList.toggle("is-complete", found);
    control("undo").hidden = current !== "landmarks" || !landmarks.length;
    control("focus").hidden = current !== "close-look";
    const hint = control("hint");
    hint.hidden = step < 4 || found || hints >= 2;
    hint.textContent = hints === 1 ? "Narrow the search" : "Show a hint";
    const filtersAvailable = step >= 2 && !found;
    const differenceAvailable = current === "difference" || current === "originals";
    $(".first-star-filters").hidden = !filtersAvailable;
    const suggestedTool = current === "contrast" ? "contrast" : current === "difference" ? "difference"
      : current === "originals" ? "original" : null;
    toolButtons.forEach(button => {
      const mode = button.dataset.tool;
      button.hidden = mode === "difference" && !differenceAvailable;
      button.setAttribute("aria-pressed", String(mode === tool));
      button.classList.toggle("is-suggested", !canContinue && mode === suggestedTool);
    });
    visitButtons.forEach((button, i) => {
      button.hidden = i > maxVisit;
      button.disabled = found || (tool === "difference" && i === 0);
      button.setAttribute("aria-pressed", String(i === visit));
      button.classList.toggle("is-suggested",
        (current === "next-visit" && !seenVisitTwo && i === 1) ||
        (current === "verify" && (retryVisit ?? 2) === i && visit !== i));
    });
    $(".first-star-active-visit").textContent = tool === "difference"
      ? "Visit 2 − Visit 1"
      : `Visit ${visit + 1} · ${visitDate(visit)}`;
    const showingDifference = tool === "difference";
    const comparingCandidate = current === "close-look" || current === "originals" || showingDifference;
    const patch = current === "compare" ? landmarkPatch()
      : comparingCandidate && marks.some(Boolean) ? candidatePatch() : undefined;
    comparison.hidden = !patch;
    guide.classList.toggle("has-comparison", !!patch);
    if (patch) {
      comparisonHeading.textContent = showingDifference ? "How the Difference image is made" : "Same patch, two visits";
      originalComparison.hidden = showingDifference;
      differenceExplanation.hidden = !showingDifference;
      if (showingDifference) {
        const images = [pictures.original[1], pictures.original[0], pictures.difference[1]];
        differenceCutouts.forEach((detail, i) => drawFirstStarPatch(detail, images[i], patch));
      } else {
        const images = pictures[tool];
        originalCutouts.forEach((detail, i) => drawFirstStarPatch(detail, images[i], patch, marks[i]));
      }
    }
    const dateLine = `Visit ${visit + 1} · ${visitDate(visit)} UTC`;
    const source = "NASA/ESA Hubble · WFC3/IR F110W";
    const note = showingDifference ? ` · Difference from Visit 1 (${visitDate(0)} UTC)`
      : tool === "contrast" ? " · High contrast" : "";
    $("#first-star-caption").textContent = `${dateLine} · ${source}${note}`;
    const observed = observations[visit];
    const exposureInfo = `The exposure midpoint is ${formatMjd(visitMidpoint(visit), true)}. Integration time was ${Math.round(observed.exposureSeconds)} seconds. MAST calibrated this product with CALWF3 ${observed.calibration.split(" ")[0]}.`;
    const processing = showingDifference
      ? "SkyShift smooths both calibrated images at Hubble’s resolution and subtracts Visit 1 pixel by pixel. The colour scale mutes differences near the estimated noise level."
      : tool === "contrast"
        ? "High contrast smooths the measured pixels at Hubble’s resolution and uses the same faint-detail stretch on every date."
        : "The original views use the same display stretch on every date.";
    $("#first-star-processing").textContent = `${exposureInfo} ${processing}`;
    $(".first-star-discovery").hidden = !revealed;
    $(".first-star-archive").hidden = !revealed;
    field.draw();
  }

  function selectPoint(at: Point) {
    switch (stage()) {
      case "landmarks":
        if (landmarks.length === 3 || landmarks.some(mark => distance(at, mark) < 4)) return;
        landmarks.push(at);
        feedback.textContent = `${landmarks.length} of 3 landmarks marked.`;
        break;
      case "mark":
      case "close-look":
      case "verify":
        marks[visit] = at;
        retryVisit = null;
        feedback.textContent = `Visit ${visit + 1} mark placed. Select another point to move it.`;
        break;
      default: return;
    }
    render();
  }

  function showVisit(index: number) {
    if (index === visit) return;
    visit = index;
    if (stage() === "next-visit" && index === 1) seenVisitTwo = true;
    const lastMark = marks[1];
    if (stage() === "verify" && index === 2 && lastMark) field.focus(lastMark, 4);
    feedback.textContent = "";
    render();
  }

  function advance() {
    if (!ready()) return;
    if (stage() === "found") {
      step = visit = hints = 0;
      tool = "original";
      landmarks = [];
      marks = [];
      retryVisit = null;
      shownContrast = seenVisitTwo = revealed = false;
      field.reset();
    } else if (stage() === "verify") {
      const unmatched = data.positions.findIndex((position, i) => {
        const mark = marks[i];
        return !mark || distance(mark, position) > MATCH_RADIUS_PIXELS;
      });
      if (unmatched !== -1) {
        retryVisit = unmatched;
        feedback.textContent = `Take another look at Visit ${unmatched + 1}. Check the faint points near your mark against the other dates.`;
        render();
        return;
      }
      step++;
      field.reset();
      field.focus(data.positions[1], 4);
      revealTimer = setTimeout(() => {
        revealed = true;
        render();
      }, 1200);
    } else {
      step++;
      const mark = marks[visit];
      if (stage() === "close-look" && mark) {
        visit = visit === 0 ? 1 : 0;
        tool = "contrast";
        field.focus(mark, 3);
      }
    }
    feedback.textContent = "";
    render();
    prompt.focus({ preventScroll: true });
    if (window.matchMedia("(max-width: 770px)").matches) prompt.scrollIntoView({ block: "nearest" });
  }

  visitButtons.forEach((button, i) => { button.onclick = () => showVisit(i); });
  toolButtons.forEach(button => {
    button.onclick = () => {
      tool = button.dataset.tool as StarTool;
      if (tool === "contrast") shownContrast = true;
      if (tool === "difference" && visit === 0) visit = 1;
      render();
    };
  });
  next.onclick = advance;
  control("zoom-in").onclick = () => field.zoom(1.5);
  control("zoom-out").onclick = () => field.zoom(1 / 1.5);
  control("reset-view").onclick = field.reset;
  control("undo").onclick = () => {
    landmarks.pop();
    feedback.textContent = `${landmarks.length} of 3 landmarks marked.`;
    render();
  };
  control("focus").onclick = () => {
    const patch = candidatePatch();
    field.focus({ x: patch.x + patch.w / 2, y: patch.y + patch.h / 2 }, 3);
  };
  control("hint").onclick = () => {
    hints++;
    field.reset();
    feedback.textContent = hints === 1
      ? "Try looking inside the outlined patch of sky."
      : "Look inside the smaller box. Do any faint points change position?";
    render();
  };
  render();
}
