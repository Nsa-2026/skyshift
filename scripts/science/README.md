# scripts/science: solar-system science from SPHEREx cutouts (P1)

Asteroid sizes and albedos, and the gas of comet 3I/ATLAS, from small IRSA cutouts only (never
whole photos). Python 3.12, its own uv project, `skyshift-core` through a path source.

```
cd scripts/science
gcloud storage cp gs://nasa-glazing-data/results/most_1_60.csv .cache/   # MOST table, asteroids 1-60
uv run python asteroids.py all    # ~35 min cold (4,000 cutouts, 8 in flight), seconds when cached
uv run python comet.py all        # ~3 min cold (258 cutouts)
uv run pytest                     # offline
```

| file | what it does |
|---|---|
| `cutouts.py` | IRSA cutout service (`<image URL>?center=RA,DEC&size=DEG`), at most 8 requests in flight, cached in `.cache/cutouts/`. A cutout holds all six parts and EPSF alone is 3.9 MB whatever the box, so the response is streamed and cut after PRIMARY, IMAGE, FLAGS, VARIANCE (~65 kB for 23 × 23 px). WCS-WAVE is one calibration file per detector (`spectral_wcs_<det>_v2.2`): identical in DR1 and QR3 files of different weeks (checked for all six detectors), so it is fetched once per detector and put back by `open_cutout`. |
| `phot.py` | Centroid within 2 px of the prediction (`skyshift_core.centroid`), sigma-clipped median background in an annulus, `skyshift_core.aperture_photometry` in µJy. A point is dropped when any mask bit (including OVERFLOW/NONLINEAR = saturation) falls within 2 px. |
| `models.py` | CALSPEC solar spectrum, H-G phase function, reflected light, NEATM, band excess, Haser. |
| `asteroids.py` | Asteroid D, pv, η → `data/asteroid_sizes.json`. |
| `comet.py` | 3I/ATLAS apertures, spectrum, CO2 / H2O / CO / C–H bands → `data/comet_3i_gas.json`. |
| `data/solar_spectrum.csv` | CALSPEC `sun_mod_001` (Kurucz model) rebinned to R ≈ 200, 0.4–5.5 µm, scaled to 1361 W m⁻² at 1 au. |

Aperture: asteroid fluxes use the 2 px radius with the core's 1.033 aperture correction
(`skyshift_core.photometry.aperture_correction(2)`, from NASA's ePSF). The 3 px flux is also
measured; the median 3 px / 2 px ratio per detector is in the results below as a check.

<!-- RESULTS -->
