export function decodeFrame(buffer, size, scale, zero) {
  const [nx, ny] = size;
  if (!Number.isInteger(nx) || !Number.isInteger(ny) || nx < 1 || ny < 1 || nx > 300 || ny > 300) throw Error('Invalid frame dimensions (maximum 300 × 300)');
  if (buffer.byteLength !== nx * ny * 2) throw Error(`Frame byte length: expected ${nx * ny * 2}, received ${buffer.byteLength}`);
  if (!Number.isFinite(scale) || !Number.isFinite(zero)) throw Error('Missing or invalid scale / zero');
  const view = new DataView(buffer), values = new Float32Array(nx * ny);
  for (let i = 0; i < values.length; i++) {
    const raw = view.getUint16(i * 2, true);
    values[i] = raw === 0 ? NaN : raw * scale + zero;
  }
  return values;
}
export function filterEpochs(meta, det) {
  if (!Array.isArray(meta.epochs) || !Array.isArray(meta.size) || !meta.center || !['months', 'hours'].includes(meta.clock)) throw Error('Response does not match the frames contract');
  return meta.epochs.filter(e => e.det === Number(det)).sort((a, b) => a.mjd_mid - b.mjd_mid);
}
export function combineFrames(frames, index, mode) {
  if (!frames.length) throw Error('No frames loaded');
  if (mode === 'blink') return frames[index];
  const out = new Float32Array(frames[0].length);
  for (let p = 0; p < out.length; p++) {
    if (mode === 'diff') out[p] = frames[index][p] - frames[0][p];
    else {
      let sum = 0, count = 0;
      for (const f of frames) if (Number.isFinite(f[p])) { sum += f[p]; count++; }
      out[p] = count ? sum : NaN;
    }
  }
  return out;
}
export function limits(frames, mode = 'blink') {
  const sample = [];
  for (const f of frames) for (let i = 0; i < f.length; i += Math.max(1, Math.floor(f.length / 5000))) if (Number.isFinite(f[i])) sample.push(f[i]);
  if (!sample.length) return [0, 1];
  sample.sort((a, b) => a - b);
  if (mode === 'diff') {
    const abs = sample.map(Math.abs).sort((a, b) => a - b);
    const hi = abs[Math.floor((abs.length - 1) * .99)] || 1;
    return [-hi, hi];
  }
  const lo = sample[Math.floor((sample.length - 1) * .02)], hi = sample[Math.floor((sample.length - 1) * .995)];
  return [lo, hi > lo ? hi : lo + 1];
}
export function epochDate(mjd) {
  if (!Number.isFinite(mjd)) return 'Unknown date';
  return new Date((mjd - 40587) * 86400000).toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
}
export function staticFile(index, clock, fov) {
  const item = index?.frames?.find(x => x.clock === clock && Number(x.fov) === Number(fov));
  if (!item) throw Error(`No static ${clock} / ${fov}′ frames in the target index`);
  return item.file;
}
