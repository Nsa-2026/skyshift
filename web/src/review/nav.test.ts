import {expect,it} from 'vitest';
import {lastDiscoverHash,lastDiscoverQuery,rememberDiscover,trackDiscover} from './nav';

it('returns from a candidate to the current filtered list after a history replacement',()=>{
  history.replaceState(null,'','/?t=barnard#/analyze/candidates/kind=mover');
  trackDiscover();
  expect(lastDiscoverQuery()).toBe('kind=mover');
  history.replaceState(null,'','/?t=barnard#/analyze/candidates/kind=mover&grade=A&sort=fewest');
  rememberDiscover();
  history.replaceState(null,'','/?t=barnard#/c/example');
  dispatchEvent(new HashChangeEvent('hashchange'));
  expect(lastDiscoverHash()).toBe('#/analyze/candidates/kind=mover&grade=A&sort=fewest');
  expect(lastDiscoverQuery()).toBe('kind=mover&grade=A&sort=fewest');
});
