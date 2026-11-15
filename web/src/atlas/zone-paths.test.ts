import { describe, expect, it } from 'vitest';
import { zoneAreas } from './zones';
import { zoneEdges, zoneFills } from './zone-paths';
import type { Vec } from './marks';

const area = zoneAreas([{ zone: 'across-zero', count: 10, ra0: 355, ra1: 365, dec0: -3, dec1: 3, center: { ra: 0, dec: 0 } }])[0];
const project = ({ ra, dec }: { ra: number; dec: number }) => ({ x: ((ra % 360) + 360) % 360, y: 50 - dec });
const span = (points: Vec[]) => Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x));

describe('zone projection boundaries', () => {
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
