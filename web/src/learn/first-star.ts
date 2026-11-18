import "./first-star.css";
import { visitOf, VISIT_DETS } from "../colour/visit";
import { formatMjd, formatRaDec } from "../app/astro";
import { clueRegion, type Point } from "../play/hunt-model";
import { cropAround } from "./data";
import { esc } from "./glossary";
import { FIRST_STAR, FIRST_STAR_DETECTOR, firstStarPictures, loadFirstStar, type FirstStarData, type StarTool } from "./first-star-data";
import { drawFirstStarPatch, mountFirstStarField } from "./first-star-field";
import { FIRST_STAR_STEPS, FIRST_STAR_TITLE } from "./first-star-steps";

const ARCHIVE = "https://irsa.ipac.caltech.edu/Missions/spherex.html";
const STEP_COUNT = FIRST_STAR_STEPS.length + 1;
const MATCH_RADIUS_PIXELS = 4;

export function mountFirstStar(host: HTMLElement, signal: AbortSignal) {
  host.innerHTML = `<article class="first-star" aria-labelledby="ln-h">
    <a class="first-star-back" href="#/learn">← Learn</a>
    <h1 id="ln-h" tabindex="-1">${FIRST_STAR_TITLE}</h1>
    <p class="first-star-intro">Retrace the evidence for a known star using real NASA SPHEREx observations at your own pace.</p>
    <div class="first-star-body"><p role="status">Loading three SPHEREx visits…</p></div>
  </article>`;
  const body = host.querySelector<HTMLElement>(".first-star-body")!;

  async function load() {
    try {
      const data = await loadFirstStar(signal);
      if (!signal.aborted) runGuide(body, data, signal);
    } catch (error) {
      if (signal.aborted) return;
      console.warn("Learn: first star observations failed to load", error);
      body.innerHTML = '<p role="alert">Couldn’t load the SPHEREx observations.</p><button type="button" class="btn btn-primary">Try again</button>';
      body.querySelector("button")!.onclick = () => {
        body.innerHTML = '<p role="status">Loading three SPHEREx visits…</p>';
        void load();
      };
    }
  }

  void load();
}

function runGuide(body: HTMLElement, data: FirstStarData, signal: AbortSignal) {
  const first = data.originals[0];
  const size = first.set.frames.size;
  const [width, height] = size;
  const world = { width, height };
  const fieldArcmin = Math.round(width * first.set.frames.pixel_arcsec / 60);
  body.innerHTML = `
    <section class="first-star-guide">
      <header class="first-star-instruction">
        <p class="first-star-progress"></p>
        <h2 class="first-star-prompt" tabindex="-1"></h2>
        <div class="first-star-discovery" hidden>
          <h3>Meet ${esc(FIRST_STAR.name)}.</h3>
          <p>This young variable star changes brightness. You retraced its position in three infrared visits and compared its changing light with nearby stars.</p>
          <p>Sky position <strong>${esc(formatRaDec(data.archivePosition))}</strong></p>
          <p>Bright stars can saturate some SPHEREx measurements. These images let you inspect the change without estimating how much the star brightened.</p>
          <p><a href="?t=${FIRST_STAR.slug}&amp;det=${FIRST_STAR_DETECTOR}#/explore">Open this star in the Viewer</a></p>
        </div>
      </header>
      <section class="first-star-comparison" aria-labelledby="first-star-comparison-heading" hidden>
        <h3 id="first-star-comparison-heading">Same patch, two visits</h3>
        <div>
          ${data.originals.slice(0, 2).map(({ epoch }, i) => `<figure>
            <canvas width="144" height="144" role="img" aria-label="Original SPHEREx detail from Visit ${i + 1}"></canvas>
            <figcaption><strong>Visit ${i + 1}</strong><br>${formatMjd(epoch.mjd_mid)}</figcaption>
          </figure>`).join("")}
        </div>
      </section>
      <figure class="first-star-observation" aria-labelledby="first-star-caption">
        <div class="first-star-frame">
          <canvas tabindex="0" aria-label="NASA SPHEREx infrared star field. Use arrow keys to pan, plus or minus to zoom, and Enter to mark the crosshair."></canvas>
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
            <button type="button" class="btn" data-tool="colours" aria-pressed="false">Star colours</button>
            <button type="button" class="btn" data-tool="difference" aria-pressed="false">Difference</button>
          </div>
        </div>
        <figcaption id="first-star-caption"></figcaption>
        <details class="first-star-provenance">
          <summary>Observation data and processing</summary>
          <p>NASA/JPL-Caltech/IPAC · SPHEREx ${esc(first.set.frames.data_release)} · ${fieldArcmin}′ field. The original views are SkyShift median stacks with the sky background subtracted and one shared display stretch.</p>
          <p id="first-star-processing"></p>
          <div class="first-star-archive" hidden>
            <p>The dashed green circle marks the archived position projected onto the observation grid.</p>
            <p><a href="${esc(first.set.jsonUrl)}">Visit metadata and image arrays</a> · <a href="${ARCHIVE}">SPHEREx archive</a></p>
          </div>
        </details>
      </figure>
      <div class="first-star-actions">
        <p class="first-star-feedback" role="status"></p>
        <button type="button" class="btn btn-primary" data-control="continue"></button>
        <div class="first-star-secondary">
          <button type="button" class="btn btn-ghost" data-control="undo" hidden>Undo landmark</button>
          <button type="button" class="btn btn-ghost" data-control="focus" hidden>Zoom to my mark</button>
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
  const details = [...comparison.querySelectorAll("canvas")];
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
  let candidate: Point | undefined;
  let thirdMark: Point | undefined;
  let shownColours = false;
  let seenVisitTwo = false;
  let hints = 0;
  let revealed = false;
  let revealTimer: ReturnType<typeof setTimeout> | undefined;

  const stage = () => step === FIRST_STAR_STEPS.length ? "found" : FIRST_STAR_STEPS[step].id;
  const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
  const nearTarget = (at: Point, index: number) => distance(at, data.positions[index]) <= MATCH_RADIUS_PIXELS;
  const landmarkPatch = () => cropAround(landmarks, size, 9, 24);
  const comparePatch = (at: Point) => cropAround([at], size, 12, 24);
  const field = mountFirstStarField(canvas, world, () => {
    const current = stage();
    const marks = landmarks.map((at, i) => ({ at, kind: "landmark" as const, label: String(i + 1) }));
    const activeMark = visit === 2 ? thirdMark : candidate;
    const showLandmarks = current === "landmarks" || current === "colours" || current === "next-visit" ||
      current === "compare" || current === "verify";
    const showPatch = current === "mark" || current === "close-look" || current === "difference" || current === "originals";
    const clue = hints && current !== "found" ? clueRegion(data.positions, world, hints) : undefined;
    return {
      image: pictures[tool][visit],
      marks: [
        ...marks,
        ...(activeMark ? [{ at: activeMark, kind: "candidate" as const, label: "Your mark" }] : []),
        ...(revealed ? [{ at: data.positions[visit], kind: "archive" as const, label: "Archive position" }] : []),
      ],
      guides: [
        ...(showLandmarks && landmarks.length ? [{
          patch: landmarkPatch(), label: "Your landmarks", kind: "landmarks" as const,
        }] : []),
        ...(showPatch && candidate ? [{
          patch: comparePatch(candidate), label: "Compare this patch", kind: "compare" as const,
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
      case "colours": return shownColours;
      case "next-visit": return seenVisitTwo;
      case "mark": return !!candidate;
      case "difference": return tool === "difference";
      case "originals": return tool === "original";
      case "verify": return visit === 2 && !!thirdMark;
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
    const coloursAvailable = current === "colours" || current === "next-visit";
    const differenceAvailable = current === "difference" || current === "originals";
    $(".first-star-filters").hidden = !(coloursAvailable || differenceAvailable);
    const suggestedTool = current === "colours" ? "colours" : current === "difference" ? "difference"
      : current === "originals" ? "original" : null;
    toolButtons.forEach(button => {
      const mode = button.dataset.tool;
      button.hidden = mode === "colours" ? !coloursAvailable : mode === "difference" && !differenceAvailable;
      button.setAttribute("aria-pressed", String(mode === tool));
      button.classList.toggle("is-suggested", !canContinue && mode === suggestedTool);
    });
    visitButtons.forEach((button, i) => {
      button.hidden = i > maxVisit;
      button.disabled = found || (tool === "difference" && i === 0);
      button.setAttribute("aria-pressed", String(i === visit));
      button.classList.toggle("is-suggested",
        (current === "next-visit" && !seenVisitTwo && i === 1) ||
        (current === "verify" && visit !== 2 && i === 2));
    });
    const date = ({ epoch }: FirstStarData["originals"][number]) => formatMjd(epoch.mjd_mid);
    const dataVisit = data.originals[visit];
    const band = `D${FIRST_STAR_DETECTOR}`;
    $(".first-star-active-visit").textContent = tool === "difference"
      ? `Visit ${visit + 1} − Visit 1`
      : `Visit ${visit + 1} · ${date(dataVisit)}`;
    const comparingCandidate = current === "close-look" || current === "originals" && tool === "original";
    const patch = current === "compare" ? landmarkPatch()
      : comparingCandidate && candidate ? comparePatch(candidate) : undefined;
    comparison.hidden = !patch;
    guide.classList.toggle("has-comparison", !!patch);
    if (patch) details.forEach((detail, i) => drawFirstStarPatch(detail, pictures.original[i], patch));
    const dateLine = `Visit ${visit + 1} · ${date(dataVisit)} UTC`;
    const bandLine = `${band} · ${dataVisit.epoch.um_min.toFixed(2)}–${dataVisit.epoch.um_max.toFixed(2)} µm · ${dataVisit.epoch.n_exposures} exposures`;
    const legend = tool === "colours" ? "Infrared channels mapped to blue (D1), green (D2), and red (D3)." :
      tool === "difference" ? `${band} relative to ${date(first)} UTC. Red shows more light and blue shows less.` : bandLine;
    $("#first-star-caption").textContent = `${dateLine} · ${legend} · NASA SPHEREx`;
    const processing = $("#first-star-processing");
    const colourDates = first.set.frames.epochs
      .filter(epoch => VISIT_DETS.nir.includes(epoch.det) && visitOf(epoch.id) === visitOf(dataVisit.epoch.id))
      .sort((a, b) => a.det - b.det)
      .map(epoch => `D${epoch.det} · ${formatMjd(epoch.mjd_mid, true)}`)
      .join(" / ");
    processing.textContent = tool === "difference"
      ? `Difference smooths the ${band} images to SPHEREx’s resolution, subtracts Visit 1 from the selected visit, and uses the same signed colour scale for both comparisons.`
      : tool === "colours"
        ? `Star colours combines three infrared detector stacks to help you recognize the field. The stack midpoints are ${colourDates}.`
        : `The stack midpoint is ${formatMjd(dataVisit.epoch.mjd_mid, true)}. Blank pixels have no usable measurement.`;
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
        candidate = at;
        feedback.textContent = "Mark placed. You can move it by selecting another spot.";
        break;
      case "verify":
        if (visit === 2) thirdMark = at;
        else { candidate = at; thirdMark = undefined; }
        feedback.textContent = visit === 2 ? "Third-visit mark placed." : "Earlier mark updated. Check Visit 3 again when ready.";
        break;
      default: return;
    }
    render();
  }

  function showVisit(index: number) {
    if (index === visit) return;
    visit = index;
    if (stage() === "next-visit" && index === 1) seenVisitTwo = true;
    feedback.textContent = "";
    render();
  }

  function advance() {
    if (!ready()) return;
    if (stage() === "found") {
      step = visit = hints = 0;
      tool = "original";
      landmarks = [];
      candidate = thirdMark = undefined;
      shownColours = seenVisitTwo = revealed = false;
      field.reset();
    } else if (stage() === "verify") {
      if (!candidate || !thirdMark || !nearTarget(candidate, 0) || !nearTarget(thirdMark, 2)) {
        feedback.textContent = "Those marks need another look. Compare Visits 1 and 2 again, then try the third visit.";
        return;
      }
      step++;
      field.focus(data.positions[2], 4);
      revealTimer = setTimeout(() => {
        revealed = true;
        render();
      }, 1200);
    } else {
      step++;
      if (stage() === "compare") tool = "original";
      if (stage() === "close-look" && candidate) field.focus(candidate, 3);
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
      if (tool === "colours") shownColours = true;
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
  control("focus").onclick = () => { if (candidate) field.focus(candidate, 3); };
  control("hint").onclick = () => {
    hints++;
    field.reset();
    feedback.textContent = hints === 1
      ? "Try looking inside the outlined patch of sky."
      : "Look inside the smaller box. What looks different between the visits?";
    render();
  };
  render();
}
