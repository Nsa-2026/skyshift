import {describe,it,expect} from 'vitest';
import {positionPixel,targetCrop,cropPick,type ImageGeometry} from './model';

describe('magnified lesson images keep the original sky geometry',()=>{
  const frame:ImageGeometry={size:[146,146],pixel_arcsec:2.05,center:{ra:269.4462,dec:4.769}};
  it('turns a magnified click back into the same sky position',()=>{
    const target={ra:269.445,dec:4.771},crop=targetCrop(target,frame);
    const point=positionPixel(target,frame);
    const clicked=cropPick((point.x-crop.x)/crop.size,(point.y-crop.y)/crop.size,crop,frame);
    expect(clicked.ra).toBeCloseTo(target.ra,9);expect(clicked.dec).toBeCloseTo(target.dec,9);
  });
  it('clips at the image edge without moving the target or wrapping RA incorrectly',()=>{
    const wrapped={...frame,center:{ra:359.999,dec:0}},target={ra:.003,dec:.04};
    const crop=targetCrop(target,wrapped),point=positionPixel(target,wrapped);
    expect(crop.y).toBe(0);expect(crop.x).toBeGreaterThanOrEqual(0);expect(crop.x+crop.size).toBeLessThanOrEqual(146);
    const clicked=cropPick((point.x-crop.x)/crop.size,(point.y-crop.y)/crop.size,crop,wrapped);
    expect(clicked.ra).toBeCloseTo(.003,9);expect(clicked.dec).toBeCloseTo(.04,9);
  });
});
