# Colour layers A1 and A2: algorithm and checks

Owner: Alexandra. Code: `web/src/colour/` (A2, and the shared wavelength tables) and the
wavelength slider in `web/src/sky/index.ts` (A1). Written 14 Nov 2026.

The rule for every layer: it adds colour or marks to the picture and never removes a pixel. "Can't
tell" (no data, too faint, saturated) is shown as its own state, never as "nothing there". Every
threshold below was checked on real SPHEREx data, and the checks are listed at the end.

## Data: the SPHEREx DR1 all-sky cube

IRSA's HiPS cube `spherex_allsky_cube` (release 1 Nov 2026), FITS cube tiles at HEALPix order 5:
`Norder5/Dir<d>/Npix<n>_cube.fits`. Each tile is 320 MB and holds:

| HDU | Type | Content |
|---|---|---|
| IMAGE | float32, 512 × 512 × 102 | Surface brightness, MJy/sr. Frames in ascending wavelength, frame = 17 × (detector − 1) + (channel − 1) |
| NHITS | int32, same shape | Exposures that went into each pixel of each frame |
| FLAGS | int32, same shape | Mask bits (same names as Level 2). Doesn't mark the saturation the cube removed (see A2 step 6) |
| WCS-WAVE | table | Wavelength of each frame |

- Frames 50 and 51 (2.399 and 2.445 µm, the dichroic channels) are all-NaN (`BLANKCH`).
- Pixels are 12.9″, twice the Level 2 images' 6.15″. Star images (7.5″) fit inside one pixel.
- Celestial WCS is HPX (HEALPix projection) rotated 45°.
- The cube merges dates: one tile spans about a year (e.g. 2025-05-05 to 2026-05-14).
- The browser never downloads a tile. It sends HTTP Range requests for the pieces of rows a field
  needs, many ranges per request, which IRSA answers as `multipart/byteranges` (CORS preflight
  allowed, cached a day). A 15′ field reads about 1.7 MB in 5–10 s. A server that ignored the range
  would send 320 MB, so a 200 answer is cancelled unread. If multi-range is refused, the reader
  falls back to whole rows. Dropped connections are retried 3 times.
- Position → tile → pixel: HEALPix `ang2pix` (nested, order 5), then the HPX projection
  (Calabretta & Roukema 2007, H = 4, K = 3) with each tile's own header. Checked against astropy
  and astropy_healpix on 35 positions from real tile headers, including both poles, the RA 0/360
  seam and tile corners, to 10⁻⁴ pixel (`web/src/colour/hpx.test.ts`, `make_hpx_ref.py`).

## A1. Wavelength slider (sky view)

Shows cube frame *k* as it is: no processing.

- A slider over all 102 frames. The label gives wavelength, detector and channel, and the feature
  that falls there if any (e.g. "3.29 µm · D4 channel 12 · methane / PAH").
- Frames 50 and 51 are skipped in the direction of travel, so the map never shows a blank frame as
  black sky. A hatched tick marks the gap.
- Red ticks mark the feature wavelengths, the same windows as the pipeline's `INDEX_WINDOWS`:
  water vapour 1.4 / 1.9 µm, methane 1.67 / 3.3, CO 2.35, water ice 3.05, CO₂ 4.27, CO 4.67.
- All PNG frames share one fixed brightness scale (asinh, cut 0–40), so brightness changes along
  the slider are real.

## A2. Colour view (false-colour composite)

The plate's spot as a picture from three wavelength windows. The windows are the pipeline's
`MAG_WINDOWS`, so the site and the pipeline mean the same thing:

| Set | Red | Green | Blue |
|---|---|---|---|
| Cold bodies | 4.40–4.80 µm (12 frames) | K 2.00–2.30 (6) | J 1.15–1.33 (6) |
| 2MASS-like | K 2.00–2.30 | H 1.50–1.78 | J 1.15–1.33 |

Steps (`web/src/colour/rgb.ts`):

1. **Sampling.** A square tangent-plane grid around the plate's centre at the cube's own pixel
   size, nearest pixel. One output pixel per cube pixel; nothing is invented between them.
2. **One value per window: the median of its frames**, not the mean. The cube has single-frame
   glitches: one frame at −1,300 to −4,800σ (or +236σ) while the other 11 are normal. A mean turns
   them into fake dots.
3. **Background and noise per window:** sigma-clipped median of the field (3σ, up to 5 passes),
   subtracted; the clipped robust σ (1.4826 × MAD) is that window's noise.
4. **No per-window rescaling.** Equal MJy/sr in all three gives equal R, G, B, so a spectrum
   that is flat per unit frequency (AB) is white and the hue is the slope of the spectrum.
   Ordinary stars come out blue in "Cold bodies"; the pipeline's "red" (J − [4.6] > −0.5 AB) is
   red.
5. **Lupton et al. (2004) stretch.** One asinh on I = (R + G + B)/3, the same factor on all three
   windows, so bright sources keep their hue instead of going white; a window over 1 scales all
   three down together. Full brightness at the 99.5th percentile of I (at least 5σ_I). The
   Stretch slider moves the asinh knee from 0.1 to 20 × σ_I.
6. **Colour only when it is measured.** A pixel's colour weight goes from 0 (grey, its brightness
   only) when its brightest window is below 3σ *in that window*, to 1 at 5σ. The first version
   tested I instead, and one noisy window painted single pixels pure red.
7. **Saturation.** The cube leaves saturated readings out, so a bright star's core has fewer
   exposures than the sky around it, or none, and its colour is biased: Barnard's Star (J = 5.2)
   came out red. FLAGS doesn't mark it. Rule: a frame has a deficit at a pixel when its NHITS is
   below half the median of the 7 × 7 box around it. Flag the pixel when ≥ 4 frames have a deficit
   and I > 10σ_I, then grow the flag by one pixel. Saturated pixels get a red outline (switchable)
   and say so in the readout; they are not hidden.
8. **No data** (NaN in any window) is transparent over a hatch.

The readout gives position and, per window, MJy/sr and σ for the pixel under the pointer. The key
says what each hue means, and that each wavelength was seen on a different date in the tile's
span, so the colours of things that move or vary are not reliable.

## Checks on real data (14 Nov 2026, 15′ fields)

| Check | Result |
|---|---|
| HPX and HEALPix against astropy, 35 positions | All within 10⁻⁴ px. One exact four-tile corner is ambiguous, either tile is valid |
| Field stars, 4.6 µm / J flux ratio | Median 0.16–0.19 in the Barnard, Eps Ind B and WISE 0855 fields (blue), as expected for stars |
| Epsilon Indi B (T1 + T6) | Ratio 1.1: 6–7 × redder than the field stars. Near-white, while the stars around it are blue |
| WISE 0855−0714 (Y dwarf) | 10σ at 4.6 µm at the centre (the targets file is already at the SPHEREx epoch). Blended with a blue star one pixel away, so it shows magenta |
| Barnard's Star, R Draconis, Eps Ind A | Saturated cores flagged (12, 24 and many deficit frames). Colours marked not reliable |
| Chance saturation flags | Faint pixels never had more than 3 deficit frames in 19,600 pixels; saturated cores had 4–24 |
| Noise test: pixels ≤ −5σ (noise is symmetric, sources are positive) | Mean of frames: 9–24 per field at 4.6 µm. Median of frames: 0 in every window of all three fields, against 270–1,200 at +5σ |
| An early "pure red WISE 0855, 58″ west" | Withdrawn: one frame at +236σ. The median removes it |
| Load time in Chromium (WISE 0855, 15′) | 42 s reading whole rows; 8 s reading only the needed pieces |

## Not done yet

- A3 feature maps (methane, ices, PAH): agreed in principle, not built. They need the same
  frame-median and saturation guards, plus a zero point from field stars.
- The saturation rule was calibrated on four fields; a dense field near the Galactic plane
  should be checked.
