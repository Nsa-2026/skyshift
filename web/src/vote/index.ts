// Flag what moved (P4). The blink hands a clicked dot to pick(); "Flag it" creates a `candidates`
// doc. Everyone can vote mover / changing / junk / not sure on candidates near the current view.
// Firestore when the Firebase config is present, else a labelled local mode.
// Nothing here throws into the page: every failure becomes a plain-words status line.
//
// Layout (web/DESIGN.md, "Lightbox"): one "desk" at the top holds the dot being handled, in three
// steps that stay in one place: flag it, say what it is, see what everyone said. Under it, a quiet
// list of the dots flagged near this view.
import "./vote.css";
import { formatMjd, formatRaDec, sepArcsec } from "../app/astro";
import type { DotPick, MountVote, View } from "../app/modules";
import {
  ANSWERS,
  ANSWER_TEXT,
  VoteError,
  candidateFields,
  emptyCounts,
  fieldRadiusArcsec,
  nearby,
  safeStorage,
  toCsv,
  total,
  type Answer,
  type Candidate,
  type Counts,
  type KV,
} from "./model";
import { VOTED_EVENT, getVoteStore, toVoteError, voteApi, type VoteApi, type VoteStore, type VotedDetail } from "./store";

export const FORUM_URL = "https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9/talk";
/** A new flag this close to an existing candidate (arcsec, about one pixel) offers that one instead. */
const SAME_DOT_ARCSEC = 6.2;

const html = String.raw;
const cssEsc = (s: string) => (typeof globalThis.CSS?.escape === "function" ? CSS.escape(s) : s.replace(/["\\\]]/g, "\\$&"));
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** The grease-pencil mark for a flagged dot: a red circle with a short tick (DESIGN.md). */
const MARK = html`<svg class="vt-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
  <circle cx="14" cy="18" r="8.5" />
  <path d="M20.5 11.5 L26 6" />
</svg>`;

/** "Visit of 5 Sept 2026" (months) or "Exposure of 5 Sept 2026, 14:03 UTC" (hours). */
function frameWhen(c: { mjd: number; clock: Candidate["clock"] }): string {
  return c.clock === "hours" ? `exposure of ${formatMjd(c.mjd, true)}` : `visit of ${formatMjd(c.mjd)}`;
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Non-zero answers in words: "3 mover, 1 junk". */
function countsInWords(c: Counts): string {
  return ANSWERS.filter((a) => c[a])
    .map((a) => `${c[a]} ${ANSWER_TEXT[a].label.toLowerCase()}`)
    .join(", ");
}

/** The tally: one stacked bar, length per answer = its votes. Numbers always sit beside it. */
function tallyBar(c: Counts): string {
  const n = total(c);
  const label = n ? `${n} vote${n === 1 ? "" : "s"}: ${countsInWords(c)}` : "No votes yet";
  const segs = ANSWERS.filter((a) => c[a])
    .map((a) => html`<i class="a-${a}" style="flex-grow:${c[a]}"></i>`)
    .join("");
  return html`<span class="vt-bar${n ? "" : " is-empty"}" role="img" aria-label="${label}">${segs}</span>`;
}

export interface VoteOptions {
  /** Inject a store (tests). Default: the page's shared vote API (Firestore if configured, else local). */
  store?: VoteStore;
  kv?: KV;
}

export const mountVoteWith = (el: HTMLElement, opts: VoteOptions = {}) => {
  let api: VoteApi | null = null;
  const ready: Promise<VoteApi> = (opts.store ? Promise.resolve(voteApi(opts.store, opts.kv ?? safeStorage())) : getVoteStore()).then(
    (a) => {
      api = a;
      if (a.notice) say(a.notice);
      renderMode();
      return a;
    },
  );
  // This browser's memory, through the API once it is ready (nothing is listed before that).
  const memory = {
    votedFor: (id: string) => api?.myVote(id) ?? null,
    flaggedAt: (id: string) => api?.flaggedAt(id) ?? null,
  };

  let view: View | null = null;
  let pending: DotPick | null = null;
  let list: Candidate[] = [];
  let counts = new Map<string, Counts>();
  let open: string | null = null; // candidate whose ballot is on the desk
  let justVoted: string | null = null; // candidate whose result is on the desk
  let busy = false;
  let loadToken = 0;
  let loadTimer: ReturnType<typeof setTimeout> | null = null;
  let loading = false;

  el.innerHTML = html`
    <div class="vt">
      <header class="vt-head">
        <h2 class="vt-title">Flag what moved</h2>
        <p class="vt-lede">Click a dot on the plate to flag it. Anyone can then say what it is.</p>
        <p class="vt-mode" hidden></p>
      </header>
      <div class="vt-desk" id="vt-desk" hidden></div>
      <p class="vt-status" role="status" aria-live="polite"></p>
      <section class="vt-near" aria-labelledby="vt-near-h">
        <div class="vt-near-head">
          <h3 class="vt-near-title" id="vt-near-h">Near this view <span class="vt-n"></span></h3>
          <button class="btn btn-ghost vt-csv" type="button" data-act="csv" hidden>Download CSV</button>
        </div>
        <ol class="vt-list" aria-labelledby="vt-near-h"></ol>
      </section>
    </div>
  `;
  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const modeEl = $<HTMLParagraphElement>(".vt-mode");
  const deskEl = $<HTMLDivElement>(".vt-desk");
  const statusEl = $<HTMLParagraphElement>(".vt-status");
  const listEl = $<HTMLOListElement>(".vt-list");
  const nEl = $<HTMLSpanElement>(".vt-n");
  const csvBtn = $<HTMLButtonElement>(".vt-csv");

  /** Errors show; confirmations are spoken to screen readers but stay quiet on screen (the desk shows them). */
  function say(text: string, kind: "info" | "error" | "ok" = "info") {
    statusEl.dataset.kind = kind;
    statusEl.textContent = text;
  }

  function renderMode() {
    if (api?.mode === "local") {
      modeEl.hidden = false;
      modeEl.textContent = "Local mode: flags and votes stay in this browser, and nobody else sees them.";
    } else {
      modeEl.hidden = true;
    }
  }

  const find = (id: string | null) => (id ? (list.find((c) => c.id === id) ?? null) : null);

  // ---- the desk: flag, then vote, then the result -------------------------------------------
  function deskPick(p: DotPick): string {
    const twin = list.find((c) => sepArcsec(c, p) <= SAME_DOT_ARCSEC);
    return html`<div class="vt-step vt-pick">
      ${MARK}
      <div class="vt-step-body">
        <p class="vt-say">Flag the dot at <b class="vt-pos">${esc(formatRaDec(p))}</b> on the D${p.det}
          ${esc(frameWhen(p))}?</p>
        ${twin
          ? html`<p class="vt-twin">Someone already flagged a dot ${sepArcsec(twin, p).toFixed(1)}″ from here.
              <button class="vt-text" type="button" data-act="twin" data-id="${esc(twin.id)}">Vote on that one</button></p>`
          : ""}
        <div class="vt-acts">
          <button class="btn btn-primary" type="button" data-act="flag" ${busy ? "disabled" : ""}>${busy ? "Flagging…" : "Flag it"}</button>
          <button class="vt-text" type="button" data-act="cancel">Cancel</button>
        </div>
      </div>
    </div>`;
  }

  function deskBallot(c: Candidate): string {
    return html`<fieldset class="vt-step vt-ballot" ${busy ? "disabled" : ""}>
      <legend class="visually-hidden">What is the dot at ${esc(formatRaDec(c))}?</legend>
      ${MARK}
      <div class="vt-step-body">
        <p class="vt-say" aria-hidden="true">What is this dot?</p>
        <p class="vt-where"><span class="vt-pos">${esc(formatRaDec(c))}</span>, ${esc(frameWhen(c))}</p>
      </div>
      <div class="vt-answers">
        ${ANSWERS.map(
          (a) => html`<button class="btn vt-answer a-${a}" type="button" data-act="answer" data-answer="${a}" data-id="${esc(c.id)}">
            <span class="vt-answer-label"><i aria-hidden="true"></i>${ANSWER_TEXT[a].label}</span>
            <span class="vt-answer-means">${ANSWER_TEXT[a].meaning}</span>
          </button>`,
        ).join("")}
      </div>
      <div class="vt-acts">
        <button class="vt-text" type="button" data-act="close">${busy ? "Saving your vote…" : "Not now"}</button>
      </div>
    </fieldset>`;
  }

  function deskResult(c: Candidate): string {
    const n = counts.get(c.id) ?? emptyCounts();
    const mine = memory.votedFor(c.id);
    const t = total(n);
    return html`<div class="vt-step vt-result">
      ${MARK}
      <div class="vt-step-body">
        <p class="vt-say" tabindex="-1">${mine ? html`You said ${ANSWER_TEXT[mine].label.toLowerCase()}.` : "Votes so far."}</p>
        <p class="vt-where"><span class="vt-pos">${esc(formatRaDec(c))}</span>, ${t} vote${t === 1 ? "" : "s"} so far</p>
      </div>
      <div class="vt-tally">
        ${tallyBar(n)}
        <dl class="vt-legend">
          ${ANSWERS.map((a) => html`<div class="a-${a}"><dt><i aria-hidden="true"></i>${ANSWER_TEXT[a].label}</dt><dd>${n[a]}</dd></div>`).join("")}
        </dl>
      </div>
      <p class="vt-after">Want astronomers to look at it? Post its position on the
        <a href="${FORUM_URL}" target="_blank" rel="noopener">Backyard Worlds forum</a>.</p>
    </div>`;
  }

  function renderDesk() {
    let body = "";
    let mode = "";
    const ballotFor = find(open);
    const resultFor = find(justVoted);
    if (pending) {
      body = deskPick(pending);
      mode = "pick";
    } else if (ballotFor && !memory.votedFor(ballotFor.id)) {
      body = deskBallot(ballotFor);
      mode = "ballot";
    } else if (resultFor) {
      body = deskResult(resultFor);
      mode = "result";
    }
    deskEl.hidden = !body;
    deskEl.dataset.step = mode;
    deskEl.innerHTML = body;
  }

  // ---- the list ----------------------------------------------------------------------------
  function item(c: Candidate): string {
    const n = counts.get(c.id) ?? emptyCounts();
    const mine = memory.votedFor(c.id);
    const flaggedHere = memory.flaggedAt(c.id) != null;
    const onDesk = c.id === open || c.id === justVoted;
    const words = total(n) ? countsInWords(n) : "No votes yet";
    return html`<li class="vt-item" data-id="${esc(c.id)}" data-source="${c.source}" ${onDesk ? 'aria-current="true"' : ""}>
      <div class="vt-item-main">
        <p class="vt-item-pos">
          <span class="vt-pos">${esc(formatRaDec(c))}</span>
          ${c.source === "pipeline" ? html`<span class="vt-tag">Pipeline suspect</span>` : ""}
        </p>
        <p class="vt-item-when">${esc(cap(frameWhen(c)))}${flaggedHere ? html`<span class="vt-src">, flagged by you</span>` : ""}</p>
        <p class="vt-item-tally">${tallyBar(n)}<span class="vt-item-n">${words}${mine
          ? html`<span class="vt-mine">. You said <b>${ANSWER_TEXT[mine].label.toLowerCase()}</b></span>`
          : ""}</span></p>
      </div>
      <div class="vt-item-acts">
        ${mine ? "" : html`<button class="vt-text" type="button" data-act="vote" aria-controls="vt-desk">Vote</button>`}
        <button class="btn vt-show" type="button" data-act="show">Show</button>
      </div>
    </li>`;
  }

  function renderList() {
    nEl.textContent = loading && !list.length ? "" : `(${list.length})`;
    csvBtn.hidden = list.length === 0;
    if (!list.length) {
      listEl.innerHTML = loading
        ? html`<li class="vt-empty">Looking for flagged dots…</li>`
        : html`<li class="vt-empty">Nothing flagged near here yet. Blink the plate and flag the first dot that jumps.</li>`;
      return;
    }
    listEl.innerHTML = list.map(item).join("");
  }

  function render() {
    renderDesk();
    renderList();
  }

  /** Move keyboard focus onto the desk's first useful control. */
  function focusDesk(sel: string) {
    deskEl.querySelector<HTMLElement>(sel)?.focus();
  }

  async function load() {
    if (!view) return;
    const token = ++loadToken;
    const v = view;
    loading = true;
    renderList();
    try {
      const a = await ready;
      const found = await a.near(v, fieldRadiusArcsec(v));
      const sorted = nearby(found, v, (id) => a.flaggedAt(id));
      const c = await a.countsFor(sorted.map((x) => x.id));
      if (token !== loadToken) return;
      list = sorted;
      counts = c;
      if (statusEl.dataset.kind === "error" && !busy) say("");
    } catch (e) {
      if (token !== loadToken) return;
      say(toVoteError(e).message, "error");
    } finally {
      if (token === loadToken) {
        loading = false;
        render();
      }
    }
  }

  // ---- actions -----------------------------------------------------------------------------
  async function flag() {
    if (!pending || busy) return;
    const p = pending;
    busy = true;
    renderDesk();
    try {
      const a = await ready;
      const c = await a.flag(candidateFields(p));
      pending = null;
      open = c.id;
      justVoted = null;
      if (!list.some((x) => x.id === c.id)) list = [c, ...list];
      say("Flagged. Now say what you think it is.", "ok");
    } catch (e) {
      say(toVoteError(e).message, "error");
    } finally {
      busy = false;
      render();
      if (open) focusDesk(".vt-answer");
    }
  }

  async function vote(id: string, answer: Answer) {
    if (busy) return;
    const had = memory.votedFor(id);
    if (had) {
      say(`You already said ${ANSWER_TEXT[had].label.toLowerCase()} for this dot. Votes can't be changed.`, "error");
      return;
    }
    busy = true;
    renderDesk();
    let saved = false;
    try {
      const a = await ready;
      // castVote remembers the vote and fires skyshift:voted, which refreshes the counts (below).
      const c = { ...(counts.get(id) ?? emptyCounts()) };
      c[answer] += 1;
      counts.set(id, c);
      await a.castVote(id, answer).catch((e) => {
        c[answer] -= 1;
        throw e;
      });
      saved = true;
      open = null;
      justVoted = id;
      say(`Vote saved: ${ANSWER_TEXT[answer].label}.`, "ok");
    } catch (e) {
      const err = toVoteError(e);
      if (err.kind === "already") {
        open = null;
        justVoted = id;
      }
      say(err.message, "error");
    } finally {
      busy = false;
      render();
      if (saved) deskEl.querySelector<HTMLElement>(".vt-result .vt-say")?.focus({ preventScroll: true });
      else focusDesk(".vt-answer");
    }
  }

  function exportCsv() {
    try {
      const blob = new Blob([toCsv(list, counts)], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const where = view ? `${view.ra.toFixed(4)}_${view.dec.toFixed(4)}` : "view";
      a.href = url;
      a.download = `skyshift-candidates-${where}.csv`;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      say("Couldn't make the CSV file in this browser.", "error");
    }
  }

  function show(id: string) {
    const c = list.find((x) => x.id === id);
    if (!c) return;
    try {
      window.dispatchEvent(new CustomEvent("skyshift:goto", { detail: { ra: c.ra, dec: c.dec, clock: c.clock } }));
    } catch {
      /* old browser: nothing to do */
    }
  }

  el.addEventListener("click", (ev) => {
    const btn = (ev.target as Element).closest<HTMLElement>("[data-act]");
    if (!btn || !el.contains(btn)) return;
    const id = btn.dataset.id ?? btn.closest<HTMLElement>(".vt-item")?.dataset.id ?? "";
    try {
      switch (btn.dataset.act) {
        case "flag":
          void flag();
          break;
        case "cancel":
          pending = null;
          say("");
          render();
          break;
        case "twin":
        case "vote":
          pending = null;
          open = id;
          justVoted = null;
          say("");
          render();
          focusDesk(".vt-answer");
          break;
        case "close": {
          const was = open;
          open = null;
          render();
          if (was) listEl.querySelector<HTMLElement>(`.vt-item[data-id="${cssEsc(was)}"] [data-act=vote]`)?.focus();
          break;
        }
        case "answer":
          void vote(id, btn.dataset.answer as Answer);
          break;
        case "show":
          show(id);
          break;
        case "csv":
          exportCsv();
          break;
      }
    } catch (e) {
      say(e instanceof VoteError ? e.message : "Something went wrong in this panel. Reload the page if it keeps happening.", "error");
    }
  });

  // A vote cast here or on another page (Review, Candidate): fetch fresh counts for that dot, so
  // other people's votes show too. The optimistic count stays if the read fails.
  addEventListener(VOTED_EVENT, (ev) => {
    const id = (ev as CustomEvent<VotedDetail>).detail?.candidateId;
    if (!id || !list.some((c) => c.id === id)) return;
    ready
      .then((a) => a.countsFor([id]))
      .then((fresh) => {
        const f = fresh.get(id);
        if (f && total(f) >= total(counts.get(id) ?? emptyCounts())) {
          counts.set(id, f);
          render();
        }
      })
      .catch(() => {});
  });

  renderMode();
  render();

  return {
    pick(p: DotPick) {
      pending = p;
      open = null;
      justVoted = null;
      say("");
      try {
        candidateFields(p);
      } catch (e) {
        pending = null;
        say(toVoteError(e).message, "error");
      }
      render();
      focusDesk("[data-act=flag]");
    },
    view(v: View) {
      const moved = !view || view.ra !== v.ra || view.dec !== v.dec || view.fov !== v.fov;
      view = v;
      if (!moved) return;
      if (loadTimer) clearTimeout(loadTimer);
      loadTimer = setTimeout(() => void load(), 250);
    },
    /** For tests: wait for the store and the current list. */
    async settle() {
      if (loadTimer) {
        clearTimeout(loadTimer);
        loadTimer = null;
      }
      await load();
    },
  };
};

export const mountVote: MountVote = (el) => mountVoteWith(el);
