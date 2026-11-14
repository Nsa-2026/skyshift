import test from 'node:test';import assert from 'node:assert/strict';
import {difference,rgba,limits} from '../src/blink/pixels.mjs';
test('missing pixels survive subtraction rather than becoming sources',()=>{const d=difference(new Float32Array([2,NaN,3]),new Float32Array([1,4,NaN]));assert.equal(d[0],1);assert.ok(Number.isNaN(d[1]));assert.ok(Number.isNaN(d[2]));assert.deepEqual(Array.from(rgba(d,0,1).slice(4,8)),[10,18,29,255])});
test('one shared stretch preserves a source brightness change',()=>{const [lo,hi]=limits([new Float32Array([0,1,2]),new Float32Array([0,1,4])]);assert.equal(lo,0);assert.ok(hi>=2);const values=rgba(new Float32Array([1,2]),lo,hi);assert.ok(values[4]>values[0])});
