import {beforeEach,expect,it,vi} from 'vitest';
import {mountGuidedPractice,readPractice,GUIDED_PRACTICE_KEY} from './practice';
import {targetBySlug,viewForTarget} from '../app/state';
import {PRACTICE,PRACTICE_KEY} from '../review/practice';
import type {KV} from '../vote/model';

const store=():KV=>{const data=new Map<string,string>();return {get:k=>data.get(k)??null,set:(k,v)=>{data.set(k,v);}};};
beforeEach(()=>{document.body.innerHTML='';HTMLElement.prototype.scrollIntoView=vi.fn();});

function setup(kv=store()) {
  const task=document.createElement('section'),panel=document.createElement('section');
  document.body.append(task,panel);
  const target=vi.fn(),display=vi.fn(),step=vi.fn();
  const practice=mountGuidedPractice(task,panel,{target,display,step},kv);
  const load=(slug:string)=>{practice.selection(viewForTarget(targetBySlug(slug)!));practice.ready(true);};
  const click=(selector:string)=>panel.querySelector<HTMLButtonElement>(selector)!.click();
  return {practice,task,panel,target,display,step,load,click,kv};
}

it('gates answers until the correct real observation is ready, including after a target change',()=>{
  const h=setup();h.practice.show();
  h.click('[data-answer="mover"]');expect(readPractice(h.kv)).toHaveLength(0);
  h.load('elpis');h.click('[data-practice="hint"]');h.click('[data-practice="difference"]');
  expect(h.display).toHaveBeenLastCalledWith('difference');
  h.click('[data-practice="step"]');expect(h.step).toHaveBeenCalledOnce();
  h.load('wise0855');
  expect(h.panel.textContent).toContain('Return to its observations');
  h.click('[data-answer="mover"]');expect(readPractice(h.kv)).toHaveLength(0);
  h.click('[data-practice="reload"]');expect(h.target).toHaveBeenLastCalledWith('elpis');
  h.load('elpis');h.click('[data-answer="mover"]');
  expect(readPractice(h.kv)).toMatchObject([{slug:'elpis',answer:'mover',correct:true}]);
});

it('offers feedback for uncertainty, resumes locally and completes without submitting real votes',()=>{
  const kv=store();let h=setup(kv);h.practice.show();h.load('elpis');
  h.click('[data-answer="unsure"]');
  expect(h.panel.textContent).toContain('Thanks for taking a careful look');
  h.practice.hide();h=setup(kv);h.practice.show();
  expect(h.target).toHaveBeenLastCalledWith('barnard');
  h.load('barnard');h.click('[data-answer="mover"]');h.click('[data-practice="next"]');
  h.load('rwaur');h.click('[data-answer="changing"]');h.click('[data-practice="next"]');
  expect(h.task.textContent).toContain('Guided practice complete');
  expect(kv.get(PRACTICE_KEY)).toBe('done');
  expect(h.panel.querySelector('a[href="#/review"]')).not.toBeNull();
  expect(readPractice(kv)).toHaveLength(3);
  h.click('[data-practice="restart"]');
  expect(readPractice(kv)).toHaveLength(0);expect(h.target).toHaveBeenLastCalledWith('elpis');
});

it('rejects malformed progress and recalculates the answer key instead of trusting a saved score',()=>{
  const kv=store();kv.set(GUIDED_PRACTICE_KEY,'not-json');expect(readPractice(kv)).toEqual([]);
  kv.set(GUIDED_PRACTICE_KEY,JSON.stringify([{slug:'elpis',answer:'unsure',correct:true},{slug:'rwaur',answer:'changing'}]));
  expect(readPractice(kv)).toEqual([{slug:PRACTICE[0].slug,answer:'unsure',correct:false}]);
});
