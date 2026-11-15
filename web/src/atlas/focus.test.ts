import { afterEach, describe, expect, it, vi } from 'vitest';
import { chromeOpacity, mountZoomFocus } from './focus';

describe('zoom-driven chrome opacity',()=>{
  it('keeps the overview clear and fades progressively to a fourfold zoom',()=>{
    expect(chromeOpacity(180,false)).toBe(1);
    expect(chromeOpacity(180/1.15,false)).toBe(1);
    expect(chromeOpacity(45,false)).toBe(0);
    expect(chromeOpacity(1,false)).toBe(0);
    const values=[180,150,120,90,60,45].map(fov=>chromeOpacity(fov,false));
    expect(values[2]).toBeGreaterThan(0);expect(values[2]).toBeLessThan(1);
    expect(values.every((value,i)=>i===0||value<=values[i-1])).toBe(true);
  });
  it('gives globe and whole-sky projections the same fade at equal zoom ratios',()=>{
    for(const fov of [180,150,100,70,45,1]) expect(chromeOpacity(fov,false)).toBeCloseTo(chromeOpacity(fov*2,true),12);
  });
  it.each([NaN,Infinity,-Infinity,0,-1])('keeps controls available for invalid FOV %s',fov=>{
    expect(chromeOpacity(fov,false)).toBe(1);
  });
});

let cleanup:(()=>void)|undefined;
afterEach(()=>{cleanup?.();cleanup=undefined;document.body.replaceChildren();vi.restoreAllMocks();});
function setup(){
  const root=document.createElement('div');root.id='app';root.className='existing';
  root.style.setProperty('--sky-chrome-opacity','.77','important');
  root.innerHTML=`<header class="top" aria-hidden="false"><a href="#/sky">Sky</a></header>
    <main style="--sky-chrome-opacity:.66">
      <div data-map-chrome class="intro">Start with the sky</div>
      <div data-map-chrome class="controls"><input aria-label="Position"><button class="atl-adjust" aria-expanded="false">Adjust</button></div>
      <div data-map-chrome class="atl-more" hidden><button>Layer</button></div>
      <p data-map-chrome class="status">Survey status</p>
      <div data-map-chrome class="originally-inert" inert aria-hidden="true"></div>
      <div data-map tabindex="0"></div>
      <button data-map-controls hidden aria-expanded="false" aria-label="Original controls" title="Original title">Map controls</button>
      <div class="atl-pin" hidden></div><aside class="atl-drawer" hidden></aside>
    </main>`;
  document.body.append(root);
  const page=root.querySelector<HTMLElement>('main')!;
  const focus=mountZoomFocus(page);cleanup=()=>focus.destroy();
  return {root,page,focus,header:root.querySelector<HTMLElement>('.top')!,
    input:page.querySelector<HTMLInputElement>('input')!,map:page.querySelector<HTMLElement>('[data-map]')!,
    button:page.querySelector<HTMLButtonElement>('[data-map-controls]')!,
    adjust:page.querySelector<HTMLElement>('.atl-adjust')!,more:page.querySelector<HTMLElement>('.atl-more')!,
    pin:page.querySelector<HTMLElement>('.atl-pin')!,drawer:page.querySelector<HTMLElement>('.atl-drawer')!};
}
const opacity=(root:HTMLElement)=>Number(root.style.getPropertyValue('--sky-chrome-opacity'));
const escape=(target:EventTarget=document)=>target.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));

it('applies a deep-linked focus view only while the atlas is active, then restores on zoom out',()=>{
  const h=setup();h.focus.update(45,false);
  expect(h.root.classList.contains('sky-focus-active')).toBe(false);
  expect(h.header.hasAttribute('inert')).toBe(false);
  h.focus.show();
  expect(opacity(h.root)).toBe(0);expect(h.root.classList.contains('sky-chrome-hidden')).toBe(true);
  expect(h.header.hasAttribute('inert')).toBe(true);expect(h.header.getAttribute('aria-hidden')).toBe('true');
  expect(h.button.hidden).toBe(false);expect(h.button.textContent).toBe('Show controls');
  h.focus.update(180,false);
  expect(opacity(h.root)).toBe(1);expect(h.button.hidden).toBe(true);
  expect(h.header.hasAttribute('inert')).toBe(false);expect(h.header.getAttribute('aria-hidden')).toBe('false');
  expect(h.root.classList.contains('sky-chrome-hidden')).toBe(false);
});

it('removes almost invisible controls from hit testing and keyboard navigation',()=>{
  const h=setup();h.focus.show();h.focus.update(46.5,false);
  expect(opacity(h.root)).toBe(0);expect(h.header.hasAttribute('inert')).toBe(true);
  expect(h.header.getAttribute('aria-hidden')).toBe('true');
  expect(h.button.hidden).toBe(false);
});

it('lets the recovery control reveal and hide chrome without changing the map or scientific panels',()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);
  expect(h.map.hasAttribute('inert')).toBe(false);
  h.button.focus();h.button.click();
  expect(opacity(h.root)).toBe(1);expect(h.button.textContent).toBe('Hide controls');
  expect(h.button.getAttribute('aria-label')).toBe('Hide controls');expect(h.button.title).toBe('Hide controls (Escape)');
  expect(h.button.getAttribute('aria-expanded')).toBe('true');expect(h.header.hasAttribute('inert')).toBe(false);
  h.button.click();
  expect(opacity(h.root)).toBe(0);expect(h.button.getAttribute('aria-expanded')).toBe('false');
  expect(h.button.getAttribute('aria-label')).toBe('Show controls');
  expect(h.pin.hasAttribute('inert')).toBe(false);expect(h.drawer.hasAttribute('inert')).toBe(false);
});

it('allows Escape recovery but gives open inspectors and text editing their normal key handling',()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);h.map.focus();
  escape(h.map);expect(opacity(h.root)).toBe(1);
  escape(h.map);expect(opacity(h.root)).toBe(0);
  h.pin.hidden=false;escape(h.map);expect(h.button.getAttribute('aria-expanded')).toBe('false');
  h.pin.hidden=true;h.drawer.hidden=false;escape(h.map);expect(h.button.getAttribute('aria-expanded')).toBe('false');
  h.drawer.hidden=true;h.focus.update(180,false);h.input.focus();h.focus.update(20,false);
  expect(opacity(h.root)).toBe(1);
  escape(h.input);expect(document.activeElement).toBe(h.input);expect(h.button.getAttribute('aria-expanded')).toBe('false');
});

it.each(['pointerdown','wheel'])('returns focus from navigation/inputs to the map on %s and resumes fading',event=>{
  const h=setup();h.focus.show();h.input.focus();h.focus.update(20,false);
  expect(opacity(h.root)).toBe(1);
  h.map.dispatchEvent(new Event(event,{bubbles:true}));
  expect(document.activeElement).toBe(h.map);expect(opacity(h.root)).toBe(0);
  h.button.focus();h.button.click();expect(opacity(h.root)).toBe(1);
  h.map.dispatchEvent(new Event(event,{bubbles:true}));
  expect(document.activeElement).toBe(h.map);expect(opacity(h.root)).toBe(0);
});

it('clears manual recovery when keyboard navigation returns to the map',()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);h.button.click();
  h.map.focus();h.map.dispatchEvent(new KeyboardEvent('keydown',{key:'+',bubbles:true}));
  expect(opacity(h.root)).toBe(0);expect(h.button.getAttribute('aria-expanded')).toBe('false');
});

it.each(['pointerdown','wheel'])('makes map keyboard zoom available after an ordinary %s',event=>{
  const h=setup();h.focus.show();h.focus.update(20,false);
  h.map.dispatchEvent(new Event(event,{bubbles:true}));
  expect(document.activeElement).toBe(h.map);
  expect(opacity(h.root)).toBe(0);
});

it.each(['pointerdown','wheel'])('returns to map focus even when the canvas consumes %s',event=>{
  const h=setup();h.focus.show();h.focus.update(20,false);h.button.click();
  const canvas=document.createElement('canvas');h.map.append(canvas);
  canvas.addEventListener(event,e=>e.stopPropagation());
  canvas.dispatchEvent(new Event(event,{bubbles:true}));
  expect(document.activeElement).toBe(h.map);expect(opacity(h.root)).toBe(0);
  expect(h.button.getAttribute('aria-expanded')).toBe('false');
});

it('lets Escape hide manually revealed chrome even after keyboard focus enters navigation',()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);h.map.focus();escape(h.map);
  h.header.querySelector('a')!.focus();expect(opacity(h.root)).toBe(1);
  escape(document.activeElement!);
  expect(document.activeElement).toBe(h.map);expect(opacity(h.root)).toBe(0);
  expect(h.button.getAttribute('aria-expanded')).toBe('false');
});

it('dismisses Adjust when returning to the map and does not reveal on incidental mouse movement',()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);
  h.adjust.setAttribute('aria-expanded','true');h.more.hidden=false;h.focus.update(20,false);
  expect(opacity(h.root)).toBe(1);
  h.map.dispatchEvent(new Event('pointerdown',{bubbles:true}));
  expect(h.adjust.getAttribute('aria-expanded')).toBe('false');expect(h.more.hidden).toBe(true);
  expect(opacity(h.root)).toBe(0);
  h.map.dispatchEvent(new MouseEvent('mousemove',{bubbles:true}));expect(opacity(h.root)).toBe(0);
});

it('keeps an open Adjust panel usable, then fades when it closes',async()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);
  h.adjust.setAttribute('aria-expanded','true');h.more.hidden=false;await Promise.resolve();
  expect(opacity(h.root)).toBe(1);expect(h.more.hasAttribute('inert')).toBe(false);
  h.adjust.setAttribute('aria-expanded','false');h.more.hidden=true;await Promise.resolve();
  expect(opacity(h.root)).toBe(0);expect(h.header.hasAttribute('inert')).toBe(true);
});

it('restores original shell attributes, styles and interaction after leaving the map',()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);h.focus.hide();
  expect(h.root.className).toBe('existing');expect(opacity(h.root)).toBe(.77);
  expect(h.page.style.getPropertyValue('--sky-chrome-opacity')).toBe('.66');
  expect(h.header.getAttribute('aria-hidden')).toBe('false');expect(h.header.hasAttribute('inert')).toBe(false);
  const original=h.page.querySelector('.originally-inert')!;
  expect(original.hasAttribute('inert')).toBe(true);expect(original.getAttribute('aria-hidden')).toBe('true');
  expect(h.button.hidden).toBe(true);escape();expect(opacity(h.root)).toBe(.77);
  h.focus.show();expect(opacity(h.root)).toBe(0);
});

it('disposes observers/listeners and ignores queued focus work or late updates',async()=>{
  const h=setup();h.focus.show();h.input.focus();h.map.focus();h.focus.update(20,false);h.focus.destroy();
  h.adjust.setAttribute('aria-expanded','true');h.more.hidden=false;h.button.click();escape();
  h.map.dispatchEvent(new Event('wheel'));h.focus.update(1,false);h.focus.show();await Promise.resolve();
  expect(h.root.className).toBe('existing');expect(opacity(h.root)).toBe(.77);
  expect(h.header.hasAttribute('inert')).toBe(false);expect(h.header.getAttribute('aria-hidden')).toBe('false');
  expect(h.button.hidden).toBe(true);expect(h.button.textContent).toBe('Map controls');
  expect(h.button.getAttribute('aria-label')).toBe('Original controls');expect(h.button.title).toBe('Original title');
});

it('does not rewrite chrome attributes or styles during unchanged-FOV panning',async()=>{
  const h=setup();h.focus.show();h.focus.update(20,false);
  const changed=vi.fn();const observer=new MutationObserver(changed);
  observer.observe(h.root,{attributes:true,childList:true,subtree:true});
  h.focus.update(20,false);h.focus.update(20,false);await Promise.resolve();
  observer.disconnect();expect(changed).not.toHaveBeenCalled();
});
