import type { SkyPos } from '../api/types';
import type { Vec } from './marks';
import { densifyRing, type RaDecBox, type ZoneArea } from './zones';

type Project = (p: SkyPos) => Vec | null;

/** Project outlines without ever joining opposite sides of the all-sky seam. */
export function zoneEdges(area: ZoneArea, project: Project, maxJump: number): Vec[][] {
  const paths: Vec[][] = [];
  for (const ring of area.rings) {
    const points = densifyRing(ring);
    if (!points.length) continue;
    let path: Vec[] = [];
    for (const [ra, dec] of [...points, points[0]]) {
      const p = project({ ra, dec });
      const prev = path[path.length - 1];
      if (!p || (prev && Math.hypot(p.x - prev.x, p.y - prev.y) > maxJump)) {
        if (path.length > 1) paths.push(path);
        path = [];
      }
      if (p) path.push(p);
    }
    if (path.length > 1) paths.push(path);
  }
  return paths;
}

/**
 * Fill the boxes as one compound path. Subdivide only at a projection seam or limb;
 * a tiny unresolved boundary is omitted instead of filling across the whole sky.
 * All boxes have the same winding, so a single nonzero fill paints overlaps once.
 */
export function zoneFills(area: ZoneArea, project: Project, maxJump: number): Vec[][] {
  const paths: Vec[][] = [];
  function add(b: RaDecBox, depth: number) {
    const ring: [number, number][] = [[b.ra0, b.dec0], [b.ra1, b.dec0], [b.ra1, b.dec1], [b.ra0, b.dec1]];
    const projected = densifyRing(ring).map(([ra, dec]) => project({ ra, dec }));
    if (projected.every(p => p === null)) return;
    if (projected.every((p, i) => {
      const prev = projected[(i + projected.length - 1) % projected.length];
      return p && prev && Math.hypot(p.x - prev.x, p.y - prev.y) <= maxJump;
    })) {
      paths.push(projected as Vec[]);
      return;
    }
    if (depth >= 12 || Math.max(b.ra1 - b.ra0, b.dec1 - b.dec0) < 0.06) return;
    if (b.ra1 - b.ra0 >= b.dec1 - b.dec0) {
      const mid = (b.ra0 + b.ra1) / 2;
      add({ ...b, ra1: mid }, depth + 1);
      add({ ...b, ra0: mid }, depth + 1);
    } else {
      const mid = (b.dec0 + b.dec1) / 2;
      add({ ...b, dec1: mid }, depth + 1);
      add({ ...b, dec0: mid }, depth + 1);
    }
  }
  for (const box of area.boxes) add(box, 0);
  return paths;
}
