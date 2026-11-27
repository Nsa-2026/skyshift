"""stack_stamps.py on fake stamps: a faint source moving along its predicted path (S/N ~1.3 per photo) is found in the
comet frame and not in the star frame, at offset places or in pure noise."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "pipeline" / "src"))
spec = importlib.util.spec_from_file_location("stack_stamps", HERE.parent / "stack_stamps.py")
SS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SS)

from skyshift_pipeline import config as C  # noqa: E402
from skyshift_pipeline import stamps as SP  # noqa: E402

PIX = 6.152


def _gauss(nx, ny, x, y, fwhm=1.25):
    s = fwhm / 2.3548
    yy, xx = np.mgrid[0:ny, 0:nx]
    g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s * s))
    return g / (2 * np.pi * s * s)


def fake_stamps(path: Path, n=40, flux=0.0, rate=60.0, sigma=0.02, seed=0, star=0.0, name="P/Fake"):
    """n stamps (one file each) of a source moving `rate` ″/h east-north-east over two days, total flux `flux`
    (MJy/sr × px), per-pixel noise sigma; each photo rolled differently; a static star of flux `star` at the middle
    photo's place."""
    rng = np.random.default_rng(seed)
    ra0, dec0 = 150.0, 10.0
    t0 = 60900.0
    times = t0 + np.sort(rng.uniform(0, 2.0, n))
    tm = times[n // 2]
    pa = np.radians(70.0)
    folder = path / "dots_v2_stamps"
    folder.mkdir(parents=True, exist_ok=True)
    half = 30
    for k, t in enumerate(times):
        hrs = (t - tm) * 24
        ra = ra0 + rate * hrs * np.sin(pa) / 3600 / np.cos(np.radians(dec0))
        dec = dec0 + rate * hrs * np.cos(pa) / 3600
        roll = rng.uniform(0, 2 * np.pi)
        # (ra cos dec, dec) per (x, y) px: east = -x at roll 0
        J = PIX / 3600 * np.array([[-np.cos(roll), np.sin(roll)], [np.sin(roll), np.cos(roll)]])
        xp, yp = 1000.0 + rng.uniform(-0.5, 0.5), 900.0 + rng.uniform(-0.5, 0.5)
        x0, y0 = int(np.rint(xp)) - half, int(np.rint(yp)) - half
        nx = ny = 2 * half + 1
        img = rng.normal(0, sigma, (ny, nx)) + flux * _gauss(nx, ny, xp - x0, yp - y0)
        if star:
            # the star at the middle photo's place (ra0, dec0): its pixel from this photo's Jacobian
            de = (ra0 - ra) * np.cos(np.radians(dec0)), dec0 - dec
            dxy = np.linalg.solve(J, de)
            img += star * _gauss(nx, ny, xp - x0 + dxy[0], yp - y0 + dxy[1])
        var = np.full((ny, nx), sigma ** 2)
        q = sigma / C.STAMP_QUANT
        planes = {"image": img + 0.3, "variance": var, "flags": np.zeros((ny, nx), np.int32),
                  "zodi": np.full((ny, nx), 0.3), "bkg": np.full((ny, nx), 0.3), "resid": img,
                  "bad": np.zeros((ny, nx), bool), "wave": np.full((ny, nx), 2.0), "bandwidth": np.full((ny, nx), 0.03)}
        row = {"kind": "comet", "name": name, "obs_id": f"F_A_{k:04d}_1", "detector": 3, "mjd": float(t),
               "ra_pred": ra, "dec_pred": dec, "x_pred": xp, "y_pred": yp, "sigma_arcsec": np.nan,
               "rate_ra_arcsec_h": rate * np.sin(pa), "rate_dec_arcsec_h": rate * np.cos(pa), "rate_arcsec_h": rate,
               "pa_deg": 70.0, "delta_au": 1.0, "r_au": 1.5, "phase_deg": 30.0, "mag_pred": 20.0,
               "parallax_arcsec": 1.0, "observer": "spacecraft", "half_px": half, "x0": x0, "y0": y0, "nx": nx,
               "ny": ny, "cd1_1": J[0, 0], "cd1_2": J[0, 1], "cd2_1": J[1, 0], "cd2_2": J[1, 1], "q_step": q,
               "n_bad": 0, "list_row": k, **{p: SP.encode(a, SP.PLANES[p], q) for p, a in planes.items()}}
        t_ = SP.table([row], {"version": 1, "obs_id": row["obs_id"], "detector": 3})
        (folder / f"{row['obs_id']}D3.parquet").write_bytes(SP.parquet_bytes(t_))
    return path


def test_moving_source_found_in_comet_frame_only(tmp_path):
    sigma, flux = 0.02, 0.06  # per-photo r = 2 px aperture S/N ~1.3
    root = fake_stamps(tmp_path / "a", flux=flux, sigma=sigma, star=0.5)
    st = SS.load(SS.find_files(root), "P/Fake")
    assert len(st) == 40
    com = SS.stack(st, "comet", "sub")
    assert com["n"] == 40 and com["single_snr_median"] < 2.5
    assert com["mf1"]["snr"] > 5.5 and com["ap2"]["snr"] > 4.5, (com["mf1"], com["ap2"])
    assert abs(com["mf1"]["best_dx_px"]) <= 1 and abs(com["mf1"]["best_dy_px"]) <= 1
    assert max(abs(v) for v in com["mf1"]["ring_snr"]) < 4  # offset places: nothing
    assert com["ap2"]["noise_ratio"] == pytest.approx(1.0, abs=0.35)  # the noise rule agrees with the variance
    star = SS.stack(st, "star", "sub")
    # star frame: the comet's light is smeared away; the static star (at the middle photo's comet place) stacks
    assert star["n"] < 40
    com_flux = com["ap2"]["flux_ujy"]
    assert star["ap2"]["flux_ujy"] > 3 * com_flux  # the star, not the comet
    nostar = SS.stack(SS.load(SS.find_files(fake_stamps(tmp_path / "b", flux=flux, sigma=sigma, seed=3)), "P/Fake"),
                      "star", "sub")
    assert abs(nostar["mf1"]["snr"]) < 4  # the comet alone does not stack in the star frame


def test_pure_noise_and_output_files(tmp_path):
    root = fake_stamps(tmp_path / "n", flux=0.0, seed=7)
    res = SS.run(root, "P/Fake", tmp_path / "out", frame="comet", which="masked")
    r = res["by_detector"][3]
    assert r["n"] == 40 and abs(r["mf1"]["snr"]) < 4 and abs(r["ap2"]["snr"]) < 4
    assert r["mf1"]["frac_over_5"] < 0.01
    assert (tmp_path / "out" / "P_Fake_D3_comet_masked.png").exists()
    assert (tmp_path / "out" / "P_Fake_comet_masked.json").exists()
    assert len(r["profile"]) == 25
