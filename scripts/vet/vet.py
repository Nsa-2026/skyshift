"""Vetting tools for one finding at a time (see .claude/skills/vet-finding/SKILL.md).

Run from pipeline/ (its venv has everything):   uv run python ../scripts/vet/vet.py <tool> ...

  info <id>                                  the engine's row(s) for a candidate + sky context (ecliptic, Sun)
  packet <id> --out DIR                      mover: the 3-row image + every engine number + catalogue context
  session <id> --out DIR                     mover: every exposure of its session over the spot, all detectors
  stamps --zone Z --ra R --dec D --out DIR   any position: per-visit stacks (+ visit difference), or with --mjd M
         [--det 1..6] [--mjd M] [--n 6] [--mark RA,DEC ...]   the n single exposures nearest to M
  catalog --zone Z --ra R --dec D [--radius 30] [--mjd M]     Gaia / 2MASS / CatWISE / our fixed sources around it
  known --zone Z --ra R --dec D --mjd M [--radius 600]        known asteroids, comets, planets, moons predicted there
  spectrum <id> --out DIR                    the source's 102-channel spectrum (plot + table)
  dots --zone Z --ra R --dec D [--radius 20]                  our own unassigned detections near a position, any time
  movers --zone Z --ra R --dec D --mjd M [--radius 120]       our movers of that session near a position (after the linker's
                                                              own checks: rejected pairs are not stored anywhere)
  pixel --zone Z --det N --x X --y Y                          history of a detector pixel and its column (hot pixels, glows)
  track <id> [--days 3]                                       mover: where its track puts it in other sessions, what is there

Images are written as PNG into --out and their path is printed; open them with the Read tool.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vetlib as V  # noqa: E402
from skyshift_pipeline.refcat import move_to_epoch  # noqa: E402
from skyshift_pipeline.sky import angsep  # noqa: E402

POOL = ThreadPoolExecutor(12)


def stop(msg: str):
    """Say why a tool cannot answer, on standard output (so it is never hidden), and end."""
    print(msg)
    sys.exit(1)


def need(zone: str, name: str) -> pd.DataFrame:
    t = V.table(zone, name)
    if t is None:
        stop(f"NOT AVAILABLE: {name} for zone {zone} is not in the local data ({V.DATA}/{zone}/{name}.parquet). "
                 f"Report this under could_not_check.")
    return t


def sky_context(ra: float, dec: float, mjd: float) -> list[str]:
    """Ecliptic coordinates, the Sun's elongation and the direction of the ecliptic at this spot."""
    from astropy import units as u
    from astropy.coordinates import GeocentricTrueEcliptic, SkyCoord, get_sun
    from astropy.time import Time
    t = Time(mjd, format="mjd")
    c = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    e = c.transform_to(GeocentricTrueEcliptic(equinox=t))
    sun = get_sun(t)
    elong = c.separation(SkyCoord(sun.ra, sun.dec, frame="icrs")).deg
    e2 = SkyCoord((e.lon.deg + 0.01) * u.deg, e.lat, frame=GeocentricTrueEcliptic(equinox=t)).icrs
    pa = c.position_angle(e2).deg  # direction of increasing ecliptic longitude (prograde), east of north
    return [f"Sky: galactic b = {c.galactic.b.deg:+.1f}°, ecliptic lon/lat = {e.lon.deg:.1f}°/{e.lat.deg:+.1f}°, "
            f"Sun elongation {elong:.0f}° on {t.utc.iso[:16]} UTC.",
            f"Ecliptic direction here: prograde (east along the ecliptic) is PA {pa:.0f}°, retrograde PA {(pa + 180) % 360:.0f}°. "
            f"Main-belt asteroids near elongation 90° move mostly prograde along the ecliptic at roughly 10-40\"/h."]


def cmd_info(a):
    c = V.candidates().get(a.id)
    zone = V.zone_of(a.id)
    if c:
        print("Site candidate row (candidates.json):")
        print(json.dumps(c, indent=1, ensure_ascii=False))
    else:
        print(f"{a.id}: no row in the site's candidates.json.")
    ra = dec = mjd = None
    mv = V.mover_row(a.id)
    if mv is not None:
        ra, dec, mjd = float(mv.ra), float(mv.dec), float(mv.mjd)
        keep = ["rate_arcsec_h", "pa_deg", "line_resid_arcsec", "motion_snr", "flux_ratio", "dup_dots", "n_dots", "n_exposures",
                "paired_exposures", "detectors", "evidence", "snr", "flux_ujy", "cover_sessions", "cover_exposures",
                "missed_exposures", "edge_exposures", "still_free", "session", "visit", "suspect_grade", "known_sep",
                "known_name", "known_class", "gaia_sep", "gaia_g", "fixed_sep"]
        print("\nEngine mover row (movers.parquet; known_sep / gaia_sep / fixed_sep = distance in arcsec to the nearest known "
              "body / Gaia star / fixed source):")
        print(json.dumps({k: (None if isinstance(mv[k], float) and not np.isfinite(mv[k]) else (mv[k].item() if hasattr(mv[k], "item") else mv[k]))
                          for k in keep if k in mv.index}, indent=1, default=str))
        sus = V.table(zone, "suspects")
        if sus is not None and (sus.id == a.id).any():
            r = sus[sus.id == a.id].iloc[0]
            gates = [k[5:] for k in r.index if k.startswith("gate_") and bool(r[k])]
            print(f"Engine vetting: grade {r.grade}; gates that fired: {gates or 'none'}; reasons {list(r.reasons)}; "
                  f"nearest fixed source {r.near_source_arcsec:.1f}\"; nearest known {r.nearest_known_arcsec}")
    for name, key in (("highpm", "-pm-"), ("shape", "-sh-"), ("sources", "-sp-")):
        if key in a.id or (name == "sources" and ("-pm-" in a.id or "-sh-" in a.id)):
            t = V.table(zone, name)
            sids = V.source_ids(a.id)
            if t is None:
                print(f"\n{name}.parquet for {zone}: NOT AVAILABLE locally.")
                continue
            r = t[t.source_id == sids[0]] if name != "sources" else t[t.source_id.isin(sids)]
            if name == "highpm" and len(sids) > 1 and "source_id_b" in t:
                r = r[r.source_id_b == sids[1]]
            if r.empty:
                print(f"\n{name}.parquet: no row for source {sids}.")
                continue
            for _, row in r.iterrows():
                d = {k: v for k, v in row.items() if not isinstance(v, (list, np.ndarray))}
                print(f"\nEngine {name} row:")
                print(json.dumps(d, indent=1, default=str))
                if ra is None:
                    ra, dec, mjd = float(row.ra), float(row.dec), float(row.get("mjd", np.nan))
                if name == "highpm":
                    print()
                    if row.get("method") == "pair":
                        print(f"PAIR METHOD: the engine matched two different sources, one per visit, {row.sep_arcsec:.0f}\" apart, as one star. "
                              f"Check both ends in every visit: stamps --ra {row.ra:.6f} --dec={row.dec:.6f} --mark {row.ra_b:.6f},{row.dec_b:.6f}. "
                              f"Both ends present in every visit = two fixed sources, not motion.")
                    print(V.gaia_offset_test(zone, float(row.ra), float(row.dec), float(row.mjd), float(row.pmra), float(row.pmdec)))
    if ra is None and c:
        ra, dec, mjd = c["ra"], c["dec"], c["mjd"]
    if ra is not None:
        print(f"\nPosition to use with the other tools: --zone {zone} --ra {ra:.6f} --dec={dec:.6f}" +
              (f" --mjd {mjd:.5f}" if mjd is not None and np.isfinite(mjd) else "") +
              "   (take coordinates from here, never from the digits of the id; write a negative Dec as --dec=-1.23)")
    if ra is not None and mjd is not None and np.isfinite(mjd):
        print()
        print("\n".join(sky_context(ra, dec, mjd)))
        ph = V.table(zone, "photos")
        if ph is not None:
            vis = ph.groupby("visit").mjd.agg(["min", "max", "count"])
            print("Visits of this zone (MJD range, photos): " + "; ".join(f"visit {v}: {r['min']:.1f}-{r['max']:.1f} ({int(r['count'])})" for v, r in vis.iterrows()))


def cmd_packet(a):
    mv = V.mover_row(a.id)
    if mv is None:
        stop(f"NOT AVAILABLE: {a.id} is not a mover in the local data (packet works for movers; use stamps/catalog for other kinds).")
    zone = V.zone_of(a.id)
    c = V.candidates().get(a.id, {})
    ctx = {"refcat": V.table(zone, "refcat"), "fixed": V.table(zone, "fixed"), "pred": V.table(zone, "pred"),
           "grade": c.get("grade"), "why": c.get("why", "")}
    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, f"{a.id}_packet.png")
    out = V.build_packet(mv, need(zone, "photos"), png, mode="blind" if a.blind else "full", ctx=ctx, pool=POOL)
    print(f"IMAGE: {png}")
    print("Rows: 1 = our detections; 2 = each detection minus the median of the same spot at other times (red = brighter now); "
          "3 = the same spot at other times. Yellow ticks = this panel's detection, cyan = the others. Amber = masked pixels.\n")
    print(out["text"])
    if out["errors"]:
        print("\nREAD ERRORS:", out["errors"])


def cmd_session(a):
    """Every exposure of the mover's session whose photo holds the spot, all detectors, in time order."""
    mv = V.mover_row(a.id)
    if mv is None:
        stop(f"NOT AVAILABLE: {a.id} is not a mover in the local data.")
    zone = V.zone_of(a.id)
    rows = V.session_rows(mv, need(zone, "photos"), POOL, a.max)
    dots = V.mover_dots(mv)
    t0 = dots[0]["mjd"]
    n = len(rows)
    ncol = min(6, max(n, 1)); nrow = math.ceil(n / ncol) if n else 1
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.3 * ncol + 0.3, 2.6 * nrow + 0.2), squeeze=False)
    for ax in axes.ravel():
        ax.set_axis_off()
    print(f"Session {int(mv.session)} of visit {int(mv.visit)}: {n} exposures hold the spot. Cyan ticks = where the fitted track puts the "
          f"mover at that exposure's time (centre = its mean position); yellow = an engine detection in that exposure.")
    print(" time       det  λ µm   obs_id                 engine detection   S/N at track (r=1.5 px)   pixel x,y of the spot in that photo")
    for i, r in enumerate(rows):
        ax = axes[i // ncol, i % ncol]
        im, sg, d = r["img"], V.sig(r["img"]), r["dot"]
        if np.isfinite(sg):
            V.grey(ax, im, sg, V.stretch_hi([im], sg))
        V.ticks(ax, r["track_g"][0], r["track_g"][1], "#33e0ff", 0.9, alpha=0.8)
        if d and r["dot_g"] is not None:
            V.ticks(ax, r["dot_g"][0], r["dot_g"][1], "#ffd400", 1.6, d["n"])
        ax.set_title(f"{V.fmt_dt((r['mjd'] - t0) * 24)}  D{r['det']}  {'DET #' + str(d['n']) if d else 'no detection'}\n"
                     f"{r['um']:.2f} µm   at track {V.fs(r['snr'])}", fontsize=8)
        print(f" {V.fmt_dt((r['mjd'] - t0) * 24):>9s}  D{r['det']}  {r['um']:5.3f}  {r['obs']:22s} "
              f"{('#' + str(d['n']) + ' S/N ' + format(d['snr'], '.0f')) if d else 'none':18s} {V.fs(r['snr']):>8s}                 "
              f"{r['px'][0]:7.1f},{r['px'][1]:7.1f}{'  READ ERROR ' + str(r['error']) if r.get('error') else ''}")
    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, f"{a.id}_session.png")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.01, wspace=0.05, hspace=0.3)
    fig.savefig(png, dpi=100, facecolor="white"); plt.close(fig)
    print(f"IMAGE: {png}")
    steps = V.session_steps(rows)
    if steps and max(steps) < 3:
        print("NOTE: the telescope did NOT step in this session: the spot stays on the same detector pixels, so a hot pixel or "
              "detector defect stays on one sky spot here and cannot be told from a source.")
    print("How to read: a real source is seen in every exposure that holds it (also in the detector exposed at the same moment: "
          "D1+D4, D2+D5, D3+D6), unless it is too faint there. The S/N aperture is 9\" wide and picks up neighbours within that "
          "distance (check catalog). Each exposure sees the spot at the wavelength shown.")


def cmd_stamps(a):
    ph = need(a.zone, "photos")
    marks = [tuple(float(x) for x in m.split(",")) for m in (a.mark or [])]
    os.makedirs(a.out, exist_ok=True)
    gra, gdec = V.grid(a.ra, a.dec)
    dets = a.det or [int(d) for d in sorted(V.covering(ph, a.ra, a.dec, whole_grid=False).detector.unique())[:1]]
    mpts = [V.to_grid(a.ra, a.dec, r, d) for r, d in marks]
    rows = []
    for det in dets:
        p = V.covering(ph, a.ra, a.dec, det, whole_grid=False).sort_values("mjd")
        if p.empty:
            print(f"D{det}: no photo holds this spot.")
            continue
        if a.mjd is not None:  # the n single exposures nearest to the time
            p = p.assign(d=(p.mjd - a.mjd).abs()).sort_values("d").head(a.n).sort_values("mjd")
            st = list(POOL.map(lambda r: V.read_stamp(r.url, gra, gdec), p.itertuples()))
            rows.append((det, [(f"{V.fmt_dt((r.mjd - a.mjd) * 24)} D{det}\nMJD {r.mjd:.4f}", V.bgsub(s["data"]) if "data" in s else None, False)
                               for r, s in zip(p.itertuples(), st)]))
        else:  # one median stack per visit + last-minus-first difference
            panels, stacks = [], []
            for v, g in p.groupby("visit"):
                g = g.iloc[np.linspace(0, len(g) - 1, min(len(g), a.n)).round().astype(int)]
                st = [s for s in POOL.map(lambda r: V.read_stamp(r.url, gra, gdec), g.itertuples()) if "data" in s]
                if not st:
                    continue
                with np.errstate(all="ignore"):
                    med = np.nanmedian(np.stack([V.bgsub(s["data"]) for s in st]), axis=0)
                stacks.append(med)
                panels.append((f"visit {v} D{det}: median of {len(st)}\nMJD {g.mjd.min():.1f}-{g.mjd.max():.1f}", med, False))
            if len(stacks) >= 2:
                panels.append((f"last visit − first visit D{det}", stacks[-1] - stacks[0], True))
            rows.append((det, panels))
    if not rows:
        stop("NOT AVAILABLE: no photo of this zone holds that position.")
    ncol = max(len(r[1]) for r in rows)
    fig, axes = plt.subplots(len(rows), ncol, figsize=(2.4 * ncol + 0.3, 2.7 * len(rows) + 0.2), squeeze=False)
    for ax in axes.ravel():
        ax.set_axis_off()
    print("Panels (aperture S/N r=1.5 px at the centre, then at each --mark):")
    for i, (det, panels) in enumerate(rows):
        ok = [im for _, im, dif in panels if im is not None and not dif]
        sg = float(np.nanmedian([V.sig(im) for im in ok])) if ok else 1.0
        hi = V.stretch_hi(ok, sg) if ok else 10.0
        for j, (title, im, dif) in enumerate(panels):
            if im is None:
                continue
            ax = axes[i, j]
            s1 = V.sig(im)
            (V.diverging if dif else V.grey)(ax, im, *( (s1,) if dif else (sg, hi)))
            V.ticks(ax, V.C0, V.C0, "#ffd400", 1.3)
            for k, (gx, gy) in enumerate(mpts):
                V.ticks(ax, gx, gy, "#33e0ff", 0.9, chr(97 + k), 0.85)
            vals = [V.ap_snr(im, s1, [(V.C0, V.C0)])] + [V.ap_snr(im, s1, [pt]) for pt in mpts]
            ax.set_title(f"{title}\ncentre {V.fs(vals[0])}", fontsize=8)
            t1 = title.replace("\n", " | ")
            print(f"  {t1}: centre {V.fs(vals[0])}" + "".join(f", mark {chr(97 + k)} {V.fs(v)}" for k, v in enumerate(vals[1:])))
    png = os.path.join(a.out, f"stamps_{a.zone}_{a.ra:.5f}_{a.dec:+.5f}{'_t' + format(a.mjd, '.3f') if a.mjd is not None else ''}_D{'-'.join(map(str, dets))}.png")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.01, wspace=0.05, hspace=0.3)
    fig.savefig(png, dpi=100, facecolor="white"); plt.close(fig)
    print(f"IMAGE: {png}")
    print(f"Each panel: {V.N} x {V.N} px of 6.15\" ({V.N * V.SCALE / 60:.1f}' across), north up, east left. Yellow ticks = the position asked for; "
          "cyan a, b, ... = your --mark positions. Amber = masked pixels. A motion under ~3\" between visits cannot be seen by eye here.")


def cmd_catalog(a):
    ref = need(a.zone, "refcat")
    mjd = a.mjd
    near = ref[(np.abs(ref.dec - a.dec) < (a.radius + 60) / 3600.0)]
    sep0 = angsep(a.ra, a.dec, near.ra.to_numpy(float), near.dec.to_numpy(float)) * 3600
    near = near[sep0 < a.radius + 40].copy()
    have = sorted(ref["cat"].unique())
    lack = [c for c in ("gaia", "2mass", "catwise") if c not in have]
    print(f"Catalogues in this zone's local reference file: {', '.join(have)}." +
          (f" NOT available here: {', '.join(lack)} (no row from them below means NOT CHECKED, not absent; record it under could_not_check)." if lack else ""))
    print(f"Catalogue sources within {a.radius:.0f}\" of RA {a.ra:.6f} Dec {a.dec:+.6f}" + (f", positions also moved to MJD {mjd:.1f}" if mjd else "") + ":")
    print(" cat      sep\"  offset E\",N\" at catalogue epoch   epoch    pm RA,Dec mas/yr   parallax mas   sep\" at MJD   mags")
    rows = []
    for _, r in near.iterrows():
        e, n = V.offsets_arcsec(a.ra, a.dec, r.ra, r.dec)
        sep = math.hypot(float(e), float(n))
        yr = 2000.0 + (r.epoch_mjd - 51544.5) / 365.25
        sep_m = None
        if mjd is not None:
            ra2, dec2 = move_to_epoch(pd.DataFrame([r]), mjd)
            sep_m = float(angsep(a.ra, a.dec, ra2, dec2)[0] * 3600)
        if min(sep, sep_m if sep_m is not None else 1e9) > a.radius:
            continue
        mags = ", ".join(f"{k[:-4].upper()}={r[k]:.2f}" for k in ("g_mag", "bp_mag", "rp_mag", "j_mag", "h_mag", "k_mag", "w1_mag", "w2_mag")
                         if k in r.index and pd.notna(r[k]))
        pm = f"{r.pmra:+8.1f},{r.pmdec:+8.1f}" if pd.notna(r.pmra) else ("  none in Gaia DR3 " if r["cat"] == "gaia" else "       -,       -")
        plx = f"{r.parallax:6.2f}" if "parallax" in r.index and pd.notna(r.parallax) else "     -"
        rows.append((sep, f" {r['cat']:7s} {sep:5.1f}  {float(e):+7.1f},{float(n):+7.1f}                  {yr:7.1f}  {pm}     {plx}        "
                          f"{'' if sep_m is None else format(sep_m, '5.1f'):>5s}        {mags}"))
    for _, line in sorted(rows):
        print(line)
    if not rows:
        print(" (none)")
    print("Epochs: Gaia DR3 2016.0, CatWISE about 2015.4 (with its own proper motion), 2MASS 1997-2001. A star with a proper motion "
          "of P\"/yr sits P x (years between epochs) arcsec from its older catalogue position, along the motion direction.")
    aw = V.allwise_near(a.ra, a.dec, a.radius)
    if aw is None:
        print("AllWISE (NASA S3): NOT AVAILABLE (read failed): WISE and 2MASS counterparts NOT CHECKED.")
    else:
        print(f"AllWISE within {a.radius:.0f}\" (positions about 2010.5; Vega magnitudes; J H K = its 2MASS match within 3\", epoch 1997-2001):")
        for r in aw.head(12).itertuples():
            e, n = V.offsets_arcsec(a.ra, a.dec, r.ra, r.dec)
            col = []
            if pd.notna(r.w1mpro) and pd.notna(r.w2mpro):
                col.append(f"W1-W2={r.w1mpro - r.w2mpro:+.2f}")
            if pd.notna(r.j_m_2mass) and pd.notna(r.k_m_2mass):
                col.append(f"J-K={r.j_m_2mass - r.k_m_2mass:+.2f}")
            mags = ", ".join(f"{k}={v:.2f}" for k, v in (("W1", r.w1mpro), ("W2", r.w2mpro), ("W3", r.w3mpro), ("J", r.j_m_2mass),
                                                        ("H", r.h_m_2mass), ("K", r.k_m_2mass)) if pd.notna(v))
            tm = f", 2MASS position {r.r_2mass:.1f}\" from it at PA {r.pa_2mass:.0f}°" if pd.notna(r.r_2mass) else ", no 2MASS match"
            print(f"  {r.sep:5.1f}\"  E{float(e):+6.1f}\" N{float(n):+6.1f}\"  {mags}  {' '.join(col)}  extended flag {r.ext_flg}, artifact flags {r.cc_flags}{tm}")
        if aw.empty:
            print("  (none: for a source brighter than about 0.1 mJy at 3.4 µm this means it was not here in 2010, or it is not real)")
        print("  Colours (Vega): brown dwarfs W1-W2 > 0.4 (T dwarfs > 1) and usually J-K > 1 for L types; AGN W1-W2 > 0.8 with a smooth "
              "red spectrum; ordinary stars W1-W2 near 0; extended flag > 0 = a galaxy.")
    if a.pm:
        pmra, pmdec = (float(x) for x in a.pm.split(","))
        print(f"Back-track for a claimed motion of ({pmra:+.0f}, {pmdec:+.0f}) mas/yr from MJD {mjd if mjd else 61000:.0f}: where the star was, "
              "and the nearest catalogue source to that spot:")
        t_now = mjd if mjd else 61000.0
        for lab, emjd, cat in (("Gaia 2016.0", 57388.5, "gaia"), ("AllWISE 2010.5", 55400.0, "allwise"), ("2MASS ~1999", 51250.0, "2mass"),
                               ("control: the same distance FORWARD (2036)", 2 * t_now - 57388.5, "gaia")):
            yrs = (emjd - t_now) / 365.25
            ra2, dec2 = V.offset_radec(a.ra, a.dec, pmra / 1000 * yrs, pmdec / 1000 * yrs)
            ra2, dec2 = float(ra2), float(dec2)
            if cat == "allwise":
                c = V.allwise_near(ra2, dec2, 15)
                near = "NOT AVAILABLE" if c is None else ("none within 15\"" if c.empty else f"{c.sep.iloc[0]:.1f}\" away (W1={c.w1mpro.iloc[0]:.1f})")
            else:
                c = ref[(ref["cat"] == cat) & (np.abs(ref.dec - dec2) < 15 / 3600.0)]
                if cat not in have:
                    near = "NOT AVAILABLE in this zone's reference file"
                elif c.empty:
                    near = "none within 15\""
                else:
                    sp = angsep(ra2, dec2, c.ra.to_numpy(float), c.dec.to_numpy(float)) * 3600
                    near = f"{sp.min():.1f}\" away" if sp.min() <= 15 else "none within 15\""
            print(f"  {lab}: {abs(yrs):.1f} yr x motion = {math.hypot(pmra, pmdec) / 1000 * abs(yrs):.1f}\" -> RA {ra2:.6f} Dec {dec2:+.6f}: nearest {cat} source {near}")
        print("  A real fast star has a counterpart within about 1-2\" of each back-tracked spot and none at the control spot.")
    fx = V.table(a.zone, "fixed")
    if fx is not None:
        sep = angsep(a.ra, a.dec, fx.ra.to_numpy(float), fx.dec.to_numpy(float)) * 3600
        k = [i for i in np.argsort(sep)[:8] if sep[i] <= a.radius]
        print(f"Our own fixed sources (seen at the same place in several sessions) within {a.radius:.0f}\":")
        for i in k:
            e, n = V.offsets_arcsec(a.ra, a.dec, fx.ra.iloc[i], fx.dec.iloc[i])
            print(f"  {sep[i]:5.1f}\"  E{float(e):+6.1f}\" N{float(n):+6.1f}\"  S/N {fx.snr.iloc[i]:.0f}  flux {fx.flux_ujy.iloc[i]:.0f} µJy  sessions {int(fx.n_sessions.iloc[i])}  id {int(fx.source_id.iloc[i])}")
        if not k:
            print("  (none)")
    g = ref[(ref["cat"] == "gaia") & (ref.g_mag < 11)]
    sep = angsep(a.ra, a.dec, g.ra.to_numpy(float), g.dec.to_numpy(float)) * 3600
    k = [i for i in np.argsort(sep)[:6] if sep[i] <= 600]
    print("Bright stars within 10' (Gaia G < 11; halos, spikes, ghosts and persistence come from these): " +
          ("; ".join(f"G={g.g_mag.iloc[i]:.1f} at {sep[i]:.0f}\" (PA {math.degrees(math.atan2(*[float(x) for x in V.offsets_arcsec(a.ra, a.dec, g.ra.iloc[i], g.dec.iloc[i])])) % 360:.0f}°)" for i in k) or "none"))
    print("Saturation (half of stars saturate at): D1 J 10.5, D2 J 10.8, D3 K 11.0, D4 W1 11.1, D5 W2 9.6, D6 W2 9.0 (AB).")


def cmd_known(a):
    pred = need(a.zone, "pred")
    ph = need(a.zone, "photos")
    s = ph.iloc[(ph.mjd - a.mjd).abs().argsort()[:1]]
    session = int(s.session.iloc[0])
    if abs(float(s.mjd.iloc[0]) - a.mjd) > 0.02:
        print(f"Note: the nearest photo is {abs(float(s.mjd.iloc[0]) - a.mjd) * 24:.1f} h from MJD {a.mjd}; predictions exist per session only.")
    rows = V.known_near(pred, a.ra, a.dec, a.mjd, session, a.radius)
    print(f"Known bodies predicted within {a.radius:.0f}\" at MJD {a.mjd:.4f} (session {session}; MPC orbits for asteroids and comets, "
          f"JPL Horizons for planets and moons; observer = Earth centre, error up to ~2\"):")
    for r in rows[:12]:
        e, n = V.offsets_arcsec(a.ra, a.dec, r["ra"], r["dec"])
        print(f"  {r['sep']:6.1f}\"  E{float(e):+7.1f}\" N{float(n):+7.1f}\"  {r['name']} ({r['class']})  V={r['v']:.1f}  moving {r['rate']:.1f}\"/h toward PA {r['pa']:.0f}°")
    if not rows:
        print("  (none)")
    print("Not covered: artificial satellites, objects discovered after the MPC file was fetched, bodies with poor orbits "
          "(their real position can be arcminutes along the track from the prediction).")


def cmd_spectrum(a):
    zone = V.zone_of(a.id)
    src = need(zone, "sources")
    if not V.source_ids(a.id):
        stop(f"NOT AVAILABLE: {a.id} has no source id (spectrum works for fast stars, spectral leads and shapes, not movers).")
    sid = V.source_ids(a.id)[0] if a.which == 1 or len(V.source_ids(a.id)) < 2 else V.source_ids(a.id)[1]
    r = src[src.source_id == sid]
    if r.empty:
        stop(f"NOT AVAILABLE: source {sid} has no row in the local sources table of {zone}.")
    r = r.iloc[0]
    ch = json.load(open(V.DATA / zone / "channels.json"))
    um = np.array([c["um_centre"] for c in ch]); det = np.array([c["detector"] for c in ch])
    f, e, n = np.asarray(r.flux_ujy, float), np.asarray(r.err_ujy, float), np.asarray(r.n, float)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for d in range(1, 7):
        m = (det == d) & np.isfinite(f)
        ax.errorbar(um[m], f[m], e[m], fmt="o-", ms=3, lw=0.8, label=f"D{d}")
    for lam, name in ((1.4, "H2O"), (1.9, "H2O"), (2.3, "CO"), (3.05, "H2O ice"), (3.3, "PAH/CH4"), (4.27, "CO2 ice"), (4.67, "CO")):
        ax.axvline(lam, color="0.8", lw=0.6); ax.text(lam, ax.get_ylim()[1], name, fontsize=6, rotation=90, va="top")
    ax.set_xlabel("wavelength (µm)"); ax.set_ylabel("flux (µJy)"); ax.legend(fontsize=7, ncol=6)
    ax.set_title(f"{a.id}: {int(r.n_dots)} detections, {int(r.n_sessions)} sessions, {int(r.n_visits)} visits", fontsize=9)
    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, f"{a.id}_spectrum.png")
    fig.tight_layout(); fig.savefig(png, dpi=100); plt.close(fig)
    print(f"IMAGE: {png}")
    print(f"Source {sid}: RA {r.ra:.6f} Dec {r.dec:+.6f}; saturated={bool(r.saturated)}; chi2_nu={r.chi2_nu:.2f} (about 1 = constant; "
          f"much more = variable or bad errors); visit flux ratio (last/first)={r.visit_flux_ratio:.2f}; variable={bool(r.variable)}")
    print("Channels (µm: flux ± error µJy [detections used]); 102 channels, 17 per detector, empty ones left out:")
    for d in range(1, 7):
        m = (det == d) & np.isfinite(f)
        print(f"  D{d}: " + "  ".join(f"{um[i]:.2f}: {f[i]:.0f}±{e[i]:.0f}[{int(n[i])}]" for i in np.where(m)[0]))
    def band(lo, hi):
        m = (um >= lo) & (um <= hi) & np.isfinite(f) & np.isfinite(e) & (e > 0)
        if m.sum() == 0:
            return np.nan, np.nan, 0
        w = 1 / e[m] ** 2
        return float(np.sum(f[m] * w) / np.sum(w)), float(1 / np.sqrt(np.sum(w))), int(m.sum())

    def ratio(name, num, den, meaning):
        a1, e1, n1 = band(*num)
        dens = [band(*d) for d in den]
        if n1 == 0 or any(d[2] == 0 for d in dens) or not all(d[0] > 0 for d in dens):
            print(f"  {name}: not measurable (empty channels)")
            return
        d0 = float(np.mean([d[0] for d in dens])); de = float(np.sqrt(sum(d[1] ** 2 for d in dens)) / len(dens))
        r_, re_ = a1 / d0, abs(a1 / d0) * math.sqrt((e1 / a1) ** 2 + (de / d0) ** 2) if a1 else np.nan
        print(f"  {name}: {r_:.2f} ± {re_:.2f}   ({meaning})")

    print("Band measurements (flux ratios, inverse-variance means; 1.00 = flat):")
    ratio("water 1.4 µm   F(1.36-1.45)/F(1.24-1.31)", (1.36, 1.45), [(1.24, 1.31)], "cool dwarfs < 0.8; galaxies and warm stars about 1")
    ratio("water 1.9 µm   F(1.82-1.96)/mean F(1.62-1.74), F(2.06-2.22)", (1.82, 1.96), [(1.62, 1.74), (2.06, 2.22)], "cool dwarfs < 0.8")
    ratio("methane 3.3 µm F(3.20-3.45)/mean F(2.90-3.10), F(3.60-3.75)", (3.20, 3.45), [(2.90, 3.10), (3.60, 3.75)],
          "T dwarfs < 0.7 with continuum on BOTH sides; > 1.1 = PAH emission (galaxy)")
    ratio("4.1 µm peak    F(3.95-4.20)/F(3.20-3.45)", (3.95, 4.20), [(3.20, 3.45)], "T dwarfs > 2; a smooth red rise also gives > 1")
    ratio("blue end       F(0.90-1.05)/F(1.24-1.31)", (0.90, 1.05), [(1.24, 1.31)], "brown dwarfs < 0.5 (faint at 1 µm); galaxies, AGN, stars > 0.6")
    ratio("red rise       F(4.4-4.9)/F(3.5-3.75)", (4.4, 4.9), [(3.5, 3.75)], "hot dust (AGN, young star) > 1.3; stars < 0.8")
    VEGA = {"J": (1.17, 1.33, 0.91), "H": (1.49, 1.78, 1.39), "K": (2.00, 2.30, 1.85), "W1": (3.00, 3.75, 2.70), "W2": (4.10, 5.00, 3.34)}
    mag = {}
    for k, (lo, hi, off) in VEGA.items():
        v, _, nn = band(lo, hi)
        if nn and v > 0:
            mag[k] = 23.9 - 2.5 * math.log10(v) - off
    if mag:
        print("Synthetic magnitudes from this spectrum (Vega, rough: band edges only): " + ", ".join(f"{k}={v:.1f}" for k, v in mag.items()) +
              ("; J-K=%.2f" % (mag["J"] - mag["K"]) if "J" in mag and "K" in mag else "") +
              ("; W1-W2=%.2f" % (mag["W1"] - mag["W2"]) if "W1" in mag and "W2" in mag else ""))
        if "J" in mag and mag["J"] < 16:
            print(f"  At J={mag['J']:.1f} a normal star must be in Gaia: an engine 'no Gaia match' is then a failed cross-match "
                  f"(or a very red object: check W1-W2 and the blue end).")
    ok = np.where(np.isfinite(f) & np.isfinite(e) & (e > 0))[0]
    lines, thin = [], [f"{um[i]:.2f}" for i in ok if n[i] <= 1]
    for q, i in enumerate(ok):
        nb = [ok[j] for j in (q - 2, q - 1, q + 1, q + 2) if 0 <= j < len(ok) and det[ok[j]] == det[i]]
        if len(nb) >= 2:
            med = float(np.median(f[nb]))
            z_ = (f[i] - med) / max(e[i], 1e-9)
            if abs(z_) >= 4 and abs(f[i] - med) > 0.15 * abs(med):
                lines.append(f"{um[i]:.2f} µm {'+' if z_ > 0 else '-'}{abs(f[i] - med) / max(abs(med), 1e-9) * 100:.0f}% ({abs(z_):.0f}σ, {int(n[i])} detection{'s' if n[i] != 1 else ''})")
    print("Single channels that stand out from their neighbours (emission lines, or one bad detection when it rests on 1): " + ("; ".join(lines) or "none"))
    print("  Lines to consider: Paα 1.875, Brγ 2.166, PAH 3.29, Brα 4.052 µm (rest); a redshifted galaxy moves them redward by (1+z).")
    if thin:
        print(f"Channels resting on a single detection (do not trust a feature seen only there): {', '.join(thin[:30])}")
    joins = [1.1, 1.6, 2.4, 3.8, 4.4]
    print("Detector joins at " + ", ".join(f"{j} µm" for j in joins) + ": a step exactly there is calibration, not physics.")
    print("Rules from round 1: a brown dwarf needs the water bands AND a faint blue end; a dip near 3.3 µm with a smooth red continuum "
          "is a galaxy or AGN, not methane; a late-M/L dwarf has water bands but no methane dip.")
    print("Cautions: a 6\" pixel blends neighbours (run catalog and stamps); each channel comes from different exposures, so a "
          "variable or moving source makes steps between channels; detector joins (1.1, 1.6, 2.4, 3.8, 4.4 µm) can show offsets.")


def cmd_dots(a):
    fd = need(a.zone, "freedots")
    near = fd[np.abs(fd.dec - a.dec) < a.radius / 3600.0]
    sep = angsep(a.ra, a.dec, near.ra.to_numpy(float), near.dec.to_numpy(float)) * 3600
    near = near[sep <= a.radius].assign(sep=sep[sep <= a.radius])
    ph = need(a.zone, "photos").set_index(["obs_id", "detector"])
    print(f"Our detections not assigned to any fixed source ('free dots') within {a.radius:.0f}\" of RA {a.ra:.6f} Dec {a.dec:+.6f}: {len(near)}")
    out = []
    for r in near.itertuples():
        try:
            p = ph.loc[(r.obs_id, r.detector)]
            out.append((float(p.mjd), int(p.session), int(p.visit), r))
        except KeyError:
            continue
    for mjd, s, v, r in sorted(out)[:60]:
        e, n = V.offsets_arcsec(a.ra, a.dec, r.ra, r.dec)
        print(f"  MJD {mjd:.4f} visit {v} session {s:4d} D{r.detector} {r.obs_id:22s} sep {r.sep:5.1f}\"  E{float(e):+6.1f}\" N{float(n):+6.1f}\"  pixel {r.x:.1f},{r.y:.1f}")
    if out:
        by = {}
        for mjd, s_, v, r in out:
            by.setdefault(int(r.detector), []).append((r.x, r.y, s_))
        for det, pts in by.items():
            xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
            col = int((np.abs(xs - np.median(xs)) <= 2).sum())
            if len(pts) >= 3 and col >= 0.7 * len(pts):
                print(f"  DETECTOR PATTERN: {col} of {len(pts)} D{det} dots share detector column x = {np.median(xs):.0f} ± 2 px over "
                      f"{len({p[2] for p in pts})} sessions (y from {ys.min():.0f} to {ys.max():.0f}): a glow, streak or defect fixed to the "
                      f"detector, not a sky source. Check with: pixel --zone {a.zone} --det {det} --x {np.median(xs):.0f} --y {np.median(ys):.0f}")
    print("A fixed faint star that never became a 'fixed source' shows up here in several sessions at the same place. "
          "A real mover shows up in one session only. Free dots carry no flux or flags (not stored).")


def cmd_pixel(a):
    """History of a detector pixel and its column: our unassigned detections there, over the whole zone."""
    fd = need(a.zone, "freedots")
    ph = need(a.zone, "photos").set_index(["obs_id", "detector"])
    d = fd[fd.detector == a.det]
    px = d[(np.abs(d.x - a.x) <= 1.5) & (np.abs(d.y - a.y) <= 1.5)]
    col = d[np.abs(d.x - a.x) <= 2]
    exp = d.obs_id.nunique()
    area = 2040 * 2040
    print(f"D{a.det} of zone {a.zone}: {len(d)} unassigned detections ('free dots') in {exp} exposures.")
    print(f"Within 1.5 px of pixel ({a.x:.0f}, {a.y:.0f}): {len(px)} dots in {px.obs_id.nunique()} exposures "
          f"(expected by chance: {len(d) * 9 / area:.2f}).")
    for r in px.head(20).itertuples():
        try:
            p = ph.loc[(r.obs_id, r.detector)]
            print(f"   MJD {p.mjd:.4f} session {int(p.session):4d} {r.obs_id:22s} pixel {r.x:.1f},{r.y:.1f}  sky {r.ra:.5f} {r.dec:+.5f}")
        except KeyError:
            pass
    print(f"Column x = {a.x:.0f} ± 2 px (all y): {len(col)} dots in {col.obs_id.nunique()} exposures (expected by chance: {len(d) * 5 / 2040:.0f}).")
    if len(col):
        h, edges = np.histogram(col.y, bins=np.arange(0, 2041, 204))
        print("   by y range: " + ", ".join(f"{int(lo)}-{int(lo) + 204}: {n}" for lo, n in zip(edges[:-1], h) if n))
    verdict = "HOT PIXEL or detector defect (a sky source lands on a different pixel in each exposure)" if px.obs_id.nunique() >= 3 else \
        "repeats on this pixel: suspicious" if px.obs_id.nunique() == 2 else "no repeat on this pixel"
    print(f"Reading: {verdict}. A column with several times the chance count is a detector feature (glow, bleed, bad column).")


def cmd_track(a):
    """Follow a mover's fitted track into the other sessions: where it should be, and what we have there."""
    mv = V.mover_row(a.id)
    if mv is None:
        stop(f"NOT AVAILABLE: {a.id} is not a mover in the local data.")
    zone = V.zone_of(a.id)
    ph, fd, mvs = need(zone, "photos"), need(zone, "freedots"), need(zone, "movers")
    rate, pa = V._g(mv, "rate_arcsec_h"), V._g(mv, "pa_deg")
    if not np.isfinite(rate):
        stop("NOT AVAILABLE: this mover has no fitted rate (detections at one moment only): no track to follow.")
    nexp = int(mv.n_exposures)
    err = max(5.0, (0.5 if nexp <= 2 else 0.15) * rate)  # round 1: 2-exposure rates are wrong by > 50% in 1 of 5
    vx, vy = rate * math.sin(math.radians(pa)), rate * math.cos(math.radians(pa))
    ses = ph.groupby("session").agg(mjd=("mjd", "mean"), visit=("visit", "first")).reset_index()
    ses["dt_h"] = (ses.mjd - float(mv.mjd)) * 24
    ses = ses[(ses.session != mv.session) & (ses.dt_h.abs() <= a.days * 24)].sort_values("dt_h", key=np.abs).head(a.max)
    fmeta = ph.set_index(["obs_id", "detector"])
    print(f"Track of {a.id}: {rate:.0f}\"/h toward PA {pa:.0f}° from {nexp} exposures; assumed rate error ±{err:.0f}\"/h "
          f"(so the search circle grows by {err:.0f}\" per hour). Sessions within ±{a.days:g} days:")
    print("  session   dt       predicted RA, Dec        circle\"   photo holds it   our movers in circle   free dots in circle")
    for r in ses.sort_values("dt_h").itertuples():
        ra2, dec2 = V.offset_radec(mv.ra, mv.dec, vx * r.dt_h, vy * r.dt_h)
        ra2, dec2 = float(ra2), float(dec2)
        rad = max(20.0, err * abs(r.dt_h))
        cov = V.covering(ph[ph.session == r.session], ra2, dec2, whole_grid=False)
        m = mvs[mvs.session == r.session]
        nm = int((angsep(ra2, dec2, m.ra.to_numpy(float), m.dec.to_numpy(float)) * 3600 <= rad).sum()) if len(m) else 0
        obs = set(ph[ph.session == r.session].obs_id)
        f = fd[fd.obs_id.isin(obs) & (np.abs(fd.dec - dec2) < rad / 3600.0)]
        nf = int((angsep(ra2, dec2, f.ra.to_numpy(float), f.dec.to_numpy(float)) * 3600 <= rad).sum()) if len(f) else 0
        big = "  (circle too large to mean much)" if rad > 300 else ""
        print(f"  {int(r.session):6d}  {V.fmt_dt(r.dt_h):>8s}  {ra2:10.5f} {dec2:+9.5f}   {rad:7.0f}   {'yes D' + ''.join(str(d) for d in sorted(set(cov.detector))) if len(cov) else 'no':14s}   {nm:6d}                 {nf:6d}{big}")
    print("How to use: for a session whose photo holds the predicted spot with a small circle, look with\n"
          "  stamps --zone Z --ra R --dec=D --mjd M --n 4   and   movers --zone Z --ra R --dec=D --mjd M --radius <circle>.\n"
          "A second session with a source on the track is what turns a tracklet into an object. With two detections a few "
          "minutes apart the rate is too uncertain to follow beyond a few hours: say so under could_not_check.")


def cmd_movers(a):
    """Our movers (every one, before the cuts) of the session nearest to --mjd, within the radius."""
    mv = need(a.zone, "movers")
    ph = need(a.zone, "photos")
    session = int(ph.iloc[(ph.mjd - a.mjd).abs().argsort()[:1]].session.iloc[0])
    m = mv[mv.session == session]
    sep = angsep(a.ra, a.dec, m.ra.to_numpy(float), m.dec.to_numpy(float)) * 3600 if len(m) else np.zeros(0)
    m = m.assign(sep=sep)[sep <= a.radius].sort_values("sep")
    print("(Movers kept by the linker's own checks; pairs it rejected are not stored, so 'none' does not prove nothing was paired.)")
    print(f"Our movers in session {session} within {a.radius:.0f}\" of RA {a.ra:.6f} Dec {a.dec:+.6f}: {len(m)}  "
          f"(the session has {int((mv.session == session).sum())} movers in all)")
    for r in m.head(15).itertuples():
        e, n = V.offsets_arcsec(a.ra, a.dec, r.ra, r.dec)
        print(f"  {r.sep:6.1f}\"  E{float(e):+7.1f}\" N{float(n):+7.1f}\"  {r.id}  {int(r.n_dots)} detections in {int(r.n_exposures)} exposures, D{r.detectors}, "
              f"S/N {r.snr:.0f}, rate {r.rate_arcsec_h:.0f}\"/h PA {r.pa_deg:.0f}°, nearest known {r.known_name} at {r.known_sep:.1f}\", "
              f"{'on the site as a candidate' if r.is_suspect else 'not listed'}")
    print("A mover needs detections in two exposures of one session (or in two detectors at one moment), each more than 6\" from "
          "every fixed source. Single detections that were never paired are under `dots`.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="tool", required=True)
    for name in ("info", "packet", "session", "spectrum", "track"):
        p = sub.add_parser(name); p.add_argument("id")
        if name not in ("info", "track"):
            p.add_argument("--out", required=True)
        if name == "packet":
            p.add_argument("--blind", action="store_true", help="leave out the catalogue and known-object lines")
        if name == "session":
            p.add_argument("--max", type=int, default=18)
        if name == "spectrum":
            p.add_argument("--which", type=int, default=1, help="1 or 2: which source of a two-source id (fast-star pairs)")
        if name == "track":
            p.add_argument("--days", type=float, default=3.0); p.add_argument("--max", type=int, default=12)
    for name in ("stamps", "catalog", "known", "dots", "movers"):
        p = sub.add_parser(name)
        p.add_argument("--zone", required=True); p.add_argument("--ra", type=float, required=True); p.add_argument("--dec", type=float, required=True)
        if name == "stamps":
            p.add_argument("--out", required=True); p.add_argument("--det", type=int, action="append")
            p.add_argument("--mjd", type=float); p.add_argument("--n", type=int, default=6); p.add_argument("--mark", action="append")
        if name == "catalog":
            p.add_argument("--radius", type=float, default=30.0); p.add_argument("--mjd", type=float)
            p.add_argument("--pm", help="claimed proper motion 'pmra,pmdec' in mas/yr: probe the back-tracked positions")
        if name == "known":
            p.add_argument("--mjd", type=float, required=True); p.add_argument("--radius", type=float, default=600.0)
        if name == "movers":
            p.add_argument("--mjd", type=float, required=True); p.add_argument("--radius", type=float, default=120.0)
        if name == "dots":
            p.add_argument("--radius", type=float, default=20.0)
    p = sub.add_parser("pixel")
    p.add_argument("--zone", required=True); p.add_argument("--det", type=int, required=True)
    p.add_argument("--x", type=float, required=True); p.add_argument("--y", type=float, required=True)
    a = ap.parse_args()
    {"info": cmd_info, "pixel": cmd_pixel, "track": cmd_track, "packet": cmd_packet, "session": cmd_session, "stamps": cmd_stamps, "catalog": cmd_catalog,
     "known": cmd_known, "spectrum": cmd_spectrum, "dots": cmd_dots, "movers": cmd_movers}[a.tool](a)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    np.seterr(all="ignore")
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print(f"TOOL ERROR: {type(e).__name__}: {e}  (at {os.path.basename(tb.filename)}:{tb.lineno}). This is a bug in the tool, "
              f"not a result: record it under could_not_check and do not read the output above as an answer.")
        sys.exit(2)
