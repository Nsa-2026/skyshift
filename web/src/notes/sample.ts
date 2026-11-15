// Example comments, so an object's notes panel and the Community roundup show what talking about
// an object looks like before there is anywhere to do it.
//
// Nothing here is real and nothing is stored. Firestore holds `candidates` and `votes` and nothing
// else (contracts/api.md), so notes from other people would need a new collection, which is a
// contract change and P1's call. Until then every one of these carries a "Sample" tag, the panel
// says in plain words that nobody wrote them, and the page sends people to the Backyard Worlds
// forum, which is where volunteers and astronomers discuss these objects today.
//
// They are picked from the object's own id, not at random, so the same object always shows the
// same comments: a list that reshuffled on every render would read as live traffic. The handles
// are made up and deliberately generic; the content is written against this project's own domain
// facts (the 2.1 min gap inside a session, D1+D4 exposed at the same moment, ghosts and spikes
// near bright stars, zodiacal light left in) so it reads as the kind of thing the real one would
// carry. When a real notes collection lands, delete this file.

/** One made-up comment on one object. `agoMin` is how long ago it reads as being written. */
export interface SampleNote {
  who: string;
  body: string;
  agoMin: number;
}

const WHO = [
  "plate_reader",
  "night_shift",
  "m_dwarf",
  "two_or_one",
  "slow_mover",
  "ghost_hunter",
  "cold_one",
  "blink_twice",
];

const BODIES = [
  "Blinked it both ways and the dot is in both frames with a clean background, so it is not a cosmic ray.",
  "There is a bright star under an arcminute away and this sits about where its ghost lands. Worth reading the flags before trusting it.",
  "Both detections are on the same detector, so this is not a D1 to D4 colour flip being read as motion.",
  "Only 2.1 minutes between these exposures, so anything slower than a solar-system object would not have moved. The Hours clock says nothing either way here.",
  "The shift is close to the position error on a single dot. I would want another visit before calling it.",
  "Nothing in Gaia at this spot and no predicted asteroid near it. That is the interesting part.",
  "Reads like a diffraction spike off the bright neighbour to me: too drawn out on one axis for a point source.",
  "Zodiacal light is not subtracted, so the gradient across the background is expected and does not make the dot junk.",
  "Faint but in the same place across every visit. Not a transient, whatever else it turns out to be.",
  "Second opinion wanted. The one-object fit looks fine to me, but the residual is lopsided on one side.",
  "Voted not sure. Two frames is not enough for this one and I would rather it stayed open than went the wrong way.",
  "Checked the neighbours: the three dots around it are all in the catalogue and only this one is not.",
];

/** The oldest a comment reads as, in minutes: five days. Ages are spread smoothly over it, not
    drawn from a handful of slots -- eight slots put half the roundup on "25 min ago" together. */
const AGE_SPAN_MIN = 5 * 24 * 60;

/** FNV-1a, so the pick is stable across reloads, machines and builds. */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

const pick = <T>(list: readonly T[], n: number): T => list[n % list.length];

/**
 * The example comments on one object: always the same one or two for the same id, never none, so
 * the panel under the plate is never an empty box.
 */
export function sampleNotes(about: string): SampleNote[] {
  const h = hash(about);
  const count = 1 + (h % 2);
  const out: SampleNote[] = [];
  for (let i = 0; i < count; i++) {
    const n = hash(`${about}#${i}`);
    out.push({
      // Step by an odd offset so two comments on one object are never the same line or handle.
      who: pick(WHO, n >>> 3),
      body: pick(BODIES, (n >>> 9) + i * 5),
      agoMin: 12 + ((n >>> 15) % AGE_SPAN_MIN),
    });
  }
  // Newest first, like everything else that lists notes.
  return out.sort((a, b) => a.agoMin - b.agoMin);
}
