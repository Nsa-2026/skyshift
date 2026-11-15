// Changing or taking back a vote on the ballot (contracts/api.md v3.16), and the counts it moves.
import { describe, expect, it } from "vitest";
import type { Answer } from "../vote/model";
import { Ballot, type BallotState } from "./ballot";
import { movedVote } from "./model";

const counts = (o: Partial<Record<Answer, number>> = {}) => ({ mover: 0, changing: 0, junk: 0, unsure: 0, ...o });
const base: BallotState = { mine: null, justNow: false, counts: null, saving: null, waiting: false, error: null, local: false, kind: "mover" };

function ballot(withdraw = true) {
  const el = document.createElement("div");
  const votes: Answer[] = [];
  let withdrawn = 0;
  const b = new Ballot(el, { onVote: (a) => votes.push(a), onWithdraw: withdraw ? () => withdrawn++ : undefined, full: false, kind: "mover" });
  const choice = (a: Answer) => el.querySelector<HTMLButtonElement>(`button[data-answer='${a}']`)!;
  return { el, b, votes, choice, withdrawn: () => withdrawn };
}

describe("movedVote", () => {
  it("moves one count from the old answer to the new", () => {
    expect(movedVote(counts({ mover: 2 }), "mover", "junk")).toEqual(counts({ mover: 1, junk: 1 }));
  });
  it("adds a first vote, removes a taken-back one, never goes below zero", () => {
    expect(movedVote(undefined, null, "unsure")).toEqual(counts({ unsure: 1 }));
    expect(movedVote(counts({ changing: 1 }), "changing", null)).toEqual(counts());
    expect(movedVote(counts(), "mover", null)).toEqual(counts());
    expect(movedVote(counts({ mover: 1 }), "mover", "mover")).toEqual(counts({ mover: 1 }));
  });
});

describe("Ballot: change or take back", () => {
  it("keeps the other answers open after a vote, so picking one changes it", () => {
    const { b, votes, choice } = ballot();
    b.set({ ...base, mine: "mover", justNow: true, counts: counts({ mover: 1 }) });
    expect(b.open()).toBe(true);
    expect(choice("junk").disabled).toBe(false);
    expect(choice("mover").getAttribute("aria-pressed")).toBe("true");
    choice("junk").click();
    expect(votes).toEqual(["junk"]);
  });

  it("offers to take the vote back and calls onWithdraw", () => {
    const { el, b, withdrawn } = ballot();
    b.set({ ...base, mine: "changing", justNow: false, counts: counts({ changing: 1 }) });
    expect(el.textContent).toContain("You said Changing on an earlier visit.");
    expect(el.textContent).not.toContain("can't be changed");
    el.querySelector<HTMLButtonElement>("button[data-act='withdraw']")!.click();
    expect(withdrawn()).toBe(1);
  });

  it("locks the choices while a vote is saved or taken back", () => {
    const { b, choice } = ballot();
    b.set({ ...base, mine: "mover", withdrawing: true });
    expect(b.open()).toBe(false);
    expect(choice("junk").disabled).toBe(true);
    b.set({ ...base, mine: "mover", saving: "junk" });
    expect(b.open()).toBe(false);
  });

  it("says the vote is taken back, and opens the choices again", () => {
    const { el, b, choice } = ballot();
    b.set({ ...base, notice: "Your vote is taken back. Pick an answer to vote again." });
    expect(el.querySelector(".rv-ballot-status")!.textContent).toContain("taken back");
    expect(choice("mover").disabled).toBe(false);
    expect(el.querySelector(".rv-result")!.hasAttribute("hidden")).toBe(true);
  });

  it("without onWithdraw it only offers to change", () => {
    const { el, b } = ballot(false);
    b.set({ ...base, mine: "junk", justNow: true, counts: counts({ junk: 1 }) });
    expect(el.querySelector("button[data-act='withdraw']")).toBeNull();
    expect(el.textContent).toContain("Pick another answer above");
  });
});
