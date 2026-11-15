// Share the existing, retryable loader with Stories. Opening either page first must not
// insert a second copy of the historical sky scripts or redefine <guxing-card>.
import { loadGuxing } from '../learn/stories-data';

export type Mansion = { index: number; zh: string; en: string; du: number };
export type Location = { mansion: Mansion; nearest: { asterism_zh: string }[] };
export type Guxing = {
  locate(ra: number, dec: number, options?: Record<string, unknown>): Location;
  mansion(ra: number, dec: number, epoch?: number): Mansion;
  segments(ra: number, dec: number, radius: number): { zh: string; en: string; segments: [[number, number], [number, number]][] }[];
};
type Libraries = Awaited<ReturnType<typeof loadGuxing>>;
let loaded: Libraries | undefined;

export async function loadCulture(): Promise<void> { loaded = await loadGuxing(); }
export function culture() {
  if (!loaded) throw new Error('古星资料还没准备好，请重试。');
  return { gx: loaded.gx as unknown as Guxing, nasa: loaded.nasa };
}
