// The To check page with a contracts/api.md v3.23 list (synthetic: ../review/checks.fixture.json):
// each row's checks, the certain matches listed apart, and the "Less likely" divider.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import pilot from "../../../fixtures/api/candidates.json";
import sample from "../review/checks.fixture.json";
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

const rowIds = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>(".dv-row")].map((r) => r.dataset.id);
const row = (el: HTMLElement, id: string) => el.querySelector<HTMLElement>(`.dv-row[data-id="${id}"]`)!;

describe("the To check list with checks and statuses (v3.23)", () => {
  beforeEach(() => loadCandidates.mockResolvedValue(structuredClone(sample) as unknown as CandidatesResponse));
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("shows every check on its row, strong first, each marked in words with what was measured and what to look at", async () => {
    const { el } = await mount();
    const items = [...row(el, "hunt-664250077777-87801640").querySelectorAll<HTMLElement>(".dv-checks .ck")];
    expect(items.map((li) => li.querySelector(".ck-level")!.textContent)).toEqual(["Can't rule out:", "Can't rule out:", "Sign for:"]);
    expect(items[1].querySelector(".ck-text")?.textContent).toBe("NASA marked 3 of the 9 pixels at its second dot OUTLIER.");
    expect(items[1].querySelector(".ck-look")?.textContent).toBe("Look: Hours, D6, photo 2: a single very sharp pixel is a cosmic ray, not a body.");
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
    expect(less).toBe(6);
    expect(kids[less].querySelector(".dv-less-h")?.textContent).toBe("Less likely 3");
    const after = kids.slice(less + 1).map((k) => (k.classList.contains("dv-less-kind") ? `[${k.textContent!.trim()}]` : k.dataset.id));
    expect(after).toEqual(["[Fast movers 2]", "ecl04-701100223344-88001122", "hunt-664250081234-87801655", "[One or two? 1]", "hunt-sh-65039007800055"]);
    expect(kids.slice(0, less).every((k) => !k.classList.contains("is-less"))).toBe(true);
  });

  it("draws no divider in another order", async () => {
    const { el } = await mount("#/candidates/sort=brightest");
    expect(el.querySelector(".dv-less")).toBeNull();
    expect(el.querySelectorAll(".dv-row")).toHaveLength(9);
  });
});

describe("an older list without checks or statuses", () => {
  beforeEach(() => loadCandidates.mockResolvedValue(structuredClone(pilot) as unknown as CandidatesResponse));
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("works as before: no switch, no divider, no checks, the chips it always had", async () => {
    const { el } = await mount();
    expect(el.querySelector<HTMLElement>(".dv-lists")!.hidden).toBe(true);
    expect(el.querySelector(".dv-less, .dv-checks, .ck-status")).toBeNull();
    expect(el.querySelector(".dv-count")?.textContent).toBe("52 of 194 objects");
  });
});
