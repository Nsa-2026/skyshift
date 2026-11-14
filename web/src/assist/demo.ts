// Dev-only harness for the check assistant (screenshots and manual checks). Not imported by the
// site. Real candidates from fixtures/api/candidates.json (pilot sample), plus one clearly labelled
// synthetic mover that carries the optional extras (detections, flags) to show the other states.
// URL: ?c=<candidate id>&details=1&votes=none|few|mover|junk
import "../styles/base.css";
import fixture from "../../../fixtures/api/candidates.json";
import type { Counts } from "../vote/model";
import { KIND_TEXT, mountChecklist, mountWhatNext, type AssistCandidate } from "./index";

const real = fixture.candidates as unknown as AssistCandidate[];
const pick = (f: (c: AssistCandidate) => boolean) => real.find(f)!;
const base = pick((c) => c.kind === "mover" && c.reasons.includes("two_exposures"));
const synthetic: AssistCandidate = {
  ...base,
  id: "demo-synthetic-mover",
  why: "Synthetic demo record (not real data): a mover with per-exposure positions, to show the extras.",
  flags: ["CROSSTALK"],
  rate_arcsec_h: 42.8,
  detections: [0, 1, 2, 3].map((i) => ({
    ra: base.ra - (i * 0.000415 + [0, 4e-5, -3e-5, 2e-5][i]) / Math.cos((base.dec * Math.PI) / 180),
    dec: base.dec + i * 0.00004 + [3e-5, -2e-5, 0, 1e-5][i],
    mjd: base.mjd + (i * 2.1) / 1440,
    det: base.det,
    pos_err_arcsec: 0.31,
    flux_ujy: 2320 + [0, 40, -25, 15][i],
    flux_err_ujy: 41,
    snr: 56,
    sc_pos_km: [i === 3 ? NaN : 4561.2 + i, -3920.7, 3311.4 - i] as [number, number, number],
  })),
};
const choices: { label: string; c: AssistCandidate }[] = [
  { label: "Fast mover, two exposures", c: base },
  { label: "Fast mover, two detectors", c: pick((c) => c.kind === "mover" && c.reasons.includes("two_detectors")) },
  { label: "Moving star, Gaia match", c: pick((c) => c.kind === "high_pm" && c.reasons.length === 0) },
  { label: "Moving star, red", c: pick((c) => c.kind === "high_pm" && c.labels.includes("red")) },
  { label: "Moving star, too fast", c: pick((c) => c.kind === "high_pm" && c.reasons.includes("over_pm_cap")) },
  { label: "Synthetic mover with extras", c: synthetic },
];

const VOTES: Record<string, Counts | null> = {
  none: null,
  few: { mover: 2, changing: 0, junk: 1, unsure: 0 },
  mover: { mover: 6, changing: 0, junk: 1, unsure: 0 },
  junk: { mover: 1, changing: 0, junk: 5, unsure: 0 },
};

const q = new URLSearchParams(location.search);
let current = choices.find((x) => x.c.id === q.get("c"))?.c ?? choices[0].c;
let details = q.get("details") === "1";
let votes = VOTES[q.get("votes") ?? "none"] ?? null;

const root = document.getElementById("demo")!;
root.style.cssText = "max-width: 760px; margin: 0 auto; padding: 24px 16px 96px; display: grid; gap: 32px;";
root.innerHTML = `
  <header style="display:grid; gap:12px">
    <p class="muted" style="font-size: var(--step--1)">Check assistant demo (dev only). Real pilot candidates from fixtures/api; one synthetic record, labelled.</p>
    <label style="display:grid; gap:4px; max-width: 360px">Candidate
      <select id="pick" class="input">${choices.map((x, i) => `<option value="${i}">${x.label}</option>`).join("")}</select>
    </label>
    <div style="display:flex; gap:12px; flex-wrap:wrap">
      <div class="seg" role="group" aria-label="Details"><button data-d="0">Plain</button><button data-d="1">Details</button></div>
      <div class="seg" role="group" aria-label="Votes"><button data-v="none">Votes not loaded</button><button data-v="few">3 votes</button><button data-v="mover">Mover consensus</button><button data-v="junk">Junk consensus</button></div>
    </div>
    <div id="head"></div>
  </header>
  <div id="check"></div>
  <div id="next"></div>`;

const sel = root.querySelector<HTMLSelectElement>("#pick")!;
sel.value = String(choices.findIndex((x) => x.c === current));
const head = root.querySelector<HTMLElement>("#head")!;
const check = mountChecklist(root.querySelector("#check")!, current, { details, onVote: () => alert("The vote panel would open here.") });
const next = mountWhatNext(root.querySelector("#next")!, current, votes, { base: "https://skyshift.web.app/" });

function paint() {
  head.innerHTML = `<h1 style="font-size: var(--step-2)">${KIND_TEXT[current.kind].short}</h1><p class="muted">${current.why}</p>`;
  root.querySelectorAll<HTMLButtonElement>("[data-d]").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.d === "1") === details)));
  root.querySelectorAll<HTMLButtonElement>("[data-v]").forEach((b) => b.setAttribute("aria-pressed", String(VOTES[b.dataset.v!] === votes)));
}
sel.addEventListener("change", () => {
  current = choices[Number(sel.value)].c;
  check.update(current);
  next.update(current, votes);
  paint();
});
root.addEventListener("click", (ev) => {
  const b = (ev.target as Element).closest("button");
  if (b?.dataset.d) {
    details = b.dataset.d === "1";
    check.update(current, { details });
  } else if (b?.dataset.v) {
    votes = VOTES[b.dataset.v];
    next.update(current, votes);
  } else return;
  paint();
});
paint();
