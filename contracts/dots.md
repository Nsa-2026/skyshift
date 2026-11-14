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
| refcat | `refcat/<zone>.parquet` (Gaia DR3 G < 20, CatWISE2020, 2MASS PSC over the zone + 0.1°, unified, NaN where not applicable) and `refcat/<zone>_forced.parquet`, written by `refcat`, read by `dots`: the forced-photometry targets, exactly `ref_id` string (`gaia:<source_id>` / `catwise:<source_name>`), `ra`, `dec` float64 (deg at `epoch_mjd`), `epoch_mjd` float64, `pmra`, `pmdec` float64 (mas/yr, pmra × cos dec, NaN allowed), `parallax` float64 (mas, NaN allowed). Gaia G < 19 plus CatWISE W1 < 16 with no Gaia source within 2″ |
| suspects | Movers with no SkyBoT match within 30″, no SOURCE bit at the peak, no ghost/crosstalk/halo/bloom bits, not near a Gaia star (6″) or CatWISE source (6″). Exported to `web/public/data/suspects.json` (contracts/api.md) and the candidate list |

We measure motion only where people look. No linking of every dot across visits.
Dropped from v1: the pass rule, the `objects` table, HEALPix tiles, batches and locks.
