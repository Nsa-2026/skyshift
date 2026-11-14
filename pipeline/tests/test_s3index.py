"""s3index: header parsing, the index tool (network faked) and photolist's fallback to it."""

import io

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from conftest import CUTOUT
from skyshift_pipeline import config as C
from skyshift_pipeline import photolist
from skyshift_pipeline import s3index as S
from skyshift_pipeline.sky import Footprint
from skyshift_pipeline.store import Store, photolist_rel
from test_photolist import square

ZONE = C.ZONES["pilot"]  # 120.5-124.1, 14.4-17.9


# --- headers -----------------------------------------------------------------------------------
def _fits_bytes(hdus) -> bytes:
    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf)
    return buf.getvalue()


def test_meta_from_a_real_image_header():
    data = CUTOUT.read_bytes()
    text = S.image_header_text(data[:S.HEADER_BYTES])
    m = S.meta_from_header(text)
    h = fits.getheader(CUTOUT, "IMAGE")
    assert m["obs_id"] == h["OBSID"] == "2025W18_1B_0074_2"
    assert m["detector"] == h["DETECTOR"] == 1
    assert m["t_min"] == round(h["MJD-BEG"], 6) and m["t_max"] == round(h["MJD-END"], 6)
    nx, ny = h["NAXIS1"], h["NAXIS2"]
    w = WCS(h)  # the full header: the minimal WCS card set must give the same numbers
    rd = w.all_pix2world([[0.5, 0.5], [nx + 0.5, 0.5], [nx + 0.5, ny + 0.5], [0.5, ny + 0.5]], 1)
    v = np.array([float(x) for x in m["s_region"].split()[2:]]).reshape(-1, 2)
    assert m["s_region"].startswith("POLYGON ICRS ") and len(v) == 5 and np.array_equal(v[0], v[-1])
    assert np.array_equal(v[:4], rd)
    c = w.all_pix2world([[(nx + 1) / 2, (ny + 1) / 2]], 1)[0]
    assert m["s_ra"] == round(float(c[0]), 8) and m["s_dec"] == round(float(c[1]), 8)


def test_image_header_after_other_hdus_and_short_reads():
    other = fits.ImageHDU(np.zeros((30, 40), np.float32), name="OTHER")  # data to skip: 4800 bytes
    img = fits.ImageHDU(np.zeros((4, 4), np.float32), name="IMAGE")
    img.header["OBSID"] = "X_1"
    data = _fits_bytes([fits.PrimaryHDU(), other, img])
    text = S.image_header_text(data)
    assert fits.Header.fromstring(text)["OBSID"] == "X_1" and text.rstrip().endswith("END")
    with pytest.raises(S._Short):
        S.image_header_text(data[:2880 * 3])


# --- a tiny synthetic index ------------------------------------------------------------------
KEY = "{p}/level2/{per}/l2b-v27-2026-200/{d}/level2_{obs}D{d}_spx_l2b-v27-2026-200.fits"


def _row(prefix, per, obs, det, ra, dec, t):
    key = KEY.format(p=prefix, per=per, obs=obs, d=det)
    return {"obs_id": obs, "collection": S.PREFIXES[prefix], "t_min": t, "t_max": t + 0.0013, "s_ra": ra, "s_dec": dec,
            "s_region": square(ra, dec), "size_bytes": 65_000_000 + det, "detector": det, "s3_key": key}


ROWS = [
    # dr1 2025W42_2A: two exposures over the zone (D1 + D4 each), one far away
    _row("dr1", "2025W42_2A", "2025W42_2A_0111_1", 1, 122.3, 16.1, 60964.989182),
    _row("dr1", "2025W42_2A", "2025W42_2A_0111_1", 4, 122.3, 16.1, 60964.989182),
    _row("dr1", "2025W42_2A", "2025W42_2A_0111_2", 1, 122.4, 16.2, 60964.990674),
    _row("dr1", "2025W42_2A", "2025W42_2A_0111_2", 4, 122.4, 16.2, 60964.990674),
    _row("dr1", "2025W42_2A", "2025W42_2A_0500_1", 2, 200.0, -30.0, 60965.5),
    # only a corner over the zone's corner
    _row("dr1", "2025W42_2A", "2025W42_2A_0700_1", 3, 125.5, 19.3, 60966.1),
    # qr3 2026W30_1A: six months later; one photo also in dr1 (DR1 must win)
    _row("qr3", "2026W30_1A", "2026W30_1A_0042_1", 2, 121.0, 15.0, 61150.2),
    _row("qr3", "2026W30_1A", "2025W42_2A_0111_2", 1, 122.4, 16.2, 60964.990674),
    _row("qr3", "2026W30_1A", "2026W30_1A_0900_3", 6, 10.0, 10.0, 61151.0),
]


def _index_frame(rows):
    meta = [{k: r[k] for k in ("obs_id", "t_min", "t_max", "s_ra", "s_dec", "s_region", "detector")} for r in rows]
    prefix = rows[0]["s3_key"].split("/")[0]
    for m, r in zip(meta, rows):
        m["key"] = r["s3_key"]
    return S.rows_frame(prefix, meta, {r["s3_key"]: r["size_bytes"] for r in rows})


def _write_index(store, complete=True, skip=()):
    shards = {}
    for r in ROWS:
        p, per = r["s3_key"].split("/")[0], r["s3_key"].split("/")[2]
        shards.setdefault((p, per), []).append(r)
    for (p, per), rows in shards.items():
        if (p, per) in skip:
            continue
        store.write_parquet(S.shard_rel(p, per), _index_frame(rows))
        store.write_json(S.shard_rel(p, per, "json"), {"keys": len(rows), "rows": len(rows), "failed": []})
    if complete:
        store.write_json(S.COMPLETE_REL, {"complete": True, "periods": {"dr1": ["2025W42_2A"], "qr3": ["2026W30_1A"]},
                                          "failed_keys": 0})


def _tap_shaped(rows):
    """What photolist.run writes from TAP for the photos whose footprint INTERSECTS the zone."""
    raw = pd.DataFrame([{"obs_id": r["obs_id"], "collection": r["collection"], "bandpass": f"SPHEREx-D{r['detector']}",
                         "t_min": r["t_min"], "t_max": r["t_max"], "s_ra": r["s_ra"], "s_dec": r["s_dec"],
                         "s_region": r["s_region"], "uri": C.IRSA_URI_PREFIX + r["s3_key"], "size_bytes": r["size_bytes"]}
                        for r in rows])
    df, _ = photolist.clean(raw)
    return photolist.add_sessions(df)


def _inside(rows):
    return [r for r in rows if S.zone_box_intersects(Footprint.from_s_region(r["s_region"]), ZONE)]


def test_fallback_equals_a_tap_shaped_list(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    _write_index(store)
    monkeypatch.setenv("SKYSHIFT_PHOTOLIST_SOURCE", "s3index")
    out = photolist.run(store, ZONE)
    got = store.read_parquet(photolist_rel("pilot"))
    want = _tap_shaped(_inside(ROWS))
    assert len(want) == 6  # 4 + corner + the qr3 one; far-away and the DR1/QR3 duplicate's QR3 copy left out
    pd.testing.assert_frame_equal(got, want)
    assert list(got.columns) == ["obs_id", "collection", "t_min", "t_max", "s_ra", "s_dec", "s_region", "size_bytes",
                                 "detector", "s3_key", "mjd", "url", "session", "visit"]
    assert got.loc[got.obs_id == "2025W42_2A_0111_2", "collection"].tolist() == ["spherex_dr1", "spherex_dr1"]
    assert out["query"] == "s3index" and out["index_complete"] is True and out["duplicates_removed"] == 1
    # same Arrow types as a TAP list
    tap_path = tmp_path / "tap.parquet"
    want.to_parquet(tap_path, index=False)
    assert pq.read_schema(store.path(photolist_rel("pilot"))).remove_metadata().equals(
        pq.read_schema(tap_path).remove_metadata())
    sch = pq.read_schema(store.path(photolist_rel("pilot")))
    assert str(sch.field("obs_id").type) == "large_string" and str(sch.field("detector").type) == "int8"
    assert str(sch.field("session").type) == "int32" and str(sch.field("visit").type) == "int16"


def test_tap_failure_falls_back(tmp_path, monkeypatch):
    store = Store(str(tmp_path))
    _write_index(store)
    monkeypatch.delenv("SKYSHIFT_PHOTOLIST_SOURCE", raising=False)

    def down(zone):
        raise RuntimeError("502 Bad Gateway")

    monkeypatch.setattr(photolist, "tap_query", down)
    out = photolist.run(store, ZONE)
    assert out["query"] == "s3index" and "502" in out["tap_error"] and out["photos"] == 6


def test_incomplete_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYSHIFT_PHOTOLIST_SOURCE", "s3index")
    monkeypatch.setattr(S, "list_periods", lambda prefix: {"dr1": ["2025W42_2A"], "qr3": ["2026W30_1A"]}[prefix])
    # a period shard missing: fail, write nothing
    store = Store(str(tmp_path / "a"))
    _write_index(store, complete=False, skip={("qr3", "2026W30_1A")})
    with pytest.raises(RuntimeError, match="incomplete"):
        photolist.run(store, ZONE)
    assert not store.exists(photolist_rel("pilot"))
    # every shard but no _complete.json: written, and said so
    store = Store(str(tmp_path / "b"))
    _write_index(store, complete=False)
    out = photolist.run(store, ZONE)
    assert out["index_complete"] is False and out["photos"] == 6


def test_box_intersection_follows_great_circles():
    # the zone's top edge (Dec 24.4 from RA 120.5 to 130.5) is a great circle: it bulges to Dec ~24.48 at RA 125.5
    hunt = C.ZONES["hunt"]
    v = np.array([[125.3, 24.45], [125.7, 24.45], [125.7, 24.9], [125.3, 24.9]])
    fp = Footprint(v)
    assert S.zone_box_intersects(fp, hunt)
    assert not S.zone_box_intersects(Footprint(v + [0, 0.1]), hunt)
    far = Footprint.from_s_region(square(300.0, -40.0))
    assert not S.zone_box_intersects(far, hunt)
    assert S.zone_box_intersects(Footprint.from_s_region(square(125.0, 20.0)), hunt)  # inside
    assert S.zone_box_intersects(Footprint.from_s_region(square(125.0, 19.0, half=10.0)), hunt)  # zone inside it


# --- the tool, with S3 faked (in-process: --procs 1) ------------------------------------------
def test_run_writes_shards_resumes_and_retries(tmp_path, monkeypatch):
    rows = {r["s3_key"]: r for r in ROWS}
    periods = {"dr1": ["2025W42_2A"], "qr3": ["2026W30_1A"]}
    flaky = {ROWS[2]["s3_key"]}
    calls = []

    def read_meta(key):
        calls.append(key)
        if key in flaky:
            raise OSError("connection reset")
        r = rows[key]
        return {k: r[k] for k in ("obs_id", "t_min", "t_max", "s_ra", "s_dec", "s_region", "detector")}

    monkeypatch.setattr(S, "list_periods", lambda prefix: periods[prefix])
    monkeypatch.setattr(S, "list_period_keys", lambda prefix, per: sorted(
        (k, r["size_bytes"]) for k, r in rows.items() if k.startswith(f"{prefix}/level2/{per}/")))
    monkeypatch.setattr(S, "read_meta", read_meta)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    store = Store(str(tmp_path))
    out = S.run(store, ("dr1", "qr3"), workers=4, procs=1)
    assert out["shards_built"] == 2 and out["complete"] and out["failed_keys_left"] == 1
    side = store.read_json(S.shard_rel("dr1", "2025W42_2A", "json"))
    assert side["keys"] == 6 and side["rows"] == 5 and [f["key"] for f in side["failed"]] == list(flaky)
    assert calls.count(ROWS[2]["s3_key"]) == S.READ_TRIES
    st = store.read_json(S.COMPLETE_REL)
    assert st["rows"] == 8 and st["keys"] == 9 and st["failed_keys"] == 1
    shard = store.read_parquet(S.shard_rel("qr3", "2026W30_1A"))
    pd.testing.assert_frame_equal(shard, _index_frame([r for r in ROWS if r["collection"] == "spherex_qr3"]))
    # rerun: the complete qr3 shard is skipped; dr1 retries only its failed key, now readable
    flaky.clear()
    calls.clear()
    out = S.run(store, ("dr1", "qr3"), workers=4, procs=1)
    assert calls == [ROWS[2]["s3_key"]] and out["shards_skipped"] == 1 and out["failed_keys_left"] == 0
    shard = store.read_parquet(S.shard_rel("dr1", "2025W42_2A"))
    pd.testing.assert_frame_equal(shard, _index_frame([r for r in ROWS if r["collection"] == "spherex_dr1"]))
    assert store.read_json(S.COMPLETE_REL)["rows"] == 9
    # --periods: only that one, and no _complete.json rewrite problem
    out = S.run(store, ("dr1",), workers=2, procs=1, periods=["2025W42*"], force=True)
    assert out["shards_built"] == 1 and out["complete"]


def test_cli_s3index_is_a_tool(tmp_path, monkeypatch):
    from skyshift_pipeline import cli

    seen = {}

    def fake_run(store, cols, workers, procs, periods, force):
        seen.update(cols=cols, workers=workers, procs=procs, periods=periods, force=force, root=store.root)
        return {"complete": True}

    monkeypatch.setattr(S, "run", fake_run)
    assert cli.main(["s3index", "--root", str(tmp_path), "--collections", "qr3", "--periods", "2026W3*"]) == 0
    assert seen == {"cols": ("qr3",), "workers": 64, "procs": None, "periods": ["2026W3*"], "force": False,
                    "root": str(tmp_path)}
    assert "s3index" in cli.TOOLS and "s3index" not in cli.STEPS
