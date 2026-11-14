import test from 'node:test';
import assert from 'node:assert/strict';
import {decodeFrame,filterEpochs,combineFrames,staticFile,epochDate} from './model.mjs';
test('decodes little-endian physical values and masks only raw zero',()=>{
  const b=new ArrayBuffer(6),v=new DataView(b);[0,1,513].forEach((x,i)=>v.setUint16(i*2,x,true));
  const f=decodeFrame(b,[3,1],2,-2);assert.ok(Number.isNaN(f[0]));assert.equal(f[1],0);assert.equal(f[2],1024);
  assert.throws(()=>decodeFrame(b,[2,2],1,0),/byte length/);
});
test('never blinks different detectors and sorts timestamps',()=>{
  const e=filterEpochs({clock:'months',center:{ra:0,dec:0},size:[1,1],epochs:[{det:2,mjd_mid:5},{det:1,mjd_mid:1},{det:2,mjd_mid:2}]},2);
  assert.deepEqual(e.map(x=>x.mjd_mid),[2,5]);
});
test('subtract and sum retain missing-data semantics and valid physical zero',()=>{
  const f=[new Float32Array([0,NaN,2,NaN]),new Float32Array([3,4,NaN,NaN])];
  assert.deepEqual(Array.from(combineFrames(f,1,'diff')),[3,NaN,NaN,NaN]);
  assert.deepEqual(Array.from(combineFrames(f,0,'sum')),[3,4,2,NaN]);
});
test('selects a static twin by both field and clock',()=>{
  const idx={frames:[{clock:'hours',fov:20,file:'hours.json'},{clock:'months',fov:5,file:'months.json'}]};
  assert.equal(staticFile(idx,'months',5),'months.json');assert.throws(()=>staticFile(idx,'months',15),/No static/);
});
test('MJD uses UTC exposure time',()=>assert.equal(epochDate(40587),'1970-01-01 00:00 UTC'));
