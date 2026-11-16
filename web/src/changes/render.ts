// HTML for the Changes list. Plain words first, the numbers right after; every value escaped.
import {
  DET_RANGE,
  falseAlarmRange,
  numbers,
  starKey,
  variableLabel,
  viewerHref,
  watchedStars,
  when,
  type Change,
  type ChangeCheck,
  type ChangesFile,
  type ListFilter,
} from './model';

const html = String.raw;

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]!);
}

const KIND_LABEL: Record<Change['kind'], string> = {
  brightness: 'Brightness', colour: 'Colour', appeared: 'Appeared', vanished: 'Vanished',
};
const LEVEL_LABEL: Record<ChangeCheck['level'], string> = { strong: 'Doubt', warn: 'Look', ok: 'Supports' };

export const FEED_URL = '/data/changes.atom';

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
}

export function headHtml(d: ChangesFile | null): string {
  const stars = d ? watchedStars(d) : 0;
  const zones = d?.watched.length ?? 0;
  const fa = d ? falseAlarmRange(d) : '';
  const watched = stars
    ? `SkyShift watches every star it measures (${plural(stars, 'star')} in ${plural(zones, 'zone')} so far)`
    : 'SkyShift watches every star it measures';
  return html`<header class="ch-head">
    <h1 class="ch-title">Changes in known stars</h1>
    <p class="ch-lede">${esc(watched)} and lists the ones whose brightness or colour changed between NASA SPHEREx
      visits about six months apart.${fa ? ` The same rules fire on ${esc(fa)} when nothing changed.` : ''}
      Stars Gaia already lists as variable are marked and come after the unexpected ones.</p>
    <p class="ch-feed"><a class="ch-feed-link" href="${FEED_URL}" type="application/atom+xml">Subscribe to the feed (Atom)</a>
      <span aria-hidden="true">·</span> Follow a star with ☆: your list stays in this browser, with no account.</p>
  </header>`;
}

export function barHtml(total: number, following: number, filter: ListFilter): string {
  return html`<div class="ch-bar">
    <div class="seg" role="group" aria-label="Which changes">
      <button type="button" data-filter="all" aria-pressed="${filter === 'all'}">All ${total}</button>
      <button type="button" data-filter="following" aria-pressed="${filter === 'following'}">Following (${following})</button>
    </div>
  </div>`;
}

function checkHtml(c: ChangeCheck): string {
  return html`<li class="ch-check is-${esc(c.level)}"><span class="ch-level">${esc(LEVEL_LABEL[c.level])}</span>
    <span>${esc(c.text)}${c.look ? html` <span class="ch-look-inline">Look: ${esc(c.look)}</span>` : ''}</span></li>`;
}

export function rowHtml(c: Change, followed: boolean): string {
  const v = variableLabel(c);
  const tags = [
    `<span class="ch-kind is-${esc(c.kind)}">${esc(KIND_LABEL[c.kind])}</span>`,
    v ? `<span class="ch-var">${esc(v)}</span>` : '',
    c.g_mag != null ? `<span>G ${esc(c.g_mag.toFixed(1))}</span>` : '',
    c.measured === 'forced' && c.kind !== 'appeared' && c.kind !== 'vanished'
      ? '<span>measured at its catalogue place</span>' : '',
    `<span>${esc(c.zones.join(', '))}</span>`,
  ].filter(Boolean).join('');
  const shown = c.checks.filter((k) => k.code !== 'known_variable');
  const nums = numbers(c);
  const det = `D${c.det}${DET_RANGE[c.det] ? ` (${DET_RANGE[c.det]})` : ''}`;
  return html`<li class="ch-row${c.gaia_variable ? ' is-variable' : ''}" id="ch-${esc(c.id)}" data-id="${esc(c.id)}">
    <div class="ch-main">
      <a class="ch-open" href="${esc(viewerHref(c))}" data-open="${esc(c.id)}">
        <span class="ch-name">${esc(c.name)}</span>
        <span class="ch-headline">${esc(c.headline || KIND_LABEL[c.kind])}</span>
      </a>
      <p class="ch-nums">${nums ? `${esc(nums)} · ` : ''}${esc(when(c))}</p>
      <p class="ch-tags">${tags}</p>
      <p class="ch-text">${esc(c.text)}</p>
      ${c.look ? html`<p class="ch-look"><b>Look:</b> ${esc(c.look)}</p>` : ''}
      ${shown.length ? html`<details class="ch-checks"><summary>What else was measured (${shown.length})</summary>
        <ul>${shown.map(checkHtml).join('')}</ul></details>` : ''}
      <a class="ch-viewer" href="${esc(viewerHref(c))}" data-open="${esc(c.id)}">Open in the Viewer: months clock, ${esc(det)} →</a>
    </div>
    <button type="button" class="btn btn-ghost btn-sm ch-follow" data-follow="${esc(starKey(c))}" aria-pressed="${followed}"
      aria-label="${followed ? 'Stop following' : 'Follow'} ${esc(c.name)}"><span aria-hidden="true">${followed ? '★' : '☆'}</span>
      ${followed ? 'Following' : 'Follow'}</button>
  </li>`;
}

export function emptyFileHtml(): string {
  return html`<div class="ch-empty" role="status">
    <p><b>No changes are published yet.</b></p>
    <p>SkyShift lists a known star here when its brightness or colour changes between SPHEREx visits. The list
      appears after the next pipeline run.</p>
  </div>`;
}

export function emptyListHtml(filter: ListFilter, elsewhere: number): string {
  if (filter === 'following') {
    return html`<div class="ch-empty" role="status"><p>${elsewhere
      ? esc(`You follow ${plural(elsewhere, 'star')}, with no change in this list.`)
      : 'You follow no star yet. Press ☆ Follow on a row: your list stays in this browser.'}</p></div>`;
  }
  return html`<div class="ch-empty" role="status"><p>No star changed beyond the false-alarm level in the zones measured so far.</p></div>`;
}

export function noteHtml(saved: boolean, elsewhere: number, filter: ListFilter, shown: number): string {
  const notes: string[] = [];
  if (!saved) notes.push("This browser won't keep your list (its storage is off): it lasts until you leave the page.");
  if (filter === 'following' && elsewhere && shown) notes.push(`You also follow ${plural(elsewhere, 'star')} with no change in this list.`);
  return notes.map((n) => `<p class="ch-note">${esc(n)}</p>`).join('');
}
