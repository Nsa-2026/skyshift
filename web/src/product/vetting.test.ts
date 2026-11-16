import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {describe,expect,it} from 'vitest';
import type {Analysis,Candidate} from '../api/science-client';
import type {GaiaStar,KnownPrediction} from '../api/catalogues';
import {candidateRows} from './evidence';
import {category,checkSummary,gaiaAt,precomputedVerdict,signalTrack,vetSignal,type Catalogues,type Check,type ScienceFrame,type Track,type Verdict} from './vetting';

const read=<T,>(slug:string,file:string)=>JSON.parse(readFileSync(join(__dirname,`../../public/science-data/${slug}/${file}.json`),'utf8')) as T;
const none=():Catalogues=>({gaia:{state:'ok',rows:[]},catwise:{state:'ok',rows:[]},mpc:{state:'ok',rows:[]}});
// A field with something in it, but far from every test signal (so the catalogues are not empty).
const far={gaia:{id:'1',ra:0,dec:0,pmra:0,pmdec:0,parallax:null,g:15},catwise:{name:'J000000.00+000000.0',ra:0,dec:0,w1mpro:15,w2mpro:15,pmra:0,pmdec:0}};
const answered=(over:Partial<Catalogues>={}):Catalogues=>({gaia:{state:'ok',rows:[far.gaia]},catwise:{state:'ok',rows:[far.catwise]},mpc:{state:'ok',rows:[]},...over});

/** Barnard's Star field: its first fast mover (three exposures in visit 3) and the science frames' times. */
const barnard=read<Analysis>('barnard','analysis');
const frames=read<{frames:ScienceFrame[]}>('barnard','science').frames;
const fast=barnard.fast_movers[0];
const fastTrack=()=>signalTrack('fast',fast,frames,2)!;
/** Predictions that follow the mover's own sightings, one per session, as /api/enrichment?kind=asteroids gives them. */
const along=(t:Track,name='306P/LINEAR',shift={east:0,north:0}):KnownPrediction[]=>t.sightings.map(s=>({name,v:18.5,visit:3,mjd:s.mjd,
  ra:s.ra+shift.east/3600/Math.cos(s.dec*Math.PI/180),dec:s.dec+shift.north/3600}));

describe('where and when a signal was seen',()=>{
  it('puts a fast mover at each exposure time and place, x west and y north on the analysis grid',()=>{
    const t=fastTrack();
    expect(t.sightings.map(s=>s.mjd)).toEqual(fast.detections!.map(d=>frames[d.t].mjd));
    // vx > 0 (x grows west), vy < 0 (south): moving west-southwest.
    expect(t.motion!.pa_deg).toBeGreaterThan(180+45);expect(t.motion!.pa_deg).toBeLessThan(270);
    expect(t.motion!.arcsec_h*24).toBeCloseTo(fast.speed_arcsec_day!,6);
    expect(t.sightings[0].ra).toBeGreaterThan(t.sightings[2].ra);  // later sightings further west
  });
  it('gives a slow mover one sighting per visit, at the middle of its detector\'s exposures',()=>{
    const eps=read<Analysis>('epsindib','analysis'),f=read<{frames:ScienceFrame[]}>('epsindib','science').frames;
    const t=signalTrack('slow',eps.slow_movers[0],f,2)!;
    expect(t.sightings).toHaveLength(4);
    const v1=f.filter(x=>x.det===2&&x.visit===1).map(x=>x.mjd);
    expect(t.sightings[0].mjd).toBeCloseTo((Math.min(...v1)+Math.max(...v1))/2,6);
    expect(t.motion!.pa_deg).toBeCloseTo(Math.atan2(3.733054633821142,-2.5684679738123273)*180/Math.PI,3);
  });
  it('without the times a mover falls back to its reference time, and a cold source is not timed',()=>{
    expect(signalTrack('fast',fast,null,2)!.sightings).toEqual([{mjd:fast.t0,ra:fast.ra,dec:fast.dec}]);
    expect(signalTrack('cold',{ra:1,dec:1,status:'x',p_false:null,x:1,y:1,detections:[{x:1,y:1,t:0}]},null,2)).toBeNull();
  });
  it('moves a Gaia star from 2016.0 with its proper motion',()=>{
    const star:GaiaStar={id:'x',ra:10,dec:0,pmra:10_000,pmdec:0,parallax:null,g:10};
    expect((gaiaAt(star,57388.5+365.25).ra-10)*3600).toBeCloseTo(10,6);
  });
});

describe('the contract\'s rule for a known body (v3.23)',()=>{
  it('is certain when the body is within 10″ at every sighting, moving at our rate and direction',()=>{
    const t=fastTrack(),v=vetSignal('fast',t,answered({mpc:{state:'ok',rows:along(t,'306P/LINEAR',{east:4,north:-3})}}),2);
    expect(v.status).toBe('known');expect(v.known_name).toBe('306P/LINEAR');expect(v.known_kind).toBe('body');
    expect(v.checks[0]).toMatchObject({code:'known_body',level:'strong'});
    expect(v.checks[0].text).toMatch(/306P\/LINEAR \(V 18\.5\) within 5\.0″ of it at all 3 sightings, moving .*″\/day toward PA \d+° \(ours .*″\/day toward PA \d+°\)/);
    expect(category(v)).toBe('body');
    // Contract v3.25's values keys: the nearest and farthest, the body's motion and ours.
    const vals=v.checks[0].values!;
    expect(Object.keys(vals)).toEqual(['sep_arcsec','max_sep_arcsec','sightings','body_rate_arcsec_h','body_pa_deg','rate_arcsec_h','pa_deg']);
    expect(vals.sep_arcsec).toBeCloseTo(5,1);expect(vals.max_sep_arcsec).toBeCloseTo(5,1);expect(vals.sightings).toBe(3);
    expect(vals.rate_arcsec_h).toBeCloseTo(t.motion!.arcsec_h,3);expect(vals.pa_deg).toBe(Math.round(t.motion!.pa_deg));
  });
  it('only can\'t rule it out when the place matches but the direction is off by more than 15°',()=>{
    // Three exposures in one session, 30″/h due east; the body sits on every one of them but its next
    // session's prediction says it moves due north at the same rate.
    const h=1/24,east=(arcsec:number)=>arcsec/3600;
    const t:Track={sightings:[0,2,4].map(min=>({mjd:61000+min/1440,ra:10+east(30*min/60),dec:0})),motion:{arcsec_h:30,pa_deg:90}};
    const rows:KnownPrediction[]=[{name:'(4) Vesta',v:7,visit:1,mjd:61000+2/1440,ra:t.sightings[1].ra,dec:0},
      {name:'(4) Vesta',v:7,visit:1,mjd:61000+2/1440+24*h,ra:t.sightings[1].ra,dec:30*24/3600}];
    const v=vetSignal('fast',t,answered({mpc:{state:'ok',rows}}),2);
    expect(v.status).toBe('open');
    const line=v.checks.find(c=>c.code==='known_nearby')!;
    expect(line.level).toBe('warn');
    expect(line.text).toMatch(/within 30″ at all 3 sightings, but moving 30\.0″\/h toward PA 0° \(ours 30\.0″\/h toward PA 90°\)/);
    expect(line.values).toEqual({sep_arcsec:0,sightings:3,body_rate_arcsec_h:30,body_pa_deg:0,rate_arcsec_h:30,pa_deg:90});
    // The same body moving east is certain.
    const same=[rows[0],{...rows[1],ra:t.sightings[1].ra+east(30*24),dec:0}];
    expect(vetSignal('fast',t,answered({mpc:{state:'ok',rows:same}}),2).status).toBe('known');
  });
  it('lists a body predicted within 30″ at one sighting as something to look at, with the date',()=>{
    const t=fastTrack(),one=along(t,'(265513)',{east:20,north:0}).slice(1,2);
    const v=vetSignal('fast',t,answered({mpc:{state:'ok',rows:one}}),2);
    const line=v.checks.find(c=>c.code==='known_nearby')!;
    expect(line.level).toBe('warn');
    expect(line.text).toMatch(/\(265513\) \(V 18\.5\) 20″ from it on \d{4}-\d\d-\d\d, within 30″ at 1 of its 3 sightings/);
    expect(line.look).toMatch(/^Blink Hours on D2 around \d{4}-\d\d-\d\d/);
    expect(category(v)).toBe('near');
  });
  it('ignores a body that passes more than 30″ away',()=>{
    const t=fastTrack(),v=vetSignal('fast',t,answered({mpc:{state:'ok',rows:along(t,'(1)',{east:45,north:0})}}),2);
    expect(v.checks.some(c=>c.code==='known_nearby')).toBe(false);
    expect(category(v)).toBe('clear');
  });
});

describe('catalogue stars',()=>{
  const eps=read<Analysis>('epsindib','analysis'),f=read<{frames:ScienceFrame[]}>('epsindib','science').frames;
  // Eps Indi B as Gaia DR3 lists it (the live /api/gaia answer of 16 Nov).
  const epsIndiB:GaiaStar={id:'6412596012146801152',ra:331.07645259656397,dec:-56.79381206688221,pmra:3981.977,pmdec:-2466.832,parallax:270.658,g:18.031};
  it('names the slow mover that is a Gaia star moving the same way, and lists it apart',()=>{
    const v=vetSignal('slow',signalTrack('slow',eps.slow_movers[0],f,2),answered({gaia:{state:'ok',rows:[epsIndiB,far.gaia]}}),2);
    expect(v).toMatchObject({status:'known',known_kind:'star',known_name:'Gaia DR3 6412596012146801152'});
    expect(v.checks[0].text).toMatch(/within 0\.\d″ of it at all 4 sightings, moving 4\.68″\/yr toward PA 122° \(ours 4\.53″\/yr toward PA 125°\)/);
    const vals=v.checks[0].values!;
    expect(Object.keys(vals)).toEqual(['sep_arcsec','max_sep_arcsec','star_rate_arcsec_yr','star_pa_deg','rate_arcsec_yr','pa_deg']);
    expect(vals.sep_arcsec).toBeLessThanOrEqual(vals.max_sep_arcsec as number);expect(vals.max_sep_arcsec).toBeLessThan(1);
    expect(vals).toMatchObject({star_rate_arcsec_yr:expect.closeTo(4.68,2),star_pa_deg:122,rate_arcsec_yr:expect.closeTo(4.53,2),pa_deg:125});
  });
  it('can\'t rule out a star that sits on it but moves another way',()=>{
    const still={...epsIndiB,pmra:0,pmdec:0,...gaiaAt(epsIndiB,61062.8)};
    const v=vetSignal('slow',signalTrack('slow',eps.slow_movers[0],f,2),answered({gaia:{state:'ok',rows:[still]}}),2);
    expect(v.status).toBe('open');
    expect(v.checks[0]).toMatchObject({code:'catalogue_star',level:'warn'});
    expect(v.checks[0].text).toMatch(/moving 0\.00″\/yr toward PA 0° \(ours 4\.53″\/yr/);
  });
  it('matches a star only at the signal\'s time: the 2016 place of a fast star is not enough',()=>{
    const t:Track={sightings:[{mjd:61000,ra:10,dec:0}],motion:null,still:true};
    const years=(61000-57388.5)/365.25;
    const mover:GaiaStar={id:'m',ra:10-1e-3*years*2000/3600,dec:0,pmra:2000,pmdec:0,parallax:null,g:12};  // reaches 10° by then
    const stays:GaiaStar={...mover,id:'s',pmra:0};
    expect(vetSignal('cold',t,answered({gaia:{state:'ok',rows:[mover]}}),6).checks[0].text).toMatch(/^Gaia DR3 m \(G 12\.0\) sits 0\.0″ from it/);
    expect(vetSignal('cold',t,answered({gaia:{state:'ok',rows:[stays]}}),6).found.gaia).toBe(false);
  });
  it('compares a CatWISE source moved from 2015.4 and where it was then, and keeps the nearer',()=>{
    const t:Track={sightings:[{mjd:61000,ra:10,dec:0}],motion:null,still:true};
    // A noisy catalogued motion of 1″/yr would carry it 10″ away by 2025; at its catalogue place it sits on ours.
    const noisy={name:'J004000.00+000000.0',ra:10,dec:0,w1mpro:17,w2mpro:16.5,pmra:1,pmdec:0};
    const v=vetSignal('cold',t,answered({catwise:{state:'ok',rows:[noisy]}}),6);
    expect(v.checks[0]).toMatchObject({code:'catalogue_star',level:'warn'});
    expect(v.checks[0].text).toContain('CatWISE2020 J004000.00+000000.0 (W1 17.0, W2 16.5) sits 0.0″');
    expect(v.checks[0].text).toContain('catalogue epoch 2015.4');
  });
  it('matches a cold source at the mean of its scattered sightings',()=>{
    const t:Track={sightings:[{mjd:61000,ra:10-8/3600,dec:0},{mjd:61200,ra:10+8/3600,dec:0}],motion:null,still:true};
    const star={...far.gaia,ra:10,dec:0,pmra:0,pmdec:0};
    const v=vetSignal('cold',t,answered({gaia:{state:'ok',rows:[star]}}),6);
    expect(v.checks[0].text).toMatch(/sits 0\.0″ from its mean place/);
  });
});

describe('honest answers',()=>{
  const t:Track={sightings:[{mjd:61000,ra:10,dec:0}],motion:null,still:true};
  it('says "Sign for" only for catalogues that answered and found nothing',()=>{
    const v=vetSignal('cold',t,answered(),6);
    expect(v.checks).toEqual([expect.objectContaining({code:'catalogues_checked',level:'ok',
      text:'Gaia DR3, CatWISE2020 and MPC orbits checked: nothing in Gaia or CatWISE within 6″, and no known asteroid (V ≤ 21) or comet predicted within 30″'})]);
    expect(category(v)).toBe('clear');
  });
  it('names a catalogue that did not answer as not checked, never as passed',()=>{
    const cats=answered({catwise:{state:'failed',error:'NASA/IPAC IRSA (CatWISE2020) did not answer'}});
    const v=vetSignal('cold',t,cats,6);
    expect(v.checks.find(c=>c.level==='warn')!.text).toBe('Not checked against CatWISE2020: the service did not answer');
    const ok=v.checks.find(c=>c.level==='ok')!.text;
    expect(ok).toBe('Gaia DR3 and MPC orbits checked: nothing in Gaia within 6″, and no known asteroid (V ≤ 21) or comet predicted within 30″');
    expect(category(v)).toBe('partial');
    const s=checkSummary([v],cats,{catwise:'NASA/IPAC IRSA (CatWISE2020) did not answer (ReadTimeout). No catalogue results are shown; try again later.'});
    expect(s.done).toBe(true);
    expect(s.text).toContain('Checked against Gaia DR3 and MPC orbits (asteroids and comets): 1 signal has nothing in the catalogues that answered.');
    expect(s.text).toContain('Not checked against CatWISE2020: NASA/IPAC IRSA (CatWISE2020) did not answer (ReadTimeout).');
  });
  it('does not count a catalogue with nothing anywhere in the field as a sign for',()=>{
    const v=vetSignal('cold',t,answered({catwise:{state:'ok',rows:[]}}),6);
    expect(v.checks.find(c=>c.code==='catalogue_empty')!.level).toBe('warn');
    expect(v.checks.find(c=>c.level==='ok')!.text).not.toContain('CatWISE');
  });
  it('does not claim MPC passed at times outside its index',()=>{
    const v=vetSignal('cold',t,answered({mpc:{state:'ok',rows:[],unchecked:[61000.004]}}),6);
    expect(v.checks.find(c=>c.code==='catalogue_unchecked')!.text).toBe('MPC orbits were not checked at its sighting: the known-object index doesn\'t reach that time');
    expect(v.checks.find(c=>c.level==='ok')!.text).not.toContain('MPC');
  });
  it('says what is still being checked while catalogues answer',()=>{
    const cats:Catalogues={...none(),catwise:{state:'pending'},mpc:{state:'pending'}};
    const v=vetSignal('cold',t,cats,6);
    expect(v.pending).toEqual(['catwise','mpc']);
    expect(checkSummary([v],cats)).toEqual({done:false,text:'Checking catalogues… Gaia DR3 answered; still waiting for CatWISE2020 and MPC orbits.'});
  });
  it('a signal without times is not checked, and says so',()=>{
    const v=vetSignal('cold',null,answered(),6);
    expect(v.untimed).toBe(true);
    expect(v.checks[0].text).toMatch(/^Not checked against catalogues: the times of its observations did not load/);
    expect(checkSummary([v],answered()).text).toContain('1 signal could not be checked');
  });
});

describe('checks written before the list was published',()=>{
  const eps=read<Analysis>('epsindib','analysis'),f=read<{frames:ScienceFrame[]}>('epsindib','science').frames;
  const epsIndiB:GaiaStar={id:'6412596012146801152',ra:331.07645259656397,dec:-56.79381206688221,pmra:3981.977,pmdec:-2466.832,parallax:270.658,g:18.031};
  const still:Track={sightings:[{mjd:61000,ra:10,dec:0}],motion:null,still:true};
  const t=fastTrack();
  // Every kind of answer the browser gives, written as a file holds it, reads back as the same answer.
  const cases:[string,Verdict][]=[
    ['body',vetSignal('fast',t,answered({mpc:{state:'ok',rows:along(t)}}),2)],
    ['star',vetSignal('slow',signalTrack('slow',eps.slow_movers[0],f,2),answered({gaia:{state:'ok',rows:[epsIndiB,far.gaia]}}),2)],
    ['near',vetSignal('fast',t,answered({mpc:{state:'ok',rows:along(t,'(265513)',{east:20,north:0}).slice(1,2)}}),2)],
    ['gaia',vetSignal('cold',still,answered({gaia:{state:'ok',rows:[{...far.gaia,ra:10,dec:0,pmra:0,pmdec:0}]}}),6)],
    ['catwise',vetSignal('cold',still,answered({catwise:{state:'ok',rows:[{...far.catwise,ra:10,dec:0}]}}),6)],
    ['clear',vetSignal('cold',still,answered(),6)],
    ['partial',vetSignal('cold',still,answered({catwise:{state:'ok',rows:[]}}),6)],
  ];
  it.each(cases)('reads back a %s answer',(want,v)=>{
    expect(category(v)).toBe(want);
    const c={ra:10,dec:0,status:'unvetted',p_false:null,check_status:v.status,known_name:v.known_name,checks:JSON.parse(JSON.stringify(v.checks))} as Candidate;
    const back=precomputedVerdict(c)!;
    expect(category(back)).toBe(want);
    expect(back).toMatchObject({status:v.status,known_name:v.known_name,known_kind:v.known_kind,checks:v.checks,found:v.found,pending:[],failed:[]});
  });
  it('is none for a candidate without checks, and keeps only well-formed ones, sorted',()=>{
    expect(precomputedVerdict({ra:1,dec:1,status:'unvetted',p_false:null})).toBeNull();
    const v=precomputedVerdict({ra:1,dec:1,status:'unvetted',p_false:null,check_status:'open',checks:[
      {code:'catalogues_checked',level:'ok',text:'Gaia DR3 checked: nothing',look:'',values:null},{code:'x',level:'??',text:'bad'},null,
      {code:'catalogue_star',level:'warn',text:'A star',look:'Look here',values:{catalogue:'catwise'}}]} as Candidate)!;
    expect(v.checks.map(c=>c.code)).toEqual(['catalogue_star','catalogues_checked']);
    expect(category(v)).toBe('catwise');
    // Only `known` is listed apart; any other status reads as open.
    expect(precomputedVerdict({ra:1,dec:1,status:'x',p_false:null,check_status:'flaw',checks:[]} as Candidate)!.status).toBe('open');
  });
  it('reads files written before contract v3.25\'s values keys (#375) the same way',()=>{
    // #375 wrote the star's motion in rate_arcsec_yr / pa_deg and the farthest in sep_arcsec: the verdict uses none of them.
    const old={ra:1,dec:1,status:'unvetted',p_false:null,check_status:'known',known_name:'Gaia DR3 6412596012146801152',checks:[
      {code:'known_star',level:'strong',text:'Gaia DR3 6412596012146801152 (G 18.0) is within 0.8″ of it',look:'',values:{sep_arcsec:0.8,rate_arcsec_yr:4.68,pa_deg:122}}]} as Candidate;
    const now={...old,checks:[{...(old as Candidate&{checks:Check[]}).checks[0],values:{sep_arcsec:0.3,max_sep_arcsec:0.8,star_rate_arcsec_yr:4.68,star_pa_deg:122,rate_arcsec_yr:4.53,pa_deg:125}}]} as Candidate;
    const [a,b]=[precomputedVerdict(old)!,precomputedVerdict(now)!];
    expect({...a,checks:[]}).toEqual({...b,checks:[]});
    expect(category(a)).toBe('star');
  });
  it('the summary says the checks were done before publishing',()=>{
    const v=precomputedVerdict({ra:1,dec:1,status:'x',p_false:null,check_status:'known',known_name:'Gaia DR3 1',checks:[{code:'known_star',level:'strong',text:'t',look:'',values:null}]} as Candidate)!;
    expect(v.known_kind).toBe('star');
    expect(checkSummary([v],answered(),{},'Checked before this list was published').text).toBe('Checked before this list was published: 1 signal is a known star.');
  });
});

it('summarises the five featured fields\' kinds of answer in one sentence, each signal counted once',()=>{
  const t=fastTrack(),known=vetSignal('fast',t,answered({mpc:{state:'ok',rows:along(t)}}),2);
  const onStar=vetSignal('cold',{sightings:[{mjd:61000,ra:0,dec:0}],motion:null,still:true},answered(),6);
  const clear=vetSignal('cold',{sightings:[{mjd:61000,ra:10,dec:0}],motion:null,still:true},answered(),6);
  expect(checkSummary([known,known,onStar,clear],answered()).text)
    .toBe('Checked against Gaia DR3, CatWISE2020 and MPC orbits (asteroids and comets): 2 signals are known bodies, 1 sits on a Gaia star, 1 has nothing in any of them.');
  const rows=candidateRows(barnard);
  expect(rows.map(r=>vetSignal(r.kind,signalTrack(r.kind,r.c as Candidate,frames,2),answered(),2).status)).toEqual(['open','open']);
});
