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
}

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

# --- known (SkyBoT) ------------------------------------------------------------------------
# One cone per session (time run), covering that run's footprints inside the zone, at the run's
# mid-time; each object is moved to each photo's mid-time with SkyBoT's rates.
SKYBOT_URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
SKYBOT_MAX_IN_FLIGHT = 4
SKYBOT_PAD_DEG = 0.05
SKYBOT_TIMEOUT_S = 300

# --- known (MPC orbits with kete) ------------------------------------------------------------
# KNOWN_SOURCE picks where `known` gets its objects: "mpc" (default; known_mpc.py: MPC orbits moved with
# kete to each photo's MJD-AVG, seen from SPHEREx's header position) or "skybot" (the IMCCE service above). The CLI
# flag --known-source and the env var SKYSHIFT_KNOWN_SOURCE set it. Both write the same per-session
# cache shape, in cache/skybot/<zone>/ or cache/mpc/<zone>/; score and suspects read the chosen one.
KNOWN_SOURCE = os.environ.get("SKYSHIFT_KNOWN_SOURCE", "mpc")
KNOWN_SOURCES = ("skybot", "mpc")
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
