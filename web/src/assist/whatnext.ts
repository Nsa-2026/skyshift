// "What next" (web/DESIGN.md, "The check assistant"): what to do with a checked candidate.
// Movers get a draft MPC report (ADES PSV), moving stars a Backyard Worlds post draft, everything a
// citation and a permalink. Copy and download only: nothing is ever sent from here.
import "./assist.css";
import { gradeOf } from "../api/grades";
import { ANSWER_TEXT, consensus, total, type Counts, type KV } from "../vote/model";
import { esc, glyph } from "./checklist";
import { copyText, downloadText, safeFileName } from "./clipboard";
import { BYW_FORUM_URL, MPC_ADES_URL, citation, forumDraft, mpcDraft, permalink, type ReportOptions } from "./reports";
import { REPLY_EVENT, loadReplies } from "./replies";
import type { AssistCandidate } from "./types";

export interface WhatNextOptions {
  /** Site address for links. Default: this page, else the public site. */
  base?: string;
  /** Collections searched, e.g. "DR1+QR3". */
  dataRelease?: string;
  /** Storage for the checklist replies the drafts quote (tests). */
  kv?: KV;
  /** Heading text; "" leaves it out. */
  title?: string;
  /** Today, for "accessed" dates (tests). */
  today?: Date;
}

export interface WhatNextHandle {
  /**
   * update(candidate, votes?): another candidate, or the same one with new counts (votes left out
   * keeps the counts for the same candidate). update(votes): new counts for the current candidate
   * (src/review passes its VotesSummary, which carries the four counts).
   */
  update(c: AssistCandidate | Counts | null, votes?: Counts | null): void;
  destroy(): void;
}

/** A candidate record, as opposed to vote counts. */
const isCandidate = (x: unknown): x is AssistCandidate =>
  !!x && typeof x === "object" && typeof (x as { id?: unknown }).id === "string" && typeof (x as { kind?: unknown }).kind === "string";

const html = String.raw;
type Block = "mpc" | "forum" | "cite";
let wnUid = 0;

/** Vote counts from the argument, else from a `votes` object on the candidate (exporter), else null. */
function countsOf(c: AssistCandidate, votes: Counts | null | undefined): Counts | null {
  if (votes) return votes;
  const v = (c as unknown as { votes?: unknown }).votes;
  if (v && typeof v === "object" && ["mover", "changing", "junk", "unsure"].every((k) => typeof (v as Record<string, unknown>)[k] === "number"))
    return v as Counts;
  return null;
}

/** The opening sentence, from the votes so far. */
export function leadSentence(c: AssistCandidate, votes: Counts | null): string {
  const report = c.kind === "mover" ? "report it to the Minor Planet Center" : c.kind === "high_pm" ? "tell the Backyard Worlds forum" : "share it";
  // Grade C is never put to a vote: no consensus will come, so say so instead of asking for votes.
  if (gradeOf(c) === "C")
    return "Not put to a vote: the pipeline graded it C because it fails a check. There's nothing to report unless you confirm the motion yourself; the citation and link are below.";
  if (!votes) return `When the community agrees on it, ${report}. Until then the drafts are here to check.`;
  const n = total(votes);
  const con = consensus(votes);
  if (!con)
    return n
      ? `${n} vote${n === 1 ? "" : "s"} so far: a consensus needs 5 votes with 70% on one answer. Vote, then come back.`
      : "No votes yet: a consensus needs 5 votes with 70% on one answer. Check it, vote, then come back.";
  const pct = `${(con.share * 100).toFixed(0)}% of ${con.votes} votes`;
  if (con.answer === "junk") return `Most voters call this junk (${pct}), so there's nothing to report. The citation and link are below.`;
  if (con.answer === "unsure") return `Voters can't tell (${pct} say not sure). Check it again, or share it with someone who can.`;
  if (con.answer === "changing")
    return `Voters see its brightness change, not motion (${pct}), so there's no motion to report. The citation and link are below.`;
  return `The community agrees: ${ANSWER_TEXT[con.answer].label.toLowerCase()} (${pct}). Now ${report}.`;
}

/** Mount "What next" for one candidate. votes: its counts so far, or null when not loaded. */
export function mountWhatNext(el: HTMLElement, c: AssistCandidate, votes: Counts | null = null, opts: WhatNextOptions = {}): WhatNextHandle {
  let cand = c;
  let counts = countsOf(c, votes);
  const open = new Set<Block>();
  let cite: "plain" | "bibtex" = "plain";
  const uidOf = `wn${++wnUid}`;
  const root = document.createElement("section");
  root.className = "wn";
  el.append(root);

  const reportOpts = (): ReportOptions => ({
    base: opts.base,
    dataRelease: opts.dataRelease,
    votes: counts,
    replies: loadReplies(cand.id, opts.kv),
    today: opts.today,
  });
  const texts = () => {
    const ro = reportOpts();
    const ci = citation(cand, ro);
    return {
      mpc: cand.kind === "mover" ? mpcDraft(cand, ro) : null,
      forum: cand.kind === "high_pm" ? forumDraft(cand, ro) : null,
      cite: cite === "plain" ? ci.plain : ci.bibtex,
      link: permalink(cand, ro.base),
    };
  };

  /** Drafts open by themselves once the community calls the candidate a mover. */
  function defaultOpen(block: Block): boolean {
    const con = counts ? consensus(counts) : null;
    return block === "cite" || con?.answer === "mover";
  }

  function draftBlock(key: "mpc" | "forum", title: string, intro: string, body: string, extra: string, label: string): string {
    const isOpen = open.has(key) || defaultOpen(key);
    return html`<div class="wn-block" data-block="${key}">
      <h3 class="wn-h">${esc(title)}</h3>
      <p class="wn-p">${intro}</p>
      ${defaultOpen(key)
        ? ""
        : html`<button type="button" class="as-how" data-open="${key}" aria-expanded="${isOpen}" aria-controls="${key}-${uidOf}">
            <svg class="as-caret" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="M4 2.5 7.5 6 4 9.5"/></svg>Show the draft
          </button>`}
      <div class="wn-draft" id="${key}-${uidOf}"${isOpen ? "" : " hidden"}>
        <p class="wn-flag">${glyph("caution")}<strong>Draft, not submitted. Check it before sending.</strong></p>
        ${extra}
        <pre class="wn-pre panel${key === "mpc" ? " is-psv" : ""}" tabindex="0" aria-label="${esc(label)}">${esc(body)}</pre>
        <p class="wn-actions">
          <button type="button" class="btn" data-copy="${key}">Copy</button>
          <button type="button" class="btn btn-ghost" data-dl="${key}">Download .txt</button>
          <span class="wn-said" role="status" data-said="${key}"></span>
        </p>
      </div>
    </div>`;
  }


  function render(focusSel?: string) {
    const t = texts();
    const title = opts.title ?? "What next";
    const blocks: string[] = [];
    const con = counts ? consensus(counts) : null;
    const gradeC = gradeOf(cand) === "C";
    const holdBack = !!con && con.answer !== "mover";
    if (t.mpc) {
      const missing = html`<div class="wn-check">
          <p>Before you send it:</p>
          <ul>${t.mpc.missing.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
        </div>`;
      blocks.push(
        draftBlock(
          "mpc",
          "Report it to the Minor Planet Center",
          html`${gradeC ? "Grade C: it fails a pipeline check, so don't send it unless you've confirmed the motion yourself. " : holdBack ? "Voters don't call this a mover, so don't send it. " : ""}A new asteroid or comet needs its positions at the MPC. Here is a report in ADES PSV, the MPC's format, for SPHEREx (observatory code 288): ${t.mpc.lines} line${t.mpc.lines === 1 ? "" : "s"}${t.mpc.fromMean ? ", from the mean position" : ""}. SkyShift never sends it: you check it and submit it through <a href="${MPC_ADES_URL}" target="_blank" rel="noopener noreferrer">the MPC's ADES page</a>.`,
          t.mpc.text,
          missing,
          "Draft MPC report, ADES pipe-separated values",
        ),
      );
    }
    if (t.forum) {
      blocks.push(
        draftBlock(
          "forum",
          "Tell Backyard Worlds",
          html`Volunteers and astronomers compare moving stars and brown dwarf candidates on the <a href="${BYW_FORUM_URL}" target="_blank" rel="noopener noreferrer">Backyard Worlds: Planet 9 science board</a>. Here is a post to start from: fill in what you saw, then paste it there.`,
          t.forum,
          "",
          "Draft forum post",
        ),
      );
    }
    blocks.push(html`<div class="wn-block" data-block="cite">
      <h3 class="wn-h">Cite it</h3>
      <p class="wn-p">For a paper, a talk or a class: SkyShift, this candidate's link and the SPHEREx data sets with their DOIs.</p>
      <div class="seg wn-seg" role="group" aria-label="Citation format">
        <button type="button" data-cite="plain" aria-pressed="${cite === "plain"}">Plain text</button>
        <button type="button" data-cite="bibtex" aria-pressed="${cite === "bibtex"}">BibTeX</button>
      </div>
      <pre class="wn-pre panel" tabindex="0" aria-label="${cite === "plain" ? "Citation, plain text" : "Citation, BibTeX"}">${esc(t.cite)}</pre>
      <p class="wn-actions">
        <button type="button" class="btn" data-copy="cite">Copy</button>
        <button type="button" class="btn btn-ghost" data-dl="cite">Download ${cite === "plain" ? ".txt" : ".bib"}</button>
        <span class="wn-said" role="status" data-said="cite"></span>
      </p>
    </div>`);
    blocks.push(html`<div class="wn-block" data-block="link">
      <h3 class="wn-h">Share it</h3>
      <p class="wn-p" id="${uidOf}-link">This link opens this candidate's page for anyone.</p>
      <div class="wn-link">
        <input class="input wn-url" type="text" readonly value="${esc(t.link)}" aria-labelledby="${uidOf}-link" />
        <button type="button" class="btn" data-copy="link">Copy link</button>
      </div>
      <p class="wn-actions"><span class="wn-said" role="status" data-said="link"></span></p>
    </div>`);
    root.innerHTML = html`<div class="as-head">
        ${title ? html`<h2 class="wn-title">${esc(title)}</h2>` : ""}
        <p class="wn-lede">${esc(leadSentence(cand, counts))}</p>
      </div>
      ${blocks.join("")}`;
    if (focusSel) root.querySelector<HTMLElement>(focusSel)?.focus();
  }

  const said = (key: string, msg: string) => {
    const s = root.querySelector(`[data-said="${key}"]`);
    if (s) s.textContent = msg;
  };
  const NAMES: Record<string, string> = { mpc: "the MPC draft", forum: "the forum post", cite: "the citation", link: "the link" };

  root.addEventListener("click", async (ev) => {
    const b = (ev.target as Element).closest("button");
    if (!b || !root.contains(b)) return;
    const t = texts();
    const get = (k: string) => (k === "mpc" ? t.mpc?.text : k === "forum" ? t.forum : k === "cite" ? t.cite : t.link) ?? "";
    if (b.dataset.open) {
      const k = b.dataset.open as Block;
      const now = !open.has(k);
      if (now) open.add(k);
      else open.delete(k);
      b.setAttribute("aria-expanded", String(now));
      const box = root.querySelector<HTMLElement>(`[id="${b.getAttribute("aria-controls")}"]`);
      if (box) box.hidden = !now;
    } else if (b.dataset.cite) {
      cite = b.dataset.cite as "plain" | "bibtex";
      render(`[data-cite="${cite}"]`);
    } else if (b.dataset.copy) {
      const k = b.dataset.copy;
      const ok = await copyText(get(k));
      said(k, ok ? `Copied ${NAMES[k]}.` : "Couldn't copy. Select the text and copy it yourself.");
    } else if (b.dataset.dl) {
      const k = b.dataset.dl;
      const name = safeFileName(`skyshift-${cand.id}`);
      const file =
        k === "mpc" ? `${name}-mpc-draft.txt` : k === "forum" ? `${name}-backyard-worlds.txt` : `${name}-citation.${cite === "bibtex" ? "bib" : "txt"}`;
      const ok = downloadText(file, get(k), k === "cite" && cite === "bibtex" ? "application/x-bibtex;charset=utf-8" : undefined);
      said(k, ok ? `Saved ${file}.` : "Couldn't save the file. Copy the text instead.");
    }
  });

  const onReply = (ev: Event) => {
    if ((ev as CustomEvent<{ candidateId: string }>).detail?.candidateId === cand.id) render();
  };
  window.addEventListener(REPLY_EVENT, onReply);

  render();
  return {
    update(next, v) {
      if (!isCandidate(next)) {
        counts = countsOf(cand, next);
        render();
        return;
      }
      const same = next.id === cand.id;
      if (!same) open.clear();
      cand = next;
      counts = countsOf(next, v === undefined ? (same ? counts : null) : v);
      render();
    },
    destroy() {
      window.removeEventListener(REPLY_EVENT, onReply);
      root.remove();
    },
  };
}
