"""Zones, tile grid, and every tunable threshold of the hunt-zone pipeline, in one place.

Tune after the pilot: change the numbers here, rerun `link`, `score` and `suspects` (they are cheap;
`dots` and `known` are not affected).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    name: str
    ra_min: float
    ra_max: float
    dec_min: float
    dec_max: float

    def contains(self, ra, dec):
        """True where (ra, dec) lies inside the RA/Dec box (works on numpy arrays)."""
        return (ra >= self.ra_min) & (ra < self.ra_max) & (dec >= self.dec_min) & (dec < self.dec_max)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.ra_min + self.ra_max) / 2.0, (self.dec_min + self.dec_max) / 2.0


ZONES: dict[str, Zone] = {
    "hunt": Zone("hunt", 120.5, 130.5, 14.4, 24.4),     # 100 deg² on the ecliptic
    "pilot": Zone("pilot", 120.5, 124.1, 14.4, 17.9),   # 12 deg², the hunt zone's corner
    # All-sky test (14 Nov, docs/research/2026-11-14-allsky-test.md): 0.5° × 0.5° on the sky around sky types the
    # pilot never saw; run photolist, refcat, dots --limit only (every photo covers the whole box).
    "t_cygnus": Zone("t_cygnus", 304.674, 305.326, 39.75, 40.25),    # Galactic plane, Cygnus (b ≈ +2.5°)
    "t_gc": Zone("t_gc", 265.714, 266.286, -29.25, -28.75),          # towards the Galactic centre (b ≈ −0.3°)
    "t_lmc": Zone("t_lmc", 80.176, 81.624, -70.05, -69.55),          # Large Magellanic Cloud bar
    "t_smc": Zone("t_smc", 12.355, 14.045, -73.05, -72.55),          # Small Magellanic Cloud
    "t_nep": Zone("t_nep", 269.372, 270.628, 66.31, 66.81),          # north ecliptic pole
    "t_sep": Zone("t_sep", 89.372, 90.628, -66.81, -66.31),          # south ecliptic pole
    "t_ngp": Zone("t_ngp", 192.579, 193.141, 26.88, 27.38),          # north Galactic pole
    # featured star targets (web/src/data/targets.json): 1.2° boxes around the Gaia 2016 position; the photo list is
    # cut to the photos their static frames use (14 Nov evening), so the blink can show what SkyShift measured there
    "t_barnard": Zone("t_barnard", 268.846, 270.051, 4.139, 5.339),           # Barnard's Star
    "t_epsindib": Zone("t_epsindib", 329.981, 332.172, -57.394, -56.194),     # Epsilon Indi B
    "t_wise0855": Zone("t_wise0855", 133.178, 134.387, -7.844, -6.644),       # WISE 0855-0714
    "t_rwaur": Zone("t_rwaur", 76.260, 77.652, 29.801, 31.001),               # RW Aurigae
    "t_rdra": Zone("t_rdra", 246.647, 249.688, 66.155, 67.355),               # R Draconis
}

# Sky run (14 Nov): 10° x 10° zones stacked along the ecliptic, aligned on the hunt zone. Column k covers
# RA 0.5 + 10k .. 10.5 + 10k (k = 0..35; column 36 is the RA 0-0.5 strip); its row 0 is centred on the
# ecliptic's Dec at the column's middle RA (rounded to 0.1°, as the hunt zone), rows ±1, ±2, ... are the
# 10° boxes above/below. Names: ecl<kk> (row 0), ecl<kk>n<r> / ecl<kk>s<r>. Column 12 row 0 IS "hunt".
ECLIPTIC_OBLIQUITY_DEG = 23.4393
ECLIPTIC_MAX_ROW = 4


def _ecliptic_dec(ra_deg: float) -> float:
    import math
    return math.degrees(math.atan(math.sin(math.radians(ra_deg)) * math.tan(math.radians(ECLIPTIC_OBLIQUITY_DEG))))


def ecliptic_zones(max_row: int = ECLIPTIC_MAX_ROW) -> dict[str, Zone]:
    out: dict[str, Zone] = {}
    cols = [(k, 0.5 + 10.0 * k, min(10.5 + 10.0 * k, 360.0)) for k in range(36)] + [(36, 0.0, 0.5)]
    for k, r0, r1 in cols:
        c = round(_ecliptic_dec((r0 + r1) / 2.0), 1)
        for row in range(-max_row, max_row + 1):
            if (k, row) == (12, 0):
                continue  # the hunt zone
            d0, d1 = round(c - 5.0 + 10.0 * row, 1), round(c + 5.0 + 10.0 * row, 1)
            if d1 <= -90.0 or d0 >= 90.0:
                continue
            name = f"ecl{k:02d}" + ("" if row == 0 else ("n" if row > 0 else "s") + str(abs(row)))
            out[name] = Zone(name, r0, r1, max(d0, -90.0), min(d1, 90.0))
    return out


ZONES.update(ecliptic_zones())

# --- photo search ----------------------------------------------------------------------------
COLLECTIONS = ("spherex_dr1", "spherex_qr3")  # in order of preference on duplicates; *_deep skipped
PHOTO_HALF_DIAG_DEG = 2.6  # a 3.5° photo reaches ~2.5° from its centre
S3_BUCKET = "nasa-irsa-spherex"
S3_HTTPS = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
IRSA_URI_PREFIX = "ibe/data/spherex/"  # spherex.artifact.uri = this + the S3 key

# --- sessions -------------------------------------------------------------------------------
# A session is a run of the ZONE's exposure times with gaps under 10 minutes (core.sessions on
# every photo of the zone), one value per photo; visit = core.visits on the same times.
# Coverage at a position = distinct sessions whose photo footprints contain it.

# --- dots ----------------------------------------------------------------------------------
# core.detect's thresh is per pixel: thresh=5, minarea 3 is really ~22σ PSF S/N. thresh=2 is about
# S/N 9 with no false detections on a blank frame; then keep aperture snr >= DOT_MIN_SNR.
DETECT_THRESH = 2.0
DOT_MIN_SNR = 5.0
PIXEL_ARCSEC = 6.152
NET_RETRIES = 3
SAT_RECENTROID_HALF_PX = 12    # stamp half-size for the wings fit of saturated dots
FINAST_OK = 0                  # IMAGE header FINAST != 0 means bad astrometry: skip the photo
DOTS_TASK_PHOTOS = 4           # photos per worker task (one process per core); the run order is unchanged
DOTS_PREFETCH = True           # read the next photo of a task while this one is measured (same results)
# pos_err_arcsec = sqrt((POS_ERR_K * PSF_SIGMA_ARCSEC / snr)^2 + POS_ERR_FLOOR_ARCSEC^2)  (contracts/dots.md)
POS_ERR_K = 1.4
PSF_SIGMA_ARCSEC = 3.2
POS_ERR_FLOOR_ARCSEC = 0.25    # measured on the first photo; V3 will replace it
ELONG_R_PX = 2.5               # elong: second moments of the pixels within this radius
ELONG_BKG_PX = (3.5, 5.5)      # local background annulus for elong

# --- known (MPC orbits with kete) ------------------------------------------------------------
# known_mpc.py: MPC orbits moved with kete to each photo's MJD-AVG, seen from SPHEREx's header position.
# One cache file per session in cache/mpc/<zone>/; score and suspects read it.
MPC_ORBITS_URL = "https://minorplanetcenter.net/Extended_Files/mpcorb_extended.json.gz"
MPC_ORBITS_DIR = "cache/mpc/orbits"  # mpcorb_<date>.parquet + cometels_<date>.parquet: dated snapshots, newest used everywhere
MPC_V_MAX = 21.0                     # keep objects brighter than this (score uses V < 16 to 18)
MPC_G_DEFAULT = 0.15                 # H-G slope when the MPC has none
MPC_HEADER_WORKERS = 32              # parallel IMAGE header reads (one per exposure; ~1 s each, I/O bound)
MPC_HEADER_BLOCK = 262_144           # bytes per HTTPS read: the primary and IMAGE headers fit in the first block
MPC_FOV_PAD_DEG = 0.05               # kete fields are the s_region corners pushed out 3′; load_predictions cuts exactly

# --- link ----------------------------------------------------------------------------------
FIXED_LINK_ARCSEC = 4.0       # friends-of-friends radius for fixed sources
FIXED_SNAP_ARCSEC = 1.0       # dots are first snapped to cells this big (speed); FoF runs on cells
FIXED_MIN_SESSIONS = 3        # a group in >= this many sessions is fixed
FIXED_VETO_ARCSEC = 6.0       # dots this close to a fixed source are dropped before pairing
PAIR_BASE_ARCSEC = 3.0        # two exposures pair if separated <= base + rate x dt
PAIR_RATE_ARCSEC_PER_MIN = 1.0
SIMULTANEOUS_PAIR_ARCSEC = 3.0
SIMULTANEOUS_PAIRS = ((1, 4), (2, 5), (3, 6))
MOVER_MAX_ELONG = 3.0         # dots longer than this (trails, cosmic rays) don't pair
MOVER_MIN_COVER_SESSIONS = 3  # sessions whose footprints contain the candidate
SOURCE_ID_PER_CELL = 100      # source_id = 6″ cell × this + rank of the source inside the cell

# --- spectra -------------------------------------------------------------------------------
# Our channel definitions (not NASA's official ones): 17 log-spaced channels per detector over
# the detector ranges below; a dot outside its detector's range goes to that detector's end channel.
SPECTRA_DETECTOR_UM = {1: (0.75, 1.1), 2: (1.1, 1.6), 3: (1.6, 2.4), 4: (2.4, 3.8), 5: (3.8, 4.4), 6: (4.4, 5.0)}
SPECTRA_CHANNELS_PER_DETECTOR = 17
SPECTRA_CLIP_SIGMA = 5.0        # drop a dot this far from its channel median, in max(its error, channel MAD scatter)
SPECTRA_CLIP_MIN_FRAC = 1.5     # ... and further than this × |channel median|: only gross outliers go, so a
                                # star that doubles keeps both states (a MAD-only clip drops the minority state)
SPECTRA_CLIP_MIN_N = 3          # clip only channels with at least this many dots
SPECTRA_CLIP_ITER = 3
SPECTRA_POS_ERR_FLOOR_ARCSEC = 0.05  # floor on the scatter/sqrt(n) position error of a source
SPECTRA_SAT_MIN_DOTS = 2        # `saturated` if at least this many of the source's dots have OVERFLOW/NONLINEAR in 3x3
SPECTRA_VARIABLE_CHI2_NU = 5.0  # `variable` = chi2_nu above this AND visit_flux_ratio beyond the factor below
SPECTRA_VARIABLE_RATIO = 1.25
SPECTRA_MEMBER_MAX_ARCSEC = 10.0  # a member dot further than this from its source is stale (dot file rewritten): dropped

# --- score ---------------------------------------------------------------------------------
SCORE_V_MAX = 16.0
SCORE_V_SATURATED = 12.0      # V < 12 reported apart: saturated cores, wings centroid
SCORE_EDGE_PX = 10.0
SCORE_MIN_EXPOSURES = 2       # a unit = (asteroid, session) with >= 2 exposures covering it
MATCH_ARCSEC = 12.0
CONTROL_OFFSET_ARCSEC = 120.0  # control: predictions moved 2′ in random directions
CONTROL_SEED = 20261114
G2_RECALL_MIN = 0.5
V_BINS = (-99.0, 12.0, 14.0, 15.0, 16.0, 17.0, 18.0)

# --- suspects ------------------------------------------------------------------------------
KNOWN_ARCSEC = 30.0
GAIA_ARCSEC = 6.0
CATWISE_ARCSEC = 6.0
BRIGHT_FIXED_ARCSEC = 30.0
BRIGHT_FIXED_SNR = 100.0       # a fixed source this bright makes ghosts and halos around it
SUSPECT_VETO_BITS = (1 << 22) | (1 << 23) | (1 << 24) | (1 << 20) | (1 << 28) | (1 << 26)
# GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO, BLOOM
SOURCE_BIT = 1 << 21           # catalogue stars / compact galaxies to AB ~19; in flags_peak -> drop
SOURCE_PHOTO_MAX_FRAC = 0.9    # a photo with SOURCE at the peak of more than this share of its dots
                               # (seen: 99.98% on 2025W42_2A_0111_1D1) carries no information: ignore it there
CATALOG_BATCH = 50
EVIDENCE_RANK = {"two_exposures": 0, "two_detectors": 1}

# --- motion and time: crossid, colours, lightcurves, astrometry, highpm --------------------
# All computed from stored dots (via members.parquet), sources.parquet, refcat/<zone>.parquet and
# forced/<obs_id>D<det>.parquet; photos are never read again. Rerun freely: they always recompute.
REFCAT_REL = "refcat/{zone}.parquet"     # Gaia DR3 / CatWISE / 2MASS unified, at the bucket root
REFCAT_FALLBACK_REL = "refcat/{zone}_forced.parquet"  # the forced-photometry targets, if the full one is missing
FORCED_DIR = "forced"                    # forced/<obs_id>D<det>.parquet
CROSSID_ARCSEC = {"gaia": 2.5, "catwise": 3.0, "tmass": 3.0}  # match radius per catalogue
CROSSID_SLACK_ARCSEC = 2.0     # extra search radius before the per-source epoch refinement
# colours: simultaneous pairs only (same obs_id), so variability cancels
COLOUR_MIN_PAIRS = 1           # simultaneous dot pairs needed for a colour
# lightcurves: compare within a detector, normalised by the source's own spectrum
LC_DETECTED_SNR = 5.0          # a forced point without a `detected` column is detected at this S/N
LC_VARIABLE_CHI2_NU = 5.0      # variable = chi2_nu above this AND relative scatter above the next
LC_VARIABLE_SCATTER = 0.05     # robust scatter of flux / model (fraction)
LC_MIN_POINTS = 3              # summary rows need this many points
# astrometry: weighted least squares on the tangent plane about each source's median position
ASTRO_MIN_DOTS = 6             # dots needed for a fit (position + proper motion)
ASTRO_PARALLAX_MIN_VISITS = 3  # parallax is fitted only with this many visits (two are degenerate with pm)
ASTRO_CLIP_SIGMA = 5.0         # one refit without dots beyond this many sigma
ASTRO_CHI2_INFLATE_P = 0.95    # errors × √χ²_ν only where χ²_ν beyond this quantile of χ²(ν)/ν (a real excess);
                               # √max(1, χ²_ν) for every source inflated errors 1.5× at ~7 dots (pm pulls 0.85)
# session-robust errors (15 Nov, hunt tail): dots of one session share errors, so pm errors also come from the scatter of
# session means (cluster-robust sandwich over source x session), whichever is larger; sources with fewer sessions: x sqrt(max(1, chi2_nu))
# dots left out of motion fits: a masked (not saturated) peak, and all but the nearest of several dots of one photo
ASTRO_CLEAN_DOTS = True
ASTRO_SESSION_ROBUST = True
ASTRO_ROBUST_MIN_SESSIONS = 6  # sessions needed for the session-robust error
# ... and detectors that disagree within a visit: sandwich over visit x detector clusters where beyond this chi2 quantile
ASTRO_DETECTOR_ROBUST = True
ASTRO_DETECTOR_ZEROPOINTS = True  # a position zero point per source x detector: pm only from changes within a detector
ASTRO_DETECTOR_ROBUST_P = 0.95
ASTRO_FRAME_CORRECT = True     # remove each photo's median offset against Gaia (quiet matched stars)
ASTRO_FRAME_MIN_STARS = 10     # ... only with at least this many reference stars in the photo
ASTRO_FRAME_PM_MAX = 50.0      # mas/yr: reference stars for the frame offset move less than this
ASTRO_REPORT_SNR_BINS = (5.0, 10.0, 20.0, 50.0, 100.0, 1e9)  # median dot S/N bins of the report
# highpm: "Planet X"-style search for nearby cold movers not in Gaia
HIGHPM_MIN_SEP_ARCSEC = 3.0    # a source seen in one visit only, with a counterpart in another visit
HIGHPM_MAX_SEP_ARCSEC = 120.0  # between these separations
HIGHPM_FLUX_RATIO_MAX = 2.0    # same-detector flux ratio within this factor (either way)
HIGHPM_SHAPE_MAX = 0.25        # rms of ln(flux ratio) across shared detectors (spectrum shape)
HIGHPM_MIN_COVER_PHOTOS = 2    # the other visit must have this many photos covering each position
HIGHPM_FIT_MIN_MAS_YR = 150.0  # fitted proper motion this fast ...
HIGHPM_FIT_MIN_SNR = 5.0       # ... and this significant also makes a candidate (method "fit")
HIGHPM_RECALL_MAS_YR = 200.0   # recall check: Gaia stars faster than this in the zone
HIGHPM_RECALL_ARCSEC = 3.0     # a candidate this close (at its epoch) recovers the Gaia star
HIGHPM_CONTROL_OFFSET_ARCSEC = 900.0  # control: one visit's sources moved 15′ east (chance-pair count)
HIGHPM_CATWISE_PM_TOL = 0.3    # CatWISE pm agrees within 30% of |pm| (or 100 mas/yr) = bonus

# --- classify: star / galaxy / unsure, spectral indices, brown-dwarf-like (classify.py, indices.py) ----------
# Index windows in µm (observed frame). A band value is the mean of the 102-channel spectrum over the window
# (each channel weighted by the fraction of it inside the window); the continuum is a power law through the
# two continuum windows (linear when a window is too faint for logs), evaluated at the band's wavelength.
# kind "abs": depth = 1 − band / continuum, sig > 0 = absorption; "emis": excess = band / continuum − 1, sig > 0 = emission.
INDEX_WINDOWS = {
    #  name          kind    band            continuum windows
    "h2o_ice_305": ("abs", (2.95, 3.15), ((2.45, 2.70), (3.65, 3.80))),   # H2O ice toward background stars
    "co2_ice_427": ("abs", (4.23, 4.31), ((4.05, 4.17), (4.40, 4.50))),   # CO2 ice
    "co_ice_467":  ("abs", (4.64, 4.70), ((4.50, 4.58), (4.76, 4.90))),   # CO ice (cool-star CO gas also lands here)
    "ch4_33":      ("abs", (3.20, 3.45), ((2.40, 2.65), (3.90, 4.10))),   # CH4 fundamental: T/Y dwarfs
    "ch4_16":      ("abs", (1.63, 1.72), ((1.53, 1.59), (2.06, 2.14))),   # CH4 overtone in H: T dwarfs
    "co_23":       ("abs", (2.29, 2.40), ((2.06, 2.14), (2.19, 2.28))),   # CO bandhead: cool giants (continuum extrapolated)
    "h2o_14":      ("abs", (1.34, 1.45), ((1.22, 1.30), (1.55, 1.65))),   # H2O: M/L dwarfs (and Miras)
    "h2o_19":      ("abs", (1.84, 1.95), ((1.55, 1.65), (2.06, 2.14))),   # H2O: M/L dwarfs
    "pah_33":      ("emis", (3.24, 3.34), ((3.00, 3.15), (3.50, 3.65))),  # PAH 3.3 µm at z ≈ 0 (photoz redoes it at z_best)
}
INDEX_MIN_COVER = 0.5          # a window needs at least this share of its channel weight measured, else NaN
INDEX_LOG_CONT_MIN_SNR = 3.0   # both continuum windows at S/N >= this and > 0: power law; else linear in λ
INDEX_SIG = 5.0                # an index counts as detected at |sig| >= this
# synthetic AB magnitudes from the spectrum (window means), for colours and the web
MAG_WINDOWS = {"j": (1.15, 1.33), "h": (1.50, 1.78), "k": (2.00, 2.30), "m34": (3.30, 3.70), "m46": (4.40, 4.80)}

# star / galaxy from the member dots' shapes (dots extras: a_arcsec, b_arcsec, psf_chi2_nu, flux_psf_ujy,
# flux_ap<r>_ujy). Each is divided by its detector's stellar locus (median over bright dots, of Gaia stars
# with a parallax when there are enough), then the median over the source's dots.
CLASSIFY_SHAPE_MIN_SNR = 8.0       # dots below this S/N don't vote on the shape
CLASSIFY_LOCUS_MIN_SNR = 20.0      # dots at least this bright define the stellar locus per detector
CLASSIFY_LOCUS_MIN_DOTS = 30       # Gaia-star dots needed to use them for the locus (else all bright dots)
CLASSIFY_SIZE_TOL = 0.10           # extended score: (size ratio − 1) / this
CLASSIFY_COG_TOL = 0.10            # ... (curve-of-growth ratio − 1) / this
CLASSIFY_CHI2_FACTOR = 2.0         # ... log(psf chi2 ratio) / log(this)
CLASSIFY_EXT_STAR = 1.0            # extended score <= this: point-like
CLASSIFY_EXT_GALAXY = 2.0          # extended score >= this (and no significant parallax): galaxy
CLASSIFY_PLX_SNR_STAR = 5.0        # Gaia parallax / error >= this: star, whatever the shape
CLASSIFY_GAIA_MATCH_ARCSEC = 3.0   # own Gaia match from refcat when crossids.parquet has none

# brown-dwarf-like: (methane or water) and red and (no Gaia or faint in Gaia) and not a galaxy, not saturated
BD_CH4_33_MIN_DEPTH = 0.25
BD_CH4_16_MIN_DEPTH = 0.15
BD_H2O_MIN_DEPTH = 0.15
BD_MIN_J_M46_AB = -0.5         # J_AB − [4.6]_AB above this is "red" (≈ J − W2 > 1.9 Vega; A0 star −2.4, M dwarfs ~ −1.4)
BD_GAIA_FAINT_G = 19.0         # Gaia G fainter than this counts as faint
BD_MIN_G_J_AB = 3.5            # ... or G − J_AB above this (L/T dwarfs are very red in G − J)

# --- photoz: template photometric redshifts (photoz.py) -------------------------------------
PHOTOZ_TEMPLATES = "eazy_fsps_qsf12_v3_rest_0p3_5p2um.csv"  # pipeline/data/, see its README
PHOTOZ_Z_MIN, PHOTOZ_Z_MAX, PHOTOZ_Z_STEP = 0.0, 1.0, 0.005
PHOTOZ_TEMPLATE_ERR = 0.05     # fractional model error added in quadrature to each channel's error
PHOTOZ_CLASSES = ("galaxy", "unsure")  # classes fitted (all sources when classes.parquet is missing)
PHOTOZ_MIN_CHANNELS = 20       # fewer measured channels: not fitted
PHOTOZ_ODDS_DZ = 0.05          # odds = p(|z − z_best| < this × (1 + z_best))
PHOTOZ_GOOD_ODDS = 0.8         # good = odds >= this, chi2_nu <= PHOTOZ_GOOD_CHI2_NU, n_channels >= PHOTOZ_GOOD_MIN_CHANNELS
PHOTOZ_GOOD_CHI2_NU = 3.0
PHOTOZ_GOOD_MIN_CHANNELS = 40
PHOTOZ_CHUNK = 4000            # sources per matrix product
PHOTOZ_SDSS_MATCH_ARCSEC = 3.0
PHOTOZ_OUTLIER = 0.15          # |Δz| / (1 + z_spec) above this is an outlier
PHOTOZ_BRIGHT_AB = 17.0        # report also for galaxies brighter than this (K-window AB)

# --- dots extras: zodi, ePSF photometry, shape, photo metadata (contracts/dots.md) --------------
DOTS_READ_ZODI = True          # read the ZODI HDU (16.6 MB more per photo) for zodi_mjysr and the photo's zodi median
DOTS_READ_EPSF = True          # read the EPSF table (3.9 MB; once per worker per ePSF calibration file) for flux_psf_*
PSF_FIT_HALF_PX = 3            # ePSF fit on a 7 × 7 stamp around the dot
PSF_FIT_MIN_PIX = 12           # fewer unmasked pixels than this: flux_psf_* NaN
PSF_MODEL_FRAC = 0.03          # ePSF shape error added in quadrature to the pixel noise (fraction of the model)
PSF_NOISE = "detect"           # pixel noise of the fit: "detect" = max(√VARIANCE, background rms) as in core.detect;
                               # "variance" = √VARIANCE only (chi2 then grows with confusion in crowded fields)
# ePSF position fit (psf.psf_position) -> x_psf, y_psf, ra_psf, dec_psf, pos_err_psf_arcsec, psf_pos_ok (dots v4).
# Off: those columns still exist and carry the windowed centroid with psf_pos_ok False.
DOTS_PSF_POSITION = True
PSF_POS_MIN_SNR = 5.0          # dots below this aperture S/N keep the windowed centroid (psf_pos_ok False)
PSF_POS_MAX_ITER = 10          # Gauss–Newton steps at most; not converged by then -> psf_pos_ok False
PSF_POS_MAX_STEP_PX = 0.3      # one step moves (x, y) at most this far
PSF_POS_MAX_SHIFT_PX = 1.0     # a fit that ends further than this from the windowed centroid fails
PSF_POS_TOL_PX = 0.002         # converged when a step is shorter than this ...
PSF_POS_TOL_SIGMA = 0.05       # ... or than this × the fit's own 1σ (faint dots); reversing steps are halved
PSF_POS_ERR_FLOOR_ARCSEC = 0.13 # added in quadrature to the fit's 1σ: pulls vs Gaia ≈ 1 at all S/N on 6 pilot photos (14 Nov)
PHOTO_META_KEY = "skyshift_photo"  # Parquet key-value metadata of each dot file (JSON)
PHOTO_META_HEADER_KEYS = (     # IMAGE header values copied into it as they are (missing keys -> null)
    "MJD-BEG", "MJD-AVG", "MJD-END", "XPOSURE", "TELAPSE",
    "X_SC_START", "Y_SC_START", "Z_SC_START", "VX_SC_START", "VY_SC_START", "VZ_SC_START",
    "X_SC", "Y_SC", "Z_SC", "VX_SC", "VY_SC", "VZ_SC",
    "X_SC_END", "Y_SC_END", "Z_SC_END", "VX_SC_END", "VY_SC_END", "VZ_SC_END",
    "FINAST", "MATCH_NSTARS", "ASTRONETSOLVED", "OMEGA_MEDIAN", "OMEGA_STDDEV", "PSF_FWHM",
    "L1DQAFLG", "L2DQAFLG", "HE_AG_AMPL", "AURORA_CORR", "SHUTTLE_GLOW_CORR",
    "NBLMSRCS", "NSNOWSRC", "L2 SATSTRKS", "SPS_ELON", "SPS_ELAT", "SPS_EPA", "SGT_MIN_GCD2SAA",
)
PHOTO_META_HEADER_PREFIXES = ("L2 N_",)  # every flag count key, e.g. 'L2 N_OVERFLOW'
AP_RADII_PX = (1.0, 1.5, 2.0, 3.0, 4.5)  # flux_ap_ujy / flux_ap_err_ujy, one value per radius
AP_BKG_ANNULUS_PX = (5.5, 8.0)  # local background annulus of those apertures (outside 4.5 px)
FORCED_EDGE_PX = 3.0            # forced targets at least this far inside the frame
FORCED_ZONE_MARGIN_DEG = 0.05   # ... and inside the zone box plus this margin
FORCED_WARN_TARGETS = 30_000    # log a warning above this many forced targets in one photo (no cut)
CALIB_ASTROM_MIN_SNR = 20.0     # photo metadata: dots used for the offset against forced Gaia targets
CALIB_ASTROM_MATCH_ARCSEC = 2.0
CALIB_PSF_MIN_SNR = 50.0        # photo metadata: unsaturated dots used for the PSF size / ellipticity
PHOTO_META_GRID = 32            # photo metadata: 32 × 32 block means of the background and ZODI maps

# --- refcat (Gaia DR3, CatWISE2020, 2MASS PSC) -----------------------------------------------
REFCAT_MARGIN_DEG = 0.1        # around the zone box
REFCAT_TILE_DEG = 1.0          # one async TAP query per catalogue per tile; each tile cached
REFCAT_WORKERS = 8             # queries in flight
REFCAT_MAXREC = 2_000_000      # a tile that returns this many rows is an error (truncated)
REFCAT_GAIA_G_MAX = 20.0
# Catalogues refcat fetches (env SKYSHIFT_REFCAT_CATALOGS, comma list). "gaia" alone: only the forced list
# refcat/<zone>_forced.parquet is written (Gaia part), so extraction can start fast; a later normal
# refcat run sees the full refcat missing, reuses the cached Gaia tiles and adds CatWISE + 2MASS.
REFCAT_CATALOGS = tuple(c.strip() for c in os.environ.get("SKYSHIFT_REFCAT_CATALOGS", "gaia,catwise,2mass").split(",") if c.strip())
# Gaia DR3 synthetic photometry (GSPC z, y: calib's D1 anchors) joins the ESA Gaia query since 15 Nov. A full (not
# partial) refcat run over a zone whose Gaia tiles were cached without it refetches those tiles only, best effort.
REFCAT_GSPC_UPGRADE = os.environ.get("SKYSHIFT_REFCAT_GSPC_UPGRADE", "1") != "0"
# forced-photometry target list (refcat/<zone>_forced.parquet), read by `dots`
# Asked for G < 19 and W1 < 16: that gave 5,400 targets/deg² (66,000 per photo) in the pilot (3,870 Gaia,
# 1,540 CatWISE-only, mostly galaxies), over the 30,000 budget. Tightened (14 Nov) to G < 17.5 (2,110/deg²)
# and CatWISE-only W1 < 16 with W1 - W2 >= 0.4 (the red ones: L/T dwarfs, cold objects).
FORCED_GAIA_G_MAX = 17.5
FORCED_CATWISE_W1_MAX = 16.0   # CatWISE-only (no Gaia within FORCED_DEDUP_ARCSEC): red/cold objects
FORCED_CATWISE_W1_W2_MIN = 0.4
FORCED_DEDUP_ARCSEC = 2.0
FORCED_MAX_PER_PHOTO = 30_000  # in a 3.5° x 3.5° photo-sized box inside the zone
PHOTO_SIDE_DEG = 3.5

# --- calib: pixel-phase correction and position errors (calib.py) ------------------------------
# Learned per zone from member dots of sources matched to one Gaia star; written to
# results/<zone>/calib.json and applied wherever the combining steps load dots (motion_io, spectra).
CALIB_REL = "calib.json"        # under results/<zone>/
CALIB_FOURIER_K = 3             # Δ(φ) = Σ_k s_k sin(2πkφ) + c_k cos(2πkφ), k = 1..K, per detector and axis
CALIB_PHASE_MIN_SNR = 30.0      # dots used for the phase fit
CALIB_ERR_MIN_SNR = 5.0         # dots used for the pos_err fit (all kept dots)
CALIB_SAT_BITS = 0x00008002     # OVERFLOW | NONLINEAR at the peak: saturated, no phase correction
CALIB_MAX_PARALLAX_MAS = 10.0   # Gaia stars nearer than this are left out (parallax not modelled here)
CALIB_FRAME_MIN_SNR = 20.0      # each photo's median offset against Gaia from dots this bright ...
CALIB_FRAME_MIN_STARS = 10      # ... at least this many, else the photo's dots are left out of the fit
CALIB_CLIP_SIGMA = 4.0          # phase fit: one refit without dots beyond this many robust sigma
CALIB_MIN_STARS = 200           # dots needed to fit a detector (phase or pos_err); else left raw
CALIB_JAC_MIN_DOTS = 50         # photo Jacobian (arcsec per pixel): affine fit of its dots' (ra, dec) on (x, y)
CALIB_JAC_MIN_SPREAD_PX = 100.0 # ... the dots must spread at least this far (std) in x and in y
CALIB_ERR_SNR_BINS = (5.0, 7.0, 10.0, 14.0, 20.0, 30.0, 50.0, 100.0, 200.0, 500.0, 1e9)
CALIB_PHASE_BINS = 10           # report: median residual in this many phase bins from -0.5 to 0.5
CALIB_CHECK_SNR_BINS = (10.0, 30.0, 60.0, 150.0, 1e9)  # report: phase amplitude by S/N (is one table enough?)
# per-photo distortion (after the phase correction): polynomial in u = (x − 1020)/1020, v = (y − 1020)/1020 of the
# residual against Gaia, per photo; the order is chosen per zone by cross-validation (stars split in two halves)
CALIB_PHOTO_MAX_ORDER = 2       # 0 = offset only, 1 = affine, 2 = quadratic
CALIB_PHOTO_MIN_GAIN = 0.01     # a higher order is used only if it lowers the held-out scatter by > 1%
CALIB_PHOTO_MIN_STARS = (10, 30, 60)  # stars needed per photo for order 0, 1, 2 (fewer: the next lower order)
CALIB_FIT_SIGMA = (5.0, 0.15)   # ″ (a, floor): weights of the distortion fit, σ = √((a/snr)² + floor²), windowed centroid
# ePSF positions (dots v4, psf_pos_ok): no phase term; their own per-photo distortion and pos_err, fitted the same way.
# Weights as measured on 36 pilot photos (14 Nov): about half the centroid's scatter at every S/N.
CALIB_PSF_FIT_SIGMA = (2.5, 0.10)
# With calib.json, a dot takes its ePSF position only where the ePSF model's calibrated pos_err is below the corrected
# centroid's: ePSF a ≈ 2.9–3.9″ vs 4.4–6.0″, but floor 0.13–0.20″ vs 0.08–0.21″ (36 pilot photos), so the
# centroid wins above S/N ≈ 30–50 in D1, D2, D4–D6. False: every psf_pos_ok dot takes its ePSF position.
CALIB_PSF_CHOOSE_BY_ERROR = True
CALIB_YMAP_BIN_PX = 64          # detector y-map: fixed Δy(y) per detector (the wavelength axis), median per bin ...
CALIB_YMAP_MIN_DOTS = 30        # ... from at least this many dots (else 0)
CALIB_YMAP_MIN_RMS_PX = 0.008   # applied only where its rms beats this (D3 pilot ~0.035 px, D2 ~0.01, others ≲ 0.004)
CALIB_REL_MIN_DOTS = 3          # pos_err floor_rel (motion): stars with at least this many dots, residuals about their mean
# flux pixel phase: ln(flux / the source's smooth spectrum) = Σ_k a_k cos(2πkφx) + b_k cos(2πkφy) + c cos(2πφx)cos(2πφy)
CALIB_FLUX_K = 2
CALIB_FLUX_MIN_SNR = 50.0       # dots used for the flux phase fit
CALIB_FLUX_REF_MIN_CHANNELS = 8 # the source's spectrum needs this many channels in the dot's detector
# aperture / detector scale: one factor per detector so r = 2 px fluxes become total fluxes, consistent across detectors.
# Anchors: catalogue SED (log-log between bands) at dots within CALIB_AP_WINDOW_UM of a band centre; stitching:
# the source spectra on either side of each detector boundary extrapolated to it. Weighted least squares.
CALIB_AP_BANDS = {"z": (0.8932, "gaia_z_ab", 0.0), "y": (0.9633, "gaia_y_ab", 0.0),
                  "J": (1.235, "j", 0.91), "H": (1.662, "h", 1.39), "Ks": (2.159, "k", 1.85),
                  "W1": (3.368, "w1", 2.699), "W2": (4.618, "w2", 3.339)}  # µm (pivot), crossids column, AB − Vega
CALIB_AP_OPTIONAL_BANDS = ("z", "y")  # Gaia GSPC (G < 17.65, ESA refcat only): used where present, else skipped
CALIB_AP_WINDOW_UM = 0.1
CALIB_AP_WINDOW_BAND_UM = {"z": 0.035, "y": 0.035}  # narrower: z and y sit 0.07 µm apart, no long interpolation
CALIB_AP_MIN_SNR = 30.0
CALIB_AP_STAR_JK = (0.25, 0.75)  # 2MASS J − Ks (Vega): F–K stars, smooth spectra
CALIB_AP_STAR_W12_MAX = 0.1      # |W1 − W2| (Vega): no excess, no strong CO
CALIB_AP_STAR_J = (9.0, 14.5)    # 2MASS J range (good photometry, not saturated in 2MASS)
CALIB_AP_STAR_W1_MIN = 8.5       # CatWISE W1 brighter than this saturates
CALIB_AP_MIN_DOTS = 50           # per (band, detector) anchor
CALIB_AP_ANCHOR_SIGMA = 0.02     # systematic per anchor (bandpass and stellar colour terms), ln units
CALIB_AP_STITCH_SIGMA = 0.01     # per boundary
CALIB_AP_STITCH_CHANNELS = 4     # channels on each side of a boundary
# Stitch a boundary only if one side has no catalogue anchor. Pilot: the 1.1 µm stitch put D1 at 0.951 while Gaia GSPC
# z and y (two bands, 0.89 and 0.96 µm) both give 1.024; D1 and D2 slope ~4–5% inside the detector, so edges mislead.
CALIB_AP_STITCH_ANCHORED = False
CALIB_FORCED_MAX_PHOTOS = 30    # forced photometry's own PSF level (calib.fit_forced): photos read per detector (~2,000 pairs each)
CALIB_AP_PRIOR_SIGMA = 0.1       # weak prior ln factor = 0 (keeps a detector without constraints at 1)

# --- pilot fixes (14 Nov): light-curve error model, band labels, fast-mover vetting ----------
# lightcurves: per (detector, origin) σ_cal² = (k σ)² + (floor · model)², fitted on the Gaia-matched light curves
# (lightcurves.py docstring). Pilot: the quoted errors gave median chi2_nu 1.96 (dots) / 1.28 (forced).
LC_ERR_MODEL = True            # False: k = 1, floor = 0 (the quoted errors)
LC_ERR_MIN_GROUPS = 30         # light curves needed per (detector, origin): Gaia-matched first, else all, else no model
LC_ERR_SNR_BINS = 10           # S/N quantile bins for the shape (k vs floor)
LC_ERR_MIN_BIN_POINTS = 200    # points needed in a bin
LC_ERR_K_GRID = (0.5, 3.0, 0.01)       # grid (lo, hi, step) for k ...
LC_ERR_FLOOR_GRID = (0.0, 0.15, 0.001)  # ... and for the fractional floor, before the median-chi2 rescale
LC_PHOTO_MAX_MEDIAN_CHI = 2.0  # a photo whose Gaia-matched points sit this far off as a group (median σ_cal) ...
LC_PHOTO_MIN_REF = 10          # ... over at least this many of them is left out of chi2_nu / scatter (photo_ok false)
LC_VARIABLE_DROP_WORST = 1     # `variable` tests chi2_nu without each light curve's worst point(s): one hit isn't variability
# classify: band labels the web shows instead of raw <index>_sig (classify.band_labels). Pilot (INDEX_SIG = 5, sig alone):
# water ice fired on 372 sources (206 of 230 Gaia ones with BP − RP > 2: M dwarfs' own water vapour; 140 no-Gaia, mostly
# galaxies' 1.6 µm bump and PAH), methane on 50 (15 Gaia stars: impossible). Each label = sig >= INDEX_SIG, depth >= the
# minimum below, not saturated, plus the physical guards.
LABEL_SUPPORT_SIG = 3.0        # "significant" for a supporting or vetoing band
LABEL_MIN_DEPTH = {            # index depth (or PAH excess) needed for a label
    "h2o_ice_305": 0.10, "co2_ice_427": 0.10, "co_ice_467": 0.10,
    "ch4_33": 0.25, "ch4_16": 0.15,      # = BD_CH4_*_MIN_DEPTH: T dwarfs have deep methane
    "h2o_14": 0.05, "h2o_19": 0.05, "co_23": 0.05, "pah_33": 0.10,
}
LABEL_ICE_MAX_BP_RP = 2.0      # ice: Gaia stars redder than this are M dwarfs (water vapour at 2.7–3.2 µm), not ice
LABEL_ICE_MAX_EXT = 1.0        # ice: point-like (extended_score <= this; NaN passes): a star behind a cloud
LABEL_ICE_MIN_J_M46 = 0.0      # ice: reddened by the cloud (J − [4.6] AB >= this; bare photospheres −1.4 to −2.4, M dwarfs −1.3)
LABEL_CH4_MIN_G = 19.5         # methane: no Gaia counterpart, or fainter than this
# highpm vetting: every candidate is kept; `vetted` false and `reason` (comma list) say why it was cut. Pilot: 241
# candidates, the 10 "new" all artefacts (9 fits at 5,000–11,000 ± 900–2,200 mas/yr on 6–13 dots, sitting on a slow
# CatWISE source; 1 pair at 171″/yr), and pairs at the control level (12 real vs 13 chance).
HIGHPM_FIT_MIN_VISITS = 2      # fit: proper motion needs two visits ...
HIGHPM_FIT_MIN_SPAN_YR = 0.25  # ... this far apart
HIGHPM_FIT_MIN_DOTS = 20       # fit: fewer dots is a barely-detected source (pilot artefacts had 6–13; median source 120)
HIGHPM_FIT_MAX_CHI2_NU = 5.0   # fit: astrometric chi2_nu above this is a blend or a bad fit
HIGHPM_FIT_MAX_PM_MAS_YR = 10_500.0  # fit: faster than Barnard's Star (10.4″/yr, the fastest known star) is not a star
HIGHPM_PM_AGREE_SIGMA = 3.0    # a Gaia / CatWISE counterpart's own pm disagrees when |Δpm| > max(this × pm_err,
                               # HIGHPM_CATWISE_PM_TOL × pm, 100 mas/yr): the counterpart is a slow star, ours is an artefact
HIGHPM_PAIR_SEP_BINS = (3.0, 10.0, 30.0, 60.0, 120.0)  # pairs are tested against the control per separation bin (″)
HIGHPM_PAIR_MAX_P = 0.01       # a bin's pairs pass when P(>= n real | Poisson, control mean) < this
HIGHPM_CONTROL_OFFSETS = ((900.0, 0.0), (-900.0, 0.0), (0.0, 900.0), (0.0, -900.0))  # ″ east, north: 4 controls

# --- forced-photometry sky list (forced_sky.py; contracts/dots.md, forced) ------------------------
# One target list for the whole sky, HEALPix-partitioned (nested, nside FORCED_SKY_NSIDE: 12,288 partitions of
# 3.36 deg²), so a photo reads only the ~9–12 partitions its footprint overlaps and gets every target in it,
# whatever zone it was extracted under. Built by `python -m skyshift_pipeline.forced_sky` (resumable); dots uses it
# only when FORCED_SKY_DIR/FORCED_SKY_MANIFEST exists (written once every partition is done), else falls back to
# refcat/<zone>_forced.parquet exactly as before. SKYSHIFT_FORCED_SKY=off forces the zone list.
# Gaia DR3 G < FORCED_GAIA_G_MAX is 221 M targets whole sky (Gaia random_index sample, 14 Nov); 45% of the sky is
# denser than the 30,000-per-photo budget (2,449/deg²), up to ~250,000/deg² near the galactic centre (2.8 M per photo).
# So each partition keeps at most its brightest FORCED_SKY_MAX_PER_DEG2 × area targets (a per-partition G limit,
# recorded in the manifest): 71 M targets. None = no cap (221 M targets; plane photos up to ~2.8 M targets each).
FORCED_SKY_MODE = os.environ.get("SKYSHIFT_FORCED_SKY", "auto")   # "auto" (use it if complete) or "off"
FORCED_SKY_NSIDE = 32
FORCED_SKY_DIR = "refcat/sky/forced_n32"           # p<pixel:05d>.parquet, FORCED_COLUMNS of refcat.py
FORCED_SKY_MANIFEST = "_complete.json"             # written last: the list is complete
FORCED_SKY_MAX_PER_DEG2: float | None = FORCED_MAX_PER_PHOTO / PHOTO_SIDE_DEG ** 2   # 2,449/deg² -> 8,222 per partition
FORCED_SKY_WORKERS = 16                            # ESA TAP sync queries in flight (16 measured fine, 14 Nov)
FORCED_SKY_TIMEOUT_S = 300                         # per TAP request (the densest partition took 53 s uncapped, 4 s capped)
FORCED_SKY_PAD_DEG = 0.05                          # extra radius when picking a photo's partitions
FORCED_SKY_CACHE_PARTITIONS = 16                   # partitions kept in memory per dots worker (a photo reads ~10-14)

# --- final pass (15 Nov): zones with a Gaia-only refcat (SKYSHIFT_REFCAT_CATALOGS=gaia) -----------
# suspects: the WISE veto. refcat/<zone>.parquet without CatWISE rows (Gaia-only) -> AllWISE from IRSA's HEALPix
# Parquet on AWS S3 (anonymous): only the order-5 partitions (nside 32, nested, 3.36 deg²) the remaining movers' 6″
# circles touch, columns ra and dec only (each file is ONE row group, so row-group predicate pushdown skips nothing:
# the saving is the partition choice and the column projection, ~1 MB of a ~22 MB file). CatWISE2020 is not on S3
# (s3://nasa-irsa-wise/ has wise/{allsky,allwise,cryo-3band,neowiser,postcryo} and unwise/neo7, 14 Nov).
# AllWISE positions are the non-moving fit at ~2010.5 (MJD ~55400), no proper motion applied: a source moving
# > ~400 mas/yr has left the 6″ circle by 2025 (Gaia, moved by its own pm, still vetoes those down to G < 20).
# "auto": AllWISE when the refcat has no CatWISE rows; "always": also instead of the TAP services when the refcat is
# missing; "off": never (a Gaia-only refcat then leaves catwise_checked false).
SUSPECTS_ALLWISE = os.environ.get("SKYSHIFT_SUSPECTS_ALLWISE", "auto")
ALLWISE_S3_BUCKET = "nasa-irsa-wise"
ALLWISE_S3_REGION = "us-west-2"
ALLWISE_S3_PREFIX = "wise/allwise/catalogs/p3as_psd/healpix_k5/wise-allwise.parquet"  # healpix_k0=<p//1024>/healpix_k5=<p>/part0.snappy.parquet
ALLWISE_NSIDE = 32             # partition order k = 5, nested
ALLWISE_ARCSEC = CATWISE_ARCSEC  # same veto radius as CatWISE
ALLWISE_EPOCH_MJD = 55400.0    # ~2010.5: documentation only, positions are used as they are
ALLWISE_COLUMNS = ("ra", "dec")
ALLWISE_WORKERS = 8            # partitions read in parallel
ALLWISE_TIMEOUT_S = 120.0      # per S3 request
ALLWISE_CACHE_DIR = "cache/allwise/k5"  # p<pixel:05d>.parquet (ra, dec) at the store root: neighbouring zones and reruns reuse it
ALLWISE_CACHE = os.environ.get("SKYSHIFT_ALLWISE_CACHE", "1") != "0"

# calib: instrument-level terms borrowed from a reference zone's calib.json when the zone can't fit them itself.
# SKYSHIFT_CALIB_BORROW: "auto" = borrow the total-flux scale (aperture factor_ap) when the zone's own catalogue anchors
# constrain fewer than CALIB_BORROW_MIN_ANCHOR_DETS detectors (a Gaia-only refcat has none), and any per-detector table
# (pixel phase, y-map, flux phase, forced) the zone could not fit; "always" = borrow factor_ap even with anchors (a check);
# "off" = never. Per-photo distortion, pos_err, the ePSF section and ap_over_psf stay the zone's own (Gaia is enough).
# calib.json `borrowed` records what came from where.
CALIB_BORROW = os.environ.get("SKYSHIFT_CALIB_BORROW", "auto")
CALIB_REF_ZONE = os.environ.get("SKYSHIFT_CALIB_REF", "hunt")   # results/<ref>/calib.json in the same root; "" = none
CALIB_BORROW_MIN_ANCHOR_DETS = 3

# --- tiles: the combining steps per sky tile, in parallel processes (tiles.py) -------------------------
# `all` cuts zones larger than TILE_AUTO_MIN_DEG2 into tiles (CLI --tiles / --tile-deg override). A tile is a core box
# (the cores partition the zone box) plus a border of TILE_MARGIN_ARCSEC; each tile runs link, spectra, crossid (pass 1),
# then, after the zone-wide calib, spectra, classify, photoz, colours, lightcurves, astrometry (pass 2) on its core + border,
# and keeps only what its core owns: a fixed source by link's position (fixed.ra/dec), a mover by its position, a forced
# target by its catalogue position. Zone-wide numbers are fitted once on the merged data and given to every tile: calib,
# the classify stellar locus, the light-curve error model and bad photos, the astrometry frame offsets; highpm, score and
# suspects run once on the merged tables. source_id is already sky-wide (6″ cell × 100 + rank in the cell), so tiles
# need no renumbering.
TILE_AUTO_MIN_DEG2 = 30.0      # `all` tiles zones larger than this (pilot 12.6 deg² stays untiled; hunt 100 deg² tiles)
# Grid: nx × ny equal cores, chosen to minimise rounds × cost per tile, rounds = ceil(tiles / workers) (each pass waits
# for its slowest round) and cost ∝ (side_x + PAD)(side_y + PAD) (the tile's own dots, plus the photos it overlaps: a
# photo's box is ~4.5°, so small tiles read the same photos many times). Hunt on 16 workers: 4 × 4 tiles of 2.5°.
TILE_MAX_DEG = 2.5             # largest core side (memory: ~2.2 M in-zone dots at hunt density)
TILE_COST_PAD_DEG = 2.0
# Border: link's 4″ friends-of-friends chains reach 30–60″ from the source at full depth (pilot members: 19 of 175k
# sampled dots at 30–60″, none further), mover pairs reach 16″ (smoke) and their 6″ veto needs complete fixed sources;
# 2′ keeps every group the core touches complete. A tile whose border was too small is detected and redone (below).
TILE_MARGIN_ARCSEC = 120.0
TILE_EDGE_TOUCH_ARCSEC = FIXED_LINK_ARCSEC + 2 * FIXED_SNAP_ARCSEC  # a group with a dot this close to the border's
                               # outer edge may be cut there ...
TILE_EDGE_CHECK_ARCSEC = 30.0  # ... which matters when it also has a dot this close to the core (owned, or the veto of an
                               # owned mover: 6″ + mover reach): then the tile is redone with a doubled border
TILE_MAX_RETRIES = 2
TILE_ZONE_EDGE_MARGIN = True   # tiles on the zone's edge load the border outside the zone box too (dots of the zone's
                               # photos there): zone edges become tile edges, no stars cut at the zone edge, no fake movers
TILE_SLIM_DOTS = True          # mirror the zone's dot files into the work dir without the columns below (same rows, so
TILE_SLIM_DROP_COLUMNS = ("flux_ap_ujy", "flux_ap_err_ujy")  # dot_row holds); no combining step reads them, and their
                               # list decoding is 3/4 of a full read (18.8 → 4.4 ms per photo); a tile reads each photo
                               # it overlaps, about (1 + 4.5° / TILE_DEG)² tiles per photo
TILE_CALIB_MIN_SNR = 30.0      # calib inputs gathered from the tiles: every dot of a Gaia-matched source, plus other
                               # dots at least this bright (calib's flux terms use S/N >= CALIB_AP_MIN_SNR, CALIB_FLUX_MIN_SNR)
TILE_THREADS = 4               # file-read threads inside each tile process
TILE_DISK_RESERVE_GB = 3.0     # the slim mirror is made only when the work dir's disk keeps this much free after it (estimated
                               # from a sample of the files, plus as much again for the tiles' own files); otherwise the tiles
                               # read the zone's dot and forced files from the root (slower, same results). Hunt: ~6 GB of
                               # slim dots + 1.9 GB forced on a 30 GB boot disk

# --- qa: a grade per photo (qa.py; results/<zone>/qa_photos.parquet, qa.json) ------------------------------------
# NASA's header QA passes every photo (FINAST 0, L1/L2DQAFLG "Pass" on the bad ones too), so qa grades each photo against
# the zone's other photos of its detector, from the dot files' footers (contracts/dots.md photo metadata), calib's
# per-photo scatter and, where the dot files are local, 5 dot columns. A reason fires at amber or red; the grade is the
# worst. Numbers measured on 14 Nov (hunt calib_photos; 1,209 ecl15 photos' footers; 216 photos' bright dots).
QA_FOOTER_TAIL_BYTES = 72 * 1024  # one ranged read per dot file (footers ~65 KB with the grids); a longer footer: a second
QA_THREADS = 32                # footer / dot-column reads in parallel
QA_SHAPES = os.environ.get("SKYSHIFT_QA_SHAPES", "auto")  # dot columns for the sharp-artefact share: auto = only when the
                               # dot files are on local disk (tiled run's slim mirror, local root), on, off
QA_MIN_PHOTOS = 5              # a detector (or detector-and-visit) median needs this many photos, else the defaults below
QA_MIN_BRIGHT = 20             # bright dots (unsaturated, S/N > CALIB_PSF_MIN_SNR) a photo needs for its PSF numbers
QA_FWHM_REF_ARCSEC = {1: 7.8, 2: 7.8, 3: 8.0, 4: 9.1, 5: 9.1, 6: 9.5}  # stellar FWHM from second moments (ecl15 medians;
                               # used only when the zone has too few photos of the detector)
QA_PSF_AP_REF = 1.035          # median PSF / r = 2 px aperture flux of stars (ePSF aperture correction 1.033)
# dots per photo / median of the same detector and visit (ecl15: p1 0.89, p99 1.18; SAA: D6 3.5× at 1.2°, D5 1.6–1.9×
# within 2°, D5 1.45× at 70°). Red needs less near the SAA, where the extra dots are particle hits.
QA_DOTS_RATIO_RED = 2.0
QA_DOTS_RATIO_RED_SAA = 1.5
QA_DOTS_RATIO_AMBER = 1.35
QA_DOTS_RATIO_LOW = 0.5        # amber below (part of the photo empty)
QA_SAA_DEG = 2.0               # header SGT_MIN_GCD2SAA below this: amber, and the lower red threshold above
QA_SATSTRKS_AMBER = 15         # header `L2 SATSTRKS` (ecl15: median 1, p99 11, 34 on the SAA photo)
# PSF of bright dots / the detector's median (ecl15 FWHM: p1 0.96, p99 1.04; PSF/aperture: p1 0.88, p99 1.03).
# Visit-3 D6 artefact photos: FWHM 0.40–0.49, PSF/aperture 0.49–0.57 (more than half the bright dots are artefacts,
# so the medians flip); 0.66–0.76 where it's nearly half.
QA_FWHM_RATIO_RED = (0.7, 1.4)
QA_FWHM_RATIO_AMBER = (0.9, 1.15)
QA_PSF_AP_RATIO_RED = (0.8, 1.25)
QA_PSF_AP_RATIO_AMBER = (0.9, 1.1)
# Sharp artefacts (dot columns): share of bright dots with FWHM < QA_SHARP_FWHM_FRAC × the detector's stellar FWHM.
# 216 sampled photos: D1–D3 ≤ 0.4%; MWIR grows with time: D4 0.2% → 1% → 7%, D5 0% → 3% → 7%, D6 1% → 24% → 44%
# (visits 1 → 3, Sep 2025 → May 2026), 73% of the sharp ones with OUTLIER at the peak. Red: the photo's PSF medians flip
# (≥ half); amber: the visit-3 D6 level. The share is a photo number: the sharp dots themselves are the artefacts.
QA_SHARP_FWHM_FRAC = 0.7
QA_SHARP_FRAC_RED = 0.5
QA_SHARP_FRAC_AMBER = 0.3
QA_SHARP_OUTLIER_MIN = 0.3     # red also needs this share of the sharp dots with OUTLIER at the peak (OUTLIER alone is
                               # never a reason: MPC asteroids carry it on 52% of their dots)
# Astrometry against Gaia. calib's per-photo scatter after the distortion fit / the detector's median (hunt: 0.99″ = 5.6×
# on D4 and 0.74″ = 5.3× on D6, the next 2.9×; D3's median is 0.26″, the others' 0.14–0.18″), the worse of centroid and
# ePSF; without a calib row (≥ QA_MIN_GAIA stars) the dot file's raw scatter against forced Gaia targets. Red also needs
# QA_SCATTER_MIN_RED_ARCSEC. Offset: the dot file's raw median offset minus the detector's median offset.
QA_MIN_GAIA = 20               # fewer matches: not checked (`astrometry_checked` false; hunt calib: 6% of photos, zone edges)
QA_SCATTER_RATIO_RED = 3.5
QA_SCATTER_MIN_RED_ARCSEC = 0.5
QA_SCATTER_RATIO_AMBER = 2.0
QA_OFFSET_RED_ARCSEC = 1.5     # ecl15 raw offsets: median 0.11″, p99 0.39″, max 1.09″ (D6); calib removes offsets anyway
QA_OFFSET_AMBER_ARCSEC = 0.6
QA_LIGHTCURVES = True          # lightcurves: red photos' points get photo_ok false (as its own bad photos)
QA_REPORT_WORST = 30           # qa.json lists this many of the worst photos

# --- orphans: the distant-planet route (orphans.py) ---------------------------------------------------------------
# A planet at 70–1000 AU drifts 0.5–0.03″/h inside a visit (Earth's reflex), so link's 4″ FoF chains it into a fixed
# source seen in ONE visit; six months later it sits 4,800–390″ away along ecliptic longitude (Earth's 2 AU parallax
# baseline, less its own orbital motion). Orphans: single-visit sources without a catalogue counterpart whose spot the
# other visit covered. Pairs across visits are tested against a bound-orbit model; controls give the chance rate.
ORPHANS_MIN_COVER_PHOTOS = 6       # the other visit covered the position with at least this many photos (absence is real)
ORPHANS_BRIGHT_SNR = 100.0         # bright-star fragment: another source this bright (max dot S/N) ...
ORPHANS_BRIGHT_ARCSEC = 15.0       # ... within this distance
ORPHANS_EDGE_ARCSEC = 180.0        # zone edge: closer than this to the zone box's edge (inside or out) is dropped
ORPHANS_MEMBER_MAX_ARCSEC = 60.0   # a member dot further than this from its source is stale (dot file rewritten): dropped
ORPHANS_POS_ERR_SCALE = 1.6        # dot pos_err is 1.4–1.8× too small against Gaia (raw dots; calib is not applied here)
ORPHANS_POS_FLOOR_ARCSEC = 0.3     # added in quadrature per dot (pixel phase, frame offsets, SPHEREx's own parallax)
ORPHANS_MIN_DOTS = 3               # dots needed for the drift fit (4 parameters, 2 numbers per dot) ...
ORPHANS_MIN_SPAN_H = 6.0           # ... spanning at least this long
ORPHANS_CLIP_SIGMA = 5.0           # one refit without dots beyond this many (χ²-inflated) sigma
ORPHANS_RATE_MIN = 0.0             # ″/h. Not 0.02: at SPHEREx's ~90° elongation Earth's velocity runs along the line of sight,
                                   # so a 700–1000 AU planet can drift only 0.002–0.03″/h in a visit; with 0.02 the table-level
                                   # injection (hunt cadence, 20 dots per visit) kept 17% / 5% of them. The pair model tests the
                                   # drift against the distance instead.
ORPHANS_RATE_MAX = 1.0             # ″/h: ~50 AU (faster movers don't chain into a fixed source)
ORPHANS_DRIFT_NSIGMA = 3.0         # drift_ok: the rate is within this many sigma of [RATE_MIN, RATE_MAX] ...
ORPHANS_DRIFT_MAX_ERR = 0.1        # ... and measured to better than this (″/h; otherwise anything is "consistent")
ORPHANS_DRIFT_FLOOR = 0.005        # ″/h added in quadrature to drift errors in the pair model (straight-line motion, r grid)
ORPHANS_SEP_MIN_ARCSEC = 300.0     # pair separation between visits: 1000 AU ≈ 390″ ...
ORPHANS_SEP_MAX_ARCSEC = 7200.0    # ... 70 AU ≈ 4,800″ prograde, 6,900″ retrograde (six months apart; the partner may be in
                                   # the next zone)
ORPHANS_MIN_DT_DAYS = 60.0         # pairs only across visits (the visit rule's gap)
ORPHANS_R_GRID_AU = (40.0, 2000.0, 160)  # heliocentric distance grid of the pair model: min, max, points (log)
ORPHANS_VMAX_FACTOR = 1.4142       # bound orbit: heliocentric speed <= this × circular speed 2π/√r AU/yr (escape)
ORPHANS_FLUX_LN_FLOOR = 0.35       # ln-flux error floor per detector (each visit samples other channels of the detector)
ORPHANS_MIN_COMMON_DET = 1         # pairs need a detector measured in both visits
ORPHANS_PAIR_MIN_P = 0.01          # a pair passes when χ²(drifts + fluxes) has p >= this
ORPHANS_CONTROL_N = 50             # controls: the later visit's orphans shifted cyclically within their box ...
ORPHANS_CONTROL_SHIFT = (0.25, 0.75)  # ... by this share of the box (uniform), in RA and in Dec: no real pair survives
ORPHANS_CONTROL_SEED = 2026
ORPHANS_TRACK_AU = (70.0, 1000.0, 24)  # partner track (parallax only) for the zone-edge check: min, max, points (log)
ORPHANS_REPORT_MAX = 300           # rows of pairs / open orphans written to distant.json
ORPHANS_KNOWN_ARCSEC = 30.0        # an MPC object this close to the orphan (moved by its drift to the object's time) ...
ORPHANS_KNOWN_RATE_TOL = 0.3       # ... moving like it (″/h, + 3σ of the drift) ...
ORPHANS_KNOWN_DAYS = 5.0           # ... within this many days of the orphan's mean time: `known_name` (a recovered body)
ORPHANS_MAX_PAIRING = 5000         # more drift_ok orphans than this (a crowded zone): pairing skipped, said in distant.json
ORPHANS_MAX_PAIR_EVALS = 3_000_000 # pairs scored over real + controls: fewer controls (>= 5) when the real set is large

# --- completeness: synthetic movers injected into the stored dots (inject.py) -------------------------------------
# Once per zone after score (untiled, also in a tiled `all`): a sub-area of INJECT_BOX_DEG × INJECT_BOX_DEG (on the sky)
# at the zone's centre (the zone itself if smaller), its real dots (every photo overlapping it) plus synthetic dots of
# objects on straight tracks at the real exposure times, detectors and footprints, linked again in memory by
# link.fixed_groups + link.classify. Unit = one object in one session with >= SCORE_MIN_EXPOSURES exposures covering it
# >= SCORE_EDGE_PX inside the photo, as score's (recall.json); found = a mover of that session within MATCH_ARCSEC of the
# object at the mover's time (score.match_movers' rule). Off: env SKYSHIFT_COMPLETENESS=off.
COMPLETENESS = os.environ.get("SKYSHIFT_COMPLETENESS", "on").strip().lower() not in ("off", "0", "false", "no")
INJECT_BOX_DEG = 1.0           # side of the sub-area (hunt centre: 474 photos overlap it, ~400k real dots)
INJECT_EDGE_ARCSEC = 120.0     # units only this far inside the sub-area (its edge cuts real fixed sources, as a tile's)
INJECT_ROUNDS = 8              # independent injections (each relinks the sub-area, ~1 s at hunt density): fewer synthetic
INJECT_OBJECTS = 400           # neighbours per round (400 objects/deg² add ~10% to the fixed sources), 3,200 objects in all
INJECT_SEED = 20261114
INJECT_RATE_ARCSEC_H = (0.01, 300.0)  # log-uniform; direction uniform
INJECT_SNR = (5.0, 100.0)      # log-uniform; the S/N of every dot in a photo of median noise for its detector
INJECT_MWIR_SNR_FACTOR = 1.0   # S/N in D4–D6 = this × the object's S/N (1: the same S/N in every detector; asteroids'
                               # reflected light is fainter there, a cold planet's brighter)
INJECT_RATE_EDGES = (0.01, 0.0215, 0.0464, 0.1, 0.215, 0.464, 1.0, 2.15, 4.64, 10.0, 21.5, 46.4, 100.0, 215.0, 300.0)
INJECT_SNR_EDGES = (5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)
INJECT_PLATEAU_SNR = 20.0      # limits: the rate range is read at S/N >= this
INJECT_RECALL_RATES = (10.0, 90.0)  # vs_recall: the known asteroids' rates (hunt: all 30 objects at 10–90″/h)
INJECT_MAX_REAL_DOTS = 2_000_000  # more real dots in the sub-area (crowded field): a smaller box at the same centre
INJECT_NOISE_MAX_SNR = 10.0    # a photo's noise: median flux_err_ujy of its dots below this S/N (else its detector's)
INJECT_POS_ERR_SCALE = 1.6     # raw dot positions scatter 1.4–1.8 × pos_err_arcsec against Gaia (CLAUDE.md)
INJECT_EXPOSURE_S = 113.0      # trailing: elong × √(1 + L²/(12 σ²)), L = rate × exposure, σ = PSF_SIGMA_ARCSEC
# Detection: P(kept | measured S/N) from the sub-area's own dot counts: a power law fitted to the S/N histogram over
# INJECT_DET_FIT_SNR, extrapolated down; observed / expected per 0.5-wide bin (hunt centre: ~0.4 at 5–6, 0.7 at 6–7,
# 0.9 at 7–8, 1 above). With too few dots: logistic, 50% at INJECT_DET_SNR50, width INJECT_DET_WIDTH.
INJECT_DET_FIT_SNR = (12.0, 35.0)
INJECT_DET_MIN_DOTS = 2000
INJECT_DET_SNR50 = 5.7
INJECT_DET_WIDTH = 0.7
