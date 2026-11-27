"""Stamps (27 Nov, agent "comet-stamps"): small patches of a photo around listed positions, cut by the dots worker
while the photo is in memory, so that faint known comets (306P in D3: S/N 1–3 per photo) can be stacked along their
path later without reading a photo again (scripts/rerun/stack_stamps.py).

- The list: stamplist.py's rows for the photo (<STAMP_LIST_DIR>/<kind>/<period>.parquet, kinds STAMP_LIST_KINDS),
  geocentric at the index's photo time. topocentric() moves each one to the photo's MJD-AVG and to SPHEREx (IMAGE header
  X_SC.., VX_SC..: geocentric equatorial km and km/s; the Earth's centre when they are missing, `observer` geocentre).
- The stamp: (2 half + 1)² px centred on the rounded predicted pixel, clipped to the photo (a stamp at the edge is
  smaller; x0, y0 say where it is), when its centre is within half px of the photo. Planes (PLANES): IMAGE (MJy/sr; the
  worker's dark-hole pixels NaN), VARIANCE, FLAGS, ZODI, BKG (the worker's background map, the level it subtracts),
  RESID (the extended pass's point-free residual: IMAGE − background − every dot's ePSF − the glow model; NaN plane
  when that pass did not run), BAD (the worker's bad-pixel mask, 1 = masked), WAVE and BANDWIDTH (µm, WCS-WAVE).
- Storage: <folder>_stamps/<obs_id>D<det>.parquet, one row per stamp, planes as byte-shuffled binary columns (zstd).
  IMAGE, ZODI, BKG, RESID are int32 × q_step (q_step = the stamp's median √VARIANCE / STAMP_QUANT: rounding adds
  0.018σ rms); VARIANCE as float16 √VARIANCE / q_step (noise relative 4.9e-4); WAVE, BANDWIDTH int32 × STAMP_WAVE_STEP_UM; FLAGS int32, BAD uint8
  exact. read_stamps() gives the arrays back. Column STAMP_META_KEY (the same JSON in every row, stored once) has the
  photo's full WCS header (TAN-SIP) and SPHEREx's position: with x0, y0 every pixel goes on the sky exactly.
- Failure-safe: attach() never raises; an error goes to the dot file's footer (`stamps.error`) and the dots are the same.
"""

from __future__ import annotations

import io
import json
import os
import time
from collections import OrderedDict

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .stamplist import AU_KM, LIST_COLUMNS, RAD_ARCSEC, period_of

L = log.get("stamps")
STAMP_META_KEY = "skyshift_stamps"
INT_NAN = np.iinfo(np.int32).min
# plane -> encoding: q (int32 × q_step), s16 (float16 √value / q_step, squared back), i32, u8, w (int32 × STAMP_WAVE_STEP_UM)
PLANES = {"image": "q", "variance": "s16", "flags": "i32", "zodi": "q", "bkg": "q", "resid": "q", "bad": "u8",
          "wave": "w", "bandwidth": "w"}
ROW_COLUMNS = {
    "kind": "str", "name": "str", "obs_id": "str", "detector": "int8", "mjd": "float64",
    "ra_pred": "float64", "dec_pred": "float64",          # at MJD-AVG, seen from SPHEREx (deg)
    "x_pred": "float64", "y_pred": "float64",            # the photo's 0-based pixel of (ra_pred, dec_pred)
    "sigma_arcsec": "float32",                           # the prediction's uncertainty (NaN: unknown)
    "rate_ra_arcsec_h": "float32", "rate_dec_arcsec_h": "float32",  # seen from SPHEREx, dRA·cos dec
    "rate_arcsec_h": "float32", "pa_deg": "float32",     # speed and direction (north through east)
    "delta_au": "float64", "r_au": "float32", "phase_deg": "float32", "mag_pred": "float32",
    "parallax_arcsec": "float32",                        # how far SPHEREx's place moved the prediction
    "observer": "str",                                   # spacecraft | geocentre
    "half_px": "int16", "x0": "int16", "y0": "int16", "nx": "int16", "ny": "int16",
    "cd1_1": "float64", "cd1_2": "float64", "cd2_1": "float64", "cd2_2": "float64",  # local Jacobian, deg/px
    "q_step": "float32", "n_bad": "int32", "list_row": "int32",
}


# ---------------------------------------------------------------- the list

_LISTS: "OrderedDict[tuple, dict]" = OrderedDict()


def list_root(store) -> str | None:
    """The stamp list folder as a local path or a path relative to the store root; None when it isn't there."""
    d = C.STAMP_LIST_DIR
    if not d:
        return None
    if os.path.isabs(d):
        return d if os.path.isdir(d) else None
    return d if store.exists(d) else None


def _read_list(store, root: str, kind: str, period: str) -> pd.DataFrame | None:
    rel = f"{root}/{kind}/{period}.parquet"
    if os.path.isabs(root):
        return pd.read_parquet(rel) if os.path.exists(rel) else None
    return store.read_parquet(rel) if store.exists(rel) else None


def photo_rows(store, obs_id: str, detector: int) -> tuple[pd.DataFrame | None, dict]:
    """The stamp list's rows of one photo (every kind), the list's info; (None, info) when there is no list."""
    root = list_root(store)
    if root is None:
        return None, {"list": "none"}
    period = period_of(obs_id)
    parts, info = [], {"list": root, "period": period, "kinds": {}}
    for kind in C.STAMP_LIST_KINDS:
        key = (store.root, root, kind, period)
        if key not in _LISTS:
            tab = _read_list(store, root, kind, period)
            idx = {} if tab is None or tab.empty else {k: v for k, v in tab.groupby(["obs_id", "detector"]).indices.items()}
            _LISTS[key] = {"tab": tab, "idx": idx}
            while len(_LISTS) > 2 * max(1, len(C.STAMP_LIST_KINDS)):
                _LISTS.popitem(last=False)
        e = _LISTS[key]
        if e["tab"] is None:
            info["kinds"][kind] = "missing"
            continue
        rows = e["idx"].get((str(obs_id), int(detector)))
        info["kinds"][kind] = 0 if rows is None else int(len(rows))
        if rows is not None:
            parts.append(e["tab"].iloc[rows].assign(list_row=rows.astype(np.int32)))
    if not parts:
        return pd.DataFrame(columns=[*LIST_COLUMNS, "list_row"]), info
    return pd.concat(parts, ignore_index=True), info


# ---------------------------------------------------------------- positions

def _unit(ra, dec) -> np.ndarray:
    ra, dec = np.radians(ra), np.radians(dec)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def _east_north(ra, dec) -> tuple[np.ndarray, np.ndarray]:
    ra, dec = np.radians(ra), np.radians(dec)
    e = np.stack([-np.sin(ra), np.cos(ra), np.zeros_like(ra)], axis=-1)
    n = np.stack([-np.sin(dec) * np.cos(ra), -np.sin(dec) * np.sin(ra), np.cos(dec)], axis=-1)
    return e, n


def topocentric(ra, dec, dra, ddec, delta_au, mjd_ref, mjd, sc_pos_km=None, sc_vel_kms=None):
    """Geocentric rows (deg, ″/h, AU at mjd_ref) -> (ra, dec, rate_ra, rate_dec, parallax_arcsec) at mjd seen from
    SPHEREx at sc_pos_km (geocentric equatorial km) moving at sc_vel_kms (km/s). The comet's tangential motion is its
    rates × Δ (its radial motion changes nothing to first order over the seconds between mjd_ref and mjd). A row
    with no delta (a fixed target) only moves by its rates; no SPHEREx position: geocentric."""
    ra, dec = np.asarray(ra, float), np.asarray(dec, float)
    dra, ddec = np.asarray(dra, float), np.asarray(ddec, float)
    d = np.asarray(delta_au, float)
    dt = (float(mjd) - np.asarray(mjd_ref, float))  # days
    to_rad_day = 24.0 / RAD_ARCSEC
    e, n = _east_north(ra, dec)
    fixed = ~np.isfinite(d) | (d <= 0)
    dd = np.where(fixed, 1.0, d)
    rho = dd[:, None] * _unit(ra, dec)                               # AU
    vt = dd[:, None] * (dra[:, None] * e + ddec[:, None] * n) * to_rad_day  # AU/day, tangential
    rho = rho + vt * dt[:, None]
    have = sc_pos_km is not None and np.all(np.isfinite(sc_pos_km))
    s = np.asarray(sc_pos_km, float) / AU_KM if have else np.zeros(3)
    v = np.asarray(sc_vel_kms, float) * 86400.0 / AU_KM if have and sc_vel_kms is not None and \
        np.all(np.isfinite(sc_vel_kms)) else np.zeros(3)
    topo = rho - np.where(fixed[:, None], 0.0, 1.0) * s[None, :]
    vt2 = vt - np.where(fixed[:, None], 0.0, 1.0) * v[None, :]
    r = np.linalg.norm(topo, axis=1)
    u = topo / r[:, None]
    ra2 = np.degrees(np.arctan2(u[:, 1], u[:, 0])) % 360.0
    dec2 = np.degrees(np.arcsin(np.clip(u[:, 2], -1, 1)))
    e2, n2 = _east_north(ra2, dec2)
    rate_ra = np.einsum("ij,ij->i", vt2, e2) / r / to_rad_day
    rate_dec = np.einsum("ij,ij->i", vt2, n2) / r / to_rad_day
    # the parallax alone (no time step): the angle between the geocentric and topocentric directions at mjd
    g = rho / np.linalg.norm(rho, axis=1)[:, None]
    par = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", g, u), -1, 1))) * 3600.0
    return ra2, dec2, rate_ra, rate_dec, np.where(fixed, 0.0, par)


def spacecraft(header) -> tuple[np.ndarray | None, np.ndarray | None]:
    def num(k):
        try:
            v = float(header.get(k))
            return v if np.isfinite(v) else np.nan
        except (TypeError, ValueError):
            return np.nan
    p = np.array([num(k) for k in ("X_SC", "Y_SC", "Z_SC")])
    v = np.array([num(k) for k in ("VX_SC", "VY_SC", "VZ_SC")])
    return (p if np.all(np.isfinite(p)) else None), (v if np.all(np.isfinite(v)) else None)


# ---------------------------------------------------------------- encoding

def _shuffle(a: np.ndarray) -> bytes:
    a = np.ascontiguousarray(a)
    if a.dtype.itemsize == 1:
        return a.tobytes()
    return np.ascontiguousarray(a.reshape(-1).view(np.uint8).reshape(-1, a.dtype.itemsize).T).tobytes()


def _unshuffle(b: bytes, dtype, shape) -> np.ndarray:
    dt = np.dtype(dtype)
    u = np.frombuffer(b, np.uint8)
    if dt.itemsize > 1:
        u = np.ascontiguousarray(u.reshape(dt.itemsize, -1).T)
    return u.view(dt).reshape(shape)


def encode(a: np.ndarray, how: str, q_step: float) -> bytes:
    if how == "q":
        v = np.asarray(a, np.float64) / q_step
        ok = np.isfinite(v)
        out = np.full(v.shape, INT_NAN, np.int32)
        out[ok] = np.clip(np.rint(v[ok]), INT_NAN + 1, np.iinfo(np.int32).max)
        return _shuffle(out)
    if how == "w":
        v = np.asarray(a, np.float64) / C.STAMP_WAVE_STEP_UM
        ok = np.isfinite(v)
        out = np.full(v.shape, INT_NAN, np.int32)
        out[ok] = np.rint(v[ok])
        return _shuffle(out)
    if how == "s16":  # the noise √VARIANCE in q_step (~16 for a typical pixel; float16 holds to 65,504: a saturated
        # star's core 2,000× the median noise still fits); negative or NaN -> NaN
        v = np.asarray(a, np.float64)
        sd = np.sqrt(np.where(v >= 0, v, np.nan)) / q_step
        return _shuffle(np.asarray(np.clip(sd, 0, 65504.0), np.float16))
    if how == "i32":
        return _shuffle(np.asarray(a, np.int32))
    if how == "u8":
        return _shuffle(np.asarray(a, np.uint8))
    raise ValueError(how)


def decode(b: bytes, how: str, shape, q_step: float) -> np.ndarray:
    if how in ("q", "w"):
        i = _unshuffle(b, np.int32, shape)
        step = q_step if how == "q" else C.STAMP_WAVE_STEP_UM
        return np.where(i == INT_NAN, np.nan, i.astype(np.float64) * step).astype(np.float32)
    if how == "s16":
        return ((_unshuffle(b, np.float16, shape).astype(np.float64) * q_step) ** 2).astype(np.float32)
    if how == "i32":
        return _unshuffle(b, np.int32, shape)
    if how == "u8":
        return _unshuffle(b, np.uint8, shape)
    raise ValueError(how)


# ---------------------------------------------------------------- cutting

def _jacobian(wcs, x: float, y: float) -> np.ndarray:
    """d(ra cos dec, dec)/d(x, y) in deg/px at a pixel (central differences through the full WCS)."""
    from skyshift_core import pixel_to_sky
    ra, dec = pixel_to_sky(wcs, [x - 0.5, x + 0.5, x, x], [y, y, y - 0.5, y + 0.5])
    c = np.cos(np.radians(dec[2:].mean()))
    dra = (ra[[1, 3]] - ra[[0, 2]] + 180.0) % 360.0 - 180.0
    return np.array([[dra[0] * c, dra[1] * c], [dec[1] - dec[0], dec[3] - dec[2]]])


def cut(l2, ctx, rows: pd.DataFrame, resid: np.ndarray | None = None) -> tuple[list[dict], dict]:
    """One dict per stamp (ROW_COLUMNS + the planes' bytes) for the list rows of this photo, and counts."""
    from skyshift_core import pixel_to_sky

    from .sky import angsep

    info = {"rows": int(len(rows)), "off_photo": 0, "cut": 0}
    if rows is None or rows.empty:
        return [], info
    rows = rows.iloc[:C.STAMP_MAX_PER_PHOTO]
    info["capped"] = int(len(rows) < info["rows"])
    sc_p, sc_v = spacecraft(l2.header)
    mjd = float(l2.mjd_avg)
    ra, dec, rra, rdec, par = topocentric(rows["ra"], rows["dec"], rows["dra_arcsec_h"], rows["ddec_arcsec_h"],
                                          rows["delta_au"], rows["mjd_ref"], mjd, sc_p, sc_v)
    ny_img, nx_img = np.shape(l2.data)
    half = rows["half_px"].to_numpy(int)
    # only places near the photo go through the SIP inverse (it diverges far outside the frame)
    cra, cdec = pixel_to_sky(l2.wcs, [(nx_img - 1) / 2.0], [(ny_img - 1) / 2.0])
    near = angsep(cra[0], cdec[0], ra, dec) * 3600.0 <= (np.hypot(nx_img, ny_img) / 2.0 + half) * C.PIXEL_ARCSEC * 1.2
    x, y = np.full(len(ra), np.nan), np.full(len(ra), np.nan)
    if near.any():
        xx, yy = l2.wcs.celestial.all_world2pix(ra[near], dec[near], 0, quiet=True)
        x[near], y[near] = xx, yy
    data = np.asarray(l2.data)
    var = np.asarray(l2.variance) if l2.variance is not None else None
    zodi = np.asarray(l2.zodi) if getattr(l2, "zodi", None) is not None else None
    out = []
    for k in range(len(rows)):
        h = int(half[k])
        if not (np.isfinite(x[k]) and np.isfinite(y[k])) or x[k] < -h or y[k] < -h or x[k] > nx_img - 1 + h \
                or y[k] > ny_img - 1 + h:
            info["off_photo"] += 1
            continue
        ix, iy = int(np.rint(x[k])), int(np.rint(y[k]))
        x0, x1 = max(0, ix - h), min(nx_img, ix + h + 1)
        y0, y1 = max(0, iy - h), min(ny_img, iy + h + 1)
        if x1 <= x0 or y1 <= y0:
            info["off_photo"] += 1
            continue
        sl = (slice(y0, y1), slice(x0, x1))
        shape = (y1 - y0, x1 - x0)
        v = var[sl] if var is not None else np.full(shape, np.nan, np.float32)
        sd = np.sqrt(np.where(np.isfinite(v) & (v > 0), v, np.nan))
        q = float(np.nanmedian(sd)) / C.STAMP_QUANT if np.isfinite(sd).any() else 1e-4
        if not (np.isfinite(q) and q > 0):
            q = 1e-4
        yy, xx = np.mgrid[y0:y1, x0:x1]
        wave = l2.wave.wavelength(xx, yy) if l2.wave is not None else np.full(shape, np.nan)
        bw = l2.wave.bandwidth(xx, yy) if l2.wave is not None else np.full(shape, np.nan)
        planes = {"image": data[sl], "variance": v, "flags": np.asarray(l2.flags)[sl],
                  "zodi": zodi[sl] if zodi is not None else np.full(shape, np.nan),
                  "bkg": ctx.bkg_level[sl], "resid": resid[sl] if resid is not None else np.full(shape, np.nan),
                  "bad": ctx.bad[sl], "wave": wave, "bandwidth": bw}
        j = _jacobian(l2.wcs, float(x[k]), float(y[k]))
        r = rows.iloc[k]
        rate = float(np.hypot(rra[k], rdec[k]))
        out.append({
            "kind": str(r["kind"]), "name": str(r["name"]), "obs_id": str(l2.obsid or r["obs_id"]),
            "detector": int(l2.detector), "mjd": mjd, "ra_pred": float(ra[k]), "dec_pred": float(dec[k]),
            "x_pred": float(x[k]), "y_pred": float(y[k]), "sigma_arcsec": float(r["sigma_arcsec"]),
            "rate_ra_arcsec_h": float(rra[k]), "rate_dec_arcsec_h": float(rdec[k]), "rate_arcsec_h": rate,
            "pa_deg": float(np.degrees(np.arctan2(rra[k], rdec[k])) % 360.0),
            "delta_au": float(r["delta_au"]), "r_au": float(r["r_au"]), "phase_deg": float(r["phase_deg"]),
            "mag_pred": float(r["mag_pred"]), "parallax_arcsec": float(par[k]),
            "observer": "spacecraft" if sc_p is not None else "geocentre",
            "half_px": h, "x0": x0, "y0": y0, "nx": shape[1], "ny": shape[0],
            "cd1_1": j[0, 0], "cd1_2": j[0, 1], "cd2_1": j[1, 0], "cd2_2": j[1, 1],
            "q_step": q, "n_bad": int(np.asarray(ctx.bad[sl]).sum()), "list_row": int(r.get("list_row", -1)),
            **{p: encode(a, PLANES[p], q) for p, a in planes.items()},
        })
        info["cut"] += 1
    return out, info


def table(stamps: list[dict], meta: dict):
    import pyarrow as pa

    cols = {}
    for c, t in ROW_COLUMNS.items():
        vals = [s[c] for s in stamps]
        cols[c] = pa.array(vals, pa.string() if t == "str" else pa.from_numpy_dtype(np.dtype(t)))
    for p in PLANES:
        cols[p] = pa.array([s[p] for s in stamps], pa.binary())
    # the photo's metadata (its WCS header, SPHEREx's place, the list) in every row: dictionary-encoded, stored once
    cols[STAMP_META_KEY] = pa.array([json.dumps(meta, default=_json)] * len(stamps), pa.string())
    return pa.table(cols)


def _json(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v) if np.isfinite(v) else None
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def parquet_bytes(t) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq
    buf = io.BytesIO()
    floats = [f.name for f in t.schema if pa.types.is_floating(f.type)]
    pq.write_table(t, buf, compression="zstd", compression_level=C.STAMP_ZSTD_LEVEL,
                   use_dictionary=["kind", "name", "obs_id", "observer", STAMP_META_KEY], use_byte_stream_split=floats or False,
                   write_statistics=[c for c in ROW_COLUMNS], store_schema=False)  # no copies of the planes or the footer
    return buf.getvalue()


def stamps_rel(obs_id: str, det: int, folder: str) -> str:
    """<folder>_stamps/<obs_id>D<det>.parquet"""
    return f"{folder}_stamps/{obs_id}D{int(det)}.parquet"


def photo_meta(l2, info: dict) -> dict:
    sc_p, sc_v = spacecraft(l2.header)
    try:
        wcs_header = l2.wcs.to_header(relax=True).tostring(sep="\n")
    except Exception:  # noqa: BLE001
        wcs_header = ""
    return {"version": 1, "obs_id": str(l2.obsid), "detector": int(l2.detector), "mjd_avg": float(l2.mjd_avg),
            "mjd_beg": l2.mjd_beg, "mjd_end": l2.mjd_end, "sc_pos_km": sc_p, "sc_vel_kms": sc_v,
            "wcs_header": wcs_header, "origin": list(getattr(l2, "origin", (0, 0))),
            "shape": list(np.shape(l2.data)), "planes": PLANES, "quant": C.STAMP_QUANT,
            "wave_step_um": C.STAMP_WAVE_STEP_UM, "list": info}


def attach(store, pending, l2, ctx, extras: dict, obs_id: str, detector: int, folder: str, st: dict) -> None:
    """The dots worker's hook (dots._measure): the photo's stamps into pending.stamps / stamps_rel and the footer key
    `stamps` into pending.meta. Never raises; a photo with no list row gets no file."""
    t0 = time.process_time()
    try:
        rows, info = photo_rows(store, obs_id, detector)
        if rows is None:
            return
        stamps, ci = cut(l2, ctx, rows, extras.get("point_free"))
        info.update(ci)
        if stamps:
            pending.stamps = table(stamps, photo_meta(l2, info))
            pending.stamps_rel = stamps_rel(obs_id, detector, folder)
        info["file"] = f"{folder}_stamps" if stamps else None
        pending.meta["stamps"] = info
        st["n_stamps"] = int(len(stamps))
    except Exception as e:  # noqa: BLE001 - never costs the photo its dots
        pending.stamps = None
        pending.meta["stamps"] = {"error": f"{type(e).__name__}: {e}"[:300]}
        st["stamps_error"] = pending.meta["stamps"]["error"]
    st["stamps_s"] = round(time.process_time() - t0, 3)


def write(store, pending, st: dict) -> None:
    """dots._finish: write the stamp file (before the dot file); a failure is noted in the dot file's footer."""
    if getattr(pending, "stamps", None) is None:
        return
    try:
        data = parquet_bytes(pending.stamps)
        store.write_bytes(pending.stamps_rel, data)
        st["stamps_bytes"] = len(data)
    except Exception as e:  # noqa: BLE001
        err = f"write: {type(e).__name__}: {e}"[:300]
        pending.meta.setdefault("stamps", {})["error"] = err
        st["stamps_error"] = err


# ---------------------------------------------------------------- reading

def read_stamps(path_or_bytes, planes: tuple | None = None, columns: list | None = None) -> tuple[pd.DataFrame, dict]:
    """A stamp file -> (DataFrame with ROW_COLUMNS and one 2-D float32/int array column per plane, the footer meta).
    planes: which planes to decode (default all; () for metadata only)."""
    import pyarrow.parquet as pq

    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    want = list(PLANES) if planes is None else list(planes)
    cols = (columns or list(ROW_COLUMNS))
    need = list(dict.fromkeys([*cols, "nx", "ny", "q_step", *want]))
    t = pq.read_table(src, columns=[*need, STAMP_META_KEY])
    meta = json.loads(t.column(STAMP_META_KEY)[0].as_py()) if len(t) else {}
    df = t.drop([*want, STAMP_META_KEY]).to_pandas()
    for p in want:
        raw = t.column(p).to_pylist()
        df[p] = [decode(b, PLANES[p], (int(ny), int(nx)), float(q)) for b, ny, nx, q in
                 zip(raw, df["ny"], df["nx"], df["q_step"])]
    return df, meta


def stamp_wcs(meta: dict, row) -> "object":
    """The astropy WCS of one stamp (the photo's TAN-SIP shifted to the stamp's origin)."""
    from astropy.io import fits
    from astropy.wcs import WCS
    w = WCS(fits.Header.fromstring(meta["wcs_header"], sep="\n"), relax=True)
    return w[int(row["y0"]):int(row["y0"]) + int(row["ny"]), int(row["x0"]):int(row["x0"]) + int(row["nx"])]
