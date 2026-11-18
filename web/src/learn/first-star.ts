import "./first-star.css";
import { formatMjd } from "../app/astro";
import { clueRegion, type Point } from "../play/hunt-model";
import { cropAround } from "./data";
import { esc } from "./glossary";
import {
  ASTROMETRY_ARTICLE, SPHEREX_WISE_VIEW, TESS_COMET, TESS_EPHEMERIS, TRACKING_IMAGES, WISE_DISCOVERY, WISE_NAME,
  loadTrackingImages, mastImage, trackingPictures, type StarTool, type TrackingData, type TrackingLesson,
} from "./first-star-data";
import { drawFirstStarPatch, mountFirstStarField } from "./first-star-field";
import { COMET_STEPS, COMET_TITLE, FIRST_STAR_STEPS, FIRST_STAR_TITLE } from "./first-star-steps";

const COMET_FILTERS: Partial<Record<StarTool, { label: string; description: string }>> = {
  negative: { label: "Negative", description: "Bright areas turn dark. Look for the outline of the head and tail." },
  background: { label: "Star background", description: "The middle light value at each pixel across all three frames keeps the steady star field visible." },
  motion: { label: "Moving objects", description: "Subtract the star background from this frame. Bright patches show extra light at this moment." },
};

export function mountTrackingGuide(host: HTMLElement, signal: AbortSignal, lesson: TrackingLesson) {
  const comet = lesson === "comet";
  const mission = comet ? "TESS" : "Hubble";
  const title = comet ? COMET_TITLE : FIRST_STAR_TITLE;
  const intro = comet
    ? "Follow a comet across three real NASA TESS images. Its name is revealed at the end."
    : "Follow a faint point across three real Hubble images. Find out what you tracked at the end.";
  host.innerHTML = `<article class="first-star" aria-labelledby="ln-h">
    <a class="first-star-back" href="#/learn">← Learn</a>
    <h1 id="ln-h" tabindex="-1">${title}</h1>
    <p class="first-star-intro">${intro}</p>
    <div class="first-star-body"><p role="status">Loading ${mission} images…</p></div>
  </article>`;
  const body = host.querySelector<HTMLElement>(".first-star-body")!;

  async function load() {
    try {
      const data = await loadTrackingImages(TRACKING_IMAGES[lesson], signal);
      if (!signal.aborted) runGuide(body, data, signal, lesson);
    } catch (error) {
      if (signal.aborted) return;
      console.warn("Learn: tracking images failed to load", error);
      body.innerHTML = `<p role="alert">Couldn’t load the ${mission} images.</p><button type="button" class="btn btn-primary">Try again</button>`;
      body.querySelector("button")!.onclick = () => {
        body.innerHTML = `<p role="status">Loading ${mission} images…</p>`;
        void load();
      };
    }
  }

  void load();
}

function runGuide(body: HTMLElement, data: TrackingData, signal: AbortSignal, lesson: TrackingLesson) {
  const comet = lesson === "comet";
  const source = TRACKING_IMAGES[lesson];
  const steps = comet ? COMET_STEPS : FIRST_STAR_STEPS;
  const { width, height, observations, arcsecPerPixel } = source.field;
  const frame = (i: number) => `${comet ? "Frame" : "Visit"} ${i + 1}`;
  const sourceCaption = comet ? "NASA TESS · Sector 1751 · Camera 2, CCD 3" : "NASA/ESA Hubble · WFC3/IR F110W";
  const size: [number, number] = [width, height];
  const world = { width, height };
  const fieldSize = comet ? `${(width * arcsecPerPixel / 60).toFixed(0)}′` : `${(width * arcsecPerPixel).toFixed(1)}″`;
  const observedShifts = data.positions.slice(1).map((position, i) => {
    const back = data.positions[i];
    return (Math.hypot(position.x - back.x, position.y - back.y) * arcsecPerPixel).toFixed(1);
  });
  const visitMidpoint = (i: number) => (observations[i].startMjd + observations[i].endMjd) / 2;
  const imageTime = (i: number) => formatMjd(visitMidpoint(i), comet);
  const utc = (i: number) => comet ? imageTime(i) : `${imageTime(i)} UTC`;
  const cutout = (i: number) => `<figure>
    <canvas width="144" height="144" role="img" aria-label="${comet ? "TESS" : "Hubble"} detail from ${frame(i)}"></canvas>
    <figcaption><strong>${frame(i)}</strong><br>${imageTime(i)}</figcaption>
  </figure>`;
  const first = data.positions[0];
  const last = data.positions[data.positions.length - 1];
  const totalPixels = Math.round(Math.hypot(last.x - first.x, last.y - first.y));
  const hours = ((visitMidpoint(2) - visitMidpoint(0)) * 24).toFixed(1);
  const story = comet
    ? `This interstellar comet crossed ${totalPixels} image pixels in ${hours} hours. Its bright head and diffuse tail moved past the same background stars in three TESS exposures.`
    : `You tracked a cold brown dwarf across three Hubble visits. In these images it moved about ${observedShifts[0]}″, then another ${observedShifts[1]}″, passing close to a brighter point.`;
  const revealLinks = comet
    ? `<p><a href="${TESS_COMET}">Watch NASA’s TESS sequence</a></p>`
    : `<p><a href="${WISE_DISCOVERY}">See NASA’s discovery animation</a></p>
      <p><a href="${SPHEREX_WISE_VIEW}">Find it at longer wavelengths in the SPHEREx Viewer</a></p>`;
  const archiveSource = comet ? TESS_EPHEMERIS : ASTROMETRY_ARTICLE;
  const archiveLabel = comet ? "TESS Science Support Center ephemeris" : "Published astrometry and Hubble observations";
  body.innerHTML = `
    <section class="first-star-guide">
      <header class="first-star-instruction">
        <p class="first-star-progress"></p>
        <h2 class="first-star-prompt" tabindex="-1"></h2>
        <div class="first-star-discovery" hidden>
          <h3>Meet ${comet ? "3I/ATLAS" : esc(WISE_NAME)}.</h3>
          <p>${story}</p>
          ${revealLinks}
        </div>
      </header>
      <section class="first-star-comparison" aria-labelledby="first-star-comparison-heading" hidden>
        <h3 id="first-star-comparison-heading"></h3>
        <div class="first-star-cutout-pair">${cutout(0)}${cutout(1)}</div>
        <div class="first-star-difference-explanation" hidden>
          <p>At each matching spot, SkyShift subtracts the light measured in ${frame(0)} from the light measured in ${frame(1)}.</p>
          <div class="first-star-difference-equation">
            ${cutout(1)}
            <span aria-hidden="true">−</span>
            ${cutout(0)}
            <span aria-hidden="true">=</span>
            <figure>
              <canvas width="144" height="144" role="img" aria-label="Difference cutout showing ${frame(1)} minus ${frame(0)}"></canvas>
              <figcaption><strong>Difference</strong></figcaption>
            </figure>
          </div>
          <ul class="first-star-difference-key" aria-label="Difference colour key">
            <li><span class="first-star-swatch is-positive" aria-hidden="true">+</span><strong>Red</strong> · More light in ${frame(1)}</li>
            <li><span class="first-star-swatch is-negative" aria-hidden="true">−</span><strong>Blue</strong> · Less light in ${frame(1)}</li>
            <li><span class="first-star-swatch is-neutral" aria-hidden="true">≈</span><strong>Gray</strong> · Little or no clear change</li>
          </ul>
          <p class="first-star-difference-note">${comet ? "Compare blue near the first mark with red near the later mark. Check both positions in the original frames." : "Bright stars can leave coloured streaks when the telescope turns. Use the two marks and the original images to check the shift."}</p>
        </div>
      </section>
      <figure class="first-star-observation" aria-labelledby="first-star-caption">
        <div class="first-star-frame">
          <canvas tabindex="0" aria-label="${comet ? "NASA TESS" : "NASA/ESA Hubble infrared"} star field. Use arrow keys to pan, plus or minus to zoom, and Enter to mark the crosshair."></canvas>
          <span class="first-star-active-visit" aria-hidden="true"></span>
          <span class="first-star-compass" aria-hidden="true">N ↑ · E ←</span>
          <div class="first-star-zoom" role="group" aria-label="Image zoom">
            <button class="btn" type="button" data-control="zoom-in" aria-label="Zoom in">+</button>
            <button class="btn" type="button" data-control="zoom-out" aria-label="Zoom out">−</button>
            <button class="btn" type="button" data-control="reset-view">Full field</button>
          </div>
        </div>
        <div class="first-star-toolbar">
          <div class="first-star-visits" role="group" aria-label="${comet ? "Observation frames" : "Observation visits"}">
            ${data.originals.map((_, i) => `<button type="button" class="btn" data-visit="${i}" aria-pressed="false">${frame(i)}</button>`).join("")}
          </div>
          <div class="first-star-filters" role="group" aria-label="Viewer filters">
            <span>Add filters</span>
            <button type="button" class="btn" data-tool="original" aria-pressed="true">Original</button>
            <button type="button" class="btn" data-tool="contrast" aria-pressed="false">High contrast</button>
            ${comet ? Object.entries(COMET_FILTERS).map(([mode, filter]) =>
              `<button type="button" class="btn" data-tool="${mode}" aria-pressed="false" hidden>${filter.label}</button>`).join("") : ""}
            <button type="button" class="btn" data-tool="difference" aria-pressed="false">Difference</button>
          </div>
        </div>
        <figcaption id="first-star-caption"></figcaption>
        <p class="first-star-filter-note" hidden></p>
        <details class="first-star-provenance">
          <summary>Observation data and processing</summary>
          <p>${sourceCaption}. The ${fieldSize} cutouts share a north-up grid of ${arcsecPerPixel}″ per pixel. ${comet
            ? "SkyShift reprojected MAST’s calibrated full-frame images onto that grid and subtracted each cutout’s median sky background."
            : "The F110W filter is centred near 1.15 µm. SkyShift used MAST programme 13802’s calibrated drizzle products, aligned stationary field sources and subtracted each cutout’s median sky background."}</p>
          <p id="first-star-processing"></p>
          <div class="first-star-archive" hidden>
            <p>The green trail connects positions measured in the three images, using published coordinates as references.</p>
            <p><a href="${archiveSource}">${archiveLabel}</a></p>
            <ul>${observations.map((o, i) => `<li><a href="${mastImage(comet ? "TESS" : "HST", o.filename)}">${frame(i)} FITS</a> · ${utc(i)}</li>`).join("")}</ul>
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
  const pictures = trackingPictures(source, data, canvas);
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
  let triedFilter = false;
  let seenVisitTwo = false;
  let hints = 0;
  let revealed = false;
  let revealTimer: ReturnType<typeof setTimeout> | undefined;

  const stage = () => step === steps.length ? "found" : steps[step].id;
  const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
  const landmarkPatch = () => cropAround(landmarks, size, 9, comet ? 124 : 48);
  const candidatePatch = () => {
    const selected = marks.slice(0, 2).filter((mark): mark is Point => !!mark);
    return cropAround(comet && selected.length < 2 ? [] : selected, size, 14, 56);
  };
  const field = mountFirstStarField(canvas, world, () => {
    const current = stage();
    const landmarkMarks = landmarks.map((at, i) => ({ at, kind: "landmark" as const, label: String(i + 1) }));
    const showLandmarks = current === "landmarks" || current === "contrast" || current === "next-visit" ||
      current === "compare" || current === "verify";
    const showPatch = current === "mark" || current === "close-look" || current === "difference" || current === "originals";
    const clue = hints && current !== "found" ? clueRegion(data.positions, world, hints) : undefined;
    const visibleMarks = tool === "difference" ? marks.slice(0, 2) : [marks[visit]];
    return {
      image: pictures[tool]?.[visit] ?? pictures.original[visit],
      marks: revealed
        ? data.positions.map((at, i) => ({ at, kind: "archive" as const, label: frame(i) }))
        : [
          ...landmarkMarks,
          ...visibleMarks.flatMap((at, i) => at ? [{
            at, kind: "candidate" as const, label: tool === "difference" ? `${frame(i)} mark` : "Your mark",
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
      case "contrast": return triedFilter;
      case "next-visit": return seenVisitTwo;
      case "mark": return !!marks[visit];
      case "close-look": return !!marks[0] && !!marks[1];
      case "difference": return tool === "difference";
      case "originals": return tool === "original" || tool === "contrast" || tool === "negative";
      case "verify": return visit === 2 && !!marks[2];
      case "found": return revealed;
      default: return true;
    }
  }

  function toolAvailable(mode: StarTool) {
    const current = stage();
    if (mode === "difference") return current === "difference" || current === "originals";
    if (!COMET_FILTERS[mode]) return true;
    if (!comet) return false;
    if (mode === "background") return current === "contrast" || current === "compare";
    if (mode === "motion") return current === "difference" || current === "originals" || current === "verify";
    return true;
  }

  function render() {
    const current = stage();
    const found = current === "found";
    const maxVisit = step < 3 ? 0 : current === "verify" || found ? 2 : 1;
    $(".first-star-progress").textContent = `Step ${step + 1} of ${steps.length + 1}`;
    const text = found
      ? comet ? "Congratulations! You tracked the comet." : "Congratulations! You found your first star."
      : steps[step].prompt;
    if (prompt.textContent !== text) prompt.textContent = text;
    next.textContent = found ? "Start again" : steps[step].action;
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
      button.disabled = found || (tool === "difference" && i === 0);
      button.setAttribute("aria-pressed", String(i === visit));
      button.classList.toggle("is-suggested",
        (current === "next-visit" && !seenVisitTwo && i === 1) ||
        (current === "verify" && (retryVisit ?? 2) === i && visit !== i));
    });
    const showingDifference = tool === "difference";
    const showingBackground = tool === "background";
    const filter = COMET_FILTERS[tool];
    $(".first-star-active-visit").textContent = showingDifference ? `${frame(1)} − ${frame(0)}`
      : showingBackground ? "Star background · 3 frames" : `${frame(visit)} · ${imageTime(visit)}`;
    const comparingCandidate = current === "close-look" || current === "originals"
      || current === "difference" && (showingDifference || tool === "motion");
    const patch = current === "compare" ? landmarkPatch()
      : comparingCandidate && marks.some(Boolean) ? candidatePatch() : undefined;
    comparison.hidden = !patch;
    guide.classList.toggle("has-comparison", !!patch);
    if (patch) {
      comparisonHeading.textContent = showingDifference ? "How the Difference image is made"
        : showingBackground ? "Original frames for comparison" : tool === "motion" ? "Moving objects in both frames"
        : comet ? "Same patch, two frames" : "Same patch, two visits";
      originalComparison.hidden = showingDifference;
      differenceExplanation.hidden = !showingDifference;
      if (showingDifference) {
        const images = [pictures.original[1], pictures.original[0], pictures.difference[1]];
        differenceCutouts.forEach((detail, i) => drawFirstStarPatch(detail, images[i], patch));
      } else {
        const images = showingBackground ? pictures.original : pictures[tool] ?? pictures.original;
        originalCutouts.forEach((detail, i) => drawFirstStarPatch(detail, images[i], patch, marks[i]));
      }
    }
    const note = showingDifference ? ` · Difference from ${frame(0)} (${utc(0)})`
      : tool === "contrast" ? " · High contrast" : filter ? ` · ${filter.label}` : "";
    $("#first-star-caption").textContent = showingBackground
      ? `Median of three aligned frames · ${formatMjd(visitMidpoint(0))} UTC · ${sourceCaption}`
      : `${frame(visit)} · ${utc(visit)} · ${sourceCaption}${note}`;
    const filterNote = $(".first-star-filter-note");
    filterNote.hidden = !filter;
    filterNote.textContent = filter?.description ?? "";

    const observed = observations[visit];
    const calibration = comet ? observed.calibration : `CALWF3 ${observed.calibration.split(" ")[0]}`;
    const exposureInfo = showingBackground
      ? `The three exposure midpoints are ${observations.map((_, i) => utc(i)).join(" · ")}. Calibration used ${calibration}.`
      : `The exposure midpoint is ${formatMjd(visitMidpoint(visit), true)}. Exposure duration was ${Math.round(observed.exposureSeconds)} seconds. Calibration used ${calibration}.`;
    const processing = showingDifference
      ? `SkyShift smooths both calibrated images and subtracts ${frame(0)} pixel by pixel. The colour scale mutes differences near the estimated noise level.`
      : showingBackground ? "SkyShift smooths each aligned frame and takes the median at each pixel."
      : tool === "motion" ? "SkyShift subtracts the pixel-wise median of the three smoothed frames and displays positive residuals above the estimated noise."
      : tool === "negative" ? "SkyShift reverses the original display palette while keeping the same intensity stretch on every image."
      : tool === "contrast" ? "High contrast smooths the measured pixels and uses the same faint-detail stretch on every image."
      : "The original views use the same display stretch on every image.";
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
        feedback.textContent = `${frame(visit)} mark placed. Select another point to move it.`;
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
    if (stage() === "verify" && index === 2) {
      if (comet) field.reset();
      else if (lastMark) field.focus(lastMark, 4);
    }
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
      triedFilter = seenVisitTwo = revealed = false;
      field.reset();
    } else if (stage() === "verify") {
      const unmatched = data.positions.findIndex((position, i) => {
        const mark = marks[i];
        return !mark || distance(mark, position) > (comet ? 2.5 : 2.25);
      });
      if (unmatched !== -1) {
        retryVisit = unmatched;
        feedback.textContent = comet
          ? `Take another look at ${frame(unmatched)}. Check the object’s bright head against the background stars.`
          : `Take another look at ${frame(unmatched)}. Check the faint points near your mark against the other dates.`;
        render();
        return;
      }
      step++;
      if (tool === "motion") tool = "original";
      field.reset();
      if (!comet) field.focus(data.positions[1], 4);
      revealTimer = setTimeout(() => {
        revealed = true;
        render();
      }, 1200);
    } else {
      step++;
      if (!toolAvailable(tool)) tool = "original";
      const mark = marks[visit];
      if (stage() === "close-look" && mark) {
        visit = visit === 0 ? 1 : 0;
        if (!comet || tool === "original") tool = "contrast";
        if (comet) field.reset();
        else field.focus(mark, 3);
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
      if (tool === "contrast" || COMET_FILTERS[tool]) triedFilter = true;
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
