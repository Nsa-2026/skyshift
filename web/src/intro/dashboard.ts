import { TARGETS } from '../app/state';
import { candidateSet } from '../review/data';
import { sceneAt } from './model';

const esc = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const kinds = { mover: 'Fast mover', high_pm: 'Moving star', spectral: 'Odd spectrum' };

/** Rehomes the existing search and player. There is no duplicate or simulated dashboard. */
export function mountEntryDashboard(root: HTMLElement, opts: { target(slug: string): void; replay(): void }) {
  const page = root.querySelector<HTMLElement>('[data-page="explore"]')!;
  const header = root.querySelector<HTMLElement>('.top')!;
  const message = root.querySelector<HTMLElement>('.find-msg')!;
  const featured = root.querySelector<HTMLElement>('.featured')!;
  const stage = page.querySelector<HTMLElement>('.stage')!;
  const shell = document.createElement('div');
  shell.className = 'entry-dashboard';
  shell.innerHTML = `<div class="entry-header" data-reveal="0"></div>
    <div class="entry-content">
      <div class="entry-heading" data-reveal="0"><div><p class="entry-eyebrow">A different way to look</p><h1>Every sky has a story.</h1><p>Compare real SPHEREx observations. Find what moves.</p></div><button type="button" class="entry-replay">↗ 重看开场</button></div>
      <section class="entry-targets" aria-label="Five featured targets" data-reveal="1">${TARGETS.slice(0, 5).map((t, i) => `<button type="button" class="entry-target" data-target="${t.slug}" aria-pressed="false"><span class="entry-target-kind"><span class="entry-target-number">0${i + 1}</span> ${esc(t.kind)}</span><strong>${esc(t.short)} <span aria-hidden="true">↗</span></strong></button>`).join('')}</section>
      <div class="entry-workspace-grid"><div class="entry-player" data-reveal="2"><div class="entry-panel-title"><span><i></i> The observation desk</span><span>Real data · SPHEREx</span></div></div>
        <aside class="entry-discoveries" aria-labelledby="entry-candidates-title" data-reveal="3"><div class="entry-discovery-heading"><span class="entry-eyebrow">Look a little closer</span><h2 id="entry-candidates-title">Signals to explore <span aria-hidden="true">↗</span></h2><p>Candidate signals awaiting review.</p></div><div class="entry-candidate-content" aria-live="polite"><p class="entry-empty">Loading candidate signals…</p></div><a class="entry-review-all" href="#/candidates">View all candidates <span aria-hidden="true">→</span></a></aside>
      </div>
      <div class="entry-footer" data-reveal="3"><span>NASA / JPL-Caltech / IPAC · ESA Gaia</span><span>Observe. Compare. Discover.</span></div>
    </div>`;
  page.prepend(shell);
  shell.querySelector('.entry-player')!.append(stage);
  shell.querySelector<HTMLButtonElement>('.entry-replay')!.onclick = opts.replay;
  shell.querySelectorAll<HTMLButtonElement>('[data-target]').forEach(button => {
    button.onclick = () => opts.target(button.dataset.target!);
  });
  let loaded = false, pending = false, destroyed = false;
  async function candidates() {
    if (loaded || pending) return;
    pending = true;
    const content = shell.querySelector<HTMLElement>('.entry-candidate-content')!;
    try {
      const set = await candidateSet();
      if (destroyed) return;
      if (!set) throw new Error('No candidate export');
      loaded = true;
      content.innerHTML = `<p class="entry-candidate-count">${set.sample ? 'Pilot sample · ' : ''}${set.candidates.length.toLocaleString()} candidate signals · not confirmed discoveries</p><ol>${set.candidates.slice(0, 4).map(c => `<li><a href="#/c/${encodeURIComponent(c.id)}"><span class="entry-signal-icon" aria-hidden="true">${c.kind === 'spectral' ? '◇' : '⊙'}</span><div><span class="entry-signal-kind">${kinds[c.kind]}</span><strong>${esc(c.id)}</strong><p>${c.ra.toFixed(3)}°, ${c.dec.toFixed(3)}°</p></div><span aria-hidden="true">↗</span></a></li>`).join('')}</ol>${set.candidates.length ? '' : '<p class="entry-empty">No candidates have been published yet. Start with a featured target.</p>'}`;
    } catch {
      if (!destroyed) {
        content.innerHTML = '<p class="entry-empty">The candidate list is unavailable. The featured observations are still ready to explore.</p><button class="entry-retry" type="button">Try again</button>';
        content.querySelector('button')!.onclick = () => void candidates();
      }
    } finally { pending = false; }
  }
  return {
    active(value: boolean) {
      root.classList.toggle('entry-workspace', value);
      if (value) {
        shell.querySelector('.entry-header')!.append(header, message);
        void candidates();
      } else {
        root.insertBefore(header, featured);
        root.insertBefore(message, featured.nextSibling);
      }
    },
    progress(p: number, preview: boolean) {
      shell.inert = preview;
      shell.setAttribute('aria-hidden', String(preview));
      shell.querySelectorAll<HTMLElement>('[data-reveal]').forEach(el => {
        const alpha = preview ? sceneAt(p).panels[Number(el.dataset.reveal)] : 1;
        el.style.opacity = String(alpha);
        el.style.transform = `translateY(${(1 - alpha) * 20}px)`;
      });
    },
    select(slug: string | null) {
      shell.querySelectorAll<HTMLButtonElement>('[data-target]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.target === slug)));
    },
    focus() { header.querySelector<HTMLInputElement>('input')?.focus({ preventScroll: true }); },
    destroy() { destroyed = true; },
  };
}
