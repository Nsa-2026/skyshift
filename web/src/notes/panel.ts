// The notes panel on an object to check (#/c/<id>). It sits in the Viewer's left column under
// "Sky around this spot", where the plate and the sky card used to leave the column short against
// the evidence beside it.
//
// Two kinds of row, never mixed up: what you wrote on this object (kept in this browser, see
// posts.ts) and example comments (made up, see sample.ts, every one tagged "Sample"). The fine
// print under the list says both in plain words.
import { FORUM_URL } from "../vote";
import { safeStorage, type KV } from "../vote/model";
import { type Shown, feedFor } from "./feed";
import {
  BODY_MAX,
  NICKNAME_KEY,
  PROBLEM_TEXT,
  addPost,
  checkBody,
  newPost,
  readPosts,
  removePost,
  timeAgo,
} from "./posts";

const html = String.raw;
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export interface NotesPanelOptions {
  kv?: KV;
  /** Now, in epoch ms. Injected so a test can pin "3 hours ago". */
  now?: () => number;
}

export interface NotesPanelHandle {
  /** Show the panel for one object to check. */
  show(about: string): void;
  /** Hide it: the Viewer is not on an object. */
  hide(): void;
  destroy(): void;
}

/** A row with its time already worded, so the renderer calls timeAgo once per render. */
type Row = Shown & { when: string };

/** One row. Sample rows carry the tag and no Remove button: they are not this browser's to drop. */
function rowHtml(r: Row): string {
  return html`<li class="nt-note${r.sample ? " nt-sample" : ""}">
    <p class="nt-note-head">
      <span class="nt-who">${esc(r.who)}</span>
      <span class="nt-when">${esc(r.when)}</span>
      ${r.sample ? html`<span class="nt-tag">Sample</span>` : ""}
      ${
        r.sample
          ? ""
          : html`<button class="nt-del" type="button" data-act="note-remove" data-id="${esc(r.key)}"
              title="Remove this note">Remove</button>`
      }
    </p>
    <p class="nt-body-text">${esc(r.body)}</p>
  </li>`;
}

/** Mount the panel. It renders nothing until show() names an object. */
export function mountNotesPanel(el: HTMLElement, opts: NotesPanelOptions = {}): NotesPanelHandle {
  const kv = opts.kv ?? safeStorage();
  const now = opts.now ?? (() => Date.now());
  let about: string | null = null;

  el.innerHTML = html`
    <div class="nt-head">
      <h2 class="nt-h">Notes</h2>
      <p class="nt-count"></p>
    </div>
    <p class="nt-sub">Something you noticed that a vote can't say: a shape, a neighbour, a reason to look again.</p>
    <form class="nt-form" autocomplete="off">
      <label class="visually-hidden" for="nt-body">Your note on this object</label>
      <textarea id="nt-body" class="input nt-body" name="body" rows="3" maxlength="${BODY_MAX}"
        placeholder="e.g. Both frames look clean to me, but the dot sits right on a diffraction spike."></textarea>
      <div class="nt-foot">
        <p class="nt-problem" role="alert"></p>
        <button class="btn btn-primary" type="submit">Post a note</button>
      </div>
    </form>
    <ol class="nt-list"></ol>
    <p class="nt-fine">Your notes stay in this browser: there is no notes server yet, so nobody else can see them
      and nothing is sent with your vote. The ones marked <b>Sample</b> are made up, to show what a discussion here
      would look like — no one wrote them. Real discussion about these objects happens on the
      <a href="${FORUM_URL}" target="_blank" rel="noopener">Backyard Worlds forum</a>.</p>
  `;

  const $ = <T extends Element>(sel: string) => el.querySelector(sel) as T;
  const form = $<HTMLFormElement>(".nt-form");
  const body = $<HTMLTextAreaElement>(".nt-body");
  const list = $<HTMLOListElement>(".nt-list");
  const problem = $<HTMLParagraphElement>(".nt-problem");
  const count = $<HTMLParagraphElement>(".nt-count");

  const nickname = (): string => (kv.get(NICKNAME_KEY) ?? "").trim().slice(0, 40);

  function render() {
    if (!about) return;
    const at = now();
    const rows = feedFor(about, readPosts(kv), at).map((r) => ({ ...r, when: timeAgo(r.ts, at) }));
    const mine = rows.filter((r) => !r.sample).length;
    count.textContent = mine ? `${mine} ${mine === 1 ? "note" : "notes"} of yours` : "";
    list.innerHTML = rows.map(rowHtml).join("");
  }

  function onSubmit(ev: Event) {
    ev.preventDefault();
    if (!about) return;
    const text = body.value;
    const bad = checkBody(text);
    problem.textContent = bad ? PROBLEM_TEXT[bad] : "";
    if (bad) {
      body.focus();
      return;
    }
    addPost(kv, newPost({ about, body: text, name: nickname() }, now()));
    body.value = "";
    render();
  }

  function onClick(ev: Event) {
    const t = ev.target;
    if (!(t instanceof Element)) return;
    const btn = t.closest<HTMLElement>("[data-act=note-remove]");
    if (!btn || !el.contains(btn)) return;
    removePost(kv, btn.dataset.id ?? "");
    render();
  }

  form.addEventListener("submit", onSubmit);
  el.addEventListener("click", onClick);

  return {
    show(id) {
      const changed = id !== about;
      about = id;
      el.hidden = false;
      if (changed) {
        body.value = "";
        problem.textContent = "";
      }
      render();
    },
    hide() {
      el.hidden = true;
      about = null;
    },
    destroy() {
      form.removeEventListener("submit", onSubmit);
      el.removeEventListener("click", onClick);
      el.innerHTML = "";
    },
  };
}
