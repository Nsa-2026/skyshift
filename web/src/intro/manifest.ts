// The opening's images, from public/opening/manifest.json (documented in
// public/opening/README.md). Swap an image by dropping a file next to the manifest and editing
// its slot: no code change. Anything missing or wrong falls back to what ships today: the
// SPHEREx sky file, and Earth and hills drawn in code (scene.ts).

export const OPENING_DIR = "/opening/";
export const MANIFEST_URL = `${OPENING_DIR}manifest.json`;

export const SLOT_IDS = ["sky", "earth", "horizon"] as const;
export type SlotId = (typeof SLOT_IDS)[number];

/** "real": a photo or data image (its credit shows as given). "illustration": drawn or generated. */
export type SlotKind = "real" | "illustration";

export interface Slot {
  id: SlotId;
  kind: SlotKind;
  /** File name inside /opening/, or null: drawn in code (earth, horizon) or no image (sky). */
  file: string | null;
  alt: string;
  /** Width:height, e.g. "16:10". */
  aspect: string;
  /** Smallest acceptable [width, height] in pixels. */
  min_px: [number, number];
  credit: string;
  brief: string;
}
export type Slots = Record<SlotId, Slot>;

/** What each slot is called in the credit line when code draws it. */
const DRAWN_NAME: Record<SlotId, string> = { sky: "sky", earth: "Earth", horizon: "hills" };

/** Today's opening: the fallback for every slot, and for the whole manifest. */
export const DEFAULT_SLOTS: Slots = {
  sky: {
    id: "sky",
    kind: "real",
    file: "spherex-sky.webp",
    alt: "The Milky Way in Aquila and Scutum as SPHEREx saw it at 1.35 µm: a bright band of stars crossed by dark dust lanes.",
    aspect: "16:10",
    min_px: [1600, 1000],
    credit: "Sky: SPHEREx DR1 at 1.35 µm, NASA/JPL-Caltech/IPAC (IRSA).",
    brief: "",
  },
  earth: {
    id: "earth",
    kind: "illustration",
    file: null,
    alt: "Earth from space, half in night.",
    aspect: "1:1",
    min_px: [1600, 1600],
    credit: "Earth",
    brief: "",
  },
  horizon: {
    id: "horizon",
    kind: "illustration",
    file: null,
    alt: "Dark hills at night.",
    aspect: "3:1",
    min_px: [2400, 800],
    credit: "hills",
    brief: "",
  },
};

const IMAGE_FILE = /^[\w-]+(\/[\w-]+)*\.(webp|png|jpe?g|avif)$/i;
const ASPECT = /^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$/;

const isText = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";

/** Width / height from "16:10", or NaN. */
export function aspectRatio(aspect: string): number {
  const m = ASPECT.exec(aspect);
  return m ? Number(m[1]) / Number(m[2]) : NaN;
}

/**
 * Read the manifest. Each slot is checked on its own: a bad one falls back to today's slot, and
 * every reason lands in `problems` (the console shows them; the manifest test fails on them).
 */
export function parseManifest(raw: unknown): { slots: Slots; problems: string[] } {
  const slots: Slots = { ...DEFAULT_SLOTS };
  const problems: string[] = [];
  const list = (raw as { slots?: unknown } | null)?.slots;
  if (!Array.isArray(list)) return { slots, problems: ["manifest has no slots list"] };
  for (const item of list as Record<string, unknown>[]) {
    const id = item?.id as SlotId;
    if (!SLOT_IDS.includes(id)) {
      problems.push(`unknown slot ${JSON.stringify(item?.id)}`);
      continue;
    }
    const fallback = DEFAULT_SLOTS[id];
    const bad = (why: string) => problems.push(`${id}: ${why}`);
    // Anything not marked real is labelled an illustration: never pass art off as data.
    const kind: SlotKind = item.kind === "real" ? "real" : "illustration";
    if (item.kind !== "real" && item.kind !== "illustration") bad(`kind must be "real" or "illustration"`);
    const file = item.file === null ? null : isText(item.file) && IMAGE_FILE.test(item.file) && !item.file.includes("..") ? item.file : undefined;
    if (file === undefined) {
      bad(`file must be null or an image name inside /opening/ (.webp, .png, .jpg, .avif)`);
      continue;
    }
    const credit = isText(item.credit) ? item.credit.trim() : "";
    if (file && !credit) {
      // A real image always carries its credit; art always carries its label. No credit, no image.
      bad("an image needs a credit line");
      continue;
    }
    for (const key of ["alt", "brief"] as const) if (!isText(item[key])) bad(`${key} is missing`);
    const aspect = isText(item.aspect) && Number.isFinite(aspectRatio(item.aspect)) ? item.aspect : fallback.aspect;
    if (aspect !== item.aspect) bad(`aspect must look like "16:10"`);
    const min = item.min_px;
    const minOk = Array.isArray(min) && min.length === 2 && min.every((n) => Number.isInteger(n) && n > 0);
    if (!minOk) bad("min_px must be [width, height] in pixels");
    const minPx: [number, number] = minOk ? [min[0], min[1]] : fallback.min_px;
    slots[id] = {
      id,
      kind,
      file,
      alt: isText(item.alt) ? item.alt.trim() : fallback.alt,
      aspect,
      min_px: minPx,
      credit: credit || fallback.credit,
      brief: isText(item.brief) ? item.brief.trim() : "",
    };
  }
  return { slots, problems };
}

/** The slot's image URL, or null when code draws it. */
export function slotUrl(slot: Slot): string | null {
  return slot.file ? OPENING_DIR + slot.file : null;
}

/** Problems with a loaded image's size, against its slot ("" when it fits). */
export function sizeProblem(slot: Slot, width: number, height: number): string {
  const [mw, mh] = slot.min_px;
  if (width < mw || height < mh) return `${slot.id}: ${slot.file} is ${width} × ${height} px, smaller than ${mw} × ${mh}`;
  const want = aspectRatio(slot.aspect);
  if (Math.abs(width / height / want - 1) > 0.03) return `${slot.id}: ${slot.file} is ${width} × ${height} px, not ${slot.aspect}`;
  return "";
}

/** Fetch the manifest; on any failure (offline, slow, bad JSON) use today's slots. */
export async function loadManifest(fetchFn: typeof fetch = fetch, timeoutMs = 2000): Promise<Slots> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    // no-cache: revalidate, so an edited manifest shows on the next visit.
    const res = await fetchFn(MANIFEST_URL, { cache: "no-cache", signal: ctl.signal });
    if (!res.ok) throw new Error(`manifest ${res.status}`);
    const { slots, problems } = parseManifest(await res.json());
    if (problems.length) console.warn(`Opening manifest: ${problems.join("; ")}`);
    return slots;
  } catch {
    return DEFAULT_SLOTS;
  } finally {
    clearTimeout(timer);
  }
}

const sentence = (s: string) => s.trim().replace(/[.\s]+$/, "");
function list(names: string[]): string {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * The credit line under the opening. Real images show their own credit; everything else is
 * labelled "Illustration", whether a file or drawn in code.
 * `shown`: the slots whose image is on screen right now (loaded); the rest are drawn in code.
 */
export function creditLine(slots: Slots, shown: ReadonlySet<SlotId>): string {
  const real: string[] = [];
  const art: string[] = [];
  const drawn: string[] = [];
  for (const id of SLOT_IDS) {
    const slot = slots[id];
    if (shown.has(id)) (slot.kind === "real" ? real : art).push(sentence(slot.credit));
    else if (id !== "sky") drawn.push(DRAWN_NAME[id]);
  }
  drawn.push("figure");
  art.push(`${list(drawn)} drawn in code`);
  return [...real.map((c) => `${c}.`), `Illustration: ${art.join("; ")}.`].join(" ");
}
