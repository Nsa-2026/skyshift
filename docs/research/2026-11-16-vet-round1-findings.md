# Vetting round 1: all 112 findings, checked

Every finding of round 1 (16 Nov 2026) with what it really is and, where the engine was wrong, why. Each was checked against the images, our own numbers and the catalogues with the tools in `scripts/vet/` and the `vet-finding` skill. The group comes from the Opus run; the other checkers' verdicts are listed beside it. Written by `scripts/vet/compare_runs.py --doc`; the full check records are in `~/.cache/skyshift-vet/rounds/r1/`.

| Group | Findings |
|---|---:|
| [Good: real and worth a look](#1-good-real-and-worth-a-look) | 11 |
| [Good: real, but already known](#2-good-real-but-already-known) | 10 |
| [Possible: could not be settled](#3-possible-could-not-be-settled) | 1 |
| [Engine mistakes](#4-engine-mistakes) | 78 |
| [Controls (answer known in advance)](#5-controls) | 12 |

## The checkers compared

The same findings went to each checker. "Same side" means both keep, both drop, or both leave it open.

| Checker | Records | Controls right | Same verdict as Opus | Same side | Cost | Tool calls per finding | Time per finding |
|---|---:|---:|---:|---:|---:|---:|---:|
| Opus | 112 | 12/12 | - | - | $9.27 | 3.6 | 113 s |
| Opus agents (round 1) | 112 | 12/12 | 71/100 | 84/100 | - | - | - |
| Sonnet, improved tools | 112 | 12/12 | 86/100 | 96/100 | $2.92 | 2.2 | 105 s |
| Haiku high | 50 | 12/12 | 17/38 | 33/38 | $0.50 | 10.4 | 454 s |

## All findings at a glance

| Finding | Kind | Result | Checkers on the same side |
|---|---|---|---:|
| `ecl17-pm-57599010679400` | fast star | Good: worth a look: real and interesting | 3/3 |
| `ecl00-pm-54017000207000` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl06-pm-66085003525200` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl14-pm-62959008588700` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl15-pm-60055009060100` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl15n1-pm-63516008931800` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl16n1-pm-65072009642100` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl17-pm-56542010405200` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl23-pm-40579013265100` | fast star | Good: worth a look: real and interesting | 2/3 |
| `ecl00-pm-57231000520800` | fast star | Good: worth a look: real, the engine is right, already catalogued | 3/3 |
| `ecl17-sp-57599010679400` | spectral lead | Good: worth a look: real and interesting | 3/3 |
| `ecl22-pm-41174012533300` | fast star | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `hunt-sh-65949006863900` | shape | Good: already known: real, the engine is right, already catalogued | 2/3 |
| `hunt-sh-63975007456300` | shape | Good: already known: real, the engine is right, already catalogued | 2/3 |
| `hunt-sh-64117007464500` | shape | Good: already known: real, the engine is right, already catalogued | 2/3 |
| `hunt-sp-66757007063700` | spectral lead | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `hunt-sp-62962007378500` | spectral lead | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `ecl17-sp-54574010793100` | spectral lead | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `ecl17-sp-55031010721300` | spectral lead | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `ecl10-sp-69653005864600` | spectral lead | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `hunt-sp-67705006730700` | spectral lead | Good: already known: real, the engine is right, already catalogued | 3/3 |
| `hunt-665180071278-87799171` | mover | Possible: could not be settled | 2/3 |
| `ecl11-pm-66005006577800` | fast star | Engine mistake: real object, the engine's claim about it is wrong | 2/3 |
| `ecl34-pm-53109020936200-53104020935700` | fast star | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl34-pm-48614020662900-48621020663600` | fast star | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl34-pm-48750020658200-48749020657300` | fast star | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl34-pm-48818020716800-48826020718100` | fast star | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl05-678570032344-87950182` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-550370107906-87570515` | mover | Engine mistake: instrument artifact | 2/3 |
| `ecl17-539610103459-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-535500104491-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-541010103207-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-539660103342-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-675630029452-87955950` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-670440029122-88230016` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-525470106246-88142670` | mover | Engine mistake: instrument artifact | 3/3 |
| `hunt-681160071553-88057915` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-548570105128-88142474` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl10-667050058181-88014022` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-542230104187-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-541580103434-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-685350027780-87691291` | mover | Engine mistake: instrument artifact | 3/3 |
| `hunt-636850070702-88052639` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-536180104411-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-536830104056-87864489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-638240034342-87934247` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-646930031326-88224830` | mover | Engine mistake: instrument artifact | 2/3 |
| `hunt-684910071362-88051656` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-652430028969-88228251` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-648800029690-87942461` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-659380032698-88230499` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl10-673950058759-88290911` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-658950058856-88015196` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl05-669690032204-87707138` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-666470058276-88014022` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl10-696930055023-88286021` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl17-533350103912-88094874` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-672660031681-88230895` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-537970103803-87864489` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl17-572610101958-88094770` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl10-665400056773-87766604` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `hunt-675100072098-87810126` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-678200059488-88032301` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `hunt-665380068525-88045793` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-693890056815-87764750` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl17-567020105384-88150489` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-656540032607-88228647` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-679040028059-88221999` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-650450060358-88290022` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl05-682600032379-87971396` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl10-668740061668-87775310` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-700490058162-88015096` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl05-647090029533-88219852` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-565350105185-87574335` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-561450107482-87889329` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-522810106506-87884828` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-680090029104-87948321` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl05-676550030886-87715155` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl17-569870106493-87880824` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl05-628890033374-88232555` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-660370059355-87768564` | mover | Engine mistake: instrument artifact | 2/3 |
| `ecl05-644390030749-88222290` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-663940060675-88015196` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl17-554450103995-87562787` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-684790031071-87709190` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-561440107481-87890792` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl05-673800030654-87945197` | mover | Engine mistake: instrument artifact | 3/3 |
| `ecl17-559890105365-88101907` | mover | Engine mistake: fixed star or galaxy | 3/3 |
| `ecl10-692050057552-87780103` | mover | Engine mistake: instrument artifact | 3/3 |
| `hunt-642590070200-87799955` | mover | Engine mistake: instrument artifact | 2/3 |
| `hunt-sh-64104007282900` | shape | Engine mistake: real object, the engine's claim about it is wrong | 2/3 |
| `hunt-sh-68114006783100` | shape | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl10-sp-68427005631600` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 2/3 |
| `ecl05-sp-64463003322300` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl05-sp-64109002907700` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl17-sp-52421010424600` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl17-sp-55846010689100` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 2/3 |
| `ecl17-sp-54884010718300` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl17-sp-56194010527800` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `ecl05-sp-67149003067800` | spectral lead | Engine mistake: real object, the engine's claim about it is wrong | 3/3 |
| `hunt-643920069340-88037583` | mover | Control: real moving body, already known | 3/3 |
| `ecl05-637670029434-88230403` | mover | Control: real moving body, already known | 3/3 |
| `ecl10-684140058214-87769345` | mover | Control: real moving body, already known | 3/3 |
| `ecl17-541780102318-87562790` | mover | Control: real moving body, already known | 3/3 |
| `ecl05-655540033216-87715250` | mover | Control: real moving body, already known | 3/3 |
| `ecl10-676940059608-88029761` | mover | Control: real moving body, already known | 3/3 |
| `ecl17-568540105758-87568756` | mover | Control: real moving body, already known | 3/3 |
| `hunt-657950073689-88053614` | mover | Control: fixed star or galaxy | 3/3 |
| `hunt-632520073634-87809440` | mover | Control: fixed star or galaxy | 3/3 |
| `ecl17-578870107066-87567582` | mover | Control: fixed star or galaxy | 3/3 |
| `ecl05-675190033272-87967584` | mover | Control: fixed star or galaxy | 3/3 |
| `ecl10-649190060692-87978336` | mover | Control: fixed star or galaxy | 3/3 |

## 1. Good: real and worth a look

Real objects where our measurement adds something, or that deserve a human's time.

### `ecl17-pm-57599010679400`
fast star · [open in the viewer](https://skyshift.web.app/?ra=178.97130&dec=5.99889&clock=months&det=4#/explore)

- **What it is:** A cool brown dwarf (probably a late-L type, judging by its spectrum) that moves about 0.5″ a year. Its 2010 WISE position matches that motion. It is not in Gaia, which is expected because these objects are very faint at visible wavelengths. Whether it is already known was not checked.
- **What settled it:** The AllWISE back-track: a W1=13.3, W1-W2=0.43 source lies 1.0″ from where the claimed motion puts the star in 2010.5. Today there is nothing at that spot, and the spectrum shows a brown dwarf.
- **Engine problem:** The candidate labels it 'h2o_vapour'. The water absorption is intrinsic to a cool dwarf, not telluric vapour (as far as the label suggests the latter). (+1 more in the record)
- **Verdicts:** Opus: real_other · Opus agents (round 1): real_other · Sonnet, improved tools: real_other · Haiku high: real_other

### `ecl00-pm-54017000207000`
fast star · [open in the viewer](https://skyshift.web.app/?ra=3.45050&dec=0.02846&clock=months&det=4#/explore)

- **What it is:** A J=12.3 star whose motion of about 0.24″/yr toward the south-southwest is confirmed by its Gaia 2016, AllWISE 2010 and 2MASS ~1999 positions. Gaia DR3 lists it with a position only and no proper motion, so our motion adds information. We did not check whether older proper-motion catalogues already have it.
- **What settled it:** catalog --pm back-track: Gaia 2016 and AllWISE 2010.5 counterparts each sit within 0.2\" of the back-tracked spots, the 2MASS offset (2.8\" at PA 32°) also fits, and the control spot is empty.
- **Engine problem:** The status 'new' and 'no catalogue lists this motion' only mean that Gaia DR3 has no proper motion for this star. It is a J=12.3 star that may well be in older proper-motion catalogues; novelty was not checked.
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_known · **the checkers disagree**

### `ecl06-pm-66085003525200`
fast star · [open in the viewer](https://skyshift.web.app/?ra=62.58199&dec=20.14266&clock=months&det=3#/explore)

- **What it is:** A red star (Gaia BP−RP 2.5, probably an M dwarf) moving about 0.42″ a year. Gaia DR3 gives only a position for it, with no proper motion, and its Gaia 2016 and AllWISE 2010 positions both sit on the back-tracked path, so the motion is confirmed. I could not check whether an older proper-motion catalogue already lists it.
- **What settled it:** The catalog --pm back-track: Gaia 2016.0 counterpart 0.2″ and AllWISE 2010.5 counterpart 0.3″ from the back-tracked spots; the control spot is empty (8.6″).
- **Engine problem:** The engine reports no Gaia match (gaia_match false) because the Gaia DR3 counterpart is 4.3" away and has no proper motion; the counterpart is real: Gaia G=16.57 at the back-tracked 2016 spot.
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_other · **the checkers disagree**

### `ecl14-pm-62959008588700`
fast star · [open in the viewer](https://skyshift.web.app/?ra=148.14822&dec=14.93264&clock=months&det=4#/explore)

- **What it is:** A red star (Gaia G=14.25, BP−RP 2.7, probably an M dwarf) that moves about 0.35″ a year to the south-west. Gaia DR3 gives only its position, with no proper motion. Its positions in Gaia (2016) and AllWISE (2010) both lie on the back-tracked path, so the motion is confirmed. Whether older proper-motion catalogues already list it was not checked.
- **What settled it:** catalog --pm back-track: the Gaia 2016 position (0.3\") and the AllWISE 2010.5 position (0.5\") both fall on the claimed motion path, and the forward control spot is empty.
- **Engine problem:** The engine's gaia_match=false is technically right, but the Gaia source does exist (G=14.25, 3.2" offset). Gaia just has no proper-motion solution for it, so the matching skipped it. The 'no catalogue lists this motion' wording should say that Gaia has only a position for this star. (+1 more in the record)
- **Verdicts:** Opus: real_other · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: real_other · Haiku high: real_known · **the checkers disagree**

### `ecl15-pm-60055009060100`
fast star · [open in the viewer](https://skyshift.web.app/?ra=153.37607&dec=10.09193&clock=months&det=5#/explore)

- **What it is:** A real nearby red star (Gaia G=13.9, BP-RP 1.75) moving 0.46″ a year to the south-southeast. Its position in Gaia 2016 and in AllWISE 2010 both line up with our measured motion, but Gaia DR3 gives no proper motion for it. We confirmed the motion but did not check whether it is new: older proper-motion catalogues could not be searched.
- **What settled it:** The catalog --pm back-track: the Gaia 2016 and AllWISE 2010.5 counterparts both lie within 0.2″ of where the claimed motion puts the star, and the control spot is empty.
- **Engine problem:** The status 'new' and the line 'no catalogue lists this motion' only mean that Gaia DR3 has no PM; older PM catalogues were not checked. The 6.9" AllWISE counterpart was not linked, although it is exactly the expected 2010.5 position.
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_known · **the checkers disagree**

### `ecl15n1-pm-63516008931800`
fast star · [open in the viewer](https://skyshift.web.app/?ra=154.75577&dec=15.86156&clock=months&det=1#/explore)

- **What it is:** A real, fairly bright red star (probably an M dwarf, judging by its colours and spectrum) moving about 0.40″ per year toward the southeast. Gaia DR3 gives a position but no proper motion for it. Its 2010 AllWISE and 2016 Gaia positions both fall on the track traced back from our measured motion. It is bright enough that an older proper-motion catalogue probably lists it, but that was not checked.
- **What settled it:** The catalog back-track: Gaia 2016.0 and AllWISE 2010.5 counterparts lie 0.3\" and 0.5\" from the spots predicted by the claimed motion, and the control spot is empty.
- **Engine problem:** The engine's rule that 'no catalogue lists this motion' is true only for Gaia DR3, which gives just a position for this star. A star at J≈11.8 is very likely in older proper-motion catalogues, which were not checked. (+1 more in the record)
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_other · **the checkers disagree**

### `ecl16n1-pm-65072009642100`
fast star · [open in the viewer](https://skyshift.web.app/?ra=169.41389&dec=18.45388&clock=months&det=2#/explore)

- **What it is:** A real star (G 16.2, red, BP−RP 1.87; possibly a nearby M dwarf, which is my guess) moving about 0.38″ a year toward the south-southwest. Its motion shows up in our data, in Gaia's 2016 position and in AllWISE's 2010 position, but Gaia DR3 gives no proper motion for it. I did not check whether older proper-motion catalogues already list it.
- **What settled it:** The Gaia 2016.0 and AllWISE 2010.5 back-tracked positions each have a counterpart within 0.4", and the forward control spot has none closer than 7.7".
- **Engine problem:** The 'no catalogue lists this motion' claim is true only for Gaia DR3, where the star has a position-only solution. CatWISE and 2MASS were not checked. The gaia_match=false flag is because Gaia has no proper motion for this star, not because Gaia has no source: there is a G=16.2 Gaia source 4" away at the back-tracked spot.
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_known · **the checkers disagree**

### `ecl17-pm-56542010405200`
fast star · [open in the viewer](https://skyshift.web.app/?ra=173.89630&dec=4.23762&clock=months&det=4#/explore)

- **What it is:** A red G≈14 star, probably an M dwarf, moving about 0.34″ per year westward. Gaia DR3 has its position but no proper motion. Two older catalogue epochs confirm the motion. It may already be in older proper-motion catalogues; that was not checked.
- **What settled it:** catalog --pm back-track: Gaia 2016.0 (0.3″) and AllWISE 2010.5 (0.4″) counterparts both sit on the back-tracked path; the control spot is empty.
- **Engine problem:** The status 'new' and gaia_match=false only mean that Gaia DR3 gives this star no proper motion. A Gaia counterpart exists, 3.4" away at epoch 2016.0. Novelty against older proper-motion catalogues was not established.
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_known · **the checkers disagree**

### `ecl23-pm-40579013265100`
fast star · [open in the viewer](https://skyshift.web.app/?ra=239.07270&dec=-22.36810&clock=months&det=4#/explore)

- **What it is:** A red star (Gaia BP−RP 2.6, likely an M dwarf) moving about 0.5″/yr toward the south-west; Gaia DR3 gives it a position but no proper motion, and both its Gaia 2016 and AllWISE 2010 positions lie on the back-tracked path. The motion is confirmed; whether it is new was not checked.
- **What settled it:** catalog --pm back-track: the Gaia 2016 spot (0.5\") and the AllWISE 2010 spot (0.4\") both hold the counterpart, and the forward control spot is empty (5.3\").
- **Engine problem:** The engine reports gaia_match=false, but a Gaia DR3 G=15.58 star is 4.8" away exactly along the motion back-track. The match failed because Gaia gives that star no proper motion, so the 10-year shift was not applied. The source should be cross-matched to that Gaia star.
- **Verdicts:** Opus: real_other · Opus agents (round 1): unsure · Sonnet, improved tools: real_other · Haiku high: real_known · **the checkers disagree**

### `ecl00-pm-57231000520800`
fast star · [open in the viewer](https://skyshift.web.app/?ra=8.71967&dec=5.38600&clock=months&det=5#/explore)

- **What it is:** A nearby, cold T-type brown dwarf moving about 0.7–0.8″ a year. Its SPHEREx spectrum has the T-dwarf signature (a sharp 1.27 µm peak, water and methane troughs, a strong 4.1 µm peak), and AllWISE caught it in 2010 at the position the motion traces back to. From memory, not checked with a tool: this is probably the known T6.5 dwarf 2MASS J00345157+0523050.
- **What settled it:** catalog --pm back-track: the AllWISE source (W1-W2=+2.54) lies 2.2" from the 2010.5 back-tracked spot, and there is nothing at the control spot. Together with the T-dwarf spectrum, this confirms the motion is real.
- **Engine problem:** The engine status says 'new' and gaia_match/catwise_match are false, but an AllWISE source sits at the 2010 back-track spot. The engine does not use AllWISE in the proper-motion match. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_other · Haiku high: real_other

### `ecl17-sp-57599010679400`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=178.97130&dec=5.99889&clock=months&det=4#/explore)

- **What it is:** This is very likely a cool L-type brown dwarf (or a very late M dwarf) near the Sun. Its spectrum shows deep water absorption and it is faint at 1 µm. It also seems to move about 0.45″ per year: it sits 6.9″ west of where WISE saw it in 2010. Whether it is already catalogued was not checked; from memory, objects this bright usually are.
- **What settled it:** The spectrum (water ratios 0.58/0.59, blue end 0.45, deep 2.7 µm water, no AGN-style red rise), together with a steady compact point source in 3 visits and red AllWISE colours W1−W2 = 0.43 with no extended flag.
- **Engine problem:** The engine did not link the AllWISE source 6.9″ away (W1 13.31, same brightness) as the counterpart. That offset is probably proper motion of about 0.45″/yr, which the engine does not report. (+1 more in the record)
- **Verdicts:** Opus: real_other · Opus agents (round 1): real_other · Sonnet, improved tools: real_other · Haiku high: real_other


## 2. Good: real, but already known

The engine is right about these, and they are already in a catalogue or an orbit list.

### `ecl22-pm-41174012533300`
fast star · [open in the viewer](https://skyshift.web.app/?ra=224.32084&dec=-21.37631&clock=months&det=5#/explore)

- **What it is:** A real, nearby, very cool brown dwarf (T type) moving about 2″ per year. It moves together with the bright naked-eye star 4′ away, which shares its proper motion. From memory this is the known T dwarf Gliese 570 D, a companion of Gl 570 at about 5.9 pc.
- **What settled it:** The common proper motion with the G=5.36 Gaia star 239″ away: pm (+1031.5, −1723.6) mas/yr against the claimed (+1028.5, −1735.8). The AllWISE counterpart (W1−W2=2.80) found 0.4″ from the back-tracked 2010.5 spot backs this up.
- **Engine problem:** Labelled 'new' with the note 'no catalogue lists this motion'. In fact the Gaia primary 4′ away (G=5.36) has the same motion, and AllWISE holds the 2010 counterpart on the track. The novelty claim is wrong, though the motion itself is right. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_other · Haiku high: static_source

### `hunt-sh-65949006863900`
shape · [open in the viewer](https://skyshift.web.app/?ra=121.67682&dec=19.91505&clock=months&det=3#/explore)

- **What it is:** Two sources that sit close together and do not move. 2MASS and AllWISE already list both; Gaia lists only the brighter, southern one (G 17.4), so the engine's claim that the catalogues show a single object is wrong.
- **What settled it:** catalog: 2MASS and AllWISE list two sources in a north-south line, matching the engine's PA of 6°. The elongation comes from an already-catalogued pair, not a new one.
- **Engine problem:** The reason 'one_catalogue_source' is wrong: 2MASS (2 sources about 2.6" apart) and AllWISE (2 sources about 4.6" apart) both list the pair. The engine's own 'why' text names the companion as '2MASS K 13.4', which contradicts that reason. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: real_known · Haiku high: static_source · **the checkers disagree**

### `hunt-sh-63975007456300`
shape · [open in the viewer](https://skyshift.web.app/?ra=129.69437&dec=16.62601&clock=months&det=3#/explore)

- **What it is:** An ordinary G=16.9 star with a fainter, fixed neighbour about 10–12″ to the south. That neighbour is already listed in AllWISE as a slightly extended source with no Gaia or 2MASS match, so it is probably a background galaxy rather than a new companion star.
- **What settled it:** The catalog tool shows an AllWISE source 10.2″ south with W1=14.73, extended flag 1. It matches the engine's companion in direction and brightness, so the 'second object' is real but already catalogued.
- **Engine problem:** The engine says the companion is 'not in Gaia, CatWISE or 2MASS' and the catalogues 'list one', but AllWISE lists it 10.2″ south (W1=14.73, extended flag 1). Its catalogue check leaves out AllWISE. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): unsure · Sonnet, improved tools: real_known · Haiku high: static_source · **the checkers disagree**

### `hunt-sh-64117007464500`
shape · [open in the viewer](https://skyshift.web.app/?ra=129.99850&dec=16.86287&clock=months&det=3#/explore)

- **What it is:** A G=17.5 star (Gaia parallax 1.35 mas) with a real, fainter neighbour about 5–6″ to the east. The neighbour is not in Gaia, CatWISE or 2MASS, but AllWISE lists it as a separate source. Because Gaia does not see it, it may be a red object or a small galaxy rather than a star.
- **What settled it:** The AllWISE listing has two sources about 5″ apart east-west (1.6″ W and 3.5″ E), matching the engine's companion at 5.6″, PA 84°.
- **Engine problem:** The 'why' text says the companion is 'not in Gaia, CatWISE or 2MASS'. That is true, but AllWISE does resolve it (W1=13.59, 3.5″ E of the position), so 'one_catalogue_source' is wrong once AllWISE is included. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): unsure · Sonnet, improved tools: real_known · Haiku high: static_source · **the checkers disagree**

### `hunt-sp-66757007063700`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=126.32781&dec=21.26213&clock=months&det=4#/explore)

- **What it is:** A real, already-catalogued nearby brown dwarf: its spectrum has the water and CO bands of a cool L-type dwarf, and it moves about 0.65″/yr, as 2MASS, AllWISE and CatWISE show. From memory, this is probably the known L7.5 dwarf 2MASSW J0825196+211552; the tools did not check the name.
- **What settled it:** The proper-motion back-track lands within about 1″ of the 2MASS (J=15.10, J−K=2.07) and AllWISE (W1−W2=0.52) sources, and the spectrum shows water, CO and a faint blue end. Together these identify an already-catalogued L dwarf.
- **Engine problem:** The engine reported 'no_gaia' and 'class_unsure' without moving catalogue positions to the observation date. The object has catwise_match=true but moves fast (0.65″/yr), and its 2MASS, AllWISE and CatWISE counterparts sit 16.5″, 9.1″ and 5.9″ away, so the engine did not recognise it as an already-catalogued high-proper-motion L dwarf.
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_known · Haiku high: real_other

### `hunt-sp-62962007378500`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=127.27723&dec=14.93772&clock=months&det=4#/explore)

- **What it is:** A nearby (about 27 pc) early-to-mid L dwarf, a star-like brown dwarf or very-low-mass star, already catalogued by Gaia (parallax 37.5 mas, proper motion about 0.25"/yr), 2MASS and WISE. From memory, this is probably 2MASSW J0829066+145622, a known L2 dwarf. A tool could not confirm the name.
- **What settled it:** The catalogue shows a Gaia DR3 counterpart with parallax 37.5 mas (27 pc), BP-RP 3.55 and 2MASS J-K 1.58. Together with the water bands and no methane in the spectrum, this makes it a known nearby L dwarf.
- **Engine problem:** The label brown_dwarf_like is correct, but the engine did not flag that the object is already catalogued in Gaia with a 37.5 mas parallax. It is a known nearby L dwarf, not a new lead. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_known · Haiku high: real_other

### `ecl17-sp-54574010793100`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=179.91052&dec=0.95735&clock=months&det=4#/explore)

- **What it is:** This is a real nearby ultracool dwarf about 33 pc away, either a very late M dwarf or an early L dwarf. Its spectrum shows water bands and CO absorption but no methane. Gaia and AllWISE/2MASS already list it with a parallax. From memory only, it is probably the known L0 dwarf SDSS J115938.5+005726, but no tool here confirmed that name.
- **What settled it:** Catalog: Gaia DR3 lists it 0.3" away with parallax 30.67 mas (33 pc) and G-RP 1.6, and 2MASS/AllWISE give J-K 1.27 and W1-W2 0.33. Together with the water bands and no methane in our spectrum, this makes it a known nearby late-M/early-L dwarf.
- **Engine problem:** The 'brown_dwarf_like' label is only half right: there is no methane, and the colours and absolute J of about 11.5 put it near the M/L boundary, so it may be a very low-mass star rather than a brown dwarf. It is also already catalogued (Gaia parallax 30.67 mas, AllWISE/2MASS), not a new lead.
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_known · Haiku high: real_other

### `ecl17-sp-55031010721300`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=178.77038&dec=1.71970&clock=months&det=2#/explore)

- **What it is:** A nearby galaxy that is already catalogued, with a smooth starlight spectrum and a small 3.3 µm bump from PAH dust emission in star-forming regions. It is not a star with a missing Gaia match.
- **What settled it:** The AllWISE counterpart 0.6" away with extended flag 5 and bright W3 (W1-W2 0.12), plus a spectrum with no water bands and a 1.14±0.02 bump at 3.3 µm: a catalogued star-forming galaxy whose PAH feature is real.
- **Engine problem:** The 'no_gaia' reason is correct but does not matter here: the source is an extended galaxy that AllWISE and 2MASS already list (AllWISE extended flag 5), so it is not new
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_known

### `ecl10-sp-69653005864600`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=108.83340&dec=26.08884&clock=months&det=4#/explore)

- **What it is:** A fairly bright, slightly extended galaxy already listed by AllWISE and 2MASS. Its spectrum looks like starlight with a modest 3.3 µm bump, consistent with dust (PAH) emission in a nearby star-forming galaxy.
- **What settled it:** The AllWISE counterpart 0.2" away has extended flag 5 and W1-W2=0.0, and there are no water bands: a known galaxy with a mild PAH-like excess.
- **Engine problem:** The 'no_gaia' reason is correct for a galaxy but is not new. The source is a catalogued AllWISE extended source (ext flag 5) with a 2MASS match, so it is known, not novel. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_known · Haiku high: static_source

### `hunt-sp-67705006730700`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=121.72464&dec=22.84185&clock=months&det=4#/explore)

- **What it is:** A bright, slightly extended galaxy already catalogued by 2MASS and AllWISE. Its spectrum looks like starlight and it is bright at 12 µm, which hints at star formation. The 3.3 µm PAH "emission" the engine reports rests on single-detection channels and is weakly supported.
- **What settled it:** AllWISE extended flag 5 with the matching 2MASS/CatWISE source (W1-W2≈0, W3 bright) and no Gaia star: a known galaxy
- **Engine problem:** The '6σ' PAH 3.3 µm emission comes from two channels (3.28, 3.36 µm) that each rest on a single detection. Better-sampled neighbouring channels do not support it, and the apparent redshift from the 1.6 µm bump (≈0.05) would put the feature at about 3.45 µm. The label is uncertain at best. (+1 more in the record)
- **Verdicts:** Opus: real_known · Opus agents (round 1): real_other · Sonnet, improved tools: real_known


## 3. Possible: could not be settled

The checks that would decide these could not be run, or pointed both ways.

### `hunt-665180071278-87799171`
mover · [open in the viewer](https://skyshift.web.app/?ra=127.13379&dec=20.86460&clock=hours&det=4#/explore)

- **What it is:** A faint, slightly fuzzy smudge seen only at 2.5–2.7 µm (D4) in four exposures of one 6.5-minute session. It hardly moved, and no catalogue star is there. It could be a slow asteroid, a short-lived transient or a noise/optical artifact, and these data cannot tell which.
- **What settled it:** The motion is not significant (1.9σ) and the source is D4-only with weak D1. With no second session there is nothing to show it is a moving object, yet the stacks and catalogues rule out a fixed source of this brightness, so the case stays open.
- **Engine problem:** Called a mover with grade B although the motion is only 1.9σ (stationary within errors); the info rate (30.3"/h, PA 15.4°) differs from the movers row (27.3"/h, PA 25.4°) (+1 more in the record)
- **Verdicts:** Opus: unsure · Opus agents (round 1): unsure · Sonnet, improved tools: no_source · Haiku high: unsure · **the checkers disagree**


## 4. Engine mistakes

Not what the engine says. Grouped by what the object really is.

**Instrument artifact (42)**

### `ecl05-678570032344-87950182`
mover · [open in the viewer](https://skyshift.web.app/?ra=58.60383&dec=23.09618&clock=hours&det=6#/explore)

- **What it is:** A faint stray-light or ghost streak seen on one detector (D6, 4.4–4.5 µm) during one 4-minute session. It stays put on the sky, and the engine mistook small shifts of its measured centre along the streak for motion.
- **What settled it:** The single D6 exposures show the detections lying on a pair of E–W streaks that stay fixed on the sky across all three exposures and are gone 6.5 h later. Nothing is in D3 at the same moments. The 1 px 'motion' runs along the streak.
- **Engine problem:** The engine reported 87.9″/h motion from centroid shifts of 1 px along an extended, sky-fixed streak; there is no real motion. (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-550370107906-87570515`
mover · [open in the viewer](https://skyshift.web.app/?ra=179.92626&dec=1.72842&clock=hours&det=6#/explore)

- **What it is:** A short-lived diffuse glow seen only in the longest-wavelength detector, probably scattered light from a bright (G=6.3) star 7.5′ away. The two "moving" positions agree within their errors.
- **What settled it:** Stamps/session: a broad (6-7\"), diffuse D6-only feature joined to a diffuse band, with nothing in paired D3 and a G=6.3 star 7.5′ away. Its motion (2.5σ) is not significant.
- **Engine problem:** Called a mover from 2 exposures with motion at only 2.5σ (0.53 px); the position change is consistent with zero (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: unsure · **the checkers disagree**

### `ecl17-539610103459-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=172.43187&dec=-0.06430&clock=hours&det=4#/explore)

- **What it is:** This is a hot pixel on detector D4, at pixel 1361,66. The telescope did not step between the two exposures, so the bad pixel stayed on one spot of sky and looked like a source.
- **What settled it:** The pixel history: D4 (1361,66) has 285 detections in 285 exposures across many sky positions.
- **Engine problem:** The 22.9"/h rate is only positional noise on a hot pixel (1.2σ). The candidate should have been rejected outright, since the engine's own hot_pixel gate fired.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-535500104491-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=174.16685&dec=-0.74985&clock=hours&det=4#/explore)

- **What it is:** A hot pixel on detector D4. The telescope did not step between the two exposures, so the pixel stayed on the same patch of sky and looked like a source there.
- **What settled it:** The pixel history: D4 pixel (269,82) has detections in 223 exposures at many different sky positions.
- **Engine problem:** The engine fit a rate of 19.2"/h from two exposures taken on the same detector pixel with no telescope step. It still emitted the track as a mover candidate, although its own hot_pixel gate fired.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-541010103207-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=172.01282&dec=0.16989&clock=hours&det=4#/explore)

- **What it is:** A hot pixel on detector D4 (pixel 1642,97) that stayed on the same sky spot because the telescope did not step between two exposures; it is not a moving object.
- **What settled it:** The pixel history shows D4 pixel 1642,97 with detections in 177 exposures at many sky positions, so it is a hot pixel.
- **Engine problem:** The engine reported a motion of 20.2"/h at PA 44° from a fixed hot pixel. The position change is 0.9σ, so the real motion is zero. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-539660103342-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=172.23718&dec=-0.05636&clock=hours&det=4#/explore)

- **What it is:** This is a hot pixel on detector D4. The telescope did not step between the two exposures, so the bad pixel stayed on one spot of sky and looked like a source that was there now and gone at other times.
- **What settled it:** The pixel history: D4 pixel (1467,26) has detections in 328 exposures, against about 1 expected by chance.
- **Engine problem:** The engine kept a stationary hot-pixel pair as a mover candidate. Its 31 arcsec/h rate comes from a 0.18 px change that is only 1.1 sigma, which is noise. Its 'hot_pixel' gate fired correctly.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-675630029452-87955950`
mover · [open in the viewer](https://skyshift.web.app/?ra=53.17306&dec=22.60620&clock=hours&det=4#/explore)

- **What it is:** Two hot pixels on detector D4, one in exposure 1 and one in exposure 4, both landed on the same faint fixed star. The engine joined them into a fake mover.
- **What settled it:** pixel: each detection sits exactly on a D4 pixel that has detections in more than 250 exposures (hot pixel)
- **Engine problem:** The engine joined two hot-pixel hits from different exposures into a mover even though its own hot_pixel and bad_column gates fired. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-670440029122-88230016`
mover · [open in the viewer](https://skyshift.web.app/?ra=52.25513&dec=21.74066&clock=hours&det=5#/explore)

- **What it is:** Two D5 hot pixels that happened to fall about 2 arcsec apart on the sky in consecutive exposures. The engine paired them as a "mover", but nothing is actually moving.
- **What settled it:** pixel: both detection pixels are hot, each with more than 130 detections at exactly the same detector pixel across many sky positions.
- **Engine problem:** The engine paired detections from two different hot pixels (D5 1907,558 and 1905,443) as a mover; its own hot_pixel gate fired, but the candidate was still listed. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-525470106246-88142670`
mover · [open in the viewer](https://skyshift.web.app/?ra=177.23516&dec=-2.42033&clock=hours&det=5#/explore)

- **What it is:** Two hot pixels on detector D5, which happened to land on almost the same patch of sky in two exposures 2.1 minutes apart. They are not a moving object.
- **What settled it:** The pixel history shows both D5 detection pixels are hot: 117 and 39 detections over many sessions, against 0.63 expected by chance for each.
- **Engine problem:** The engine paired two different hot pixels, D5 (1907,558) and (1905,443), into a mover at 55.7"/h. Its own hot_pixel gate fired, and the finding was correctly given grade C.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `hunt-681160071553-88057915`
mover · [open in the viewer](https://skyshift.web.app/?ra=130.06843&dec=23.52768&clock=hours&det=4#/explore)

- **What it is:** Two unrelated hot pixels on detector D4, landing about 3″ apart on the sky in two exposures of one session, which the engine joined into a false slow "mover". A faint fixed source probably sits at the same spot.
- **What settled it:** The pixel tool shows both detection pixels firing in more than 150 exposures each (about 0.6 expected by chance): both are hot pixels.
- **Engine problem:** The engine joined two different hot pixels on D4 (1131,1361 and 1121,1017) into one mover. Its hot_pixel gate did fire, and the grade was C. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-548570105128-88142474`
mover · [open in the viewer](https://skyshift.web.app/?ra=175.26823&dec=1.42918&clock=hours&det=5#/explore)

- **What it is:** Two different hot pixels on detector D5. The telescope's step between exposures happened to land them almost on the same patch of sky, so they looked like one object that moved slightly.
- **What settled it:** pixel: both detection pixels are hot. They fire in 117 and 39 exposures across unrelated sky positions.
- **Engine problem:** The engine paired two different known hot pixels (D5 1907,558 and 1905,443) into a mover, because the telescope step of about 115 px made them land near the same sky spot. The hot-pixel gate fired correctly, but the candidate was still listed.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl10-667050058181-88014022`
mover · [open in the viewer](https://skyshift.web.app/?ra=103.99122&dec=21.17622&clock=hours&det=6#/explore)

- **What it is:** This is not a moving object. A hot pixel on detector 6 was wrongly paired with a faint star that stays in one place on detector 3.
- **What settled it:** The pixel history: D6 pixel 506,407 has detections in 128 exposures at different sky positions, so it is a hot pixel.
- **Engine problem:** The engine linked a D6 hot pixel (506,407) with a fixed faint source in D3 and called the pair a mover moving at 68"/h. It flagged hot_pixel but still produced the candidate. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-542230104187-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=173.64908&dec=0.37316&clock=hours&det=4#/explore)

- **What it is:** A hot pixel on detector D4 (pixel 819,577). The telescope did not step between two exposures, so the pixel stayed on one sky spot, 8.5" from a faint fixed star, and looked like a new source there.
- **What settled it:** The pixel history: D4 (819,577) has detections in 343 exposures at many sky positions, and both detections sit on that pixel in a session where the telescope did not step.
- **Engine problem:** The engine kept this as a mover candidate even though its own hot_pixel gate fired. The 22.7"/h rate comes from a 0.13 px shift on one hot pixel (1.3σ) and is meaningless.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-541580103434-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=172.39273&dec=0.26453&clock=hours&det=4#/explore)

- **What it is:** A hot pixel on detector D4 at (1462, 235). The telescope did not step between the two exposures, so the bad pixel stayed on one sky spot and looked like a source there.
- **What settled it:** pixel: D4 (1462,235) has 60 detections in 60 exposures at unrelated sky positions, against 1.0 expected by chance
- **Engine problem:** Listed a hot pixel as a mover candidate even though its own hot_pixel gate fired. The pair sits on the same D4 pixel with no step between exposures, and the claimed 20.4"/h rate is noise (1.0σ).
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-685350027780-87691291`
mover · [open in the viewer](https://skyshift.web.app/?ra=50.77207&dec=24.22583&clock=hours&det=4#/explore)

- **What it is:** Two hot pixels on detector D4, hit in two different exposures of one session, that the engine joined into a fake moving object.
- **What settled it:** pixel: both detector pixels have repeated detections across many sessions and sky positions (26 and 105 exposures, 0.88 expected by chance).
- **Engine problem:** Paired two hot-pixel hits from exposures 1 and 4 of session 10 into a mover at 57"/h, even though the hot_pixel gate fired; it should be rejected outright rather than kept at grade C.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `hunt-636850070702-88052639`
mover · [open in the viewer](https://skyshift.web.app/?ra=122.67370&dec=16.14197&clock=hours&det=4#/explore)

- **What it is:** Two separate hot pixels on detector D4 landed near a faint fixed star in exposures 1 and 4 of one session. The engine linked them into a fake "mover".
- **What settled it:** pixel: both detection pixels fire in more than 100 exposures each, at unrelated sky positions
- **Engine problem:** The engine linked detections on two different hot pixels (D4 147,1267 and 135,924) into a 76"/h mover, even though its own hot_pixel gate fired (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-536180104411-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=174.02947&dec=-0.63638&clock=hours&det=1#/explore)

- **What it is:** A defect on detector D1 (a hot pixel or small glowing patch at pixel 367,123). It showed up twice in one session because the telescope did not step between exposures, so it looked like a source sitting at one spot on the sky.
- **What settled it:** pixel: D1 pixel 367,123 has detections in 11 exposures at different sky positions across all three visits (0.05 expected by chance), and both detections here sit exactly on it in a session where the telescope did not step.
- **Engine problem:** It lists the object as a mover at 23.1"/h, but the positions agree to 1.1 sigma on one detector pixel. The engine's own hot_pixel gate fired correctly; the 'rate' is centroid noise.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-536830104056-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=173.43481&dec=-0.52699&clock=hours&det=4#/explore)

- **What it is:** A hot pixel on detector D4 (pixel 713,41). It stayed on the same patch of sky because the telescope did not step between the two exposures, so it looked like a faint source that barely moved.
- **What settled it:** The pixel history shows D4 pixel 713,41 firing in 55 exposures across many sessions at unrelated sky positions, where about 1 would be expected by chance.
- **Engine problem:** The engine reported a rate of 20.5"/h toward PA 41° from two exposures taken on the same hot pixel. The real motion is zero within the errors (0.7σ). Its own hot_pixel gate fired correctly.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-638240034342-87934247`
mover · [open in the viewer](https://skyshift.web.app/?ra=59.65692&dec=16.37428&clock=hours&det=6#/explore)

- **What it is:** This is not a moving object. Two hot pixels in the same column of detector D6 (y=2021 and y=1907) are 114 px apart. The telescope stepped about that far between the two exposures, so both bad pixels landed at almost the same sky spot and looked like a slowly moving dot.
- **What settled it:** The pixel tool shows that D6 (1229,2021) fires in 280 exposures and D6 (1229,1907) in 34, both at many different sky positions: they are hot pixels.
- **Engine problem:** The engine built a mover from two hot pixels in one D6 column (x=1229, y=2021 and y=1907). Their 114 px separation equals the telescope step, so they mapped to almost the same sky spot. The engine's hot_pixel gate fired correctly, but the candidate was still listed (grade C).
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-646930031326-88224830`
mover · [open in the viewer](https://skyshift.web.app/?ra=54.84269&dec=17.82201&clock=hours&det=4#/explore)

- **What it is:** Two hot pixels on detector D4 that, by chance, sat 5 arcseconds apart on the sky in two exposures taken a couple of minutes apart. The engine linked them into a fake moving object.
- **What settled it:** pixel: both detections sit on D4 pixels that fire in 119 and 230 exposures, where chance predicts about 0.9.
- **Engine problem:** The engine linked two different hot pixels (D4 at 1365,359 and at 1362,243) into a mover. Its hot_pixel gate fired correctly, but the candidate was still listed as a mover with a rate of 133"/h.
- **Verdicts:** Opus: artifact · Opus agents (round 1): unsure · Sonnet, improved tools: artifact · Haiku high: artifact · **the checkers disagree**

### `hunt-684910071362-88051656`
mover · [open in the viewer](https://skyshift.web.app/?ra=130.34848&dec=24.15207&clock=hours&det=4#/explore)

- **What it is:** Two separate hot pixels on detector D4, about one telescope step (115 px) apart in y. Because of that spacing they landed on the same sky spot in two exposures in a row, which looked like a slow-moving object.
- **What settled it:** The pixel history: D4 (1152,129) has 131 detections in 131 exposures against 0.58 expected, and (1155,244) has 17. Both detections are hot pixels.
- **Engine problem:** The engine linked two hot-pixel hits into a mover with a rate of 32.8"/h, although the motion is 0.8σ and both positions fall on whole pixels. Its hot_pixel and bad_column gates did fire, so it graded the candidate C.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-652430028969-88228251`
mover · [open in the viewer](https://skyshift.web.app/?ra=50.98559&dec=18.73834&clock=hours&det=4#/explore)

- **What it is:** Two separate hot pixels on detector D4 that happened to land on nearly the same patch of sky in two exposures taken one after the other. The engine joined them into a fake moving object.
- **What settled it:** pixel: both detector pixels have detections in more than 100 exposures (119 and 230) at fixed detector coordinates, on many different parts of the sky
- **Engine problem:** The engine built a mover out of two different hot pixels, D4 (1365,359) and (1362,243). The 115 px telescope step between exposures put both pixels at nearly the same sky spot. Its own hot_pixel gate caught this (grade C), but the pair was still written out as a mover candidate.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-648800029690-87942461`
mover · [open in the viewer](https://skyshift.web.app/?ra=52.07106&dec=18.13488&clock=hours&det=6#/explore)

- **What it is:** This is not a moving object. Two hot (always-bright) pixels on the D6 detector each happened to land near the same patch of sky as the telescope stepped between two exposures, which made it look like one moving dot.
- **What settled it:** The pixel tool: D6 pixel (1229,2021) has detections in 280 exposures and (1229,1907) in 34, against about 1 expected by chance. Both are hot pixels.
- **Engine problem:** The pair links two different hot pixels on the same D6 column, both in session 163. The engine's hot_pixel gate fired correctly, but it still emitted the candidate as a mover. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-659380032698-88230499`
mover · [open in the viewer](https://skyshift.web.app/?ra=57.95763&dec=19.89685&clock=hours&det=4#/explore)

- **What it is:** Two hot pixels on detector D4, one in each of two exposures, which the engine linked into a fake moving object.
- **What settled it:** The pixel tool showed both detector pixels firing in 119 and 230 exposures at unrelated sky positions, so both are hot pixels.
- **Engine problem:** The engine linked two hot-pixel hits (D4 1365,359 and 1362,243) into a mover. Its own hot_pixel gate fired, but the candidate was still listed as a mover.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl10-666470058276-88014022`
mover · [open in the viewer](https://skyshift.web.app/?ra=104.09305&dec=21.07879&clock=hours&det=6#/explore)

- **What it is:** Two D6 hot pixels that happened to fall near a faint fixed star 7″ away. They were paired up as a "mover", but nothing moved.
- **What settled it:** The pixel tool shows both detector positions are hot pixels: 95 and 231 detections in different exposures at fixed detector coordinates.
- **Engine problem:** The engine paired two hot-pixel hits from exposures 1 and 4 into a mover, although the D6 exposures in between show nothing. Its hot_pixel/masked_peak gates did fire (grade C).
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-533350103912-88094874`
mover · [open in the viewer](https://skyshift.web.app/?ra=173.21978&dec=-1.10708&clock=hours&det=2#/explore)

- **What it is:** A straight satellite or space-debris trail that crossed the field during a single exposure. The engine picked up one point on the trail and reported it as a moving object.
- **What settled it:** A8: the packet's row 1 shows a straight trail across the whole stamp in both D2 and D5 in the single exposure, and both detections lie on it.
- **Engine problem:** The engine accepted a point on a satellite trail seen in two paired detectors (D2+D5) at one moment as a grade-B mover. Its rate of 4,795,728"/h is meaningless. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-672660031681-88230895`
mover · [open in the viewer](https://skyshift.web.app/?ra=56.99455&dec=22.11106&clock=hours&det=4#/explore)

- **What it is:** Two separate hot pixels on detector D4 that happened to fall on the same patch of sky in exposures 1 and 3 of one session. The engine paired them as a slow mover, but nothing actually moved.
- **What settled it:** The pixel history shows that both detection pixels are hot: D4 (1631,1508) has dots in 126 exposures and (1625,1278) in 55.
- **Engine problem:** The engine built a 'mover' from two different hot pixels, (1631,1508) and (1625,1278), that are 230 px (two telescope steps) apart in y, so both land on the same sky position. The hot_pixel gate fired, but the candidate was still listed. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-572610101958-88094770`
mover · [open in the viewer](https://skyshift.web.app/?ra=170.69912&dec=5.43646&clock=hours&det=4#/explore)

- **What it is:** Two unrelated hot pixels on detector D4, hit in exposures 1 and 3 of one session. Because the telescope stepped between exposures, they landed about 6″ apart on the sky and the engine read them as one moving object.
- **What settled it:** The pixel tool shows both detections sit on known D4 hot pixels, (786,513) and (792,742), each with about 150 detections over many exposures against about 1 expected by chance.
- **Engine problem:** Formed a mover from two hot-pixel hits in non-adjacent exposures (1 and 3) with nothing in between; the claimed 78″/h rate is meaningless (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-567020105384-88150489`
mover · [open in the viewer](https://skyshift.web.app/?ra=176.18547&dec=4.50441&clock=hours&det=1#/explore)

- **What it is:** This is a piece of a satellite or space-debris trail, not a new object: a straight streak crossed the whole picture in one exposure, and the engine picked out one spot on it.
- **What settled it:** A8: the packet stamps show a straight trail crossing the whole field in both D1 and D4 in the same exposure, with both detections lying on it.
- **Engine problem:** The engine graded B a 'two detectors at one moment' candidate that lies on a visible straight trail crossing the whole stamp. Its streak check did not catch the satellite trail.
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-656540032607-88228647`
mover · [open in the viewer](https://skyshift.web.app/?ra=57.62599&dec=19.42439&clock=hours&det=4#/explore)

- **What it is:** This is not a moving object. It is a known hot pixel on detector D4 that happened to sit near a faint fixed star (Gaia G=17.2, about 9″ away), and the engine paired it with a second dot near that star.
- **What settled it:** The pixel history: detection #1 sits on D4 pixel (1631,1508), which has dots in 126 exposures at many different sky positions (0.88 expected by chance), so it is a hot pixel.
- **Engine problem:** The engine built a 'mover' from a known hot pixel (D4 1631,1508, with 126 dots in this zone) and a dot near the fixed source 9.2″ away. Its own hot_pixel gate did fire (grade C). (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-682600032379-87971396`
mover · [open in the viewer](https://skyshift.web.app/?ra=58.96711&dec=23.76821&clock=hours&det=4#/explore)

- **What it is:** This is not a moving object. One detection sits on a hot pixel of detector D4, and the other is a faint star that is always at that spot, 9″ from a catalogued source.
- **What settled it:** The pixel history shows D4 (402,459) is hot (264 dots in 264 exposures), and the packet shows a source at that sky spot at other times and in D1.
- **Engine problem:** The engine reports a 79″/h mover from two D4 exposures, but detection #1 sits on a known hot pixel (402,459) and the source is seen in D1 and at other times. The hot_pixel gate fired correctly, but the candidate was still listed.
- **Verdicts:** Opus: artifact · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-647090029533-88219852`
mover · [open in the viewer](https://skyshift.web.app/?ra=51.71144&dec=17.84915&clock=hours&det=4#/explore)

- **What it is:** Not a moving object. The first detection sits on a known hot pixel of detector D4, and a faint fixed star (Gaia G=17.9, 6.6″ away) is visible at the same spot at other times; together they made the fake "motion".
- **What settled it:** pixel: D4 pixel (1131,1361) has detections in 251 exposures; together with the +14 to +24σ fixed source at the spot at other times
- **Engine problem:** The pair links a hot pixel (D4 1131,1361) with a faint fixed Gaia star, which gave the claimed 68″/h motion; the hot_pixel gate fired correctly (grade C) (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl17-565350105185-87574335`
mover · [open in the viewer](https://skyshift.web.app/?ra=175.78636&dec=4.22604&clock=hours&det=4#/explore)

- **What it is:** This is not a moving object: it is one bright spot on a straight trail, probably left by a satellite, that crossed both paired detectors in one exposure near the detector corner.
- **What settled it:** The packet's rows 1 and 2 show a straight trail through both the D1 and D4 stamps in the same exposure, with the detections lying on it.
- **Engine problem:** The engine reported a point detection on a linear trail that crosses both paired detectors, and it is partly masked. The streak was not caught, and AUTOMATIC WARNINGS listed none. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact · Haiku high: artifact

### `ecl17-561450107482-87889329`
mover · [open in the viewer](https://skyshift.web.app/?ra=179.48748&dec=3.57665&clock=hours&det=6#/explore)

- **What it is:** This is a smeared glow seen only on detector 6, on the same detector column (x ≈ 1786) in three sessions on three days, probably scattered light from a bright star (G 5.8) 7′ away. It is not a moving object.
- **What settled it:** dots: 18 of 18 D6 detections nearby share detector column x=1786±2 over 3 sessions on different days, so this is a glow fixed to the detector, not a sky object.
- **Engine problem:** The engine graded it B as a mover even though all 4 detections are broad (about 7″), 2 have whole-pixel centroids, and the free dots share D6 column x≈1786 across 3 sessions. The engine noted a 'repeat_position' but did not reject it. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-522810106506-87884828`
mover · [open in the viewer](https://skyshift.web.app/?ra=177.73319&dec=-2.86449&clock=hours&det=2#/explore)

- **What it is:** A faint satellite trail crossing a faint fixed star in a single exposure. The engine read the trail as a new object seen in two detectors.
- **What settled it:** Packet row 1/2: a straight trail across the whole stamp in D2 and D5 in the one exposure. The same spot shows a faint fixed source at +4 to +6σ at other times and in the other visit.
- **Engine problem:** The engine kept a 'two_detectors' candidate that sits on a visible satellite trail crossing the full stamp in both paired detectors; it has no streak test. (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: static_source

### `ecl10-660370059355-87768564`
mover · [open in the viewer](https://skyshift.web.app/?ra=105.31713&dec=20.06212&clock=hours&det=5#/explore)

- **What it is:** A hot pixel on detector D5 that the engine paired with a faint dot in the next exposure. It is not a moving object.
- **What settled it:** pixel: D5 (406,1925) has detections in 52 of 52 exposures across many sky positions, so detection #1 is a hot pixel.
- **Engine problem:** The engine reported a mover even though its own hot_pixel gate fired: detection #1 sits on D5 pixel (406,1925), a hot pixel with 52 dots in 52 exposures (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): unsure · Sonnet, improved tools: artifact · Haiku high: artifact · **the checkers disagree**

### `ecl17-554450103995-87562787`
mover · [open in the viewer](https://skyshift.web.app/?ra=173.47891&dec=2.40857&clock=hours&det=5#/explore)

- **What it is:** A diffuse, elongated glow seen only in the 4.4 µm detector (D5) during one session, most likely scattered light or a ghost from a bright G=5.8 star 7′ away. It is not a moving object.
- **What settled it:** Session images: a broad (6.3–6.8\") linear glow in D5 only, with nothing in the simultaneous D2 exposures, and a G=5.8 star 7′ away.
- **Engine problem:** The engine graded this B and no gate fired, although both detections are broad (more than 6.3") and appear in D5 only, with D2 at +0σ and +3σ at the same moments. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact · Haiku high: artifact

### `ecl05-684790031071-87709190`
mover · [open in the viewer](https://skyshift.web.app/?ra=56.74475&dec=24.13329&clock=hours&det=5#/explore)

- **What it is:** A faint, smeared patch of scattered light seen only in the 3.8 µm detector, 7′ from a G=2.9 star (from memory: Alcyone in the Pleiades, which also has reflection nebulosity). It is not a moving object.
- **What settled it:** The detections are broad diffuse blobs (5.9–6.9″, whole-pixel centroids) seen in D5 only, with nothing in the simultaneous D2 exposures, 7′ from a G=2.9 star.
- **Engine problem:** Grade B was given to a D5-only detection with whole-pixel centroids and sizes of 5.9–6.9″ (BROAD), 7′ from a G=2.9 star; the engine should have gated it. (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl17-561440107481-87890792`
mover · [open in the viewer](https://skyshift.web.app/?ra=179.48528&dec=3.57413&clock=hours&det=6#/explore)

- **What it is:** A faint, diffuse patch of scattered light seen only at 4.8 µm (detector D6) and only during the second visit. It is most likely a ghost or glow from a bright star (G=5.8) 7′ away, and the engine mistook its noisy centroid wobble for motion.
- **What settled it:** dots: unassigned D6 detections at the same sky spot (within about 15\") in three sessions over 2 days, and a +6 sigma diffuse patch in the visit-2 median that is absent in visits 1 and 3. This is a stationary transient glow, not a moving body.
- **Engine problem:** Called a mover although the same diffuse D6 feature appears in sessions 312 and 327 (±1 day) within 17"; the engine's own 'repeat_position' reason should have stopped it (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `ecl05-673800030654-87945197`
mover · [open in the viewer](https://skyshift.web.app/?ra=55.22103&dec=22.30060&clock=hours&det=6#/explore)

- **What it is:** A smeared, stretched glow seen only in the longest-wavelength detector (D6). It does not move between exposures, and the same glow shows up again two days later on the same detector column at the same spot on the sky, so it is an instrument effect sitting next to an ordinary faint star, not an asteroid.
- **What settled it:** The same elongated D6 glow comes back at the same sky spot and the same detector column x≈1530 in session 178, two days later. It does not move within either session and has no new counterpart in D3.
- **Engine problem:** The engine called a non-significant (2.3 sigma) shift of a broad (6") D6-only feature a mover, at grade B (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: static_source

### `ecl10-692050057552-87780103`
mover · [open in the viewer](https://skyshift.web.app/?ra=106.13405&dec=25.34310&clock=hours&det=6#/explore)

- **What it is:** A faint, smeared, east-west glow seen only at 4.6 µm (D6) and only in the first visit. It sits in the same place on the sky for over a week, so it is not a moving object. It is most likely scattered light or a ghost, not a new asteroid.
- **What settled it:** The dots tool shows repeated D6 detections at this sky spot across 5 sessions over 8 days of visit 1, and the visit-1 D6 stack is +6σ. The feature is stationary, so it is not a mover.
- **Engine problem:** The engine counts this as a mover even though it shows no significant motion (0.5σ). Its reported rate of 28.8"/h disagrees with its own rate of 11.1"/h. (+2 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): artifact · Sonnet, improved tools: artifact

### `hunt-642590070200-87799955`
mover · [open in the viewer](https://skyshift.web.app/?ra=122.41220&dec=17.09951&clock=hours&det=6#/explore)

- **What it is:** A faint, smeared patch of light seen only at the longest wavelength (D6), only in one 6-minute session. It does not move measurably, and a very bright star (G=6.3) sits 7.2′ away, so it is most likely scattered light or a ghost from that star, not an asteroid.
- **What settled it:** The detection is broad (6–7\"), D6-only with nothing in the paired D3 exposures, and does not move measurably over 6.4 minutes; a G=6.3 star sits 7.2′ away, so it is scattered light, not a moving source.
- **Engine problem:** Gave grade B to a stationary (1.5σ), broad (6–7"), D6-only detection 7′ from a G=6.3 star (+1 more in the record)
- **Verdicts:** Opus: artifact · Opus agents (round 1): unsure · Sonnet, improved tools: artifact · Haiku high: artifact · **the checkers disagree**

**Fixed star or galaxy (22)**

### `ecl34-pm-53109020936200-53104020935700`
fast star · [open in the viewer](https://skyshift.web.app/?ra=349.05433&dec=-1.48405&clock=months&det=4#/explore)

- **What it is:** Two separate faint fixed infrared sources 39″ apart, both seen in every visit and both in AllWISE. The engine paired them as one star moving an impossible 73″ a year.
- **What settled it:** The stamps show both ends present at about +9 to +14σ in D4 in all three visits, with no change from the first to the last visit, and AllWISE (2010.5) has a source at each end.
- **Engine problem:** The pair method matched two different fixed sources (53109020936200 and 53104020935700), each wrongly given n_visits=1 even though both are detected in all three visits in the D4 stacks; the claimed pm of 73261 mas/yr is false
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source · Haiku high: static_source

### `ecl10-673950058759-88290911`
mover · [open in the viewer](https://skyshift.web.app/?ra=105.86936&dec=22.32572&clock=hours&det=1#/explore)

- **What it is:** Two fixed stars that sit close together (G 14.6 and 14.8), seen as one blurred blob and wrongly read as a slowly moving object. They are not a moving object.
- **What settled it:** B1: about 85-91σ at the same spot in both the other visit and the same visit, with the difference image cancelling to +2/-5σ
- **Engine problem:** The engine called a static blend of two Gaia stars (8.5" and 9.3", on opposite sides) a mover. Its rate of 8.3"/h is noise: 0.5σ. (+1 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-658950058856-88015196`
mover · [open in the viewer](https://skyshift.web.app/?ra=104.27509&dec=19.82627&clock=hours&det=2#/explore)

- **What it is:** Two stars (Gaia G 15.4 each) about 16 arcsec apart blur into one source in SPHEREx images. The source is there in every session and in both visits, so nothing is moving.
- **What settled it:** B1: the source is at the same spot at S/N 52-64 in other sessions and in the other visit, and it lies between two Gaia stars of G 15.4.
- **Engine problem:** The engine called a stationary blend of two Gaia stars a mover: the 0.3 arcsec shift is 0.5 sigma. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-669690032204-87707138`
mover · [open in the viewer](https://skyshift.web.app/?ra=57.73446&dec=21.61519&clock=hours&det=4#/explore)

- **What it is:** This is a fixed blend of two faint stars that sit about 14″ apart, with the spot halfway between them. The engine measured the blend as a separate source and mistook small changes in its measured position for motion.
- **What settled it:** The same spot shows a source at +22 to +30σ in other sessions and in the other visit, and the difference images cancel to -1 and -5σ.
- **Engine problem:** It called a fixed blend between two Gaia stars (6.9″ and 7.0″ away on opposite sides) a mover, because the 6.2″ fixed-source veto radius just missed the blend centroid. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-696930055023-88286021`
mover · [open in the viewer](https://skyshift.web.app/?ra=102.16856&dec=26.15537&clock=hours&det=1#/explore)

- **What it is:** This is a fixed source, not a moving object: a blend of two faint Gaia stars about 8″ apart, which SPHEREx cannot separate. It shows up in the same place at every date.
- **What settled it:** B1: the same spot shows a +16 to +18σ source in the other visit and at other times in the same visit, and the difference image is about -1σ.
- **Engine problem:** Called a mover with 'motion_not_significant' (1.0σ) even though a +16σ source is at the spot at other times: the static rule should have rejected it. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl17-537970103803-87864489`
mover · [open in the viewer](https://skyshift.web.app/?ra=173.00803&dec=-0.33673&clock=hours&det=4#/explore)

- **What it is:** An ordinary fixed star (Gaia G=16.4) seen in two back-to-back exposures with no telescope step. Small scatter in its measured position made it look like it was moving.
- **What settled it:** B1: source at +37 to +46σ at the same spot in both visits, plus a Gaia G=16.4 star and our fixed source 7.6" away
- **Engine problem:** Made a 'mover' from two detections of a known fixed source, 7.6" from our fixed-source position, in a session with no telescope step; the 31"/h rate is position noise (1.7σ) (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-665400056773-87766604`
mover · [open in the viewer](https://skyshift.web.app/?ra=101.28724&dec=20.90105&clock=hours&det=2#/explore)

- **What it is:** Two fixed stars, about 14″ apart, blend into one blob. The blob is seen at the same place in every session, so this is not a moving object.
- **What settled it:** B1: the same spot shows a source at S/N 29–38 in other sessions of the same visit and in the visit six months later.
- **Engine problem:** It did not recognise a blend of two Gaia stars (6.3″ and 8.4″ away, on opposite sides) as a fixed source: the nearest fixed source is 6.5″ away, just outside the 6″ veto radius. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `hunt-675100072098-87810126`
mover · [open in the viewer](https://skyshift.web.app/?ra=130.08122&dec=22.51771&clock=hours&det=3#/explore)

- **What it is:** A fixed star, or a blend of two close fixed sources (one is a G=16.2 Gaia star). It shows up in every visit, and the engine took two detections of it from one session as a moving object.
- **What settled it:** B1: the same spot shows a source at S/N 36-39 in the same visit and in the other visit, and the claimed motion is 0.2σ.
- **Engine problem:** The engine reported a fixed blended source 6.5" from its own fixed source as a mover: the 6" veto radius is too tight for blends. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-678200059488-88032301`
mover · [open in the viewer](https://skyshift.web.app/?ra=107.73767&dec=23.03493&clock=hours&det=4#/explore)

- **What it is:** A faint fixed source, probably the G=16 Gaia star about 7″ away or a blend with it. It is visible at this spot in every visit. The engine measured a 0.26-pixel position jitter and reported it as motion.
- **What settled it:** B1: a source is at the spot at +18 to +23σ in other sessions and in the other visit, and the difference image cancels it (-4σ and -7σ).
- **Engine problem:** It reported a 44.7″/h rate from a 0.26 px shift that is only 1.9σ: this is position noise on a fixed source. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `hunt-665380068525-88045793`
mover · [open in the viewer](https://skyshift.web.app/?ra=122.25047&dec=20.89724&clock=hours&det=2#/explore)

- **What it is:** Two faint stars (Gaia G≈16.7 and 16.9) that sit about 18″ apart and blur together in SPHEREx's large pixels. The engine measured the blend twice and read noise in its position as motion.
- **What settled it:** The B1 and B2 checks: the source shows at +18 to +21σ in both visits, and the difference image keeps only +1σ.
- **Engine problem:** The engine reported a stationary blend of two Gaia stars (7.9″ and 10.3″ away, on opposite sides) as a mover. The fixed-source veto radius is 6″, but the blend centroid is 7.1″ from our fixed source, so the veto missed it. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-693890056815-87764750`
mover · [open in the viewer](https://skyshift.web.app/?ra=105.04254&dec=25.64951&clock=hours&det=1#/explore)

- **What it is:** This is not a moving object. It is a blend of two fixed faint stars, and the engine measured the light between them as a source of its own.
- **What settled it:** B1/B2: the same spot shows +12 to +20σ in other sessions and in the other visit, and the difference image is -1σ, so the source is fixed.
- **Engine problem:** Reported a 'mover' with a rate of 9.3"/h when the motion is only 0.8σ. The source is fixed: a blend of two Gaia stars (G 16.7 and G 17.3) about 8" either side, which our own fixed catalogue also holds (sources 7.7" and 8.0" away). The 6" veto radius missed it. (+1 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-679040028059-88221999`
mover · [open in the viewer](https://skyshift.web.app/?ra=50.86996&dec=23.17474&clock=hours&det=4#/explore)

- **What it is:** A fixed star (Gaia G=15.4, about 8″ away) that the engine measured at slightly different positions in two exposures. Nothing is moving.
- **What settled it:** B1: the same spot shows a +27 to +32σ source in the other visit and in later sessions, matching the fixed source/Gaia star 8″ away.
- **Engine problem:** The engine claimed a mover at 74″/h from a 0.87 px shift between two badly fitted (χ² 17–30) measurements of a fixed G=15.4 star 8″ away, which our catalogue sees in 66 sessions. Its 6″ fixed-source veto missed the star. (+1 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-650450060358-88290022`
mover · [open in the viewer](https://skyshift.web.app/?ra=106.02374&dec=18.40960&clock=hours&det=1#/explore)

- **What it is:** A fixed faint star (Gaia G=16.4, about 6″ away, also in our own fixed-source catalogue). The engine took two measurements of it in different detectors as a moving object.
- **What settled it:** B1: a source of S/N about 20 sits at the same spot in other sessions of both visits, and it matches the Gaia G=16.4 star and our fixed source about 6–7″ away.
- **Engine problem:** The engine linked a fixed star seen in D1 and D4 into a mover with a sub-pixel 'motion' of 0.38 px. The fixed source is 7.0″ away, just outside the 6″ veto radius. (+1 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-668740061668-87775310`
mover · [open in the viewer](https://skyshift.web.app/?ra=110.43493&dec=21.45690&clock=hours&det=4#/explore)

- **What it is:** This is not a moving object. It is a faint fixed source, probably a small galaxy that the engine had already recorded as a fixed source 7″ away; the engine paired two measurements of it from different exposures and different detectors, and called the small shift between them motion.
- **What settled it:** B1/B2: a +9 to +364σ source sits at the spot in other sessions and in the other visit, the difference images cancel to +1 or +2σ, and the shift is only 1.9σ.
- **Engine problem:** It built a mover from one D4 detection and one D1 detection of a fixed source that shows at +14σ in D4 in three of the four exposures; the D1/D4 centroid difference was read as a 25″/h motion. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-700490058162-88015096`
mover · [open in the viewer](https://skyshift.web.app/?ra=108.55439&dec=26.74995&clock=hours&det=3#/explore)

- **What it is:** This is a faint fixed source, a star or galaxy that SkyShift had already catalogued 6.9″ away, not a moving object. The engine measured it slightly off-centre in one session and read the small shift as motion.
- **What settled it:** B1: the same spot shows +3 to +6σ in the other visit and on other days of this visit, and our fixed source of similar flux is 6.9″ away. The candidate also does not move within its errors.
- **Engine problem:** The engine reports a 28.7"/h motion that is not significant (1.1σ); in fact the source is stationary. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-680090029104-87948321`
mover · [open in the viewer](https://skyshift.web.app/?ra=52.83458&dec=23.34882&clock=hours&det=3#/explore)

- **What it is:** A faint star or galaxy that does not move. The engine picked it up in two exposures and wrongly called it a mover. It is probably our catalogued fixed source 8.3″ away, or a blend with the G=19.7 Gaia star 9.6″ away.
- **What settled it:** B1 plus the session table: a source is at the same spot in the other visit (+7σ) and in all 4 exposures of the session, and the difference image keeps only +1 to +2σ.
- **Engine problem:** Reported a 24.2″/h motion that is 0.5σ (not significant) as a mover with grade B. (+3 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source · Haiku high: static_source

### `ecl05-676550030886-87715155`
mover · [open in the viewer](https://skyshift.web.app/?ra=55.82469&dec=22.75943&clock=hours&det=3#/explore)

- **What it is:** A faint fixed source (probably a faint galaxy or star with no Gaia entry) that sits in the same place in every session. The engine split it from its own catalogued fixed source 7.4" away and called it a mover.
- **What settled it:** B1: the same spot shows a source at +4 to +6σ in sessions 26-47 h earlier and 160 days later, and the difference image keeps only +2σ
- **Engine problem:** Called a fixed source a mover: the 0.4" shift is 0.2σ, and the source is at +7σ in all 4 exposures and at +4 to +6σ in other sessions and visits (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl17-569870106493-87880824`
mover · [open in the viewer](https://skyshift.web.app/?ra=178.16211&dec=4.97954&clock=hours&det=2#/explore)

- **What it is:** A faint fixed source, probably a faint galaxy or star with no Gaia entry, that is always at this spot. The engine treated noise in its measured position as motion.
- **What settled it:** B1: the same spot shows +4 to +7σ at other times in both visits, and the difference images keep only +2/+3σ.
- **Engine problem:** The detection is 6.8" from fixed source 56988010649400, just outside the 6" veto radius, so the source was not recognised as fixed. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-628890033374-88232555`
mover · [open in the viewer](https://skyshift.web.app/?ra=57.53734&dec=14.81559&clock=hours&det=2#/explore)

- **What it is:** A faint fixed source (our fixed source seen in 39 sessions, also in AllWISE at W1 15.4) that sits 7″ from the claimed position; the engine counted two noisy detections of it as a slowly moving object.
- **What settled it:** The catalogue shows a fixed source of ours 7.2″ away (seen in 39 sessions, matching AllWISE at 8.3″), and the position did not change over the session (0.8σ). It is the same faint fixed source.
- **Engine problem:** Flagged a stationary source (0.8σ motion) as a mover; the rate of 15.2"/h comes from two whole-pixel peak positions and means nothing (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl05-644390030749-88222290`
mover · [open in the viewer](https://skyshift.web.app/?ra=53.70723&dec=17.39982&clock=hours&det=4#/explore)

- **What it is:** A fixed faint star (the G=16.6 Gaia star and our catalogued fixed source about 9" away), detected twice in one session; it did not actually move.
- **What settled it:** B1: the same spot shows +5 to +10σ in other sessions and the other visit, and the source is present in every exposure of the session (D1 and D4) without measurable motion.
- **Engine problem:** Claimed rate 11.8"/h is noise (1.3σ displacement); object is stationary. (+2 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl10-663940060675-88015196`
mover · [open in the viewer](https://skyshift.web.app/?ra=108.07470&dec=20.65755&clock=hours&det=3#/explore)

- **What it is:** A faint star (or blend of faint stars) that never moved. The engine took small scatter in the measured positions to be motion.
- **What settled it:** B1: in packet row 3 a source sits at the spot in the other visit (+4σ) and in the same visit (+6σ), and the difference image keeps only +1 to +2σ.
- **Engine problem:** The engine reported a motion of 25.6"/h, but it is 0.9 sigma, i.e. not significant; the centroids fell back to whole pixels. (+1 more in the record)
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

### `ecl17-559890105365-88101907`
mover · [open in the viewer](https://skyshift.web.app/?ra=175.90384&dec=3.31625&clock=hours&det=4#/explore)

- **What it is:** A faint fixed source (probably the AllWISE object about 9″ away, or our fixed source 10.7″ away) that the engine measured twice with a slight jitter. Nothing is moving.
- **What settled it:** B1/B2: the same spot shows a source at other times (+4 to +8σ) and the difference image cancels (+3σ, −0σ).
- **Engine problem:** The engine called a 0.62 px shift at 2.2σ from two failed (whole-pixel) centroids a mover at 106.6"/h. The source is fixed: it is seen at the spot in other sessions and visits.
- **Verdicts:** Opus: static_source · Opus agents (round 1): static_source · Sonnet, improved tools: static_source

**Real object, the engine's claim about it is wrong (14)**

### `ecl11-pm-66005006577800`
fast star · [open in the viewer](https://skyshift.web.app/?ra=116.67429&dec=20.00850&clock=months&det=5#/explore)

- **What it is:** A bright, very red nearby ultracool dwarf (late-M/L spectrum) that really does move about 0.37″/yr westward. The engine says 0.69″/yr, about twice the real value, most likely because parallax leaks into a fit from only two visits. From memory, the position and J≈11.8 match the known L-dwarf binary 2MASS J0746+2000AB, so it is probably not new.
- **What settled it:** Gaia 2016.0 and AllWISE 2010.5 offsets agree on ~0.37″/yr toward PA ~262°, about half the engine's 0.69″/yr.
- **Engine problem:** The proper motion is overestimated: the engine gives 695±39 mas/yr at PA 271°. Gaia 2016 and AllWISE 2010.5 offsets both give about 370 mas/yr at PA ~262°, likely because parallax was absorbed into a two-visit fit. (+2 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: real_other · Haiku high: engine_claim_wrong · **the checkers disagree**

### `ecl34-pm-48614020662900-48621020663600`
fast star · [open in the viewer](https://skyshift.web.app/?ra=348.65153&dec=-8.97521&clock=months&det=4#/explore)

- **What it is:** Two different faint objects 40″ apart, one picked up in each visit, were wrongly joined into one "star" moving 77″ a year. That speed is impossible. One of the two matches a faint infrared source in the AllWISE catalogue and does not move.
- **What settled it:** The claimed 77.5″/yr is physically impossible, and end A matches a fixed AllWISE source 0.6″ away, so this is two different faint sources, not one fast star.
- **Engine problem:** The pair method joined two unrelated faint sources, each seen in only one visit, and gave a proper motion of 77.5″/yr. It still kept status 'new', vetted=true, even though it also raised the implausible_pm reason. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): static_source · Sonnet, improved tools: static_source · Haiku high: static_source

### `ecl34-pm-48750020658200-48749020657300`
fast star · [open in the viewer](https://skyshift.web.app/?ra=348.35758&dec=-8.74957&clock=months&det=3#/explore)

- **What it is:** Two ordinary faint fixed sources 47″ apart, each seen in only one visit by the engine, wrongly paired up as one star moving 83″ a year.
- **What settled it:** The stamps show both ends present in all three visits (centre +4/+4/+2σ, other end +5/+7/+8σ), and each end has its own AllWISE counterpart.
- **Engine problem:** Pair method matched two different faint fixed sources (ids 48750020658200 and 48749020657300), each detected in only one visit, as one star; proper motion 82707 mas/yr is impossible (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): static_source · Sonnet, improved tools: static_source · Haiku high: static_source

### `ecl34-pm-48818020716800-48826020718100`
fast star · [open in the viewer](https://skyshift.web.app/?ra=349.24058&dec=-8.63566&clock=months&det=4#/explore)

- **What it is:** This is not a fast-moving star: the engine paired two unrelated spots 58″ apart. One is a faint fixed source that appears in every visit and in AllWISE, and the other cannot be seen in any visit's stacked image.
- **What settled it:** stamps (D4): end b is a +9σ source in all three visits with a 0σ difference image, so the claimed motion is two different sources paired together
- **Engine problem:** The pair method matched two unrelated sources 58″ apart and reported pm 115327 mas/yr. End b is a fixed source in all 3 visits (and in AllWISE), and end a cannot be seen in any visit stack. There is no proper motion here. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: static_source · Haiku high: engine_claim_wrong

### `hunt-sh-64104007282900`
shape · [open in the viewer](https://skyshift.web.app/?ra=126.82206&dec=16.84069&clock=months&det=2#/explore)

- **What it is:** A fixed, red source (probably a galaxy, or a star blended with a galaxy) whose stretched image comes from a fainter catalogued neighbour 5.4″ to the south-east (AllWISE). The engine's claim of an uncatalogued companion 7″ to the north-west is not supported.
- **What settled it:** catalog: the AllWISE neighbour (W1=14.70, 5.4″ at PA ~134°, flux ratio ~0.25) explains the elongation along PA 136°, and nothing is catalogued at the engine's NW companion spot.
- **Engine problem:** reasons includes 'one_catalogue_source', but AllWISE lists a second source (W1=14.70) 5.4″ away at PA ~134°, along the elongation axis. (+2 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): unsure · Sonnet, improved tools: engine_claim_wrong · Haiku high: static_source · **the checkers disagree**

### `hunt-sh-68114006783100`
shape · [open in the viewer](https://skyshift.web.app/?ra=123.29886&dec=23.52474&clock=months&det=4#/explore)

- **What it is:** An ordinary single star (Gaia G=16.5, 2MASS J=13.9) about 57″ from a much brighter G=9.7 star. Its image looks stretched because the bright star's halo leaks into it, not because it has a companion.
- **What settled it:** The catalogue shows a G=9.7 star 57″ away at PA 145°, matching the elongation direction (176°/167°). Gaia, 2MASS and AllWISE list no companion, and the engine's companion is under S/N 3.1 in every detector.
- **Engine problem:** Flagged 'elongated, steady_pa' (27.5σ) on a star 57″ from a G=9.7 star. The elongation direction points toward the bright star, so the shape measurement was biased by its halo. The companion is below S/N 3.1 in every detector.
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong · Haiku high: engine_claim_wrong

### `ecl10-sp-68427005631600`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=102.77997&dec=24.04600&clock=months&det=4#/explore)

- **What it is:** A real, steady, point-like infrared source with a smooth red spectrum, hot-dust colours and a strong emission feature near 4.0 µm. It is not a methane brown dwarf. It is most likely a dusty emission-line object (young star or AGN), and Gaia lists nothing at its position.
- **What settled it:** The spectrum's band ratios: no water bands (1.08, 1.02), no methane trough (0.96), and the blue end is not faint (0.70). Together with a hot-dust red rise and a 4.02 µm emission feature, this rules out a brown dwarf.
- **Engine problem:** The engine claimed 'methane at 3.3 µm (13σ)', but the methane band ratio is 0.96±0.04 with no trough. The apparent dip is a smooth decline in D4 followed by an emission feature at 4.0 µm and a red rise in D5/D6. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): real_other · Sonnet, improved tools: engine_claim_wrong · Haiku high: static_source · **the checkers disagree**

### `ecl05-sp-64463003322300`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=58.04069&dec=17.43916&clock=months&det=4#/explore)

- **What it is:** A real, steady, point-like infrared source with very red mid-infrared colours and no water absorption. It is almost certainly a dust-reddened active galactic nucleus (quasar), not a brown dwarf. The "methane dip" is the low point between starlight and hot dust.
- **What settled it:** The spectrum has no water bands (1.4 µm ratio 1.23, 1.9 µm ratio 1.10) and a smooth red rise. AllWISE gives W1-W2=1.38 and a bright W3, which points to an AGN, not a brown dwarf.
- **Engine problem:** Labelled brown_dwarf_like because of a 3.3 µm 'methane' dip, but the water band ratios are 1.23 and 1.10 (no absorption) and the blue end is 0.57. The dip is the minimum between starlight and hot dust in an AGN-like spectrum (W1-W2=1.38, W2-W3=3.2).
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong · Haiku high: engine_claim_wrong

### `ecl05-sp-64109002907700`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=50.63609&dec=16.84886&clock=months&det=3#/explore)

- **What it is:** This is a real, steady, point-like infrared source that is very red, with no star in Gaia within 20″. Its spectrum is smooth, with no water absorption and a strong rise past 4 µm from hot dust. That fits a dusty active galaxy or quasar seen at high Galactic latitude, not a brown dwarf.
- **What settled it:** The spectrum has no water bands (1.4 and 1.9 µm ratios of 1.13 and 1.05) and a smooth, strongly rising red continuum. AllWISE gives W1-W2=1.77 with a bright W3. Together these mark a dusty AGN-type spectrum, not a brown dwarf.
- **Engine problem:** The 'brown_dwarf_like' label and the 'Methane at 3.3 µm (17σ)' claim are wrong. The 3.3 µm ratio is 0.85, there are no water bands (1.13 and 1.05) and the blue end is 0.67. The dip is the minimum between the starlight bump and hot dust in a red AGN-like spectrum. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong · Haiku high: static_source

### `ecl17-sp-52421010424600`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=173.92717&dec=-2.63148&clock=months&det=4#/explore)

- **What it is:** A real, steady red point source with a smooth red continuum, very red WISE colours and one strong emission line at 4.05 µm. It looks like an active galaxy (AGN), not a brown dwarf. One possibility is redshifted Paschen-alpha at z≈1.16, but that is only a guess.
- **What settled it:** The spectrum's band ratios: no water bands (1.14, 1.01), no methane dip (1.03), blue-end ratio 0.61. Together with AllWISE W1-W2=0.78 and W3=9.0, this is an AGN-like red continuum, not a brown dwarf.
- **Engine problem:** The 'why' says methane at 3.3 µm (13σ), but the measured methane ratio is 1.03±0.03, so there is no dip (+2 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong · Haiku high: engine_claim_wrong

### `ecl17-sp-55846010689100`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=178.40989&dec=3.07761&clock=months&det=3#/explore)

- **What it is:** A steady, compact red source with no Gaia star, best read as a dusty star-forming galaxy (stellar bump near 2 µm, an emission line at 4.02 µm that fits redshifted PAH 3.3 µm at z≈0.22). It is not a brown dwarf: there are no water bands and no methane dip.
- **What settled it:** The spectrum's band ratios: no water bands (1.10, 1.04), no methane dip (0.97), a blue end at 0.62, and an emission line at 4.02 µm. Together with W2-W3≈4.2 this is a dusty galaxy, not a brown dwarf.
- **Engine problem:** Brown_dwarf_like label is wrong: the methane ratio is 0.97±0.03, not a 12σ dip. The water ratios are 1.10 and 1.04 and the blue end is 0.62, so there are no brown-dwarf signatures. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: real_known · Haiku high: static_source · **the checkers disagree**

### `ecl17-sp-54884010718300`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=178.69906&dec=1.47357&clock=months&det=3#/explore)

- **What it is:** A real, steady infrared source that is not a brown dwarf: its spectrum has no water bands and is a smooth red continuum, with very red mid-infrared (WISE) colours and probable emission lines. It is most likely a dusty active galaxy or star-forming galaxy at redshift ≈0.15.
- **What settled it:** The spectrum has no water bands (1.4 and 1.9 µm ratios 1.07) and a bright blue end (0.69), and AllWISE gives W2-W3≈4.4: this is a dusty galaxy or AGN, not a brown dwarf.
- **Engine problem:** Labelled 'brown_dwarf_like' on a 3.3 µm 'methane' ratio of only 0.90. The water ratios of 1.07 and the blue end of 0.69 rule out a brown dwarf, and the engine ignored them. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong

### `ecl17-sp-56194010527800`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=175.82164&dec=3.65674&clock=months&det=4#/explore)

- **What it is:** Almost certainly a galaxy with an active, dust-heated nucleus (AGN), not a brown dwarf. It is red and very bright in mid-infrared dust emission, but its spectrum has none of the water absorption a brown dwarf must show.
- **What settled it:** The spectrum's band ratios: both water bands are absent (ratios 1.21 and 1.09, where a brown dwarf needs below 0.8), the blue end is not faint (0.67), and there is a hot-dust red rise (1.59). With AllWISE W3=9.18, this is an AGN-like spectral shape.
- **Engine problem:** It labelled the source 'brown_dwarf_like' on a 9σ '3.3 µm methane' feature. The measured methane ratio is only 0.89, and both water-band ratios are above 1 (1.21 and 1.09), so the class is wrong. (+1 more in the record)
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong

### `ecl05-sp-67149003067800`
spectral lead · [open in the viewer](https://skyshift.web.app/?ra=55.11353&dec=21.91631&clock=months&det=3#/explore)

- **What it is:** A real, steady point source with a smooth red spectrum, no water bands and very red WISE colours (W1-W2 = 1.12). It is most likely a background quasar/AGN or a dusty red object, not a brown dwarf.
- **What settled it:** The spectrum has no water bands (1.4 µm ratio 1.14, 1.9 µm ratio 1.03), and AllWISE gives W1-W2 = 1.12 on a smooth red continuum: an AGN-like source, not a brown dwarf.
- **Engine problem:** The engine called it 'brown_dwarf_like' because of 'methane at 3.3 µm (8σ)'. The dip ratio is only 0.87 on a smooth red continuum with no water bands (1.14 and 1.03) and a blue-end ratio of 0.83: this is the AGN pattern, not methane.
- **Verdicts:** Opus: engine_claim_wrong · Opus agents (round 1): engine_claim_wrong · Sonnet, improved tools: engine_claim_wrong


## 5. Controls

Findings whose answer we knew in advance (known asteroids and fixed stars), mixed in unmarked to test the checkers.

| Finding | Truth | Opus | Opus agents (round 1) | Sonnet, improved tools | Haiku high |
|---|---|---|---|---|---|
| `hunt-643920069340-88037583` | known asteroid (Parvati) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `ecl05-637670029434-88230403` | known asteroid (Hubble) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `ecl10-684140058214-87769345` | known asteroid (Aavasaksa) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `ecl17-541780102318-87562790` | known asteroid (Kabashima) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `ecl05-655540033216-87715250` | known asteroid (Christy Carol) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `ecl10-676940059608-88029761` | known asteroid (Himalia) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `ecl17-568540105758-87568756` | known asteroid ((18056)) | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known | ✓ real_mover_known |
| `hunt-657950073689-88053614` | gaia star (Gaia star 0.99" away, G=14.9) | ✓ static_source | ✓ static_source | ✓ static_source | ✓ static_source |
| `hunt-632520073634-87809440` | gaia star (Gaia star 0.64" away, G=19.1) | ✓ static_source | ✓ static_source | ✓ static_source | ✓ static_source |
| `ecl17-578870107066-87567582` | gaia star (Gaia star 0.50" away, G=19.4) | ✓ static_source | ✓ static_source | ✓ static_source | ✓ static_source |
| `ecl05-675190033272-87967584` | gaia star (Gaia star 0.49" away, G=17.5) | ✓ static_source | ✓ static_source | ✓ static_source | ✓ static_source |
| `ecl10-649190060692-87978336` | gaia star (Gaia star 0.79" away, G=19.6) | ✓ static_source | ✓ static_source | ✓ static_source | ✓ static_source |
