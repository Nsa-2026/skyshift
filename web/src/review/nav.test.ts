import { expect, it } from "vitest";
import { lastDiscoverHash, lastDiscoverQuery, rememberDiscover, trackDiscover } from "./nav";

it("returns from a candidate to the filtered list, even when the filter was written with replaceState", () => {
  history.replaceState(null, "", "/#/candidates/kind=mover");
  trackDiscover();
  expect(lastDiscoverQuery()).toBe("kind=mover");
  // Discover rewrites its filter in place (no hashchange), then records it.
  history.replaceState(null, "", "/#/candidates/kind=mover&grade=A&sort=fewest");
  rememberDiscover();
  history.replaceState(null, "", "/#/c/example");
  dispatchEvent(new HashChangeEvent("hashchange"));
  expect(lastDiscoverHash()).toBe("#/candidates/kind=mover&grade=A&sort=fewest");
  expect(lastDiscoverQuery()).toBe("kind=mover&grade=A&sort=fewest");
});
