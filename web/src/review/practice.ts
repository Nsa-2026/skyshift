// The practice round: three featured targets where the answer is known, with feedback.
// Frames are the static /data/<slug>/ files, so practice never waits for the live service.
import type { Answer } from "../vote/model";
import type { KV } from "../vote/model";

export interface PracticeItem {
  slug: string;
  /** The card's question. */
  title: string;
  /** Short name for the recap after the round, e.g. "Asteroid (59) Elpis". */
  name: string;
  /** What we ask the person to watch. Never gives the answer away. */
  prompt: string;
  answer: Answer;
  /** Feedback per answer. The right answer's text starts with "Yes". */
  feedback: Record<Answer, string>;
}

export const PRACTICE: PracticeItem[] = [
  {
    slug: "elpis",
    title: "What do you see in this field?",
    name: "Asteroid (59) Elpis",
    prompt: "Exposures from one day, a few minutes to hours apart. Watch the whole field, not only the centre.",
    answer: "mover",
    feedback: {
      mover:
        "Yes — that's asteroid (59) Elpis. It crosses the field in a few hours; the violet diamonds mark where its known orbit puts it, and the dot sits on them.",
      changing:
        "Not quite: the dot doesn't stay in one place. That's asteroid (59) Elpis crossing the field. Follow the violet diamonds, where its known orbit puts it.",
      junk:
        "It's real: asteroid (59) Elpis. A flaw stays put on the detector or shows up once; Elpis travels along a line, frame after frame, on the violet diamonds.",
      unsure:
        "Fair, there's a lot to take in. That's asteroid (59) Elpis: find the violet diamonds and watch the dot that walks along them.",
    },
  },
  {
    slug: "barnard",
    title: "What's in the red reticle?",
    name: "Barnard's Star",
    prompt: "Visits months apart. Watch the dark blot in the red reticle closely: the change is small.",
    answer: "mover",
    feedback: {
      mover:
        "Yes — that's Barnard's Star, six light years away and the fastest-moving star in our sky: 10.4″ a year, under one pixel between visits.",
      changing:
        "Close: it looks different, but it's the place that changes. Barnard's Star moves 10.4″ a year, about 5″ north between visits.",
      junk:
        "It's real: Barnard's Star. Its core is so bright it saturates (solid ink), which can look odd, but the star itself steps north between visits.",
      unsure:
        "Fair: the step is under one pixel. That's Barnard's Star, moving 10.4″ a year. Subtract two visits in the player to see it clearly.",
    },
  },
  {
    slug: "rwaur",
    title: "What does this star do?",
    name: "RW Aurigae",
    prompt: "Visits months apart. Watch the star in the red reticle: does it move, change, or neither?",
    answer: "changing",
    feedback: {
      changing:
        "Yes — that's RW Aurigae, a young star still pulling in gas. It stays put while its brightness changes by about half between visits.",
      mover:
        "Not quite: it stays in the same place. RW Aurigae is a young star whose brightness changes between visits, so the dot grows and shrinks.",
      junk:
        "It's real: RW Aurigae, a young star. Its brightness changes between visits, which is exactly what “Changing” is for.",
      unsure:
        "Fair: the change is gentle. RW Aurigae stays put while it brightens by about half from the first visit to the second.",
    },
  },
];

export interface PracticeResult {
  slug: string;
  answer: Answer;
  correct: boolean;
}

export function judge(item: PracticeItem, answer: Answer): PracticeResult & { text: string } {
  return { slug: item.slug, answer, correct: answer === item.answer, text: item.feedback[answer] };
}

/** How many right, and a short closing line. */
export function practiceSummary(results: PracticeResult[]): { right: number; of: number; line: string } {
  const right = results.filter((r) => r.correct).length;
  const of = results.length;
  const line =
    of === 0
      ? "Practice skipped."
      : right === of
        ? `${right} of ${of}. You're ready for real candidates.`
        : right === 0
          ? `${right} of ${of}. That's normal on a first try: real candidates come with the same four answers, and “Not sure” is always fine.`
          : `${right} of ${of}. Good start. When a real candidate is hard to call, “Not sure” is an honest answer.`;
  return { right, of, line };
}

export const PRACTICE_KEY = "skyshift.review.practice";

export function practiceDone(kv: KV): boolean {
  return kv.get(PRACTICE_KEY) === "done";
}

export function markPracticeDone(kv: KV): void {
  kv.set(PRACTICE_KEY, "done");
}
