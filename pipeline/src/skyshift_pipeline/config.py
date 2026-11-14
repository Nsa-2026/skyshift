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

# --- tiles ---------------------------------------------------------------------------------
# One grid for every zone, anchored at the hunt zone's corner, so a tile id means the same
# patch of sky in the pilot and in the hunt zone. Steps in plain RA and Dec degrees.
TILE_DEG = 0.25
TILE_RA0 = 120.5
TILE_DEC0 = 14.4
TILE_ID_STRIDE = 10_000  # tile = j * TILE_ID_STRIDE + i

# --- photo search ----------------------------------------------------------------------------
COLLECTIONS = ("spherex_dr1", "spherex_qr3")  # in order of preference on duplicates; *_deep skipped
PHOTO_HALF_DIAG_DEG = 2.6  # a 3.5° photo reaches ~2.5° from its centre
S3_BUCKET = "nasa-irsa-spherex"
S3_HTTPS = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
IRSA_URI_PREFIX = "ibe/data/spherex/"  # spherex.artifact.uri = this + the S3 key

# --- dots ----------------------------------------------------------------------------------
DETECT_SIGMA = 5.0
FLAGS_UNDER_R_PX = 1.5
PIXEL_ARCSEC = 6.152
NET_RETRIES = 3
# pos_err_arcsec = sqrt((POS_ERR_K * PSF_SIGMA_ARCSEC / snr)^2 + POS_ERR_FLOOR_ARCSEC^2)  (contracts/dots.md)
POS_ERR_K = 1.4
PSF_SIGMA_ARCSEC = 3.2
POS_ERR_FLOOR_ARCSEC = 0.25    # measured on the first photo; V3 will replace it
ELONG_R_PX = 2.5               # elong: second moments of the pixels within this radius
ELONG_BKG_PX = (3.5, 5.5)      # local background annulus for elong

# --- known (SkyBoT) ------------------------------------------------------------------------
SKYBOT_URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
SKYBOT_MAX_IN_FLIGHT = 4
SKYBOT_RADIUS_PAD_DEG = 0.05  # added to the photo's centre-to-corner distance
SKYBOT_TIMEOUT_S = 180

# --- link ----------------------------------------------------------------------------------
FIXED_MIN_SESSIONS = 3        # seen in >= this many sessions -> fixed
MOVER_MIN_COVER_SESSIONS = 3  # the tile must be covered by >= this many sessions
MOVER_MIN_PHOTOS = 2          # 2 photos of the one session, or a simultaneous detector pair
SIMULTANEOUS_PAIRS = ((1, 4), (2, 5), (3, 6))
MOVER_MAX_ELONG = 3.0         # dots longer than this (trails, cosmic rays) don't count as mover evidence

# --- score ---------------------------------------------------------------------------------
SCORE_V_MAX = 16.0
SCORE_EDGE_PX = 10.0
MATCH_ARCSEC = 12.0
SAME_SESSION_DAYS = 0.02       # a mover within ~30 min of the prediction is in the same session
CONTROL_OFFSET_ARCSEC = 120.0  # control: predictions moved 2′ in random directions
CONTROL_SEED = 20261114
G2_RECALL_MIN = 0.5
V_BINS = (-99.0, 14.0, 15.0, 16.0, 17.0, 18.0)

# --- suspects ------------------------------------------------------------------------------
KNOWN_ARCSEC = 30.0
GAIA_ARCSEC = 6.0
BRIGHT_FIXED_ARCSEC = 30.0
BRIGHT_FIXED_SNR = 100.0       # a fixed source this bright makes ghosts and halos around it
SUSPECT_VETO_BITS = (1 << 22) | (1 << 23) | (1 << 24) | (1 << 20) | (1 << 28) | (1 << 26)
# GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO, BLOOM
GAIA_BATCH = 50
EVIDENCE_RANK = {"two_exposures": 0, "two_detectors": 1}
