// Which known asteroids and comets get a diamond on the plate (P4). The server lists MPC objects
// down to V 21 (contracts/api.md `known`), but SPHEREx sees asteroids only to about V 18: the pilot
// found 83% of them at V 16–17 and 40% at V 17–18. Fainter ones would put diamonds on empty sky
// (RA 125.5, Dec 19.4: 55 of 56 entries were fainter than V 18).
import type { KnownObject } from "../api/types";

/** The faintest known object marked by default, V magnitude. */
export const SPHEREX_V_LIMIT = 18;

/** Bright enough for SPHEREx to show (V ≤ 18), or a comet: MPC gives comets no V, so v is null. */
export function seenBySpherex(k: Pick<KnownObject, "v">): boolean {
  return k.v == null || k.v <= SPHEREX_V_LIMIT;
}
