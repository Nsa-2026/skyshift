import landPolygons from './data/earth-land.json';

/** Rasterize the original vectors on the main thread, preserving exact coastline coverage. */
export function createEarthMask(): Uint8ClampedArray {
  const width = 1024, height = 512;
  const maskCanvas = document.createElement('canvas'); maskCanvas.width = width; maskCanvas.height = height;
  const c = maskCanvas.getContext('2d')!;
  c.fillStyle = '#fff';
  for (const polygon of landPolygons) {
    c.beginPath();
    for (const ring of polygon) {
      ring.forEach(([lon, lat], i) => {
        const x = (lon + 180) / 360 * width, y = (90 - lat) / 180 * height;
        if (i) c.lineTo(x, y); else c.moveTo(x, y);
      });
      c.closePath();
    }
    c.fill('evenodd');
  }
  return c.getImageData(0, 0, width, height).data;
}
