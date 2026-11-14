# contracts/dots.md: the dot table, forced photometry and derived tables (v3)

Owner: P1. Written by pipeline/, read by validation scripts and the suspects export.
Units: degrees, arcsec, µm, µJy, MJD (UTC).

## dots: one row per detected point in one photo

One Parquet file per photo in `gs://nasa-glazing-data/dots/`. Its existence marks the photo done;
skip it on restart. No job list, no locks, no tiles.
**Proposal:** file name `dots/<obs_id>D<detector>.parquet`, e.g. `dots/2025W18_1B_0074_2D1.parquet`.

| column | type | unit | meaning |
|---|---|---|---|
| obs_id | string | | IRSA observation id, e.g. `2025W18_1B_0074_2` |
| detector | int8 | | 1–6, from `energy_bandpassname` or the IMAGE header `DETECTOR`, never the file name |
| mjd | float64 | MJD | MJD-AVG of the exposure (UTC midpoint) |
| session | int32 | | From the visit rule (contracts/api.md), per position; 1 = earliest |
| visit | int16 | | From the visit rule, per position; 1 = earliest |
| ra, dec | float64 | deg | Sky position, ICRS, as measured (no proper-motion correction) |
| pos_err_arcsec | float32 | arcsec | 1σ position error per axis: `sqrt((1.4 × 3.2″ / snr)² + 0.25″²)`. 3.2″ is the PSF's σ (FWHM 7.53″); 0.25″ is the floor measured against Gaia on the first photo, to be replaced by V3's value |
| x, y | float32 | pixel | Position in the exposure, 0-based (numpy) |
| flux_ujy | float32 | µJy | Brightness (IMAGE MJy/sr × 889 µJy per 6.15″ pixel, summed in the aperture) |
| flux_err_ujy | float32 | µJy | Brightness error |
| wavelength_um | float32 | µm | Wavelength at that pixel, from WCS-WAVE |
| bandwidth_um | float32 | µm | Bandwidth at that pixel, from WCS-WAVE (one of the 102 channels' width) |
| elong | float32 | | Elongation a/b of the detected shape (sep). Near 1 for stars; large for trails, streaks and cosmic-ray hits |
| snr | float32 | | Signal-to-noise. Detection runs at `thresh=2.0` (about S/N 9 for a point source); only dots with snr ≥ 5 are kept |
| flags | int32 | | OR of the flag bits in the 3×3 pixels around the dot, including recorded ones (contracts/flags.md) |
| flags_peak | int32 | | Flag bits at the dot's peak pixel. SOURCE here means a catalogued star or compact galaxy (to AB 19); used only to filter suspects |
| cell | int64 | | 6″ sky cell, used to group dots by position |
| zodi_mjysr | float32 | MJy/sr | ZODI model (zodiacal light) at the dot's pixel. IMAGE is **not** zodi-subtracted (Expsupp 2.2, 3.2.11); NaN if the file has no ZODI |
| bkg_mjysr | float32 | MJy/sr | Background map core.detect subtracted (sep, 32 px mesh) at the dot's pixel; includes zodi and any diffuse light |
| peak_mjysr | float32 | MJy/sr | Peak pixel above that background (sep `peak`) |
| a_arcsec, b_arcsec | float32 | arcsec | √ of the eigenvalues of the intensity-weighted second moments (good pixels within 2.5 px, local annulus 3.5–5.5 px subtracted). A star gives a ≈ b ≈ 3.5–5″ (the window truncates and the PSF is undersampled: a relative measure). NaN as for elong |
| theta_deg | float32 | deg | Major-axis angle in pixel coordinates, from +x towards +y, in (−90, 90]. `elong = a_arcsec / b_arcsec` (capped at 99) |
| flux_psf_ujy | float32 | µJy | Total flux from a fit of the photo's ePSF (EPSF HDU, zone containing the dot) at the dot's fixed (x, y) plus a local constant, on 7 × 7 pixels, masked pixels left out. Weights 1/(max(√VARIANCE, background rms)² + (0.03 · model)²). Total flux, so `flux_psf_ujy / flux_ujy` is the r = 2 px aperture correction (ePSF: 1.033). NaN with fewer than 12 good pixels or no EPSF HDU |
| flux_psf_err_ujy | float32 | µJy | 1σ of flux_psf_ujy from the fit (no extra systematic term) |
| psf_chi2_nu | float32 | | χ²/(n − 2) of that fit. Compare at a given S/N: star-like dots sit near 2–3 at S/N 5–10 and rise with S/N (centroid error at fixed position and the ePSF's own error: median 20–30 at S/N > 100 on the Cygnus photo); galaxies, blends and artefacts sit well above the star locus |
| flux_ap_ujy | fixed_size_list&lt;float32&gt;[5] | µJy | Aperture fluxes at radii 1.0, 1.5, 2.0, 3.0, 4.5 px (config `AP_RADII_PX`, also in the photo metadata), background = mean of the 5.5–8 px annulus around the dot, masked pixels left out |
| flux_ap_err_ujy | fixed_size_list&lt;float32&gt;[5] | µJy | Their errors (noise max(√VARIANCE, background rms), incl. the annulus' error) |

**One dot = one wavelength.** SPHEREx's filter changes wavelength across each detector, so a
photo measures a source at one wavelength per detector. As the telescope re-points, the same
source lands on other parts of the filter; grouping a source's dots by `cell` over a visit gives
its 102-channel spectrum (wavelength_um, bandwidth_um, flux_ujy); `spectra` does this for every
fixed source over all visits (`sources` below). Brightness is `flux_ujy`
(AB magnitude = 23.9 − 2.5 log10 flux_ujy); luminosity needs a distance, so it is computed only
where Gaia gives a parallax, never stored per dot.

Row order is fixed when the file is written (brightest `flux_ujy` first, as core.detect returns
dots) and files are never rewritten: `members.dot_row` points at rows by index.
Floats are written with byte-stream-split + zstd (Cygnus worst case, 58,725 dots: 6.5 MB).

**Photo metadata.** Every dot file (empty ones too) carries one JSON object in the Parquet
key-value metadata under the key `skyshift_photo` (read it with `skyshift_pipeline.dots.photo_meta(path)`):
`schema` (1), `obs_id`, `detector`, `status` (`done` | `done_finast`), `pipeline_version` (PRIMARY `VERSION`),
`epsf_calib` (ePSF calibration id from the IMAGE HISTORY), `n_dots`, `n_forced` (null without a refcat),
`read_s`, `detect_s`, `forced_s`, `stages_s` (seconds per stage), `zodi_median_mjysr`, `bkg_median_mjysr`,
`ujy_per_mjysr`, `aperture_radii_px`, `aperture_bkg_annulus_px`, `psf_fit` (`half_px`, `model_frac`, `noise`),
`header` (IMAGE header values as written by IRSA, null when absent: MJD-BEG/AVG/END, XPOSURE, TELAPSE;
spacecraft geocentric position X_SC/Y_SC/Z_SC [km] and velocity VX_SC/VY_SC/VZ_SC [km/s] at mid, plus
`_START` and `_END`; FINAST, MATCH_NSTARS, ASTRONETSOLVED, OMEGA_MEDIAN/OMEGA_STDDEV [arcsec²], PSF_FWHM [″],
L1DQAFLG, L2DQAFLG, HE_AG_AMPL, AURORA_CORR, SHUTTLE_GLOW_CORR, NBLMSRCS, NSNOWSRC, `L2 SATSTRKS`, SPS_ELON,
SPS_ELAT, SPS_EPA [deg], SGT_MIN_GCD2SAA [deg], and every `L2 N_*` flag count),
`calibration` (`astrometry_vs_gaia`: n, median `dra_cosdec_arcsec` and `ddec_arcsec` of dot − Gaia,
`sigma_ra_arcsec`/`sigma_dec_arcsec` = 1.4826 × MAD, for dots with snr > 20 within 2″ of a forced `gaia:`
target; `psf_moments`: n, median `fwhm_arcsec` = 2.3548 √(a b), `ellipticity` = 1 − b/a, `theta_deg`, from
unsaturated dots with snr > 50; `psf_over_aperture_flux`: median flux_psf_ujy / flux_ujy of the same dots),
`grids` (`n` = 32, `bkg_mjysr` and `zodi_mjysr`: 32 × 32 block means, rows = y, block edges
round(linspace(0, 2040, 33)), MJy/sr, 5 significant digits; null if absent). NaN is written as null.

Session and visit are one value per photo, from the zone's photo list: a session is a run of the
zone's exposure times with gaps under 10 minutes (`core.sessions`), a visit comes from `core.visits`
on the same times. How many sessions covered a position is counted from the photos' footprints
when a candidate is checked. (Changed 14 Nov after review: per-tile sessions left 3.4% of dots without one.)

**Proposal for `cell`:** `band = floor((dec + 90) × 600)`, `col = floor(ra × cos(dec_band_centre) × 600)`,
`cell = band × 10_000_000 + col` (600 = 3600″ / 6″). Group with the neighbouring cells too, so a
dot on a cell edge isn't split.

## forced: one row per reference target per photo, detected or not

One Parquet file per photo, `forced/<obs_id>D<detector>.parquet`, written before the dot file (so a
dot file still means "photo fully done"), only when `refcat/<zone>_forced.parquet` exists (else a warning;
the photo is still done). Targets: the refcat rows moved to the photo's MJD-AVG by proper motion only
(core.propagate; NaN proper motion = 0; parallax ignored), kept if ≥ 3 px inside the frame and inside the
zone box + 0.05°. Negative and low fluxes are kept: they are the non-detections.

| column | type | unit | meaning |
|---|---|---|---|
| obs_id, detector, mjd | string, int8, float64 | , , MJD | As in dots |
| ref_id | string | | From the refcat, e.g. `gaia:4472832130942575872`, `catwise:J…` |
| ra, dec | float64 | deg | Target position at the photo's MJD-AVG |
| x, y | float32 | pixel | Its 0-based pixel position (full WCS incl. SIP) |
| flux_psf_ujy, flux_psf_err_ujy, psf_chi2_nu | float32 | µJy, µJy, | ePSF fit at the fixed (x, y), as in dots |
| flux_ap2_ujy, flux_ap2_err_ujy | float32 | µJy | r = 2 px aperture on IMAGE minus the detection background map: the same measure as a dot's `flux_ujy` |
| wavelength_um, bandwidth_um | float32 | µm | From WCS-WAVE at (x, y) |
| zodi_mjysr, bkg_mjysr | float32 | MJy/sr | As in dots |
| flags | int32 | | OR of the flag bits in the 3×3 pixels at the target |

## Derived tables (in `gs://nasa-glazing-data/results/`)

| table | rule |
|---|---|
| fixed | Dots within 4″ of each other seen in 3 or more sessions (stars, galaxies) |
| movers | Not within 6″ of a fixed source; paired inside one session (≤ 3″ + 1″/min × Δt between exposures) or by 2 detectors exposed at the same moment (D1+D4, D2+D5, D3+D6); position covered by ≥ 3 sessions. Only fixed sources veto; a stray single dot elsewhere doesn't |
| members | `results/<zone>/members.parquet`, written by `link`: one row per dot of a fixed source. `source_id` int64, `obs_id` string, `detector` int8, `dot_row` int32 (0-based row in `dots/<obs_id>D<detector>.parquet` as written; dot files are never rewritten). Sorted by source_id, obs_id, detector, dot_row. `fixed` also carries `source_id` (= 6″ `cell` × 100 + rank of the source in its cell by RA, Dec) |
| sources | `results/<zone>/sources.parquet`, written by `spectra`: one row per fixed source. `source_id` int64, `ra`, `dec` float64 (median of its dots), `pos_err_arcsec` float32 (dot scatter / √n per axis, floor 0.05″), `n_dots` int32, `n_sessions` int16, `n_visits` int16, `detectors` int8 (bit d−1 set if seen by detector d), `cell` int64, `saturated` bool (≥ 2 dots with OVERFLOW/NONLINEAR in their 3×3), `flux_ujy` fixed_size_list<float32>[102] (NaN where empty), `err_ujy` fixed_size_list<float32>[102], `n` fixed_size_list<int16>[102] (dots used), `chi2_nu` float32 (kept dots against their channel values; ≈ 1 constant, ≫ 1 variable or bad errors), `visit_flux_ratio` float32 (last visit / first visit: per detector the median of flux ÷ channel value; median over detectors; NaN with one visit), `variable` bool (chi2_nu > 5 and ratio beyond ×/÷ 1.25). Per channel: dots with MASK bits (contracts/flags.md) in their 3×3 left out; gross outliers clipped (> 5 × max(error, MAD) and > 1.5 × |median| from the median); inverse-variance weighted mean, error 1/√Σw × √max(1, χ²/(n−1)) |
| channels | `results/<zone>/channels.json`: the 102 channels as a list of `{channel, detector, index, um_lo, um_hi, um_centre}`. **Our definition, not NASA's official channels:** 17 log-spaced channels per detector over D1 0.75–1.1, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 µm; centre = √(lo × hi); a dot outside its detector's range (D1 measured 0.735–1.126) goes to that detector's end channel. Channel = (detector − 1) × 17 + index |
| refcat | `refcat/<zone>.parquet` (Gaia DR3 G < 20, CatWISE2020, 2MASS PSC over the zone + 0.1°, unified, NaN where not applicable) and `refcat/<zone>_forced.parquet`, written by `refcat`, read by `dots`: the forced-photometry targets, exactly `ref_id` string (`gaia:<source_id>` / `catwise:<source_name>`), `ra`, `dec` float64 (deg at `epoch_mjd`), `epoch_mjd` float64, `pmra`, `pmdec` float64 (mas/yr, pmra × cos dec, NaN allowed), `parallax` float64 (mas, NaN allowed). Gaia G < 17.5 plus CatWISE W1 < 16 and W1 − W2 ≥ 0.4 with no Gaia source within 2″ (limits in pipeline config.py, sized to ≤ 30,000 targets per 3.5° photo) |
| suspects | Movers with no SkyBoT match within 30″, no SOURCE bit at the peak, no ghost/crosstalk/halo/bloom bits, not near a Gaia star (6″) or CatWISE source (6″). Exported to `web/public/data/suspects.json` (contracts/api.md) and the candidate list |

We measure motion only where people look. No linking of every dot across visits.
Dropped from v1: the pass rule, the `objects` table, HEALPix tiles, batches and locks.
