"""Tiles (tiles.py): the grid, the TileStore, and tiled == untiled on a synthetic root.

The synthetic zone (1° × 1°, 32 photos in 8 sessions over 2 visits, detectors 1 and 4) has 300 stars (250 in Gaia, with
forced photometry), stray single dots, asteroids crossing tile edges, a 2″/yr star sitting on a tile edge (its Gaia
position 20″ away, in the next tile), a forced target with no source on a tile edge, a star on the zone's edge, and a
known asteroid whose 120″ control shift lands in the next tile on another mover (score's control case).
"""

from __future__ import annotations

import io
import json
import math

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skyshift_pipeline import config as C
from skyshift_pipeline import tiles
from skyshift_pipeline.sky import cell_of
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

ZONE = C.Zone("tst", 120.5, 121.5, 15.0, 16.0)
TILE_DEG = 0.25           # 4 × 4 tiles; core edges at RA 120.75, 121.0, 121.25 and Dec 15.25, 15.5, 15.75
PIX = 6.152
VISIT_T0 = (60950.0, 61130.0)
GAIA_EPOCH = 57388.0       # J2016.0
FAST = (120.75, 15.6, 2000.0)  # a 2″/yr star on the RA = 120.75 core edge (ra, dec, pmra mas/yr)
EDGE_STAR = (121.1, 15.0 + 0.6 / 3600)  # 0.6″ inside the zone's south edge


def _photos() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for v, t0 in enumerate(VISIT_T0, start=1):
        for s in range(4):
            session = (v - 1) * 4 + s + 1
            for e in range(2):
                mjd = t0 + s * 0.3 + e * 2.1 / 1440
                obs = f"2025W{v}{s}_1A_000{e}_1"
                c_ra, c_dec = 121.0 + rng.uniform(-0.05, 0.05), 15.5 + rng.uniform(-0.05, 0.05)
                h = 0.8
                cd = math.cos(math.radians(c_dec))
                poly = [(c_ra - h / cd, c_dec - h), (c_ra + h / cd, c_dec - h), (c_ra + h / cd, c_dec + h), (c_ra - h / cd, c_dec + h)]
                s_region = "POLYGON ICRS " + " ".join(f"{a:.6f} {b:.6f}" for a, b in poly)
                for det in (1, 4):
                    rows.append({"obs_id": obs, "collection": "spherex_dr1", "t_min": mjd - 0.0007, "t_max": mjd + 0.0007,
                                 "s_ra": c_ra, "s_dec": c_dec, "s_region": s_region, "size_bytes": 1, "detector": np.int8(det),
                                 "s3_key": "", "mjd": mjd, "url": "", "session": np.int32(session), "visit": np.int16(v)})
    return pd.DataFrame(rows)


def _stars(rng) -> pd.DataFrame:
    n = 300
    st = pd.DataFrame({"ra": rng.uniform(120.45, 121.55, n), "dec": rng.uniform(14.95, 16.05, n),
                       "flux": 10 ** rng.uniform(1.8, 3.8, n), "pmra": rng.normal(0, 15, n), "pmdec": rng.normal(0, 15, n)})
    st.loc[0, ["ra", "dec", "flux", "pmra", "pmdec"]] = [FAST[0], FAST[1], 3000.0, FAST[2], 0.0]
    st.loc[1, ["ra", "dec", "flux", "pmra", "pmdec"]] = [EDGE_STAR[0], EDGE_STAR[1], 2000.0, 0.0, 0.0]
    st["gaia"] = np.arange(n) < 250
    st["sigma"] = np.clip(1.2 * 300 / st["flux"], 0.15, 1.0)  # ″ per axis
    st.loc[1, "sigma"] = 1.0
    return st


def _pos(st: pd.DataFrame, mjd: float) -> tuple[np.ndarray, np.ndarray]:
    t_mid = (VISIT_T0[0] + VISIT_T0[1]) / 2 + 0.5
    dt = (mjd - t_mid) / 365.25
    dec = st["dec"].to_numpy() + st["pmdec"].to_numpy() * dt / 3.6e6
    return st["ra"].to_numpy() + st["pmra"].to_numpy() * dt / 3.6e6 / np.cos(np.deg2rad(dec)), dec


def _dot_frame(obs, det, mjd, session, visit, ra, dec, flux, err, ph, rng) -> pd.DataFrame:
    n = len(ra)
    cd = math.cos(math.radians(ph.s_dec))
    x = 1020 + (ra - ph.s_ra) * cd * 3600 / PIX
    y = 1020 + (dec - ph.s_dec) * 3600 / PIX
    lo, hi = C.SPECTRA_DETECTOR_UM[int(det)]
    snr = flux / err
    order = np.argsort(-flux, kind="stable")
    df = pd.DataFrame({
        "obs_id": obs, "detector": np.int8(det), "mjd": mjd, "session": np.int32(session), "visit": np.int16(visit),
        "ra": ra, "dec": dec, "pos_err_arcsec": np.hypot(1.4 * 3.2 / snr, 0.25).astype(np.float32),
        "x": x.astype(np.float32), "y": y.astype(np.float32), "flux_ujy": flux.astype(np.float32),
        "flux_err_ujy": err.astype(np.float32), "wavelength_um": rng.uniform(lo, hi, n).astype(np.float32),
        "bandwidth_um": np.float32(0.02), "elong": rng.uniform(1.0, 1.3, n).astype(np.float32), "snr": snr.astype(np.float32),
        "flags": np.zeros(n, np.int32), "flags_peak": np.zeros(n, np.int32), "cell": cell_of(ra, dec),
        "a_arcsec": rng.normal(4.0, 0.1, n).astype(np.float32), "b_arcsec": rng.normal(3.9, 0.1, n).astype(np.float32),
        "psf_chi2_nu": rng.uniform(1.5, 3, n).astype(np.float32), "flux_psf_ujy": (flux * 1.033).astype(np.float32),
        "flux_psf_err_ujy": (err * 1.033).astype(np.float32),
    }).iloc[order].reset_index(drop=True)
    t = pa.Table.from_pandas(df, preserve_index=False)
    ap = np.repeat(df["flux_ujy"].to_numpy(np.float32)[:, None], 5, axis=1)
    t = t.append_column("flux_ap_ujy", pa.FixedSizeListArray.from_arrays(pa.array(ap.ravel()), 5))
    t = t.append_column("flux_ap_err_ujy", pa.FixedSizeListArray.from_arrays(pa.array(ap.ravel() * 0.05), 5))
    meta = {**(t.schema.metadata or {}), b"skyshift_photo": json.dumps({"schema": 1, "obs_id": obs, "detector": int(det),
                                                                        "header": {"X_SC": 6000.0, "Y_SC": 1000.0, "Z_SC": 200.0}}).encode()}
    return t.replace_schema_metadata(meta)


def make_root(path) -> Store:
    """The synthetic root (see the module docstring)."""
    from skyshift_pipeline import known, score
    rng = np.random.default_rng(11)
    store = Store(str(path))
    photos = _photos()
    store.write_parquet(photolist_rel(ZONE.name), photos)
    st = _stars(rng)
    # catalogues: Gaia (the first 250) at J2016.0, a forced list, an empty SDSS cache
    gra, gdec = _pos(st, GAIA_EPOCH)
    g = st["gaia"].to_numpy()
    ref = pd.DataFrame({"ref_id": [f"gaia:{1000 + i}" for i in np.flatnonzero(g)], "cat": "gaia", "ra": gra[g], "dec": gdec[g],
                        "epoch_mjd": GAIA_EPOCH, "pmra": st["pmra"][g].to_numpy(), "pmdec": st["pmdec"][g].to_numpy(),
                        "parallax": 2.0, "parallax_error": 0.1, "phot_g_mean_mag": 23.9 - 2.5 * np.log10(st["flux"][g].to_numpy()) + 1,
                        "phot_bp_mean_mag": 15.0, "phot_rp_mean_mag": 14.2})
    lone = pd.DataFrame({"ref_id": ["gaia:999", "gaia:998"], "cat": "gaia", "ra": [121.0, 120.9], "dec": [15.3, 15.25],
                         "epoch_mjd": GAIA_EPOCH, "pmra": 0.0, "pmdec": 0.0, "parallax": 1.0, "parallax_error": 0.2,
                         "phot_g_mean_mag": 17.0, "phot_bp_mean_mag": 17.5, "phot_rp_mean_mag": 16.6})  # no source: on tile edges
    ref = pd.concat([ref, lone], ignore_index=True)
    store.write_parquet(f"refcat/{ZONE.name}.parquet", ref)
    store.write_parquet(f"refcat/{ZONE.name}_forced.parquet", ref[["ref_id", "ra", "dec", "epoch_mjd", "pmra", "pmdec", "parallax"]])
    store.write_parquet(f"cache/sdss/{ZONE.name}.parquet", pd.DataFrame({"ra": [121.0], "dec": [15.5], "z": [0.1], "zErr": [0.001]}))
    # a known asteroid in session 1 (V 15, 60″/h east) and its control unit
    s1 = photos[photos["session"] == 1]
    p0 = s1.iloc[0]
    rate = (60.0, 0.0)
    probe = pd.DataFrame({"unit": [0]})
    ang = np.random.default_rng(C.CONTROL_SEED).uniform(0, 2 * np.pi, 1)[0]
    se, sn = C.CONTROL_OFFSET_ARCSEC * math.sin(ang), C.CONTROL_OFFSET_ARCSEC * math.cos(ang)
    del probe
    # the object sits so that it and its control position straddle the RA = 121.0 / Dec = 15.5 core edges
    k_ra, k_dec = 121.0 - se / 2 / 3600 / math.cos(math.radians(15.5)), 15.5 - sn / 2 / 3600
    dt_h = (s1["mjd"].to_numpy() - float(p0["mjd"])) * 24.0
    per_photo = {"obs_id": s1["obs_id"].tolist(), "detector": s1["detector"].astype(int).tolist(), "name": ["2000 AA"] * len(s1),
                 "ra": (k_ra + rate[0] * dt_h / 3600 / math.cos(math.radians(k_dec))).tolist(),
                 "dec": (k_dec + rate[1] * dt_h / 3600).tolist()}
    cache = {"zone": ZONE.name, "session": 1, "obs_ids": sorted(set(s1["obs_id"])), "mjd": float(p0["mjd"]),
             "objects": {"name": ["2000 AA"], "number": [99999], "ra": [k_ra], "dec": [k_dec], "v": [15.0],
                         "dra_arcsec_h": [rate[0]], "ddec_arcsec_h": [rate[1]], "class": ["MB"]}, "per_photo": per_photo}
    store.write_json(known.run_rel(ZONE, s1), cache)
    for sess, gs in photos.groupby("session"):  # the other sessions: no known objects (so `known` has nothing to fetch)
        if sess != 1:
            store.write_json(known.run_rel(ZONE, gs), {**cache, "session": int(sess), "obs_ids": sorted(set(gs["obs_id"])),
                                                       "mjd": float(gs["mjd"].iloc[0]),
                                                       "objects": {k: [] for k in cache["objects"]},
                                                       "per_photo": {k: [] for k in per_photo}})
    pred = known.load_predictions(store, photos, ZONE)
    units = score.score_units(pred, ZONE, 18.0)
    ctl = score.control_shift(units)
    assert len(units) == 4 and units["unit"].nunique() == 1
    # asteroids: (session, ra, dec, east ″/min, north ″/min); the known one and one at its control position
    movers = [(1, k_ra, k_dec, 1.0, 0.0), (3, 121.0 - 0.5 / 3600, 15.4, 1.2, 0.3), (5, 120.6, 15.75 + 0.4 / 3600, 0.2, -1.0),
              (6, 121.3, 15.2, -0.8, 0.8), (8, 121.25 + 0.3 / 3600, 15.9, 0.9, 0.0)]
    # forced photometry and dots, photo by photo
    fref = ref.reset_index(drop=True)
    for ph in photos.itertuples():
        ra, dec = _pos(st, ph.mjd)
        seen = rng.random(len(st)) < 0.95
        sig = st["sigma"].to_numpy()
        ra = ra + rng.normal(0, 1, len(st)) * sig / 3600 / np.cos(np.deg2rad(dec))
        dec = dec + rng.normal(0, 1, len(st)) * sig / 3600
        flux = st["flux"].to_numpy() * (1 + rng.normal(0, 0.03, len(st)))
        err = 0.03 * flux + 4.0
        parts_ra, parts_dec, parts_f, parts_e = [ra[seen]], [dec[seen]], [flux[seen]], [err[seen]]
        nn = 150  # stray single dots
        parts_ra.append(rng.uniform(120.4, 121.6, nn))
        parts_dec.append(rng.uniform(14.9, 16.1, nn))
        parts_f.append(np.full(nn, 40.0))
        parts_e.append(np.full(nn, 6.0))
        for sess, mra, mdec, ve, vn in movers:
            if ph.session != sess:
                continue
            dt = (ph.mjd - photos.loc[photos["session"] == sess, "mjd"].min()) * 1440
            parts_ra.append([mra + ve * dt / 3600 / math.cos(math.radians(mdec))])
            parts_dec.append([mdec + vn * dt / 3600])
            parts_f.append([300.0])
            parts_e.append([12.0])
        c = ctl[(ctl["obs_id"] == ph.obs_id) & (ctl["detector"] == ph.detector)]
        if len(c):  # the control mover sits where the control unit predicts it
            parts_ra.append(c["ra"].to_numpy())
            parts_dec.append(c["dec"].to_numpy())
            parts_f.append([250.0])
            parts_e.append([12.0])
        t = _dot_frame(ph.obs_id, ph.detector, ph.mjd, ph.session, ph.visit, np.concatenate(parts_ra), np.concatenate(parts_dec),
                       np.concatenate(parts_f), np.concatenate(parts_e), ph, rng)
        buf = io.BytesIO()
        pq.write_table(t, buf, compression="zstd")
        store.write_bytes(dots_rel(ph.obs_id, ph.detector), buf.getvalue())
        fra, fdec = tiles.moved(fref, ph.mjd)
        fl = np.r_[st["flux"][g].to_numpy(), [80.0, 60.0]] * (1 + rng.normal(0, 0.03, len(fref)))
        fe = 0.03 * fl + 4.0
        store.write_parquet(f"forced/{ph.obs_id}D{int(ph.detector)}.parquet", pd.DataFrame({
            "obs_id": ph.obs_id, "detector": np.int8(ph.detector), "mjd": ph.mjd, "ref_id": fref["ref_id"], "ra": fra, "dec": fdec,
            "x": np.float32(1000), "y": np.float32(1000), "flux_psf_ujy": fl.astype(np.float32), "flux_psf_err_ujy": fe.astype(np.float32),
            "psf_chi2_nu": np.float32(2), "wavelength_um": np.float32(np.mean(C.SPECTRA_DETECTOR_UM[int(ph.detector)])),
            "flags": np.int32(0)}))
    return store


def run_untiled(store: Store, zone: C.Zone) -> None:
    """`all`'s combining steps, untiled."""
    from skyshift_pipeline import astrometry, calib, classify, colours, crossid, highpm, lightcurves, link, photoz, score, spectra, suspects
    link.run(store, zone, 4)
    spectra.run(store, zone, 4)
    crossid.run(store, zone, 4)
    calib.run(store, zone, 4)
    if store.exists(results_rel(zone.name, C.CALIB_REL)):
        spectra.run(store, zone, 4)
    classify.run(store, zone, 4)
    photoz.run(store, zone)
    for m in (colours, lightcurves, astrometry, highpm):
        m.run(store, zone, 4)
    score.run(store, zone)
    suspects.run(store, zone)


KEYS = {"fixed": ["source_id"], "members": ["source_id", "obs_id", "detector", "dot_row"], "movers": ["session", "mjd", "ra", "dec"],
        "freedots": ["obs_id", "detector", "x", "y"], "sources": ["source_id"], "crossids": ["source_id"], "classes": ["source_id"], "photoz": ["source_id"],
        "colours": ["source_id", "pair"], "lightcurves": ["source_id", "ref_id", "origin", "detector", "mjd", "flux_ujy"],
        "lightcurves_summary": ["source_id", "ref_id", "detector", "origin"], "astrometry": ["source_id"],
        "highpm_candidates": ["method", "source_id", "source_id_b"], "suspects": ["id"], "recall_units": ["unit"],
        "calib_photos": ["obs_id", "detector"]}


def _norm(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if isinstance(df[c].dtype, pd.CategoricalDtype) or (df[c].dtype == object and len(df) and isinstance(df[c].iloc[0], str)):
            df[c] = df[c].astype(str)
    return df.sort_values(keys, kind="stable").reset_index(drop=True) if len(df) else df


def assert_same_results(a: Store, b: Store, zone: C.Zone, rtol: float = 1e-9) -> None:
    for name, keys in KEYS.items():
        rel = results_rel(zone.name, f"{name}.parquet")
        assert a.exists(rel) == b.exists(rel), name
        if not a.exists(rel):
            continue
        x, y = a.read_parquet(rel), b.read_parquet(rel)
        assert len(x) == len(y), f"{name}: {len(x)} vs {len(y)} rows"
        if not len(x):
            continue
        assert set(x.columns) == set(y.columns), name
        x, y = _norm(x, keys), _norm(y, keys)
        for c in x.columns:
            u, v = x[c].to_numpy(), y[c].to_numpy()
            if len(u) and isinstance(u[0], (list, np.ndarray)):
                assert [len(q) for q in u] == [len(q) for q in v], f"{name}.{c}"
                strs = any(np.asarray(q).dtype.kind in "OUS" for q in u)  # dot_obs, reasons
                u = np.concatenate([np.asarray(q).astype(str if strs else float) for q in u])
                v = np.concatenate([np.asarray(q).astype(str if strs else float) for q in v])
            if u.dtype.kind in "fc" or v.dtype.kind in "fc":
                np.testing.assert_allclose(u.astype(float), v.astype(float), rtol=rtol, atol=1e-12, equal_nan=True, err_msg=f"{name}.{c}")
            else:
                assert (u.astype(str) == v.astype(str)).all(), f"{name}.{c}"
    for name in ("calib.json", "lightcurves_report.json", "astrometry_report.json", "photoz_report.json",
                 "highpm_report.json", "recall.json"):
        rel = results_rel(zone.name, name)
        assert a.exists(rel) == b.exists(rel), name
        if a.exists(rel):
            ja, jb = a.read_json(rel), b.read_json(rel)
            for k in ("created", "generated"):
                ja.pop(k, None)
                jb.pop(k, None)
            assert json.dumps(ja, sort_keys=True) == json.dumps(jb, sort_keys=True), name


# --- grid and store -------------------------------------------------------------------------------------------
def test_cores_partition_the_zone_and_borders_hold_them():
    ts = tiles.plan(ZONE, TILE_DEG, 120.0, edge_margin=False)
    assert len(ts) == 16
    rng = np.random.default_rng(3)
    ra, dec = rng.uniform(ZONE.ra_min, ZONE.ra_max, 20000), rng.uniform(ZONE.dec_min, ZONE.dec_max, 20000)
    ra[:4], dec[:4] = 120.75, 15.25  # on core corners
    owners = sum(t.core.contains(ra, dec).astype(int) for t in ts)
    assert (owners == 1).all()
    for t in ts:
        assert t.ext.ra_min <= t.core.ra_min and t.ext.dec_max >= t.core.dec_max
        assert ZONE.ra_min <= t.ext.ra_min and t.ext.dec_max <= ZONE.dec_max  # clipped to the zone box
        if t.core.ra_min > ZONE.ra_min:
            assert (t.core.ra_min - t.ext.ra_min) * math.cos(math.radians(t.ext.dec_max)) * 3600 >= 120.0 - 1e-6
    te = tiles.plan(ZONE, TILE_DEG, 120.0, edge_margin=True)
    assert te[0].ext.ra_min < ZONE.ra_min and te[0].ext.dec_min < ZONE.dec_min  # zone edges are tile edges too
    out_ra, out_dec = np.array([120.49, 121.6]), np.array([15.1, 16.2])  # just outside the zone
    assert not any(tiles.owns(t, ZONE, out_ra, out_dec, True).any() for t in te)  # border: the zone next door's
    assert tiles.owns(ts[0], ZONE, out_ra[:1], out_dec[:1], False).all() and tiles.owns(ts[15], ZONE, out_ra[1:], out_dec[1:], False).all()


def test_choose_tiling():
    hunt, pilot = C.ZONES["hunt"], C.ZONES["pilot"]
    assert tiles.choose(pilot, "auto", None, 16) is None  # 12.6 deg² stays untiled
    assert tiles.choose(hunt, "auto", None, 16) == (4, 4)  # one round of 16 tiles of 2.5°
    assert tiles.choose(hunt, "off", None, 16) is None
    assert tiles.choose(hunt, "16", None, 16) == (4, 4)
    assert tiles.choose(pilot, "off", 0.3, 16) == tiles.grid_for_deg(pilot, 0.3) == (12, 12)
    assert tiles.grid_for_workers(pilot, 8) == (2, 4)  # 8 tiles, not 9: a ninth would wait alone
    big = C.Zone("big", 100.0, 130.0, 0.0, 30.0)
    nx, ny = tiles.grid_for_workers(big, 16)
    w, h = tiles.sides_deg(big)
    assert w / nx <= C.TILE_MAX_DEG and h / ny <= C.TILE_MAX_DEG


def test_tilestore_routes(tmp_path):
    base = Store(str(tmp_path / "base"))
    t = tiles.plan(ZONE, TILE_DEG, 120.0, edge_margin=False)[5]
    ts = tiles.TileStore(base.root, str(tmp_path / "work"), ZONE, t, slim=True, forced_mirror=False, zone_calib=False, edge_margin=True)
    n = t.name
    assert ts.route(photolist_rel(n)) == (ts.work, f"tiles/{n}/photolist.parquet")
    assert ts.route(results_rel(n, "fixed.parquet")) == (ts.work, f"tiles/{n}/results/fixed.parquet")
    assert ts.route(results_rel(n, "calib.json"))[0] is ts.work  # pass 1: the zone's calib is not seen
    ts.zone_calib = True
    assert ts.route(results_rel(n, "calib.json")) == (ts.base, results_rel(ZONE.name, "calib.json"))
    assert ts.route(f"refcat/{n}.parquet") == (ts.work, f"tiles/{n}/refcat/{n}.parquet")
    assert ts.route("dots/a.parquet") == (ts.work, "dots/a.parquet")
    assert ts.route(f"cache/mpc/{n}/run_x.json") == (ts.base, f"cache/mpc/{ZONE.name}/run_x.json")
    assert ts.route(f"cache/sdss/{n}.parquet") == (ts.base, f"cache/sdss/{ZONE.name}.parquet")
    base.write_parquet("forced/x.parquet", pd.DataFrame({"ref_id": ["in", "out"], "ra": [t.core.centre[0], 100.0],
                                                         "dec": [t.core.centre[1], 15.5], "mjd": [61000.0, 61000.0]}))
    assert ts.read_parquet("forced/x.parquet")["ref_id"].tolist() == ["in"]
    assert tiles.forced_owned(ts.forced_seen, t, ZONE, True).tolist() == ["in"]
    assert ts.read_parquet("forced/x.parquet", columns=["ref_id"]).columns.tolist() == ["ref_id"]
    ts.write_json(results_rel(n, "a.json"), {"k": 1})
    assert ts.fs.info(ts.path(results_rel(n, "a.json")))["size"] > 0


def test_slim_mirror_keeps_rows_and_metadata(tmp_path):
    store = make_root(tmp_path / "root")
    ph = store.read_parquet(photolist_rel(ZONE.name)).iloc[0]
    name = f"{ph.obs_id}D{int(ph.detector)}.parquet"
    ctx = {"root": store.root, "work": str(tmp_path / "work"), "slim": True, "forced_mirror": True}
    assert tiles.prepare_photos(ctx, [name])["dots"] == 1
    from skyshift_pipeline.dots import photo_meta
    raw, slim = store.read_bytes(f"dots/{name}"), Store(ctx["work"]).read_bytes(f"dots/{name}")
    a, b = pq.read_table(io.BytesIO(raw)), pq.read_table(io.BytesIO(slim))
    assert b.num_rows == a.num_rows and "flux_ap_ujy" not in b.column_names and "flux_ap_ujy" in a.column_names
    assert b.select(["ra", "dec", "flux_ujy"]).equals(a.select(["ra", "dec", "flux_ujy"]))
    assert photo_meta(slim)["header"]["X_SC"] == 6000.0
    assert Store(ctx["work"]).exists(f"forced/{name}")


# --- tiled == untiled -----------------------------------------------------------------------------------------
def test_tiled_equals_untiled(tmp_path):
    """Same sources, members, movers, classes, light curves, astrometry and every report, bit for bit or to float noise,
    with 16 tiles over 1° (a border of 2′ and the zone box clipped, as untiled)."""
    from skyshift_pipeline import motion_io
    u = make_root(tmp_path / "u")
    t = make_root(tmp_path / "t")
    run_untiled(u, ZONE)
    motion_io.clear_cache()
    out = tiles.run(t, ZONE, TILE_DEG, 1, edge_margin=False, work_dir=str(tmp_path / "work"))
    assert out["tiles"]["tiles"] == 16 and out["tiles"]["border"]["unresolved_tiles"] == []
    assert_same_results(u, t, ZONE)
    assert u.exists(results_rel(ZONE.name, C.CALIB_REL))  # calib ran, zone-wide, and every tile used it
    # the fast star: one source, on the tile edge, matched to its Gaia entry 20″ away, its motion measured
    src = t.read_parquet(results_rel(ZONE.name, "crossids.parquet"))
    near = src[(np.abs(src["ra"] - FAST[0]) * 3600 * math.cos(math.radians(FAST[1])) < 3) & (np.abs(src["dec"] - FAST[1]) * 3600 < 3)]
    assert len(near) == 1 and near["gaia_ref_id"].iloc[0] == "gaia:1000"
    ast = t.read_parquet(results_rel(ZONE.name, "astrometry.parquet")).set_index("source_id").loc[near["source_id"].iloc[0]]
    assert ast["pmra"] == pytest.approx(FAST[2], abs=300)
    # score's control: the unit's 120″ shift lands on a mover in another tile
    units = t.read_parquet(results_rel(ZONE.name, "recall_units.parquet"))
    assert units["mover"].all() and units["ctl_mover"].all()
    # forced targets without a source on tile edges: one light curve each (per detector), not one per tile
    lc = t.read_parquet(results_rel(ZONE.name, "lightcurves_summary.parquet"))
    assert (lc["ref_id"].astype(str) == "gaia:999").sum() == 2


def test_tiled_in_processes_and_border_retry(tmp_path, monkeypatch):
    """Two worker processes; a 2″ border is too narrow for link, so tiles are found and redone with a wider one.
    The disk is "full" (TILE_DISK_RESERVE_GB), so there is no slim mirror and the tiles read the root's dot files."""
    t = make_root(tmp_path / "t")
    monkeypatch.setattr(C, "TILE_DISK_RESERVE_GB", 1e9)
    out = tiles.run(t, ZONE, 0.5, 2, margin_arcsec=2.0, edge_margin=False, work_dir=str(tmp_path / "work"))
    assert out["tiles"]["disk"]["mirror"] is False and "slim_dots" not in out["tiles"]
    assert out["tiles"]["border"]["retried_tiles"] > 0
    fixed = t.read_parquet(results_rel(ZONE.name, "fixed.parquet"))
    assert fixed["source_id"].is_unique and len(fixed) > 200


def test_zone_edge_is_a_tile_edge(tmp_path):
    """With TILE_ZONE_EDGE_MARGIN a star on the zone's edge keeps the dots that fall just outside the zone box."""
    u = make_root(tmp_path / "u")
    t = make_root(tmp_path / "t")
    from skyshift_pipeline import link, spectra
    link.run(u, ZONE, 4)
    spectra.run(u, ZONE, 4)
    tiles.run(t, ZONE, TILE_DEG, 1, edge_margin=True, work_dir=str(tmp_path / "work"))

    def n_dots(store):
        s = store.read_parquet(results_rel(ZONE.name, "sources.parquet"), columns=["ra", "dec", "n_dots"])
        d = np.hypot((s["ra"] - EDGE_STAR[0]) * math.cos(math.radians(15.0)), s["dec"] - EDGE_STAR[1]) * 3600
        return int(s["n_dots"][d < 3].sum())

    assert n_dots(t) > n_dots(u)  # untiled clips the dots south of Dec 15.0
    assert n_dots(t) >= 28        # ~0.95 × 32 photos


def test_all_runs_the_combining_steps_tiled(tmp_path, monkeypatch):
    """`all --from link --tile-deg` → tiles.run → summary.json per step (offline: refcat would query the services)."""
    from skyshift_pipeline import cli
    store = make_root(tmp_path / "root")
    monkeypatch.setitem(C.ZONES, ZONE.name, ZONE)
    assert cli.main(["all", "--zone", ZONE.name, "--root", store.root, "--workers", "1", "--from", "link", "--tile-deg", "0.5",
                     "--zone-edge", "clip", "--tile-dir", str(tmp_path / "work")]) == 0
    summ = store.read_json(results_rel(ZONE.name, "summary.json"))["steps"]
    assert summ["tiles"]["grid"] == [2, 2] and summ["tiles"]["zone_edge_margin"] is False
    for step in ("link", "calib", "highpm", "score", "suspects"):
        assert step in summ
    for name in ("fixed", "members", "sources", "classes", "lightcurves", "astrometry", "suspects"):
        assert store.exists(results_rel(ZONE.name, f"{name}.parquet")), name
    assert not list((tmp_path / "work").iterdir())  # the work dir is removed after a good run
    assert cli.main(["all", "--zone", ZONE.name, "--root", store.root, "--workers", "1", "--from", "link", "--tiles", "4",
                     "--tile-dir", str(tmp_path / "work")]) == 0
    assert store.read_json(results_rel(ZONE.name, "summary.json"))["steps"]["tiles"]["zone_edge_margin"] is True


def test_stale_work_dirs_are_cleared(tmp_path):
    """A pre-empted run's work dir (its process gone) is removed when the zone starts again; a live run's is kept."""
    import os
    dead = tmp_path / "tst-20261114T010203-999999"
    (dead / "tiles").mkdir(parents=True)
    live = tmp_path / f"tst-20261114T010203-{os.getppid()}"
    live.mkdir()
    other = tmp_path / "tst2-20261114T010203-999999"
    other.mkdir()
    assert tiles.clear_stale(str(tmp_path), "tst") == [dead.name]
    assert live.exists() and other.exists() and not dead.exists()
    # after a machine restart the old run's pid may belong to another process: its unlocked lock file says it's gone
    reused = tmp_path / f"tst-20261114T020304-{os.getppid()}"
    reused.mkdir()
    (reused / tiles.LOCK_NAME).write_text("")
    run = tiles.Run(make_root(tmp_path / "root"), ZONE, 0.5, 1, work_dir=str(tmp_path))
    assert not reused.exists() and live.exists() and os.path.exists(run.work)
    assert tiles.clear_stale(str(tmp_path), "tst") == []  # this run holds its lock
    assert os.path.exists(run.work)
