# Watch known stars: what changed between SPHEREx visits

Prototype and measurements, 16 Nov 2026 (agent "watch", branch p1/watch-changes). Code:
`pipeline/src/skyshift_pipeline/changes.py`, tests `pipeline/tests/test_changes.py`. The step and the site file came
after (section "Built", agent "watch2"); the step is not in `all`.

The project lead's idea: track even known stars and say when something about one changes. SPHEREx sees each spot
every six months (more often near the ecliptic poles: R Dra's zone has 7 visits), in six detectors, so every
catalogued star gets, per visit, a brightness in each detector, a colour across them and a position.

## Short answer

| change | measurable now? | false alarms (null test) | what fired, real data | verdict |
|---|---|---|---|---|
| Brightness, per detector | yes, from lightcurves' points | 0.01–0.15 per 1,000 stars per visit pair that Gaia doesn't know as variable (0.09–0.38 with stricter floors) | 0.38–0.44 per 1,000 listed; 54% are Gaia DR3 variables (control 1–4%) | **alert** |
| Colour (across detectors) | yes | 0 in the pilot and R Dra nulls; 1–7 in R Aur's | 5 pilot, 1 R Aur, 0 R Dra | alert, rare |
| Spectrum shape inside a detector | no: per-visit spectra are not stored | – | – | rerun: per-visit table with wavelength |
| Position against Gaia | measured, useless as an alert | 12% of quiet Gaia stars sit ≥ 5σ off; motion test 0.8 per 1,000, all fakes | – | **no alert** (reasons below) |
| New source (appeared) | yes, from orphans | not measurable with 2 visits (no null) | 1 (ecl04) in ~650,000 sources of five zones | alert, look by eye |
| Known source gone (vanished) | yes, from orphans; forced stars fade in the brightness rule | as above | 3 (hunt 2, ecl04 1); one strong | alert, look by eye |

Two population findings decide who may be listed (numbers in "Brightness"):
- A catalogue star **inside** a zone with no SPHEREx source of its own (link folded its dots into a neighbour's group)
  "changes" 20.6 per 1,000 in the pilot (91 of 4,427), with Gaia variables no commoner than chance (2.6% vs 1.4%):
  crowding, not the star. Not listed.
- A catalogue star measured only by forced photometry **outside** the zone box (link never ran there) behaves like a
  star with its own source (R Aur 0.52, R Dra 0.61 per 1,000; 43% and 26% Gaia variables). Listed.

## Data used (all from stored tables, no photos)

Bucket reads 786 MB in all (logged in /Users/raducucu/skyshift-data/DOWNLOADS.log): lightcurves.parquet points
(pilot 153 MB, t_rdra 158 MB, t_rwaur 204 MB; 11 columns), lightcurves_summary for pilot, hunt, ecl04, t_rdra,
t_rwaur, astrometry, crossids/sources columns, fragments, qa_photos, orphans, refcat columns for the featured zones.
Gaia DR3 `phot_variable_flag` and `vari_classifier_result` classes from IRSA (box) and ESA's archive (by id: the
sky-wide forced list reaches beyond the zone box, so a box query misses most featured-zone stars). MPC asteroid
positions from the existing `known` cache. Tables of 15 Nov (before the 16 Nov relink).

Zones: pilot (12 deg², 2 visits), t_rwaur (RW Aurigae, 3 visits), t_rdra (R Draconis, 7 visits), hunt and ecl04
(no points table: 1.5 GB each, over budget; summary only, upper bounds). R Dra and RW Aur themselves saturate and
are masked, so they don't test the method.

## Brightness

**Method** (changes.py). Per light curve (star, detector, origin = dots or forced photometry) and visit: the points'
`rel_flux` (flux / the star's spectrum at that wavelength, so colour and wavelength are handled as lightcurves does;
never across detectors), cosmic rays clipped at 5σ, weighted mean, error × √χ²_ν, sessions counted. A zone zero point
per (detector, origin, visit) from the Gaia stars' median (pilot 0.990–1.005). Before = the previous visit (two
visits) or all earlier visits combined (history); after = the latest visit. A floor per (detector, origin) so that
Gaia stars' changes have a robust spread of 1 (pilot floors 1.0–1.5%). Per detector the origin with the smaller
error. A star **alerts** when one detector changes ≥ 5σ and ≥ 5%, another detector agrees in sign at ≥ 2σ, and the
change survives dropping each visit's most deviant session.

**Null test** (the false-alarm rate): the same rule between the two halves of each visit's sessions (2–4 weeks
apart). Almost nothing changes that fast, except real fast variables (eclipsing binaries, RS CVn, YSOs): 8 of the
pilot's 9 null alerts are Gaia DR3 variables. So "null alerts Gaia doesn't know" is the false-alarm rate and the
plain null rate is an upper bound. Two versions: the floors fitted between visits ("visit floors") and the halves'
own floors (stricter: smaller floors, more alerts).

**Who is listed.** Stars with their own SPHEREx source, and forced-only catalogue stars outside the zone box; no Gaia
star ≥ 2.5 mag brighter within 20″ (its wings land differently after the 180° roll); survives the session drop.

| zone | visits compared | listable stars | alerts | after session drop = listed | per 1,000 | Gaia DR3 variables (flag or class) |
|---|---|---|---|---|---|---|
| pilot | 1 → 2 | 40,893 | 21 | 18 | 0.44 | 9 |
| t_rwaur | 1–2 → 3 | 97,830 | 47 | 37 | 0.38 | 23 |
| t_rdra | 1–6 → 7 | 38,251 | 20 | 17 | 0.44 | 7 |
| all | | 176,974 | 88 | 72 | 0.41 | 39 (54%) |

| zone | null, visit floors (per 1,000) | of which not Gaia variables | null, own floors | not Gaia variables |
|---|---|---|---|---|
| pilot | 9 / 76,491 (0.12) | 1 (0.013) | 19 (0.25) | 7 (0.09) |
| t_rwaur | 71 / 219,492 (0.32) | 33 (0.15) | 145 (0.66) | 83 (0.38) |
| t_rdra | 28 / 196,785 (0.14) | 20 (0.10) | 35 (0.18) | 23 (0.12) |

Expected false alarms among the listed: pilot 0.5–4 of 18, t_rwaur 15–37 of 37 (an upper bound: 23 of its 37 are
Gaia variables), t_rdra 4–5 of 17. Gaia variable share of a random control of watched stars: pilot 1.8%, t_rwaur
3.9%, t_rdra 1.6%; of the listed: 50%, 62%, 41%.

**Before the rules: what the artefacts were** (pilot, 118 raw alerts of 45,559 stars, 2.6 per 1,000):
- 91 forced-only stars inside the zone (above). Their changes are 20–200%, both signs, D1–D6, with a nearest SPHEREx
  source 6–20″ away; 44 of 177 forced-only stars without a CatWISE counterpart within 8″ alerted.
- 6 with a Gaia star ≥ 2.5 mag brighter within 20″ (more in the forced-only group: 58 of 118 overall).
- 3 that one session carried (session drop).
- Known asteroids crossing: 5 of 118 had an MPC asteroid within 15″ during the visits, against 3.6% of 4,000 random
  watched stars: not a cause.
- Blends (fragments: ≥ 2 Gaia G < 19 within 8″): 1; saturated: 8 (kept, with a `saturated` check).
- Bad photos: lightcurves' `photo_ok` (qa red photos, photos whose Gaia points sit off as a group) already removes
  them; the zone zero points are within 1%.
- Edges: forced-only stars outside the box are fine (3.9 per 1,000 in the pilot, 0.5–0.6 in the featured zones).

**The summary table is not enough.** lightcurves_summary.parquet has each visit's median but no per-visit error or
count; the best fallback (population scatter in brightness bins) gives 532 pilot alerts against 118 from the points
(4.5×). Upper bounds from it: hunt 2,366 of 294,765 (8.0 per 1,000), ecl04 1,033 of 229,121 (4.5). The rerun should
store the per-visit table (below).

**Top 20 listed** (by σ; "forced" = forced photometry only, outside the zone box; checks other than
`other_detectors_agree`, which all have):

| # | zone | star | G | measured | det | before → after (× usual) | change | σ | other dets agree / disagree | Gaia variable | checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | t_rwaur | Gaia DR3 181458215025292160 | 12.1 | forced | D5 | 1.14 → 0.83 | -27% | 20.7 | 2 / 0 | – | – |
| 2 | t_rwaur | Gaia DR3 156141684919662336 | 13.4 | forced | D5 | 0.85 → 1.11 | +30% | 17.7 | 1 / 0 | LPV | known_variable |
| 3 | t_rwaur | Gaia DR3 156024518209509248 | 15.5 | forced | D1 | 1.09 → 0.87 | -20% | 15.6 | 1 / 1 | ECL | detectors_disagree, known_variable |
| 4 | t_rdra | Gaia DR3 1636889826953275392 | 11.7 | forced | D2 | 1.12 → 0.84 | -24% | 13.1 | 1 / 0 | ECL | known_variable |
| 5 | t_rwaur | Gaia DR3 160747814004063488 | 14.3 | forced | D4 | 1.10 → 0.92 | -17% | 11.6 | 1 / 0 | – | – |
| 6 | t_rwaur | Gaia DR3 157525587802767360 | 13.7 | forced | D6 | 1.02 → 0.89 | -13% | 10.8 | 1 / 0 | LPV | known_variable |
| 7 | t_rwaur | Gaia DR3 157247170843175040 | 11.7 | forced | D6 | 1.04 → 0.92 | -11% | 10.6 | 1 / 0 | YSO | known_variable |
| 8 | t_rwaur | Gaia DR3 3446722593755424768 | 13.3 | forced | D5 | 1.03 → 0.94 | -9% | 9.6 | 2 / 0 | YSO | known_variable |
| 9 | t_rwaur | Gaia DR3 156111448348782976 | 13.7 | forced | D2 | 1.07 → 0.94 | -12% | 9.4 | 1 / 0 | – | – |
| 10 | t_rdra | Gaia DR3 1630169891059069184 | 14.2 | forced | D2 | 0.97 → 1.13 | +16% | 9.3 | 1 / 0 | RR | known_variable |
| 11 | pilot | CatWISE J081628.26+142456.2 | – | forced | D2 | 0.18 → 1.77 | +876% | 9.1 | 1 / 0 | – | – |
| 12 | t_rdra | Gaia DR3 1642624604726507776 | 17.3 | forced | D4 | 1.08 → 0.94 | -13% | 9.0 | 2 / 0 | – | – |
| 13 | t_rwaur | Gaia DR3 3422140881012182528 | 13.8 | forced | D5 | 0.96 → 1.10 | +15% | 8.8 | 1 / 0 | LPV | known_variable |
| 14 | t_rwaur | Gaia DR3 181848576013562880 | 14.6 | forced | D2 | 1.14 → 0.89 | -22% | 8.7 | 3 / 0 | ECL | known_variable |
| 15 | t_rdra | Gaia DR3 1635175486463727360 | 15.0 | forced | D1 | 0.90 → 1.09 | +21% | 8.5 | 1 / 0 | – | – |
| 16 | pilot | Gaia DR3 656825156689209344 | 12.0 | own | D1 | 1.10 → 0.91 | -17% | 8.5 | 2 / 0 | – | bright_neighbour |
| 17 | pilot | Gaia DR3 656753830169212288 | 15.8 | own | D2 | 1.09 → 0.88 | -19% | 8.2 | 3 / 2 | ECL | detectors_disagree, known_variable |
| 18 | t_rwaur | Gaia DR3 160806500436910080 | 14.9 | forced | D4 | 1.17 → 0.87 | -26% | 8.2 | 1 / 0 | – | – |
| 19 | t_rwaur | Gaia DR3 3446880103093387264 | 13.1 | forced | D5 | 1.06 → 0.93 | -12% | 8.0 | 3 / 0 | – | – |
| 20 | t_rdra | Gaia DR3 1635690367143415552 | 16.6 | forced | D2 | 1.15 → 0.94 | -18% | 8.0 | 1 / 1 | – | detectors_disagree |

The 20 listed stars with their own source (pilot 16, t_rwaur 4): 13 Gaia DR3 variables, classes ECL 9, RS 2, LPV 1. #11 is the one to look at first: a CatWISE-only object (no Gaia) on the pilot's east edge that went from
0.18 to 1.77 of its usual D2 brightness (D1 +256%) between 23 Oct 2025 and 10 Apr 2026; it lies in the hunt zone too,
where it may have its own source.

**Example alert text** (changes.brightness_text, `look` after the bar):
"Gaia DR3 181458215025292160 went from 1.14 to 0.83 of its usual brightness in D5 (3.8–4.4 µm) between its earlier
visits 1–2 together and visit 3 (2026-09-12): 27% fainter, 20.7σ beyond its own scatter; other detectors: D2 -18%
(-10.6σ), D6 -37% (-11.3σ)." | "Viewer, months clock, D5: blink visit 2 (and earlier ones) against visit 3; it should
look fainter in visit 3 while the stars around it stay the same."

## Colour

The same visit pair across detectors: ln(after / before) against ln(wavelength), weighted line; an alert when the
slope is ≥ 5σ, changes the D1/D6 ratio by ≥ 10% and rests on ≥ 4 detectors. A brightness change of the same size in
every detector is not a colour change. Pilot 5 (null 0; one Gaia variable, DSCT), t_rwaur 1 (null 1–7 over three
visits), t_rdra 0 (null 0). Rare: show it as a check on a brightness alert ("it became redder") rather than its own
list, except when no single detector passes 5σ.

## Spectrum shape inside a detector

Each detector has 17 channels and a visit fills most of them, so a change of shape (a band deepening, not the level)
could be measured per visit. The stored spectra (spectra.py) are combined over all visits with only a last/first
ratio, and the pilot's points table has no wavelength column. Needs the per-visit table with wavelength (rerun, no
new extraction). With 2 visits it would still be one comparison per star; band changes of LPVs (water, CO) are the
expected first catch.

## Position: why there is no position alert

Pilot, 33,594 Gaia-matched astrometry fits (15 Nov tables, no parallax model, so Gaia parallax ≤ 3 mas only):
- **Offset from Gaia's place** (Gaia moved by its motion to the fit's epoch): after scaling the errors (k 1.86), 12%
  of quiet single Gaia stars sit ≥ 5σ off (S/N 5–10: 11%, 10–20: 16%, 20–50: 11%, 50–200: 7%, > 200: 1.7%). Gaia
  neighbours within 12″ explain 1,429 of the 3,920; the rest are faint uncatalogued neighbours inside 6″ pixels.
  RUWE > 1.4 (Gaia's own sign of a binary) is 3.9% of them against 6.2% of a control: the offsets say nothing about
  companions.
- **Motion against Gaia's**: 25 of 30,418 quiet stars (0.82 per 1,000) are ≥ 5σ off, with "motions" of 180–10,500
  mas/yr for stars Gaia measures at a few mas/yr. Two visits half a year apart give a 1σ of 45–925 mas/yr (10th–90th
  percentile); Gaia's is 0.02–0.5 mas/yr. SPHEREx cannot test a Gaia proper motion; every hit is ours.
- So: no position alert for Gaia stars. Stars Gaia has no motion for (454 in the pilot) and CatWISE-only stars are
  highpm's job already. An unseen companion needs ≥ 3 visits (a wobble: astrometry's `unsteady`, 16 Nov motion model;
  t_wise0855 has 5.6% of Gaia stars unsteady, too many to alert on yet) or Gaia DR4's epoch astrometry.
- changes.position_offsets keeps the measures (`pos_off`, `motion_off`, neighbour pull) for a star's own page.

## Appeared and vanished

From orphans.parquet (single-visit sources with no Gaia/CatWISE/2MASS entry, the other visit covered the spot with
≥ 6 photos, not a bright-star fragment, not the zone edge): static within the visit (drift < 3σ: a transient stays,
a TNO drifts), no MPC body, S/N ≥ 20, ≥ 8 dots in ≥ 2 detectors. Before the last two rules, 39 of the hunt's 46
candidates were three MWIR dots (D4 + D6) spread over 4–25 days: glitches that link joined.

| zone | sources | appeared | vanished |
|---|---|---|---|
| hunt | 311,448 | 0 | 2 |
| pilot | 46,622 | 0 | 1 (the hunt's first) |
| ecl04 | 264,731 | 1 | 1 |
| t_rdra, t_rwaur | 8,141, 15,894 | 0 | 0 |

- hunt/pilot source 63782006953200 (RA 120.743437, Dec +16.304645): 27 dots in 13 sessions, D1–D5, S/N 61, static
  (0.011 ± 0.011″/h), nothing within 23″ in Gaia, gone in visit 2 although 112 photos covered it. The strongest.
  To rule out: a ghost of the G 10.1 star 303″ away (the 180° roll moves ghosts); look at its dots' GHOST bits.
- hunt 62904007319000 (8 dots, 3 detectors) and ecl04 62184002903600 (10 dots, 2 detectors) have a Gaia star 4.9″
  and 3.1″ away: probably that star's dots split off (check `gaia_near`, warn).
- ecl04 66934002646800 appeared in visit 3: 10 dots, 3 sessions, 4 detectors, S/N 67.
- No null exists for this kind with two visits; the list is short enough to look at each one.
- What it cannot see: a flare or outburst shorter than ~3 sessions (link needs a source in ≥ 3 sessions; one-session
  events never become sources), anything below S/N 20, and "gone" for a catalogue star without forced photometry
  (forced list: Gaia G < 17.5). A forced star that fades shows up in the brightness rule ("it faded below detection").

## Which need extraction v2, which need more visits

- **Now** (stored tables): brightness, colour, appeared/vanished as above.
- **The combined rerun, no new extraction**: the per-visit light-curve table (makes the hunt and every zone possible;
  the summary fallback has 4.5× the false alarms); per-visit spectra (shape changes); stars with no spectrum
  (forced-only) get a colour model shared by all visits with a level per visit instead of lightcurves' free quadratic
  in ln λ fitted across all visits (that quadratic varies 2× inside one detector for faint stars and can absorb or
  invent a level change; not measured here: the pilot's points lack wavelengths); the relink fix of 16 Nov should
  shrink the "inside, no own source" group: measure it again.
- **Extraction v2** (docs/extraction-v2.md): the per-photo 5σ depth grids say how deep the other visit looked, so a
  "vanished" gets a limit ("not seen in visit 2, fainter than AB … in D2") and an "appeared" a "was not there
  before" limit; the matched-filter pass and peaks file find faint sources the sky run missed (isolated Gaia stars at
  aperture S/N 5–5.5: 33% → 85%), so appeared/vanished can go below S/N 20; measured
  noise per photo makes forced-photometry errors believable for faint stars; session coadds would catch flares
  shorter than three sessions. Partner flux (D1↔D4 …) is no help here: one exposure, not two visits.
- **More visits**: positions (wobble needs ≥ 3), periodic variables (a period needs many visits; R Dra's zone has 7),
  colour histories, and a null for appeared/vanished (a source seen in visits 1 and 3 but not 2).

## The alert itself

**What it says.** One plain sentence with the numbers and a `look`, like contract v3.23 `checks`
(changes.brightness_text, colour_text): the star's name, detector and wavelength range, before and after as a
fraction of its usual brightness, the change in % and σ, the dates, and the other detectors. Checks
(changes.brightness_checks): `one_session` strong (not listed today), `detectors_disagree`, `origin_disagrees`,
`bright_neighbour`, `blend`, `saturated` warn, `other_detectors_agree`, `known_variable` ok (with Gaia's class).

**Where it appears.** A new candidates kind `changed` ("Changed"), listed apart from the discoveries: a known star
doing something is not a new object, and 54% of the list are Gaia's own variables. On the site, a "Changes" filter or
tab next to To check, sorted by σ, with the Gaia variables after the others. Proposed contract text is in the PR.
The lead decides whether a Gaia-classified variable gets `status` `known` (listed apart, known_name
"Gaia DR3 variable (ECL)") or stays `open` with the `known_variable` check.

**Following a star.** Options, cheapest first; nothing is built.

| option | what | cost | personal data | catch |
|---|---|---|---|---|
| A. Browser watch list | "Watch" button stores star ids in localStorage; each visit to the site compares them with the latest changes file and shows a badge | ~1 day of web work; $0 | none | one browser; told only when they come back |
| B. Feed | a static Atom/RSS file per zone (or per watched id, made on request) listing changes per release | ~0.5 day; $0 (Hosting) | none | needs a feed reader |
| C. Firestore watches | collection `watches` {star id, created}; needs sign-in (Firebase Auth) so people can see and delete their own: today's rules are create-only | 1–2 days; free tier (50k reads, 20k writes a day) | an account id | auth UI; privacy notice |
| D. E-mail | C plus an address; a sender (Firebase "Trigger Email" extension over SMTP, or a transactional e-mail service's free tier of a few hundred a day); unsubscribe link | +1 day; $0 at this size | e-mail address | spam rules (CAN-SPAM/GDPR), bounces |
| E. Web push | C plus Firebase Cloud Messaging and a service worker | +1 day; $0 | a device token | browser permission prompt |
| F. Scheduled check between reruns | Cloud Scheduler (3 jobs free) starts a Cloud Run job daily: list new photos for the watched positions (S3 index or IRSA), forced photometry of cutouts (~0.2 MB range reads from NASA's public S3, free) plus ~50 Gaia reference stars per photo for the zero point, compare with the stored per-visit table, write changes | 3–5 days; ~1 CPU-s per photo: 1,000 watched stars × 60 new photos a visit ≈ 17 CPU-hours a release ≈ $1.5 on Cloud Run | none | duplicates part of the pipeline; worth it only if releases come often |

Recommended for the hackathon: A + B (no accounts, no personal data, $0), fed by the `changed` kind that every zone
rerun produces. C–E only if the lead wants people told without coming back; F only if new data arrives between full
reruns often enough to matter.

**What the lead must decide**
1. `changed` inside candidates.json (one more kind, its own tab) or a separate changes.json?
2. Gaia-classified variables: `status` `known` (listed apart) or `open` with an `ok` check?
3. Watch every star automatically (what the step does) or only stars people follow (needs F for anything between
   reruns)?
4. Accounts and e-mail (C/D/E) or none (A/B)?
5. List forced-only stars outside the zone box (most of the featured zones' stars: 52 of the 72 listed) or only
   stars with their own source (20)?

## Built (16 Nov, agent "watch2", branch p1/watch-export)

The lead decided: `changed` gets its own file and list (`/data/changes.json`, not candidates.json); every star is
watched by the pipeline (following is only about being told); forced-only stars are listed; stars Gaia already lists
as variable are listed, marked "Gaia already lists it as variable" and ranked below the unexpected changes; following
is a watch list kept in the browser plus a public Atom feed (A + B above: no accounts, no personal data, $0).

- `python -m skyshift_pipeline.changes_step --zones a,b --root gs://nasa-glazing-data --cache DIR` reads a finished
  zone (lightcurves, crossids, sources, fragments, refcat, orphans, photolist) and writes
  results/<zone>/changes.parquet and changes_report.json: brightness and colour (the listing rule above, with the
  session drop), vanished/appeared from orphans, each with `text`, `look` and `checks`. It reads one detector at a
  time (peak 1.4–1.9 GB on the featured zones). Gaia variability and bright neighbours outside refcat's box come from
  ESA's Gaia archive by source_id only (cached; when it is down the row says variability is unknown).
- `python -m skyshift_pipeline.changes_export --root gs://nasa-glazing-data --out ../web/public/data/changes.json`
  merges every zone's changes.parquet into web/public/data/changes.json and the feed changes.atom.
- Site: `#/changes` (web/src/changes/): each row says what changed in a sentence and numbers, carries the Gaia mark,
  opens the Viewer on the star (months clock, its detector) and has a ☆ Follow button (localStorage); "Following (N)"
  and the feed link sit above the list.
- Sample (pilot, t_rdra, t_rwaur): 69 changes (66 brightness, 2 colour, 1 vanished), 40 of them Gaia variables;
  182,580 stars watched; false alarms from the halves null 0.12–0.33 per 1,000 stars. Brightness: the prototype listed
  72 (pilot 18, t_rwaur 37, t_rdra 17), the step 66 (18, 35, 13): bright neighbours are now also checked outside
  refcat's box, through Gaia (t_rdra: 6 alerts dropped for a neighbour, 4 for one session).
- Known bodies (16 Nov, branch p1/changes-known-bodies): every listed brightness or colour change is checked against
  the `known` step's session files (MPC asteroids and comets with kete, JPL Horizons planets and moons). A body within
  15″ (2 × FWHM) of the star during an exposure of the compared visits: the listing rule runs again without those
  sessions; a change that goes is not listed (changes_report.json `dropped_known_body`), one that stays gets the check
  `known_body_passed` (warn: body, UTC time, separation, sessions to blink on the hours clock). Sample: 1 of 68 warned
  ((17051) Oflynn, V 19.1, 9″ from Gaia DR3 156329976283479040 on 2026-03-06, in a session without D3 points), 0
  dropped; t_rdra and t_rwaur have no Horizons files (their `known` ran before 15 Nov), and no planet came within 14°.

## What goes into the combined rerun

1. lightcurves writes `lightcurve_visits.parquet`: per light curve (source_id, ref_id, detector, origin) and visit,
   and the two halves of each visit for the null: n, n_sessions, mjd_mid, mean rel_flux (zero point applied), err,
   chi2_nu, median, zero_point (changes.visit_table + zero_points). Pilot 656k rows / 16 MB, halves 1.25M rows /
   28 MB; the hunt about 7× that. Cost: a group median over the points it already holds.
2. lightcurves: stars without a spectrum get a shared-colour model with a level per visit (above).
3. A `changes` step after lightcurves, fragments, astrometry and orphans: brightness and colour (listing rule,
   session drop), appeared/vanished, position measures; → results/<zone>/changes.parquet and changes_report.json
   (floors, zero points, alerts and null rates per population, Gaia variable shares).
4. Gaia DR3 `phot_variable_flag` and the classifier's class in refcat (by id from ESA's archive, or IRSA's
   gaia_dr3_source joined in the Gaia tiles) so `known_variable` needs no extra query.
5. The exporter adds kind `changed` with `checks`; the static frames for the top ones on the months clock.

## Reproducing

Scratch scripts (not in the repo) ran on the shared local copy:
`run_bright.py <zone>` (visit tables, floors, between and null), `analyse.py` (Gaia flags, session drop),
`population.py` and `final.py` (populations, neighbour rule, listing, texts), `run_position.py`, `run_new.py`,
`asteroid_check.py`, `null_share.py`, `gaia_share.py`. The library calls are all in changes.py; a `run(store, zone)`
entry point is the step's job in the rerun.
