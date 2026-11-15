// Check the composition filter (layer A3) on real sky, through the same code the site runs.
// Reads NASA's map live, so it needs the network:
//   cd web && npx vite-node src/colour/check_composition.ts            (the fields of docs/colour-layers.md)
//   cd web && npx vite-node src/colour/check_composition.ts 148.968 69.68   (any position, degrees)
import { pixelToSky } from "../app/astro";
import type { TileInfo } from "./cube";
import { BANDS, GROUPS, markerOf } from "./compview";
import { FieldReader } from "./engine";
import { INDEX_IDS } from "./indices";

const FIELD_ARCMIN = 30;
const FIELDS: { name: string; ra: number; dec: number; expect: string }[] = [
  { name: "Epsilon Indi B", ra: 331.09668, dec: -56.80067, expect: "methane and water vapour found at the centre" },
  { name: "WISE 0855-0714", ra: 133.75969, dec: -7.24216, expect: "centre: can't tell (blended, faint at short wavelengths)" },
  { name: "Barnard's Star", ra: 269.4462, dec: 4.76883, expect: "centre: can't tell (too bright)" },
  { name: "R Draconis", ra: 248.16723, dec: 66.75502, expect: "centre: can't tell (too bright)" },
  { name: "M82", ra: 148.968, dec: 69.68, expect: "PAH glow squares beside the galaxy; its core too bright" },
  { name: "Hunt zone (empty sky)", ra: 125.5, dec: 19.4, expect: "nothing found" },
  { name: "Cygnus (crowded)", ra: 305.0, dec: 40.0, expect: "nothing found" },
  // Star-forming clouds: stars seen through cold dust, where ices are expected.
  { name: "Rho Ophiuchi cloud", ra: 246.7, dec: -24.4, expect: "ices on several sources; no methane" },
  { name: "NGC 1333", ra: 52.27, dec: 31.35, expect: "ices on several sources, PAH glow squares; no methane" },
  { name: "Chamaeleon I", ra: 167.0, dec: -77.3, expect: "ices on several sources; no methane" },
  { name: "Serpens core", ra: 277.49, dec: 1.23, expect: "ices on several sources; no methane" },
];

const argv = (globalThis as { process?: { argv: string[] } }).process?.argv.slice(2) ?? [];
const fields = argv.length >= 2 ? [{ name: `${argv[0]}, ${argv[1]}`, ra: Number(argv[0]), dec: Number(argv[1]), expect: "" }] : FIELDS;
const infos = new Map<number, Promise<TileInfo>>();
const pct = (v: number) => `${Math.round(100 * v)}%`;
const found: string[] = [];

for (const f of fields) {
  const c = { ra: f.ra, dec: f.dec };
  const reader = new FieldReader(c, FIELD_ARCMIN, (x, y, n, s) => pixelToSky({ x, y }, { pixel_arcsec: s, size: [n, n] }, c), (u, i) => fetch(u, i), infos);
  const t0 = Date.now();
  const pic = await reader.composition(new AbortController().signal, () => {});
  const r = pic.result;
  const mid = (r.n - 1) / 2;
  const tally = { found: 0, possible: 0, none: 0, cant: 0 };
  for (const s of r.sources) tally[markerOf(s).state]++;
  console.log(`\n${f.name} (${f.ra}, ${f.dec}), expected: ${f.expect}. Read in ${((Date.now() - t0) / 1000).toFixed(0)} s.`);
  console.log(`   ${r.sources.length} sources: ${tally.found} found, ${tally.possible} possible, ${tally.none} nothing found, ${tally.cant} can't tell; frame-to-frame jitter ${pct(r.eps)}`);
  const cells = (state: string) => r.blocks.filter((b) => b.state === state).length;
  console.log(`   dust (PAH) glow: sky ${r.glow.sky >= 0 ? "+" : ""}${r.glow.sky.toFixed(4)} MJy/sr at 3.3 µm, cell noise ${r.glow.cellSigma.toFixed(4)}; cells found ${cells("found")}, hint ${cells("possible")}, nothing ${cells("none")}, can't tell ${cells("cant")}; wings ÷ peak ${Number.isFinite(r.glow.wingShare) ? r.glow.wingShare.toFixed(2) : "-"}`);
  console.log(`   zero points (stars used, error scale): ${INDEX_IDS.map((id) => (r.cal[id].ok ? `${id} ${r.cal[id].r0.toFixed(2)} (${r.cal[id].n}, ×${r.cal[id].k.toFixed(2)})` : `${id} not calibrated (${r.cal[id].n})`)).join("; ")}`);
  const centre = r.sources.reduce((a, s) => (Math.hypot(s.x - mid, s.y - mid) < Math.hypot(a.x - mid, a.y - mid) ? s : a), r.sources[0]);
  const line = (s: (typeof r.sources)[number]) =>
    INDEX_IDS.filter((id) => s.bands[id].state === "found" || s.bands[id].state === "possible")
      .map((id) => `${BANDS[id].name} ${BANDS[id].um} ${s.bands[id].state} ${pct(s.bands[id].depth)} ± ${pct(s.bands[id].err)}${s.bands[id].reasons.length ? ` [${s.bands[id].reasons}]` : ""}`)
      .join("; ");
  if (centre && Math.hypot(centre.x - mid, centre.y - mid) <= 2) {
    const m = markerOf(centre);
    const why = [...new Set(INDEX_IDS.flatMap((id) => centre.bands[id].reasons))].join(", ");
    console.log(`   centre source: ${m.state}${m.groups.length ? ` (${m.groups.map((g) => g.label).join(", ")})` : ""}${line(centre) ? `: ${line(centre)}` : why ? ` [${why}]` : ""}`);
  } else console.log("   centre source: none within 2 pixels");
  for (const s of r.sources) {
    if (s === centre || !line(s)) continue;
    console.log(`   at (${(s.x - mid).toFixed(0)}, ${(s.y - mid).toFixed(0)}) px, ${s.snr.toFixed(0)}σ: ${line(s)}`);
    if (markerOf(s).state === "found") found.push(`${f.name}: ${markerOf(s).groups.map((g) => g.label).join(" + ")}`);
  }
}
const count = new Map<string, number>();
for (const x of found) count.set(x, (count.get(x) ?? 0) + 1);
console.log(`\nGroups: ${GROUPS.map((g) => g.label).join(", ")}.`);
console.log(`"Found" away from a field's centre:${count.size ? "" : " none"}`);
for (const [k, v] of count) console.log(`   ${v} × ${k}`);
