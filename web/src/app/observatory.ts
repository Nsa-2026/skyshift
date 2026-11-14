import type { View } from './modules';
import type { ObjectResponse } from '../api/types';
import { DETECTORS, formatRaDec } from './astro';
import { TARGETS } from './state';
const escape = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
/** Rehomes the existing, live components. There is still one player and one object card. */
export function mountObservatory(app: HTMLElement, player: HTMLElement, opts: {
    target(slug: string): void;
    analysis(visible: boolean): void;
    workspace?(): void;
}) {
    const scene = player.querySelector<HTMLElement>('.bk')!;
    const $ = <T extends HTMLElement = HTMLElement>(s: string) => scene.querySelector<T>(s)!;
    const header = app.querySelector<HTMLElement>('.top')!;
    const featured = app.querySelector<HTMLElement>('.featured')!;
    const message = app.querySelector<HTMLElement>('.find-msg')!;
    const overview = document.createElement('section');
    overview.className = 'obs-overview';
    overview.setAttribute('aria-label', 'Selected sky');
    overview.innerHTML = `<div class="obs-overview-head"><h1 class="obs-name">Selected sky</h1><button class="obs-menu" type="button" aria-label="Open workspace navigation" aria-expanded="false">☰</button></div><p class="obs-coordinates"></p><p class="obs-band"></p><p class="obs-description">Compare observations of the same sky across time.</p>`;
    scene.append(overview);
    const nav = document.createElement('section');
    nav.className = 'obs-navigation';
    nav.hidden = true;
    nav.setAttribute('aria-label', 'Workspace navigation');
    nav.innerHTML = '<div class="obs-panel-head"><strong>Workspace</strong><button class="obs-nav-close" type="button" aria-label="Close workspace navigation">×</button></div><div class="obs-navigation-content"></div>';
    scene.append(nav);
    const aside = document.createElement('aside');
    aside.className = 'obs-details';
    aside.id = 'obs-details';
    aside.setAttribute('aria-label', 'Details');
    aside.innerHTML = `<header class="obs-panel-head"><h2>Details</h2><button class="obs-close" type="button" aria-label="Close details">×</button></header>
    <div class="obs-tabs" role="tablist" aria-label="Inspect this sky"><button type="button" role="tab" id="obs-tab-view" aria-controls="obs-pane-view" data-panel="view" aria-selected="true">View</button><button type="button" role="tab" id="obs-tab-analysis" aria-controls="obs-pane-analysis" data-panel="analysis" aria-selected="false" tabindex="-1">Analysis</button><button type="button" role="tab" id="obs-tab-context" aria-controls="obs-pane-context" data-panel="context" aria-selected="false" tabindex="-1">Sky & votes</button></div>
    <div class="obs-details-scroll"><div class="obs-pane" id="obs-pane-view" role="tabpanel" aria-labelledby="obs-tab-view"><div class="obs-controls"></div><div class="obs-object"></div></div><div class="obs-pane" id="obs-pane-analysis" role="tabpanel" aria-labelledby="obs-tab-analysis" hidden></div><div class="obs-pane" id="obs-pane-context" role="tabpanel" aria-labelledby="obs-tab-context" hidden></div></div>`;
    scene.append(aside);
    const controls = $('.obs-controls');
    controls.append($('.bk-modes'));
    $<HTMLButtonElement>('[data-mode="blink"]').textContent = 'Normal';
    const contrast = $<HTMLInputElement>('.bk-contrast');
    const contrastRow = contrast.closest<HTMLElement>('.bk-adj-row')!;
    contrastRow.classList.add('obs-brightness');
    contrastRow.querySelector('label')!.textContent = 'Brightness stretch';
    const amount = document.createElement('output');
    amount.value = contrast.value;
    amount.setAttribute('for', contrast.id);
    contrastRow.append(amount);
    contrast.addEventListener('input', () => amount.value = Number(contrast.value).toFixed(2));
    controls.append(contrastRow);
    const invert = document.createElement('label');
    invert.className = 'obs-invert';
    invert.innerHTML = '<input type="checkbox" aria-label="Invert image"> <span>Invert</span>';
    controls.append(invert);
    invert.querySelector('input')!.onchange = e => $<HTMLButtonElement>(`[data-positive="${(e.target as HTMLInputElement).checked}"]`).click();
    const marks = $('.bk-marks');
    controls.append(marks);
    const names: Record<string, string> = { gaia: 'Catalogued stars', known: 'Moving objects', target: 'Selected target', guides: 'Scale and compass' };
    marks.querySelectorAll<HTMLButtonElement>('[data-ov]').forEach(b => { b.textContent = names[b.dataset.ov!]; b.setAttribute('aria-label', names[b.dataset.ov!]); });
    const band = document.createElement('section');
    band.className = 'obs-wavelength';
    band.innerHTML = '<h3>Wavelength band</h3><p class="obs-band-description"></p>';
    band.insertBefore($('.bk-ramp'), band.lastElementChild);
    controls.append(band);
    const advanced = document.createElement('details');
    advanced.className = 'obs-advanced';
    advanced.innerHTML = '<summary>Sampling & display</summary><div class="obs-sampling"></div><p class="obs-field-note">The image fills the window at a uniform pixel scale. Fit the full field to see its complete footprint.</p><div class="seg obs-fit"><button type="button" data-blink-control data-fit="window" aria-pressed="true">Fill window</button><button type="button" data-blink-control data-fit="field" aria-pressed="false">Fit full field</button></div>';
    const sampling = advanced.querySelector('.obs-sampling')!;
    sampling.append($('.bk-clock'), $('.bk-fov'));
    const adjust = $('.bk-adjust');
    adjust.hidden = false;
    advanced.append(adjust);
    adjust.querySelector('[data-positive]')!.closest<HTMLElement>('.bk-adj-row')!.hidden = true;
    // The original mark row is now empty; the live marks themselves are above.
    const marksLabel = adjust.querySelector('[id$="-mk"]');
    if (marksLabel)
        marksLabel.closest<HTMLElement>('.bk-adj-row')!.hidden = true;
    controls.append(advanced);
    $('.bk-row').hidden = true;
    $('.obs-object').append(app.querySelector('#card')!);
    $('#obs-pane-analysis').append(app.querySelector('#science-workbench')!);
    $('#obs-pane-context').append(app.querySelector('#vote')!, app.querySelector('#sky')!);
    const dock = document.createElement('div');
    dock.className = 'obs-dock';
    dock.setAttribute('aria-label', 'Observation playback');
    dock.innerHTML = `<label class="visually-hidden" for="obs-target">Target</label><select id="obs-target" class="obs-target">${TARGETS.map(t => `<option value="${t.slug}">${escape(t.short)}</option>`).join('')}<option value="custom">Custom sky</option></select><div class="obs-transport"></div><button class="obs-prev" type="button" data-blink-control data-step="-1" aria-label="Previous observation">◂</button><div class="obs-timeline"></div><button class="obs-next" type="button" data-blink-control data-step="1" aria-label="Next observation">▸</button><label class="visually-hidden" for="obs-speed">Playback speed</label><select id="obs-speed" class="obs-speed"><option value="0.5">0.25×</option><option value="1">0.5×</option><option value="2" selected>1×</option><option value="4">2×</option><option value="8">4×</option></select><div class="obs-zoom"><button type="button" data-blink-control data-zoom-delta="0.5" aria-label="Zoom out">−</button><button type="button" data-blink-control data-zoom="1" aria-label="Centre field">⊙</button><button type="button" data-blink-control data-zoom-delta="2" aria-label="Zoom in">+</button></div><button class="obs-toggle" type="button" aria-expanded="true" aria-controls="obs-details">Hide details</button>`;
    dock.querySelector('.obs-transport')!.append($('.bk-play'));
    dock.querySelector('.obs-timeline')!.append($('.bk-axis'));
    scene.append(dock);
    $('.bk-strip').hidden = true;
    $<HTMLSelectElement>('.obs-target').onchange = e => { const slug = (e.target as HTMLSelectElement).value; if (slug === 'custom')
        workspace(true);
    else
        opts.target(slug); };
    $<HTMLSelectElement>('.obs-speed').onchange = e => $<HTMLButtonElement>(`[data-fps="${(e.target as HTMLSelectElement).value}"]`)?.click();
    let open = !window.matchMedia?.('(max-width: 720px)').matches;
    let active = false;
    let selectedPanel = "view";
    function details(value: boolean, focus = false) {
        open = value;
        aside.hidden = !open;
        scene.dataset.details = open ? 'open' : 'closed';
        const toggle = $<HTMLButtonElement>('.obs-toggle');
        toggle.textContent = open ? 'Hide details' : 'Details';
        toggle.setAttribute('aria-expanded', String(open));
        if (active)
            opts.analysis(open && selectedPanel === "analysis");
        if (focus)
            (open ? aside.querySelector<HTMLButtonElement>('.obs-close')! : toggle).focus();
    }
    function workspace(value: boolean) {
        if (value && opts.workspace) { opts.workspace(); return; }
        nav.hidden = !value;
        overview.querySelector('button')!.setAttribute('aria-expanded', String(value));
        if (value) {
            details(false);
            nav.querySelector<HTMLInputElement>('#q')?.focus();
        }
        else
            overview.querySelector<HTMLButtonElement>('button')!.focus();
    }
    function panel(name: string, focus = false) {
        selectedPanel = name;
        if (name === 'analysis')
            $<HTMLDetailsElement>('.wb-main').open = true;
        details(true);
        aside.querySelectorAll<HTMLElement>('.obs-pane').forEach(e => e.hidden = e.id !== `obs-pane-${name}`);
        aside.querySelectorAll<HTMLButtonElement>('[data-panel]').forEach(b => { const selected = b.dataset.panel === name; b.setAttribute('aria-selected', String(selected)); b.tabIndex = selected ? 0 : -1; if (selected && focus)
            b.focus(); });
    }
    aside.querySelectorAll<HTMLButtonElement>('[data-panel]').forEach(b => b.onclick = () => panel(b.dataset.panel!));
    aside.querySelector('.obs-tabs')!.addEventListener('keydown', e => {
        const event = e as KeyboardEvent;
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
            return;
        event.preventDefault();
        const tabs = [...aside.querySelectorAll<HTMLButtonElement>('[data-panel]')];
        const at = tabs.indexOf(event.target as HTMLButtonElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (at + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        panel(tabs[next].dataset.panel!, true);
    });
    $<HTMLButtonElement>('.obs-close').onclick = () => details(false, true);
    $<HTMLButtonElement>('.obs-toggle').onclick = () => details(!open, true);
    overview.querySelector<HTMLButtonElement>('.obs-menu')!.onclick = () => workspace(nav.hidden);
    nav.querySelector<HTMLButtonElement>('.obs-nav-close')!.onclick = () => workspace(false);
    const onKey = (e: KeyboardEvent) => { if (!active || e.key !== 'Escape')
        return; if (!nav.hidden)
        workspace(false);
    else if (open)
        details(false, true); };
    document.addEventListener('keydown', onKey);
    const observer = new MutationObserver(() => { invert.querySelector('input')!.checked = scene.dataset.positive === 'true'; const pressed = $<HTMLButtonElement>('[data-fps][aria-pressed="true"]'); if (pressed)
        $<HTMLSelectElement>('.obs-speed').value = pressed.dataset.fps!; });
    observer.observe(scene, { attributes: true, attributeFilter: ['data-positive'] });
    details(open);
    return {
        view(v: View, o: ObjectResponse | null) {
            if (!nav.hidden)
                workspace(false);
            overview.querySelector('.obs-name')!.textContent = o?.name ?? v.name ?? 'Selected sky';
            overview.querySelector('.obs-coordinates')!.textContent = formatRaDec(v);
            const d = DETECTORS[v.det];
            overview.querySelector('.obs-band')!.textContent = `Band D${v.det} · ${d.min}–${d.max} µm`;
            $('.obs-band-description').textContent = `D${v.det} covers ${d.min}–${d.max} µm. Compare visits within this band to check motion and brightness.`;
            const description = overview.querySelector('.obs-description')!;
            description.replaceChildren();
            if (o?.measure) {
                const m = o.measure, strong = document.createElement('strong');
                strong.textContent = `Measured motion ${m.rate_arcsec_yr.toFixed(2)}″/yr. `;
                description.append(strong, `A ${m.shift_arcsec.toFixed(2)}″ shift over ${m.dt_yr.toFixed(2)} years${m.predicted_arcsec != null ? `; ${o.gaia ? 'Gaia' : 'the catalogue'} predicts ${m.predicted_arcsec.toFixed(2)}″` : ''}.`);
            }
            else
                description.textContent = 'Compare the same sky at different times. Select a dot to inspect and flag its position.';
            $<HTMLSelectElement>('.obs-target').value = v.slug ?? 'custom';
        },
        active(value: boolean) {
            active = value;
            opts.analysis(active && open && selectedPanel === "analysis");
            if (value) {
                nav.querySelector('.obs-navigation-content')!.append(header, message);
                featured.hidden = true;
            }
            else {
                app.insertBefore(header, featured);
                app.insertBefore(message, featured.nextSibling);
                nav.hidden = true;
                overview.querySelector('button')!.setAttribute('aria-expanded', 'false');
            }
        },
        panel,
        get open() { return open; },
        destroy() { observer.disconnect(); document.removeEventListener('keydown', onKey); },
    };
}
