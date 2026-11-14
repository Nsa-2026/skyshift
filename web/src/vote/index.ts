// Flag a dot and vote on it (P4). The blink hands a clicked dot to pick(); "Flag it" creates a
// `candidates` doc. Everyone can vote mover / changing / junk / not sure on candidates near the
// current view. Firestore when the Firebase config is present, else a labelled local mode.
// Nothing here throws into the page: every failure becomes a plain-words status line.
import "./vote.css";
import { loadSuspects } from "../api/client";
import { formatMjd, formatRaDec, sepArcsec } from "../app/astro";
import type { DotPick, MountVote, View } from "../app/modules";
import {
  ANSWERS,
  ANSWER_TEXT,
  BrowserMemory,
  VoteError,
  candidateDoc,
  emptyCounts,
  fieldRadiusArcsec,
  nearby,
  safeStorage,
  toCandidate,
  toCsv,
  total,
  type Answer,
  type Candidate,
  type Counts,
  type KV,
} from "./model";
import { LocalStore, firebaseConfig, toVoteError, type VoteStore } from "./store";

export const FORUM_URL = "https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9/talk";
/** A new flag this close to an existing candidate (arcsec, about one pixel) offers that one instead. */
const SAME_DOT_ARCSEC = 6.2;

const html = String.raw;
const cssEsc = (s: string) => (typeof globalThis.CSS?.escape === "function" ? CSS.escape(s) : s.replace(/["\\\]]/g, "\\$&"));
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** Pipeline suspects from the static export, as read-only candidates (local mode only). */
async function staticSuspects(): Promise<Candidate[]> {
  const r = await loadSuspects();
  return (r?.suspects ?? [])
    .map((s) => toCandidate(`pipeline-${s.id}`, { ra: s.ra, dec: s.dec, mjd: s.mjd, clock: s.clock, source: "pipeline" }))
    .filter((c): c is Candidate => c !== null);
}

/** Firestore if configured and the SDK loads; LocalStore otherwise. */
async function openStore(kv: KV): Promise<{ store: VoteStore; note: string | null }> {
  const local = () => new LocalStore(kv, staticSuspects);
  const config = firebaseConfig();
  if (!config) return { store: local(), note: null };
  try {
    const { FirestoreStore } = await import("./firestore");
    return { store: new FirestoreStore(config), note: null };
  } catch {
    return { store: local(), note: "The vote server didn't load, so this panel fell back to local mode." };
  }
}

export interface VoteOptions {
  /** Inject a store (tests). Default: Firestore if configured, else local. */
  store?: VoteStore;
  kv?: KV;
}

export const mountVoteWith = (el: HTMLElement, opts: VoteOptions = {}) => {
  const kv = opts.kv ?? safeStorage();
  const memory = new BrowserMemory(kv);
  let store: VoteStore | null = opts.store ?? null;
  const ready: Promise<VoteStore> = store
    ? Promise.resolve(store)
    : openStore(kv).then(({ store: s, note }) => {
        store = s;
        if (note) say(note, "error");
        renderMode();
        return s;
      });

  let view: View | null = null;
  let pending: DotPick | null = null;
  let list: Candidate[] = [];
  let counts = new Map<string, Counts>();
  let open: string | null = null; // candidate whose ballot is open
  let justVoted: string | null = null;
  let busy = false;
  let loadToken = 0;
  let loadTimer: ReturnType<typeof setTimeout> | null = null;
  let loading = false;

  el.innerHTML = html`
    <div class="vt">
      <header class="vt-head">
        <h2 class="vt-title">Flag a dot</h2>
        <p class="vt-mode" hidden></p>
      </header>
      <div class="vt-pick" hidden></div>
      <p class="vt-hint">Click a dot in the blink to flag it. Anyone can then vote on what it is.</p>
      <p class="vt-status" role="status" aria-live="polite"></p>
      <div class="vt-list-head">
        <h3 class="vt-list-title">Near this view <span class="vt-n"></span></h3>
        <button class="btn vt-csv" type="button" data-act="csv" disabled>Download CSV</button>
      </div>
      <ol class="vt-list" aria-label="Candidates near this view"></ol>
    </div>
  `;
  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const modeEl = $<HTMLParagraphElement>(".vt-mode");
  const pickEl = $<HTMLDivElement>(".vt-pick");
  const hintEl = $<HTMLParagraphElement>(".vt-hint");
  const statusEl = $<HTMLParagraphElement>(".vt-status");
  const listEl = $<HTMLOListElement>(".vt-list");
  const nEl = $<HTMLSpanElement>(".vt-n");
  const csvBtn = $<HTMLButtonElement>(".vt-csv");

  function say(text: string, kind: "info" | "error" | "ok" = "info", link = false) {
    statusEl.dataset.kind = kind;
    statusEl.innerHTML = esc(text) + (link ? html` <a href="${FORUM_URL}" target="_blank" rel="noopener">Backyard Worlds forum</a>.` : "");
  }

  function renderMode() {
    if (store?.mode === "local") {
      modeEl.hidden = false;
      modeEl.textContent = "Local mode: flags and votes stay in this browser. Nobody else sees them.";
    } else {
      modeEl.hidden = true;
    }
  }

  // ---- the pick ----------------------------------------------------------------------------
  function renderPick() {
    hintEl.hidden = !!pending;
    if (!pending) {
      pickEl.hidden = true;
      pickEl.innerHTML = "";
      return;
    }
    const p = pending;
    const twin = list.find((c) => sepArcsec(c, p) <= SAME_DOT_ARCSEC);
    pickEl.hidden = false;
    pickEl.innerHTML = html`
      <h3 class="vt-pick-q">Flag this dot?</h3>
      <p class="vt-pos">${esc(formatRaDec(p))}</p>
      <p class="vt-meta">Frame of ${esc(formatMjd(p.mjd, true))}, ${p.clock} clock, detector D${p.det}</p>
      ${twin
        ? html`<p class="vt-twin">Someone already flagged a dot ${sepArcsec(twin, p).toFixed(1)}″ away.
            <button class="vt-link" type="button" data-act="twin" data-id="${esc(twin.id)}">Vote on that one</button></p>`
        : ""}
      <div class="vt-pick-actions">
        <button class="btn vt-primary" type="button" data-act="flag" ${busy ? "disabled" : ""}>${busy ? "Flagging…" : "Flag it"}</button>
        <button class="btn" type="button" data-act="cancel">Cancel</button>
      </div>
    `;
  }

  // ---- the list ----------------------------------------------------------------------------
  function strip(c: Counts): string {
    const n = total(c);
    const label = n
      ? `${n} vote${n === 1 ? "" : "s"}: ${ANSWERS.map((a) => `${c[a]} ${ANSWER_TEXT[a].label.toLowerCase()}`).join(", ")}`
      : "No votes yet";
    const segs = ANSWERS.filter((a) => c[a]).map((a) => html`<i class="a-${a}" style="flex-grow:${c[a]}"></i>`).join("");
    return html`<div class="vt-strip${n ? "" : " is-empty"}" role="img" aria-label="${label}">${segs}</div>`;
  }

  function sourceLabel(c: Candidate): string {
    if (c.source === "pipeline") return "Pipeline suspect";
    return memory.flaggedAt(c.id) != null ? "Flagged by you" : "Flagged by a visitor";
  }

  function item(c: Candidate): string {
    const n = counts.get(c.id) ?? emptyCounts();
    const mine = memory.votedFor(c.id);
    const isOpen = open === c.id && !mine;
    return html`<li class="vt-item" data-id="${esc(c.id)}" data-source="${c.source}">
      <div class="vt-row">
        <span class="vt-pos">${esc(formatRaDec(c))}</span>
        <span class="vt-src">${sourceLabel(c)}</span>
      </div>
      <p class="vt-meta">${esc(formatMjd(c.mjd, true))}, ${c.clock} clock</p>
      ${strip(n)}
      ${total(n)
        ? html`<dl class="vt-counts">
            ${ANSWERS.map((a) => html`<div class="a-${a}"><dt>${ANSWER_TEXT[a].label}</dt><dd>${n[a]}</dd></div>`).join("")}
          </dl>`
        : html`<p class="vt-none">No votes yet.</p>`}
      <div class="vt-actions">
        <button class="btn" type="button" data-act="show">Show</button>
        ${mine
          ? html`<span class="vt-mine">You said <b>${ANSWER_TEXT[mine].label}</b></span>`
          : html`<button class="btn" type="button" data-act="vote" aria-expanded="${isOpen}">Vote</button>`}
      </div>
      ${isOpen ? ballot(c) : ""}
      ${justVoted === c.id
        ? html`<p class="vt-after">Want astronomers to look at it? Post the position on the
            <a href="${FORUM_URL}" target="_blank" rel="noopener">Backyard Worlds forum</a>.</p>`
        : ""}
    </li>`;
  }

  function ballot(c: Candidate): string {
    return html`<fieldset class="vt-ballot" ${busy ? "disabled" : ""}>
      <legend>What is this dot?</legend>
      ${ANSWERS.map(
        (a) => html`<button class="vt-answer a-${a}" type="button" data-act="answer" data-answer="${a}" data-id="${esc(c.id)}">
          <b>${ANSWER_TEXT[a].label}</b><span>${ANSWER_TEXT[a].meaning}</span>
        </button>`,
      ).join("")}
    </fieldset>`;
  }

  function renderList() {
    nEl.textContent = loading && !list.length ? "" : `(${list.length})`;
    csvBtn.disabled = list.length === 0;
    if (!list.length) {
      listEl.innerHTML = loading
        ? html`<li class="vt-empty">Looking for flagged dots…</li>`
        : html`<li class="vt-empty">Nothing flagged in this field yet. Blink it, and flag anything that jumps.</li>`;
      return;
    }
    listEl.innerHTML = list.map(item).join("");
  }

  function render() {
    renderPick();
    renderList();
  }

  async function load() {
    if (!view) return;
    const token = ++loadToken;
    const v = view;
    loading = true;
    renderList();
    try {
      const s = await ready;
      const found = await s.near(v, fieldRadiusArcsec(v));
      const sorted = nearby(found, v, (id) => memory.flaggedAt(id));
      const c = sorted.length ? await s.counts(sorted.map((x) => x.id)) : new Map<string, Counts>();
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
    renderPick();
    try {
      const s = await ready;
      const c = await s.flag(candidateDoc(p));
      memory.rememberFlag(c.id);
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
      if (open) listEl.querySelector<HTMLButtonElement>(`.vt-item[data-id="${cssEsc(open)}"] .vt-answer`)?.focus();
    }
  }

  async function vote(id: string, answer: Answer) {
    if (busy) return;
    const had = memory.votedFor(id);
    if (had) {
      say(`You already voted ${ANSWER_TEXT[had].label} on this dot. Votes can't be changed.`, "error");
      return;
    }
    busy = true;
    renderList();
    try {
      const s = await ready;
      await s.vote(id, answer, memory.voter);
      memory.rememberVote(id, answer);
      const c = { ...(counts.get(id) ?? emptyCounts()) };
      c[answer] += 1;
      counts.set(id, c);
      open = null;
      justVoted = id;
      say(`Vote saved: ${ANSWER_TEXT[answer].label}.`, "ok");
      // Refresh from the store so other people's votes show too; the optimistic count stays if it fails.
      s.counts([id])
        .then((fresh) => {
          const f = fresh.get(id);
          if (f && total(f) >= total(c)) {
            counts.set(id, f);
            renderList();
          }
        })
        .catch(() => {});
    } catch (e) {
      const err = toVoteError(e);
      if (err.kind === "already") memory.rememberVote(id, answer);
      say(err.message, "error");
    } finally {
      busy = false;
      renderList();
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
          renderPick();
          break;
        case "twin":
          pending = null;
          open = id;
          render();
          listEl.querySelector<HTMLButtonElement>(`.vt-item[data-id="${cssEsc(id)}"] .vt-answer`)?.focus();
          break;
        case "vote":
          open = open === id ? null : id;
          justVoted = null;
          renderList();
          listEl.querySelector<HTMLButtonElement>(`.vt-item[data-id="${cssEsc(id)}"] [data-act="${open ? "answer" : "vote"}"]`)?.focus();
          break;
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

  renderMode();
  render();

  return {
    pick(p: DotPick) {
      pending = p;
      say("");
      try {
        candidateDoc(p);
      } catch (e) {
        pending = null;
        say(toVoteError(e).message, "error");
      }
      renderPick();
      pickEl.querySelector<HTMLButtonElement>("[data-act=flag]")?.focus();
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
