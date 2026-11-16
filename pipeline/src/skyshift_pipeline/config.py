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
# sep deblend_cont of core.detect: a blob is split where a branch holds at least this share of its light. At 0.005 a
# star beside a much brighter star's glow is never split off (R Dra, 2025W43_2A_0464_2D4, peak 2,623 MJy/sr: 0 of 4
# Gaia neighbours at 92–162″ with S/N 43–59; at 1e-4, 3 of 4). The sky run keeps DOTS_DEBLEND_CONT; the zones of
# DOTS_FINE_ZONES use DOTS_FINE_DEBLEND_CONT. Dot files are never rewritten (members.dot_row points at rows) and these
# zones' photos also lie in ecliptic zones, so each other setting has its own folder, dots_deblend<value>/ (dots_dir_for):
# `dots` and `link` of such a zone use it; later steps read the folder `link` recorded (results/<zone>/summary.json
# steps.link.dots_dir; "dots" when absent). Each dot file's metadata records the setting (`detect`).
DOTS_DIR = "dots"              # the sky run's dot files: dots/<obs_id>D<det>.parquet
DOTS_DEBLEND_CONT = 0.005
DOTS_FINE_DEBLEND_CONT = 1e-4  # one line to change; a new value gets a new folder (files are never mixed)
DOTS_FINE_ZONES = ("t_rdra", "t_barnard", "t_rwaur", "t_epsindib", "t_wise0855")
# Fine detection v2 (15 Nov night; contracts/dots.md "Fine detection v2"), the DOTS_FINE_ZONES only. Test on 43 hunt photos
# (D1–D6) + R Dra D1/D4 (deblend test, 15 Nov): clean_param 3 keeps ~7% more faint real dots (S/N 5–7, 98% catalogued) and
# no measurable fakes; the saturation guard removes ~90% of the fakes finer deblending and the second pass make (pieces
# of a saturated star's wings, S/N 200–550, mostly with no dot on the masked core to anchor anything on) for 0.7% of their
# gains; the ePSF second pass finds Gaia pairs 2–3 px apart 9% → 81% and known asteroids within 6 px of a brighter star
# 4 → 14 of 17. Each setting here is in the folder name (dots_dir_for): change one and the files go to a new folder.
DOTS_FINE_CLEAN_PARAM = 3.0    # sep clean_param (1.0 = sep's default = the sky run): Moffat β of its cleaning model
DETECT_SAT_GUARD_PX = 6.0      # a dot the sky run's setting doesn't also find (within 1 px) is dropped this close to an
                               # OVERFLOW/NONLINEAR pixel; second-pass dots too
DOTS_FINE_PSF_PASS = True      # the ePSF second pass (psf.residual_companions) for the fine zones
DOTS_FINE_GLOW = True          # the glow model (core.glow; GLOW_* below) for the fine zones: folder suffix _glow
DOTS_SKY_GLOW = False          # ... and for the sky run (off: dots/ stays as written; on: dots_glow/)
# ePSF second pass: subtract the ePSF of every unsaturated dot with S/N >= PSF_PASS_PRIMARY_SNR (ePSF position, then
# flux), run sep on the residual within PSF_PASS_R_PX of those dots (noise √(noise² + (PSF_PASS_MODEL_FRAC · model)²),
# minarea PSF_PASS_MINAREA, deblend_cont PSF_PASS_DEBLEND_CONT), keep new dots > PSF_PASS_DEDUP_PX from every dot with
# residual S/N >= DOT_MIN_SNR, not within PSF_PASS_SAT_R_PX of a saturated dot, passing the mirror test (residual in
# r = 1.5 px at the point reflection through the nearest subtracted dot < PSF_PASS_MIRROR_FRAC × the dot's, and lower
# by >= PSF_PASS_MIRROR_SIGMA σ: a companion leaves the far side empty, a ring or PSF mismatch doesn't) and the guard.
PSF_PASS_PRIMARY_SNR = 10.0
PSF_PASS_R_PX = 6.0
PSF_PASS_MODEL_FRAC = 0.05
PSF_PASS_STAMP_HALF = 3        # ePSF model stamps (2h+1)² (the ePSF is ~0 beyond ±3.2 px)
PSF_PASS_MINAREA = 2
PSF_PASS_DEBLEND_CONT = 1e-4
PSF_PASS_DEDUP_PX = 1.0
PSF_PASS_SAT_R_PX = 8.0
PSF_PASS_MIRROR_R_PX = 1.5
PSF_PASS_MIRROR_FRAC = 0.5
PSF_PASS_MIRROR_SIGMA = 3.0
SEP_FLAG_PSF_PASS = 0x200      # sep_flag bit of a dot found by the second pass (sep's own flags use bits 0x1–0x80)
# Extraction v2 (16 Nov; docs/extraction-v2.md): the settings of the one combined rerun of every photo. Off until that
# rerun (DOTS_V2, env SKYSHIFT_DOTS_V2=1): every zone then uses DOTS_V2_SETTINGS (fine detection v2 + glow + the matched-filter pass and the
# v2 columns, peaks file and photo metadata), folder dots_deblend1e-4_cp3_g6_psf_glow_v2/. Measured on ecl04 D2
# (2025W34_1B_0173_1D2, full frame) and 256-row bands of ecl04 / hunt photos: see docs/extraction-v2.md.
DOTS_V2 = os.environ.get("SKYSHIFT_DOTS_V2", "0") == "1"   # env SKYSHIFT_DOTS_V2=1 turns it on
# Matched-filter pass: the ePSF-matched filter (inverse-variance weighted, ePSF of the pixel's zone, source centred on
# the pixel) on the residual of the second pass (IMAGE minus its background and the ePSF models of the S/N >= 10 dots;
# noise √(noise² + (PSF_PASS_MODEL_FRAC · model)²)). A local maximum (3 × 3) of its S/N >= MF_PASS_SNR, more than
# MF_PASS_DEDUP_PX from every dot, outside the saturation guard and a glow star's own pixels, and passing the mirror test
# when it lies within PSF_PASS_R_PX of a subtracted dot, is a dot (sep_flag SEP_FLAG_MF_PASS), measured on the residual.
# Its aperture snr may be < DOT_MIN_SNR (MF S/N ≈ 1.5 × aperture S/N at S/N 5–7). Every dot gets snr_mf.
MF_PASS_SNR = 5.0
MF_PASS_DEDUP_PX = 1.0
MF_KERNEL_HALF = 3             # ePSF kernel (2h+1)² px
MF_PHASES = 1                  # sub-pixel source positions per axis (2: 4 kernels per tile; on ecl04 D2 Gaia at aperture
                               # S/N 3–4 64 → 80% found, but 4× the noise peaks (negative image, >= 5: 30 → 131): off)
MF_ZONE_GROUP = 3              # ePSF zones merged 3 × 3 per kernel tile (21 × 21 zones per photo -> 7 × 7 tiles)
SEP_FLAG_MF_PASS = 0x400
# Peaks file (v2): every other local maximum of the same S/N map >= MF_PEAK_SNR with no dot within MF_PASS_DEDUP_PX
# (S/N 4–5, and the >= 5 peaks the guard / glow / mirror test refused): <folder>_peaks/<obs_id>D<det>.parquet.
MF_PEAK_SNR = 4.0
DOTS_TRANSIENT_R_PX = 3.0      # v2 column n_transient: TRANSIENT pixels within this radius of the dot
EMPIRICAL_NOISE_N = 400        # v2 photo metadata: r = 2 px forced aperture at this many random places ...
EMPIRICAL_NOISE_MIN_SEP_PX = 8.0  # ... at least this far from every dot
EMPIRICAL_NOISE_SEED = 0
PARTNER_DETECTOR = {1: 4, 4: 1, 2: 5, 5: 2, 3: 6, 6: 3}  # exposed at the same moment (CLAUDE.md)
SAT_CORES_MAX = 1000           # v2 photo metadata: OVERFLOW/NONLINEAR clumps listed (largest first)


@dataclass(frozen=True)
class DetectSettings:
    """How a zone's dots are detected (dots.dots_table). The default is the sky run."""
    deblend_cont: float = 0.005
    clean_param: float = 1.0
    sat_guard_px: float = 0.0
    psf_pass: bool = False
    glow: bool = False
    v2: bool = False               # extraction v2: matched-filter pass, v2 columns, peaks file, v2 metadata

    @property
    def folder(self) -> str:
        return dots_dir_for(self.deblend_cont, self.clean_param, self.sat_guard_px, self.psf_pass, self.glow, self.v2)

    @property
    def is_sky(self) -> bool:
        return self == DetectSettings(DOTS_DEBLEND_CONT)


def _num(v: float) -> str:
    """1e-4 -> 1e-4, 0.001 -> 1e-3, 2.5e-4 -> 2.5e-4 (deblend_cont)."""
    mant, exp = f"{float(v):.6e}".split("e")
    mant = mant.rstrip("0").rstrip(".")
    return f"{mant}e{int(exp)}"


def dots_dir_for(deblend_cont: float, clean_param: float = 1.0, sat_guard_px: float = 0.0, psf_pass: bool = False,
                 glow: bool = False, v2: bool = False) -> str:
    """The folder of dot files made with these settings: DOTS_DIR for the sky run's, else DOTS_DIR plus one part per
    setting that differs: _deblend<v> (1e-4 -> dots_deblend1e-4), _cp<clean_param>, _g<guard px>, _psf, _glow, _v2
    (the fine zones' v2: dots_deblend1e-4_cp3_g6_psf; with the glow model, 16 Nov: dots_deblend1e-4_cp3_g6_psf_glow;
    extraction v2: dots_deblend1e-4_cp3_g6_psf_glow_v2)."""
    name = DOTS_DIR
    if float(deblend_cont) != float(DOTS_DEBLEND_CONT):
        name += f"_deblend{_num(deblend_cont)}"
    if float(clean_param) != 1.0:
        name += f"_cp{float(clean_param):g}"
    if float(sat_guard_px) > 0:
        name += f"_g{float(sat_guard_px):g}"
    if psf_pass:
        name += "_psf"
    if glow:
        name += "_glow"
    if v2:
        name += "_v2"
    return name


DOTS_V2_SETTINGS = DetectSettings(DOTS_FINE_DEBLEND_CONT, DOTS_FINE_CLEAN_PARAM, DETECT_SAT_GUARD_PX, True, True, True)


def zone_detect(zone: str | None) -> DetectSettings:
    """The detection settings of the `dots` step for this zone (None: the sky run's); DOTS_V2: extraction v2 for all."""
    if DOTS_V2:
        return DOTS_V2_SETTINGS
    if zone in DOTS_FINE_ZONES:
        return DetectSettings(DOTS_FINE_DEBLEND_CONT, DOTS_FINE_CLEAN_PARAM, DETECT_SAT_GUARD_PX, DOTS_FINE_PSF_PASS,
                              DOTS_FINE_GLOW)
    return DetectSettings(DOTS_DEBLEND_CONT, glow=DOTS_SKY_GLOW)


def zone_dots(zone: str | None) -> tuple[str, float]:
    """(folder, deblend_cont) the `dots` step uses for this zone (None: the sky run's); zone_detect has every setting."""
    s = zone_detect(zone)
    return s.folder, s.deblend_cont
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
POS_ERR_SCALE = 1.6            # pos_err_arcsec is 1.4–1.8× too small against Gaia (pilot): × this before use
ELONG_R_PX = 2.5               # elong: second moments of the pixels within this radius
ELONG_BKG_PX = (3.5, 5.5)      # local background annulus for elong

# --- known (MPC orbits with kete) ------------------------------------------------------------
# known_mpc.py: MPC orbits moved with kete to each photo's MJD-AVG, seen from SPHEREx's header position.
# One cache file per session in cache/mpc/<zone>/; score and suspects read it.
MPC_ORBITS_URL = "https://minorplanetcenter.net/Extended_Files/mpcorb_extended.json.gz"
MPC_ORBITS_DIR = "cache/mpc/orbits"  # mpcorb_<date>.parquet + cometels_<date>.parquet: dated snapshots, newest used everywhere
# Comets (16 Nov): cometels.json.gz (what kete reads, = CometEls.txt) holds only the comets whose orbits the MPC moved to
# its current epoch (973 on 16 Nov 2026: epoch 2026-11-15); comets last observed earlier in the mission are left out
# (306P/LINEAR, the ecl04 "grade A" mover: epoch 2025-11-21). allcometels.json.gz (= AllCometEls.txt, 4,666 comets) has
# every comet's latest orbit. The cometels snapshot is both, by designation, cometels' row first; from allcometels
# only orbits with an epoch (or, without one, a perihelion) from MPC_ALLCOMETS_MIN_JD on: older ones are lost or
# historical comets (SOHO sungrazers, 19th-century apparitions) whose elements say nothing about 2025–2026.
MPC_COMETS_URL = "https://minorplanetcenter.net/Extended_Files/cometels.json.gz"         # kete's comet file
MPC_COMETS_ALL_URL = "https://minorplanetcenter.net/Extended_Files/allcometels.json.gz"
MPC_ALLCOMETS_MIN_JD = 2457023.5     # 2015-01-01
MPC_V_MAX = 21.0                     # the 30″ position-only known cut (suspects) and nearest_known_arcsec use objects brighter
                                     # than this (or without V: comets, planets); score uses V < 16 to 18
MPC_V_STORE = 23.0                   # the cache keeps objects to this V (16 Nov; was MPC_V_MAX): fainter known asteroids and
                                     # NEOs glowing in D5–D6 are flagged by the certain rule below, never by position alone.
                                     # Rows per photo vs V < 21: x2.5 at 22, x5.1 at 23, x8.9 at 25 (1,754 movers, 16 Nov); the
                                     # faintest body SPHEREx put on a mover was V 21.3 (an NEO), so 23 leaves 1.7 mag; the check
                                     # producer scripts/candidates/known_check.py matches near candidates with no V cut at all
MPC_G_DEFAULT = 0.15                 # H-G slope when the MPC has none
# Certain known body (contracts/api.md v3.23 `status` known): predicted within KNOWN_CERTAIN_ARCSEC of every dot's time and
# place, its on-sky rate within KNOWN_RATE_TOL (fraction) of ours and its direction within KNOWN_PA_TOL_DEG when both move.
# Any brightness. Nearer than KNOWN_NEARBY_ARCSEC but not certain: a `known_nearby` check (warn) for a person to look at.
KNOWN_CERTAIN_ARCSEC = 10.0
KNOWN_RATE_TOL = 0.2
KNOWN_PA_TOL_DEG = 15.0
KNOWN_NEARBY_ARCSEC = 120.0
MPC_HEADER_WORKERS = 32              # parallel IMAGE header reads (one per exposure; ~1 s each, I/O bound)
MPC_HEADER_BLOCK = 262_144           # bytes per HTTPS read: the primary and IMAGE headers fit in the first block
MPC_FOV_PAD_DEG = 0.05               # kete fields are the s_region corners pushed out 3′; load_predictions cuts exactly
# known_planets (JPL Horizons): planets and their large moons, which the MPC orbits don't hold. radius_deg: how far
# the system reaches from the planet (Himalia ~1° at Jupiter, Phoebe ~0.6° at Saturn, Nereid ~0.15° at Neptune).
PLANET_SYSTEMS = {
    "Mars": {"naif": 499, "radius_deg": 0.05, "moons": [(401, "Phobos"), (402, "Deimos")]},
    "Jupiter": {"naif": 599, "radius_deg": 1.1, "moons": [(501, "Io"), (502, "Europa"), (503, "Ganymede"), (504, "Callisto"),
                                                          (505, "Amalthea"), (506, "Himalia"), (507, "Elara")]},
    "Saturn": {"naif": 699, "radius_deg": 0.65, "moons": [(601, "Mimas"), (602, "Enceladus"), (603, "Tethys"), (604, "Dione"),
                                                          (605, "Rhea"), (606, "Titan"), (607, "Hyperion"), (608, "Iapetus"),
                                                          (609, "Phoebe"), (610, "Janus"), (611, "Epimetheus")]},
    "Uranus": {"naif": 799, "radius_deg": 0.05, "moons": [(701, "Ariel"), (702, "Umbriel"), (703, "Titania"), (704, "Oberon"),
                                                         (705, "Miranda")]},
    "Neptune": {"naif": 899, "radius_deg": 0.15, "moons": [(801, "Triton"), (802, "Nereid")]},
}
PLANETS_COARSE_STEP_H = 6    # the planets' coarse table over the zone's time range, to find the visits they come near
PLANETS_MARGIN_DEG = 4.0     # near = within this + radius_deg of the zone box (a photo reaches ~2.5° past its centre)
PLANETS_STEP_MIN = 10        # each body's table during a visit, interpolated to the photos' times (Io: ~3″ per step)

# --- link ----------------------------------------------------------------------------------
FIXED_LINK_ARCSEC = 4.0       # friends-of-friends radius for fixed sources
FIXED_SNAP_ARCSEC = 1.0       # dots are first snapped to cells this big (speed); FoF runs on cells
FIXED_MIN_SESSIONS = 3        # a group in >= this many sessions is fixed
FIXED_VETO_ARCSEC = 6.0       # dots this close to a fixed source are dropped before pairing
# members.parquet (the dots spectra, calib, astrometry, lightcurves measure) keeps a fixed group's dots except
# (hunt, 14 Nov: these made most of the fake proper motions of Gaia stars):
FIXED_MEMBER_DROP_MASKED_PEAK = True  # a mask bit other than saturation at the peak (PERSIST, TRANSIENT, SUR_ERROR, …):
                                      # the core is masked, the centroid sits on the wings 1.5–5″ off; saturated dots
                                      # (OVERFLOW/NONLINEAR at the peak) stay, dots re-centroids them on purpose
FIXED_MEMBER_ONE_PER_PHOTO = True     # one dot per photo: the nearest to the group's position; the others are a bright
                                      # star's wing fragments or a neighbour chained in (they still veto movers)
PAIR_BASE_ARCSEC = 3.0        # two exposures pair if separated <= base + rate x dt
PAIR_RATE_ARCSEC_PER_MIN = 1.0
SIMULTANEOUS_PAIR_ARCSEC = 3.0
SIMULTANEOUS_PAIRS = ((1, 4), (2, 5), (3, 6))
PAIR_CONFIRMED_RATE_ARCSEC_PER_MIN = 5.0  # two dots that each have a simultaneous partner also pair up to 3″ + this × Δt
                              # (fast movers, ~140–380″/h, step past the plain rule; their per-exposure D1+D4 pairs
                              # would otherwise be one-exposure candidates that MOVER_MAX_MISSED_FEW drops)
# Fast movers without a detector partner (16 Nov; near-Earth asteroids glowing in D5/D6 alone, or too faint in the
# partner): beyond the plain rule two dots of different exposures pair up to 3″ + this × Δt only when a dot of a third
# exposure of the session lies within PAIR_FAST_LINE_ARCSEC of their constant-rate line at its time (a straight,
# evenly spaced chain of >= 3 exposures). 15″/min = 900″/h: a trail of 28″ in 113 s, where the dots reach
# MOVER_MAX_ELONG (elong × √(1 + L²/12σ²), σ = 3.2″). None = off. Injection (pilot centre, one detector only, 16 Nov):
# movers found 146–250″/h 5% → 68%, 250–400 0 → 69%, 400–600 0 → 52%, 600–900 0 → 20%; slower bins unchanged.
# Known asteroids (MPC): ecl08 146–400″/h 0 → 7 found (every one with a free dot), pilot 0 → 1.
PAIR_FAST_RATE_ARCSEC_PER_MIN = 15.0
PAIR_FAST_LINE_ARCSEC = 3.0
# ... and above PAIR_FAST_TRAIL_MIN_ARCSEC_H the chain's dots must look trailed: median elong >= 1 + PAIR_FAST_TRAIL_FRAC ×
# (√(1 + L²/12σ²) − 1), L = rate × PAIR_EXPOSURE_S (point-like dots: median elong 1.3 at S/N 5–10). ecl08 (crowded, b ~ 0°)
# fast movers after the flux veto, real vs shuffled control: 146–250″/h 11 vs 0, 250–400 5 vs 3, 400–900 5 vs 5 without
# this test, 3 vs 1 with it (0.5: 5 vs 5; 1.0: 2 vs 0). None = no trail test.
PAIR_FAST_TRAIL_MIN_ARCSEC_H = 400.0
PAIR_FAST_TRAIL_FRAC = 0.75
PAIR_EXPOSURE_S = 113.0
# Shuffled-exposure control (16 Nov; link stats `control`): the free dots, every dot and the footprints of each exposure
# moved by its own offset (LINK_CONTROL_OFFSET_ARCSEC, directions spread evenly per session from a seeded random start,
# so no two exposures of a session move together) and paired, covered and vetted like the real ones: no real object
# links any more, chance pairs of unrelated dots stay. The movers it makes are the chance level. None = off.
LINK_CONTROL_OFFSET_ARCSEC = 300.0
MOVER_MAX_ELONG = 3.0         # dots longer than this (trails, cosmic rays) don't pair
MOVER_MIN_COVER_SESSIONS = 3  # sessions whose footprints contain the candidate
# Fixed groups: an asteroid at ~40″/h moves ~1.4″ per exposure, so the 4″ FoF chains its dots, and a track passing a
# star joins the star's fixed group. Hunt, 14 Nov, 36-session sample: 17 of 42 known V < 17 object-sessions that link
# missed had their dots (≤ 1″ from the MPC position) inside a star's group, 3–13″ from its median. A would-be fixed
# group's dots farther than this from its median are released and regrouped among themselves (a second star chained
# to the first is fixed again with >= FIXED_MIN_SESSIONS sessions; a one-session track is left free). None = off (with
# every rule below that releases or keeps released groups).
FIXED_RELEASE_ARCSEC = 6.0
# Review of #176 (14 Nov, hunt): 25 of 44 missed V < 16 sightings sat inside a star's group, most closer than 6″ (Alekto
# s75: a 2,600 µJy asteroid chained into a 476 µJy star 3.4–4.8″ away), and the release made 85 uncatalogued fixed sources
# on a 2.1 deg² box (wing fragments ~7.7″ from S/N ~370 stars, seen in a few of the star's sessions). So also released:
FIXED_RELEASE_FLUX_RATIO = 3.0      # a dot >= this × its group's median flux in its detector (not saturated at the peak)
# ... and (16 Nov, variable-safe) only when it also sits off the group (FIXED_RELEASE_SESSION_ARCSEC + _SIGMA × its σ from
# the median) or the group's bright dots lie in one session and it sits > FIXED_RELEASE_FLUX_ONE_SESSION_ARCSEC off: a
# variable star's bright state (Mira, young star) lies on the star over several sessions and stays one source; an
# asteroid crossing a fainter star is one session, 2–5″ off (Alekto s75: 2.5–4.8″). False = the flux alone.
FIXED_RELEASE_FLUX_SAFE = True
FIXED_RELEASE_FLUX_ONE_SESSION_ARCSEC = 1.0
FIXED_RELEASE_SESSION_ARCSEC = 2.0  # a session whose dots' mean is > this + FIXED_RELEASE_SESSION_SIGMA × the mean's σ from
FIXED_RELEASE_SESSION_SIGMA = 3.0   # the median of the group's session means (those of its visit when it has >= 3 there)
# ... and > this × the median offset of those sessions: a slow mover (0.02–0.2″/h, a distant planet) drifts through all
# its sessions and must stay one single-visit fixed source for `orphans` (completeness test: without it, 0.05–0.2″/h
# single-visit fixed fell from 90% to 2–51%). A star's sessions scatter ~0.3″, so one session 2–6″ off still goes.
# Of such a session only the dots themselves > FIXED_RELEASE_SESSION_ARCSEC + SIGMA × their σ off go: the star's stay.
FIXED_RELEASE_SESSION_SPREAD = 5.0
LINK_POS_ERR_SCALE = 1.6            # σ of a dot position = pos_err (contracts/dots.md, from S/N) × this (1.4–1.8 vs Gaia)
# A released group seen in >= FIXED_MIN_SESSIONS sessions stays its own fixed source only when catalogued (a refcat
# Gaia/CatWISE/2MASS entry, moved to the group's time, within FIXED_RELEASE_CATALOG_ARCSEC; link reads refcat/<zone>.parquet
# when present) or seen in >= FIXED_RELEASE_KEEP_SHARE of its parent's sessions. Otherwise its far dots go back to the
# parent and its dots released by flux or session offset stay free (never fixed). None = keep every such group.
FIXED_RELEASE_KEEP_SHARE = 0.5
FIXED_RELEASE_CATALOG_ARCSEC = 2.5
# A free dot >= this × the median flux, in its detector, of every fixed source within FIXED_VETO_ARCSEC (not saturated at
# the peak) escapes the 6″ veto: an asteroid passing a fainter star. None = the plain veto.
FIXED_VETO_BRIGHT_RATIO = 3.0
# Star crossings (16 Nov): an asteroid passing within 4″ of a star is chained into its fixed group (hunt: 2.4% of known
# V < 16 sightings, 10% at V 17–18; ecl08 5–24%). nonfixed_dots.parquet also lists (in_fixed) a group's member dots that
# sit > FIXED_OFF_ARCSEC + FIXED_OFF_SIGMA × σ off its median in their photo, or are a second dot of the group in one photo
# > FIXED_OFF_ARCSEC off, unless another session has such a dot within FIXED_OFF_REPEAT_ARCSEC (a neighbour, a wing piece).
# tracklets recovers them only on a track's predicted path (TRACK_RECOVER_IN_FIXED). None = off.
FIXED_OFF_ARCSEC = 1.5
FIXED_OFF_SIGMA = 3.0
FIXED_OFF_REPEAT_ARCSEC = 2.0
# Mover vetting (link.vet_reason, after the session-coverage test; stats vetoed_<rule>; SKYSHIFT_MOVER_VET=off keeps
# every candidate, with the columns). Hunt, 14 Nov: of 6,143 movers 4,464 were seen in two exposures and 4,045 of those
# matched no MPC object; 209 of the 228 suspects were two-dot pairs. Known asteroids with V < 17 showed a dot in every
# exposure that covered them in 848 of 893 movers; chance pairs (two unrelated dots ≤ 3″ + 1″/min × Δt apart) don't.
# On the sample (87 suspects): missed 43, both dots at a frame edge 21, flux jump 21, kept 2.
# Review (14 Nov): the missed rule dropped 239 of 1,652 known-asteroid movers zone-wide; the "missed" exposures had the
# track merged into a star's detection (Aegina s201: the nearest dot an S/N 60–220 star 10–14″ away), 3–14 px from the
# frame edge, outside the zone box (dots are clipped there), or one real non-detection.
MOVER_COVER_EDGE_PX = 25.0       # an exposure covers a track when one of its photos holds it this far inside (px)
MOVER_EDGE_VETO_PX = 10.0        # the `edge` rule: a dot in a photo holding the track less than this far inside
MOVER_COVER_FIXED_SLACK_ARCSEC = 2.0  # ... and the track isn't within FIXED_VETO_ARCSEC + this of a fixed source
                                 # (link drops dots there: no dot is expected). None = no exclusion
MOVER_COVER_BLEND_ARCSEC = 15.0  # ... and that photo has no dot (fixed members too) this close to the track (merged
                                 # into another source's detection). None = no exclusion
MOVER_COVER_BRIGHT = (100.0, 30.0)  # ... nor a dot of S/N >= [0] within [1]″ (a bright star's isophote). None = off
MOVER_MAX_MISSED_FEW = 0         # tracks seen in <= 2 exposures: covering exposures without a dot allowed
MOVER_MAX_MISSED_PAIRED = 1      # ... seen in 2 exposures, each with a simultaneous detector pair (D1+D4, D2+D5, D3+D6)
MOVER_MAX_MISSED = 1             # tracks seen in >= 3 exposures: ... allowed (a masked pixel, a faint exposure)
MOVER_COVER_ZONE_MARGIN_ARCSEC = 10.0  # a track position closer than this to the zone box's edge doesn't cover (dots are
                                 # clipped at the box; a 2-dot track's direction is rough: Alekto s43, 1″ inside)
# `still` (a star seen in < 3 sessions, never fixed): a free dot of another session within MOVER_STILL_FREE_ARCSEC, in
# >= MOVER_STILL_FREE_MIN_SESSIONS sessions or in one of its detectors with a flux within MOVER_STILL_FREE_FLUX_RATIO.
MOVER_STILL_FREE_ARCSEC = 2.0
MOVER_STILL_FREE_MIN_SESSIONS = 2
MOVER_STILL_FREE_FLUX_RATIO = 2.0
# ... or seen in 2 exposures and moved less than this × σ (dot_sigma_arcsec). None = off: on the boxes 16 of 25 known
# two-exposure asteroids moved < 3σ (S/N 6–30 dots 2.1 min apart at 20–100″/h), as many as the unmatched pairs.
MOVER_STILL_SIGMA = None
MOVER_LINE_MAX_ARCSEC = 4.0      # >= 3 exposures: largest distance of an exposure's position from the fitted line
                                 # (known: max 3.2″, 99% < 2.6″)
MOVER_FLUX_RATIO_MAX = 3.0       # largest max/min flux among one detector's dots (known: max 2.6, 99% < 2.2)
MOVER_VET = os.environ.get("SKYSHIFT_MOVER_VET", "on") != "off"
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
# Dot error model (15 Nov review: 85% of hunt channels hold 1–2 dots, so the channel errors are the quoted dot errors
# alone; the labelled sources' chi2_nu had median 3.4, p90 29). Each dot's error before weighting:
# σ' = √((k σ)² + (floor × |channel median|)²). Measured on aperture fluxes (flux_ujy) of quiet single Gaia stars, pairs of
# dots in one channel (146k pairs, hunt box 126.2–127.7°, 20–21.5°): k = 1 (below S/N 10 the S/N ≥ 5 cut truncates the
# pairs), floor 1.5–2.5% (median pair chi2 1.00 per detector; quoted errors alone 1.36–1.48 in D1–D3, ~3× too small at
# S/N > 100). Not the light-curve model: that is fitted on PSF fluxes (k 1.04–1.42), on aperture pairs it gives 0.5–0.9.
SPECTRA_ERR_K = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0}
SPECTRA_ERR_FLOOR = {1: 0.018, 2: 0.024, 3: 0.025, 4: 0.019, 5: 0.017, 6: 0.018}
# Single bad dots (cosmic rays, artefacts) made band labels (review: 15 of 38 PAH rows; e.g. a D4 dot at 3,012 µJy
# among ~350, √(a·b) 0.42 × a star's; another with aperture flux 33,968 µJy, PSF fit 561 µJy). Left out of the spectrum:
SPECTRA_AP_PSF_MAX = 2.0       # aperture flux > this × the ePSF flux (flux_psf_ujy): not a point source's light
SPECTRA_SHARP_FRAC = 0.7       # √(a·b) < this × the detector's stellar size at the dot's S/N (vet.star_size; no OUTLIER needed)
SPECTRA_SPIKE_SIGMA = 5.0      # a dot further than this × σ' from the median of its source's ±2 neighbouring channels
SPECTRA_SPIKE_FRAC = 0.5       # ... and than this × |that median| (same detector, >= 2 of them measured), unless the
SPECTRA_SPIKE_KEEP_VISITS = 2  # channel's deviating dots come from at least this many visits (a real narrow feature)
SPECTRA_VISIT_BITS = 15        # sources `visit_bits`: bit (visit − 1) mod 15 per channel (int16)

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
# 16 Nov: only what is certain is removed (a known body matching place and, where measured, rate and direction; a
# planet, its moons or its glare). Every other former cut stays on the suspect as a warning: warn_codes (contracts/api.md
# v3.23 check codes) and warn_values ({code: {name: number}}); the exporter turns them into `checks`.
KNOWN_ARCSEC = 30.0            # a known object this close (same session) that isn't certainly it: warning `known_nearby`
# removed as that known body: within SUSPECT_KNOWN_CERTAIN_ARCSEC at the mover's time (place alone), or within KNOWN_ARCSEC
# with our measured motion at its rate (50% or 3σ) and direction (45° or 3σ). ecl04, 1,911 movers within 5″ of an MPC
# prediction with our motion measured (2–6 exposures): rate ratio p1–p99 0.43–1.94, direction p95 30°, p99 59°; a
# 20% / 15° test would have kept 32% of them (619) as suspects, 50% / 45° keeps 6%: motion alone never overrules place.
SUSPECT_KNOWN_CERTAIN_ARCSEC = 5.0
SUSPECT_KNOWN_RATE_TOL = 0.5
SUSPECT_KNOWN_PA_TOL_DEG = 45.0
SUSPECT_KNOWN_MOTION_SNR = 3.0  # our motion is measured: two exposures at motion_snr >= this, or a line through >= 3
SUSPECT_HOT_PIXEL_PX = 1.0     # removed as a hot pixel: in one detector, dots of >= 2 exposures all this close (px) to
SUSPECT_HOT_SKY_PX = 3.0       # their median pixel while the sky under them moved >= this many pixels (rate × Δt)
GAIA_ARCSEC = 6.0              # catalogue_star (and reason `gaia`): a Gaia DR3 star (G < 20) this close
CATWISE_ARCSEC = 6.0           # catalogue_star (and reason `catwise`): a CatWISE2020 source this close
# a catalogue source within its radius is a vetting reason (gaia, catwise, allwise, 2mass: grade C) only when it is at
# most this much fainter than the mover (AB, the catalogue's brightest band; unknown brightness counts as catalogued);
# fainter, it is a chance neighbour: catalogue_star only. ecl04: real asteroids' sources p50 3.3 mag fainter, junk 1.1–1.6.
SUSPECT_CATALOGUE_FAINTER_MAG = 2.0
SUSPECT_VEGA_TO_AB = {"g_mag": 0.0, "w1_mag": 2.699, "w2_mag": 3.339, "j_mag": 0.91, "k_mag": 1.85}  # catalogue (Vega) -> AB
BRIGHT_FIXED_ARCSEC = 30.0     # bright_star: a fixed source with snr >= BRIGHT_FIXED_SNR this close
BRIGHT_FIXED_SNR = 100.0       # a fixed source this bright makes ghosts and halos around it
# GHOST, GHOST_FPA, GHOST_EXT, CROSSTALK, HALO, BLOOM in a dot's 3×3: warnings since 16 Nov, no longer a cut
SUSPECT_FLAG_WARN = {"flag_bloom": 1 << 26, "flag_halo": 1 << 28, "flag_ghost": (1 << 22) | (1 << 23) | (1 << 24),
                     "flag_crosstalk": 1 << 20}  # warn code -> record bits in any dot's 3×3 (`flags`)
# the strong form of a warning is also a vetting reason (grade C, ranked last, never removed). Measured on every mover of
# ecl04 / ecl11 (real = an MPC prediction within 5″; rest = no known body within 60″), share hit real vs rest:
#   BLOOM in a dot's 3×3 0.0 / 0.5% vs 13.5 / 3.4%; at every dot's peak 0.0 / 0.0% vs 11.3 / 2.8%   -> reason `bloom`
#   HALO in a dot's 3×3 20.4 / 22.1% vs 21.9 / 24.4% (no use); at every dot's peak 0 / 0% vs 5.2 / 5.9% -> reason `halo`
#   bright fixed source within 30″ 1.0 / 2.9% vs 26.4 / 18.4%; within 15″ 0.2 / 0.3% vs 23.5 / 11.0%   -> `bright_star`
SUSPECT_PEAK_ALL_GATES = {"bloom": 1 << 26, "halo": 1 << 28}  # reason -> bit at every dot's peak (flags_peak_all)
SUSPECT_BRIGHT_GATE_ARCSEC = 15.0  # reason `bright_star`: the bright fixed source this close (the warning goes to 30″)
SOURCE_BIT = 1 << 21           # catalogue stars / compact galaxies to AB ~19; at every dot's peak -> catalogue_star
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
ASTRO_MIN_MOTION_DOF = 8       # ... and degrees of freedom the motion keeps after the source × detector zero points:
                               # 2 × (used dots − zero points) − 2 (− 1 with parallax), over detectors seen in >= 2
                               # visits (astrometry.motion_dof). Hunt, Gaia stars with a fake pm (pull > 5) per fit:
                               # 1–2.6% under 6, 0.3% at 6–10, 0.06% from 20; 8 drops 65 of 214 fakes and 2.7% of the
                               # Gaia fits, no fast (> 200 mas/yr) Gaia star (the least free one has 8; 10 would lose it)
# The motion model's mode per source (skyshift_core.motion; 16 Nov): a Gaia DR3 parallax is a prior (pm+gaia_plx, the
# six-month parallax leak gone); without one, parallax is fitted only with >= 3 visits spanning both seasons (pm+plx),
# else not (pm, or pm_leak when the leak k is large: two visits in opposite seasons). Thresholds: core motion.PLX_*,
# LEAK_SMALL, GAIA_PLX_ERR_FLOOR_MAS.
ASTRO_GAIA_PLX_PRIOR = True
# Steadiness over the visits (motion.visit_steadiness): each visit's mean residual, error √(scale²/Σw + floor²) with w
# from pos_err_rel_arcsec; visit-level chi2_nu > core motion.UNSTEADY_CHI2_NU = `unsteady` (no straight-line motion;
# highpm reason `unsteady`). Needs >= 3 visits (2 leave no freedom).
ASTRO_UNSTEADY = True
ASTRO_VISIT_ERR_SCALE = 1.0     # on calib's pos_err_rel_arcsec, already honest: t_wise0855 Gaia stars' visit chi2_nu median
                                # 0.69 at 1.0 (chi2(2)/2's median 0.69; 0.28 at 1.6, 16 Nov)
ASTRO_VISIT_ERR_SCALE_RAW = 1.6  # on the dot files' own pos_err_arcsec (no calib): core motion.POS_ERR_SCALE
ASTRO_VISIT_FLOOR_ARCSEC = 0.02  # core motion.VISIT_FLOOR_ARCSEC
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
# Saturated dots (CALIB_SAT_BITS at the peak: centroided on the wings, left uncorrected by calib) sit 0.01–0.05 px
# along the detector's −x axis, the same in both visits (hunt, 14 Nov: D1 −0.038, D2 −0.021, D3 −0.035, D4 −0.017,
# D5 −0.014, D6 −0.016 px; |Δy| < 0.01). The 180° roll between visits turns that into fake RA motion: −110 mas/yr at
# G 8–10, −81 at G 10–11, −32 at G 11–12. The median offset in pixels per detector and depth bin, over the zone's
# saturated dots of quiet Gaia stars after the frame offset, is subtracted from every saturated dot (photo Jacobian).
ASTRO_SAT_OFFSET = True
ASTRO_SAT_MIN_DOTS = 200       # saturated reference dots needed for a detector's (or a depth bin's) offset; a bin with
                               # fewer takes the detector's "all" value, a detector with fewer gets none
# depth = the source's magnitude in the detector's band (crossids, Vega) + Vega→AB − the AB magnitude at which half the
# stars saturate there (pilot, root CLAUDE.md); < 0 = deeper. Hunt: D4 −0.032 px at depth −1.5…−1, −0.009 at 0…0.5,
# +0.009 beyond +2 (saturated peaks on faint sources: the G-based bins mixed those in).
ASTRO_SAT_DEPTH_BANDS = {1: ("j", 0.91, 10.5), 2: ("j", 0.91, 10.8), 3: ("k", 1.85, 11.0), 4: ("w1", 2.70, 11.1),
                         5: ("w2", 3.34, 9.6), 6: ("w2", 3.34, 9.0)}
ASTRO_SAT_DEPTH_BINS = (-2.5, -1.0, -0.5, 0.0, 0.5, 1.5)  # bin edges (mag; open-ended both ways); NaN depth: "all"
DETECTOR_PX = 2040             # SPHEREx images are 2040 × 2040
# Unsaturated dots (15 Nov; astrometry.pixel_offsets): after calib (per-photo distortion, at most quadratic) and the frame
# offsets, quiet Gaia stars' dots still sit off Gaia by amounts fixed in each detector's pixels, which the 180° roll turns
# into fake motion as for saturated dots. (1) A static map per detector (ASTRO_PIXEL_MAP): hunt rms 3–5 mpx in x, 3–9 in
# y on 128 px cells, up to 0.04 px; visit 1 and visit 2 measure the same map (r 0.87–1.0). A star sits at another x in
# each visit, so the map gives every star its own fake motion. (2) A brightness term (ASTRO_BRIGHT_OFFSET), following the
# dot's own level (bright_level: AB from flux_ujy − the detector's 50% saturation AB): the centroid moves along −x as a dot
# nears saturation below the flags (hunt: −0.3 to −4.5 mpx within 0.5 mag of it) and along +x at the faint end (+0.3 to
# +1.4 mpx at 5–7 mag below it); the median pm − Gaia − ϖ·k in RA was −34 mas/yr at G 9–10, −19 at G 11–12, +15 beyond
# G 18. Both are fitted on the zone's unsaturated dots of quiet Gaia stars after the frame offsets (alternately,
# ASTRO_PIXEL_ITER times) and subtracted from every unsaturated dot through its photo's Jacobian; centroid and ePSF
# positions separately. Saturated dots keep ASTRO_SAT_OFFSET. Hunt, fitted on half the stars, the other half:
# G 9–10 −34 → −8, G 10–11 −29 → −11, G 11–12 −19 → −3, G 12–13 −7 → +2, G > 18 +15 → +2 mas/yr.
ASTRO_PIXEL_MAP = True
ASTRO_MAP_CELL_PX = 128        # map cells (px); bilinear between cell centres, 0 in cells with too few dots
ASTRO_MAP_MIN_SNR = 20.0       # dots that measure the map
ASTRO_MAP_MIN_DOTS = 50        # ... per cell
ASTRO_BRIGHT_OFFSET = True
# bin edges (open-ended both ways) of the dot's own depth: AB from flux_ujy − the detector's 50% saturation AB above
ASTRO_BRIGHT_BINS = (-0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0)
ASTRO_BRIGHT_MIN_DOTS = 300    # reference dots needed for a brightness bin's offset (fewer: none)
ASTRO_PIXEL_ITER = 3
ASTRO_REPORT_SNR_BINS = (5.0, 10.0, 20.0, 50.0, 100.0, 1e9)  # median dot S/N bins of the report
ASTRO_REPORT_G_BINS = (0.0, 8.0, 10.0, 11.0, 12.0, 14.0, 17.0, 21.0)  # Gaia G bins of the report (saturated: G < ~12)
# highpm: "Planet X"-style search for nearby cold movers not in Gaia
HIGHPM_MIN_SEP_ARCSEC = 3.0    # a source seen in one visit only, with a counterpart in another visit
HIGHPM_MAX_SEP_ARCSEC = 120.0  # between these separations
HIGHPM_FLUX_RATIO_MAX = 2.0    # same-detector flux ratio within this factor (either way)
HIGHPM_SHAPE_MAX = 0.25        # rms of ln(flux ratio) across shared detectors (spectrum shape)
HIGHPM_MIN_COVER_PHOTOS = 2    # the other visit must have this many photos covering each position
HIGHPM_FIT_MIN_MAS_YR = 150.0  # fitted proper motion this fast ...
HIGHPM_FIT_MIN_SNR = 4.5       # ... and this significant also makes a candidate (method "fit"). Hunt (Gaia as truth):
                               # 5σ found 55 of the 104 detected fast Gaia stars; 4.5σ with the catalogue checks below 55
                               # (46 of the 49 misses are precision-limited: Gaia's motion / our pm_err < 5)
                               # Unconfirmed: 4.5σ is the lowest whose fp_control stays well under 1 expected new fake
                               # (hunt, dof-cut fits: 5.0 0.00, 4.5 0.30, 4.25 0.91, 4.0 2.13, 3.5 8.8, 3.0 122).
HIGHPM_FIT_MIN_SNR_GAIA = 3.0  # a fit whose fast motion Gaia confirms (pm + ϖ·k >= HIGHPM_FIT_MIN_MAS_YR, agreeing) is a
                               # candidate from this significance. Hunt: fast Gaia stars counted 63 (4.5) → 68 (4.0), 74
                               # (3.5), 77 (3.0), 80 (2.5); still stars given a random fast Gaia motion agree by chance at
                               # 3.2e-3 (3.0) and 1.5e-2 (2.5): × the 277 fits Gaia calls fast = 0.88 and 4.1 miscounted
HIGHPM_FIT_MIN_SNR_CATWISE = 3.5  # ... and one CatWISE confirms (no Gaia pm; CatWISE's own motion fast and significant,
                               # agreeing). The same control with the motion as CatWISE's: 9.4e-4 (3.5), 4.1e-3 (3.0) × the
                               # 235 such fits with no Gaia match = 0.22, 0.95 chance "new" rows on top of fp_control's 0.30
HIGHPM_CW_CONFIRM_SNR = 3.0    # CatWISE confirms a fast motion only when its own |pm| is this many of its errors (a
                               # low-significance fit "agrees" within 3σ with a still CatWISE source: that confirms nothing)
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
# the locus per S/N bin (lower edges): Gaia stars' √(a·b) / its S/N >= 20 value is 1.14–1.24 at S/N 8–12, 1.07–1.14 at
# 12–20, 0.96–0.99 above 80 (hunt box, 15 Nov); ap/PSF 1.03–1.10 at 8–12. One locus made faint stars "galaxies".
CLASSIFY_LOCUS_SNR_EDGES = (8.0, 12.0, 20.0, 40.0, 80.0)
CLASSIFY_SIZE_TOL = 0.10           # extended score: (size ratio − 1) / this
CLASSIFY_COG_TOL = 0.10            # ... (curve-of-growth ratio − 1) / this
CLASSIFY_CHI2_FACTOR = 2.0         # ... log(psf chi2 ratio) / log(this)
CLASSIFY_EXT_STAR = 1.0            # extended score <= this: point-like
CLASSIFY_EXT_GALAXY = 2.0          # extended score >= this (and no significant parallax): galaxy
CLASSIFY_PLX_SNR_STAR = 3.0        # Gaia parallax / error >= this: star, whatever the shape (no galaxy has a 3σ parallax)
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
PHOTOZ_GOOD_MIN_Z = 0.01       # good also needs z_best > this: z ≈ 0 is where stars and failed fits land
PHOTOZ_PAH_Z_MAX = 0.02        # candidates: classify's PAH 3.3 µm at z ≈ 0 counts only for a good photo-z below this
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
# The env var limits only a standalone `refcat` run: `all` (the combine) always builds every catalogue (16 Nov: the
# sky run's global SKYSHIFT_REFCAT_CATALOGS=gaia left 64 combined zones Gaia-only: 59 ecliptic, 5 featured targets).
# When IRSA's TAP fails for CatWISE2020 or 2MASS, the tile comes from AllWISE on S3 (and AllWISE's 2MASS
# associations); "0" turns that off.
REFCAT_S3_FALLBACK = os.environ.get("SKYSHIFT_REFCAT_S3_FALLBACK", "1") != "0"
# "1": CatWISE's and 2MASS's roles come from AllWISE on S3 first, IRSA's TAP only if S3 fails. For a many-zone rerun:
# IRSA's CatWISE took 60–444 s per 1° tile (14 and 16 Nov), an AllWISE partition (~3.4 deg²) ~33 s from a laptop.
# Costs: no CatWISE motions (AllWISE positions at ~2010.5), 2MASS only where AllWISE matched a source within 3″.
REFCAT_S3_FIRST = os.environ.get("SKYSHIFT_REFCAT_S3_FIRST", "0") == "1"
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
LABEL_MIN_VISITS = 2           # every label: its band window measured in this many visits (sources.parquet visit_bits)
# ice also needs reddened-star colours (Vega; 2MASS J, H, Ks and CatWISE W1, W2 from crossids, else our own spectrum's
# windows: J/H/K AB − 0.91/1.39/1.85, W1 − W2 = [3.4] − [4.6] AB + 0.64): H − K >= 0.6 and J − H >= 1.0 (A_V ≳ 9, where
# ice can be seen), on or above the reddening line J − H >= 1.7 (H − K) − 0.15 (below it: an infrared excess, galaxies
# and quasars), and W1 − W2 < 0.4 (quasars and AGN are redder). Hunt (15 Nov): 5 of 8 "ice" were galaxy-like.
LABEL_ICE_MIN_HK = 0.6
LABEL_ICE_MIN_JH = 1.0
LABEL_ICE_REDDENING = (1.7, -0.15)  # J − H >= slope × (H − K) + intercept
LABEL_ICE_MAX_W1W2 = 0.4
AB_TO_VEGA = {"j": -0.91, "h": -1.39, "k": -1.85, "w1_w2": 0.64}  # Vega = AB + this (W1 − W2: colour offset)
# candidates (the export): a Gaia star with BP − RP >= this (an M dwarf) is expected to show water vapour and CO 2.3 µm:
# those labels don't make it a spectral candidate (hunt: 42 of 43 nearby Gaia rows were textbook M dwarfs)
LABEL_MDWARF_BP_RP = 1.8
LABEL_MDWARF_EXPECTED = ("h2o_vapour", "co_23")
# candidates: a Gaia DR3 star (G < BLEND_MAX_G) within BLEND_ARCSEC of a source at its epoch (pm-propagated) counts as its
# Gaia match: crossid's 2.5″ radius misses blends (hunt: 8 of 54 no-Gaia spectral rows had one at 2.6–6″)
BLEND_ARCSEC = 6.0
BLEND_MAX_G = 20.0
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
# Parallax-aware checks (hunt, 14 Nov tables, Gaia DR3 as truth). Two visits half a year apart make parallax and proper
# motion one thing: the fit's pm = true pm + ϖ·(k_e, k_n), k = cov(t, parallax factor) / var(t) over the source's
# dots (hunt k_e ≈ −4.1, k_n ≈ +1.0 per mas). Gaia's motion is compared as pm + ϖ·k (real nearby stars were
# "gaia_pm_disagrees" before: 16 of the 55 recovered fast stars). Gaia-5p stars: the blind 4.5σ selection's tail is
# not Gaussian (pm_snr ≥ 5 on 584 of 211,690 still stars vs 0.8 expected): saturated stars (pmra ≈ −200…−300 mas/yr),
# one-session visits and coherent ~0.5″ shifts with chi2_nu ≈ 1. Table-level cuts (chi2_nu, n_dots, neighbours) remove
# < 30% of it; CatWISE's own 10-yr motion removes 95% (it agrees with Gaia for 100 of 101 fast stars).
HIGHPM_FIT_MIN_SESSIONS = 3    # fit: at least this many sessions in each visit it is seen in (one-session visits
                               # gave 6–12-dot fits at 5–10″/yr on Gaia still stars)
HIGHPM_NOCAT_MIN_SNR = 10.0    # fit with no Gaia or CatWISE motion to check it against: this significant (the still
                               # stars' tail without the CatWISE check: 1.9 per 100k at 10σ vs 276 per 100k at 5σ)
HIGHPM_CW_PLX_PER_PM = 0.25    # CatWISE check: our pm may exceed CatWISE's by a parallax leak ϖ·k with 0 ≤ ϖ ≤ this ×
                               # CatWISE's |pm| (tangential velocity ≥ 19 km/s); kept 140 of 143 Gaia-confirmed movers
HIGHPM_CW_PLX_PER_PM_SAT = 0.1 # ... for saturated sources (their centroid bias runs along RA like the leak): 27 Gaia
                               # fakes pass the blind route instead of 58, at the same 55 of 104 fast stars
HIGHPM_CW_REL_TOL = 0.2        # CatWISE agrees when the residual <= max(3σ (ours ⊕ CatWISE), this × |pm_cw|, 100 mas/yr)
HIGHPM_CW_SAT_W1_MAX = 8.0     # CatWISE's motion of a saturated source brighter than this W1 (Vega) neither checks nor
                               # disproves ours (hunt: fp_control's 2 fakes, G 7.9–8.0 still stars, passed on it)
# Deep checks before a vetted row counts as "new" (refcat stops at G 20; crossid's 2MASS match ignores motion). Hunt,
# 14 Nov: the one vetted "new" row was Gaia DR3 664214600677732864 (G 20.40, 2MASSW J0825196+211552, L7.5, ϖ 92.6).
HIGHPM_DEEP_CHECK = True
HIGHPM_DEEP_ARCSEC = 5.0       # Gaia DR3, any G (ESA TAP sync): within this of our position and of the segment our position
                               # takes moved to 2016.0 with our pm less a parallax leak ϖ·k, 0 <= ϖ <= HIGHPM_CW_PLX_PER_PM × |pm|
HIGHPM_DEEP_MATCH_ARCSEC = 3.0 # a deep Gaia source is our object when its own motion puts it this close to us at our epoch;
                               # a 2MASS source (refcat) when our motion (less such a leak) puts us this close at its epoch
HIGHPM_DEEP_TMASS_MAX_DMAG = 1.5  # ... and its J (AB) is within this of our D2 flux (AB), where both are known
HIGHPM_DEEP_BATCH = 20         # rows per ESA query (two circles each)
HIGHPM_DEEP_TIMEOUT_S = 60     # per ESA request; a failure keeps the rows "new" (report deep_gaia_checked false)
HIGHPM_STILL_PM_MAX = 30.0     # FP control: Gaia-5p stars slower than this (mas/yr) ...
HIGHPM_STILL_PLX_MAX = 5.0     # ... and nearer than 200 pc no more: their pm + leak < ~50 mas/yr, i.e. still for us
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
# suspects: the WISE catalogue (16 Nov: a reason and a warning, no longer a veto). refcat/<zone>.parquet without CatWISE
# rows (Gaia-only) -> AllWISE from IRSA's HEALPix Parquet on AWS S3 (anonymous): only the order-5 partitions (nside 32,
# nested, 3.36 deg²) the suspects' 6″ circles touch, columns ALLWISE_COLUMNS only (each file is ONE row group, so
# row-group predicate pushdown skips nothing: the saving is the partition choice and the column projection, ~1 MB of
# a ~22 MB file; 38–43 partitions for a 10° × 10° zone). CatWISE2020 is not on S3
# (s3://nasa-irsa-wise/ has wise/{allsky,allwise,cryo-3band,neowiser,postcryo} and unwise/neo7, 14 Nov).
# AllWISE positions are the non-moving fit at ~2010.5 (MJD ~55400), no proper motion applied: a source moving
# > ~400 mas/yr has left the 6″ circle by 2025 (Gaia, moved by its own pm, still matches those down to G < 20).
# "auto": AllWISE when the refcat has no CatWISE rows; "always": also instead of the TAP services when the refcat is
# missing; "off": never (a Gaia-only refcat then leaves catwise_checked false).
SUSPECTS_ALLWISE = os.environ.get("SKYSHIFT_SUSPECTS_ALLWISE", "auto")
ALLWISE_S3_BUCKET = "nasa-irsa-wise"
ALLWISE_S3_REGION = "us-west-2"
ALLWISE_S3_PREFIX = "wise/allwise/catalogs/p3as_psd/healpix_k5/wise-allwise.parquet"  # healpix_k0=<p//1024>/healpix_k5=<p>/part0.snappy.parquet
ALLWISE_NSIDE = 32             # partition order k = 5, nested
ALLWISE_ARCSEC = CATWISE_ARCSEC  # same veto radius as CatWISE
ALLWISE_EPOCH_MJD = 55400.0    # ~2010.5: documentation only, positions are used as they are
ALLWISE_COLUMNS = ("ra", "dec", "w1mpro", "w2mpro")  # magnitudes for catalogue_star (16 Nov)
# a partition cached before 16 Nov (ra, dec only: 1,752 files, 1.6 GB in the bucket) is read again from S3 for the
# magnitudes (the catalogue reasons compare them with the mover's); SKYSHIFT_ALLWISE_REREAD_MAGS=0 uses it as it is
# (magnitudes NaN: every AllWISE source within 6″ is then a reason, as before 16 Nov)
ALLWISE_REREAD_MAGS = os.environ.get("SKYSHIFT_ALLWISE_REREAD_MAGS", "1") == "1"
ALLWISE_WORKERS = 8            # partitions read in parallel
ALLWISE_TIMEOUT_S = 120.0      # per S3 request
ALLWISE_CACHE_DIR = "cache/allwise/k5"  # p<pixel:05d>.parquet (ALLWISE_COLUMNS) at the store root: neighbouring zones and reruns reuse it
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

# --- vetting: gates, grades and reasons on the suspects (vet.py, suspects.py) ---------------------
# Run on the suspects only (every mover but the certain removals: a known body, a planet, a hot pixel; 16 Nov), never
# before link's pairing, so known-asteroid recall (score) is untouched. A suspect that fails a gate stays in
# suspects.json with grade C and its reasons. Until 16 Nov the flag bits, a bright source within 30″, SOURCE at every
# peak and Gaia/WISE within 6″ removed movers first. Hunt zone (14 Nov, 228 suspects): recurring detector pixels 94, within 6 px of the edge 70, sharp/poor PSF 22,
# masked bit at the peak 13, one D6 column 2; ~4 unexplained. link carries each mover's dots as lists (dot_obs, dot_det,
# dot_x, dot_y, dot_mjd, dot_flux, dot_snr, dot_size, dot_chi2, dot_fpk, dot_row) and writes freedots.parquet (every
# free dot: obs_id, detector, x, y, ra, dec) for the zone-level hot-pixel and column maps.
VET_DETECTOR_PX = 2040
VET_EDGE_PX = 6.0              # a dot within 6 px of the detector edge (x or y < 6 or > 2033)
VET_HOT_PX = 1.0               # same detector pixel: |Δx| and |Δy| <= 1 px
VET_HOT_MIN_EXP_MOVERS = 3     # hot pixel: the zone's movers' dots on that pixel in >= 3 exposures ...
VET_HOT_MIN_EXP_FREE = 5       # ... or its free dots in >= 5 (hunt: 133k free dots per detector, 0.29 per 3×3 px: >= 3 would
                               # hit 3.4% of real dots by chance, >= 5 0.01%; the five worst pixels made 10–14 suspects each)
VET_COLUMN_PX = 0.5            # bad column: free dots of one exposure and detector within ±0.5 px in x ...
VET_COLUMN_MIN_DOTS = 3        # ... at least this many (hunt: ~216 free dots per photo, 0.1 per column: 0.5% by chance)
# ... and more than chance gives at the photo's free-dot density: Poisson tail <= this (16 Nov). Ecliptic photos hold
# p50 65–94, p90 680–780 free dots: >= 3 alone hit 12.5% / 11.9% of the real asteroids (MPC within 5″) of ecl04 / ecl11
# and 10.2% / 19.5% of the rest; with the tail <= 0.1% 0.3% / 0.4% vs 0.3% / 9.3%. At the hunt's ~216 it needs 4.
VET_COLUMN_CHANCE_P = 1e-3
# sharp: √(a·b) below VET_SHARP_FRAC[det] × the detector's stellar √(a·b) at that S/N, with OUTLIER (19) at the peak.
# OUTLIER alone is set at the peak of 52–69% of MPC-matched movers' dots, so it never vetoes on its own. 150 MPC-matched
# hunt movers (643 dots): ratio p1 0.69, p5 0.78; with OUTLIER, 10 dots at 0.62–0.69, all in D1–D3, none below 0.6. The
# sharp MWIR artefacts grow over the mission (QA: bright D6 dots under 0.7× stellar 1% → 44% from visit 1 to 3), so
# D4–D6 keep 0.7 and D1–D3 take 0.6: no real asteroid of the sample is gated, 3 fewer hunt suspects than 0.7 everywhere.
VET_SHARP_FRAC = {1: 0.6, 2: 0.6, 3: 0.6, 4: 0.7, 5: 0.7, 6: 0.7}
VET_OUTLIER_BIT = 1 << 19
# very_sharp: √(a·b) below 0.4 × the stellar value at S/N >= 15, OUTLIER or not: no point source is that sharp (a star's
# 5th percentile is ~0.8×). Hunt: suspect #3, a 1.0″ dot at S/N 85 (35,000 µJy) paired with a 500 µJy partner in D6.
VET_VERY_SHARP_FRAC = 0.4
VET_VERY_SHARP_MIN_SNR = 15.0
# median √(a_arcsec·b_arcsec) of unflagged dots per detector in S/N bins [5,7,10,15,20,30,50,100,∞) (94 hunt photos, 1.87 M dots)
VET_STAR_SNR_EDGES = (5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)
VET_STAR_SIZE_ARCSEC = {
    1: (4.65, 4.40, 4.08, 3.83, 3.63, 3.45, 3.32, 3.26),
    2: (4.64, 4.42, 4.14, 3.90, 3.70, 3.52, 3.37, 3.29),
    3: (4.72, 4.51, 4.22, 3.99, 3.80, 3.61, 3.47, 3.39),
    4: (4.97, 4.81, 4.54, 4.31, 4.13, 3.97, 3.87, 3.86),
    5: (4.99, 4.79, 4.51, 4.24, 4.09, 3.96, 3.89, 3.89),
    6: (5.04, 4.88, 4.60, 4.36, 4.22, 4.12, 4.08, 4.13),
}
# masked bit at the peak of any dot: MASK_BITS (contracts/flags.md) without OVERFLOW/NONLINEAR (saturation is no defect)
VET_PEAK_BITS = 0x0A02CEC7 & ~0x00008002
VET_FLUX_RATIO_MAX = 4.0       # brightest / faintest dot within one detector (cosmic ray + a 5–20× fainter partner)
VET_QA_NAME = "qa_photos.parquet"  # results/<zone>/qa_photos.parquet (qa.py): obs_id, detector, grade green|amber|red, reasons
VET_CATALOG_ARCSEC = 6.0       # a 2MASS PSC source (refcat) within this: catalogued (`2mass`), grade C
# AllWISE within 6″ (`allwise`, grade C) also where CatWISE2020 is the zone's WISE catalogue: hunt, 5 of the 28
# suspects that passed every gate on 14 Nov had one. Read from S3 as for a Gaia-only refcat (cache/allwise/k5 at the
# root); a failure only skips the reason (stats `allwise`). SKYSHIFT_VET_ALLWISE=0: off.
VET_ALLWISE = os.environ.get("SKYSHIFT_VET_ALLWISE", "1") != "0"
VET_NEAR_SOURCE_ARCSEC = 10.0  # nearest refcat (Gaia/CatWISE/2MASS) or fixed source within this:
                               # near_source_arcsec, a blend/wing warning (not a gate)
VET_MOTION_SIGMA = 5.0         # grade A: B and on a tracklets track with rate_snr >= this (tracklets.parquet) ...
VET_A_MIN_SESSIONS = 2         # ... and movers in >= this many sessions (tracks need TRACK_MIN_SESSIONS = 3 anyway)
VET_GRADE_RANK = {"A": 0, "B": 1, "C": 2}
# reasons are listed in this order; not_vetted = the movers table has no dot lists (link before 14 Nov evening)
VET_GATES = ("not_vetted", "hot_pixel", "bad_column", "edge", "masked_peak", "sharp_outlier", "very_sharp", "flux_ratio",
             "red_photo", "bloom", "halo", "bright_star", "gaia", "catwise", "2mass", "allwise")
VET_CATALOGS = ("gaia", "catwise", "2mass", "allwise")  # the catalogue reasons among VET_GATES

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

# --- fragments: bright-star ring fragments, saturated stars, blended pairs (fragments.py) --------------------------
# IRSA flags 30–180 px around a saturated core TRANSIENT, SUR_ERROR and BLOOM; the mask zeroes the island and sep finds
# the wings as a ring of "fixed sources" without a catalogue entry (hunt review, 14 Nov: 16 per G < 6 star within 60″,
# 1.5 at G 8–9). Their member dots carry BLOOM in 98% (Gaia stars' dots: 0%). Two stars < 8″ apart merge into one source
# that crossid's 2.5″ may match to neither. Link's release (FIXED_RELEASE_ARCSEC) can regroup a star's far dots into a
# second source ~2.7″ off it (hunt cross-check, 14 Nov: 200 Gaia stars with two sources within 3″).
# Tags (first that applies): bright_fragment, duplicate, saturated_star, blend.
FRAGMENTS_BLOOM_FRAC = 0.5         # BLOOM (3×3 `flags`) on more than this share of the member dots ...
FRAGMENTS_MATCH_ARCSEC = 3.0       # ... and no Gaia / 2MASS entry this close, nor a Gaia star it is the kept source of
#                                    (FRAGMENTS_STAR_ARCSEC): bright_fragment; with either: saturated_star
FRAGMENTS_MATCH_CATALOGUES = ("gaia", "tmass")  # the catalogues of that test (match_dist_arcsec)
# Every distance from a source (catalogue, bright neighbour, blend count, a fragment's parent) is also measured from link's
# position (fixed.parquet) where it lies more than this from the source's (sources.parquet); the smaller wins: a
# saturated star's spectrum position can sit 5–9″ off it, a release-split piece's link position 6–7″ off its spectrum's.
FRAGMENTS_LINK_MIN_OFFSET_ARCSEC = 0.5
# Which source is which Gaia star: each source's home is the nearest Gaia star (moved to its epoch) within that star's
# radius, by G: (G below, radius ″), first that applies, else FRAGMENTS_MATCH_ARCSEC. A saturated star's position sits
# further off (hunt: a G 7.2 star's nearest source 4.8″, a G 10.6 one 3.6″), and so does a fainter star's inside a
# bright star's glow (2.9–3.8″ at G 13.6–16.7). Each star keeps one home source (crossid's match, else the most member
# dots within its duplicate radius, else the nearest): that one is the star (never bright_fragment) ...
FRAGMENTS_STAR_ARCSEC = ((11.0, 6.0), (18.0, 4.0))
# ... though beyond FRAGMENTS_MATCH_ARCSEC only with at least this many member dots, by G (same form; else 0): a ring
# piece has 3–8, a star inside a glow 18–179 (hunt); the 1–9-dot pieces near faint stars stay fragments (3 were orphans)
FRAGMENTS_STAR_MIN_DOTS = ((11.0, 0), (18.0, 10))
# ... and its other home sources within the duplicate radius (same form; else FRAGMENTS_MATCH_ARCSEC) are `duplicate` of
# it. Fainter than G 11 that stays 3″ (< half the PSF's FWHM: one object): at 3–4″ the bigger source is often a
# neighbour's, and calling the star's own small source its duplicate would read two stars as one (hunt: 137 stars) ...
FRAGMENTS_DUP_ARCSEC = ((11.0, 6.0),)
FRAGMENTS_DUP_MAX_MOVE_ARCSEC = 1.0  # ... when the star moved less than this between the two sources' epochs (a fast star's
#                                      two visits are two sources: highpm pairs them) ...
FRAGMENTS_DUP_OWN_MARGIN_ARCSEC = 1.0  # ... and no catalogue entry is this much nearer to it than the star (its own object),
#                                        and no other Gaia star whose radius holds it lacks a source of its own
FRAGMENTS_CAT_SEARCH_ARCSEC = 15.0  # cat_dist_arcsec: nearest Gaia / 2MASS / CatWISE entry searched this far (NaN beyond)
FRAGMENTS_BRIGHT_G = 10.0          # bright_neighbour: Gaia G brighter than this ...
FRAGMENTS_BRIGHT_K = 7.0           # ... or 2MASS K (G misses red giants: K 3.4 at G 10.2 in the hunt) ...
FRAGMENTS_BRIGHT_W1 = 7.0          # ... or CatWISE W1 ...
FRAGMENTS_BRIGHT_ARCSEC = 120.0    # ... searched this far (NaN beyond)
FRAGMENTS_BLEND_ARCSEC = 8.0       # blend: at least FRAGMENTS_BLEND_MIN_GAIA Gaia stars this close (PSF FWHM 7.5″) ...
FRAGMENTS_BLEND_G_MAX = 19.0       # ... brighter than this (18 before 15 Nov missed two similar G 18–19 stars)
FRAGMENTS_BLEND_MIN_GAIA = 2
FRAGMENTS_PARENT_G = 12.0          # a bright_fragment's parent: the brightest Gaia star brighter than this (nearer on a tie) ...
FRAGMENTS_PARENT_ARCSEC = 60.0     # ... this close (hunt: 2,226 of 2,234 have one; the ring sits ~10″ out at G 8–11, ~30″ at G < 6)

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

# --- tracklets: movers linked across the sessions of a visit, missed sightings recovered (tracklets.py) -------------
# link's movers live inside one session (2-5 exposures over minutes): too short to show main-belt motion at S/N 7-9.
# tracklets links movers of different sessions of one visit into tracks (positions on a local tangent plane, linear
# motion, quadratic once a local fit spans >= TRACK_QUAD_MIN_SPAN_H with >= 4 members), measures each mover's rate
# from the track, then looks in the visit's other sessions at the predicted positions for link's unpaired dots
# (results/<zone>/nonfixed_dots.parquet) and keeps them as recovered detections. score counts a known sighting found when
# a mover or a recovered dot matches it. Hunt (known asteroids' predicted session positions, V < 18): linear fits leave
# <= 1.9″ (99%) over 1 day, 14″ over 3 days; quadratic <= 1.9″ over 3 days (SPHEREx's orbital parallax included).
TRACK_RATE_MIN_ARCSEC_H = 0.5    # slower: not linked (stars, galaxies, fixed junk)
TRACK_RATE_MAX_ARCSEC_H = 150.0  # link's pair rule allows ~146″/h (3″ + 1″/min over 2.1 min)
TRACK_SEED_MAX_GAP_H = 36.0      # seed pairs: movers of two sessions at most this far apart
TRACK_TRIPLET_WINDOW_H = 36.0    # a seed is tested in sessions up to this far outside its two times
TRACK_MAX_GAP_H = 60.0           # a track grows across gaps up to this long (no detection in between)
TRACK_FIT_WINDOW_H = 48.0        # local fits use the members within this of the time they predict (at least 2)
TRACK_QUAD_MIN_SPAN_H = 24.0     # a local fit with >= 4 members spanning this long is quadratic, else linear
TRACK_ACCEL_ARCSEC_H2 = 0.04     # first pass: curvature allowance of a linear prediction, ½ a |(t-t1)(t-t2)| (hunt:
                                 # known asteroids' apparent acceleration 0.028″/h² median, 0.039 at 99%)
TRACK_ACCEL_MIN_SESSIONS = 5     # second pass: the visit's median acceleration vector from first-pass tracks this long
TRACK_ACCEL_MIN_SPAN_H = 36.0    # ... over at least this many hours (a quadratic fit within this window)
TRACK_ACCEL_MIN_TRACKS = 5       # ... at least this many of them, else the first pass stands
TRACK_ACCEL_TOL_ARCSEC_H2 = 0.016  # ... and the allowance around it (hunt: 99% of known asteroids within 0.016)
TRACK_JERK_ARCSEC_H3 = 0.0005    # allowance of a quadratic prediction: j dt³ / 6
TRACK_SYS_ARCSEC = 0.8           # per-axis systematic added to every mover's error (parallax, model; hunt residuals)
TRACK_POS_K = 2.2                # mover per-axis error: sqrt((K × PSF σ / snr)² + floor²) / sqrt(n_dots): the raw
TRACK_POS_FLOOR_ARCSEC = 0.43    # pos_err is 1.4-1.8× too small; single-dot floor ~0.43″ against Gaia
TRACK_NSIGMA = 4.0               # match radius = NSIGMA × prediction σ + curvature allowance, within the limits below
TRACK_R_MIN_ARCSEC = 3.0
TRACK_R_MAX_ARCSEC = 30.0
TRACK_MIN_SESSIONS = 3           # a tracklet: movers in >= 3 sessions (2 sessions alone are any pair in the rate range)
# Fast tracks (16 Nov; tracklets.fast_tracks): above TRACK_RATE_MAX_ARCSEC_H up to this, two movers of sessions <= 36 h
# apart link when the chord between them agrees with both their own within-session velocities (a fast mover shows its
# motion inside its session: 14″ per 2.1 min at 400″/h). None = off.
TRACK_FAST_RATE_MAX_ARCSEC_H = 900.0    # link's fast pairing limit (PAIR_FAST_RATE_ARCSEC_PER_MIN × 60)
TRACK_FAST_OWN_MIN_ARCSEC_H = 100.0     # a mover's own speed at least this ...
TRACK_FAST_OWN_SNR = 4.0                # ... measured at >= this × its σ
TRACK_FAST_VEL_FRAC = 0.3               # chord vs own velocity: within this × the speed or NSIGMA × σ, the larger
TRACK_FAST_VEL_NSIGMA = 3.0
TRACK_FAST_MIN_SESSIONS = 2
TRACK_SHORT_MAX_SPAN_H = 36.0    # ... and with exactly 3, over at most this long (hunt control: chance 3-session tracks
                                 # all spanned 37-72 h, their third mover far out on the extrapolated path)
TRACK_VISIT_MAX_S = 30.0         # time budget of one linking pass over a visit (hunt: ~1 s); then the weakest seeds are skipped
# recovery: link's dots in no fixed group (results/<zone>/nonfixed_dots.parquet: not in a fixed group, elong <= MOVER_MAX_ELONG).
# Hunt (V < 16, free dots rebuilt from the missed units' photos): recall 90.4% (movers) → 93.0% with 24 h and no
# near-fixed dots, 94.7% with the settings below; control 0; the dots moved 120″ gave 3 chance track-sessions vs 56.
# Most of the rest pass within 4″ of a star: their dot is in the star's fixed group, not a free dot.
TRACK_RECOVER = True
TRACK_RECOVER_EXT_H = 60.0       # sessions up to this far before the first / after the last member are searched too
TRACK_RECOVER_R_MIN_ARCSEC = 3.0
TRACK_RECOVER_R_MAX_ARCSEC = 8.0
TRACK_RECOVER_NEAR_FIXED = True  # also dots 4-6″ from a fixed source (link's veto zone; not in its group)
TRACK_RECOVER_IN_FIXED = True    # also the fixed groups' member dots that sit off their star (link off_star_members)

# --- stack (stack.py, 15 Nov night): detect on per-visit stacks, then a joint ePSF fit in every photo ---------------
# Single photos (6.15″ px, FWHM 7.5″) miss sources below their limit, 8–15″ pairs and stars in a bright star's glow.
# Each visit's ~18 dithered photos per detector (~110 over the six) are resampled onto one tangent-plane grid.
STACK_ZONES = DOTS_FINE_ZONES    # the zones `stack` runs on by default (the five featured targets)
STACK_PIX_ARCSEC = 2.0           # grid pixel of the stacks
STACK_NATIVE_PIX_ARCSEC = 6.152  # SPHEREx pixel (the matched filter's ePSF on the grid)
STACK_MARGIN_ARCSEC = 60.0       # grid beyond the zone box: sources there are fitted (neighbours) but not written
STACK_PAD_PX = 40                # native pixels read beyond the grid (the photo's background mesh, the ePSF stamps)
STACK_BKG_BOX = 32               # photo background mesh (native px; core.detect's)
STACK_GRID_BKG_BOX = 32          # stack background mesh (grid px = 64″): a bright star's glow out of the detection maps
STACK_MIN_PHOTOS = 3             # grid pixels covered by fewer photos of a stack are not searched in it
STACK_DETECT_SNR = 5.0           # a peak of a matched-filter S/N map (ePSF of the detector), noise normalised on the map
STACK_EDGE_PX = 3                # peaks this close to the grid edge are skipped
STACK_MERGE_ARCSEC = 2.5         # peaks of different stacks within this are one source
STACK_RESID_PASSES = 1           # detect again on the residual stacks (every source's ePSF fit subtracted in each photo)
STACK_RESID_PSF_ERR = 0.05       # residual maps: noise² += (this × model)², the ePSF's own misfit near bright sources
STACK_RESID_MIN_SEP_ARCSEC = 4.0  # a residual peak closer than this to a source is that source's misfit
# Residual maps: every source's leftover counts as noise, F × E(r): E = the STACK_WING_PCT percentile of
# |residual flux| / F at distance r around the STACK_WING_MAX_STARS brightest sources (S/N >= STACK_WING_MIN_SNR) of
# that stack, out to STACK_WING_RADIUS_ARCSEC (the ePSF stops at 20″; the misfit core and the wing beyond are measured).
# Without it (proto box A, 15 Nov night): 575 of 1,321 residual detections had no catalogue entry, piled at < 8″ and
# 20–30″ (the ePSF's edge) from bright sources.
STACK_WING_MIN_SNR = 300.0
STACK_WING_MAX_STARS = 300
STACK_WING_PCT = 80.0
STACK_WING_RADIUS_ARCSEC = 60.0
STACK_STAMP_HALF = 3             # ePSF stamp 7 × 7 native px (the ePSF is 0 beyond ±3.2 px)
STACK_FIT_TILE_PX = 256          # a photo's block is fitted in tiles of this many native px (bounded sparse solves) ...
STACK_FIT_TILE_MARGIN_PX = 8     # ... each with the sources this far outside it as neighbours
STACK_FREE_MIN_SNR = 5.0         # free-position fit for a source in a photo where its fixed fit has this S/N
STACK_PRIOR_STACK_ARCSEC = 0.6   # free fit: prior σ of the position around the stack position
STACK_PRIOR_GAIA_ARCSEC = 0.15   # ... around Gaia DR3 moved to the photo's time (pm + parallax)
STACK_FREE_MAX_SHIFT_PX = 1.0    # a free fit moving further than this is not kept (free_ok false)
STACK_CG_ITER = 300              # joint flux fit: conjugate gradients on the normal equations (sparse, all sources)
STACK_CG_RTOL = 1e-6
STACK_RIDGE_REL = 1e-5           # ... + this × the diagonal (two sources at one spot share the flux instead of diverging)
STACK_DEDUPE_ARCSEC = 2.0        # after refining, sources this close are one (the stronger stays)
STACK_KEEP_SNR = 5.0             # a source is kept with forced S/N >= this in at least one stack (detector × visit,
                                 # visit or all), from its photo fluxes (inverse-variance mean)
STACK_NEG_SNR = 5.0              # ... and no detector below −this (a misfit piece of a bright star: proto box A, 57 of
                                 # 62 such residual detections had no catalogue entry)
STACK_TRANSIENT_SHARE = 0.5      # one photo holds more than this share of a source's χ² (S/N²): `transient` (a mover)
STACK_GAIA_MATCH_ARCSEC = 2.0    # catalogue match at the source's epoch (Gaia DR3 moved with pm)
STACK_CAT_MATCH_ARCSEC = 3.0     # CatWISE2020 and 2MASS (moved with the row's pm when it has one)
STACK_SINGLE_MATCH_ARCSEC = 3.0  # a single-photo source (results/<zone>/sources.parquet) this close is the same source
STACK_BRIGHT_G = 12.0            # bright star context: nearest Gaia G < this ...
STACK_BRIGHT_RADIUS_ARCSEC = 120.0  # ... within this
# artifact_zone: an uncatalogued source < CORE or RING″ from a source with S/N >= MIN_SNR and >= RATIO × its own (the
# misfit core and the ePSF's edge at 20″). Proto boxes: 54 of 696 uncatalogued sources < 8″, 125 at 20–30″.
STACK_ARTIFACT_MIN_SNR = 100.0
STACK_ARTIFACT_RATIO = 5.0
STACK_ARTIFACT_RADIUS_ARCSEC = 60.0
STACK_ARTIFACT_CORE_ARCSEC = 8.0
STACK_ARTIFACT_RING_ARCSEC = (20.0, 30.0)
STACK_TASK_PHOTOS = 8            # photos per worker task (one detector × visit, so the worker sums its own stack)
# Glow model (16 Nov; core.glow): a bright star's wings and halo stay above the sky to ~25–30 px (Barnard's Star D1:
# 0.5 MJy/sr at 10 px, 0.16 at 15–20 px, sky noise 0.04); detection cut them into "sources" (Barnard frames.json:
# stack-only sources 20.8 / 6.4 / 6.7 / 5.0 per arcmin² per epoch within 30 / 30–60 / 60–90 / 90–120″ vs 1.4 at
# 120–180″). Per photo: saturated cores and peaks >= GLOW_MIN_PEAK_SNR are modelled (ePSF where the unmasked wings
# constrain it + polar medians of the rest out to GLOW_R_MAX_PX), subtracted, and the detection noise is scaled by the
# residual's local scatter over the field's (noise factor k >= 1).
GLOW_R_MAX_PX = 40.0
GLOW_MIN_PEAK_SNR = 300.0
GLOW_MAX_STARS = 400
GLOW_R_KEEP_PX = 3.5           # each star's own pixels (beyond the ePSF's ±3.2 px; a saturated core + 1.5 px) stay as observed
GLOW_SELF_PX = 2.0             # dots: a modelled star's own dot (nearest within max(this, core radius + 1) px) comes
                               # from the unmodified image; residual dots that close are dropped
# Stack: each photo block's glow (outside STACK_GLOW_R_KEEP_PX of each star: its ePSF fit sees it as observed) is
# subtracted at load and its noise scaled by k; then each S/N map is divided by its own local scatter around bright
# stars (holes with a bright rim, or peaks >= STACK_GLOW_MIN_SNR), polar bins of >= STACK_GLOW_BIN_MIN_PX grid px out to
# STACK_GLOW_R_MAX_ARCSEC (the leftovers are coherent over a visit's photos: the formal noise underrates them).
STACK_GLOW = True
STACK_GLOW_R_KEEP_PX = 3.5
STACK_GLOW_CORE_PAD_PX = None  # Glow.outside: STACK_GLOW_R_KEEP_PX for saturated stars too (their unmasked rim is modelled; rim_peaks stands for the star)
STACK_GLOW_MIN_SNR = 300.0
STACK_GLOW_RIM_ARCSEC = 12.0
STACK_GLOW_HOLE_MAX_ARCSEC = 120.0
STACK_GLOW_MERGE_ARCSEC = 20.0
STACK_GLOW_R_MAX_ARCSEC = 250.0
STACK_GLOW_BIN_MIN_PX = 300
STACK_GLOW_RING_MIN_ARCSEC = 4.0
GLOW_MAX_SECTORS = 12
STACK_GLOW_HOLE_RIM_SNR = 100.0  # a hole with no Gaia star in it is a saturated star when its rim reaches this S/N
STACK_GLOW_HOLE_MIN_ARCSEC = 4.0  # smaller holes (a few masked pixels) are not a saturated core
STACK_GLOW_GAIA_G = 11.0         # Gaia stars this bright confirm a hole as a saturated star's core
STACK_GLOW_PEAK_R_MAX_ARCSEC = 60.0  # an unsaturated bright peak's local scatter is measured this far out
