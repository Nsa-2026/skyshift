import {describe,it,expect} from 'vitest';
import {renderSpectrum} from './spectrum';
import {mountCard} from './index';
import type {SpectrumPoint} from '../api/types';
const points:SpectrumPoint[]=[{um:2,flux_ujy:10,err_ujy:1,det:3,mjd:60000,visit:1,saturated:false}];
describe('unified science adapter',()=>{
 it('overlays DR1 inside the measured spectrum without duplicating measured points',()=>{
  const chart=renderSpectrum(points,{width:400,mode:'det',id:'test-reference',reference:[{um:4.5,flux_ujy:100},{um:1,flux_ujy:-1}]});
  const el=document.createElement('div');el.innerHTML=chart.svg;
  expect(el.querySelectorAll('svg')).toHaveLength(1);
  expect(el.querySelectorAll('svg .reference-point')).toHaveLength(1);
  expect(chart.points).toHaveLength(1);expect(chart.svg).not.toContain('NaN');
 });
 it('reuses the formal card without rendering a second spectrum or light curve',()=>{
  const el=document.createElement('div');const card=mountCard(el,{plots:false});
  card.show({ra:1,dec:2,clock:'months',fov:5,det:3,slug:null,name:'one target'},
   {name:'one target',gaia:null,known_ucd:null,spectrum:points,measure:null},null);
  expect(el.querySelector('.cd-measure')).not.toBeNull();
  expect(el.querySelector('.cd-spec')).toBeNull();
  expect(el.querySelector('.cd-lc')).toBeNull();
 });
});
