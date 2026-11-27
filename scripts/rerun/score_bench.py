#!/usr/bin/env python3
"""What the new extraction finds and misses on the bench photos, against the old extraction of the same photos and
against things we know are there. Everything is read locally; run after `rerun.py bench`:
    uv run --project pipeline python scripts/rerun/score_bench.py [--data ~/skyshift-data] [--root <data>/rerun-bench]

After  = <root>/<v2 dots folder>/, its _peaks/ folder and <root>/session_stacks/ (the bench's output).
Before = <data>/before/dots/ (sky run) or <data>/before/dots_deblend1e-4_cp3_g6_psf/ (the featured zones' fine run).
Truth  = Gaia DR3 G < 20 in <data>/refcat/<zone>.parquet moved to each photo's time (isolated: no other object
         within 12.3″, where a CatWISE / 2MASS entry within 2.5″ is the star itself, another Gaia entry always counts
         (isolated_gaia); more than 30″ inside the footprint); MPC asteroids and comets at each photo's time from
         SPHEREx (<data>/cache/mpc/<zone>/run_*.json per_photo); 306P/LINEAR (reports-16nov/ecl04-306P-vs-dots.csv);
         3I/ATLAS (scripts/validate/data/photolists/3i-atlas.csv); the featured targets (web/src/data/targets.json).
Ext = <root>/<v2 dots folder>_ext/ (the extended pass, when written): named rows `ext`, known `after_peaks_ext`.
dot_flag (v2 files from 18 Nov, config DOT_FLAGS): dots per photo carrying each bit (`dot_flag_per_photo`), and the
nearest dot's dot_flag / sep_flag in every named row, so a find can be told apart by the kind of dot that made it.
A match is the nearest dot within --star-arcsec (stars, 3″) or --body-arcsec (bodies, 6.15″ = 1 px). Chance: the same
truth shifted 45″ (the `control` columns), so a found share can be read against it.
Writes <root>/score.json and prints the tables.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import rerun as R  # noqa: E402
from skyshift_core import flags as F  # noqa: E402
from skyshift_pipeline import coadd  # noqa: E402
from skyshift_pipeline import config as C  # noqa: E402
from skyshift_pipeline.refcat import move_to_epoch  # noqa: E402
from skyshift_pipeline.sky import Footprint, SkyTree  # noqa: E402

FINE_ZONES = set(C.DOTS_FINE_ZONES)
G_BINS = [0, 12, 14, 16, 17, 18, 19, 20]
V_BINS = [0, 16, 17, 18, 19, 20, 30]
RATE_BINS = [0, 50, 150, 386, 1000, 6000]  # ″/h: main belt, fast main belt, the pair rule's limit, NEOs, trails
NAMED = ("Galatea", "135P/Shoemaker-Levy", "98P/Takamizawa", "Iapetus", "Titan", "Rhea", "2006 GC1", "2026 GU", "2025 TX2")
EDGE_DEG = 30 / 3600
SHIFT_ARCSEC = 45.0
BRIGHT_G = 12.0
BRIGHT_G_BINS = [-5, 6, 8, 10, 11, 12]
PHOTO_DEG2 = (2040 * C.PIXEL_ARCSEC / 3600) ** 2


def read(p: Path, cols=None) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(p, columns=cols)
    except (OSError, ValueError):
        return None


def near(tree: SkyTree | None, ra, dec, arcsec: float) -> tuple[np.ndarray, np.ndarray]:
    """(found, index of nearest) for each position."""
    ra, dec = np.atleast_1d(ra), np.atleast_1d(dec)
    if tree is None or len(ra) == 0:
        return np.zeros(len(ra), bool), np.full(len(ra), -1)
    d, i = tree.nearest(ra, dec)
    return d <= arcsec, i


def shifted(ra, dec, arcsec: float = SHIFT_ARCSEC):
    dec2 = np.asarray(dec) + arcsec / 3600 / np.sqrt(2)
    ra2 = np.asarray(ra) + arcsec / 3600 / np.sqrt(2) / np.cos(np.deg2rad(dec2))
    return ra2, dec2


def in_box(z, ra, dec) -> np.ndarray:
    """Inside the zone's box (its catalogue's area), shrunk by 30″."""
    if z is None:
        return np.ones(len(ra), bool)
    m = EDGE_DEG
    return (ra > z.ra_min + m / max(np.cos(np.deg2rad(z.dec_max)), 0.05)) & (ra < z.ra_max - m / max(np.cos(np.deg2rad(z.dec_max)), 0.05)) \
        & (dec > z.dec_min + m) & (dec < z.dec_max - m)


def finite(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Rows with a finite ra and dec (index reset, so a tree's indices are row numbers)."""
    if df is None:
        return None
    ok = np.isfinite(df["ra"].to_numpy(np.float64)) & np.isfinite(df["dec"].to_numpy(np.float64))
    return df[ok].reset_index(drop=True)


ISO_ARCSEC = 12.3  # a scored Gaia star has no other object this close
SELF_ARCSEC = 2.5  # an entry of another catalogue (CatWISE, 2MASS) this close to a Gaia star is that star itself


def n_within(tree: SkyTree | None, ra, dec, arcsec: float) -> np.ndarray:
    if tree is None:
        return np.zeros(np.atleast_1d(ra).size, np.int64)
    return np.array([len(x) for x in tree.query_radius(ra, dec, arcsec)], np.int64)


def isolated_gaia(ra, dec, all_tree: SkyTree | None, other_tree: SkyTree | None,
                  iso: float = ISO_ARCSEC, own: float = SELF_ARCSEC) -> np.ndarray:
    """True for a Gaia star (ra, dec at the photo's time) with no OTHER object within `iso`″. `all_tree` holds every
    refcat entry, the star included; `other_tree` only the non-Gaia ones (CatWISE, 2MASS): those within `own`″ are
    the star's own counterparts and do not count. Another Gaia entry always counts. Before 27 Nov the rule was
    `n_within(all_tree, iso) == 1` (isolated_gaia_old): in zones whose refcat has CatWISE and 2MASS (hunt, t_gc,
    t_nep, t_lmc) a star's own infrared entries made it 'crowded', so only Gaia stars without an infrared
    counterpart were scored, a sample SPHEREx mostly cannot see."""
    return n_within(all_tree, ra, dec, iso) - n_within(other_tree, ra, dec, own) == 1


def isolated_gaia_old(ra, dec, all_tree: SkyTree | None, iso: float = ISO_ARCSEC) -> np.ndarray:
    """The rule before 27 Nov, kept only for the comparison table gaia_isolation_rules."""
    return n_within(all_tree, ra, dec, iso) == 1


def tree_of(df: pd.DataFrame | None, ra="ra", dec="dec") -> SkyTree | None:
    return SkyTree(df[ra].to_numpy(), df[dec].to_numpy()) if df is not None and len(df) else None


def load_known(data: Path, zone: str) -> pd.DataFrame:
    rows = []
    for f in glob.glob(str(data / "cache" / "mpc" / zone / "run_*.json")) + glob.glob(str(data / "cache" / "planets" / zone / "run_*.json")):
        d = json.load(open(f))
        pp, ob = d.get("per_photo") or {}, d.get("objects") or {}
        if not pp:
            continue
        names = ob.get("name", [])
        v, cls = dict(zip(names, ob.get("v", []))), dict(zip(names, ob.get("class", [])))
        dra = np.asarray(ob.get("dra_arcsec_h", [np.nan] * len(names)), float)
        ddec = np.asarray(ob.get("ddec_arcsec_h", [np.nan] * len(names)), float)
        rate = dict(zip(names, np.hypot(dra, ddec)))
        pa = dict(zip(names, np.degrees(np.arctan2(dra, ddec)) % 180.0))  # dRA·cos dec, dDec: east of north, no sign
        df = pd.DataFrame(pp)
        df["v"], df["class"], df["rate"] = df["name"].map(v), df["name"].map(cls), df["name"].map(rate)
        df["pa"] = df["name"].map(pa)
        rows.append(df)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["obs_id", "detector", "name", "ra", "dec", "v"])


def streak_hits(tr: SkyTree | None, st: pd.DataFrame | None, ra, dec, pa, arcsec: float, dpa: float = 20.0) -> np.ndarray:
    """A streak centred within `arcsec` of each position whose sky PA is within `dpa` of `pa` (NaN pa: any)."""
    ra, dec, pa = np.atleast_1d(ra), np.atleast_1d(dec), np.atleast_1d(np.asarray(pa, float))
    out = np.zeros(len(ra), bool)
    if tr is None or not len(ra):
        return out
    for i, li in enumerate(tr.query_radius(ra, dec, arcsec)):
        if len(li):
            d = np.abs((st["pa_deg"].to_numpy()[li] - pa[i] + 90) % 180 - 90)
            out[i] = bool(np.isnan(pa[i]) or (d <= dpa).any())
    return out


def num(df: pd.DataFrame) -> pd.DataFrame:
    """`before` as a number (NaN where there is no old dot file), so means are shares."""
    return df.astype({"before": float}) if "before" in df else df


def frac(ok: pd.Series) -> float | None:
    return round(float(ok.mean()), 3) if len(ok) else None


def table(df: pd.DataFrame, by: list[str], cols: list[str]) -> list[dict]:
    if df.empty:
        return []
    g = df.groupby(by, observed=True)
    out = g.size().rename("n").to_frame()
    for c in cols:
        out[c] = g[c].mean().round(3)
    return out.reset_index().to_dict("records")


def bright_table(b: pd.DataFrame) -> list[dict]:
    """Every Gaia G < 12 star inside the footprint (moved to the photo's time; crowded or not) by G: the share with
    a dot within 3″ and 6″, the nearest dot's offset (median, 90th percentile) for those within 6″, and the share of
    those nearest dots that carry OVERFLOW/NONLINEAR at the peak or the saturated-core bit."""
    if not len(b):
        return []
    rows = []
    for gb, q in b.groupby("g_bin", observed=True):
        m6 = q["sep"] <= 6.0
        rows.append({"g_bin": str(gb), "n": int(len(q)), "found_3": frac(q["sep"] <= 3.0), "found_6": frac(m6),
                     "sep_p50": round(float(q.loc[m6, "sep"].median()), 2) if m6.any() else None,
                     "sep_p90": round(float(q.loc[m6, "sep"].quantile(0.9)), 2) if m6.any() else None,
                     "sat_share": frac(q.loc[m6, "sat"]) if m6.any() else None,
                     "core_dot_share": frac(q.loc[m6, "core_dot"]) if m6.any() else None})
    return rows


def named_bodies(bo: pd.DataFrame) -> list[dict]:
    """Comets, planets' moons and fast bodies (> 150″/h) one by one: sightings (photos), how many have an old dot file,
    found by the old dots, the new dots, a dot or peak, a dot / peak / ext row, a streak, a dot or streak (6.15″)."""
    if not len(bo) or "cls" not in bo:
        return []
    cls, name = bo["cls"].astype(str), bo["name"].astype(str)
    comet = cls.str.lower().str.contains("comet") | name.str.contains("/")
    q = bo[comet | cls.isin(["moon", "planet"]) | (bo["rate"] > 150)].copy()
    q.loc[comet[q.index], "cls"] = "comet"
    if not len(q):
        return []
    for c in ("after_peaks_ext", "after_streak", "after_or_streak"):
        if c not in q:
            q[c] = False
    q["before_n"] = q["before"].fillna(0).astype(float)
    g = q.groupby("name", observed=True)
    out = g.agg(cls=("cls", "first"), v=("v", "first"), rate=("rate", "mean"), n=("after", "size"),
                n_before=("has_before", "sum"), before=("before_n", "sum"), after=("after", "sum"),
                after_peaks=("after_peaks", "sum"), after_peaks_ext=("after_peaks_ext", "sum"),
                streak=("after_streak", "sum"), dot_or_streak=("after_or_streak", "sum"))
    return out.round(1).reset_index().sort_values(["cls", "rate"]).to_dict("records")


def isolation_rules(st: pd.DataFrame) -> list[dict]:
    """Gaia found shares per zone and G bin under the old isolation rule (iso_old) and the new one (iso)."""
    if st.empty or "iso_old" not in st:
        return []
    out = []
    for rule, col in (("old", "iso_old"), ("new", "iso")):
        for by in (["zone"], ["zone", "g_bin"], ["g_bin"]):
            for r in table(st[st[col]], by, ["before", "after", "after_peaks"]):
                out.append({"rule": rule, "zone": r.get("zone", "all"), "g_bin": str(r.get("g_bin", "all")), **{
                    k: v for k, v in r.items() if k not in ("zone", "g_bin")}})
    return out


def streak_report(rows: list) -> dict:
    """Streak files (<folder>_streaks/): rows and bytes per photo by detector, clean ones (why 0), and how many repeat
    in another exposure of the same session and detector (a streak centred within 6″ at a PA within 20°: a fixed
    source, not a mover; a real trail moves by its own length in ~2 exposures' time)."""
    if not rows:
        return {}
    st = pd.concat(rows, ignore_index=True)
    photos = st.drop_duplicates("photo")[["photo", "detector", "file_bytes"]]
    st = st[st["ra"].notna()].reset_index(drop=True)
    st["static"] = False
    for _, g in st.groupby(["zone", "session", "detector"]):
        if g["photo"].nunique() < 2:
            continue
        tr = SkyTree(g["ra"].to_numpy(), g["dec"].to_numpy())
        ph, pa = g["photo"].to_numpy(), g["pa_deg"].to_numpy()
        for i, li in zip(g.index, tr.query_radius(g["ra"].to_numpy(), g["dec"].to_numpy(), 6.0)):
            j = np.asarray(li)
            k = g.index.get_loc(i)
            other = j[ph[j] != ph[k]]
            if len(other) and (np.abs((pa[other] - pa[k] + 90) % 180 - 90) <= 20).any():
                st.loc[i, "static"] = True
    st["clean"] = st["why"] == 0
    per = st.groupby("photo").agg(n=("ra", "size"), clean=("clean", "sum"),
                                  moving=("static", lambda v: int((~v).sum())),
                                  moving_clean=("static", lambda v: int((~v & st.loc[v.index, "clean"]).sum())))
    per = photos.set_index("photo").join(per).fillna(0)
    byd = per.groupby("detector").agg(photos=("n", "size"), streaks=("n", "mean"), clean=("clean", "mean"),
                                      not_repeated=("moving", "mean"), not_repeated_clean=("moving_clean", "mean"),
                                      kb=("file_bytes", lambda v: v.mean() / 1e3)).round(1).reset_index()
    deg2 = len(per) * PHOTO_DEG2
    return {"photos": int(len(per)), "streaks_per_photo": round(float(per["n"].mean()), 1),
            "clean_per_photo": round(float(per["clean"].mean()), 1),
            "not_repeated_per_photo": round(float(per["moving"].mean()), 1),
            "not_repeated_clean_per_photo": round(float(per["moving_clean"].mean()), 1),
            "not_repeated_clean_per_deg2": round(float(per["moving_clean"].sum() / deg2), 2),
            "mb_per_photo": round(float(per["file_bytes"].mean()) / 1e6, 3),
            "by_detector": byd.to_dict("records")}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(R.DEFAULT_DATA))
    ap.add_argument("--root", default=None)
    ap.add_argument("--targets", default=str(R.TARGETS))
    ap.add_argument("--star-arcsec", type=float, default=3.0)
    ap.add_argument("--body-arcsec", type=float, default=C.PIXEL_ARCSEC)
    ap.add_argument("--out", default=None, help="where to write the report (default <root>/score.json)")
    ap.add_argument("--stars-out", default=None,
                    help="also write every isolated Gaia star's row (photo, ra, dec, g_mag, found) to this parquet "
                         "(scripts/rerun/cond_report.py reads it)")
    a = ap.parse_args(argv)
    data = Path(a.data)
    root = Path(a.root or data / "rerun-bench")
    folder = C.zone_dots(None)[0]
    sel = R.bench_photos(data, json.loads(Path(a.targets).read_text())["targets"])

    stars, bodies, unmatched, named, counts, bright, streak_rows = [], [], [], [], [], [], []
    refcats: dict[str, pd.DataFrame | None] = {}
    knowns: dict[str, pd.DataFrame] = {}
    try:
        p306 = pd.read_csv(data / "reports-16nov" / "ecl04-306P-vs-dots.csv")
        p306 = p306[p306["frame"] == "spherex"]
    except OSError:
        p306 = None
    try:
        atlas = pd.read_csv(REPO / "scripts" / "validate" / "data" / "photolists" / "3i-atlas.csv")
    except OSError:
        atlas = None
    feat = {t["slug"]: t for t in json.load(open(REPO / "web" / "src" / "data" / "targets.json"))["targets"]}

    for zone, rows, tgt in sel:
        if zone not in refcats:
            rc = read(data / "refcat" / f"{zone}.parquet")
            refcats[zone] = rc
            knowns[zone] = load_known(data, zone)
        rc, kn = refcats[zone], knowns[zone]
        zbox = R.zone_box(zone, tgt)  # a wide-sample zone (w_<name>) has its box in the targets file
        stack_keys = {}
        for t in coadd.session_tasks(rows):
            for o in t["obs_ids"]:
                stack_keys[(o, int(t["detector"]))] = t["key"]
        for r in rows.itertuples():
            pid = f"{r.obs_id}D{int(r.detector)}"
            det = int(r.detector)
            aft = read(root / folder / f"{pid}.parquet")
            if aft is None:
                continue
            pk = read(root / f"{folder}_peaks" / f"{pid}.parquet")
            ext = finite(read(root / f"{folder}_ext" / f"{pid}.parquet"))  # the extended pass's rows, when written
            te = tree_of(ext)
            sk = finite(read(root / f"{folder}_streaks" / f"{pid}.parquet"))
            tk = tree_of(sk)
            if sk is not None:
                fsz = (root / f"{folder}_streaks" / f"{pid}.parquet").stat().st_size
                streak_rows.append(sk.assign(zone=zone, session=int(r.session), detector=det, photo=pid,
                                             mjd=float(r.mjd), file_bytes=fsz)[
                    ["zone", "session", "detector", "photo", "mjd", "ra", "dec", "pa_deg", "length_px", "rate_arcsec_h",
                     "snr", "snr_local", "why", "file_bytes"]])
            bdir = "dots_deblend1e-4_cp3_g6_psf" if zone in FINE_ZONES else "dots"
            bef = read(data / "before" / bdir / f"{pid}.parquet")
            sk_key = stack_keys.get((str(r.obs_id), det))
            stk = read(root / C.SESSION_STACK_DIR / f"{sk_key}.parquet") if sk_key else None
            if stk is not None and "ra_psf" in stk:  # the ePSF position, else the map peak's
                stk = stk.assign(ra=stk["ra_psf"].fillna(stk["ra"]), dec=stk["dec_psf"].fillna(stk["dec"]))
            aft, bef, pk, stk = (finite(x) for x in (aft, bef, pk, stk))
            fp = Footprint.from_s_region(r.s_region)
            ta, tb, tp, ts = tree_of(aft), tree_of(bef), tree_of(pk), tree_of(stk)
            dfl = aft["dot_flag"].to_numpy().astype(np.int64) if "dot_flag" in aft else np.zeros(len(aft), np.int64)
            counts.append({"zone": zone, "detector": det, "photo": pid, "after": len(aft),
                           "before": None if bef is None else len(bef), "peaks": None if pk is None else len(pk),
                           "stack_rows": None if stk is None else len(stk),
                           "ext_rows": np.nan if ext is None else len(ext),
                           "streak_rows": np.nan if sk is None else len(sk),
                           "dot_flag_any": int((dfl != 0).sum()),
                           **{f"dot_flag_{k}": int(((dfl & v) != 0).sum()) for k, v in C.DOT_FLAGS.items()}})
            # --- catalogue truth at this photo's time
            cat_tree = None
            if rc is not None and len(rc):
                rra, rdec = move_to_epoch(rc, float(r.mjd))
                inside = fp.edge_distance_deg(rra, rdec) > EDGE_DEG
                cat_tree = SkyTree(rra[inside], rdec[inside]) if inside.any() else None
                oth = inside & (rc["cat"].to_numpy() != "gaia")
                other_tree = SkyTree(rra[oth], rdec[oth]) if oth.any() else None
                g = rc[inside].assign(ra_t=rra[inside], dec_t=rdec[inside])
                g = g[(g["cat"] == "gaia") & (g["g_mag"] < 20)]
                b = rc[inside].assign(ra_t=rra[inside], dec_t=rdec[inside])
                b = b[(b["cat"] == "gaia") & (b["g_mag"] < BRIGHT_G)]
                if len(b) and ta is not None:
                    bd, bi = ta.nearest(b["ra_t"].to_numpy(), b["dec_t"].to_numpy())
                    fp_ = aft["flags_peak"].to_numpy() if "flags_peak" in aft else np.zeros(len(aft), int)
                    sf = aft["dot_flag"].to_numpy() if "dot_flag" in aft else np.zeros(len(aft), int)
                    bright.append(pd.DataFrame({"zone": zone, "detector": det, "photo": pid,
                                                "g_bin": pd.cut(b["g_mag"], BRIGHT_G_BINS), "sep": bd,
                                                "sat": (fp_[bi].astype(np.int64) & F.SAT_BITS) != 0,
                                                "core_dot": (sf[bi].astype(np.int64) & C.DOT_FLAG_SAT_CORE) != 0}))
                if len(g) and cat_tree is not None:
                    gra_, gdec_ = g["ra_t"].to_numpy(), g["dec_t"].to_numpy()
                    iso_new = isolated_gaia(gra_, gdec_, cat_tree, other_tree)
                    iso_old = isolated_gaia_old(gra_, gdec_, cat_tree)
                    g = g.assign(iso=iso_new, iso_old=iso_old)[iso_new | iso_old]
                if len(g):
                    gra, gdec = g["ra_t"].to_numpy(), g["dec_t"].to_numpy()
                    cra, cdec = shifted(gra, gdec)
                    s = pd.DataFrame({"zone": zone, "detector": det, "g_bin": pd.cut(g["g_mag"], G_BINS),
                                      "after": near(ta, gra, gdec, a.star_arcsec)[0],
                                      "after_peaks": near(ta, gra, gdec, a.star_arcsec)[0] | near(tp, gra, gdec, a.star_arcsec)[0],
                                      "after_stack": near(ts, gra, gdec, a.star_arcsec)[0],
                                      "control_after": near(ta, cra, cdec, a.star_arcsec)[0]})
                    s["before"] = near(tb, gra, gdec, a.star_arcsec)[0] if tb is not None else np.nan
                    s["has_before"] = tb is not None
                    s["iso"] = g["iso"].to_numpy() if "iso" in g else True
                    s["iso_old"] = g["iso_old"].to_numpy() if "iso_old" in g else True
                    if a.stars_out:  # each star's own row (conditions report: Gaia found per tile)
                        s["photo"], s["ra"], s["dec"], s["g_mag"] = pid, gra, gdec, g["g_mag"].to_numpy()
                    stars.append(s)
            # --- known bodies at this photo's time
            k = kn[(kn["obs_id"] == r.obs_id) & (kn["detector"] == det)] if len(kn) else kn
            if len(k):
                k = k[fp.edge_distance_deg(k["ra"].to_numpy(), k["dec"].to_numpy()) > EDGE_DEG]
            if len(k):
                kra, kdec = k["ra"].to_numpy(), k["dec"].to_numpy()
                cra, cdec = shifted(kra, kdec)
                b = pd.DataFrame({"zone": zone, "detector": det, "name": k["name"].values, "v": k["v"].values,
                                  "v_bin": pd.cut(k["v"], V_BINS), "rate_bin": pd.cut(k["rate"], RATE_BINS), "after": near(ta, kra, kdec, a.body_arcsec)[0],
                                  "after_peaks": near(ta, kra, kdec, a.body_arcsec)[0] | near(tp, kra, kdec, a.body_arcsec)[0],
                                  "after_stack": near(ts, kra, kdec, a.body_arcsec)[0],
                                  "control_after": near(ta, cra, cdec, a.body_arcsec)[0],
                                  "control_peaks": near(tp, cra, cdec, a.body_arcsec)[0],
                                  "has_streaks": sk is not None,
                                  "after_streak": streak_hits(tk, sk, kra, kdec, k["pa"].to_numpy(), a.body_arcsec),
                                  "control_streak": streak_hits(tk, sk, cra, cdec, k["pa"].to_numpy(), a.body_arcsec)})
                if te is not None:  # a dot, a peak or an extended-pass row
                    b["after_peaks_ext"] = b["after_peaks"] | near(te, kra, kdec, a.body_arcsec)[0]
                    b["control_peaks_ext"] = b["control_peaks"] | near(tp, cra, cdec, a.body_arcsec)[0] \
                        | near(te, cra, cdec, a.body_arcsec)[0]
                b["after_or_streak"] = b["after"] | b["after_streak"]
                b["rate"] = k["rate"].to_numpy()
                b["cls"] = k["class"].to_numpy() if "class" in k else ""
                b["before"] = near(tb, kra, kdec, a.body_arcsec)[0] if tb is not None else np.nan
                b["has_before"] = tb is not None
                bodies.append(b)
            # --- dots with nothing known under them (catalogue 3″, body 6″), by detector and the weak-spot rows
            ktree = tree_of(k) if len(k) else None
            for name, d in (("after", aft), ("before", bef)):
                if d is None or not len(d):
                    continue
                inside = (fp.edge_distance_deg(d["ra"].to_numpy(), d["dec"].to_numpy()) > EDGE_DEG) \
                    & in_box(zbox, d["ra"].to_numpy(), d["dec"].to_numpy())  # the catalogue covers the zone box only
                d = d[inside]
                un = ~near(cat_tree, d["ra"].to_numpy(), d["dec"].to_numpy(), a.star_arcsec)[0]
                if ktree is not None:
                    un &= ~near(ktree, d["ra"].to_numpy(), d["dec"].to_numpy(), a.body_arcsec)[0]
                # shifted control: the same dots against the catalogue and bodies moved 45″ (what chance matches)
                sra, sdec = shifted(d["ra"].to_numpy(), d["dec"].to_numpy())
                un_s = ~near(cat_tree, sra, sdec, a.star_arcsec)[0]
                if ktree is not None:
                    un_s &= ~near(ktree, sra, sdec, a.body_arcsec)[0]
                y = d["y"].to_numpy()
                unmatched.append({"zone": zone, "detector": det, "photo": pid, "which": name, "dots": int(len(d)),
                                  "unmatched": int(un.sum()), "unmatched_shifted": int(un_s.sum()), "unmatched_snr_lt7": int((un & (d["snr"].to_numpy() < 7)).sum()),
                                  "unmatched_rows_lt330": int((un & (y < 330)).sum()),
                                  "unmatched_rows_ge1975": int((un & (y >= 1975)).sum()),
                                  "has_before": bef is not None})
            # --- named things
            checks = []
            if p306 is not None and zone == "ecl04":
                q = p306[p306["obs"] == r.obs_id]
                if len(q) and fp.contains(q["pred_ra"].values, q["pred_dec"].values)[0]:
                    checks.append(("306P/LINEAR", float(q["pred_ra"].iloc[0]), float(q["pred_dec"].iloc[0])))
            if atlas is not None:
                q = atlas[(atlas["obs_id"] == r.obs_id) & (atlas["detector"] == det)]
                if len(q):
                    checks.append(("3I/ATLAS", float(q["obj_ra"].iloc[0]), float(q["obj_dec"].iloc[0])))
            for nm in NAMED:
                q = k[k["name"] == nm] if len(k) else k
                if len(q):
                    checks.append((nm, float(q["ra"].iloc[0]), float(q["dec"].iloc[0])))
            slug = zone[2:] if zone.startswith("t_") else None
            if slug in feat and rc is not None and len(rc):
                t = feat[slug]
                rra, rdec = move_to_epoch(rc, float(r.mjd))
                dd = np.hypot((rra - t["ra"]) * np.cos(np.deg2rad(t["dec"])), rdec - t["dec"]) * 3600
                c = rc.assign(sep=dd, ra_t=rra, dec_t=rdec)
                c = c[c["sep"] < 90]
                if len(c):
                    pm = np.hypot(c["pmra"].fillna(0), c["pmdec"].fillna(0))
                    pick = c.loc[(pm if pm.max() > 500 else -c["g_mag"].fillna(30)).idxmax()]
                    checks.append((t["name"], float(pick["ra_t"]), float(pick["dec_t"])))
                    for which, fdir in (("after", root / C.FORCED_DIR), ("before", data / "before" / "forced")):
                        fo = read(fdir / f"{pid}.parquet")
                        q = fo[fo["ref_id"] == pick["ref_id"]] if fo is not None and "ref_id" in fo else None
                        named.append({"name": t["name"] + " forced", "photo": pid, "zone": zone, "detector": det,
                                      which: None if q is None or not len(q) else
                                      {"flux_psf_ujy": round(float(q["flux_psf_ujy"].iloc[0]), 1),
                                       "flux_ap2_ujy": round(float(q["flux_ap2_ujy"].iloc[0]), 1),
                                       "saturated": bool(int(q["flags"].iloc[0]) & F.SAT_BITS)}})
                else:
                    checks.append((t["name"] + " (frame centre)", t["ra"], t["dec"]))
            for nm, ra0, dec0 in checks:
                if not fp.contains([ra0], [dec0])[0]:
                    continue
                rec = {"name": nm, "photo": pid, "zone": zone, "detector": det}
                for which, d, tr in (("after", aft, ta), ("peaks", pk, tp), ("stack", stk, ts), ("before", bef, tb),
                                     ("ext", ext, te), ("streak", sk, tk)):
                    if tr is None:
                        rec[which] = None
                        continue
                    dist, i = tr.nearest(np.array([ra0]), np.array([dec0]))
                    row = d.iloc[int(i[0])]
                    rec[which] = {"sep_arcsec": round(float(dist[0]), 2),
                                  "snr": round(float(row.get("snr", row.get("snr_mf", row.get("snr_ext", np.nan)))), 1),
                                  "saturated": bool(int(row.get("flags_peak", 0) or 0) & F.SAT_BITS) if "flags_peak" in d else None}
                    if which == "after":  # the kind of dot (sep_flag passes 0x200 / 0x400; dot_flag config DOT_FLAGS)
                        rec[which]["sep_flag"] = int(row.get("sep_flag", 0) or 0)
                        rec[which]["dot_flag"] = int(row.get("dot_flag", 0) or 0)
                named.append(rec)

    st = pd.concat(stars, ignore_index=True) if stars else pd.DataFrame()
    bo = pd.concat(bodies, ignore_index=True) if bodies else pd.DataFrame()
    un = pd.DataFrame(unmatched)
    st_all = st[st["has_before"]] if len(st) else st  # stars isolated by either rule
    both_st = st_all[st_all["iso"]] if len(st_all) else st_all  # the scored sample: isolated_gaia
    both_bo = bo[bo["has_before"]] if len(bo) else bo
    rep = {
        "photos_scored": int(len(counts)),
        "dots_per_photo": table(pd.DataFrame(counts), ["detector"],
                                ["after", "before", "peaks", "stack_rows", "ext_rows", "streak_rows"]),
        "dot_flag_per_photo": table(pd.DataFrame(counts), ["detector"],
                                    ["after", "dot_flag_any"] + [f"dot_flag_{k}" for k in C.DOT_FLAGS]),
        "gaia_found_by_detector": table(num(both_st), ["detector"], ["before", "after", "after_peaks",
                                                                               "after_stack", "control_after"]),
        "gaia_found_by_g": table(num(both_st), ["g_bin"], ["before", "after", "after_peaks",
                                                                     "after_stack", "control_after"]),
        "gaia_found_by_zone": table(num(both_st), ["zone", "detector"], ["before", "after", "after_peaks"]),
        "gaia_isolation_rules": isolation_rules(num(st_all)),
        "known_found_by_v": table(num(both_bo), ["v_bin"], ["before", "after", "after_peaks",
                                                                      "after_stack", "control_after", "control_peaks"]
                                  + [c for c in ("after_peaks_ext", "control_peaks_ext") if c in both_bo]),
        "known_found_by_rate": table(num(both_bo), ["rate_bin"], ["before", "after", "after_peaks", "after_stack",
                                                                  "control_after"]),
        "known_found_by_detector": table(num(both_bo), ["detector"], ["before", "after", "after_peaks",
                                                                                 "after_stack", "control_after"]),
        "known_found_by_rate_streaks": (table(bo[bo["has_streaks"]], ["rate_bin"], ["after", "after_streak", "after_or_streak",
                                                                                   "control_after", "control_streak"])
                                        if len(bo) and "has_streaks" in bo else []),
        "fast_known_streaks": (bo[bo["has_streaks"] & (bo["rate"] > 150)]
                               .groupby(["name", "detector"], observed=True)
                               .agg(rate=("rate", "mean"), v=("v", "first"), n=("after", "size"), dot=("after", "sum"),
                                    streak=("after_streak", "sum"), either=("after_or_streak", "sum"))
                               .round(1).reset_index().to_dict("records") if len(bo) and "has_streaks" in bo else []),
        # every scored photo, with or without an old dot file (the wide sample's photos have none: wide_targets.json)
        "gaia_found_by_detector_all": table(st, ["detector"], ["after", "after_peaks", "after_stack", "control_after"]),
        "gaia_found_by_g_all": table(st, ["g_bin"], ["after", "after_peaks", "after_stack", "control_after"]),
        "gaia_found_by_zone_all": table(st, ["zone", "detector"], ["after", "after_peaks", "after_stack",
                                                                   "control_after"]),
        "known_found_by_v_all": table(bo, ["v_bin"], ["after", "after_peaks", "after_stack", "control_after",
                                                      "control_peaks"]),
        "known_found_by_rate_all": table(bo, ["rate_bin"], ["after", "after_peaks", "after_stack", "control_after"]),
        "known_found_by_zone_all": table(bo[pd.to_numeric(bo["v"], errors="coerce") < 18] if len(bo) else bo, ["zone"],
                                         ["after", "after_peaks", "after_stack", "control_after"]),
        "streaks": streak_report(streak_rows),
        "named_bodies": named_bodies(bo),
        "unmatched_per_photo": (un.groupby(["which", "detector"])[["dots", "unmatched", "unmatched_shifted",
                                                                    "unmatched_snr_lt7",
                                                                    "unmatched_rows_lt330", "unmatched_rows_ge1975"]]
                                .mean().round(1).reset_index().to_dict("records")) if len(un) else [],
        "bright_found_by_g": bright_table(pd.concat(bright, ignore_index=True) if bright else pd.DataFrame()),
        "named": named,
    }
    out = Path(a.out) if a.out else root / "score.json"
    out.write_text(json.dumps(rep, indent=1, default=str))
    if a.stars_out and len(st):  # isolated by the current rule only (isolated_gaia), not the old one
        cols = ["zone", "detector", "photo", "ra", "dec", "g_mag", "after", "after_peaks", "control_after"]
        st.loc[st["iso"].astype(bool), cols].to_parquet(a.stars_out, index=False)
    pd.set_option("display.width", 200)
    for k in ("dots_per_photo", "dot_flag_per_photo", "gaia_found_by_detector", "gaia_found_by_g", "known_found_by_v",
              "known_found_by_rate", "known_found_by_detector", "unmatched_per_photo", "bright_found_by_g",
              "known_found_by_rate_streaks", "fast_known_streaks", "gaia_found_by_g_all", "known_found_by_v_all"):
        print(f"\n== {k}\n{pd.DataFrame(rep[k]).to_string(index=False)}")
    if rep["streaks"]:
        print("\n== streaks per photo, by detector")
        print(pd.DataFrame(rep["streaks"]["by_detector"]).to_string(index=False))
        print({k: v for k, v in rep["streaks"].items() if k != "by_detector"})
    print("\n== named")
    for n in named:
        print(" ", n)
    print(f"\n{out}")
    return rep


if __name__ == "__main__":
    main()
