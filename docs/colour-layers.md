# Colour layers A1, A2 and A3: algorithm and checks

Owner: Alexandra. Code: `web/src/colour/`. Written 14 Nov 2026.

**Where they are on the site.** An "Add filters" button sits just above the main image (the blink
plate). Its menu offers No filter, Temperature, Star colours and Single colour
(with a slider over all 102 wavelengths) and Molecules (A3, the composition filter),
plus "Mark stars too bright to measure" and Brightness.
The chosen filter replaces the picture on the plate, centred on the same spot and oriented the same
way (north up, east left), and a key under the plate says what each colour means. All visitor-facing
text is in plain words; the technical terms stay in this document.

The filter picture covers three times the blink's field, at least 15′ and at most 60′, with the
blink's field drawn as a dashed square (`filterField`). Showing only the blink's 5′ field made the
map's 12.9″ pixels 23 across the plate, which looked like a huge zoom. While a filter is on, the blink's date, caption and
time strip are dimmed: the filtered picture merges all dates. "Remove filter" brings the blink
back. Nothing is downloaded until a filter is chosen. The module adds its button, layer and key
around the blink's plate from outside (`mountFilters` in `web/src/colour/index.ts`, mounted in
`web/src/app/app.ts`); it doesn't edit `web/src/blink/`.

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

## A1. Single colour (one wavelength; first named "One colour of light")

The plate's field at one of the 102 wavelengths, white on black.

- A slider over all 102 frames. The label gives wavelength, detector and channel, and the feature
  that falls there if any (e.g. "3.29 µm · D4 channel 12 · methane / PAH").
- Frames 50 and 51 are skipped in the direction of travel, so the picture never shows a blank
  frame as black sky. A hatched tick marks the gap.
- Red ticks mark the feature wavelengths, the same windows as the pipeline's `INDEX_WINDOWS`:
  water vapour 1.4 / 1.9 µm, methane 1.67 / 3.3, CO 2.35, water ice 3.05, CO₂ 4.27, CO 4.67.
- The picture is the median of the chosen frame and its neighbours in the same detector (two or
  three frames), so a single-frame glitch can't show as a dot.
- Each frame's own background is subtracted, but the brightness scale is fixed for the field (asinh,
  from the 2.0–2.3 µm window's noise and 99.5th percentile), so a star that fades as the slider
  moves really is fainter there.
- A hole at the target (no data in the 3 × 3 core while ≥ 12 of the 16 pixels around it have data)
  is explained under the picture as saturation.

## A2. Colour pictures (false-colour composites)

The plate's spot as a picture from three wavelength windows. The windows are the pipeline's
`MAG_WINDOWS`, so the site and the pipeline mean the same thing:

| Set | Red | Green | Blue |
|---|---|---|---|
| Temperature (first named "Find cold objects") | 4.40–4.80 µm (12 frames) | K 2.00–2.30 (6) | J 1.15–1.33 (6) |
| Compare with 2MASS | K 2.00–2.30 | H 1.50–1.78 | J 1.15–1.33 |

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

**The key** (`web/src/colour/legend.ts`). Each colour swatch is drawn by the picture's own
algorithm from what that kind of object looks like in the three windows: published typical colours
(A0, Sun-like, M4, L5 and T6 dwarfs) converted to brightness per unit frequency, plus Epsilon Indi
B as measured here. So the key can't drift from the picture. A named example must look like its
swatch on its own plate: Barnard's Star (saturated) and WISE 0855 (blended) don't, so they aren't
named. In Star colours (first named "Compare with 2MASS"), methane T dwarfs come out blue like hot stars, and the key says to use
Temperature to tell them apart.

**Why a colour isn't reliable** (`web/src/colour/why.ts`). When the target itself is affected, a
box under the picture says why, in plain words and with the numbers measured for that star:

- *Too bright for SPHEREx* (a saturated pixel within 2 pixels of the centre). It gives the share of
  the usual exposures the map kept at the core in each window (NHITS in the 3 × 3 core ÷ the
  median of the 7 × 7 box around each pixel), and says which way the colour is biased only when
  the numbers show it: short wavelengths kept less than half as much as long ones → "looks redder
  than it really is"; the reverse → "bluer"; every window under 10% → "the core is missing at
  every wavelength, so the colour comes from the star's edge". Measured on 14 Nov: Barnard's Star
  kept 28% at 1.24 µm, 17% at 2.15 µm and 70% at 4.60 µm (looks too red); R Draconis kept 8%, 4%
  and 6% (core missing everywhere, no direction claimed).
- *Blended with a neighbour* (colour pictures, not when saturated). In the 3 × 3 core the short
  (1.2 µm) and long (4.6 µm) windows peak in different pixels, each ≥ 5σ, and at the other
  window's peak one of them is at most half its own peak. One object puts both peaks in the same
  pixel. Measured on 14 Nov: fires on WISE 0855 (its 1.2 µm light is a blue star one pixel away),
  not on Epsilon Indi B, and on 5 of 219 field stars (2.3%) brighter than 10σ in both windows,
  some of them real blends.
- *Too bright for SPHEREx* in a single-wavelength picture: the hole at the core (see A1).
- *Its brightness changes* (featured targets of kind "variable star"), with the map's dates.
- *It moves* (asteroids, comets, and plates that follow their target).

The readout gives position and, per window, MJy/sr and σ for the pixel under the pointer. The key
says what each hue means, and that each wavelength was seen on a different date in the tile's
span, so the colours of things that move or vary are not reliable.

## A2b. Colour pictures that change with the date

On the Months view, "Temperature" and "Star colours" follow the blink: one picture per
visit, redrawn whenever the blink moves to another date (`web/src/colour/visit.ts`; the app passes
the blink's epoch to `filters.epoch`). Each picture is built from the blink's own visit stacks of three
detectors, levelled and composed exactly like A2 (steps 3–6), at the blink's own pixels (2.05″ or
3.08″), so it is sharper than the still picture and objects are not blended at 13″.

| Filter | Red | Green | Blue |
|---|---|---|---|
| Temperature | D6 (4.4–5.0 µm) | D3 (1.6–2.4) | D2 (1.1–1.6) |
| Star colours | D3 (1.6–2.4) | D2 (1.1–1.6) | D1 (0.75–1.1) |

"Compare with 2MASS" was renamed "Near-infrared colours", then "Star colours": the detectors don't split into 2MASS's J, H
and K, so the moving version can't be a 2MASS comparison. The key has its own swatches for these
bands (methane T dwarfs come out green in Star colours, pink in Temperature).

- **Checked on the featured targets' Months 5′ frames (14 Nov):** no pixel at −5σ in any D2, D3 or
  D6 frame (the stacks are already medians over each visit's exposures); field stars' D6/D2 ratio
  0.13–0.18 on every date.
- **Each date sees slightly different wavelengths.** A visit's frame covers part of the detector's
  range: WISE 0855's D6 saw 4.85–5.01 µm in visit 1 (target not detected) and 4.41–4.99 µm in visits
  2 and 3 (7σ and 15σ); Epsilon Indi B's D6/D2 went 0.69, 0.90, 1.12, 0.75. So the key gives each
  date's ranges and says that small colour changes between dates can come from that, while a change
  in position is real.
- **Missing snapshots.** A date without all three detectors gets no picture and says so (R Draconis:
  only 1 of 7 dates has D2, D3 and D6).
- **Saturation.** The visit frames have no per-pixel flags, so the red squares come from the all-sky
  map over the plate's field (A2 step 7): a star too bright for SPHEREx is too bright on every date.
  The "why" box says the core has no usable readings on each date, without claiming a direction.
- **Falls back to the still picture** (and says why) on the Hours view, where the detectors were not
  exposed together, or when a view lacks one of the three detectors.
- "Single colour" stays a still picture: the blink's snapshots each mix many wavelengths.

## Checks on real data (14 Nov 2026, 15′ fields)

| Check | Result |
|---|---|
| HPX and HEALPix against astropy, 35 positions | All within 10⁻⁴ px. One exact four-tile corner is ambiguous, either tile is valid |
| Field stars, 4.6 µm / J flux ratio | Median 0.16–0.19 in the Barnard, Eps Ind B and WISE 0855 fields (blue), as expected for stars |
| Epsilon Indi B (T1 + T6) | Ratio 1.1: 6–7 × redder than the field stars. Near-white, while the stars around it are blue |
| WISE 0855−0714 (Y dwarf) | 10σ at 4.6 µm at the centre (the targets file is already at the SPHEREx epoch). Blended with a blue star one pixel away, so it shows magenta; the blend check says so under the picture |
| Blend check on field stars | Fires on 5 of 219 stars brighter than 10σ at 1.2 and 4.6 µm (2.3%), on WISE 0855, not on Epsilon Indi B |
| Barnard's Star, R Draconis, Eps Ind A | Saturated cores flagged (12, 24 and many deficit frames). Colours marked not reliable |
| Chance saturation flags | Faint pixels never had more than 3 deficit frames in 19,600 pixels; saturated cores had 4–24 |
| Noise test: pixels ≤ −5σ (noise is symmetric, sources are positive) | Mean of frames: 9–24 per field at 4.6 µm. Median of frames: 0 in every window of all three fields, against 270–1,200 at +5σ |
| An early "pure red WISE 0855, 58″ west" | Withdrawn: one frame at +236σ. The median removes it |
| Load time in Chromium (WISE 0855, 15′) | 42 s reading whole rows; 8 s reading only the needed pieces; 3–5 s for the plate's 5′ field |

## A3. Molecules (composition; first named "What things are made of")

Marks the sources whose light shows a molecule, and the patches of sky that glow with PAH dust.
Code: `composition.ts` (the measurement), `indices.ts` (the index maths, a port of the pipeline's
`indices.py`), `compview.ts` (markers, words, two catalogue guards), `engine.ts` (the read),
`index.ts` (the menu, picture and key). Built 14 Nov 2026.

**Per source, never per pixel.** On empty sky a band ratio is noise divided by noise, and a
per-pixel map would paint coloured speckle everywhere. So the picture stays grey (each pixel's
strongest signal among five wide windows, in units of noise) and the result is drawn on top: one
ring per source, and one patch over diffuse glow.

**The picture shows only what was found** (redrawn 15 Nov, after the first version covered the
picture in hundreds of grey rings and the key ran to ten lines).

| Mark | Meaning | Drawn |
|---|---|---|
| Solid coloured ring | **Found.** A second molecule on the same source is a second ring around the first | always |
| Name beside the ring | The molecules found there ("Methane + water vapour") | when the picture has at most 4 found rings |
| Green shade, cell by cell | PAH dust glow of the sky; stronger green = brighter (range in the key). Faintest green = hint | always (hints with the switch) |
| Dashed coloured ring or outline | **A hint, not confirmed.** A band is measured but a check failed; a click says which | switch "Show hints", on at first (real fields held 0–7) |
| Thin grey ring | Measured, none of the nine bands shows | switch "Show the other stars checked", off at first |
| Grey cross | **Can't tell**: too faint, too bright, blended or erratic. Not "nothing there" | same switch |

Nothing is lost by hiding the grey marks: the picture underneath still shows every source,
pointing at any of them reads its state, a click rings it in white and opens its numbers, and the
key always says how many were checked and what the centre source gave ("At the centre. Can't tell:
too bright…"). With the second switch on, the key also says on what share of the picture the dust
glow could be measured at all.

**Colours.** Three ring colours and no more: methane magenta `#d55181`, ices blue `#3987e5`, water
vapour or CO gas amber `#c98500` (the two gases of a cool star's air share one colour; the readout
names which). Marks scattered over a picture must be told apart pair by pair, and these three are
the largest set that stays apart for colour-blind readers (worst pair ΔE 13 under simulated
colour-blindness, 19 in normal vision, OKLab × 100; a fourth ring colour dropped a pair below 5).
PAH is green `#199e70` but a filled patch, so its shape tells it from the rings. All four are at
least 3:1 against black sky, and each ring has a dark edge so it reads over a bright star.

**The key** lists only the molecules that are in the picture, each with its count, then the centre
source, then the two switches. A picture with nothing found says so in one sentence. Clicking a
source shows its verdict in words, the bands found or hinted with depth ± error (σ), what else
could look like them, a link that opens the normal view on that source (sharper photos, and its
spectrum), and, folded, all nine bands with the reason for each "can't tell" or "possible".

**Field.** Always 30′ (140 × 140 map pixels) around the plate's centre, whatever the plate's field
(`COMPOSITION_FIELD`). The zero point needs ordinary stars: 30′ fields held 10–170 per band. A
wider field costs download time in proportion to its area.

**Download.** 83 frames (1.1–5.0 µm; D1 isn't used, see step 1) of surface brightness, about 7 MB,
then the exposure counts (NHITS) only near bright stars (step 4). In Chromium this took 31–44 s on
the four targets: IRSA's connection gave about 0.3 MB/s however the requests were split (one small
range, 100 ranges, or whole blocks), so the time is set by the bytes. The measurement itself takes
1.3 s.

### Steps (`measureComposition`)

1. **Frames 17–101, background off.** Each frame's sigma-clipped median is subtracted; its clipped
   σ is the pixel noise. D1 (frames 0–16, below 1.1 µm) is left out: no band uses it, and some of
   its frames have field-wide artefacts (5,867 pixels over 8σ in one frame of the Epsilon Indi
   field, 2,279 in the hunt zone, against 40–60 in a normal frame).
2. **Sources.** Five windows (the pipeline's `MAG_WINDOWS`: J, H, K, 3.3–3.7, 4.4–4.8 µm), each the
   median of its frames, turned into S/N. A source is a 3 × 3 local maximum at ≥ 5σ in any of
   them, at least 3 pixels from the edge. Using all five finds objects seen only in the red.
3. **Light per frame.** The 3 × 3 pixels minus the median of the ring 3 pixels out (24 pixels). The
   noise of this number is measured, not assumed: the same aperture on a grid over the field, and
   its clipped scatter per frame. That came out 1.5–2.4 times the uncorrelated value (3 × pixel σ),
   which is the map's correlated noise.
4. **Frames left out, per source.**
   - *Lost exposures.* The map drops saturated readings, so a bright star's core has fewer
     exposures than the sky around it. A frame is dropped when the source's brightest pixel kept
     under 70% of the median of the 7 × 7 box, or any pixel holding its light under 50%. A source
     is **too bright** (all bands "can't tell") when 10% of its frames lost exposures at the peak,
     or 4 frames have a hole in the core, or any pixel reaches 30 MJy/sr. Measured on three fields:
     below 30 MJy/sr the brightest pixel kept all its exposures (10th percentile 1.00); between 30
     and 100, some stars 0.46–0.92; above 100, 0.35–0.57.
     Exposure counts are only read within 5 pixels of a pixel ≥ 10 MJy/sr (`brightMask`), a factor
     of three under where losses begin; that was 9–18% of five fields (42% and 83% in the two
     crowded ones). Elsewhere no frame is dropped for its count.
   - *Glitches.* A frame is dropped when its 3 × 3 pixels disagree with the shape of the two
     frames either side (their median, scaled to this frame's light) by more than 6σ and more than
     half the source's light in that frame, or when its light is below −5σ. Ordinary stars'
     shapes vary by up to 40% of their light between frames (99th percentile), so the threshold is
     50%. A smaller glitch stays in; steps 5 and 7 keep it from becoming a detection.
5. **Errors.** Per frame: aperture noise ⊕ ε × flux. ε is the frame-to-frame jitter of bright
   stars, measured in each field from the second differences of stars over 50σ: 8–10% in all seven
   fields, far more than their photon noise (0.2–2%). Then the source's own roughness: the robust
   scatter of its second differences in units of these errors. Above 1 the errors are multiplied
   by it; above 2.5 the source is **erratic** (a variable star, a mover or damaged data) and all
   its bands are "can't tell".
6. **Indices, zero point, error scale.** The pipeline's nine indices (`INDEX_WINDOWS`), with its
   window weights, its 50% coverage rule (a window on the blank frames 50–51 with less is "no
   data"), and its power-law continuum. Channel edges are the midpoints between neighbouring
   frames. Then, per band and per field, from the reference stars (continuum S/N ≥ 20, band ≥ 10,
   not too bright, blended or erratic; at least 8, else the band is **not calibrated** and
   "can't tell"):
   - zero point r₀ = their sigma-clipped median band ÷ continuum. It came out 0.89–1.08: without
     it, a 5–10% offset between detectors would look like a band in every star;
   - error scale k = the robust spread of their pulls, at least 1 (1.00–1.37 measured);
   - depth D = 1 − (B/C)/r₀ (excess (B/C)/r₀ − 1 for PAH), σ_D from k × the ratio's error and
     the zero point's own error.
7. **States.** A band is "can't tell" if the source is too bright, blended (another source within
   2 pixels with ≥ 40% of its peak in any window), erratic, uncalibrated, without data, or its
   continuum is under 5σ. Otherwise "measured". It **fires** at D/σ_D ≥ 5 and D ≥ the pipeline's
   minimum (`LABEL_MIN_DEPTH`: methane 0.25 and 0.15, water 0.05, ices and PAH 0.10). A band that
   fires is **found** only if all of these hold; otherwise it is **possible**:
   - *More than one frame.* Leaving any one frame out, it stays ≥ 3σ and ≥ half the minimum
     depth. A band that can't spare a frame (under a quarter of its weight left) fails. So PAH and
     CO ice on a single star, whose bands are one map frame wide, can only ever be "possible".
   - *The pipeline's look-alike guards* (`classify.band_labels`), as far as the map allows.
     Methane: red (J − [4.6] > −0.5 AB, using J's 2σ upper limit when J isn't seen) and a second
     band agreeing at ≥ 3σ (the other methane band, or water). Ices: no water vapour at ≥ 3σ (a
     red dwarf's own water absorbs at 2.7–3.2 µm), reddened (J − [4.6] ≥ 0), and for CO ice no
     2.3 µm CO gas. Water vapour: both of its bands (1.4 and 1.9 µm), which is stricter than the
     pipeline.
   - *Ice is not methane* (added 15 Nov, not in the pipeline). Water ice's band is wide (2.8–3.6
     µm) and dips the methane window at 3.2–3.45 µm. In four star-forming clouds every ice source
     also fired "methane 3.3 µm" (30–105% deep), and one, which also showed water vapour, passed
     every other guard: a false methane. So methane at 3.3 µm counts only where the ice band
     (3.05 µm) was measured and is under 3σ. Where the ice band is at least as deep as the dip
     (true of every ice source seen: ice 71–114%), the dip is reported as ice ("measured, not
     there"); otherwise methane stays possible. Epsilon Indi B is unaffected: its ice band is
     flat (−10 ± 14%) under a 68% methane dip.
   Two rules add detections, each with its false-alarm rate measured:
   - *Water vapour as a pair.* Its two bands always come together, so they are also tested
     jointly: each ≥ 3σ and deep enough, together ≥ 5σ (root sum of squares), and without any one
     frame of either band each still ≥ 2σ and together ≥ 4σ. On 2,881 sources of eight ordinary
     fields the mirror-image test (both bands too bright, which nothing real does) fired 0 times.
     The real one fired on 3 sources before the leave-one-out step and on 2 after it: Epsilon
     Indi B, and a star in the R Draconis field with the bands 40% and 55% deep.
   - *One-frame ices beside water ice.* CO₂ and CO ice are one map frame wide, so leave-one-out
     can't pass for them. Where water ice (three frames, confirmed on its own) is found in the
     same source, a CO₂ or CO ice band that passed every other check counts as found, and the
     detail table says why. A chance one-frame dip that size was seen in 3 of 2,983 band
     measurements on ordinary sources (0.1%), none of them on a source with water ice.
   - *Catalogue guards* (`applyContext`, from the plate's own lists). Methane on a source Gaia
     sees brighter than G = 17 is an ordinary star. (The pipeline's limit is 19.5, which would
     reject Epsilon Indi B itself: Gaia has it at G = 18.0.) Any band on a source within 2 pixels
     of a known asteroid (V ≤ 20) at one of the plate's dates, or on a plate target that is a
     variable star or a solar-system body, is possible only.
8. **Diffuse PAH glow.** Pixels in blocks of 16, 8 and 4 a side; a block's value per frame is the
   median of its pixels (stars and glitches drop out), its noise the scatter of the blocks. Same
   index, zero point and thresholds. A block with a hole or a pixel ≥ 20 MJy/sr is "can't tell".
   The PAH band is one frame wide, so instead of leave-one-out a found block needs a neighbouring
   block of the same size at ≥ 3σ; alone, it is "possible".

### Checks on real data (14 Nov 2026, 30′ fields)

Reproduce: `cd web && npx vite-node src/colour/check_composition.ts` (reads NASA's map through the
site's own code, about 10 s a field).

| Field | Expected | Result |
|---|---|---|
| Epsilon Indi B (T1 + T6) | Methane | **Found**: methane 3.3 µm 68 ± 3% deep (19.6σ), water vapour 1.4 µm 77 ± 3%, 1.9 µm 69 ± 4%. Methane 1.67 µm 23 ± 11%: not counted on its own, agrees |
| WISE 0855−0714 | Probably can't tell | **Can't tell**: blended with the star one pixel away; continuum 4–8σ |
| Barnard's Star | Water vapour, no methane | **Can't tell**: too bright, 82 of 83 frames lost exposures. Not the hoped-for water, and the honest answer for this map |
| R Draconis | Water vapour + variability warning | **Can't tell**: too bright (69 of 83 frames), and the key carries the variable-star warning |
| M82 | PAH emission | **19 squares (4 px) and 5 (8 px) found**, on both sides of the disc along the minor axis, where M82's dusty wind is. The core is too bright |
| Hunt zone, 125.5 +19.4 | Nothing | 805 sources, 0 found, 1 possible |
| Cygnus, 305 +40 (crowded) | Nothing | 1,009 sources, 0 found, 0 possible; CO 2.3 µm not calibrated (no clean reference stars) |
| A 62′ hunt-zone field | Nothing | 3,376 sources, 0 found, 3 possible |
| Rho Ophiuchi cloud, 246.70 −24.40 | Ices (stars behind cold dust) | **11 sources with ices**: water ice 46–100% deep, CO₂ ice on several. No methane |
| NGC 1333, 52.27 +31.35 | Ices, PAH | **11 sources with ices** in the embedded cluster; **29 PAH squares** on the reflection nebula north of it. No methane |
| Chamaeleon I, 167.00 −77.30 | Ices | **5 sources with ices**. No methane (one false methane before the ice rule) |
| Serpens core, 277.49 +1.23 | Ices | **4 sources with ices** (water, CO₂ and CO). No methane |
| Taurus, 68.0 +26.0 (cloud edge) | Few | 1 source with water ice |
| Orion Nebula, 83.82 −5.39 | PAH everywhere | 1 source with ice; no PAH squares: the glow fills the field, so it is taken off as background, and much of it is too bright |

- **False "found": none known.** About 5,400 sources in the seven ordinary 30′ fields, nine bands
  each: the found sources are Epsilon Indi B and one star in the R Draconis field with both water
  bands 40% and 55% deep (the pair rule). "Possible" marks: 0–2 per field. In the clouds, ices are
  found where stars sit behind cold dust and nowhere in the ordinary fields; they are not checked
  against an independent spectrum yet.
- **Why ordinary fields show almost nothing.** A band's error per source is 11–19% (median; 7–13%
  for the best tenth), so only bands deeper than about 35% reach 5σ. The common features are
  shallower (water in red dwarfs 5–20%, CO in giants 10–20%); the deep ones are methane and water
  in brown dwarfs, ices behind clouds, and PAH glow. The error is the map's own: the 8–10% jitter
  is not shared by neighbouring stars (median pair correlation within ±0.1 at every separation
  from 2′ to 45′ with ten or more pairs, three fields), so field stars can't calibrate it away,
  and a 5 × 5 aperture has it too (9%). At 3σ and above there is a small real excess in the water
  bands (41 measurements above, 11 below, on 8,813 sources), too mixed with noise to call one by
  one, except by the pair rule.
- **What the guards are worth.** With only the 5σ and depth cuts (first prototype), each field
  had 1–5 false detections on ordinary stars, several of them on very bright stars whose spectra
  were jagged from partly lost exposures. The roughness scaling, the peak-pixel exposure rule,
  leave-one-out and the two-band rule for water removed them.
- **Significances aren't inflated.** For measured sources the spread of D/σ_D is 0.5–1.0 in every
  band and field (1 would be exact; below 1 is cautious).
- **The index maths is the pipeline's.** `indices.test.ts` compares all nine indices on ten
  spectra (power laws, bands, missing channels, near-zero continuum) with values written by the
  pipeline's own `indices.py` (`make_indices_ref.py` → `indices_ref.json`): equal to 2 × 10⁻⁵.
- **The measurement was first written and tuned in Python** on the same seven fields. With the
  same inputs the site's code gave the same state for 99.9% of source-bands, the rest sitting on
  a threshold. (Reading exposure counts only near bright stars came after; it changed no "found"
  or "can't tell (too bright)" result in the seven fields.)
- **Synthetic fields** (`composition.test.ts`): a T-dwarf spectrum is found; a dip on a star that
  isn't red, one water band alone, a bump in the one PAH frame, a whole-star drop in one frame
  and half-light glitches in six different frames are never "found"; lost exposures, a blend, a
  flickering star and missing data are "can't tell"; too few stars is "not calibrated"; a glow is
  found only where it was put.
- **In the browser** (Chromium, the local site): the four targets and M82 loaded in 31–44 s and
  showed the results above. M82 was opened at plain coordinates with the API down: the filter
  doesn't need it.

### Added 15 Nov (second round): dust glow of the sky, water's three bands, glare

The user found the filter marked almost nothing in most pictures. Three things were tried on real
data to raise the sensitivity per star, and all three failed, so the limit is the map itself:
neighbouring stars don't share their frame-to-frame jumps (no calibration possible); a 5 × 5
aperture has the same 9% jitter; and measuring on the map's own HEALPix grid instead of the
resampled picture gave 8.7% against 9.0% (the resampling uses 14% of the map's pixels twice, but
that is not where the jitter comes from). What did work:

**1. Dust (PAH) glow of the sky itself, in absolute brightness (`dustGlow`).** The first version
looked for the 3.3 µm glow only above the picture's own background, so a glow that fills the
picture was subtracted away. But the map's sky level itself carries the band: per pixel,
`excess = I(3.29 µm) − max(line through six continuum frames at 2.95–3.12 and 3.57–3.77 µm, mean of
the red three)`. Stars (anything ≥ 5σ in the continuum frames, grown by one pixel) are left out; the
picture is cut into cells of 7 pixels (1.5′) and a cell's value is the median of its star-free
pixels (at least 12 of 49; fewer pixels, larger error). The cell noise is measured on two frames
that hold no band. Found: ≥ 5 × noise and ≥ 0.008 MJy/sr, with a neighbouring cell at hint level,
and the 3.2 + 3.4 µm wings holding ≥ 15% of the peak over the found cells. Hint: ≥ 3 × noise and
≥ 0.004 MJy/sr.

Checked on 31 fields of 30′ (24 new ones spread over the sky plus the 7 above) against the IRAS
100 µm dust map (IRSA DUST service), which is independent of SPHEREx:

| IRAS 100 µm, MJy/sr | Fields | Sky excess at 3.3 µm, MJy/sr | Cells found (of 400) |
|---|---|---|---|
| under 2.5 | 9 (NGP, SGP, COSMOS, CDFS, Epsilon Indi, R Dra, hunt zone, …) | −0.002 to +0.004 | 0 in each |
| 1.7 (WISE 0855) and 2.2 (260 +30) | 2 | +0.008, +0.004 | 23, 10 |
| 2.5–8 | 9 | +0.005 to +0.047 | 0–396; two fields (30 +40, 220 −40) hints only: their wings don't confirm |
| 18–840 (Milky Way, clouds) | 11 | +0.05 to +1.65 | 228–376 |

Excess ÷ IRAS 100 µm is 0.0014–0.006 over a factor of 800 in dust. The same search on the two
no-band frames found 0 cells of 23,452. The floors (0.008 and 0.004 MJy/sr) come from the first
row: the map's zero level differs from frame to frame by that much.

**2. Water vapour through three wide bands together (`WATER_WIDE`, `JOINT_*`).** Bands 1.33–1.50,
1.78–2.05 and 2.45–2.95 µm with their own continuum windows; each ≥ 1.5σ, their weighted mean ≥ 5σ
and ≥ 5%, the two one-detector bands alone ≥ 4σ, and all of it surviving any one frame left out.
On the 31 fields: 17 sources (10 in Chamaeleon I, a nearby cluster of young cool stars; 2 in
NGC 1333; 2 in Rho Ophiuchi; Epsilon Indi B; 2 in ordinary fields). The mirror image (three
"emission" bands) marked 0 of 6,654 measurable sources.

**3. Glare guard (`GLARE_SHARE`, `GLARE_CANT`), and a correction.** The first run of rule 2 marked
four faint sources beside R Draconis and one beside Epsilon Indi A. The light around each was 3–13
times its own: they sit in the bright star's glare, and R Draconis itself has deep water bands, so
the "water" was the star's scattered light. Now a source whose surroundings (the ring's median over
its 9 pixels) reach half its own light keeps a sign only as a hint, and at twice its own light
nothing is said. This also withdraws the one "water vapour" star in the R Draconis field reported
in the first round, and turns some ice detections inside bright nebulae into hints (NGC 1333:
11 → 5 found; Rho Ophiuchi 11 → 8; Chamaeleon I 5 → 4; Serpens 4 → 2).

What an ordinary picture shows now: the dust glow wherever the Milky Way's dust is brighter than
about 3 MJy/sr at 100 µm (most of the sky within about 30° of the Galactic plane, and patches
beyond), and a ringed star only where a band is deeper than about 15–35%: brown dwarfs, young cool
stars, stars behind clouds. Shallower bands (water in ordinary red dwarfs, CO in giants) need the
per-exposure photos; that is the pipeline's job, not this filter's.

### What A3 can't do

- **It can't see variability.** The map merges about a year, each wavelength on its own dates.
  A star that changed brightness between the dates of a band and its continuum looks like a band.
  Only our named variable targets are warned about; the roughness test catches the wild cases.
- **It doesn't know stars from galaxies.** The pipeline's "not a galaxy" and "not a star" guards
  aren't applied. Found water or CO on a galaxy, or possible PAH on a star, would need the card.
- **Gaia and asteroid guards cover the plate's own lists only** (the blink's field and dates).
- **Bands one frame wide can't be confirmed on a single source** (PAH, CO and CO₂ ice): possible
  at best, unless water ice is found in the same source.
- **A glow that fills the 30′ field is invisible**: each frame's median is taken off as
  background (the map's zero level is zodiacal light, which we can't separate). Orion shows this.
- **"CO ice" near young stars can be CO gas** around them, which absorbs at the same 4.67 µm.
- **Bright stars are lost.** Anything saturating (roughly J or K brighter than 10–11 AB) is
  "can't tell", including Barnard's Star and R Draconis.
- **Many sources are "can't tell"**: a band needs its continuum at 5σ after the 8–10% jitter, and
  close pairs are set aside, so a quarter to a half of a field's sources are measured.
- **It is a first look.** The better measurement is the per-exposure spectrum (`/api/object`, and
  the pipeline's `sources.parquet` where a zone was processed): real error bars, a date per point,
  6″ pixels. Showing those results in this filter where they exist is the next step, and needs
  P1 or P2 to publish them.

## Saturated stars: what we tried, and what might work

Flagging a saturated star tells the viewer not to trust its colour; it doesn't give the right
colour. Recovering it is open, and worth another try.

**Tried: rebuilding the colour from the star's wings in the cube** (14 Nov). The core is missing,
but the pixels around it are not saturated, so we scaled the light in a ring around the core up by
the share of a star's light that falls in that ring, measured per window on unsaturated stars in
the same field. Test: on 26 bright unsaturated, isolated stars in four 30′ fields (Barnard, Eps Ind
B, WISE 0855, R Dra), hide the core, rebuild, compare with the true value (half the stars set the
ring share, the other half are tested).

| Ring | Share of a star's light in it | 4.6 µm / J colour error, typical | 90% of stars within |
|---|---|---|---|
| Outside a 3 × 3 core (radius 2–3 px) | 6–14% | ±67% | ±140% |
| Outside a 5 × 5 core (radius 3 px) | 3–9% | ±100% | ±200% |

At the cube's 12.9″ pixels a star's light sits almost entirely in one pixel, so the ring holds
little of it and neighbouring stars and noise dominate. Errors this size would invent red stars (an
ordinary star and a cold brown dwarf differ by about ×6 in this colour), so the flag stays. This
says the cube is too coarse for this simple method, not that the colour can't be recovered.
Script: kept with the session notes (`wings.ts`); it is short and can be rewritten from the
description above.

**Ideas that might work:**

- **Fit the star's shape to its unsaturated pixels in the Level 2 images.** Each image has 6.15″
  pixels (twice as fine), its own effective PSF (`EPSF` HDU) and per-exposure saturation flags
  (OVERFLOW, NONLINEAR), so the wings hold far more of the light and the model of the star's shape
  is known. This is the standard method for saturated stars. It fits the pipeline (which already
  fits PSFs and re-centres saturated dots on the wings) rather than the browser. Validate it with
  the same hide-the-core test before showing it.
- **Use a better wing model in the cube.** Fit each star's sub-pixel position and a PSF built from
  the field's own bright unsaturated stars, instead of one ring share per field. May cut the
  scatter; untested.
- **Show catalogue colours as a labelled stand-in.** For flagged stars only, colour from 2MASS
  (J, K, measured with short exposures for bright stars) and WISE (4.6 µm), drawn distinctly and
  labelled as not SPHEREx. WISE also saturates on the brightest stars (W2 brighter than about
  6.5 mag) and its corrections there need checking.
- **Use the partial exposures.** Barnard's Star kept 70% of its exposures at 4.6 µm and 17–28% at
  shorter wavelengths: per-exposure fluxes from the kept readings, with their own saturation flags,
  might give a usable colour where a window isn't completely lost.

## Not done yet

- A3 is on the plate (above), not yet on the Sky globe. There it would draw the same markers in
  Aladin, by sky position.
- A3 from per-exposure spectra (`/api/object`, the pipeline's sources) where they exist: see
  "What A3 can't do".
- The saturation rule was calibrated on four fields; a dense field near the Galactic plane
  should be checked.
