import { afterEach, describe, expect, it, vi } from "vitest";
import type { Epoch, FramesResponse } from "../api/types";
import { embedCode, shareUrl } from "./candidate";
import { OWN_GAP_DAYS, candidateFrames, candidateView, findGap, focusFrames, gapText, liveMjd, toCandidate, toCandidateSet } from "./data";
import { cand } from "./testkit";

function epoch(id: string, mjd: number, det: 1 | 2 | 3 | 4 | 5 | 6 = 3): Epoch {
  return {
    id,
    kind: "exposure",
    mjd_mid: mjd,
    mjd_start: mjd - 0.0006,
    mjd_end: mjd + 0.0006,
    det,
    um_min: 1.6,
    um_max: 2.4,
    n_exposures: 1,
    flag_frac: 0,
    sat_frac: 0,
    data_url: `${id}.bin`,
    scale: 1,
    zero: 0,
  } as Epoch;
}

function frames(epochs: Epoch[], clock: "hours" | "months" = "hours"): FramesResponse {
  return {
    clock,
    center: { ra: 10, dec: -5 },
    pixel_arcsec: 6.15,
    size: [100, 100],
    epochs,
    gaia: [],
    known: [
      { name: "(59) Elpis", epoch_id: "near1", ra: 10, dec: -5, v: 12, source: "skybot" },
      { name: "(59) Elpis", epoch_id: "far1", ra: 10, dec: -5, v: 12, source: "skybot" },
      { name: "always", epoch_id: null, ra: 10, dec: -5, v: null, source: "most" },
    ],
    data_release: "qr3",
    archive_checked: "2026-11-14",
  } as FramesResponse;
}

describe("toCandidate", () => {
  it("reads a pipeline row and fills safe defaults", () => {
    const c = toCandidate({ id: "x", kind: "mover", ra: 1, dec: 2, det: "4", score: "high", reasons: ["two_exposures", 3] });
    expect(c).toMatchObject({ id: "x", kind: "mover", clock: "hours", det: 4, score: null, reasons: ["two_exposures"], frames_url: null });
    expect(toCandidate({ id: "y", kind: "high_pm", ra: 1, dec: 2, det: 9 })).toMatchObject({ clock: "months", det: 2 });
  });

  it("drops rows it can't place", () => {
    expect(toCandidate(null)).toBeNull();
    expect(toCandidate({ id: "", kind: "mover", ra: 1, dec: 2 })).toBeNull();
    expect(toCandidate({ id: "z", kind: "comet", ra: 1, dec: 2 })).toBeNull();
    expect(toCandidate({ id: "z", kind: "mover", ra: "1", dec: 2 })).toBeNull();
  });

  it("keeps the first of duplicate ids", () => {
    const set = toCandidateSet(
      { candidates: [{ id: "a", kind: "mover", ra: 1, dec: 2 }, { id: "a", kind: "high_pm", ra: 3, dec: 4 }, { bad: 1 }] } as never,
      true,
    );
    expect(set.candidates.map((c) => [c.id, c.kind])).toEqual([["a", "mover"]]);
    expect(set.sample).toBe(true);
  });

  it("carries v3.23's status, known_name and checks when the file has them, and adds nothing when it doesn't", () => {
    const c = toCandidate({
      id: "k", kind: "mover", ra: 1, dec: 2, status: "known", known_name: "306P/LINEAR",
      checks: [{ code: "edge", level: "warn", text: "Near the edge" }, { code: "known_nearby", level: "strong", text: "306P was predicted 2″ away", look: "Hours, D6" }],
    });
    expect(c).toMatchObject({ status: "known", known_name: "306P/LINEAR" });
    expect(c?.checks?.map((k) => k.code)).toEqual(["known_nearby", "edge"]);
    const old = toCandidate({ id: "o", kind: "mover", ra: 1, dec: 2 })!;
    expect(["status", "known_name", "checks"].filter((k) => k in old)).toEqual([]);
  });
});

describe("the Viewer's Previous and Next for a candidate listed apart", () => {
  it("steps through the known bodies, never the objects to check", async () => {
    const { listFor } = await import("./list");
    const rows = [
      { id: "o1", kind: "mover", ra: 1, dec: 2, score: 0.9, checks: [] },
      { id: "k1", kind: "mover", ra: 1, dec: 2, score: 0.8, status: "known", known_name: "Iapetus", checks: [] },
      { id: "o2", kind: "mover", ra: 1, dec: 2, score: 0.7, status: "open", checks: [] },
      { id: "k2", kind: "mover", ra: 1, dec: 2, score: 0.6, status: "known", known_name: "(4512) Sinuhe", checks: [] },
    ].map((r) => toCandidate(r)!);
    const ids = async (q: string) => (await listFor(rows, q, new Map(), () => null)).map((c) => c.id);
    expect(await ids("show=all")).toEqual(["o1", "o2"]);
    expect(await ids("show=all&status=known")).toEqual(["k1", "k2"]);
  });
});

describe("the plate's view and frames", () => {
  it("opens at the candidate's spot, clock and detector", () => {
    expect(candidateView(cand("m", { kind: "mover", clock: "hours", det: 3, ra: 5, dec: 6 }))).toMatchObject({ ra: 5, dec: 6, clock: "hours", det: 3, fov: 20, slug: null });
    expect(candidateView(cand("p")).fov).toBe(5);
  });

  const many = [
    epoch("far0", 60500),
    epoch("far1", 60500.1),
    epoch("near0", 60900.0),
    epoch("near1", 60900.05),
    epoch("near2", 60900.5),
    epoch("near3", 60900.9),
    epoch("other", 60900.05, 6),
    epoch("far2", 61100),
  ];

  it("shows an hours candidate's own day first", () => {
    const f = focusFrames(frames(many), { mjd: 60900.05, clock: "hours" }, 3);
    expect(f.focused).toBe(true);
    expect(f.all).toBe(7);
    expect(f.shown).toBe(4);
    expect(f.frames.epochs.map((e) => e.id)).toEqual(["near0", "near1", "near2", "near3", "other"]);
    // known positions follow their epochs; undated ones stay
    expect(f.frames.known.map((k) => k.epoch_id)).toEqual(["near1", null]);
  });

  it("keeps every epoch for months, or when the day has fewer than 3", () => {
    expect(focusFrames(frames(many, "months"), { mjd: 60900, clock: "months" }, 3).focused).toBe(false);
    const few = focusFrames(frames(many), { mjd: 60500.05, clock: "hours" }, 3);
    expect(few).toMatchObject({ focused: false, all: 7, shown: 7 });
    expect(focusFrames(frames(many), { mjd: 0, clock: "hours" }, 3).focused).toBe(false);
  });

  it("measures how far the nearest frame is from the find", () => {
    // live frames of another session: the nearest of D3 is 0.4 days after the find
    const gap = findGap(frames(many), { mjd: 60899.6, clock: "hours" }, 3)!;
    expect(gap).toBeCloseTo(0.4, 6);
    expect(gap > OWN_GAP_DAYS).toBe(true);
    expect(gapText(gap)).toBe("9.6 hours");
    // the find's own exposure is there (other detectors don't count)
    expect(findGap(frames(many), { mjd: 60900.05, clock: "hours" }, 3)).toBe(0);
    expect(findGap(frames(many), { mjd: 60900.05, clock: "hours" }, 3)! > OWN_GAP_DAYS).toBe(false);
    expect(findGap(frames(many, "months"), { mjd: 60900, clock: "months" }, 3)).toBeNull();
    expect(findGap(frames(many), { mjd: 0, clock: "hours" }, 3)).toBeNull();
    expect(findGap(frames(many), { mjd: 60900, clock: "hours" }, 1)).toBeNull();
  });

  it("counts only the find's own exposures as its own, not the session's next ones", () => {
    // Live frames show a session's first two exposures (ecl22-447170132180-87947503 on 15 Nov); the
    // mover was found in its 3rd and 4th, whose middle is its mjd: the nearest frame is 3.2 min off.
    const t0 = 61074.651374;
    const step = 0.001492;
    const session = [epoch("e1", t0), epoch("e2", t0 + step)];
    const gap = findGap(frames(session), { mjd: t0 + 2.5 * step, clock: "hours" }, 3)!;
    expect(gap * 1440).toBeCloseTo(3.2, 1);
    expect(gap > OWN_GAP_DAYS).toBe(true);
    // Found in the 1st and 2nd: each about a minute from its mjd.
    expect(findGap(frames(session), { mjd: t0 + 0.5 * step, clock: "hours" }, 3)! > OWN_GAP_DAYS).toBe(false);
  });

  it("says a gap in plain words", () => {
    expect(gapText(0.02)).toBe("29 minutes");
    expect(gapText(-0.5)).toBe("12 hours");
    expect(gapText(3.25)).toBe("3.3 days");
    expect(gapText(40)).toBe("40 days");
  });
});

describe("live frames at the find's time (contracts/api.md v3.19)", () => {
  afterEach(() => vi.unstubAllGlobals());

  /** candidateFrames with fetch stubbed: the URLs it asked for. */
  async function asked(c: ReturnType<typeof cand>, clock: "hours" | "months" = c.clock): Promise<string[]> {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(String(url));
        return { ok: true, status: 200, json: async () => frames([epoch("e1", 61074.65, c.det)], clock) };
      }),
    );
    await candidateFrames(c, { ...candidateView(c), clock, fov: clock === "hours" ? 20 : 5 });
    return calls;
  }

  it("asks the live hours clock at a mover's mjd, so its own session is in the answer", async () => {
    // ecl22-447170132180-87947503: no static frames; found in exposures 3-4 of a session.
    const mover = cand("ecl22-447170132180-87947503", { kind: "mover", clock: "hours", det: 4, mjd: 61074.655105 });
    const [url] = await asked(mover);
    const q = new URL(url).searchParams;
    expect(new URL(url).pathname).toBe("/api/frames");
    expect(Object.fromEntries(q)).toEqual({ ra: "10", dec: "-5", clock: "hours", fov: "20", det: "4", mjd: "61074.655105" });
  });

  it("sends no time for months, for an hours candidate seen on months, or without a find time", async () => {
    for (const [c, clock] of [
      [cand("pm", { clock: "months" }), "months"],
      [cand("mv", { kind: "mover", clock: "hours" }), "months"],
      [cand("mv0", { kind: "mover", clock: "hours", mjd: 0 }), "hours"],
    ] as const) {
      const [url] = await asked(c, clock);
      expect(new URL(url).searchParams.has("mjd")).toBe(false);
    }
    expect(liveMjd({ clock: "months", mjd: 60900 }, { clock: "hours" })).toBeUndefined();
    expect(liveMjd({ clock: "hours", mjd: 60900.5 }, { clock: "hours" })).toBe(60900.5);
  });

  it("still reads a candidate's static frames first, without the API", async () => {
    const c = cand("st", { kind: "mover", clock: "hours", det: 4, mjd: 61074.655105, frames_url: "cand/st/frames.json" });
    const urls = await asked(c);
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain("/data/cand/st/frames.json");
  });
});

describe("share", () => {
  it("links to the candidate page", () => {
    expect(shareUrl({ id: "pilot-pm-0043" })).toBe("https://skyshift.web.app/#/c/pilot-pm-0043");
    expect(shareUrl({ id: "a b/c" })).toBe("https://skyshift.web.app/#/c/a%20b%2Fc");
  });

  it("embeds the blink at the candidate's spot", () => {
    const code = embedCode(cand("pilot-pm-0043", { ra: 12.345678, dec: -1.5, det: 2 }));
    expect(code).toMatch(/^<iframe src="https:\/\/skyshift\.web\.app\/\?/);
    expect(code).toContain("ra=12.34568&dec=-1.50000&clock=months&fov=5&det=2&embed=1");
    const odd = embedCode(cand('x"><script>', { kind: "mover", clock: "hours" }));
    expect(odd).not.toContain('"><script>');
  });
});
