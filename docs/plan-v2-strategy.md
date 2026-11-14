# SkyShift plan v2: the strategy to win

Written Saturday 14 Nov 2026, before kickoff. It replaces the scope, schedule and claims in the two HTML plans, and contains no product code.
"Expected" means computed from catalogues, not measured. Two facts come from a quick lookup and need checking on real data: SPHEREx saturates near AB 10–11 (arXiv:2604.22983 quotes 9.7–11.7), and the Gagné list is on MOCAdb and Zenodo.

**Three terms**
- **Exposure:** one SPHEREx photo (113 s).
- **Session:** 1–5 exposures taken 2.1 min apart.
- **Visit:** the 2–4 weeks in which SPHEREx sees one spot, every six months: about 21 sessions, 55–60 exposures. The sky at RA 125.5 has two visits so far (Oct–Nov 2025, Apr–May 2026). Dates differ by position.

---

## 1. Winning thesis

**SkyShift is a WiseView for SPHEREx that blinks each kind of object on the clock that suits it, measures what moved, and checks that measurement against Gaia and known asteroid positions.**

Most other blink tools will flip raw exposures. That fails twice. Every exposure is in a different colour, so everything flickers. And the test objects move less than one pixel per visit, so a plain blink shows nothing. We solve both, and we show numbers. We don't claim a first.

**What a judge sees in 30 seconds**
1. **Two clocks.** "Months" blinks one stacked frame per visit: nearby stars and brown dwarfs step. "Hours" blinks single exposures: comets and asteroids cross.
2. **A number under every movie.** "Moved 5.1″ north in 6 months. Gaia predicts 5.2″."
3. **Interstellar comet 3I/ATLAS crossing the stars**, next to a counter: "N known asteroids re-found".
4. **A spectrum built on the fly, coloured by visit**, with water, methane, CO₂ and CO marked. It says what the mover is and how a variable star changed.

| Criterion | How we earn it | Evidence we show |
|---|---|---|
| Impact | Anyone can paste coordinates, watch, flag a dot and vote, on a phone too. | Candidate list with vote counts and CSV export; people and votes from a venue test. |
| Creativity | Clocks matched to SPHEREx's timing; spectrum by visit; a planted-object drill. | The Months/Hours switch in the video. |
| Validity | The tool checks itself and states its limits. | "How we know it works" page (section 4), with a control test. |
| Relevance | Every objective and consideration is answered; the five test objects are one click away. | A challenge-text-to-feature table; the challenge's own coordinate string works when pasted. |
| Presentation | One story: Pluto was found by blinking → volunteers found comets and brown dwarfs the same way → SPHEREx re-images the sky every six months → anyone can blink it. | GIF at the top of the page; a 29-second video. |

---

## 2. Scope

Order of work: five targets blink and measure → any coordinates → comet and asteroids → validation numbers → flag and vote → hunt zone.

### Must (polished by the Sunday 09:00 freeze)

| # | Feature | Owner |
|---|---|---|
| M1 | Coordinate box first. Accepts `08h55m10.831s -7d14m42.53s` and decimal degrees. Five test-object chips. State in the URL, plus `&embed=1`. | P3 |
| M2 | Months clock: per visit and detector, a median stack of aligned cutouts: 5′ field at 3× oversampling (146 px) or 15′ at 2× (293 px). Blink, subtract, add; speed, stretch, invert, dates. | P2, P4 |
| M3 | Hours clock: single exposures in time order: 20′ field at the native 6.15″ pixel (195 px) or 60′ binned 2× (293 px). | P2, P4 |
| M4 | Live API for any coordinates; first frame under 15 s. | P2 |
| M5 | Measure readout: shift, rate, error, Gaia's prediction and distance. | P1, P3 |
| M6 | Spectrum on the fly: aperture photometry on every cutout, by wavelength, coloured by visit, molecules marked. No model fitting. | P1, P3 |
| M7 | Featured targets as static files: the five, 3I/ATLAS, two asteroids. | P2 |
| M8 | Sky view: Aladin Lite with IRSA layers and Gaia dots. The blink works without it. | P3 |
| M9 | Flag and vote: flag a dot in any blink; candidate list; votes (mover / changing / junk / not sure); counts; CSV export. | P4 |
| M10 | "How we know it works" page with V1–V4. | P1 |
| M11 | Project page, 29-second video, public repo with README, licence, AI disclosure. | P4, all |

### Should (in order, after the Saturday gates)

1. **S1 Hunt zone** (P1, gated at Sat 18:00): 100 deg² on the ecliptic, where asteroids are densest (RA 120.5–130.5, Dec +14.4 to +24.4). About 3,400 photos, under an hour on the worker (to be measured), after a 12 deg² pilot. Known asteroids are matched; suspects join the candidate list.
2. **S2 Fast Gaia stars** (P2, 1.5 h): measured vs predicted shift for about 40 stars.
3. **S3 Gagné brown-dwarf list** (P1, 1.5 h): a "known brown dwarf" label and a recovery count.
4. **S4 "Hidden neighbour" drill** (P4, 2 h): a simulated WISE 0855-like object planted in real frames, labelled as simulated.
5. **S5 False-colour still** per visit (P4, 1.5 h).
6. **S6** Flag-pixel overlay, AllWISE layer, light-curve chart (P3, 1 h each).

### Cut

- **The 5% deep zone (2,000 deg²).** About 37,000 photos at unmeasured speed, and its day-2 linking step doesn't fit the real cadence. S1 replaces it at one-twentieth the size.
- **Whole-sky run.** Adds nothing to any criterion. One projected-cost sentence instead.
- **Linking every dot across visits.** We measure motion only where people look.
- **Job list with locks, HEALPix tiling.** Not needed for 3,400 photos.
- **False colour as a blink mode, 3D neighbourhood, tour overlay, in-app GIF export, NEOSSat, accounts.**

### The decisions asked for

- **Deep zone: shrinks** to S1. **Voting: stays, smaller** (M9); any visitor can flag a dot, so it no longer depends on the pipeline. **Spectrum: stays, simpler** (M6). **False colour: shrinks** to a still (S5); switching D1 → D6 on WISE 0855 gives the same effect for free.
- **Blink becomes two clocks.**
  - *Months.* Median-stack each detector within a visit, then blink visits. For nearby stars, brown dwarfs and variables. The median also removes asteroids and cosmic rays.
  - *Hours.* Single exposures. Sessions are hours to days apart, so an asteroid hops arcminutes between frames. Inside one session nothing moves visibly, which rejects junk: a real mover must appear in two exposures of a session, or in both detectors exposed at the same moment (D1+D4, D2+D5, D3+D6).
  - Variables are compared within one detector, visit to visit, and through the spectrum coloured by visit.

---

## 3. Demo targets and the 30-second video

The five are what judges will test. The comet is famous, fast, and NASA supplies its position in every image. The asteroids prove the Hours clock on ordinary objects.

| Target | Clock | Expected | Caveat |
|---|---|---|---|
| Barnard's Star | Months | Steps north 5.2″ (0.84 px) per visit; 10.39″/yr at position angle 355.6° (Gaia DR3) | K ≈ 4.5: the core saturates. Measure from the wings. |
| WISE 0855−0714 | Months, D6 | Absent in D1–D3. In D5–D6 a dot with no Gaia ring, stepping west 4.1″ (0.66 px) per visit | Faint. If weak, add D5+D6 and shift-and-add along its known motion. |
| Epsilon Indi B | Months, subtract | One dot (the pair is under 1″ apart), 2.3″ (0.38 px) per visit | Epsilon Indi A, 7′ away, is very bright. |
| RW Aurigae | Months, spectrum | Same place; brightness and spectrum change between visits | May saturate in the short bands. |
| R Draconis | Months, light curve | Many visits (near the north ecliptic pole); brightness changes | Very bright, saturates. Measure in the wings and say so. |
| Comet 3I/ATLAS | Hours | 55 images over 20 h on 14 Dec 2025; 176″/h (29 px/h); 0.98° in total | Faint in many channels; pick the best detector. |
| Two numbered asteroids | Hours | A dot inside NASA's predicted ring, under 2 px off | Pick from the V4 batch, V 12–15. |

Two limits go on the cards. Parallax is at most 1.1″ between visits, below what we can measure, so we show Gaia's distance. Three of the five are expected to saturate; the tool marks saturated cores. At the Sunday 09:00 stand-up, pick the cleaner of WISE 0855 and Barnard's Star as the hero.

### The video (cut to 29.0 s; check the length with a tool before upload)

Screen recordings of the live site only, so no watermark is needed. English captions; no voice, or a teammate's own. From shot 2 a credit stays on screen: "Real data: NASA SPHEREx (IRSA) · ESA Gaia DR3".

| Time | Shot | On-screen text |
|---|---|---|
| 0:00–0:03 | Title over the sky view | "SkyShift" · team and four names · "NASA Space Apps 2026 · Planet X and SPHEREx" |
| 0:03–0:06 | Paste `08h55m10.831s -7d14m42.53s`, press Enter | "Pluto was found by blinking two photos. Now blink NASA's infrared sky." |
| 0:06–0:11 | WISE 0855: D1 empty; switch to D6; a dot appears and steps west | "A brown dwarf at −20 °C. Invisible until 4.5 µm. No Gaia star here." |
| 0:11–0:15 | Barnard's Star steps north; the readout fills in | "Measured: ⟨x⟩″ a year. Gaia: 10.4″ a year." |
| 0:15–0:20 | Hours clock: 3I/ATLAS crosses; then an asteroid in its ring | "Interstellar comet 3I/ATLAS, 20 hours, 14 Dec 2025." · "⟨N⟩ known asteroids re-found." |
| 0:20–0:24 | Spectrum by visit with molecule marks; flag a dot; the count ticks | "102 colours say what it is. People decide what is real." |
| 0:24–0:29 | Zoom out to the whole sky | "New SPHEREx data every week. The next neighbour may already be in it." · URL |

### Seven slides (fallback demo, and the deck for the live pitch)

1. Title: team, members, challenge, one screenshot.
2. Problem: SPHEREx photos are hard to compare (a new colour each time, sub-pixel motion).
3. User experience: paste coordinates → blink → flag.
4. Two clocks and 102 colours, one diagram.
5. How we know it works: the numbers.
6. How NASA and ESA data make it possible.
7. What's next, and the link.

---

## 4. Validation story

Scripts in `scripts/validate/` write `validation.json`; the page reads it. A poor number is still printed.

| # | Claim | How to compute it | Expected | Due |
|---|---|---|---|---|
| V1 | Five test objects recovered | Per target and visit: predict the position (Gaia DR3 position and proper motion moved to the visit's mid-time; published astrometry for WISE 0855). Count a detection within 2 px. | 5 of 5; WISE 0855 only in D5–D6 | Sat 15:00 |
| V2 | Motion matches Gaia | Centroid the target in each visit stack (wings if saturated); median over detectors. Shift = last visit − first. Rate = shift ÷ time. Compare with Gaia `pmra`, `pmdec`. | Barnard's Star: within 15% and 10° of 10.39″/yr | Sat 14:00 |
| V3 | Position error floor | In the same cutouts, take 10 or more Gaia stars with G 13–17 and motion under 20 mas/yr. The RMS of their visit-to-visit shifts is V2's error bar, and gives the slowest motion visible at 3σ. | Unknown; hoping for 0.6–1.2″ | Sat 14:00 |
| V4 | Asteroids re-found, targeted | For asteroids numbered 1–60, ask IRSA MOST (`catalog=spherex`, `output_mode=Brief`, numbers not names) for exposures and predicted positions. Fetch 5′ cutouts for up to 5 exposures each. Success: a detection within 2 px that is absent from the visit stack. **Control:** repeat 3′ away; that is the chance rate. | N of 60; median offset under 1 px; control under 5% | Sat 15:00 |
| V5 | Asteroids re-found, blind (S1) | In the hunt zone, query SkyBoT (`ssp.imcce.fr`, geocentre, `-from=SkyShift`, cached) once per 6 h of observing, about 200 queries. Move objects to each exposure's MJD-AVG with the returned rates; check 20 against MOST. Match within 2 px. Report recall by V magnitude, with the same control. | Roughly 70–150 asteroids with V < 18; recall falls with V | Sun 03:00 |
| V6 | Fast stars (S2) | ESA Gaia TAP: `pm > 1500 AND phot_g_mean_mag > 12`, top 40 by `pm`. Run V2 on each; report the slope and RMS of measured against predicted. | Slope near 1 | Sun 08:00 |
| V7 | Brown dwarfs (S3) | For Gagné et al. 2026 entries faster than 1″/yr, run V2; count those shifting in the predicted direction. If the list isn't loaded in 30 min, use a hand list of 10. | A count | Sun 03:00 |
| V8 | Spectra and speed | Two unsaturated stars: our spectrum averaged over 2MASS Ks and WISE W1 vs the catalogue. Median time to first frame for 10 random coordinates. | Within 20%; under 15 s | Sun 08:00 |

Pitch headline, from V2 and V4: "Barnard's Star moves ⟨x⟩″ a year in our tool; Gaia says 10.4. We re-found ⟨N⟩ of 60 asteroids where NASA predicted them."

---

## 5. Project page

Save a complete version on Saturday at 21:00, so a valid submission always exists.

- **Name:** SkyShift.
- **Summary (about 100 words):** the thesis sentence; coordinates in, movie out, tested on the five objects; why it matters; the two headline numbers.
- **Demo link:** the 29-second video on YouTube. **Project link:** the live site, which links the repo. Test both in a private window.
- **Detailed description, in order:**
  1. The GIF (one file, under 8 MB, 640 px wide, about 9 s): WISE 0855 appearing in D6 and stepping; Barnard's Star with its readout; 3I/ATLAS crossing.
  2. What it does, in three sentences, then seven "try it" links with coordinates in the URL.
  3. How it answers the challenge: a table of the objective, the five test objects and the eight considerations.
  4. Why two clocks: four sentences and a diagram.
  5. How we know it works: the V numbers, the control, a screenshot.
  6. Limits: saturation, the blended Epsilon Indi pair, parallax, and which "Planet X" SPHEREx could see (a cold brown dwarf, not a faint distant planet).
  7. How it works: data flow, tools, languages.
  8. After a vote: counts, the CSV, a route to astronomers (Backyard Worlds forum).
  9. What's next: weekly data, the embed mode, a larger hunt zone.
- **AI ("Super Intelligence") disclosure — a draft; edit until every sentence is true:**
  "We used Claude (Anthropic) through Claude Code as a coding and research assistant. It wrote most of the code from our instructions. We reviewed it, ran it and tested it against known objects. It also helped us read NASA documentation, plan the work and edit this text. Commits made with it carry a `Co-Authored-By` line. Every image, GIF and video here is a screen recording of the running tool on real SPHEREx data; none is AI-generated. The numbers come from our pipeline's output files. The planted object in the drill is simulated and labelled. Before the event we prepared only a cloud account, planning notes and an empty repository."
- **NASA data, named one by one:** SPHEREx Level 2 spectral images, DR1 and QR3, from NASA/IPAC IRSA (image search, cutouts, `s3://nasa-irsa-spherex`); the IRSA SPHEREx all-sky cube; IRSA's Moving Object Search Tool; IRSA's SPHEREx table for comet 3I/ATLAS; 2MASS and AllWISE layers from IRSA; the SPHEREx Explanatory Supplement and IRSA tutorials; WiseView and Backyard Worlds as the model.
- **Partner data:** ESA Gaia DR3, through the ESA Gaia Archive. Don't list CSA; we don't use it.
- **Other references:** IMCCE SkyBoT; Aladin Lite 3.8.2 (CDS, GPL-3.0, loaded from jsDelivr); astropy, astroquery, sep, reproject, FastAPI, DuckDB, Vite; Gagné et al. 2026 (arXiv:2604.22012) and MOCAdb; Kuchner et al. 2017; WiseView (ASCL 1806.004); the Lisse et al. 3I/ATLAS papers; Claude Code.
- **Acknowledgements:** copy the SPHEREx acknowledgement and its two required citations (Bock et al. 2026, Akeson et al. 2026) word for word from IRSA's mission page. Add the standard Gaia acknowledgement. Licence: Apache-2.0.

---

## 6. Architecture changes

**1. Static first.** One Python function builds a blink for a position. A script runs it for the featured targets and writes `web/public/data/<slug>/`. The API runs the same function for any other coordinates and caches in the bucket. The site tries static files first.

```
IRSA image search ─► cutouts ─► stack and measure ─► frames.json + frame data
   featured targets: scripts/precompute ─► Firebase Hosting /data/…  (static)
   any coordinates:  Cloud Run /api/…   ─► bucket cache              (live)
ESA Gaia · SkyBoT · MOST · 3I/ATLAS table ─► same server, cached
Flags and votes ─► Firestore
```

**2. After the credit ends (17 Jan; winners 26 Jan).** The static site, featured targets, scoreboard and candidate list need no server. Hosting and Firestore have a no-cost tier; P2 confirms on Firebase's pricing page. The live API needs billing: a card on the account, a €10 alert, Cloud Run at 0–2 instances. If the API fails, the page offers the featured targets.

**3. No CDS.**
- Aladin Lite pinned: `cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js`, unpkg as the second source. Never install it unpinned; `latest` is a beta.
- Layers by IRSA URL only: 2MASS colour as the base; the SPHEREx cube (`https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube`, 102 frames, about 12.9″/px) on top; AllWISE optional. Never a CDS survey id: the default `P/DSS2/color` is broken.
- `showSimbadPointerControl: false`. No `gotoObject`, `catalogFromSimbad`, `catalogFromVizieR`, `catalogFromSkyBot`.
- Names from a built-in list behind `/api/resolve`. Gaia dots from our own `/api/gaia`.

**4. Contract changes.**
- **Time:** `mjd` is `MJD-AVG` (UTC, exposure midpoint). For search results, the middle of `t_min` and `t_max`.
- **Band:** detector from `energy_bandpassname` ("SPHEREx-D2"), never from the filename.
- **Visit rule, replacing the pass rule:** sort the exposure times at a position; start a new visit where the gap exceeds 60 days; cut any visit longer than 45 days (near the ecliptic poles) into 30-day bins. A session is a run with gaps under 10 minutes.
- **Flags** (`contracts/flags.md`; check bit meanings on the first file):
  - Mask for detection and photometry: TRANSIENT, OVERFLOW, SUR_ERROR, NONFUNC, DICHROIC, MISSING_DATA, HOT, COLD, PHANMISS, NONLINEAR, PERSIST, STREAK, SNOWBALL.
  - Record on the dot, don't mask: CROSSTALK, the three GHOST bits, BLOOM, HALO, and OUTLIER until we've checked it isn't set on a known asteroid.
  - Never mask: SOURCE (set on about 99% of pixels), FULLSAMPLE.
  - OVERFLOW or NONLINEAR in a source's core means `saturated`.
- **Units:** IMAGE is in MJy/sr; 1 MJy/sr is 889 µJy in a 6.15″ pixel.
- **Frames are data, not pictures:** 16-bit arrays with `scale` and `zero`, at most 300 px a side (sampling per field as in M2 and M3). The browser does stretch, invert, subtract and add.

```
GET /api/frames?ra=&dec=&fov=&clock=months|hours&det=1..6
→ { clock, center:{ra,dec}, pixel_arcsec, size:[nx,ny],
    epochs:[{ id, kind:"visit"|"exposure", mjd_mid, mjd_start, mjd_end, det,
              um_min, um_max, n_exposures, flag_frac, sat_frac,
              data_url, scale, zero }],
    gaia:[{ id, ra, dec, pmra, pmdec, parallax, g }],
    known:[{ name, epoch_id, ra, dec, v, source:"skybot"|"most"|"irsa-3i" }],
    data_release, archive_checked }
GET /api/object?ra=&dec=
→ { name|null, gaia|null, known_ucd|null,
    spectrum:[{ um, flux_ujy, err_ujy, det, mjd, visit, saturated }],
    measure:{ shift_arcsec, pa_deg, dt_yr, rate_arcsec_yr, sigma_arcsec,
              n_ref_stars, predicted_arcsec|null } }
GET /api/gaia?ra=&dec=&r=     GET /api/known?ra=&dec=&r=&mjd=
GET /api/resolve?q=           # any coordinate format, or a built-in name
Static twins, same shapes: /data/<slug>/frames.json, object.json;
                           /data/validation.json, /data/suspects.json
Firestore: candidates { ra, dec, mjd, clock, source:"user"|"pipeline" }
           votes { candidate_id, answer: mover|changing|junk|unsure, ts, voter }
           (create-only rules, fixed fields, no personal data)
Dot table: obs_id, detector, mjd, session, visit, ra, dec, x, y, flux_ujy,
           flux_err_ujy, wavelength_um, snr, flags, cell (6″ sky cell)
Derived:   fixed (cell seen in 3+ sessions) · movers (one session only, in
           2 exposures or 2 detectors) · suspects (movers with no SkyBoT match)
```

**5. Also.**
- Shared science code: P1 writes `core/` (centroid, shift, photometry, flag masks, visit rule); server and pipeline import it.
- Photo lists first: at 10:00 on Saturday P2 saves the search results for every target and the hunt zone to the bucket. If IRSA fails later, the photos can still be read from Amazon.
- Machines: the worker is `n2-highcpu-64` spot, not N2D. The 8-core test machine does most of the work.
- Cost: test machine €4, worker €2, storage €2, Cloud Run and Firebase under €2. **Under €15 of €264.** Delete the machines and bulk files on Monday.

---

## 7. Schedule

- **Sleep:** five hours each, in two shifts, so one backend and one frontend person are always awake. P2 and P3: 23:00–04:00. P1 and P4: 04:00–09:00. If the venue closes at night, the same plan runs from home.
- **Merges:** owners merge their own area when tests pass. Only contract changes wait for P1.
- **Pitch:** P4 presents; P1 answers data questions. Dry run Saturday 21:00. Timed rehearsals Sunday 13:00 and 14:15, all four present.

| Time | P1 · lead, science, pipeline | P2 · server, deploys | P3 · sky, cards | P4 · blink, votes, pitch |
|---|---|---|---|---|
| **Sat 09:00** | All: ask the Local Lead for the deadline, pitch length, night access; agree this scope. Then public repo, contracts v2, `CLAUDE.md` files. | New hosting site; empty site and API stub live. | Vite app; Aladin from jsDelivr with IRSA layers. | Open the project page form. Blink player on made-up arrays. |
| 10:00 | Open the first photo: check MJD-AVG, detector naming, flag bits, units, sizes. Post the facts. | Image search; save photo lists to the bucket. | Coordinate parser, chips, URL state. | Player: speed, stretch, invert, subtract, add. |
| 11:00 | `core/`: detect, centroid, compare positions with Gaia. | Cutouts, visit rule, median stack: Barnard's Star, one detector. | Gaia dots; card layout. | Months/Hours switch; date legend. |
| 12:00 | Lunch in turns. Shift measurement. | Barnard's frames as static files. | Card on fixtures. | Load the static frames. |
| **13:00 · M1** | *Barnard's Star blinks from real data on the public site.* Error floor (V3). | Six detectors; WISE 0855, Epsilon Indi B. | Measure readout; Gaia distance. | Gaia rings moved to each date. |
| 14:00 | Motion vs Gaia (V2). MOST asteroid test (V4). | RW Aurigae, R Draconis. Live `/api/frames`. | Spectrum chart, molecule marks. | Field-of-view picker. |
| **15:00 · G1** | *Gate: five targets blink; one motion measured.* Photometry. | `/api/object`; spectra for the five. | Spectrum wired; "what to look for" text. | Hours clock on a fixture. |
| 16:00 | Pilot: 12 deg², about 230 photos, test machine. | 3I/ATLAS: NASA's table → cutouts → static movie. | Embed mode, data badge, gallery. | Comet movie with its path ring. |
| 17:00 | SkyBoT match and control for the pilot. | Deploy; cache; time to first frame. | Scoreboard page layout. | Flag-a-dot; candidate list. |
| **18:00 · G2** | *Gate: pilot recall at V < 16 is 50% or more.* Yes: start the hunt zone on the worker. No: stop S1. Dinner in turns. | Two asteroid movies from V4. | Phone-width check. | Votes to Firestore; counts. |
| 19:00 | All: integration hour. Open every target on the public site; write the bug list. | | | |
| **20:00 · M2** | *Any coordinates work live; five targets and the comet are static.* SkyBoT queries for the hunt zone. | Bug list. | Bug list. | CSV export. |
| 21:00 | All, 15 min: 60-second pitch dry run. Then `validation.json` v1. | Start S2; it runs unattended. | Scoreboard reads `validation.json`. | Save project page v0 with working links. |
| 22:00 | Hunt-zone matching. | Static export script; handover note. | Card polish; handover note. | Walk the storyboard on the real site; list missing shots. |
| 23:00–04:00 | Suspects and recall chart (V5). S3. Spectrum check. Numbers frozen 03:30; handover note. | Sleep. | Sleep. | Votes on real suspects. S4 or S5. Page text; handover note. |
| 04:00–09:00 | Sleep. | Static export of everything; deploy. S2 results. Time to first frame. 08:00: open every target. | Scoreboard with final numbers; gallery; phone layout; copy; S6. | Sleep. |
| **Sun 09:00 · M3** | *Feature freeze.* Stand-up; choose the hero. Then README, licence, AI section, references. | Check the budget alert ignores credits; FortiGuard request; fixes. | UI and copy pass. | Venue test with a QR code; record the GIF. |
| 10:00 | Page: data, validation, limits. | Private-window link tests; offline copy on the pitch laptop. | Screen set-up and captions. | Record video takes. |
| 11:00 | Finish the page. | Bug fixes only. | Seven slides. | Cut to 29.0 s; upload; check length. |
| **12:00 · M4** | *First full submission saved.* Lunch. Everyone reads the page once. | | | |
| 13:00 | Pitch rehearsal 1, then fixes. | Last deploy 14:00. | Fixes. | Fixes. |
| 14:15 | Pitch rehearsal 2. | | | |
| **14:30 · M5** | *Final submission saved* and checked in a private window. Hard stop 14:45. | | | |
| 15:00 · 15:30 | Local deadline · live pitch. | | | |

If local rules allow edits until 23:59, use the evening for typos only.

---

## 8. Risks

| # | Risk | Trigger | Fallback |
|---|---|---|---|
| 1 | IRSA slow or down | Search or cutouts over 20 s, or one in five failing, for 15 minutes | Use the saved photo lists; read files from `s3://nasa-irsa-spherex` on the cloud machine. The site keeps serving static targets. |
| 2 | Spot machine reclaimed | The instance stops | Restart; finished photos are skipped. After two stops, use the 8-core machine. |
| 3 | No asteroids found | Pilot recall under 50% at V < 16, Sat 18:00 | Check the time keyword, the OUTLIER mask and the match radius for 30 minutes. Then stop S1; rely on V4 and the comet, where NASA supplies positions. |
| 4 | CDS still down, or Aladin fails | Aladin doesn't start, or tiles return 404 | Switch to unpkg. If it still fails, ship the coordinate box and gallery without the sky sphere. |
| 5 | Local deadline earlier, or venue closed at night | The Local Lead says so at 09:00 | Move every time by the difference. Drop in order: S1, S4–S6, S2–S3, M8. Page v0 is saved early regardless. |
| 6 | Test stars saturate, or WISE 0855 is too faint | First cutouts, Sat 11:00–13:00 | Centroid on the wings; mark the core. WISE 0855: D5+D6 and shift-and-add. Use S2's fast stars as the clean measurement. |
| 7 | Frontend and backend don't meet | M2 missed at Sat 20:00 | The static file format is the contract. P2 writes static files by script; the frontend reads them; the live API waits. |
| 8 | Submission ineligible | Any item fails the Sunday 12:00 checklist: video over 30 s, private link, missing AI disclosure, unlisted source, pre-event code | Fix from the checklist. The seven slides are ready as the demo. The plan pages' simulated demo is never reused. |

For the live pitch, the presenting laptop carries an offline copy of the site and the video.

---

## 9. Corrections to the current plans

### Both pages

| # | Now | Replace with |
|---|---|---|
| 1 | "pass", "sky pass"; "one frame per sky pass"; pass dates such as "May–Nov 2025" | "visit"; "one stacked frame per visit (six months apart) for slow movers; single exposures inside a visit for comets and asteroids"; "a visit lasts 2–4 weeks; its dates depend on the position" |
| 2 | "Deep zone", "5% of the sky", Phases A–C; "~65,000 photos, ~1.8 TB … about 2 hours"; "whole sky … 15–20 hours"; the calculator presets | "Hunt zone: 100 deg² on the ecliptic, about 3,400 photos, under an hour (to be measured)", behind gates G1 and G2. Whole sky: not attempted. |
| 3 | Any wording that suggests an N2D worker; "request a processor quota of 96" | The worker is `n2-highcpu-64` spot. Quota is done: N2 200, preemptible 96; N2D was denied and isn't needed. The dev plan's commands already use the right type. |
| 4 | "Aladin Lite v3", "from npm", "npm package aladin-lite"; the Aladin docs link (404) | "Aladin Lite 3.8.2 from jsDelivr, IRSA layer URLs, SIMBAD features off"; `https://cds-astro.github.io/aladin-lite/` |
| 5 | `pass = max(1, floor((mjd − 60796) / 182.6) + 1)` | Delete; use the visit rule (section 6). Fixed bins cut real visits: the RA 125.5 visit runs to 6 May 2026 and the formula splits it on 1 May. |
| 6 | "Flagged pixels never become dots." · "Mask flagged pixels except SOURCE." · "ignoring flagged pixels (but not the SOURCE flag)" | "We mask a named list of bad-pixel flags (cosmic rays, hot and dead pixels, saturation, persistence, streaks), kept in `contracts/flags.md`. We never mask SOURCE, which is set on about 99% of pixels. Ghost, halo and crosstalk flags are recorded on the dot." |
| 7 | "65 MB … about 28 MB" | "about 66 MB; the parts we need are an estimated 28 MB, to be measured" |
| 8 | Schedules, milestones, "feature freeze on Day 2 afternoon" | Section 7: freeze Sunday 09:00, first submission 12:00, final 14:30, pitch 15:30 |
| 9 | Contract boxes: `passes:[…]`, `det=…\|fc&mode=…`, columns `photo_id`, `pass`, `hp32`, table `objects` | The shapes in section 6 |
| 10 | "job list", "batch of 100", "locks", "HEALPix tiles" | "each photo's output file marks it done"; delete the tiles |

### Concept plan (`skyshift-hackathon-plan.html`)

| Where | Now | Replace with |
|---|---|---|
| Hero facts | "~3 full-sky passes, May 2025 to Oct 2026" | "2–3 visits per spot so far, six months apart" |
| Idea | "Nobody has put them together for SPHEREx." | "Many teams are building SPHEREx blink tools this weekend. Ours follows SPHEREx's real timing and checks its own measurements." |
| Crowd card; tech step 8; risks | "dots that moved or match no known star"; "…or look unusually red"; "at least two photos per pass" | "dots seen in one session only, in two exposures or in both detectors exposed at the same moment, with no known asteroid there; plus any dot a visitor flags" |
| Zones; scale-up | "NASA's ready-made whole-sky picture"; "Aladin's ready-made picture" | "IRSA's SPHEREx all-sky cube (102 frames, about 12.9″/px, coarser than the 6.15″ photos)" |
| Target texts | Barnard's Star "about one SPHEREx pixel each time"; R Draconis "it should give the best light curve of the five" | The numbers and caveats in section 3's table: "5.2″ (0.84 px) per visit; the core saturates"; "visited far more often, but it saturates; we measure in the wings and say so" |
| Screens; features row 1 | "Search by name"; "Search by name or position" | "Coordinate box first, accepting `08h55m10.831s -7d14m42.53s` and decimal degrees; five one-click test objects; names from a built-in list (SIMBAD is offline)" |
| Screens | "✓ Real ✕ Junk ? Not sure" | "Mover · Changing · Junk · Not sure"; the click marks the position |
| Features row 2 | "Every search asks NASA's archive live … photos it hasn't seen before." | "Any coordinates query IRSA live, so new weekly data appears without code changes. A badge shows 'Data: DR1 + QR3, checked ⟨date⟩'. Featured targets are static files." |
| Feature cards | "Full-sky run", "3D neighbourhood", "GIF export", "Beginner tour" | Delete |
| Tech steps 6–7 | "within one pass, then follow them between passes"; "for each photo's date" | "Group dots by sky position: many sessions = fixed, one session = mover candidate"; "SkyBoT at each exposure's MJD-AVG, spot-checked with NASA's MOST" |
| On demand | "sends back PNG frames … so the demo is instant" | "sends back frame data. Featured targets are static files: they load at once and keep working after the credit ends." |
| Data table | "none yet, so ours would be the first" | "No general public catalogue yet. Related work exists: IRSA's Source Discovery Tool, SPLICES, and the Gagné et al. 2026 brown-dwarf library. We don't claim a first." |
| Team | "blinking photos first, then the suspect list, then voting"; P4 "Demo video and slides" | The order in section 2; "29-second video (30 s is a hard limit), project page, live pitch" |
| Risks | "Name lookup service is down … returned errors" | "CDS has a security incident (notice of 9 Nov): SIMBAD, Aladin's own CDN and CDS sky tiles are down." Then the workaround in row 4 above. |
| Tonight | "Download one SPHEREx photo and open it" | "Read the tutorial notebooks. Open the first photo at 09:00 on Saturday." |
| Hero note | "demos on this page use simulated stars" | Add: "This demo code predates the event and is not used in the product." |

### Developer plan (`skyshift-dev-plan.html`)

| Where | Now | Replace with |
|---|---|---|
| Hero, "Heavy work" | "One big Google machine …" | "An 8-core test machine does most of it. The 64-core spot machine runs the hunt zone for under an hour." |
| Diagram | "28 MB each"; no static path | "about 66 MB files, needed parts only"; add "Hosting /data (static targets)" |
| Cloud choice | Azure "only if Google refuses the processor quota" | Remove; the quota is in place. |
| Root `CLAUDE.md` | "blink photos of any spot across sky passes"; "manifests/, locks/, dots/, objects/, suspects/, cache/frames/" | "blink any spot on two clocks: visits six months apart, and exposures inside a visit"; "photolists/, dots/, results/, cache/" |
| Setup, step 9 | "create a private repo … Make it public at submission" | "Create the repo at 09:00 on Saturday so its history starts at kickoff; public from the first commit" |
| Cost table; credit | "€20–80, even with the whole sky"; "expires 17 January 2027" | "under €15"; add "Winners are announced on 26 January, so the site must work without the credit" |
| Tonight note | "I couldn't find a clear rule on code written before the start" | "The FAQ says teams may not start work on the challenge before the hackathon. Accounts, quotas, installs and reading are preparation. No project code before 09:00 on Saturday." |
| P1 card | "link dots within a pass, then across passes; fit motion"; done: "Barnard's Star is in the objects table …" | `core/`, validation scripts, pilot, hunt zone; done: "`validation.json` holds the five targets, Barnard's motion vs Gaia, and N of 60 asteroids re-found" |
| P2 card, prompt | "Modes blink, diff, sum; det 1–6 or false colour"; "IRSA TAP, table spherex.obscore"; "3 passes × 6 detectors in under 10 seconds" | Subtract and add happen in the browser; "IRSA image search (`Irsa.query_sia`); the TAP table name is unverified"; "first frame under 15 s live, under 2 s from static files" |
| P3 card, prompt | "find its address in the IRSA HiPS tutorial"; "Search box with the five targets" | The IRSA URLs and switched-off options from section 6; "Coordinate parser first, then chips and the built-in name list" |
| P4 card, prompt | "false colour"; "From feature freeze: demo video script, recording and slides" | False colour is S5; "storyboard walk Saturday 22:00, recording Sunday 10:00" |

New material for the rebuilt pages (features, repo layout, `CLAUDE.md` facts, contracts) comes from sections 2 and 6.

---

## 10. Open decisions for the user

1. **Which local event, and its real times.** Ask the Local Lead at 09:00: deadline, pitch length, whether judges watch the online demo, night access. *Recommendation:* assume 15:00 and a 3-minute pitch until told otherwise.
2. **Rename the public address.** `nasa-glazing.web.app` puts "nasa" in a non-NASA address and reads oddly. *Recommendation:* add a second Hosting site at 09:00, for example `skyshift-sky.web.app`, with neither "nasa" nor "spherex" in it.
3. **Billing after 17 Jan 2027.** *Recommendation:* keep a card on the account with a €10 alert, so any-coordinates search works through the winners' announcement. Without it only the static site stays up.
4. **Hunt zone instead of the 5% zone.** *Recommendation:* accept the cut. The freed time goes to the five targets, the comet, the validation numbers and the project page.
5. **How to describe AI's part in the idea.** The rules say AI must not come up with the idea. The concept (WiseView-style blink, spectra, public vetting) is the team's; this document, written with AI help, reshapes scope and schedule. *Recommendation:* the team reviews and owns these decisions at kickoff, and the disclosure says plainly that AI helped with research, planning and code.
