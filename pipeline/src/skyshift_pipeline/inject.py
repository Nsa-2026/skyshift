"""Step `completeness`: synthetic movers injected into the stored dots, linked like the real ones.

Turns "we found nothing" into "nothing brighter than X moving between R1 and R2 ″/h in this zone".
Once per zone, after score, on a sub-area (config INJECT_BOX_DEG at the zone's centre; the zone if smaller):
1. Real dots of every photo overlapping the sub-area, clipped to it (link's load format; only the needed columns).
2. Per round (INJECT_ROUNDS), INJECT_OBJECTS objects on straight tracks: a reference position inside the sub-area
   (INJECT_EDGE_ARCSEC from its edge) at the time of a random photo covering it, rate log-uniform over
   INJECT_RATE_ARCSEC_H, direction uniform, S/N log-uniform over INJECT_SNR. A synthetic dot wherever the track is
   inside a photo that has a dot file: flux = S/N × the detector's median noise, measured with the photo's own noise
   (median flux_err_ujy of its faint dots), kept with the zone's own detection probability at that measured S/N and
   snr >= DOT_MIN_SNR; position scattered by INJECT_POS_ERR_SCALE × the dots' pos_err formula; elong drawn from real
   dots of that detector and S/N, stretched by the trail.
3. Real + synthetic dots through link.fixed_groups and link.classify, unchanged.
4. Unit = one object in one session with >= SCORE_MIN_EXPOSURES exposures covering it >= SCORE_EDGE_PX inside the
   photo (score's unit). Outcome, first that applies: mover (a mover of that session within MATCH_ARCSEC of the object
   at the mover's time, as score), fixed (a dot of it in a fixed group: too slow, or merged into a star), undetected
   (< 2 dots), vetoed (a dot within FIXED_VETO_ARCSEC of a fixed source), unlinked (no pair). fragmented = matched by
   >= 2 movers. control = the same match with the units moved CONTROL_OFFSET_ARCSEC (chance matches).
   Per object and visit: `fixed_single_visit` = the object's own dots made a fixed source (>= FIXED_MIN_SESSIONS of its
   sessions) whose dots all lie in that one visit: the slow-mover route (a source seen in one visit only).
Writes results/<zone>/completeness.json (grid rate × S/N, by rate, by S/N, limits, AB magnitudes per detector).
"""

from __future__ import annotations

import math
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config as C
from . import log
from .link import LOAD_COLUMNS, classify, fixed_groups
from .sky import Footprint, SkyTree, angsep, offset_radec
from .store import Store, dots_rel, photolist_rel, results_rel

L = log.get("inject")

READ_COLUMNS = [*LOAD_COLUMNS, "flux_err_ujy"]
OUTCOMES = ("mover", "fixed", "merged", "undetected", "vetoed", "unlinked")
AB_ZP_UJY = 23.9  # AB = 23.9 − 2.5 log10(f / µJy)


# --- sub-area and its dots -------------------------------------------------------------------------------------------
def sub_area(zone: C.Zone, side_deg: float = C.INJECT_BOX_DEG, edge_arcsec: float = C.INJECT_EDGE_ARCSEC) -> tuple[C.Zone, C.Zone]:
    """(box, inner): the sub-area at the zone's centre, clipped to the zone, and the part where units count."""
    ra0, dec0 = zone.centre
    cosd = max(math.cos(math.radians(dec0)), 0.05)
    h = side_deg / 2.0
    box = C.Zone(f"{zone.name}_inject", max(zone.ra_min, ra0 - h / cosd), min(zone.ra_max, ra0 + h / cosd),
                 max(zone.dec_min, dec0 - h), min(zone.dec_max, dec0 + h))
    w = (box.ra_max - box.ra_min) * cosd
    e = min(edge_arcsec / 3600.0, w / 4.0, (box.dec_max - box.dec_min) / 4.0)
    inner = C.Zone(f"{zone.name}_inject_inner", box.ra_min + e / cosd, box.ra_max - e / cosd, box.dec_min + e, box.dec_max - e)
    return box, inner


def box_photos(photos: pd.DataFrame, box: C.Zone) -> pd.DataFrame:
    """Photos whose footprint box meets the sub-area (tiles.py's rule)."""
    from .tiles import photo_boxes, tile_photos
    if len(photos) == 0:
        return photos.reset_index(drop=True)
    return tile_photos(photos, photo_boxes(photos), box)


def photo_exposures(photos: pd.DataFrame) -> np.ndarray:
    """Per photo its exposure number (link.load_dots' code: rank of the obs_id)."""
    code = {o: i for i, o in enumerate(sorted(photos["obs_id"].unique()))}
    return np.array([code[o] for o in photos["obs_id"]], dtype=np.int64)


def load_box_dots(store: Store, photos: pd.DataFrame, box: C.Zone, workers: int = 8) -> tuple[pd.DataFrame, np.ndarray]:
    """Dots of `photos` inside the box in link's load format (+ flux_err_ujy, `photo` = row in `photos`), and per photo
    whether it has a usable dot file (exists and not empty: an empty file is a skipped photo, e.g. FINAST != 0)."""
    photo_exp = photo_exposures(photos)
    usable = np.zeros(len(photos), dtype=bool)

    def one(k_r):
        k, r = k_r
        try:
            df = store.read_parquet(dots_rel(r.obs_id, int(r.detector)))
        except FileNotFoundError:
            return None
        if len(df) == 0:
            return None
        usable[k] = True
        df = df[[c for c in READ_COLUMNS if c in df.columns]]
        df = df.assign(dot_row=np.arange(len(df), dtype=np.int32))
        df = df[box.contains(df["ra"].to_numpy(), df["dec"].to_numpy())].reset_index(drop=True)
        df["session"] = np.int32(r.session)
        df["visit"] = np.int16(r.visit)
        df["photo"] = np.int32(k)
        df["exposure"] = np.int32(photo_exp[k])
        return df

    with ThreadPoolExecutor(max_workers=max(4, min(2 * workers, 32))) as ex:
        parts = [p for p in ex.map(one, enumerate(photos.itertuples())) if p is not None and len(p)]
    cols = [*LOAD_COLUMNS, "flux_err_ujy", "session", "visit", "photo", "exposure", "dot_row"]
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols}), usable
    df = pd.concat(parts, ignore_index=True)
    if "flux_err_ujy" not in df:
        df["flux_err_ujy"] = np.nan
    miss = ~np.isfinite(df["flux_err_ujy"].to_numpy(np.float64))
    if miss.any():
        df.loc[miss, "flux_err_ujy"] = df["flux_ujy"].to_numpy(np.float64)[miss] / np.maximum(df["snr"].to_numpy(np.float64)[miss], 1e-3)
    for c in ("flags_peak", "elong"):
        if c not in df:
            df[c] = 0 if c == "flags_peak" else np.nan
    return df[cols], usable


# --- the zone's own noise, detection and shape models -------------------------------------------------------------
def noise_model(dots: pd.DataFrame, photos: pd.DataFrame) -> tuple[dict[int, float], np.ndarray]:
    """({detector: median noise µJy}, per photo noise µJy): median flux_err_ujy of dots below INJECT_NOISE_MAX_SNR."""
    faint = dots[dots["snr"].to_numpy(np.float64) < C.INJECT_NOISE_MAX_SNR]
    det_sigma: dict[int, float] = {}
    for d in range(1, 7):
        x = faint.loc[faint["detector"].to_numpy() == d, "flux_err_ujy"].to_numpy(np.float64)
        x = x[np.isfinite(x) & (x > 0)]
        if x.size:
            det_sigma[d] = float(np.median(x))
    fallback = float(np.median(list(det_sigma.values()))) if det_sigma else 100.0
    for d in range(1, 7):
        det_sigma.setdefault(d, fallback)
    per_photo = photos["detector"].astype(int).map(det_sigma).to_numpy(np.float64).copy()
    if len(faint):
        med = faint.groupby("photo")["flux_err_ujy"].median()
        cnt = faint.groupby("photo").size()
        med = med[(cnt >= 20) & np.isfinite(med) & (med > 0)]
        per_photo[med.index.to_numpy(np.int64)] = med.to_numpy(np.float64)
    return det_sigma, per_photo


def detection_model(snr: np.ndarray) -> dict:
    """P(a dot is kept | measured S/N) from the S/N counts: power law over INJECT_DET_FIT_SNR extrapolated down,
    observed / expected per 0.5-wide bin from DOT_MIN_SNR (1 above the fit's lower end)."""
    snr = np.asarray(snr, np.float64)
    lo, hi = C.INJECT_DET_FIT_SNR
    fit = snr[(snr >= lo) & (snr < hi)]
    edges = np.arange(C.DOT_MIN_SNR, lo + 1e-9, 0.5)
    centres = (edges[:-1] + edges[1:]) / 2
    if fit.size >= C.INJECT_DET_MIN_DOTS:
        # maximum-likelihood slope of a truncated power law dN/dS ∝ S^-a on [lo, hi]
        def nll(a):
            if abs(a - 1) < 1e-6:
                norm = math.log(hi / lo)
            else:
                norm = (hi ** (1 - a) - lo ** (1 - a)) / (1 - a)
            return a * np.log(fit).sum() + fit.size * math.log(norm)
        grid = np.linspace(0.2, 4.0, 381)
        a = float(grid[int(np.argmin([nll(g) for g in grid]))])
        norm = (hi ** (1 - a) - lo ** (1 - a)) / (1 - a) if abs(a - 1) > 1e-6 else math.log(hi / lo)
        k = fit.size / norm
        expected = k * (edges[1:] ** (1 - a) - edges[:-1] ** (1 - a)) / (1 - a) if abs(a - 1) > 1e-6 else k * np.log(edges[1:] / edges[:-1])
        observed = np.histogram(snr, edges)[0].astype(np.float64)
        p = np.clip(observed / np.maximum(expected, 1e-9), 0.0, 1.0)
        p = np.maximum.accumulate(p)  # never less likely at a higher S/N
        return {"source": "dot counts", "slope": round(a, 3), "fit_snr": [lo, hi], "n_fit": int(fit.size),
                "snr": [round(float(c), 2) for c in centres], "p": [round(float(x), 3) for x in p]}
    p = 1.0 / (1.0 + np.exp(-(centres - C.INJECT_DET_SNR50) / C.INJECT_DET_WIDTH))
    return {"source": "logistic (too few dots)", "snr50": C.INJECT_DET_SNR50, "width": C.INJECT_DET_WIDTH,
            "n_fit": int(fit.size), "snr": [round(float(c), 2) for c in centres], "p": [round(float(x), 3) for x in p]}


def p_detect(model: dict, snr: np.ndarray) -> np.ndarray:
    snr = np.asarray(snr, np.float64)
    x, p = np.asarray(model["snr"], np.float64), np.asarray(model["p"], np.float64)
    out = np.interp(snr, x, p, left=p[0] if p.size else 1.0, right=1.0) if x.size else np.ones_like(snr)
    out[snr >= C.INJECT_DET_FIT_SNR[0]] = 1.0
    out[snr < C.DOT_MIN_SNR] = 0.0
    return out


SNR_SHAPE_BINS = np.array([C.DOT_MIN_SNR, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0, np.inf])


def elong_pools(dots: pd.DataFrame) -> dict[tuple[int, int], np.ndarray]:
    """Real elong values per (detector, S/N bin)."""
    e = dots["elong"].to_numpy(np.float64)
    b = np.clip(np.digitize(dots["snr"].to_numpy(np.float64), SNR_SHAPE_BINS) - 1, 0, len(SNR_SHAPE_BINS) - 2)
    det = dots["detector"].to_numpy(np.int64)
    out: dict[tuple[int, int], np.ndarray] = {}
    ok = np.isfinite(e)
    for d in range(1, 7):
        for k in range(len(SNR_SHAPE_BINS) - 1):
            v = e[ok & (det == d) & (b == k)]
            if v.size >= 20:
                out[(d, k)] = v
    return out


def draw_elong(pools: dict, det: np.ndarray, snr: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = np.full(det.size, 1.2)
    b = np.clip(np.digitize(snr, SNR_SHAPE_BINS) - 1, 0, len(SNR_SHAPE_BINS) - 2)
    for (d, k), pool in pools.items():
        m = (det == d) & (b == k)
        if m.any():
            out[m] = rng.choice(pool, int(m.sum()))
    return out


# --- synthetic objects, their tracks and dots ---------------------------------------------------------------------
def make_objects(n: int, inner: C.Zone, photos: pd.DataFrame, footprints: list[Footprint], usable: np.ndarray,
                 rng: np.random.Generator, first_id: int = 0) -> pd.DataFrame:
    """n objects: reference position in `inner`, at the time of a random usable photo covering it."""
    ra = rng.uniform(inner.ra_min, inner.ra_max, n)
    dec = rng.uniform(inner.dec_min, inner.dec_max, n)
    cover = np.zeros((len(photos), n), dtype=bool)
    for k, fp in enumerate(footprints):
        if usable[k]:
            cover[k] = fp.contains(ra, dec)
    mjd = photos["mjd"].to_numpy(np.float64)
    t0 = np.full(n, np.nan)
    for j in range(n):
        ks = np.flatnonzero(cover[:, j])
        if ks.size:
            t0[j] = mjd[rng.choice(ks)]
    r0, r1 = C.INJECT_RATE_ARCSEC_H
    s0, s1 = C.INJECT_SNR
    rate = np.exp(rng.uniform(math.log(r0), math.log(r1), n))
    pa = rng.uniform(0.0, 2 * math.pi, n)
    snr = np.exp(rng.uniform(math.log(s0), math.log(s1), n))
    obj = pd.DataFrame({"oid": np.arange(first_id, first_id + n, dtype=np.int64), "ra0": ra, "dec0": dec, "t0": t0,
                        "rate": rate, "pa_deg": np.degrees(pa), "vra": rate * np.sin(pa), "vdec": rate * np.cos(pa), "snr": snr})
    return obj[np.isfinite(obj["t0"])].reset_index(drop=True)


def tracks(obj: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint], usable: np.ndarray, box: C.Zone) -> pd.DataFrame:
    """Every (object, photo) with the object inside the photo's footprint and the sub-area: true position and the
    distance to the photo's edge (deg)."""
    mjd = photos["mjd"].to_numpy(np.float64)
    cosd = max(math.cos(math.radians((box.dec_min + box.dec_max) / 2)), 0.05)
    diag = math.hypot((box.ra_max - box.ra_min) * cosd, box.dec_max - box.dec_min) * 3600.0 + 60.0
    dt_h = (mjd[:, None] - obj["t0"].to_numpy()[None, :]) * 24.0
    near = (obj["rate"].to_numpy()[None, :] * np.abs(dt_h) <= diag) & usable[:, None]
    k, o = np.nonzero(near)
    dt = dt_h[k, o]
    ra, dec = offset_radec(obj["ra0"].to_numpy()[o], obj["dec0"].to_numpy()[o], obj["vra"].to_numpy()[o] * dt,
                           obj["vdec"].to_numpy()[o] * dt)
    inb = box.contains(ra, dec)
    k, o, ra, dec = k[inb], o[inb], ra[inb], dec[inb]
    edge = np.full(k.size, -1.0)
    order = np.argsort(k, kind="stable")
    k, o, ra, dec = k[order], o[order], ra[order], dec[order]
    cuts = np.flatnonzero(np.diff(k)) + 1
    for idx in np.split(np.arange(k.size), cuts):
        if idx.size:
            edge[idx] = footprints[int(k[idx[0]])].edge_distance_deg(ra[idx], dec[idx])
    keep = edge > 0
    return pd.DataFrame({"oid": obj["oid"].to_numpy()[o[keep]], "obj": o[keep], "photo": k[keep], "ra": ra[keep], "dec": dec[keep],
                         "edge_deg": edge[keep]})


def synth_dots(tr: pd.DataFrame, obj: pd.DataFrame, photos: pd.DataFrame, det_sigma: dict[int, float], photo_sigma: np.ndarray,
               det_model: dict, pools: dict, photo_exp: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """Measured synthetic dots (link's load format, `inj` = object id) for the track rows."""
    k = tr["photo"].to_numpy(np.int64)
    o = tr["obj"].to_numpy(np.int64)
    det = photos["detector"].to_numpy(np.int64)[k]
    sig_det = np.array([det_sigma[int(d)] for d in det], dtype=np.float64)
    sig = photo_sigma[k]
    flux_true = obj["snr"].to_numpy()[o] * np.where(det >= 4, C.INJECT_MWIR_SNR_FACTOR, 1.0) * sig_det
    flux = flux_true + rng.normal(0.0, 1.0, k.size) * sig
    snr = flux / sig
    keep = rng.uniform(0.0, 1.0, k.size) < p_detect(det_model, snr)
    k, o, det, flux, snr, sig = k[keep], o[keep], det[keep], flux[keep], snr[keep], sig[keep]
    ra, dec = tr["ra"].to_numpy()[keep], tr["dec"].to_numpy()[keep]
    perr = C.INJECT_POS_ERR_SCALE * np.hypot(C.POS_ERR_K * C.PSF_SIGMA_ARCSEC / snr, C.POS_ERR_FLOOR_ARCSEC)
    ra, dec = offset_radec(ra, dec, rng.normal(0.0, 1.0, k.size) * perr, rng.normal(0.0, 1.0, k.size) * perr)
    trail = obj["rate"].to_numpy()[o] * C.INJECT_EXPOSURE_S / 3600.0
    elong = draw_elong(pools, det, snr, rng) * np.sqrt(1.0 + trail ** 2 / (12.0 * C.PSF_SIGMA_ARCSEC ** 2))
    return pd.DataFrame({
        "detector": det.astype(np.int8), "mjd": photos["mjd"].to_numpy(np.float64)[k], "ra": ra, "dec": dec,
        "flux_ujy": flux.astype(np.float32), "snr": snr.astype(np.float32), "flags": np.zeros(k.size, np.int32),
        "flags_peak": np.zeros(k.size, np.int32), "elong": elong.astype(np.float32), "flux_err_ujy": sig.astype(np.float32),
        "session": photos["session"].to_numpy()[k].astype(np.int32), "visit": photos["visit"].to_numpy()[k].astype(np.int16),
        "photo": k.astype(np.int32), "exposure": photo_exp[k].astype(np.int32),
        "dot_row": np.full(k.size, -1, np.int32), "inj": obj["oid"].to_numpy()[o]})


# --- units and outcomes ---------------------------------------------------------------------------------------------
def make_units(tr: pd.DataFrame, obj: pd.DataFrame, photos: pd.DataFrame, inner: C.Zone, photo_exp: np.ndarray) -> pd.DataFrame:
    """score's unit: (object, session) with >= SCORE_MIN_EXPOSURES exposures covering it >= SCORE_EDGE_PX inside the photo,
    at a position inside `inner`. Reference = its first such row."""
    ok = (tr["edge_deg"].to_numpy() * 3600.0 >= C.SCORE_EDGE_PX * C.PIXEL_ARCSEC) & inner.contains(tr["ra"].to_numpy(), tr["dec"].to_numpy())
    t = tr[ok]
    k = t["photo"].to_numpy(np.int64)
    t = t.assign(session=photos["session"].to_numpy()[k], visit=photos["visit"].to_numpy()[k], mjd=photos["mjd"].to_numpy()[k],
                 exposure=photo_exp[k])
    t = t.sort_values(["oid", "session", "mjd"], kind="stable")
    g = t.groupby(["oid", "session"], sort=True)
    u = g.agg(obj=("obj", "first"), visit=("visit", "first"), ra=("ra", "first"), dec=("dec", "first"), mjd=("mjd", "first"),
              n_exposures=("exposure", "nunique")).reset_index()
    u = u[u["n_exposures"] >= C.SCORE_MIN_EXPOSURES].reset_index(drop=True)
    o = u["obj"].to_numpy(np.int64)
    for c in ("rate", "vra", "vdec", "snr"):
        u[c] = obj[c].to_numpy()[o]
    return u


def match_units(u: pd.DataFrame, movers: pd.DataFrame, radius: float = C.MATCH_ARCSEC) -> np.ndarray:
    """Number of movers of the unit's session within `radius` of the object at the mover's time (score's rule)."""
    n = np.zeros(len(u), dtype=np.int64)
    if len(u) == 0 or len(movers) == 0:
        return n
    a = u[["session", "ra", "dec", "mjd", "vra", "vdec"]].assign(_u=np.arange(len(u)))
    b = movers[["session", "ra", "dec", "mjd"]].rename(columns={"ra": "m_ra", "dec": "m_dec", "mjd": "m_mjd"})
    j = a.merge(b, on="session", how="inner")
    if len(j) == 0:
        return n
    dt = (j["m_mjd"].to_numpy() - j["mjd"].to_numpy()) * 24.0
    pra, pdec = offset_radec(j["ra"].to_numpy(), j["dec"].to_numpy(), j["vra"].to_numpy() * dt, j["vdec"].to_numpy() * dt)
    hit = angsep(pra, pdec, j["m_ra"].to_numpy(), j["m_dec"].to_numpy()) * 3600.0 <= radius
    np.add.at(n, j["_u"].to_numpy()[hit], 1)
    return n


def control_units(u: pd.DataFrame, rng: np.random.Generator, offset: float = C.CONTROL_OFFSET_ARCSEC) -> pd.DataFrame:
    ang = rng.uniform(0.0, 2 * math.pi, len(u))
    ra, dec = offset_radec(u["ra"].to_numpy(), u["dec"].to_numpy(), offset * np.sin(ang), offset * np.cos(ang))
    return u.assign(ra=ra, dec=dec)


def outcomes(u: pd.DataFrame, syn: pd.DataFrame, syn_sid: np.ndarray, fixed: pd.DataFrame, n_match: np.ndarray,
             real_source_ids: np.ndarray | None = None) -> np.ndarray:
    """Per unit: mover, fixed (a dot in a fixed group that is no real source), merged (a dot joined a real source:
    one whose real dots alone span >= FIXED_MIN_SESSIONS sessions), undetected, vetoed or unlinked (first that applies)."""
    key = pd.MultiIndex.from_arrays([syn["inj"].to_numpy(), syn["session"].to_numpy()])
    merged = np.isin(syn_sid, real_source_ids) & (syn_sid >= 0) if real_source_ids is not None else np.zeros(len(syn), bool)
    s = pd.DataFrame({"in_fixed": (syn_sid >= 0) & ~merged, "merged": merged, "one": 1}, index=key)
    if len(fixed) and len(syn):
        sep, _ = SkyTree(fixed["ra"].to_numpy(), fixed["dec"].to_numpy()).nearest(syn["ra"].to_numpy(), syn["dec"].to_numpy())
        s["vetoed"] = (sep <= C.FIXED_VETO_ARCSEC) & (syn_sid < 0)
    else:
        s["vetoed"] = False
    agg = s.groupby(level=[0, 1]).agg(in_fixed=("in_fixed", "any"), merged=("merged", "any"), vetoed=("vetoed", "any"),
                                      n=("one", "size"))
    ui = pd.MultiIndex.from_arrays([u["oid"].to_numpy(), u["session"].to_numpy()])
    a = agg.reindex(ui)
    in_fixed = a["in_fixed"].fillna(False).to_numpy(bool)
    in_real = a["merged"].fillna(False).to_numpy(bool)
    vetoed = a["vetoed"].fillna(False).to_numpy(bool)
    ndots = a["n"].fillna(0).to_numpy(np.int64)
    out = np.full(len(u), "unlinked", dtype=object)
    out[vetoed] = "vetoed"
    out[ndots < 2] = "undetected"
    out[in_real] = "merged"
    out[in_fixed] = "fixed"
    out[n_match > 0] = "mover"
    return out


def single_visit_fixed(syn: pd.DataFrame, syn_sid: np.ndarray, all_sid: np.ndarray, all_visit: np.ndarray) -> pd.DataFrame:
    """(oid, visit) pairs whose object's own dots made a fixed source (>= FIXED_MIN_SESSIONS of its sessions in the group)
    seen in that one visit only (every dot of the group, real or synthetic, in that visit)."""
    m = syn_sid >= 0
    if not m.any():
        return pd.DataFrame({"oid": pd.Series(dtype="int64"), "visit": pd.Series(dtype="int64")})
    d = pd.DataFrame({"oid": syn["inj"].to_numpy()[m], "sid": syn_sid[m], "session": syn["session"].to_numpy()[m],
                      "visit": syn["visit"].to_numpy()[m]})
    made = d.groupby(["oid", "sid"]).agg(n_sessions=("session", "nunique"), visit=("visit", "first")).reset_index()
    made = made[made["n_sessions"] >= C.FIXED_MIN_SESSIONS]
    g = pd.DataFrame({"sid": all_sid[all_sid >= 0], "visit": all_visit[all_sid >= 0]}).groupby("sid")["visit"].nunique()
    made = made[made["sid"].map(g).to_numpy() == 1]
    return made[["oid", "visit"]].drop_duplicates().astype("int64")


# --- one round ------------------------------------------------------------------------------------------------------
def run_round(real: pd.DataFrame, photos: pd.DataFrame, footprints: list[Footprint], usable: np.ndarray, box: C.Zone,
              inner: C.Zone, models: dict, n_objects: int, rng: np.random.Generator, first_id: int) -> dict:
    t0 = time.time()
    photo_exp = models["photo_exp"]
    obj = make_objects(n_objects, inner, photos, footprints, usable, rng, first_id)
    tr = tracks(obj, photos, footprints, usable, box)
    syn = synth_dots(tr, obj, photos, models["det_sigma"], models["photo_sigma"], models["detection"], models["elong"], photo_exp, rng)
    u = make_units(tr, obj, photos, inner, photo_exp)
    t1 = time.time()
    dots = pd.concat([real.assign(inj=np.int64(-1)), syn], ignore_index=True)
    fixed_result = fixed_groups(dots)
    fixed, movers, st = classify(dots, photos, footprints=footprints, fixed_result=fixed_result)
    t2 = time.time()
    is_syn = dots["inj"].to_numpy() >= 0
    sid = fixed_result[1]
    n_match = match_units(u, movers)
    u["n_movers"] = n_match
    real_sid = pd.DataFrame({"sid": sid[~is_syn], "session": dots["session"].to_numpy()[~is_syn]})
    real_sid = real_sid[real_sid["sid"] >= 0].groupby("sid")["session"].nunique()
    u["outcome"] = outcomes(u, syn, sid[is_syn], fixed, n_match, real_sid.index[real_sid >= C.FIXED_MIN_SESSIONS].to_numpy())
    u["control"] = match_units(control_units(u, rng), movers) > 0
    sv = single_visit_fixed(syn, sid[is_syn], sid, dots["visit"].to_numpy())
    vis = u.groupby(["oid", "visit"]).agg(obj=("obj", "first"), n_sessions=("session", "nunique"),
                                          mover=("outcome", lambda x: bool((x == "mover").any()))).reset_index()
    vis["fixed_single_visit"] = pd.MultiIndex.from_frame(vis[["oid", "visit"]]).isin(pd.MultiIndex.from_frame(sv)) if len(sv) else False
    for c in ("rate", "snr"):
        vis[c] = obj[c].to_numpy()[vis["obj"].to_numpy(np.int64)]
    return {"objects": obj, "units": u, "visits": vis, "synthetic_dots": int(len(syn)), "movers": int(len(movers)), "link": st, "seconds": {"inject": round(t1 - t0, 1), "link": round(t2 - t1, 1),
                                                              "evaluate": round(time.time() - t2, 1)}}


# --- tables for completeness.json -----------------------------------------------------------------------------------
def _frac(x) -> float | None:
    x = np.asarray(x)
    return round(float(np.mean(x)), 4) if x.size else None


def _bin(v: np.ndarray, edges) -> np.ndarray:
    e = np.asarray(edges, np.float64)
    b = np.digitize(v, e) - 1
    b[(v == e[-1])] = len(e) - 2
    b[(v < e[0]) | (v > e[-1])] = -1
    return b


def summarize(u: pd.DataFrame, vis: pd.DataFrame, obj: pd.DataFrame) -> dict:
    re, se = C.INJECT_RATE_EDGES, C.INJECT_SNR_EDGES
    rb, sb = _bin(u["rate"].to_numpy(), re), _bin(u["snr"].to_numpy(), se)
    out = u["outcome"].to_numpy()
    nr, ns = len(re) - 1, len(se) - 1
    grid: dict[str, list] = {"n_units": [], **{k: [] for k in OUTCOMES}, "fragmented": []}
    for i in range(nr):
        rows: dict[str, list] = {k: [] for k in grid}
        for j in range(ns):
            m = (rb == i) & (sb == j)
            rows["n_units"].append(int(m.sum()))
            for k in OUTCOMES:
                rows[k].append(_frac(out[m] == k))
            rows["fragmented"].append(_frac(u["n_movers"].to_numpy()[m] >= 2))
        for k in grid:
            grid[k].append(rows[k])
    plateau = u["snr"].to_numpy() >= C.INJECT_PLATEAU_SNR
    by_rate = []
    vrb = _bin(vis["rate"].to_numpy(), re)
    vplat = vis["snr"].to_numpy() >= C.INJECT_PLATEAU_SNR
    orb = _bin(obj["rate"].to_numpy(), re)
    ofound = obj["oid"].isin(u.loc[out == "mover", "oid"]).to_numpy()
    ohas = obj["oid"].isin(u["oid"]).to_numpy()
    for i in range(nr):
        m = rb == i
        vm = (vrb == i) & (vis["n_sessions"].to_numpy() >= C.FIXED_MIN_SESSIONS)
        om = (orb == i) & ohas
        by_rate.append({"rate_min": re[i], "rate_max": re[i + 1], "n_units": int(m.sum()),
                        **{k: _frac(out[m] == k) for k in OUTCOMES},
                        f"mover_snr_ge_{C.INJECT_PLATEAU_SNR:g}": _frac(out[m & plateau] == "mover"),
                        "n_units_snr_ge": int((m & plateau).sum()),
                        "fragmented": _frac(u["n_movers"].to_numpy()[m] >= 2),
                        "control": _frac(u["control"].to_numpy()[m]),
                        "n_objects": int(om.sum()), "object_found": _frac(ofound[om]),
                        "n_object_visits": int(vm.sum()),
                        "fixed_single_visit": _frac(vis["fixed_single_visit"].to_numpy()[vm]),
                        f"fixed_single_visit_snr_ge_{C.INJECT_PLATEAU_SNR:g}": _frac(vis["fixed_single_visit"].to_numpy()[vm & vplat])})
    by_snr = []
    for j in range(ns):
        m = sb == j
        by_snr.append({"snr_min": se[j], "snr_max": se[j + 1], "n_units": int(m.sum()),
                       **{k: _frac(out[m] == k) for k in OUTCOMES}})
    return {"grid": grid, "by_rate": by_rate, "by_snr": by_snr}


def limits(u: pd.DataFrame, det_sigma: dict[int, float]) -> dict:
    """Rate range where >= 50% / 90% of units at S/N >= INJECT_PLATEAU_SNR come out as movers (contiguous bins around the
    best), and inside the 50% range the S/N (and AB per detector) where half of them do."""
    re = np.asarray(C.INJECT_RATE_EDGES)
    rb = _bin(u["rate"].to_numpy(), re)
    mover = (u["outcome"].to_numpy() == "mover")
    plat = u["snr"].to_numpy() >= C.INJECT_PLATEAU_SNR
    frac = np.array([np.mean(mover[(rb == i) & plat]) if ((rb == i) & plat).sum() >= 5 else np.nan for i in range(len(re) - 1)])
    out: dict = {"plateau_snr": C.INJECT_PLATEAU_SNR, "plateau_by_rate": [None if not np.isfinite(f) else round(float(f), 4) for f in frac]}
    if not np.isfinite(frac).any():
        return out
    best = int(np.nanargmax(frac))
    for level in (0.5, 0.9):
        if not frac[best] >= level:
            out[f"rate_{int(level * 100)}"] = None
            continue
        lo = hi = best
        while lo - 1 >= 0 and frac[lo - 1] >= level:
            lo -= 1
        while hi + 1 < frac.size and frac[hi + 1] >= level:
            hi += 1
        out[f"rate_{int(level * 100)}"] = [float(re[lo]), float(re[hi + 1])]
    r = out.get("rate_50")
    if r:
        m = (u["rate"].to_numpy() >= r[0]) & (u["rate"].to_numpy() < r[1])
        se = np.asarray(C.INJECT_SNR_EDGES)
        sb = _bin(u["snr"].to_numpy(), se)
        f = np.array([np.mean(mover[m & (sb == j)]) if (m & (sb == j)).sum() >= 5 else np.nan for j in range(len(se) - 1)])
        c = np.sqrt(se[:-1] * se[1:])
        ok = np.isfinite(f)
        snr50 = None
        if ok.any() and np.nanmax(f) >= 0.5:
            if f[ok][0] >= 0.5:
                snr50 = float(se[0])
            else:
                x, y = np.log(c[ok]), f[ok]
                for a in range(len(y) - 1):
                    if y[a] < 0.5 <= y[a + 1]:
                        snr50 = float(np.exp(x[a] + (0.5 - y[a]) * (x[a + 1] - x[a]) / (y[a + 1] - y[a])))
                        break
        out["snr_50"] = None if snr50 is None else round(snr50, 2)
        out["by_snr_in_rate_50"] = [None if not np.isfinite(v) else round(float(v), 4) for v in f]
        if snr50 is not None:
            out["mag_ab_50"] = {str(d): round(AB_ZP_UJY - 2.5 * math.log10(snr50 * s), 2) for d, s in det_sigma.items()}
    return out


def vs_recall(store: Store, zone: C.Zone, u: pd.DataFrame) -> dict | None:
    """The zone's measured known-asteroid recall (recall.json, V < SCORE_V_MAX) next to the synthetic one at the rates
    and S/N of those asteroids (INJECT_RECALL_RATES, S/N >= INJECT_PLATEAU_SNR): their ratio is how optimistic the
    injection is (missing dots, masks and artefacts it doesn't model). Synthetic objects count as found by a mover only,
    so the measured side is `recall_movers` (movers alone) when recall.json has it; `recall` also counts the dots
    tracklets recovered (older files have only `recall`, which was movers alone). `recall_field` says which."""
    rel = results_rel(zone.name, "recall.json")
    try:
        rec = store.read_json(rel) if store.exists(rel) else None
    except Exception:  # noqa: BLE001
        rec = None
    lo, hi = C.INJECT_RECALL_RATES
    m = (u["rate"].to_numpy() >= lo) & (u["rate"].to_numpy() < hi) & (u["snr"].to_numpy() >= C.INJECT_PLATEAU_SNR)
    syn = _frac(u["outcome"].to_numpy()[m] == "mover")
    out = {"rates_arcsec_h": [lo, hi], "snr_min": C.INJECT_PLATEAU_SNR, "n_units": int(m.sum()), "synthetic": syn}
    field = "recall_movers" if rec and rec.get("recall_movers") is not None else "recall"
    if rec and rec.get(field) is not None:
        r = rec[field]
        out.update({"recall": r, "recall_field": field, "recall_n": rec.get("n_known"), "recall_v_max": rec.get("v_max"),
                    "dot_share": rec.get("dot_share"),
                    **({"recall_with_tracks": rec.get("recall")} if field == "recall_movers" else {}),
                    "ratio": round(r / syn, 4) if syn else None})
    return out


def headline(lim: dict) -> str:
    r, s, m = lim.get("rate_50"), lim.get("snr_50"), lim.get("mag_ab_50")
    if not r or s is None or not m:
        return "not enough recovered synthetic movers for a limit"
    swir = [m[k] for k in ("1", "2", "3") if k in m]
    mwir = [m[k] for k in ("4", "5", "6") if k in m]
    return (f"per session, at least half of the movers between {r[0]:g} and {r[1]:g}″/h with S/N ≥ {s:.1f} per exposure "
            f"(AB {min(swir):.1f}–{max(swir):.1f} in D1–D3, {min(mwir):.1f}–{max(mwir):.1f} in D4–D6) are found")


# --- the step -------------------------------------------------------------------------------------------------------
def run(store: Store, zone: C.Zone, workers: int = 8, rounds: int | None = None, n_objects: int | None = None,
        side_deg: float | None = None) -> dict:
    if not C.COMPLETENESS:
        L.info("completeness %s: off (SKYSHIFT_COMPLETENESS)", zone.name)
        return {"skipped": "SKYSHIFT_COMPLETENESS=off"}
    t_all = time.time()
    rounds = C.INJECT_ROUNDS if rounds is None else int(rounds)
    n_objects = C.INJECT_OBJECTS if n_objects is None else int(n_objects)
    box, inner = sub_area(zone, C.INJECT_BOX_DEG if side_deg is None else side_deg)
    allp = store.read_parquet(photolist_rel(zone.name))
    photos = box_photos(allp, box)
    footprints = [Footprint.from_s_region(s) for s in photos["s_region"]]
    t0 = time.time()
    real, usable = load_box_dots(store, photos, box, workers)
    t_load = round(time.time() - t0, 1)
    side = C.INJECT_BOX_DEG if side_deg is None else side_deg
    if len(real) > C.INJECT_MAX_REAL_DOTS:  # a crowded zone: a smaller box at the same centre (link's cost and memory)
        side *= math.sqrt(C.INJECT_MAX_REAL_DOTS / len(real))
        box, inner = sub_area(zone, side)
        keep = box.contains(real["ra"].to_numpy(), real["dec"].to_numpy())
        L.info("completeness %s: %d real dots > %d: sub-area side %.2f°", zone.name, len(real), C.INJECT_MAX_REAL_DOTS, side)
        real = real[keep].reset_index(drop=True)
    L.info("completeness %s: sub-area %s, %d photos (%d with dots), %d real dots, read in %.0f s", zone.name, box, len(photos),
           int(usable.sum()), len(real), t_load)
    rel = results_rel(zone.name, "completeness.json")
    base = {"zone": zone.name, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "schema": 1,
            "sub_area": {"ra": [box.ra_min, box.ra_max], "dec": [box.dec_min, box.dec_max],
                         "inner_ra": [inner.ra_min, inner.ra_max], "inner_dec": [inner.dec_min, inner.dec_max],
                         "deg2": round((box.ra_max - box.ra_min) * math.cos(math.radians((box.dec_min + box.dec_max) / 2))
                                       * (box.dec_max - box.dec_min), 4),
                         "photos": int(len(photos)), "photos_with_dots": int(usable.sum()), "real_dots": int(len(real)),
                         "sessions": int(photos.loc[usable, "session"].nunique()) if len(photos) else 0,
                         "visits": int(photos.loc[usable, "visit"].nunique()) if len(photos) else 0}}
    if len(real) == 0 or not usable.any():
        out = {**base, "error": "no dots in the sub-area", "wall_s": round(time.time() - t_all, 1)}
        store.write_json(rel, out)
        L.warning("completeness %s: no dots in the sub-area; nothing injected", zone.name)
        return {"error": out["error"], "wall_s": out["wall_s"]}
    det_sigma, photo_sigma = noise_model(real, photos)
    models = {"det_sigma": det_sigma, "photo_sigma": photo_sigma, "detection": detection_model(real["snr"].to_numpy()),
              "elong": elong_pools(real), "photo_exp": photo_exposures(photos)}
    rng = np.random.default_rng(C.INJECT_SEED + zlib.crc32(zone.name.encode()))
    us, vs, objs, per_round = [], [], [], []
    for r in range(rounds):
        res = run_round(real, photos, footprints, usable, box, inner, models, n_objects, rng, first_id=r * n_objects)
        res["units"]["round"] = r
        us.append(res["units"])
        vs.append(res["visits"])
        objs.append(res["objects"])
        per_round.append({"objects": int(len(res["objects"])), "units": int(len(res["units"])), "synthetic_dots": res["synthetic_dots"],
                          "movers": res["movers"], "seconds": res["seconds"]})
        L.info("completeness %s: round %d: %s", zone.name, r + 1, per_round[-1])
    u = pd.concat(us, ignore_index=True)
    vis = pd.concat(vs, ignore_index=True)
    obj = pd.concat(objs, ignore_index=True)
    tables = summarize(u, vis, obj)
    lim = limits(u, det_sigma)
    se = np.asarray(C.INJECT_SNR_EDGES)
    out = {
        **base,
        "method": "synthetic movers injected into the stored dots of the sub-area, relinked with link.classify (inject.py)",
        "unit": f"one synthetic object in one session with >= {C.SCORE_MIN_EXPOSURES} exposures covering it (as score's recall)",
        "outcomes": {"mover": f"a mover of the session within {C.MATCH_ARCSEC:g}″ of the object at the mover's time",
                     "fixed": "a dot of it is in a fixed source of its own (too slow: its sessions chain within 4″)",
                     "merged": "a dot of it joined a real fixed source (its real dots alone in >= 3 sessions)",
                     "undetected": "fewer than 2 dots kept", "vetoed": f"a dot within {C.FIXED_VETO_ARCSEC:g}″ of a fixed source",
                     "unlinked": "dots there but no pair", "fixed_single_visit": "per object and visit with >= "
                     f"{C.FIXED_MIN_SESSIONS} sessions: its own dots made a fixed source seen in that visit only"},
        "injected": {"rounds": rounds, "objects": int(len(obj)), "objects_per_round": n_objects, "units": int(len(u)),
                     "object_visits": int(len(vis)), "synthetic_dots": int(sum(p["synthetic_dots"] for p in per_round)),
                     "seed": C.INJECT_SEED, "rate_arcsec_h": list(C.INJECT_RATE_ARCSEC_H), "snr": list(C.INJECT_SNR),
                     "pos_err_scale": C.INJECT_POS_ERR_SCALE, "mwir_snr_factor": C.INJECT_MWIR_SNR_FACTOR},
        "noise_ujy": {str(d): round(s, 2) for d, s in det_sigma.items()},
        "detection": models["detection"],
        "rate_edges_arcsec_h": list(C.INJECT_RATE_EDGES), "snr_edges": list(C.INJECT_SNR_EDGES),
        "mag_ab_at_snr_edges": {str(d): [round(AB_ZP_UJY - 2.5 * math.log10(x * s), 2) for x in se] for d, s in det_sigma.items()},
        "control_mover": _frac(u["control"]),
        "overall": {k: _frac(u["outcome"] == k) for k in OUTCOMES},
        "limits": lim,
        "headline": headline(lim),
        "vs_recall": vs_recall(store, zone, u),
        **tables,
        "rounds": per_round,
        "seconds": {"read": t_load, "rounds": round(sum(sum(p["seconds"].values()) for p in per_round), 1)},
        "wall_s": round(time.time() - t_all, 1),
    }
    store.write_json(rel, out)
    L.info("completeness %s: %d units from %d objects; %s; control %s; %.0f s", zone.name, len(u), len(obj), out["headline"],
           out["control_mover"], out["wall_s"])
    for b in tables["by_rate"]:
        L.info("completeness %s:   %.3g–%.3g″/h: n %d mover %s fixed %s (S/N≥%g: %s), single-visit fixed %s", zone.name,
               b["rate_min"], b["rate_max"], b["n_units"], b["mover"], b["fixed"], C.INJECT_PLATEAU_SNR,
               b[f"mover_snr_ge_{C.INJECT_PLATEAU_SNR:g}"], b["fixed_single_visit"])
    return {"units": int(len(u)), "objects": int(len(obj)), "sub_area_deg2": base["sub_area"]["deg2"],
            "photos": int(len(photos)), "real_dots": int(len(real)), "overall_mover": out["overall"]["mover"],
            "control_mover": out["control_mover"], "limits": {k: v for k, v in lim.items() if k in ("rate_50", "rate_90", "snr_50", "mag_ab_50")},
            "headline": out["headline"], "wall_s": out["wall_s"]}
