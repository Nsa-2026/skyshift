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
