import { describe, expect, it } from "vitest";
import file from "../../public/data/spherex-now.json";
import { sepDeg } from "./sphere";
import {
  buildModel,
  covered,
  eclToEq,
  eqToEcl,
  newestBatch,
  overheadAt,
  patternAt,
  releaseLagDays,
  smallCircle,
  sunLon,
  visitsAt,
  type NowFile,
  type NowOrbit,
} from "./spherex-now";

// CelesTrak's elements for SPHEREx (NORAD 63182), epoch 2026-11-26 22:47 UTC.
const ORBIT: NowOrbit = {
  epoch_mjd: 61370.94937333,
  inclination_deg: 97.9565,
  raan_deg: 152.8433,
  eccentricity: 0.0011031,
  arg_perigee_deg: 36.5251,
  mean_anomaly_deg: 323.6708,
  mean_motion_rev_day: 14.74330448,
  mean_motion_dot: 4.24e-6,
};

describe("the Sun and ecliptic coordinates", () => {
  it("puts the Sun at 0° at the March 2026 equinox (20 Mar 14:46 UTC)", () => {
    const l = sunLon(61119.615);
    expect(Math.min(l, 360 - l)).toBeLessThan(0.05);
  });

  it("turns ecliptic coordinates into equatorial ones and back", () => {
    for (const [lon, lat] of [[0, 0], [90, 0], [123.4, -45.6], [271, 89]]) {
      const back = eqToEcl(eclToEq(lon, lat));
      expect(back.lat).toBeCloseTo(lat, 6);
      expect(Math.abs(((back.lon - lon + 540) % 360) - 180)).toBeLessThan(1e-6);
    }
    // The north ecliptic pole is at RA 18h, Dec +66.56°.
    const nep = eclToEq(0, 90);
    expect(nep.ra).toBeCloseTo(270, 3);
    expect(nep.dec).toBeCloseTo(66.5607, 3);
  });
});

describe("the point overhead SPHEREx", () => {
  it("matches full SGP4 from the same elements to a fraction of a degree, a day and a week on", () => {
    // Reference: python sgp4 2.x, geocentric direction of the TEME position.
    expect(sepDeg(overheadAt(ORBIT, 61371.5), { ra: 146.552, dec: 40.381 })).toBeLessThan(0.3);
    expect(sepDeg(overheadAt(ORBIT, 61377.25), { ra: 172.018, dec: -58.109 })).toBeLessThan(0.3);
  });

  it("goes round once an orbit (97.7 min)", () => {
    const a = overheadAt(ORBIT, 61371);
    expect(sepDeg(a, overheadAt(ORBIT, 61371 + 1440 / ORBIT.mean_motion_rev_day / 1440))).toBeLessThan(1);
    expect(sepDeg(a, overheadAt(ORBIT, 61371 + 49 / 1440))).toBeGreaterThan(150);
  });

  it("draws the 35° cone as a closed small circle", () => {
    const c = { ra: 10, dec: 80 };
    const ring = smallCircle(c, 35, 48);
    expect(ring).toHaveLength(49);
    for (const p of ring) expect(sepDeg(p, c)).toBeCloseTo(35, 6);
  });
});

describe("the measured survey pattern", () => {
  const model = buildModel(file as unknown as NowFile);

  it("holds the newest public photos, sorted, with a week for the pattern", () => {
    expect(model.photos.length).toBeGreaterThan(1000);
    for (let i = 1; i < model.photos.length; i++) expect(model.photos[i].mjd).toBeGreaterThanOrEqual(model.photos[i - 1].mjd);
    expect(model.newest).toBe(model.photos[model.photos.length - 1].mjd);
    expect(model.week.length).toBeGreaterThan(200);
  });

  it("turns with the Sun: 30 days on, the same photos sit 29.6° further east along the ecliptic", () => {
    const t = model.newest;
    const a = patternAt(model, t, 50);
    const b = patternAt(model, t + 30, 50);
    const shift = ((sunLon(t + 30) - sunLon(t) + 360) % 360);
    expect(shift).toBeGreaterThan(28);
    expect(shift).toBeLessThan(31);
    a.forEach((p, i) => {
      const ea = eqToEcl(p);
      const eb = eqToEcl(b[i]);
      expect(eb.lat).toBeCloseTo(ea.lat, 6);
      expect(((eb.lon - ea.lon + 360) % 360)).toBeCloseTo(shift, 4);
    });
  });

  it("covers each of the last week's photos at its own time", () => {
    const week = model.photos.filter((p) => p.mjd > model.newest - 7);
    const hit = week.filter((p) => covered(model, p, p.mjd)).length;
    expect(hit).toBe(week.length);
  });

  it("predicts Barnard's Star's visits to within two weeks of the ones we measured", () => {
    // Visit times from web/public/data/barnard/frames.json (one per detector and visit).
    const measured = [60908.68, 60913.33, 60931.74, 61110.43, 61117.63, 61141.7, 61270.77, 61280.61, 61288.25];
    const visits = visitsAt(model, { ra: 269.4521, dec: 4.6934 }, 60785, 61560);
    for (const m of measured) {
      const gap = Math.min(...visits.map((v) => (m < v.start ? v.start - m : m > v.end ? m - v.end : 0)));
      expect(gap).toBeLessThanOrEqual(14);
    }
  });
});

describe("the release clock", () => {
  const f: NowFile = {
    generated: "2026-11-27T12:00:00Z",
    newest_mjd: 61332,
    orbit: null,
    photos: [[10, 20, 61325], [11, 21, 61332]],
    batches: [
      { period: "2026W41_1A", files: 10000, published: "2026-11-18T19:16:32Z", first_mjd: 61318, last_mjd: 61321.5 },
      { period: "2026W42_1A", files: 10000, published: "2026-11-24T16:53:09Z", first_mjd: 61325, last_mjd: 61328.5 },
      { period: "2026W42_2A", files: 10000, published: "2026-11-24T17:41:51Z", first_mjd: 61328.5, last_mjd: 61332 },
    ],
  };

  it("names the newest batch: published 24 Nov with photos of 12–18 Oct", () => {
    const b = newestBatch(buildModel(f))!;
    expect(new Date((b.published - 40587) * 864e5).toISOString().slice(0, 10)).toBe("2026-11-24");
    expect(b.first).toBe(61325);
    expect(b.last).toBe(61332);
  });

  it("measures the delay from the newest photo to its release", () => {
    expect(releaseLagDays(buildModel(f))).toBeCloseTo(61368.737 - 61332, 2);
  });
});
