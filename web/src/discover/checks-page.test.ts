// The To check page with a contracts/api.md v3.23/v3.24 list (synthetic: ../review/checks.fixture.json
// and its Less likely files, ../review/checks.more-*.fixture.json): each row's checks, the certain
// matches listed apart, the "Less likely" divider and the zone files it opens.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import pilot from "../../../fixtures/api/candidates.json";
import sample from "../review/checks.fixture.json";
import moreEcl04 from "../review/checks.more-ecl04.fixture.json";
import moreHunt from "../review/checks.more-hunt.fixture.json";
import { _resetMoreForTests } from "../api/more";
import type { CandidatesResponse } from "../api/types";

const loadCandidates = vi.fn<() => Promise<CandidatesResponse | null>>();
vi.mock("../api/client", async (orig) => ({ ...(await orig<typeof import("../api/client")>()), loadCandidates: () => loadCandidates() }));
vi.mock("./votes", () => ({
  openVoteReader: async () => ({ mode: "local", notice: null, counts: async () => new Map(), votedOn: () => false }),
  onVoted: () => () => {},
}));

const { mountDiscover } = await import("./index");
const tick = () => new Promise((r) => setTimeout(r, 0));

async function mount(hash = "#/candidates") {
  history.replaceState(null, "", `/${hash}`);
  const el = document.createElement("main");
  document.body.append(el);
  const page = mountDiscover(el, { navigate: vi.fn(), explore: vi.fn() });
  for (let i = 0; i < 5; i++) await tick();
  page.show(hash.replace(/^#\/candidates\/?/, "") || null);
  return { el, page };
}

// The Less likely files, served by path; anything else isn't there.
const files: Record<string, unknown> = { "/data/candidates-more/hunt.json": moreHunt, "/data/candidates-more/ecl04.json": moreEcl04 };
const fetchMock = vi.fn(async (url: string) => {
  const body = files[new URL(url, location.href).pathname];
  return body ? new Response(JSON.stringify(body)) : new Response("", { status: 404 });
});
const moreReads = () => fetchMock.mock.calls.map(([u]) => new URL(u, location.href).pathname).filter((u) => u.includes("candidates-more"));
const settle = async () => {
  for (let i = 0; i < 6; i++) await tick();
};

const rowIds = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>(".dv-row")].map((r) => r.dataset.id);
const row = (el: HTMLElement, id: string) => el.querySelector<HTMLElement>(`.dv-row[data-id="${id}"]`)!;

describe("the To check list with checks and statuses (v3.23)", () => {
  beforeEach(() => {
    _resetMoreForTests();
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
    loadCandidates.mockResolvedValue(structuredClone(sample) as unknown as CandidatesResponse);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("shows every check on its row, strong first, each marked in words with what was measured and what to look at", async () => {
    const { el } = await mount();
    const items = [...row(el, "hunt-664250077777-87801640").querySelectorAll<HTMLElement>(".dv-checks .ck")];
    expect(items.map((li) => li.querySelector(".ck-level")!.textContent)).toEqual(["Can't rule out:", "Sign for:", "Sign for:"]);
    expect(items[0].querySelector(".ck-look")?.textContent).toBe("Look: Hours, D6: look for a third dot along the same line in the next session.");
    expect(items[1].querySelector(".ck-text")?.textContent).toBe("NASA marked OUTLIER at the peak of 1 of its 2 dots, as for 68% of known asteroids: a sign it moved.");
    expect(items[1].querySelector(".ck-look")).toBeNull();
    const strong = row(el, "hunt-664250081234-87801655").querySelector<HTMLElement>(".ck")!;
    expect(strong.classList.contains("is-strong")).toBe(true);
    expect(strong.textContent).toContain("Sign against");
    expect(strong.textContent).toContain("D1 measured 76 ± 41 µJy at its place");
  });

  it("lets the checks replace the doubt and caution chips read from codes and flags", async () => {
    const { el } = await mount();
    const r = row(el, "hunt-664250077777-87801640"); // flags: outlier, with a flag_outlier check
    expect(r.querySelector(".tag-caution, .tag-doubt")).toBeNull();
    expect(r.querySelector(".tag-support")?.textContent).toBe("Seen in two exposures");
    // A row without checks shows no list.
    expect(row(el, "hunt-664250055555-87801599").querySelector(".dv-checks")).toBeNull();
  });

  it("lists to check only what's open, with the known bodies and instrument flaws one click apart", async () => {
    const { el } = await mount();
    const ids = rowIds(el);
    expect(ids).not.toContain("ecl04-701100229999-88001190");
    expect(ids).not.toContain("hunt-664250099999-87801700");
    const tabs = [...el.querySelectorAll<HTMLButtonElement>(".dv-lists [data-status]")];
    expect(tabs.map((b) => b.textContent)).toEqual(["To check 9", "Known bodies 2", "Instrument flaws 1"]);
    expect(tabs[0].getAttribute("aria-pressed")).toBe("true");

    tabs[1].click();
    expect(location.hash).toBe("#/candidates/status=known");
    expect(rowIds(el)).toEqual(["ecl04-701100229999-88001190", "ecl04-701100231111-88001201"]);
    expect(row(el, "ecl04-701100229999-88001190").querySelector(".ck-status")?.textContent).toBe("Known body: 306P/LINEAR");
    expect(el.querySelector(".dv-lists-text")?.textContent).toContain("Listed apart, not deleted.");
    expect(el.querySelector<HTMLElement>(".dv-scope")!.hidden).toBe(true);
    expect(el.querySelector(".dv-less")).toBeNull();

    el.querySelector<HTMLButtonElement>('.dv-lists [data-status="flaw"]')!.click();
    expect(rowIds(el)).toEqual(["hunt-664250099999-87801700"]);
    expect(row(el, "hunt-664250099999-87801700").querySelector(".ck-status")?.textContent).toBe("Instrument flaw");
  });

  it("opens on the list a link names", async () => {
    const { el } = await mount("#/candidates/status=flaw");
    expect(rowIds(el)).toEqual(["hunt-664250099999-87801700"]);
    expect(el.querySelector('.dv-lists [data-status="flaw"]')?.getAttribute("aria-pressed")).toBe("true");
  });

  it("puts the open ones with a strong check under “Less likely”, kind by kind, at the end", async () => {
    const { el } = await mount();
    const list = el.querySelector<HTMLOListElement>(".dv-list")!;
    const kids = [...list.children] as HTMLElement[];
    const less = kids.findIndex((k) => k.classList.contains("dv-less"));
    // v3.24: Sinuhe 14″ away is a `warn` (near, not certain), so it stays with the rest.
    expect(less).toBe(7);
    expect(kids[6].dataset.id).toBe("ecl04-701100223344-88001122");
    expect(kids[less].querySelector(".dv-less-h")?.textContent).toBe("Less likely 2");
    const after = kids.slice(less + 1).map((k) => (k.classList.contains("dv-less-kind") ? `[${k.textContent!.trim()}]` : k.dataset.id));
    expect(after).toEqual(["[Fast movers 1]", "hunt-664250081234-87801655", "[One or two? 1]", "hunt-sh-65039007800055"]);
    expect(kids.slice(0, less).every((k) => !k.classList.contains("is-less"))).toBe(true);
  });

  it("draws no divider in another order", async () => {
    const { el } = await mount("#/candidates/sort=brightest");
    expect(el.querySelector(".dv-less, .dv-morefiles")).toBeNull();
    expect(el.querySelectorAll(".dv-row")).toHaveLength(9);
  });
});

describe("the Less likely files (v3.24 `more`)", () => {
  beforeEach(() => {
    _resetMoreForTests();
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
    loadCandidates.mockResolvedValue(structuredClone(sample) as unknown as CandidatesResponse);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  const zoneButtons = (el: HTMLElement) => [...el.querySelectorAll<HTMLButtonElement>(".dv-less [data-more-file]")];

  it("says under “Less likely” how many the zone files hold, and reads none until asked", async () => {
    const { el } = await mount();
    const files = el.querySelector(".dv-less .dv-morefiles")!;
    expect(files.querySelector(".dv-morefiles-text")?.textContent).toBe(
      "5 more grade C fast movers are kept in a file per zone, so this page stays light. Open a zone to list them here:",
    );
    expect(zoneButtons(el).map((b) => [b.textContent, b.getAttribute("aria-pressed")])).toEqual([
      ["Hunt3", "false"],
      ["Ecl042", "false"],
      ["All zones5", "false"],
    ]);
    expect(moreReads()).toEqual([]);
  });

  it("reads a zone's file when its button is pressed, lists it under “Less likely”, and takes it off again", async () => {
    const { el } = await mount();
    zoneButtons(el)[0].click();
    await settle();
    expect(moreReads()).toEqual(["/data/candidates-more/hunt.json"]);
    const hunt = zoneButtons(el)[0];
    expect(hunt.getAttribute("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe(hunt);
    expect(el.querySelector(".dv-morefiles-status")?.textContent).toBe("The Hunt file: 3 objects listed below, under “Less likely”.");
    expect(el.querySelector(".dv-less-h")?.textContent).toBe("Less likely 5");
    const kids = [...el.querySelector(".dv-list")!.children] as HTMLElement[];
    const less = kids.findIndex((k) => k.classList.contains("dv-less"));
    const after = kids.slice(less + 1).map((k) => (k.classList.contains("dv-less-kind") ? `[${k.textContent!.trim()}]` : k.dataset.id));
    expect(after).toEqual([
      "[Fast movers 4]",
      "hunt-664250081234-87801655",
      "hunt-664250090001-87801720",
      "hunt-664250090002-87801731",
      "hunt-664250090003-87801745",
      "[One or two? 1]",
      "hunt-sh-65039007800055",
    ]);
    // Its checks are on its row, like any other.
    expect(row(el, "hunt-664250090002-87801731").querySelector(".ck.is-strong")?.textContent).toContain("4 px from the photo's edge");
    expect(el.querySelector('.dv-lists [data-status="open"]')?.textContent).toBe("To check 12");

    zoneButtons(el)[0].click();
    await settle();
    expect(rowIds(el)).not.toContain("hunt-664250090001-87801720");
    expect(zoneButtons(el)[0].getAttribute("aria-pressed")).toBe("false");
    expect(el.querySelector(".dv-morefiles-status")?.textContent).toBe("The Hunt file: taken off the list.");
    // Open again: already read, nothing fetched.
    zoneButtons(el)[0].click();
    await settle();
    expect(rowIds(el)).toContain("hunt-664250090001-87801720");
    expect(moreReads()).toHaveLength(1);
  });

  it("opens every zone at once, and says when a file can't be read", async () => {
    delete files["/data/candidates-more/ecl04.json"];
    try {
      const { el } = await mount();
      zoneButtons(el)[2].click();
      await settle();
      expect(moreReads().sort()).toEqual(["/data/candidates-more/ecl04.json", "/data/candidates-more/hunt.json"]);
      expect(zoneButtons(el).map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "false", "false"]);
      expect(el.querySelector(".dv-morefiles-status")?.textContent).toBe(
        "The Hunt file: 3 objects listed below, under “Less likely”. The Ecl04 file couldn't be read just now. Try again.",
      );
    } finally {
      files["/data/candidates-more/ecl04.json"] = moreEcl04;
    }
  });

  it("offers only the zones the filters let through, even when nothing in candidates.json matches", async () => {
    const { el } = await mount("#/candidates/grade=C&zone=ecl04");
    expect(el.querySelectorAll(".dv-row")).toHaveLength(0);
    expect(zoneButtons(el).map((b) => b.textContent)).toEqual(["Ecl042"]);
    zoneButtons(el)[0].click();
    await settle();
    expect(rowIds(el)).toEqual(["ecl04-701100240001-88001240", "ecl04-701100240002-88001255"]);
    expect(el.querySelector(".dv-less-h")?.textContent).toBe("Less likely 2");
  });

  it("offers nothing on the lists apart", async () => {
    const { el } = await mount("#/candidates/status=known");
    expect(el.querySelector(".dv-morefiles")).toBeNull();
  });
});

describe("an older list without checks or statuses", () => {
  beforeEach(() => {
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
    loadCandidates.mockResolvedValue(structuredClone(pilot) as unknown as CandidatesResponse);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("works as before: no switch, no divider, no checks, no zone files, the chips it always had", async () => {
    const { el } = await mount();
    expect(el.querySelector<HTMLElement>(".dv-lists")!.hidden).toBe(true);
    expect(el.querySelector(".dv-less, .dv-checks, .ck-status, .dv-morefiles")).toBeNull();
    expect(el.querySelector(".dv-count")?.textContent).toBe("52 of 194 objects");
    expect(moreReads()).toEqual([]);
  });

  it("keeps a graded older list's grade C where it was, with no divider", async () => {
    const graded = structuredClone(pilot) as unknown as CandidatesResponse;
    graded.candidates.forEach((c, i) => Object.assign(c, { grade: i % 3 === 0 ? "C" : "B" }));
    loadCandidates.mockResolvedValue(graded);
    const { el } = await mount("#/candidates/show=all");
    expect(el.querySelector(".dv-less, .dv-morefiles")).toBeNull();
  });
});
