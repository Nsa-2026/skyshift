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
| snr | float32 | | Signal-to-noise |
| flags | int32 | | OR of the flag bits under the dot, including recorded ones (contracts/flags.md) |
| cell | int64 | | 6″ sky cell, used to group dots by position |

**One dot = one wavelength.** SPHEREx's filter changes wavelength across each detector, so a
photo measures a source at one wavelength per detector. As the telescope re-points, the same
source lands on other parts of the filter; grouping a source's dots by `cell` over a visit gives
its 102-channel spectrum (wavelength_um, bandwidth_um, flux_ujy). Brightness is `flux_ujy`
(AB magnitude = 23.9 − 2.5 log10 flux_ujy); luminosity needs a distance, so it is computed only
where Gaia gives a parallax, never stored per dot.

Session and visit come from `core.sessions()` and `core.visits()` on the exposure times that
cover the position, taken from the saved photo lists (`photolists/`).

**Proposal for `cell`:** `band = floor((dec + 90) × 600)`, `col = floor(ra × cos(dec_band_centre) × 600)`,
`cell = band × 10_000_000 + col` (600 = 3600″ / 6″). Group with the neighbouring cells too, so a
dot on a cell edge isn't split.

## Derived tables (in `gs://nasa-glazing-data/results/`)

| table | rule |
|---|---|
| fixed | Cell seen in 3 or more sessions |
| movers | Seen in one session only, in 2 exposures or in 2 detectors exposed at the same moment (D1+D4, D2+D5, D3+D6) |
| suspects | Movers with no SkyBoT match. Exported to `web/public/data/suspects.json` (contracts/api.md) and the candidate list |

We measure motion only where people look. No linking of every dot across visits.
Dropped from v1: the pass rule, the `objects` table, HEALPix tiles, batches and locks.
