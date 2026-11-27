// The sky page's state in the URL (web/DESIGN.md "Sky"): #/sky/<ra>,<dec>,<fov>[,gal][,mol][,kinds]
// ra, dec: the view centre in ICRS degrees; fov: the map's field of view in degrees.
// gal: galactic grid; mol: the whole-sky (Mollweide) map instead of the globe;
// kinds: letters of the kinds shown (m fast movers, p moving stars, s odd spectra), left out when
// all are shown, "none" when none are. Pure.
import type { Candidate, CandidateKind } from "../api/types";
import { KINDS, KIND_TEXT } from "./kinds";
import { wrap360 } from "./sphere";

export interface AtlasState {
  ra: number;
  dec: number;
  fov: number;
  gal: boolean;
  mol: boolean;
  kinds: CandidateKind[];
}

export const MIN_FOV = 0.02;
export const MAX_FOV = 360;

const BY_CODE = new Map(KINDS.map((k) => [KIND_TEXT[k].code, k]));

function trim(n: number, digits: number): string {
  return n.toFixed(digits).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

/** Field of view to about three significant figures, never 0. */
function fovText(fov: number): string {
  const digits = fov >= 10 ? 1 : fov >= 1 ? 2 : fov >= 0.1 ? 3 : 4;
  return trim(fov, digits);
}

/** Parse the route parameter (what follows #/sky/). null when it isn't a view. */
export function parseState(param: string | null | undefined): AtlasState | null {
  if (!param) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(param);
  } catch {
    return null;
  }
  const parts = decoded.split(",").map((s) => s.trim());
  if (parts.length < 3) return null;
  const [ra, dec, fov] = parts.slice(0, 3).map(Number);
  if (![ra, dec, fov].every(Number.isFinite) || Math.abs(dec) > 90 || fov <= 0) return null;
  const s: AtlasState = {
    ra: wrap360(ra),
    dec,
    fov: Math.min(MAX_FOV, Math.max(MIN_FOV, fov)),
    gal: false,
    mol: false,
    kinds: [...KINDS],
  };
  for (const tok of parts.slice(3)) {
    const t = tok.toLowerCase();
    if (t === "gal") s.gal = true;
    else if (t === "mol") s.mol = true;
    else if (t === "now") continue; // old links: SPHEREx now is off on every visit, never from the address
    else if (t === "none") s.kinds = [];
    else if (t && [...t].every((ch) => BY_CODE.has(ch))) {
      s.kinds = KINDS.filter((k) => t.includes(KIND_TEXT[k].code));
    }
  }
  return s;
}

/** The route parameter for a state: "122.554,17.004,4.2,gal,mp". */
export function formatState(s: AtlasState): string {
  const parts = [trim(wrap360(s.ra), 4), trim(s.dec, 4), fovText(s.fov)];
  if (s.gal) parts.push("gal");
  if (s.mol) parts.push("mol");
  const shown = KINDS.filter((k) => s.kinds.includes(k));
  if (shown.length === 0) parts.push("none");
  else if (shown.length < KINDS.length) parts.push(shown.map((k) => KIND_TEXT[k].code).join(""));
  return parts.join(",");
}

export function stateHash(s: AtlasState): string {
  return `#/sky/${formatState(s)}`;
}

/** True when two states would show the same view (to skip redundant URL writes and flights). */
export function sameState(a: AtlasState, b: AtlasState): boolean {
  return formatState(a) === formatState(b);
}

/**
 * Static first: an object with prepared frames (frames_url) blinks them on its own page, #/c/<id>,
 * which reads the static files. Null: no prepared frames, so Blink here builds them live (Explore).
 */
export function preparedBlinkHash(c: Pick<Candidate, "id" | "frames_url">): string | null {
  return c.frames_url ? `#/c/${encodeURIComponent(c.id)}` : null;
}
