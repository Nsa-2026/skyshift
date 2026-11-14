// Evidence extras under the object card on the Blink page (web/DESIGN.md, "Lightbox"): three quiet
// sections, closed by default, each loading only when opened.
//   Compare short and long wavelengths: D3 beside D6, the cold-source check.
//   Known objects and long-term observations: known asteroids, Gaia DR3, CatWISE2020, NEOWISE.
//   Downloads: the data behind the page, and ADES drafts for unexplained movers.
// The page hands them what it hands the card (view, object, frames), so they ride on the card's
// handle: withExtras(card, el). A new target clears every section at once and aborts its requests,
// so nothing of the previous target ever shows under the new one's name.
// From PR #214 (春。 and Codex), reworked for the Blink page.
import type { CardHandle } from "../app/modules";
import { mountCompare } from "./compare";
import { mountCrossmatch } from "./crossmatch";
import { mountDownloads } from "./downloads";
import "./extras.css";
import { type Ctx, targetKey } from "./shared";

export interface ExtrasHandle {
  show: CardHandle["show"];
  status: CardHandle["status"];
}

export function mountExtras(el: HTMLElement): ExtrasHandle {
  el.classList.add("ex");
  const sections = [mountCompare(el), mountCrossmatch(el), mountDownloads(el)];
  return {
    show(view, object, frames) {
      const ctx: Ctx = { view, object, frames, key: targetKey(view) };
      for (const s of sections) s.show(ctx);
    },
    status(kind) {
      // The page is fetching a new target (or this one again): drop everything now.
      if (kind === "loading") for (const s of sections) s.clear();
    },
  };
}

/** The card's handle, also feeding the extras in `el`: one line in the page shell. */
export function withExtras(card: CardHandle, el: HTMLElement | null): CardHandle {
  if (!el) return card;
  const extras = mountExtras(el);
  return {
    show(view, object, frames) {
      card.show(view, object, frames);
      extras.show(view, object, frames);
    },
    status(kind, message) {
      card.status(kind, message);
      extras.status(kind, message);
    },
  };
}
