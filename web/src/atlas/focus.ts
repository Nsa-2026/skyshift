import { wholeFov } from './zoom';

/** Fade as soon as zooming starts; a 1.3× zoom clears the viewing chrome. */
export function chromeOpacity(fov: number, mol: boolean): number {
  if (!Number.isFinite(fov) || fov <= 0) return 1;
  const start = wholeFov(mol), end = wholeFov(mol) / 1.3;
  if (fov >= start) return 1;
  if (fov <= end) return 0;
  const t = Math.log(fov / end) / Math.log(start / end);
  const opacity = t * t * (3 - 2 * t);
  // Do not leave imperceptible surfaces intercepting map gestures or keyboard focus.
  return opacity < .01 ? 0 : opacity;
}

type Attribute = string | null;
const restoreAttribute = (el: HTMLElement, name: string, value: Attribute) => {
  if (el.getAttribute(name) === value) return;
  if (value === null) el.removeAttribute(name);
  else el.setAttribute(name, value);
};
const setClass = (el:HTMLElement,name:string,on:boolean) => {
  if(el.classList.contains(name)!==on)el.classList.toggle(name,on);
};

/** Reversible viewing chrome. The scientific map and its selected-object panels stay active. */
export function mountZoomFocus(el: HTMLElement) {
  const root = el.closest<HTMLElement>('#app') ?? el;
  const header = root.querySelector<HTMLElement>(':scope > .top');
  const groups = [...new Set([...(header ? [header] : []), ...el.querySelectorAll<HTMLElement>('[data-map-chrome]')])];
  const map = el.querySelector<HTMLElement>('[data-map]');
  const button = el.querySelector<HTMLButtonElement>('[data-map-controls]');
  const adjust = el.querySelector<HTMLElement>('.atl-adjust');
  const more = el.querySelector<HTMLElement>('.atl-more');
  const originals = groups.map(group => ({ group, inert:group.getAttribute('inert'), aria:group.getAttribute('aria-hidden') }));
  const surfaces = [...new Set([root, el])];
  const styles = surfaces.map(surface => ({ surface, value:surface.style.getPropertyValue('--sky-chrome-opacity'), priority:surface.style.getPropertyPriority('--sky-chrome-opacity') }));
  const classes = ['sky-focus-active', 'sky-chrome-hidden'].map(name => ({name, present:root.classList.contains(name)}));
  const originalButton = button ? {hidden:button.hidden, expanded:button.getAttribute('aria-expanded'), text:button.textContent,
    label:button.getAttribute('aria-label'),title:button.getAttribute('title')} : null;
  let active = false, destroyed = false, manual = false, base = 1, queued = false;

  function restoreGroups() {
    for (const {group,inert,aria} of originals) {
      restoreAttribute(group,'inert',inert);
      restoreAttribute(group,'aria-hidden',aria);
    }
  }
  function restoreShell() {
    restoreGroups();
    for (const {surface,value,priority} of styles) {
      if (value) {
        if(surface.style.getPropertyValue('--sky-chrome-opacity')!==value||surface.style.getPropertyPriority('--sky-chrome-opacity')!==priority)
          surface.style.setProperty('--sky-chrome-opacity',value,priority);
      } else if(surface.style.getPropertyValue('--sky-chrome-opacity'))surface.style.removeProperty('--sky-chrome-opacity');
    }
    for (const {name,present} of classes) setClass(root,name,present);
  }
  function isEditing(target: EventTarget | null) {
    return target instanceof Element && !!target.closest('input,textarea,select,[contenteditable="true"]');
  }
  function interactionProtected() {
    return groups.some(group => group.contains(document.activeElement)) ||
      adjust?.getAttribute('aria-expanded') === 'true' || (!!more && !more.hidden);
  }
  function inspectorOpen() {
    return !!el.querySelector('.atl-pin:not([hidden]), .atl-drawer:not([hidden])');
  }
  function render() {
    if (destroyed) return;
    if (!active) {
      restoreShell();
      if (button && !button.hidden) button.hidden = true;
      return;
    }
    const opacity = manual || interactionProtected() ? 1 : base;
    setClass(root,'sky-focus-active',true);
    setClass(root,'sky-chrome-hidden',opacity === 0);
    for (const surface of surfaces) if(surface.style.getPropertyValue('--sky-chrome-opacity')!==String(opacity)||surface.style.getPropertyPriority('--sky-chrome-opacity'))
      surface.style.setProperty('--sky-chrome-opacity',String(opacity));
    if (opacity === 0) for (const group of groups) {
      restoreAttribute(group,'inert','');
      restoreAttribute(group,'aria-hidden','true');
    } else restoreGroups();
    if (button) {
      if(button.hidden!==(base>=.99))button.hidden = base >= .99;
      const label = manual ? 'Hide controls' : 'Show controls';
      if(button.textContent!==label)button.textContent = label;
      restoreAttribute(button,'aria-label',label);
      restoreAttribute(button,'title',`${label} (Escape)`);
      restoreAttribute(button,'aria-expanded',String(manual));
    }
  }
  function scheduleRender() {
    if (queued || destroyed) return;
    queued = true;
    queueMicrotask(() => { queued = false; render(); });
  }
  function focusMap() {
    if (document.activeElement !== map) map?.focus({preventScroll:true});
  }
  function closeAdjust() {
    if (adjust?.getAttribute('aria-expanded') !== 'true') return;
    adjust.setAttribute('aria-expanded','false');
    if (more) more.hidden = true;
  }
  function mapInteraction() {
    if (!active || destroyed) return;
    manual = false;
    closeAdjust();
    focusMap();
    render();
  }
  function mapKey(event: KeyboardEvent) {
    if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','_'].includes(event.key)) mapInteraction();
  }
  function toggle() {
    if (!active || destroyed) return;
    manual = !manual;
    if (!manual) {
      closeAdjust();
      // Escape must be able to dismiss a manually revealed, keyboard-focused nav.
      if (groups.some(group => group.contains(document.activeElement))) map?.focus({preventScroll:true});
    }
    render();
  }
  function onEscape(event: KeyboardEvent) {
    if (event.key !== 'Escape' || event.defaultPrevented || !active || destroyed || base >= .99 || inspectorOpen() || isEditing(event.target)) return;
    event.preventDefault();
    toggle();
  }
  root.addEventListener('focusin',render);
  root.addEventListener('focusout',scheduleRender);
  // Aladin consumes pointer/wheel events on its canvas before they can bubble.
  map?.addEventListener('pointerdown',mapInteraction,{passive:true,capture:true});
  map?.addEventListener('wheel',mapInteraction,{passive:true,capture:true});
  map?.addEventListener('keydown',mapKey);
  button?.addEventListener('click',toggle);
  // Capture lets the existing inspector's Escape handler close it first without also toggling chrome.
  document.addEventListener('keydown',onEscape,true);
  const observer = typeof MutationObserver === 'function' ? new MutationObserver(render) : null;
  if (adjust) observer?.observe(adjust,{attributes:true,attributeFilter:['aria-expanded']});
  if (more) observer?.observe(more,{attributes:true,attributeFilter:['hidden']});

  return {
    update(fov:number,mol:boolean) {
      if (destroyed) return;
      base = chromeOpacity(fov,mol);
      if (base >= .99) manual = false;
      render();
    },
    show() { if (!destroyed) { active = true; render(); } },
    hide() { if (!destroyed) { active = false; manual = false; render(); } },
    destroy() {
      if (destroyed) return;
      active = false; manual = false;
      restoreShell();
      destroyed = true;
      observer?.disconnect();
      root.removeEventListener('focusin',render);
      root.removeEventListener('focusout',scheduleRender);
      map?.removeEventListener('pointerdown',mapInteraction,true);
      map?.removeEventListener('wheel',mapInteraction,true);
      map?.removeEventListener('keydown',mapKey);
      button?.removeEventListener('click',toggle);
      document.removeEventListener('keydown',onEscape,true);
      if (button && originalButton) {
        button.hidden = originalButton.hidden;
        button.textContent = originalButton.text;
        restoreAttribute(button,'aria-expanded',originalButton.expanded);
        restoreAttribute(button,'aria-label',originalButton.label);
        restoreAttribute(button,'title',originalButton.title);
      }
    },
  };
}
