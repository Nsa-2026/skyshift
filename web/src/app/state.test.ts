import { expect, it } from 'vitest';
import { readView, writeView } from './state';

it.each(['?ra=&dec=0', '?ra=1&dec= ', '?ra=-1&dec=0', '?ra=360&dec=0', '?ra=1&dec=90.01', '?ra=1&dec=-91', '?ra=NaN&dec=0', '?ra=1&dec=Infinity'])('rejects invalid shared sky coordinates: %s', search => {
  expect(readView(search)).toBeNull();
});

it('keeps valid polar positions and featured links usable', () => {
  expect(readView('?ra=359.999&dec=-90')).toMatchObject({ ra:359.999, dec:-90 });
  expect(readView('?t=barnard')).toMatchObject({ slug:'barnard' });
});

it('shares a valid link when right ascension rounds through 360 degrees', () => {
  const previous=location.href;
  try {
    writeView({ra:359.999999,dec:20,slug:null,name:null,clock:'months',fov:5,det:2},true);
    expect(readView()).toMatchObject({ra:0,dec:20});
  } finally { history.replaceState(null,'',previous); }
});
