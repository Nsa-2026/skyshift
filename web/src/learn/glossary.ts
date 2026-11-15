// The glossary: one plain sentence per word, shared by every page.
//   term("parallax")                 → HTML for a dotted-underline button that explains "parallax"
//   term("visit", "visits")          → same entry, different words on the page
//   enhanceTerms(root)               → upgrades <span data-term="visit">visits</span> to that button
// Hover, focus or tap a term to see its sentence; Escape, a tap elsewhere or leaving closes it.
// Facts follow CLAUDE.md "Domain facts" and web/DESIGN.md. Numbers carry units.
import "./term.css";
import { GATES, GATE_TEXT, GRADE_TEXT, type Gate } from "../api/grades";

export interface GlossaryEntry {
  /** Stable id: #/learn/glossary/<slug>. */
  slug: string;
  /** The word as the glossary lists it. */
  term: string;
  /** One plain sentence (two at most). */
  def: string;
  /** Other spellings that find this entry (plurals are found without listing them). */
  aliases?: string[];
}

/** Catalogue checks read better as the catalogue first; the other checks use the lists' sentence. */
const GATE_GLOSSARY: Partial<Record<Gate, string>> = {
  "2mass": "An older infrared survey of the whole sky; the vetting grades C any mover with a 2MASS source within 6″, a star or galaxy that stays put.",
  allwise: "A catalogue from NASA's WISE telescope; the vetting grades C any mover with an AllWISE source within 6″, a star or galaxy that stays put.",
};

export const GLOSSARY: GlossaryEntry[] = [
  {
    slug: "spherex",
    term: "SPHEREx",
    def: "NASA's infrared space telescope, launched in March 2025, that photographs the whole sky every six months in 102 colours.",
  },
  {
    slug: "blink",
    term: "Blink",
    def: "Showing pictures of the same patch of sky one after another, so anything that moved or changed jumps out while the stars stay still.",
    aliases: ["blinking", "blinked"],
  },
  {
    slug: "exposure",
    term: "Exposure",
    def: "One SPHEREx photo: 113 seconds of collecting light, 2040 × 2040 pixels on each detector.",
    aliases: ["photo"],
  },
  {
    slug: "session",
    term: "Session",
    def: "A run of 1 to 5 SPHEREx photos of the same spot, taken 2.1 minutes apart.",
  },
  {
    slug: "visit",
    term: "Visit",
    def: "The 2 to 4 weeks, every six months, when SPHEREx sees one spot of sky: about 21 sessions and 55 to 60 photos.",
  },
  {
    slug: "detector",
    term: "Detector",
    def: "One of SPHEREx's six light sensors: D1 to D3 see 0.75 to 2.4 µm and D4 to D6 see 2.4 to 5.0 µm.",
    aliases: ["D1", "D2", "D3", "D4", "D5", "D6"],
  },
  {
    slug: "channel",
    term: "Channel",
    def: "One of SPHEREx's 102 narrow colours, 17 on each detector; together they make a spectrum.",
    aliases: ["102 colours", "colours", "band"],
  },
  {
    slug: "spectrum",
    term: "Spectrum",
    def: "How bright something is at each colour; SPHEREx measures up to 102 points between 0.75 and 5.0 µm.",
    aliases: ["spectra"],
  },
  {
    slug: "infrared",
    term: "Infrared",
    def: "Light with longer wavelengths than red, invisible to our eyes, which see about 0.4 to 0.7 µm.",
  },
  {
    slug: "micrometre",
    term: "µm (micrometre)",
    def: "A millionth of a metre, the unit for wavelengths of infrared light.",
    aliases: ["µm", "um", "micron", "micrometer", "wavelength"],
  },
  {
    slug: "pixel",
    term: "Pixel",
    def: "One square of a detector; a SPHEREx pixel covers 6.15″ of sky.",
  },
  {
    slug: "arcsecond",
    term: "Arcsecond (″)",
    def: "A tiny angle, 1/3600 of a degree: about the width of a coin seen from 4 km away.",
    aliases: ["arcsec", "″", "arcminute", "′"],
  },
  {
    slug: "psf",
    term: "PSF",
    def: "The point spread function: the blob a single point of light makes in an image; SPHEREx's is 7.53″ wide at half its peak.",
    aliases: ["point spread function"],
  },
  {
    slug: "proper-motion",
    term: "Proper motion",
    def: "How fast a star crosses the sky because it really moves through space, in arcseconds per year (″/yr).",
  },
  {
    slug: "parallax",
    term: "Parallax",
    def: "The small yearly wobble of a nearby star as Earth circles the Sun; the closer the star, the bigger the wobble, so it gives the distance.",
  },
  {
    slug: "parsec",
    term: "Parsec (pc)",
    def: "A distance astronomers use: 3.26 light years, or about 31 trillion km.",
    aliases: ["pc"],
  },
  {
    slug: "ujy",
    term: "µJy (microjansky)",
    def: "A unit of brightness at one colour for faint sources; a star of AB magnitude 23.9 gives 1 µJy.",
    aliases: ["µJy", "uJy", "microjansky", "jansky", "flux"],
  },
  {
    slug: "mjysr",
    term: "MJy/sr",
    def: "Brightness per patch of sky, the unit of SPHEREx images; 1 MJy/sr in one 6.15″ pixel is 889 µJy.",
    aliases: ["MJy / sr", "megajansky per steradian"],
  },
  {
    slug: "magnitude",
    term: "AB magnitude",
    def: "A brightness scale where bigger numbers are fainter: 5 magnitudes is a factor of 100, and AB 23.9 is 1 µJy.",
    aliases: ["magnitude", "mag", "AB"],
  },
  {
    slug: "mjd",
    term: "MJD",
    def: "Modified Julian Date: a running count of days astronomers use for time; MJD 61000 is 21 November 2025.",
    aliases: ["modified julian date"],
  },
  {
    slug: "gaia",
    term: "Gaia",
    def: "ESA's star-mapping spacecraft; its DR3 catalogue gives positions and motions of more than a billion stars, which we move to each visit's date.",
    aliases: ["Gaia DR3"],
  },
  {
    slug: "catwise",
    term: "CatWISE",
    def: "A catalogue made from NASA's WISE infrared images with positions and motions of nearly 1.9 billion sources, including brown dwarfs too cold for Gaia.",
    aliases: ["CatWISE2020", "WISE"],
  },
  {
    slug: "healpix",
    term: "HEALPix",
    def: "A way to cut the sky into tiles of equal area; the Sky map counts objects to check in HEALPix tiles.",
  },
  {
    slug: "stack",
    term: "Stack",
    def: "Several photos combined pixel by pixel (we take the median), so noise drops and one-photo artefacts vanish.",
    aliases: ["median stack", "stacked"],
  },
  {
    slug: "difference",
    term: "Difference image",
    def: "One frame minus another: whatever stayed put cancels, and a mover shows as a pair, one light and one dark.",
    aliases: ["difference", "subtract"],
  },
  {
    slug: "flag",
    term: "Flag",
    def: "A label NASA's pipeline puts on each pixel of a SPHEREx photo to say what may be wrong with it, such as TRANSIENT or GHOST.",
    aliases: ["flags", "flagged", "mask", "masked"],
  },
  {
    slug: "artefact",
    term: "Artefact",
    def: "Something in a picture that isn't in the sky: a particle hit, a reflection, an afterimage or the edge of a detector.",
    aliases: ["artifact", "fake"],
  },
  {
    slug: "cosmic-ray",
    term: "Cosmic ray",
    def: "A fast particle that hits the detector and leaves a sharp dot in one photo only; NASA flags it TRANSIENT.",
    aliases: ["transient", "particle hit"],
  },
  {
    slug: "ghost",
    term: "Ghost",
    def: "A faint reflection of a bright star inside the telescope, in the wrong place; NASA flags GHOST, GHOST_FPA and GHOST_EXT.",
  },
  {
    slug: "persistence",
    term: "Persistence",
    def: "An afterimage: pixels hit by a bright star keep glowing in the next photos; NASA flags PERSIST.",
  },
  {
    slug: "crosstalk",
    term: "Crosstalk",
    def: "An electronic echo of a bright star in another part of the detector; NASA flags CROSSTALK.",
  },
  {
    slug: "saturation",
    term: "Saturation",
    def: "When a star is so bright that its pixels overflow (flags OVERFLOW or NONLINEAR); we then measure it on its fainter wings.",
    aliases: ["saturated", "saturates"],
  },
  {
    slug: "snr",
    term: "S/N",
    def: "Signal-to-noise: how far a dot stands above the noise; at S/N 5 the signal is five times the typical noise.",
    aliases: ["signal-to-noise", "signal to noise", "SNR"],
  },
  {
    slug: "brown-dwarf",
    term: "Brown dwarf",
    def: "An object between a planet and a star, too small to shine by fusing hydrogen; cold ones glow mostly near 4 to 5 µm.",
  },
  {
    slug: "methane",
    term: "Methane",
    def: "A molecule (CH₄) that absorbs light near 1.65, 2.3 and 3.3 µm; deep methane dips mark cold brown dwarfs and giant planets.",
    aliases: ["CH4", "CH₄"],
  },
  {
    slug: "asteroid",
    term: "Asteroid",
    def: "A rocky body orbiting the Sun; in SPHEREx it moves between photos taken minutes to hours apart.",
    aliases: ["minor planet"],
  },
  {
    slug: "comet",
    term: "Comet",
    def: "An icy body orbiting the Sun that grows a fuzzy cloud and tail when warmed; 3I/ATLAS came from another star system.",
  },
  {
    slug: "mpc",
    term: "MPC",
    def: "The Minor Planet Center: it collects observations of asteroids and comets from around the world and designates new ones.",
    aliases: ["Minor Planet Center"],
  },
  {
    slug: "consensus",
    term: "Consensus",
    def: "On SkyShift, an object with at least 5 votes where at least 70% agree on one answer.",
  },
  {
    slug: "planet-nine",
    term: "Planet Nine",
    def: "A planet some astronomers think may orbit far beyond Neptune to explain how distant icy bodies move; nobody has seen it yet.",
    aliases: ["Planet X", "Planet 9"],
  },
  {
    slug: "vetting-grade",
    term: "Vetting grade",
    def: "The pipeline's verdict on an unknown mover after its checks: A (candidate mover), B (unexplained single-session source) or C (fails a check).",
    aliases: ["vetting", "grade", "vetted"],
  },
  {
    slug: "grade-a",
    term: "Grade A (candidate mover)",
    def: `A vetting grade. ${GRADE_TEXT.A.plain}`,
    aliases: ["grade A", "candidate mover"],
  },
  {
    slug: "grade-b",
    term: "Grade B (unexplained single-session source)",
    def: `A vetting grade. ${GRADE_TEXT.B.plain}`,
    aliases: ["grade B", "unexplained single-session source", "single-session source"],
  },
  {
    slug: "grade-c",
    term: "Grade C",
    def: "A vetting grade: it fails a pipeline check or sits on a catalogued source, so it's most likely an artefact. It stays listed, greyed, with no vote.",
  },
  // The vetting's checks (gate reasons), in the words the lists use.
  ...GATES.map(
    (code): GlossaryEntry => ({
      slug: code.replace(/_/g, "-"),
      term: GATE_TEXT[code].term,
      def: GATE_GLOSSARY[code] ?? GATE_TEXT[code].plain,
      aliases: [code.replace(/_/g, " "), GATE_TEXT[code].label],
    }),
  ),
];

const norm = (s: string): string =>
  s
    .toLowerCase()
    .normalize("NFKC")
    .replace(/[^a-z0-9µ″′₄]+/g, "");

let index: Map<string, GlossaryEntry> | null = null;
function lookup(): Map<string, GlossaryEntry> {
  if (index) return index;
  index = new Map();
  for (const g of GLOSSARY) {
    for (const k of [g.slug, g.term, g.term.replace(/\s*\(.*\)$/, ""), ...(g.aliases ?? [])]) {
      const n = norm(k);
      if (n && !index.has(n)) index.set(n, g);
    }
  }
  return index;
}

/** The entry for a word, its slug, an alias or a simple plural ("visits", "ghosts"), or undefined. */
export function findTerm(word: string): GlossaryEntry | undefined {
  const m = lookup();
  const n = norm(word);
  if (!n) return undefined;
  return m.get(n) ?? (n.endsWith("es") ? m.get(n.slice(0, -2)) : undefined) ?? (n.endsWith("s") ? m.get(n.slice(0, -1)) : undefined);
}

export const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/**
 * HTML for a glossary word in running text: a dotted-underline button that shows the word's
 * sentence on hover, focus or tap. `label` is what the page shows (default: `word`).
 * An unknown word comes back as plain (escaped) text, so a typo never breaks a page.
 */
export function term(word: string, label?: string): string {
  const g = findTerm(word);
  const text = esc(label ?? word);
  if (!g) return text;
  installTermListeners();
  return `<button type="button" class="gl-term" data-term="${g.slug}">${text}</button>`;
}

/**
 * Upgrade every `[data-term]` element under root that isn't a term button yet (for pages that
 * write their own HTML: `<span data-term="parallax">parallax</span>`). Unknown words stay as text.
 */
export function enhanceTerms(root: ParentNode): void {
  installTermListeners();
  root.querySelectorAll<HTMLElement>("[data-term]:not(.gl-term)").forEach((el) => {
    const g = findTerm(el.dataset.term ?? "");
    if (!g) return;
    const b = el.ownerDocument.createElement("button");
    b.type = "button";
    b.className = "gl-term";
    b.dataset.term = g.slug;
    b.textContent = el.textContent || g.term;
    el.replaceWith(b);
  });
}

// ---- the popover --------------------------------------------------------------------------
// One shared element. Hover and focus describe the word (role tooltip, aria-describedby);
// a click or tap pins it open (aria-expanded) until a second tap, a tap elsewhere or Escape.

export const POP_ID = "gl-pop";
let installed: Document | null = null;
let openFor: HTMLButtonElement | null = null;
let pinned = false;
let hideTimer: ReturnType<typeof setTimeout> | null = null;

function popEl(doc: Document): HTMLElement {
  let pop = doc.getElementById(POP_ID);
  if (!pop) {
    pop = doc.createElement("div");
    pop.id = POP_ID;
    pop.className = "gl-pop";
    pop.setAttribute("role", "tooltip");
    pop.hidden = true;
    doc.body.appendChild(pop);
  }
  return pop;
}

function place(btn: HTMLElement, pop: HTMLElement): void {
  const doc = btn.ownerDocument;
  const win = doc.defaultView;
  if (!win) return;
  const r = btn.getBoundingClientRect();
  const vw = doc.documentElement.clientWidth || win.innerWidth;
  const vh = win.innerHeight;
  const pw = pop.offsetWidth || 280;
  const ph = pop.offsetHeight || 80;
  const gap = 8;
  let left = r.left + r.width / 2 - pw / 2;
  left = Math.max(gap, Math.min(left, vw - pw - gap));
  const below = r.bottom + gap + ph <= vh || r.top - gap - ph < 0;
  const top = below ? r.bottom + gap : r.top - gap - ph;
  pop.dataset.side = below ? "below" : "above";
  pop.style.left = `${Math.round(left + win.scrollX)}px`;
  pop.style.top = `${Math.round(top + win.scrollY)}px`;
}

/** Show a term's sentence next to its button. pin = opened by click or tap. */
export function openTerm(btn: HTMLButtonElement, pin = false): void {
  const g = findTerm(btn.dataset.term ?? "");
  if (!g) return;
  const doc = btn.ownerDocument;
  const pop = popEl(doc);
  cancelHide();
  if (openFor && openFor !== btn) release(openFor);
  openFor = btn;
  pinned = pin;
  pop.innerHTML = `<strong>${esc(g.term)}.</strong> ${esc(g.def)}`;
  pop.hidden = false;
  btn.setAttribute("aria-describedby", POP_ID);
  btn.setAttribute("aria-expanded", String(pin));
  place(btn, pop);
}

function release(btn: HTMLButtonElement): void {
  btn.removeAttribute("aria-describedby");
  btn.setAttribute("aria-expanded", "false");
}

/** Close the popover, wherever it is open. */
export function closeTermPopover(): void {
  cancelHide();
  const doc = openFor?.ownerDocument ?? (typeof document !== "undefined" ? document : null);
  if (openFor) release(openFor);
  openFor = null;
  pinned = false;
  const pop = doc?.getElementById(POP_ID);
  if (pop) pop.hidden = true;
}

/** Which term is open, and whether it was pinned by a click (for tests and other pages). */
export function termState(): { slug: string | null; pinned: boolean } {
  return { slug: openFor?.dataset.term ?? null, pinned };
}

function cancelHide(): void {
  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = null;
}

function hideSoon(): void {
  cancelHide();
  hideTimer = setTimeout(() => {
    if (!pinned) closeTermPopover();
  }, 150);
}

const termFrom = (t: EventTarget | null): HTMLButtonElement | null =>
  t instanceof Element ? (t.closest("button.gl-term") as HTMLButtonElement | null) : null;

function installTermListeners(): void {
  if (typeof document === "undefined" || installed === document) return;
  installed = document;
  const doc = document;
  doc.addEventListener("click", (ev) => {
    const btn = termFrom(ev.target);
    if (btn) {
      ev.preventDefault();
      if (openFor === btn && pinned) closeTermPopover();
      else openTerm(btn, true);
      return;
    }
    const pop = doc.getElementById(POP_ID);
    if (openFor && !(pop && ev.target instanceof Node && pop.contains(ev.target))) closeTermPopover();
  });
  const inPop = (t: EventTarget | null) => {
    const pop = doc.getElementById(POP_ID);
    return !!pop && t instanceof Node && pop.contains(t);
  };
  doc.addEventListener("mouseover", (ev) => {
    // The sentence itself can be hovered without closing (WCAG 1.4.13).
    if (inPop(ev.target)) return cancelHide();
    const btn = termFrom(ev.target);
    if (btn && !(pinned && openFor && openFor !== btn)) {
      if (openFor !== btn) openTerm(btn, false);
      else cancelHide();
    }
  });
  doc.addEventListener("mouseout", (ev) => {
    if (!openFor || pinned) return;
    if (termFrom(ev.target) === openFor || inPop(ev.target)) hideSoon();
  });
  doc.addEventListener("focusin", (ev) => {
    const btn = termFrom(ev.target);
    if (btn && openFor !== btn) openTerm(btn, false);
  });
  doc.addEventListener("focusout", (ev) => {
    if (termFrom(ev.target) !== openFor) return;
    if (pinned) closeTermPopover();
    else hideSoon();
  });
  doc.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && openFor) closeTermPopover();
  });
  const reflow = () => {
    if (openFor && openFor.isConnected) place(openFor, popEl(doc));
    else if (openFor) closeTermPopover();
  };
  doc.defaultView?.addEventListener("resize", reflow);
  // A hash change swaps pages: never leave a sentence floating over the next one.
  doc.defaultView?.addEventListener("hashchange", () => closeTermPopover());
}
