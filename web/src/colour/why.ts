// Why a target's colour isn't reliable in the colour view, in plain words, from what we measured.
// Each reason is shown only when the data (or the target's known nature) says so.

export interface TargetCheck {
  /** Display name, or null for any coordinates. */
  name: string | null;
  /** Featured target kind from targets.json ("star", "variable star", "asteroid", …), or null. */
  kind: string | null;
  /** A saturated pixel within 2 pixels of the centre. */
  saturated: boolean;
  /**
   * Per colour window, shortest wavelength first: the share of the usual exposures the map kept in
   * the 3 × 3 pixels at the centre (exposures there ÷ median of the 7 × 7 box around each, ≤ 1).
   */
  kept: { um: number; share: number }[];
  /** The plate follows the target (epochs carry their own centre), or the kind is a solar-system body. */
  moving: boolean;
  /**
   * Single-wavelength picture: pixels in the 3 × 3 core with no data while the ring around them has
   * data (a hole where overfilled readings were left out). 0 when not checked.
   */
  holes?: number;
  /**
   * Colour picture: the short and long windows peak (≥ 5σ each) in different pixels of the 3 × 3
   * core, so two objects share these coarse pixels. null when they agree or weren't checked.
   */
  blend?: { shortUm: number; longUm: number } | null;
  dates: { beg: string | null; end: string | null };
}

export interface Reason {
  key: "saturated" | "hole" | "blend" | "variable" | "moving";
  title: string;
  text: string;
}

const day = (d: string | null) => (d ? d.slice(0, 10) : "?");

export function whyUnreliable(c: TargetCheck): Reason[] {
  const who = c.name ?? "The object at the centre";
  const out: Reason[] = [];
  if (c.saturated) {
    out.push({ key: "saturated", title: "Too bright for SPHEREx", text: saturationText(who, c.kept) });
  } else if ((c.holes ?? 0) > 0) {
    out.push({
      key: "hole",
      title: "Too bright for SPHEREx",
      text:
        `At this wavelength NASA's map has no data at the core of ${who} (the hatched pixels), though it has ` +
        `data all around it. That is what happens when an object is so bright that it overfilled SPHEREx's ` +
        `detector pixels (they saturated): the map leaves those readings out.`,
    });
  }
  if (c.blend) {
    out.push({
      key: "blend",
      title: "Blended with a neighbour",
      text:
        `Around ${who}, the light at ${c.blend.shortUm.toFixed(2)} µm and the light at ${c.blend.longUm.toFixed(2)} µm ` +
        `peak in different pixels. These pixels are about 13″ wide, so two objects share them and the colour ` +
        `at the centre mixes both. The blink's own frames are sharper and can separate them.`,
    });
  }
  if (c.kind && /variable/i.test(c.kind)) {
    out.push({
      key: "variable",
      title: "Its brightness changes",
      text:
        `${who} is a variable star. Each wavelength of the map was observed on a different date between ` +
        `${day(c.dates.beg)} and ${day(c.dates.end)}, so its colour mixes the star at different brightnesses.`,
    });
  }
  if (c.moving) {
    out.push({
      key: "moving",
      title: "It moves",
      text:
        `${who} moves across the sky, and each wavelength of the map was observed on a different date, so it ` +
        `sits in different places at different wavelengths, or is missing. The picture shows the fixed sky at ` +
        `the plate's centre; this object's colour can't be read from it.`,
    });
  }
  return out;
}

/** Plain words for one saturated pixel in the readout. */
export const SATURATED_READOUT =
  "colour not reliable: too bright, SPHEREx saturated here and NASA's map left those readings out";

const pct = (x: number) => `${Math.round(100 * x)}%`;
const um = (x: number) => `${x.toFixed(2)} µm`;

/** Which way the colour is biased, from how much each window lost. Says only what the numbers show. */
export function saturationText(who: string, kept: { um: number; share: number }[]): string {
  const start =
    `${who} is so bright that it overfilled SPHEREx's detector pixels (they saturated). NASA's all-sky map ` +
    `leaves those overfilled readings out, so the star's core is built from fewer exposures than the sky ` +
    `around it, or none.`;
  if (kept.length < 2) return start;
  const parts = kept.map((k, i) => `${pct(k.share)}${i === 0 ? " of the usual exposures" : ""} at ${um(k.um)}`);
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0];
  const blue = kept[0];
  const red = kept[kept.length - 1];
  let effect: string;
  if (kept.every((k) => k.share < 0.1)) {
    effect =
      "Its core is missing at every wavelength, so the colour you see comes only from the edge of the star, " +
      "where the wavelengths lost different amounts. It says little about the star itself.";
  } else if (blue.share < 0.5 * red.share) {
    effect =
      `The short wavelengths lost far more, so what is left of the core is too faint at ${um(blue.um)} ` +
      `compared with ${um(red.um)}, and the star looks redder than it really is.`;
  } else if (red.share < 0.5 * blue.share) {
    effect =
      `The long wavelengths lost far more, so the core is too faint at ${um(red.um)} compared with ` +
      `${um(blue.um)}, and the star looks bluer than it really is.`;
  } else {
    effect = "The wavelengths lost similar amounts, but the core is still incomplete, so its colour can go either way.";
  }
  return (
    `${start} At the centre the map kept ${list}. ${effect} The red squares mark the ` +
    `pixels affected. This map's pixels are too coarse to rebuild the colour from the light around the core; ` +
    `the original SPHEREx images might allow it (docs/colour-layers.md).`
  );
}
