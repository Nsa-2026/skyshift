# contracts/dots.md: the dot table and derived tables (v2)

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

**One dot = one wavelength.** SPHEREx's filter changes wavelength across each detector, so a
photo measures a source at one wavelength per detector. As the telescope re-points, the same
source lands on other parts of the filter; grouping a source's dots by `cell` over a visit gives
its 102-channel spectrum (wavelength_um, bandwidth_um, flux_ujy); `spectra` does this for every
fixed source over all visits (`sources` below). Brightness is `flux_ujy`
(AB magnitude = 23.9 − 2.5 log10 flux_ujy); luminosity needs a distance, so it is computed only
where Gaia gives a parallax, never stored per dot.

Session and visit are one value per photo, from the zone's photo list: a session is a run of the
zone's exposure times with gaps under 10 minutes (`core.sessions`), a visit comes from `core.visits`
on the same times. How many sessions covered a position is counted from the photos' footprints
when a candidate is checked. (Changed 14 Nov after review: per-tile sessions left 3.4% of dots without one.)

**Proposal for `cell`:** `band = floor((dec + 90) × 600)`, `col = floor(ra × cos(dec_band_centre) × 600)`,
`cell = band × 10_000_000 + col` (600 = 3600″ / 6″). Group with the neighbouring cells too, so a
dot on a cell edge isn't split.

## Derived tables (in `gs://nasa-glazing-data/results/`)

| table | rule |
|---|---|
| fixed | Dots within 4″ of each other seen in 3 or more sessions (stars, galaxies) |
| movers | Not within 6″ of a fixed source; paired inside one session (≤ 3″ + 1″/min × Δt between exposures) or by 2 detectors exposed at the same moment (D1+D4, D2+D5, D3+D6); position covered by ≥ 3 sessions. Only fixed sources veto; a stray single dot elsewhere doesn't |
| members | `results/<zone>/members.parquet`, written by `link`: one row per dot of a fixed source. `source_id` int64, `obs_id` string, `detector` int8, `dot_row` int32 (0-based row in `dots/<obs_id>D<detector>.parquet` as written; dot files are never rewritten). Sorted by source_id, obs_id, detector, dot_row. `fixed` also carries `source_id` (= 6″ `cell` × 100 + rank of the source in its cell by RA, Dec) |
| sources | `results/<zone>/sources.parquet`, written by `spectra`: one row per fixed source. `source_id` int64, `ra`, `dec` float64 (median of its dots), `pos_err_arcsec` float32 (dot scatter / √n per axis, floor 0.05″), `n_dots` int32, `n_sessions` int16, `n_visits` int16, `detectors` int8 (bit d−1 set if seen by detector d), `cell` int64, `saturated` bool (≥ 2 dots with OVERFLOW/NONLINEAR in their 3×3), `flux_ujy` fixed_size_list<float32>[102] (NaN where empty), `err_ujy` fixed_size_list<float32>[102], `n` fixed_size_list<int16>[102] (dots used), `chi2_nu` float32 (kept dots against their channel values; ≈ 1 constant, ≫ 1 variable or bad errors), `visit_flux_ratio` float32 (last visit / first visit: per detector the median of flux ÷ channel value; median over detectors; NaN with one visit), `variable` bool (chi2_nu > 5 and ratio beyond ×/÷ 1.25). Per channel: dots with MASK bits (contracts/flags.md) in their 3×3 left out; gross outliers clipped (> 5 × max(error, MAD) and > 1.5 × |median| from the median); inverse-variance weighted mean, error 1/√Σw × √max(1, χ²/(n−1)) |
| channels | `results/<zone>/channels.json`: the 102 channels as a list of `{channel, detector, index, um_lo, um_hi, um_centre}`. **Our definition, not NASA's official channels:** 17 log-spaced channels per detector over D1 0.75–1.1, D2 1.1–1.6, D3 1.6–2.4, D4 2.4–3.8, D5 3.8–4.4, D6 4.4–5.0 µm; centre = √(lo × hi); a dot outside its detector's range (D1 measured 0.735–1.126) goes to that detector's end channel. Channel = (detector − 1) × 17 + index |
| suspects | Movers with no SkyBoT match within 30″, no SOURCE bit at the peak, no ghost/crosstalk/halo/bloom bits, not near a Gaia star (6″) or CatWISE source (6″). Exported to `web/public/data/suspects.json` (contracts/api.md) and the candidate list |
| crossids | `results/<zone>/crossids.parquet`, written by `crossid`: one row per source. `source_id` int64, `ra`, `dec` float64, `epoch_mjd` float64 (mean MJD of its photos). Gaia DR3: `gaia_ref_id` string ("" = none), `gaia_id` int64 (−1), `gaia_sep_arcsec` float32, `gaia_n` int16 (entries within the radius), `gaia_ra`, `gaia_dec`, `gaia_epoch_mjd` float64 (catalogue values), `gaia_g`, `gaia_bp_rp`, `gaia_pmra`, `gaia_pmdec` (mas/yr, pmra × cos dec), `gaia_parallax` (mas) float32. CatWISE2020: `catwise_ref_id`, `catwise_name` string, `catwise_sep_arcsec`, `catwise_n`, `w1`, `w2`, `catwise_pmra`, `catwise_pmdec`. 2MASS: `tmass_ref_id`, `tmass_name`, `tmass_sep_arcsec`, `tmass_n`, `j`, `h`, `k`. Catalogue positions moved to `epoch_mjd` with their pm (Gaia also parallax; 2MASS not moved); nearest within 2.5″ (Gaia), 3″ (CatWISE, 2MASS). Input `refcat/<zone>.parquet` (ref_id, cat, ra, dec, epoch_mjd, pmra, pmdec, parallax, magnitudes) |
| colours | `results/<zone>/colours.parquet`, written by `colours`: one row per (source, simultaneous pair). `source_id` int64, `pair` string (`D1D4`, `D2D5`, `D3D6`), `det_a`, `det_b` int8, `n` int32 (photos with both), `ratio` float32 (median f_b / f_a), `ratio_err`, `colour_ab` float32 (m_a − m_b = 2.5 log10 ratio, mag), `colour_err`, `um_a`, `um_b` float32 (median wavelengths). Same obs_id only, so variability cancels; MASK bits out |
| lightcurves | `results/<zone>/lightcurves.parquet`, written by `lightcurves`: one row per point. `source_id` int64 (−1 = catalogue star with no source), `ref_id` string ("" for dots), `origin` (`dot` \| `forced`), `mjd` float64, `visit` int16, `detector` int8, `wavelength_um`, `flux_ujy`, `flux_err_ujy` float32 (PSF flux when present), `detected` bool, `model_ujy` float32 (the source's spectrum at that wavelength × per-(source, detector, origin) scale; without a spectrum a quadratic in ln λ fitted to its own points), `rel_flux` float32 (flux / model, ≈ 1 when constant). Forced rows come from `forced/<obs_id>D<det>.parquet`, tied to a source through crossids |
| lightcurves_summary | `results/<zone>/lightcurves_summary.parquet`: one row per (source or ref, detector, origin) with ≥ 3 points. `source_id`, `ref_id`, `detector`, `origin`, `n`, `n_detected` int32, `flux_median_ujy`, `um_median`, `scatter_rel` (1.4826 MAD of rel_flux), `chi2_nu`, `amp_rel` (p95 − p5 of rel_flux) float32, `visits` list<int16>, `visit_median_rel` list<float32>, `visit_ratio` float32 (last / first visit), `variable` bool (chi2_nu > 5 and scatter_rel > 0.05). Compare within a detector only |
| astrometry | `results/<zone>/astrometry.parquet`, written by `astrometry`: one row per source with ≥ 6 dots. `source_id` int64, `ra`, `dec`, `epoch_mjd` float64 (weighted mean epoch), `ra_err_arcsec`, `dec_err_arcsec`, `pmra`, `pmdec`, `pmra_err`, `pmdec_err`, `pm`, `pm_snr` (mas/yr, pmra × cos dec), `parallax`, `parallax_err` (mas; NaN unless ≥ 3 visits), `fit` string (`pm` \| `pm+plx`), `n_dots`, `n_used` int32, `n_visits` int16, `span_yr`, `chi2_nu`, `snr_median` float32, `frame_corrected` bool. Weighted by pos_err_arcsec; observer = Earth (astropy) + SPHEREx geocentric X_SC/Y_SC/Z_SC when present; each photo's median offset against quiet Gaia stars removed (≥ 10 stars); errors × √max(1, χ²_ν). `astrometry_report.json`: SPHEREx − Gaia pm bias, robust scatter, median error, pull scatter, overall and by median dot S/N, raw and frame-corrected |
| highpm_candidates | `results/<zone>/highpm_candidates.parquet`, written by `highpm`. `method` (`pair`: a single-visit source and a single-visit source of another visit 3″–120″ apart, both positions covered by ≥ 2 photos of the other visit, every shared detector's flux within ×/÷ 2, rms of ln ratio ≤ 0.25; `fit`: astrometry pm ≥ 150 mas/yr at ≥ 5σ), `status` (`new` = no Gaia match on either side \| `gaia`), `source_id`, `source_id_b` (−1 for fit) int64, `ra`, `dec`, `ra_b`, `dec_b`, `mjd`, `mjd_b` float64, `visit`, `visit_b` int16, `sep_arcsec`, `pmra`, `pmdec`, `pm`, `pm_err` (mas/yr), `flux_ratio`, `shape_rms`, `red_ratio` (D6 / D2 flux) float32, `n_common_det` int8, `gaia_ref_id`, `gaia_id`, `catwise_name`, `catwise_pm_agree` bool, `score` float32 (lower = better). `highpm_report.json`: counts, chance pairs from a 15′-offset control, and recall of Gaia stars faster than 200 mas/yr in the zone (in zone, detected as a source, recovered) |

We measure motion only where people look. No linking of every dot across visits.
Dropped from v1: the pass rule, the `objects` table, HEALPix tiles, batches and locks.
