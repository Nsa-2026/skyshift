// The filter menu drives the blink's Difference view and hosts its Contrast slider, without the
// blink's own click handler mistaking filter buttons for blink modes.
import { describe, expect, it, vi } from "vitest";
import { mountFilters } from "./index";

/** The parts of the blink (P4) the filters touch, with a delegated handler like the real one. */
function fakeBlink() {
  const el = document.createElement("section");
  el.innerHTML = `
    <div class="bk" data-mode="blink">
      <div class="bk-col">
        <div class="bk-mat"><div class="bk-plate"></div></div>
        <div class="seg bk-modes">
          <button type="button" data-mode="blink" aria-pressed="true">Blink</button>
          <button type="button" data-mode="difference" aria-pressed="false">Difference</button>
          <button type="button" data-mode="stack" aria-pressed="false">Stack</button>
        </div>
        <div class="bk-adjust">
          <div class="bk-adj-row"><label class="bk-adj-k">Contrast</label>
            <input class="bk-contrast" type="range" min="0" max="1" step="0.01" value="0.45" /></div>
        </div>
      </div>
    </div>`;
  document.body.append(el);
  const root = el.querySelector<HTMLElement>(".bk")!;
  const modes: string[] = [];
  root.addEventListener("click", (ev) => {
    const b = (ev.target as Element).closest<HTMLButtonElement>("button");
    if (!b?.dataset.mode) return;
    modes.push(b.dataset.mode);
    root.dataset.mode = b.dataset.mode;
    root.querySelectorAll<HTMLButtonElement>(".bk-modes [data-mode]").forEach((o) =>
      o.setAttribute("aria-pressed", String(o.dataset.mode === b.dataset.mode)),
    );
  });
  const contrastInput = vi.fn();
  el.querySelector(".bk-contrast")!.addEventListener("input", contrastInput);
  return { el, root, modes, contrastInput };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("filter menu and the blink", () => {
  it("moves Difference into the menu and Contrast next to Brightness", async () => {
    const { el, root, modes, contrastInput } = fakeBlink();
    const h = mountFilters(el, vi.fn());
    const diffBlink = root.querySelector<HTMLButtonElement>('.bk-modes [data-mode="difference"]')!;
    expect(diffBlink.hidden).toBe(true);

    // Contrast now sits beside Brightness under the plate, and still reaches the blink's listener.
    const tools = el.querySelector(".flt-tools")!;
    const contrast = tools.querySelector<HTMLInputElement>(".bk-contrast")!;
    expect(tools.querySelector("[data-knee]")).not.toBeNull();
    expect(contrast).not.toBeNull();
    expect(el.querySelector<HTMLElement>(".bk-adj-row")!.hidden).toBe(true);
    contrast.dispatchEvent(new Event("input"));
    expect(contrastInput).toHaveBeenCalledTimes(1);
    // No filter picture: Contrast shows, Brightness (nothing to change) doesn't; nothing is greyed out.
    const shown = (sel: string) => !tools.querySelector<HTMLElement>(sel)!.closest("label")!.hidden;
    expect(shown(".bk-contrast")).toBe(true);
    expect(shown("[data-knee]")).toBe(false);
    expect(contrast.disabled).toBe(false);

    // Difference in the menu puts the blink in its Difference view; no picture covers the plate.
    el.querySelector<HTMLButtonElement>('[data-filter="diff"]')!.click();
    expect(root.dataset.mode).toBe("difference");
    expect(el.querySelector<HTMLElement>(".flt-layer")!.hidden).toBe(true);
    expect(el.querySelector(".flt-active-name")!.textContent).toBe("Filter: difference");
    expect(shown(".bk-contrast")).toBe(true);

    // A colour filter returns the blink to Blink, and never sends its own name to the blink.
    el.querySelector<HTMLButtonElement>('[data-filter="cold"]')!.click();
    expect(root.dataset.mode).toBe("blink");
    expect(modes).toEqual(["difference", "blink"]);
    expect(shown(".bk-contrast")).toBe(false);
    expect(shown("[data-knee]")).toBe(true);
    // Nothing drawn yet (no view): no "doesn't change with the date" line.
    expect(tools.querySelector<HTMLElement>(".flt-still")!.hidden).toBe(true);

    // Stack pressed on the blink while Difference is the filter: the menu follows.
    el.querySelector<HTMLButtonElement>('[data-filter="diff"]')!.click();
    root.querySelector<HTMLButtonElement>('.bk-modes [data-mode="stack"]')!.click();
    await flush();
    expect(el.querySelector('[data-filter="none"]')!.getAttribute("aria-pressed")).toBe("true");
    expect(root.dataset.mode).toBe("stack");

    // Destroy puts the blink back as it was.
    h.destroy();
    expect(diffBlink.hidden).toBe(false);
    expect(el.querySelector(".bk-adj-row .bk-contrast")).toBe(contrast);
    expect(el.querySelector<HTMLElement>(".bk-adj-row")!.hidden).toBe(false);
    el.remove();
  });
});
