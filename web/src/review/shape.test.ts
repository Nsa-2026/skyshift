// The "shape" kind (one object or two?): read from candidates.json, named, and voted on with the
// same four stored answers as every other kind, only labelled for the question.
import { describe, expect, it } from "vitest";
import type { CandidatesResponse } from "../api/types";
import { ANSWERS, ANSWER_TEXT } from "../vote/model";
import { Ballot, countsBar, nextStepsHtml } from "./ballot";
import { toCandidate, toCandidateSet } from "./data";
import { factsHtml, headHtml } from "./parts";
import { cand } from "./testkit";
import { KIND_ICON, SHAPE_ANSWER_TEXT, answerLabel, answerText, ballotHeading, consensusText, countsText, kindName, reasonText } from "./text";

const ROW = {
  id: "hunt-sh-123456",
  kind: "shape",
  ra: 125.1,
  dec: 17.2,
  mjd: 60850.5,
  det: 3,
  zone: "hunt",
  zones: ["hunt"],
  score: 0.8,
  snr: 120,
  flux_ujy: 450,
  rate_arcsec_h: null,
  pm_mas_yr: null,
  pm_err_mas_yr: null,
  pa_deg: 37.2,
  nearest_known_arcsec: null,
  gaia_match: true,
  catwise_match: true,
  galactic_b: 24,
  labels: [],
  reasons: ["elongated", "steady_pa", "one_catalogue_source"],
  why: "Looks like two objects here but the catalogues list one: worth a look.",
  flags: [],
  frames_url: null,
};

describe("shape candidates", () => {
  it("are read from candidates.json, on the months clock when the file has no clock", () => {
    const c = toCandidate(ROW)!;
    expect(c.kind).toBe("shape");
    expect(c.clock).toBe("months");
    expect(c.pa_deg).toBe(37.2);
    expect(c.reasons).toEqual(["elongated", "steady_pa", "one_catalogue_source"]);
    expect(toCandidate({ ...ROW, kind: "nonsense" })).toBeNull();
  });

  it("load from a file whose counts predate the kind", () => {
    const data = { generated: "", data_release: "DR1", zones: ["hunt"], counts: { mover: 0, high_pm: 0, spectral: 0 }, candidates: [ROW] };
    const set = toCandidateSet(data as unknown as CandidatesResponse, false);
    expect(set.candidates.map((c) => c.id)).toEqual(["hunt-sh-123456"]);
  });

  it("have a name, an icon, reason chips and the stretch direction", () => {
    const c = cand("s1", { kind: "shape", pa_deg: 37.2, snr: 50, flux_ujy: 300 });
    expect(kindName("shape")).toBe("One or two?");
    expect(KIND_ICON.shape).toContain("<svg");
    expect(headHtml(c, "h2", "")).toContain("One or two?");
    expect(reasonText("elongated").tone).toBe("for");
    expect(reasonText("steady_pa").chip).toBe("Same direction every time");
    expect(reasonText("one_visit").tone).toBe("info");
    expect(reasonText("one_catalogue_source").chip).toBe("Catalogues list one object");
    expect(factsHtml(c)).toContain("37° east of north");
    expect(factsHtml(cand("p1", { kind: "high_pm", pa_deg: 37.2, snr: 50 }))).not.toContain("Stretched along");
  });

  it("keep the four stored answers, labelled for one object or two", () => {
    // The Firestore rules allow only these four answers: shape relabels them, never adds one.
    expect(Object.keys(SHAPE_ANSWER_TEXT).sort()).toEqual([...ANSWERS].sort());
    expect(answerText("shape").mover.label).toBe("Two objects");
    expect(answerText("shape").junk.label).toBe("One object");
    expect(answerText("shape").changing.label).toBe("Changes");
    expect(answerText("shape").unsure.label).toBe("Not sure");
    // Other kinds keep today's words.
    for (const k of ["mover", "high_pm", "spectral", undefined] as const) expect(answerText(k)).toBe(ANSWER_TEXT);
    expect(answerLabel("mover")).toBe("Mover");
    expect(answerLabel("mover", "shape")).toBe("Two objects");
    expect(ballotHeading("shape")).toBe("One object or two?");
    expect(ballotHeading("mover")).toBe("What do you see?");
    const k = { mover: 4, changing: 0, junk: 1, unsure: 0 };
    expect(countsText(k, "shape")).toBe("4 two objects, 0 changes, 1 one object, 0 not sure");
    expect(countsText(k)).toBe("4 mover, 0 changing, 1 junk, 0 not sure");
    expect(countsBar(k, "shape")).toContain("Two objects");
    expect(countsBar(k)).toContain("Mover");
    expect(consensusText({ state: "reached", answer: "mover", top: "mover", topCount: 5, total: 5, share: 1 } as never, "shape")).toContain("Two objects");
  });

  it("ask one object or two on the ballot, and vote with the stored answers", () => {
    const el = document.createElement("div");
    const votes: string[] = [];
    const b = new Ballot(el, { onVote: (a) => votes.push(a), full: true, kind: "shape" });
    expect(el.querySelector(".rv-h")!.textContent).toBe("One object or two?");
    const labels = [...el.querySelectorAll(".rv-choice-label")].map((x) => x.textContent);
    expect(labels).toEqual(["Two objects", "Changes", "One object", "Not sure"]);
    b.set({ mine: null, justNow: false, counts: null, saving: null, waiting: false, error: null, local: true, kind: "shape" });
    el.querySelector<HTMLButtonElement>("button[data-answer='mover']")!.click();
    expect(votes).toEqual(["mover"]);
    b.set({ mine: "mover", justNow: true, counts: { mover: 1, changing: 0, junk: 0, unsure: 0 }, saving: null, waiting: false, error: null, local: true, kind: "shape" });
    expect(el.textContent).toContain("Saved: you said Two objects.");
    expect(el.textContent).toContain("agree it's two objects or changing");

    const other = document.createElement("div");
    new Ballot(other, { onVote: () => {}, full: true, kind: "mover" });
    expect(other.querySelector(".rv-h")!.textContent).toBe("What do you see?");
    expect([...other.querySelectorAll(".rv-choice-label")].map((x) => x.textContent)).toEqual(["Mover", "Changing", "Junk", "Not sure"]);
  });

  it("say what happens next on the astronomers' list", () => {
    const reached = { state: "reached", answer: "mover", top: "mover", topCount: 5, total: 5, share: 1 } as never;
    expect(nextStepsHtml(reached, "shape")).toContain("Two objects where the catalogues list one");
    expect(nextStepsHtml(reached, "high_pm")).not.toContain("Two objects");
  });
});
