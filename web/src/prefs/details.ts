// The site-wide "Details" preference (web/DESIGN.md, "Two audiences, one tool"). Off by default.
// On, pages show the dense numbers professionals want: MJDs, rates with errors, flag bit names,
// pipeline scores, raw ids. Mark such elements with class "pro-only" (prefs.css hides them while
// Details is off); "plain-only" is the reverse. The switch lives in the page header.
//
// State: localStorage "skyshift:details" ("on" | "off"), mirrored on <html data-details="on|off">.
// Changes fire a window CustomEvent "skyshift:details" with detail { on }.
import "./prefs.css";

export const DETAILS_KEY = "skyshift:details";
export const DETAILS_EVENT = "skyshift:details";
export interface DetailsChange {
  on: boolean;
}

/** This session's choice, used when storage is blocked (private mode, sandboxed iframes). */
let memory: boolean | null = null;

function stored(): boolean | null {
  try {
    const v = localStorage.getItem(DETAILS_KEY);
    return v === "on" ? true : v === "off" ? false : null;
  } catch {
    return null;
  }
}

function mirror(on: boolean): void {
  if (typeof document !== "undefined") document.documentElement.dataset.details = on ? "on" : "off";
}

/** Is Details on? */
export function getDetails(): boolean {
  return memory ?? stored() ?? false;
}

/** Turn Details on or off: remembered, mirrored on <html>, and announced (only when it changes). */
export function setDetails(on: boolean): void {
  const changed = on !== getDetails();
  memory = on;
  try {
    localStorage.setItem(DETAILS_KEY, on ? "on" : "off");
  } catch {
    /* storage blocked: the choice lasts for this page */
  }
  mirror(on);
  if (changed && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent<DetailsChange>(DETAILS_EVENT, { detail: { on } }));
  }
}

/** Call cb(on) whenever Details changes (here or in another tab). Returns the unsubscribe. */
export function onDetails(cb: (on: boolean) => void): () => void {
  const handler = (ev: Event) => cb((ev as CustomEvent<DetailsChange>).detail?.on ?? getDetails());
  window.addEventListener(DETAILS_EVENT, handler);
  return () => window.removeEventListener(DETAILS_EVENT, handler);
}

/** Set <html data-details> from the stored choice and follow other tabs. Runs once on import. */
function init(): void {
  if (typeof window === "undefined") return;
  mirror(getDetails());
  window.addEventListener("storage", (ev) => {
    if (ev.key !== DETAILS_KEY) return;
    const on = ev.newValue === "on";
    // The other tab has already written storage: compare with what this page shows.
    if (on === (document.documentElement.dataset.details === "on")) return;
    memory = on;
    mirror(on);
    window.dispatchEvent(new CustomEvent<DetailsChange>(DETAILS_EVENT, { detail: { on } }));
  });
}
init();

/**
 * A small labelled switch, "Details", appended to el. Several switches stay in step.
 * Keyboard: Tab to it, Space or Enter toggles (a real button with role="switch").
 */
export function mountDetailsSwitch(el: HTMLElement): { destroy(): void } {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "details-switch";
  btn.setAttribute("role", "switch");
  btn.title = "Show the dense numbers: MJDs, rates with errors, flag names, pipeline scores, raw ids";
  btn.innerHTML =
    '<span class="details-track" aria-hidden="true"><span class="details-thumb"></span></span>' +
    '<span class="details-label">Details</span>';
  const sync = () => btn.setAttribute("aria-checked", String(getDetails()));
  btn.addEventListener("click", () => setDetails(!getDetails()));
  const off = onDetails(sync);
  sync();
  el.append(btn);
  return {
    destroy() {
      off();
      btn.remove();
    },
  };
}

/** For tests: forget this session's choice. */
export function _resetDetailsForTests(): void {
  memory = null;
}
