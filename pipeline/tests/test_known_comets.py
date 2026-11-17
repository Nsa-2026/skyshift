"""known_comets.py: MPC comet orbits via kete exempt matching movers cleanly and fail-softly."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from skyshift_core import known_index as KI
from skyshift_pipeline import candidates as K
from skyshift_pipeline import config as C
from skyshift_pipeline import known_comets as KC
from skyshift_pipeline import known_mpc as KM
from skyshift_pipeline.store import Store, dots_rel, photolist_rel, results_rel


def _rec(name: str, num: int | None = None, peri=(2025, 8, 1.647), epoch=(2025, 11, 21), q=1.272986) -> dict:
    r = {"Orbit_type": "P" if num else "C", "Year_of_perihelion": peri[0], "Month_of_perihelion": peri[1],
         "Day_of_perihelion": peri[2], "Perihelion_dist": q, "e": 0.59251, "Peri": 0.9146, "Node": 341.339, "i": 8.3017,
         "Designation_and_name": name, "H": 19.0, "G": 4.0}
    if num:
        r["Comet_num"] = num
    if epoch:
        r["Epoch_year"], r["Epoch_month"], r["Epoch_day"] = epoch
    return r


def _comet_ephem(kete, states, row_idx: int, times: list[float], sc: dict | None = None):
    """Exact predicted (ra, dec, rate_arcsec_h, pa_deg) of comet row_idx across `times` via kete."""
    obs = [KI.observer_state(kete, t, sc) for t in times]
    pos = []
    for o in obs:
        st = kete.propagate_n_body([states[row_idx]], o.jd, suppress_errors=True)[0]
        fov = kete.ConeFOV(kete.Vector.from_ra_dec(0.0, 0.0), 180.0, o)
        chk = list(kete.fov_state_check([st], [fov]))[0]
        ra, dec, dra, ddec = np.asarray(chk.ra_dec_with_rates, float).reshape(-1, 4)[0]
        pos.append((float(ra) % 360.0, float(dec), float(dra) * 150.0, float(ddec) * 150.0))
    return pos


def _mover_row(cid: str, ra: float, dec: float, mjd: float, rate: float, pa: float,
               times: np.ndarray, obs_ids: np.ndarray, **kw) -> dict:
    n = len(times)
    r = {
        "id": cid, "ra": ra, "dec": dec, "mjd": mjd, "session": 1, "visit": 1, "det": 2, "detectors": "2",
        "n_dots": n, "n_exposures": n, "evidence": "two_exposures", "snr": 25.0, "flux_ujy": 500.0, "flags": 0,
        "obs_ids": ",".join(obs_ids), "cell": 1, "nearest_known_arcsec": np.nan, "gaia_checked": True,
        "catwise_checked": True, "rate_arcsec_h": rate, "pa_deg": pa, "motion_snr": 20.0, "flux_ratio": 1.1,
        "dup_dots": 0, "cover_exposures": n, "missed_exposures": 0, "edge_exposures": 0, "still_free": 0,
        "paired_exposures": 0, "near_source_arcsec": np.nan,
        "dot_det": np.full(n, 2), "dot_x": np.linspace(100.0, 120.0, n), "dot_y": np.linspace(50.0, 60.0, n),
        "dot_mjd": np.asarray(times, np.float64), "dot_flux": np.full(n, 500.0), "dot_snr": np.full(n, 20.0),
        "dot_size": np.full(n, 6.0), "dot_chi2": np.full(n, 1.0), "dot_fpk": np.zeros(n, dtype=np.int64),
        "dot_obs": np.asarray(obs_ids, dtype=object),
    }
    r.update(kw)
    return r


def test_comet_orbits_and_matching_rules(tmp_path):
    kete = pytest.importorskip("kete")
    store = Store(str(tmp_path))
    cur = KM.comet_table([_rec("2P/Encke", 2, epoch=(2026, 11, 15))])
    every = KM.comet_table([_rec("2P/Encke", 2, epoch=(2025, 11, 21), q=0.33), _rec("306P/LINEAR", 306)])
    store.write_parquet(f"{C.MPC_ORBITS_DIR}/cometels_2026-11-14.parquet", cur)
    store.write_parquet("cache/known_check/allcometels_2026-11-16.parquet", every)

    orbits = KC.load_orbits(store)
    assert orbits is not None
    states, names, label = orbits
    assert "allcometels_2026-11-16.parquet" in label
    idx_306 = int(np.flatnonzero(names == "306P/LINEAR")[0])

    sc = {"X_SC": 4500.0, "Y_SC": 4200.0, "Z_SC": 2500.0, "VX_SC": -1.0, "VY_SC": -2.5, "VZ_SC": 6.5}
    times = [61000.0, 61000.04]
    ep = _comet_ephem(kete, states, idx_306, times, sc=sc)
    dra, ddec = ep[0][2], ep[0][3]
    rate = float(np.hypot(dra, ddec))
    pa = float(np.degrees(np.arctan2(dra, ddec)) % 360.0)
    m_ra = float(0.5 * (ep[0][0] + ep[1][0]))
    m_dec = float(0.5 * (ep[0][1] + ep[1][1]))
    m_t = float(0.5 * (times[0] + times[1]))

    store.write_parquet(photolist_rel("z1"), pd.DataFrame({
        "obs_id": ["o1", "o2", "o3", "o4"], "session": [1, 1, 2, 2], "mjd": [times[0], times[1], times[0], times[1]],
    }))
    store.write_json("cache/mpc/z1/run_o1.json", {
        "observers": {o: {"observer": "spacecraft", **sc} for o in ("o1", "o2")},
    })
    store.write_parquet(KC.KNOWN_OBS_PARQUET, pd.DataFrame([
        {"obs_id": "o3", "mjd_avg": times[0], **sc},
        {"obs_id": "o4", "mjd_avg": times[1], **sc},
    ]))

    # Table without track columns (session_rate_arcsec_h absent): direct rate_arcsec_h match
    plain = pd.DataFrame([
        _mover_row("z1-plain", m_ra, m_dec, m_t, rate, pa, np.array(times), np.array(["o1", "o2"])),
    ])
    plain_got, _ = KC.zone_comets(store, "z1", plain, orbits=orbits)
    assert plain_got == {"z1-plain": "306P/LINEAR"}

    rows = [
        # 0: unlinked row (tracklet_id=-1, session_rate populated as track_grades writes), session file observer -> match
        _mover_row("z1-match-run", m_ra, m_dec, m_t, np.nan, np.nan, np.array(times), np.array(["o1", "o2"]),
                   tracklet_id=-1, session_rate_arcsec_h=rate, session_pa_deg=pa),
        # 1: observers.parquet fallback, on track with wrong chord rate but right session_rate -> match
        _mover_row("z1-match-pq-track", m_ra, m_dec, m_t, 5.0, (pa + 180.0) % 360.0,
                   np.array(times), np.array(["o3", "o4"]), session=2, tracklet_id=7,
                   session_rate_arcsec_h=rate, session_pa_deg=pa),
        # 2: same place within 2", but wrong speed & direction -> must NOT match, keeps float rb_score
        _mover_row("z1-wrong-motion", m_ra + 2.0 / 3600.0, m_dec, m_t, np.nan, np.nan,
                   np.array(times), np.array(["o1", "o2"]),
                   tracklet_id=-1, session_rate_arcsec_h=rate * 3.0, session_pa_deg=(pa + 90.0) % 360.0),
        # 3: 40" away -> must NOT match, keeps float rb_score
        _mover_row("z1-far", m_ra + 40.0 / 3600.0, m_dec, m_t, np.nan, np.nan,
                   np.array(times), np.array(["o1", "o2"]),
                   tracklet_id=-1, session_rate_arcsec_h=rate, session_pa_deg=pa),
    ]
    susp = pd.DataFrame(rows)
    got, st = KC.zone_comets(store, "z1", susp, orbits=orbits)
    assert got == {"z1-match-run": "306P/LINEAR", "z1-match-pq-track": "306P/LINEAR"}
    assert st["matched"] == 2 and st["observers_spherex"] == 4

    cands = {c["id"]: c for c in K.movers(susp, "z1", None)}
    assert cands["z1-match-pq-track"]["rate_arcsec_h"] == pytest.approx(rate)  # the session's motion, not the chord
    got_c, st_c = K.exempt_comets(store, "z1", susp, cands, orbits)
    assert got_c == got and st_c["matched"] == 2
    assert cands["z1-match-run"]["rb_score"] is None
    assert cands["z1-match-pq-track"]["rb_score"] is None
    assert cands["z1-wrong-motion"]["rb_score"] is not None
    assert cands["z1-far"]["rb_score"] is not None

    # A table without session_* columns (before 16 Nov): the mover's own motion (build: its dots') beats the track's
    # chord in rate_arcsec_h; a mover shown without a motion off a track keeps link's rate_arcsec_h
    old = susp.drop(columns=["session_rate_arcsec_h", "session_pa_deg"]).iloc[:2].copy()
    old["rate_arcsec_h"], old["pa_deg"] = [rate, 5.0], [pa, (pa + 180.0) % 360.0]
    oc = {c["id"]: c for c in K.movers(old, "z1", None)}
    assert oc["z1-match-run"]["rate_arcsec_h"] is None and oc["z1-match-pq-track"]["rate_arcsec_h"] is None
    assert K.exempt_comets(store, "z1", old, {k: dict(v) for k, v in oc.items()}, orbits)[0] == {"z1-match-run": "306P/LINEAR"}
    oc["z1-match-pq-track"]["rate_arcsec_h"], oc["z1-match-pq-track"]["pa_deg"] = rate, pa
    assert K.exempt_comets(store, "z1", old, oc, orbits)[0] == {"z1-match-run": "306P/LINEAR",
                                                               "z1-match-pq-track": "306P/LINEAR"}

    # Empty store fails soft with no exception
    empty_got, empty_st = KC.zone_comets(Store(str(tmp_path / "empty")), "z1", susp)
    assert empty_got == {} and "error" in empty_st


def test_build_exempts_matched_comet(tmp_path):
    kete = pytest.importorskip("kete")
    store = Store(str(tmp_path))
    com = KM.comet_table([_rec("306P/LINEAR", 306)])
    store.write_parquet(f"{C.MPC_ORBITS_DIR}/cometels_2026-11-16.parquet", com.assign(**{KM.ORBIT_FILE: "cometels"}))
    states, names, _ = KC.load_orbits(store)
    sc = {"X_SC": 4500.0, "Y_SC": 4200.0, "Z_SC": 2500.0, "VX_SC": -1.0, "VY_SC": -2.5, "VZ_SC": 6.5}
    times = [61000.0, 61000.04]
    ep = _comet_ephem(kete, states, 0, times, sc=sc)
    dra, ddec = ep[0][2], ep[0][3]
    rate = float(np.hypot(dra, ddec))
    pa = float(np.degrees(np.arctan2(dra, ddec)) % 360.0)
    m_ra, m_dec, m_t = 0.5 * (ep[0][0] + ep[1][0]), 0.5 * (ep[0][1] + ep[1][1]), 0.5 * (times[0] + times[1])

    store.write_parquet(photolist_rel("z1"), pd.DataFrame({
        "obs_id": ["o1", "o2"], "detector": [2, 2], "session": [1, 1], "mjd": times,
        "collection": ["spherex_dr1", "spherex_qr3"],
    }))
    store.write_json("cache/mpc/z1/run_o1.json", {"observers": {o: {"observer": "spacecraft", **sc} for o in ("o1", "o2")}})
    for o, t in zip(("o1", "o2"), times):
        store.write_parquet(dots_rel(o, 2), pd.DataFrame({
            "ra": [m_ra], "dec": [m_dec], "mjd": [t], "snr": [25.0], "pos_err_arcsec": [0.1], "flags": [0],
        }))
    rows = [
        _mover_row("z1-comet", m_ra, m_dec, m_t, np.nan, np.nan, np.array(times), np.array(["o1", "o2"]),
                   grade="B", tracklet_id=-1, session_rate_arcsec_h=rate, session_pa_deg=pa),
        _mover_row("z1-other", (m_ra + 2.0) % 360.0, m_dec, m_t, np.nan, np.nan, np.array(times), np.array(["o1", "o2"]),
                   grade="B", tracklet_id=-1, session_rate_arcsec_h=rate, session_pa_deg=pa),
    ]
    store.write_parquet(results_rel("z1", "suspects.parquet"), pd.DataFrame(rows))
    store.write_parquet(results_rel("z1", "highpm_candidates.parquet"), pd.DataFrame(columns=["source_id", "ra", "dec"]))
    store.write_parquet(results_rel("z1", "classes.parquet"), pd.DataFrame(columns=["source_id", "label"]))
    store.write_parquet(results_rel("z1", "sources.parquet"), pd.DataFrame(columns=["source_id", "ra", "dec"]))
    store.write_parquet(results_rel("z1", "members.parquet"), pd.DataFrame(columns=["source_id", "obs_id", "detector", "dot_row"]))

    doc = K.build(store, ["z1"])
    by_id = {c["id"]: c for c in doc["candidates"]}
    assert by_id["z1-comet"]["rb_score"] is None
    assert isinstance(by_id["z1-other"]["rb_score"], float) and 0.0 <= by_id["z1-other"]["rb_score"] <= 1.0

