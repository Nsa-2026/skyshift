"""The static twins in web/public/data/<slug>/ (written by scripts/precompute) keep the contract:
every frames file has the /api/frames shape, every data_url is a file next to it with
2 * nx * ny bytes, ids are unique, `known` points at real epochs, and a folder stays small."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from skyshift_server import stack

DATA = Path(__file__).resolve().parents[2] / "web" / "public" / "data"
SLUGS = sorted(p.name for p in DATA.iterdir() if p.is_dir() and (p / "index.json").exists()) if DATA.exists() else []
MAX_FOLDER_MB = 24.0   # raw bytes in the repo; the `measured` layer adds 2-5 MB per star (gzipped ~7x smaller on the site)
TOP = {"clock", "center", "pixel_arcsec", "size", "epochs", "gaia", "known", "data_release", "archive_checked"}
EPOCH = {"id", "kind", "mjd_mid", "mjd_start", "mjd_end", "det", "um_min", "um_max", "n_exposures",
         "flag_frac", "sat_frac", "data_url", "scale", "zero"}
PIXEL = {("months", 146): 2.05, ("months", 293): 3.075, ("hours", 195): 6.15, ("hours", 293): 12.3}  # contracts/api.md


def _frames_files(slug: str) -> list[Path]:
    return sorted((DATA / slug).glob("frames*.json"))


def test_there_are_static_targets():
    assert "barnard" in SLUGS


@pytest.mark.parametrize("slug", SLUGS)
def test_index_lists_what_is_on_disk(slug):
    index = json.loads((DATA / slug / "index.json").read_text())
    assert index["slug"] == slug and index["name"]
    files = {f["file"] for f in index["frames"]}
    assert files == {p.name for p in _frames_files(slug)} - {"frames.json"}
    assert (DATA / slug / "frames.json").exists() and (DATA / slug / "object.json").exists()
    assert (DATA / slug / "frames.json").read_bytes() == (DATA / slug / index["own"]).read_bytes()
    size = sum(p.stat().st_size for p in (DATA / slug).rglob("*") if p.is_file()) / 1e6
    assert size < MAX_FOLDER_MB, f"{slug} is {size:.1f} MB"


@pytest.mark.parametrize("slug", SLUGS)
def test_frames_files_keep_the_contract(slug):
    for path in _frames_files(slug):
        m = json.loads(path.read_text())
        assert TOP <= set(m), path.name
        nx, ny = m["size"]
        assert nx == ny <= 300
        assert m["pixel_arcsec"] == pytest.approx(PIXEL[(m["clock"], nx)], abs=1e-3), path.name
        ids = [e["id"] for e in m["epochs"]]
        assert len(ids) == len(set(ids)) and ids
        assert [(e["mjd_mid"], e["det"]) for e in m["epochs"]] == sorted((e["mjd_mid"], e["det"]) for e in m["epochs"])
        for e in m["epochs"]:
            assert EPOCH <= set(e), (path.name, e["id"])
            assert e["kind"] == ("visit" if m["clock"] == "months" else "exposure")
            assert 1 <= e["det"] <= 6 and e["n_exposures"] >= 1
            assert e["mjd_start"] <= e["mjd_mid"] <= e["mjd_end"]
            assert not e["data_url"].startswith(("/", "http")) and ".." not in e["data_url"]
            blob = path.parent / e["data_url"]
            assert blob.stat().st_size == 2 * nx * ny, (path.name, e["data_url"])
            assert math.isfinite(e["scale"]) and e["scale"] > 0 and math.isfinite(e["zero"])
        epoch_ids = set(ids)
        for k in m["known"]:
            assert k["epoch_id"] in epoch_ids and k["source"] in ("mpc", "most", "irsa-3i")
        for g in m["gaia"]:
            assert isinstance(g["id"], str)


@pytest.mark.parametrize("slug", SLUGS)
def test_one_frame_decodes_to_a_sky(slug):
    m = json.loads((DATA / slug / "frames.json").read_text())
    e = m["epochs"][len(m["epochs"]) // 2]
    nx, ny = m["size"]
    img = stack.decode((DATA / slug / e["data_url"]).read_bytes(), e["scale"], e["zero"], (ny, nx))
    ok = np.isfinite(img)
    assert ok.mean() > 0.5  # mostly data
    # zodi-subtracted sky in MJy/sr: small, and the frame is not flat
    assert abs(float(np.median(img[ok]))) < 1.0 and stack.robust_sigma(img) > 0


@pytest.mark.parametrize("slug", SLUGS)
def test_object_json_shape(slug):
    o = json.loads((DATA / slug / "object.json").read_text())
    assert set(o) >= {"name", "gaia", "known_ucd", "spectrum", "measure"}
    um = [p["um"] for p in o["spectrum"]]
    assert um == sorted(um)
    for p in o["spectrum"]:
        assert set(p) == {"um", "flux_ujy", "err_ujy", "det", "mjd", "visit", "saturated"}
    src = (o["measure"] or {}).get("source")  # v3.9, optional: files from before it have none
    if src is not None:
        assert set(src) == {"det", "visits", "mjd", "n_exposures", "fov", "pixel_arcsec", "core_filled"}
        assert 1 <= src["det"] <= 6 and src["visits"][0] < src["visits"][1]
        assert o["measure"]["dt_yr"] == pytest.approx((src["mjd"][1] - src["mjd"][0]) / 365.25, abs=1e-4)
        assert {p["visit"] for p in o["spectrum"] if p["det"] == src["det"]} >= set(src["visits"])
