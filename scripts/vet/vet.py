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
  movers --zone Z --ra R --dec D --mjd M [--radius 120]       our movers of that session near a position (before the cuts)

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


def need(zone: str, name: str) -> pd.DataFrame:
    t = V.table(zone, name)
    if t is None:
        sys.exit(f"NOT AVAILABLE: {name} for zone {zone} is not in the local data ({V.DATA}/{zone}/{name}.parquet). "
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
    if ra is None and c:
        ra, dec, mjd = c["ra"], c["dec"], c["mjd"]
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
        sys.exit(f"NOT AVAILABLE: {a.id} is not a mover in the local data (packet works for movers; use stamps/catalog for other kinds).")
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
        sys.exit(f"NOT AVAILABLE: {a.id} is not a mover in the local data.")
    zone = V.zone_of(a.id)
    ph = need(zone, "photos")
    p = V.covering(ph[ph.session == mv.session], mv.ra, mv.dec).sort_values(["mjd", "detector"])
    dots = V.mover_dots(mv)
    have = {(d["obs"], d["det"]): d for d in dots}
    if len(p) > a.max:
        own = p[[(o, d) in have for o, d in zip(p.obs_id, p.detector)]]
        rest = p.drop(own.index)
        rest = rest[rest.detector.isin(set(own.detector))] if len(own) else rest
        p = pd.concat([own, rest.head(max(0, a.max - len(own)))]).sort_values(["mjd", "detector"])
    gra, gdec = V.grid(mv.ra, mv.dec)
    stamps = list(POOL.map(lambda r: V.read_stamp(r.url, gra, gdec, [(have[(r.obs_id, r.detector)]["x"], have[(r.obs_id, r.detector)]["y"])]
                                                  if (r.obs_id, r.detector) in have else None), p.itertuples()))
    t0 = dots[0]["mjd"]
    vx = V._g(mv, "rate_arcsec_h") * math.sin(math.radians(V._g(mv, "pa_deg"))) if np.isfinite(V._g(mv, "rate_arcsec_h")) else 0.0
    vy = V._g(mv, "rate_arcsec_h") * math.cos(math.radians(V._g(mv, "pa_deg"))) if np.isfinite(V._g(mv, "rate_arcsec_h")) else 0.0
    imgs = [V.bgsub(s["data"]) if "data" in s else np.full((V.N, V.N), np.nan) for s in stamps]
    n = len(imgs)
    ncol = min(6, max(n, 1)); nrow = math.ceil(n / ncol) if n else 1
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.3 * ncol + 0.3, 2.6 * nrow + 0.2), squeeze=False)
    for ax in axes.ravel():
        ax.set_axis_off()
    print(f"Session {int(mv.session)} of visit {int(mv.visit)}: {n} exposures hold the spot. Cyan ticks = where the fitted track puts the "
          f"mover at that exposure's time (centre = its mean position); yellow = an engine detection in that exposure.")
    print(" time       det  obs_id                 engine detection   S/N at track position (r=1.5 px)   pixel x,y of the spot in that photo")
    for i, (r, s, im) in enumerate(zip(p.itertuples(), stamps, imgs)):
        ax = axes[i // ncol, i % ncol]
        sg = V.sig(im)
        dt_h = (r.mjd - float(mv.mjd)) * 24
        gx, gy = V.C0 - vx * dt_h / V.SCALE, V.C0 + vy * dt_h / V.SCALE
        v = V.ap_snr(im, sg, [(gx, gy)])
        d = have.get((r.obs_id, r.detector))
        if np.isfinite(sg):
            V.grey(ax, im, sg, V.stretch_hi([im], sg))
        V.ticks(ax, gx, gy, "#33e0ff", 0.9, alpha=0.8)
        if d and "dots" in s and s["dots"]:
            dx, dy = V.to_grid(mv.ra, mv.dec, s["dots"][0][0], s["dots"][0][1])
            V.ticks(ax, dx, dy, "#ffd400", 1.6, d["n"])
        ax.set_title(f"{V.fmt_dt((r.mjd - t0) * 24)}  D{r.detector}  {'DET #' + str(d['n']) if d else 'no detection'}\nat track {V.fs(v)}", fontsize=8)
        cp = s.get("centre_px", (np.nan, np.nan))
        print(f" {V.fmt_dt((r.mjd - t0) * 24):>9s}  D{r.detector}  {r.obs_id:22s} {('#' + str(d['n']) + ' S/N ' + format(d['snr'], '.0f')) if d else 'none':18s} "
              f"{V.fs(v):>8s}                          {cp[0]:7.1f},{cp[1]:7.1f}{'  ERROR ' + s['error'] if 'error' in s else ''}")
    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, f"{a.id}_session.png")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.01, wspace=0.05, hspace=0.3)
    fig.savefig(png, dpi=100, facecolor="white"); plt.close(fig)
    print(f"IMAGE: {png}")
    print("How to read: a real source is seen in every exposure that holds it (also in the detector exposed at the same moment: "
          "D1+D4, D2+D5, D3+D6), unless it is too faint there. Each exposure sees the spot at a different wavelength.")


def cmd_stamps(a):
    ph = need(a.zone, "photos")
    marks = [tuple(float(x) for x in m.split(",")) for m in (a.mark or [])]
    os.makedirs(a.out, exist_ok=True)
    gra, gdec = V.grid(a.ra, a.dec)
    dets = a.det or [int(d) for d in sorted(V.covering(ph, a.ra, a.dec).detector.unique())[:1]]
    mpts = [V.to_grid(a.ra, a.dec, r, d) for r, d in marks]
    rows = []
    for det in dets:
        p = V.covering(ph, a.ra, a.dec, det).sort_values("mjd")
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
        sys.exit("NOT AVAILABLE: no photo of this zone holds that position.")
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
        pm = f"{r.pmra:+8.1f},{r.pmdec:+8.1f}" if pd.notna(r.pmra) else "       -,       -"
        plx = f"{r.parallax:6.2f}" if "parallax" in r.index and pd.notna(r.parallax) else "     -"
        rows.append((sep, f" {r.cat:7s} {sep:5.1f}  {float(e):+7.1f},{float(n):+7.1f}                  {yr:7.1f}  {pm}     {plx}        "
                          f"{'' if sep_m is None else format(sep_m, '5.1f'):>5s}        {mags}"))
    for _, line in sorted(rows):
        print(line)
    if not rows:
        print(" (none)")
    print("Epochs: Gaia DR3 2016.0, CatWISE about 2015.4 (with its own proper motion), 2MASS 1997-2001. A star with a proper motion "
          "of P\"/yr sits P x (years between epochs) arcsec from its older catalogue position, along the motion direction.")
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
    g = ref[(ref.cat == "gaia") & (ref.g_mag < 11)]
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
    sid = V.source_ids(a.id)[0] if a.which == 1 or len(V.source_ids(a.id)) < 2 else V.source_ids(a.id)[1]
    r = src[src.source_id == sid]
    if r.empty:
        sys.exit(f"NOT AVAILABLE: source {sid} has no row in the local sources table of {zone}.")
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
    print("A fixed faint star that never became a 'fixed source' shows up here in several sessions at the same place. "
          "A real mover shows up in one session only.")


def cmd_movers(a):
    """Our movers (every one, before the cuts) of the session nearest to --mjd, within the radius."""
    mv = need(a.zone, "movers")
    ph = need(a.zone, "photos")
    session = int(ph.iloc[(ph.mjd - a.mjd).abs().argsort()[:1]].session.iloc[0])
    m = mv[mv.session == session]
    sep = angsep(a.ra, a.dec, m.ra.to_numpy(float), m.dec.to_numpy(float)) * 3600 if len(m) else np.zeros(0)
    m = m.assign(sep=sep)[sep <= a.radius].sort_values("sep")
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
    for name in ("info", "packet", "session", "spectrum"):
        p = sub.add_parser(name); p.add_argument("id")
        if name != "info":
            p.add_argument("--out", required=True)
        if name == "packet":
            p.add_argument("--blind", action="store_true", help="leave out the catalogue and known-object lines")
        if name == "session":
            p.add_argument("--max", type=int, default=18)
        if name == "spectrum":
            p.add_argument("--which", type=int, default=1, help="1 or 2: which source of a two-source id (fast-star pairs)")
    for name in ("stamps", "catalog", "known", "dots", "movers"):
        p = sub.add_parser(name)
        p.add_argument("--zone", required=True); p.add_argument("--ra", type=float, required=True); p.add_argument("--dec", type=float, required=True)
        if name == "stamps":
            p.add_argument("--out", required=True); p.add_argument("--det", type=int, action="append")
            p.add_argument("--mjd", type=float); p.add_argument("--n", type=int, default=6); p.add_argument("--mark", action="append")
        if name == "catalog":
            p.add_argument("--radius", type=float, default=30.0); p.add_argument("--mjd", type=float)
        if name == "known":
            p.add_argument("--mjd", type=float, required=True); p.add_argument("--radius", type=float, default=600.0)
        if name == "movers":
            p.add_argument("--mjd", type=float, required=True); p.add_argument("--radius", type=float, default=120.0)
        if name == "dots":
            p.add_argument("--radius", type=float, default=20.0)
    a = ap.parse_args()
    {"info": cmd_info, "packet": cmd_packet, "session": cmd_session, "stamps": cmd_stamps, "catalog": cmd_catalog,
     "known": cmd_known, "spectrum": cmd_spectrum, "dots": cmd_dots, "movers": cmd_movers}[a.tool](a)


if __name__ == "__main__":
    main()
