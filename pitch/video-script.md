# SkyShift: the 29-second video

The demo for the Space Apps project page. **30 s is a hard limit**; we cut to 29.0 s and check the
length with a tool before upload (`ffprobe -v error -show_entries format=duration -of csv=p=0 skyshift.mp4`).

- **Footage:** screen recordings of the live site, https://skyshift.web.app, on real SPHEREx data.
  Nothing is AI-generated, so no watermark is needed. No NASA logos, no people under 18.
- **Captions:** English, burned in, bottom third, at least 2.5 s on screen each.
- **Voice-over:** optional, spoken by a teammate, 64 words in all. If there is no voice, the
  captions carry the story alone.
- **Credit line,** small, bottom right, from 0:03 to the end:
  "Real data: NASA SPHEREx (IRSA) and ESA Gaia DR3".
- **Browser:** Chrome, window 1440 × 900, page zoom 100%, bookmarks bar hidden. Open every target once
  before recording so its frames are cached.

Every action below was checked on the live site on 14 November 2026.

## Shot list

| Time | On screen (real site) | Caption | Voice-over |
|---|---|---|---|
| 0.0–3.0 | The Barnard's Star view as it opens, playing. Title card over it. | **SkyShift** · *[team name]* · *[the four member names, as registered]* · NASA Space Apps 2026: Planet X and SPHEREx | "Pluto was found by blinking two photos." |
| 3.0–7.0 | Click the coordinate box, paste `08h55m10.831s -7d14m42.53s`, press **Blink**. The WISE 0855 chip lights up and its frames load in D6. | Paste any coordinates. These are the challenge's own. | "SkyShift lets anyone blink NASA's SPHEREx infrared sky." |
| 7.0–9.5 | Press Space to pause on the May 2026 visit. Click **D1**: the dot at the target has a blue Gaia ring (a background star). Click **D6**: a dot with no Gaia ring sits at the target. | At 1 µm: a background star. At 4.4–5 µm: a dot Gaia doesn't list. | "A brown dwarf Gaia can't see," |
| 9.5–12.0 | Zoom into the card: **7.05″ ± 0.90″** and "The catalogue predicts 8.06″". | WISE 0855, the coldest known brown dwarf. Moved 7.05″ in a year; predicted 8.06″. | "caught moving." |
| 12.0–16.0 | Click the **Barnard's Star** chip, then **Subtract**. Visit 3 minus visit 1: orange above the star, blue below, because it moved north. The card shows **10.83″ ± 0.36″** and "Gaia predicts 10.27″". | Barnard's Star, measured on SPHEREx: 10.83″ ± 0.36″ between visits. Gaia predicts 10.27″. | "Every movie gets a number, checked against Gaia." |
| 16.0–19.5 | Click the **3I/ATLAS** chip (Hours clock, 20′, D3). Set Speed to 8/s. The comet stays at the centre and the stars stream past. | Hours clock: interstellar comet 3I/ATLAS, 17 exposures on 14 Dec 2025. | "A second clock, in hours, for comets and asteroids." |
| 19.5–22.0 | Click **Scoreboard**, scroll to the V4 card. | 60 of 60 known asteroids re-found where NASA predicted them. | (no voice) |
| 22.0–25.5 | Back to WISE 0855, card set to **By visit**: the spectrum in three colours, molecule bands marked. Then click a dot in the movie: "Flag this dot?" and the **Flag it** button appear. | Spectra on the fly from six detectors. Click any dot to flag it; anyone can vote. | "Spectra on the fly. Anyone can flag a dot and vote." |
| 25.5–29.0 | Scroll to **Sky around this spot**, choose **SPHEREx**, and scroll the mouse wheel to zoom out to a wide field. | New SPHEREx data every week. Blink it yourself: **skyshift.web.app** | "New data every week. Blink it yourself at skyshift dot web dot app." |

Total: 29.0 s. Voice-over: 64 words.

## How this differs from the plan's storyboard

The plan (docs/skyshift-hackathon-plan.html, "The video: cut to 29.0 s") was written before the site
existed. These lines changed to match what the live site shows:

- **WISE 0855 in D1 is not empty.** A background star lies 1–2 pixels from it, with a Gaia ring. The
  caption says so; the point of the shot is the ringless dot in D6.
- **WISE 0855's step is not visible by eye.** It moves 0.66 px per visit. The shot shows the dot and the
  measured number, not a visible step. "−20 °C" is dropped: it is not one of our numbers.
- **3I/ATLAS has 17 exposures on 14 December 2025 in D3 on the 20′ field**, over about 13 hours,
  not "55 images over 20 hours". The frames follow the comet, so the stars move, not the comet.
- **The asteroid-in-its-ring shot is replaced by the scoreboard's V4 card.** On the live (59) Elpis
  view the asteroid crosses a fixed field; it is clearer to show the 60 of 60 result.
- **Flagging is shown up to the question, not the click on Flag it.** Flags on the live site are
  permanent (create-only). If the team wants the vote step on camera, flag a dot you would really
  list, and leave it.

## Before recording, check on the recording laptop

- The sky view needs WebGL2. Zoom out once by hand and make sure the SPHEREx tiles draw.
- The Months clock at 2/s shows each frame long enough to read; the comet shot needs 8/s.
- Captions don't cover the card's number or the target marker.
