import { describe, it, expect } from 'vitest';
import { frameViewport } from './viewport';
describe('astronomical viewport', () => {
    it('covers a wide screen without stretching pixels or changing the target centre', () => {
        const v = frameViewport(100, 100, 1200, 600, 1, { u: 50, v: 50 }, true);
        expect(v.s).toBe(12);
        expect(v.vw).toBe(100);
        expect(v.vh).toBe(50);
        expect(v.v0).toBe(25);
        expect(v.ox).toBe(0);
        expect(v.oy).toBe(0);
        expect((50 - v.u0) * v.s).toBe(600);
        expect((50 - v.v0) * v.s).toBe(300);
    });
    it('fits the complete field on request and clamps panning at image boundaries', () => {
        const fit = frameViewport(100, 100, 1200, 600, 1, { u: 50, v: 50 });
        expect(fit.s).toBe(6);
        expect(fit.ox).toBe(300);
        const zoom = frameViewport(100, 100, 400, 800, 2, { u: -100, v: 500 }, true);
        expect(zoom.u0).toBe(0);
        expect(zoom.v0 + zoom.vh).toBe(100);
    });
});
