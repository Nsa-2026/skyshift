import { describe, expect, it } from 'vitest';
import { zoneAreas } from './zones';
import { zoneEdges, zoneFills } from './zone-paths';
import type { Vec } from './marks';
import { mollweide } from '../live/project';
import { wrap180 } from './sphere';

const area = zoneAreas([{ zone: 'across-zero', count: 10, ra0: 355, ra1: 365, dec0: -3, dec1: 3, center: { ra: 0, dec: 0 } }])[0];
const project = ({ ra, dec }: { ra: number; dec: number }) => ({ x: ((ra % 360) + 360) % 360, y: 50 - dec });
const span = (points: Vec[]) => Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x));

describe('zone projection boundaries', () => {
  it.each([-80, 80].flatMap(dec => [80, 805.76, 2400].map(rx => ({ dec, rx }))))('splits the Mollweide seam near Dec $dec at ellipse radius $rx', ({ dec, rx }) => {
    // The screenshot's fitted ellipse: near either pole its full width is below the old 918.5px cutoff.
    const ellipse = { cx: 918.5, cy: 487.5, rx, ry: rx / 2 };
    const polar = zoneAreas([{ zone: 'polar', count: 10, ra0: 355, ra1: 365, dec0: dec - 3, dec1: dec + 3, center: { ra: 0, dec } }])[0];
    const projectPolar = ({ ra, dec }: { ra: number; dec: number }) => {
      const p = mollweide(wrap180(180 - ra) * Math.PI / 180, dec * Math.PI / 180);
      return { x: ellipse.cx + p.x * ellipse.rx / (2 * Math.SQRT2), y: ellipse.cy - p.y * ellipse.ry / Math.SQRT2 };
    };
    const paths = [zoneEdges(polar, projectPolar, 918.5, ellipse), zoneFills(polar, projectPolar, 918.5, ellipse)];
    for (const parts of paths) {
      expect(parts.length).toBeGreaterThan(1);
      expect(parts.some(points => points.some(p => p.x < ellipse.cx))).toBe(true);
      expect(parts.some(points => points.some(p => p.x > ellipse.cx))).toBe(true);
      // A path must stay on one side: no white chord or grey fill across the narrowing ellipse.
      expect(parts.every(points => points.every(p => p.x < ellipse.cx) || points.every(p => p.x > ellipse.cx))).toBe(true);
    }
  });

  it('preserves curved polar outlines and fills that do not cross the seam', () => {
    const ellipse = { cx: 500, cy: 250, rx: 500, ry: 250 };
    const polar = zoneAreas([{ zone: 'polar', count: 10, ra0: 170, ra1: 190, dec0: 77, dec1: 83, center: { ra: 180, dec: 80 } }])[0];
    const projectPolar = ({ ra, dec }: { ra: number; dec: number }) => {
      const p = mollweide(wrap180(180 - ra) * Math.PI / 180, dec * Math.PI / 180);
      return { x: ellipse.cx + p.x * ellipse.rx / (2 * Math.SQRT2), y: ellipse.cy - p.y * ellipse.ry / Math.SQRT2 };
    };
    const edges = zoneEdges(polar, projectPolar, 500, ellipse);
    expect(edges).toHaveLength(1);
    expect(edges[0][0]).toEqual(edges[0].at(-1));
    expect(zoneFills(polar, projectPolar, 500, ellipse)).toHaveLength(1);
  });

  it('keeps both sides of a seam without outlining or filling across the map', () => {
    const edges = zoneEdges(area, project, 180);
    const fills = zoneFills(area, project, 180);
    expect(edges.length).toBeGreaterThan(1);
    expect(fills.length).toBeGreaterThan(1);
    expect([...edges, ...fills].every(points => span(points) <= 10)).toBe(true);
    expect(fills.some(points => points.some(p => p.x < 5))).toBe(true);
    expect(fills.some(points => points.some(p => p.x > 355))).toBe(true);
    // No polygon can paint the horizontal stripe through the middle of the sky.
    expect(fills.every(points => points.every(p => p.x < 6) || points.every(p => p.x > 354))).toBe(true);
  });

  it('retains the original union outline and unsplit fill away from a seam', () => {
    const shifted = ({ ra, dec }: { ra: number; dec: number }) => ({ x: ((ra + 180) % 360), y: 50 - dec });
    const edges = zoneEdges(area, shifted, 180);
    const fills = zoneFills(area, shifted, 180);
    expect(edges).toHaveLength(1);
    expect(fills).toHaveLength(1);
    expect(edges[0][0]).toEqual(edges[0].at(-1));
    expect(span(fills[0])).toBe(10);
  });

  it('clips a partially visible zone without closing outlines across the hidden hemisphere', () => {
    const half = ({ ra, dec }: { ra: number; dec: number }) => ra >= 355 ? { x: ra, y: dec } : null;
    const edges = zoneEdges(area, half, 180);
    const fills = zoneFills(area, half, 180);
    expect(edges.length).toBeGreaterThan(0);
    expect(fills.length).toBeGreaterThan(0);
    expect([...edges, ...fills].flat().every(p => p.x >= 355 && p.x < 360)).toBe(true);
    expect(zoneFills(area, () => null, 180)).toEqual([]);
  });
});
