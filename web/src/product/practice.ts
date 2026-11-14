import type {View} from '../app/modules';
import {targetBySlug} from '../app/state';
import {PRACTICE, judge, markPracticeDone, type PracticeResult} from '../review/practice';
import {ANSWERS, safeStorage, type Answer, type KV} from '../vote/model';
import {esc} from '../review/text';
import './practice.css';

export const GUIDED_PRACTICE_KEY = 'skyshift.explore.practice.v1';
const LABELS: Record<Answer,string> = {mover:'Moving',changing:'Changing brightness',junk:'Image artifact',unsure:'Not sure'};
const HINTS = [
  'Follow the violet diamonds. They mark the predicted positions of a known asteroid. A real mover should follow a consistent path across several exposures.',
  'Watch the red reticle. The shift is smaller than one SPHEREx pixel between visits. Show changes to compare a visit with the reference image; the saturated core needs care.',
  'Watch the red reticle. Compare the same star in the same detector. Its position stays similar while its brightness changes; image quality can also affect its appearance.',
];

/** Restore only an ordered prefix of known examples; never trust a stored score. */
export function readPractice(kv: KV): PracticeResult[] {
  try {
    const saved: unknown = JSON.parse(kv.get(GUIDED_PRACTICE_KEY) ?? '[]');
    if(!Array.isArray(saved)) return [];
    const results: PracticeResult[] = [];
    for(const [i,row] of saved.entries()) {
      if(i >= PRACTICE.length || !row || row.slug !== PRACTICE[i].slug || !ANSWERS.includes(row.answer)) break;
      const {slug,answer,correct} = judge(PRACTICE[i],row.answer);
      results.push({slug,answer,correct});
    }
    return results;
  } catch { return []; }
}

export function mountGuidedPractice(task: HTMLElement, panel: HTMLElement, opts: {
  target(slug:string):void;
  display(mode:'blink'|'difference'):void;
  step():void;
}, kv:KV=safeStorage()) {
  let results = readPractice(kv);
  let index = Math.min(results.length,PRACTICE.length-1);
  let summary = results.length === PRACTICE.length;
  let visible = false, ready = false, matched = false, hint = false, difference = false;
  let selected: View|null = null;
  let answer: ReturnType<typeof judge>|null = null;
  const save = () => kv.set(GUIDED_PRACTICE_KEY,JSON.stringify(results));
  const matches = () => {
    const target=targetBySlug(PRACTICE[index].slug);
    return !!target && !!selected && selected.slug===target.slug && selected.det===target.det && selected.clock===target.clock && selected.fov===target.fov;
  };

  function render() {
    if(summary) {
      task.innerHTML='<p class="practice-eyebrow">Guided practice complete</p><h2>Keep looking. Keep questioning.</h2>';
      panel.innerHTML=`<div class="practice-content"><h2>Three real examples, three observations</h2>
        <p>You compared movement and brightness in real NASA SPHEREx images.</p>
        <ol class="practice-recap">${results.map((r,i)=>`<li><strong>${esc(PRACTICE[i].name)}</strong><span>You chose ${LABELS[r.answer].toLowerCase()}. ${r.correct?'That matches the known example.':`The known example shows ${LABELS[PRACTICE[i].answer].toLowerCase()}.`}</span></li>`).join('')}</ol>
        <p class="practice-note">Real candidates have no answer key. Your review is an observation for follow-up, not a confirmed discovery. “Not sure” is always useful.</p>
        <div class="practice-actions"><a class="btn btn-primary" href="#/analyze/review">Review real candidates →</a><a class="btn" href="#/analyze/motion">Analyze this target</a><button class="btn btn-ghost" type="button" data-practice="restart">Practice again</button></div>
        <p class="practice-note">Practice progress stays in this browser. No practice answers are submitted as scientific votes.</p></div>`;
      return;
    }
    const item=PRACTICE[index];
    task.innerHTML=`<div class="practice-task-top"><p class="practice-eyebrow">Guided practice · ${index+1} of ${PRACTICE.length}</p><a href="#/explore">Explore freely</a></div>
      <h2>${esc(item.title)}</h2><p>${esc(item.prompt)}</p>
      <div class="practice-progress" role="progressbar" aria-label="Examples completed" aria-valuemin="0" aria-valuemax="3" aria-valuenow="${results.length}"><span style="width:${results.length/PRACTICE.length*100}%"></span></div>`;
    panel.innerHTML=`<div class="practice-content"><p class="practice-eyebrow">Real SPHEREx observations · ${item.slug==='elpis'?'Hours':'Months'} apart</p>
      <h2>What do you notice?</h2><p>Use Play or step through the dates. Compare the same point with the stars around it.</p>
      <div class="practice-controls"><button class="btn" type="button" data-practice="step" ${!ready||!matched?'disabled':''}>Next observation</button><button class="btn" type="button" data-practice="hint" aria-expanded="${hint}">${hint?'Hide hint':'Show a hint'}</button></div>
      <div class="practice-hint" ${hint?'':'hidden'}><p>${HINTS[index]}</p><button class="btn" type="button" data-practice="difference" aria-pressed="${difference}" ${!ready||!matched?'disabled':''}>${difference?'Show observations':'Show changes'}</button></div>
      <div class="practice-choices" role="group" aria-label="Your observation">${ANSWERS.map(a=>`<button class="btn practice-choice" type="button" data-answer="${a}" aria-pressed="${answer?.answer===a}" ${!ready||!matched||answer?'disabled':''}>${LABELS[a]}</button>`).join('')}</div>
      <p class="practice-status" role="status">${!matched?'This example uses a different target, clock or detector. Return to its observations to continue.':!ready?'Loading the observations. Choices open when the images are ready.':'No time limit. “Not sure” is welcome.'}</p>
      <button class="btn btn-ghost" type="button" data-practice="reload">${!matched?'Return to practice target':'Reload observations'}</button>
      <div class="practice-feedback" aria-live="polite">${answer?`<p class="practice-feedback-title">${answer.correct?'You spotted it.':answer.answer==='unsure'?'Thanks for taking a careful look.':'Here is what to look for.'}</p><p>${esc(answer.text)}</p><button class="btn btn-primary" type="button" data-practice="next">${index===PRACTICE.length-1?'See your observations':'Next example →'}</button>`:''}</div>
      <p class="practice-note">Hatched areas have no usable data. These are known examples for learning; practice answers stay in this browser.</p></div>`;
  }
  function load() {
    ready=false;matched=false;difference=false;render();
    opts.display('blink');opts.target(PRACTICE[index].slug);
  }
  panel.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('button');
    if(!visible||!button||button.disabled)return;
    const choice=button.dataset.answer as Answer|undefined;
    if(choice && ANSWERS.includes(choice) && ready && matched && !answer && !summary) {
      answer=judge(PRACTICE[index],choice);
      results[index]={slug:answer.slug,answer:choice,correct:answer.correct};save();render();
      panel.querySelector<HTMLButtonElement>('[data-practice="next"]')?.focus({preventScroll:true});
    }
    switch(button.dataset.practice) {
      case 'step':opts.step();break;
      case 'hint':hint=!hint;render();panel.querySelector<HTMLButtonElement>('[data-practice="hint"]')?.focus({preventScroll:true});break;
      case 'difference':difference=!difference;opts.display(difference?'difference':'blink');render();panel.querySelector<HTMLButtonElement>('[data-practice="difference"]')?.focus({preventScroll:true});break;
      case 'reload':load();break;
      case 'next':
        if(!answer)return;
        if(index===PRACTICE.length-1){summary=true;markPracticeDone(kv);render();}
        else{index++;answer=null;hint=false;load();}
        task.scrollIntoView({block:'nearest'});break;
      case 'restart':results=[];save();index=0;summary=false;answer=null;hint=false;load();break;
    }
  });
  render();
  return {
    show(){if(visible)return;visible=true;if(!summary)load();else{markPracticeDone(kv);render();opts.target(PRACTICE[PRACTICE.length-1].slug);}},
    hide(){if(visible)opts.display('blink');visible=false;},
    selection(view:View){selected=view;matched=matches();ready=false;if(visible)render();},
    ready(value:boolean){ready=value;matched=matches();if(visible)render();},
  };
}
