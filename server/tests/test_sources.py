"""Search grouping, Gaia and SkyBoT parsing (recorded samples), the FITS stream cut."""

import io
import json

import numpy as np
import pytest
from astropy.io import fits

from skyshift_server import cutouts, gaia, known, search
from skyshift_server.cache import Cache

from conftest import DATA, sia_row


def test_rows_to_images_dedup_detector_time_and_visits():
    rows = [
        sia_row("A_1", 2, 60900.0), sia_row("A_1", 5, 60900.0),
        sia_row("A_2", 2, 60900.0015),  # same session (2.1 min later)
        sia_row("B_1", 2, 60910.0),  # same visit, new session
        sia_row("C_1", 2, 61090.0),  # 180 days later: visit 2
        sia_row("C_1", 2, 61090.0, collection="spherex_qr3"),  # duplicate: DR1 wins
        sia_row("D_1", 2, 61095.0, collection="spherex_qr3"),
        sia_row("E_1", 2, 61095.0, collection="spherex_dr1_deep"),  # never the deep fields
        sia_row("F_1", 2, 61095.0, dataproduct_subtype="auxiliary"),
    ]
    images = search.rows_to_images(rows)
    assert [im.key for im in images] == ["A_1D2", "A_1D5", "A_2D2", "B_1D2", "C_1D2", "D_1D2"]
    assert [im.visit for im in images] == [1, 1, 1, 1, 2, 2]
    assert [im.session for im in images] == [1, 1, 1, 2, 3, 4]
    assert images[4].collection == "spherex_dr1"
    assert images[0].mjd == pytest.approx(60900.0)  # middle of t_min and t_max
    assert images[0].det == 2 and images[1].det == 5  # from energy_bandpassname
    assert images[0].s3_url == "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/x/A_1D2.fits"


def test_search_is_cached(tmp_path, monkeypatch):
    calls = []

    def fake(ra, dec, collection):
        calls.append(collection)
        return [sia_row("A_1", 2, 60900.0)] if collection == "spherex_dr1" else []

    monkeypatch.setattr(search, "_query_sia", fake)
    c = Cache(str(tmp_path))
    a = search.search(10.0, 20.0, cache=c)
    b = search.search(10.0, 20.0, cache=c)
    assert sorted(calls) == ["spherex_dr1", "spherex_qr3"]  # second call came from the cache
    assert a.images == b.images and len(a.images) == 1
    assert a.archive_checked == search.today()


def test_gaia_parse_recorded_sample(tmp_path, monkeypatch):
    payload = json.loads((DATA / "gaia_tap_barnard.json").read_text())
    stars = gaia.parse_tap_json(payload)
    assert stars[0] == {"id": "4472832130942575872", "ra": 269.44850252543836, "dec": 4.739420051112412,
                        "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976, "g": 8.194}
    assert all(isinstance(s["id"], str) for s in stars)  # ids are strings (2^53)
    assert [s["g"] for s in stars] == sorted(s["g"] for s in stars)

    n = []
    monkeypatch.setattr(gaia, "_tap", lambda adql: n.append(adql) or payload)
    c = Cache(str(tmp_path))
    assert gaia.cone(269.4462, 4.76883, 3.0, cache=c) == stars
    assert gaia.cone(269.4462, 4.76883, 3.0, cache=c) == stars
    assert len(n) == 1 and "gaiadr3.gaia_source" in n[0] and "0.050000" in n[0]


def test_gaia_nulls_stay_null():
    payload = {"metadata": [{"name": k} for k in ("source_id", "ra", "dec", "pmra", "pmdec", "parallax", "phot_g_mean_mag")],
               "data": [[4472832444465199744, 269.44020528335307, 4.726332016196472, None, None, None, 20.813616]]}
    s = gaia.parse_tap_json(payload)[0]
    assert s["pmra"] is None and s["pmdec"] is None and s["parallax"] is None and s["g"] == 20.814


def test_skybot_parse_recorded_sample(tmp_path, monkeypatch):
    text = (DATA / "skybot_3i_atlas.txt").read_text()
    found = known.parse_skybot_text(text)
    assert len(found) == 6
    comet = found[0]
    assert comet["name"] == "3I/ATLAS" and comet["source"] == "skybot" and comet["v"] is None
    assert comet["ra"] == pytest.approx(15 * (16 + 50 / 60 + 41.9539 / 3600), abs=1e-6)
    assert comet["dec"] == pytest.approx(-(17 + 29 / 60 + 32.255 / 3600), abs=1e-6)
    assert found[1] == {"name": "(565183) 2017 BR127", "ra": pytest.approx(252.703060, abs=1e-5),
                        "dec": pytest.approx(-17.455155, abs=1e-5), "v": 22.1, "source": "skybot"}
    assert found[3]["name"] == "2009 UP65"  # unnumbered

    n = []
    monkeypatch.setattr(known, "_skybot", lambda *a: n.append(a) or text)
    c = Cache(str(tmp_path))
    assert known.cone(252.6633, -17.4904, 7.0, 60888.334106, cache=c) == found
    assert known.cone(252.6633, -17.4904, 7.0, 60888.334106, cache=c) == found
    assert len(n) == 1 and n[0][2] == pytest.approx(420.0)


def test_skybot_empty_answer():
    assert known.parse_skybot_text("# Flag: 0\n# Ticket: 1\n# No solar system object was found\n") == []


def _fits_bytes() -> bytes:
    hdul = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(np.arange(600, dtype=np.float32).reshape(20, 30), name="IMAGE"),
        fits.ImageHDU(np.ones((20, 30), dtype=np.int32), name="FLAGS"),
        fits.ImageHDU(np.ones((20, 30), dtype=np.float32), name="VARIANCE"),
        fits.ImageHDU(np.zeros((20, 30), dtype=np.float32), name="ZODI"),
        fits.ImageHDU(np.zeros((300, 300), dtype=np.float32), name="EPSF"),
    ])
    b = io.BytesIO()
    hdul.writeto(b)
    return b.getvalue()


@pytest.mark.parametrize("chunk", [100, 2880, 5000, 1 << 20])
def test_take_until_stops_after_variance(chunk):
    full = _fits_bytes()
    served = []

    def chunks():
        for i in range(0, len(full), chunk):
            served.append(i)
            yield full[i:i + chunk]

    head = cutouts.take_until(chunks())
    assert len(head) % 2880 == 0 and len(head) < len(full) / 10
    with fits.open(io.BytesIO(head)) as h:
        assert [x.name for x in h] == ["PRIMARY", "IMAGE", "FLAGS", "VARIANCE"]
        assert h["IMAGE"].data[19, 29] == 599
    if chunk <= 5000:
        assert served[-1] < len(head) + chunk  # the rest of the stream (EPSF) was never read


def test_take_until_rejects_non_fits_and_short_streams():
    with pytest.raises(ValueError):
        cutouts.take_until([b"Server busy, retry later." + b" " * 2880])
    with pytest.raises(ValueError):
        cutouts.take_until([_fits_bytes()[:5000]])


def test_tier_for_field():
    assert cutouts.tier_for_field(5) == 0.12
    assert cutouts.tier_for_field(15) == 0.36
    assert cutouts.tier_for_field(20) == 0.48
    assert cutouts.tier_for_field(60) == 1.45
