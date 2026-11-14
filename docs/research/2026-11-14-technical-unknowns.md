# Technical unknowns for SkyShift (14 Nov 2026)

Research only. No SPHEREx FITS image data was downloaded. Small metadata endpoints (SIA/TAP tables, HiPS `properties`, SkyBoT, MOST, Gaia counts) were queried live on 14 Nov 2026 (UTC ~00:00–01:30).
Labels: **confirmed** = read on an official page or seen in a live response; **likely** = strong indirect evidence; **unknown** = not found.

---

## 0. Two things that change the plan the most

1. **CDS (Strasbourg) has a cybersecurity incident.** The CDS fallback page (https://aladin.cds.unistra.fr/, updated 9 Nov 2026) says CDS is "experiencing a cybersecurity incident" and that "access to SIMBAD, VizieR, Aladin image surveys, X-Match" is degraded. Live checks: the Aladin Lite CDN path `aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js` returns 404, the docs path returns 404, `alasky` HiPS tiles return 404, `alaskybis` doesn't respond, and SIMBAD returns 404. The MocServer and the VizieR front page still answer. **Confirmed.** That's why the link check got a 404 for the Aladin docs.
2. **SPHEREx cadence is not "one frame per pass".** A live SIA query at RA 125.5, Dec +5.5 (inside our patch) returned 226 DR1 images = 115 exposures × 2 detectors. Dates run from 2025-10-20 to 2026-05-06 in two seasons, Oct–Nov 2025 and Apr–May 2026. Those split into 42 short sessions of 1–5 exposures taken 2.1 min apart, and the sessions are hours to days apart. Each session uses a detector pair: D1+D4, D2+D5 or D3+D6. **Confirmed** (my analysis of the SIA output). Main-belt asteroids move about 30″/h. That's under 1 px inside a session, but arcminutes between sessions, so they get averaged out of any per-pass stack. Also, `access_estsize` ≈ 65,693 KB (about 66 MB) per L2 file.

---

## 1. Aladin Lite v3

| Item | Finding | Status |
|---|---|---|
| Current version | npm `aladin-lite` dist-tag `latest` = **3.9.2-beta** (15 Sep 2026). The newest non-beta on npm is **3.8.2** (6 Mar 2026). On GitHub the newest non-prerelease is v3.8.1 (5 Mar 2026), and v3.9.0-beta is from 1 Jun 2026. The CDS fallback serves `aladin_3.9.0-beta.js`. | confirmed (npm registry, GitHub API) |
| Embed via CDN | The official `https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js` (README) **is 404 right now**. Working mirrors (HTTP 200): `https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js` and `https://unpkg.com/aladin-lite@3.8.2/dist/aladin.js`. The CDS fallback copy is at `https://aladin.cds.unistra.fr/aladin_3.9.0-beta.js`. | confirmed |
| Embed via npm | `npm i aladin-lite`, then `import A from 'aladin-lite';` (README). | confirmed |
| Working docs | JSDoc API: https://cds-astro.github.io/aladin-lite/ (generated 5 Nov 2026), with pages `A.html`, `Aladin.html`, `HiPS.html`, `Catalog.html`, `global.html`. The README links the examples at `aladin.cds.unistra.fr/AladinLite/doc/API/examples/`, which is 404 during the incident. | confirmed |
| Init | `A.init.then(() => { aladin = A.aladin('#div', {...}) })`. `A.init` "Throws an error if WebGL2 is not supported". | confirmed |
| Options | `AladinOptions` includes `survey` (default `"P/DSS2/color"`, which resolves to CDS and is **broken now**), `target` (default `"0 +0"`), `fov` (default 60), `cooFrame`, `projection`, `hipsList`, `showSimbadPointerControl`, `showCooGridControl` and others. | confirmed |
| Position/FoV | `aladin.gotoRaDec(ra, dec)`, `aladin.gotoObject(name)` (needs Sesame, so expect it to be flaky now), `aladin.setFoV(deg)`, `aladin.setFoVRange(min,max)`, `aladin.getRaDec()`, `aladin.getFov()`, `aladin.pix2world(x,y)`, `aladin.world2pix(ra,dec)`. | confirmed |
| Custom HiPS by URL | `A.HiPS(urlOrId, options)` (`A.imageHiPS` is deprecated), then `aladin.setBaseImageLayer(hips)` or `aladin.setImageLayer(...)`. The id "Can be: An http url towards a HiPS". `aladin.addNewImageLayer`/`setOverlayImageLayer` add overlays. A HiPS supports `setOpacity`, `setColormap`, `setCuts`, `setContrast`, `setBrightness` and `setGamma`. | confirmed |
| Catalogs | `A.catalog(options)` then `aladin.addCatalog(cat)` and `cat.addSources([A.source(ra,dec,data), ...])` or `addSourcesAsArray`. `A.marker(ra, dec, options, data)` gives a marker with a popup. `A.catalogFromURL(url, options, ok, err)` loads a VOTable/CSV URL. There are also `A.catalogFromVizieR`, `A.catalogFromSimbad`, `A.catalogFromNED`, `A.catalogHiPS` and **`A.catalogFromSkyBot(ra, dec, radius, epoch, queryOptions, options)`**. Catalog options include `onClick: 'showTable'/'showPopup'`, `shape`, `color` and `sourceSize`. | confirmed |
| catalogFromSkyBot caveat | In the 3.8.2 build it calls `http://vo.imcce.fr/webservices/skybot/skybotconesearch_query.php`. That URL is plain http, which browsers block as mixed content, and it returned **403** to us. Don't use it. Proxy through our backend to `ssp.imcce.fr` instead (see Q5). | confirmed (source grep plus live 403) |
| Click events | `aladin.on(event, fn)`. The allowed list in the 3.8.2 build is `select, objectsSelected, objectClicked, objectHovered, objectHoveredStop, footprintClicked, footprintHovered, positionChanged, zoomChanged, rotationChanged, click, rightClickMove, mouseMove, wheelTriggered, fullScreenToggled, cooFrameChanged, resizeChanged, projectionChanged, stackChanged`. Use `click` for "open any spot" (the callback includes ra/dec) and `objectClicked` for Gaia/suspect dots. `positionChanged` and `zoomChanged` are "throttled every 100ms". | confirmed (docs plus build). The exact `click` payload fields are **likely** `{ra, dec, x, y}`. Check in the console on day 1. |
| HiPS cubes | The build reads `hips_cube_depth` / `hips_cube_firstframe` and has a cube-slice UI. `HiPS.setFrequency` exists, though the docs describe it for "freq HiPS3D" objects. | likely, not tested in a browser |

Minimal embed for this weekend:
```html
<script src="https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js"></script>
<script>
A.init.then(() => {
  const aladin = A.aladin('#sky', { target: '133.79 -7.25', fov: 3,
    survey: 'https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color', // NOT a CDS id
    showSimbadPointerControl: false });
  aladin.setOverlayImageLayer(A.HiPS('https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube', {opacity: 0.8}));
  aladin.on('click', e => console.log(e));          // open spot -> /api/frames
  const gaia = A.catalog({name: 'Gaia DR3', color: '#4af', onClick: 'showPopup'});
  aladin.addCatalog(gaia);                           // fill from our backend
});
</script>
```

## 2. SPHEREx HiPS for Aladin Lite

| HiPS | URL / ID | Order / resolution | Type | Works now? |
|---|---|---|---|---|
| **IRSA SPHEREx DR1 all-sky cube** | `https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube` (creator_did `ivo://IPAC/P/SPHEREx/DR1/cube`) | `hips_order = 5`, `hips_pixel_scale = 0.0035786` deg (**≈12.9″/px**, about 2× coarser than the native 6.15″), 512-px PNG tiles, plus `fits-cube` tiles at order 5 | **Spectral cube**: `hips_cube_depth = 102`, 6 bands × 17 channels, with "frame index = 17*(band-1) + (channel-1)". Frames 50 and 51 are blank (dichroic). Stretch is asinh, cut 0–40. t_min to t_max is MJD 60789.75–61174.49. Release date 2026-11-01. | **Yes.** Tiles `Norder3/Dir0/Npix100.png` and `Npix100_10.png` return 200 with `Access-Control-Allow-Origin: *`. **Confirmed.** Not registered in the MocServer, so load it by URL. |
| CDS SPHEREx QR2 colour | ID `CDS/P/SPHEREx/QR2/color` → `https://alasky.cds.unistra.fr/SPHEREx/color` | order 7, 8.946e-4 deg (≈3.2″/px), PNG | **Colour image** made from the 6 per-band HiPS | **No, tiles 404 (CDS incident).** Confirmed. |
| CDS SPHEREx QR2 per band | `CDS/P/SPHEREx/QR2/D1` … `D6` | order 7, PNG + FITS | single-band image | **No, tiles 404.** Confirmed. |
| Bucket `s3://nasa-irsa-spherex/hips5_cube/hips_20261026/` | Listed as a prefix at the bucket root, but a listing under it returns `KeyCount 0` and `properties` returns 404 | (n/a) | (n/a) | **unknown**. Use the IRSA HTTP copy above instead. |

**Fallback infrared HiPS (all on IRSA, CORS `*`, confirmed live):**
- 2MASS colour: `https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color` (ID `CDS/P/2MASS/color`, order 9, ≈0.8″/px, JPEG)
- AllWISE colour: `https://irsa.ipac.caltech.edu/data/hips/CDS/AllWISE/RGB-W4-W2-W1` (ID `CDS/P/allWISE/color`, order 8, ≈1.6″/px). Mirrors: `https://skies.esac.esa.int/AllWISEColor`, `https://hips.china-vo.org/m/CDS_P_allWISE_color`
- AllWISE W1/W2 single bands: `.../CDS/AllWISE/W1`, `.../W2` (JPEG + FITS)
- The full IRSA list (28 HiPS) is at https://irsa.ipac.caltech.edu/data/hips/list

Recommendation: use the IRSA SPHEREx cube as the "real SPHEREx sky" layer, which shows frame 0 by default. Pick a frame such as band 1 or band 4 for display, and keep 2MASS/AllWISE colour as the pretty base. Zoomed-in inspection needs our cutouts, because 12.9″/px is coarse.

## 3. IRSA MOST (Moving Object Search Tool)

- **Web form** (https://irsa.ipac.caltech.edu/applications/MOST): inputs are an object name, NAIF ID, MPC one-line elements or manual elements; an observation begin/end in UTC; an ephemeris step; and an image dataset, where `spherex` is one option. Output modes are Regular, Full, Brief, VOTable and Gator. It can also make FITS/DS9 region tarballs. **Confirmed.**
- **Program interface** (https://irsa.ipac.caltech.edu/applications/MOST/MOSTProgramInterface.html): endpoint `https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most`. Parameters are `catalog=spherex`, `input_type=name_input|naifid_input|mpc_input|manual_input`, `obj_name`, `obs_begin`/`obs_end` (`yyyy+mm+dd`), `ephem_step` (default 0.25 d) and `output_mode=Regular|Full|Brief|VOTable`. **Confirmed.**
- **Live test:** `...nph-most?catalog=spherex&input_type=name_input&obj_name=4&obs_begin=2025+08+01&obs_end=2025+08+31&output_mode=Brief` answered in 1.8 s with 8 SPHEREx DR1 images of 4 Vesta. Columns include `ra_obj, dec_obj, sun_dist, geo_dist, dist_ctr, phase, vmag, match, obs_id, planeid, detector (D3/D6), mjd_start, mjd_end, ra1..dec4` (corners) and an `ibe/data/spherex/dr1/...fits` URI. `obj_name=Vesta` returned "Object Name Error", so use numbers or NAIF IDs. `obj_name=Bamberga` worked but had no August 2025 coverage. **Confirmed.**
- **astroquery:** `from astroquery.ipac.irsa.most import Most`; `Most.query_object(output_mode="Regular", catalog="spherex", obj_name="20000004", obs_begin="2025-08-01", obs_end="2025-08-31")` returns a dict with `'results'` (Table), `'metadata'` (Table) and `'region'` (DS9 URL). Tutorial: https://caltech-ipac.github.io/irsa-tutorials/most-queries. **Confirmed** (from the tutorial; not run here).
- **Using it to validate "we re-found N known asteroids":** MOST is object-centric (one object at a time). It answers "which SPHEREx exposures should contain asteroid X, and where", but it can't list every asteroid in a field. Workflow:
  1. Field-centric: SkyBoT for each exposure's centre and epoch (Q5) gives the list of known objects with V < limit.
  2. Pick 20–50 of them, run MOST, and get each object's per-exposure predicted RA/Dec and `obs_id`.
  3. Count how many of our pipeline detections in those exact exposures fall within ~1–2 px of the prediction. Report recall vs V mag ("we re-found 41/50 with V<17").
  4. Use MOST as an independent check of our SkyBoT handling (time scale, observer location).

## 4. Comet 3I/ATLAS SPHEREx page

URL: https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/index.html
- **What's there:** an overview ("observed the interstellar comet 3I/ATLAS in August and December 2025") and a VOTable `obscore_3I_Atlas.vot` that lists every SPHEREx image with `comet_ra`/`comet_dec` per exposure, plus `obs_id`, `spherex_detector`, `t_min`/`t_max`, `s_region` and a datalink `access_url`. It also has wget/curl scripts for the full image set, a note that the comet has "low signal-to-noise at many wavelengths" and is "spatially extended", and three Lisse et al. papers. **Confirmed.**
- **The table, analysed live (259 rows, collection `spherex_qr2`):**
  - 2025-08-01 08:00–16:13: 14 images, the comet moves 0.23°, about 99″/h ≈ 16 px/h
  - 2025-08-06 → 08-15: 147 images, RA 243–249, Dec −17
  - 2025-12-08 00:06 → 12-09 12:01: 43 images, RA 172.3–173.8, Dec +2.8 to +3.4
  - 2025-12-14 04:26–23:59: 55 images, RA 166.4–167.3, Dec +5.1 to +5.4, about 176″/h ≈ 29 px/h
  - The same exposures exist in **DR1**: a SIA query with `TIME=61023.99 61024.01` at the comet position returned `2025W50_2A_0854_1` D3/D6 from `dr1/level2/...`. **Confirmed.**
- **As a demo target: strong.** It's famous, motion is tens of px per hour, and NASA gives the positions for every exposure, so no ephemeris work is needed. Cutouts are about 0.3–0.5° per session. Caveats: it's faint and extended at many wavelengths. The December positions (RA 166–174) lie **just east of our 5% patch (RA 105–150)**, so treat it as a pre-cached "featured object", not a pipeline result. A cutout per exposure centred on `comet_ra/dec` gives a comet-centred movie; a fixed-sky cutout gives the "it moves across stars" movie.

## 5. SkyBoT (IMCCE) cone search

- **Endpoint (working):** `https://ssp.imcce.fr/webservices/skybot/api/conesearch.php?...`. Docs: https://ssp.imcce.fr/webservices/skybot/api/conesearch/. The old `vo.imcce.fr/...skybotconesearch_query.php` returned **403**. **Confirmed.**
- **Parameters (non-standard interface):**
  - `-ep` (epoch as JD or ISO; the valid range ends 2060)
  - `-ra`, `-dec` (deg)
  - `-rd` radius in deg (0–10°), or `-rm`/`-rs`; a box can be given with `-bd` etc.
  - `-mime=votable|html|text|json`
  - `-output=object|basic|obs|all`
  - `-loc` (observer code, default 500 = geocentre)
  - `-filter` (position error, default 120″)
  - `-objFilter` (default 111 = asteroids, planets, comets)
  - `-refsys=EQJ2000|ECJ2000`
  - `-from=<appname>`

  An SCS-standard form also exists (`RA`, `DEC`, `SR` ≤ 30°, `TIME`, `VERB`, `RESPONSEFORMAT`, `MAXREC`). **Confirmed.**
- **Output:** columns `Num | Name | RA(h) | DE(deg) | Class | Mv | Err(arcsec) | d(arcsec) | dRA, dDEC (arcsec/h) | Dg | Dh` (basic), plus phase, elongation and state vectors with `all`. JSON is a list of objects. The text format is pipe-separated with `# Flag: 1` on success. **Confirmed live:** a 0.5° cone at RA 120, Dec +10, epoch 2026-01-15 returned 12+ main-belt objects in about 6 s (JSON, output=all).
- **Rate limits:** **unknown**, since none are documented on the conesearch page. Send `-from=SkyShift`, cache per (exposure, field), and limit parallelism to a few requests.
- **Python:** `astroquery.imcce.Skybot.cone_search(coord, rad, epoch)` exists (**likely**; not run here).
- **Observer location:** SPHEREx is in LEO. The L2 header has spacecraft position keywords `X_SC`, `Y_SC`, `Z_SC` (km, `XYZ_SC_SYSTEM = 'GEOCENTER'`). Geocentre parallax error for a main-belt asteroid at 1.5 au is about 0.6″ (0.1 px), which is fine. For NEOs it can be several px. Whether SkyBoT accepts a custom geocentric observer position is **unknown**; use 500.

## 6. Gaia DR3

- **Best route:** ESA Gaia Archive TAP (`https://gea.esac.esa.int/tap-server/tap`) via `astroquery.gaia` (`Gaia.launch_job_async`), or plain HTTP TAP. Live sync queries answered in about 3 s. **Confirmed.**
- **Limits:** astroquery docs say synchronous output is "limited to 2000 rows" and async results for anonymous users "are kept for three days". `Gaia.ROW_LIMIT = -1` removes astroquery's client cap. The Gaia FAQ says registered users get "increased quotas". Older FAQ figures for anonymous users: async time-out 120 min, unlimited rows, 20 GB job output. A rate limit is **unknown** (not documented). **Confirmed** for the astroquery statements; **likely** for the old FAQ numbers.
- **Density measured live (gaiadr3.gaia_source):**
  - 1 deg² at RA 125–126, Dec 5–6 (high latitude): 9,597 sources in total, 2,845 at G<18, 1,087 at G<16
  - 1 deg² at RA 106–107, Dec −10 to −9 (near the Galactic plane): 55,984 in total, 13,401 at G<18, 3,943 at G<16
  - Rough estimate for 2,000 deg²: **~6–15 million rows at G<18, ~20–60+ million in total**
- **Sensible pre-fetch:** run async TAP jobs split into 9 RA strips of 5° (about 220 deg² each) on the cloud VM, with a lean column set:
  ```sql
  SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag
  FROM gaiadr3.gaia_source
  WHERE ra BETWEEN 105 AND 110 AND dec BETWEEN -15 AND 25 AND phot_g_mean_mag < 19
  ```
  Use `launch_job_async(q, output_format='fits' or 'csv', dump_to_file=True)` and load the results into DuckDB/Parquet. `gaiadr3.gaia_source_lite` also exists, has fewer columns, and has no `ref_epoch` (it's always 2016.0). Propagate proper motion from 2016.0 to the exposure epoch: about 10 yr, so a 600 mas/yr star moves 1 px.
  - Bulk alternative: `https://cdn.gea.esac.esa.int/Gaia/gdr3/gaia_source/` (CSV.gz by HEALPix ranges). It exists, but the directory listing wasn't parsed. **Likely.**
  - For the browser overlay, don't ship millions of rows. Serve a per-view cone (`/api/gaia?ra&dec&r`) from DuckDB with a G cut that depends on the FoV.

## 7. WiseView and Backyard Worlds

**WiseView** (http://byw.tools/wiseview is **up again**, HTTP 200; https doesn't work. Repo https://github.com/backyardworlds/wiseview, public domain; ASCL 1806.004). Controls read from the live page:
- Inputs: RA/Dec in decimal or sexagesimal; field of view in arcsec (default 60); band W1, W2 or W1+W2 colour (W1 blue, W2 red, average green).
- Blink: "Blink interval (ms)" slider ("Lower interval = faster blink"). README example `speed=274`.
- Time windowing: "Sliding window (yrs)" for co-adding time-resolved epochs; "Skip middle epochs"; "Skip duplicate windows"; "Use only NEOWISE imagery"; "Separate scan directions" (parallax-enhancing); "Smooth by scan direction".
- Stretch: "Min - max pixel values", "Stretch Linearity" (astropy AsinhStretch `a`), "Invert pixel values" and "Maximize dynamic range". The old version had `fixed`, `percent` and `adapt` modes with "Trim Bright".
- **Difference imaging** with an "Exclusion window (yrs)"; **"Gaia DR3 overlay"**; **"Shift and add"** with pmra/pmdec to stack along a proposed motion; **"Synthetic Object"** injection with RA, Dec, mags, PM and reference MJD for testing your eye; "Border epoch 0", a dashed border shown "whenever the animation resets"; an epoch legend of `+ - .` per frame.
- Navigation: click to recentre; click-and-drag opens a new tab with that FoV; a PanSTARRS panel below; "Nearest Zooniverse Subjects".
- **URL hash parameters** carry the whole state, e.g. `#ra=133.787&dec=-7.245150&size=120&band=2&speed=274&minbright=..&maxbright=..&window=0.75&diff=0&gaia=1&invert=1&zoom=8.5&pmra=0&pmdec=0...`. **Confirmed** (live HTML, README, third-party links).

**Backyard Worlds: Planet 9 on Zooniverse** (project 2416; Zooniverse API, confirmed):
- Launched 2017-02-15. State `paused`, 867,588 classifications, 91,833 volunteers, 106,845 subjects (all retired).
- Workflow 27321 ("Reboot 2023 Batch 2"): **retirement after 15 classifications**; config `multi_image_mode: flipbook`, `flipbook_autoplay`, `pan_and_zoom`, `invert_subject`, and switching between flipbook and separate frames.
- The task is a **point-marking drawing tool**: "mark its location in all of the frames". A help text teaches "mover" vs "dipole" and the artifacts (star residuals, ghosts, striations).
- Kuchner et al. 2017 (ApJL 841 L19, arXiv 1705.02919) on the original pipeline:
  - Four unWISE epochs per 256×256-px subtile, each frame a **difference image** (epoch minus the median of the opposite-half epochs), clipped to −0.5σ…9.5σ, with a 12×12 top-hat background removal and an asinh stretch.
  - Users mark the moving objects. They flag interesting objects on **Talk** with `#dipole`/`#mover` hashtags, and advanced users check SIMBAD/VizieR and **report objects that aren't in SIMBAD through a web form**.
  - The science team vets those reports and follows up with spectroscopy. The first discovery was the 117th form submission.

**Worth copying:** blink-interval slider; difference-image toggle; invert; asinh linearity plus min/max sliders; Gaia overlay; state in the URL hash; click to recentre; epoch legend and a dashed "loop restart" border; flipbook vs side-by-side switch; point-marking on the frame (store RA/Dec, not just a vote); retire a suspect after N votes (N≈5–15); "mover vs dipole vs artifact" mini field guide with examples; a "report this" form that sends a candidate to the team after a catalogue check; synthetic-object injection as a training and recall test (a great judge demo).

## 8. SPHEREx Level 2 timing keyword

- **Explanatory Supplement v2.1 (2026-11-10)**, https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf, p. 77, IMAGE header table:
  - `TIMESYS = 'UTC'`, `MJDREF = 0.0`
  - `DATE-OBS` / `MJD-OBS` = time of observation, the same value as `-BEG` in the example
  - `DATE-BEG` / `MJD-BEG` = "MJD at start of observation"
  - **`DATE-AVG` / `MJD-AVG` = "MJD at midpoint of observation"**
  - `DATE-END` / `MJD-END`
  - `XPOSURE = 113.5826 s` ("the scientifically meaningful exposure time") and `TELAPSE = 118.19 s`

  **Confirmed.** This corrects the link check, which said MJD-AVG wasn't found.
- Use **`MJD-AVG`** (UTC) for SkyBoT/MOST epochs. IRSA's own source-discovery tutorial code reads `image_header['MJD-OBS']` (start of exposure, about 57 s earlier, which shifts a main-belt asteroid only about 0.5″).
- **TAP/SIA time columns:** ObsCore `t_min`, `t_max` (MJD; confirmed in a live SIA response, e.g. `t_min=60968.175697`, `t_max=60968.177029`) and `t_exptime=113.5826`. In the `spherex.plane` table the tutorials use `time_bounds_lower`/`time_bounds_upper`. SIA accepts `TIME=<mjd1> <mjd2>` (confirmed live).
- Also in the header: the spacecraft position and velocity at start, mid and end (`X_SC`…`VZ_SC_END`, km, GEOCENTER). Pixel scale is 6.15″ (supplement Table, p. 6).

## 9. Existing SPHEREx motion/variability work and citizen science (for honest pitch claims)

- **Moving sources:** Gagné et al., "A SPHEREx Pipeline and Spectral Library for Ultracool Dwarfs" (arXiv 2604.22012). It describes an extraction tool "tailored for fast-moving point sources" with 6,003 L0–Y1 dwarfs, 947 of them "newly identified". **Confirmed** (abstract). It's a forced-photometry/spectral tool, not a blink search (**likely**).
- **Solar-system:** the SPHEREx Solar System Object Catalog (SSOC) team is extracting asteroid spectra (EPSC 2026 abstract EPSC2026-797; Lisse & Bauer, arXiv 2402.08705), and SOLO is a ground light-curve support project (arXiv 2602.08037). 3I/ATLAS papers come from Lisse et al. **Confirmed** (titles/abstracts via search). These use *known* orbits; no SPHEREx blind moving-object discovery search was found (**unknown/none found**).
- **Variability/transients:** "SPHEREx as a frontier for infrared transients" (arXiv 2602.07129) classifies NEOWISE transients with SPHEREx spectra. A search snippet attributed to arXiv 2604.05332 said the LVF cadence "complicates the identification of variable sources", but the abstract page doesn't mention SPHEREx, so treat that quote as **unverified**. The SPLICES ices overview (arXiv 2603.22135) flags about 22% of targets as variable (search snippet, **likely**).
- **IRSA tools:** the SPHEREx Source Discovery Tool and Spectrophotometry Tool (from the link check) do forced/aperture photometry at user positions, not a motion search. **Confirmed.**
- **Citizen science:** no SPHEREx Zooniverse project was found (**unknown/none found**). But **NASA Space Apps 2026 (14–15 Nov) has a "Planet X and SPHEREx" challenge**, and GitHub shows dozens of repos created 11–14 Nov with near-identical goals ("Flip through every SPHEREx image of a sky patch to spot moving or changing objects", "public web tool to blink spherex sky images over time"), e.g. `zohaiblazuli/noctilith`, `root3315/muninn`, `elsayedelmandoh/planetx-nasa26`, `epsretriever/spherex-planet-x`, `wfrghh-code/ParallaxStack-`. **Confirmed** (gh search). The challenge statement itself wasn't read.
- **So:** don't claim "first SPHEREx blink tool" or "first public SPHEREx dot list". The plan's "none yet, so ours would be the first" is risky. IRSA has the Source Discovery Tool, other teams are building the same thing this weekend, and the SPHEREx team has internal catalogues. Safe claims: "a public, citizen-vetted moving-object suspect list for a 2,000 deg² patch, validated against N known asteroids."

---

## Changes to the plan this implies (priority order)

1. **Don't depend on CDS at all this weekend.**
   - Load Aladin Lite from `cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js` or bundle it from npm.
   - Always pass HiPS **URLs** (IRSA), never CDS IDs, including `survey`, because the default `P/DSS2/color` is broken.
   - Turn off `showSimbadPointerControl`. Avoid `gotoObject`, `catalogFromSimbad`, `catalogFromVizieR` and `catalogFromSkyBot`, and put a built-in name→coordinates list behind our own `/api/resolve`.
2. **Redefine blink frames.** Single-exposure frames and per-session frames (1–5 exposures 2.1 min apart) are the frames for asteroids and comets. Per-pass stacks (a season of 2–4 weeks) average asteroids out; use them only for slow movers (brown dwarfs, high-PM stars) and variables. The `/api/frames` contract should return `sessions[]` (t_mid, detector pair, n) and optionally `passes[]`.
3. **Asteroid validation = detection at the predicted position in single exposures, not cross-pass linking.**
   - For each exposure, take `MJD-AVG` and query SkyBoT (ssp.imcce.fr, cached, geocentre) to get the expected objects.
   - Match our sep detections within 2 px and report recall vs V mag.
   - Spot-check 20–50 objects with MOST (`nph-most?catalog=spherex&output_mode=Brief`).
   - Link within the hours-long sequences only as a stretch goal.
4. **Use `MJD-AVG` (UTC) as the time keyword.** It's confirmed in the Explanatory Supplement. `t_min`/`t_max` are the TAP/SIA columns.
5. **Use the IRSA SPHEREx DR1 cube HiPS as the "real SPHEREx sky" layer** (order 5, 12.9″/px, 102 frames). Keep 2MASS or AllWISE colour from IRSA as the base, and tell users that zoomed views come from live cutouts.
6. **Add 3I/ATLAS as a pre-cached featured demo.** Use the Dec 14 2025 sequence (55 images over 20 h at RA ~166.4–167.3, Dec ~+5.3, about 29 px/h). Use NASA's `comet_ra/dec` per exposure and DR1 cutouts via `obs_id`. It's outside the 5% patch, so present it as a showcase, not a discovery.
7. **Re-check the storage and time estimate.** About 66 MB per L2 file (`access_estsize`). About 18 DR1 images per deg² so far, so roughly 37k DR1 images (~2.4 TB) for 2,000 deg², before QR3. Read only the needed HDUs from S3 with range requests or `fsspec`, or reduce the patch. The plan's "65,000 photos / 28 MB used" is plausible, but measure on the first file.
8. **Gaia:** pre-fetch with 9 async TAP jobs of RA strips (G<19, lean columns) onto the VM, load into DuckDB, and propagate PM to each epoch. Serve the browser overlay per view from DuckDB, not from ESA directly. Expect about 6–15 M rows at G<18.
9. **Blink-player UI: copy WiseView** (interval slider, difference toggle, invert, asinh linearity and min/max, Gaia overlay, URL-hash state, loop-restart border, epoch legend, synthetic-object injection). **Copy Backyard Worlds** for voting: point-marking on the frame, mover/dipole/artifact field guide, retire after N votes, and a report form after a catalogue check.
10. **Fix the pitch claims.** Drop "first public SPHEREx dot list" and "first blink tool". Many Space Apps teams are building blink tools this weekend, and IRSA has a Source Discovery Tool. Lead with the measured "re-found N known asteroids", a 2,000 deg² suspect list, and citizen vetting.
11. Because each exposure is in a different channel, keep blinking within the same detector and adjacent channels. In each session the detector pairs D1+D4, D2+D5 and D3+D6 are simultaneous, so a D1→D4 flip is colour, not motion. This matches what the plan already says about colour vs flicker.

### Sources
- Aladin Lite: https://github.com/cds-astro/aladin-lite · https://cds-astro.github.io/aladin-lite/ · https://www.npmjs.com/package/aladin-lite · https://aladin.cds.unistra.fr/ (incident notice)
- HiPS: https://irsa.ipac.caltech.edu/data/hips/list · https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube/properties · https://alasky.cds.unistra.fr/MocServer/query?ID=CDS/P/SPHEREx/QR2/color&get=record
- MOST: https://irsa.ipac.caltech.edu/applications/MOST · https://irsa.ipac.caltech.edu/applications/MOST/MOSTProgramInterface.html · https://caltech-ipac.github.io/irsa-tutorials/most-queries
- 3I/ATLAS: https://irsa.ipac.caltech.edu/data/SPHEREx/docs/comet3iatlas/index.html (+ obscore_3I_Atlas.vot)
- SkyBoT: https://ssp.imcce.fr/webservices/skybot/api/conesearch/
- Gaia: https://docs.astropy.org/projects/astroquery/en/stable/gaia/gaia.html · https://www.cosmos.esa.int/web/gaia-users/archive/faq · https://www.cosmos.esa.int/web/gaia-users/archive/faq-old
- WiseView / BYW: http://byw.tools/wiseview · https://github.com/backyardworlds/wiseview · https://blog.backyardworlds.org/2017/09/13/wiseview/ · https://www.zooniverse.org/projects/marckuchner/backyard-worlds-planet-9 · https://arxiv.org/abs/1705.02919
- SPHEREx timing: https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf (v2.1, p. 77) · https://github.com/Caltech-IPAC/irsa-tutorials (spherex_source_discovery_tool/aperture_photometry.py)
- Prior work: https://arxiv.org/abs/2604.22012 · https://arxiv.org/abs/2602.07129 · https://arxiv.org/abs/2602.08037 · https://meetingorganizer.copernicus.org/EPSC2026/EPSC2026-797.html · https://arxiv.org/abs/2402.08705
