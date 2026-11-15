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
  `region` reads only that block (range reads over HTTPS). EPSF (epsf=True): evaluate / evaluate_grad (value and
  derivatives), stamps / stamps_at (separable, fixed pixel grid); one cubic spline per zone.
- visits: visits(mjds), sessions(mjds), summarize_visits(mjds).
- detect: detect(data, flags, variance=) -> structured array (x, y, flux MJy/sr summed, flux_err, snr, ...);
  centroid(data, x, y, flags=, method="auto"|"window"|"fit"). Noise = max(sqrt(VARIANCE), background rms).
  detection_snr(data, x, y) (PSF-matched S/N, noise measured on the frame), no_data_near(data, x, y, r)
  (missing sky, not a saturated core's hole).
- astrometry: pixel_to_sky, sky_to_pixel, propagate (Gaia J2016.0 -> MJD, optional parallax),
  select_reference_stars, measure_shift (V2 + V3) -> ShiftResult.to_measure() = api `measure`.
  measure_shift's detection guard raises ShiftError(reason) unless one source is clearly seen in both
  stacks: S/N >= 5 in each, no missing sky within 4 px, centroids within max(FWHM 7.53", 12"/yr x dt)
  + 3 sigma of each other (or of the predicted motion). Reasons: not_found, not_detected, edge,
  different_sources.
- photometry: pixel_solid_angle_sr, ujy_per_mjysr (from the WCS, ~889.7), aperture_photometry -> uJy.
- known_index: known asteroids and comets near (ra, dec, r) at given MJDs, fast (live API). load_index(npz) is
  numpy only; candidates() = two-body coarse cut with per-object margins; known_near() = kete n-body on the
  candidates -> api `known[]` entries (source "mpc"). build_index / `python -m skyshift_core.known_index` makes the
  .npz from the MPC snapshot parquets. kete is the optional extra `known` (`uv sync --extra known`).
  Design and numbers: docs/research/2026-11-14-live-known-mpc.md.
- planets: near_planet(ra, dec, mjd) -> (planet, sep_arcsec) | None, near_planets (arrays), planet_label ("Saturn" within
  SELF_ARCSEC, else "a moon of Saturn"), positions(mjd). Mars, Jupiter, Saturn, Uranus, Neptune with their moons and
  glare (radii *_RADIUS_ARCMIN 60/80/45/5/3, justified in the module): MPC files hold none. astropy's built-in ephemeris
  (no network), geocentric astrometric: vs JPL Horizons ≤ 20″ (Jupiter, Saturn), ≤ 7″ (Mars, Uranus), ≤ 4″ (Neptune).

## Tests and fixtures
- `uv run pytest` (network test: `SKYSHIFT_NETWORK=1 uv run pytest -k url`).
- fixtures/fits/cutout_2025W18_1B_0074_2D1.* made by `uv run python scripts/make_cutout_fixture.py`
  (+ `--gaia` for the Gaia list). The source file stays outside the repo.
