"""Zones, tile grid, and every tunable threshold of the hunt-zone pipeline, in one place.

Tune after the pilot: change the numbers here, rerun `link`, `score` and `suspects` (they are cheap;
`dots` and `known` are not affected).
"""

from __future__ import annotations

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
