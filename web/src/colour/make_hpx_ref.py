"""Reference values for the web's HEALPix/HPX code: tile index (order 5, nested) and the
0-based pixel position inside that tile's FITS cube, from astropy, using real tile headers."""
import json, sys, urllib.request, warnings
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy_healpix import HEALPix
import astropy.units as u
warnings.simplefilter("ignore")
BASE = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube"
hp = HEALPix(nside=32, order="nested")
rng = np.random.default_rng(7)
pts = [(269.452, 4.693), (330.84, -56.79), (133.79, -7.245), (246.42, 66.75), (166.8, 5.3),
       (12.0, 89.5), (200.0, -88.2), (179.99, 10.0), (0.01, -20.0), (359.99, 30.0), (45.0, 41.81),
       (100.0, 41.85), (300.0, -41.83), (90.0, 0.0), (270.0, 75.0)]
pts += [(float(r), float(np.degrees(np.arcsin(z)))) for r, z in zip(rng.uniform(0, 360, 20), rng.uniform(-1, 1, 20))]
cache = {}
out = []
for ra, dec in pts:
    tile = int(hp.lonlat_to_healpix(ra * u.deg, dec * u.deg))
    if tile not in cache:
        url = f"{BASE}/Norder5/Dir{(tile // 10000) * 10000}/Npix{tile}_cube.fits"
        import subprocess; raw = subprocess.run(["curl", "-s", "--max-time", "60", "-r", "0-14399", url], capture_output=True, check=True).stdout
        h = fits.Header.fromstring(raw[2880:].split(b"END" + b" " * 77)[0].decode() + "END", sep="")
        cache[tile] = h
    h = cache[tile]
    h2 = h.copy()
    for k in list(h2.keys()):
        if k.endswith("3") or k.startswith("PS3") or k in ("CNAME3",): h2.remove(k, ignore_missing=True, remove_all=True)
    h2["NAXIS"] = 2; h2["WCSAXES"] = 2
    w = WCS(h2)
    x, y = w.all_world2pix([[ra, dec]], 0)[0]
    out.append({"ra": ra, "dec": dec, "tile": tile, "x": float(x), "y": float(y)})
headers = {str(t): {k: h[k] for k in ("CRPIX1", "CRPIX2", "PC1_1", "PC1_2", "PC2_1", "PC2_2", "CDELT1", "CDELT2", "CRVAL1", "CRVAL2")} for t, h in cache.items()}
json.dump({"points": out, "headers": headers}, open(sys.argv[1], "w"), indent=1)
print(len(out), "points", len(headers), "tiles")
for p in out[:15]: print(p)
