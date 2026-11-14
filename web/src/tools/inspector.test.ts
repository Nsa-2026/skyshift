import { describe, expect, it } from 'vitest';
import { requestUrl } from './inspector';
const query = {ra:269.4,dec:4.8,clock:'months',det:2,fov:5,r:3,mjd:61000,name:"Barnard's Star"};
describe('API inspector request boundaries',()=>{
  it('keeps endpoint parameters and units separate',()=>{
    expect(requestUrl('frames',query)).toBe('/api/frames?ra=269.4&dec=4.8&clock=months&det=2&fov=5');
    expect(requestUrl('known',query)).toBe('/api/known?ra=269.4&dec=4.8&r=3&mjd=61000');
    expect(requestUrl('gaia',query)).not.toContain('mjd');
    expect(requestUrl('health',query)).toBe('/api/health');
    expect(new URL(requestUrl('resolve',query),'http://localhost').searchParams.get('q')).toBe(query.name);
  });
  it('rejects invalid coordinates and catalog radius before requesting',()=>{
    expect(()=>requestUrl('frames',{...query,ra:400})).toThrow('RA');
    expect(()=>requestUrl('frames',{...query,dec:NaN})).toThrow('Dec');
    expect(()=>requestUrl('gaia',{...query,r:0})).toThrow('Radius');
    expect(()=>requestUrl('known',{...query,mjd:Infinity})).toThrow('MJD');
  });
});
