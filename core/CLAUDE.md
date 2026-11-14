# core/CLAUDE.md
Owner: P1. Python package skyshift_core, managed with uv.
Shared science code, imported by server/ and pipeline/. No web or cloud code here.
- visits(mjds), sessions(mjds): the visit rule in contracts/api.md.
- bad_pixel_mask(flags): exactly the 'mask' bits in contracts/flags.md. Never SOURCE or FULLSAMPLE.
- detect, centroid (wings when saturated), shift between visit stacks, aperture photometry in µJy.
- Time = MJD-AVG (UTC). Detector = energy_bandpassname or header DETECTOR, never the file name. IMAGE in MJy/sr; 1 MJy/sr = 889 µJy per 6.15″ pixel.
- Pure functions: numpy in, numpy or dicts out. Tests: uv run pytest on fixtures/fits/.
- Header facts from the first file: docs/research/2026-11-14-first-photo-facts.md (DETECTOR 1–6, 6.152″/px, PSF FWHM 7.53″, FLAGS is tile-compressed).

## Modules (src/skyshift_core/)
- flags: bit constants, MASK_BITS/RECORD_BITS/NEVER_BITS/SAT_BITS, bad_pixel_mask, record_bits,
  is_saturated_core, flags_under (dot `flags` column), flag_frac, sat_frac.
- level2: open_level2(path_or_https_url, variance=False, region=(x0,x1,y0,y1)) -> Level2
  (data, flags, variance, wcs, mjd_avg, detector, obsid, wavelength(x,y), wavelength_range()).
  `region` reads only that block (range reads over HTTPS).
- visits: visits(mjds), sessions(mjds), summarize_visits(mjds).
- detect: detect(data, flags, variance=) -> structured array (x, y, flux MJy/sr summed, flux_err, snr, ...);
  centroid(data, x, y, flags=, method="auto"|"window"|"fit"). Noise = max(sqrt(VARIANCE), background rms).
- astrometry: pixel_to_sky, sky_to_pixel, propagate (Gaia J2016.0 -> MJD, optional parallax),
  select_reference_stars, measure_shift (V2 + V3) -> ShiftResult.to_measure() = api `measure`.
- photometry: pixel_solid_angle_sr, ujy_per_mjysr (from the WCS, ~889.7), aperture_photometry -> uJy.

## Tests and fixtures
- `uv run pytest` (network test: `SKYSHIFT_NETWORK=1 uv run pytest -k url`).
- fixtures/fits/cutout_2025W18_1B_0074_2D1.* made by `uv run python scripts/make_cutout_fixture.py`
  (+ `--gaia` for the Gaia list). The source file stays outside the repo.
