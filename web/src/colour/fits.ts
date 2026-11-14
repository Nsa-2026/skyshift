// Just enough FITS to read the SPHEREx cube tiles: header cards in 2880-byte blocks, ended by END.

export const BLOCK = 2880;
const CARD = 80;

export type Cards = Record<string, string | number | boolean>;

function value(raw: string): string | number | boolean {
  const s = raw.trim();
  if (s.startsWith("'")) {
    const end = s.indexOf("'", 1);
    return (end > 0 ? s.slice(1, end) : s.slice(1)).trimEnd();
  }
  const v = s.split("/")[0].trim();
  if (v === "T") return true;
  if (v === "F") return false;
  const n = Number(v);
  return Number.isFinite(n) && v !== "" ? n : v;
}

/**
 * Parse one header starting at byte `start` of `bytes`. Returns the cards and the header's length
 * in bytes (whole blocks), or null when END isn't inside `bytes` yet (fetch more).
 */
export function parseHeader(bytes: Uint8Array, start = 0): { cards: Cards; length: number } | null {
  const cards: Cards = {};
  for (let off = start; off + CARD <= bytes.length; off += CARD) {
    const card = String.fromCharCode(...bytes.subarray(off, off + CARD));
    const key = card.slice(0, 8).trim();
    if (key === "END") {
      const used = off + CARD - start;
      return { cards, length: Math.ceil(used / BLOCK) * BLOCK };
    }
    if (card.slice(8, 10) === "= ") cards[key] = value(card.slice(10));
  }
  return null;
}

/** Bytes of an HDU's data part, padded to whole blocks. */
export function dataLength(cards: Cards): number {
  const naxis = Number(cards.NAXIS ?? 0);
  if (!naxis) return 0;
  let n = Math.abs(Number(cards.BITPIX)) / 8;
  for (let i = 1; i <= naxis; i++) n *= Number(cards[`NAXIS${i}`]);
  return Math.ceil(n / BLOCK) * BLOCK;
}
