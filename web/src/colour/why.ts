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
  const who = c.name ?? "The object in the middle";
  const out: Reason[] = [];
  if (c.saturated) {
    out.push({ key: "saturated", title: "Too bright to measure", text: saturationText(who, c.kept) });
  } else if ((c.holes ?? 0) > 0) {
    out.push({
      key: "hole",
      title: "Too bright to measure",
      text:
        `In this colour of light the map has nothing at the centre of ${who} (the striped spot), even though ` +
        `it has data all around it. That happens when a star is too bright: its light overflowed the camera's ` +
        `pixels, and NASA's map throws those readings away.`,
    });
  }
  if (c.blend) {
    out.push({
      key: "blend",
      title: "Mixed with a neighbour",
      text:
        `${who} sits right next to another object. In this blurry map the two share the same pixels, so the ` +
        `colour here mixes both of them. The normal view (Remove filter) is sharper and shows them apart.`,
    });
  }
  if (c.kind && /variable/i.test(c.kind)) {
    out.push({
      key: "variable",
      title: "Its brightness changes",
      text:
        `${who} changes brightness over time. Each colour in this picture was taken on a different day ` +
        `(between ${day(c.dates.beg)} and ${day(c.dates.end)}), so the colours mix brighter and fainter moments.`,
    });
  }
  if (c.moving) {
    out.push({
      key: "moving",
      title: "It moves",
      text:
        `${who} moves across the sky. Each colour in this picture was taken on a different day, so it lands in ` +
        `a different place in each colour, or is missing. This picture shows the background sky instead.`,
    });
  }
  return out;
}

/** Plain words for one saturated pixel in the readout. */
export const SATURATED_READOUT = "too bright to measure here, so this colour is wrong";

const pct = (x: number) => `${Math.round(100 * x)}%`;

/** Colour names of the windows, shortest wavelength first. */
function names(n: number): string[] {
  return n === 3 ? ["blue", "green", "red"] : n === 2 ? ["blue", "red"] : Array.from({ length: n }, (_, i) => `colour ${i + 1}`);
}

/** Which way the colour is off, from how much each window lost. Says only what the numbers show. */
export function saturationText(who: string, kept: { um: number; share: number }[]): string {
  const start =
    `${who} is too bright for SPHEREx. Its light overflowed the camera's pixels, and NASA's map throws ` +
    `those readings away, so the middle of the star is missing some or all of its light.`;
  if (kept.length < 2) return start;
  const nm = names(kept.length);
  const parts = kept.map((k, i) => `${pct(k.share)} in ${nm[i]} light`);
  const list = `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
  const blue = kept[0];
  const red = kept[kept.length - 1];
  let effect: string;
  if (kept.every((k) => k.share < 0.1)) {
    effect =
      "Almost nothing is left in any colour, so what you see is only the edge of the star. It doesn't tell us the star's real colour.";
  } else if (blue.share < 0.5 * red.share) {
    effect = "It lost much more blue light than red light, so it looks redder than it really is.";
  } else if (red.share < 0.5 * blue.share) {
    effect = "It lost much more red light than blue light, so it looks bluer than it really is.";
  } else {
    effect = "Every colour lost some light, so its colour could be off either way.";
  }
  return (
    `${start} In the middle of the star the map kept only ${list} of its usual readings. ${effect} The red ` +
    `squares show the affected spots. It might be possible to fix this with NASA's original, sharper pictures.`
  );
}
