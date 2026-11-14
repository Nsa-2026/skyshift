import { describe, expect, it } from "vitest";
import { gradeLine } from "./drawer";
import { gradeCLast, isCaution, isGradeC, oneLine, reasonText } from "./kinds";
import { drawSignal, type Ink } from "./marks";
import { cand } from "./testdata";

const text = (s: string) => {
  const d = document.createElement("div");
  d.innerHTML = s;
  return d.textContent ?? "";
};

describe("vetting grades on the Sky page", () => {
  it("lists grade C after the rest and grade A first, keeping nearest-first order otherwise", () => {
    const near = cand({ id: "near", grade: "C" });
    const mid = cand({ id: "mid" });
    const far = cand({ id: "far", grade: "B" });
    const farthest = cand({ id: "farthest", grade: "A" });
    expect(gradeCLast([near, mid, far, farthest]).map((c) => c.id)).toEqual(["farthest", "mid", "far", "near"]);
    expect(isGradeC(near)).toBe(true);
    expect(isGradeC(mid)).toBe(false);
  });

  it("says the grade in words on the list line, so grey is never the only cue", () => {
    expect(oneLine(cand({ grade: "C" }))).toMatch(/, grade C$/);
    expect(oneLine(cand())).not.toMatch(/grade/);
  });

  it("reads gate codes as plain-word cautions", () => {
    expect(reasonText("bad_column")).toBe("On a bad column");
    expect(isCaution("very_sharp")).toBe(true);
    expect(isCaution("two_exposures")).toBe(false);
  });

  it("explains a grade C in the drawer and says it isn't put to a vote", () => {
    const t = text(gradeLine(cand({ grade: "C", reasons: ["two_exposures", "masked_peak", "allwise"] })));
    expect(t).toBe(
      "Grade C: bad pixel at its peak and an AllWISE source sits here. Most likely an artefact or a catalogued source, so it isn't put to a vote; you can still blink it.",
    );
    expect(text(gradeLine(cand({ grade: "B" })))).toMatch(/^Grade B: unexplained single-session source\. Passes every pipeline check/);
    expect(gradeLine(cand())).toBe("");
  });

  it("draws a grade C mark in grey pencil, fainter", () => {
    const strokes: [string, number][] = [];
    const ctx = new Proxy({} as Record<string, unknown>, {
      get: (t, k) => (k in t ? t[k as string] : () => {}),
      set: (t, k, v) => {
        t[k as string] = v;
        if (k === "globalAlpha") strokes.push([String(t.strokeStyle), Number(v)]);
        return true;
      },
    }) as unknown as CanvasRenderingContext2D;
    const ink: Ink = { red: "red", violet: "violet", ink: "ink", ink2: "grey", glass: "glass", sheet: "sheet" };
    drawSignal(ctx, ink, "mover", 10, 10, null, 0, { muted: true });
    expect(strokes.some(([s, a]) => s === "grey" && a === 0.6)).toBe(true);
    expect(strokes.some(([s]) => s === "red")).toBe(false);
  });
});
