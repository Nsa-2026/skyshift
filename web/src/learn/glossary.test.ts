import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GATES, GATE_TEXT } from "../api/grades";
import { mjdToDate } from "../app/astro";
import { CONSENSUS } from "../vote/model";
import { GLOSSARY, POP_ID, closeTermPopover, enhanceTerms, findTerm, openTerm, term, termState } from "./glossary";

describe("glossary entries", () => {
  it("has at least 25 words with unique slugs and one or two plain sentences each", () => {
    expect(GLOSSARY.length).toBeGreaterThanOrEqual(25);
    expect(new Set(GLOSSARY.map((g) => g.slug)).size).toBe(GLOSSARY.length);
    for (const g of GLOSSARY) {
      expect(g.slug).toMatch(/^[a-z0-9-]+$/);
      expect(g.def.endsWith(".")).toBe(true);
      expect(g.def.length).toBeLessThanOrEqual(200);
      expect(g.def.split(/\.\s+[A-Z]/).length).toBeLessThanOrEqual(2);
    }
  });

  it("covers the words other pages need", () => {
    const need = [
      "blink", "visit", "session", "exposure", "detector", "proper motion", "parallax", "arcsecond", "µm",
      "µJy", "MJy/sr", "MJD", "Gaia", "CatWISE", "HEALPix", "artefact", "ghost", "persistence", "saturation",
      "brown dwarf", "asteroid", "comet", "consensus", "S/N", "PSF",
    ];
    for (const w of need) expect(findTerm(w), w).toBeDefined();
  });

  it("matches the domain facts (CLAUDE.md, DESIGN.md)", () => {
    expect(findTerm("exposure")!.def).toContain("113 seconds");
    expect(findTerm("session")!.def).toContain("2.1 minutes");
    expect(findTerm("visit")!.def).toMatch(/2 to 4 weeks.*21 sessions.*55 to 60/);
    expect(findTerm("pixel")!.def).toContain("6.15″");
    expect(findTerm("psf")!.def).toContain("7.53″");
    expect(findTerm("MJy/sr")!.def).toContain("889 µJy");
    expect(findTerm("detector")!.def).toMatch(/0\.75 to 2\.4 µm.*2\.4 to 5\.0 µm/);
    expect(findTerm("consensus")!.def).toContain(`at least ${CONSENSUS.minVotes} votes`);
    expect(findTerm("consensus")!.def).toContain(`${CONSENSUS.share * 100}%`);
    const d = mjdToDate(61000);
    expect(d.toISOString().slice(0, 10)).toBe("2025-11-21");
    expect(findTerm("mjd")!.def).toContain("MJD 61000 is 21 November 2025");
  });

  it("explains the vetting grades and every gate reason in plain words", () => {
    expect(findTerm("grade A")?.slug).toBe("grade-a");
    expect(findTerm("candidate movers")?.slug).toBe("grade-a");
    expect(findTerm("unexplained single-session source")?.slug).toBe("grade-b");
    expect(findTerm("Grade C")?.slug).toBe("grade-c");
    expect(findTerm("vetting grades")?.slug).toBe("vetting-grade");
    expect(findTerm("grade-a")!.def).toContain("5σ");
    expect(findTerm("grade-c")!.def).toMatch(/no vote/);
    for (const code of GATES) {
      const g = findTerm(code);
      expect(g, code).toBeDefined();
      expect(g!.def).not.toMatch(/_/);
      expect(findTerm(GATE_TEXT[code].label)?.slug, code).toBe(g!.slug);
    }
    expect(findTerm("flux_ratio")?.term).toBe("Brightness jump");
    expect(findTerm("2mass")!.def).toContain("6″");
  });

  it("finds words by slug, alias, case and simple plurals", () => {
    expect(findTerm("Parallax")?.slug).toBe("parallax");
    expect(findTerm("visits")?.slug).toBe("visit");
    expect(findTerm("ghosts")?.slug).toBe("ghost");
    expect(findTerm("brown dwarfs")?.slug).toBe("brown-dwarf");
    expect(findTerm("photo")?.slug).toBe("exposure");
    expect(findTerm("arcsec")?.slug).toBe("arcsecond");
    expect(findTerm("Minor Planet Center")?.slug).toBe("mpc");
    expect(findTerm("D5")?.slug).toBe("detector");
    expect(findTerm("Planet X")?.slug).toBe("planet-nine");
    expect(findTerm("flux capacitor")).toBeUndefined();
    expect(findTerm("")).toBeUndefined();
  });
});

describe("term()", () => {
  it("returns a dotted-underline button for a known word", () => {
    const html = term("visit", "visits");
    expect(html).toBe(`<button type="button" class="gl-term" data-term="visit">visits</button>`);
  });
  it("escapes the label and leaves unknown words as plain text", () => {
    expect(term("parallax", "<b>p</b>")).toContain("&lt;b&gt;p&lt;/b&gt;");
    expect(term("nonsense word")).toBe("nonsense word");
    expect(term("x<y>")).toBe("x&lt;y&gt;");
  });
});

describe("the term popover", () => {
  let root: HTMLElement;
  beforeEach(() => {
    root = document.createElement("div");
    root.innerHTML = `<p>A ${term("parallax")} and a ${term("ghost", "ghosts")}.</p><p id="other">Elsewhere</p>
      <span data-term="visit">visits</span> <span data-term="nope">nope</span>`;
    document.body.appendChild(root);
    enhanceTerms(root);
  });
  afterEach(() => {
    closeTermPopover();
    root.remove();
    vi.useRealTimers();
  });
  const btn = (slug: string) => root.querySelector<HTMLButtonElement>(`.gl-term[data-term="${slug}"]`)!;
  const pop = () => document.getElementById(POP_ID)!;

  it("upgrades data-term elements and leaves unknown ones alone", () => {
    expect(btn("visit").tagName).toBe("BUTTON");
    expect(btn("visit").textContent).toBe("visits");
    expect(root.querySelector('span[data-term="nope"]')).not.toBeNull();
  });

  it("opens on focus with the sentence, as a described tooltip", () => {
    btn("parallax").focus();
    expect(pop().hidden).toBe(false);
    expect(pop().getAttribute("role")).toBe("tooltip");
    expect(pop().textContent).toContain("yearly wobble");
    expect(btn("parallax").getAttribute("aria-describedby")).toBe(POP_ID);
    expect(termState()).toEqual({ slug: "parallax", pinned: false });
  });

  it("pins on click or tap and closes on a second click", () => {
    btn("ghost").click();
    expect(pop().hidden).toBe(false);
    expect(btn("ghost").getAttribute("aria-expanded")).toBe("true");
    expect(termState().pinned).toBe(true);
    btn("ghost").click();
    expect(pop().hidden).toBe(true);
    expect(btn("ghost").getAttribute("aria-expanded")).toBe("false");
    expect(btn("ghost").hasAttribute("aria-describedby")).toBe(false);
  });

  it("closes on Escape", () => {
    btn("parallax").click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(pop().hidden).toBe(true);
    expect(termState().slug).toBeNull();
  });

  it("closes on a click elsewhere", () => {
    btn("parallax").click();
    root.querySelector<HTMLElement>("#other")!.click();
    expect(pop().hidden).toBe(true);
  });

  it("opens on hover and closes shortly after the pointer leaves", () => {
    vi.useFakeTimers();
    btn("ghost").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    expect(pop().hidden).toBe(false);
    btn("ghost").dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
    expect(pop().hidden).toBe(false);
    vi.advanceTimersByTime(200);
    expect(pop().hidden).toBe(true);
  });

  it("stays open while the pointer is over the sentence itself", () => {
    vi.useFakeTimers();
    btn("ghost").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    btn("ghost").dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
    pop().dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    vi.advanceTimersByTime(300);
    expect(pop().hidden).toBe(false);
  });

  it("keeps one shared popover and moves it between words", () => {
    openTerm(btn("parallax"));
    openTerm(btn("ghost"), true);
    expect(document.querySelectorAll(`#${POP_ID}`).length).toBe(1);
    expect(btn("parallax").hasAttribute("aria-describedby")).toBe(false);
    expect(pop().textContent).toContain("reflection");
  });

  it("closes when the page route changes", () => {
    btn("parallax").click();
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(pop().hidden).toBe(true);
  });
});
