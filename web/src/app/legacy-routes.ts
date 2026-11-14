// Links written by the area-first shell (live 14 Nov, 21:10–22:00 UTC: Explore / Analyze / Research)
// map to the same pages on this shell, so a link shared in that hour still opens the right thing.
// Returns the hash this shell uses, or null when the hash is already one of ours.
export function legacyHash(hash: string): string | null {
  const [head, feature = "", ...tail] = hash.replace(/^#\/?/, "").split("/");
  const rest = tail.join("/");
  const at = (page: string) => `#/${page}${rest ? `/${rest}` : ""}`;
  if (head === "explore") {
    if (feature === "sky") return at("sky");
    if (feature === "learn") return at("learn");
    if (feature === "community") return "#/community";
    // Guided practice → the Review page's practice round (known answers, with feedback).
    if (feature === "play") return "#/review";
    return null;
  }
  if (head === "analyze") {
    if (feature === "candidates") return at("candidates");
    if (feature === "review") return "#/review";
    if (feature === "c" && rest) return `#/c/${rest}`;
    // Measurements (motion, spectrum, light curve) are the Blink page's card.
    return "#/explore";
  }
  if (head === "research") {
    if (feature === "live") return "#/live";
    if (feature === "results") return "#/results";
    // Data & methods, Tools & API.
    return "#/tools";
  }
  return null;
}
