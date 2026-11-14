# First-photo facts (Sat 14 Nov 2026, 10:00)

Measured on one real SPHEREx Level 2 file:
`dr1/level2/2025W18_1B/l2b-v27-2026-196/1/level2_2025W18_1B_0074_2D1_spx_l2b-v27-2026-196.fits`
from `s3://nasa-irsa-spherex` (public, anonymous HTTPS). The file lives outside the repo; it's never committed.

## Answers to the open questions
- **Time:** `MJD-AVG` exists in the IMAGE header: "MJD at midpoint of observation", `TIMESYS = 'UTC'`.
  Also `MJD-BEG`, `MJD-END`, `MJD-OBS` (= start), and the ISO forms `DATE-OBS`, `DATE-AVG`, `DATE-BEG`, `DATE-END`.
  `DATE` is the file creation time; don't use it. This exposure: MJD-AVG 60794.0177 = 29 Apr 2025 00:25 UTC.
- **Detector:** IMAGE header `DETECTOR = 1` ("1-3: SWIR, 4-6: MWIR"). The file name `..._0074_2D1_...` means
  OBSID `2025W18_1B_0074_2` (exposure 2) + `D1` (detector 1), so the name and the header agree. Still take the band
  from the header or TAP `energy_bandpassname`, never by parsing names.
- **Pixel scale:** 6.152″/px from the celestial WCS (`RA---TAN-SIP`, `DEC--TAN-SIP`). PSF FWHM (median) 7.53″.
- **Units:** IMAGE `BUNIT = 'MJy / sr'`, float32. Median 0.53, 99.9th percentile 80.8, max 5,146 in this frame.
- **Exposure:** `TELAPSE` 118.2 s (start to stop).
- **Orientation:** `DETCOORD = 'sky'`: "MWIR pixels flipped on X to match SWIR view".
- **Centre of this frame:** RA 303.876, Dec +40.547.

## File layout and sizes (60,157,440 bytes total)
| HDU | Type | Shape | Data bytes | Data offset |
|---|---|---|---|---|
| PRIMARY | header only | – | 0 | 2,880 |
| IMAGE | float32 image | 2040 × 2040 | 16,646,400 | 28,800 |
| FLAGS | tile-compressed int32 | 2040 × 2040 | 6,275,520 | 16,692,480 |
| VARIANCE | float32 image | 2040 × 2040 | 16,646,400 | 22,979,520 |
| ZODI | float32 image | 2040 × 2040 | 16,646,400 | 39,637,440 |
| EPSF | table | 441 rows | 3,862,080 | 56,289,600 |
| WCS-WAVE | table | 1 row | 2,880 | 60,154,560 |

Parts we need: IMAGE + FLAGS + WCS-WAVE ≈ **22.9 MB** (+ VARIANCE for error bars ≈ 39.6 MB).
The HDUs are contiguous, so HTTP range reads can skip ZODI and EPSF. FLAGS is a compressed HDU, so let astropy decode it.

## Flag bits (from the FLAGS header; they match docs/research/2026-11-14-link-check.md)
Percent of pixels set in this frame:

| Bit | Name | % | v2 policy |
|---|---|---|---|
| 0 | TRANSIENT | 0.75 | mask |
| 1 | OVERFLOW | 0.07 | mask (marks saturation) |
| 2 | SUR_ERROR | 0.48 | mask |
| 6 | NONFUNC | 0.26 | mask |
| 7 | DICHROIC | 0 | mask |
| 9 | MISSING_DATA | 0 | mask |
| 10 | HOT | 0.00 (1 px) | mask |
| 11 | COLD | 0.03 | mask |
| 12 | FULLSAMPLE | 0.01 | never mask (information only) |
| 14 | PHANMISS | 0 | mask |
| 15 | NONLINEAR | 0 | mask (marks saturation) |
| 17 | PERSIST | 0.56 | mask |
| 19 | OUTLIER | 0.02 | record (check it isn't set on known asteroids) |
| 20 | CROSSTALK | 1.28 | record |
| 21 | SOURCE | **99.37** | **never mask** |
| 22–24 | GHOST, GHOST_FPA, GHOST_EXT | 0 | record |
| 25 | STREAK | 0 | mask |
| 26 | BLOOM | 0.19 | record |
| 27 | SNOWBALL | 0.00 | mask |
| 28 | HALO | 1.71 | record |

The mask list removes about 2.1% of pixels in this frame. Per-image counts are in the IMAGE header as `L2 N_*` keys.
