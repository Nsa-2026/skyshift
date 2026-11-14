// Lesson 6's example: cast pretend votes on one dot and watch the real consensus rule
// (src/vote/model.ts, the same code the Review page uses) decide.
import { ANSWERS, ANSWER_TEXT, CONSENSUS, consensus, emptyCounts, total, type Answer, type Counts } from "../vote/model";

export const MAX_DEMO_VOTES = 12;

/** The sentence under the ballots. Pure, for tests. */
export function verdict(c: Counts): string {
  const n = total(c);
  if (n === 0) return `No votes yet. Consensus needs at least ${CONSENSUS.minVotes}.`;
  const verdictOf = consensus(c);
  if (n < CONSENSUS.minVotes) {
    const left = CONSENSUS.minVotes - n;
    return `${n} vote${n === 1 ? "" : "s"}. ${left} more before the rule can decide.`;
  }
  if (verdictOf) {
    const pct = Math.round(verdictOf.share * 100);
    const what = ANSWER_TEXT[verdictOf.answer].label;
    const next =
      verdictOf.answer === "mover" || verdictOf.answer === "changing"
        ? "It goes on the list for astronomers."
        : "It stays off the list for astronomers.";
    return `Consensus: ${what}, ${c[verdictOf.answer]} of ${n} (${pct}%). ${next}`;
  }
  const top = ANSWERS.reduce((best, a) => (c[a] > c[best] ? a : best), ANSWERS[0]);
  const pct = Math.round((c[top] / n) * 100);
  return `No consensus: the most common answer, ${ANSWER_TEXT[top].label}, has ${pct}%, short of ${Math.round(
    CONSENSUS.share * 100,
  )}%. It stays in the queue for more eyes.`;
}

export function mountVoteDemo(el: HTMLElement): { destroy(): void } {
  let votes: Answer[] = [];
  el.innerHTML = `
    <div class="ln-votes">
      <p class="ln-votes-ask">Several people blinked the same dot. Add their votes one at a time.</p>
      <div class="ln-vote-btns" role="group" aria-label="Add a vote">
        ${ANSWERS.map((a) => `<button type="button" class="btn" data-a="${a}">${ANSWER_TEXT[a].label}</button>`).join("")}
      </div>
      <ol class="ln-ballots" aria-label="Votes so far"></ol>
      <p class="ln-verdict" aria-live="polite"></p>
      <button type="button" class="btn btn-ghost ln-votes-reset">Start again</button>
    </div>`;
  const ballots = el.querySelector<HTMLOListElement>(".ln-ballots")!;
  const out = el.querySelector<HTMLParagraphElement>(".ln-verdict")!;
  const buttons = [...el.querySelectorAll<HTMLButtonElement>(".ln-vote-btns button")];

  function render() {
    const c = emptyCounts();
    for (const v of votes) c[v]++;
    ballots.innerHTML = votes.map((v) => `<li class="ln-ballot ln-ballot-${v}">${ANSWER_TEXT[v].label}</li>`).join("");
    const result = consensus(c);
    out.textContent = verdict(c);
    out.dataset.state = result ? (result.answer === "mover" || result.answer === "changing" ? "list" : "off") : "open";
    buttons.forEach((b) => (b.disabled = votes.length >= MAX_DEMO_VOTES));
  }
  buttons.forEach((b) =>
    b.addEventListener("click", () => {
      if (votes.length < MAX_DEMO_VOTES) votes.push(b.dataset.a as Answer);
      render();
    }),
  );
  el.querySelector(".ln-votes-reset")!.addEventListener("click", () => {
    votes = [];
    render();
  });
  render();
  return { destroy: () => (el.innerHTML = "") };
}
