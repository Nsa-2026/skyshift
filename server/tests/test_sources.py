"""Search grouping, Gaia parsing (recorded samples), known objects from the MPC index, the FITS stream cut."""

import io
import json

import numpy as np
import pytest
from astropy.io import fits

from skyshift_core import known_index

from skyshift_server import cutouts, gaia, known, search
from skyshift_server.cache import Cache
from skyshift_server.errors import ApiError, UpstreamError

from conftest import DATA, sia_row, tiny_index_npz


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


def test_mpc_index_is_read_once_from_the_cache(tmp_path, monkeypatch):
    c = Cache(str(tmp_path))
    known.reset_index()
    with pytest.raises(UpstreamError):  # not in the cache yet
        known.mpc_index(c)
    c.put(known.INDEX_KEY, tiny_index_npz())
    gets = []
    real_get = c.get
    monkeypatch.setattr(c, "get", lambda key: gets.append(key) or real_get(key))
    idx = known.mpc_index(c)
    assert known.mpc_index(c) is idx and gets == [known.INDEX_KEY]
    assert len(idx) == 3 and idx.name(2) == "C/2025 A1" and idx.meta["snapshot"] == "synthetic"
    known.reset_index()


def test_known_cone_from_the_mpc_index(tmp_path, monkeypatch):
    """The coarse cut runs for real on a tiny index; kete (the exact step) is replaced."""
    c = Cache(str(tmp_path))
    c.put(known.INDEX_KEY, tiny_index_npz())
    known.reset_index()
    seen = {}

    def fake_exact(kete, idx, rows, ra, dec, radius_deg, mjds, observers=None):
        seen.update(rows=[int(r) for r in rows], radius=radius_deg, mjds=list(mjds))
        return [{"k": 0, "row": 2, "ra": ra + 0.01, "dec": dec, "v": None},     # the comet: no V, kept
                {"k": 0, "row": 1, "ra": ra, "dec": dec + 0.001, "v": 15.2},     # nearest first
                {"k": 0, "row": 0, "ra": ra, "dec": dec, "v": 22.5}]             # fainter than V 21: dropped

    monkeypatch.setattr(known_index, "_kete", lambda: object())
    monkeypatch.setattr(known_index, "exact", fake_exact)
    got = known.cone(40.0, 10.0, 6.0, 61000.5, cache=c)
    assert 2 in seen["rows"]  # comets always go to kete
    assert seen["radius"] == pytest.approx(0.1) and seen["mjds"] == [61000.5]
    assert got == [{"name": "(12345)", "epoch_id": None, "ra": 40.0, "dec": 10.001, "v": 15.2, "source": "mpc"},
                   {"name": "C/2025 A1", "epoch_id": None, "ra": 40.01, "dec": 10.0, "v": None, "source": "mpc"}]
    # several times at once carry their epoch ids
    two = known.near(40.0, 10.0, 6.0, [61000.5, 61001.5], epoch_ids=["a", "b"], cache=c)
    assert {k["epoch_id"] for k in two} == {"a"} and seen["mjds"] == [61000.5, 61001.5]
    # outside the index's range: a bad request for /api/known, a ValueError for frames
    with pytest.raises(ApiError) as e:
        known.cone(40.0, 10.0, 6.0, 50000.0, cache=c)
    assert e.value.status == 400
    with pytest.raises(ValueError):
        known.near(40.0, 10.0, 6.0, [50000.0], epoch_ids=["x"], cache=c)
    known.reset_index()


def test_known_position_of_one_object_by_its_mpc_name(tmp_path, monkeypatch):
    """GET /api/resolve's moving targets: one object's place from its MPC orbit with kete (replaced
    here: the real kete needs its kernels), at the time asked, by the index's exact name."""
    from types import SimpleNamespace

    c = Cache(str(tmp_path))
    c.put(known.INDEX_KEY, tiny_index_npz())
    known.reset_index()
    idx = known.mpc_index(c)
    assert [known.row_of(idx, n) for n in ("Ceres", "(12345)", "C/2025 A1", "Cere", "eres", "C/2025")] == \
        [0, 1, 2, None, None, None]
    seen = {}

    class FakeKete:
        @staticmethod
        def propagate_n_body(states, jd, suppress_errors=False):
            seen.update(rows=states, jd=jd)
            return [SimpleNamespace(is_finite=True)]

        @staticmethod
        def OmniDirectionalFOV(obs):  # noqa: N802 - kete's name
            return ("omni", obs)

        @staticmethod
        def fov_state_check(states, fovs):
            seen["fov"] = fovs[0]
            return [SimpleNamespace(states=states, ra_dec_with_rates=[[361.5, -12.25, 0.1, 0.2]])]

    monkeypatch.setattr(known_index, "_kete", lambda: FakeKete)
    monkeypatch.setattr(known_index, "observer_state", lambda kete, mjd, sc=None: SimpleNamespace(jd=mjd + 2400000.5))
    monkeypatch.setattr(known_index, "kete_states", lambda kete, idx, rows: [int(r) for r in rows])
    assert known.position("(12345)", 61000.25, cache=c) == (1.5, -12.25)
    assert seen["rows"] == [1] and seen["jd"] == 61000.25 + 2400000.5 and seen["fov"][0] == "omni"
    with pytest.raises(KeyError):
        known.position("Vesta", 61000.25, cache=c)
    with pytest.raises(ValueError):
        known.position("Ceres", 50000.0, cache=c)  # outside the index's time range
    monkeypatch.setattr(FakeKete, "fov_state_check", staticmethod(lambda states, fovs: []))
    with pytest.raises(ValueError):
        known.position("Ceres", 61000.25, cache=c)  # kete lost it
    known.reset_index()


def test_known_warm_up_never_raises(tmp_path, monkeypatch):
    known.reset_index()
    known.warm_up(Cache(str(tmp_path)))  # no index in this cache: logged, not raised
    monkeypatch.setenv("SKYSHIFT_KNOWN_WARMUP", "0")
    assert known.warm_up_in_background(Cache(str(tmp_path))) is None


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


def test_visits_near_the_ecliptic_pole_are_cut_into_parts():
    # near the pole SPHEREx sees a spot for months on end: one session a day for 120 days, then
    # a normal visit half a year later
    rows = [sia_row(f"P{d:03d}", 2, 60800.0 + d) for d in range(120)]
    rows += [sia_row(f"Q{d}", 2, 61100.0 + d) for d in range(10)]
    images = search.rows_to_images(rows)
    long_ = [im for im in images if im.obs_id.startswith("P")]
    parts = sorted({im.visit for im in long_})
    assert parts == [1, 2, 3, 4]  # 119 days > 75: cut into ceil(119 / 30) = 4 parts
    for v in parts:
        t = [im.mjd for im in long_ if im.visit == v]
        assert max(t) - min(t) <= 30.0
    assert {im.visit for im in images if im.obs_id.startswith("Q")} == {5}
    assert [im.visit for im in images] == sorted(im.visit for im in images)  # numbered in time order


def test_gaia_csv_from_irsa_nulls_stay_null():
    text = ("source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag\n"
            "4253498051115501824,280.27570117928,-5.61848485960,,,,11.760853\n"
            "2161688071217418240,270.34705106937,65.94887411202,5.353,35.718,5.9489,7.817915\n")
    a, b = gaia.parse_tap_csv(text)
    assert a == {"id": "4253498051115501824", "ra": 280.27570117928, "dec": -5.6184848596,
                 "pmra": None, "pmdec": None, "parallax": None, "g": 11.761}
    assert b["pmdec"] == 35.718 and b["parallax"] == 5.949 and isinstance(b["id"], str)


def test_gaia_falls_back_to_irsa_and_pauses_esa(tmp_path, monkeypatch):
    """ESA's TAP answering an HTML page (25 Nov 2026): IRSA's gaia_dr3_source answers, at once, and
    ESA is not asked again for ESA_PAUSE_S."""
    esa, irsa = [], []

    class Html:
        status_code = 200
        headers = {"content-type": "text/html; charset=UTF-8"}
        text = "<!doctype html>"

        def json(self):
            raise ValueError("not JSON")

    class Csv:
        status_code = 200
        headers = {"content-type": "text/csv"}
        text = "source_id,ra,dec,pmra,pmdec,parallax,phot_g_mean_mag\n1,270.0,66.0,1.0,2.0,3.0,9.5\n"

    def post(url, data, **kw):
        (esa if url == gaia.TAP_SYNC else irsa).append(data["QUERY"])
        return Html() if url == gaia.TAP_SYNC else Csv()

    monkeypatch.setattr(gaia.requests, "post", post)
    monkeypatch.setattr(gaia.time, "sleep", lambda s: None)
    monkeypatch.setattr(gaia, "_esa_down_until", 0.0)
    c = Cache(str(tmp_path))
    stars = gaia.cone(270.0, 66.0, 14.1, cache=c)
    assert stars == [{"id": "1", "ra": 270.0, "dec": 66.0, "pmra": 1.0, "pmdec": 2.0, "parallax": 3.0, "g": 9.5}]
    assert len(esa) == 1 and "gaiadr3.gaia_source" in esa[0]  # an HTML page is not asked twice
    assert len(irsa) == 1 and "FROM gaia_dr3_source" in irsa[0]
    gaia.cone(10.0, 20.0, 14.1, cache=c)  # another cone: ESA paused, IRSA first
    assert len(esa) == 1 and len(irsa) == 2


def test_gaia_both_failing_is_an_upstream_error(monkeypatch):
    class Down:
        status_code = 503
        headers = {"content-type": "text/plain"}
        text = "Service Unavailable"

    monkeypatch.setattr(gaia.requests, "post", lambda url, data, **kw: Down())
    monkeypatch.setattr(gaia.time, "sleep", lambda s: None)
    monkeypatch.setattr(gaia, "_esa_down_until", 0.0)
    with pytest.raises(UpstreamError, match="IRSA Gaia query failed"):
        gaia.stars_in(270.0, 66.0, 14.1)
