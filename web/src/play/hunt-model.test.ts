import {describe,it,expect} from 'vitest';
import {clampCamera,toWorld,toScreen,zoomAt,verifyTrail,clueRegion} from './hunt-model';
const world={width:293,height:293},screen={width:1000,height:600};
describe('real sky map navigation',()=>{
  it('keeps a source under the pointer when zooming, and reverses the screen transform',()=>{
    const camera={x:146,y:146,zoom:4},anchor={x:650,y:220},source=toWorld(anchor,camera,world,screen);
    const next=zoomAt(camera,8,anchor,world,screen),actual=toScreen(source,next,world,screen);
    expect(actual.x).toBeCloseTo(anchor.x,9);expect(actual.y).toBeCloseTo(anchor.y,9);
    expect(toWorld(actual,next,world,screen)).toEqual(source);
  });
  it('never pans beyond data boundaries or zooms below the overview',()=>{
    const overview=clampCamera({x:-200,y:900,zoom:.1},world,screen);
    expect(overview).toEqual({x:146.5,y:146.5,zoom:1});
    const zoomed=clampCamera({x:-200,y:900,zoom:4},world,screen),top=toWorld({x:0,y:0},zoomed,world,screen),bottom=toWorld({x:screen.width,y:screen.height},zoomed,world,screen);
    expect(top.x).toBeGreaterThanOrEqual(0);expect(bottom.y).toBeLessThanOrEqual(world.height);
  });
  it('keeps every observed position inside progressive clues, including grid crossings and field edges',()=>{
    for(const points of [[{x:146,y:148},{x:147,y:139}],[{x:2,y:5},{x:8,y:12}]]){
      const broad=clueRegion(points,world,1),narrow=clueRegion(points,world,2);
      expect(narrow.width).toBeLessThan(broad.width);
      for(const box of [broad,narrow])for(const p of points){
        expect(p.x).toBeGreaterThanOrEqual(box.x);expect(p.x).toBeLessThanOrEqual(box.x+box.width);
        expect(p.y).toBeGreaterThanOrEqual(box.y);expect(p.y).toBeLessThanOrEqual(box.y+box.height);
      }
    }
  });
});
describe('following a source across independent observations',()=>{
  const expected=[{epoch:'a',x:120,y:140},{epoch:'b',x:121,y:137},{epoch:'c',x:122,y:133}];
  it('requires three different observation IDs and the correct source in all three',()=>{
    expect(verifyTrail(expected,expected,4)).toBe(true);
    expect(verifyTrail(expected.slice(0,1),expected,4)).toBe(false);
    expect(verifyTrail([expected[0],expected[0],expected[0]],expected,4)).toBe(false);
    expect(verifyTrail([{...expected[0],x:50},expected[1],expected[2]],expected,4)).toBe(false);
    expect(verifyTrail(expected.map(p=>({...p,x:p.x+60})),expected,4)).toBe(false);
  });
});
