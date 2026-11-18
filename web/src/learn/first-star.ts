import "./first-star.css";
import { visitOf, VISIT_DETS } from "../colour/visit";
import { formatMjd, formatRaDec } from "../app/astro";
import { clueRegion, type Point } from "../play/hunt-model";
import { esc } from "./glossary";
import { FIRST_STAR, FIRST_STAR_DETECTOR, firstStarPictures, loadFirstStar, type FirstStarData, type StarTool } from "./first-star-data";
import { mountFirstStarField } from "./first-star-field";
import { FIRST_STAR_STEPS, FIRST_STAR_TITLE } from "./first-star-steps";

const ARCHIVE = "https://irsa.ipac.caltech.edu/Missions/spherex.html";
const STEP_COUNT = FIRST_STAR_STEPS.length + 1;
const MATCH_RADIUS_PIXELS = 4;

export function mountFirstStar(host: HTMLElement, signal: AbortSignal) {
  host.innerHTML = `<article class="first-star" aria-labelledby="ln-h">
    <a class="first-star-back" href="#/learn">← Learn</a>
    <h1 id="ln-h" tabindex="-1">${FIRST_STAR_TITLE}</h1>
    <p class="first-star-intro">Retrace the evidence for a known star using real NASA SPHEREx observations. About 8–10 minutes.</p>
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
  const [width, height] = first.set.frames.size;
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
      <figure class="first-star-observation" aria-labelledby="first-star-caption">
        <div class="first-star-frame">
          <canvas tabindex="0" aria-label="NASA SPHEREx infrared star field. Use arrow keys to pan, plus or minus to zoom, and Enter to mark the crosshair."></canvas>
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
  const canvas = $<HTMLCanvasElement>("canvas");
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
  let switches = 0;
  let zoomed = false;
  let hints = 0;
  let revealed = false;
  let revealTimer: ReturnType<typeof setTimeout> | undefined;

  const stage = () => step === FIRST_STAR_STEPS.length ? "found" : FIRST_STAR_STEPS[step].id;
  const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
  const nearTarget = (at: Point, index: number) => distance(at, data.positions[index]) <= MATCH_RADIUS_PIXELS;
  const field = mountFirstStarField(canvas, world, () => {
    const marks = landmarks.map((at, i) => ({ at, kind: "landmark" as const, label: String(i + 1) }));
    const activeMark = visit === 2 ? thirdMark : candidate;
    return {
      image: pictures[tool][visit],
      marks: [
        ...marks,
        ...(activeMark ? [{ at: activeMark, kind: "candidate" as const, label: "Your mark" }] : []),
        ...(revealed ? [{ at: data.positions[visit], kind: "archive" as const, label: "Archive position" }] : []),
      ],
      clue: hints && stage() !== "found" ? clueRegion(data.positions, world, 1) : undefined,
    };
  }, selectPoint, magnification => {
    if (stage() === "close-look" && magnification >= 2) {
      zoomed = true;
      render();
    }
  });

  signal.addEventListener("abort", () => { field.destroy(); clearTimeout(revealTimer); }, { once: true });

  function ready() {
    switch (stage()) {
      case "landmarks": return landmarks.length === 3;
      case "colours": return shownColours;
      case "next-visit": return visit === 1;
      case "first-visit": return visit === 0;
      case "compare": return switches >= 2;
      case "mark": return !!candidate;
      case "close-look": return zoomed && switches > 0;
      case "difference": return tool === "difference";
      case "originals": return tool === "original" && switches > 0;
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
    next.disabled = !ready();
    next.hidden = found && !revealed;
    $(".first-star-actions").classList.toggle("is-complete", found);
    control("undo").hidden = current !== "landmarks" || !landmarks.length;
    control("focus").hidden = current !== "close-look";
    control("hint").hidden = step < 5 || found || hints >= 2;
    const coloursAvailable = current === "colours" || current === "next-visit";
    const differenceAvailable = current === "difference" || current === "originals";
    $(".first-star-filters").hidden = !(coloursAvailable || differenceAvailable);
    toolButtons.forEach(button => {
      const mode = button.dataset.tool;
      button.hidden = mode === "colours" ? !coloursAvailable : mode === "difference" && !differenceAvailable;
      button.setAttribute("aria-pressed", String(mode === tool));
    });
    visitButtons.forEach((button, i) => {
      button.hidden = i > maxVisit;
      button.disabled = found || (tool === "difference" && i === 0);
      button.setAttribute("aria-pressed", String(i === visit));
    });
    const date = ({ epoch }: FirstStarData["originals"][number]) => formatMjd(epoch.mjd_mid);
    const dataVisit = data.originals[visit];
    const band = `D${FIRST_STAR_DETECTOR}`;
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
    switches++;
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
      shownColours = zoomed = revealed = false;
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
      if (stage() === "first-visit") tool = "original";
    }
    switches = 0;
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
  control("focus").onclick = () => {
    if (!candidate) return;
    field.focus(candidate, 3);
    zoomed = true;
    render();
  };
  control("hint").onclick = () => {
    hints++;
    if (hints === 1) {
      field.reset();
      feedback.textContent = "Try looking inside the outlined patch of sky.";
    } else {
      feedback.textContent = "Switch visits and compare how bright each star looks beside its neighbours.";
    }
    render();
  };
  render();
}
