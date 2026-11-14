// Parse what people paste: "08h55m10.831s -7d14m42.53s", "08:55:10.8 -07:14:42", "8 55 10.8 -7 14 42",
// "133.795 -7.245", "133.795, -7.245". Returns degrees, or null if it isn't coordinates.
import type { SkyPos } from "../api/types";

const NUM = String.raw`[+-−]?\d+(?:\.\d*)?`;

function sexa(parts: number[], negative: boolean): number {
  const [a, b = 0, c = 0] = parts.map(Math.abs);
  const v = a + b / 60 + c / 3600;
  return negative ? -v : v;
}

export function parseCoords(input: string): SkyPos | null {
  const s = input.trim().replace(/−/g, "-").replace(/[,;]/g, " ").replace(/\s+/g, " ");
  if (!s) return null;

  // Decimal degrees: two numbers.
  const dec2 = s.match(new RegExp(`^(${NUM})\\s*(?:d|°)?\\s+(${NUM})\\s*(?:d|°)?$`));
  if (dec2) {
    const ra = Number(dec2[1]);
    const dec = Number(dec2[2]);
    if (!/[:hms]/.test(s) && ra >= 0 && ra < 360 && Math.abs(dec) <= 90) return { ra, dec };
  }

  // Letter form: 08h55m10.831s -7d14m42.53s
  const letters = s.match(
    /^(\d+(?:\.\d*)?)h\s*(?:(\d+(?:\.\d*)?)m)?\s*(?:(\d+(?:\.\d*)?)s?)?\s+([+-]?)(\d+(?:\.\d*)?)(?:d|°)\s*(?:(\d+(?:\.\d*)?)(?:m|'|′))?\s*(?:(\d+(?:\.\d*)?)(?:s|"|″)?)?$/i,
  );
  if (letters) {
    if ([letters[2], letters[3], letters[6], letters[7]].some(v => Number(v ?? 0) >= 60)) return null;
    const ra = sexa([Number(letters[1]), Number(letters[2] ?? 0), Number(letters[3] ?? 0)], false) * 15;
    const dec = sexa([Number(letters[5]), Number(letters[6] ?? 0), Number(letters[7] ?? 0)], letters[4] === "-");
    return valid({ ra, dec });
  }

  // Colon or space sexagesimal: 08:55:10.8 -07:14:42 or 8 55 10.8 -7 14 42
  const colon = s.match(/^(\d+):(\d+)(?::(\d+(?:\.\d*)?))?\s+([+-]?)(\d+):(\d+)(?::(\d+(?:\.\d*)?))?$/);
  const spaced = s.match(/^(\d+) (\d+) (\d+(?:\.\d*)?) ([+-]?)(\d+) (\d+) (\d+(?:\.\d*)?)$/);
  const m = colon ?? spaced;
  if (m) {
    if ([m[2], m[3], m[6], m[7]].some(v => Number(v ?? 0) >= 60)) return null;
    const ra = sexa([Number(m[1]), Number(m[2]), Number(m[3] ?? 0)], false) * 15;
    const dec = sexa([Number(m[5]), Number(m[6]), Number(m[7] ?? 0)], m[4] === "-");
    return valid({ ra, dec });
  }
  return null;
}

function valid(p: SkyPos): SkyPos | null {
  return p.ra >= 0 && p.ra < 360 && Math.abs(p.dec) <= 90 ? p : null;
}
