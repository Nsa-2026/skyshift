import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {catalogueAnswered,catalogueUrl,fetchCatalogue,forgetCatalogues} from './catalogues';

const at={ra:331.09668,dec:-56.80067};
beforeEach(()=>forgetCatalogues());
afterEach(()=>vi.unstubAllGlobals());

it('asks each field\'s catalogue once and shares the answer, at the URL the buttons always used',async()=>{
  let release!:()=>void;const held=new Promise<void>(r=>{release=r});
  const fetch=vi.fn(async()=>{await held;return {ok:true,status:200,json:async()=>({gaia:[]})}});
  vi.stubGlobal('fetch',fetch);
  expect(catalogueUrl('gaia',at)).toBe('/api/gaia?ra=331.09668&dec=-56.80067&r=3');
  expect(catalogueUrl('catwise',at)).toBe('/api/enrichment?ra=331.09668&dec=-56.80067&kind=catwise');
  const first=fetchCatalogue('gaia',at),second=fetchCatalogue('gaia',at);
  expect(catalogueAnswered('gaia',at)).toBe(false);
  release();
  expect(await first).toEqual({gaia:[]});expect(await second).toEqual({gaia:[]});
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(catalogueAnswered('gaia',at)).toBe(true);
});

it('a caller that stops waiting does not cancel the answer for the others',async()=>{
  let release!:()=>void;const held=new Promise<void>(r=>{release=r});
  vi.stubGlobal('fetch',vi.fn(async()=>{await held;return {ok:true,status:200,json:async()=>({source:'x',sources:[]})}}));
  const leaving=new AbortController();
  const gone=fetchCatalogue('catwise',at,leaving.signal),staying=fetchCatalogue('catwise',at);
  leaving.abort();
  await expect(gone).rejects.toMatchObject({name:'AbortError'});
  release();
  expect(await staying).toEqual({source:'x',sources:[]});
});

it('never keeps a failure or a synthetic example',async()=>{
  const fetch=vi.fn(async()=>({ok:true,status:200,json:async()=>({gaia:[],synthetic:true})}));
  vi.stubGlobal('fetch',fetch);
  await expect(fetchCatalogue('gaia',at)).rejects.toThrow(/Synthetic examples/);
  await expect(fetchCatalogue('gaia',at)).rejects.toThrow(/Synthetic examples/);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(catalogueAnswered('gaia',at)).toBe(false);
});
