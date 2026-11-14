import { describe, expect, it } from "vitest";
import { noVoteHtml } from "./candidate";
import { fillNearSource, gradeHtml } from "./parts";
import { cand } from "./testkit";

const dom = (s: string) => {
  const d = document.createElement("div");
  d.innerHTML = s;
  return d;
};
const text = (n: Element | null) => (n?.textContent ?? "").replace(/\s+/g, " ").trim();

describe("the vetting grade on the Candidate page", () => {
  it("names grade B and says its motion isn't shown", () => {
    const d = dom(gradeHtml(cand("b", { kind: "mover", grade: "B" }), true));
    expect(d.querySelector(".rv-grade")?.getAttribute("data-grade")).toBe("B");
    expect(text(d.querySelector(".rv-grade-head"))).toBe("Grade B Unexplained single-session source");
    expect(text(d)).toContain("its motion isn't shown");
  });

  it("lists every check a grade C failed, in plain words", () => {
    const c = cand("c", { kind: "mover", grade: "C", reasons: ["two_exposures", "hot_pixel", "very_sharp"] });
    const d = dom(gradeHtml(c, true));
    const why = [...d.querySelectorAll(".rv-grade-why li b")].map((b) => b.textContent);
    expect(why).toEqual(["On a hot pixel.", "Far too sharp."]);
    expect(d.textContent).not.toMatch(/hot_pixel|very_sharp/);
  });

  it("says why there is no vote on a grade C", () => {
    const c = cand("c", { kind: "mover", grade: "C", reasons: ["hot_pixel", "edge"] });
    const d = dom(noVoteHtml(c));
    expect(text(d.querySelector("h2"))).toBe("No vote on this one");
    expect(text(d)).toContain("The pipeline graded it C: it fails a check (on a hot pixel and at the detector edge)");
    expect(text(d)).toContain("Votes go to grade A and B objects.");
    expect(text(d)).toContain("You can still blink it");
  });

  it("says 'Not vetted yet' for an ungraded fast mover, and nothing for other kinds", () => {
    const d = dom(gradeHtml(cand("m", { kind: "mover" }), true));
    expect(d.querySelector(".rv-grade")?.getAttribute("data-grade")).toBe("none");
    expect(text(d.querySelector(".rv-grade-badge"))).toBe("Not vetted yet");
    expect(gradeHtml(cand("p", { kind: "high_pm" }), true)).toBe("");
  });

  it("shows near_source_arcsec as a quiet warning, and hides it when absent", () => {
    const d = dom(gradeHtml(cand("b", { kind: "mover", grade: "B" }), true));
    const near = d.querySelector<HTMLElement>("[data-near]")!;
    expect(near.hidden).toBe(true);
    fillNearSource(d, 4.1);
    expect(near.hidden).toBe(false);
    expect(near.textContent).toBe("A catalogued source sits 4.1″ away. A warning only: the two may blend.");
    expect(near.title).toMatch(/not a check it failed/);
    fillNearSource(d, null);
    expect(near.hidden).toBe(true);
  });
});
