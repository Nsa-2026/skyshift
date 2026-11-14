import {describe,it,expect} from 'vitest';
import {falseAlarm,quality,matchedCandidate,motionDirection,spectralColour,separation} from './evidence';
import {bandPairs} from './observations';
import {isTargetPick} from '../play/model';
import type {Analysis,ObjectData,Target} from '../api/science-client';
import type {Epoch} from '../api/types';

describe('scientific evidence shown to beginners',()=>{
  it('never turns missing or invalid probabilities into green badges',()=>{
    for(const p of [null,undefined,NaN,-1,1.1])expect(falseAlarm(p).tone).toBe('unknown');
    expect(falseAlarm(.0198).tone).toBe('good');expect(falseAlarm(.02).tone).toBe('caution');expect(falseAlarm(.1).tone).toBe('caution');expect(falseAlarm(.10001).tone).toBe('risk');
  });
  it('does not mark legacy missing flags or saturated frames as clean',()=>{
    expect(quality({flag_frac:null,sat_frac:null} as unknown as Epoch).tone).toBe('unknown');
    expect(quality({flag_frac:0,sat_frac:.001}).tone).toBe('caution');expect(quality({flag_frac:.01,sat_frac:0}).tone).toBe('good');
  });
  it('never borrows an unrelated candidate probability for a lesson',()=>{
    const a={slow_movers:[{ra:100,dec:0,p_false:.01}],fast_movers:[],cold_candidates:[]} as unknown as Analysis;
    expect(matchedCandidate(a,{ra:101,dec:0},30)).toBeUndefined();expect(matchedCandidate(a,{ra:100,dec:0},30)?.p_false).toBe(.01);
  });
  it('wraps RA and ignores insignificant apparent directions',()=>{
    expect(separation({ra:359.999,dec:0},{ra:.001,dec:0})).toBeCloseTo(7.2);
    expect(motionDirection({shift_arcsec:1,sigma_arcsec:1,pa_deg:270})).toBe('未测得显著移动');expect(motionDirection({shift_arcsec:5,sigma_arcsec:1,pa_deg:270})).toBe('西行');
  });
  it('does not turn saturation or nondetection into an infrared colour',()=>{
    const base={mjd:60000,visit:1,um:2,err_ujy:1,saturated:false};
    expect(spectralColour([{...base,det:3,flux_ujy:1},{...base,det:6,flux_ujy:20}]).value).toBe('unknown');
    expect(spectralColour([{...base,det:3,flux_ujy:10,saturated:true},{...base,det:6,flux_ujy:20}]).value).toBe('unknown');
    expect(spectralColour([{...base,det:3,flux_ujy:10},{...base,det:6,flux_ujy:20}]).value).toBe('long');
  });
  it('compares only overlapping band intervals and does not reuse a visit',()=>{
    const epoch=(a:number,b:number)=>({mjd_start:a,mjd_end:b,mjd_mid:(a+b)/2}) as Epoch;
    expect(bandPairs([epoch(1,5),epoch(11,15)],[epoch(100,110)])).toHaveLength(0);expect(bandPairs([epoch(1,5),epoch(4,6)],[epoch(3,4)])).toHaveLength(1);
  });
  it('checks clicks against propagated target coordinates, not arbitrary bright pixels',()=>{
    const target={ra:269,dec:5} as Target;const object={gaia:{ra:269,dec:5,pmra:0,pmdec:10000}} as ObjectData;
    expect(isTargetPick({ra:269,dec:5+100/3600},target,object,57388.5+3652.5,6.15)).toBe(true);expect(isTargetPick({ra:269,dec:5},target,object,57388.5+3652.5,6.15)).toBe(false);
  });
});
