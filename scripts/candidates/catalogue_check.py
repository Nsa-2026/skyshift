"""Catalogue check (check producer `catalogue`): what IRSA's catalogues hold at every candidate of a candidates.json.

    cd pipeline && uv run python ../scripts/candidates/catalogue_check.py \\
        --candidates ../web/public/data/candidates.json \\
        --root gs://nasa-glazing-data --col-cache /Users/raducucu/skyshift-data/cache     # own magnitudes (optional)
        [--highpm-root /Users/raducucu/skyshift-data]   # highpm's counterpart columns: a rerun's tables first
    # writes /Users/raducucu/skyshift-data/checks/catalogue.json (--out); answers cached in --cache

Runs from pipeline/ (or server/ without --root): numpy, astropy, pyvo. Only IRSA is asked (no CDS): TAP queries with the
candidates' positions uploaded (UPLOAD_BATCH a query, cached per position and catalogue, so a rerun on a new export asks
only for new positions):
  Gaia DR3 (gaia_dr3_source, every G, moved by its own motion), CatWISE2020 (moved by its motion where that is ≥ 3σ),
  AllWISE (W1–W4, ext_flg), 2MASS PSC (J, H, Ks, ph_qual, at its own epoch) and 2MASS XSC (galaxies), within
  QUERY_ARCSEC (high_pm without Gaia: + its motion back to 1998; spectral and shape on a fast Gaia star: the other
  catalogues + that star's motion back to 1998); bright stars (Gaia G < BRIGHT_G, 2MASS Ks < BRIGHT_K) within
  BRIGHT_ARCSEC. Our own magnitudes: the candidate's flux in `det`, and with --root the spectrum's J, H, Ks,
  3.4 and 4.6 µm windows (results/<zone>/classes.parquet mag_ab_*, column reads, cached).

Checks (contracts/api.md v3.23 codes; `status` is never set: a catalogue can't make anything certain):
  mover    catalogue_star     warn: a catalogue source within MOVER_STAR_ARCSEC that may or may not be it, with the
                              chance that a random spot of the zone has one that close (`chance`, from the zone's
                              catalogue density in the 6–10″ ring around its candidates).
  spectral catalogues_checked what the catalogues hold at its place (ID_ARCSEC; XSC within XSC_ARCSEC): strong when an
                              ordinary catalogued galaxy or star explains the label (rules per label below), ok when the
                              catalogue colours agree with it, warn when they don't settle it or nothing is catalogued.
           no_j               brown_dwarf_like / ch4: 2MASS J (or its limit) against our 4.6 µm magnitude. Brown dwarfs
                              have J − [4.6] (Vega) ≥ 2 (L0) up to ~7 (late T); ordinary stars and M dwarfs < 1.5:
                              detected bluer than BD_JW2_ORDINARY → strong, BD_JW2_ORDINARY..BD_JW2_MIN → warn, else ok;
                              no 2MASS source: ok when 2MASS would have seen an L dwarf, warn when it is too shallow.
           bright_neighbour   warn: a star NEIGHBOUR_DMAG brighter than it within NEIGHBOUR_ARCSEC, or a saturating star
                              (G < SATURATING_G, Ks < SATURATING_K) within BRIGHT_ARCSEC.
  shape    galaxy             2MASS XSC within XSC_SHAPE_ARCSEC → strong; WISE extended or a Gaia galaxy candidate →
                              warn; else ok (no catalogue calls it a galaxy).
           bright_neighbour   as spectral.
  high_pm  catalogues_checked (no Gaia match only; proposed for high_pm, see the PR): a catalogue source where its motion
                              puts it at the catalogue's epoch → strong (catalogued star, not new); one at today's place
                              when the motion should have moved it ≥ STATIC_MIN_SHIFT → strong (a still star: the motion
                              is likely fake); nothing at either → warn. A CatWISE source that its OWN motion brings to
                              today's place moves: never "still" (warn: the same star, CatWISE's motion not ours).
                              Where highpm's table has its counterpart columns (#388; --highpm-root, --root), its deep
                              check's verdict replaces this reading: a counterpart that lists the object (gaia_pm,
                              gaia_position, catwise_pm, tmass_position, allwise_position) → strong, catalogued; gaia_still
                              / allwise_still → strong, still; none → our own track test only, no still guess.
High_pm rows with a Gaia match are not measured (the pipeline already matched them to Gaia's motion).

Label rules for spectral `catalogues_checked` (the most important label decides; Vega magnitudes):
  every label                  strong: its Gaia DR3 entry has a parallax (≥ PLX_SIGMA) below PLX_NEAR_MAS: a catalogued
                               star beyond 50 pc, which the export's own rule leaves out (most are G > 20, below the
                               pipeline's reference catalogue, so it saw "no Gaia star").
  brown_dwarf_like, ch4, red   strong: a 2MASS XSC galaxy within XSC_ARCSEC (or AllWISE ext_flg 2–3), WISE colours of a
                               galaxy or quasar (W2 − W3 ≥ 2.5 at W3 S/N ≥ 5 and W1 − W2 < 1), a Gaia parallax giving
                               absolute G < STAR_ABS_G_MAX, or an ordinary star (W1 − W2 < 0.1 and J − Ks < 0.85, or a
                               Gaia star G < 19 with BP − RP < 1.5).
                               ok: absolute G ≥ UCD_ABS_G_MIN from the parallax (BP − RP not below 2), or L/T-like
                               colours (W1 − W2 ≥ 0.25, or J − Ks ≥ 1.2). else warn.
  h2o_ice, co2_ice, co_ice     strong: a catalogued galaxy, or the catalogue colours not reddened enough for a cloud
                               (H − Ks < 0.6 or J − H < 1.0, classify's own ice rule).
                               ok: they pass the whole rule (also J − H ≥ 1.7 (H − Ks) − 0.15, W1 − W2 < 0.4).
                               warn: reddened with an infrared excess (fails only those two: a young star's envelope
                               holds ice too, and ice in W1 reddens W1 − W2), or no 2MASS colours.
  h2o_vapour, co_23            strong: a catalogued galaxy (its old stars carry CO and water bands).
                               ok: L-dwarf-like (J − Ks ≥ 1.1 or W1 − W2 ≥ 0.2). else warn.
  pah_33                       strong: a catalogued galaxy or star-forming WISE colours (W2 − W3 ≥ 2 at W3 S/N ≥ 3 and
                               W1 − W2 < 0.8): PAH at 3.3 µm is ordinary there; or a Gaia star (G < 19 or a parallax:
                               PAH needs a galaxy).
                               else warn.
Nothing within ID_ARCSEC in any catalogue → warn: 2 or more sources of one catalogue within BLEND_ARCSEC ("their
blended light"), else "not catalogued although bright" (an artefact, a variable, or something that moved).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATES = ROOT / "web" / "public" / "data" / "candidates.json"
DATA = Path(os.environ.get("SKYSHIFT_DATA", str(Path.home() / "skyshift-data")))
DEFAULT_OUT = DATA / "checks" / "catalogue.json"
DEFAULT_CACHE = DATA / "cache" / "catalogue"
SOURCE = "catalogue"
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP"

QUERY_ARCSEC = 10.0          # every candidate: catalogue sources within this
QUERY_MAX_ARCSEC = 120.0     # high_pm: 10″ + its motion back to TMASS_MJD, capped here
GAIA_MATCH_ARCSEC = 60.0     # spectral/shape on a Gaia star (gaia_match): Gaia is searched this far (5″/yr since 2016)
BRIGHT_ARCSEC = 90.0         # bright stars (a saturated star's rings and ghosts reach ~60–90″)
BRIGHT_G, BRIGHT_K = 12.0, 9.0
ID_ARCSEC = 3.0              # "at its place": crossid's CatWISE/2MASS radius (Gaia 2.5″)
XSC_ARCSEC = 5.0             # a 2MASS galaxy's catalogued centre
BLEND_ARCSEC = 6.0           # one SPHEREx pixel: two catalogue sources this close blend into one dot
XSC_SHAPE_ARCSEC = 10.0      # shape: a galaxy within this explains a stretched image
MOVER_STAR_ARCSEC = 6.0      # suspects' catalogue veto radius
NEIGHBOUR_ARCSEC = 30.0      # bright_neighbour: a star NEIGHBOUR_DMAG brighter within this ...
NEIGHBOUR_DMAG = 3.0
NEIGHBOUR_MIN_ARCSEC = 4.0   # ... that isn't its own entry
SATURATING_G, SATURATING_K = 10.0, 8.0   # ... or a saturating star within BRIGHT_ARCSEC (SPHEREx saturates at AB ~9–11)
STATIC_MIN_SHIFT = 4.0       # high_pm: the motion must move it this far for "at today's place" to mean "still"
PLX_SIGMA = 5.0              # a Gaia parallax counts when measured at this many sigma
PLX_NEAR_MAS = 20.0          # the export keeps spectral candidates on a Gaia star only when closer than 50 pc
REFCAT_G_MAX = 20.0          # the pipeline's reference catalogue stops at Gaia G 20 (config REFCAT_GAIA_G_MAX)
UCD_ABS_G_MIN = 15.0         # absolute G of an ultracool dwarf (M8 and later), and fainter
STAR_ABS_G_MAX = 13.0        # brighter than this (M5 and earlier): too luminous to be a brown dwarf
BD_JW2_MIN = 2.0             # J − [4.6] Vega: L0 dwarfs ~2, T dwarfs 3–7
BD_JW2_ORDINARY = 1.5        # ... ordinary stars < ~1, M dwarfs 1.2–1.8
TMASS_J_LIMIT = 16.8         # 2MASS PSC J detection limit (Vega, ~5σ), when no upper limit is given
UPLOAD_BATCH = 1000
NET_RETRIES = 3
TMASS_MJD = 51000.0          # 2MASS survey epoch, roughly (1998): how far back high_pm candidates are searched
GAIA_MJD = 57388.5           # J2016.0
CATWISE_PM_MJD = 57170.0
ALLWISE_MJD = 55400.0
# AB = Vega + offset (2MASS: Cohen et al. 2003; WISE: Jarrett et al. 2011)
AB_OFFSET = {"j": 0.91, "h": 1.39, "k": 1.85, "w1": 2.699, "w2": 3.339, "w3": 5.174}
NAMES = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "allwise": "AllWISE", "tmass": "2MASS", "xsc": "2MASS XSC"}

# catalogue -> (IRSA table, columns, extra WHERE, radius kind)
CATALOGUES: dict[str, tuple[str, list[str], str, str]] = {
    "gaia": ("gaia_dr3_source", ["source_id", "ra", "dec", "pmra", "pmdec", "parallax", "parallax_error",
                                 "phot_g_mean_mag", "bp_rp", "in_galaxy_candidates", "in_qso_candidates"], "", "query"),
    "catwise": ("catwise_2020", ["source_name", "ra", "dec", "ra_pm", "dec_pm", "pmra", "pmdec", "sigpmra", "sigpmdec",
                                 "w1mpro", "w1sigmpro", "w2mpro", "w2sigmpro", "meanobsmjd"], "", "query"),
    "allwise": ("allwise_p3as_psd", ["designation", "ra", "dec", "w1mpro", "w2mpro", "w3mpro", "w3snr", "w4mpro",
                                     "w4snr", "ext_flg", "ph_qual"], "", "query"),
    "tmass": ("fp_psc", ["designation", "ra", "dec", "j_m", "j_msigcom", "h_m", "h_msigcom", "k_m", "k_msigcom",
                         "ph_qual", "jdate", "gal_contam"], "", "query"),
    "xsc": ("fp_xsc", ["designation", "ra", "dec", "r_k20fe", "j_m_k20fe", "k_m_k20fe", "jdate"], "", "query"),
    "gaia_bright": ("gaia_dr3_source", ["source_id", "ra", "dec", "pmra", "pmdec", "phot_g_mean_mag"],
                    f"c.phot_g_mean_mag < {BRIGHT_G}", "bright"),
    "tmass_bright": ("fp_psc", ["designation", "ra", "dec", "j_m", "k_m", "jdate"], f"c.k_m < {BRIGHT_K}", "bright"),
}

log = logging.getLogger("catalogue-check")


# --- geometry -------------------------------------------------------------------------------------
def offset(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    """(east, north) arcsec of (ra, dec) from (ra0, dec0), gnomonic."""
    a0, d0, a, d = map(math.radians, (ra0, dec0, ra, dec))
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    e = math.cos(d) * math.sin(a - a0) / cosc
    n = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cosc
    return math.degrees(e) * 3600.0, math.degrees(n) * 3600.0


def direction(e: float, n: float) -> str:
    """Compass word for an offset east/north."""
    pa = math.degrees(math.atan2(e, n)) % 360.0
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((pa + 22.5) // 45) % 8]


def num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v


def fin(x) -> bool:
    return math.isfinite(num(x))


def ab_of_flux(flux_ujy) -> float:
    f = num(flux_ujy)
    return 23.9 - 2.5 * math.log10(f) if f > 0 else math.nan


def cand_motion(c: dict) -> tuple[float, float] | None:
    """(pmra, pmdec) mas/yr of a high_pm candidate (pa east of north), else None."""
    pm, pa = num(c.get("pm_mas_yr")), num(c.get("pa_deg"))
    if not (math.isfinite(pm) and math.isfinite(pa)):
        return None
    return pm * math.sin(math.radians(pa)), pm * math.cos(math.radians(pa))


def row_epoch(cat: str, r: dict) -> float:
    """MJD of a catalogue row's position as we use it."""
    if cat.startswith("gaia"):
        return GAIA_MJD
    if cat == "catwise":  # ra_pm/dec_pm are at the motion fit's epoch, ra/dec at the mean observation
        return CATWISE_PM_MJD if fin(r.get("ra_pm")) else (num(r.get("meanobsmjd")) if fin(r.get("meanobsmjd")) else CATWISE_PM_MJD)
    if cat == "allwise":
        return ALLWISE_MJD
    jd = num(r.get("jdate"))
    return jd - 2400000.5 if math.isfinite(jd) else TMASS_MJD


def catwise_moving(r: dict) -> bool:
    """CatWISE's own motion is used only where it is ≥ 3σ (faint sources' motions are mostly noise)."""
    pmra, pmdec, sra, sdec = (num(r.get(k)) for k in ("pmra", "pmdec", "sigpmra", "sigpmdec"))
    if not all(math.isfinite(v) for v in (pmra, pmdec, sra, sdec)) or not fin(r.get("ra_pm")):
        return False
    return math.hypot(pmra / max(sra, 1e-6), pmdec / max(sdec, 1e-6)) >= 3.0


def row_place(cat: str, r: dict, mjd: float) -> tuple[float, float]:
    """The row's (ra, dec) moved to `mjd` by its own catalogue motion (Gaia; CatWISE where significant), else as listed."""
    if cat.startswith("gaia") and fin(r.get("pmra")) and fin(r.get("pmdec")):
        dt_yr = (mjd - GAIA_MJD) / 365.25
        dec = num(r["dec"]) + num(r["pmdec"]) * dt_yr / 3.6e6
        return num(r["ra"]) + num(r["pmra"]) * dt_yr / 3.6e6 / max(math.cos(math.radians(num(r["dec"]))), 1e-6), dec
    if cat == "catwise" and catwise_moving(r):
        dt_yr = (mjd - CATWISE_PM_MJD) / 365.25  # CatWISE motions are arcsec/yr
        dec = num(r["dec_pm"]) + num(r["pmdec"]) * dt_yr / 3600.0
        return num(r["ra_pm"]) + num(r["pmra"]) * dt_yr / 3600.0 / max(math.cos(math.radians(num(r["dec_pm"]))), 1e-6), dec
    return num(r["ra"]), num(r["dec"])


def near(c: dict, cat: str, rows: list[dict], motion: tuple[float, float] | None = None) -> list[dict]:
    """Rows with their offset from the candidate, nearest first. Without `motion`: each row moved to the candidate's
    epoch by its own catalogue motion. With `motion` (the candidate's own, mas/yr): the candidate is moved to each row's
    epoch instead and the row stays where the catalogue saw it (2MASS 1998–2001, AllWISE 2010, ...)."""
    out = []
    for r in rows:
        if motion is None:
            ra, dec = row_place(cat, r, num(c["mjd"]))
            e, n = offset(num(c["ra"]), num(c["dec"]), ra, dec)
        else:
            ra, dec = (r["ra_pm"], r["dec_pm"]) if cat == "catwise" and fin(r.get("ra_pm")) else (r["ra"], r["dec"])
            e, n = offset(num(c["ra"]), num(c["dec"]), num(ra), num(dec))
            dt_yr = (row_epoch(cat, r) - num(c["mjd"])) / 365.25
            e, n = e - motion[0] * dt_yr / 1000.0, n - motion[1] * dt_yr / 1000.0
        out.append({"sep": math.hypot(e, n), "e": e, "n": n, "row": r, "cat": cat})
    return sorted(out, key=lambda x: x["sep"])


# --- what a row says, in words --------------------------------------------------------------------
def tmass_detected(r: dict, band: str) -> bool:
    """2MASS ph_qual letter A–D for J/H/Ks (E, F: poor; U: upper limit; X: none)."""
    q = str(r.get("ph_qual") or "")
    i = "jhk".index(band)
    return len(q) > i and q[i] in "ABCD" and fin(r.get(f"{band}_m"))


def describe(x: dict, of: str = "") -> str:
    """'Gaia DR3 lists a G 20.4 star 4.2″ north-east [of <of>]' style words for one nearby row."""
    cat, r = x["cat"], x["row"]
    if x["sep"] >= 0.5:
        where = f"{x['sep']:.1f}″ {direction(x['e'], x['n'])}" + (f" of {of}" if of else "")
    else:
        where = f"{x['sep']:.1f}″ from {of}" if of else f"{x['sep']:.1f}″ away"
    if cat.startswith("gaia"):
        what = f"a G {num(r.get('phot_g_mean_mag')):.1f} star" if fin(r.get("phot_g_mean_mag")) else "a source"
    elif cat in ("catwise", "allwise"):
        what = f"a W1 {num(r.get('w1mpro')):.1f}, W2 {num(r.get('w2mpro')):.1f} source"
    elif cat.startswith("tmass"):
        bands = [f"{b.upper() if b != 'k' else 'Ks'} {num(r.get(f'{b}_m')):.1f}" for b in "jhk"
                 if (tmass_detected(r, b) if cat == "tmass" else fin(r.get(f"{b}_m")))]
        what = f"a {', '.join(bands)} source" if bands else "a source"
    else:
        what = f"a galaxy (Ks {num(r.get('k_m_k20fe')):.1f}, {num(r.get('r_k20fe')):.0f}″ across)" \
            if fin(r.get("k_m_k20fe")) else "a galaxy"
    return f"{NAMES.get(cat.replace('_bright', ''), cat)} lists {what} {where}"


def row_values(x: dict) -> dict:
    """The numbers of one row for `values` (Vega magnitudes, separations in arcsec)."""
    cat, r = x["cat"], x["row"]
    v = {"catalogue": NAMES.get(cat.replace("_bright", ""), cat), "sep_arcsec": round(x["sep"], 2)}
    keys = {"gaia": [("g_mag", "phot_g_mean_mag"), ("bp_rp", "bp_rp")], "catwise": [("w1_mag", "w1mpro"), ("w2_mag", "w2mpro")],
            "allwise": [("w1_mag", "w1mpro"), ("w2_mag", "w2mpro"), ("w3_mag", "w3mpro")],
            "tmass": [("j_mag", "j_m"), ("h_mag", "h_m"), ("ks_mag", "k_m")], "xsc": [("ks_mag", "k_m_k20fe"), ("r_arcsec", "r_k20fe")]}
    for k, col in keys.get(cat.replace("_bright", ""), []):
        if fin(r.get(col)):
            v[k] = round(num(r[col]), 3)
    return v


# --- per-candidate context ----------------------------------------------------------------------------
class Spot:
    """Everything the catalogues hold around one candidate, matched at its epoch (pm-aware)."""

    def __init__(self, c: dict, answers: dict[str, list[dict] | None], own: dict | None = None):
        self.c = c
        self.own = own or {}
        self.checked = {k: v is not None for k, v in answers.items()}
        motion = cand_motion(c) if c["kind"] == "high_pm" else None
        self.near = {k: near(c, k, v or [], motion) for k, v in answers.items()}
        # the candidate's own Gaia entry, and its motion: 2MASS / AllWISE are matched at their epoch with it
        g = [x for x in self.near.get("gaia", []) if x["sep"] <= ID_ARCSEC]
        self.gaia = g[0] if g else None
        if motion is None and self.gaia is not None and fin(self.gaia["row"].get("pmra")):
            gm = (num(self.gaia["row"]["pmra"]), num(self.gaia["row"]["pmdec"]))
            if math.hypot(*gm) > 50.0:  # > 0.05″/yr: 1.3″ since 2MASS, worth matching at its epoch
                for k in ("catwise", "tmass", "allwise", "xsc"):  # CatWISE's own motions of fast stars are often off
                    self.near[k] = near(c, k, answers.get(k) or [], gm)

    def at(self, cat: str, radius: float = ID_ARCSEC) -> dict | None:
        xs = [x for x in self.near.get(cat, []) if x["sep"] <= radius]
        return xs[0] if xs else None

    def any_within(self, radius: float, cats=("gaia", "catwise", "allwise", "tmass", "xsc")) -> list[dict]:
        return sorted([x for k in cats for x in self.near.get(k, []) if x["sep"] <= radius], key=lambda x: x["sep"])

    # colours (Vega) of its own catalogue entries
    def w1w2(self) -> float:
        x = self.at("catwise") or self.at("allwise")
        return num(x["row"].get("w1mpro")) - num(x["row"].get("w2mpro")) if x else math.nan

    def w2w3(self) -> tuple[float, float]:
        x = self.at("allwise")
        if not x:
            return math.nan, math.nan
        return num(x["row"].get("w2mpro")) - num(x["row"].get("w3mpro")), num(x["row"].get("w3snr"))

    def tmass_colour(self, a: str, b: str) -> float:
        x = self.at("tmass")
        if not x or not (tmass_detected(x["row"], a) and tmass_detected(x["row"], b)):
            return math.nan
        return num(x["row"][f"{a}_m"]) - num(x["row"][f"{b}_m"])

    def galaxy(self) -> str | None:
        """Words naming a catalogued galaxy here, or None."""
        x = self.at("xsc", XSC_ARCSEC)
        if x:
            return describe(x)
        a = self.at("allwise")
        if a and str(a["row"].get("ext_flg")) in ("2", "3"):
            return f"AllWISE marks its entry {a['sep']:.1f}″ away as part of a 2MASS galaxy (ext_flg {a['row']['ext_flg']})"
        g = self.gaia
        if g and str(g["row"].get("in_galaxy_candidates")).lower() in ("true", "1", "t"):
            return f"Gaia DR3 lists its entry {g['sep']:.1f}″ away as a galaxy candidate"
        return None

    def parallax(self) -> dict | None:
        """Its own Gaia entry's parallax when measured at ≥ PLX_SIGMA: {plx, err, g, bp_rp, pc, abs_g}, else None."""
        g = self.gaia
        if not g:
            return None
        plx, err, gm = num(g["row"].get("parallax")), num(g["row"].get("parallax_error")), num(g["row"].get("phot_g_mean_mag"))
        if not (math.isfinite(plx) and math.isfinite(err) and err > 0 and plx >= PLX_SIGMA * err):
            return None
        return {"plx": plx, "err": err, "g": gm, "bp_rp": num(g["row"].get("bp_rp")), "pc": 1000.0 / plx,
                "abs_g": gm + 5.0 * math.log10(plx / 100.0) if math.isfinite(gm) else math.nan, "sep": g["sep"]}

    def ordinary_star(self) -> str | None:
        """Words naming an ordinary (non-brown-dwarf) catalogued star here, or None."""
        g = self.gaia
        if g:
            gm, bprp = num(g["row"].get("phot_g_mean_mag")), num(g["row"].get("bp_rp"))
            if math.isfinite(gm) and gm < 19.0 and math.isfinite(bprp) and bprp < 1.5:
                return f"Gaia DR3 lists a G {gm:.1f} star with BP − RP {bprp:.2f} {g['sep']:.1f}″ away (a warm, ordinary star)"
        w, jk = self.w1w2(), self.tmass_colour("j", "k")
        if math.isfinite(w) and math.isfinite(jk) and w < 0.1 and jk < 0.85:
            return f"its catalogue colours are an ordinary star's (W1 − W2 {w:.2f}, J − Ks {jk:.2f}; brown dwarfs: W1 − W2 ≥ 0.2, J − Ks ≥ 1.1 or W1 − W2 ≥ 0.6)"
        return None

    def summary(self) -> str:
        """One clause per catalogue: its entry at the place, or none (or not checked)."""
        parts = []
        for k in ("gaia", "catwise", "allwise", "tmass"):
            if not self.checked.get(k):
                parts.append(f"{NAMES[k]} not checked (query failed)")
                continue
            x = self.at(k)
            if not x:
                parts.append(f"{NAMES[k]} nothing")
            elif k == "gaia":
                parts.append(f"Gaia G {num(x['row'].get('phot_g_mean_mag')):.1f}")
            elif k == "catwise":
                parts.append(f"CatWISE W1 {num(x['row'].get('w1mpro')):.1f}, W2 {num(x['row'].get('w2mpro')):.1f}")
            elif k == "allwise":
                w23, snr = self.w2w3()
                parts.append(f"AllWISE W3 {num(x['row'].get('w3mpro')):.1f}" + (f" (S/N {snr:.0f})" if math.isfinite(snr) else "")
                             if fin(x["row"].get("w3mpro")) else "AllWISE W1 {:.1f}".format(num(x["row"].get("w1mpro"))))
            else:
                bands = [f"{b.upper() if b != 'k' else 'Ks'} {num(x['row'].get(f'{b}_m')):.1f}" for b in "jhk" if tmass_detected(x["row"], b)]
                parts.append("2MASS " + (", ".join(bands) if bands else "only limits"))
        if self.checked.get("xsc"):
            x = self.at("xsc", XSC_ARCSEC)
            parts.append("2MASS XSC a galaxy" if x else "no 2MASS galaxy")
        return f"within {ID_ARCSEC:.0f}″: " + "; ".join(parts)

    def values(self) -> dict:
        v: dict = {}
        for k in ("gaia", "catwise", "allwise", "tmass"):
            x = self.at(k)
            if x:
                for key, val in row_values(x).items():
                    if key not in ("catalogue",):
                        v[f"{k}_{key}"] = val
        x = self.at("xsc", XSC_ARCSEC)
        if x:
            v["xsc_sep_arcsec"] = round(x["sep"], 2)
        for name, val in (("w1_w2", self.w1w2()), ("w2_w3", self.w2w3()[0]), ("j_ks", self.tmass_colour("j", "k")),
                          ("j_h", self.tmass_colour("j", "h")), ("h_ks", self.tmass_colour("h", "k"))):
            if math.isfinite(val):
                v[name] = round(val, 3)
        return v


def check(code: str, level: str, text: str, look: str, values: dict | None = None) -> dict:
    return {"code": code, "level": level, "text": text, "look": look, "values": values or None}


def det_words(det) -> str:
    d = int(det) if fin(det) else 2
    return f"D{d}"


# --- movers ---------------------------------------------------------------------------------------
def chance_within(density: dict | None, cat: str, sep: float) -> float:
    """Probability that a random spot of the zone has a `cat` source within `sep` arcsec (Poisson, zone_density)."""
    rho = num((density or {}).get(cat))
    if not math.isfinite(rho):
        return math.nan
    return 1.0 - math.exp(-rho * math.pi * max(sep, 0.5) ** 2)


def mover_checks(s: Spot, density: dict | None = None) -> list[dict]:
    xs = s.any_within(MOVER_STAR_ARCSEC)
    if not xs:
        return []
    by_cat, seen = [], set()
    for x in xs:
        if x["cat"] not in seen:
            seen.add(x["cat"])
            by_cat.append(x)
    first = by_cat[0]
    p = chance_within(density, first["cat"], first["sep"])
    p6 = chance_within(density, first["cat"], MOVER_STAR_ARCSEC)
    odds = (f" By chance alone, {p:.0%} of spots here have a {NAMES[first['cat']]} source this close "
            f"({p6:.0%} within {MOVER_STAR_ARCSEC:.0f}″)." if math.isfinite(p) else "")
    text = "; ".join([describe(first, "its middle position")] + [describe(x) for x in by_cat[1:3]]) + \
        ": it may be that source (a faint star seen in only some exposures, or a dot on its edge) rather than something " \
        "that moves." + odds
    look = (f"On the hours clock in {det_words(s.c.get('det'))}, blink its exposures: if every dot sits on the catalogued spot "
            f"({first['sep']:.1f}″ {direction(first['e'], first['n'])} of the middle) it is that source; a mover's dots step across it.")
    vals = row_values(first)
    vals["n_catalogues"] = len(by_cat)
    vals["chance"] = round(p, 3) if math.isfinite(p) else None
    vals["chance_6arcsec"] = round(p6, 3) if math.isfinite(p6) else None
    return [check("catalogue_star", "warn", text[0].upper() + text[1:], look, vals)]


DENSITY_INNER = MOVER_STAR_ARCSEC  # zone_density counts sources in the ring DENSITY_INNER..QUERY_ARCSEC


def zone_density(cands: list[dict], answers: dict[str, dict[str, list[dict] | None]]) -> dict[str, dict[str, float]]:
    """{zone: {catalogue: sources per arcsec²}} from the ring DENSITY_INNER..QUERY_ARCSEC around its candidates (the
    candidate's own entry is inside the ring, so it doesn't count): what a random spot of the zone has around it."""
    acc: dict[str, dict[str, list[int]]] = {}
    for c in cands:
        if c.get("kind") == "high_pm":
            continue
        for cat in ("gaia", "catwise", "allwise", "tmass", "xsc"):
            rows = answers.get(c["id"], {}).get(cat)
            if rows is None:
                continue
            n = 0
            for r in rows:
                sep = math.hypot(*offset(num(c["ra"]), num(c["dec"]), num(r["ra"]), num(r["dec"])))
                n += DENSITY_INNER <= sep <= QUERY_ARCSEC
            a = acc.setdefault(c.get("zone", ""), {}).setdefault(cat, [0, 0])
            a[0] += n
            a[1] += 1
    area = math.pi * (QUERY_ARCSEC ** 2 - DENSITY_INNER ** 2)
    return {z: {cat: n / (k * area) for cat, (n, k) in d.items() if k} for z, d in acc.items()}


# --- spectral ---------------------------------------------------------------------------------------
BD_LABELS = ("brown_dwarf_like", "ch4")
ICE_LABELS = ("h2o_ice", "co2_ice", "co_ice")
COOL_LABELS = ("h2o_vapour", "co_23")


def label_group(labels: list[str]) -> str:
    ls = set(labels or [])
    if ls & set(BD_LABELS):
        return "bd"
    if ls & set(ICE_LABELS):
        return "ice"
    if ls & set(COOL_LABELS):
        return "cool"
    if "pah_33" in ls:
        return "pah"
    return "bd" if "red" in ls else "other"


def own_colours(own: dict) -> dict:
    """Vega J − H, H − Ks, W1 − W2 from our own spectrum's windows (classify's synthetic colours)."""
    def m(k):
        return num(own.get(f"mag_ab_{k}"))
    return {"jh": m("j") - AB_OFFSET["j"] - (m("h") - AB_OFFSET["h"]), "hk": m("h") - AB_OFFSET["h"] - (m("k") - AB_OFFSET["k"]),
            "w1w2": m("m34") - m("m46") - 0.64}


ICE_HK_MIN, ICE_JH_MIN = 0.6, 1.0   # classify's LABEL_ICE_* (pipeline config)


def ice_rule(jh: float, hk: float, w1w2: float) -> bool:
    """classify.band_labels' reddened-star test (config LABEL_ICE_*), NaN W1 − W2 passes."""
    if not (math.isfinite(jh) and math.isfinite(hk)):
        return False
    return hk >= ICE_HK_MIN and jh >= ICE_JH_MIN and jh >= 1.7 * hk - 0.15 and not (math.isfinite(w1w2) and w1w2 >= 0.4)


def spectral_identity(s: Spot) -> dict:
    """catalogues_checked for a spectral candidate."""
    c, labels = s.c, c_labels(s.c)
    group = label_group(labels)
    summ = s.summary()
    det = det_words(c.get("det"))
    vals = s.values()
    vals["label_group"] = group
    gal, star = s.galaxy(), s.ordinary_star()
    w12, (w23, w3snr), jk = s.w1w2(), s.w2w3(), s.tmass_colour("j", "k")
    anything = s.any_within(ID_ARCSEC, ("gaia", "catwise", "allwise", "tmass")) or s.at("xsc", XSC_ARCSEC)
    look_galaxy = (f"On the months clock in {det}, look at its shape against the stars around it: a galaxy is a soft, "
                   f"slightly wider blob; a brown dwarf or star is a sharp dot the same size as its neighbours.")
    if not anything and all(s.checked.get(k) for k in ("gaia", "catwise", "tmass")):
        ab = ab_of_flux(c.get("flux_ujy"))
        nearest = s.any_within(QUERY_ARCSEC)
        tail = f" The nearest entry: {describe(nearest[0])}." if nearest else ""
        best = max(("gaia", "catwise", "allwise", "tmass"), key=lambda k: len(s.any_within(BLEND_ARCSEC, (k,))))
        n_blend = len(s.any_within(BLEND_ARCSEC, (best,)))
        if n_blend >= 2:
            vals["blend_n"], vals["blend_catalogue"] = n_blend, NAMES[best]
            return check("catalogues_checked", "warn",
                         f"Nothing catalogued within {ID_ARCSEC:.0f}″, but {NAMES[best]} lists {n_blend} sources "
                         f"{ID_ARCSEC:.0f}–{BLEND_ARCSEC:.0f}″ away, inside one SPHEREx pixel: it is likely their "
                         f"blended light, and its spectrum and shape a mix of theirs.{tail}",
                         f"On the months clock in {det}, check whether the dot is stretched toward those neighbours or "
                         f"shifts between visits.", {**vals, "ab_det": round(ab, 2)})
        return check("catalogues_checked", "warn",
                     f"No catalogue lists anything within {ID_ARCSEC:.0f}″ although it is AB {ab:.1f} in {det} "
                     f"(CatWISE reaches about AB 20 at 3.4 µm, 2MASS about AB 17.7 in J): an artefact, a variable or "
                     f"new source, or something that moved since the catalogues were made.{tail}",
                     f"On the months clock in {det}, check it is a round dot in every visit and every detector, not a "
                     f"glint next to a bright star or a dot seen in one visit only.", {**vals, "ab_det": round(ab, 2)})
    px = s.parallax()
    if px:
        vals.update({"gaia_parallax_mas": round(px["plx"], 2), "gaia_parallax_error_mas": round(px["err"], 2),
                     "distance_pc": round(px["pc"], 1), "abs_g": round(px["abs_g"], 2) if math.isfinite(px["abs_g"]) else None})
    if px and px["plx"] < PLX_NEAR_MAS:
        faint = (f", fainter than our reference catalogue's G < {REFCAT_G_MAX:.0f}, so the pipeline saw no Gaia star"
                 if px["g"] >= REFCAT_G_MAX else "")
        return check("catalogues_checked", "strong",
                     f"Gaia DR3 lists it {px['sep']:.1f}″ away (G {px['g']:.1f}{faint}) with a parallax of {px['plx']:.1f} ± "
                     f"{px['err']:.1f} mas ({px['pc']:.0f} pc): a catalogued star beyond 50 pc, which the export leaves out "
                     f"when it sees the match ({summ}).",
                     f"On the months clock in {det}, check the dot is this star (sharp, at its place) and not a neighbour.", vals)
    if group == "bd":
        if gal:
            return check("catalogues_checked", "strong", f"A galaxy explains the brown-dwarf-like spectrum: {gal} ({summ}).",
                         look_galaxy, vals)
        if math.isfinite(w23) and math.isfinite(w3snr) and w23 >= 2.5 and w3snr >= 5 and not (w12 >= 1.0):
            return check("catalogues_checked", "strong",
                         f"WISE colours of a galaxy or quasar, not a brown dwarf: W2 − W3 = {w23:.1f} at W3 S/N {w3snr:.0f} "
                         f"(brown dwarfs below 2.5 unless W1 − W2 is above 1; here {w12:.2f}) ({summ}).", look_galaxy, vals)
        if px and math.isfinite(px["abs_g"]):
            where = (f"Gaia DR3's parallax {px['plx']:.1f} ± {px['err']:.1f} mas puts it at {px['pc']:.0f} pc, so its "
                     f"G {px['g']:.1f} is absolute G {px['abs_g']:.1f}")
            if px["abs_g"] < STAR_ABS_G_MAX:
                return check("catalogues_checked", "strong",
                             f"{where}: too luminous for a brown dwarf (ultracool dwarfs: {UCD_ABS_G_MIN:.0f} and fainter), an "
                             f"ordinary M dwarf or earlier ({summ}).",
                             f"On the months clock, compare D2 (1.1–1.6 µm) with D6 (4.4–5.0 µm): a brown dwarf is much "
                             f"fainter in D2 than in D6.", vals)
            if px["abs_g"] >= UCD_ABS_G_MIN and not (px["bp_rp"] < 2.0):
                return check("catalogues_checked", "ok",
                             f"{where}: as faint as an ultracool dwarf (M8 and later: {UCD_ABS_G_MIN:.0f} and fainter) and no "
                             f"catalogued galaxy ({summ}).",
                             f"On the months clock in {det}, check it is a sharp dot that shifts a little between visits "
                             f"(a nearby dwarf moves).", vals)
        if star:
            return check("catalogues_checked", "strong", f"An ordinary star explains it: {star} ({summ}).",
                         f"On the months clock, compare D2 (1.1–1.6 µm) with D6 (4.4–5.0 µm): a brown dwarf is much "
                         f"fainter in D2 than in D6, an ordinary star about as bright or brighter.", vals)
        if (math.isfinite(w12) and w12 >= 0.25) or (math.isfinite(jk) and jk >= 1.2):
            bits = [f"W1 − W2 {w12:.2f}" if math.isfinite(w12) else "", f"J − Ks {jk:.2f}" if math.isfinite(jk) else ""]
            return check("catalogues_checked", "ok",
                         f"Catalogue colours as red as an L or T dwarf ({', '.join(b for b in bits if b)}; L dwarfs: W1 − W2 "
                         f"0.2–0.6, J − Ks 1.2–2.2; T dwarfs W1 − W2 above 0.6) and no catalogued galaxy ({summ}).",
                         f"On the months clock in {det}, check it is a sharp dot and stays put between visits (a nearby brown "
                         f"dwarf may move a little).", vals)
        return check("catalogues_checked", "warn", f"The catalogues don't settle it: {summ}.",
                     f"On the months clock, compare D2 (1.1–1.6 µm) with D6 (4.4–5.0 µm): a brown dwarf is much fainter "
                     f"in D2.", vals)
    if group == "ice":
        if gal:
            return check("catalogues_checked", "strong", f"A galaxy, not a star behind a cloud: {gal} ({summ}).", look_galaxy, vals)
        jh, hk = s.tmass_colour("j", "h"), s.tmass_colour("h", "k")
        oc = own_colours(s.own)
        src = "2MASS" if math.isfinite(jh) and math.isfinite(hk) else "our spectrum"
        jh = jh if math.isfinite(jh) else oc["jh"]
        hk = hk if math.isfinite(hk) else oc["hk"]
        w = w12 if math.isfinite(w12) else oc["w1w2"]
        vals.update({"rule_j_h": round(jh, 3) if math.isfinite(jh) else None, "rule_h_ks": round(hk, 3) if math.isfinite(hk) else None,
                     "rule_w1_w2": round(w, 3) if math.isfinite(w) else None, "colour_source": src})
        if not (math.isfinite(jh) and math.isfinite(hk)):
            return check("catalogues_checked", "warn", f"No 2MASS colours to test the cloud's reddening: {summ}.",
                         f"On the months clock in D4 (2.4–3.8 µm), the ice dip sits at 3.05 µm; check the source is a "
                         f"single sharp dot.", vals)
        words = f"J − H {jh:.2f}, H − Ks {hk:.2f}" + (f", W1 − W2 {w:.2f}" if math.isfinite(w) else "")
        if ice_rule(jh, hk, w):
            return check("catalogues_checked", "ok",
                         f"Colours of a star reddened by dust ({src}: {words}), as a star seen through an icy cloud should "
                         f"be ({summ}).", f"On the months clock in D4 (2.4–3.8 µm), check the source is a single sharp dot.", vals)
        if hk < ICE_HK_MIN or jh < ICE_JH_MIN:
            return check("catalogues_checked", "strong",
                         f"With {src} colours ({words}) it is not reddened enough to sit behind an icy cloud (ice needs "
                         f"H − Ks ≥ {ICE_HK_MIN} and J − H ≥ {ICE_JH_MIN}: our own ice rule would not label it) ({summ}).",
                         f"On the months clock in D4 (2.4–3.8 µm), check whether a neighbour or a galaxy shares its light.", vals)
        # reddened, but redder at Ks or W2 than dust alone makes it: an infrared excess. Ice is common there too (a young
        # star's own envelope), and ice in W1 itself reddens W1 − W2, so this is a warning, not a sign it is not real.
        return check("catalogues_checked", "warn",
                     f"Reddened by dust ({src}: {words}) and redder at long wavelengths than dust alone makes a star: a "
                     f"young star in its own envelope (ice is common there too) or a cool giant whose molecules can mimic "
                     f"the 3 µm dip; our ice rule keeps only stars behind a cloud ({summ}).",
                     f"On the months clock in D4 (2.4–3.8 µm), check the dip sits at 3.05 µm and is wide, not a narrow dip "
                     f"at 3.1 µm (a cool giant's molecules).", vals)
    if group == "cool":
        if gal:
            return check("catalogues_checked", "strong",
                         f"A galaxy explains it (its old stars carry CO and water bands): {gal} ({summ}).", look_galaxy, vals)
        if (math.isfinite(jk) and jk >= 1.1) or (math.isfinite(w12) and w12 >= 0.2):
            return check("catalogues_checked", "ok",
                         f"Catalogue colours as red as a cool dwarf (J − Ks {jk:.2f}, W1 − W2 {w12:.2f}) and no catalogued "
                         f"galaxy ({summ}).", f"On the months clock in {det}, check it is a sharp dot.", vals)
        return check("catalogues_checked", "warn", f"The catalogues don't settle it: {summ}.", look_galaxy, vals)
    if group == "pah":
        if gal:
            return check("catalogues_checked", "strong",
                         f"A catalogued galaxy: {gal}. PAH emission at 3.3 µm is ordinary in star-forming galaxies ({summ}).",
                         look_galaxy, vals)
        if math.isfinite(w23) and math.isfinite(w3snr) and w23 >= 2.0 and w3snr >= 3 and not (w12 >= 0.8):
            return check("catalogues_checked", "strong",
                         f"WISE colours of an ordinary star-forming galaxy (W2 − W3 = {w23:.1f}, W1 − W2 = {w12:.2f}), where "
                         f"PAH emission at 3.3 µm is expected ({summ}).", look_galaxy, vals)
        if px:
            return check("catalogues_checked", "strong",
                         f"Gaia DR3 measures a parallax of {px['plx']:.1f} ± {px['err']:.1f} mas for its entry {px['sep']:.1f}″ "
                         f"away: a star in our Galaxy, and PAH emission needs a galaxy ({summ}).", look_galaxy, vals)
        g = s.gaia
        if g and fin(g["row"].get("phot_g_mean_mag")) and num(g["row"]["phot_g_mean_mag"]) < 19 and not gal:
            return check("catalogues_checked", "strong",
                         f"Gaia DR3 lists a G {num(g['row']['phot_g_mean_mag']):.1f} point source {g['sep']:.1f}″ away; PAH "
                         f"emission needs a galaxy, so the band is likely noise or a neighbour's light ({summ}).", look_galaxy, vals)
        return check("catalogues_checked", "warn", f"The catalogues don't settle it: {summ}.", look_galaxy, vals)
    return check("catalogues_checked", "warn", f"Catalogues searched: {summ}.", look_galaxy, vals)


def c_labels(c: dict) -> list[str]:
    return list(c.get("labels") or [])


def no_j(s: Spot) -> dict | None:
    """2MASS J at its place against our 4.6 µm magnitude (brown_dwarf_like, ch4)."""
    c = s.c
    if not set(c_labels(c)) & set(BD_LABELS):
        return None
    m46_ab, src46 = num(s.own.get("mag_ab_m46")), "our spectrum"
    if math.isfinite(m46_ab):
        m46 = m46_ab - AB_OFFSET["w2"]
    else:
        x = s.at("catwise")
        m46, src46 = (num(x["row"].get("w2mpro")), "CatWISE W2") if x else (math.nan, "")
    if not math.isfinite(m46) or not s.checked.get("tmass"):
        return None
    look = ("On the months clock, switch between D2 (1.1–1.6 µm, the J band) and D6 (4.4–5.0 µm): a brown dwarf is faint "
            "or missing in D2 and bright in D6; a dot as bright in D2 as in D6 is an ordinary star.")
    x = s.at("tmass")
    vals = {"m46_vega": round(m46, 2), "m46_source": src46}
    own_j = num(s.own.get("mag_ab_j"))
    if math.isfinite(own_j):
        vals["our_j_vega"] = round(own_j - AB_OFFSET["j"], 2)
    if x and tmass_detected(x["row"], "j"):
        j = num(x["row"]["j_m"])
        d = j - m46
        vals.update({"j_mag": round(j, 2), "j_minus_m46": round(d, 2), "sep_arcsec": round(x["sep"], 2)})
        head = (f"2MASS measured J {j:.1f} {x['sep']:.1f}″ from it, against [4.6] {m46:.1f} ({src46}): J − [4.6] = {d:.1f}")
        if d < BD_JW2_ORDINARY:
            return check("no_j", "strong", f"{head}, an ordinary star's colour (brown dwarfs have 2 to 7, M dwarfs about 1.2–1.8).",
                         look, vals)
        if d < BD_JW2_MIN:
            return check("no_j", "warn", f"{head}, between an M dwarf (1.2–1.8) and an L dwarf (2 and more).", look, vals)
        return check("no_j", "ok", f"{head}, as red as an L or T dwarf (2 to 7).", look, vals)
    lim = num(x["row"].get("j_m")) if x and str(x["row"].get("ph_qual") or "X")[0] == "U" and fin(x["row"].get("j_m")) else TMASS_J_LIMIT
    vals.update({"j_limit": round(lim, 2), "j_minus_m46_min": round(lim - m46, 2)})
    if lim - m46 >= BD_JW2_MIN:
        return check("no_j", "ok",
                     f"2MASS saw nothing in J to {lim:.1f} at its place, so J − [4.6] > {lim - m46:.1f} with [4.6] {m46:.1f} "
                     f"({src46}): redder than any ordinary star or M dwarf (below 1.8), as an L or T dwarf would be "
                     f"(or it is not a star at all).", look, vals)
    return check("no_j", "warn",
                 f"2MASS saw nothing in J to {lim:.1f}, too shallow to test it: with [4.6] {m46:.1f} ({src46}) a brown dwarf "
                 f"would have J of {m46 + BD_JW2_MIN:.1f} or fainter.", look, vals)


# --- bright neighbours (spectral, shape) -------------------------------------------------------------
def bright_neighbour(s: Spot) -> dict | None:
    c = s.c
    ab = ab_of_flux(c.get("flux_ujy"))
    det = det_words(c.get("det"))
    best = None
    for cat, magcol, off in (("gaia_bright", "phot_g_mean_mag", 0.0), ("tmass_bright", "k_m", AB_OFFSET["k"]),
                             ("gaia", "phot_g_mean_mag", 0.0), ("tmass", "k_m", AB_OFFSET["k"])):
        for x in s.near.get(cat, []):
            m = num(x["row"].get(magcol))
            if not math.isfinite(m) or x["sep"] < NEIGHBOUR_MIN_ARCSEC:
                continue
            sat = (cat.startswith("gaia") and m < SATURATING_G) or (cat.startswith("tmass") and m < SATURATING_K)
            brighter = math.isfinite(ab) and (ab - (m + off)) >= NEIGHBOUR_DMAG and x["sep"] <= NEIGHBOUR_ARCSEC
            if not (sat and x["sep"] <= BRIGHT_ARCSEC) and not brighter:
                continue
            # rank by the neighbour's light at this distance (brighter and nearer first)
            key = (m + off) + 5.0 * math.log10(max(x["sep"], 1.0))
            if best is None or key < best[0]:
                best = (key, x, m, cat, off)
    if best is None:
        return None
    _, x, m, cat, off = best
    band = "G" if cat.startswith("gaia") else "Ks"
    dmag = ab - (m + off) if math.isfinite(ab) else math.nan
    vals = {**row_values(x), "neighbour_mag": round(m, 2), "neighbour_band": band,
            "dmag": round(dmag, 2) if math.isfinite(dmag) else None, "our_ab": round(ab, 2) if math.isfinite(ab) else None}
    how = f", {dmag:.1f} mag brighter than this source in {det}" if math.isfinite(dmag) and dmag > 0 else ""
    sat = " (bright enough to saturate SPHEREx)" if (band == "G" and m < SATURATING_G) or (band == "Ks" and m < SATURATING_K) else ""
    return check("bright_neighbour", "warn",
                 f"A {band} {m:.1f} star sits {x['sep']:.0f}″ {direction(x['e'], x['n'])}{sat}{how}: its glow, rings or "
                 f"ghosts can add light at this spot and bend the spectrum or the shape.",
                 f"On the months clock in {det}, look {direction(x['e'], x['n'])}: check whether the bright star's halo or "
                 f"rings reach this spot, and whether the source looks the same in visits where the star falls elsewhere "
                 f"on the detector.", vals)


# --- shape --------------------------------------------------------------------------------------------
def shape_galaxy(s: Spot) -> dict | None:
    det = det_words(s.c.get("det"))
    look = (f"On the months clock in {det}, compare its shape with the stars around it: a galaxy is a soft oval in "
            f"every visit; two objects are two peaks, or one that shifts between visits.")
    x = s.at("xsc", XSC_SHAPE_ARCSEC)
    if x:
        return check("galaxy", "strong", f"{describe(x)}: a galaxy's own shape explains a stretched image.", look, row_values(x))
    if not s.checked.get("xsc"):
        return None
    a = s.at("allwise")
    if a and str(a["row"].get("ext_flg")) not in ("", "0", "None", "nan"):
        return check("galaxy", "warn", f"AllWISE fits its entry {a['sep']:.1f}″ away as wider than a point source "
                     f"(ext_flg {a['row']['ext_flg']}), though 2MASS lists no galaxy here.", look, row_values(a))
    g = s.gaia
    if g and str(g["row"].get("in_galaxy_candidates")).lower() in ("true", "1", "t"):
        return check("galaxy", "warn", f"Gaia DR3 lists its entry {g['sep']:.1f}″ away as a galaxy candidate.", look, row_values(g))
    return check("galaxy", "ok", f"No catalogue calls it a galaxy: no 2MASS XSC galaxy within {XSC_SHAPE_ARCSEC:.0f}″ and "
                 f"WISE fits it as a point source.", look, {"xsc_arcsec": XSC_SHAPE_ARCSEC})


# --- high_pm without Gaia -----------------------------------------------------------------------------
# highpm's counterpart (#388, pipeline highpm.COUNTERPARTS): what lists the object, from its deep check along our track
# with the parallax leak allowed for. These replace our own reading where the table has them.
COUNTERPART_LISTED = {"gaia_pm": "lists this star with the same motion",
                      "gaia_position": "lists it without a motion",
                      "catwise_pm": "lists the same motion",
                      "tmass_position": "saw a star", "allwise_position": "saw it"}
COUNTERPART_STILL = {"gaia_still": "lists a source without a motion", "allwise_still": "saw a source"}
COUNTERPART_CATALOGUE = {"gaia": "Gaia DR3", "catwise": "CatWISE2020", "tmass": "2MASS", "2mass": "2MASS",
                         "allwise": "AllWISE"}
HIGHPM_COLUMNS = ["method", "source_id", "source_id_b", "counterpart", "counterpart_id", "counterpart_sep_arcsec",
                  "counterpart_epoch_mjd", "counterpart_offset_now_arcsec"]   # the last: v3.27 tables only


def counterpart_name(kind: str, ref_id) -> str:
    """'gaia:672055699133504768' → 'Gaia DR3 672055699133504768', '2mass:07464209+2000310' → '2MASS J07464209+2000310',
    'allwise:J074642.10+200031.2' → 'AllWISE J074642.10+200031.2'; a bare CatWISE name (highpm's crossid) → 'CatWISE2020
    <name>'; without an id, the catalogue of the counterpart kind."""
    s = str(ref_id or "").strip()
    cat, _, rest = s.partition(":")
    if not rest:
        rest, cat = s, kind.split("_")[0]
    name = COUNTERPART_CATALOGUE.get(cat, cat)
    if cat == "2mass":
        rest = "" if rest.startswith("aw") else ("J" + rest if rest[:1].isdigit() else rest)
    return f"{name} {rest}".strip()


def counterpart_check(c: dict, hp: dict, pm: float, look: str) -> dict:
    """The check from highpm's counterpart: strong either way (a catalogue lists the object, or saw a still source where
    our motion says the object was not)."""
    kind = str(hp.get("counterpart") or "")
    who = counterpart_name(kind, hp.get("counterpart_id"))
    sep, ep = num(hp.get("counterpart_sep_arcsec")), num(hp.get("counterpart_epoch_mjd"))
    yr = 2000.0 + (ep - 51544.5) / 365.25 if math.isfinite(ep) else math.nan
    at = f" in {math.floor(yr)}" if math.isfinite(yr) else ""   # the calendar year (AllWISE 2010.6: 2010)
    off = f"{sep:.2f}″ from " if math.isfinite(sep) else "at "
    vals = {"counterpart": kind, "counterpart_id": str(hp.get("counterpart_id") or "") or None,
            "sep_arcsec": round(sep, 2) if math.isfinite(sep) else None,
            "epoch_year": round(yr, 1) if math.isfinite(yr) else None, "pm_mas_yr": round(pm, 1)}
    if kind in COUNTERPART_STILL:
        # counterpart_sep_arcsec is the entry's distance from our TRACK (>= 4.5″); from today's place it is
        # counterpart_offset_now_arcsec (v3.27; older tables: no number)
        now = num(hp.get("counterpart_offset_now_arcsec"))
        off_now = f"{now:.2f}″ from " if math.isfinite(now) else "at "
        vals["offset_now_arcsec"] = round(now, 2) if math.isfinite(now) else None
        shift = pm * abs(num(c["mjd"]) - ep) / 365.25 / 1000.0 if math.isfinite(ep) else math.nan
        text = (f"{who} {COUNTERPART_STILL[kind]} {off_now}its place today{at}, when its motion ({pm:.0f} mas a year) puts "
                + (f"it {shift:.0f}″ away" if math.isfinite(shift) else "it elsewhere")
                + " (the pipeline's deep check): a still star, so the measured motion is likely not real.")
        return check("catalogues_checked", "strong", text, look, {**vals, "expected_shift_arcsec":
                                                                  round(shift, 1) if math.isfinite(shift) else None})
    text = (f"{who} {COUNTERPART_LISTED[kind]} {off}where its motion ({pm:.0f} mas a year) puts it{at} (the pipeline's "
            "deep check, which allows for the yearly parallax wobble): a catalogued star, not a new one"
            + (" (Gaia gives it no motion)." if kind == "gaia_position" else "."))
    return check("catalogues_checked", "strong", text, look, vals)


def catwise_motion(r: dict) -> tuple[float, float]:
    """(mas/yr, PA °) of a CatWISE row's own motion (its pmra is μα cos δ, arcsec/yr)."""
    pmra, pmdec = num(r.get("pmra")), num(r.get("pmdec"))
    return math.hypot(pmra, pmdec) * 1000.0, math.degrees(math.atan2(pmra, pmdec)) % 360.0


def highpm_checks(s: Spot, answers: dict, hp: dict | None = None) -> list[dict]:
    """hp: highpm's counterpart columns of this candidate's row (highpm_counterparts), None when its table has none."""
    c = s.c
    motion = cand_motion(c)
    if motion is None:
        return []
    still = Spot({**c, "kind": "spectral"}, answers)  # every row where the catalogue saw it, the candidate not moved
    pm = math.hypot(*motion)
    det = det_words(c.get("det"))
    look = (f"On the months clock in {det}, blink the visits: a real moving star shifts by the same step each half year "
            f"along PA {num(c.get('pa_deg')):.0f}°; a still star doesn't move at all.")
    cp = None if hp is None else str(hp.get("counterpart") or "")
    if cp in COUNTERPART_LISTED or cp in COUNTERPART_STILL:   # highpm's deep check decided (#388)
        return [counterpart_check(c, hp, pm, look)]
    guess_still = cp is None or cp == "gaia_crossid"   # highpm's table says nothing (older table, or crossid's Gaia)
    track = [x for k in ("gaia", "catwise", "allwise", "tmass") for x in s.near.get(k, []) if x["sep"] <= ID_ARCSEC]
    if track:
        x = min(track, key=lambda y: y["sep"])
        yr = 2000.0 + (row_epoch(x["cat"], x["row"]) - 51544.5) / 365.25
        nopm = x["cat"] == "gaia" and not fin(x["row"].get("pmra"))
        return [check("catalogues_checked", "strong",
                      f"{describe(x, f'where its motion ({pm:.0f} mas a year) puts it in {yr:.0f}')}: a catalogued star, "
                      f"not a new one" + (" (Gaia gives it no motion, so a match at today's epoch missed it)." if nopm else "."),
                      look, {**row_values(x), "pm_mas_yr": round(pm, 1), "epoch_year": round(yr, 1),
                             "gaia_source_id": str(x["row"].get("source_id")) if x["cat"] == "gaia" else None,  # > 2^53
                             "gaia_without_motion": nopm})]
    shifts, moving = [], []
    for k in ("catwise", "allwise", "tmass"):
        for x in still.near.get(k, []):
            if x["sep"] > ID_ARCSEC:
                continue
            if k == "catwise" and catwise_moving(x["row"]):   # its own motion brought it here: it moves, it isn't still
                moving.append(x)
            elif guess_still:
                shift = pm * abs(num(c["mjd"]) - row_epoch(k, x["row"])) / 365.25 / 1000.0
                if shift >= STATIC_MIN_SHIFT:
                    shifts.append((x, shift))
    if shifts:
        x, shift = min(shifts, key=lambda y: y[0]["sep"])
        yr = 2000.0 + (row_epoch(x["cat"], x["row"]) - 51544.5) / 365.25
        return [check("catalogues_checked", "strong",
                      f"{describe(x, 'its place today')} in {yr:.0f}, when its motion would have put it {shift:.0f}″ away: "
                      f"a still star, so the measured motion is likely not real.", look,
                      {**row_values(x), "pm_mas_yr": round(pm, 1), "expected_shift_arcsec": round(shift, 1)})]
    if moving:
        x = min(moving, key=lambda y: y["sep"])
        cpm, cpa = catwise_motion(x["row"])
        err = num(c.get("pm_err_mas_yr"))
        return [check("catalogues_checked", "warn",
                      f"{describe(x, 'its place today')} once moved by CatWISE's own motion ({cpm:.0f} mas a year towards PA "
                      f"{cpa:.0f}°); ours is {pm:.0f}" + (f" ± {err:.0f}" if math.isfinite(err) else "")
                      + f" mas a year towards PA {num(c.get('pa_deg')):.0f}°: probably this star, catalogued with a "
                      "different motion (CatWISE's motions of fast stars can be off, and so can a fit over two or three "
                      "visits).", look,
                      {**row_values(x), "pm_mas_yr": round(pm, 1), "catwise_pm_mas_yr": round(cpm, 1),
                       "catwise_pa_deg": round(cpa, 1)})]
    nearest = s.any_within(QUERY_MAX_ARCSEC, ("gaia", "catwise", "allwise", "tmass"))
    tail = f" Nearest: {describe(nearest[0], 'its track')}." if nearest else ""
    today = ", nor at today's place" if guess_still else ""
    deep = ("" if guess_still else " The pipeline's deep check (Gaia at any brightness, CatWISE2020, AllWISE and 2MASS "
            "along its track, the yearly parallax wobble allowed for) found no entry either.")
    return [check("catalogues_checked", "warn",
                  f"No catalogue (Gaia DR3 any brightness, CatWISE2020, AllWISE, 2MASS) lists it where its motion "
                  f"({pm:.0f} mas a year) puts it at their epochs{today}.{deep}{tail}", look, {"pm_mas_yr": round(pm, 1)})]


def highpm_counterparts(cands: list[dict], roots: list[str]) -> dict[str, dict]:
    """{high_pm candidate id: highpm's counterpart columns} from results/<zone>/highpm_candidates.parquet (#388), read from
    the first root whose table has them (a local mirror holding a rerun's tables first, then the bucket); zones whose
    tables predate #388 give nothing, and their candidates keep this script's own reading. Ids as the exporter makes them:
    <zone>-pm-<source_id>, a pair <zone>-pm-<source_id>-<source_id_b>."""
    want: dict[str, set[str]] = {}
    for c in cands:
        if c.get("kind") == "high_pm" and not c.get("gaia_match") and c.get("zone"):
            want.setdefault(str(c["zone"]), set()).add(str(c["id"]))
    out: dict[str, dict] = {}
    if not want or not roots:
        return out
    import fsspec
    import pyarrow.parquet as pq

    for zone, ids in sorted(want.items()):
        for root in roots:
            try:
                fs, base = fsspec.core.url_to_fs(str(root).rstrip("/"))
                path = f"{base.rstrip('/')}/results/{zone}/highpm_candidates.parquet"
                if not fs.exists(path):
                    continue
                with fs.open(path, "rb") as f:
                    pf = pq.ParquetFile(f)
                    names = set(pf.schema_arrow.names)
                    if "counterpart" not in names:
                        continue
                    t = pf.read(columns=[x for x in HIGHPM_COLUMNS if x in names]).to_pylist()
            except Exception as e:  # noqa: BLE001 - a missing filesystem or a bad file: this script's own reading
                log.warning("highpm counterparts %s from %s: %s", zone, root, str(e)[:200])
                continue
            for r in t:
                sid, sidb = int(r["source_id"]), int(r.get("source_id_b") if r.get("source_id_b") is not None else -1)
                cid = f"{zone}-pm-{sid}" if str(r.get("method")) != "pair" else f"{zone}-pm-{sid}-{sidb}"
                if cid in ids:
                    out[cid] = {k: r.get(k) for k in HIGHPM_COLUMNS[3:] if k in r}   # older tables: fewer keys
            log.info("highpm counterparts %s: %d of %d candidates (from %s)", zone, sum(i in out for i in ids), len(ids), root)
            break
    return out


# --- per candidate ------------------------------------------------------------------------------------
LEVEL_ORDER = {"strong": 0, "warn": 1, "ok": 2}


def candidate_checks(c: dict, answers: dict[str, list[dict] | None], own: dict | None = None,
                     density: dict | None = None, hp: dict | None = None) -> list[dict] | None:
    """The checks of one candidate (None: not measured). `density`: its zone's catalogue densities (zone_density); `hp`:
    a high_pm candidate's highpm counterpart columns (highpm_counterparts; None: its table has none)."""
    kind = c.get("kind")
    if kind == "high_pm" and c.get("gaia_match"):
        return None
    s = Spot(c, answers, own)
    if kind == "mover":
        out = mover_checks(s, density)
    elif kind == "spectral":
        out = [spectral_identity(s), no_j(s), bright_neighbour(s)]
    elif kind == "shape":
        out = [shape_galaxy(s), bright_neighbour(s)]
    elif kind == "high_pm":
        out = highpm_checks(s, answers, hp)
    else:
        return None
    out = [x for x in out if x]
    for x in out:
        x["values"] = clean_values(x.get("values"))
    return sorted(out, key=lambda x: LEVEL_ORDER[x["level"]])


def clean_values(v: dict | None) -> dict | None:
    if not v:
        return None
    out = {}
    for k, x in v.items():
        if isinstance(x, float) and not math.isfinite(x):
            x = None
        out[k] = x
    return out


# --- queries ------------------------------------------------------------------------------------------
def query_radius(c: dict, cat: str = "gaia", gaia_pm: float = math.nan) -> float:
    """Arcsec: QUERY_ARCSEC; for a high_pm candidate + its motion back to 2MASS's epoch (capped). A spectral or shape
    candidate on a fast Gaia star (`gaia_pm` mas/yr, its own entry) is searched in the other catalogues out to where
    that motion puts it in 1998, so 2MASS still finds a nearby dwarf that has moved 10″ and more since."""
    if CATALOGUES[cat][3] == "bright":
        return BRIGHT_ARCSEC
    if c.get("kind") == "high_pm":
        pm = num(c.get("pm_mas_yr"))
        if math.isfinite(pm):
            back = pm * max(num(c.get("mjd")) - TMASS_MJD, 0.0) / 365.25 / 1000.0
            return min(QUERY_ARCSEC + back, QUERY_MAX_ARCSEC)
    if cat == "gaia" and c.get("kind") in ("spectral", "shape") and c.get("gaia_match"):
        return GAIA_MATCH_ARCSEC  # its Gaia star may have moved 10″ and more since 2016 (no motion in the export)
    if cat != "gaia" and c.get("kind") in ("spectral", "shape") and math.isfinite(gaia_pm):
        back = gaia_pm * max(num(c.get("mjd")) - TMASS_MJD, 0.0) / 365.25 / 1000.0
        if ID_ARCSEC + back + 1.0 > QUERY_ARCSEC:
            return min(math.ceil(ID_ARCSEC + back + 1.0), QUERY_MAX_ARCSEC)
    return QUERY_ARCSEC


def own_gaia_pm(c: dict, rows: list[dict] | None) -> float:
    """mas/yr of the candidate's own Gaia entry (within ID_ARCSEC at its epoch), nan when none or no motion."""
    xs = [x for x in near(c, "gaia", rows or []) if x["sep"] <= ID_ARCSEC]
    if not xs or not (fin(xs[0]["row"].get("pmra")) and fin(xs[0]["row"].get("pmdec"))):
        return math.nan
    return math.hypot(num(xs[0]["row"]["pmra"]), num(xs[0]["row"]["pmdec"]))


def wanted(c: dict, cat: str) -> bool:
    kind = c.get("kind")
    if kind == "high_pm" and c.get("gaia_match"):
        return False
    if CATALOGUES[cat][3] == "bright":
        return kind in ("spectral", "shape")
    return True


def qkey(ra: float, dec: float, r: float) -> str:
    return f"{ra:.6f},{dec:+.6f},{r:.1f}"


class Cache:
    """{catalogue: {qkey: rows}} on disk, one JSON per catalogue (rows as dicts)."""

    def __init__(self, folder: Path | None):
        self.folder = folder
        self.data: dict[str, dict[str, list[dict]]] = {}

    def path(self, cat: str) -> Path:
        return self.folder / f"{cat}.json"

    def load(self, cat: str) -> dict[str, list[dict]]:
        if cat not in self.data:
            d: dict = {}
            if self.folder is not None and self.path(cat).exists():
                raw = json.loads(self.path(cat).read_text())
                if raw.get("columns") == CATALOGUES[cat][1] and raw.get("where") == CATALOGUES[cat][2]:
                    d = raw.get("answers", {})
            self.data[cat] = d
        return self.data[cat]

    def save(self, cat: str) -> None:
        if self.folder is None:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path(cat).with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps({"columns": CATALOGUES[cat][1], "where": CATALOGUES[cat][2], "answers": self.data[cat]}))
        tmp.replace(self.path(cat))


def irsa_crossmatch(cat: str, pos: list[tuple[float, float, float]]) -> list[list[dict]]:
    """One TAP query at IRSA with the positions uploaded: rows within each (ra, dec, radius arcsec), in input order."""
    import pyvo
    from astropy.table import Table

    table, cols, where, _ = CATALOGUES[cat]
    up = Table({"k": np.arange(len(pos), dtype=np.int32), "ra": [p[0] for p in pos], "dec": [p[1] for p in pos],
                "r": [p[2] / 3600.0 for p in pos]})
    adql = (f"SELECT u.k, {', '.join('c.' + c for c in cols)} FROM {table} AS c, TAP_UPLOAD.u AS u "
            f"WHERE CONTAINS(POINT('ICRS', c.ra, c.dec), CIRCLE('ICRS', u.ra, u.dec, u.r)) = 1"
            + (f" AND {where}" if where else ""))
    svc = pyvo.dal.TAPService(IRSA_TAP)
    t = svc.run_sync(adql, uploads={"u": up}, maxrec=2_000_000).to_table()
    out: list[list[dict]] = [[] for _ in pos]
    for row in t:
        d = {}
        for col in cols:
            v = row[col]
            if np.ma.is_masked(v):
                d[col] = None
            elif isinstance(v, (bytes, np.bytes_)):
                d[col] = v.decode().strip()
            elif isinstance(v, (str, np.str_)):
                d[col] = str(v).strip()
            elif isinstance(v, (bool, np.bool_)):
                d[col] = bool(v)
            elif isinstance(v, (np.integer, int)):
                d[col] = int(v)
            else:
                f = float(v)
                d[col] = f if math.isfinite(f) else None
        out[int(row["k"])].append(d)
    return out


def fetch(cands: list[dict], cache: Cache, query=irsa_crossmatch, batch: int = UPLOAD_BATCH) -> dict[str, dict[str, list[dict] | None]]:
    """{candidate id: {catalogue: rows or None (query failed)}}; asks only for positions not cached yet."""
    out: dict[str, dict[str, list[dict] | None]] = {c["id"]: {} for c in cands}
    for cat in CATALOGUES:  # Gaia first: its motions set the other catalogues' radius (query_radius)
        keys = {}
        for c in cands:
            if wanted(c, cat):
                gpm = own_gaia_pm(c, out[c["id"]].get("gaia")) if cat != "gaia" else math.nan
                keys[c["id"]] = (num(c["ra"]), num(c["dec"]), query_radius(c, cat, gpm))
        ans = cache.load(cat)
        todo = sorted({qkey(*p): p for p in keys.values() if qkey(*p) not in ans}.items())
        log.info("%s: %d candidates, %d positions to ask (%d cached)", cat, len(keys), len(todo), len(keys) - len(todo))
        for i in range(0, len(todo), batch):
            part = todo[i:i + batch]
            for attempt in range(1, NET_RETRIES + 1):
                try:
                    t0 = time.time()
                    rows = query(cat, [p for _, p in part])
                    for (k, _), rs in zip(part, rows):
                        ans[k] = rs
                    cache.save(cat)
                    log.info("%s: %d-%d of %d asked, %d rows in %.1f s", cat, i, i + len(part), len(todo),
                             sum(len(r) for r in rows), time.time() - t0)
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("%s batch %d try %d: %s", cat, i, attempt, str(e)[:300])
                    if attempt < NET_RETRIES:
                        time.sleep(10 * attempt)
        for cid, p in keys.items():
            out[cid][cat] = ans.get(qkey(*p))  # None: never answered
    return out


# --- our own magnitudes (classes.parquet) -----------------------------------------------------------------
OWN_COLUMNS = ["source_id", "class", "mag_ab_j", "mag_ab_h", "mag_ab_k", "mag_ab_m34", "mag_ab_m46", "j_m46_ab"]
OWN_WORKERS = 2  # zones read at once (~5 MB of columns each)


def source_id_of(c: dict) -> int | None:
    parts = str(c["id"]).split("-")
    try:
        return int(parts[-1]) if c["kind"] in ("spectral", "shape") else None
    except ValueError:
        return None


def own_magnitudes(cands: list[dict], root: str | None, col_cache: str | None, cache: Cache) -> dict[str, dict]:
    """{candidate id: {mag_ab_j, …}} for spectral and shape candidates: cached in cache/own_mags.json, else read from
    results/<zone>/classes.parquet (column reads through the pipeline's exporter, --col-cache shared with it)."""
    path = cache.folder / "own_mags.json" if cache.folder else None
    have = json.loads(path.read_text()) if path and path.exists() else {}
    need = [c for c in cands if c["kind"] in ("spectral", "shape") and c["id"] not in have and source_id_of(c) is not None]
    if need and root:
        sys.path.insert(0, str(ROOT / "pipeline" / "src"))
        from skyshift_pipeline import candidates as X
        from skyshift_pipeline.store import Store, results_rel
        if col_cache:
            X.CACHE_DIR = Path(col_cache)
        import threading
        from concurrent.futures import ThreadPoolExecutor
        store = Store(root)
        by_zone: dict[str, list[dict]] = {}
        for c in need:
            by_zone.setdefault(c["zone"], []).append(c)
        lock = threading.Lock()

        def one(item):
            zone, cs = item
            rel = results_rel(zone, "classes.parquet")
            try:
                df = X.read_columns(store, rel, OWN_COLUMNS, ids=np.array([source_id_of(c) for c in cs], np.int64))
            except Exception as e:  # noqa: BLE001
                log.warning("own magnitudes %s: %s", zone, str(e)[:200])
                return
            rows = {int(r["source_id"]): r for r in df.to_dict("records")}
            with lock:
                for c in cs:
                    r = rows.get(source_id_of(c))
                    if r is not None:
                        have[c["id"]] = {k: (None if isinstance(v, float) and not math.isfinite(v) else
                                             (float(v) if isinstance(v, (float, np.floating)) else str(v)))
                                         for k, v in r.items() if k != "source_id"}
                if path:  # after every zone: a stopped run keeps what it read
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(have))
                log.info("own magnitudes %s: %d of %d", zone, sum(c["id"] in have for c in cs), len(cs))

        with ThreadPoolExecutor(OWN_WORKERS) as ex:
            list(ex.map(one, sorted(by_zone.items())))
    return have


# --- main -----------------------------------------------------------------------------------------------
def run(cands_path: Path, out_path: Path | None, cache_dir: Path | None, root: str | None = None,
        col_cache: str | None = None, kinds: tuple[str, ...] | None = None, query=irsa_crossmatch,
        highpm_roots: list[str] | None = None) -> dict:
    """highpm_roots: where to look for results/<zone>/highpm_candidates.parquet before `root` (highpm_counterparts)."""
    doc = json.loads(Path(cands_path).read_text())
    cands = [c for c in doc["candidates"] if kinds is None or c["kind"] in kinds]
    cache = Cache(cache_dir)
    answers = fetch(cands, cache, query)
    own = own_magnitudes(cands, root, col_cache, cache)
    density = zone_density(cands, answers)
    hp = highpm_counterparts(cands, [*(highpm_roots or []), *([root] if root else [])])
    res: dict[str, dict] = {}
    for c in cands:
        ch = candidate_checks(c, answers[c["id"]], own.get(c["id"]), density.get(c.get("zone", "")), hp.get(c["id"]))
        if ch is None:
            continue
        res[c["id"]] = {"checks": ch, "status": None, "known_name": None}
    out = {"source": SOURCE, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "candidates_file": str(cands_path), "candidates": res}
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        tmp.replace(out_path)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--cache", default=str(DEFAULT_CACHE), help="IRSA answers per catalogue and position (and own magnitudes)")
    ap.add_argument("--root", default=None, help="results root for our own magnitudes (e.g. gs://nasa-glazing-data); "
                                                 "without it, spectral checks use the catalogues and the export's flux only")
    ap.add_argument("--col-cache", default=None, help="the exporter's column-read cache folder (shared)")
    ap.add_argument("--highpm-root", action="append", default=None,
                    help="also look here for results/<zone>/highpm_candidates.parquet, before --root (repeatable; e.g. "
                         "the local mirror after a highpm rerun): its counterpart columns (#388) replace this script's "
                         "own still/moving reading of high_pm candidates")
    ap.add_argument("--kinds", default=None, help="comma list: only these kinds")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kinds = tuple(k.strip() for k in a.kinds.split(",")) if a.kinds else None
    out = run(Path(a.candidates), Path(a.out), Path(a.cache) if a.cache else None, a.root, a.col_cache, kinds,
              highpm_roots=a.highpm_root)
    n = {}
    for v in out["candidates"].values():
        for ch in v["checks"]:
            n[(ch["code"], ch["level"])] = n.get((ch["code"], ch["level"]), 0) + 1
    log.info("wrote %s: %d candidates measured; %s", a.out, len(out["candidates"]),
             ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(n.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
