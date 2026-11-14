# contracts/flags.md: what we do with each FLAGS bit

Owner: P1. Used by core/ (`bad_pixel_mask`), pipeline/ (the `flags` column of dots), server/
(`flag_frac`, `sat_frac`, `saturated`) and web/ (flag overlay).

**Bit meanings confirmed on 14 Nov 2026** on the first real file, from the FLAGS header `MSKNnnnn`
keys. They match IRSA's documentation and docs/research/2026-11-14-link-check.md.
Measurements: docs/research/2026-11-14-first-photo-facts.md.
FLAGS is a tile-compressed int32 HDU: let astropy decode it.

## Policy

| Policy | Bits | Mask value | Why |
|---|---|---|---|
| **Mask** for detection and photometry | TRANSIENT 0, OVERFLOW 1, SUR_ERROR 2, NONFUNC 6, DICHROIC 7, MISSING_DATA 9, HOT 10, COLD 11, PHANMISS 14, NONLINEAR 15, PERSIST 17, STREAK 25, SNOWBALL 27 | `0x0A02CEC7` | Cosmic rays, hot and dead pixels, saturation, persistence, streaks. A dot on these pixels can't be trusted. |
| **Record** on the dot, don't mask | OUTLIER 19, CROSSTALK 20, GHOST 22, GHOST_FPA 23, GHOST_EXT 24, BLOOM 26, HALO 28 | `0x15D80000` | Useful for vetting, but masking could hide real objects. OUTLIER stays here until we've checked it isn't set on a known asteroid. |
| **Never mask** | FULLSAMPLE 12, SOURCE 21 | `0x00201000` | SOURCE is set on about 99% of pixels (99.37% on the first file). Masking it would remove almost everything. FULLSAMPLE is information only. |
| **Saturated** | OVERFLOW or NONLINEAR in a source's core | `0x00008002` | Sets `saturated` in the spectrum and counts towards `sat_frac` on the frame. Centroid on the wings. |

Bits not listed (3, 4, 5, 8, 13, 16, 18, 29–31) are not defined in the FLAGS header: ignore them.

```python
MASK_BITS   = 0x0A02CEC7   # bad_pixel_mask(flags) = (flags & MASK_BITS) != 0
RECORD_BITS = 0x15D80000   # kept in the dot's flags column, never used to mask
NEVER_BITS  = 0x00201000   # FULLSAMPLE | SOURCE
SAT_BITS    = 0x00008002   # OVERFLOW | NONLINEAR
```

## Share of pixels set on the first file

`dr1/level2/2025W18_1B/l2b-v27-2026-196/1/level2_2025W18_1B_0074_2D1_spx_l2b-v27-2026-196.fits`:
TRANSIENT 0.75%, OVERFLOW 0.07%, SUR_ERROR 0.48%, NONFUNC 0.26%, HOT 1 px, COLD 0.03%,
FULLSAMPLE 0.01%, PERSIST 0.56%, OUTLIER 0.02%, CROSSTALK 1.28%, SOURCE 99.37%, BLOOM 0.19%,
HALO 1.71%; DICHROIC, MISSING_DATA, PHANMISS, NONLINEAR, the GHOST bits and STREAK 0.
The mask list removes about 2.1% of pixels. Per-image counts are in the IMAGE header `L2 N_*` keys.

## Still open
- OUTLIER (19): check it isn't set on known asteroids (V4 batch) before deciding whether to mask it.
