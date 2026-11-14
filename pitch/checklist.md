# Submission checklist

From the plan (Risks, row 8: "video over 30 s, private link, missing AI disclosure, unlisted source,
pre-event code") and the Space Apps project submission guide. State as of Saturday 14 November 2026,
evening. First full save **Sat 22:00**; final save **Sun 14:30** (hard stop 14:45), before the
15:00 local deadline. Owners: P1 lead and science, P2 server and deploys, P3 sky and cards,
P4 blink, votes and pitch.

## Eligibility

| Item | State | Who | Notes |
|---|---|---|---|
| Demo is a video of 30 s or less, or at most 7 slides | todo | P4 | Script: `video-script.md` (29.0 s). Check the cut with `ffprobe` before upload. Slides drafted in `slides.md` as the fallback. |
| Video in English, or with English subtitles | todo | P4 | Captions are burned in. |
| No images, video or voice of anyone under 18 | todo | P4 | Screen recordings only. |
| Demo link is public and opens in a private window | todo | P4 | YouTube, public or unlisted. |
| Final project link is public, no login | done | P2 | https://skyshift.web.app opened in a fresh browser on 14 Nov. Repeat at 22:00 and Sun 14:30. |
| Repository is public | done | P1 | https://github.com/cucuradu/skyshift is public. |
| All members registered at the same local event, not waitlisted, listed on the team page | todo | all | Each person checks their own registration. |
| Every required field filled in | todo | P4 | Paste from `project-page.md`. |
| Terms and originality checkboxes ticked | todo | P4 | Only after the references are complete. |

## Use of AI ("Super Intelligence")

| Item | State | Who | Notes |
|---|---|---|---|
| Disclosure in the README | done | P4 | "Use of AI" section, with what the team decided and checked. |
| Disclosure on the project page | done | P4 | "Use of Super Intelligence (SI)" in `project-page.md`. |
| SI form field answered | todo | P4 | Same text. The form answer isn't shown to judges; the page text is. |
| AI-written code acknowledged in metadata | done | all | `Co-Authored-By` line on commits made with Claude Code. |
| No AI-generated image or video; no NASA logos | done | P4 | Every image in `screens/` is a capture of the live site. Keep it that way in the video and GIF. |
| Every sentence of the pitch texts read by a person against the live site | todo | P1, P4 | Claude drafted `pitch/`; the team owns every claim. |

## Sources listed

| Item | State | Who | Notes |
|---|---|---|---|
| NASA data named one by one | done | P4 | SPHEREx DR1 and QR3, all-sky cube, MOST, the 3I/ATLAS table, 2MASS, AllWISE, CatWISE2020, docs. |
| Partner data (ESA Gaia DR3) listed | done | P4 | In its own field. |
| Every library, service and paper listed | done | P4 | README "Data and credits" and the page's "Other references". Add any library added after 14 Nov. |
| SPHEREx acknowledgement and its citations, word for word | done | P1 | Copied 14 Nov from IRSA's SPHEREx mission page and DR1 overview (acknowledgement, IRSA statement, DOIs IRSA678/662/679, canonical papers Bock, Akeson, Korngut, Crill) plus the standard Gaia acknowledgement and DR3 citations. In README and project-page.md. |
| Aladin Lite licence (GPL-3.0) stated | done | P3 | Loaded at run time, not bundled. |
| Our licence (Apache-2.0) | done | P1 | `LICENSE` in the repo root. |

## No pre-event code

| Item | State | Who | Notes |
|---|---|---|---|
| All product code written after 09:00 on Sat 14 Nov | done | P1 | The first commit is at 10:03 on 14 Nov. Before 09:00: cloud account, planning notes and repo configuration only. |
| The plan's simulated blink demo isn't used in the product | done | P4 | It stays in `docs/` and is labelled as predating the event. |
| Pre-event work described plainly | done | P4 | README and page, "Before and after kickoff". |

## The project page

| Item | State | Who | Notes |
|---|---|---|---|
| Name, summary (≤ 150 words), description, links | done (draft) | P4 | `project-page.md`; summary is 140 words. |
| Challenge-to-feature table | done | P4 | In the description. |
| Limits stated (parallax, saturation, Epsilon Indi pair, which Planet X) | done | P4 | In the description and on every object card. |
| GIF at the top (under 8 MB, 640 px wide, about 9 s) | todo | P4 | WISE 0855 D1 → D6, Barnard's Star with its readout, 3I/ATLAS. Until then use `screens/barnard-months.png`. |
| Screenshots uploaded (PNG, under 10 MB each) | todo | P4 | `screens/`, all under 400 kB. |
| Video link added to the page and to the README | todo | P4 | After upload. |
| Numbers frozen and matching `validation.json` | done | P1 | Checked on 14 Nov. Recheck if any check is re-run. |

## Fixes to raise with owners (outside pitch/)

| Item | State | Who | Notes |
|---|---|---|---|
| Scoreboard intro says "Eight checks"; there are eleven | todo | P3 | `web/src/score`. |
| V8 headline says "speed pending (site not deployed)"; the site is deployed | todo | P1 | Run `scripts/demo_check.py` for the speed half, then rebuild `validation.json`. |
| Asteroid card says "the frames follow it along its known orbit"; asteroid frames keep a fixed centre (only the comet's follow it) | todo | P3 | `web/src/card`; visible on the Elpis and Euphrosyne views. |
| Unknown movers list is empty ("published after the hunt-zone run") | todo | P1 | Fine to submit as is; the page says it is pending. |

## Optional

| Item | State | Who | Notes |
|---|---|---|---|
| FortiGuard web-filter classification for skyshift.web.app | todo | P2 | So judges' networks don't block the site. |
| Offline copy of the site and the video on the pitch laptop | todo | P2 | Sunday 15:30 live pitch. |
| Rehearsals Sun 11:00 and 13:00, all four | todo | all | |
