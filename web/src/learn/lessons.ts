// The course: seven short lessons, each one screen of plain words around one real example.
// Facts follow CLAUDE.md "Domain facts", web/DESIGN.md and the validation results; numbers
// that come from the data (dates, rates, measured shifts) are filled in by figures.ts.
import { term as t } from "./glossary";

export type FigureId = "six" | "barnard" | "clocks" | "fakes" | "colours" | "votes" | "planetx";

export interface TryAction {
  label: string;
  href: string;
}

export interface Lesson {
  n: number;
  title: string;
  /** One sentence for the course list. */
  summary: string;
  lede(): string;
  body(): string;
  figure: FigureId;
  /** Words before the actions at the end. */
  tryText: string;
  tryIt: TryAction[];
}

/** Open a featured target on the Blink page from its static frames. */
export const exploreHref = (slug: string): string => `?t=${encodeURIComponent(slug)}#/explore`;

export const LESSONS: Lesson[] = [
  {
    n: 1,
    title: "What SPHEREx sees",
    summary: "A NASA telescope that photographs the whole sky in 102 colours of infrared light.",
    lede: () =>
      `<p>${t("SPHEREx")} is a NASA space telescope that has photographed the whole sky every six months since 2025. It sees ${t("infrared")} light, which our eyes can't, and splits it into 102 colours.</p>`,
    body: () => `
      <p>Each photo, an ${t("exposure")}, collects light for 113 seconds. The light goes to six ${t("detector", "detectors")} at once.
      D1, D2 and D3 see short-wave infrared from 0.75 to 2.4 ${t("µm")}; D4, D5 and D6 see from 2.4 to 5.0 µm. Our eyes stop at about 0.7 µm.</p>
      <dl class="ln-dets">
        <div class="det-1"><dt>D1</dt><dd>0.75–1.1 µm</dd></div>
        <div class="det-2"><dt>D2</dt><dd>1.1–1.6 µm</dd></div>
        <div class="det-3"><dt>D3</dt><dd>1.6–2.4 µm</dd></div>
        <div class="det-4"><dt>D4</dt><dd>2.4–3.8 µm</dd></div>
        <div class="det-5"><dt>D5</dt><dd>3.8–4.4 µm</dd></div>
        <div class="det-6"><dt>D6</dt><dd>4.4–5.0 µm</dd></div>
      </dl>
      <p>Each detector splits its range into 17 narrow ${t("channel", "channels")}: 6 × 17 = 102 colours. As SPHEREx steps across the sky, every star lands on many channels, so within six months it has been measured in all of them. That list of brightness against colour is the star's ${t("spectrum")}.</p>
      <p>A ${t("pixel")} covers 6.15″ of sky (an ${t("arcsecond")} is 1/3600 of a degree), and a photo is 2040 × 2040 pixels: about 3.5° across, the width of seven full Moons.</p>`,
    figure: "six",
    tryText: "Open Barnard's Star on the Blink page and switch between D1 and D6.",
    tryIt: [{ label: "Open Barnard's Star", href: exploreHref("barnard") }],
  },
  {
    n: 2,
    title: "Blinking: how Pluto was found",
    summary: "Flick between pictures taken months apart: stars stay, anything that moved jumps.",
    lede: () =>
      `<p>In February 1930 Clyde Tombaugh found Pluto by ${t("blink", "blinking")}: flicking between two photographs of the same stars taken six days apart. The stars stayed still. One faint dot jumped.</p>`,
    body: () => `
      <p>He used a blink comparator at Lowell Observatory, a viewer that switches quickly between two glass plates. Your eye ignores what stays put and catches what moves. SkyShift is that viewer for SPHEREx, with the plates shown as negatives: dark stars on light glass.</p>
      <p>On the Months clock each frame is one ${t("visit")}: the ${t("stack", "median")} of up to 6 photos from the few weeks SPHEREx spends on this spot. Visits come every six months.</p>
      <p>Barnard's Star crosses our sky faster than any other star: ${t("Gaia")} measures 10.39″ a year, because it is only 1.83 ${t("parsec", "pc")} away. Between visits it steps north about 5″, less than one SPHEREx pixel. Watch it move up past the red marks.</p>
      <p>The hatched hole in the middle is honest: the star is so bright that its centre overflows the pixels, called ${t("saturation")}; NASA ${t("flag", "flags")} them and we leave them blank. SkyShift measures the position on the star's wings.</p>`,
    figure: "barnard",
    tryText: "Blink Barnard's Star yourself, then press Difference: one visit minus another shows where light left and arrived.",
    tryIt: [{ label: "Blink Barnard's Star", href: exploreHref("barnard") }],
  },
  {
    n: 3,
    title: "Two clocks: months and hours",
    summary: "Stars crawl between visits; asteroids and comets race across photos taken hours apart.",
    lede: () =>
      `<p>Things cross the sky at very different speeds, so SkyShift blinks on two clocks.</p>`,
    body: () => `
      <dl class="ln-clocks">
        <div><dt>Months</dt><dd>Nearby stars and ${t("brown dwarf", "brown dwarfs")} move a few arcseconds a year: their ${t("proper motion")}. Compare visits six months apart.</dd></div>
        <div><dt>Hours</dt><dd>${t("asteroid", "Asteroids")} and ${t("comet", "comets")} move arcseconds to arcminutes in an hour. Compare photos inside one visit.</dd></div>
      </dl>
      <p>A visit lasts 2 to 4 weeks: about 21 ${t("session", "sessions")} and 55 to 60 photos. A session is 1 to 5 photos taken 2.1 minutes apart. On the Hours clock you step through them in time order.</p>
      <p>Each clock hides the other's movers. By the next visit an asteroid is long gone, and within a day Barnard's Star doesn't seem to move at all.</p>`,
    figure: "clocks",
    tryText: "Watch an asteroid or the interstellar comet on the Hours clock.",
    tryIt: [
      { label: "Watch (59) Elpis", href: exploreHref("elpis") },
      { label: "Watch 3I/ATLAS", href: exploreHref("3i-atlas") },
    ],
  },
  {
    n: 4,
    title: "Fakes: blinks that aren't real",
    summary: "Cosmic rays, afterimages, ghosts, edges and colour jumps, and how to tell them apart.",
    lede: () =>
      `<p>Most things that blink are not new worlds. Knowing the fakes is most of the skill.</p>`,
    body: () => `
      <p>NASA's pipeline ${t("flag", "flags")} every pixel of every photo. SkyShift leaves the untrustworthy ones blank (hatched) and lists the others on each dot. The names in brackets are NASA's.</p>
      <dl class="ln-fakes">
        <div><dt>${t("cosmic ray", "Cosmic rays")}</dt><dd>A sharp dot in one photo only (TRANSIENT, SNOWBALL). A real mover shows up in several photos along a straight line.</dd></div>
        <div><dt>${t("persistence", "Afterimages")}</dt><dd>A bright star leaves a glow on the detector that fades over the next photos (PERSIST). It stays on the same pixels even when the sky moves.</dd></div>
        <div><dt>${t("ghost", "Ghosts")} and echoes</dt><dd>Reflections inside the telescope and ${t("crosstalk", "electronic echoes")} of bright stars (GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK), often in neat patterns around them.</dd></div>
        <div><dt>Edges and gaps</dt><dd>The photo ends, or pixels don't work (MISSING_DATA, NONFUNC). Hatched, never mistaken for dark sky.</dd></div>
        <div><dt>Burnt cores</dt><dd>The brightest stars overflow their pixels (OVERFLOW, NONLINEAR) and are blank in every photo.</dd></div>
        <div><dt>Colour, not motion</dt><dd>D1 and D4, D2 and D5, D3 and D6 photograph the same sky at the same moment. A dot that is in D4 but not in D1 is a red object, not something that moved. Compare brightness only within one detector.</dd></div>
      </dl>`,
    figure: "fakes",
    tryText: "Practise on real candidates: the Review queue starts with a short practice round.",
    tryIt: [{ label: "Go to the Review queue", href: "#/review" }],
  },
  {
    n: 5,
    title: "Colours tell you what it is",
    summary: "Brown dwarfs are red, and water and methane carve dips into their spectra.",
    lede: () =>
      `<p>A dot's colours say how warm it is and what it is made of.</p>`,
    body: () => `
      <p>Cold things glow at long wavelengths. The Sun is brightest in visible light; a ${t("brown dwarf")} a few hundred degrees above absolute zero is brightest in the infrared, near 4 µm. To SPHEREx, brown dwarfs look red: faint in D1, bright in D5.</p>
      <p>Molecules leave fingerprints. Water and ${t("methane")} in a brown dwarf's air swallow certain colours and carve dips into its ${t("spectrum")}. SPHEREx's 102 colours are fine enough to see them.</p>
      <p>A dip is a hint, not proof: cool stars can mimic some bands. The pipeline marks them and people check.</p>`,
    figure: "colours",
    tryText: "Open WISE 0855 and switch from D2 to D5: the brown dwarf appears.",
    tryIt: [
      { label: "Open WISE 0855", href: exploreHref("wise0855") },
      { label: "Open Epsilon Indi B", href: exploreHref("epsindib") },
    ],
  },
  {
    n: 6,
    title: "From your vote to a discovery",
    summary: "How votes become consensus, and where a consensus candidate goes next.",
    lede: () =>
      `<p>One person's eyes can be fooled. Many people looking at the same dot rarely are.</p>`,
    body: () => `
      <p>The pipeline proposes candidates: fast movers, moving stars and odd spectra. You blink each one and answer Mover, Changing, Junk or Not sure.</p>
      <p>A candidate reaches ${t("consensus")} with at least 5 votes and at least 70% on one answer. Consensus movers and changing sources go on the list for astronomers on the Community page, with CSV and JSON downloads.</p>
      <p>Then astronomers take over. A new asteroid or comet needs its positions reported to the ${t("MPC", "Minor Planet Center")}. A new nearby star or brown dwarf goes to the Backyard Worlds: Planet 9 forum, where volunteers who hunt brown dwarfs in NASA's WISE images check it against other surveys. SkyShift drafts the report; it never sends anything for you.</p>
      <p>No account needed: your votes carry a random id kept in your browser, never your name or email.</p>`,
    figure: "votes",
    tryText: "Your first few reviews are a practice round with known answers.",
    tryIt: [
      { label: "Start reviewing", href: "#/review" },
      { label: "See the community's list", href: "#/community" },
    ],
  },
  {
    n: 7,
    title: "Planet X, honestly",
    summary: "What SPHEREx can find (cold neighbours) and what it can't (a far, faint planet).",
    lede: () =>
      `<p>Could you find the next Planet X with SkyShift? Maybe, if Planet X is a cold brown dwarf nearby. Not if it is a planet far beyond Neptune.</p>`,
    body: () => `
      <p>${t("Planet Nine")}, if it exists, would orbit hundreds of times farther from the Sun than Earth and shine only by reflected sunlight. Predictions put it around ${t("magnitude")} 20 or fainter in visible light, and methane would darken it further in the infrared. On our test photo, the faintest dots SPHEREx caught were about AB 17.5. It would also jump: seen six months apart from opposite sides of Earth's orbit, a planet 500 times farther than the Sun shifts by up to about 14′, nearly three widths of a 5′ frame.</p>
      <p>The other kind of Planet X is within reach: a brown dwarf or a free-floating giant planet a few parsecs away, glowing at 4 to 5 µm. WISE 0855−0714, found in 2014, is exactly that: 2.3 pc away, about 250 K, and in SPHEREx it steps west about 4″ per visit.</p>
      <dl class="ln-limits">
        <div><dt>Slowest motion</dt><dd>About 1.1″ a year: three times our 0.36″ position error, over one year.</dd></div>
        <div><dt>${t("parallax", "Parallax")}</dt><dd>Under about 1.5″ between visits even for the nearest stars (about 1.1″ for Barnard's Star), close to our errors, so we show Gaia's distances instead of measuring our own.</dd></div>
        <div><dt>Too bright</dt><dd>Stars brighter than about AB 9 to 11, depending on the detector, ${t("saturation", "saturate")}.</dd></div>
        <div><dt>Too close together</dt><dd>Two objects under one pixel apart blend into one dot, like the Epsilon Indi B pair, less than 1″ apart.</dd></div>
      </dl>`,
    figure: "planetx",
    tryText: "Go hunting: zoom into the Sky map, or review what the pipeline found.",
    tryIt: [
      { label: "Explore the Sky", href: "#/" },
      { label: "Review candidates", href: "#/review" },
    ],
  },
];

export const LESSON_COUNT = LESSONS.length;
