# Planet X and SPHEREx: challenge text, judging and submission rules (14 Nov 2026)

Research for SkyShift. Pages were read on 14 Nov 2026, the morning the hackathon opens.
Quotes are kept under 15 words; everything else is paraphrased. "Not found" means the pages read did not say it.

**How the pages were read.** spaceappschallenge.org loads its challenge and team pages with JavaScript, so WebFetch failed (TLS error) and plain curl returned empty shells. The guides (judging, submission, FAQ, legal, 2026 hackathon page) came back as normal HTML through curl. The challenge page, the challenge list, past winners and local event pages were read by rendering them in headless Chrome. Calling the site's GraphQL API directly was blocked by the session's permission system, so I didn't do it.

---

## 1. The challenge page

**URL:** https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/
(Resources tab: same URL with `?tab=resources`.) It is one of 14 challenges in 2026 ([list](https://www.spaceappschallenge.org/2026/challenges/)).

**Labels on the page:** Difficulty **Advanced**. Subjects: Astrophysics, Planets & Moons, Software.

### Summary and background (paraphrased unless quoted)
- Since 2025, SPHEREx has mapped the whole sky every six months in 102 near-infrared bands, with images of more than a billion objects. Comets, asteroids, stars, brown dwarfs "and even new planets" show up by shifting position between images.
- The goal: "create a public-facing web tool to display images of the sky from SPHEREx", "making it easy for anyone to quickly see how they change over time".
- Background: asteroids and comets orbit the Sun, nearby stars move across the sky, and **Earth's own orbit shifts the apparent positions of nearby objects**. Watching these motions finds new objects and **measures their distances** (parallax). Pluto was found by comparing two images taken at different times.
- Volunteers have used this method to find asteroids, comets, stars and brown dwarfs. The page says most known comets and brown dwarfs were found by volunteers, through projects like backyardworlds.org.
- SPHEREx is especially sensitive to objects rich in **hydrogen, water, carbon dioxide, carbon monoxide** and other molecules. Its images include brown dwarfs, quasars, comets, asteroids, stars, supernovae and galaxies, which "often overlap or vary in brightness".
- What NASA hopes for: creative new tools that "help as many people as possible pitch in to make discoveries".

### Objectives (paraphrased unless quoted)
- "Users will enter the coordinates of a patch of sky", given as right ascension and declination.
- The tool shows "a series of images of that patch like a little video", so moving objects are easy to spot.
- **Test it on:** Barnard's Star; Epsilon Indi B ("a pair of brown dwarfs orbiting a star"); WISE 0855 (a fast-moving brown dwarf, given as RA 08h55m10.831s, Dec −7d14m42.53s); and the variable stars RW Aurigae and R Draconis.

### Potential considerations (optional, "may but are not required to")
1. New data arrives fast, so the tool should be easy to update with new releases.
2. Think creatively about showing the 102 bands. For example, add up each of the six detector arrays separately for better signal-to-noise. "Extra kudos if your tool can generate spectra on the fly."
3. Options to add or subtract images taken at different times.
4. Let users choose the field of view. A larger field tracks faster movers but transfers more data.
5. Overlays from other telescopes, such as Gaia or WISE.
6. Coordinates in the URL, so the tool can be embedded in other websites and projects.
7. Show metadata such as contamination flags.
8. Use existing tools: "Astrophy tools" (sic, presumably Astropy) for handling data, and CDS Aladin for display.

### Resources tab
NASA:
- IRSA SPHEREx resources: https://irsa.ipac.caltech.edu/Missions/spherex.html
- SPHEREx tutorial notebooks: https://caltech-ipac.github.io/irsa-tutorials/spherex/
- WiseView, http://byw.tools/wiseview: a similar tool for WISE data, offered "as a model or as inspiration". It refused connections in our link check.
- Gaia data via STScI/MAST: https://archive.stsci.edu/missions-and-data/gaia. The page says to use the latest release, DR3.

Space agency partner (CSA):
- NEOSSat overview: https://www.asc-csa.gc.ca/eng/satellites/neossat/
- Canadian Astronomy Data Centre (CADC): https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/
- NEOSSat Python tutorial: https://github.com/asc-csa/NEOSSAT_Tutorial
- NEOSSat dataset: https://data.asc-csa.gc.ca/dataset/neossat

The tab also says NASA doesn't endorse non-US-government sites, and that participants must follow each site's data-use terms.

### Point by point: challenge vs our "Every NASA suggestion, answered" table

| Challenge item | Our plan | Verdict |
|---|---|---|
| **Objective:** user enters RA/Dec, sees "a little video" | "Search by name or position" | **Under-weighted.** The challenge makes coordinate entry the main input. Make it the first thing on the landing page, accept the exact format the challenge uses (`08h55m10.831s -7d14m42.53s`) as well as decimal degrees, and add one-click chips for the five test objects. |
| **Objective:** test on the five named objects | Covered in "Test objects" and Phase A, not in the table | Add a row to the table. Judges will check exactly these five. Show each one working, with numbers. |
| Epsilon Indi B is "a pair of brown dwarfs" | Treated as one target | **Missing nuance.** The pair (Ba/Bb) is far tighter than a 6″ pixel, so it will look like one dot. Say so in the UI (one blended source; motion shared with Eps Indi A) instead of letting judges expect two dots. |
| Background: Earth's orbit shifts nearby objects; motion measures **distance** | Not mentioned | **Missing.** The 6-month cadence puts Earth on opposite sides of its orbit, which is the ideal setup for parallax. Real parallaxes (≤0.8″) are far below a pixel, so don't claim to measure them. Instead, add one explainer line plus a Gaia parallax/distance field on the card. Cheap, and it hits "Best Use of Science". |
| Background: sensitivity to H, water, CO₂, CO | Spectrum view, shape marked "illustrative" | **Under-weighted.** Mark molecular bands on the spectrum: water ice about 3.0 µm, CH₄ about 3.3 µm, CO₂ about 4.27 µm, CO about 4.6 µm. Positions are approximate; check them against SPHEREx material before showing. Optionally overlay a brown-dwarf template. |
| Background: objects "often overlap or vary in brightness" | False-alarm risk listed | Covered in the pipeline. Also show it in the UI: a "blended / confused" flag on suspects. |
| "Help as many people as possible pitch in" | Votes real / junk / not sure | Partly covered. **Missing: what happens after a vote** (consensus count, exportable candidate list, a "report to scientists" path such as a Backyard Worlds link). This is what makes it citizen science rather than a poll. |
| C1. Easy to update | Live archive queries; incremental deep-zone job | Covered. **Add proof in the UI**: a "Data: DR1 + QR3, checked <date>" badge. |
| C2. Combine each detector; spectra on the fly ("extra kudos") | Six stacked channels, false colour, 102-point spectrum | Covered. Spectra on the fly is the named bonus, so it should appear in the 30-second video. |
| C3. Add/subtract images | "Add passes", "Show only changes" | Covered. |
| C4. Choose field of view | Small/medium/large | Covered. Show the trade-off (load time) as the challenge describes it. |
| C5. Overlay Gaia or WISE | Gaia DR3 dots; WISE/2MASS image layers; asteroids | Covered. Possible upgrade: a WISE *catalogue* layer with proper motions (CatWISE2020), which flags known brown dwarfs. **Also: Gaia is an ESA mission, and ESA is a 2026 Space Agency Partner**, so list it as partner data. |
| C6. Coordinates in URL, for embedding | `?ra=&dec=&fov=` | Mostly covered. **Missing: an explicit embed mode** (`&embed=1`, minimal chrome, iframe-safe) plus a "copy embed code" button. The challenge names embedding as the reason for the feature. |
| C7. Metadata like contamination flags | Flag overlay, photo info panel | Covered. |
| C8. Astropy and Aladin | Astropy/astroquery, Aladin Lite v3 | Covered. |
| Resource: WiseView "as a model" | Borrowed as inspiration | Covered. Say it in the project text ("WiseView for SPHEREx, plus spectra"). |
| Resource: CSA NEOSSat / CADC | Not used | **Missing.** The submission guide "highly" encourages partner data. A minimal option: list Gaia (ESA) as partner data. Optional: link CADC for cross-checks. Don't build a NEOSSat feature unless time is spare. |
| Title: "Planet X" | Mentioned only in the plan's header | **Under-used as a story.** The title invites a "could someone find the next Planet X / Planet Nine with this?" hook. Keep it honest: SPHEREx at 6″ pixels finds nearby brown dwarfs and asteroids; a distant planet would be faint and slow. |
| "Public-facing", "anyone", "quickly" | Pre-cache 5 targets; on-demand cutouts elsewhere | **Risk.** Arbitrary coordinates depend on how fast IRSA responds. Set a time-to-first-frame target, show progressive loading (first pass frame first), and cache popular spots. |

Related prior work worth citing (and possibly using): Gagné et al. 2026, "A SPHEREx Pipeline and Spectral Library for Ultracool Dwarfs" (arXiv 2604.22012, https://arxiv.org/abs/2604.22012v1). It presents the SPIFF tool for extracting spectra of fast-moving sources in SPHEREx, and spectra for 6,003 ultracool dwarfs, 947 of them newly identified. Data at https://mocadb.ca. Its co-authors include Backyard Worlds people (Kuchner, Meisner, Caselden). It is a ready-made list of known brown dwarfs to validate against and overlay. It also tells us "first SPHEREx moving-source work" is not a claim we can make. Our claim should be the public blink-and-vote tool.

---

## 2. Judging (2026 guide)

Source: https://www.spaceappschallenge.org/resources/judging-awards-guide/ (it names 2026 dates, so it is the current version).

**Scale:** every level of judging (local, global, executive) scores projects "on a scale of 1-to-5, in each of the following criteria". **No weights are given.** The five criteria appear equal.

| Criterion | What the guide says (paraphrased) | A score of 5 means |
|---|---|---|
| **Impact** | Size of the problem addressed, and how far the project inspires or helps people (a broad audience vs a select few). | Addresses a significant problem, reaches a large audience, "could change the world". |
| **Creativity** | Originality: a novel concept, or a meaningful improvement on existing ideas. | Groundbreaking, never attempted before, stands out in its field. |
| **Validity** | Grounded in science; technically or logically feasible; works for its purpose. **Usability and user-friendliness count here.** | Scientifically sound, fully developed, ready for real use, highly usable. |
| **Relevance** | Fully and directly addresses *its* challenge. NASA data must be a core part, not "a superficial addition". | NASA data is integral and the depth of data use adds credibility. |
| **Presentation** | Clearly conveys the challenge, the solution and why it matters; engaging storytelling. "Judges evaluate the demo and full project to compute this score." | Polished, well structured, compelling. |

The guide asks teams to "pay equal attention to the final criterion: presentation". It recommends investing in the pitch and story, and having team members with storytelling and design skills.

**Eligibility for global judging:**
- Respond to an official challenge. "Create Your Own Challenge" was described as ineligible in search snippets; that wording wasn't on the 2026 page I read.
- All members registered at the **same Local Event, not waitlisted**, and listed on the team page. 1–6 members for Local Events.
- All required fields completed; a demo within the limits (see section 3); English; submitted by the deadline.
- Two checkboxes: the terms, and an originality statement saying every external resource (code, text, images, even open source) is listed in References.
- Review for China affiliation.

**Phases and dates:**
1. **Local judging:** a panel recruited by the Local Lead scores with the same criteria and picks **Global Nominees**, announced **3 Dec 2026**. Local awards are separate from global ones. Universal Event projects are judged only against other Universal Event projects. The number of nominees per local event is not stated on the pages read.
2. **Global judging:** expert judges choose **Global Finalists** and **Honorable Mentions**, announced **17 Dec 2026**.
3. **Executive judging:** NASA and partner-agency leaders pick **10 Global Winners**, announced **26 Jan 2027**, with a virtual celebration in early 2027.

**The 10 global awards** (short descriptions quoted or paraphrased):
- **Best Use of Science:** best and most valid use of the scientific method.
- **Best Use of Data:** "best makes space data accessible or leverages it to a unique application". *Best fit for SkyShift.*
- **Best Use of Technology:** most innovative use of technology.
- **Galactic Impact:** most potential to improve life on Earth or the universe.
- **Best Mission Concept:** most plausible concept and design.
- **Most Inspirational:** "captures our hearts".
- **Best Storytelling:** communicates the potential of open data through storytelling.
- **Global Connection:** best connects people around the world through technology. *Also fits, through the crowd voting.*
- **Art & Technology:** combines technical and creative skills.
- **Local Impact:** greatest potential to make a difference in the team's local community.

Scale for context: in 2025 the 10 winners came from 18,860 teams and 11,500+ submissions, through 45 Global Finalists ([winners](https://www.spaceappschallenge.org/2025/awards/global-winners/), [finalists](https://www.spaceappschallenge.org/2025/awards/global-finalists/), [meet the winners](https://www.spaceappschallenge.org/2025/meet-the-2025-global-winners/)).

---

## 3. Submission requirements and deadlines

Source: https://www.spaceappschallenge.org/resources/project-submission-guide/

**Window:** opens **Sat 14 Nov, 9:00**, closes **Sun 15 Nov, 23:59**, in the Local Event's time zone. Universal Event teams use the Team Owner's local time. You can edit and resubmit until the deadline; only the last saved version is judged. The guide says to ask your Local Lead about local judging times, which may differ (see section 5).

**Project page fields** (on the team page's Project tab):
- Project Name
- High-Level Project Summary: what you built, how it addresses the challenge, why it matters.
- Link to Project Demo
- Link to Final Project: public; no password, permission or registration.
- Detailed Project Description: what it does, how it works, benefits, goals, tools and languages.
- Use of Super Intelligence (SI): see below.
- NASA Data: to be eligible for a Global Award, "you must use at least one NASA data or resource".
- Space Agency Partner Data and Other References: **all** data, code, text and images used, including open source. The 2026 partners include **ESA**, CSA, JAXA, ISRO, UKSA and others.
- Images can be pasted or uploaded (**JPG, PNG, GIF up to 10 MB**). Videos and documents cannot be uploaded; link them instead.

**Weight of the page vs the demo:** the demo counts, but "the majority of judges' evaluation will be based on your project submission". A strong demo mainly helps the Presentation score.

**Demo limits (hard):**
- **A video of 30 seconds or less, OR a slide deck of at most 7 slides** (including title and intro slides).
- "Projects that exceed these limits will not be eligible for Global Judging."
- Host it externally (YouTube, Drive, GitHub, etc.) with a public link. Test the link in a private window. "If judges can't access your project, it can't be evaluated or judged."
- English. A video may be spoken in another language but must have English subtitles; slides must be in English.
- Suggested video structure: 0:00 names; 0:02 the challenge and why you chose it; 0:05 title and tagline; 0:10 how it works, the user's experience, and which NASA/partner data made it possible; 0:20–0:30 the future.
- Slides must include: team name, member names, the challenge chosen, an image of the project, the user experience, and how NASA and partner data make it possible.
- No images, video, audio or likeness of anyone under 18.

**Working link / repo:** a publicly accessible "final project" link is a required field. It can be a website, a repo or a hosted file. The guide doesn't say a live running app is mandatory, but judges must be able to open whatever you link.
For a new website, the guide recommends requesting a **FortiGuard web-filter classification** (https://www.fortiguard.com/faq/wfratingsubmit) so judges' networks don't block it. This is optional.

**AI ("Super Intelligence") rules for 2026:**
- The FAQ says that after a 29 Sep 2026 US Executive Order, NASA uses "Super Intelligence (SI)" in place of "AI". SI tools (LLMs, code generators, image tools) are **permitted** ([FAQ](https://www.spaceappschallenge.org/resources/participant-faqs/)).
- **Limit:** "SI should not be used to (come up with your idea" of how to solve the challenge.
- **Disclosure:** state where and how SI was used. Images and video "must include a visible watermark to indicate SI generation". Audio, code and data must acknowledge SI generation "in descriptive text and metadata".
- The SI form answer is for data collection only and **is not shown to judges**. Judges *do* see the watermarks, descriptive text and metadata in the project, and "evaluate originality, intent, and execution" of the SI-assisted parts.
- SI-generated content must not use or modify NASA branding (logos, flags, mission identifiers).

**NASA and partner data:**
- At least one NASA data source or resource is required for global awards.
- Partner-agency data is "highly encouraged".
- Any non-NASA material must be freely available or OSI-open-source licensed ([terms](https://www.spaceappschallenge.org/legal/)).
- Our own original content will be distributed under Apache 2.0 unless we pick another OSI license.
- No material promoting commercial services, and no personal data such as birthdays or addresses.

**Work before the hackathon:**
- The FAQ says: "Teams are not allowed to begin working on the challenges prior to the hackathon."
- Challenge statements were released 28 Oct and resources on 13 Nov, so reading and planning before the event is clearly expected. *Building* the solution is what the rule targets.
- The originality checkbox requires the submission to be the team's original work, with all external code listed.
- **Not found:** a precise definition of "working" (for example, whether preparing infrastructure counts). Our repo currently holds only scaffolding (CLAUDE.md files, contracts, empty folders, no code), which looks like preparation, not a built solution. The interactive blink-lab demo inside the concept plan HTML is pre-event code, though. See the recommendations.

---

## 4. Patterns in past global winners and finalists

These are observations from the pages read. The "why" is my inference, not a judge's statement.

Relevant examples:
- **Resonant Exoplanets** (2025 Best Use of Data, astrophysics) ([project](https://www.spaceappschallenge.org/2025/find-a-team/resonant-exoplanets/?tab=project)):
  - Built an end-to-end pipeline: fetch NASA data, detect, validate with physics, then show results in a web UI with plots and auto-generated reports.
  - The pitch centred on *trustworthiness* and *fewer false positives*.
  - Had a live deployed URL and a YouTube demo.
  - Wrote a long, specific AI-use section: what AI did, what humans checked, "No AI-generated images, logos, or NASA branding".
  - Listed every NASA dataset and every library used.
- **Twisters / SkySense** (2025 Best Use of Technology) ([project](https://www.spaceappschallenge.org/2025/find-a-team/twisters/?tab=project)):
  - One plain user question ("Will it rain on my parade?") turned into an everyday tool, with a polished deployed site and a Figma-designed UI.
  - Its NASA blurb stresses a data pipeline "designed, built, and deployed" quickly.
  - Very candid AI disclosure (which tools, for what).
- **SpaceGenes+** (2025 Best Use of Science, a solo UX designer): NASA credited how "thoughtful design and data visualization can make complex space biology far more accessible" ([meet the winners](https://www.spaceappschallenge.org/2025/meet-the-2025-global-winners/)).
- **WMPGang / SkyShield** (2024 Best Use of Science, NEO orrery challenge): a web app on real-time near-Earth-object data showing risk zones, built "over the course of 30 hours" ([SpaceQ](https://spaceq.ca/two-canadian-teams-selected-as-global-winners-for-the-nasa-space-apps-challenge/), [NASA](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-international-space-apps-challenge-announces-2024-global-winners/)).
- **Asteroid Destroyer** (2024 Global Connection, Habitable Worlds Observatory navigator): data processing plus ML to *map* exoplanets by size, temperature and distance, i.e. an astrophysics visual explorer ([NASA](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-international-space-apps-challenge-announces-2024-global-winners/)).
- **HerCode Space / A Solar Tale** (2025 Best Storytelling) ([project](https://www.spaceappschallenge.org/2025/find-a-team/hercode-space/?tab=project)): a simple, polished narrative site with a clear audience (children) and sources taken straight from NASA/NOAA.

Common threads (inference):
1. **One clear user question and a working, deployed tool.** Every winner I read had a live URL plus a separate demo link.
2. **Real NASA data at the core, named precisely.** Data lists name specific archives and APIs, not "NASA data".
3. **Credibility signals.** The data/science winners emphasise validation and fewer false positives (Resonant), or scientifically grounded risk metrics (WMPGang).
4. **Design matters for non-experts.** Several winners were led or co-led by designers. NASA's own blurbs praise accessibility and visualization.
5. **Story with a human hook** (a child, a parade, a farmer) and a "look into the future" ending, matching the guide's 30-second template.
6. **Transparent AI disclosure** that separates what the team did from what tools did.
7. Winners came from small local events and the Universal Event alike. There's no sign that big venues are favoured.

I found no past winner that was a citizen-science voting tool, so I can't point to direct evidence for that format. The closest are the astrophysics data explorers above.

---

## 5. Event dates and local format

- **Global event: Sat 14 and Sun 15 Nov 2026** ([2026 page](https://www.spaceappschallenge.org/2026/), [FAQ](https://www.spaceappschallenge.org/resources/participant-faqs/), [NASA](https://science.nasa.gov/universe/nasa-welcomes-all-ages-experience-levels-for-space-apps-challenge/)). Submission runs 14 Nov 9:00 to 15 Nov 23:59, local time.
- **Local events can differ:**
  - The terms say events start 14 Nov, or another day in the same week if the Global Organizing team approves it in writing.
  - Local events can be in person or virtual.
  - The submission guide says local judging and awards times may differ from the global submission times, so check with the Local Lead.
- **Example (Cluj-Napoca, Romania; in person at ClujHub)** ([schedule](https://www.spaceappschallenge.org/2026/local-events/cluj-napoca/?tab=schedule)):
  - Sat: 9:00 check-in; 11:00 hacking starts; 19:00 status check.
  - Sun: 10:00 last sprint; **15:00 submissions; 15:30 live pitching**; 18:00 jury deliberation; 19:00 awards.
  - So the effective local deadline is about **9 hours earlier** than the global one, and there is a **live pitch**, a separate format from the 30-second online demo.
- **Bucharest** (in person at Romanian-American University, 100 spots, full with a waitlist): its schedule tab was still a placeholder ("Add your local schedule here ..."). The page says teams present to a jury of experts at the end ([page](https://www.spaceappschallenge.org/2026/local-events/bucharest/)).
- **Unknown:** which local event our team is registered at, the live pitch length, and whether local judges have to watch the online demo. Ask the Local Lead this morning.

---

## What this means for SkyShift

Ordered by priority.

1. **Respect the hard limits.**
   - The demo is **≤30 s video or ≤7 slides**, or the project is out of global judging. The plan says "demo video and slides" without a length, so set it now.
   - Spoken language other than English needs English subtitles. No minors on camera.
   - Make the local live pitch a separate item.

2. **Plan for the local deadline, not 23:59.** If the team is at an event like Cluj (submit 15:00, pitch 15:30 Sunday), move the Day 2 feature freeze to about 11:00 and the submission to about 14:00. Confirm the times and pitch length with the Local Lead before the opening.

3. **Treat the project page as the main deliverable.** Most of the score comes from it.
   - Write the Summary and Details to mirror the challenge text: objective, the five test objects, each consideration, then the five criteria.
   - **Embed an animated GIF of Barnard's Star blinking** (GIFs up to 10 MB are allowed), plus screenshots of the spectrum and the suspect list.

4. **Make RA/Dec entry the hero.**
   - Landing page: a coordinate box that accepts the challenge's own format (`08h55m10.831s -7d14m42.53s`) and decimal degrees, plus five one-click test-object chips.
   - Apply proper-motion correction so Barnard's Star is centred.
   - Add a time-to-first-frame target and progressive loading for arbitrary positions.

5. **Prove it works with numbers (Validity, Best Use of Science).** Add a "How we know it works" panel and paragraph:
   - all five test objects recovered;
   - measured motion of Barnard's Star and WISE 0855 vs Gaia/literature proper motion;
   - "re-found N known asteroids" (SkyBoT, IRSA MOST);
   - recovery rate of known brown dwarfs from the SPHEREx ultracool-dwarf library (Gagné et al. 2026, mocadb.ca);
   - false alarms per square degree before and after votes.

6. **Keep it alive until 26 Jan 2027.**
   - Judging runs through December and January. The €264 credit ends 17 Jan 2027, and IRSA may be slow on the day a judge clicks.
   - Precompute and serve the five targets, the demo patch and the suspect list as **static files on Firebase Hosting**.
   - Run Cloud Run with min instances 0 and budget alerts. Degrade gracefully if IRSA fails.
   - Request a FortiGuard classification for the site.

7. **Rename the public URL.** `nasa-glazing.web.app` puts "nasa" in a non-NASA domain, close to the branding and impersonation lines in the rules, and "glazing" is slang that reads oddly to judges. Create a new Firebase Hosting site such as `skyshift-<something>.web.app` and use it everywhere.

8. **Clean pre-hackathon and AI handling.**
   - Write all product code after 9:00 on 14 Nov, in a repo whose history starts at kickoff.
   - Don't paste the plan page's simulated blink-lab JavaScript into the product; rewrite it, or disclose it.
   - In the description, state plainly what existed beforehand: research notes, cloud account setup, an empty scaffold.
   - Describe Claude Code use in the SI field and in a README "Use of SI" section. Commit `Co-Authored-By` trailers count as metadata.
   - Watermark any SI-generated image or video, and keep NASA logos out of generated media.
   - Be ready to explain that the concept (WiseView-style blink + spectra + crowd vetting) is the team's own. The rule says SI must not come up with the idea.

9. **Spectra that teach.** Put molecular band markers (water ice, CH₄, CO₂, CO) on the on-the-fly spectrum, plus an optional brown-dwarf template from the SPHEREx spectral library. This ties directly to the challenge background and the "extra kudos" line, and it is the most SPHEREx-specific thing we can show. Feature it in the 30-second video.

10. **Close the citizen-science loop (Impact, Global Connection).**
    - Show consensus per suspect ("7 of 9 say real").
    - Offer a downloadable candidate list (CSV with RA/Dec, motion, votes) and a "send to experts" link, for example the Backyard Worlds forum.
    - Store votes anonymously, with no personal data.

11. **Add an embed mode.** `?ra=&dec=&fov=&embed=1` with minimal UI and a "copy embed code" button. The challenge names embedding as the purpose of URL coordinates, and it costs about an hour.

12. **Show the update story in the UI.** Add a badge like "Data: SPHEREx DR1 + QR3, archive checked <date>", and one line in the description explaining that new releases appear automatically.

13. **Claim partner data properly.** List **Gaia DR3 as ESA data** in the partner field (ESA is a 2026 partner). Mention CSA CADC/NEOSSat only if we actually use them. List every library and HiPS survey (Aladin Lite, CDS HiPS, astropy, sep, DuckDB) in References, and pick an OSI license (MIT or Apache-2.0).

14. **Tell one story.** The challenge hands it to us: Pluto was found by blinking two plates → volunteers found most known brown dwarfs → SPHEREx now re-images the whole sky every six months in 102 colours → anyone can blink it. End with "help find the next Planet X", kept honest about what 6″ pixels can find. Follow the guide's 0/2/5/10/20–30 s structure, and put the team name, members and challenge on the title card.

15. **Scope discipline (Relevance comes first).** The core blink-at-any-coordinates tool must be flawless before the 5% deep zone gets more time. Phase C (whole sky) adds little to any criterion. Present it as a cost projection rather than spending Day 2 on it.

### Sources
- Challenge: https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/ (and `?tab=resources`)
- Challenge list: https://www.spaceappschallenge.org/2026/challenges/
- Judging & Awards Guide: https://www.spaceappschallenge.org/resources/judging-awards-guide/
- Project Submission Guide: https://www.spaceappschallenge.org/resources/project-submission-guide/
- Participant FAQs: https://www.spaceappschallenge.org/resources/participant-faqs/
- Terms & Conditions: https://www.spaceappschallenge.org/legal/
- 2026 hackathon page: https://www.spaceappschallenge.org/2026/
- NASA announcement: https://science.nasa.gov/universe/nasa-welcomes-all-ages-experience-levels-for-space-apps-challenge/
- Local events: https://www.spaceappschallenge.org/2026/local-events/cluj-napoca/?tab=schedule · https://www.spaceappschallenge.org/2026/local-events/bucharest/
- 2025 winners/finalists: https://www.spaceappschallenge.org/2025/awards/global-winners/ · https://www.spaceappschallenge.org/2025/awards/global-finalists/ · https://www.spaceappschallenge.org/2025/meet-the-2025-global-winners/
- 2024 winners: https://www.spaceappschallenge.org/nasa-space-apps-2024/awards/global-winners/ · https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-international-space-apps-challenge-announces-2024-global-winners/ · https://spaceq.ca/two-canadian-teams-selected-as-global-winners-for-the-nasa-space-apps-challenge/
- Prior work: https://arxiv.org/abs/2604.22012v1
- Romanian coverage of the 14 challenges: https://zilesinopti.ro/2026/09/29/nasa-space-apps-challenge-2026/
