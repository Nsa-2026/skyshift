import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from skyshift_pipeline import config as C
from skyshift_pipeline import spectra as SP
from skyshift_pipeline.cli import main
from skyshift_pipeline.dots import DOT_COLUMNS
from skyshift_pipeline.link import fixed_groups, members_table, source_id_of
from skyshift_pipeline.photolist import add_sessions
from skyshift_pipeline.sky import cell_of, offset_radec
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel

from conftest import t
from test_photolist import square

STAR = (122.30, 16.10)


# --- channels --------------------------------------------------------------------------------
def test_channels_are_102_log_spaced_and_contiguous():
    ch = SP.channels()
    assert len(ch) == 102 and [c["channel"] for c in ch] == list(range(102))
    for d in range(1, 7):
        cs = [c for c in ch if c["detector"] == d]
        lo, hi = C.SPECTRA_DETECTOR_UM[d]
        assert len(cs) == 17 and np.isclose(cs[0]["um_lo"], lo) and np.isclose(cs[-1]["um_hi"], hi)
        ratios = [c["um_hi"] / c["um_lo"] for c in cs]
        assert np.allclose(ratios, ratios[0], rtol=1e-4)
        assert all(np.isclose(a["um_hi"], b["um_lo"]) for a, b in zip(cs, cs[1:]))


def test_channel_assignment_edges_and_out_of_range():
    e = SP.channel_edges()
    det = np.array([1, 1, 1, 1, 1, 2, 4, 6, 6, 3, 7, 1])
    wl = np.array([0.75, e[0, 1], e[0, 1] - 1e-9, 1.0999, 0.735, 1.1, 2.4, 5.0, 5.3, np.nan, 1.0, 1.126])
    got = SP.channel_of(det, wl)
    # first edge belongs to channel 0; the edge between 0 and 1 to channel 1; outside -> own end channel
    assert got.tolist() == [0, 1, 0, 16, 0, 17, 51, 101, 101, -1, -1, 16]
    # a D1 dot at 1.2 µm stays in D1 (top channel), never moves to D2
    assert SP.channel_of([1], [1.2])[0] == 16


# --- synthetic sources -----------------------------------------------------------------------
def true_flux(wl):
    """A power law with an absorption dip at 3.0 µm (µJy)."""
    return 1000.0 * (wl / 1.0) ** -1.5 * (1.0 - 0.5 * np.exp(-0.5 * ((wl - 3.0) / 0.08) ** 2))


def star_dots(rng, sid=1, n_per_det=100, rel_err=0.03, ratio=1.0, flux=true_flux, visits=2):
    """One source seen n_per_det times in every detector at random wavelengths; 2 visits,
    session = dot index // 3. Flux in the last visit is multiplied by `ratio`."""
    rows = []
    for d in range(1, 7):
        lo, hi = C.SPECTRA_DETECTOR_UM[d]
        wl = np.exp(rng.uniform(np.log(lo), np.log(hi), n_per_det))
        visit = 1 + (np.arange(n_per_det) * visits) // n_per_det
        f0 = flux(wl) * np.where(visit == visits, ratio, 1.0)
        err = rel_err * flux(wl)
        rows.append(pd.DataFrame({
            "source_id": sid, "detector": d, "session": np.arange(n_per_det) // 3 + 100 * visit, "visit": visit,
            "ra": STAR[0] + rng.normal(0, 0.3 / 3600, n_per_det), "dec": STAR[1] + rng.normal(0, 0.3 / 3600, n_per_det),
            "flux_ujy": f0 + rng.normal(0, 1, n_per_det) * err, "flux_err_ujy": err, "wavelength_um": wl, "flags": 0,
        }))
    return pd.concat(rows, ignore_index=True)


def test_power_law_with_dip_is_recovered(rng):
    d = star_dots(rng, n_per_det=100)
    # a cosmic-ray hit and a masked dot must not move the spectrum
    d.loc[5, "flux_ujy"] *= 30
    d.loc[6, ["flux_ujy", "flags"]] = [1e6, 1 << 10]  # HOT
    src = SP.compute(d).iloc[0]
    flux, err, n = np.asarray(src["flux_ujy"]), np.asarray(src["err_ujy"]), np.asarray(src["n"])
    ch = SP.channels()
    centre = np.array([c["um_centre"] for c in ch])
    have = n > 0
    assert have.sum() > 90 and n.sum() == len(d) - 2
    # within errors (plus the model's spread inside a channel, which matters at the dip)
    lo = np.array([true_flux(c["um_lo"]) for c in ch]); hi = np.array([true_flux(c["um_hi"]) for c in ch])
    spread = np.abs(hi - lo) / 2 + np.abs(true_flux(centre) - (lo + hi) / 2)
    pull = (flux - true_flux(centre))[have] / np.hypot(err, spread)[have]
    assert np.all(np.abs(pull) < 4.5), pull
    # the dip is there: the 3.0 µm channel is well below its neighbours' power law
    k = int(np.argmin(np.abs(centre - 3.0)))
    assert flux[k] < 0.75 * 1000 * centre[k] ** -1.5
    assert np.isnan(flux[~have]).all() and (n[~have] == 0).all()
    assert src["detectors"] == 63 and src["n_dots"] == 600 and src["n_visits"] == 2
    assert not src["saturated"]
    assert 0.05 <= src["pos_err_arcsec"] < 0.1


def flat(wl):
    return np.full_like(wl, 500.0)


def test_constant_star_chi2_near_one_and_not_variable(rng):
    src = SP.compute(star_dots(rng, n_per_det=200, flux=flat)).iloc[0]
    assert 0.8 < src["chi2_nu"] < 1.25
    assert abs(src["visit_flux_ratio"] - 1) < 0.05
    assert not src["variable"]


def test_star_that_doubles_between_visits_is_flagged(rng):
    src = SP.compute(star_dots(rng, n_per_det=200, flux=flat, ratio=2.0)).iloc[0]
    assert src["chi2_nu"] > 20
    assert 1.6 < src["visit_flux_ratio"] < 2.4
    assert src["variable"]


def test_many_sources_vectorised_and_saturation(rng):
    parts = [star_dots(rng, sid=i, n_per_det=20, flux=flat) for i in (7, 3, 11)]
    parts[1].loc[:2, "flags"] = 1 << 15  # NONLINEAR (a SAT and MASK bit) on 3 dots
    src = SP.compute(pd.concat(parts, ignore_index=True))
    assert src["source_id"].tolist() == [3, 7, 11]
    assert src["saturated"].tolist() == [True, False, False]
    assert src.loc[0, "n"].sum() == 120 - 3
    tab = SP.to_arrow(src)
    assert str(tab.schema.field("flux_ujy").type) == "fixed_size_list<item: float>[102]"
    assert str(tab.schema.field("n").type) == "fixed_size_list<item: int16>[102]"


# --- link -> members -> spectra round trip ---------------------------------------------------
def test_source_id_deterministic():
    ra = np.array([122.3, 122.3 + 2 / 3600, 123.0]); dec = np.array([16.1, 16.1, 16.5])
    a = source_id_of(ra, dec)
    b = source_id_of(ra[::-1], dec[::-1])[::-1]
    assert (a == b).all() and len(set(a)) == 3
    assert (a // C.SOURCE_ID_PER_CELL == cell_of(ra, dec)).all()


def dots_df(obs_id, det, mjd, pts, wl):
    ra = np.array([p[0] for p in pts]); dec = np.array([p[1] for p in pts])
    n = len(pts)
    return pd.DataFrame({
        "obs_id": obs_id, "detector": det, "mjd": mjd, "session": 0, "visit": 0, "ra": ra, "dec": dec,
        "pos_err_arcsec": 0.3, "x": 1.0, "y": 1.0, "flux_ujy": np.arange(n) + 100.0, "flux_err_ujy": 5.0,
        "wavelength_um": wl, "bandwidth_um": 0.02, "elong": 1.1, "snr": np.full(n, 20.0), "flags": 0, "flags_peak": 0,
        "cell": cell_of(ra, dec),
    }).astype(DOT_COLUMNS)


def test_members_round_trip(tmp_path):
    store = Store(str(tmp_path))
    rows = [dict(obs_id=f"S{s}E{e}", detector=1, mjd=t(s, e), s_region=square(122.3, 16.15), collection="spherex_dr1",
                 url="", s3_key="", size_bytes=1) for s in range(5) for e in range(2)]
    photos = add_sessions(pd.DataFrame(rows))
    store.write_parquet(photolist_rel("pilot"), photos)
    other = offset_radec(*STAR, 30.0, 0.0)
    outside = (100.0, 0.0)  # outside the zone: dropped by link, but it shifts the rows of the others
    for i, p in enumerate(photos.itertuples()):
        pts = [outside, STAR, (122.0 + i * 0.01, 16.0), other] if i % 2 else [STAR, other]
        store.write_parquet(dots_rel(p.obs_id, 1), dots_df(p.obs_id, 1, p.mjd, pts, 0.8 + 0.02 * i))
    assert main(["link", "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
    fixed = store.read_parquet(results_rel("pilot", "fixed.parquet"))
    mem = store.read_parquet(results_rel("pilot", "members.parquet"))
    assert len(fixed) == 2 and set(mem["source_id"]) == set(fixed["source_id"]) and len(mem) == 20
    assert list(mem.columns) == ["source_id", "obs_id", "detector", "dot_row"]
    # fetch one source's dots back through members
    sid = fixed.loc[np.argmin(np.abs(fixed["ra"] - STAR[0])), "source_id"]
    back = []
    for r in mem[mem["source_id"] == sid].itertuples():
        back.append(store.read_parquet(dots_rel(r.obs_id, r.detector)).iloc[r.dot_row])
    back = pd.DataFrame(back)
    assert len(back) == 10 and np.allclose(back["ra"], STAR[0]) and np.allclose(back["dec"], STAR[1])

    assert main(["spectra", "--zone", "pilot", "--root", str(tmp_path), "--workers", "2"]) == 0
    src = store.read_parquet(results_rel("pilot", "sources.parquet"))
    assert sorted(src["source_id"]) == sorted(fixed["source_id"])
    s = src[src["source_id"] == sid].iloc[0]
    assert s["n_dots"] == 10 and s["n_sessions"] == 5 and s["n_visits"] == 1 and len(s["flux_ujy"]) == 102
    assert np.asarray(s["n"]).sum() == 10
    schema = pq.read_schema(tmp_path / "results/pilot/sources.parquet")
    assert schema.field("flux_ujy").type.list_size == 102
    assert len(json.loads((tmp_path / "results/pilot/channels.json").read_text())) == 102


def test_fixed_groups_ids_match_members():
    rows = []
    for s in range(4):
        for p in (STAR, offset_radec(*STAR, 40.0, 0.0)):
            rows.append(dict(ra=p[0], dec=p[1], session=s, snr=10.0, flux_ujy=1.0, photo=s, dot_row=0))
    dots = pd.DataFrame(rows)
    fixed, dot_sid = fixed_groups(dots)
    assert (dot_sid >= 0).all() and set(dot_sid) == set(fixed["source_id"])
    photos = pd.DataFrame({"obs_id": [f"O{i}" for i in range(4)], "detector": [2] * 4})
    mem = members_table(dots, dot_sid, photos)
    assert len(mem) == 8 and mem["source_id"].is_monotonic_increasing and (mem["detector"] == 2).all()
