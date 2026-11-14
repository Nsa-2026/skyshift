import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/api/candidates.json";
import {
  DATA_DOIS,
  DRAFT_LINE,
  MPC_COLUMNS,
  SPHEREX_MPC_CODE,
  citation,
  forumDraft,
  mpcDraft,
  permalink,
  trackletId,
  votesSentence,
} from "./reports";
import type { AssistCandidate, Detection } from "./types";

const all = fixture.candidates as unknown as AssistCandidate[];
const mover = all.find((c) => c.id === "pilot-642020070317-87802695")!;
const star = all.find((c) => c.id === "pilot-pm-0000")!;
const BASE = "https://skyshift.web.app/";

/** A PSV draft's header records (# and ! lines) and its table, cells trimmed (ADES allows padding). */
function parsePsv(text: string) {
  const lines = text.trimEnd().split("\n");
  const head = lines.filter((l) => /^[#!]/.test(l));
  const table = lines.filter((l) => !/^[#!]/.test(l)).map((l) => l.split("|").map((x) => x.trim()));
  return { head, keys: table[0], rows: table.slice(1), raw: lines.filter((l) => !/^[#!]/.test(l)) };
}

const dots: Detection[] = [0, 1, 2].map((i) => ({
  ra: 122.554 - i * 0.0004,
  dec: 17.0035 + i * 0.00004,
  mjd: 60974.09 + (i * 2.1) / 1440,
  det: 3,
  pos_err_arcsec: 0.31,
  flux_ujy: 2320 + i,
  snr: 56,
  sc_pos_km: [4561.2 + i, -3920.7, 3311.4],
}));

describe("MPC ADES PSV draft", () => {
  it("writes the ADES PSV header records for SPHEREx (code 288) and marks the draft", () => {
    const { head } = parsePsv(mpcDraft(mover).text);
    expect(head[0]).toBe("# version=2017");
    expect(head).toContain("# observatory");
    expect(head).toContain(`! mpcCode ${SPHEREX_MPC_CODE}`);
    expect(SPHEREX_MPC_CODE).toBe("288");
    expect(head).toContain("# submitter");
    expect(head).toContain("# measurers");
    expect(head).toContain("# telescope");
    expect(head).toContain("! design Three-mirror anastigmat");
    expect(head).toContain("! aperture 0.2");
    expect(head).toContain("! detector HgCdTe");
    expect(head).toContain("! pixelScale 6.15");
    expect(head).toContain(`! line ${DRAFT_LINE}`);
    expect(DRAFT_LINE).toMatch(/^DRAFT - NOT SUBMITTED\./);
    for (const l of head) {
      expect(l, l).toMatch(/^(# [a-z][A-Za-z]*(=\S+)?|! [a-z][A-Za-z0-9]* .+)$/);
      expect(l, l).not.toContain("|");
    }
  });

  it("has the ADES keywords as the table header, in order, all lower camel case", () => {
    const { keys } = parsePsv(mpcDraft(mover).text);
    expect(keys).toEqual([...MPC_COLUMNS]);
    for (const k of keys) expect(k).toMatch(/^[a-z][A-Za-z0-9]*$/);
  });

  it("lines up the pipes and gives every row one cell per keyword", () => {
    const { raw, rows } = parsePsv(mpcDraft({ ...mover, detections: dots }).text);
    const pipes = (l: string) => [...l.matchAll(/\|/g)].map((m) => m.index);
    for (const l of raw) expect(pipes(l)).toEqual(pipes(raw[0]));
    for (const r of rows) expect(r).toHaveLength(MPC_COLUMNS.length);
  });

  it("writes the mean position as a single line when the dots are not on record", () => {
    const d = mpcDraft(mover);
    expect(d.fromMean).toBe(true);
    expect(d.lines).toBe(1);
    const { rows, keys } = parsePsv(d.text);
    const row = Object.fromEntries(keys.map((k, i) => [k, rows[0][i]]));
    expect(row).toEqual({
      trkSub: trackletId(mover.id),
      mode: "CMO",
      stn: "288",
      obsTime: "2025-10-26T02:14:43.805Z",
      ra: "122.554154",
      dec: "+17.003570",
      rmsRA: "",
      rmsDec: "",
      astCat: "Gaia3",
      sys: "ICRF_KM",
      ctr: "399",
      pos1: "",
      pos2: "",
      pos3: "",
      notes: "Z",
      remarks: "D3 1.6-2.4 um, 2320 uJy, S/N 57, mean of the pipeline's dots",
    });
    expect(d.text).toContain("! line This line is a mean position.");
    expect(d.text).toContain("! line pos1-pos3 are empty");
  });

  it("lists what a person must add before sending", () => {
    const { missing } = mpcDraft(mover);
    expect(missing[0]).toContain("Your name");
    expect(missing.join(" ")).toContain("Each exposure's own position and time");
    expect(missing.join(" ")).toContain("X_SC, Y_SC, Z_SC in km");
    expect(missing.join(" ")).toContain("MPChecker");
    expect(mpcDraft(mover, { dataRelease: "QR3" }).text).toContain("(QR3, NASA/IPAC IRSA)");
  });

  it("writes one line per dot, with SPHEREx's position, when the dots carry it", () => {
    const d = mpcDraft({ ...mover, detections: dots, n_dots: 3 });
    expect(d.fromMean).toBe(false);
    expect(d.lines).toBe(3);
    const { rows, keys } = parsePsv(d.text);
    const col = (k: string) => rows.map((r) => r[keys.indexOf(k)]);
    expect(new Set(col("trkSub")).size).toBe(1);
    expect(col("obsTime")).toEqual(["2025-10-26T02:09:36.000Z", "2025-10-26T02:11:42.000Z", "2025-10-26T02:13:48.000Z"]);
    for (const t of col("obsTime")) expect(t).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
    expect(col("ra")).toEqual(["122.554000", "122.553600", "122.553200"]);
    expect(col("dec")).toEqual(["+17.003500", "+17.003540", "+17.003580"]);
    expect(col("rmsRA")).toEqual(["0.310", "0.310", "0.310"]);
    expect(col("pos1")).toEqual(["4561.200", "4562.200", "4563.200"]);
    expect(col("pos2")[0]).toBe("-3920.700");
    expect(col("remarks")[0]).toBe("D3 1.6-2.4 um, 2320 uJy, S/N 56");
    expect(d.missing.join(" ")).not.toContain("X_SC");
    expect(d.text).not.toContain("mean position");
  });

  it("leaves SPHEREx's position empty on a line whose numbers are not finite", () => {
    const bad = dots.map((x, i) => (i === 1 ? { ...x, sc_pos_km: [Number.NaN, 1, 2] as [number, number, number] } : x));
    const d = mpcDraft({ ...mover, detections: bad });
    const { rows, keys } = parsePsv(d.text);
    expect(rows[1][keys.indexOf("pos1")]).toBe("");
    expect(rows[0][keys.indexOf("pos1")]).toBe("4561.200");
    expect(d.missing.join(" ")).toContain("X_SC");
    expect(d.text).not.toMatch(/NaN|undefined|null/);
  });

  it("keeps pipes and line breaks out of free text", () => {
    const d = mpcDraft({ ...mover, id: "odd|id\nwith break" }, { dataRelease: "A|B" });
    expect(d.text).toContain("! line SkyShift candidate odd/id with break.");
    expect(d.text).toContain("(A/B, NASA/IPAC IRSA)");
  });

  it("makes a stable trkSub of at most 8 alphanumerics", () => {
    const ids = all.map((c) => trackletId(c.id));
    for (const t of ids) expect(t).toMatch(/^S[0-9A-Z]{7}$/);
    expect(new Set(ids).size).toBe(ids.length);
    expect(trackletId(mover.id)).toBe(trackletId(mover.id));
    expect(trackletId("")).toMatch(/^S[0-9A-Z]{7}$/);
  });
});

describe("votes sentence", () => {
  it("says nothing without votes and spells out counts and consensus", () => {
    expect(votesSentence(null)).toBeNull();
    expect(votesSentence({ mover: 0, changing: 0, junk: 0, unsure: 0 })).toBe("No votes yet.");
    expect(votesSentence({ mover: 1, changing: 0, junk: 0, unsure: 0 })).toBe("1 mover (1 vote; no consensus yet).");
    expect(votesSentence({ mover: 6, changing: 0, junk: 1, unsure: 0 })).toBe("6 mover, 1 junk (7 votes; consensus: mover, 86%).");
    expect(votesSentence({ mover: 2, changing: 0, junk: 1, unsure: 1 })).toBe("2 mover, 1 junk, 1 not sure (4 votes; no consensus yet).");
  });
});

describe("Backyard Worlds forum draft", () => {
  it("gives the position in sexagesimal and decimal, with the time", () => {
    const t = forumDraft(star, { base: BASE });
    expect(t.split("\n")[0]).toBe("Possible moving star in NASA SPHEREx data: SkyShift candidate pilot-pm-0000");
    const m = forumDraft(mover, { base: BASE });
    expect(m).toContain("Position (ICRS) on 26 Oct 2025, 02:14 UTC (MJD 60974.09356):");
    expect(m).toContain("RA 08h10m13.00s, Dec +17°00′12.9″");
    expect(m).toContain("RA 122.554154°, Dec +17.003570°");
    expect(m).toContain("Galactic latitude +24.8°");
  });

  it("states the motion with its error, significance and direction", () => {
    expect(forumDraft(star, { base: BASE })).toContain(
      "Motion measured by SkyShift between SPHEREx visits: 501 ± 57 mas/yr (8.9σ), position angle 151° east of north.",
    );
    expect(forumDraft({ ...mover, rate_arcsec_h: 42.83 }, { base: BASE })).toContain("within one SPHEREx session: 42.8″ per hour.");
    expect(forumDraft(mover, { base: BASE })).toMatch(/\nMotion: .+\.\n/);
  });

  it("carries the checklist findings, pipeline notes and the detector with units", () => {
    const t = forumDraft(mover, { base: BASE });
    expect(t).toContain("Best seen in SPHEREx detector D3 (1.6–2.4 µm), 2,320 µJy, S/N 57.");
    expect(t).toContain("Catalogues: No Gaia or CatWISE source within 6″");
    expect(t).toMatch(/\nArtefacts: .+\n/);
    expect(t).toMatch(/\nColour: .+\n/);
    expect(t).toMatch(/\nPipeline notes: [A-Z].+\.\n/);
    expect(forumDraft(star, { base: BASE })).not.toContain("Pipeline notes");
  });

  it("adds votes and the person's own checks when there are some, else a placeholder", () => {
    const blank = forumDraft(mover, { base: BASE });
    expect(blank).not.toContain("Votes on SkyShift");
    expect(blank).toContain("- [Say what you saw when you blinked it");
    const t = forumDraft(mover, { base: BASE, votes: { mover: 6, changing: 0, junk: 1, unsure: 0 }, replies: { motion: "yes", same: "unsure" } });
    expect(t).toContain("Votes on SkyShift: 6 mover, 1 junk (7 votes; consensus: mover, 86%).");
    expect(t).toContain("- Blink it on the Hours clock. Does it step along one line by the same amount each exposure? Yes.");
    expect(t).toMatch(/\n- .+\? Can't tell\.\n/);
    expect(t).not.toContain("[Say what you saw");
  });

  it("ends with the permalink and the data credit, never claiming a discovery", () => {
    const t = forumDraft(mover, { base: BASE });
    expect(t).toContain(`Blink it yourself: ${BASE}#/c/pilot-642020070317-87802695`);
    expect(t).toContain("NASA SPHEREx DR1+QR3 spectral images from NASA/IPAC IRSA");
    expect(t).toContain("not a confirmed discovery");
    for (const c of all) expect(forumDraft(c, { base: BASE }), c.id).not.toMatch(/null|undefined|NaN/);
  });
});

describe("citation and permalink", () => {
  const today = new Date("2026-11-14T12:00:00Z");

  it("builds #/c/<id> links, encoding the id and dropping an old hash", () => {
    expect(permalink(mover, BASE)).toBe(`${BASE}#/c/pilot-642020070317-87802695`);
    expect(permalink({ id: "a b/c" }, "https://x.org/app/#/explore")).toBe("https://x.org/app/#/c/a%20b%2Fc");
  });

  it("cites SkyShift, the three SPHEREx DOIs and the acknowledgements", () => {
    const { plain } = citation(mover, { base: BASE, today });
    expect(plain).toContain(`${BASE} (accessed 2026-11-14)`);
    expect(plain).toContain(`Candidate pilot-642020070317-87802695 (fast mover): ${BASE}#/c/pilot-642020070317-87802695`);
    for (const d of DATA_DOIS) expect(plain).toContain(`https://doi.org/${d.doi}`);
    expect(DATA_DOIS.map((d) => d.doi)).toEqual(["10.26131/IRSA678", "10.26131/IRSA662", "10.26131/IRSA679"]);
    expect(plain).toContain("NASA/IPAC Infrared Science Archive");
    expect(plain).toContain("Gaia Multilateral Agreement");
    expect(citation(null, { base: BASE, today }).plain).not.toContain("Candidate ");
  });

  it("writes balanced BibTeX with one entry per work and escaped ids", () => {
    const { bibtex } = citation({ id: "pm_0001", kind: "high_pm" }, { base: BASE, today });
    const entries = [...bibtex.matchAll(/^@misc\{(\w+),$/gm)].map((m) => m[1]);
    expect(entries).toEqual(["SkyShift2026", "SPHEREx_DR1_images", "SPHEREx_QR3_images", "SPHEREx_DR1_cube"]);
    const unescaped = bibtex.replace(/\\[{}]/g, "");
    let depth = 0;
    for (const ch of unescaped) {
      depth += ch === "{" ? 1 : ch === "}" ? -1 : 0;
      expect(depth).toBeGreaterThanOrEqual(0);
    }
    expect(depth).toBe(0);
    expect(bibtex).toContain("Candidate pm\\_0001: \\url{https://skyshift.web.app/#/c/pm_0001}.");
    expect(bibtex).toContain("doi       = {10.26131/IRSA678},");
    expect(bibtex).toContain("Accessed 2026-11-14}");
  });
});
