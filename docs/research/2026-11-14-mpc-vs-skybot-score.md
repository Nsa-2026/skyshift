# Pilot score: MPC orbits vs SkyBoT (14 Nov 2026)

Gate G2 was scored on the pilot with SkyBoT. Since PR #30, `known` uses MPC orbits moved with kete from SPHEREx's
own position. This note scores the pilot both ways with the same movers and dots, and says whether G2 holds with MPC.

## Answer
**G2 holds with MPC.** Recall at V < 16 is 83 of 90 object-sessions (92.2%), all 8 objects, control 0%.
On the 68 object-sessions both sources score, the results are identical: 63 found with either source.
MPC's positions sit 0.49″ from our dots, against 1.44″ for SkyBoT (median, V < 16).
The dev plan's switch rule is met: MPC has 100% of SkyBoT's V < 18 objects (56 of 56), and the median offset is
well under 2″. Keep `--known-source mpc` as the default, also for the hunt zone. Don't score the hunt with SkyBoT
as it is: SkyBoT gives some comets V = 0.0 (see Hunt zone below).

## Inputs and method
- Local store root (`--root <dir>`) with copies from `gs://nasa-glazing-data`: photolists/zones/pilot.parquet,
  results/pilot/movers.parquet (link run of 13:06 UTC, 873 movers), cache/skybot/pilot/ and cache/mpc/pilot/
  (108 sessions each; the MPC cache was already complete, computed 13:04 UTC with mpcorb_2026-11-14 + cometels_2026-11-14).
- Dots: `score` needs dots only for photos with a prediction (V < 18, ≥ 10 px from the edge, inside the zone,
  ≥ 2 exposures per session). That's 593 photos for SkyBoT and 629 for MPC, 637 together. The full files are
  1.52 GB, so only their `ra` and `dec` columns were read (the only columns `score` uses; parquet column reads).
  These were **dots v3** files (written 11:44–12:07 UTC, before the v4 ePSF re-extraction; v4 keeps the same rows).
- `uv run skyshift-pipeline score --zone pilot --root <dir> --known-source skybot|mpc`, with each recall.json
  renamed after the run. Offsets were computed with a separate script, using the same matching as score.py.
- **Downloaded: 372 MB.** Pilot 265 MB (dot columns 219 MB, the two caches 46 MB), hunt zone 107 MB (below).

**Reproduction.** The SkyBoT run gives 63 of 68 (92.65%), 7 of 7 objects, control 0%, V 16–17 83.3%,
V 17–18 43.9%. This is the official G2 result exactly, although movers.parquet was rebuilt after it.
The MPC run is identical to the `recall.json` now in the bucket: a teammate's `all --zone pilot` overwrote it at
13:12 UTC with the MPC default. The bucket therefore no longer holds the SkyBoT G2 file.

## Pilot: SkyBoT vs MPC
| | SkyBoT | MPC |
|---|---|---|
| **Recall, V < 16** (object-sessions) | **63 / 68 (92.6%)** | **83 / 90 (92.2%)** |
| Objects, V < 16 | 7 / 7 | 8 / 8 |
| V < 12 (saturated) | 17 / 18 | 17 / 18 |
| V 12–14 | 5 / 5 | 5 / 5 |
| V 14–15 | 21 / 23 | 25 / 28 |
| V 15–16 | 20 / 22 | 36 / 39 |
| V 16–17 | 80 / 96 (83.3%) | 58 / 70 (82.9%) |
| V 17–18 | 54 / 123 (43.9%) | 59 / 149 (39.6%) |
| Dot share, V < 16 | 98.5% | 98.9% |
| Control recall (2′ shift) | 0% | 0% |
| Control dot share, V < 16 | 25.0% | 16.7% |
| Photos with predictions | 593 | 629 |
| Median offset, prediction → dot, V < 16 | 1.44″ | 0.49″ |
| Median offset, prediction → matched mover, V < 16 | 1.43″ | 0.27″ |

Per object, at V < 16 (sessions found / scored; SkyBoT V, MPC V):
Nysa 5/5 both (10.9, 10.87) · Laetitia 12/13 both (11.0, 11.13) · Galatea 5/5 both (13.9, 13.88) ·
Sapientia 15/15 both (14.1, 14.01) · Penelope 2/3 both (14.0, 14.11) · Laodica 14/16 both (14.8, 14.90) ·
Amaryllis 10/11 both (15.9, 15.81) · **Sniadeckia** not scored by SkyBoT (16.0) / 20/22 with MPC (15.88).

## Why the counts differ
- **Brightness rounding, nothing else.** SkyBoT gives V to 0.1 mag; MPC (kete H-G) gives two decimals.
  The median difference V(MPC) − V(SkyBoT) is +0.02 mag (16–84%: −0.05 to +0.16; largest 0.41, over 53 objects).
  The eighth object, Sniadeckia, is V 16.0 in SkyBoT and 15.88–15.99 in MPC, so its 22 sessions enter V < 16
  with MPC (20 found). Five of Laodica's sessions (SkyBoT 15.0, MPC 14.95–14.98) move from the 15–16 bin to 14–15.
- **Units in one source only (V < 18): 25 MPC-only, 3 SkyBoT-only.** All 25 MPC-only units have MPC V 17.89–17.99
  and SkyBoT V 18.0. The 3 SkyBoT-only units have SkyBoT V 17.9 and MPC V 18.01–18.2. All sit on the V 18 cut.
- **Common units: 284, the same mover result in all of them.** The number of photos per unit is also the same in all
  284, so no unit was lost to an edge effect. The dot result differs in 2 (V 17–18): MPC finds a dot within 12″
  and SkyBoT doesn't, because MPC's position is closer.
- **Objects outside the zone box.** MPC lists 62 objects with V < 18 that SkyBoT doesn't have. All of them are
  outside the zone (0 predictions inside it), where SkyBoT's cones, clipped to the zone + 0.05°, don't reach.
  They don't affect score or suspects: every pilot mover is inside the zone. Every SkyBoT V < 18 object is also in
  MPC (56 of 56 objects, 3,836 of 3,836 object-photo rows). At V < 21 it's 98% (1,316 of 1,343). Of the 27 missing,
  24 are at SkyBoT V 20.9, at MPC's V 21 cut, and one is comet 143P under another name. Two unnumbered
  objects at V 20.4 weren't traced.
- **Offsets.** SkyBoT's offset runs along the direction of motion: the dot is 0.97″ ahead of SkyBoT's position
  (median), and 0.37″ to the side. It isn't a clock error: as a time lead, it varies from 35 s to 395 s by object.
  It fits the observer instead. SkyBoT is geocentric, and SPHEREx is about 7,000 km from the Earth's centre,
  moving at 7.5 km/s. The rates differ too: Penelope moves 13.1″/h by SkyBoT and 22.2″/h by MPC from SPHEREx.
  MPC's offset has no along-track part (0.03″) and is 0.4–0.6″ on every detector. Either is far inside the 12″
  match radius, which is why recall doesn't change.

## Hunt zone (extra; mover recall only)
The hunt has a full MPC cache (291 sessions) and 160 SkyBoT sessions. Their dots didn't fit the download cap, so
only mover recall was scored (score.py's own functions, with dots switched off; no dot share). It covers the 99 sessions
both caches hold that share no photo with the pilot (107 MB for both caches), with results/hunt/movers.parquet
(13:05 UTC, 6,143 movers).

| Hunt zone, 99 sessions | SkyBoT | MPC |
|---|---|---|
| Recall, V < 16 | 98 / 126 (77.8%), 11 of 13 objects | 107 / 120 (89.2%), 11 of 11 objects |
| … without the two comets SkyBoT gives V = 0.0 | 98 / 110 (89.1%), 11 / 11 | (not listed: comets have no V in MPC) |
| V < 12 | 0 / 16 (all 16 are those comets) | none |
| V 12–14 / 14–15 / 15–16 | 18/21 · 39/43 · 41/46 | 19/22 · 43/47 · 45/51 |
| V 16–17 / 17–18 | 70/81 (86.4%) · 147/323 (45.5%) | 63/74 (85.1%) · 166/414 (40.1%) |
| Control recall | 0% | 0% |
| Median offset, prediction → same mover, V < 16 (662 rows) | 1.37″ | 0.22″ |

- 513 common units, with the same mover result in all of them. 95 units are MPC-only, all at MPC V 17.80–17.99 (rounding again).
  17 are SkyBoT-only: 16 comet units plus one at V 17.9.
- **SkyBoT bug for comets**: in the hunt cache, SkyBoT gives V = 0.0 to comets without a magnitude (P/2026 B1, 98P,
  278P, 462P, C/2020 H11, C/2026 A3). `score` takes that as a V < 12 asteroid and counts it as missed.
  Two of them, 98P and P/2026 B1, are inside the zone in these sessions, which brings SkyBoT's hunt recall down
  from 89% to 78%. The pilot's comets had real V values (143P 19.9, P/2013 G1 20.9, C/2016 Q4 27.7), so G2 is unaffected.
  MPC comets have no V and stay out of score, as intended. If the SkyBoT path is still needed, treat V = 0 on
  class "Comet" as unknown in `known.parse_skybot_json` (not changed here).

## Reproduce
```
R=<empty dir>
gcloud storage cp gs://nasa-glazing-data/photolists/zones/pilot.parquet $R/photolists/zones/
gcloud storage cp gs://nasa-glazing-data/results/pilot/movers.parquet $R/results/pilot/
gcloud storage cp -r gs://nasa-glazing-data/cache/skybot/pilot $R/cache/skybot/
gcloud storage cp -r gs://nasa-glazing-data/cache/mpc/pilot $R/cache/mpc/
# dots/<obs_id>D<det>.parquet for the photos in score_units(load_predictions(...)) of either source
# (ra, dec columns are enough)
cd pipeline
uv run skyshift-pipeline score --zone pilot --root $R --known-source skybot   # then rename recall.json
uv run skyshift-pipeline score --zone pilot --root $R --known-source mpc
```
