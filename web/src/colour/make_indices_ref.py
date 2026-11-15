"""Reference values for the web's spectral indices (indices.ts), from the pipeline's own
indices.py on its 102 channels. Run from pipeline/:  .venv/bin/python ../web/src/colour/make_indices_ref.py
Writes web/src/colour/indices_ref.json: the channel grid, test spectra and every index of each."""
import json, pathlib
import numpy as np
from skyshift_pipeline import config as C
from skyshift_pipeline import indices as I

rng = np.random.default_rng(11)
lam = I.CENTRE
spectra = []

def add(name, flux, err):
    spectra.append((name, np.asarray(flux, float), np.asarray(err, float)))

flat = np.full(102, 100.0)
add("flat", flat, np.full(102, 1.0))
add("power law -1.5", 100 * (lam / 2.0) ** -1.5, np.full(102, 0.5))
add("power law +2", 20 * (lam / 2.0) ** 2.0, np.full(102, 0.3))
t = 100 * (lam / 2.0) ** -0.5
for a, b, d in ((3.20, 3.45, 0.7), (1.63, 1.72, 0.3), (1.34, 1.45, 0.6), (1.84, 1.95, 0.5)):
    t = np.where((lam >= a) & (lam <= b), t * (1 - d), t)
add("T dwarf-like: methane and water dips", t, 0.02 * np.abs(t) + 0.2)
p = 50 * (lam / 3.0) ** 1.0
p = np.where((lam >= 3.24) & (lam <= 3.34), p * 1.8, p)
add("PAH bump", p, np.full(102, 1.0))
ice = 80 * (lam / 3.0) ** 0.5
for a, b, d in ((2.95, 3.15, 0.4), (4.23, 4.31, 0.5), (4.64, 4.70, 0.3)):
    ice = np.where((lam >= a) & (lam <= b), ice * (1 - d), ice)
add("ices", ice, 0.03 * ice)
noisy = 5 + rng.normal(0, 2.0, 102)
add("faint and noisy (linear continuum fallback)", noisy, np.full(102, 2.0))
neg = 3 * (lam / 2.0) ** -1 + rng.normal(0, 3.0, 102)
add("continuum near zero", neg, np.full(102, 3.0))
holes = (100 * (lam / 2.0) ** -1.0 + rng.normal(0, 1.0, 102)).copy()
eh = np.full(102, 1.0)
for k in (50, 51, 52, 31, 62, 80, 92):
    holes[k] = np.nan
eh[33] = np.nan
eh[44] = 0.0
add("missing channels", holes, eh)
add("random", 40 + rng.normal(0, 6.0, 102), rng.uniform(0.5, 3.0, 102))

flux = np.stack([s[1] for s in spectra])
err = np.stack([s[2] for s in spectra])
out_idx = I.all_indices(flux, err)
clean = lambda a: [None if not np.isfinite(v) else float(v) for v in np.asarray(a, float)]
out = {
    "channels": {"lo": I.LO.tolist(), "hi": I.HI.tolist(), "centre": I.CENTRE.tolist()},
    "windows": {k: [v[0], list(v[1]), [list(w) for w in v[2]]] for k, v in C.INDEX_WINDOWS.items()},
    "min_cover": C.INDEX_MIN_COVER,
    "log_cont_min_snr": C.INDEX_LOG_CONT_MIN_SNR,
    "label_min_depth": C.LABEL_MIN_DEPTH,
    "index_sig": C.INDEX_SIG,
    "label_support_sig": C.LABEL_SUPPORT_SIG,
    "spectra": [
        {"name": n, "flux": clean(f), "err": clean(e),
         "indices": {k: (None if not np.isfinite(v[i]) else float(v[i])) for k, v in out_idx.items()}}
        for i, (n, f, e) in enumerate(spectra)
    ],
}
path = pathlib.Path(__file__).with_name("indices_ref.json")
path.write_text(json.dumps(out))
print("wrote", path, len(spectra), "spectra")
