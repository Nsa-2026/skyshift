// The Community page (#/community): what people have done, the list going to astronomers,
// activity, the most debated candidates, this browser's contribution, and how consensus works.
import type { PageContext, PageHandle } from "../app/modules";
import { formatMjd, formatRaDec } from "../app/astro";
import { FORUM_URL } from "../vote";
import { ANSWERS, ANSWER_TEXT, safeStorage, type Counts, type KV } from "../vote/model";
import { VOTED_EVENT } from "../vote/store";
import { mountActivityChart, votesWord, type ChartHandle } from "./chart";
import { loadSnapshot, type Snapshot } from "./source";
import { REPO_URL } from "../tools/docs";
import {
  CONSENSUS,
  CONSENSUS_PCT,
  activity,
  agreement,
  astronomersList,
  mostDebated,
  summarize,
  tally,
  astroFile,
  type AstroRow,
  type CandInfo,
} from "./stats";

export const MPC_URL = "https://minorplanetcenter.net/";
export const MPCHECKER_URL = "https://minorplanetcenter.net/cgi-bin/checkmp.cgi";
export const NICKNAME_KEY = "skyshift.nickname";
/** Re-read the votes when the page is shown again after this long. */
const STALE_MS = 60_000;

const html = String.raw;
const nf = new Intl.NumberFormat("en-GB");
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const n = (x: number) => html`<b class="cm-n">${nf.format(x)}</b>`;
const pct = (x: number) => `${Math.round(x * 100)}%`;
const plural = (x: number, one: string, many: string) => (x === 1 ? one : many);

export const KIND_NAME: Record<CandInfo["kind"], string> = {
  mover: "Fast mover",
  high_pm: "Moving star",
  spectral: "Odd spectrum",
  shape: "One or two?",
  flag: "Flagged dot",
};

const NEXT_TEXT = {
  mpc: { name: "Minor Planet Center", url: MPC_URL },
  backyard_worlds: { name: "Backyard Worlds", url: FORUM_URL },
} as const;

/** Thin segmented bar of the four answers, with its counts in words for screen readers. */
export function splitBar(c: Counts): string {
  const t = ANSWERS.reduce((s, a) => s + c[a], 0);
  const words = ANSWERS.filter((a) => c[a]).map((a) => `${c[a]} ${ANSWER_TEXT[a].label.toLowerCase()}`).join(", ");
  const segs = ANSWERS.filter((a) => c[a]).map((a) => html`<i class="cm-a-${a}" style="flex-grow:${c[a]}"></i>`).join("");
  return html`<span class="cm-split" role="img" aria-label="${t} ${plural(t, "vote", "votes")}: ${words}">${segs}</span>`;
}

/** "3 mover, 2 junk, 1 not sure", only the answers that have votes. */
export function countsText(c: Counts): string {
  return ANSWERS.filter((a) => c[a])
    .map((a) => `${c[a]} ${ANSWER_TEXT[a].label.toLowerCase()}`)
    .join(", ");
}

function candidateHref(id: string, info: CandInfo | undefined): string {
  if (info?.kind === "flag") {
    const p = new URLSearchParams({ ra: info.ra.toFixed(5), dec: info.dec.toFixed(5), clock: info.clock });
    return `?${p}#/explore`;
  }
  return `#/c/${encodeURIComponent(id)}`;
}

function candidateLinkHtml(id: string, info: CandInfo | undefined, label?: string): string {
  const name = label ?? (info ? KIND_NAME[info.kind] : "Candidate");
  const explore = info?.kind === "flag" ? ` data-explore="${esc(id)}"` : "";
  return html`<a class="cm-cand" href="${esc(candidateHref(id, info))}"${explore}>${esc(name)}</a>`;
}

export interface CommunityOptions {
  kv?: KV;
  load?: (kv: KV) => Promise<Snapshot>;
  now?: () => number;
  /**
   * "page" (default): the whole Community page. "astronomers": only the vote count, the list for
   * astronomers with its downloads, what happens next and the consensus rule, for a section of
   * another page (Results → List for astronomers) that gives the heading.
   */
  variant?: "page" | "astronomers";
}

export function mountCommunityWith(el: HTMLElement, ctx: PageContext, opts: CommunityOptions = {}): PageHandle & { ready(): Promise<void> } {
  const kv = opts.kv ?? safeStorage();
  const load = opts.load ?? ((k: KV) => loadSnapshot({ kv: k }));
  const now = opts.now ?? (() => Date.now());
  const embedded = opts.variant === "astronomers";
  const toolsLink = embedded ? html`<a href="#/results/tools/downloads">Tools</a>` : html`<a href="#/tools">Tools and data</a>`;
  const consensusRule = html`<p>Each object to check collects votes (mover, changing, junk or not sure), one per browser, and a vote can't be changed.</p>
          <p>It reaches consensus once it has at least ${CONSENSUS.minVotes} votes and ${CONSENSUS_PCT}% or more agree on one answer.</p>
          <p>Consensus movers and changing sources join the list for astronomers; junk and not-sure verdicts are set aside, and split votes stay open.</p>`;

  el.innerHTML = html`
    <div class="cm${embedded ? " cm-embedded" : ""}">
      ${
        embedded
          ? html`<div class="cm-head">
        <p class="cm-lead" aria-live="polite">Counting votes…</p>
        <p class="cm-source"></p>
      </div>`
          : html`<header class="cm-head">
        <h1 class="cm-title">Community</h1>
        <p class="cm-lead" aria-live="polite">Counting votes…</p>
        <p class="cm-source"></p>
        <p class="cm-cta">
          <a class="btn btn-primary" href="#/candidates/one=1">Start reviewing</a>
          <a class="btn btn-ghost" href="#/candidates">Browse objects to check</a>
        </p>
      </header>`
      }

      <section class="cm-astro" ${embedded ? html`aria-label="The list for astronomers"` : html`aria-labelledby="cm-astro-h"`}>
        <div class="cm-sec-head">
          ${embedded ? "" : html`<h2 id="cm-astro-h">The list for astronomers</h2>`}
          <div class="cm-exports">
            <button class="btn" type="button" data-act="csv" disabled>Download CSV</button>
            <button class="btn" type="button" data-act="json" disabled>Download JSON</button>
          </div>
        </div>
        <p class="cm-sub">Candidates with at least ${CONSENSUS.minVotes} votes where ${CONSENSUS_PCT}% or more said
          <b>mover</b> or <b>changing</b>. Each one is a lead for an astronomer to check, not a discovery.</p>
        <div class="cm-astro-body"></div>
        <p class="cm-status" role="status" aria-live="polite"></p>

        <div class="cm-next">
          <h3>What happens next</h3>
          <dl class="cm-routes">
            <div>
              <dt>Moving stars and brown-dwarf-like movers</dt>
              <dd>Slow movers on the Months clock, often red or faint in Gaia, are posted on the
                <a href="${FORUM_URL}" target="_blank" rel="noopener">Backyard Worlds forum</a>. Its volunteers and
                astronomers check them against WISE and other surveys, and the scientists there decide on follow-up.</dd>
            </div>
            <div>
              <dt>Fast movers in the solar system</dt>
              <dd>Movers on the Hours clock may be asteroids or comets. An astronomer first checks them against known
                orbits (<a href="${MPCHECKER_URL}" target="_blank" rel="noopener">MPChecker</a>); a new object is
                reported to the <a href="${MPC_URL}" target="_blank" rel="noopener">Minor Planet Center</a> by
                observers with an observatory code, usually after more images.</dd>
            </div>
          </dl>
          <p class="cm-honest">SkyShift doesn't report anything itself and claims no discoveries. Astronomers confirm,
            and they report. The CSV and JSON give them positions, times and vote counts to start from; the full list
            of objects to check can be downloaded from ${toolsLink}.</p>
          ${embedded ? html`<h3>How consensus works</h3>${consensusRule}` : ""}
        </div>
      </section>
      ${embedded ? "" : html`

      <section class="cm-open cm-next" aria-labelledby="cm-open-h">
        <h2 id="cm-open-h">Anyone can use this list</h2>
        <p class="cm-sub">The objects to check, their positions and times, the measured motion with its error, the
          cautions against each one, and the vote counts are all public. No account, no API key.</p>
        <p class="cm-honest">Download the current list as CSV or JSON above. The site's own data files are plain
          JSON at stable addresses, so a script can fetch exactly what this page reads.
          <a href="#/tools">Tools and data</a> has working examples, the endpoints the live service exposes, and the
          commands to rerun the pipeline from <a href="${REPO_URL}" target="_blank" rel="noopener">the
          repository</a>.</p>
        <p class="cm-honest">The SPHEREx images are already open at IRSA. What SkyShift adds is the layer on top:
          positions matched across visits, motion measured with its error, catalogue cross-checks, the artefact
          cautions, and the judgement of the people who looked. We publish that layer as it stands, including the
          entries people have voted down.</p>
      </section>

      <div class="cm-pair">
        <section class="cm-activity" aria-labelledby="cm-activity-h">
          <h2 id="cm-activity-h">Activity</h2>
          <p class="cm-sub cm-activity-sub"></p>
          <div class="panel cm-chart"></div>
        </section>
        <section class="cm-debated" aria-labelledby="cm-debated-h">
          <h2 id="cm-debated-h">Most debated</h2>
          <p class="cm-sub">Votes split across answers. These need more eyes: a few more votes can settle them.</p>
          <ol class="cm-debate-list"></ol>
        </section>
      </div>

      <div class="cm-pair">
        <section class="cm-you" aria-labelledby="cm-you-h">
          <h2 id="cm-you-h">Your contribution</h2>
          <div class="cm-you-body"></div>
          <form class="cm-nick" autocomplete="off">
            <label for="cm-nick-input">Nickname on this device <span class="muted">(optional)</span></label>
            <div class="cm-nick-row">
              <input id="cm-nick-input" class="input" name="nick" maxlength="40" spellcheck="false" placeholder="e.g. Plate reader" />
              <button class="btn" type="submit">Save</button>
              <button class="btn btn-ghost" type="button" data-act="nick-clear" hidden>Remove</button>
            </div>
            <p class="cm-fine">Kept in this browser only and never sent with your votes.</p>
          </form>
        </section>
        <section class="cm-how" aria-labelledby="cm-how-h">
          <h2 id="cm-how-h">How consensus works</h2>
          ${consensusRule}
        </section>
      </div>`}
    </div>
  `;

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const leadEl = $<HTMLParagraphElement>(".cm-lead");
  const sourceEl = $<HTMLParagraphElement>(".cm-source");
  const astroEl = $<HTMLDivElement>(".cm-astro-body");
  const statusEl = $<HTMLParagraphElement>(".cm-status");
  const actSubEl = $<HTMLParagraphElement>(".cm-activity-sub");
  const debateEl = $<HTMLOListElement>(".cm-debate-list");
  const youEl = $<HTMLDivElement>(".cm-you-body");
  const nickForm = $<HTMLFormElement>(".cm-nick");
  const nickInput = $<HTMLInputElement>("#cm-nick-input");
  const nickClear = $<HTMLButtonElement>("[data-act=nick-clear]");
  const csvBtn = $<HTMLButtonElement>("[data-act=csv]");
  const jsonBtn = $<HTMLButtonElement>("[data-act=json]");
  // The astronomers' variant has no activity, debated or "you" parts: their renderers return early.
  const chart: ChartHandle | null = embedded ? null : mountActivityChart($<HTMLDivElement>(".cm-chart"));

  let snap: Snapshot | null = null;
  let rows: AstroRow[] = [];
  let info = new Map<string, CandInfo>();
  let loading: Promise<void> | null = null;

  const nickname = (): string => (kv.get(NICKNAME_KEY) ?? "").trim().slice(0, 40);

  // ---- rendering ---------------------------------------------------------------------------
  function renderLead(s: Snapshot) {
    const counts = tally(s.votes);
    const sum = summarize(s.votes, counts);
    const listedIds = [...counts.keys()].filter((id) => {
      const k = s.info.get(id)?.kind;
      return k && k !== "flag";
    }).length;
    const flags = sum.reviewed - listedIds;
    if (!sum.votes) {
      leadEl.innerHTML = s.listed
        ? html`No votes yet. The pipeline has listed ${n(s.listed)} objects to check in NASA's SPHEREx data: things it couldn't explain with Gaia, WISE or a known asteroid. Most are detector artefacts until people check them.`
        : html`No votes yet. Every object on the list needs people to look at it: be the first.`;
    } else {
      const who = html`${n(sum.people)} ${plural(sum.people, "person has", "people have")} cast ${n(sum.votes)} ${plural(sum.votes, "vote", "votes")}`;
      let on: string;
      if (s.listed && listedIds) {
        on = html`on ${n(listedIds)} of the ${n(s.listed)} objects the pipeline listed to check`;
        if (flags > 0) on += html` and ${n(flags)} other flagged ${plural(flags, "dot", "dots")}`;
      } else on = html`on ${n(sum.reviewed)} ${plural(sum.reviewed, "object", "objects")}`;
      const settled = sum.consensus
        ? html`${n(sum.consensus)} reached consensus, and ${n(sum.forAstronomers)} of them ${plural(sum.forAstronomers, "is", "are")} on the list for astronomers.`
        : html`None has reached consensus yet: that takes ${CONSENSUS.minVotes} votes with ${CONSENSUS_PCT}% agreeing.`;
      // What the listed objects are, next to their count: most are artefacts until people check.
      const what = s.listed
        ? " The list holds things the pipeline couldn't explain with Gaia, WISE or a known asteroid. Most are detector artefacts until people check them."
        : "";
      leadEl.innerHTML = `${who} ${on}. ${settled}${what}`;
    }
    const at = new Date(s.loadedAt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    const people = "People are counted by browser: each keeps a random id, never a name.";
    if (s.mode === "firestore") {
      sourceEl.dataset.mode = s.note ? "local" : "firestore";
      sourceEl.textContent = s.note ?? `Everyone's votes, read from the vote server at ${at}. ${people}`;
    } else {
      sourceEl.dataset.mode = "local";
      sourceEl.textContent = s.note
        ? `Local mode: ${s.note} These numbers count only votes cast in this browser.`
        : "Local mode: this copy of the site has no vote server, so these numbers count only votes cast in this browser. On skyshift.web.app they count everyone's.";
    }
  }

  function renderAstro() {
    csvBtn.disabled = jsonBtn.disabled = rows.length === 0;
    if (!rows.length) {
      astroEl.innerHTML = embedded
        ? html`<p class="cm-empty">Nothing has reached consensus as a mover or changing source yet.
        <a href="#/candidates/one=1">Review the objects to check</a>: your vote may settle one.</p>`
        : html`<p class="cm-empty">Nothing has reached consensus as a mover or changing source yet.
        The objects under Most debated are closest: <a href="#/candidates/one=1">review them</a> and your vote may settle one.</p>`;
      return;
    }
    const body = rows
      .map((r) => {
        const i = info.get(r.id);
        const next = NEXT_TEXT[r.next];
        return html`<tr>
          <th scope="row" data-label="Candidate">${candidateLinkHtml(r.id, i)}<span class="cm-id">${esc(r.id)}</span></th>
          <td data-label="Position" class="cm-pos">${r.ra != null && r.dec != null ? esc(formatRaDec({ ra: r.ra, dec: r.dec })) : `<span class="muted">Not loaded</span>`}</td>
          <td data-label="Seen">${r.mjd != null ? esc(formatMjd(r.mjd)) : "–"}${r.clock ? html`<span class="cm-id">${r.clock === "hours" ? "Hours" : "Months"} clock${r.det ? `, D${r.det}` : ""}</span>` : ""}</td>
          <td data-label="Verdict" class="cm-verdict"><b>${ANSWER_TEXT[r.answer].label}</b>
            <span class="cm-agree">${r.agree} of ${r.votes} agree (${pct(r.share)})</span>${splitBar(r.counts)}</td>
          <td data-label="Next step"><a href="${next.url}" target="_blank" rel="noopener">${next.name}</a></td>
        </tr>`;
      })
      .join("");
    astroEl.innerHTML = html`<div class="panel cm-table-wrap">
      <table class="cm-table">
        <caption class="visually-hidden">Candidates at consensus as mover or changing, strongest agreement first</caption>
        <thead><tr><th scope="col">Candidate</th><th scope="col">Position (RA, Dec)</th><th scope="col">Seen (UTC)</th><th scope="col">Verdict</th><th scope="col">Next step</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
  }

  function renderActivity(s: Snapshot) {
    if (!chart) return;
    const a = activity(s.votes, now());
    chart.update(a);
    const span = a.bins.length;
    actSubEl.textContent = !s.votes.length
      ? "Votes per hour will show here once people start voting."
      : `Votes per ${a.unit}, the last ${span} ${a.unit}s in your local time.${a.undated ? ` ${a.undated} still waiting for a server time are left out.` : ""}`;
  }

  function renderDebated(s: Snapshot) {
    if (embedded) return;
    const list = mostDebated(tally(s.votes));
    if (!list.length) {
      debateEl.innerHTML = html`<li class="cm-empty">No split votes right now. Objects show here once they have three or more votes that disagree.</li>`;
      return;
    }
    debateEl.innerHTML = list
      .map((d) => {
        const i = info.get(d.id);
        return html`<li class="cm-debate">
          <div class="cm-debate-row"><span>${candidateLinkHtml(d.id, i)} <span class="cm-debate-id">${esc(d.id)}</span></span><span class="cm-debate-n">${votesWord(d.verdict.votes)}</span></div>
          ${splitBar(d.counts)}
          <p class="cm-debate-words">${esc(countsText(d.counts))}${i?.why ? html`. <span class="muted">${esc(i.why)}</span>` : ""}</p>
        </li>`;
      })
      .join("");
  }

  function renderYou(s: Snapshot) {
    if (embedded) return;
    const nick = nickname();
    nickInput.value = nick;
    nickClear.hidden = !nick;
    const a = agreement(s.mine, tally(s.votes));
    const hello = nick ? html`<p class="cm-hello">Hello, ${esc(nick)}.</p>` : "";
    if (!a.voted) {
      youEl.innerHTML = html`${hello}<p>You haven't voted from this browser yet. The review queue starts with a short
        practice round, so you'll know what a mover looks like first.</p>
        <p><a class="btn" href="#/candidates/one=1">Review your first object</a></p>`;
      return;
    }
    const mix = ANSWERS.filter((x) => a.answers[x])
      .map((x) => `${a.answers[x]} ${ANSWER_TEXT[x].label.toLowerCase()}`)
      .join(", ");
    const agreeText =
      a.rate == null
        ? html`None of them has reached consensus yet, so there's no agreement rate to show. Check back once more people have voted.`
        : html`${n(a.settled)} of them ${plural(a.settled, "has", "have")} reached consensus, and your answer matched it on
            ${n(a.agreed)} (${pct(a.rate)}).`;
    const meter =
      a.rate == null
        ? ""
        : html`<div class="cm-meter" role="img" aria-label="Agreement with consensus: ${pct(a.rate)}">
            <i style="width:${(a.rate * 100).toFixed(1)}%"></i></div>`;
    youEl.innerHTML = html`${hello}
      <p class="cm-you-lead">You've voted on ${n(a.voted)} ${plural(a.voted, "object", "objects")} from this browser: ${esc(mix)}.</p>
      <p>${agreeText}</p>${meter}
      <p class="cm-fine">Counted from this browser's own memory of its votes. Disagreeing isn't wrong: early votes are how hard cases get found.</p>`;
  }

  function renderAll(s: Snapshot) {
    renderLead(s);
    renderAstro();
    renderActivity(s);
    renderDebated(s);
    renderYou(s);
  }

  async function refresh() {
    const s = await load(kv);
    snap = s;
    info = new Map(s.info);
    const counts = tally(s.votes);
    rows = astronomersList(counts, (id) => info.get(id));
    renderAll(s);
    // Dots flagged by visitors live in the store, not in candidates.json: fetch their positions.
    const missing = [...new Set([...rows.map((r) => r.id), ...mostDebated(counts).map((d) => d.id)])].filter((id) => !info.has(id));
    if (missing.length) {
      const found = await s.lookup(missing).catch(() => new Map<string, CandInfo>());
      if (found.size && snap === s) {
        for (const [id, i] of found) info.set(id, i);
        rows = astronomersList(counts, (id) => info.get(id));
        renderAstro();
        renderDebated(s);
      }
    }
  }

  function ensure(force = false): Promise<void> {
    if (loading) return loading;
    if (!force && snap && now() - snap.loadedAt < STALE_MS) {
      if (snap) renderYou(snap);
      return Promise.resolve();
    }
    loading = refresh()
      .catch(() => {
        leadEl.textContent = "Couldn't count the votes. Reload the page to try again.";
      })
      .finally(() => {
        loading = null;
      });
    return loading;
  }

  // ---- actions -----------------------------------------------------------------------------
  function download(name: string, text: string, type: string) {
    try {
      const url = URL.createObjectURL(new Blob([text], { type }));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      statusEl.dataset.kind = "ok";
      statusEl.textContent = `Saved ${name}.`;
    } catch {
      statusEl.dataset.kind = "error";
      statusEl.textContent = "This browser couldn't make the file. Try another browser.";
    }
  }


  function onClick(ev: MouseEvent) {
    const t = ev.target as Element;
    const ex = t.closest<HTMLAnchorElement>("a[data-explore]");
    if (ex && el.contains(ex)) {
      const i = info.get(ex.dataset.explore ?? "");
      if (i && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && ev.button === 0) {
        ev.preventDefault();
        ctx.explore({ ra: i.ra, dec: i.dec, clock: i.clock });
      }
      return;
    }
    const btn = t.closest<HTMLElement>("[data-act]");
    if (!btn || !el.contains(btn)) return;
    if (btn.dataset.act === "nick-clear") return clearNick();
    if (!snap) return;
    switch (btn.dataset.act) {
      case "csv":
      case "json": {
        const meta = { generated: new Date(now()).toISOString(), votes_from: snap.mode, data_release: snap.dataRelease };
        const file = astroFile(btn.dataset.act, rows, meta, location.href);
        download(file.name, file.text, file.type);
        break;
      }
    }
  }

  function clearNick() {
    kv.set(NICKNAME_KEY, "");
    nickInput.value = "";
    if (snap) renderYou(snap);
    else nickClear.hidden = true;
    nickInput.focus();
  }

  function onNick(ev: SubmitEvent) {
    ev.preventDefault();
    const v = nickInput.value.replace(/\s+/g, " ").trim().slice(0, 40);
    kv.set(NICKNAME_KEY, v);
    if (snap) renderYou(snap);
    else nickClear.hidden = !v;
  }

  const onVoted = () => {
    if (snap) snap = { ...snap, loadedAt: -Infinity };
  };
  el.addEventListener("click", onClick);
  nickForm?.addEventListener("submit", onNick);
  addEventListener(VOTED_EVENT, onVoted);

  return {
    show() {
      void ensure();
    },
    destroy() {
      el.removeEventListener("click", onClick);
      nickForm?.removeEventListener("submit", onNick);
      removeEventListener(VOTED_EVENT, onVoted);
      chart?.destroy();
      el.innerHTML = "";
    },
    ready: () => loading ?? Promise.resolve(),
  };
}

