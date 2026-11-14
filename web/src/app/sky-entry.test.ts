import {expect,it} from 'vitest';
import {observationForSky} from './sky-entry';
import {targetBySlug,viewForTarget} from './state';

it('opens a selected featured science target from prepared observations',()=>{
  const target=targetBySlug('barnard')!;
  expect(observationForSky(target)).toEqual(viewForTarget(target));
  const asteroid=targetBySlug('elpis')!;
  expect(observationForSky(asteroid)).toEqual(viewForTarget(asteroid));
});
it('keeps the sky position, clock and detector while choosing an observation field in arcminutes',()=>{
  expect(observationForSky({ra:123.456,dec:-23.456,clock:'hours',det:6})).toMatchObject({ra:123.456,dec:-23.456,clock:'hours',det:6,fov:20,slug:null});
  expect(observationForSky({ra:123.456,dec:-23.456})).toMatchObject({ra:123.456,dec:-23.456,clock:'months',fov:5,det:2});
});
