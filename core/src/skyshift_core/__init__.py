"""SkyShift shared science library (P1). Imported by server/ and pipeline/.

    from skyshift_core import open_level2, bad_pixel_mask, visits, sessions, detect, measure_shift

Units follow the contracts: degrees, arcsec, um, MJD (UTC, MJD-AVG), uJy; images in MJy/sr.
Pixels are 0-based numpy (x = column, y = row).
"""

from .astrometry import (
    GAIA_EPOCH_MJD,
    ShiftError,
    ShiftResult,
    measure_shift,
    mjd_to_jyear,
    parallax_offset_arcsec,
    pixel_to_sky,
    position_angle_deg,
    propagate,
    select_reference_stars,
    sky_to_pixel,
    tangent_deproject,
    tangent_offset_arcsec,
)
from .detect import DETECTION_DTYPE, background, build_mask, centroid, detect
from .flags import (
    MASK_BITS,
    NEVER_BITS,
    RECORD_BITS,
    SAT_BITS,
    bad_pixel_mask,
    bit_names,
    flag_frac,
    flags_under,
    is_saturated_core,
    record_bits,
    sat_frac,
    saturated_mask,
)
from .level2 import Level2, WaveTable, detector_from_bandpass, open_level2
from .photometry import Photometry, aperture_photometry, pixel_solid_angle_sr, to_ujy, ujy_per_mjysr
from .visits import Visit, sessions, summarize_visits, visits

__all__ = [
    "GAIA_EPOCH_MJD", "ShiftError", "ShiftResult", "measure_shift", "mjd_to_jyear",
    "parallax_offset_arcsec", "pixel_to_sky", "position_angle_deg", "propagate",
    "select_reference_stars", "sky_to_pixel", "tangent_deproject", "tangent_offset_arcsec",
    "DETECTION_DTYPE", "background", "build_mask", "centroid", "detect",
    "MASK_BITS", "NEVER_BITS", "RECORD_BITS", "SAT_BITS", "bad_pixel_mask", "bit_names",
    "flag_frac", "flags_under", "is_saturated_core", "record_bits", "sat_frac", "saturated_mask",
    "Level2", "WaveTable", "detector_from_bandpass", "open_level2",
    "Photometry", "aperture_photometry", "pixel_solid_angle_sr", "to_ujy", "ujy_per_mjysr",
    "Visit", "sessions", "summarize_visits", "visits",
]
