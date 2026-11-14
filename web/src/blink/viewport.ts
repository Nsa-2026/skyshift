export function frameViewport(nx: number, ny: number, width: number, height: number, zoom: number, centre: {
    u: number;
    v: number;
}, cover = false) {
    const z = Math.max(1, zoom);
    const scale = (cover ? Math.max(width / nx, height / ny) : Math.min(width / nx, height / ny)) * z;
    const vw = Math.min(nx, width / scale), vh = Math.min(ny, height / scale);
    const u0 = Math.min(Math.max(centre.u - vw / 2, 0), nx - vw), v0 = Math.min(Math.max(centre.v - vh / 2, 0), ny - vh);
    return { u0, v0, vw, vh, s: scale, ox: (width - vw * scale) / 2, oy: (height - vh * scale) / 2 };
}
