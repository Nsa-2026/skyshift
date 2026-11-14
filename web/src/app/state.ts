// The view lives in the URL so any blink can be shared: ?ra=&dec=&clock=&fov=&det=&t=<slug>&embed=1
import type { Clock, Det } from "../api/types";
import { ALLOWED_FOV, DEFAULT_FOV } from "../api/client";
import type { View } from "./modules";
import targetsJson from "../data/targets.json";
import { sepArcsec } from "./astro";

export interface Target {
  slug: string;
  name: string;
  short: string;
  ra: number;
  dec: number;
  clock: Clock;
  fov: number;
  det: Det;
  kind: string;
  look_for: string;
  expected: string;
}

export const TARGETS = targetsJson.targets as Target[];

export function targetBySlug(slug: string | null | undefined): Target | undefined {
  return TARGETS.find((t) => t.slug === slug);
}

export function viewForTarget(t: Target): View {
  return { ra: t.ra, dec: t.dec, clock: t.clock, fov: t.fov, det: t.det, slug: t.slug, name: t.name };
}

const isDet = (n: number): n is Det => Number.isInteger(n) && n >= 1 && n <= 6;

export function readView(search: string = location.search): View | null {
  const p = new URLSearchParams(search);
  const t = targetBySlug(p.get("target")) ?? targetBySlug(p.get("t"));
  const base = t ? viewForTarget(t) : null;
  const ra = p.has("ra") ? Number(p.get("ra")) : base?.ra;
  const dec = p.has("dec") ? Number(p.get("dec")) : base?.dec;
  if (ra == null || dec == null || !Number.isFinite(ra) || !Number.isFinite(dec)) return null;
  const clock: Clock = p.get("clock") === "hours" ? "hours" : p.get("clock") === "months" ? "months" : base?.clock ?? "months";
  const fovIn = Number(p.get("fov"));
  const fov = ALLOWED_FOV[clock].includes(fovIn) ? fovIn : base && base.clock === clock ? base.fov : DEFAULT_FOV[clock];
  const detIn = Number(p.get("det"));
  const det: Det = isDet(detIn) ? detIn : base?.det ?? 2;
  return { ra, dec, clock, fov, det, slug: t?.slug ?? null, name: t?.name ?? null };
}

export function writeView(v: View, replace = false): void {
  const p = new URLSearchParams();
  if (v.slug) p.set("t", v.slug);
  else {
    p.set("ra", v.ra.toFixed(5));
    p.set("dec", v.dec.toFixed(5));
  }
  p.set("clock", v.clock);
  p.set("fov", String(v.fov));
  p.set("det", String(v.det));
  if (isEmbed()) p.set("embed", "1");
  const url = `${location.pathname}?${p}${location.hash}`;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
}

export function isEmbed(search: string = location.search): boolean {
  return new URLSearchParams(search).get("embed") === "1";
}

/**
 * The featured target whose prepared field contains this position, if any (within half its
 * field). The challenge's own string, 08h55m10.831s -7d14m42.53s, lands on WISE 0855.
 * Only fixed-position targets: comet and asteroid frames follow the object.
 */
export function featuredNear(pos: { ra: number; dec: number }): Target | undefined {
  let best: Target | undefined;
  let bestSep = Infinity;
  for (const t of TARGETS) {
    if (t.clock !== "months") continue;
    const sep = sepArcsec(pos, t);
    if (sep <= (t.fov * 60) / 2 && sep < bestSep) {
      best = t;
      bestSep = sep;
    }
  }
  return best;
}
