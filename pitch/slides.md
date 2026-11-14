# SkyShift: seven slides

The fallback demo if the video can't play, and the deck for the live pitch. The submission guide allows
**at most 7 slides, title included**, in English, and asks for: team name, member names, the challenge,
an image of the project, the user experience, and how NASA and partner data make it possible.
Screenshots are in `pitch/screens/`; all are captures of the live site on real data.

---

## 1. SkyShift

- Blink NASA's SPHEREx infrared sky, measure what moved, check it against Gaia.
- *[Team name]* · *[the four member names, as registered]*
- NASA Space Apps 2026, challenge "Planet X and SPHEREx"
- skyshift.web.app

**Image:** `screens/barnard-months.png`

## 2. SPHEREx photos are hard to blink

- SPHEREx maps the whole sky every six months in 102 colours, from 0.75 to 5 µm.
- Each exposure sees a spot at a different wavelength, so a plain blink flickers with colour.
- Nearby stars move less than one 6.15″ pixel per visit: Barnard's Star steps 5.2″ (0.84 px).
- Pluto was found by blinking two photos. Volunteers found brown dwarfs the same way. SkyShift makes SPHEREx blinkable.

**Image:** `screens/wise0855-d1-d6.png` (WISE 0855: a background star at 1 µm; at 4.4–5 µm, a dot with no Gaia ring)

## 3. Paste, blink, measure, flag

- Paste coordinates, in the challenge's format or in degrees, or click one of eight targets.
- Blink, Subtract or Add, in any of six detectors, at 5′ to 60′.
- The card gives the shift, its error and Gaia's prediction, with the limits written out.
- Click any dot to flag it; anyone votes Mover, Changing, Junk or Not sure; export as CSV. Works on a phone.

**Image:** `screens/phone.png` beside `screens/flag-dot.png`

## 4. Two clocks and 102 colours

- Months: one median stack per visit and detector, for stars and brown dwarfs.
- Hours: single exposures inside a visit, for comets and asteroids (3I/ATLAS, 14 Dec 2025).
- A spectrum on the fly from every cutout, by detector or by visit, with water, methane, CO₂ and CO bands marked.

**Image:** `screens/atlas-hours.png` beside `screens/wise0855-card.png`

## 5. How we know it works

- Barnard's Star moved 10.83″ ± 0.36″ between visits; Gaia predicts 10.27″ (+5.5%).
- 5 of 5 test objects found where predicted, in 19 of 20 visits.
- 60 of 60 known asteroids re-found where NASA predicted them; control 3′ away: 1.0% of photos.
- Brightness against 2MASS Ks: 1.010, scatter 3.0% over 3,918 stars.
- 8 of 11 checks pass, 1 partial, 2 cut; all printed on the site.

**Image:** `screens/scoreboard.png`

## 6. Made possible by NASA and ESA data

- NASA SPHEREx DR1 and QR3 images from IRSA, read live, so weekly data appears without code changes.
- IRSA's Moving Object Search Tool and 3I/ATLAS table: where known asteroids and the comet should be.
- 2MASS, AllWISE and the SPHEREx all-sky cube as sky layers, served by IRSA.
- ESA Gaia DR3: every star's position and motion, moved to each frame's date.

**Image:** `screens/barnard-subtract.png` (visit 3 minus visit 1: orange where light arrived, blue where it left)

## 7. What's next

- Limits we state: no SPHEREx parallax yet (at most about 1.1″ between visits); bright stars saturate.
- Finish the 100 deg² hunt zone and put its unknown movers up for votes.
- A third visit makes a fast-star search possible; the whole sky is costed at about €75 of compute.
- Try it now: **skyshift.web.app** · code: github.com/cucuradu/skyshift (Apache-2.0)

**Image:** a QR code to https://skyshift.web.app (make it from the URL; don't use a third-party logo)

---

**Who presents:** P4 presents; P1 answers data questions. The presenting laptop carries an offline
copy of the site and the video.
