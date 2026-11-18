import "./first-star.css";
import { formatMjd } from "../app/astro";
import { clueRegion, type Point } from "../play/hunt-model";
import { cropAround } from "./data";
import { esc } from "./glossary";
import {
  ASTROMETRY_ARTICLE, ELPIS_NAME, ELPIS_ORBIT, INTENSITY_GRADIENT, IRSA_MOST,
  SPHEREX_WISE_VIEW, TESS_COMET, TESS_EPHEMERIS, WISE_DISCOVERY, WISE_NAME,
  loadTrackingImages, mastImage, trackingPictures, type StarTool, type TrackingData, type TrackingLesson,
} from "./first-star-data";
import { drawFirstStarPatch, mountFirstStarField } from "./first-star-field";
import {
  ASTEROID_TITLE, COMET_STEPS, COMET_TITLE, FIRST_STAR_STEPS, FIRST_STAR_TITLE, PRACTICE_STEPS,
} from "./first-star-steps";

const FRAME_DELAY_MS = 1000;
const CORRECT_MARK_HOLD_MS = 450;

const MOVING_FILTERS: Partial<Record<StarTool, { label: string; description: string }>> = {
  intensity: { label: "Intensity colours", description: "Colours show the brightness measured in this image." },
  negative: { label: "Negative", description: "Bright areas turn dark, which can make shapes easier to see." },
  background: { label: "Star background", description: "The middle light value at each pixel across all three frames keeps the steady star field visible." },
  motion: { label: "Moving objects", description: "Subtract the star background from this frame. Bright patches show extra light at this moment." },
};

const PRACTICE_ROUNDS = ["comet", "asteroid", "brown-dwarf"] as const;

const HUBBLE_GUIDE = {
  mission: "Hubble", name: WISE_NAME,
  sourceCaption: "NASA/ESA Hubble · WFC3/IR F110W",
  preparation: "The F110W filter is centred near 1.15 µm. SkyShift used MAST programme 13802’s calibrated drizzle products, aligned stationary field sources and subtracted each cutout’s median sky background.",
};

const GUIDES = {
  comet: {
    title: COMET_TITLE, mission: "TESS", steps: COMET_STEPS, name: "3I/ATLAS",
    intro: "Follow a comet across three real NASA TESS images.",
    sourceCaption: "NASA TESS · Sector 1751 · Camera 2, CCD 3",
    found: "Congratulations! You tracked the comet.",
    preparation: "SkyShift reprojected MAST’s calibrated full-frame images onto that grid and subtracted each cutout’s median sky background.",
  },
  asteroid: {
    title: ASTEROID_TITLE, mission: "SPHEREx", steps: PRACTICE_STEPS, name: ELPIS_NAME,
    intro: "Watch the star field in three NASA SPHEREx images.",
    sourceCaption: "NASA SPHEREx · Detector 2",
    found: "Congratulations! You found the moving point.",
    preparation: "SkyShift cropped the same region from three calibrated SPHEREx images archived at NASA/IPAC IRSA and subtracted each cutout’s median sky background.",
  },
  "brown-dwarf": {
    ...HUBBLE_GUIDE,
    title: "Follow a Faint Point", steps: PRACTICE_STEPS,
    intro: "Follow a faint point through three infrared images from Hubble.",
    found: "Practice complete!",
  },
  star: {
    ...HUBBLE_GUIDE,
    title: FIRST_STAR_TITLE, steps: FIRST_STAR_STEPS,
    intro: "Follow a faint point across three real Hubble images. Find out what you tracked at the end.",
    found: "Congratulations! You found your first star.",
  },
};

interface PracticeFlow {
  foundCount: number;
  advance?: () => void;
  restart: () => void;
}

export function mountTrackingGuide(host: HTMLElement, signal: AbortSignal, lesson: TrackingLesson) {
  host.innerHTML = `<article class="first-star${lesson === "star" ? "" : " first-star-moving"}" aria-labelledby="ln-h">
    <a class="first-star-back" href="#/learn">← Learn</a>
    <h1 id="ln-h" tabindex="-1"></h1>
    <p class="first-star-round-status" role="status"></p>
    <p class="first-star-intro"></p>
    <div class="first-star-body"></div>
  </article>`;
  const title = host.querySelector<HTMLElement>("#ln-h")!;
  const roundStatus = host.querySelector<HTMLElement>(".first-star-round-status")!;
  const intro = host.querySelector<HTMLElement>(".first-star-intro")!;
  const body = host.querySelector<HTMLElement>(".first-star-body")!;
  const images = new Map<TrackingLesson, Promise<TrackingData>>();
  let activeGuide: AbortController | undefined;
  let completed = 0;
  let lastFind: { name: string; href: string } | undefined;
  signal.addEventListener("abort", () => activeGuide?.abort(), { once: true });

  function getImages(active: TrackingLesson) {
    const cached = images.get(active);
    if (cached) return cached;
    const request = loadTrackingImages(active, signal).catch(error => {
      images.delete(active);
      throw error;
    });
    images.set(active, request);
    return request;
  }

  function openRound(active: TrackingLesson, focus = false) {
    if (signal.aborted) return;
    activeGuide?.abort();
    const controller = new AbortController();
    activeGuide = controller;
    const config = GUIDES[active];
    const round = active === "star" ? -1 : PRACTICE_ROUNDS.indexOf(active);
    const following = round < 0 ? undefined : PRACTICE_ROUNDS[round + 1];
    title.textContent = config.title;
    roundStatus.innerHTML = lastFind ? `Found <a href="${lastFind.href}">${esc(lastFind.name)}</a>.` : "";
    intro.textContent = round < 0 ? config.intro : `Round ${round + 1} of ${PRACTICE_ROUNDS.length}. ${config.intro}`;

    const flow: PracticeFlow | undefined = round < 0 ? undefined : {
      foundCount: completed + 1,
      advance: following ? () => {
        completed++;
        lastFind = { name: config.name, href: active === "comet" ? TESS_COMET : ELPIS_ORBIT };
        openRound(following, true);
      } : undefined,
      restart: () => {
        completed = 0;
        lastFind = undefined;
        openRound("comet", true);
      },
    };

    async function load() {
      body.innerHTML = `<p role="status">Loading ${config.mission} images…</p>`;
      try {
        const data = await getImages(active);
        if (controller.signal.aborted) return;
        runGuide(body, data, controller.signal, active, flow);
        if (following) void getImages(following).catch(() => { /* Retry when that round opens. */ });
        if (focus) {
          title.focus({ preventScroll: true });
          title.scrollIntoView({ block: "nearest" });
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        console.warn("Learn: tracking images failed to load", error);
        body.innerHTML = `<p role="alert">Couldn’t load the ${config.mission} images.</p><button type="button" class="btn btn-primary">Try again</button>`;
        body.querySelector("button")!.onclick = load;
      }
    }

    void load();
  }

  openRound(lesson);
}

function runGuide(body: HTMLElement, data: TrackingData, signal: AbortSignal, lesson: TrackingLesson, flow?: PracticeFlow) {
  const moving = lesson !== "star";
  const comet = lesson === "comet";
  const asteroid = lesson === "asteroid";
  const brownDwarf = lesson === "brown-dwarf";
  const hubble = brownDwarf || lesson === "star";
  const { steps, name, mission, sourceCaption, preparation, found: foundMessage } = GUIDES[lesson];
  const { width, height, observations, arcsecPerPixel } = data.field;
  const frame = (i: number) => `${moving ? "Frame" : "Visit"} ${i + 1}`;
  const size: [number, number] = [width, height];
  const world = { width, height };
  const fieldSize = hubble ? `${(width * arcsecPerPixel).toFixed(1)}″` : `${(width * arcsecPerPixel / 60).toFixed(0)}′`;
  const observedShifts = data.positions.slice(1).map((position, i) => {
    const back = data.positions[i];
    return (Math.hypot(position.x - back.x, position.y - back.y) * arcsecPerPixel).toFixed(1);
  });
  const visitMidpoint = (i: number) => observations[i].midpointMjd ?? (observations[i].startMjd + observations[i].endMjd) / 2;
  const band = (i: number) => observations[i].wavelength?.map(value => value.toFixed(4)).join("–");
  const imageTime = (i: number) => formatMjd(visitMidpoint(i), moving);
  const utc = (i: number) => moving ? imageTime(i) : `${imageTime(i)} UTC`;
  const cutout = (i: number) => `<figure>
    <canvas width="144" height="144" role="img" aria-label="${mission} detail from ${frame(i)}"></canvas>
    <figcaption><strong>${frame(i)}</strong><br>${imageTime(i)}</figcaption>
  </figure>`;
  const story = `You tracked a cold brown dwarf across three Hubble visits. In these images it moved about ${observedShifts[0]}″, then another ${observedShifts[1]}″, passing close to a brighter point.`;
  const archiveSource = comet ? TESS_EPHEMERIS : asteroid ? IRSA_MOST : ASTROMETRY_ARTICLE;
  const archiveLabel = comet ? "TESS Science Support Center ephemeris"
    : asteroid ? "IRSA Moving Object Search Tool" : "Published astrometry and Hubble observations";
  const differenceEquation = `
    <div class="first-star-difference-equation">
      ${cutout(1)}
      <span aria-hidden="true">−</span>
      ${cutout(0)}
      <span aria-hidden="true">=</span>
      <figure>
        <canvas width="144" height="144" role="img" aria-label="Difference cutout showing ${frame(1)} minus ${frame(0)}"></canvas>
        <figcaption><strong>Difference</strong></figcaption>
      </figure>
    </div>`;
  const differenceKey = `
    <ul class="first-star-difference-key" aria-label="Difference colour key">
      <li><span class="first-star-swatch is-positive" aria-hidden="true">+</span><strong>Red</strong> · More light in ${frame(1)}</li>
      <li><span class="first-star-swatch is-negative" aria-hidden="true">−</span><strong>Blue</strong> · Less light in ${frame(1)}</li>
      <li><span class="first-star-swatch is-neutral" aria-hidden="true">≈</span><strong>Gray</strong> · Little or no clear change</li>
    </ul>`;
  const feedbackMarkup = '<p class="first-star-feedback" role="status"></p>';
  const filterButton = (mode: StarTool, label: string) =>
    `<button type="button" class="btn" data-tool="${mode}" aria-pressed="false" hidden>${label}</button>`;

  body.innerHTML = `
    <section class="first-star-guide">
      <header class="first-star-instruction">
        <p class="first-star-progress"></p>
        <h2 class="first-star-prompt" tabindex="-1"></h2>
        ${moving ? '<p class="first-star-track-instruction" hidden></p>' : ""}
        <div class="first-star-discovery" hidden>
          ${hubble ? `
            ${flow ? `<p>You tracked ${flow.foundCount} moving objects.</p>` : ""}
            <h3>Meet ${esc(name)}.</h3>
            <p>${story}</p>
            <p><a href="${WISE_DISCOVERY}">See NASA’s discovery animation</a></p>
            <p><a href="${SPHEREX_WISE_VIEW}">Find it at longer wavelengths in the SPHEREx Viewer</a></p>` : ""}
        </div>
      </header>
      <section class="first-star-comparison" aria-labelledby="first-star-comparison-heading" hidden>
        <h3 id="first-star-comparison-heading"></h3>
        <div class="first-star-cutout-pair">${cutout(0)}${cutout(1)}</div>
        ${moving ? "" : `<div class="first-star-difference-explanation" hidden>
          <p>At each matching spot, SkyShift subtracts the light measured in ${frame(0)} from the light measured in ${frame(1)}.</p>
          ${differenceEquation}
          ${differenceKey}
          <p class="first-star-difference-note">Bright stars can leave coloured streaks when the telescope turns. Use the two marks and the original images to check the shift.</p>
        </div>`}
      </section>
      <figure class="first-star-observation" aria-labelledby="first-star-caption">
        <div class="first-star-frame">
          <canvas tabindex="0" aria-label="${sourceCaption} star field. Use arrow keys to pan, plus or minus to zoom, and Enter to mark the crosshair."></canvas>
          <span class="first-star-active-visit" aria-hidden="true"></span>
          <span class="first-star-compass" aria-hidden="true">N ↑ · E ←</span>
          <div class="first-star-zoom" role="group" aria-label="Image zoom">
            <button class="btn" type="button" data-control="zoom-in" aria-label="Zoom in">+</button>
            <button class="btn" type="button" data-control="zoom-out" aria-label="Zoom out">−</button>
            <button class="btn" type="button" data-control="reset-view">Full field</button>
          </div>
        </div>
        ${moving ? feedbackMarkup : ""}
        ${moving ? `<div class="first-star-difference-explanation first-star-moving-difference" hidden>
          ${differenceKey}
          <details class="first-star-difference-details">
            <summary>How the Difference image is made</summary>
            <p>SkyShift subtracts Frame 1’s light from Frame 2’s at each matching spot.</p>
            ${differenceEquation}
          </details>
        </div>` : ""}
        <div class="first-star-toolbar">
          <div class="first-star-visits" role="group" aria-label="${moving ? "Observation frames" : "Observation visits"}">
            ${moving ? '<button type="button" class="btn" data-control="playback">Play frames</button>' : ""}
            ${data.originals.map((_, i) => `<button type="button" class="btn" data-visit="${i}" aria-pressed="false">${frame(i)}</button>`).join("")}
          </div>
          <div class="first-star-filters" role="group" aria-label="Viewer filters">
            ${moving ? "" : "<span>Add filters</span>"}
            <button type="button" class="btn" data-tool="original" aria-pressed="true">Original</button>
            ${moving ? `
              ${filterButton("intensity", "Intensity colours")}
              ${filterButton("difference", "Difference")}
              <details class="first-star-more-filters">
                <summary>More filters</summary>
                <div>
                  ${filterButton("contrast", "High contrast")}
                  ${Object.entries(MOVING_FILTERS).filter(([mode]) => mode !== "intensity").map(([mode, filter]) =>
                    filterButton(mode as StarTool, filter.label)).join("")}
                </div>
              </details>` : `${filterButton("contrast", "High contrast")}${filterButton("difference", "Difference")}`}
          </div>
        </div>
        <figcaption id="first-star-caption"></figcaption>
        <p class="first-star-filter-note" hidden></p>
        <div class="first-star-intensity-key" hidden>
          <span>Fainter</span>
          <span class="first-star-intensity-ramp" aria-hidden="true"></span>
          <span>Brighter</span>
        </div>
        <details class="first-star-provenance">
          <summary>Observation data and processing</summary>
          <p>${sourceCaption}. The ${fieldSize} cutouts share a north-up grid of ${arcsecPerPixel}″ per pixel. ${preparation}</p>
          <p id="first-star-processing"></p>
          <div class="first-star-archive" hidden>
            ${hubble ? "<p>The green trail connects positions measured in the three images, using published coordinates as references.</p>" : ""}
            <p><a href="${archiveSource}">${archiveLabel}</a></p>
            <ul>${observations.map((o, i) => asteroid
              ? `<li>${frame(i)} · ${utc(i)} · ${band(i)} µm · ${esc(o.filename)}</li>`
              : `<li><a href="${mastImage(comet ? "TESS" : "HST", o.filename)}">${frame(i)} FITS</a> · ${utc(i)}</li>`).join("")}</ul>
          </div>
        </details>
      </figure>
      <div class="first-star-actions">
        ${moving ? "" : feedbackMarkup}
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
  const intensityKey = $(".first-star-intensity-key");
  $(".first-star-intensity-ramp").style.backgroundImage = INTENSITY_GRADIENT;
  const comparison = $(".first-star-comparison");
  const comparisonHeading = $("#first-star-comparison-heading");
  const originalComparison = $(".first-star-cutout-pair");
  const differenceExplanation = $(".first-star-difference-explanation");
  const originalCutouts = [...originalComparison.querySelectorAll("canvas")];
  const differenceCutouts = [...differenceExplanation.querySelectorAll("canvas")];
  const trackInstruction = body.querySelector<HTMLElement>(".first-star-track-instruction");
  const pictures = trackingPictures(lesson, data, canvas);
  const prompt = $(".first-star-prompt");
  const feedback = $(".first-star-feedback");
  const next = control("continue");
  const playback = body.querySelector<HTMLButtonElement>('[data-control="playback"]');
  const reducedMotion = moving ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const toolButtons = buttons("[data-tool]");
  const visitButtons = buttons("[data-visit]");
  let step = 0;
  let visit = 0;
  let tool: StarTool = "original";
  let landmarks: Point[] = [];
  let marks: (Point | undefined)[] = [];
  const rejectedMarks: (Point | undefined)[] = [];
  let retryVisit: number | null = null;
  let triedFilter = false;
  let seenVisitTwo = false;
  let hints = 0;
  let revealed = false;
  let revealTimer: ReturnType<typeof setTimeout> | undefined;
  let markAdvanceTimer: ReturnType<typeof setTimeout> | undefined;
  let playbackTimer: ReturnType<typeof setInterval> | undefined;

  const stage = () => step === steps.length ? "found" : steps[step].id;
  const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
  const withinTarget = (at: Point, index: number) => distance(at, data.positions[index]) <= (moving ? 2.5 : 2.25);
  const matchesMark = (index: number) => {
    const mark = marks[index];
    return !!mark && withinTarget(mark, index);
  };
  const landmarkPatch = () => cropAround(landmarks, size, 9, moving ? 20 : 48);
  const candidatePatch = () => {
    const selected = marks.slice(0, 2).filter((mark): mark is Point => !!mark);
    return cropAround(moving && stage() === "track" ? [] : selected, size, 14, 56);
  };
  const field = mountFirstStarField(canvas, world, () => {
    const current = stage();
    const landmarkMarks = landmarks.map((at, i) => ({ at, kind: "landmark" as const, label: moving ? "" : String(i + 1) }));
    const showLandmarks = current === "landmarks" || current === "contrast" || current === "next-visit" ||
      current === "compare" || current === "track" || current === "verify";
    const showPatch = moving ? current === "difference"
      : current === "mark" || current === "close-look" || current === "difference" || current === "originals";
    const clue = hints && current !== "found" ? clueRegion(data.positions, world, hints) : undefined;
    const visibleMarks = tool === "difference" ? marks.slice(0, 2) : [marks[visit]];
    const rejected = tool === "difference" ? undefined : rejectedMarks[visit];
    return {
      image: pictures[tool]?.[visit] ?? pictures.original[visit],
      marks: revealed
        ? data.positions.map((at, i) => ({
          at, kind: "archive" as const, label: brownDwarf ? String(i + 1) : frame(i), radius: brownDwarf ? 8 : undefined,
        }))
        : [
          ...(!moving || showLandmarks ? landmarkMarks : []),
          ...visibleMarks.flatMap((at, i) => at ? [{
            at,
            kind: moving ? "correct" as const : "candidate" as const,
            label: tool === "difference" ? `${frame(i)} mark` : moving ? "" : "Your mark",
          }] : []),
          ...(rejected ? [{ at: rejected, kind: "incorrect" as const, label: "" }] : []),
        ],
      trail: revealed ? data.positions : undefined,
      guides: [
        ...(showLandmarks && landmarks.length ? [{
          patch: landmarkPatch(),
          label: moving ? "Your landmark" : "Your landmarks", kind: "landmarks" as const,
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

  signal.addEventListener("abort", () => {
    field.destroy();
    stopPlayback();
    clearTimeout(revealTimer);
    clearTimeout(markAdvanceTimer);
  }, { once: true });

  function ready() {
    switch (stage()) {
      case "landmarks": return moving || landmarks.length === 3;
      case "contrast": return triedFilter;
      case "next-visit": return seenVisitTwo;
      case "mark": return !!marks[visit];
      case "track": return matchesMark(0) && matchesMark(1);
      case "practice": return data.positions.every((_, i) => matchesMark(i));
      case "close-look": return !!marks[0] && !!marks[1];
      case "difference": return moving || tool === "difference";
      case "originals": return tool === "original" || tool === "contrast";
      case "verify": return moving ? matchesMark(2) : visit === 2 && !!marks[2];
      case "found": return revealed;
      default: return true;
    }
  }

  function toolAvailable(mode: StarTool) {
    const current = stage();
    if (mode === "difference") return current === "difference" || current === "originals";
    if (!MOVING_FILTERS[mode]) return true;
    if (!moving) return false;
    if (mode === "background") return current === "landmarks" || current === "track";
    if (mode === "motion") return current === "difference" || current === "verify" || current === "practice";
    return true;
  }

  function render() {
    const current = stage();
    const currentStep = steps[step];
    const found = current === "found";
    const watching = (moving && current === "landmarks") || current === "practice";
    const canCompare = moving ? !watching : step >= 3;
    const maxVisit = watching || current === "verify" || found ? 2 : canCompare ? 1 : 0;
    $(".first-star-progress").textContent = current === "practice"
      ? `${data.positions.filter((_, i) => matchesMark(i)).length} of ${observations.length} frames marked`
      : brownDwarf && found ? "" : `Step ${step + 1} of ${steps.length + 1}`;
    const text = found ? foundMessage : currentStep.prompt;
    if (prompt.textContent !== text) prompt.textContent = text;
    const canContinue = ready();
    if (trackInstruction) {
      trackInstruction.hidden = current !== "track";
      if (current === "track") {
        const same = matchesMark(1 - visit);
        const target = same ? "the same object’s head" : "the bright head";
        trackInstruction.textContent = `Mark ${target} in ${frame(visit)}.`;
      }
    }
    const action = found ? revealed ? "Start again" : "" : "action" in currentStep ? currentStep.action : "";
    next.textContent = action;
    next.disabled = !canContinue;
    next.hidden = !action;
    $(".first-star-actions").classList.toggle("is-complete", found);
    control("undo").hidden = current !== "landmarks" || !landmarks.length;
    control("undo").textContent = moving ? "Remove landmark" : "Undo landmark";
    control("focus").hidden = current !== "close-look";
    const hint = control("hint");
    hint.hidden = (moving ? current !== "track" && current !== "verify" && current !== "practice" : step < 4) || found || hints >= 2;
    hint.textContent = hints === 1 ? "Narrow the search" : "Show a hint";
    const filtersAvailable = (moving || step >= 2) && !found;
    $(".first-star-filters").hidden = !filtersAvailable;
    const suggestedTool = current === "contrast" ? "contrast" : current === "difference" ? "difference"
      : current === "originals" ? "original" : null;
    toolButtons.forEach(button => {
      const mode = button.dataset.tool as StarTool;
      button.hidden = !toolAvailable(mode);
      button.setAttribute("aria-pressed", String(mode === tool));
      button.classList.toggle("is-suggested", !canContinue && mode === suggestedTool);
    });
    visitButtons.forEach((button, i) => {
      button.hidden = i > maxVisit;
      button.disabled = found || markAdvanceTimer !== undefined || (tool === "difference" && i === 0);
      button.setAttribute("aria-pressed", String(i === visit));
      button.classList.toggle("is-suggested",
        (current === "next-visit" && !seenVisitTwo && i === 1) ||
        (current === "verify" && (retryVisit ?? 2) === i && visit !== i));
    });
    const showingDifference = tool === "difference";
    const showingBackground = tool === "background";
    $(".first-star-visits").hidden = moving && (found || current === "verify" || showingDifference);
    if (playback) {
      playback.hidden = !watching;
      playback.disabled = markAdvanceTimer !== undefined;
      playback.textContent = playbackTimer === undefined ? "Play frames" : "Pause frames";
    }
    const filter = MOVING_FILTERS[tool];
    $(".first-star-active-visit").textContent = showingDifference ? `${frame(1)} − ${frame(0)}`
      : showingBackground ? "Star background · 3 frames" : `${frame(visit)} · ${imageTime(visit)}`;
    const comparingCandidate = current === "track" || current === "close-look" || current === "originals"
      || current === "difference" && (moving || showingDifference);
    const patch = current === "compare" ? landmarkPatch()
      : comparingCandidate ? candidatePatch() : undefined;
    comparison.hidden = !patch || (moving && showingDifference);
    guide.classList.toggle("has-comparison", !comparison.hidden);
    differenceExplanation.hidden = !showingDifference;
    if (patch) {
      originalComparison.hidden = showingDifference;
      if (showingDifference) {
        if (!moving) comparisonHeading.textContent = "How the Difference image is made";
        const images = [pictures.original[1], pictures.original[0], pictures.difference[1]];
        differenceCutouts.forEach((detail, i) => drawFirstStarPatch(detail, images[i], patch));
      } else {
        comparisonHeading.textContent = showingBackground ? "Original frames for comparison"
          : tool === "motion" ? "Moving objects in both frames" : current === "track" ? "Compare the same sky"
          : moving ? "Same patch, two frames" : "Same patch, two visits";
        const images = showingBackground ? pictures.original : pictures[tool] ?? pictures.original;
        originalCutouts.forEach((detail, i) =>
          drawFirstStarPatch(detail, images[i], patch, marks[i], moving ? "correct" : "candidate"));
      }
    }
    const channel = asteroid ? ` · ${band(visit)} µm` : "";
    const note = showingDifference ? ` · Difference from ${frame(0)} (${utc(0)})`
      : tool === "contrast" ? " · High contrast" : filter ? ` · ${filter.label}` : "";
    $("#first-star-caption").textContent = showingBackground
      ? `Median of three aligned frames · ${formatMjd(visitMidpoint(0))} UTC · ${sourceCaption}`
      : `${frame(visit)} · ${utc(visit)} · ${sourceCaption}${channel}${note}`;
    const filterNote = $(".first-star-filter-note");
    filterNote.hidden = !filter;
    filterNote.textContent = filter?.description ?? "";
    intensityKey.hidden = tool !== "intensity";

    const observed = observations[visit];
    const calibration = !hubble ? observed.calibration : `CALWF3 ${observed.calibration.split(" ")[0]}`;
    const exposureInfo = showingBackground
      ? `The three exposure midpoints are ${observations.map((_, i) => utc(i)).join(" · ")}. ${asteroid ? "SPHEREx data release" : "Calibration used"} ${calibration}.`
      : asteroid ? `The exposure midpoint is ${utc(visit)}. This frame covers ${band(visit)} µm. SPHEREx data release ${calibration}.`
      : `The exposure midpoint is ${formatMjd(visitMidpoint(visit), true)}. Exposure duration was ${Math.round(observed.exposureSeconds)} seconds. Calibration used ${calibration}.`;
    const processing = showingDifference
      ? `SkyShift smooths both calibrated images and subtracts ${frame(0)} pixel by pixel. The colour scale mutes differences near the estimated noise level.`
      : showingBackground ? "SkyShift smooths each aligned frame and takes the median at each pixel."
      : tool === "motion" ? "SkyShift subtracts the pixel-wise median of the three smoothed frames and displays positive residuals above the estimated noise."
      : tool === "negative" ? "SkyShift reverses the original display palette while keeping the same intensity stretch on every image."
      : tool === "intensity" ? "SkyShift maps measured brightness to a colour scale. All frames use the same intensity stretch as Original."
      : tool === "contrast" ? "High contrast smooths the measured pixels and uses the same faint-detail stretch on every image."
      : "The original views use the same display stretch on every image.";
    $("#first-star-processing").textContent = `${exposureInfo} ${processing}`;
    $(".first-star-discovery").hidden = !revealed;
    $(".first-star-archive").hidden = hubble && !revealed;
    field.draw();
  }

  function selectPoint(at: Point) {
    const current = stage();
    if (moving && (current === "track" || current === "verify" || current === "practice")) {
      stopPlayback();
      clearTimeout(markAdvanceTimer);
      markAdvanceTimer = undefined;
      if (withinTarget(at, visit)) {
        marks[visit] = at;
        rejectedMarks[visit] = undefined;
        feedback.textContent = `Correct. ${frame(visit)} marked.`;
        markAdvanceTimer = setTimeout(advanceAfterMark, CORRECT_MARK_HOLD_MS);
      } else {
        rejectedMarks[visit] = at;
        feedback.textContent = comet
          ? "Try again. Look for the bright head of the object that moved."
          : "Try again. Look for a point that shifts against the background stars.";
      }
      render();
      return;
    }

    switch (current) {
      case "landmarks":
        if (moving) {
          stopPlayback();
          landmarks = [at];
          feedback.textContent = "Landmark placed.";
        } else {
          if (landmarks.length === 3 || landmarks.some(mark => distance(at, mark) < 4)) return;
          landmarks.push(at);
          feedback.textContent = `${landmarks.length} of 3 landmarks marked.`;
        }
        break;
      case "mark":
      case "close-look":
      case "verify":
        marks[visit] = at;
        retryVisit = null;
        feedback.textContent = `${frame(visit)} mark placed. Select another point to move it.`;
        break;
      default: return;
    }
    render();
  }

  function advanceAfterMark() {
    markAdvanceTimer = undefined;
    if (ready()) {
      advance();
      return;
    }
    const marked = frame(visit);
    do {
      visit = (visit + 1) % (stage() === "track" ? 2 : observations.length);
    } while (matchesMark(visit));
    feedback.textContent = `${marked} marked. ${frame(visit)} is now open.`;
    render();
  }

  function startPlayback() {
    if (playbackTimer !== undefined) return;
    if (tool === "background") tool = "original";
    playbackTimer = setInterval(() => {
      visit = (visit + 1) % observations.length;
      render();
    }, FRAME_DELAY_MS);
  }

  function stopPlayback() {
    clearInterval(playbackTimer);
    playbackTimer = undefined;
  }

  function pauseForInspection() {
    if (playbackTimer === undefined) return;
    stopPlayback();
    render();
  }

  function showVisit(index: number) {
    if (index === visit) {
      pauseForInspection();
      return;
    }
    stopPlayback();
    visit = index;
    if (stage() === "next-visit" && index === 1) seenVisitTwo = true;
    const lastMark = marks[1];
    if (stage() === "verify" && index === 2 && lastMark) field.focus(lastMark, 4);
    feedback.textContent = "";
    render();
  }

  function advance() {
    if (!ready()) return;
    stopPlayback();
    if (stage() === "found") {
      if (flow) {
        flow.restart();
        return;
      }
      step = visit = hints = 0;
      tool = "original";
      landmarks = [];
      marks = [];
      retryVisit = null;
      triedFilter = seenVisitTwo = revealed = false;
      field.reset();
    } else if (stage() === "verify" || stage() === "practice") {
      if (flow?.advance) {
        flow.advance();
        return;
      }
      const unmatched = moving ? -1 : data.positions.findIndex((_, i) => !matchesMark(i));
      if (unmatched !== -1) {
        retryVisit = unmatched;
        feedback.textContent = `Take another look at ${frame(unmatched)}. Check the faint points near your mark against the other dates.`;
        render();
        return;
      }
      step++;
      if (moving) {
        tool = "original";
        visit = 2;
        revealed = true;
      } else {
        field.reset();
        field.focus(data.positions[1], 4);
        revealTimer = setTimeout(() => {
          revealed = true;
          render();
        }, 1200);
      }
    } else {
      step++;
      if (!toolAvailable(tool)) tool = "original";
      if (stage() === "track") {
        visit = 0;
        field.reset();
      }
      if (moving && stage() === "difference") {
        visit = 1;
        hints = 0;
        tool = "difference";
        field.reset();
      }
      if (moving && stage() === "verify") {
        visit = 2;
        hints = 0;
        tool = "original";
        field.reset();
      }
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
      const mode = button.dataset.tool as StarTool;
      if (!toolAvailable(mode)) return;
      tool = mode;
      if (tool === "background") stopPlayback();
      if (tool === "contrast") triedFilter = true;
      if (tool === "difference" && visit === 0) visit = 1;
      render();
    };
  });
  if (playback) {
    playback.onclick = () => {
      if (playbackTimer === undefined) startPlayback();
      else stopPlayback();
      render();
    };
    canvas.addEventListener("pointerdown", pauseForInspection, { signal });
    canvas.addEventListener("focus", pauseForInspection, { signal });
    reducedMotion?.addEventListener("change", () => {
      if (reducedMotion.matches) pauseForInspection();
    }, { signal });
    if (!reducedMotion?.matches) startPlayback();
  }
  next.onclick = advance;
  control("zoom-in").onclick = () => { pauseForInspection(); field.zoom(1.5); };
  control("zoom-out").onclick = () => { pauseForInspection(); field.zoom(1 / 1.5); };
  control("reset-view").onclick = () => { pauseForInspection(); field.reset(); };
  control("undo").onclick = () => {
    landmarks.pop();
    feedback.textContent = moving ? "Landmark removed." : `${landmarks.length} of 3 landmarks marked.`;
    render();
  };
  control("focus").onclick = () => {
    const patch = candidatePatch();
    field.focus({ x: patch.x + patch.w / 2, y: patch.y + patch.h / 2 }, 3);
  };
  control("hint").onclick = () => {
    hints++;
    if (!brownDwarf) field.reset();
    feedback.textContent = hints === 1
      ? "Try looking inside the outlined patch of sky."
      : asteroid || brownDwarf ? "Look for a single point that shifts inside the smaller box."
      : comet ? "Look for a bright head with a faint tail inside the smaller box."
      : "Look inside the smaller box. Do any faint points change position?";
    render();
  };
  if (brownDwarf) field.zoom(2);
  render();
}
