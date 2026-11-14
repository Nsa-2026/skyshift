import {readFileSync} from 'node:fs';
import {beforeAll, describe, expect, it} from 'vitest';
import {culture, englishCultureText} from './culture';

beforeAll(()=>{
  for(const name of ['guxing-data','guxing','guxing-nasa','guxing-widget']){
    window.eval(readFileSync(`public/guxing/${name}.js`,'utf8'));
  }
});

describe('English historical sky',()=>{
  it('renders English names, chart labels, descriptions and attribution',()=>{
    for(const motion of [false,true]){
      const card=document.createElement('guxing-card');
      card.setAttribute('lang','en');
      card.setAttribute('ra','269.452');card.setAttribute('dec','4.693');
      card.setAttribute('date','2026-11-14');
      card.setAttribute('source','NASA SPHEREx');
      if(motion){card.setAttribute('pmra','-1010');card.setAttribute('pmdec','10230');}
      document.body.append(card);
      const text=card.shadowRoot!.textContent!;
      expect(text).not.toMatch(/\p{Script=Han}/u);
      expect(card.shadowRoot!.querySelector('svg')).not.toBeNull();
      expect(text).toContain('Lodge');
      expect(text).toContain('Stellarium');
      card.remove();
    }
  });

  it('preserves English event and historical records without Chinese name annotations',()=>{
    const {nasa}=culture();
    const records=[...nasa.planets.events.map(e=>e.log_en),...nasa.guest_stars.items.map(g=>g.record_en)];
    expect(records.length).toBeGreaterThan(100);
    for(const record of records){
      const text=englishCultureText(record);
      expect(text).not.toMatch(/\p{Script=Han}/u);
      expect(text.trim().length).toBeGreaterThan(10);
    }
  });
});
